# 112 AI Trainer

Практический режим ДДС: кнопка «Практика с подсказками» ведёт по сохранённым
действиям, а завершение показывает невыполненные условия. Первое упражнение
`tools/data/dds_guided_practice.json` включает четыре доклада и оценку фактов
в комментарии; назначение — `tools/assign_guided_practice.py` через API преподавателя.
Подтверждение новых обязательных фактов телефонограммы не ждёт генерации модели;
уточняющие вопросы остаются в диалоге ИИ. Прежние занятия не переписываются.

Телефонные доклады: технические номера бригад исключены из озвучки,
но сохранены в карточках. Дежурному явно запрещено повторно запрашивать
уже принятые сведения; он уточняет только недостающие факты.

Сервер Ubuntu: [настройка и обслуживание](docs/SERVER_INSTALLATION_2026-09-27.md).
Внешний HTTPS-порт задаётся `WEB_PORT`; внутренний порт Frontend остаётся 3000.

Комплект руководств для передачи от 28.09.2026: [документация DOCX/PDF](docs/delivery/README.md).
Основной документ для жюри объединяет описание продукта, установку и пошаговые инструкции со скриншотами. Ответственный за разработку и документацию — Кодзаев Николай Петрович.
Содержит инструкции сервера, ПК ученика и преподавателя, описание технологий,
матрицу ТЗ/Q&A, измерения, модель угроз и программу испытаний. Комплект описывает проект и его использование; формуляр и документы сдачи и приёмки исключены.

## Комплект в репозитории

Здесь опубликованы исходники, учебные сценарии, конфигурационные примеры,
тесты и документация. Начало работы: [развёртывание](docs/DOCKER_DEPLOYMENT.md),
[текущая эксплуатация](docs/CURRENT_RUNBOOK.md),
[перенос на машину защиты](docs/DELIVERY_CHECKLIST.md).
Локальная папка `artifacts/` (видео, исходные документы заказчика, журналы
испытаний), рабочие ENV, ключи, БД, модели и карты не публикуются в Git.
Исторические ссылки на `artifacts/` ниже доступны только на исходном стенде.
Модели и карты нужно подготовить отдельно по инструкции развёртывания.

27 сентября: критерий производительности модели — CPU без видеокарты.
Телефонный ход ДДС/112 ждёт локальную модель до 15 секунд и затем использует
ответ по подтверждённым фактам; это ограничение ожидания, не гарантия, что
длинная генерация успеет на минимальном процессоре. Устаревшее предупреждение
об облачной передаче убрано из ИИ-авторинга. [CPU-проверка и границы](docs/LOCAL_MODEL.md).

Телефонная модель теперь отделена от генерации сценариев: `PHONE_LLM_MODEL`,
контекст 4096, короткая история, CPU-only и сохранение весов в памяти. Исправлен
прогрев из Docker через настроенный `OLLAMA_URL`; повтор исправления роли входит
в общий 15-секундный бюджет. [Сравнение моделей](docs/CPU_PHONE_2026-09-27.md).

Актуальная проверка 27 сентября: [SIP, параллельные разговоры, карта и поставка](docs/ACCEPTANCE_2026-09-27.md).

27 сентября: Voice получает лимит 4 ГБ вместо 2 ГБ; добавлены реальная
параллельная SIP-проба, голосовые уточнения в полном DDS-проходе и read-only
проверка комплекта поставки. [Перенос на машину защиты](docs/DELIVERY_CHECKLIST.md).
Критические вопросы о завершении/прибытии, сроках, причинах и пострадавших
не подтверждаются из предположений ученика: используются известные доклады.

27 сентября: телефонные доклады бригады поддерживают уточняющие вопросы через
LLM по текущему докладу, ранее полученным сведениям и адресу карточки. Будущие
вводные и эталоны не передаются; ответы не открывают статусы без подтверждения
доклада. При недоступности модели используются ответы по доступным фактам.
Проверка локальной модели: [диалог бригады и замеры](docs/FIELD_DIALOGUE_2026-09-27.md).

Текущее руководство: [эксплуатация перед защитой](docs/CURRENT_RUNBOOK.md).
26 сентября: обновлённые 96 упражнений опубликованы для `prepod` отдельными
версиями без перезаписи прежних сценариев. Полное SIP-занятие ДДС завершено:
пять звонков, четыре оперативных доклада, оценка 100% (12/12).
Подробности и границы проверки: [результаты стенда](docs/DEPLOYMENT_CHECK_2026-09-26.md).
Все 96 билетов получили подготовленную карточку и полный авторский цикл ДДС:
бригада, четыре доклада, эталон назначения/доклада/результата и адресные критерии.
Все 96 карточек размечены существующими кодами классификатора; результаты работ
индивидуальны, интервалы докладов варьируются. Неоднозначные вводные раскрыты
в описаниях преподавателя как авторские условия, а не факты исходного билета.
Публикация остаётся за преподавателем в `/tickets`; существующие сценарии и БД
не перезаписываются. Источники и ограничения: [учебные билеты](docs/TICKETS.md).
Разделы ниже с датами — история реализации и замеров, не единый акт приёмки.
26 сентября: Silero использует ограниченный пул и кэш; поставлено 20 правил
территорий с источниками; АРМ учитывает высоту рядов служб и позволяет запустить
обучение из открытой карточки. Замер 20 реплик: 4,449 с → 2,980 с (1 → 2
прогретых процесса, только TTS, без SIP/STT/LLM).

## Доведение к дедлайну — 26.09.2026

АРМ ДДС: компактные плитки служб с временем и статусом, дополнительные ряды
над нижним рядом, исправлена видимость статусов и уплотнено рабочее место.
Голос: сохранённые состояния звонков доступны после рестарта Voice; подтверждённый
аварийный обрыв допускает ограниченный повтор вызова, включая доклад ДДС, без
сброса карточки и диалога. ARI переподключается с ограниченной частотой.
Грамматика: локальные подсказки согласования подлежащего/сказуемого, управления
предлогов, повторов и парности скобок. Неоднозначные подсказки не штрафуются.
Территории: в редакторе готовой карточки можно явно задать получателей района,
округа и ведомства (`recipient_affiliations`); они объединяются с классификатором
и справочником. Полный официальный реестр не выдумывается.
Границы и проверка: `docs/DEADLINE_HARDENING_2026-09-26.md`.

