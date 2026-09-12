# Voice Gateway для учебных SIP звонков

Python 3.12 / FastAPI сервис инициирует звонок на extension 201, принимает звук оператора, передаёт финальные реплики Backend, воспроизводит ответы и сохраняет три WAV дорожки. Бизнес-логика, LLM, сценарные факты и оценивание здесь отсутствуют.

**Состояние реализации:** локально проверены mock-диалог, REST API, медиапротокол, обработка ошибок и освобождение ресурсов. Реальный Asterisk, регистрация телефона, слышимость и отсутствие эха на стенде ещё не проверены. Адаптеры Vosk/Piper реализованы, но модели на этой машине не запускались. По умолчанию включён `PIPELINE_MODE=spike`: STT и TTS в этом режиме не создаются. Переключение реального звонка в разговорный режим требует `TOPOLOGY_VERIFIED=true` после проверки ниже.

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

На установленном стенде используется `STT_PROVIDER=hybrid`: Vosk выдаёт промежуточный текст, а русская GigaAM v2 уточняет окончательную фразу через sherpa-onnx. Для GigaAM задайте `STT_FINAL_MODEL` на каталог с `model.int8.onnx` и `tokens.txt`. Адаптер также поддерживает прежний Zipformer с файлами `am/encoder.onnx`, `am/decoder.onnx`, `am/joiner.onnx` и `lang/tokens.txt`. Зависимость sherpa-onnx входит в набор `[local]`.

Для более естественного русского голоса и автоматических ударений можно установить CPU PyTorch, указать `TTS_PROVIDER=silero`, `TTS_VOICE=/models/v5_5_ru.pt` и `TTS_SPEAKER=baya`. Процесс Silero прогревается при старте и остаётся загруженным между репликами. `TTS_FALLBACK_PROVIDER=piper` вместе с `TTS_FALLBACK_VOICE` оставляет Piper резервом.

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

Voice отправляет `call.connected`, `operator.utterance`, `operator.barge_in`, `call.ended`, `recording.ready`, `voice.error`. Принимает `caller.reply`, `environment.update`, `call.hangup`. Каждый исходящий конверт имеет собственные UUID и локальный монотонный `seq`; Backend канонизирует последовательность сессии. Сессия входящего события проверяется. Повторный `reply_id` в пределах звонка не воспроизводится.

`operator.utterance.payload` содержит `call_id`, `utterance_id`, `text`, `is_final=true`. Backend может вернуть тот же `utterance_id` в `caller.reply.payload`: это необязательное дополнение для точной корреляции latency при ответах не по порядку. Без него отсутствующие межсервисные метрики не выдумываются.

Backend подключается до originate. `call.connected` отправляется только после ответа телефона, построения мостов и готовности всех media sockets. Opening reply, пришедший раньше готовности, ожидает запуска pipeline.

Переподключение Backend ограничено числом попыток с backoff; originate при этом не повторяется. Исходящие события сначала сохраняются с fsync в `outbox/SESSION_ID/CALL_ID.jsonl`. Успешный WebSocket send не является подтверждением обработки Backend; exactly-once доставка не обещается. При исчерпании попыток звонок завершается, журнал остаётся для сверки и явного восстановления. Для replay после согласования с Backend:

```text
python tools/replay_outbox.py outbox/SESSION_ID/CALL_ID.jsonl
```

Backend должен дедуплицировать `event_id`. Утилита повторяет журнал целиком, сохраняет исходные IDs и никогда не создаёт звонок. Автоматического восстановления активного звонка после рестарта Voice нет.

## Barge in и окружение

VAD работает на исходном входе оператора: 60 мс подтверждения, 200 мс кольцевого pre-roll, 400 мс тишины до финала по умолчанию. Это настраиваемый energy VAD; шумный микрофон может потребовать настройки порога или замены детектора. Максимальная фраза ограничена 30 секундами.

Подтверждённое начало речи отменяет текущий TTS task, очищает локальные очереди, отправляет Asterisk `FLUSH_MEDIA` и Backend `operator.barge_in`. Вход не отключается. Обычный ответ ждёт окончания речи, `should_interrupt=true` вытесняет текущий ответ и начинает воспроизведение даже при говорящем операторе.

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

Запускайте **один Uvicorn worker**: CallContext и маршрутизация ARI/media находятся в памяти процесса. Горизонтальное масштабирование требует отдельной маршрутизации звонков и здесь не реализовано. Потеря ARI завершает активные вызовы и переводит health в degraded; для восстановления ARI требуется перезапуск сервиса. Ошибки удаления ресурсов АТС логируются явно: при недоступной АТС необходимо сверить оставшиеся каналы на стенде.

В Docker можно выполнить `docker compose up --build` после создания `.env`. Состав содержит только Voice; Asterisk и Backend должны быть доступны отдельно. Для связи с АТС на Docker host используйте `host.docker.internal`; для подключения АТС к Voice задайте `VOICE_BIND_ADDRESS` приватным IP хоста. Записи/outbox находятся в named volumes. Опциональные провайдеры требуют `INSTALL_LOCAL_PROVIDERS=true`, сборки заново и read-only mount моделей. Docker сборка в текущей среде не проверялась.

## Официальные материалы

- [Жизненный цикл версий Asterisk](https://docs.asterisk.org/About-the-Project/Asterisk-Versions/).
- [chan_websocket и ExternalMedia](https://docs.asterisk.org/Configuration/Channel-Drivers/WebSocket/).
- [ARI Channels API, Snoop и ExternalMedia](https://docs.asterisk.org/Asterisk_22_Documentation/API_Documentation/Asterisk_REST_Interface/Channels_REST_API/).
- [websocket_client.conf sample](https://github.com/asterisk/asterisk/blob/22/configs/samples/websocket_client.conf.sample) и [chan_websocket.conf sample](https://github.com/asterisk/asterisk/blob/22/configs/samples/chan_websocket.conf.sample).
- [Пример Vosk Python](https://github.com/alphacep/vosk-api/blob/master/python/example/test_simple.py).
- [Piper Python API](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md).

Протокол сверялся с этими первичными источниками при реализации. Итоги локальных проверок — в `docs/VERIFICATION.md`; стендовая приёмка — в `docs/ACCEPTANCE.md`.
