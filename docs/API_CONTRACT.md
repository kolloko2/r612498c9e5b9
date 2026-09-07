# API Contract

REST prefix: `/api/v1`; control WebSocket prefix: `/ws/v1`.

Backend REST includes health, scenario CRUD/validation, session create/start/finish, mock operator message, report, transcript, events and recording. Voice REST includes health, call create/status/hangup.

Control endpoint: `/ws/v1/voice/sessions/{session_id}`. Messages use `EventEnvelope`: `event_id`, `seq`, `session_id`, `type`, `elapsed_ms`, `payload`. Binary Asterisk media is a separate Voice/Asterisk protocol.

Machine-readable contracts are `shared/api/openapi.yaml` and `shared/api/asyncapi.yaml`.