Обновлённый короткий ролик: [параллельная работа преподавателя и двух учеников](artifacts/customer-video-2026-09-23/Тренажер112_параллельная_работа.mp4).
Три независимых учебных сессии, реальные действия, текстовые доклады и общий отчёт.
Версия от 23.09: 2:13, нейтральное светлое оформление, адресные выноски, тихая музыка.
Крупно показаны ввод докладов, ответы, результат работ и поиск адреса на карте.
Проход переснят после исправления белого текста в полях реагирования; обе карточки завершены на 100%.

Демонстрация для согласования с заказчиком: [видео без звука](artifacts/customer-video-2026-09-23/Тренажер112_демонстрация.mp4),
[10 ключевых вопросов](artifacts/customer-video-2026-09-23/Вопросы_заказчику.md).
Монтаж реальных экранов на учебном mock-стенде; голосовые испытания выполняются отдельно.

Доведение пунктов 1–5 аудита: [изменения и границы](docs/CUSTOMER_FIXES_2026-09-23.md).
Доклады проверяют отрицания и адресные компоненты; материалы поддерживают локальный
OCR и поиск по всему извлечённому тексту; таймер использует серверный лимит.
Добавлены настройки эталонов, доступные ученику вводные для исправлений и импорт
территориального справочника. Официальный полный справочник территорий не подменяется
учебным примером. Развёртывание требует обновления зависимостей/образов.

Проверка 2, 5 и 20 параллельных голосовых сессий:
[измерения и ограничения](docs/VOICE_CHECK_2026-09-23.md). На текущем CPU задержка
растёт из-за очереди синтеза; приёмочные SIP/RTP-показатели пока не подтверждены.

Сверка с ТЗ, QA, ответами Str1fe и скриншотами заказчика от 23.09.2026:
[подробный аудит](docs/CUSTOMER_REQUIREMENTS_AUDIT_2026-09-23.md).
Отчёт разделяет реализованные функции, ограничения, противоречия источников
и ещё не подтверждённые приёмочные характеристики.

## Территории, проверка текста и XML — 23.09.2026

Добавлены локальные утверждаемые правила получателей по адресным полям и объекту
(`TERRITORIAL_ROUTES_FILE`), ручная проверка текста карточки/сценария и учёт
комментариев ДДС в отчёте. XML методички и рабочих мест загружается в формы
для проверки перед обычным сохранением. По умолчанию включён ограниченный учебный
набор из 20 правил с источниками, включая Щукино из QA; полный официальный перечень служб отсутствует.
Полная русская грамматика не заявляется.
Подробности: `docs/TERRITORIES_XML_GRAMMAR.md`.
Добавлен исходящий запрос обстановки назначенной бригаде с повторным использованием
SIP-докладов; текстовый путь проверен в браузере. Подробности и границы проверки —
`docs/DDS_PROGRESS_CALL_PLAN.md`. PostgreSQL не изменялся.

## Доводка ДДС по материалам заказчика — 23.09.2026

Отдельный `dds-layout.css` возвращает описание под адрес в левой колонке,
раскрывает историю над плитками и убирает постоянную таблицу реагирования
из центра карточки. Карандаш своей службы открывает действия реагирования.
Это изменение именно АРМ ДДС; форма приёма 112 и тема кабинетов отделены.
Изолированный стенд запущен: карточка и панель службы просмотрены на 1280×720,
карта нашла адрес и показала здания. Это не утверждение пиксельного совпадения 1:1.
Исключение 103 «Завершение работ без бригады» согласовано с гейтингом и
фиксацией срока приёма; явно заданный эталон выбора бригады продолжает оцениваться.
PostgreSQL и рабочие данные не менялись. Для безопасного просмотра без загрузки
рабочего .env: `python tools/run_workspace.py --review-db <путь-к-тестовой.sqlite>`.
Такой запуск использует mock и синтетический территориальный справочник, не SIP.

## Кабинеты и проверка учебного цикла — 23.09.2026

Кабинеты, редакторы, материалы и отчёты используют общую графитовую тему
`frontend/assets/cabinet.css`. Она не подключается к АРМ ученика, панели служб
и журналу ДДС: их исходное оформление сохранено. Поля сценария синхронизируются
при вводе, предпросмотр показывает телефоны и эталон решений читаемым текстом.
Список попыток преподавателя учитывает оценку действий ДДС.

В изолированном SQLite/mock-стенде пройден полный цикл: сохранение сценария,
запуск занятия, получение готовой карточки, принятие, назначение бригады,
доклад, вводные и статусы, завершение, отчёт группы и экспертная оценка.
Это не проверка физического SIP-звука и не пересборка рабочего Docker-развёртывания.
PostgreSQL не изменялся. Детали: `docs/UI_REVIEW_2026-09-23.md`.

Working local simulator for training emergency dispatch operators. The operator uses
MicroSIP extension 201; the simulated victim is controlled manually, by the local
scenario model, or through the authenticated External AI API.

License: not specified

## Current completion block (2026-09-15)

The implementation now adds: owner-scoped training PDF certificates, simulated
VIS delivery and DDS journal (`/dds`), an installed offline Moscow/Moscow Oblast map
with 778,016 address records and 3,160,639 objects, configuration XML preview/import and admin service
logs/configuration/update controls. Current details supersede older staged-only
notes below; see `docs/COMPLETION_BLOCK.md` for exact boundaries.

Disclaimer texts have been removed from the interface entirely: the operator sees
instructions, not reminders that the simulator simulates. What remains on screen is
functional — error messages, data-loss confirmations, real service/provider status
in the operations cabinet, and the warning that a cloud provider receives task
content. API fields such as `simulated` and `limitations` are unchanged; they are
part of the contract and are simply not rendered. The map now pans/zooms and queries an offline spatial
index instead of downloading a centre-only JSON; see `docs/MAPS.md`. Copy the
911 MiB `deploy/maps/regional.sqlite` package when moving the installation.

