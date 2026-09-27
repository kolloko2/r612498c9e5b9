import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import calls, health, chat, integration
from app.asterisk.call_manager import CallManager
from app.asterisk.media_ws import router as media_router
from app.config import Settings
from app.audio.tts import preload_tts, shutdown_tts
from app.security_audit import VoiceAuditLog, VoiceAuditMiddleware


def create_app(settings=None):
    cfg = settings or Settings()
    audit = VoiceAuditLog(cfg.security_audit_dir)

    async def archive_loop():
        while True:
            try:
                await asyncio.to_thread(audit.archive)
            except OSError:
                logging.error("VOICE_AUDIT_ARCHIVE_FAILED")
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(app):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        manager = CallManager(cfg)
        app.state.manager = manager
        archive_task = asyncio.create_task(archive_loop(), name="voice-audit-archive")
        try:
            await manager.start()
            await preload_tts(cfg)
            yield
        finally:
            archive_task.cancel()
            await asyncio.gather(archive_task, return_exceptions=True)
            try:
                await manager.close()
            finally:
                await shutdown_tts()

    app = FastAPI(title="Training Voice Gateway", version="0.1.0", lifespan=lifespan)
    app.state.security_audit = audit
    app.add_middleware(VoiceAuditMiddleware, audit=audit)
    app.include_router(integration.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(calls.router, prefix="/api/v1")
    # Short aliases from the requirements. Canonical documented API is /api/v1.
    app.include_router(calls.router, include_in_schema=False)
    app.include_router(media_router)
    return app


app = create_app()
