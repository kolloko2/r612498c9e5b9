# Local web console

The map now uses Backend regional viewport/search routes, supports dragging,
arrow-key navigation and wheel zoom, and can search before coordinates have been
saved. The former centre-only JSON and optional PNG tiles are no longer fetched.
Prepare/copy the full regional package as documented in `docs/MAPS.md`.

`/audit` is the admin request-audit viewer. `/login` offers directory authentication
only when configured; the admin portal explicitly links non-admin users. Statistics
support safe CSV; operations support sanitized XML export (no import). Student
sessionStorage drafts restore only on explicit choice and survive network recovery;
SIP calls are not redialed automatically. See the security/deployment documentation.

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
numbers or the injured flag. Saved cards place the description on the right;
desktop header/footer remain visible around scrollable content.

`/map?sid=UUID` opens a read-only incident coordinate window through the existing
owner-scoped API. Coordinates must first be saved on the card. Provide authorized
offline Web-Mercator XYZ PNG tiles at `assets/map-tiles/{z}/{x}/{y}.png` (zoom2–19)
for a geographic background, with the data provider's required license/attribution.
No tile pack is included. Without it, the UI explicitly shows only a grid/marker,
not a geographic map. It does not determine coordinates from an address or device.
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
Completed reports have a separate on-demand AI review action, a cloud-data notice,
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
series alongside standalone assignments. Series use text, selected by Backend.
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
banners live in the project documentation instead of every screen. The UI still shows
the active mock provider, external-provider data transfer, destructive confirmations,
data-loss warnings, errors and operationally relevant limitations.
