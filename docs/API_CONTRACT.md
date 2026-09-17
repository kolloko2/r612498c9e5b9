# API Contract

REST prefix: `/api/v1`; control WebSocket prefix: `/ws/v1`.

## Offline regional map

Student/service-authenticated GET routes on the existing Backend, proxied by BFF:

- `/student/map/manifest`: region title, OSM provenance, prepared_at, bbox
  `[south,west,north,east]`, feature and address counts.
- `/student/map/features?west=&south=&east=&north=&zoom=`: finite ordered WGS84
  viewport bounds, integer zoom 2..19; `{features:[{kind,name,coordinates}],truncated}`.
  At most 8,000 features with zoom-dependent detail; a truncated response requests
  a closer zoom. Invalid bounds 422, absent package/read timeout 503.
- `/student/map/search?q=`: 2..160 character address query, up to 30
  `{label,latitude,longitude}` entries in `results`; empty results are not errors.

Read-only `MAP_DATA_DIR/regional.sqlite` contains public cartography, not classroom
data. Runtime never calls external geocoders or map providers. Address selection
still requires operator confirmation and a normal card save; no session mutation
or external service notification is performed by these GET routes.

## Request audit and external directory identities

GET `/admin/audit?day=YYYY-MM-DD&limit=100` requires admin and service credentials.
Limit1..200, default UTC today. Response: enabled, failed, minimum_retention_days183,
automatic_deletion:false, days, selected day, events. Events contain request_id,
at(epoch), method, phase(started/finished); finished adds registered route template,
status, actor_id/role when identified, elapsed_ms. No payload/query/header/path values.
Audit persistence disabled only when SECURITY_AUDIT_DIR is absent (native mock).
When enabled, a failed initial append blocks the HTTP request with503. Failure to
append completion never replays the already executed action. Health is excluded.
Backend WS connection metadata is also audited. Voice has a separate HTTP/WS
journal selected through `source=voice`; the default is `source=backend`.
Media contents and unsaved UI actions are outside this metadata audit.

GET `/auth/directory-status` returns configured:boolean (service auth only).
POST `/auth/directory-login` takes username and password(1..128), issuing the same
user/session response as local login; BFF still hides the token in an HttpOnly cookie.
Only explicitly linked active non-admin users can log in; no LDAP role provisioning.
Missing config503, credentials/link/blocked401, concurrent directory login429.
The existing account failure throttle also applies. Local login remains available.
Admin GET `/admin/directory-links` returns user_id/username entries.
Admin POST `/admin/users/{uid}/directory` takes `{username}` and replaces that user's
link; missing404, linking admin403, another user's existing directory identity409.
Username is3..40 safe ASCII letters/digits/_.- and normalized lowercase.
Links persist in directory_links; link changes and login success are audited.

CSV statistics and sanitized XML configuration are client exports of authorized
snapshots; they add no public data API and no XML import/parser.

## Technical administration

All `/admin/operations` routes require service Bearer and an active admin session;
teachers/students403, unauthenticated401. GET returns
`{enabled,stale,backup_available,backup_worker,status,settings,events,jobs}`. Service
and host metrics remain a host-worker snapshot, not a live Docker command. Backup
availability, manifest and job results are merged from the Docker backup worker's
heartbeat/result files, so backup does not depend on the Windows worker.
Missing/older-than90s/future samples are stale. Events include last100 bounded
operational/account-audit records, not unfiltered application logs or secrets.
POST `/admin/operations/jobs` accepts `{action:backup|start|stop|restart,
service:voice|asterisk|null}`; backup must have no service, other actions require
one. Returns202 with queued id, actor_id, created_at and command. Only one pending
request is admitted (409), including a request atomically claimed by the backup
container. Backup submission needs a fresh backup-container heartbeat; service
operations still require the host worker. Acceptance does not imply
execution success; poll jobs for the result. Stop/restart can interrupt calls.
PATCH `/admin/operations/settings` replaces
`{backup_enabled:boolean,backup_hour_utc:integer0..23}` and appends a settings audit
file. Defaults daily enabled at00:00 UTC. This is UTC, not browser local time.
No credentials, arbitrary service/path/command, backup deletion or restore API.
The backup container connects directly to PostgreSQL using TLS `verify-full`; no
Docker socket, backup encryption key, database password or private key is returned.

Container deployment does not change REST/WebSocket schemas. Backend DATABASE_URL
selects PostgreSQL while the same roles, revisions and service authentication apply.
Frontend ALLOWED_ORIGINS configures exact trusted origins for a private deployment;
unknown Hosts/Origins and browser-supplied identity headers remain rejected.
COOKIE_SECURE enables Secure login cookies when HTTPS is configured. Internal
service ports are not published by the root Compose. See DOCKER_DEPLOYMENT.md.

## ARM coordinates and registration

Card adds nullable `latitude` (-90..90), `longitude` (-180..180), finite WGS84
numbers. Both must be present or both null (otherwise422). Defaults are null;
zero is valid. These describe the incident point, not an inferred address or
student/device location. Saving uses the existing revision/ownership/audit rules.
New boolean card flags `emergency`, `important`, `bookmarked` default false and
are independent of `injured`; they do not add routing rules or official grading.
New sessions freeze `registration:{operator,workstation}` from authenticated
display name and server `TRAINING_WORKSTATION_ID` (default `Учебное АРМ`). Clients
cannot set registration through Card. Historical records may omit it.
Frontend `/map?sid=UUID` loads only the owner's existing session route; no new
API permission or external map service is introduced. Local XYZ PNG tiles are
optional; absence is explicitly displayed and no streets are fabricated.

## Group SIP and live teacher view

