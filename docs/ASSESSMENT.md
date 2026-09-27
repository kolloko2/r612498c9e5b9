# Assessment and statistics

## Three distinct results

For prepared DDS cards the 30-second check is issuance-to-open and the
3-minute check is issuance-to-first-record; acceptance/refusal is a separate
decision check. The DDS reviewer can
also check teacher-authored `brief_keywords` and `result_keywords` against the
accepted duty briefing and closing comment. A manual notification is not evidence
of that briefing. These literal checks remain deterministic; a teacher may override
the final result with the audited expert decision below.

1. `evaluation` is the existing immutable rubric-based field score and timing.
2. `policy_result` checks teacher-defined thresholds and ordered actions, once on
   completion. No policy is retroactively applied to old attempts.
3. Expert decisions are append-only records, with reason, author, timestamp,
   revision and request UUID. The latest non-revoked decision supplies the effective
   score/verdict; revocation restores the original automatic score/policy verdict.

The expert deliberately supplies both a score and pass/fail decision. These may
override the policy and need not agree with its passing threshold; the required
reason and full history make this explicit. The application has no endpoint to
erase or rewrite expert history. This is an application audit, not a cryptographically
tamper-proof log against a database administrator. Up to100 decisions per attempt.
Concurrent/stale edits use revisions; identical retry UUID/body is idempotent.

Only the frozen owning teacher can grade a completed attempt, including action-only
or legacy attempts without an automatic score. Students see only their own completed
assessments. Active attempts409, foreign/unowned404, wrong role403. Expert grading
does not mutate the original card, rubric report, event timeline or AI review.
The dedicated assessment page/API shows effective grades; legacy field-report
exports remain the original automatic report rather than silently changing meaning.

## Thresholds and action sequence

Policy checks are independent: optional minimum score0–100, maximum erroneous field
criteria0–30, maximum erroneous sequence steps0–20 (default0), and optional timeout
failure using the frozen rubric's time limit. A field error means one failed rubric
criterion, not a word/character count. Missing rubric/limit makes a dependent check
unknown, not zero or a pass. Any failed check gives false; otherwise an unknown
check gives null; otherwise true. Empty policies are rejected: use null to disable.

Up to20 ordered steps, each with a unique ID/label and a supported audit event:
card.saved, service.updated, notification.recorded, card.processed, card.linked,
call.requested, session.finished. A service filter is allowed for service.updated
or notification.recorded; a status filter only for service.updated. Filters are
exact strings, using the canonical saved status (e.g. Принята), not legacy aliases.

The evaluator greedily matches each step to the first matching event after the
previous matched step. Unrelated actions are ignored; repeated steps need distinct
events. A later successful repetition can satisfy the sequence. A required action
seen only before the cursor is out_of_order; an absent one is missing. The result
records the supporting event sequence number. Finish contributes its actual final
event. Rejected API operations, button clicks, unsaved edits and spoken question
order are not recorded as successful actions and are not evaluated here.

Policies are private before completion, stored per teacher/scenario with revision
history in existing SQLite. Standalone sessions freeze at creation; fill lesson
pools at start; action templates at preparation. Action exercises can have a
sequence-only verdict without inheriting a score for prefilled fields. Merely
labelling difficulty does not alter policies or scores. Source-specific service
state transitions remain independently enforced by ARM. No official thresholds
or clinical/procedural correctness are invented by this feature.

## Statistics boundaries

Student totals cover their own attempts across teachers. Teacher totals cover only
attempts carrying that teacher's frozen ownership, optionally restricted to an owned
group. A group summary also lists current members with no attempts, and retains past
participants with owned attempts. Ungraded/null scores never count as numeric zero.
`graded` counts numeric scores; `unassessed` counts completed attempts without a
pass/fail verdict. Thus a scored attempt can still have an undefined verdict.

Average score uses graded completed attempts only; average time uses completed
attempts with a recorded duration. Expert override/revocation changes effective
totals without changing automatic diagnostic error counts. Error rate uses only
attempts evaluated against that same teacher/scenario/rubric or policy revision
and criterion/step ID. Changed rule versions are not mixed; missing assessments are
not counted as error-free. AI advisory findings are not counted as confirmed errors.

Totals include all scoped attempts. Progress lists the last200 completions in time
order, with level/profile and source; teacher rows identify the student. It is a
history, not a claim that incomparable scenarios prove mastery. Scenario averages
may combine differing configurations; use the row details for interpretation.
There is no cross-teacher leaderboard, predictive scoring or external analytics.

