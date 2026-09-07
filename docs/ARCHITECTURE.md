# Architecture

Frontend -> Backend -> Voice -> Asterisk. Frontend uses only Backend REST/control WebSocket. Backend owns world state, disclosure, orchestration and evaluation. Voice owns telephony/media adapters. Control WebSocket and Asterisk media WebSocket are separate.

Target Asterisk is 22.x. Verify `chan_websocket` and `ExternalMedia transport_data` support against the deployed patch version.
