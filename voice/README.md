# Voice Gateway для учебных SIP звонков

27 September: the root Compose Voice memory limit defaults to 4096 MiB;
the previous 2048 MiB cap caused memory reclaim/swap during hybrid STT + Silero.
This is not proof of 20-call capacity. `tools/acceptance_parallel_sip.py` tests
two independent TLS/SRTP phones through the actual deployed pipeline.

`tools/concurrency_probe.py` measures concurrent pipelines without SIP using
synthetic audio. `--speech --hybrid` enables installed Vosk/GigaAM/Silero models;
phone transport and backend replies remain mock and are labelled as such in JSON.
This does not measure RTP delay. Workers force UTF-8 and support Cyrillic Windows
installation paths.

Новый корневой Docker Compose собирает Voice вместе с Backend/PostgreSQL и
локальным Asterisk: см. `docs/DOCKER_DEPLOYMENT.md`. Секреты берутся из приватного
`.env.docker`; ARI и Media доступны только внутри Docker-сети. Это заменяет старое
ограничение «Compose содержит только Voice» для корневого Compose; voice/compose.yaml
остаётся отдельным developer-стендом. По умолчанию сохраняется честный spike-режим:
готовность SIP не означает проверенный голосовой диалог. Локальная LLM работает
в Backend через Ollama: Voice пересылает распознанную реплику и ждёт его ответ,
не обращаясь к модели напрямую. Backend ожидает ответ модели до 15 секунд за ход,
после чего использует безопасную запасную реплику соответствующего сценария.

Групповые занятия: Backend передаёт назначенный преподавателем номер каждого
студента. Эти номера нужно заранее зарегистрировать в Asterisk и включить в
`ALLOWED_EXTENSIONS`. Один номер не принимает два одновременных учебных звонка:
новая сессия получает429, повтор активной сессии возвращает прежний вызов.
Завершение карточки/группы использует существующий hangup. Mock сохраняется.

Python 3.12 / FastAPI сервис инициирует звонок на extension 201, принимает звук оператора, передаёт финальные реплики Backend, воспроизводит ответы и сохраняет три WAV дорожки. Бизнес-логика, LLM, сценарные факты и оценивание здесь отсутствуют.

**Состояние реализации:** локально проверены mock-диалог, REST API, медиапротокол,
обработка ошибок и освобождение ресурсов. Развёрнутый локальный стенд использует
Vosk/Silero в `PIPELINE_MODE=conversation`, но при `TOPOLOGY_VERIFIED=false`
разрешает звонки только на явно заданный `TOPOLOGY_PROBE_EXTENSION=220`.
Переключение пользовательских звонков в разговорный режим требует
`TOPOLOGY_VERIFIED=true` после проверки ниже.

## Быстрый запуск без Asterisk

Из каталога `voice` в PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
Copy-Item .env.example .env
```

В `.env` установите `PIPELINE_MODE=conversation`; оставьте `TELEPHONY_MODE=mock`, `BACKEND_MODE=mock`, `STT_PROVIDER=mock`, `TTS_PROVIDER=mock`.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1
```

В другом терминале из того же каталога:

```powershell
.\.venv\Scripts\python.exe tools/mock_demo.py
.\.venv\Scripts\python.exe -m pytest -q
```

На Linux эквивалент: `python3.12 -m venv .venv`, затем используйте `.venv/bin/python` вместо Windows пути. Интерактивная документация REST: `http://127.0.0.1:8001/docs`.

Mock STT возвращает фиксированный текст после финализации VAD. Mock TTS выдаёт тон 440 Гц, а не произносит текст. Mock Backend присылает приветствие и предсказуемый ответ на `operator.utterance`. Утилита подаёт искусственный вход в mock-микрофон и завершает звонок; пути WAV возвращаются в JSON.

## Маршрут аудио

```text
Телефон PJSIP/201 <---- playback bridge ----> ExternalMedia playback
       |
       +-- Snoop spy=in, whisper=none --> capture bridge --> ExternalMedia capture --> VAD/STT
       |
       +-- Snoop spy=out, whisper=none --> monitor bridge --> ExternalMedia monitor --> caller.wav
```

