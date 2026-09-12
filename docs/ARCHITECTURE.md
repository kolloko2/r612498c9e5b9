# Architecture

The browser talks only to the local web-console proxy on port 8002; service tokens
never enter browser JavaScript. The proxy reads scenarios from Backend and controls
calls through Voice. Backend owns scenario state and the victim dialogue. Voice owns
telephony, media, STT/TTS, call chat history and the External AI transport API.

Automatic mode: Browser -> web console -> Voice -> Backend -> Voice -> Asterisk.
Manual mode: Browser -> web console -> Voice -> Asterisk. External mode uses an
authenticated server-to-server client -> Voice. Control WebSocket and Asterisk media
WebSockets remain separate.

Target Asterisk is 22.x. Verify `chan_websocket` and `ExternalMedia transport_data` support against the deployed patch version.