This section supersedes historical text-only lesson descriptions below.
CreateLesson adds `transport: text|sip` (default text), and `sip_extensions`:
an object mapping group student IDs to distinct strings of 1–8 ASCII digits,
maximum 100 entries. Non-members, duplicate numbers, text with mappings and
actions-only with SIP return422. At start, every active member needs a mapping
(otherwise409). Numbers must already be provisioned in Asterisk and Voice's ENV
allowlist; this API does not configure telephony. Older lessons remain text.
Student lesson summaries expose transport and only their own `sip_extension`.
Fill cards freeze their student's number; mixed action cards always remain text.
The browser starts a call on opening a new active SIP lesson card; creation errors
offer manual retry, not automatic repeated calls. `/sessions/{sid}/call` reuses a
saved call ID and audits sanitized connection failures as `call.failed`.
Voice rejects a different active session on the same extension with429, including
mock mode. Backend wraps Voice failures as503. Completion hangs up before grading;
teacher group stop attempts every active card even if another hangup fails, leaves
the lesson stopping with503 and allows retry without regrading completed cards.

Teacher GET `/instructor/lessons/{lid}/live` requires the owning teacher (foreign404,
student403) and returns `{id,title,state,observed_at,participants}`. Each participant
has `student_id,display_name,completed,active_card,latest_result`. Active card is
null or `{id,number,title,created_at,elapsed_seconds,transport,call_id,last_event,
provider_error}`. Latest result is null or `{score_percent,policy_result}` from
the most recently issued completed card; these are automatic results, not expert
overrides. No active-work grade is inferred. Elapsed time starts at card creation;
activity means persisted actions, not online presence or unsaved keystrokes.
This read-only snapshot does not call Voice or LLM. The teacher UI polls every5s
while visible; connection failures retain the previous snapshot as stale.

## Assessment policy, expert audit and statistics

All routes below require service authentication plus the corresponding teacher or
student user session. Full semantics and limitations: `ASSESSMENT.md`.

- Teacher GET/PUT `/instructor/scenarios/{sid}/assessment-policy`: `{revision,policy}`.
  PUT checks revision>=0, stale409. Policy null disables future application.
- Teacher GET `/instructor/sessions/{sid}/assessment` and student GET
  `/student/sessions/{sid}/assessment`: completed, owned assessment only.
- Teacher POST `/instructor/sessions/{sid}/assessment`: append expert grade/revocation.
- Teacher GET `/instructor/statistics?group_id=UUID` (optional own-group filter);
  student GET `/student/statistics` (own attempts only).

Policy: `{pass_score_percent:number|null(0–100),max_field_errors:int|null(0–30),
max_sequence_errors:int(0–20,default0),fail_on_timeout:bool(false),steps:[]}`.
At least one active check or step required. Up to20 steps, each
`{id:ASCII identifier(1–64),label:string(1–200),event_type,service:string(0–160),
status:string(0–80)}`. IDs unique. Event types: card.saved, service.updated,
notification.recorded, card.processed, card.linked, call.requested, session.finished.
Service filter only for service/notification; status only service.updated.
Snapshots freeze at session creation / fill lesson start / action preparation.

Completion adds `policy_result:null|{version:'policy-v1',policy_revision,passed:
boolean|null,field_errors:int|null,sequence_errors:int,checks:[{id,passed,actual,
limit}],steps:[{id,label,event_type,passed,reason,event_seq}]}` to workspace detail.
Step reason is matched/missing/out_of_order; event_seq points to persisted evidence.
Check IDs: score, field_errors, sequence_errors, time. Missing required rubric data
makes a check unknown, not successful. No result is recalculated retroactively.

Expert POST: `{request_id:UUID,revision:int>=0,action:'grade'|'revoke'(default grade),
score_percent:number|null(0–100),passed:boolean|null,reason:string(1–2000 nonblank)}`.
Grade requires numeric score and explicit verdict; revoke requires both null and
an existing active grade. Identical UUID/body retries return current assessment;
conflicting reuse/stale revision/invalid revocation or100-decision limit409.
Active attempt409; foreign/missing404; wrong role403. No grade/history deletion API.

Assessment response: `{session_id,title,student_name,automatic:evaluation|null,policy_result,
expert:{revision,current:review|null,history:review[]},effective:{score_percent,
passed,source:'expert'|'automatic'}}`. Review contains request_id, action, score,
passed, reason, teacher_id, teacher_name, at, revision and original request payload.
Automatic report stays unchanged; latest expert grade wins, revoke restores auto.

Statistics: `{summary,progress,students,typical_errors,by_scenario}`. Summary fields:
attempts, completed, graded, passed, failed, unassessed, average_score, average_seconds.
Null score/verdict remains unknown. Progress holds last200 completions in ascending
time order: `{session_id,title,finished_at,score_percent,passed,source,difficulty,
dds_profile}`, with student_id/student_name added only for teacher. Students are
`{student_id,display_name,...summary}`; student-role response has an empty students
array. Own group roster includes zero-attempt members and past participants.
By-scenario rows are `{scenario_id,title,...summary}`. Typical errors:
`{key,label,count,attempts,rate_percent}` grouped by teacher/scenario/config revision
and criterion/step ID. These remain automatic diagnostics even after expert override.
Totals cover all scoped attempts, not merely the displayed progress window.
The teacher session discovery endpoint also adds `student_name` to identify the
owner before opening expert assessment; it does not change its original field score.

### Group AI recommendations