Local STT/TTS run on CPU: hybrid recognition (Vosk for partial results, GigaAM v2 CTC
via sherpa-onnx for the final one) and Silero synthesis. See `docs/SPEECH.md`.
The shipped contour runs a **local LLM on the host Ollama** (`LLM_PROFILE`;
the current standard profile is `qwen3:8b` Q4_K_M). OpenRouter stays an optional
code path that is disabled in the delivered configuration. See `docs/LOCAL_MODEL.md`.
Speech models are ignored
deployment assets, not embedded in Git. Backend application ACKs and Voice durable
outbox replay protect accepted control events across reconnects; they cannot recover
audio that never reached the server. HTTPS and internal TLS are active on this PC;
the training CA is trusted for the current Windows user with explicit consent.
Bounded Backend read-scaling/failover is available. A separate PostgreSQL streaming
standby rehearsal supports fenced manual promotion, not automatic multi-host HA.
The isolated extension-220 probe now verifies TLS registration, mandatory SRTP,
bidirectional non-silent media, Vosk recognition and Silero playback. Physical
extension-201/headset acceptance is still separate. `TOPOLOGY_VERIFIED=true` is
now enabled for the existing allowlist based on the server-side digital proof;
this does not claim subjective headset acceptance for 201.

Daily backups now also create an AES-256-GCM encrypted bundle of recordings,
outbox, configuration and audit, in addition to pg_dump. Prepare and separately
safeguard `deploy/private/backup.env`; losing that key makes the encrypted bundle
unrecoverable. Never share that file or commit it. Install host dependencies from
`deploy/requirements.txt`. The TLS Compose profile now runs backup scheduling in a
dedicated no-Docker-socket container; the host operations worker remains for
monitoring and Voice/Asterisk controls, not as a backup prerequisite.

## Учебные билеты, тепловая карта и выгрузки (2026-09-15)

Учебные билеты из поставленного датасета импортированы в каталог заготовок:
32 билета, 96 сценариев с черновиками эталонов и нормативом 30 секунд. Заготовки
выключены до проверки преподавателем; публикация на странице `/tickets` создаёт
его собственный сценарий и идемпотентна. Телефоны заявителей синтетические.
См. `docs/TICKETS.md`.

Статистика дополнена тепловой картой ошибок «сценарий × проверка» и выгрузкой
XLSX со всеми разделами отчёта; выгрузка защищена от исполнения формул так же,
как существующий CSV. См. `docs/ASSESSMENT.md`.

Журнал безопасности переведён на групповую фиксацию: один `fsync` на группу
записей вместо двух на каждый HTTP-запрос, без ослабления гарантии. Нагрузочная
приёмка на SQLite с включённым аудитом даёт 149 записей/с при требуемых 100;
на PostgreSQL в Docker Desktop — 86/с, причина разобрана в
`docs/ACCEPTANCE_LOAD.md`. Стенд запускается через Docker Compose, номер 201
зарегистрирован по TLS.

Совместимость проверена прогоном браузерных сценариев в Chromium (Chrome,
Яндекс.Браузер) и Firefox 155 — 8 из 8 в каждом движке, плюс проверка отсутствия
горизонтальной прокрутки при ширине 400 px. В рабочем месте оператора устранено
переполнение на 33 px: панель вкладок резервировала место под свисающую кнопку
создания карточки. См. `docs/COMPATIBILITY.md`.

## Роль диспетчера ДДС (2026-09-16)

Обновление 18 сентября: ИИ-авторинг теперь умеет создавать отдельный черновик
готовой карточки ДДС с оперативными вводными и эталоном решений; прежний цикл
заявителя остаётся отдельным режимом. Преподаватель может сохранить исправления
неудачных ответов ИИ, которые учитываются в следующих генерациях, докладах и
разборах только в его учебном контуре. Опубликованные им методические материалы
теперь также доступны модели ограниченными текстовыми выдержками. Оценка ДДС проверяет указанные в эталоне
факты доклада и итогового комментария, а 30-секундный норматив теперь измеряет
выдачу → первый статус «Принята» или «Не принята» согласно памятке ДДС.

Первоначальное уточнение выделяло роль диспетчера профильной ДДС; поздние ответы
заказчика подтвердили оба режима 112 и ДДС, с фокусом телефонии на ДДС. Добавлен доклад
дежурному должностного лица службы из АРМ: диспетчер вызывает службу, называет
адрес и тип происшествия, дежурный подтверждает приём, а принятый доклад
записывается телефонограммой. Минимальный сценарий работает без модели.

Нормативы ДДС по ответу заказчика 27.09.2026: 30 секунд от появления карточки
до её открытия, 3 минуты до первой записи (статус и текст); общее время работ не
нормируется. Карточка отработана, когда пройдены все статусы с комментариями.
Добавлены одновременные карточки, адресные задания по рабочим местам, счётчик
ошибок ручного ввода с критическими опечатками в адресах и подбор уровня
сложности по результатам. Приём голосового вызова от заявителя сохранён как
отдельный режим; живой голосовой заявитель — расширение базового телефонного цикла ДДС. См.
`docs/DDS_DISPATCHER.md`.

## Речь и режим новичка (2026-09-16)

Распознавание переведено на двухступенчатый режим: быстрый промежуточный текст
Vosk и точное финальное распознавание GigaAM v2 через sherpa-onnx. На
воспроизводимом наборе фраз WER снизился с 19,1 % до 4,3 %, названия улиц больше
не искажаются. Исправлены две ошибки, мешавшие нативному запуску на Windows:
рукопожатие с воркером ломалось на CRLF, а Vosk не открывает модель по пути с
кириллицей. См. `docs/SPEECH.md` и `tools/stt_benchmark.py`.

Добавлен режим новичка — пошаговое объяснение рабочего места с подсветкой
элементов; запускается сам при первом входе на пустом рабочем месте.

