# Локальные проверки Voice Gateway

Дата последней проверки: 15 сентября 2026 года. Среда: Windows, Python 3.12,
Docker Desktop Linux CPU.

## Результаты

| Проверка | Результат |
| --- | --- |
| Установка проекта через pip editable | Успешно собран и установлен training-voice-gateway 0.1.0 |
| pytest | 41 passed в финальном прогоне |
| pip check | No broken requirements found |
| Отдельный Uvicorn процесс и HTTP клиент | Успешный mock-диалог через localhost |
| Финализация записи HTTP mock звонка | operator/caller/mixed WAV, по 49 280 samples, mono PCM16 16 kHz |
| Последовательность событий HTTP smoke | call.connected → operator.barge_in → operator.utterance → call.ended → recording.ready |
| Ошибки Voice в HTTP smoke | Не зарегистрированы |
| Speech image | Собран `trainer112-voice:speech-ready` с Vosk/Sherpa/Piper и CPU-only PyTorch |
| Offline speech smoke | Silero «Проверка связи. Раз два три.» → Vosk «проверка связи раз два три» |
| Conversation pipeline smoke | 151 реальный PCM frame принят; Vosk final непустой; Backend mock ответил; Silero playback завершён |
| SIP 220 TLS/SRTP probe | PASS: TLS `200 OK`, SDES-SRTP `AES_CM_128_HMAC_SHA1_80`, PCMU, двусторонний ненулевой media, Vosk recognized, Silero played |

У mock-провайдеров часть latency значений равна нулю на миллисекундной шкале. Это не замер реальных нейросетей, телефонной сети или акустической задержки.

Тесты дополнительно покрывают application ACK, сохранение ACK с fsync,
повтор того же `event_id` после reconnect и фильтрацию подтверждённых событий при
явном replay. Reconnect-бюджет по умолчанию — 30 секунд.

Transport audit проверен отдельно: route template и status сохраняются без bearer,
query/body; ошибка диска блокирует HTTP до мутации; WebSocket сохраняет только
счётчики сообщений/байтов, но не содержимое тестового PCM frame.

Синтетическая проверка extension 220 подтвердила TLS-сигнализацию, обязательный
SRTP и двусторонний media flow. Финальный automatic-прогон
`call_id=aa3fdc08-e8e0-4ae0-93f2-6593e0c3a7c4` получил operator RMS 687 и caller
RMS 1244; Vosk распознал ожидаемое «раз два три», Backend/OpenRouter сформировал
сценарный ответ, а opening и ответ Silero получили `played`. Это был полный контур
WAV → SIP/SRTP → Voice/Vosk → Backend/OpenRouter → Voice/Silero → SIP/SRTP.
Причиной прежнего нулевого входа был SDP адрес `127.0.0.1`: отдельный
probe-контейнер отправлял RTP в собственный loopback. Probe теперь разделяет
network namespace с локальным Asterisk. Дополнительно устранена гонка ARI:
служебные каналы ожидают `StasisStart` до добавления в bridge. Расширение 201 не
использовалось. После этого цифрового доказательства для существующего allowlist
включён `TOPOLOGY_VERIFIED=true`; это не означает, что проверены слышимость и
акустическое эхо реальной гарнитуры 201.

Mock-тест операторской записи действительно проходит через CallRuntime и проверяет отсутствие собственного TTS на operator track при наличии его на caller track. Он не моделирует реализацию audiohooks внутри Asterisk и физическую акустику телефона.

Два предупреждения относятся к deprecated интерфейсам Starlette TestClient/httpx и AnyIO BlockingPortal в установленных зависимостях. Ошибок тестов нет. Точные версии локального окружения перечислены в `requirements-tested.txt`; optional Vosk/Piper туда не входят.

## Не проверено в этой среде

- SIP-регистрация extension 201, физический звонок и слышимость в гарнитуре.
- Цифровая изоляция направлений на установленной АТС и акустическое эхо.
- Качество речи через реальную гарнитуру и нагрузка; синтетический CPU smoke не
  является пользовательской приёмкой.
- Интеграция с настоящим Backend и его правила дедупликации/доступ к recording volume.

Эти пункты требуют стенда. Для них подготовлены `tools/media_spike.py`, `tools/analyze_spike.py`, конфигурации Asterisk и `docs/ACCEPTANCE.md`. По умолчанию реальные STT/TTS не подключаются к звонкам до прохождения media spike.
