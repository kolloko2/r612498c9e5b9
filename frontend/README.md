# Local web console

Russian operator UI and server-side proxy on port 8002. It keeps Voice and Backend
tokens on the server, streams the chat to the browser over SSE, supports manual,
automatic and External AI modes, scenario construction, history, and TXT/JSON export.

```powershell
python -m pip install -r requirements.txt
$env:API_TOKEN='local-voice-token'
$env:BACKEND_TOKEN='local-dialogue-token'
python -m uvicorn server:app --host 127.0.0.1 --port 8002
```