## Локальная модель (2026-09-16)

Проект переведён на локальную модель через Ollama: внешние сервисы контуру
запрещены, и это требование постановщик подтвердил на Q&A. Администратор
выбирает профиль в техническом кабинете — без модели, быстрая (qwen3:4b)
или точная (qwen3:8b). На текущем стенде включён точный профиль.

Исторические замеры qwen3:4b на процессоре: генерация сценария с эталоном
3–6 секунд, реплика дежурного 0,4–0,7 секунды. Разбор текста карточки требует
модели не менее 4b: qwen3:1.7b не выдерживает требование дословных цитат, и её разбор
отклоняется проверкой. Исправлены три вещи, без которых модель не работала —
короткий таймаут, выгрузка весов между запросами и рассуждения вместо ответа.
См. `docs/LOCAL_MODEL.md`.

## Security, directory login and offline recovery

`/audit` exposes admin-only request metadata by UTC date. Daily JSONL files and
gzip archives live in the persistent operations directory; retention minimum is
183 days and automatic deletion is disabled. No passwords, headers, request bodies
or query strings are recorded. This complements business audit; it is not an
audit of unsaved UI clicks or media packets. Backend and Voice HTTP/WS metadata
are selectable separately in the audit cabinet. See SECURITY.md.

The login page offers LDAP/AD when LDAPS ENV configuration is complete. An admin
must explicitly link an existing non-admin account to its directory username;
directory authentication never grants roles or creates users. An isolated test
OpenLDAP overlay is available in deploy/directory; see docs/DIRECTORY_AUTH.md.
Local admins retain their separate login. Test infrastructure is not a real AD domain.
On this workstation the synthetic LDAPS directory is running and `trainer.test`
successfully logged in as explicitly linked student `ldap-demo`. Use
`python deploy/manage.py up --directory` for subsequent starts with this fixture.

Statistics now export CSV as well as JSON. Technical configuration supports a
sanitized diagnostic XML snapshot plus a separate validated settings XML import.
Student drafts survive reload and are preserved across reconnect/teacher stop.
An open student screen can request bounded SIP redial after known transport
failures; ordinary hangup and teacher stop never trigger automatic redial.
Operations monitoring includes directory/LB services and counts healthy Backend
replicas, so one healthy replica cannot hide a missing or failed second replica.
TLS certificate provisioning and additional-CA client support are described in
docs/TLS.md. Full SIP/SRTP, PostgreSQL TLS and cluster failover remain separate work.

## Docker / PostgreSQL / SIP deployment

Technical administration is available at `/operations` to administrators: host
worker snapshots, operational alerts/events, Voice/Asterisk controls and scheduled
daily encrypted backups. The backup container runs with the TLS Compose deployment
independently of the browser or Windows task; keep the host worker for monitoring
and service controls as described in `docs/OPERATIONS.md`.
No Docker socket or secret-management console is exposed to the web application.
Without the worker, the screen explicitly reports unavailable/stale data.

The root Compose now builds real Backend, Frontend, Voice and Asterisk images with
PostgreSQL 16 and persistent data volumes. The local LLM is served by Ollama on the
host over `OLLAMA_URL`; OpenRouter remains an explicitly Internet-dependent
development option and is not enabled in the delivered configuration. Start with `python deploy/prepare.py`,
then `python deploy/manage.py build` and `python deploy/manage.py up`. Read
`docs/DOCKER_DEPLOYMENT.md` BEFORE migrating existing SQLite data or enabling LAN
access. SIP starts in media-spike mode until real audio topology is verified; it
must not be mistaken for ready speech recognition or a conversational AI caller.

The deployment notes supersede historical SQLite-only and absent-SIP-container
descriptions below. Native SQLite mock/development remains supported.

On this workstation all five Compose services are healthy. Migrated 20 tables /
62 records, preserving 5 users and 8 cards; source SQLite and both SQLite/PostgreSQL
backups are retained. Portable MicroSIP201 is registered with the real Asterisk.
SIP/RTP bind to loopback (5060 and20000–20199); Voice remains in spike mode pending
headset and speech-provider verification. The image archive and exact verification
limits are recorded in `docs/DOCKER_DEPLOYMENT.md`.

## Acceptance work — 2026-09-15

Latest ARM/performance block: paired WGS84 incident coordinates and a separate
read-only map window; source-shaped extended search and expandable journal rows;
independent emergency/important/bookmark flags; frozen real teaching registration
metadata; saved-card description on the right with persistent header/footer.
No offline map pack is installed, so the map explicitly shows a grid, not streets.
Full pixel/behavior identity with every source screen is still not established.

SQLite now uses WAL and FULL synchronous commits. A bounded 5-second offered
burst completed 750 writes at 88.55/s on this desktop; this is not certification
for another server and still below the 100/s target. See ACCEPTANCE_LOAD.md for
methodology and variability. Run the same harness on the intended server.
SIP startup preflight found no configured Ubuntu-24.04/Asterisk/MicroSIP stand on
this PC; it exits clearly before starting a retrying supervisor. Installation or
the address of an existing training server is needed, not a fake mock success.

Group AI practice recommendations now work through the configured LLM adapter;
a real model-backed UI run identified the deliberately incorrect house number in
two synthetic demo attempts (100% and 66.67% on a three-field teaching rubric).
The demo also exercised manual scenario/rubric creation, a two-card group lesson,
dialogue, classification, card saving and a service status with an order number.
`output/demo-20260915/demo.mp4` is a silent sequence of actual browser captures
with idle gaps cut, not a SIP/audio recording. Demo data remains available locally.

ARM service history now expands per service, supports an optional order number,
and highlights abnormal incident states. A real generation response missing only
`opening` now reuses its supplied `incident`; strict validation otherwise remains.
See `docs/ACCEPTANCE_REVIEW.md` for the source-page matrix and unresolved expert
questions. Full ARM fidelity and real SIP acceptance are **not established**.
See `docs/ACCEPTANCE_LOAD.md` for measured load results and Voice stand blockers.
The BFF now reuses upstream HTTP clients without forwarding upstream cookies.
On this machine, 100 concurrent reads took 0.422s (previously 72.401s); the
bounded save burst reached 77.20 writes/s, so the 100/s target remains unmet.

