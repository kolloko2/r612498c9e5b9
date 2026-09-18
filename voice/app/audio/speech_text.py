"""Подготовка текста к произнесению: сокращения и числа словами.

Синтез читает строку буквально. «ул. Лесная, д. 12, стр. 2» звучит как «ул
лесная д двенадцать стр два» — диспетчер такого адреса на слух не разберёт, а
именно адрес он обязан записать без ошибки. Здесь текст переводится в то, что
человек произнёс бы вслух: сокращения раскрываются, числа читаются словами.

Модуль намеренно без словарей и без модели: только детерминированные правила,
одинаковые при каждом запуске.
"""

from __future__ import annotations

import re

# Сокращения адреса и служб в том виде, в каком они встречаются в карточке.
# Раскрываются только как отдельное слово, чтобы «строение» не появилось
# посреди «страховка».
ABBREVIATIONS: dict[str, str] = {
    "ул": "улица",
    "пр-т": "проспект",
    "просп": "проспект",
    "пер": "переулок",
    "пл": "площадь",
    "ш": "шоссе",
    "наб": "набережная",
    "б-р": "бульвар",
    "мкр": "микрорайон",
    "д": "дом",
    "вл": "владение",
    "корп": "корпус",
    "к": "корпус",
    "стр": "строение",
    "соор": "сооружение",
    "кв": "квартира",
    "оф": "офис",
    "под": "подъезд",
    "эт": "этаж",
    "г": "город",
    "пос": "посёлок",
    "р-н": "район",
    "обл": "область",
    "тел": "телефон",
}

UNITS = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
UNITS_FEMININE = {1: "одна", 2: "две"}
TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
         "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят",
        "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот",
            "шестьсот", "семьсот", "восемьсот", "девятьсот"]


def _under_thousand(value: int, feminine: bool = False) -> list[str]:
    words: list[str] = []
    if value >= 100:
        words.append(HUNDREDS[value // 100])
        value %= 100
    if 10 <= value <= 19:
        words.append(TEENS[value - 10])
        return words
    if value >= 20:
        words.append(TENS[value // 10])
        value %= 10
    if value:
        words.append(UNITS_FEMININE[value] if feminine and value in UNITS_FEMININE else UNITS[value])
    return words


def number_to_words(value: int) -> str:
    """Целое число словами. Поддерживает диапазон, достаточный для адреса."""
    if value < 0:
        return "минус " + number_to_words(-value)
    if value == 0:
        return UNITS[0]
    words: list[str] = []
    if value >= 1_000_000:
        millions = value // 1_000_000
        tail = millions % 10
        noun = "миллион" if tail == 1 and millions % 100 != 11 else (
            "миллиона" if tail in (2, 3, 4) and millions % 100 not in (12, 13, 14) else "миллионов")
        words += _under_thousand(millions) + [noun]
        value %= 1_000_000
    if value >= 1000:
        thousands = value // 1000
        tail = thousands % 10
        noun = "тысяча" if tail == 1 and thousands % 100 != 11 else (
            "тысячи" if tail in (2, 3, 4) and thousands % 100 not in (12, 13, 14) else "тысяч")
        words += _under_thousand(thousands, feminine=True) + [noun]
        value %= 1000
    if value:
        words += _under_thousand(value)
    return " ".join(word for word in words if word)


def _expand_abbreviations(text: str) -> str:
    def replace(match: re.Match) -> str:
        word = match.group(1)
        full = ABBREVIATIONS.get(word.casefold())
        return full if full else match.group(0)

    # Сокращение считается таковым только с точкой: «д. 12» — дом, «д 12» может
    # быть чем угодно, и выдумывать за источник не нужно.
    return re.sub(r"\b([А-Яа-яЁё]{1,4}|[а-я]-[а-я])\.", replace, text)


def _expand_numbers(text: str) -> str:
    def replace(match: re.Match) -> str:
        digits = match.group(0)
        # Длинные последовательности — это телефон или номер карточки: они
        # читаются по цифрам, иначе получается неразборчивое «девятьсот
        # миллионов».
        if len(digits) > 4:
            return " ".join(UNITS[int(ch)] for ch in digits)
        return number_to_words(int(digits))

    return re.sub(r"\d+", replace, text)


# Телефон произносится по цифрам целиком, иначе «000-00-83» превращается в
# «ноль тире ноль тире восемьдесят три» и номер на слух не записать.
PHONE = re.compile(r"(?:\+7|8)?[\s(]*\d{3}[\s)]*\d{3}[\s-]*\d{2}[\s-]*\d{2}")


def _spell_digits(text: str) -> str:
    return " ".join(UNITS[int(ch)] for ch in text if ch.isdigit())


def _expand_phones(text: str) -> str:
    def replace(match: re.Match) -> str:
        raw = match.group(0)
        prefix = "плюс семь " if raw.lstrip().startswith("+7") else ""
        digits = [ch for ch in raw if ch.isdigit()]
        if prefix:
            digits = digits[1:]
        return prefix + " ".join(UNITS[int(ch)] for ch in digits)

    return PHONE.sub(replace, text)


def for_speech(text: str) -> str:
    """Текст, пригодный для произнесения вслух."""
    if not isinstance(text, str) or not text.strip():
        return ""
    result = _expand_abbreviations(text)
    # Телефон раскрывается до общих правил: иначе его разорвут числа и дефисы.
    result = _expand_phones(result)
    # Диапазон «10-15» произносится как «с десяти до пятнадцати» только в адресе
    # это редкость; достаточно прочитать оба числа через «-» как «тире».
    result = re.sub(r"(?<=\d)\s*[-–—]\s*(?=\d)", " тире ", result)
    result = _expand_numbers(result)
    result = re.sub(r"\s+", " ", result).strip()
    return result


__all__ = ["for_speech", "number_to_words", "ABBREVIATIONS"]