The assessment page can download either the complete current statistics snapshot
as JSON or one selected section as CSV. CSV is generated in the browser from that
already authorized snapshot; it does not make another API request. Files use a
UTF-8 BOM, semicolon delimiters, CRLF rows and quoted cells. String values beginning
with `=`, `+`, `-`, `@`, tab or carriage return (including a formula marker after
leading whitespace) receive a leading apostrophe to prevent spreadsheet formula
execution.

CSV columns are stable API field names. `summary` contains `attempts`, `completed`,
`graded`, `passed`, `failed`, `unassessed`, `average_score`, `average_seconds`.
`progress` contains `session_id`, `title`, `finished_at`, `student_id`,
`student_name`, `score_percent`, `passed`, `source`, `difficulty`, `dds_profile`;
student identity cells are empty in a student's own export. Teacher-only `students`
contains `student_id`, `display_name` and all summary columns. `typical-errors`
contains `key`, `label`, `count`, `attempts`, `rate_percent`. Missing/null values
are empty cells, booleans remain `true`/`false`, and timestamps remain server ISO
strings. Empty collections still export their header row.

## Error heatmap and XLSX export

`error_heatmap` presents the same criterion and sequence outcomes as
`typical_errors`, grouped as scenario × check with a failure share per cell. It
introduces no second way of counting: a cell's `count`/`attempts` sum back to the
matching typical-error row, and an unconfigured check simply produces no cell.
The assessment page renders it as a table with a five-step colour scale; the
colour repeats `rate_percent` and is not a threshold of acceptable error.

The page also downloads the current snapshot as an XLSX workbook through
`statistics-workbook`. The workbook contains the same sections as the CSV export
plus the heatmap sheet, applies the same formula-injection protection, and states
in a separate sheet that it is synthetic training data and not an attestation
document. A student's workbook has no sheet about other people.

## Verification

127 Backend/proxy tests passed. Targeted follow-up tests cover teacher identity
display and proxy query forwarding. The mocked browser flow verifies policy
conflict retention, automatic criterion evidence, expert retry UUID reuse, visible
error feedback, grade/revocation audit and student-only statistics requests. JS
syntax, Python compilation, OpenAPI export and Git-visible secret checks pass.
Two existing FastAPI startup deprecation warnings remain. No real LLM/SIP call,
real 112 data, or live student grading was used for verification.

## Оценка решений диспетчера ДДС

Эталон по полям проверяет заполненность карточки. В основном режиме поля
приходят от Службы 112 уже заполненными, и такой эталон почти ничего не
измеряет. `backend/dds_review.py` оценивает то, что диспетчер действительно
решает, и выводит всё из журнала событий карточки — без модели и без нечёткого
сравнения.

Ожидания задаются в сценарии полем `dds_expectation`:

| Поле | Смысл |
| --- | --- |
| `should_accept` | профильную карточку принимают; ложь — её правильно НЕ принимать |
| `refusal_kind` | `foreign_territory`, `duplicate`, `no_works` |
| `refusal_keywords` | слова, обязательные в обосновании отказа |
| `brief_service` | кому диспетчер обязан доложить |
| `update_response_limit_seconds` | норматив реакции на доклад с места |

Проверки:

- **Приём карточки.** Профильную приняли; непрофильную не приняли. Ошибка
  критическая: принять чужую карточку хуже, чем промедлить.
- **Обоснование отказа.** Памятка требует не отказа как такового, а объяснения:
  почему и куда передана информация. Проверяется наличие заданных слов.
- **Реакция на доклад с места.** По каждой оперативной вводной: выставлен ли
  соответствующий статус и за сколько секунд. Пропущенный доклад — критическая
  ошибка.
- **Результат работ.** Перед закрытием в комментарии записан итог.
- **Доклад дежурному.** Состоялся ли и не потерян ли адрес: улица и дом должны
  прозвучать в телефонограмме.

Итог — доля выполненных проверок и отдельный список критических ошибок. Балл по
полям и разбор решений показываются рядом и не смешиваются: первый отвечает за
данные, второй за действия. Содержательную правильность реагирования
подтверждает преподаватель.

Сценарии без `dds_expectation` разбор решений не получают, поэтому прежние
занятия продолжают оцениваться как раньше.

Для готовой карточки с `owner_service` ожидания ДДС теперь создаются по
умолчанию, если преподаватель не задал их явно. `dds_review.score_percent`
становится итоговым автоматическим баллом действий в отчёте преподавателя,
статистике и адаптивном подборе; порог `pass_score_percent` сравнивается с
этим баллом. Экспертное решение по-прежнему имеет приоритет. Проверки включают
решение о приёме/отказе не позднее 30 секунд от выдачи, обработку за 3 минуты и полноту
завершения. Вынужденная остановка преподавателем сохраняет невыполненные
пункты как ошибки.
