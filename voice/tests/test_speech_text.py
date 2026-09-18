"""Подготовка текста к произнесению: сокращения и числа словами."""

from app.audio.speech_text import for_speech, number_to_words


def test_address_abbreviations_are_spoken_in_full():
    """«стр. 2» должно звучать «строение два», а не «стр два»."""
    assert for_speech("ул. Лесная, д. 12, стр. 2") == "улица Лесная, дом двенадцать, строение два"
    assert for_speech("г. Москва, пер. Леонтьевский, д. 16") == (
        "город Москва, переулок Леонтьевский, дом шестнадцать")
    assert for_speech("кв. 45, под. 3, эт. 7") == "квартира сорок пять, подъезд три, этаж семь"


def test_abbreviation_needs_a_dot():
    """Без точки сокращение не раскрывается: «д 12» может быть чем угодно."""
    assert "дом" not in for_speech("д 12")
    # И внутри слова подмены не происходит.
    assert for_speech("страховка выплачена") == "страховка выплачена"


def test_numbers_are_read_as_words():
    assert number_to_words(0) == "ноль"
    assert number_to_words(12) == "двенадцать"
    assert number_to_words(21) == "двадцать один"
    assert number_to_words(145) == "сто сорок пять"
    assert number_to_words(2000) == "две тысячи"
    assert number_to_words(1001) == "одна тысяча один"


def test_phone_is_spelled_digit_by_digit():
    """Номер на слух записывают по цифрам, а не «восемьдесят три»."""
    spoken = for_speech("тел. +7 900 000-00-83")
    assert spoken.startswith("телефон плюс семь")
    assert "восемьдесят" not in spoken
    assert spoken.endswith("восемь три")


def test_range_is_readable():
    assert for_speech("Дерутся 10-15 человек") == "Дерутся десять тире пятнадцать человек"


def test_empty_input_is_safe():
    assert for_speech("") == ""
    assert for_speech(None) == ""