Телефон и выход TTS не включаются в capture bridge. Вход STT берётся исключительно из `spy=in`. Дополнительный `spy=out` записывает звук, отправляемый Asterisk телефону, включая фон и фактическое прерывание очереди. Это позволяет не записывать в caller track ещё не воспроизведённый или отменённый TTS.

Используются отдельные соединения:

- Voice → Asterisk: ARI HTTP control и ARI event WebSocket.
- Asterisk → Voice: три Media WebSocket на звонок, каждый со своим UUID канала.
- Voice → Backend: JSON control WebSocket на сессию.

ExternalMedia: `transport=websocket`, `encapsulation=none`, `format=slin16`, `external_host=voice-media`. `transport_data=f(json)d(in)` используется для capture/monitor и `f(json)d(out)` для playback. Здесь `d()` задаёт направление относительно приложения Voice. ARI query `direction=both` оставлен совместимым; ограничение задаётся параметром драйвера. Binary сообщения содержат только mono PCM16 LE 16 kHz, text — JSON команды. `MEDIA_START.channel_id` сопоставляется с заранее зарегистрированным UUID, а не с порядком подключения.

Ответ ARI на создание snoop/ExternalMedia может прийти раньше `StasisStart`.
Voice ожидает `StasisStart` каждого служебного канала перед `addChannel`, иначе
под нагрузкой возможен промежуточный HTTP 422 и разрушение ещё исправного звонка.

Гарнитура предпочтительна для проверки: акустический возврат из динамика в микрофон может существовать даже при правильной цифровой маршрутизации. VAD продолжает слушать вход во время TTS.

## Подготовка Asterisk 22.8 или новее в ветке 22.x

Нужен отдельный учебный Linux стенд Asterisk. Этот проект не устанавливает АТС и не подключает PSTN. Проверьте версию и модули в CLI Asterisk:

```text
core show version
module show like chan_websocket
module show like res_websocket_client
module show like res_http_websocket
module show like res_ari
module show like res_stasis
module show like bridge_softmix
module show like chan_pjsip
```

Используются также Stasis, ARI channels/bridges и Snoop. Отсутствующие модули необходимо включить в сборку вашего Asterisk. В `pjsip.conf` отключён `direct_media`; мосты создаются с `mixing,proxy_media`, чтобы медиапуть проходил через Asterisk.

Заполните `.env` локальными секретами: `ARI_PASSWORD`, `MEDIA_PASSWORD`, `SIP201_PASSWORD`, `API_TOKEN`. Подходящий способ получить секрет: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Секреты и сгенерированные конфиги исключены из Git.

Укажите реальные адреса:

- `ARI_URL`: HTTP адрес Asterisk с суффиксом `/ari`, доступный из Voice.
- `ASTERISK_HTTP_BIND`: адрес интерфейса АТС, доступный Voice; пример по умолчанию ограничен localhost.
- `ASTERISK_SIP_BIND`: интерфейс для регистрации учебного телефона.
- `VOICE_MEDIA_URL`: адрес Voice `/media`, доступный из Asterisk, например `ws://192.168.10.20:8001/media`.
- `ARI_USERNAME`, `MEDIA_USERNAME`: должны совпадать с конфигурациями.
- `MEDIA_CONNECTION=voice-media`: соответствует имени секции `websocket_client.conf`.

Применение ENV к конфигам выполняется явно, Asterisk сам не читает `.env`:

```text
python tools/render_asterisk_config.py --env .env --out asterisk/generated
```

Скрипт создаст шесть конфигураций и откажется перезаписывать существующие. Перенесите их на выделенный учебный стенд в каталог конфигурации Asterisk с ограниченным доступом. Для существующей АТС сначала объедините нужные секции с её конфигурацией. После изменения transport/HTTP настроек перезапустите учебный Asterisk. Не публикуйте SIP/ARI/Media порты в Интернет; используйте закрытую сеть и, при передаче за её пределами, TLS и сетевой контроль доступа.

На softphone/IP-телефоне: SIP сервер — адрес АТС, пользователь/auth ID — `201`, пароль — `SIP201_PASSWORD`, транспорт UDP, кодеки PCMU/PCMA. Затем проверьте:

```text
pjsip show endpoint 201
pjsip show contacts
http show status
```

Extension 201 должен иметь зарегистрированный контакт. В примере нет внешних trunks, исходящего dialplan или маршрутов к экстренным номерам. REST разрешает только `ALLOWED_EXTENSIONS`, по умолчанию `201`.

## Обязательный media spike перед STT и TTS

В `.env`:

```dotenv
TELEPHONY_MODE=asterisk
PIPELINE_MODE=spike
TOPOLOGY_VERIFIED=false
BACKEND_MODE=mock
```

Запустите Voice на адресе, доступном АТС; укажите заполненные ARI/media секреты и API токен. Сервис проверяет версию АТС и наличие ARI event connection перед созданием звонков.

В терминале клиента задайте `API_TOKEN` как переменную окружения, затем:

```text
python tools/media_spike.py --base http://VOICE_HOST:8001
```

Телефон 201 должен зазвонить. Ответьте и говорите во время пятисекундного тона 997 Гц. Скрипт завершит звонок через семь секунд после перехода в active, сохранит JSON результата в `spike-results` и укажет пути WAV. При использовании контейнера файлы находятся в его recording volume, а не автоматически на машине клиента.

Скопируйте каталог записи в доступное место и выполните:

```text
python tools/analyze_spike.py PATH_TO_CALL_RECORDING --out spike-results/analysis.json
```

Прослушайте **operator.wav**, **caller.wav** и **mixed.wav**. В operator должна быть речь оператора без цифрового возврата тестового тона; в caller — слышимый на телефоне тестовый тон. Анализатор оценивает уровень 997 Гц и подозрение на возврат, но не подменяет прослушивание. Его порог — диагностический параметр, не SLA и не автоматическое доказательство изоляции. В исходном состоянии `live_topology_verified=false` остаётся в отчёте.

Зафиксируйте версию Asterisk, настройки телефона, результат прослушивания и измерение в `docs/ACCEPTANCE.md`. Только после положительной проверки задайте:

```dotenv
PIPELINE_MODE=conversation
TOPOLOGY_VERIFIED=true
```

Перезапустите Voice. Это явный переключатель оператора стенда; сервис не выдаёт mock-тесты за проверку реального Asterisk.

### Автоматический probe на extension 220

Изолированный тест не звонит на пользовательский 201. Соберите клиент и запустите
его из корня проекта:

```powershell
docker build -t trainer112-sip220-probe:dev voice/tools/sip_probe
.\voice\tools\run_sip220_probe.ps1
```

Скрипт читает пароль 220 из ignored `.env.docker`, передаёт его через временный
read-only файл и удаляет файл в `finally`; пароль не попадает в argv или лог.
Поскольку локальный Asterisk рекламирует `external_media_address=127.0.0.1` для
MicroSIP на Windows-хосте, контейнер тестового телефона разделяет network namespace
с Asterisk. Иначе RTP ошибочно уходит в loopback самого probe-контейнера.

Probe принимает звонок по TLS с обязательным SDES-SRTP, передаёт подготовленный
русский WAV после паузы, получает автоматический Backend/OpenRouter-ответ, произносит
его через Silero и завершается не позднее TTL. Успех требует ненулевых `operator`
и `caller` RMS, финального Vosk текста «раз два три», минимум двух статусов TTS
`played` (opening и ответ) и явных SIP/SRTP свидетельств в логе. Для изолированной
проверки без Backend модели доступен `-Mode manual`.
Это цифровая проверка цепи; она не заменяет прослушивание и акустическую проверку
гарнитуры 201, поэтому сама не включает `TOPOLOGY_VERIFIED`.

## Провайдеры распознавания и синтеза

Есть интерфейсы `STTProvider` и `TTSProvider`, mock реализации, локальные адаптеры Vosk/Piper и загрузка собственной фабрики через ENV. Провайдеры создаются отдельно для каждого звонка; никакого глобального потока STT для нескольких операторов нет.

Для Vosk/Piper установите `python -m pip install -e ".[local]"`, подготовьте модель распознавания русского языка и модель голоса Piper вместе с её `.onnx.json`. Скачивание моделей не выполняется автоматически. Проверяйте условия конкретной голосовой модели; optional пакет Piper распространяется отдельно от кода этого проекта.

