# Project rules

Read `README.md`, `docs/ARCHITECTURE.md`, `docs/API_CONTRACT.md` and the relevant service README before changes.

- API changes update `docs/API_CONTRACT.md` plus `shared/api/openapi.yaml` or `asyncapi.yaml` together.
- Do not add Kafka, Kubernetes, RabbitMQ, Redis, vector DB or new microservice splits without an architecture decision.
- Every service has mock mode; secrets are ENV-only; real 112 data is forbidden.
- Do not invent official 112 regulations.
- Backend :8000, Voice :8001, Frontend :3000; REST `/api/v1`; WebSocket `/ws/v1`.
- Before completion: applicable tests/lint/typecheck, secret check, README update.

This file is the source of truth for AI coding rules.
