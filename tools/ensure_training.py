"""Запустить свежее обучающее занятие ДДС с подсказками во всех группах преподавателя.

Каждый ученик группы получает пошаговое обучение с нуля: если занятие уже
идёт, оно начинается заново (прежние результаты остаются в отчётах).

    python tools/ensure_training.py --base-url https://127.0.0.1:3000 --teacher prepod --password <пароль>
"""
import argparse
import json
import ssl
from pathlib import Path

from seed_demo import Client

TITLE = "Обучение: первое занятие ДДС с подсказками"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--teacher", default="prepod")
    parser.add_argument("--password", required=True)
    parser.add_argument("--insecure", action="store_true", help="не проверять сертификат (учебный стенд)")
    args = parser.parse_args()
    teacher = Client(args.base_url)
    if args.insecure:
        teacher.context = ssl._create_unverified_context()
    teacher.call("POST", "/auth/login", {"username": args.teacher, "password": args.password})
    guided = json.loads((Path(__file__).parent / "data" / "dds_guided_practice.json").read_text(encoding="utf-8"))
    if guided["id"] not in {item["id"] for item in teacher.catalog()}:
        saved = teacher.base_path
        teacher.base_path = "/api"
        try:
            teacher.call("POST", "/scenarios", guided, expect=(200, 201, 409))
        finally:
            teacher.base_path = saved
    lessons = teacher.call("GET", "/instructor/lessons")[1]
    for group in teacher.call("GET", "/instructor/groups")[1]:
        if not group.get("member_ids"):
            continue
        current = [item for item in lessons if item["title"] == TITLE and item["group_id"] == group["id"]]
        live = next((item for item in current if item["state"] in ("running", "planned", "stopping")), None)
        if live:
            teacher.call("POST", f"/instructor/lessons/{live['id']}/restart")
            print(f"{group['title']}: обучение начато заново")
            continue
        _, lesson = teacher.call("POST", "/instructor/lessons", {
            "title": TITLE, "group_id": group["id"], "mode": "actions",
            "prefilled_scenario_ids": [guided["id"]], "cards_per_student": 1, "transport": "text"})
        teacher.call("POST", f"/instructor/lessons/{lesson['id']}/start")
        print(f"{group['title']}: обучение запущено")


if __name__ == "__main__":
    main()
