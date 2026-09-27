# Backend

Целевой минимальный контур использует только CPU. `LLM_PROFILE=standard`
выбирает локальную `qwen3:8b` Q4_K_M для авторинга; телефон использует отдельную
`PHONE_LLM_MODEL` с контекстом 4096. На весь модельный ход даётся до 15 секунд,
затем возвращается ответ по подтверждённым фактам. CPU-замеры и границы этого
лимита описаны в `docs/LOCAL_MODEL.md`. Наличие GPU не учитывается в приёмке;
полная вместимость 20 одновременных гибридных разговоров ещё не доказана.

`field_dialogue.py` handles crew follow-up questions for incoming reports and
outgoing progress calls. Context freezes the initial address, current report and
previously delivered operational reports, excluding future updates and rubrics.
The fifteen-second LLM budget applies; mock/timeout answers remain grounded.
No endpoint or database schema changes. Tests: `test_field_dialogue.py`.

Phone turns now have a fifteen-second model wait budget independent of long
scenario generation. DDS falls back to deterministic missing-fact clarification;
112 requests repetition on timeout. Ticket publication accepts `new_revision`
to create a fresh catalog copy while preserving teacher edits and issued attempts.

CPU phone tuning: `PHONE_LLM_MODEL` (default `qwen3:4b-instruct-2507-q4_K_M`), `PHONE_LLM_THREADS`
(default6), at most120 generated tokens. `num_gpu=0` is sent for every local
inference; warmup uses the actual Ollama host and matching phone options.
All system messages survive role repair. A 112 retry shares the original deadline.
Synthetic comparison: `python tools/benchmark_phone_cpu.py --models qwen3:4b-instruct-2507-q4_K_M
--threads 6 --runtime --output artifacts/cpu-phone/result.json` from the project root.

26 September: `recipient_affiliations` on prepared cards supplies explicit area,
district and departmental recipients with routing provenance. Grammar v3 adds
local syntax suggestions without automatic penalties. Owned open SIP briefings
support bounded recovery; see API_CONTRACT and DEADLINE_HARDENING_2026-09-26.

Customer audit fixes: `material_text.py` extracts full bounded documents, local OCR
and task-relevant passages; install updated requirements and local Tesseract rus/eng.
Materials allow25 MiB PDF/TXT/DOCX/XLSX and expose extraction status. `text_facts.py`
adds local Russian word forms/polarity; DDS rubrics support required report fields,
weights and student-visible correction evidence. See `docs/CUSTOMER_FIXES_2026-09-23.md`.

`territories.py` resolves only approved exact-match local recipient rules from ENV
`TERRITORIAL_ROUTES_FILE`. New DDS cards freeze those recipients with classifier
routing. `POST student/grammar/preview` and its instructor counterpart check drafts
without persistence or exposing rubric answers. Completed grammar reports include
own-service comments. See `docs/TERRITORIES_XML_GRAMMAR.md`.

Direct initial 103 completion with the explicit no-brigade comment counts as the
receipt decision. It bypasses brigade/report prerequisites, not the teacher's
expected brigade/result checks. Future report checks are not applied to that
terminal path. Historical completed assessments are unchanged.

Все 96 билетов содержат готовые карточки, бригаду, четыре оперативных доклада и
эталоны действий; `tools/ticket_exercises.py` задаёт авторскую учебную постановку.
Каталог обновляется командой `python tools/import_tickets.py` из корня проекта,
без записи в БД. Для выдачи нужна публикация преподавателем в `/tickets`.
Все 96 карточек имеют запись классификатора из `tools/ticket_annotations.py`
и известные структурированные адресные поля. Десять прежних адресных уточнений
сохранены в `tools/data/dds_prefilled_cards.json`. Старый инструмент синхронизации
не заменяет публикацию обновлённого каталога. Для его ограниченного применения выполните
`python sync_curated_cards.py` внутри контейнера для сухого прогона и
`python sync_curated_cards.py --apply` для добавления карточек в ранее
опубликованные неизменённые сценарии. Изменения преподавателя не затираются.
Норматив 30 секунд для ДДС считается от выдачи до первого статуса «Принята»
или «Не принята»; открытие строки фиксируется отдельно. Генератор поддерживает `mode=dds` с
готовой карточкой, докладами с места и эталоном решений. Преподаватель может
сохранять и отключать исправления для следующих вызовов ИИ через
`/api/v1/instructor/corrections`; прежние оценки не меняются.

The production image installs `websockets` explicitly: plain Uvicorn does not
include a WebSocket protocol implementation, and without it Voice control upgrades
are rejected as ordinary HTTP 404 requests.

`maps.py` serves authenticated regional viewport and address-search reads from
`MAP_DATA_DIR/regional.sqlite` (read-only, spatial R-tree + FTS5). No map provider
is called at runtime; package preparation and transfer are in `docs/MAPS.md`.
Focused check: `python -m pytest test_maps.py -q`.

