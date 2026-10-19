import importlib.util
import json
from array import array
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('streaming_phone_demo', Path(__file__).with_name('streaming_phone_demo.py'))
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.mark.parametrize('text', ['На месте. Срок неизвестен.', 'Он сказал: "Ждите".\nПродолжаем.', 'Связь 📞 есть.'])
@pytest.mark.parametrize('ascii_escape', [True, False])
def test_every_json_boundary_keeps_valid_monotone_reply(text, ascii_escape):
    raw = json.dumps({'reply': text}, ensure_ascii=ascii_escape)
    previous = ''
    for end in range(1, len(raw) + 1):
        current, closed = demo.reply_prefix(raw[:end])
        assert current.startswith(previous)
        assert text.startswith(current)
        assert not any(0xD800 <= ord(c) <= 0xDFFF for c in current)
        previous = current
    assert previous == text and closed


def test_sentences_wait_for_boundary_and_keep_decimal():
    splitter = demo.Sentences()
    assert splitter.feed('Обследуем трубу.') == []
    assert splitter.feed(' Срок ') == ['Обследуем трубу.']
    assert splitter.feed('2.5 часа') == []
    assert splitter.feed('', final=True) == ['Срок 2.5 часа']


def test_timeline_retains_gaps_and_serializes_buffered_audio():
    # 1s question, answer ready after 0.3s; next chunk ready while audio plays.
    question = b'\x01\0' * 10
    pcm, times = demo.timeline(question, [(0.3, b'\x02\0' * 10), (0.4, b'\x03\0' * 5),
                                         (2.0, b'\x04\0' * 2)], 10)
    assert times == [0.3, 1.3, 2.0]
    assert pcm[:20] == question
    assert pcm[20:26] == b'\0' * 6
    assert len(pcm) == 64


def test_unexpected_json_is_not_spoken():
    with pytest.raises(ValueError):
        demo.reply_prefix('{"not_reply":"' + 'x' * 50)


def test_context_preparation_never_replays_the_future_question():
    options = {'num_thread': 6, 'num_predict': 120}
    payload = demo.context_preparation_payload(options)
    assert payload['messages'] == [{'role': 'system', 'content': demo.SYSTEM}]
    assert demo.QUESTION not in json.dumps(payload, ensure_ascii=False)
    assert payload['options']['num_predict'] == 1
    assert payload['keep_alive'] == -1
    assert options['num_predict'] == 120


@pytest.mark.parametrize('mode', ['full', 'sentence', 'clause', 'words3', 'word'])
def test_chunk_modes_preserve_text_at_every_character_boundary(mode):
    text = 'В подвале вода, ограждение стоит. Срок 2.5 часа? Неизвестен.'
    chunker = demo.Chunks(mode)
    pieces = []
    for char in text:
        pieces.extend(chunker.feed(char))
    pieces.extend(chunker.feed('', final=True))
    assert ' '.join(pieces) == text
    if mode == 'full':
        assert pieces == [text]
    if mode == 'word':
        assert all(len(piece.split()) == 1 for piece in pieces)


def test_voice_onset_reports_speech_not_zero_filled_first_packet():
    pcm = array('h', [0] * 1000 + [1000, -1000] * 100).tobytes()
    assert demo.voice_onset(pcm, 1000) == 1.0
    assert demo.voice_onset(b'\0' * 2000, 1000) is None
