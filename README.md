# 112 AI Trainer

Bootstrap monorepo for an AI training simulator for emergency dispatch operators.

License: not specified

## Architecture

Frontend talks only to Backend. Backend owns Scenario/Fact/Event world state, disclosure, sessions and evaluation. Voice owns telephony, media, STT/TTS and Asterisk integration, but not business logic. LLM is not source of truth.

Flow: Frontend -> Backend -> Voice -> Asterisk. Control and media WebSockets are separate.

| Service | Port | Owner |
|---|---:|---|
| Backend | 8000 | backend developer |
| Voice | 8001 | voice developer |
| Frontend | 3000 | frontend developer |
| Asterisk | 8088 | voice/integration |

```bash
cp .env.example .env
docker compose up --build
```

Read `AGENTS.md` before changes. See `docs/` for project, architecture, contracts, scenario, integration, security and demo documentation.