Latest full Backend check: 144 passed; launcher checks: 2 passed; ARM and map
browser checks passed. Last Voice check: 33 passed (not rerun in this block).
These supersede older counts below. Mock tests do not prove physical voice quality.

## Group voice and live supervision

Teachers can select text or SIP for fill/mixed lessons and assign a separate
provisioned training extension to each student. Configure matching Asterisk
endpoints and Voice ALLOWED_EXTENSIONS first; the default stand only has201.
New fill cards start the assigned call when opened in the student's browser;
prefilled action cards never call. Completion hangs up before the next card.
Connection errors permit manual retry; teacher stop blocks issuance and can be
retried after Voice recovery. One active call per extension is enforced in Voice.

The teacher's live participants view shows current cards, elapsed time, last saved
action, provider errors and latest completed automatic results. Visible dashboards
refresh every5s without overlapping polls. This is saved activity, not proof of
online presence, and active work is not silently graded. See API_CONTRACT.md.
Real multi-phone audio requires the configured Voice/Asterisk stand and is not
established by mocked lifecycle tests. These additions supersede text-only notes
in the historical development sections below.

Verification: 128 Backend tests, 33 Voice tests and 2 frontend proxy tests pass.
Mocked Edge browser flows cover text/SIP next-card and reload, teacher live view,
preserved feedback drafts and SIP setup. JS syntax, Python compilation, OpenAPI
synchronization and Git-visible secret-pattern checks pass. No paid model call or
physical multi-phone audio test was made in this block.

## Assessment and statistics

Teachers can also request group-level practice recommendations from `/assessment`.
Only aggregate counts and configured error labels are sent to the selected model;
student identities and submitted text are excluded. Suggestions reference existing
scenarios, remain advisory, and require manual teacher assignment. Mock mode is
explicit, saved snapshots are labeled when stale, and no grade is changed. See
`docs/GROUP_INSIGHTS.md`.

Open `/assessment` from the portal. Teachers configure optional passing-score,
field-error, sequence-error and timeout checks per scenario. Ordered steps match
successful persisted ARM actions with optional exact service/status filters, not
mouse clicks or inferred dialogue semantics. Policies freeze at attempt creation,
lesson start (fill), or template preparation (actions); old results are not regraded.
No policy means no automatic pass/fail verdict, even when a field score exists.

On a completed owned attempt, the teacher can assign a score and pass/fail decision
with a required reason, revise it, or revoke it. Every decision is appended with
author/time/revision; automatic evaluation and original cards remain unchanged.
Students can inspect their own results and progress. Group summaries include
effective grades, unassessed attempts, average time and recurring field/sequence
errors. Typical errors remain automatic diagnostics, separate from expert judgment.
The page exports the current statistics as JSON. See `docs/ASSESSMENT.md` for exact
semantics, access boundaries and limits. This is pedagogical configuration, not
an official 112 grading standard, and it does not call an AI provider.

Assessment verification: 127 Backend/proxy tests and the mocked assessment browser
flow pass, including audit/revocation, stale edits, retries and student privacy.

## Difficulty, DDS profiles and reference materials

Teachers configure basic/standard/advanced difficulty, DDS profile and student-visible
learning objectives in `/scenarios` or AI generation. These are pedagogical labels,
not emergency-service regulations. The teacher designs/reviews the actual challenge;
the level does not silently change routing, grades or time limits. Group lesson
filters constrain both empty-card and prefilled-card pools; metadata freezes with
each issued scenario. Older scenarios default to basic/general.

Open `/materials` from the portal or student workspace. Teachers create text articles
and upload PDF, UTF-8 TXT or DOCX (maximum 5 MiB per file), choose their groups and
publish. Drafts are private; students browse/search published references available
to their current groups, read the article and download its attachment. Updates use
revision checks, and unpublishing revokes subsequent access (not downloaded copies).
Files stay in local SQLite and are not sent to AI. Only synthetic/authorized training
materials: no real 112 data. Upload signature checks are not antivirus scanning;
download only trusted files. See `docs/CURRICULUM.md` for limits and API behavior.

Verification: 121 Backend/proxy tests pass; mocked browser flows pass for materials,
lesson filters and AI generation. No paid model call was made in this block.

## Current teacher-led lesson workflow

In `/portal`, prepare a group lesson with several event categories and select
fill, actions, or mixed mode. Set 1–200 cards per student or enable unlimited
practice until teacher stop. Start the prepared lesson; finish it for everyone
with a reason. Students join through the lesson bar, including before the start.
The workstation automatically opens the next random card after completion, resumes
the active card after reload, and waits after the configured limit. Random repeats
are allowed. Group fill practice supports text or assigned SIP; actions stay text.

For actions, choose completed cards from your students or prefill from scenario
facts (name, location note, incident). Scenario prefill is deterministic, not AI
generation or a claim that every structured address field is populated. Copies
do not inherit the original student's identity, conversation or grades. The group
report lists participants, cards and results, links to individual history, and
exports JSON. Action correctness grading is a separate feature, not implemented.

Categories are pedagogical labels, not official 112 regulations. Edit them in the
scenario editor; older scenarios default to other. Membership and fill scenario/
rubric snapshots freeze at start; action sources freeze at setup. Duplicate and
concurrent next-card requests are safe. Teacher stop blocks further work and closes
the last saved cards. Unsaved drafts remain separate from grades in user/session-
scoped browser sessionStorage for JSON export.

The later verification notes describe historical development stages; this section
describes the current lesson workflow.

Lesson-cycle verification: 86 Backend tests and 2 BFF tests passed. The headless
browser regression passed for student waiting/start/next/reload/teacher-stop and
teacher preparation/start/report/finish with mocked HTTP. JavaScript syntax,
Python compilation, OpenAPI synchronization and Git-visible secret-pattern checks
passed. No paid LLM request or physical SIP call was made in this block.

