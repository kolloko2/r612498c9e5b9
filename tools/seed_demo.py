"""Создать демонстрационные учётные записи, группу и занятия на поднятом стенде.

Скрипт нужен для показа: он воспроизводит одинаковый набор данных на любой
машине, чтобы не заводить преподавателя, группу и занятия руками. Все данные
синтетические; телефоны и адреса взяты из учебных билетов датасета.

Запуск после `python deploy/manage.py up`:

    python tools/seed_demo.py --base-url http://127.0.0.1:3000

Скрипт идемпотентен: повторный запуск обновляет пароли демонстрационных
учётных записей и не создаёт вторую группу с тем же названием. Существующие
занятия он не трогает — новые создаются только если их ещё нет.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import json
import ssl
import sys
import urllib.error
import urllib.request
import uuid

PASSWORD = "Trener112-2026"
GROUP_TITLE = "Смена А (демо)"
ACCOUNTS = [
    ("prepod", "Преподаватель Иванов", "teacher"),
    ("kursant1", "Мария Молодова", "student"),
    ("kursant2", "Дмитрий Лазарев", "student"),
]
# Шесть заготовок из двух билетов: два пожарных, два полицейских, медицина и ЖКХ.
TICKET_DRAFTS = [
    ["ticket-01-1", "ticket-01-2", "ticket-01-3"],
    ["ticket-02-1", "ticket-02-2", "ticket-02-3"],
]


class Client:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.cookie = ""
        self.base_path = "/api/v1"
        self.context = ssl._create_unverified_context() if self.base.startswith("https") else None

    def call(self, method: str, path: str, body=None, *, expect=(200, 201)):
        headers = {
            "Content-Type": "application/json",
            "Origin": self.base,
            "X-Voice-UI": "1",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        request = urllib.request.Request(
            self.base + self.base_path + path, method=method,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=120) as response:
                raw = response.read().decode("utf-8")
                set_cookie = response.headers.get("set-cookie", "")
                if set_cookie:
                    self.cookie = set_cookie.split(";")[0]
                payload = json.loads(raw) if raw else None
                return response.status, payload
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            if error.code in expect:
                return error.code, detail
            raise SystemExit(f"{method} {path} -> {error.code}: {detail}")

    def catalog(self):
        """Frontend proxies the scenario library at /api/scenarios; Backend at /api/v1."""
        status, payload = self.call("GET", "/scenarios", expect=(200, 404))
        if not isinstance(payload, list):
            saved, self.base_path = self.base_path, "/api"
            try:
                status, payload = self.call("GET", "/scenarios", expect=(200, 404))
            finally:
                self.base_path = saved
        return payload if isinstance(payload, list) else []

    def login(self, username: str) -> None:
        self.cookie = ""
        self.call("POST", "/auth/login", {"username": username, "password": PASSWORD})


def ensure_accounts(admin: Client) -> dict[str, str]:
    """Создать демонстрационные учётные записи; существующие оставить как есть."""
    _, users = admin.call("GET", "/admin/users")
    existing = {user["username"]: user for user in users}
    created = []
    for username, display_name, role in ACCOUNTS:
        if username in existing:
            continue
        status, _ = admin.call(
            "POST", "/admin/users",
            {"username": username, "password": PASSWORD, "display_name": display_name, "role": role},
            expect=(201, 409),
        )
        created.append(username)
    if created:
        print("созданы учётные записи:", ", ".join(created))
    else:
        print("учётные записи уже есть")
    _, users = admin.call("GET", "/admin/users")
    return {user["username"]: user["id"] for user in users}


def ensure_group(teacher: Client, student_ids: list[str]) -> str:
    _, groups = teacher.call("GET", "/instructor/groups")
    group = next((item for item in groups if item["title"] == GROUP_TITLE), None)
    if group is None:
        _, group = teacher.call("POST", "/instructor/groups", {"title": GROUP_TITLE})
        print("создана группа:", GROUP_TITLE)
    else:
        print("группа уже есть:", GROUP_TITLE)
    for student_id in student_ids:
        if student_id not in group["member_ids"]:
            _, group = teacher.call(
                "POST", f"/instructor/groups/{group['id']}/members", {"student_id": student_id})
    return group["id"]


def ensure_scenarios(teacher: Client) -> list[str]:
    """Опубликовать сценарии из учебных билетов датасета."""
    published: list[str] = []
    for batch in TICKET_DRAFTS:
        status, result = teacher.call(
            "POST", "/instructor/tickets/publish", {"draft_ids": batch}, expect=(200, 201, 409))
        if isinstance(result, dict):
            published.extend(item["scenario_id"] for item in result.get("published", []))
    if published:
        print("опубликовано сценариев из билетов:", len(published))
    return published


def ensure_simple_scenarios(teacher: Client) -> list[str]:
    """Publish synthetic DDS exercises; never replace an existing teacher edition."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
    from simple_dds import scenarios as simple_scenarios
    existing = {item['id'] for item in teacher.catalog()}
    identifiers = []
    for scenario in simple_scenarios():
        if scenario['id'] not in existing:
            saved, teacher.base_path = teacher.base_path, '/api'
            try:
                teacher.call('POST', '/scenarios', scenario, expect=(200, 201, 409))
            finally:
                teacher.base_path = saved
        identifiers.append(scenario['id'])
    return identifiers


