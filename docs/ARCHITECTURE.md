# Architecture

Live dialogue has a separate local `PHONE_LLM_MODEL` from the authoring profile.
Both use the existing Ollama adapter with CPU-only `num_gpu=0`; no new service.
Phone requests use context4096, at most120 output tokens, bounded recent history
and the original scenario/current report facts. Oversized input fails to the
existing grounded fallback rather than silently dropping facts. All system
instructions, including role repair, are retained. Fifteen seconds bounds model
wait including a 112 role retry, but not speech recognition/synthesis/playback.
Startup warms the actual configured host/model/options; weights stay resident.

Silero synthesis uses a bounded per-event-loop/model warm process pool (default
two workers, two CPU threads each). It remains inside Voice, not a service split.
Workers are returned before playback consumes PCM; cancellation replaces only the
owned worker. An 8 MiB/128-entry memory-only LRU avoids repeated phrase inference.
The gateway closes the pool explicitly. Uvicorn remains single-worker.

26 September: call recovery reads durable terminal snapshots from the existing
Voice ChatStore; no new persistence system or PostgreSQL change. ARI reconnects
with bounded backoff. Backend-owned redial preserves the logical session/briefing;
the last saved answer can be replayed, but unreceived audio cannot be recovered.
Prepared Card recipient_affiliations explicitly bind area/district/department to
destinations; rules merge their provenance with the existing local directory.
No invented geographic inference or external directory lookup is introduced.

Document ingestion stays in Backend: bounded extraction/OCR on save, revision-local
JSON passages and lexical retrieval with local morphology. No external AI ingestion,
vector database, broker or new service. Tesseract is a bounded local subprocess.
Material mutations run in FastAPI's worker pool and recheck revision/quota before
writing after extraction. Speech workers use UTF-8 and model-local paths on Windows.

Local territorial routing is a bounded, explicitly approved file-based directory
inside Backend, not a new service or database. Matching uses exact existing Card
address/object fields; future issued cards freeze recipients. No external lookup.
Manual grammar checks reuse the deterministic module without changing grades.
Material/workstation XML imports are client-side previews followed by the existing
validated JSON save APIs; they never create roles or bypass publication checks.

## Security extensions and test directory decision

Request audit remains Backend middleware; files are on the existing operations
bind mount, with no new logging service. LDAP verification is optional and proves
credentials only. Local directory_links explicitly attach identities to existing
roles. An isolated OpenLDAP container is a user-requested synthetic test fixture,
not a business microservice or enterprise directory. It exposes only LDAPS inside
Compose and uses separately generated local certificates. No new cluster broker,
Redis is introduced. The later cluster ADR adds HAProxy and PostgreSQL-backed
aggregate leases on the same connection as writes; only that bounded two-replica
profile permits active-active Backend. See CLUSTER.md and the cluster ADR.

## Local operations boundary (2026-09-15)

The admin operations module stays inside Backend. A local host worker
(`deploy/ops_worker.py`) owns Docker monitoring/service-control access; no Docker socket, host
shell, secret editor or arbitrary command API is exposed to the web containers.
An explicit shared operations directory carries atomic snapshots and allowlisted
jobs. Voice/Asterisk start/stop/restart, validated technical configuration and
host-approved offline image updates remain host-executed. A separate no-socket
Compose helper performs direct TLS PostgreSQL dump plus encrypted mounted-data
backup and owns daily/manual backup jobs. Neither helper is a business microservice. RBAC and service
authentication guard every API. Snapshots older than90 seconds disable commands;
queued commands expire rather than executing unexpectedly after a long outage.
Host administrators must protect the shared directory as a control boundary.
The web panel cannot alter credentials or submit arbitrary commands/images.
Configuration/update maintenance can recreate Backend/PostgreSQL through the
host worker; cluster updates bootstrap one Backend before scaling to two.
Database promotion remains a host-only, fenced maintenance operation.

## Container deployment decision (2026-09-15)

Root Compose packages the existing Frontend/Backend/Voice boundaries, adds the
required PostgreSQL database and isolated Asterisk runtime, and introduces no new
business microservice. Backend DATABASE_URL selects PostgreSQL; SQLite remains for
native development/mock tests. Portable SQL preserves account/card ownership and
transaction boundaries. Each Backend/Voice container retains one worker; the
optional cluster profile runs two coordinated Backend containers.
The delivered contour serves the LLM from a local Ollama over `OLLAMA_URL`;
OpenRouter stays an ENV-selected development egress and is not enabled in delivery.
Persistent named volumes hold PostgreSQL and Voice recordings/outbox. Runtime
credentials are excluded from image contexts; ARI/Backend/Voice/DB are not published.
SIP media requires a routable advertised host address and separate physical testing.
See DOCKER_DEPLOYMENT.md. Historical SQLite-only descriptions below are superseded.

