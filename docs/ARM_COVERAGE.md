# ARM simulation: implemented behavior and reference boundaries

Reference: supplied `Датасет.zip`, `Работа с АРМ-112 для ДДС от ОКр_ГСИ.pdf`.
This reproduces the supplied training material, not independently verified current
112 regulations. No real services, telephone numbers, maps or agencies are contacted.

| Reference | Implementation |
| --- | --- |
| pp12–16 journal and editable/saved card | Existing two-column card; saved view uses plain selected facts; incident and exercise status separated |
| p14 notification list | Resolved services automatically appended on save; notified services cannot be removed; manual additions preserved |
| p16 telephone notification record | Service, destination, phone, recipient, message, operator/time in a separate synthetic log |
| pp21–25 service responses | Initial acceptance/refusal; forward phases; terminal work completion/refusal; optional order number; collapsed service tile opens status/comment history; 103 completion-without-brigade exception |
| p26 comments | Refusal requires a comment; other response comments optional |
| p27 card status | Registered/processed, response delay and completion derived separately from exercise finish |
| pp35–40 search | Own-card date/address/district/area/description/service/transport/status filters over paginated history |
| p16 additional controls | Directed links to own cards, saved-card print, processed marker, separate exercise completion |

The whole source range N:CU is retained; detailed source columns and unresolved
conditions are documented in ROUTING.md. Text requiring agreement or a missing
territorial/object decision is not silently treated as permission to notify.
There are 86 source columns, 64 separate destination/channel entries and 21
condition flags. A channel entry is not a geocoded local service office. The
source's Mосгортранс injured column AM is empty; it does not fall back to AL.
Free-text source conditions O637/O638, AB637/AB638, Y864 and agreement-required
P/U/X/AA1293 remain explicit unresolved decisions.

Scope boundaries: no live external-information-system integration, municipal GIS/
geocoding, real SMS or dispatcher telephone calls. The source PDF does not define
every toolbar icon's behavior, nor supply all territorial/object registries. Unknown
behavior is not invented. The student has a training role, not an authenticated
employee of each external service. Marking processed confirms the trainee's action;
it is not evidence of real voice notification. Printed output is browser-local.

## Synthetic VIS delivery and DDS incoming journal

Pages 7–10 describe cards arriving from external information systems (VIS), and
page 20 says a VIS may return a revised incident type that adds response services.
The new delivery exercise records an immutable snapshot of an already saved card;
it does not recalculate the card, contact a VIS, or claim successful real delivery.
It is available only for an assigned session and is idempotent by client-generated
`message_id` (maximum 20 delivery exercises per card).

The receiving journal is restricted twice: by the teacher who owns the assignment
and by the card's frozen `dds_profile`. A curriculum profile (`general`, `fire`,
`police`, `medical`, `gas`, or `utilities`) is only a training filter. It is not an
employee identity, an official DDS category, or permission to inspect other
teachers' or students' cards. The teacher must select one profile at a time; there
is no unscoped all-student incoming feed. Opening a row appends a separate receipt
audit event and never fabricates a response-service status in the source card.

Page 20 also states that some non-responding information recipients are omitted
from the visible notification strip. Neither the PDF nor the supplied classifier
identifies those recipients per incident. The simulator therefore never derives
them from `нет реагирования`, `Без оповещения`, a DDS profile,
or a general category. A student may enter up to 20 clearly synthetic training
labels when creating a delivery. They are stored outside `card.services`, omitted
from the compact incoming list, and disclosed in the owned detail as manual
training input. Labels that duplicate visible response services are rejected.

The two append-only tables are `arm_vis_deliveries` (delivery snapshot/state) and
`arm_vis_audit` (send/open actor, time and bounded metadata). No transcript or
hidden scenario facts are copied into a delivery. All API results carry
`simulated: true`.

API paths (all use the existing service and browser-session authorization):

- `POST /api/v1/student/sessions/{sid}/vis-deliveries` with
  `{message_id, informational_recipients:[]}` creates or repeats an idempotent
  delivery of the student's own saved, assigned card.
- `GET /api/v1/student/sessions/{sid}/vis-deliveries` lists only that student's
  delivery exercises for the owned card.
- `GET /api/v1/instructor/dds/profiles` returns the fixed curriculum profile list
  and teacher-owned incoming counts.
- `GET /api/v1/instructor/dds/incoming?profile=...` returns the teacher-owned,
  profile-scoped journal without hidden-recipient labels.
- `GET /api/v1/instructor/dds/incoming/{delivery_id}?profile=...` returns one owned
  snapshot including manually entered informational-recipient labels.
- `POST /api/v1/instructor/dds/incoming/{delivery_id}/open` with `{profile}` marks
  the simulated journal row opened idempotently and appends an audit event.