def ensure_assignments(teacher: Client, group_id: str, scenarios: list[str],
                       titles_by_id: dict[str, str]) -> None:
    """Прямые назначения: карточку можно создать без входа в занятие.

    Без них кнопка «создать новую карточку» не предлагает ни одного сценария, и
    единственным входом остаётся занятие преподавателя — для одиночной
    тренировки это лишний шаг.
    """
    _, existing = teacher.call("GET", "/instructor/assignments")
    have = {item["title"] for item in existing}
    made = 0
    for scenario_id in dict.fromkeys(scenarios[:3] + [s for s in scenarios if s.startswith(("refusal_", "simple_dds_"))]):
        title = "Свободная тренировка: " + titles_by_id.get(scenario_id, scenario_id)[:60]
        if title in have:
            continue
        status, _ = teacher.call("POST", "/instructor/assignments",
                                 {"group_id": group_id, "scenario_id": scenario_id, "title": title},
                                 expect=(200, 201, 409))
        if status == 409:
            # Only DDS scenarios with a ready card can be assigned in the DDS release.
            print("пропущено назначение без готовой карточки ДДС:", scenario_id)
            continue
        have.add(title)
        made += 1
    if made:
        print("создано свободных назначений:", made)


# Карточки, которые правильно НЕ принимать. Памятка уделяет этому много
# внимания: диспетчер обязан не только отказать, но и обосновать — почему и
# куда передана информация. Данные синтетические.
REFUSAL_SCENARIOS = [
    {
        "id": "refusal_territory",
        "title": "Не наша территория: подтопление в соседнем округе",
        "category_id": "utilities", "difficulty": "standard", "dds_profile": "utilities",
        "victim_name": "Зайцева Ольга Петровна",
        "incident": "Подтопление подвала жилого дома, адрес относится к соседнему округу",
        "location": "Московская область, Люберцы, улица Инициативная, дом 4",
        "known_facts": ["Адрес за пределами зоны ответственности службы",
                        "Вода поступает в подвал около часа"],
        "unknown_facts": ["Источник поступления воды неизвестен"],
        "emotion": "Встревожена", "behavior": "Сообщает известные факты",
        "opening": "Здравствуйте, у нас подвал заливает, дом на Инициативной.",
        "owner_service": "Деп. ЖКХ",
        "dds_expectation": {
            "should_accept": False, "refusal_kind": "foreign_territory",
            "refusal_keywords": ["территория", "передан"],
            "update_response_limit_seconds": 90,
        },
        "learning_objectives": "Отработать отказ по территориальности с обоснованием и передачей.",
        "description": "Учебная карточка: происшествие вне зоны ответственности службы.",
    },
    {
        "id": "refusal_duplicate",
        "title": "Дубль: повторное сообщение о том же происшествии",
        "category_id": "fire", "difficulty": "standard", "dds_profile": "fire",
        "victim_name": "Ковалёв Артём Игоревич",
        "incident": "Повторное сообщение о задымлении по адресу, который уже отрабатывается",
        "location": "Москва, улица Берзарина, дом 21",
        "known_facts": ["Происшествие по этому адресу уже принято ранее",
                        "Основная карточка № 910001 в работе"],
        "unknown_facts": ["Новых обстоятельств заявитель не сообщает"],
        "emotion": "Спокоен", "behavior": "Повторяет ранее сообщённые сведения",
        "opening": "Здравствуйте, тут дым из мусоропровода, я уже звонил.",
        "owner_service": "Служба 101",
        "dds_expectation": {
            "should_accept": False, "refusal_kind": "duplicate",
            "refusal_keywords": ["дубль", "910001"],
            "update_response_limit_seconds": 90,
        },
        "learning_objectives": "Отработать распознавание дубля и отказ со ссылкой на основную карточку.",
        "description": "Учебная карточка: дубль уже отрабатываемого происшествия.",
    },
]


def ensure_refusal_scenarios(teacher: Client) -> list[str]:
    """Создать сценарии неправильного приёма, если их ещё нет."""
    existing = {item["id"] for item in teacher.catalog()}
    created = []
    for scenario in REFUSAL_SCENARIOS:
        if scenario["id"] in existing:
            continue
        saved = teacher.base_path
        teacher.base_path = "/api"
        try:
            teacher.call("POST", "/scenarios", {**scenario, "enabled": True}, expect=(200, 201, 409))
        finally:
            teacher.base_path = saved
        created.append(scenario["id"])
    if created:
        print("созданы сценарии отказа:", ", ".join(created))
    return [scenario["id"] for scenario in REFUSAL_SCENARIOS]