Teacher-only GET/POST `/instructor/groups/{gid}/insights` requires an owned group;
foreign/missing group is 404 and wrong role is 403. GET returns the last saved
snapshot, or `{status:'not_generated',group_id}`. POST requires at least two
completed owned attempts and one visible enabled scenario (otherwise 409), then
explicitly generates or returns an identical cached result. It never creates an
assignment or changes a grade.

Saved response: `{version:'group-insights-v1',status:'ready'|'mock',group_id,
created_at,provider,model,data_fingerprint,aggregate,evidence,summary,difficult_skills,
recommendations,limitations,stale,stale_reasons}`. Aggregate contains
`completed_attempts,current_group_size,participant_count`; participant_count is null
only for a stale legacy snapshot where the historical value cannot be reconstructed. Evidence repeats the
bounded server aggregate rows used by cited keys, so the UI can show labels/counts
instead of opaque keys. Each difficult skill has
`{label,explanation,evidence_key}`. Each recommendation has
`{title,rationale,scenario_ids:[1..5],evidence_keys:[1..5]}`. IDs and keys are
unique and server-validated against the current bounded input. `limitations` lists
catalogue/evidence truncation. Up to 100 scenarios, 20 recurring-error rows and 120
aggregate evidence rows in total enter the payload; up to six skills and six
recommendations are accepted. GET labels a saved snapshot stale when the
aggregate, available scenarios, provider or model has changed; regeneration is
never automatic.

The model input contains group-level counts, recurring configured error labels
(revision-separated, with eligible-attempt denominators), opaque generated evidence
keys, scenario-level aggregate performance and visible author-controlled scenario
and criterion metadata. It excludes student account fields, individual rows,
card/dialogue text, feedback and expert reasons; scenario titles/objectives and
configured labels must still contain only synthetic/authorized content. Mock mode is explicit and performs no model
call. Provider/schema failures return a sanitized 502 and remain retryable; no
failed body replaces a saved result. See `GROUP_INSIGHTS.md`.

## Curriculum and materials

Scenario accepts `difficulty: basic|standard|advanced` (default basic),
`dds_profile: general|fire|police|medical|gas|utilities` (default general), and
`learning_objectives: string` (default empty, max1500). These student-visible
fields appear in student scenario/assignment discovery and are frozen in new
session responses and teacher summaries. They contain no hidden scenario facts.
Generation POST accepts the same fields; they enter the model's teaching context,
survive revisions, and are server-controlled during validation. Request-ID retries
must match these fields as well as brief/category. Mock remains a fixed example.

Lesson POST additionally accepts nullable `difficulty` and `dds_profile` filters.
All selected scenarios/action sources must match, otherwise422. Filters without
explicit scenario IDs resolve an enabled shared/own pool (max20); empty or oversized
pools422. Changed fill metadata at lesson start409. Student lesson summaries expose
these optional filters. Existing lessons without them remain unrestricted.

`GET /api/v1/instructor/curriculum` (teacher) and `/student/curriculum` (student)
return `{difficulties:[{id,title,description}],profiles:[{id,title}]}`.

- Teacher `GET /instructor/materials`: own summaries, including drafts.
- Teacher `POST /instructor/materials`: create, 201 full detail.
- Teacher `GET /instructor/materials/{id}`: own detail.
- Teacher `PUT /instructor/materials/{id}`: replace metadata/text, requires revision.
- Student `GET /student/materials`: currently accessible published summaries.
- Student `GET /student/materials/{id}`: accessible published detail and attachment.

All paths use `/api/v1`, service authentication and role-specific user sessions.
Wrong role403; missing/foreign/inaccessible document404; stale edit409. IDs are UUIDs.
Write: `{title:string(3–160),description:string(0–2000),body:string(0–100000),
difficulty,dds_profile,group_ids:UUID[](max100),published:boolean(false),
filename:string(0–180),file_base64:string,remove_attachment:boolean(false)}`.
PUT adds `revision:int>=1`. Groups must belong to author; publish without groups422.
Text or attachment required422. Empty attachment fields preserve existing file on
PUT; remove_attachment deletes it; remove and upload together422. Files PDF/TXT/DOCX
are bounded to5MiB with structural checks; JSON requests over8MiB413. See CURRICULUM.md.
Limit100 documents/50MiB files per teacher409. No external file paths or remote URLs.

Detail adds `id,teacher_id,revision,created_at,updated_at,file_size` (bytes), with
attachment returned as base64. Summaries omit `body,file_base64`. Student responses
also omit `group_ids,teacher_id`. Membership/publication is rechecked on every read.
Unpublish via PUT (published=false) preserves author access; downloads already made
cannot be revoked. Files are never sent to the LLM. No deletion endpoint.

Current ARM additions and changed notification-save semantics are specified in
`ARM_COVERAGE.md` (API additions). It supersedes earlier preview-only/manual service
selection: resolved services now append automatically on save and cannot be removed.
Routing service/condition metadata and unresolved cases are described in ROUTING.md.
`GET /api/v1/student/routing/catalog` returns `{services: string[], flags:
[{id,label}]}`. Card accepts the additional boolean flags listed in ROUTING.md;
all default false. Routing preview adds `unresolved: [{service,cell,reason,
source_value}]`, never included in automatic notification selection. Current rules
version is full-v2; the catalog retains columns N:CU. Service-set rubric criteria
now allow up to 100 expected services; other criteria retain the 20-value limit.

Backend REST currently includes health and scenario CRUD/validation. Voice REST
includes health, call create/status/hangup, persistent chat, manual speech playback,
and the authenticated External AI endpoints under `/api/v1/integration`.

Control endpoint: `/ws/v1/voice/sessions/{session_id}`. Messages use `EventEnvelope`: `event_id`, `seq`, `session_id`, `type`, `elapsed_ms`, `payload`. Binary Asterisk media is a separate Voice/Asterisk protocol.

