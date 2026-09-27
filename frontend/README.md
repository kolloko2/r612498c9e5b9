# Local web console

Practice coaching requires the issued `practice_with_hints` flag, not a scenario
ID or saved browser preference. Teacher forms and existing lesson controls expose
the permission. «Начать эту карточку заново» in response tools/history creates a
fresh attempt with confirmation; teacher detail offers the same per-student action.
Regression: `ATTEMPT_RESTART_TEST=1 node frontend/test_briefing_browser.cjs`.

DDS telephone icons are vertically centered within their grey cells, with an
explicit line height independent of the phone input's font metrics.

The map uses vendored MapLibre GL JS 5.18.0 (CSP worker), PMTiles 4.5.0 and
Protomaps light style with Russian labels. Fonts/sprites are local. The frontend
mounts `deploy/maps` read-only and serves only `/map-data/region.pmtiles` with
HTTP Range support. Address search still uses Backend's regional SQLite index.
See `docs/MAPS.md`; run `node frontend/test_map_browser.cjs` with the archive installed.

Map point selection: «Указать на карте» selects by click/tap or Enter at the
viewport centre. Dragging (threshold 5 CSS pixels) only pans. The toolbar copies
WGS84 coordinates and restores the original card point. DDS/completed cards are
copy-only; editable 112 cards retain explicit transfer confirmation and save.
Search results select a point through the same toolbar. No reverse geocoding or
automatic address replacement. Regression: `node frontend/test_map_browser.cjs`.

Редактор сценария позволяет запретить текстовый диалог. Кабинет преподавателя
показывает фактическое общение (голос/текст/смешанное), число реплик и настройку
SIP-номеров участников занятия. В голосовой практике наставник предлагает
произнести пример в телефоне; кнопка вставки текста для этого шага скрыта.

В редакторе эталона ДДС можно задать факты комментария к каждому докладу:
`working = повреждена труба; перекрыли воду`. Статус в АРМ не выбирается
автоматически, в том числе после принятия карточки: следующее решение за учеником.

DDS practical coaching (`assets/dds-coach.js`) is opt-in for an existing card and
auto-enabled for the introductory `dds-guided-practice-v1` scenario. Steps derive
from persisted actions and already delivered reports, never the private rubric.
Finish displays the backend completion checklist. The crew is the default phone
recipient after assignment; status and comment entry remain manual.
Regression: `node test_dds_coach.cjs`; `BROWSER_PATH` selects an installed Chromium
for `ARM_DDS_PREVIEW=1 node test_briefing_browser.cjs`.

27 September: DDS source-screen alignment refines the call row, incident actions,
service tiles, per-service history and compact response strip without copying the
browser chrome. Live ARM checks cover 1920×1080, 1280×720 and 760×900. The narrow
journal toolbar has its own responsive layout. A card with an absent `card_locked`
value no longer remains accidentally read-only. Teacher cabinet columns collapse
before their forms overflow at 760 px; seven deployed pages were checked at
1600, 1280 and 760 px. Exact visual parity still depends on the incident data
and is not claimed for browser chrome or training-only controls.

26 September: own-service pencil opens the compact reference status/order/comment
strip; footer actions keep crew assignment, telephone briefing and other tools.
Live card/history/status/tools checks passed at 1920x1080 and 1280x720; synthetic
layout checks also cover 760px.

Materials: the editor displays extraction completeness and preview,
accepts25 MiB PDF/TXT/DOCX/XLSX. DDS editor exposes report fields, check weights,
pass threshold and correction sources. The student sees those source statements;
the timer shows the opening and first-record norms.

`xml-exchange.js` adds bounded XML form import/export for material text/metadata
and group workstation labels. It never submits forms or imports permissions.
The card response panel and scenario editor expose manual text checks. DDS layout
keeps response tools reachable with the own-service pencil or footer action button;
Escape closes those tools before closing the card.

DDS-only layout overrides live in `assets/dds-layout.css`. Saved DDS cards place
description below address; service history opens above the footer, and the own
service pencil opens response tools. The 112 input form is not restyled by these
selectors. Card/history/status/tools were checked in the deployed browser on 26 September.

