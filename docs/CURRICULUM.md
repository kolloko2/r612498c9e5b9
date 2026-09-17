# Curriculum and reference library

## Pedagogical configuration

Difficulty is `basic`, `standard` or `advanced`; profile is `general`, `fire`,
`police`, `medical`, `gas` or `utilities`. These labels are not official DDS
qualification standards. The teacher supplies the actual facts, caller behavior,
learning objectives and reference criteria, or reviews an AI-generated preview.
Advanced is not automatically a shorter timer or a different scoring multiplier.
Mock generation remains a marked fixed fixture and does not interpret these goals.

Scenario metadata (`difficulty`, `dds_profile`, `learning_objectives`) is editable
and included in assigned exercise discovery. Objectives are student-visible and
must not contain secret reference answers. Legacy defaults are basic/general/empty.
Lesson filters are exact matches; general means general-purpose, not every profile.
Unselected filters admit all. Explicit selections outside filters are rejected,
including both types of prefilled source. Automatic resolution with filters is
limited to 20 enabled visible scenarios. Fill pools revalidate at start and freeze;
prefilled templates freeze at preparation. Issued sessions retain their metadata.
Changing a scenario never reclassifies old work or changes its immutable grade.

## Material workflow and privacy

Teachers own private drafts. They can write plain-text articles, attach one file,
select their own groups and publish. Publishing requires a group and text or a file.
Students see only published materials for current group membership, on every list
and detail request. Other teachers cannot read or update an author's documents.
No global student library or administrator bypass is implied. Updates are revision-
checked, not silent overwrites. Empty file fields preserve an attachment on edit;
`remove_attachment=true` removes it. Unpublish preserves the teacher's draft.
There is no permanent-delete UI; reuse documents or unpublish when no longer needed.

Lists omit attachment bytes and article bodies; opening detail fetches them.
The UI searches titles/descriptions and filters by level/profile; it does not
perform OCR or search inside PDF/DOCX attachments. Article bodies render as plain
text. Downloads use an opaque binary Blob, not an embedded HTML/PDF executable view.
No model reads uploaded material, and no document text becomes a system prompt.
Reference use is allowed during training; no exam-mode restriction is implemented.

Only PDF, UTF-8 TXT and DOCX, 1 byte–5 MiB per attachment. The service checks names,
extensions, basic file signatures, TXT decoding, DOCX required members and archive
bounds (1000 members/20 MiB declared expanded size; encrypted/macros rejected).
It neither extracts archives nor executes scripts. These are structural checks,
not a guarantee of document safety or an antivirus scan. Use trusted training files.
Each teacher is limited to 100 materials and 50 MiB of attachment data. Upload JSON
is bounded to 8 MiB in both Frontend and Backend, including chunked requests.
Files are stored as SQLite BLOBs under opaque IDs, never at user-supplied paths.
No new infrastructure, online storage, paid calls or external regulation content.

Published updates replace the current reference version, rather than freezing a
reference package per lesson. Previously downloaded copies cannot be revoked.
For a formally controlled curriculum, teachers should identify source/version in
the title or description. Historical document versions and read receipts are not
part of this block.

## Verification

Backend and Frontend proxy suite: 121 tests pass, including publication privacy,
foreign groups/roles, stale revisions, attachment persistence/removal/validation,
chunked upload bounds, curriculum filtering and frozen lesson metadata. Two existing
FastAPI startup deprecation warnings remain. The group-lesson browser regression
also checks level/profile filtering and the submitted selection; AI authoring's
mocked browser regression passes. Provider requests are mocked; no paid model call,
real emergency-service data or physical call is used in this block.

The materials browser flow passes teacher upload/publication, conflict retention
inside the editor, attachment preservation on edit, student filtering/plain-text
rendering and a real browser download event (mocked HTTP). The student library
screenshot was visually inspected at 1440x1000. JavaScript syntax, OpenAPI export,
diff whitespace and Git-visible secret-pattern checks pass.