Machine-readable contracts are `shared/api/openapi.yaml` and `shared/api/asyncapi.yaml`.

Student API on Backend (proxied unchanged by Frontend :3000):

| Method | Path under `/api/v1/student` | Result |
| --- | --- | --- |
| GET | `/scenarios` | Enabled IDs, titles and public curriculum metadata; no hidden facts |
| GET | `/classifier` | Source-versioned groups and records, three ordered features, final incident type and source row |
| POST | `/routing/preview` | Card body; deterministic core-service suggestions without persistence or notification |
| GET / POST | `/sessions` | Recent sessions / create blank card with frozen scenario |
| GET | `/sessions/{sid}` | Card, messages, audit, revision and provider error |
| PUT | `/sessions/{sid}/card` | Save `{revision, card}`; stale or completed = 409 |
| POST | `/sessions/{sid}/messages` | `{message_id, text}`; idempotent text dialogue |
| POST | `/sessions/{sid}/services` | `{service, status, comment, order_number?:string, message_id?:UUID}`; selected services only |
| POST / GET | `/sessions/{sid}/call` | Create SIP call / inspect status through Voice |
| POST | `/sessions/{sid}/finish` | Finish once; freeze card and record elapsed time |
| GET | `/sessions/{sid}/report` | Frozen criterion report after completion; 409 while active, 404 for legacy sessions without report |
| POST | `/sessions/{sid}/ai-review` | Separate advisory analysis after completion; cached successful result; no card/grade mutation |

Backend endpoints require the existing Bearer service token. The local Frontend
injects it server-side, validates local Host/Origin and requires `X-Voice-UI: 1`
for mutations. User routes also require `X-User-Session` with a valid account session.
The BFF obtains it only from its HttpOnly `training_session` cookie.
Service `order_number` defaults to empty, is trimmed and limited to 80 characters;
it is stored in the latest service state and append-only `service.updated` event.
Existing clients and historical entries without it remain valid.
The health response reports provider configuration, not upstream reachability.
Cards may include `classifier_id`, `classifier_version`, `classifier_group` and
`classifier_features` (three ordered source strings). Backend resolves the ID,
checks version and feature agreement, and writes the canonical `incident_type`.
Invalid/mismatched records return 422. Partial classifications can be saved as
drafts with an empty final type. Legacy cards retain their original text until
the user explicitly selects a classifier group. Saved sessions include the
classification source row and main-service value for traceability; this does not
automatically add services or notify external agencies.

Cards also carry `threat_to_people`, `offense`, `injured_offsite`, `gasification`
(default false), alongside existing `injured` and `no_access`. `refused` is not
treated as `injured_offsite`. Source feature strings preserve whitespace exactly.
Catalog records include `additional_details` from J and `routing_cells` from N:AB.
`POST /routing/preview` returns `rules_version`, `classifier_id`,
`classifier_version`, `source_row`, `flags`, `suggestions`, `excluded`, `warnings`.
Suggestions contain `{service, mappings: [{cell, incident_type}]}`; exclusions
contain `{service, cell, reason}`. Invalid IDs/versions/features return 422.
An incomplete classification returns no suggestions and a warning.
Save stores the server-calculated `routing` snapshot, including input flags and
source provenance; reopening and finishing retain it. Only `card.services` is the
student's actual selection. Preview never mutates sessions. Conditional branches
N:AB are supported; other agency columns remain outside this rule set. Concurrent
matching branches retain all mappings and expose conflicts instead of guessing
priority. Source `нет реагирования` is an exclusion, not a service suggestion.
Dialogue provider failures appear in `provider_error`; upstream response bodies
and credentials are never returned. Existing Voice WebSocket envelopes are unchanged.

## Configurable assessment (local development)

### AI scenario and rubric authoring

All `/api/v1/instructor/generations` routes require service authentication and a
teacher session. Drafts are private to their author; foreign ID = 404, wrong role
= 403. No draft appears in the scenario library or student assignments.

- `GET /generations`: own summaries `{id,status,revision,title,updated_at}`.
- `POST /generations`: `{request_id: UUID, brief: string (3–3000), category_id}`;
  201 full draft. Category defaults to other. A successful request ID is reusable
  with identical input (no additional model call); different input = 409.
- `GET /generations/{id}`: full persisted draft and revision history.
- `POST /generations/{id}/revise`: `{revision: integer >=1, comment: string
  (3–2000, nonblank)}`. Returns an updated preview, not a published scenario.
- `POST /generations/{id}/approve`: `{revision: integer >=1}`. Publishes a new
  teacher-owned enabled scenario plus its rubric revision 1 atomically. Returns
  approved draft; a repeated approval of the same revision is idempotent.

Full draft: id, brief, category_id, status (draft/approved), revision, scenario,
rubric, provider, model, updated_at, history. History entries contain revision,
comment, at, scenario and rubric. Approval adds approved_scenario_id, approved_at,
approved_by. The scenario inside the draft remains the original disabled preview;
the published library copy is enabled. Published drafts cannot be revised (409);
edit their library scenario/rubric through the existing manual editors instead.
Stale revisions = 409 without mutation. Maximum 20 draft versions. Model errors,
timeouts, malformed JSON or invalid references = sanitized 502; prior draft remains
unchanged. Failed initial requests can be retried with the same request ID.

