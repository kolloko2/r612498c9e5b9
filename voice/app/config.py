from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    telephony_mode: Literal["mock", "asterisk"] = "mock"
    pipeline_mode: Literal["spike", "conversation"] = "spike"
    topology_verified: bool = False
    topology_probe_extension: str = ""
    backend_mode: Literal["mock", "websocket"] = "mock"
    backend_url: str = "ws://backend:8000/ws/v1/voice/sessions/{session_id}"
    backend_token: SecretStr = SecretStr("")
    backend_retries: int = Field(4, ge=0, le=10)
    backend_backoff_s: float = Field(0.5, gt=0, le=10)
    backend_reconnect_budget_s: float = Field(30, ge=0, le=300)
    api_token: SecretStr = SecretStr("")
    ari_url: str = "http://asterisk:8088/ari"
    ari_username: str = "voice"
    ari_password: SecretStr = SecretStr("")
    ari_app: str = "voice-gateway"
    internal_ca_file: Path | None = None
    media_connection: str = "voice-media"
    media_username: str = "asterisk"
    media_password: SecretStr = SecretStr("")
    allowed_extensions: str = "201"
    connect_timeout_s: float = Field(40, gt=0, le=180)
    provider_timeout_s: float = Field(20, gt=0, le=120)
    max_call_s: int = Field(1800, ge=5, le=14400)
    max_calls: int = Field(8, ge=1, le=100)
    history_limit: int = Field(100, ge=1, le=10000)
    recording_dir: Path = Path("recordings")
    outbox_dir: Path = Path("outbox")
    security_audit_dir: Path | None = None
    stt_provider: str = "mock"
    stt_fallback_provider: str = ""
    stt_api_key: SecretStr = SecretStr("")
    stt_model: str = ""
    stt_final_model: str = ""
    stt_language: str = "ru"
    tts_provider: str = "mock"
    tts_fallback_provider: str = ""
    tts_api_key: SecretStr = SecretStr("")
    tts_voice: str = ""
    tts_speaker: str = "baya"
    tts_fallback_voice: str = ""
    vad_threshold: float = Field(0.025, gt=0, lt=1)
    vad_start_ms: int = Field(60, ge=20, le=200)
    vad_end_ms: int = Field(400, ge=100, le=2000)
    preroll_ms: int = Field(200, ge=100, le=1000)
    utterance_limit_s: int = Field(30, ge=1, le=120)
    echo_guard_ms: int = Field(1400, ge=200, le=5000)

    @model_validator(mode="after")
    def validate_live(self):
        if self.preroll_ms < self.vad_start_ms:
            raise ValueError("PREROLL_MS must cover VAD_START_MS confirmation")
        if self.telephony_mode == "asterisk":
            for name in ("ari_password", "media_password", "api_token"):
                if not getattr(self, name).get_secret_value():
                    raise ValueError(f"{name.upper()} must be set for Asterisk mode")
            if (self.pipeline_mode == "conversation" and not self.topology_verified
                    and not self.topology_probe_extension):
                raise ValueError("Run the live media spike first, then set TOPOLOGY_VERIFIED=true")
            if self.topology_probe_extension and not self.topology_probe_extension.isdigit():
                raise ValueError("TOPOLOGY_PROBE_EXTENSION must be numeric")
        return self