```dotenv
STT_PROVIDER=vosk
STT_MODEL=/models/vosk-ru
STT_LANGUAGE=ru
TTS_PROVIDER=piper
TTS_VOICE=/models/ru_voice.onnx
PROVIDER_TIMEOUT_S=20
```

Текущая инвентаризация Docker-стенда от 15 сентября 2026: каталог
`deploy/models` изначально был пуст, а зарегистрированный WSL-дистрибутив Ubuntu,
упомянутый в старой инструкции, отсутствует. В `deploy/models/vosk-model-small-ru-0.22`
повторно загружена небольшая русская Vosk-модель из
`https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip` (около 91 МБ
после распаковки). Файл модели не хранится в Git. Загрузка Piper Irina с
`https://huggingface.co/rhasspy/piper-voices/tree/main/ru/ru_RU/irina/medium`
не завершилась из-за недоступности CDN; один `.onnx.json` без соответствующего
`.onnx` не считается установленным голосом. Вместо неё загружена официальная
Silero v5.5 `https://models.silero.ai/models/tts/ru/v5_5_ru.pt` (145 420 684
байта). Текущий контейнер использует Vosk/Silero в разговорном режиме.
`TOPOLOGY_VERIFIED=true` включён после полного цифрового automatic-прогона 220;
субъективная слышимость и акустическое эхо гарнитуры 201 проверяются отдельно.

Для корневого Docker Compose после появления обоих файлов нужны как минимум:

```dotenv
INSTALL_LOCAL_PROVIDERS=true
INSTALL_SILERO=true
STT_PROVIDER=vosk
STT_MODEL=/models/vosk-model-small-ru-0.22
TTS_PROVIDER=silero
TTS_VOICE=/models/v5_5_ru.pt
TTS_SPEAKER=baya
PIPELINE_MODE=spike
TOPOLOGY_VERIFIED=false
```

Сначала пересоберите Voice и проверьте загрузку провайдеров синтетически. В
`conversation` и `TOPOLOGY_VERIFIED=true` переходите только после нового media
spike и проверки пользователем через гарнитуру.

На установленном стенде используется `STT_PROVIDER=hybrid`: Vosk выдаёт промежуточный текст, а русская GigaAM v2 уточняет окончательную фразу через sherpa-onnx. Для GigaAM задайте `STT_FINAL_MODEL` на каталог с `model.int8.onnx` и `tokens.txt`. Адаптер также поддерживает прежний Zipformer с файлами `am/encoder.onnx`, `am/decoder.onnx`, `am/joiner.onnx` и `lang/tokens.txt`. Зависимость sherpa-onnx входит в набор `[local]`.

Для русского голоса установите CPU PyTorch, укажите `TTS_PROVIDER=silero`, `TTS_VOICE=/models/v5_5_ru.pt` и `TTS_SPEAKER=baya`. Пул Silero сохраняет модели между репликами: `TTS_WORKERS=2` (1–4), `TTS_THREADS_PER_WORKER=2` (1–8). Это дочерние процессы синтеза, не Uvicorn workers. Кэш до 8 MiB/128 реплик хранится только в памяти и очищается при остановке. Отмена звонка останавливает только занятый им процесс, остальные продолжают работу. `TTS_FALLBACK_PROVIDER=piper` вместе с `TTS_FALLBACK_VOICE` оставляет Piper резервом.

Vosk возвращает partial; Backend получает только итоговую фразу после VAD endpoint. Piper отдаёт PCM поток по фрагментам, `rate` преобразуется в `length_scale`; emotion/intensity остаются необязательными hints и игнорируются этим адаптером. Частота голоса приводится к 16 kHz с сохранением состояния между фрагментами. Загрузка/инференс работают в отдельных завершаемых процессах: barge-in и hangup могут остановить их без блокировки FastAPI event loop. Эта простая реализация загружает Piper на каждый ответ; после измерений можно добавить управляемый пул процессов.

