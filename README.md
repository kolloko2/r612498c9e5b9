# 112 AI Trainer

Working local simulator for training emergency dispatch operators. The operator uses
MicroSIP extension 201; the simulated victim is controlled manually, by the local
scenario model, or through the authenticated External AI API.

License: not specified

## Architecture

Frontend talks only to Backend. Backend owns Scenario/Fact/Event world state, disclosure, sessions and evaluation. Voice owns telephony, media, STT/TTS and Asterisk integration, but not business logic. LLM is not source of truth.

Flow: Frontend -> Backend -> Voice -> Asterisk. Control and media WebSockets are separate.

| Service | Port | Owner |
|---|---:|---|
| Backend | 8000 | backend developer |
| Voice | 8001 | voice developer |
| Local web console | 8002 | frontend/BFF |
| Asterisk | 8088 | voice/integration |

The installed Windows/WSL stand is documented in `docs/Запуск-и-доступность.md`.
For isolated Voice development and mock tests, see `voice/README.md`.

Read `AGENTS.md` before changes. See `docs/` for project, architecture, contracts, scenario, integration, security and demo documentation.
