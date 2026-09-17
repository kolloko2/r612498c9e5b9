# Student workspace foundation

Reference: supplied «Работа с АРМ-112 для ДДС от ОКр_ГСИ.pdf», pages 12, 15–16.
Replicated composition: incident journal, search/control header, phone row, caller
identity, address block, description, right-hand questionnaire, orange save bar,
saved-card mode and service response history. Training controls live in dialogs.
The screenshot is the available visual reference, not an executable original;
pixel-identical behavior of unpictured screens cannot be inferred.

## Try it

1. Start `python tools/run_workspace.py`, open `http://127.0.0.1:3000`.
2. Create an incident and select a text scenario. Ask the caller questions.
3. Close the conversation dialog. Fill the card, choose features and services.
4. Save, inspect the read-only card, use «дополнение» to edit again.
5. Add a service status and comment. Complete the session with «отработана».
6. Inspect the timeline and export JSON. Reopen the card from the journal.

## Configuration

Use the root ignored `.env` for `LLM_PROVIDER`, `OPENROUTER_API_KEY` and
`OPENROUTER_MODEL`. No credentials are compiled into frontend assets.
The launcher creates a random process-only Backend token unless one is set.
For existing SIP installations, set a stable `DIALOGUE_TOKEN` and configure the
same value as Voice `BACKEND_TOKEN`; set Backend `VOICE_API_TOKEN` to Voice's
`API_TOKEN`. Keep the existing model and Asterisk setup. A text session cannot be
converted to SIP mid-call. Ordinary browser speech does not require a neural
voice download and is independent of Voice's existing synthesizers.

## Current boundaries

- The supplied classifier is imported as a versioned catalog with three dependent
  feature levels and a server-validated final type. Blank source features remain
  explicit choices. Draft cards may retain incomplete selections; legacy cards
  keep their saved type until reclassified. Core-service routing uses explicit
  source branches N:AB. Other agencies remain manual; concurrent conflicting
  source types are displayed without an invented priority.
- No new medical/operational regulations; service statuses are recorded as input,
  not enforced as an official workflow. Reference criteria provide deterministic
  field scoring, not semantic/grammar AI assessment or expert grading.
- Local single-user access and SQLite persistence, one Backend process.
- Real SIP quality, microphones and acoustic playback require the configured
  Voice/Asterisk stand. Unit tests do not prove physical audio quality.
- In the delivered contour the scenario text reaches only the local Ollama host.
  If OpenRouter is explicitly enabled for development, the full scenario text
  leaves the machine. Use synthetic data only in either mode.
- Browser synthesis depends on installed voices; text remains usable without it.

## Core-service selection

Use the plus button or «основания» to open the notification list. Review extra
flags, click «Подобрать по признакам», then explicitly add the suggested services.
Existing manual selections are retained. Save the card to persist both selected
services and the server-calculated routing grounds. Read-only cards show the
saved snapshot, not a recalculation against newer rules. A preview is not a
notification and not an expert assessment. Unsupported agencies require manual
selection; the original workbook is unchanged.

## Reference assessment

Configure a rubric at `/instructor` before starting a new attempt. Criteria compare
exact normalized values, all literal text fragments or an exact service set. The
report displays weighted points and criterion differences; timing is a separate
comparison with the configured limit. No rubric means no score. The assessment
does not examine dialogue quality, grammar or the order of operational actions.
An optional on-demand AI text review separately reports grammar, clarity and
possible contradictions; its remarks require teacher review and never alter scores.
Rubrics freeze at session creation and reports persist at finish. The configuration
page is currently accessible to the trusted local user, not protected by user roles.

## Verification

Backend tests cover revision conflicts, hidden facts, idempotent turns, terminal
read-only state, audit, concurrent turns and the OpenRouter HTTP contract.
Voice retains its existing regression suite. Run each suite in its service folder.
Run `node --check frontend/assets/student.js` for JavaScript syntax.
Classifier tests cover catalog integrity and source preservation; workspace tests
cover canonical types, inconsistent features and stale classifier versions.
Regenerate student OpenAPI using `python tools/export_workspace_contract.py`
(requires PyYAML).