## Architecture

Current ARM additions: source-based response statuses, synthetic telephone logs,
own-card links, print, expanded search and separate «отработана» / exercise finish.
Resolved notification services append automatically on save; notified services
cannot be removed. See `docs/ARM_COVERAGE.md` for coverage and source limitations.

Frontend talks only to Backend. Backend owns Scenario/Fact/Event world state, disclosure, sessions and evaluation. Voice owns telephony, media, STT/TTS and Asterisk integration, but not business logic. LLM is not source of truth.

Flow: Frontend -> Backend -> Voice -> Asterisk. Control and media WebSockets are separate.

| Service | Port | Owner |
|---|---:|---|
| Backend | 8000 | backend developer |
| Voice | 8001 | voice developer |
| Student workspace | 3000 | frontend/BFF |
| Asterisk | 8088 | voice/integration |

The installed Windows/WSL stand is documented in `docs/Запуск-и-доступность.md`.
For isolated Voice development and mock tests, see `voice/README.md`.

Read `AGENTS.md` before changes. See `docs/` for project, architecture, contracts, scenario, integration, security and demo documentation.

## Student workspace and the dialogue provider

```powershell
python -m pip install -r backend/requirements.txt -r frontend/requirements.txt
python tools/run_workspace.py
```

Open http://127.0.0.1:3000. The launcher loads the root `.env`, generates an
ephemeral local Backend token when none is supplied and starts Backend :8000 and
Frontend :3000. Do not run it alongside another Backend on the same port.
Set `LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` in
the ignored root `.env`. Default without configuration is `mock`; `ollama` is
also supported. OpenRouter requires Internet access and is a development mode,
not the isolated deployment required by the full competition specification.

The student workspace reproduces the journal and editable/saved incident-card
layouts from the supplied AРМ-112 memo, pages 12, 15 and 16. It supports blank
cards, address and caller fields, incident features, service selection, saved
revisions, service comments, a timer, text dialogue, browser system speech,
completion and JSON history export. Hidden scenario facts stay on Backend.
The feature picker uses the imported source classifier with three dependent
levels and server-validated record IDs. Core-service branches N:AB support a
server-calculated preview with source cells. Routing now covers N:CU, appending
resolved services on save; conditional/free-text cases stay explicit. Configurable
reference criteria now produce weighted field reports on completion, separately
from field-presence checks. Advisory semantic/grammar AI review is requested
separately in the completed report and never changes the deterministic grade.

The legacy global Voice console is isolated from this authenticated BFF. Student SIP
mode calls Voice through Backend; configure `VOICE_URL`, `VOICE_API_TOKEN`,
and configure Voice with `BACKEND_MODE=websocket` and the same `DIALOGUE_TOKEN`
as its `BACKEND_TOKEN`. Configure a stable token in `.env` for that mode.
Voice and Asterisk are not started or reconfigured by this launcher.
Browser speech is optional ordinary system synthesis; it does not perform STT.

The launcher above is a local SQLite development path. The Docker deployment
uses PostgreSQL and has optional TLS, directory and bounded Backend-cluster
profiles; see `deploy/manage.py` and `docs/CLUSTER.md`. It has not been qualified
as host-failure high availability. Existing Voice model files remain external.

Checks: `cd backend; python -m pytest -q`, `cd voice; python -m pytest -q`,
`node --check frontend/assets/student.js`. Workspace implementation details:
`docs/STUDENT_WORKSPACE.md`.

Verification on 2026-09-15: 45 tests passed (11 Backend, 33 Voice, 1 proxy).
Browser verification covered a real OpenRouter reply, card persistence across
reload, a recorded service status, session completion and its audit report.
Completion uses an in-page confirmation dialog. Physical SIP/audio verification
and full classifier coverage are not included in these results.

Classifier block (2026-09-15): imported 1,283 records in 24 groups from the
supplied workbook; 27 non-record rows skipped, no ambiguous rows or duplicate
codes reported by the importer. Backend suite: 15 tests passed. JavaScript syntax
and diff whitespace checks passed. Notification routing remains a separate block.

Core-service block (2026-09-15): source branches N:AB now support preview,
explicit addition of suggestions, and saved routing provenance. See
`docs/ROUTING.md` for the exact supported branch mapping and remaining scope.
Verification: 24 Backend tests and 1 frontend proxy test passed; compilation,
JavaScript syntax and Git-visible secret-pattern scan passed. Browser interaction
verified classification, gasification-dependent selection of 101/104, card save
and reopening the read-only routing grounds. No SIP call or new OpenRouter
request was made during this block.

## Reference criteria and reports

Open `http://127.0.0.1:3000/instructor`, select a scenario, add reference criteria
and save. Start a NEW student session to use that rubric. Its reference answers
and revision are frozen on Backend; editing the rubric affects future sessions
only. Complete the card and open the report to see the score, differences,
recommendations and elapsed time against the configured limit (default 30 seconds).
No rubric means no grade. Existing completed sessions are not retroactively graded.
The instructor editor requires a teacher account. Rubrics are teacher-specific;
new student sessions must reference a group assignment from that teacher.

Assessment block verification (2026-09-15): 40 Backend tests and 1 proxy test
passed. Browser flow saved a sample address rubric, started a new attempt,
submitted an intentionally wrong street and displayed a persisted zero-score
report with the reference answer and correction. The local fire scenario now has
a minimal sample street criterion; expand it before substantive training.
No new OpenRouter request or SIP call was made. JavaScript syntax, compilation
and Git-visible secret-pattern checks passed.

## Advisory AI review

After completing a session, open its report and click «Получить ИИ-разбор».
The configured provider analyses selected text fields against the frozen training
facts. Findings show exact card quotes and supporting references where applicable.
The result needs teacher review; reference validity is not proof of model accuracy.
Successful/mock results are cached; failed attempts can retry. Mock mode explicitly
reports that no AI analysis occurred. In OpenRouter mode the selected synthetic card
text and scenario facts leave the local machine. Do not use real 112 data.