`/dds` is the teacher-facing incoming journal once its page and router are mounted
by application assembly. It repeats the simulation warning and profile boundary.

## Map/geocoding dataset check

No `.mbtiles`, `.pbf`, `.gpkg`, `.geojson`, `.shp`, or local XYZ map pack is
present in the supplied workspace/archive. The only ZIP found in the project is a
MicroSIP package; the classifier catalog contains routing/classification data, not
geocoding geometry. Consequently no address geocoding or service-territory lookup
was added. The existing coordinate window remains a clearly labelled grid unless
an authorized, licensed local tile dataset is installed. Coordinates are still
manual paired WGS84 values and never drive notification routing.

Source screenshots inspected: pages 12, 15 and 16. Whole-program pixel identity
cannot be claimed from these three reference states: other roles/windows differ,
and the source's operator and service-dispatcher screens are not interchangeable.

## API additions

Update after reviewing pages 13,17,18,35–40: journal rows have accessible inline
disclosure; extended search has date/time, incident/features, structured/descriptive
address, district/area/region, service, number and caller fields. Separate
emergency/important/bookmark flags persist; emergency is not inferred from injury.
New sessions freeze registration metadata instead of hard-coded operator numbers;
old records display unknown values. Saved cards place address on the left and
description on the right, with desktop header and action footer kept visible.

Paired nullable WGS84 coordinates persist independently from caller address and
open via `/map?sid=UUID` in a separate read-only window. Backend validates finite
bounds and ownership. Optional local XYZ tiles remain uninstalled; the displayed
fallback grid is not a street map or Yandex integration. External VIS metadata,
geocoding, actual deliveries and whole-program pixel identity remain unverified.

All paths below are under `/api/v1/student`, with existing user/service guards.

- `GET /sessions?limit=100&offset=0`: own history, limit1–200, offset>=0.
- `POST /sessions/{sid}/processed`: idempotently mark a saved card processed;
  does not finish the exercise. Unsaved=409.
- `POST /sessions/{sid}/notifications`: `{message_id:UUID,service,destination,
  phone:"",recipient,comment}`. Service must be saved; destination/recipient1–160,
  comment1–1000, phone<=40. Returns session with notifications and operator/at.
  Same ID+body is idempotent; conflicting reuse409; maximum200 entries.
- `POST /sessions/{sid}/links`: `{target_id:UUID}` to an owned card; directed link
  `{id,number}`, duplicates ignored, maximum50. Self422, foreign404.
- `POST /sessions/{sid}/services`: `{service,status,order_number:"",comment:"",message_id?:UUID}`;
  legal forward transitions only, refusals require comments, terminal states locked.
  Same message ID/body deduplicates; conflict409. Legacy aliases normalize on new
  writes: Выезд→Начало реагирования, Завершение→Работы завершены, Отбой→Отказ от
  выполнения работ. Existing history remains unchanged. Technical receipt is not
  a selectable manual status. For initial 103 completion the comment must contain
  «Завершение работ без бригады».

  The request may also include `order_number` (string, default empty, maximum 80
  characters). It is stored with the latest service state and in the append-only
  `service.updated` audit event visible to the owning teacher. Older records and
  clients without the field remain valid. Journal statuses `Не оповещено`, `Отказ`
  and `Не завершено` are highlighted as abnormal states in the student journal.

All mutations respect teacher stop and completed-exercise locks. `/finish` alone
finalizes the exercise and permits the next lesson card. Save unions submitted,
previously saved and resolved rule services (maximum100 names, each1–160 characters)
and initializes new service states as Добавлена. Nothing actually gets dispatched.

Session detail exposes `allowed_service_statuses` per service; lists/details expose
`incident_status`, independent of training `status`. The >30-second and >48-hour
indicators use saved/registration times and freeze at exercise finish. Refusal
requires checked_by to derive Отказ; the simulator never fabricates that check.
Completed exercise scores/history remain unchanged by new routing rules.

## Verification (2026-09-15)

The complete Backend suite and Frontend proxy tests pass: 112 tests, with two
existing FastAPI startup-deprecation warnings. Mock-HTTP browser flows pass for
ARM actions, teacher/student lessons and AI generation. ARM browser coverage
includes saving, allowed service transitions, telephone messages, card links,
processed vs exercise finish, print invocation, filters and paginated history.
Editing, saved-card and journal screenshots were visually inspected at 1600x1000
against the source's main screen states; this is not a whole-program pixel audit.
JavaScript syntax, OpenAPI export, diff whitespace and secret-pattern checks pass.
No paid model request, physical call, real dispatch or printer output was exercised
in this verification block.