Для своей интеграции укажите `STT_PROVIDER=python:my_package:make_stt` или `TTS_PROVIDER=python:my_package:make_tts`. Фабрика получает `Settings` и возвращает объект соответствующего протокола. Пользовательский TTS обязан выдавать **сырые mono PCM16 LE 16 kHz**, а не WAV/MP3; `close()` должен завершать ресурсы. STT `get_final()` финализирует текущую фразу и готовит поток к следующей. `STT_API_KEY`, `TTS_API_KEY`, модель, голос и язык передаются через `Settings`; встроенные локальные адаптеры ключи не используют.

`STT_FALLBACK_PROVIDER` и `TTS_FALLBACK_PROVIDER` включают одну резервную попытку. STT повторно получает сохранённую текущую фразу, включая pre-roll. TTS очищает вывод и начинает ответ заново через резервный провайдер; уже услышанную часть нельзя отменить. При отсутствии резерва/повторной ошибке отправляется `voice.error`, звонок корректно завершается. Не ставьте mock резервом для реального упражнения, если фиксированный текст/тон нежелательны.

## Backend и контракт событий

```dotenv
BACKEND_MODE=websocket
BACKEND_URL=ws://backend:8000/ws/v1/voice/sessions/{session_id}
BACKEND_TOKEN=
```

Общий конверт:

```json
{
  "event_id": "49083398-5bce-4b69-8c47-1b6bb1d3bdd0",
  "seq": 1,
  "session_id": "ad61e4c2-20ad-4110-973a-0e37f5b07334",
  "type": "caller.reply",
  "elapsed_ms": 250,
  "payload": {
    "reply_id": "252ab5be-c9cc-4b60-a1ba-6782a8d59fdb",
    "text": "На Учебной улице!",
    "voice_style": {"emotion": "panic", "rate": 1.1, "intensity": 0.8},
    "should_interrupt": false
  }
}
```

Voice отправляет `call.connected`, `operator.utterance`,
`call.ended`, `recording.ready`, `voice.error`. Принимает `caller.reply`,
`environment.update`, `call.hangup` и служебный `backend.ack`. Каждый исходящий
конверт имеет собственные UUID и локальный монотонный `seq`; Backend канонизирует
последовательность сессии. Сессия входящего события проверяется. Повторный
`reply_id` в пределах звонка не воспроизводится.

`operator.utterance.payload` содержит `call_id`, `utterance_id`, `text`, `is_final=true`. Backend может вернуть тот же `utterance_id` в `caller.reply.payload`: это необязательное дополнение для точной корреляции latency при ответах не по порядку. Без него отсутствующие межсервисные метрики не выдумываются.

Backend подключается до originate. `call.connected` отправляется только после ответа телефона, построения мостов и готовности всех media sockets. Opening reply, пришедший раньше готовности, ожидает запуска pipeline.

Исходящие события сначала сохраняются с fsync в
`outbox/SESSION_ID/CALL_ID.jsonl`. Backend после сохранения события и отправки
связанного `caller.reply` (если он есть) возвращает `backend.ack` с
`payload.event_id` исходного события. Voice держит только одно событие в полёте,
так что `call.ended` не обгоняет предыдущие события. До ACK тот же UUID повторно
отправляется после переподключения; originate при этом не повторяется. ACK также
fsync-сохраняется в соседний `CALL_ID.acked.jsonl`. Это at-least-once доставка:
Backend обязан дедуплицировать `event_id`, exactly-once не обещается.

Reconnect продолжается не меньше `BACKEND_RECONNECT_BUDGET_S=30` секунд (и не
меньше заданного числа попыток); в это время физический звонок не переинициируется.
После исчерпания бюджета звонок завершается, а неподтверждённые события остаются
для сверки и явного восстановления. Для ACK-aware replay после согласования с
Backend:

```text
python tools/replay_outbox.py outbox/SESSION_ID/CALL_ID.jsonl
```

Replay пропускает UUID из `.acked.jsonl`, ждёт отдельный ACK для каждого события
и дописывает подтверждение с fsync. Он не инициирует и не повторяет звонок.