AI-review block verification (2026-09-15): 57 Backend tests and 1 frontend proxy
test passed. Tests cover strict evidence validation, bounded sources, mock mode,
sanitized failures/retry, immutable grades and concurrent request deduplication.
JavaScript syntax, Python compilation and Git-visible secret-pattern checks passed.
Provider responses were mocked for verification; no new paid OpenRouter request
or physical SIP call was made in this block.

## Accounts, groups and assignments

Open `http://127.0.0.1:3000/login`. On first launch, choose the first administrator's
login and password (at least 12 characters); no default passwords are seeded.
In `/portal`, the administrator creates teacher/student accounts and may block them.
Sign in as a teacher, create a group, add students, configure a rubric at
`/instructor`, then assign a scenario to the group. Sign in as a student to start
an assigned exercise in the existing ARM. The teacher's portal lists their sessions
and opens the saved card, transcript and available assessment.

SQLite stores scrypt password hashes and hashed, expiring/revocable login sessions.
The BFF uses an HttpOnly SameSite=Strict cookie; service/model keys stay server-side.
Students cannot access another student's cards; teachers see their own assignments
and sessions. Old unowned cards and rubrics remain stored but are not automatically
attached to a new user. Existing scenarios are shared read-only templates; newly
created scenarios belong to their teacher. The old global call/history proxy is
disabled to avoid bypassing session ownership; session-scoped SIP remains available.

This block does not add password recovery, teacher intervention, PostgreSQL, TLS,
load testing or multi-phone routing. Keep the launcher bound to loopback. The legacy
sample rubric is not transferred: configure a rubric under the teacher account.

Classroom block verification (2026-09-15): 69 Backend tests and 2 BFF tests passed,
including cross-user denial, teacher isolation, assignment membership, cookie/header
spoofing checks and token removal from browser JSON. Four JavaScript syntax checks,
Python compilation, contract generation and Git-visible secret-pattern scan passed.
The restarted local login and portal pages return 200; first-admin setup is pending
user input. No paid LLM request, physical SIP call or browser credential setup was
performed in this block. Existing FastAPI lifecycle deprecation warnings remain.

## Teacher scenario editor

AI authoring: as a teacher, open `/generation` from «Сценарии». Enter synthetic
requirements and a category, generate a private scenario/rubric preview, then ask
for corrections with a comment. Review both the caller facts and every criterion;
only «Утвердить» publishes them together. Saved drafts and prior versions survive
reload. A failed generation preserves the previous preview. Approval never rewrites
existing student work. The new scenario can then be assigned through `/portal`.

Uses the configured OpenRouter/Ollama provider; mock is explicitly a fixture and
does not interpret correction comments. No real 112 data, personal data or secrets
may be entered. Generated literal reference criteria must occur in the scenario;
this is not semantic validation or an official regulatory grading system. Manual
scenario/rubric editing remains available after approval.

Open `/portal` as a teacher and choose «Сценарии» (`/scenarios`). Create a scenario
or copy a shared template, edit caller facts/behavior/opening, validate structure
and save. Shared templates stay read-only; only the author's scenarios are editable.
Disable a scenario to prevent new attempts without deleting history. Existing
attempts retain their frozen scenario. Rubrics are not copied: configure the new
scenario's reference criteria separately, then assign it to a group.
No model call is needed to create or validate a scenario. Concurrent editor updates
are protected by a server-checked content version; voice takeover remains future work.

AI authoring verification: 7 integration tests cover generation, correction,
approval, ownership, malformed output, retries and transaction rollback. The
headless generation-page regression passed with mocked HTTP; no paid LLM request.

Scenario-editor verification: 71 Backend and 2 BFF tests passed; JavaScript syntax,
Python compilation, OpenAPI generation and Git-visible secret-pattern check passed.
No real LLM/SIP request was made. Browser interaction has not been verified in this block.

## Teacher feedback

In the teacher portal, open a session and send feedback to the student. Notes are
available during the exercise and after completion, with author/time and audit.
The student sees a notification and reads notes in the report/history. Feedback
does not change their card, caller dialogue, or criterion score. Delivery retries
are idempotent and notes are included in JSON exports. Teacher detail has a manual
refresh button for the saved card/dialogue; student feedback polls every 5 seconds
while visible without replacing unsaved card fields. Voice takeover and forced
session termination were outside this historical feedback block; see the current workflow above.

Feedback verification: 72 Backend tests passed, including live/completed feedback,
role isolation, idempotency and unchanged grades. Modified JavaScript syntax,
Python compilation, OpenAPI generation and secret-pattern checks passed. No paid
LLM request, physical SIP call or browser interaction was performed in this block.

Scenario conflict protection: stale saves return 409 without replacing saved data.
Form input is retained; copy it to a new scenario or explicitly reload. Targeted
authoring tests cover two editor versions, missing version, template protection and
role isolation. No schema migration or paid provider call is required.

## Teacher-controlled completion

Open an active session in the teacher portal, enter a reason and confirm completion.
The last saved card is graded once; author/reason are recorded in the report/audit.
SIP completion first hangs up through Voice; errors leave the session active.
Concurrent workspace mutations are serialized. Student polling freezes a remotely
completed card; any unsaved draft can be downloaded in JSON before leaving the page,
but is not submitted or scored. Group-wide start/stop and automatic progression
are now implemented as described above.

Completion-stage checks: 75 Backend tests passed; modified JavaScript syntax,
Python compilation, OpenAPI generation and Git-visible secret-pattern checks passed.
Coverage includes teacher ownership, duplicate finish, immutable saved card and a
Voice-not-configured failure. Physical SIP hangup and browser interaction were not
tested; no paid model request was made.

## Group lesson series

In the teacher portal prepare a group lesson, select one or more scenarios and
1–200 cards per student (or no limit), then press «Начать». Group membership is frozen at start.
Students choose the running series in the new-session dialog; each gets one active
text card, randomly selected from the enabled scenario pool (repeats allowed).
After completion the joined lesson automatically issues the next card. Repeated requests
reopen the active card instead of creating duplicates. Each card retains its own
grade/history and is linked to the common lesson.