Generation uses the existing ENV-selected mock/OpenRouter/Ollama provider with a
2000-token budget and 45-second operation deadline. Only the brief, category,
previous preview and correction comment are sent, never student sessions. OpenRouter
sends these synthetic materials outside the machine. Mock returns a marked fixture
and records comments without interpreting them. Generated criteria currently cover
literal text fields caller_name/address_note/description/street/house/city/apartment;
each expected string must occur in a scenario fact. Service/regulatory criteria are
not generated. Structural/reference checks do not establish methodological accuracy:
the teacher must review the complete scenario and rubric before approval.

### Accounts and classroom access

All Backend REST routes below require the service Bearer token. Auth status,
bootstrap and login do not require a user token; others require `X-User-Session`.

| Method | Path under `/api/v1` | Access / result |
| --- | --- | --- |
| GET | `/auth/status` | `{bootstrap_required}` |
| POST | `/auth/bootstrap` | First admin only, `{username,password,display_name}`, 201 `{user,session_token}` |
| POST | `/auth/login` | `{username,password}` → `{user,session_token}`; throttled failures 429 |
| GET / POST | `/auth/me` / `/auth/logout` | Current public user / revoke current token (204) |
| GET / POST | `/admin/users` | Admin list / create teacher or student |
| PATCH | `/admin/users/{uid}` | Admin `{active}`; cannot block admin, blocking revokes tokens |
| GET | `/instructor/students` | Teacher: active student IDs, names and usernames |
| GET / POST | `/instructor/groups` | Own groups / create `{title}` |
| POST | `/instructor/groups/{gid}/members` | Own group: `{student_id}` |
| GET / POST | `/instructor/assignments` | Own assignments / create `{group_id,scenario_id,title}` |
| PATCH | `/instructor/assignments/{aid}` | Own assignment `{active}` |
| GET | `/instructor/sessions` | Own student sessions, summary and available score |
| GET | `/instructor/sessions/{sid}` | Own student session, card and messages; not private frozen prompt |
| GET | `/student/assignments` | Active assignments for current student's groups |

User: `{id,username,display_name,role,active}`. Roles: admin, teacher, student.
User creation accepts `{username,password,display_name,role}`; password 12–128
characters, username 3–40 ASCII characters. Credentials never appear in user responses.
Frontend auth responses remove `session_token` and set an HttpOnly SameSite=Strict
cookie instead. Missing/expired/blocked identities return 401; wrong role 403.

Student endpoints require the student role. Session creation now requires an
`assignment_id` matching `scenario_id`, active assignment and group membership.
Sessions freeze `student_id`, `teacher_id`, `group_id`, `assignment_id` and the
assigning teacher's rubric. Student list/get/mutations only expose owned sessions;
foreign/unowned cards return 404. Teacher inspection requires session teacher
ownership. Assignment deactivation prevents new starts but keeps past work visible.
Scenario CRUD requires teacher identity: shared preexisting templates are readable
but not editable/deletable; new scenarios are teacher-owned. Other teachers cannot
view or assign those scenarios. Old unowned sessions/rubrics are retained, not adopted.
Teacher `GET /scenarios` and `GET /scenarios/{id}` return `ScenarioView`: all
Scenario fields plus server-calculated `editable` and content-hash `version` (64 hex
characters). POST/PUT responses use the same view. `editable` is never accepted as
client permission. POST/validate accept Scenario; PUT requires Scenario fields plus
the last-read `version` (ScenarioUpdate). Missing version = 422, stale version = 409
without mutation. Successful PUT returns the new version. The editor retains input
on conflict; explicitly reload or save as a new copy. No automatic merge is performed.
The editor copies a visible scenario by submitting a new ID to POST; rubrics are
not copied. `enabled=false` blocks new starts, preserving existing session snapshots.
Frontend global legacy `/api/calls*`, `/api/history`, `/api/health`, `/voice-console`
return 403; authenticated session-scoped SIP routes remain available.

`GET /api/v1/instructor/scenarios/{scenario_id}/rubric` returns `{revision, rubric}`
(revision 0 and null before configuration). `PUT` accepts `{revision, rubric}`;
stale revisions return 409 and invalid criteria return 422. Null disables future
assessment. Both require service Bearer plus a teacher user session. Frontend proxies
these routes with the same local Host/Origin checks. Rubrics are scoped to the
current teacher; shared scenarios may have different rubrics for different teachers.

Rubric: `{title, time_limit_seconds: 30, criteria: [...]}`. Each criterion has
`id`, `label`, whitelisted Card `field`, `mode`, nonempty `expected` string list,
and positive integer `weight`. Modes: `equals` accepts any expected alternative;
`contains_all` requires all literal fragments; `set_equals` compares service sets.
Flags use `equals` with one `true`/`false` string. Normalization: NFKC, casefold,
ё→е, collapsed whitespace; punctuation is retained. No fuzzy or semantic matching.

Session creation freezes the rubric privately with the dialogue scenario. Student
session responses expose only `assessment_enabled` and `time_limit_seconds` before
completion, not reference answers. Rubric revisions are retained in a local audit
table. Completion computes `evaluation` once with version `rubric-v1`, rubric title
and revision, status, weighted `score_percent`, earned/total weights, per-criterion
expected/actual values, passed flag, weight and recommendation, plus `timing` and
`limitations`. Timing measures creation-to-finish, not voice latency or first
reaction; it does not affect the field score. With no rubric the status is
`not_configured` and score is null, never an invented zero or pass. Completed
reports are not recalculated after rubric changes. JSON history exports include
the report. Expert overrides remain future work.

## Advisory AI text review

### Teacher feedback

### Group lessons (text series)

