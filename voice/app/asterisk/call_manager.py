import asyncio
from collections import OrderedDict, deque
from uuid import uuid4

from app.chat import ChatStore
from app.audio.environment import Environment
import numpy as np

from app.audio.formats import FRAME_MS, Framer, SAMPLE_RATE, SILENCE, FramePacer, samples
from app.audio.latency import TurnMetrics, log_event
from app.audio.playback import Playback
from app.audio.recorder import Recorder
from app.audio.spike import run_spike
from app.audio.stt import make_stt
from app.audio.vad import EnergyVAD
from app.backend.control_ws import ControlWS
from app.domain.call import CallContext, CallStatus
from app.domain.messages import CallerReply, EnvironmentUpdate
from .ari_client import ARIClient
from .media_ws import MockPeer
from .topology import Topology


class CallRuntime:
    def __init__(self, manager, context, mode="auto", scenario_id=None):
        self.manager, self.context, self.settings = manager, context, manager.settings
        self.topology = Topology(manager.ari, self.settings, context.call_id) if manager.ari else None
        self.command_lock = asyncio.Lock()
        self.mode = mode
        self.scenario_id = scenario_id
        self.peers = {}
        self.media_ready = {role: asyncio.Event() for role in ("capture", "playback", "monitor")}
        self.framers = {role: Framer() for role in ("capture", "monitor")}
        self.input = asyncio.Queue(maxsize=250)
        self.stt_input = asyncio.Queue(maxsize=500)
        self.mock_input = asyncio.Queue(maxsize=500)
        self.replies = asyncio.Queue(maxsize=64)
        self.quiet = asyncio.Event()
        self.quiet.set()
        self.environment = Environment()
        self.recorder = Recorder(self.settings.recording_dir, context.session_id, context.call_id)
        backend_settings = self.settings.model_copy(update={"backend_mode": "mock"}) if mode != "auto" else self.settings
        self.backend = ControlWS(backend_settings, context, self.on_message, self.fail)
        self.backend.manual = mode != "auto"
        self.tasks = []
        self.turns = OrderedDict()
        self.playback = None
        self.capture_blocked_until = 0.0
        # Перебивание: кадры громкой речи во время ответа и признак открытого входа.
        self.barge_frames = deque(maxlen=max(1, (self.settings.barge_in_ms + self.settings.preroll_ms) // FRAME_MS))
        self.barge_loud = 0
        self.barge_open = False
        self.error = None
        self.recording_paths = None

    def fail(self, code, detail):
        if not self.context.stop.is_set():
            self.error = (code, detail)
            self.context.failed = True
            self.context.stop_reason = code
            self.context.stop.set()

    def spawn(self, coro, name):
        task = asyncio.create_task(coro, name=name)
        self.tasks.append(task)

        def check(done):
            if not done.cancelled() and done.exception():
                self.fail(name, type(done.exception()).__name__)

        task.add_done_callback(check)
        return task

    def attach(self, role, peer):
        if role in self.peers or self.context.stop.is_set():
            raise ValueError("Duplicate or late media connection")
        self.peers[role] = peer
        self.media_ready[role].set()

    def on_audio(self, role, data):
        if self.context.stop.is_set() or role == "playback":
            return
        try:
            for frame in self.framers[role].feed(data):
                self.recorder.put("operator" if role == "capture" else "caller", frame)
                if role == "capture" and self.context.ready.is_set() and self.playback:
                    # A speakerphone can feed the generated victim voice back into
                    # the microphone. In bot-controlled modes, do not turn that echo
                    # into a new operator turn. Manual mode keeps full duplex STT.
                    if not self.barge_open and self.mode != "manual" and (self.playback.active or
                            asyncio.get_running_loop().time() < self.capture_blocked_until):
                        if not self.barge_in(frame):
                            continue
                        for held in self.barge_frames:
                            self.input.put_nowait(held)
                        self.barge_frames.clear()
                        continue
                    self.input.put_nowait(frame)
        except Exception as exc:
            self.fail("audio_backpressure", type(exc).__name__)

    def barge_in(self, frame):
        """Громкая речь оператора дольше barge_in_ms прерывает ответ собеседника.

        Эхо динамика тише прямой речи в микрофон и отсекается порогом; короткие
        звуки (кашель, щелчок) не набирают нужной длительности.
        """
        if not self.settings.barge_in_ms:
            return False
        self.barge_frames.append(frame)
        rms = float(np.sqrt(np.mean(samples(frame) ** 2)))
        self.barge_loud = self.barge_loud + 1 if rms >= self.settings.barge_in_threshold else 0
        if self.barge_loud * FRAME_MS < self.settings.barge_in_ms:
            return False
        self.barge_loud = 0
        self.barge_open = True
        log_event("voice.barge_in", call_id=str(self.context.call_id))
        self.spawn(self.stop_playback(), "barge-in")
        return True

    async def emit_error(self, code, detail, **fields):
        log_event("voice.error", call_id=str(self.context.call_id), code=code, detail=detail)
        await self.backend.emit("voice.error", {"call_id": str(self.context.call_id),
                                                "code": code, "detail": detail, **fields})

    async def on_message(self, event):
        if event.session_id != self.context.session_id:
            raise ValueError("Wrong session")
        if event.type == "caller.reply":
            reply = CallerReply.model_validate(event.payload)
            if self.mode == "auto":
                await self.accept_reply(reply, "bot")
        elif event.type == "environment.update":
            # Validate before patch application, preserving omitted fields.
            EnvironmentUpdate.model_validate(event.payload)
            self.environment.update(event.payload)
        elif event.type == "call.hangup":
            self.context.stop_reason = "backend_hangup"
            self.context.stop.set()
        else:
            log_event("backend.unknown_event", type=event.type, call_id=str(self.context.call_id))

    async def accept_reply(self, reply, role):
        key = str(reply.reply_id)
        if key in self.context.seen_replies or self.context.stop.is_set():
            return
        if len(self.context.seen_replies) >= 4096 or self.replies.full():
            raise ValueError("Playback queue is full")
        self.context.seen_replies.add(key)
        self.manager.chat.message(self.context.call_id, key, role=role, text=reply.text, status="queued")
        self.replies.put_nowait(reply)

    async def playback_event(self, reply_id, status):
        self.manager.chat.message(self.context.call_id, reply_id, status=status)
        if self.mode == 'auto' and status in ('played', 'interrupted', 'error'):
            await self.backend.emit('caller.playback', {'reply_id': str(reply_id), 'status': status})
        if status == "playing":
            self.barge_frames.clear()
            self.barge_loud = 0
        if self.mode != "manual" and status in ("playing", "played", "interrupted", "error"):
            guard = self.settings.echo_guard_ms / 1000
            self.capture_blocked_until = max(self.capture_blocked_until,
                                             asyncio.get_running_loop().time() + guard)

    async def stop_playback(self):
        while not self.replies.empty():
            reply = self.replies.get_nowait()
            await self.playback_event(reply.reply_id, "interrupted")
        if self.playback:
            await self.playback.interrupt()

    async def _mock_microphone(self):
        pacer = FramePacer()
        while True:
            try:
                frame = self.mock_input.get_nowait()
            except asyncio.QueueEmpty:
                frame = SILENCE
            self.on_audio("capture", frame)
            await pacer.tick()

    async def setup(self):
        await self.recorder.start()
        await self.backend.start()
        if self.topology:
            await self.topology.originate(self.context.extension)
            await self.context.answered.wait()
            await self.topology.build()
            await asyncio.gather(*(event.wait() for event in self.media_ready.values()))
        else:
            self.context.status = CallStatus.ringing
            await asyncio.sleep(0.05)
            self.context.answered.set()
            self.peers["playback"] = MockPeer(lambda pcm: self.on_audio("monitor", pcm))
            for event in self.media_ready.values():
                event.set()
            self.spawn(self._mock_microphone(), "mock-microphone")
        self.context.status = CallStatus.active
        self.manager.chat.update(self.context.call_id, status="active")
        if self.settings.pipeline_mode == "conversation":
            self.playback = Playback(self.settings, self.peers["playback"], self.environment,
                                     self.quiet, self.emit_error, self.fail,
                                     call_id=str(self.context.call_id), on_status=self.playback_event)
            self.spawn(self._vad_loop(), "operator-vad")
            self.spawn(self._stt_loop(), "operator-stt")
            self.spawn(self._reply_loop(), "backend-replies")
        self.context.ready.set()
        await self.backend.emit("call.connected", {"call_id": str(self.context.call_id),
                                                   "extension": self.context.extension, "mode": self.mode,
                                                   "scenario_id": self.scenario_id})
        if self.settings.pipeline_mode == "spike":
            self.spawn(run_spike(self.peers["playback"]), "media-spike")

    async def run(self):
        setup_task = asyncio.create_task(self.setup(), name="call-setup")
        stopped = asyncio.create_task(self.context.stop.wait())
        try:
            done, _ = await asyncio.wait([setup_task, stopped],
                                         timeout=self.settings.connect_timeout_s,
                                         return_when=asyncio.FIRST_COMPLETED)
            if stopped in done:
                return
            if setup_task not in done:
                self.fail("call_setup_timeout", "Answer or media readiness timed out")
                return
            await setup_task
            try:
                await asyncio.wait_for(self.context.stop.wait(), self.settings.max_call_s)
            except TimeoutError:
                self.context.stop_reason = "max_duration"
        except asyncio.CancelledError:
            self.context.stop_reason = "service_shutdown"
        except Exception as exc:
            self.fail("call_setup_failed", type(exc).__name__)
        finally:
            self.context.stop.set()
            setup_task.cancel()
            stopped.cancel()
            await asyncio.gather(setup_task, stopped, return_exceptions=True)
            await self.cleanup()

    async def _vad_loop(self):
        vad = EnergyVAD(self.settings)
        utterance_id = None
        while True:
            frame = await self.input.get()
            result = vad.feed(frame)
            if result.started:
                utterance_id = str(uuid4())
                metrics = TurnMetrics(utterance_id, clock=lambda: self.context.elapsed_ms() / 1000)
                metrics.mark("vad_start_ms")
                self.turns[utterance_id] = metrics
                while len(self.turns) > 128:
                    self.turns.popitem(last=False)
                self.quiet.clear()
                self.manager.chat.message(self.context.call_id, utterance_id, role="me", status="recognizing")
                self.stt_input.put_nowait(("start", utterance_id, b""))
            if result.audio:
                self.stt_input.put_nowait(("audio", utterance_id, result.audio))
            if result.ended:
                # Реплика, начатая перебиванием, закончена: эхо-защита снова действует.
                self.barge_open = False
                self.turns[utterance_id].mark("vad_end_ms")
                self.stt_input.put_nowait(("end", utterance_id, b""))
                self.quiet.set()

    async def _stt_loop(self):
        provider = None
        fallback_used = False
        audio = bytearray()
        last_partial = ""
        timeout = self.settings.provider_timeout_s

        async def open_provider(name):
            nonlocal provider
            if provider:
                await provider.close()
            provider = make_stt(self.settings, name)
            await asyncio.wait_for(provider.open_stream(str(self.context.call_id), SAMPLE_RATE), timeout)

        async def recover(exc):
            nonlocal fallback_used
            await self.emit_error("stt_provider", type(exc).__name__)
            if fallback_used or not self.settings.stt_fallback_provider:
                raise RuntimeError("STT failed and no fallback is available") from exc
            fallback_used = True
            await open_provider(self.settings.stt_fallback_provider)
            if audio:
                await asyncio.wait_for(provider.push_audio(bytes(audio)), timeout)

        try:
            try:
                await open_provider(self.settings.stt_provider)
            except Exception as exc:
                await recover(exc)
            while True:
                kind, utterance_id, pcm = await self.stt_input.get()
                if kind == "start":
                    audio.clear()
                    last_partial = ""
                elif kind == "audio":
                    audio.extend(pcm)
                    try:
                        await asyncio.wait_for(provider.push_audio(pcm), timeout)
                        partial = await asyncio.wait_for(provider.get_partial(), timeout)
                        if partial and partial != last_partial:
                            log_event("stt.partial", call_id=str(self.context.call_id),
                                      utterance_id=utterance_id, characters=len(partial))
                            last_partial = partial
                            self.manager.chat.message(self.context.call_id, utterance_id, role="me", text=partial, status="recognizing")
                    except Exception as exc:
                        await recover(exc)
                elif kind == "end":
                    try:
                        final = await asyncio.wait_for(provider.get_final(), timeout)
                    except Exception as exc:
                        await recover(exc)
                        final = await asyncio.wait_for(provider.get_final(), timeout)
                    metrics = self.turns.get(utterance_id)
                    if metrics:
                        metrics.mark("stt_final_ms")
                    self.manager.chat.message(self.context.call_id, utterance_id, role="me", text=final.strip(), status="recognized" if final.strip() else "error")
                    if final.strip():
                        if metrics:
                            metrics.mark("backend_send_ms")
                        await self.backend.emit("operator.utterance", {
                            "call_id": str(self.context.call_id), "utterance_id": utterance_id,
                            "text": final.strip(), "is_final": True, "mode": self.mode})
                    if metrics:
                        metrics.report(str(self.context.call_id))
                    audio.clear()
        finally:
            if provider:
                await provider.close()

    async def _reply_loop(self):
        await self.context.ready.wait()
        while True:
            reply = await self.replies.get()
            metrics = self.turns.get(str(reply.utterance_id)) if reply.utterance_id else None
            metrics = metrics or TurnMetrics(str(reply.reply_id), clock=lambda: self.context.elapsed_ms() / 1000)
            metrics.mark("backend_reply_ms")
            await self.playback.submit(reply, metrics)

    async def cleanup(self):
        ctx = self.context
        errors = []

        async def attempt(label, operation):
            try:
                return await operation
            except Exception as exc:
                errors.append(label)
                log_event("cleanup.error", call_id=str(ctx.call_id), resource=label,
                          error=type(exc).__name__)

        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await attempt("stop_playback", self.stop_playback())
        if self.playback:
            await attempt("playback", self.playback.close())
        if self.topology:
            await attempt("asterisk", self.topology.close())
        for role, peer in self.peers.items():
            await attempt(f"media.{role}", peer.close())
        if self.recorder.task:
            self.recording_paths = await attempt("recording", self.recorder.finalize())
        ctx.failed |= bool(errors)
        ctx.status = CallStatus.failed if ctx.failed else CallStatus.ended
        if self.error:
            await attempt("error_event", self.emit_error(*self.error))
        if errors:
            await attempt("cleanup_error_event", self.emit_error("cleanup_failed", ",".join(errors)))
        await attempt("ended_event", self.backend.emit("call.ended", {
            "call_id": str(ctx.call_id), "reason": ctx.stop_reason, "status": ctx.status.value}))
        if self.recording_paths:
            await attempt("recording_event", self.backend.emit("recording.ready", {
                "call_id": str(ctx.call_id), "format": "wav", "sample_rate": SAMPLE_RATE,
                "paths": self.recording_paths,
                "alignment": "local_monotonic_receive_time"}))
        await attempt("backend", self.backend.close())
        self.manager.release(self)
        ctx.done.set()


class CallManager:
    def __init__(self, settings):
        self.settings = settings
        self.calls, self.sessions, self.channel_bindings, self.media_bindings = {}, {}, {}, {}
        self.history = OrderedDict()
        self.run_tasks = {}
        self.chat = ChatStore(settings.outbox_dir / "chat.sqlite3")
        self.ari = ARIClient(settings, self.on_ari_event, self.on_ari_disconnect) \
            if settings.telephony_mode == "asterisk" else None

    async def start(self):
        if self.ari:
            await self.ari.start()

    async def create(self, request):
        if (self.settings.telephony_mode == "asterisk"
                and self.settings.pipeline_mode == "conversation"
                and not self.settings.topology_verified
                and request.extension != self.settings.topology_probe_extension):
            raise ValueError("Unverified conversation is restricted to the topology probe extension")
        if request.extension not in self.settings.allowed_extensions.split(","):
            raise ValueError("Extension is not allowlisted for training calls")
        existing = self.sessions.get(str(request.session_id))
        if existing:
            runtime = self.calls[existing]
            if (runtime.context.extension != request.extension or runtime.mode != request.mode
                    or runtime.scenario_id != request.scenario_id):
                raise ValueError("Session already has a call with different parameters")
            return runtime.context.snapshot()
        if len(self.calls) >= self.settings.max_calls:
            raise OverflowError("Active call limit reached")
        if any(runtime.context.extension == request.extension for runtime in self.calls.values()):
            raise OverflowError("Training extension already has an active call")
        if self.ari and not self.ari.ready.is_set():
            raise ConnectionError("ARI event connection is unavailable")
        context = CallContext(request.session_id, request.extension, status=CallStatus.calling)
        runtime = CallRuntime(self, context, request.mode, request.scenario_id)
        self.chat.create(context, request.mode, request.scenario_id)
        key = str(context.call_id)
        self.calls[key] = runtime
        self.sessions[str(context.session_id)] = key
        if runtime.topology:
            for cid in runtime.topology.channel_ids:
                self.channel_bindings[cid] = runtime
            for role, cid in runtime.topology.media.items():
                self.media_bindings[cid] = (runtime, role)
        task = asyncio.create_task(runtime.run(), name=f"call-{key}")
        self.run_tasks[key] = task
        task.add_done_callback(lambda _: self.run_tasks.pop(key, None))
        return context.snapshot()

    async def on_ari_event(self, event):
        channel = event.get("channel", {})
        runtime = self.channel_bindings.get(channel.get("id"))
        if not runtime or runtime.context.stop.is_set():
            return
        ctx = runtime.context
        if channel.get("id") == str(ctx.call_id):
            if event["type"] == "ChannelStateChange" and channel.get("state") in ("Ring", "Ringing"):
                ctx.status = CallStatus.ringing
            if event["type"] == "StasisStart" and channel.get("state") == "Up":
                ctx.answered.set()
        if event["type"] in ("StasisEnd", "ChannelDestroyed"):
            if channel.get("id") == str(ctx.call_id):
                ctx.failed = not ctx.ready.is_set()
                ctx.stop_reason = "remote_hangup" if ctx.ready.is_set() else "not_answered"
                ctx.stop.set()
            else:
                runtime.fail("asterisk_media_ended", "Media or snoop channel ended")

    async def on_ari_disconnect(self):
        for runtime in list(self.calls.values()):
            runtime.fail("ari_disconnected", "ARI control connection lost")

    def release(self, runtime):
        key = str(runtime.context.call_id)
        self.chat.update(key, status=runtime.context.status.value,
                         reason=runtime.context.stop_reason)
        chat = self.chat.get(key)
        for message in chat["messages"]:
            if message["status"] in ("queued", "playing", "recognizing"):
                self.chat.message(key, message["id"], status="interrupted" if message["role"] != "me" else "error")
        self.history[key] = runtime.context.snapshot() | {"recordings": runtime.recording_paths}
        while len(self.history) > self.settings.history_limit:
            self.history.popitem(last=False)
        self.calls.pop(key, None)
        self.sessions.pop(str(runtime.context.session_id), None)
        if runtime.topology:
            for cid in runtime.topology.channel_ids:
                self.channel_bindings.pop(cid, None)
                self.media_bindings.pop(cid, None)

    def get(self, call_id):
        key = str(call_id)
        if runtime := self.calls.get(key):
            return runtime.context.snapshot()
        if key in self.history:
            return self.history[key]
        # Persistent terminal state survives process restarts and cache eviction.
        saved = self.chat.get(key)
        if saved:
            return {name: saved.get(name) for name in
                    ('call_id', 'session_id', 'status', 'reason')}
        return None

    async def hangup(self, call_id):
        if runtime := self.calls.get(str(call_id)):
            runtime.context.stop_reason = "api_hangup"
            runtime.context.stop.set()
            await asyncio.wait_for(runtime.context.done.wait(), 60)
        return self.get(call_id)

    async def close(self):
        for runtime in list(self.calls.values()):
            runtime.context.stop_reason = "service_shutdown"
            runtime.context.stop.set()
        await asyncio.gather(*list(self.run_tasks.values()), return_exceptions=True)
        if self.ari:
            await self.ari.close()