Cabinet pages load `assets/cabinet.css` last for a shared graphite visual system.
Do not add it to student.html or dds.html: the source-matched ARM and service UI
are deliberately excluded. Scenario structured inputs synchronize on input, not
only blur. Generation approval uses the explicit review checkbox and publication
button, without an additional native browser confirmation. Group stop uses an
in-page reason form. Map requests have cancellation/timeouts and retain geometry
on failure; all road labels render after geometry.

На рабочем месте есть отдельная кнопка «Начать обучение интерфейсу»: она
повторно запускает пошаговый тур после первого входа. В журнале ожидающая
принятия карточка ДДС показывает оставшееся время до 30-секундного норматива;
открытие строки не останавливает отсчёт: нужен статус приёма/отказа. В форме преподавателя SIP-номер для
готовой карточки обозначает исходящий голосовой доклад дежурному службы.
На странице ИИ-авторинга основной режим — готовая карточка ДДС; отдельно
доступен полный цикл 112. Там же преподаватель сохраняет и отключает примеры
исправленных ответов для последующих вызовов модели.

The map now uses Backend regional viewport/search routes, supports dragging,
arrow-key navigation and wheel zoom, and can search before coordinates have been
saved. The former centre-only JSON and optional PNG tiles are no longer fetched.
Prepare/copy the full regional package as documented in `docs/MAPS.md`.

`/audit` is the admin request-audit viewer. `/login` offers directory authentication
only when configured; the admin portal explicitly links non-admin users. Statistics
support safe CSV; operations support sanitized XML export (no import). Student
sessionStorage drafts restore only on explicit choice and survive network recovery;
Confirmed interrupted SIP calls support bounded redial while the relevant workspace
or briefing is open. Normal hangup never triggers redial. See the recovery documentation.

`/operations` is the technical administrator panel, linked from the admin portal.
It polls authenticated Backend snapshots; stale monitoring is not displayed as healthy.
Service stop/restart requires explicit confirmation; settings drafts survive polling.
All authorization is enforced server-side; the page contains no runtime credentials.

Root Docker Compose builds this Python BFF on :3000. `ALLOWED_ORIGINS` is an exact
comma-separated list of trusted http(s) origins; only their hostnames plus loopback
are accepted. No wildcard is added for container/LAN deployment. `COOKIE_SECURE=true`
is available behind HTTPS; local HTTP defaults to false. Backend/Voice URLs and
tokens remain ENV-only server-side. See docs/DOCKER_DEPLOYMENT.md.

ARM refinement: advanced search includes date/time, classification/feature/address,
district/region/service/caller filters. Journal disclosure shows persisted detail
without opening the card. Registration and ЧС no longer use invented operator
numbers or the injured flag. Saved DDS cards place the description below the address on the left;
desktop header/footer remain visible around scrollable content.

`/map?sid=UUID` opens a read-only incident coordinate window through the existing
owner-scoped API. The map uses the installed regional offline package and supports
address search before saving coordinates. Selection requires confirmation in the
card. Copy `deploy/maps/regional.sqlite` for deployment; no external geocoder or
browser location permission is required. See `docs/MAPS.md`.
Checks: `node frontend/test_map_math.cjs`, `node frontend/test_map_browser.cjs`.

The BFF keeps one pooled HTTP client per upstream service for its process lifetime.
Browser cookies are never forwarded upstream; user identity is copied explicitly to
`X-User-Session`. This avoids blocking the event loop by rebuilding HTTP/TLS client
state for every proxied request while preserving user-session isolation.

`/assessment` combines teacher policy configuration, expert grading/audit and
teacher/student statistics. It preserves the original field score separately from
effective expert decisions and exports statistics as JSON. BFF forwards group query
filters; all authorization remains in Backend. Browser regression:
`node frontend/test_assessment_browser.cjs` (mocked HTTP).
For a selected teacher-owned group, the same page loads or explicitly generates an
advisory practice plan, labels mock/stale snapshots, and links suggested existing
scenarios to the manual assignment workflow. It never allocates exercises itself.

