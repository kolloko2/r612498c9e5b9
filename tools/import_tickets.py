"""Import the supplied training tickets into a bounded scenario-draft catalog.

Source: `Датасет.zip` → `Билеты- задачи по C 112 . АГС_ГСИ.pdf`, 32 scanned pages
with three calls each. The pages carry no text layer, so `tools/data/tickets_source.json`
holds a manual transcription with its own provenance block; this importer never
reads the PDF and never invents incident facts.

Every generated draft keeps the source text verbatim in `incident` and derives
facts only by splitting that same text. The explicit 96-row ticket_exercises
table supplies the final DDS category, address and authored operational cycle;
the keyword table records the original automatic classification only.
Drafts are always disabled: a
teacher validates each one before it can be issued, as the task statement requires.

Caller telephone numbers are replaced with deterministic synthetic numbers by
default, because the project forbids storing real-looking personal data. Pass
`--keep-source-phones` to reproduce the booklet exactly for a source comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from ticket_exercises import complete


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "data" / "tickets_source.json"
OUTPUT = ROOT / "backend" / "data" / "tickets.json"
CURATED_DDS = ROOT / "tools" / "data" / "dds_prefilled_cards.json"
SCHEMA_VERSION = 1

# Scenario.id must match ^[a-z0-9][a-z0-9_-]{2,63}$ in backend/server.py.
DRAFT_ID = "ticket-{ticket:02d}-{call}"
MAX_KNOWN_FACTS = 30
MAX_FACT_CHARS = 1000
MAX_INCIDENT_CHARS = 1000
MAX_LOCATION_CHARS = 500
MAX_TITLE_CHARS = 120

PHONE = re.compile(r"\b(?:\d[\s-]?){10}\b")
# Три слова с заглавной буквы подряд — «Фамилия Имя Отчество» в исходных билетах.
FULL_NAME = re.compile(r"\b([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+(?:ич|на|вна|чна))\b")

# Порядок важен: первое совпавшее правило определяет категорию.
# Ключевые слова взяты из текста самих билетов, а не из официальных регламентов.
CATEGORY_RULES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("gas", "utilities", "gas", ("запах газа", "газовой трубы", "свист от газовой")),
    ("fire", "fire", "fire", (
        "горит", "горят", "возгорание", "задымление", "пожар", "столб черного дыма",
        "пожарная сигнализация", "открытое пламя", "открытый огонь",
    )),
    ("traffic", "traffic", "police", (
        "дтп", "наезд на пешехода", "сбила электричка", "упало бревно с грузовика",
        "падение автомашины в воду", "троллейбус",
    )),
    ("public", "public", "police", (
        "дерутся", "драка", "избита", "изнасиловали", "угон", "завладение автотранспортом",
        "подозрительн", "коробка", "тикает", "взорвать", "затащили", "попрошайничает",
        "нетрезвый", "ругается", "сломать тумбу", "скандал", "ссора", "труп",
        "хочет повеситься", "просит вызвать полицию", "не пояснил", "громко играет музыка",
    )),
    ("medical", "medical", "medical", (
        "потеря сознания", "без сознания", "теряет сознание", "потеряла сознание",
        "боли в сердце", "головная", "головные боли", "судороги", "задыхается", "астма",
        "беременность", "рожает", "отошли воды", "кровотечение", "укусила змея",
        "рвота", "боль в животе", "травма", "ожог", "отек", "хрипы", "скончалась",
        "выпила", "речь невнятная", "инсульт", "плохо", "в крови", "лежит мужчина",
    )),
    ("other", "other", "general", (
        "тонет", "упал с моста", "плывут на льдине", "заблудилась", "упал в котлован",
        "нырнул в воду", "потерялся ребенок", "не вернулся", "не открывает дверь",
        "открыть дверь", "двери заблокировались", "просит о помощи", "крики о помощи",
        "трещина", "отлетела плитка", "крепления", "уличное освещение",
        "поругался с продавцом",
    )),
)

# Формулировки источника, прямо помечающие сведения как неизвестные оператору.
UNKNOWN_MARKERS: tuple[tuple[str, str], ...] = (
    ("гос. знак назвать не может", "Государственный знак транспортного средства заявителю неизвестен"),
    ("гос. номер не запомнил", "Государственный номер автомобиля заявитель не запомнил"),
    ("№ дома неизвестен", "Номер дома заявителю неизвестен"),
    ("источник не установлен", "Источник задымления не установлен"),
    ("точной информации нет", "Точных сведений о пострадавших у заявителя нет"),
    ("что горит не знает", "Что именно горит, заявитель не знает"),
    ("название не знаю", "Название ориентира заявителю неизвестно"),
    ("неизвестный", "Личность пострадавшего не установлена"),
    ("неизвестная", "Личность пострадавшей не установлена"),
    ("информации о пострадавших нет", "Сведений о пострадавших у заявителя нет"),
)

EMOTIONS = {
    "fire": "Встревожена, говорит быстро, торопит оператора.",
    "medical": "Испугана за пострадавшего, отвечает сбивчиво, просит поторопиться.",
    "traffic": "Возбуждена, говорит громко, отвлекается на обстановку.",
    "public": "Раздражена и насторожена, говорит отрывисто.",
    "utilities": "Обеспокоена, говорит тихо, опасается находиться рядом.",
    "other": "Обеспокоена, отвечает по существу.",
}
BEHAVIOR = (
    "Сначала называет суть происшествия. Адрес, имя, телефон и остальные подробности "
    "сообщает только в ответ на вопросы оператора. Не придумывает фактов, которых нет "
    "в известных сведениях; на вопрос о неизвестном отвечает, что не знает."
)


def _synthetic_phone(original: str) -> str:
    """Stable per-source-number training placeholder; never a dialable number."""
    digits = re.sub(r"\D", "", original)
    index = int(hashlib.sha256(digits.encode("utf-8")).hexdigest(), 16) % 100
    return f"+7 900 000-00-{index:02d}"


def _normalize_phones(text: str, keep_source: bool) -> tuple[str, list[str]]:
    found: list[str] = []

    def replace(match: re.Match[str]) -> str:
        original = match.group(0)
        stored = original if keep_source else _synthetic_phone(original)
        # Списком возвращаются номера в том виде, в котором они попали в текст,
        # иначе известные факты раскрыли бы исходный номер после нормализации.
        found.append(stored)
        return stored

    return PHONE.sub(replace, text), found


def _caller_name(text: str) -> str:
    # A named casualty is not necessarily the caller. Explicit caller role wins.
    for role in ("вызывает мама", "вызывает папа", "вызывает муж", "вызывает супруг",
                 "вызывает отец", "вызывает брат", "вызывает подруга", "вызывает себе"):
        if role in text.lower():
            return role.replace("вызывает ", "").capitalize()
    match = FULL_NAME.search(text)
    if match:
        return " ".join(match.groups())
    return "Заявитель"


def _title(situation: str) -> str:
    head = re.split(r"[,.;]", situation, maxsplit=1)[0].strip()
    head = head or situation.strip()
    if len(head) > MAX_TITLE_CHARS:
        head = head[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    if len(head) < 3:
        head = situation.strip()[:MAX_TITLE_CHARS]
    return head[0].upper() + head[1:]


def _classify(situation: str) -> tuple[str, str, str | None]:
    lowered = situation.lower()
    for _rule, category, profile, keywords in CATEGORY_RULES:
        for keyword in keywords:
            if keyword in lowered:
                return category, profile, keyword
    return "other", "general", None


def _difficulty(situation: str, unknown_count: int) -> str:
    lowered = situation.lower()
    if unknown_count >= 2 or "не блокированы" in lowered or "не знает" in lowered:
        return "advanced"
    if unknown_count == 1 or len(situation) > 220:
        return "standard"
    return "basic"


def _facts(situation: str, address: str, clarification: str, phones: list[str],
           caller: str) -> tuple[list[str], list[str]]:
    known: list[str] = [f"Заявитель: {caller}"]
    if phones:
        known.append("Телефон заявителя: " + ", ".join(dict.fromkeys(phones)))
    known.append(f"Адрес происшествия по словам заявителя: {address}")
    if clarification:
        known.append(f"Уточнение адреса, которое заявитель сообщает по дополнительному вопросу: {clarification}")
    # Фабула разбивается на отдельные сведения по знакам препинания источника,
    # чтобы модель выдавала их порциями в ответ на вопросы, а не одним блоком.
    # Фрагменты с уже перечисленными ФИО и телефоном пропускаются.
    for part in re.split(r"[.;,]", situation):
        part = part.strip(" ;,")
        if len(part) < 4 or PHONE.search(part):
            continue
        if caller != "Заявитель" and part in caller:
            continue
        known.append(part[:MAX_FACT_CHARS])

    unknown: list[str] = []
    lowered = situation.lower()
    for marker, text in UNKNOWN_MARKERS:
        if marker in lowered and text not in unknown:
            unknown.append(text)

    deduplicated: list[str] = []
    for fact in known:
        fact = fact.strip()
        if fact and fact not in deduplicated:
            deduplicated.append(fact[:MAX_FACT_CHARS])
    return deduplicated[:MAX_KNOWN_FACTS], unknown[:20]


def _opening(situation: str, category: str) -> str:
    summary = re.split(r"[,.;]", situation, maxsplit=1)[0].strip()
    lead = {
        "fire": "Здравствуйте, у нас тут",
        "medical": "Здравствуйте, нужна скорая",
        "traffic": "Здравствуйте, здесь",
        "public": "Здравствуйте, вызовите полицию",
        "utilities": "Здравствуйте, у нас",
        "other": "Здравствуйте, тут",
    }[category]
    text = f"Это учебный звонок. {lead}: {summary.lower()}."
    return text[:1000]


# Служебные слова не несут смысла происшествия и в опоры не годятся.
STOP_WORDS = {
    "или", "его", "её", "ее", "как", "что", "это", "для", "над", "под", "при",
    "без", "все", "они", "она", "оно", "там", "тут", "уже", "еще", "ещё", "был",
    "была", "было", "были", "есть", "нет", "около", "рядом", "очень", "самый",
}
# Длина опоры-основы. Шесть букв отсекают русские окончания у большинства слов
# («мусорного» и «мусорный» дают «мусорн»), но не склеивают разные слова.
STEM_LENGTH = 6


def _anchors(summary: str) -> list[str]:
    """Смысловые опоры фабулы для проверки описания.

    Дословная фраза в роли единственной опоры делала критерий непроходимым:
    диспетчер пишет своими словами, и «Дерутся 15 человек с прутами» не
    содержит подстроки «Дерутся 10-15 человек», хотя суть передана верно.
    Опорами становятся основы значимых слов — проверка остаётся буквальным
    поиском подстроки, без нечёткого сравнения и порогов.

    Числа в опоры не идут: заявитель называет их приблизительно («10-15»), а
    оператор записывает по-своему. Количество пострадавших проверяется
    отдельным признаком карточки, а не текстом описания.
    """
    words: list[str] = []
    for raw in re.findall(r"[А-Яа-яЁёA-Za-z]+", summary):
        word = raw.casefold().replace("ё", "е")
        if len(word) < 4 or word in STOP_WORDS:
            continue
        if word[:STEM_LENGTH] not in [w[:STEM_LENGTH] for w in words]:
            words.append(word)
    # Берутся две самые длинные опоры: длинное слово конкретнее и реже
    # случайно совпадает. Чем меньше опор, тем реже верный по сути ответ
    # отклоняется из-за иной формулировки.
    chosen = sorted(sorted(words, key=len, reverse=True)[:2], key=words.index)
    return [word[:STEM_LENGTH] for word in chosen]


# Вводные по категории происшествия. Источник — билеты — их не содержит, и
# выдумывать обстоятельства за источник нельзя, поэтому берутся общие для
# категории осложнения, одинаковые и проверяемые. Преподаватель правит их при
# валидации, как и остальной черновик.
# Профильная служба ДДС по категории: обучающийся работает именно в ней.
CATEGORY_OWNER: dict[str, str] = {
    "fire": "Служба 101",
    "public": "Служба 102",
    "medical": "Служба 103",
    "utilities": "Деп. ЖКХ",
    "traffic": "ЦОДД",
}

# Оперативные вводные от реагирующей стороны. Каждая открывает ровно один
# статус: диспетчер не ставит «Прибытие» раньше, чем ему сообщили о прибытии.
# Тексты общие для категории — билет их не содержит, а выдумывать за источник
# конкретные обстоятельства нельзя. Преподаватель правит их при валидации.
def _operational(owner: str) -> list[dict[str, Any]]:
    return [
        {"id": "dispatched", "after_seconds": 40, "source": f"Дежурный {owner}",
         "text": "Наряд сформирован и направлен по адресу",
         "unlocks_status": "Начало реагирования"},
        {"id": "arrived", "after_seconds": 100, "source": f"Дежурный {owner}",
         "text": "Наряд прибыл на адрес", "unlocks_status": "Прибытие"},
        {"id": "working", "after_seconds": 160, "source": f"Дежурный {owner}",
         "text": "Приступили к работам на месте", "unlocks_status": "Проведение работ"},
        {"id": "done", "after_seconds": 230, "source": f"Дежурный {owner}",
         "text": "Работы на месте закончены, обстановка нормализована",
         "unlocks_status": "Работы завершены"},
    ]


CATEGORY_UPDATES: dict[str, list[dict[str, Any]]] = {
    "fire": [
        {"id": "spread", "after_seconds": 45, "source": "Заявитель",
         "text": "Огонь перекинулся дальше, людей выводят из здания"},
        {"id": "access", "after_seconds": 150, "source": "Служба 101",
         "text": "Проезд к месту затруднён припаркованными машинами"},
    ],
    "medical": [
        {"id": "worse", "after_seconds": 45, "source": "Заявитель",
         "text": "Состояние пострадавшего ухудшилось, он потерял сознание"},
    ],
    "public": [
        {"id": "escalation", "after_seconds": 45, "source": "Заявитель",
         "text": "Конфликт разрастается, участников стало больше"},
    ],
    "traffic": [
        {"id": "jam", "after_seconds": 60, "source": "ЦОДД",
         "text": "На участке образовался затор, движение перекрыто"},
    ],
    "utilities": [
        {"id": "spreading", "after_seconds": 60, "source": "Деп. ЖКХ",
         "text": "Подтопление распространяется на соседние подъезды"},
    ],
}


def _rubric(ticket: int, call: int, caller: str, known: list[str], situation: str) -> dict[str, Any]:
    """Deterministic draft reference built only from literal source fragments.

    The task statement sets the default check window at 30 seconds. Criteria stay
    minimal on purpose: a teacher extends them during validation, and an invented
    criterion would be a fabricated grading rule rather than a source fact.
    """
    criteria: list[dict[str, Any]] = []
    # Критерий по ФИО ставится только когда источник действительно называет имя.
    # Там, где билет указывает лишь роль («вызывает мама»), фамилии заявителя в
    # источнике нет, и требовать её в карточке значило бы проверять выдумку.
    if " " in caller:
        criteria.append({"id": "caller", "label": "ФИО заявителя", "field": "caller_name",
                         "mode": "equals", "expected": [caller], "weight": 1})
    # Первый фрагмент фабулы — суть происшествия; он обязан попасть в описание.
    summary = re.split(r"[,.;]", situation, maxsplit=1)[0].strip()
    anchors = _anchors(summary) if len(summary) >= 4 else []
    if anchors:
        criteria.append({"id": "summary", "label": "Суть происшествия в описании", "field": "description",
                         "mode": "contains_all", "expected": anchors,
                         # В отчёте показывается исходная фабула, а не основы слов.
                         "expected_hint": summary, "weight": 1})
    if not criteria:
        fallback = _anchors(situation[:200])
        criteria.append({"id": "summary", "label": "Описание заполнено", "field": "description",
                         "mode": "contains_all", "expected": fallback or [situation[:60].strip()], "weight": 1})
    return {"title": f"Эталон билета {ticket}, вызов {call}",
            "time_limit_seconds": 30, "criteria": criteria}


def _curated_dds_card(draft_id: str, location: str, phones: list[str], overlay: dict) -> tuple[dict, str]:
    """Use only a checked address transcription and an exact classifier record."""
    if overlay['source_address'] != location:
        raise ValueError(f'{draft_id}: source address changed; review the DDS card')
    backend_path = str(ROOT / 'backend')
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    from classifier import get_catalog, resolve
    from routing import main_services, preview

    version = get_catalog()['version']
    record = resolve(overlay['classifier_id'], version)
    owner = next(iter(main_services(record['main_service'])), '')
    if not owner:
        raise ValueError(f'{draft_id}: classifier has no mapped primary DDS')
    card = {
        'address_note': location,
        'phone': phones[0] if phones else '',
        'incident_type': record['incident_type'],
        'classifier_id': record['id'],
        'classifier_version': version,
        'classifier_group': record['group_id'],
        'classifier_features': record['features'],
        **overlay['fields'],
    }
    routed = preview(card)
    card['services'] = list(dict.fromkeys([owner, *(item['service'] for item in routed['suggestions'])]))
    # A synthetic contact is explicit in the reviewed training card. Recipients
    # without a number remain visible, but cannot be called by the DDS.
    card['service_phones'] = {owner: '+7 900 000-00-01'}
    return card, owner


def build(source: dict[str, Any], keep_source_phones: bool) -> dict[str, Any]:
    drafts: list[dict[str, Any]] = []
    unclassified: list[str] = []
    curated = json.loads(CURATED_DDS.read_text(encoding='utf-8'))
    for ticket in source["tickets"]:
        for call in ticket["calls"]:
            situation, phones = _normalize_phones(call["situation"], keep_source_phones)
            address = call["address"].strip()
            clarification = (call.get("clarification") or "").strip()
            caller = _caller_name(situation)
            category, profile, keyword = _classify(situation)
            known, unknown = _facts(situation, address, clarification, phones, caller)
            location = address if not clarification else f"{address} ({clarification})"
            draft_id = DRAFT_ID.format(ticket=ticket["number"], call=call["n"])
            if keyword is None:
                unclassified.append(draft_id)
            draft = {
                "id": draft_id,
                "ticket": ticket["number"],
                "call": call["n"],
                "source_page": ticket["page"],
                "classified_by": keyword,
                "scenario": {
                    "id": draft_id,
                    "title": _title(situation),
                    "category_id": category,
                    "difficulty": _difficulty(situation, len(unknown)),
                    "dds_profile": profile,
                    "learning_objectives": (
                        f"Отработать приём вызова по билету {ticket['number']}, задание {call['n']}: "
                        "полный сбор адреса, данных заявителя и признаков происшествия."
                    ),
                    "description": f"Билет {ticket['number']}, вызов {call['n']} из учебных билетов ГБУ «Система 112».",
                    "victim_name": caller[:80],
                    "incident": situation[:MAX_INCIDENT_CHARS],
                    "location": location[:MAX_LOCATION_CHARS],
                    "known_facts": known,
                    "unknown_facts": unknown,
                    "emotion": EMOTIONS[category],
                    "behavior": BEHAVIOR,
                    "opening": _opening(situation, category),
                    "enabled": False,
                },
                "rubric": _rubric(ticket["number"], call["n"], caller, known, situation),
            }
            if draft_id in curated:
                card, owner = _curated_dds_card(draft_id, location, phones, curated[draft_id])
                draft['scenario']['prefilled_card'] = card
                draft['scenario']['owner_service'] = owner
            complete(draft, call, phones)
            drafts.append(draft)

    payload = json.dumps(drafts, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source["source"],
        "phones": "source" if keep_source_phones else "synthetic",
        "catalog_sha256": hashlib.sha256(payload).hexdigest(),
        "metadata": {
            "tickets": len(source["tickets"]),
            "drafts": len(drafts),
            "unclassified": unclassified,
            "by_category": {
                category: sum(1 for d in drafts if d["scenario"]["category_id"] == category)
                for category in sorted({d["scenario"]["category_id"] for d in drafts})
            },
        },
        "drafts": drafts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--keep-source-phones", action="store_true",
                        help="Reproduce booklet telephone numbers instead of synthetic ones")
    args = parser.parse_args()

    source = json.loads(args.source.read_text(encoding="utf-8"))
    catalog = build(source, args.keep_source_phones)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = catalog["metadata"]
    print(f"tickets={meta['tickets']} drafts={meta['drafts']} categories={meta['by_category']}")
    if meta["unclassified"]:
        print("unclassified:", ", ".join(meta["unclassified"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
