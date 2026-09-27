from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class EventEnvelope(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    seq: int = Field(ge=1)
    session_id: UUID
    type: str
    elapsed_ms: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class VoiceStyle(BaseModel):
    emotion: str | None = None
    # Голос собеседника: начальник смены, бригада и заявитель звучат по-разному.
    speaker: str | None = Field(None, pattern=r'^[a-z_]{1,32}$')
    rate: float = Field(1, ge=0.5, le=2)
    intensity: float = Field(0.5, ge=0, le=1)


class CallerReply(BaseModel):
    reply_id: UUID
    text: str = Field(min_length=1, max_length=10000)
    voice_style: VoiceStyle = Field(default_factory=VoiceStyle)
    should_interrupt: bool = False
    # Optional correlation, negotiated with Backend. Without it timings cannot
    # reliably associate out-of-order replies with operator utterances.
    utterance_id: UUID | None = None


class EnvironmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    noise_type: Literal["none", "road", "crowd", "wind", "alarm", "indoor"] = "none"
    noise_level: float = Field(0, ge=0, le=1)
    connection_quality: Literal["good", "medium", "bad"] = "good"
    dropout_probability: float | None = Field(None, ge=0, le=1)
    volume_multiplier: float = Field(1, ge=0, le=2)


class CreateCall(BaseModel):
    mode: Literal["manual", "auto", "external"] = "auto"
    session_id: UUID
    extension: str = Field("201", pattern=r"^[0-9]{1,8}$")
    scenario_id: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