`/materials` is the teacher document editor and student reference library, using
existing role-aware BFF authentication. Scenario/generation forms expose difficulty,
DDS profile and objectives; lesson selection filters by level/profile. Attachments
are downloaded, never embedded/executed. See docs/CURRICULUM.md for limits.

ARM source coverage and new card/search/notification controls are documented in
docs/ARM_COVERAGE.md. «Отработана» marks only the incident; «Завершить занятие»
finishes the exercise. Service states and routing conditions come from Backend.

`/generation` is the teacher AI scenario/rubric authoring page, linked from
`/scenarios`. It previews private drafts, sends correction comments, restores saved
drafts and requires confirmation to publish. The existing BFF keeps credentials
server-side. Check `node --check frontend/assets/generation.js` from repo root.

The default `/` now serves the student АРМ workspace on :3000. `student.html` and
`assets/student.*` implement the source-matched incident journal and card forms.
The original global console is isolated (403) in this BFF. Start both services using
`python tools/run_workspace.py` from the repository root. Student API requests
go to Backend exclusively; Backend controls Voice. Ordinary speech uses the
browser's installed Russian voice when available and enabled by the student.
The notification dialog previews core-service mappings from Backend, exposes
their source cells and adds suggestions only on an explicit student action.
Manual selections are preserved; saved routing grounds remain viewable.
`/instructor` provides an authenticated teacher rubric editor. Student completion reports
show criterion differences, weights and timing; JSON export includes the report.
Check both assets: `node --check assets/student.js` and
`node --check assets/instructor.js`.
Completed reports have a separate on-demand AI review action,
pending/retry states and source-backed findings. The card and criterion grade are
not rewritten by model results.

`/login` supports first-admin setup and account login. `/portal` routes by role:
admin user management; teacher groups, memberships, assignments and own sessions;
student assignments. Backend enforces every permission, not just UI visibility.
The HttpOnly SameSite=Strict cookie never exposes its value to JavaScript. The BFF
ignores incoming identity headers and injects the cookie server-side. Check the
login and portal JavaScript with `node --check` as well. Keep this HTTP build local.

The following documents the original console. Its global call/history endpoints
are retained below as historical context. The new `/scenarios` teacher page supports
creation, copying shared templates, editing owned scenarios and disabling new starts.
Structural validation does not call an LLM. Check `assets/scenarios.js` with Node.
Saving an existing scenario submits its last-read version. Conflicts retain form
input; use «Создать копию» to preserve it or explicitly reload the saved version.
The legacy endpoints
remain historical. Teacher session detail now supports student-visible feedback
and manual refresh of the saved card/dialogue. Student feedback polling runs every
5 seconds while a session is open and the page visible; it does not replace drafts.
Feedback is displayed in the report/history, separately from caller dialogue.
Teacher detail also offers completion with a required reason and confirmation.
Student polling notices remote completion within its next successful poll. The last
saved card is displayed read-only; any unsaved draft is included as `unsaved_draft`
in JSON export, retained in user/session-scoped sessionStorage across reload in that
tab. It is not part of the grade and is not uploaded to Backend.
Teacher portal includes group series setup: title, group, multi-select scenarios,
card count; prepare, start and finish all. Student's new-session dialog lists running
series alongside standalone assignments. Series support text or SIP with teacher-assigned extensions.
Completed reports offer «Следующая карточка серии». Reload/new-session reopens the
same active card. Teacher refresh updates issued/completed counts.
Lesson setup offers fill/actions/mixed modes and a completed-card source selector.
For action attempts the student lands directly on a prefilled copy with service
responses available. Reports show changed-field and service-action counts; teacher
detail displays the service history. Automatic correctness grading remains pending.

The student lesson bar supports waiting for teacher start, automatic random-card
progression, unlimited practice, active-card recovery on reload and teacher stop.
The portal offers multi-category selection, scenario-based prefill and a group
report with individual histories and JSON export. Teacher counters refresh every
five seconds; joined students poll every two seconds while the page is visible.

