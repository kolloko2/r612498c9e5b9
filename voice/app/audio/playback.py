import asyncio
from uuid import uuid4

from .formats import Framer, SILENCE, FramePacer
from .speech_text import for_speech
from .tts import make_tts


class Playback:
    """Independent cancellable synthesis, priority dispatch and one media writer."""

    def __init__(self, settings, peer, environment, quiet, on_error, on_fatal, call_id="test", on_status=None):
        self.settings, self.peer, self.environment, self.quiet = settings, peer, environment, quiet
        self.waiting_for_quiet = False
        self.on_error, self.on_fatal = on_error, on_fatal
        self.call_id = call_id
        self.on_status = on_status
        self.current_id = None
        self.replies = asyncio.PriorityQueue(maxsize=64)
        self.frames = asyncio.Queue(maxsize=10)
        self.serial = self.generation = 0
        self.current = None
        self.providers = {}
        self.lock = asyncio.Lock()
        self.closed = False
        self.renderer = asyncio.create_task(self._render(), name="audio-output")
        self.worker = asyncio.create_task(self._dispatch(), name="reply-dispatch")

    async def status(self, reply_id, status):
        if self.on_status:
            await self.on_status(str(reply_id), status)

    @property
    def active(self):
        return self.current is not None and not self.current.done()

    @property
    def speaking(self):
        """Ответ звучит, а не ждёт, пока оператор договорит.

        Пока ответ ждёт тишины, речь оператора должна доходить до VAD: иначе
        VAD не увидит конца фразы, тишина не наступит, и ответ с дальнейшими
        репликами оператора зависнут до конца звонка.
        """
        return self.active and not self.waiting_for_quiet

    async def submit(self, reply, metrics):
        if reply.should_interrupt:
            await self.interrupt()
        self.serial += 1
        self.replies.put_nowait((0 if reply.should_interrupt else 1, self.serial,
                                self.generation, reply, metrics))

    async def _render(self):
        pacer = FramePacer()
        try:
            while True:
                try:
                    item = self.frames.get_nowait()
                except asyncio.QueueEmpty:
                    item = SILENCE
                if isinstance(item, str):
                    await self.peer.command("STOP_MEDIA_BUFFERING", correlation_id=item)
                    continue
                if isinstance(item, tuple):
                    reply_id, item = item
                    await self.peer.send(self.environment.process(item))
                    await self.status(reply_id, "playing")
                else:
                    await self.peer.send(self.environment.process(item))
                await pacer.tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.on_fatal("playback_transport", type(exc).__name__)

    async def _dispatch(self):
        while True:
            _, _, generation, reply, metrics = await self.replies.get()
            try:
                async with self.lock:
                    if generation != self.generation:
                        continue
                    self.current_id = reply.reply_id
                    self.current = asyncio.create_task(self._reply(reply, metrics), name="tts-reply")
                    current = self.current
                try:
                    await current
                except asyncio.CancelledError:
                    await self.status(reply.reply_id, "interrupted")
                    if self.closed:
                        raise
                except Exception as exc:
                    await self.status(reply.reply_id, "error")
                    self.on_fatal("tts_failed", type(exc).__name__)
            finally:
                self.replies.task_done()

    async def _reply(self, reply, metrics):
        if not reply.should_interrupt:
            self.waiting_for_quiet = True
            try:
                await self.quiet.wait()
            finally:
                self.waiting_for_quiet = False
        names = [self.settings.tts_provider]
        if self.settings.tts_fallback_provider:
            names.append(self.settings.tts_fallback_provider)
        for index, name in enumerate(names):
            provider = None
            correlation = str(uuid4())
            completed = self.peer.expect_completion(correlation)
            try:
                provider = self.providers.get(name)
                if provider is None:
                    provider = self.providers[name] = make_tts(self.settings, name)
                metrics.mark("tts_request_ms")
                await self.peer.command("START_MEDIA_BUFFERING")
                # Синтез читает строку буквально, поэтому сокращения и числа
                # раскрываются перед произнесением: «д. 12, стр. 2» иначе
                # звучит как «д двенадцать стр два» (см. speech_text.py).
                stream = provider.synthesize_stream(for_speech(reply.text), reply.voice_style)
                framer, first = Framer(), True
                first_frame = True
                while True:
                    try:
                        chunk = await asyncio.wait_for(anext(stream), self.settings.provider_timeout_s)
                    except StopAsyncIteration:
                        break
                    if not chunk:
                        continue
                    if first:
                        metrics.mark("tts_first_audio_ms")
                        # Local enqueue timestamp; phone acoustics/network not included.
                        metrics.mark("playback_start_ms")
                        first = False
                    for frame in framer.feed(chunk):
                        await self.frames.put((reply.reply_id, frame) if first_frame else frame)
                        first_frame = False
                if tail := framer.finish():
                    await self.frames.put(tail)
                if first:
                    raise RuntimeError("TTS returned no audio")
                await self.frames.put(correlation)
                await asyncio.wait_for(completed.wait(), 15)
                await self.status(reply.reply_id, "played")
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.on_error("tts_provider", type(exc).__name__, provider=name)
                if provider:
                    await provider.close()
                    self.providers.pop(name, None)
                if index + 1 == len(names):
                    raise
                await self._reset_output()
            finally:
                metrics.report(self.call_id, str(reply.reply_id))
                self.peer.forget_completion(correlation)

    async def _reset_output(self):
        self.renderer.cancel()
        await asyncio.gather(self.renderer, return_exceptions=True)
        while not self.frames.empty():
            self.frames.get_nowait()
        await self.peer.flush()
        if not self.closed:
            self.renderer = asyncio.create_task(self._render(), name="audio-output")

    async def interrupt(self):
        async with self.lock:
            active = self.active
            self.generation += 1
            if self.current:
                self.current.cancel()
                await asyncio.gather(self.current, return_exceptions=True)
            while not self.replies.empty():
                _, _, _, reply, _ = self.replies.get_nowait()
                await self.status(reply.reply_id, "interrupted")
                self.replies.task_done()
            await self._reset_output()
            return active

    async def close(self):
        self.closed = True
        try:
            await self.interrupt()
        finally:
            tasks = [self.worker, self.renderer] + ([self.current] if self.current else [])
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*(provider.close() for provider in self.providers.values()),
                                 return_exceptions=True)
            self.providers.clear()