Group SIP reuses the existing session-scoped Backend -> Voice calls. Lessons store
teacher-assigned extension mappings, frozen into issued fill cards; no new service
or transport is introduced. Voice allows one active session per training extension.
Teacher live snapshots are read-only aggregations in Workspace, polled by the BFF
client; they report persisted activity rather than online presence. Historical
text-only group descriptions below are superseded by this addition.

`assessment.py` keeps teacher-defined policies/history and append-only expert
decisions in the existing SQLite store. Workspace freezes policies alongside
private scenario/rubric snapshots and computes policy_result once on completion.
Expert decisions use separate records and never rewrite completed workspace JSON;
statistics join the latest decision to owned attempts at read time. No LLM, queue
or analytics service is introduced. See ASSESSMENT.md for exact sequence semantics.
`group_insights.py` derives a privacy-bounded aggregate from those persisted group
attempts and reuses the existing LLM adapter only on an explicit teacher request.
Strict evidence/scenario references bind output to server data. Successful results
are fingerprint-cached in SQLite and can be labeled stale; they never mutate
assignments or grades. See GROUP_INSIGHTS.md.

`curriculum.py` defines pedagogical difficulty/profile metadata, independent of
classifier routing and assessment. Workspace enforces lesson filters and freezes
metadata with scenario snapshots. `materials.py` stores teacher-owned reference
metadata/text and opaque file BLOBs in the same SQLite process, with group-scoped
publication, revision checks and bounded uploads. Frontend uses the existing
authenticated JSON proxy; attachment downloads are binary Blobs. Bounded extraction,
OCR and source-linked retrieval run inside Backend on save; no new service is used.

`service_workflow.py` separates incident/response states from training lifecycle.
Card saves preserve notified services and append resolved routing selections.
Synthetic telephone logs, directed own-card links and processed markers persist in
workspace JSON/audit under existing mutation locks. Marking processed does not call
the finalizer. See ARM_COVERAGE.md for source boundaries and API details.

## Student workspace (current development path)

Incident coordinates are nullable paired WGS84 values on Card, independent from
the caller's address. The separate read-only `/map` page reads the same owner-scoped
session API and optional local XYZ PNG tiles under `assets/map-tiles`; there is no
third-party geocoder, location permission or map API key. Missing map assets are
explicitly reported, not replaced by fabricated geography. Registration metadata
freezes the authenticated display name and configured teaching workstation label.

Browser -> Frontend :3000 (same-origin proxy) -> Backend :8000 -> Voice :8001.
Student endpoints use `/api/v1/student`. Frontend never obtains the provider
or Voice credentials. Backend `llm.py` selects mock/OpenRouter/Ollama through ENV;
the existing dialogue engine is shared by REST text turns and Voice WebSocket.
Per-session locks serialize dialogue mutations. A frozen scenario snapshot feeds
the prompt; this is prompt-based fact control, not a formal semantic guarantee.

`workspace.py` owns cards, revisions, service actions and audit events in the
existing SQLite store. Optimistic revision checks prevent stale card overwrites.
PostgreSQL and the network deployment are in place; see `docs/DOCKER_DEPLOYMENT.md`
and `docs/CLUSTER.md`. OpenRouter is explicitly selected for development only and
requires Internet access, so it is off in the delivered isolated contour.
The optional browser speech path uses OS/browser synthesis; SIP retains Voice's
existing STT/TTS providers. Only synthetic scenarios may be used.

Classifier data is a versioned generated JSON catalog owned by Backend. Its
three feature levels retain the source workbook's ordering. Frontend filters
dependent choices locally; Backend validates the selected record and canonicalizes
its final type. Original cards remain readable. No inference is used to invent
service-routing rules from ambiguous spreadsheet columns. `routing.py` evaluates
the mapped full-v2 columns N:CU using card flags, without an LLM or new
service. Preview is pure; card saves freeze its provenance and rules version.
Suggestions do not replace the student's selection or contact real agencies.

`evaluation.py` validates configurable criterion rubrics and evaluates completed
cards deterministically. Rubrics and their revisions live in the same SQLite
store; the selected revision is frozen privately at session creation. Reports are
stored once at completion and remain immutable. The `/instructor` editor requires
a teacher identity; rubric keys are scoped by teacher and scenario.
Evaluation currently covers exact text, literal fragments, flags and service sets;
it does not claim neural semantic/grammar analysis or official regulatory grading.
`ai_review.py` is a separate bounded advisory pass through the existing LLM adapter.
It uses a strict output schema, literal quote checks and server-resolved reference
IDs. The analysis is not a grading authority. Completed reviews persist alongside
the original evaluation; per-session locks deduplicate requests in this process.
Provider/schema failures are sanitized and retryable, with no change to grades.

## Legacy voice console

The description below records the original voice topology. Its global call/history
proxy is now blocked in Frontend to prevent bypassing classroom ownership. Voice
itself is unchanged; authenticated students use session-scoped Backend SIP routes.

