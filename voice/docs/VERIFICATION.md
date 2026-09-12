# Локальные проверки Voice Gateway

Дата проверки: 8 сентября 2026 года. Среда: Windows, Python 3.12.14, изолированный venv.

## Результаты

| Проверка | Результат |
| --- | --- |
| Установка проекта через pip editable | Успешно собран и установлен training-voice-gateway 0.1.0 |
| pytest | 28 passed, 2 warnings, 3.08 s в финальном прогоне |
| pip check | No broken requirements found |
| Отдельный Uvicorn процесс и HTTP клиент | Успешный mock-диалог через localhost |
| Финализация записи HTTP mock звонка | operator/caller/mixed WAV, по 49 280 samples, mono PCM16 16 kHz |
| Последовательность событий HTTP smoke | call.connected → operator.barge_in → operator.utterance → call.ended → recording.ready |
| Ошибки Voice в HTTP smoke | Не зарегистрированы |

У mock-провайдеров часть latency значений равна нулю на миллисекундной шкале. Это не замер реальных нейросетей, телефонной сети или акустической задержки.

Тесты покрывают REST авторизацию и валидацию, запрет неподтверждённого реального conversation режима, дедупликацию активной сессии и reply_id, directional ARI topology и очистку частичного создания, бинарные media frames и JSON handshake, XOFF/FLUSH, VAD pre-roll, приоритетное воспроизведение, STT fallback, ошибки провайдеров, reconnect Backend с сохранением event_id/seq, ограничение попыток, запись трёх дорожек и финализацию, синтетический noise/dropout, непрерывность ресемплера, анализ заведомо внесённого self-loop, генерацию конфигов и освобождение задач при hangup.

Mock-тест операторской записи действительно проходит через CallRuntime и проверяет отсутствие собственного TTS на operator track при наличии его на caller track. Он не моделирует реализацию audiohooks внутри Asterisk и физическую акустику телефона.

Два предупреждения относятся к deprecated интерфейсам Starlette TestClient/httpx и AnyIO BlockingPortal в установленных зависимостях. Ошибок тестов нет. Точные версии локального окружения перечислены в `requirements-tested.txt`; optional Vosk/Piper туда не входят.

## Не проверено в этой среде

- Сборка/запуск Docker: Docker здесь не обнаружен.
- Реальный Asterisk 22.8+, SIP регистрация extension 201, звонок и слышимость.
- Цифровая изоляция направлений на установленной АТС и акустическое эхо.
- Работа Vosk/Piper с выбранными русскими моделями, качество речи и нагрузка.
- Интеграция с настоящим Backend и его правила дедупликации/доступ к recording volume.

Эти пункты требуют стенда. Для них подготовлены `tools/media_spike.py`, `tools/analyze_spike.py`, конфигурации Asterisk и `docs/ACCEPTANCE.md`. По умолчанию реальные STT/TTS не подключаются к звонкам до прохождения media spike.