После завершения `GET /api/v1/calls/{call_id}` и ответ hangup содержат
`reason`. Нормальные терминальные причины: `api_hangup`, `backend_hangup`,
`remote_hangup`, `not_answered`, `max_duration`, `service_shutdown`. Причины
неожиданного транспорта: `ari_disconnected`, `media_disconnected`,
`asterisk_media_ended`, `backend_unavailable`. Backend может использовать только
вторую группу для ограниченного recovery; Voice сам телефон повторно не набирает.

`GET /api/v1/health` возвращает безопасные эксплуатационные метаданные без путей
и секретов: выбранные провайдеры, факты настройки и наличия моделей, состояние topology,
режим Backend control, семантику доставки и reconnect-бюджет.

## Транспортный аудит Voice

При заданном `SECURITY_AUDIT_DIR` каждый HTTP запрос получает fsync-записи start и
finish с UTC-временем, методом, route template, статусом и длительностью. Заголовки,
Authorization, query string и body не записываются. WebSocket фиксирует только
connect/disconnect, итоговый статус, число сообщений и суммарное число байтов;
JSON-команды, текст и PCM frame contents не сохраняются. Если start-запись не
удалась, HTTP получает 503 до обработчика, а WebSocket закрывается 1011 до accept.

Файлы `YYYY-MM-DD.jsonl` не удаляются автоматически; для завершённых UTC-дней
ежечасно создаётся gzip-копия. Заявленный минимум хранения — 183 дня, но политика
резервного копирования каталога остаётся обязанностью оператора стенда.

Backend дедуплицирует `event_id`. Replay сохраняет исходные IDs и сам не создаёт звонок. После аварийного рестарта Voice сохранённый terminal snapshot позволяет Backend выполнить ограниченный повторный вызов при открытом рабочем месте/докладе. Это новый физический звонок в прежнем занятии, не восстановление потерянных аудиопакетов.

## Barge in и окружение

VAD работает на исходном входе оператора: 60 мс подтверждения, 200 мс кольцевого pre-roll, 400 мс тишины до финала по умолчанию. Это настраиваемый energy VAD; шумный микрофон может потребовать настройки порога или замены детектора. Максимальная фраза ограничена 30 секундами.

Пока говорит собеседник, тихий входной сигнал считается эхом динамика и не распознаётся; после ответа защита держится `ECHO_GUARD_MS` (1,4 с). Речь оператора громче `BARGE_IN_THRESHOLD` (0,05) дольше `BARGE_IN_MS` (400 мс) перебивает собеседника: текущий TTS отменяется, очереди очищаются, Asterisk получает `FLUSH_MEDIA`, Backend — статус воспроизведения `interrupted`, а реплика оператора вместе с накопленным началом фразы уходит в распознавание. `BARGE_IN_MS=0` оставляет полудуплекс для громкой связи. В ручном режиме преподавателя вход работает в полном дуплексе.

Пример payload `environment.update`:

```json
{
  "noise_type": "road",
  "noise_level": 0.45,
  "connection_quality": "bad",
  "dropout_probability": 0.10,
  "volume_multiplier": 0.85
}
```

Поддерживаются `none/road/crowd/wind/alarm/indoor`, синтетические шумы без внешних assets, PCM dropout, ослабление и фильтрация. Обновление — patch: пропущенные поля сохраняются. `dropout_probability=null` возвращает значение preset. Обработка применяется к направлению Voice → телефон, включая фоновый звук между ответами; исходный operator input и VAD ей не искажаются. Решение, когда менять окружение, принимает Backend.

