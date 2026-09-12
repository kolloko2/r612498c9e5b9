import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import calls, health, chat, integration
from app.asterisk.call_manager import CallManager
from app.asterisk.media_ws import router as media_router
from app.config import Settings
from app.audio.tts import preload_tts


def create_app(settings=None):
    cfg = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        manager = CallManager(cfg)
        app.state.manager = manager
        try:
            await manager.start()
            await preload_tts(cfg)
            yield
        finally:
            await manager.close()

    app = FastAPI(title="Training Voice Gateway", version="0.1.0", lifespan=lifespan)
    app.include_router(integration.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(calls.router, prefix="/api/v1")
    # Short aliases from the requirements. Canonical documented API is /api/v1.
    app.include_router(calls.router, include_in_schema=False)
    app.include_router(media_router)
    return app


app = create_app()
