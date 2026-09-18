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
    ("kursant1", "Курсант Петров", "student"),
    ("kursant2", "Курсант Сидорова", "student"),
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
            self.base + "/api/v1" + path, method=method,
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


def ensure_lessons(teacher: Client, group_id: str, scenarios: list[str],
                   student_ids: dict[str, str]) -> None:
    _, lessons = teacher.call("GET", "/instructor/lessons")
    titles = {lesson["title"] for lesson in lessons}
    places = {student_ids["kursant1"]: "АРМ-1", student_ids["kursant2"]: "АРМ-2"}

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

    text_title = "Полный цикл 112: приём вызова (текст)"
    if text_title not in titles and len(scenarios) >= 3:
        # Полный цикл в текстовом канале: заявителя играет модель, и разговор
        # идёт с клавиатуры, без SIP и речевых моделей. Это самый простой способ
        # показать диалог на чужой машине.
        _, lesson = teacher.call("POST", "/instructor/lessons", {
            "title": text_title, "group_id": group_id, "mode": "fill",
            "scenario_ids": scenarios[:3], "cards_per_student": 3,
            "parallel_cards": 1, "workstations": places, "transport": "text",
        })
        teacher.call("POST", f"/instructor/lessons/{lesson['id']}/start")
        print("создано и запущено занятие:", text_title)

    mixed_title = "Смешанное: ДДС + полный цикл 112 (телефон)"
    if mixed_title not in titles and len(scenarios) >= 6:
        teacher.call("POST", "/instructor/lessons", {
            "title": mixed_title, "group_id": group_id, "mode": "mixed",
            "scenario_ids": scenarios[4:6], "prefilled_scenario_ids": scenarios[:2],
            "allow_mode_switch": True, "cards_per_student": 4, "parallel_cards": 1,
            "workstations": places,
            "transport": "sip",
            "sip_extensions": {student_ids["kursant1"]: "201", student_ids["kursant2"]: "202"},
        })
        print("создано занятие (ожидает старта):", mixed_title)


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
        _, lessons_scenarios = teacher.call("GET", "/scenarios")
        scenarios = [item["id"] for item in lessons_scenarios if item.get("enabled")][:6]
    ensure_lessons(teacher, group_id, scenarios, student_ids)
    print()
    print("Готово. Вход:", args.base_url + "/login")
    print("  преподаватель  prepod    /", PASSWORD)
    print("  обучающиеся    kursant1, kursant2 /", PASSWORD)


if __name__ == "__main__":
    main()