def ensure_lessons(teacher: Client, group_id: str, scenarios: list[str],
                   student_ids: dict[str, str]) -> None:
    _, lessons = teacher.call("GET", "/instructor/lessons")
    titles = {lesson["title"] for lesson in lessons}
    places = {student_ids["kursant1"]: "АРМ-1", student_ids["kursant2"]: "АРМ-2"}

    # Первое занятие — пошаговое обучение: наставник на экране показывает, куда
    # нажать, какой статус поставить и что записать по каждому докладу бригады.
    guided_title = "Обучение: первое занятие ДДС с подсказками"
    if guided_title not in titles:
        guided = json.loads((Path(__file__).parent / "data" / "dds_guided_practice.json").read_text(encoding="utf-8"))
        if guided["id"] not in {item["id"] for item in teacher.catalog()}:
            saved = teacher.base_path
            teacher.base_path = "/api"
            try:
                teacher.call("POST", "/scenarios", guided, expect=(200, 201, 409))
            finally:
                teacher.base_path = saved
        _, lesson = teacher.call("POST", "/instructor/lessons", {
            "title": guided_title, "group_id": group_id, "mode": "actions",
            "prefilled_scenario_ids": [guided["id"]], "cards_per_student": 1, "practice_with_hints": True,
            "workstations": places, "transport": "sip",
            "sip_extensions": {student_ids["kursant1"]: "201", student_ids["kursant2"]: "202"},
        })
        teacher.call("POST", f"/instructor/lessons/{lesson['id']}/start")
        print("создано и запущено занятие:", guided_title)

    dds_title = "ДДС: дежурная смена (демо)"
    if dds_title not in titles and len(scenarios) >= 4:
        _, lesson = teacher.call("POST", "/instructor/lessons", {
            "title": dds_title, "group_id": group_id, "mode": "actions",
            "prefilled_scenario_ids": scenarios[:4],
            "cards_per_student": 6, "parallel_cards": 2,
            "workstations": places,
            # Адресное задание: второму месту — конкретный сценарий.
            "student_scenarios": {student_ids["kursant2"]: scenarios[0]},
            "transport": "text",
        })
        teacher.call("POST", f"/instructor/lessons/{lesson['id']}/start")
        print("создано и запущено занятие:", dds_title)


def ensure_materials(teacher: Client, group_ids: list[str]) -> None:
    """Учебные памятки ДДС: порядок работы, образцы записей, доклад по телефону."""
    materials = json.loads((Path(__file__).parent / "data" / "materials.json").read_text(encoding="utf-8"))
    existing = {item["title"] for item in teacher.call("GET", "/instructor/materials")[1]}
    for material in materials:
        if material["title"] not in existing:
            teacher.call("POST", "/instructor/materials", {**material, "group_ids": group_ids, "published": True})
            print("опубликован материал:", material["title"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000",
                        help="Адрес Frontend, например http://127.0.0.1:3000")
    parser.add_argument("--admin", required=True, help="Логин администратора")
    parser.add_argument("--admin-password", required=True, help="Пароль администратора")
    args = parser.parse_args()

    admin = Client(args.base_url)
    status, _ = admin.call("POST", "/auth/login",
                           {"username": args.admin, "password": args.admin_password})
    if status != 200:
        raise SystemExit("Не удалось войти администратором")

    ids = ensure_accounts(admin)
    teacher = Client(args.base_url)
    teacher.login("prepod")
    student_ids = {name: ids[name] for name in ("kursant1", "kursant2")}
    group_id = ensure_group(teacher, list(student_ids.values()))
    scenarios = ensure_scenarios(teacher)
    if not scenarios:
        scenarios = [item["id"] for item in teacher.catalog() if item.get("enabled")][:6]
    simple = ensure_simple_scenarios(teacher)
    titles = {item["id"]: item["title"] for item in teacher.catalog()}
    refusals = ensure_refusal_scenarios(teacher)
    titles.update({item["id"]: item["title"] for item in REFUSAL_SCENARIOS})
    ensure_assignments(teacher, group_id, scenarios + refusals + simple, titles)
    ensure_lessons(teacher, group_id, scenarios, student_ids)
    ensure_materials(teacher, [group_id])
    print()
    print("Готово. Вход:", args.base_url + "/login")
    print("  преподаватель  prepod    /", PASSWORD)
    print("  обучающиеся    kursant1, kursant2 /", PASSWORD)


if __name__ == "__main__":
    main()
