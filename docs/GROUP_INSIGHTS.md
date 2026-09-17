# Group AI recommendations

The assessment page lets a teacher request an advisory practice plan for one of
their groups. It is deliberately separate from assessment: generating or reading
a recommendation never changes a card, grade, assignment, lesson or group roster.
The teacher reviews the result and creates any assignment manually in the portal.

## Data boundary

Backend builds the model input from completed attempts owned by the requesting
teacher and frozen to the selected group. The input contains only group-level
counts, generated evidence keys, recurring configured criterion/sequence labels,
scenario-level attempt/score aggregates, and up to 100 entries from the teacher's
currently visible enabled scenario catalogue. At most 20 recurring-error rows and
120 aggregate evidence rows in total are sent; the saved response reports omitted
error rows, other evidence rows and catalogue entries. It contains no
student account fields, card values, dialogue, feedback, expert reasons or individual
progress rows. Scenario IDs and author-controlled scenario titles, objectives and
curriculum metadata are included so suggestions can link to real exercises. Those
author-entered fields, including configured error labels, must themselves contain
only synthetic/authorized content.

Saved responses include the bounded evidence rows so the UI can translate opaque
model references into aggregate labels and counts. A permanent small-sample warning
is added when the snapshot has fewer than five completed attempts or fewer than
three participating students, independently of model output.

At least two completed attempts and one available scenario are required. This is
only a technical minimum, not a claim of statistical significance. Small samples
can be unstable and must be interpreted by the teacher.

## Validation and lifecycle

The provider must return strict JSON. Recurring errors are separated by rubric/policy
revision and their denominator includes only attempts carrying that exact configured
criterion or step. Difficult skills may cite only evidence keys
that Backend generated. Every exercise recommendation must cite at least one such
key and one currently available scenario ID. Unknown/duplicate references, extra
properties, malformed JSON and empty recommendations fail closed with a generic
502 response. Raw provider messages, prompts and credentials are never returned.
The request can be retried.

Successful and mock results are saved in local SQLite. A SHA-256 fingerprint covers
the aggregate input, available catalogue, provider and model. An identical POST
returns the saved result without another model call. GET compares the saved
fingerprint with current state and labels the snapshot stale when group data,
scenario availability or model configuration changed; it does not silently
regenerate. Concurrent matching requests are deduplicated within this Backend
process.

Mock mode never calls a model. It returns an explicitly marked deterministic fixture
based on the same aggregate evidence. OpenRouter sends the bounded aggregate and
scenario catalogue outside the machine; Ollama sends it to its configured host.
Only synthetic/authorized training data may be stored in the trainer.

## Limits

Recommendations are advisory, not official 112 regulations. Evidence keys prove
that a suggestion points to an aggregate supplied to the model, not that its
pedagogical conclusion is correct. Current caching and locks are single-process.
There is no automatic assignment, personalized allocation, student ranking or
grade mutation.