## REST API и запись

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/api/v1/health` | Готовность ARI и активные звонки |
| POST | `/api/v1/calls` | `{session_id: UUID, extension: "201"}` |
| GET | `/api/v1/calls/{call_id}` | Статус звонка |
| POST | `/api/v1/calls/{call_id}/hangup` | Завершение и финализация |
| POST | `/api/v1/calls/{call_id}/mock/audio` | Только mock: сырые PCM фреймы |

Короткие `/calls...` доступны как алиасы. Статусы: `created/calling/ringing/active/ended/failed`. Повторный POST с активным `session_id` возвращает имеющийся звонок. После завершения сессия освобождается; для защиты от повторов после завершения idempotency должен обеспечивать Backend. Активные вызовы ограничены `MAX_CALLS`; терминальные snapshots хранятся в ограниченном cache без audio/tasks.

Записи: `recordings/SESSION_ID/CALL_ID/{operator,caller,mixed}.wav`. Они выровнены по локальному monotonic времени прихода media с компенсацией небольшого джиттера. Это приблизительное выравнивание потоков, не sample-accurate синхронизация по RTP timestamps. Mixed WAV складывает дорожки с ограничением амплитуды. Файловая работа вынесена из event loop; переполнение ограниченной очереди приводит к явной ошибке вместо скрытой потери записи.

`recording.ready` следует за `call.ended` только после закрытия WAV, fsync, атомарного переименования и проверки длины файлов. Payload содержит абсолютные `paths`, `format`, `sample_rate`, `alignment`. Backend должен иметь доступ к тому же recording volume по тем же путям; HTTP раздача аудио в проект не включена. При ошибке записи `recording.ready` не отправляется. Журналы содержат тексты реплик: храните их вместе с записями в ограниченном хранилище, не в Git.

## Метрики и ограничения эксплуатации

JSON логи `turn.latency` содержат `vad_start_ms`, `vad_end_ms`, `stt_final_ms`, `backend_send_ms`, `backend_reply_ms`, `tts_request_ms`, `tts_first_audio_ms`, `playback_start_ms` и доступные разности. Метки — миллисекунды от начала звонка. `playback_start_ms` фиксирует начало передачи в локальную очередь вывода; акустический момент воспроизведения в телефоне не измеряется. Первый звук считается от запроса TTS, а total turn — от VAD end. SLA не задан. Partial логируется без текста и не отправляется в business logic.

Запускайте **один Uvicorn worker**: CallContext и маршрутизация ARI/media находятся в памяти процесса. Пул TTS не меняет это ограничение. Потеря ARI завершает активные вызовы и переводит health в degraded; канал событий переподключается автоматически с паузой 1–5 секунд. Backend управляет ограниченным повторным вызовом. При недоступной АТС необходимо сверить оставшиеся каналы на стенде.

В Docker можно выполнить `docker compose up --build` после создания `.env`. Состав содержит только Voice; Asterisk и Backend должны быть доступны отдельно. Для связи с АТС на Docker host используйте `host.docker.internal`; для подключения АТС к Voice задайте `VOICE_BIND_ADDRESS` приватным IP хоста. Записи/outbox находятся в named volumes. Опциональные провайдеры требуют `INSTALL_LOCAL_PROVIDERS=true`, сборки заново и read-only mount моделей. Docker сборка в текущей среде не проверялась.

## Официальные материалы

- [Жизненный цикл версий Asterisk](https://docs.asterisk.org/About-the-Project/Asterisk-Versions/).
- [chan_websocket и ExternalMedia](https://docs.asterisk.org/Configuration/Channel-Drivers/WebSocket/).
- [ARI Channels API, Snoop и ExternalMedia](https://docs.asterisk.org/Asterisk_22_Documentation/API_Documentation/Asterisk_REST_Interface/Channels_REST_API/).
- [websocket_client.conf sample](https://github.com/asterisk/asterisk/blob/22/configs/samples/websocket_client.conf.sample) и [chan_websocket.conf sample](https://github.com/asterisk/asterisk/blob/22/configs/samples/chan_websocket.conf.sample).
- [Пример Vosk Python](https://github.com/alphacep/vosk-api/blob/master/python/example/test_simple.py).
- [Piper Python API](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md).

Протокол сверялся с этими первичными источниками при реализации. Итоги локальных проверок — в `docs/VERIFICATION.md`; стендовая приёмка — в `docs/ACCEPTANCE.md`.

# 26 September recovery update

Persistent chat snapshots now retain terminal call reasons. An unclean restart
marks previously active calls `failed/service_restart`; GET calls reads those
snapshots after the in-memory cache is gone. Normal ended calls retain their reason
and do not become recoverable. Backend controls bounded redial, not Voice itself.
ARI event transport reconnects with 1–5 second backoff. This supersedes historical
notes above that require restarting Voice after every ARI disconnect.
Unreceived audio cannot be restored; separate physical attempts keep separate recordings.