`POST /api/v1/instructor/lessons` creates a planned lesson with
`{title, group_id, scenario_ids: [up to 20 IDs], cards_per_student: 1–200|null (default 3)}`.
Null means unlimited until teacher stop. Optional `category_ids` accepts up to six
values from `GET /api/v1/instructor/categories`: fire, traffic, medical, utilities,
public, other. Scenario create/update accepts `category_id` (default other).
These are pedagogical categories, independent of the official incident classifier.
For fill/mixed, categories with empty scenario_ids resolve all matching enabled
own/shared scenarios at preparation. Explicit scenario/source selections must match
the categories when supplied; mismatches or unknown categories return 422.
The creation body also accepts `mode: fill|actions|mixed` (default fill) and
`source_session_ids: UUID[]` (up to 20) and `prefilled_scenario_ids: string[]` (up to
20). The latter creates action templates using exactly victim_name, location and
incident as caller_name, address_note and description; other fields remain empty.
This does not invoke a model. Fill requires resolved scenario_ids and no action
sources; actions requires either type of action source and no scenario_ids; mixed requires
both pools. Sources must belong to the teacher's sessions (foreign = 404, active =
422). Their Card and private scenario snapshot are copied into the lesson at creation;
student identity, transcript, results, feedback and service history are not copied.
The private lesson templates are never included in student lesson/session responses.
Only own groups and shared/own enabled scenarios are accepted. GET at the same path
lists own lessons with issued-card summaries. `POST /instructor/lessons/{lid}/start`
freezes active group member IDs and the fill scenario/rubric snapshots, then starts
a planned lesson; empty group or changed/disabled pool = 409.
Starting an already running lesson is idempotent; a finished lesson cannot restart.

`GET /student/lessons` includes planned lessons for current group members and later
lessons for their frozen members. It exposes id/title/state/card limit/mode/category_ids,
completed count, active_session_id and exhausted, never hidden facts or other students.
`POST /student/lessons/{lid}/next` accepts an optional `{after_session_id: UUID}`:
opens the existing active card or creates a random text card from the frozen pool.
The predecessor must be a completed own card of this lesson (otherwise 409).
Retrying a predecessor returns its already issued successor, even when completed.
Repetition is allowed. Cards store lesson_id, lesson_position, lesson_title and
previous_session_id. At the finite limit or outside running state returns 409;
non-members receive 404. The standard session/card/report APIs remain applicable.

`POST /instructor/lessons/{lid}/finish` accepts `{reason}` and transitions through
stopping to finished (also permits cancelling a planned lesson). New cards and
workspace mutations are blocked before active cards are individually
finalized. Completed reports remain unchanged. On failure the lesson stays stopping;
retry finishes remaining cards. States: planned/running/stopping/finished. Lesson
events and each card result persist. This series is text-only; normal standalone
assignments retain SIP. The joined student UI polls every two seconds while visible,
waits for start and automatically opens the next card after completion.
`GET /instructor/lessons/{lid}/report` returns own lesson metadata, events, summary
(participants/issued/completed), and participants with card IDs, status, elapsed time,
score_percent, action_report and completed_by. Foreign teacher = 404; wrong role = 403.
For an action card, issuance sets `exercise_mode=actions`, `initial_card`, revision 1
and a fresh empty service history. Text dialogue is disabled (409). Student may edit
the copy and record service actions using existing endpoints. No inherited rubric
is applied and no score is awarded for prefilled fields. Completion adds
`action_report: {changed_fields, service_actions, note}` for teacher review, not an
automatic correctness grade. Source sessions remain unchanged. Mixed lessons select
randomly across available fill scenarios and frozen action templates.

`POST /api/v1/instructor/sessions/{sid}/finish` accepts `{reason}` (trimmed 1–1000
characters), requires the owning teacher and returns the completed public session.
Wrong role = 403, foreign/unowned session = 404. Uses the last saved card, never
unsaved browser fields. SIP hangup must succeed before completion; Voice failure
returns 503 leaving the session active. Duplicate finish returns the original
result without a second evaluation/event. `completed_by` records role, user_id,
teacher name/reason where applicable; `session.finished` audits these details.
Student finish shares the same completion path. Session write locks serialize card,
message, service, call and finish requests in this Backend process.

`POST /api/v1/instructor/sessions/{sid}/feedback` requires the owning teacher and
accepts `{message_id: UUID, text: string (trimmed, 1–2000 characters)}`. Returns
`{id, text, at, teacher_id, teacher_name, phase: active|completed}`. Foreign/unowned
session = 404, wrong role = 403. Reusing an ID with identical text returns the same
note; different text = 409. At most 100 notes per session (409 at the limit).
Notes persist in `teacher_feedback` on student/teacher session responses and JSON
exports, with `teacher.feedback` audit events. Both active and completed sessions
accept notes; card, revision, dialogue and immutable grade are not changed. Notes
are student-visible feedback, not caller utterances or private teacher annotations.

`POST /api/v1/student/sessions/{sid}/ai-review` accepts no body and returns the
session with `ai_review`. Active sessions return 409. Review is requested explicitly,
never automatically during finish. The current ENV-selected LLM receives selected
card text fields and bounded facts from the frozen scenario/rubric, not credentials
or the full session. OpenRouter sends this synthetic data outside the local contour;
Ollama stays on its configured host. The mock provider performs no model analysis.

`ai_review` includes `version: advisory-v1`, `created_at`, `status` (ready/mock/failed),
provider and model. Ready/mock results include summary, findings and limitations.
Findings carry kind (grammar/clarity/contradiction), field, exact quote, explanation,
suggestion and server-resolved source references. Quotes must occur in the submitted
field; source IDs must exist, and contradictions require a reference. Unsupported
or malformed output fails closed. This verifies provenance, not the truth of the
model's conclusion. No model-generated grade is accepted.