The browser talks only to the local web-console proxy on port 8002; service tokens
never enter browser JavaScript. The proxy reads scenarios from Backend and controls
calls through Voice. Backend owns scenario state and the victim dialogue. Voice owns
telephony, media, STT/TTS, call chat history and the External AI transport API.

Automatic mode: Browser -> web console -> Voice -> Backend -> Voice -> Asterisk.
Manual mode: Browser -> web console -> Voice -> Asterisk. External mode uses an
authenticated server-to-server client -> Voice. Control WebSocket and Asterisk media
WebSockets remain separate.

Target Asterisk is 22.x. Verify `chan_websocket` and `ExternalMedia transport_data` support against the deployed patch version.

## Classroom identity boundary

AI authoring lives in `generation.py` within Backend and stores private draft JSON
in `generation_drafts` in the same SQLite database. Per-teacher async locks serialize
generation/revision/approval; no transaction spans an LLM request. A successful
request UUID deduplicates initial generation; revision checks prevent stale approval.
Approval inserts scenario, ownership, rubric, rubric history and approved draft in
one transaction. Model failures never overwrite the previous preview. Draft history
and approval identity are retained; nothing automatically enters a lesson.
The existing LLM adapter is reused without new services. Only synthetic authoring
inputs leave the machine with OpenRouter. JSON/schema/literal-reference validation
is a guardrail, not proof of truth or official 112 compliance. Human review is required.

`accounts.py` owns users, scrypt hashes, hashed opaque sessions (8-hour expiry),
login throttling and audit in the same SQLite database. Bootstrap creates only the
first admin; admin creates teacher/student accounts. Blocking revokes sessions.
Frontend reads the HttpOnly SameSite=Strict cookie and injects `X-User-Session`
alongside its service Bearer token; it never trusts a browser-supplied identity header.
Native loopback may use HTTP; the deployment TLS profile supplies HTTPS/internal TLS.
MFA is not imposed by the clarified scope; password recovery remains administrative.

`learning.py` owns teacher groups, memberships and assignments. New sessions freeze
student/teacher/group/assignment ownership. Student routes enforce owner identity;
teacher session inspection enforces the frozen teacher. Deactivating assignments
prevents new starts, without deleting previous attempts. Unowned legacy sessions
remain inaccessible through user APIs. Teacher rubrics and authored scenarios are
isolated; preexisting scenarios are shared read-only templates. No extra service
or infrastructure dependency is introduced.

The `/scenarios` teacher editor reuses existing scenario CRUD and validation.
Read responses add server-derived editability; writes still enforce ownership and
strict Scenario input. Copy creates a new teacher-owned scenario, not a rubric or
assignment. Disabling affects new starts only; dialogue snapshots stay unchanged.
Scenario edit versions hash canonical sorted JSON. PUT checks the current hash before
synchronous persistence, without an await between read and write in this single-process
deployment. Version metadata stays outside scenario facts and needs no DB migration.

Teacher feedback is owned by workspace, with the same frozen teacher ownership
check. Appending notes has no asynchronous boundary between read and persistence
in this single-process service. Notes are separate from caller messages and grading;
student polling merges only feedback, never overwriting an unsaved card draft.

Student and teacher completion share a finalizer with a per-session workspace write
lock, separate from the Engine dialogue lock. Writes queue behind an in-flight
dialogue/call request; finalization waits for SIP hangup and dialogue termination.
The last persisted card is graded once. Failed Voice hangup leaves the session active.
The browser detects remote completion and freezes its UI. Unsaved draft fields are
kept in user/session-scoped browser sessionStorage as a separate JSON-export field,
not scored or persisted to Backend.

Group lessons live in a separate `lessons` table within the same SQLite store.
Workspace owns their lifecycle and reuses card creation/finalization, not a new
service. A lesson lock serializes start/next/stop. Stop acquires individual card
mutation locks after setting stopping, blocking further issuance. Active members
and fill scenario/rubric snapshots are frozen at start. Legacy running lessons
without these snapshots retain their previous per-card snapshot behavior.
The bounded random pool allows repeats; no stored card queue or LLM scheduler is
needed. Next requests reuse an active card and cannot exceed a finite student limit;
null allows progression until stop. A predecessor ID makes successor retries
idempotent. The visible student UI polls lesson state every two seconds and keeps
the joined lesson ID in user-scoped sessionStorage for reload recovery.

Action/mixed lessons reuse completed teacher-owned Card snapshots as private templates.
Only Card and caller scenario are retained, not source account/transcript/assessment.
Each issued copy has independent ownership, initial_card, actions and audit. Caller
generation is disabled; completion records changed fields and service-action count.
No original rubric score is reused or awarded for prefilled data. Original sessions
and their grades are never mutated. Deterministic scenario templates copy only
known caller name/location/incident; AI generation remains separate work. Category
labels constrain the pool without changing the incident classifier. Group reports
aggregate persisted cards and audit events; no separate queue or report service.