security_audit.py records metadata-only HTTP start/end events in SECURITY_AUDIT_DIR,
with daily files and preserved gzip archives. directory_auth.py + directory_routes.py
add optional validated LDAPS login for explicitly linked non-admin users. The
directory_links table is included in the SQLite-to-PostgreSQL migration allowlist.
See docs/SECURITY.md, docs/DIRECTORY_AUTH.md and updated API_CONTRACT.

`operations.py` adds admin-only monitoring, bounded deployment jobs and backup
schedule settings through OPERATIONS_DIR. No Docker SDK/socket in Backend.
Host worker: `deploy/ops_worker.py`; contract and limits: docs/OPERATIONS.md.
Focused checks: `python -m pytest test_operations.py -q`.

Card coordinates are paired nullable WGS84 numbers; they are not auto-derived from
the address. `TRAINING_WORKSTATION_ID` labels new session registration; the actor
display name is frozen server-side. Legacy registration is not invented. Tests:
`test_card_coordinates.py` verifies bounds, pair validation, zero values,
persistence, audit and cross-student denial.

Docker deployment uses PostgreSQL when `DATABASE_URL` is set. Local development
and mock tests keep using `DIALOGUE_DB` (SQLite, default `dialogue.sqlite3`); a
configured `DATABASE_URL` takes precedence. PostgreSQL credentials are ENV-only.
The Backend still uses one process-level connection and application mutation locks,
so this change does not establish multi-worker support. Run the bounded acceptance
harness on the actual deployment server before acceptance; desktop measurements do
not certify another machine.

File-backed SQLite uses WAL with `synchronous=FULL`; memory mock databases remain
in-memory. Keep a SQLite database and its `-wal`/`-shm` sidecars together on a local
filesystem. Use SQLite backup/checkpoint procedures, not a live copy of only the
main file.

To move a stopped local SQLite installation, first start the new Backend once so
its PostgreSQL schema exists, then stop both Backend instances. Run the migration
preflight from the repository root (the URL should come from an environment variable):

```powershell
$env:DATABASE_URL = "postgresql://..."
python tools/migrate_sqlite_to_postgres.py backend/dialogue.sqlite3
python tools/migrate_sqlite_to_postgres.py backend/dialogue.sqlite3 --apply
```

The first command is read-only. `--apply` refuses a destination containing anything
other than the automatically installed seed scenario, copies all supported tables
in one PostgreSQL transaction, and verifies the total row count. Do not migrate a
live SQLite database. Keep the original file and sidecars as a rollback backup until
the PostgreSQL deployment has been verified.

Group lessons accept text/SIP with per-student extension mappings; both fill and
DDS action cards freeze their workplace number. Teacher `/lessons/{id}/live` exposes an
owner-scoped saved-activity snapshot without calling Voice or LLM. See the latest
API_CONTRACT section. `test_lesson_voice.py` covers mapping validation, privacy,
idempotent calls, next-card voice, mixed actions and stop/retry on Voice failure.

`assessment.py`: versioned threshold/sequence policies, immutable completion checks,
append-only expert decisions and owner/group-scoped statistics. Workspace snapshot
integration covers standalone/fill/action attempts. Run test_assessment.py for
policy freezing, sequence evidence, audit/idempotency/revocation and aggregate privacy.
`group_insights.py` adds explicit teacher-requested group practice suggestions through
the existing LLM adapter. Its payload excludes student identity and submitted text;
strict evidence/scenario references, fingerprint caching and stale labels are covered
by `test_group_insights.py`. It does not assign work or alter assessment.

Curriculum: `curriculum.py` owns difficulty/profile enums and selection matching.
Scenario, generation and lesson flows preserve metadata. `materials.py` provides
teacher-owned drafts/publication and group-scoped student reference access, storing
bounded attachments as SQLite BLOBs. Tests: test_materials.py, test_curriculum.py,
test_generation.py. Uploads work locally without a model (including mock mode).

ARM operations: service_workflow.py owns response transitions and derived incident
status. Workspace persists notification logs, own-card links and processed markers;
card saves append resolved services and preserve notified services. Tests:
test_service_workflow.py and test_arm_actions.py. Details: docs/ARM_COVERAGE.md.

`generation.py` provides teacher-private AI authoring drafts with preview, correction
history, revision conflicts and explicit atomic scenario/rubric publication. It
reuses llm.py and existing models/storage. `python -m pytest test_generation.py -q`
covers private drafts, invalid output, correction failures and duplicate approval
without paid provider calls. API details: docs/API_CONTRACT.md.

Local scenario and victim-dialogue service on port 8000. It stores scenario snapshots
and conversation history in the configured SQL database, validates the victim role, and calls a local
Ollama model only in automatic mode.

```powershell
python -m pip install -r requirements.txt
python -m uvicorn server:app --host 127.0.0.1 --port 8000
python -m pytest test_dialogue.py -q
```

