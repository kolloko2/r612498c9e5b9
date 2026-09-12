import asyncio
import importlib
import sys


async def stop_process(process):
    if process and process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 2)
        except TimeoutError:
            process.kill()
            await process.wait()


def custom_provider(spec, settings):
    # This is administrator configuration, never accepted in REST/backend data.
    module, factory = spec.removeprefix("python:").rsplit(":", 1)
    return getattr(importlib.import_module(module), factory)(settings)


async def spawn_worker(kind, model):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.audio.provider_worker", kind, model,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
