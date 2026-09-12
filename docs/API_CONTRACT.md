# API Contract

REST prefix: `/api/v1`; control WebSocket prefix: `/ws/v1`.

Backend REST currently includes health and scenario CRUD/validation. Voice REST
includes health, call create/status/hangup, persistent chat, manual speech playback,
and the authenticated External AI endpoints under `/api/v1/integration`.

Control endpoint: `/ws/v1/voice/sessions/{session_id}`. Messages use `EventEnvelope`: `event_id`, `seq`, `session_id`, `type`, `elapsed_ms`, `payload`. Binary Asterisk media is a separate Voice/Asterisk protocol.

Machine-readable contracts are `shared/api/openapi.yaml` and `shared/api/asyncapi.yaml`.

External AI starts a call in `external` mode, reads ordered SSE events, submits the
victim's exact text for synthesis, stops playback, and ends the call. Every message is
bound to `session_id`, `call_id`, and a caller-supplied idempotent `message_id`.