Failures return HTTP 200 with status failed and a generic `error`, never raw provider
diagnostics. The prior card and deterministic evaluation remain intact. Failed
reviews may retry. Ready/mock reviews are cached and returned without another LLM
call, including concurrent duplicate requests within the single Backend process.
Review completion/failure is audited, persisted and included in session JSON export.
The immutable `/report` remains the deterministic criterion report.

External AI starts a call in `external` mode, reads ordered SSE events, submits the
victim's exact text for synthesis, stops playback, and ends the call. Every message is
bound to `session_id`, `call_id`, and a caller-supplied idempotent `message_id`.
# Completion block additions (2026-09-15)

GET `/student/sessions/{sid}/call` adds `reason`, `recovery_allowed`, and stored
`recovery` status. POST `/student/sessions/{sid}/call/recover?expected_call_id=<UUID>`
redials only an owned editable SIP session after an observed transport failure
(ari_disconnected, media_disconnected, asterisk_media_ended, backend_unavailable).
Normal user/teacher/remote hangup, not-answered and service shutdown never redial.
Requests serialize with card/finish; a stale expected ID returns the current session.
Maximum three attempts within 30 seconds from first recovery request. The browser
poller initiates recovery automatically while the session is open; it is not a
background scheduler after the student closes the browser. Card/revision/transcript
stay unchanged; call_history links separate physical calls and recordings. Unknown
or unavailable Voice state fails closed. Internal session.resume control ignores late
call.ended from superseded calls without reopening a teacher-completed session.

GET `/admin/audit` accepts `source=backend|voice` (default backend) in addition to
day/limit; both sources remain admin-only and preserve metadata privacy.

Admin operations adds `configure` (no service, nonempty allowlisted `configuration`)
and `update` (no service/payload) to POST `/api/v1/admin/operations/jobs`. Both are
maintenance operations: queued is not completed. Worker failures remain visible.
GET `/admin/operations/configuration` returns `{values,xml,note}`; POST
`/admin/operations/configuration/preview` accepts `{xml}` and returns validated
`configuration`, `changes[{key,before,after}]`, `requires_restart:true` without writes.
Unknown keys/entities/DTD/duplicate XML or >32KiB fail 422. GET
`/admin/operations/logs` returns `{sampled_at,available,lines}` (bounded scrubbed tail).
All use existing service token + admin user session, including preview/export.

GET `/student/sessions/{sid}/certificate` and `/instructor/sessions/{sid}/certificate`
return `{filename,content_type,file_base64,certificate_id}` for an owned completed
passing attempt. Foreign 404; unfinished/not passed 409; PDF/font unavailable 503.
Latest effective expert verdict is checked at export, never inferred from a UI flag.

POST `/student/sessions/{session_id}/vis-deliveries` accepts
`{message_id:UUID, informational_recipients: string[]}` and snapshots a saved,
registered assigned card for synthetic delivery. GET same path lists owned deliveries.
Instructor DDS endpoints: GET `/instructor/dds/profiles`, GET `/instructor/dds/incoming`
with required profile and bounded limit/offset, GET `/instructor/dds/incoming/{delivery_id}`
with profile, POST `/instructor/dds/incoming/{delivery_id}/open` with `{profile}`.
Every read/write is teacher-scoped; profile is curriculum metadata, not a new role.
Hidden recipient labels are configured by the teacher with GET/PUT
`/instructor/dds/profiles/{profile}/recipients`; PUT accepts `{revision,recipients}`,
returns incremented revision, and rejects stale updates with 409. Students cannot
set recipients (nonempty informational_recipients is 403) and never receive the
labels. The profile settings affect new deliveries; retries return the original
snapshot. No real VIS call is made.

Voice control WS now sends `backend.ack` after persisted processing and after any
`caller.reply`: ordinary envelope, type backend.ack, payload `{event_id:<original UUID>}`.
Null-reply control events also get durable receipts. Repeated UUID returns cached
reply/ACK without advancing dialogue. Voice sends one pending event at a time,
persists events and ACK sidecars with fsync, and reconnects within configured budget.
This protects durable control events, not lost RTP/audio or a destroyed SIP dialog.
# Operations service inventory

Admin operations status `services[]` includes optional integer `replicas`,
`healthy_replicas`, and `expected_replicas`. Enabled directory and load-balancer
services are included. Aggregate Backend health requires both expected replicas
to be healthy in the two-replica profile; a partial deployment is degraded.

# Учебные билеты (2026-09-15)

Teacher-only, mounted from `backend/tickets.py`. The catalog is the deterministic
import of the supplied ticket booklet; see `docs/TICKETS.md`.

`GET /api/v1/instructor/tickets` returns `{source, phones, metadata, tickets}`.
`tickets` lists `{number, page, calls}`; each call carries `{id, call, title,
category_id, difficulty, dds_profile, published_scenario_id}`. The identifier is
non-null only for a draft this teacher already published.

`GET /api/v1/instructor/tickets/{number}` adds the full `scenario`, its draft
`rubric` and `classified_by` (the keyword that chose the category). Previewing
creates nothing. Unknown ticket numbers return 404.

`POST /api/v1/instructor/tickets/publish` accepts `{draft_ids}` (1–3 entries) and
returns `{published: [{draft_id, scenario_id, created}]}`. Publishing copies the
draft into the teacher's own scenario catalog with a fresh id, records ownership
and writes rubric revision 1. It is idempotent per teacher and draft: a repeat
returns the existing `scenario_id` with `created: false` and never overwrites
teacher edits. Unknown drafts return 404; repeated ids inside one request 422.

# Тепловая карта ошибок и выгрузка XLSX (2026-09-15)