Optional browser regression: `node frontend/test_lesson_browser.cjs` from repository
root, with Playwright installed/resolvable and LESSON_BROWSER_PATH pointing to a
Chromium browser. This test mocks HTTP (no credentials, paid calls or live database)
and exercises teacher controls and the student waiting/next/reload/stop workflow.
The legacy endpoints
are now blocked regardless of port; use the session-scoped student SIP flow:

Russian operator UI and server-side proxy on port 8002. It keeps Voice and Backend
tokens on the server, streams the chat to the browser over SSE, supports manual,
automatic and External AI modes, scenario construction, history, and TXT/JSON export.

```powershell
python -m pip install -r requirements.txt
$env:API_TOKEN='local-voice-token'
$env:BACKEND_TOKEN='local-dialogue-token'
python -m uvicorn server:app --host 127.0.0.1 --port 8002
```
# Completion UI

`/dds` is the teacher-scoped incoming training journal. `/assessment` offers passing
attempt PDF certificates. `/operations` includes XML preview/confirmation, resource
settings, scrubbed logs and host-approved updates. `/map` uses the installed offline
OSM centre extract; selected coordinates require confirmation in the student window.

Interface copy is intentionally concise: repeated synthetic-data and unofficial-status
banners live in the project documentation instead of every screen. The current local
deployment has no blanket cloud-transfer notice; the UI still shows the actual model
or mock status, destructive confirmations, data-loss warnings, errors and operationally
relevant limitations.
# UI fixes — 2026-09-23

The service briefing dialog uses two shrinkable columns and full-width controls.
Long transcript messages wrap while preserving line breaks; the dialog scrolls
vertically without clipping the call button or shifting replies horizontally.

DDS response tools include a current-progress request. Inbox updates refresh allowed
statuses immediately without replacing the student's unsaved comment. The scoped
DDS layout uses a compact address/map link and chronological incoming reports.
Browser acceptance on the isolated mock store includes a full 12/12 exercise and
offline address search; it is not a physical SIP/audio acceptance test.

Scenario editing includes structured inputs for prepared DDS cards, operational reports, crews and decision expectations. Advanced JSON preserves additional classifier metadata. Assigned crews with a phone can be selected in the briefing contact list.

The crew selector is not a nested form. Map opens in a same-origin dialog iframe, with explicit coordinate confirmation, loading/error/retry states. Only `/map` permits same-origin framing; other pages retain `frame-ancestors 'none'`. Inbox polling delivers updates across active cards without replacing unsaved edits.

DDS response inputs explicitly use dark text on their white background. This fixes
white-on-white comments and status selectors inherited from the dark response panel.
Browser verification includes visible typing, saved actions and two completed cards.

# 26 September DDS hardening

DDS tiles display time and state together, with upper overflow rows and compact
response controls. Scenario authoring exposes explicit recipient affiliations.
While a SIP briefing dialog is open, polling requests bounded recovery after a
confirmed transport failure, preserving the same briefing and transcript.
Run `$env:ARM_DDS_PREVIEW='1'; node frontend/test_briefing_browser.cjs` in PowerShell
for isolated DDS layout checks/screenshots at three viewport sizes.

27 September DDS visual pass: the saved-card header uses the reference's narrow
call control, three telephone cells and compact incident summary, with separate
`просмотр` / `дополнение` actions. The 64 px service bar keeps overflow tiles above
it; service history opens from its tile and the pencil opens a compact status row.
The preview check now captures 1920×1080 and 1280×720, verifies that the upper tiles
do not cover the card, and exercises both view modes. The general ARM browser check
verifies the embedded map dialog.
At 760 px, the open DDS card uses a compact journal toolbar without overlapping
date, clock, navigation or new-card action; search remains available on the journal.
The ARM realism and onboarding browser fixtures cover inbox polling and the current
13-step tutorial. Card view/edit mode is always a boolean, including for cards whose
lock flag is absent.