«Завершить всем» closes active cards with the supplied reason and blocks new cards.
If stopping fails, retry it; already completed results remain unchanged. Older
standalone assignments and SIP remain available separately. Prefilled exercises,
automatic next-card display and categories are supported. Group SIP calling and
difficulty levels are not added here. Fill scenarios/rubrics freeze at lesson start.

Series verification: 77 Backend tests passed, including group scope, start gates,
active-card reuse, progression, card limits, empty-group rejection and group finish.
Modified JS syntax, compilation, OpenAPI generation and secret-pattern checks passed.
No paid model calls, physical SIP calls or browser interaction were used in this block.

## Actions with prefilled cards

Latest verification (2026-09-15): 112 Backend/proxy tests pass; mocked browser
flows pass for ARM actions, group lessons and AI scenario generation. Main ARM
editing/saved/journal screens were visually inspected. See
[ARM coverage and remaining source limitations](docs/ARM_COVERAGE.md) for the
implemented actions and distinctions from live emergency-service integrations.

Group lesson setup now offers fill, actions, and mixed modes. Select completed
cards from your own students as sources for action exercises. The lesson freezes
only card data and caller facts, without the source student's identity, transcript,
grade or feedback. Each student receives a separate editable copy and records new
service responses. No caller dialogue starts for these cards; source results remain
untouched. Mixed mode draws from both blank-scenario and completed-card pools.

Action reports record changed fields and service-action count for teacher review.
No automatic correctness score is claimed or inherited from already-filled fields.
AI-generated prefilled cards and configurable action grading remain separate work.
Deterministic scenario prefill and automatic next-card display are now supported.

Action-mode verification: 79 Backend tests passed, including independent copy,
source immutability, rejected foreign/active sources, blocked dialogue and action
reporting without inherited grades. Modified JS syntax, Python compilation, OpenAPI
generation and secret-pattern checks passed. No physical SIP, paid model call or
browser interaction was performed in this block.

## Готовые карточки ДДС: полный цикл (2026-09-18)

Сценарий может хранить структурированную `prefilled_card` (адрес по полям,
тип происшествия, службы). Готовая карточка поступает без входящего звонка,
но назначенный SIP-номер позволяет сделать отдельный голосовой доклад
дежурному. 30 секунд считаются от выдачи до открытия карточки, 3 минуты — до
первой записи своей службы (статус и текст); дальше сроки не нормируются. В ДДС список служб и их номера приходят
в готовой карточке и не редактируются обучающимся. Связаться можно только
со службой, для которой задан телефон. Поля карточки 112 в ДДС не правятся:
правильные сведения приходят со звонком бригады, а диспетчер сообщает об ошибке
в 112 кнопкой «Сообщить в 112 об ошибке» (`POST .../error-reports`). Голосом ДДС
говорит с руководителем бригады и с вышестоящим начальником (начальником
дежурной смены).
Диспетчер выбирает учебную бригаду из сценария после принятия карточки;
до назначения нельзя отметить выезд. Решение руководителя фиксируется отдельно.
Студент не может завершить принятую карточку без итогового статуса своей ДДС,
отметки отработки и запланированных вводных. Преподаватель может остановить
её с фиксацией пропущенных шагов. Балл действий ДДС отображается в отчёте,
статистике и влияет на подбор сложности. Подробности — в
`docs/API_CONTRACT.md`, `docs/ASSESSMENT.md`, `docs/DDS_DISPATCHER.md`.

Первоначально 10 билетов получили структурированную входящую карточку.
Обновление 26 сентября распространяет разметку на все 96: известный адрес
разделён по полям, тип и признаки взяты из существующей записи классификатора.
`tools/data/dds_prefilled_cards.json` хранит только выверенную разметку;
`tools/import_tickets.py` воспроизводит каталог. Для ранее опубликованных
неизменённых сценариев служит идемпотентный `backend/sync_curated_cards.py`
(сначала сухой прогон, затем `--apply`); он не заменяет публикацию нового каталога.
Все 96 заготовок требуют утверждения преподавателем. Кнопка «Начать обучение интерфейсу» вновь
открывает пошаговый тур после первого входа.
В занятии ДДС с назначенным SIP-номером оперативные доклады старшего бригады
поступают отдельными звонками. Одного таймера недостаточно для смены статуса:
нужно принять телефонный доклад и подтвердить его в карточке. Без SIP-номера
сохраняется текстовый путь. Факты доклада берутся из утверждённого сценария,
не генерируются моделью во время занятия.
# Исправления АРМ и карты — 23.09.2026

Подробности исправлений и фактически выполненных проверок: [отчёт UI](docs/UI_REVIEW_2026-09-23.md).

Исправлена вложенная форма назначения бригады, которая останавливала загрузку АРМ. Карта открывается внутри рабочего места без зависимости от popup-разрешений; есть индикатор загрузки и повтор после ошибки. Картографический пакет использует индекс `features_zoom` (создаётся при подготовке пакета); обзорные запросы сначала отбирают объекты нужного масштаба. Поиск числового номера дома не использует префиксное совпадение.

Закрыты обходы статусов ДДС через синонимы, изменение закрытой карточки и выдача эталонов студенту. Оперативные вводные обновляются по всем активным карточкам. Подтверждение SIP-доклада зависит от фактического окончания воспроизведения. PostgreSQL и его конфигурация в рамках этих исправлений не изменяются.

## Видеодемонстрации

В `output/videos-2026-09-28/` находятся три MP4: обзор решения, работа преподавателя и прохождение занятия учеником с телефонными докладами. Интерфейс записан при реальном прохождении в изолированном Chromium; телефон работает в отдельном контейнере Baresip по TLS/SRTP. Звук взят из записей состоявшихся звонков, без захвата рабочего стола и аудиоустройств Windows. Монтаж и проверка файлов: `tools/documentation/edit_walkthroughs.py` и `tools/documentation/check_videos.py`. Исходные записи и временные параметры доступа хранятся только в исключённом из Git каталоге `artifacts/`; в комплект видео они не входят.