Set `DIALOGUE_TOKEN`, `DIALOGUE_DB`, and `DIALOGUE_MODEL` through environment
variables for local development. Set `DATABASE_URL` to a PostgreSQL URL in Docker.
No real emergency-service integration is included.

`llm.py` provides mock/OpenRouter/Ollama adapters. `workspace.py` adds the student
session/card API, optimistic saves and audit. Use `python ../tools/run_workspace.py`
from any directory to load the root `.env` and start both local services.
File-backed SQLite runs in WAL mode with `synchronous=FULL`; the write-throughput
optimization does not weaken per-commit durability. In-memory tests retain their
native journal mode.
`LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` select cloud
dialogue for development. The API key stays in the process environment.
Run `python -m pytest -q` for dialogue and workspace tests.

`classifier.py` serves the versioned catalog in `data/classifier.json`; no Excel
dependency is needed at runtime. To reimport the supplied synthetic/reference
dataset, install `openpyxl` in the import environment, then run from repository root:

```powershell
python tools/import_classifier.py "C:/Users/MainUser/Downloads/Датасет.zip"
```

Review the import summary before replacing the catalog. The importer preserves
source feature text and main-service cells without deriving notification rules.
Saved classifications include the workbook hash and source row for traceability.
`routing.py` evaluates explicit core-service branches N:AB. The preview endpoint
is side-effect free; saved cards retain the calculated snapshot. Conditions for
other agencies and priorities between conflicting branches are not inferred.

`evaluation.py` provides validated weighted reference criteria and a pure evaluator.
Workspace stores versioned rubric configuration separately from scenarios and
freezes it at session creation. Completion persists an immutable report. Run
`python -m pytest -q` for evaluator and snapshot/privacy/regression coverage.
The deterministic evaluator does not call a model. `ai_review.py` separately offers
advisory semantic/grammar findings with quote/source validation through `llm.py`.
The bounded review token budget does not change the default dialogue budget.

`accounts.py` adds persistent scrypt users, hashed expiring sessions, login throttling,
first-admin bootstrap and role guards. `learning.py` adds teacher-owned groups and
assignments. Student endpoints enforce frozen owner IDs; old unowned work is retained
but hidden. Shared scenarios are read-only; new scenarios and rubrics are scoped to
their teacher. See `docs/API_CONTRACT.md` for both service and user authentication.
Run `python -m pytest -q` including account, learning and RBAC integration tests.
The existing service-authenticated Voice WebSocket is unchanged.

Teacher scenario GET responses include `editable`; write schemas do not accept it.
Read/create/update responses also include a content-hash `version`. PUT requires
that version and rejects stale updates with 409. POST and validation remain unchanged.
The new frontend editor uses the existing create/update/validate endpoints. Coverage
in `test_scenario_authoring.py` checks template protection and cross-role ownership.

Workspace teacher feedback uses owner-scoped, idempotent note IDs. It is available
during and after an attempt, without changing card revisions or grades. The RBAC
integration suite checks ownership, duplicate/conflicting IDs and grade immutability.

Teachers can finish owned sessions with a reason, through the same finalizer as
student completion. Per-session write dependencies serialize competing mutations.
SIP hangup errors block completion; duplicate finish preserves the first report.

Workspace also owns planned/running/stopping/finished group lessons. Lesson IDs link
the separately graded cards. See API_CONTRACT for teacher start/stop and idempotent
student next issuance. New lessons can use text or SIP with a provisioned
extension per student. Existing assignments are unchanged.
Action/mixed modes freeze completed teacher-owned card templates in the lesson.
Copies exclude source feedback, transcript and evaluation. Action attempts disable
dialogue and produce an action report without inheriting the original field score.
When a DDS workplace has a SIP extension, timed field reports require a separate
answered voice call and explicit acknowledgement before unlocking their status.

Lessons support category selection, unlimited cards (null limit), deterministic
scenario prefill and teacher-owned group reports. Fill snapshots freeze at start.
Next requests accept after_session_id for retry-safe automatic progression.
Tests in test_lesson_lifecycle.py and test_rbac_integration.py cover the lifecycle,
ownership, frozen pools, concurrent issuance and independent action copies.
# Completion extensions

DDS progress requests reuse field-report playback receipts. New prepared attempts
anchor operational updates at crew assignment and freeze a reachable training time
budget. Applicant messages retain receipt timing. Legacy attempts are unchanged.
See `docs/API_CONTRACT.md` and `test_progress_requests.py`.

Certificates, VIS/DDS, technical configuration preview and WS metadata audit are
assembled in server.py. Optional Coordinator namespaces and the cluster-wide HTTP
mutation guard protect multi-replica updates; see CLUSTER.md for limits. ReportLab
needs DejaVu Sans (`CERTIFICATE_FONT` overrides the Docker-provided font).
