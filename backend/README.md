# Backend

Local scenario and victim-dialogue service on port 8000. It stores scenario snapshots
and conversation history in SQLite, validates the victim role, and calls a local
Ollama model only in automatic mode.

```powershell
python -m pip install -r requirements.txt
python -m uvicorn server:app --host 127.0.0.1 --port 8000
python -m pytest test_dialogue.py -q
```

Set `DIALOGUE_TOKEN`, `DIALOGUE_DB`, and `DIALOGUE_MODEL` through environment
variables. No real emergency-service integration is included.