`GET /api/v1/instructor/statistics` and `/student/statistics` now also return
`error_heatmap` with `{scenarios, checks, cells}`. A cell is
`{scenario_id, title, label, count, attempts, rate_percent}` and is derived from
the same completed attempts as `typical_errors`; the two views cannot disagree.
Scope is unchanged: a student sees only their own attempts.

`GET /api/v1/instructor/statistics-workbook?group_id=` and
`/api/v1/student/statistics-workbook` return the same authorized snapshot as an
XLSX workbook in the base64 envelope already used by the PDF certificate:
`{filename, content_type, file_base64, scope}`. The BFF proxies JSON only, which
is why the workbook is not streamed as a binary body. Sheets: Сводка, Прогресс,
Обучающиеся (teacher only), Типичные ошибки, Тепловая карта, О выгрузке. Nothing
is recomputed for the export and no extra query is issued. Text that a spreadsheet
could evaluate as a formula is prefixed with an apostrophe, matching the CSV rule.

# Доклад дежурному службы и нормативы (2026-09-16)

Student-only, mounted from `backend/briefing.py`. See `docs/DDS_DISPATCHER.md`.

`POST /api/v1/student/sessions/{sid}/briefings` starts a training call from the
DDS dispatcher to a service on the saved card: `{message_id, service,
destination?, phone?}`. The service must already be in `card.services`. The reply
carries the duty officer's opening line, the assigned `voice`, `state: open`,
`simulated: true` and a `report` of what the briefing still has to name. Repeating
the same `message_id` returns the existing briefing; a different service for that
id is 409. Limits: 20 briefings per card, 20 turns per briefing.

`POST .../briefings/{bid}/messages` with `{message_id, text}` appends the
dispatcher's line and returns the duty officer's answer. Completeness is recomputed
against the saved card — street, house and incident type — never by the model. With
`LLM_PROVIDER=mock` or an unavailable provider the answers stay deterministic.

`POST .../briefings/{bid}/finish` with `{message_id, recipient}` closes the call.
An incomplete briefing is refused with 409 and the list of missing facts. On
success the briefing is copied into the card as a telephone message and appended
as a `notification.recorded` event with `detail.source = "briefing"`, so it reaches
the journal, the sequence policy and the teacher's report.

`GET .../briefings` lists the student's own briefings for that card.

Evaluation `timing` now reports two norms: `limit_seconds` / `within_limit` for
handling (default 180 s) and `response_seconds` / `response_limit_seconds` /
`response_within_limit` for the reaction (default 30 s). An unmeasured reaction
stays `null` rather than counting as met.

Lesson creation accepts `parallel_cards` (1–10, default 1), `workstations`
(student id → workstation number), `student_scenarios` (student id → scenario id,
must belong to the lesson) and `adaptive_difficulty`. `GET /api/v1/student/lessons`
adds `active_session_ids` and `parallel_cards`. An issued card may carry
`difficulty_advice` with the chosen level, the previous one and the reason.
The lesson report adds `workstation`, `grammar_errors`, `critical_grammar_errors`
per participant and `response_seconds`, `timing`, `grammar`, `workstation` per card.

# Рабочее место по инструкции оператора Системы 112 (2026-09-18)

Источник — «Инструкция пользователя. Заведение карточки происшествия» модуля
«Приём и обработка вызовов 112» и снимки экранов рабочего места ДДС. Тренажёр
воспроизводит поведение, а не подключается к реальной системе.

**Номер АРМ.** Оператор вводит его на экране входа; он хранится в браузере и
передаётся полем `workstation` в `POST /api/v1/student/sessions` и
`POST /api/v1/student/lessons/{lid}/next` (до 80 символов). Назначение
преподавателя перекрывает введённое значение. `registration.workstation_source`
показывает происхождение номера: `teacher`, `operator` или `default`.

**Нерезультативный вызов.** `POST /api/v1/student/sessions/{sid}/unproductive`
с `{message_id, kind}`, где `kind` — `no_contact` или `interrupted`. Карточка
сохраняется пустой, получает `checked_by` и сразу завершается: ответ — та же
карточка, что и у `finish`, с оценкой и нормативами. Повтор того же
`message_id` возвращает результат без второго события. Если в карточке уже есть
службы, вызов нерезультативным не считается — 409.

**Напоминание.** `POST /api/v1/student/sessions/{sid}/reminders` с
`{message_id, text, at}`. Хранится в карточке (`reminders`), до 20 на карточку,
повтор `message_id` идемпотентен. Срабатывание показывает рабочее место.

**Основная служба.** `POST /api/v1/student/routing/preview` дополнительно
возвращает `primary_services` — названия служб из колонки «Главная служба»
классификатора, и у каждого элемента `suggestions` появляется `primary`.
Рабочее место подчёркивает такие службы двойной линией. Коды, которых нет в
таблице соответствия `routing.MAIN_SERVICE_NAMES`, основными не считаются.

**Служба от внешней системы (ВИС).**
`POST /api/v1/instructor/sessions/{sid}/vis-service` с `{message_id, service, reason}`
добавляет службу в сохранённую карточку обучающегося от имени внешней
информационной системы. Доступно только преподавателю — владельцу занятия.
Состояние службы получает `source: "vis"`, и рабочее место показывает пометку
ВИС. Повтор `message_id` идемпотентен; уже назначенная служба — 422;
несохранённая или завершённая карточка — 409. Интеграции нет: данные не
покидают стенд.

`GET /api/v1/instructor/routing/catalog` отдаёт преподавателю тот же список
служб, что и `/student/routing/catalog` — он нужен для выбора службы ВИС.
