"""Запустить свежее голосовое обучающее занятие ДДС с подсказками для каждого ученика.

Обучение проходит по IP-телефону: доклады бригаде и начальнику смены
произносятся голосом. Ученикам из --phones назначается их учебный номер;
группа, где у кого-то нет номера, получает занятие без телефона, а номера
преподаватель задаёт кнопкой «Настроить IP-телефоны». Если обучение уже идёт,
оно начинается заново (прежние результаты остаются в отчётах).

    python tools/ensure_training.py --base-url https://127.0.0.1:3000 --password <пароль> \\
        --phones kursant1=201,kursant2=202
"""
import argparse
import json
import ssl
from pathlib import Path

from seed_demo import Client

TITLE = "Обучение: первое занятие ДДС с подсказками"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--teacher", default="prepod")
    parser.add_argument("--password", required=True)
    parser.add_argument("--phones", default="kursant1=201,kursant2=202",
                        help="логин=внутренний номер через запятую")
    parser.add_argument("--insecure", action="store_true", help="не проверять сертификат (учебный стенд)")
    args = parser.parse_args()
    teacher = Client(args.base_url)
    if args.insecure:
        teacher.context = ssl._create_unverified_context()
    teacher.call("POST", "/auth/login", {"username": args.teacher, "password": args.password})
    guided = json.loads((Path(__file__).parent / "data" / "dds_guided_practice.json").read_text(encoding="utf-8"))
    published = guided["id"] in {item["id"] for item in teacher.catalog()}
    saved = teacher.base_path
    teacher.base_path = "/api"
    try:
        if not published:
            teacher.call("POST", "/scenarios", guided, expect=(200, 201, 409))
        else:
            # Опубликованный сценарий обновляется из файла: адрес, доклады и эталон.
            current = teacher.call("GET", f"/scenarios/{guided['id']}")[1]
            teacher.call("PUT", f"/scenarios/{guided['id']}",
                         {**guided, "enabled": True, "version": current["version"]})
    finally:
        teacher.base_path = saved
    by_login = {item["username"]: item["id"] for item in teacher.call("GET", "/instructor/students")[1]}
    phones = {by_login[login]: number for login, number in
              (pair.split("=", 1) for pair in args.phones.split(",") if "=" in pair) if login in by_login}
    lessons = teacher.call("GET", "/instructor/lessons")[1]
    groups = [group for group in teacher.call("GET", "/instructor/groups")[1] if group.get("member_ids")]
    # Группы, где у всех учеников есть номер, идут первыми: им достаётся голосовое обучение.
    groups.sort(key=lambda group: not set(group["member_ids"]) <= set(phones))
    covered, created = set(), set()
    for group in groups:
        members = set(group["member_ids"])
        if not members - covered:
            continue
        voice = members <= set(phones)
        for item in lessons:
            if item["title"] == TITLE and item["group_id"] == group["id"] and item["state"] != "finished":
                teacher.call("POST", f"/instructor/lessons/{item['id']}/finish", {"reason": "Обучение начато заново"})
        body = {"title": TITLE, "group_id": group["id"], "mode": "actions",
                "prefilled_scenario_ids": [guided["id"]], "cards_per_student": 1, "practice_with_hints": True,
                "transport": "sip" if voice else "text"}
        if voice:
            body["sip_extensions"] = {uid: phones[uid] for uid in members}
        _, lesson = teacher.call("POST", "/instructor/lessons", body)
        teacher.call("POST", f"/instructor/lessons/{lesson['id']}/start")
        covered |= members
        created.add(lesson["id"])
        print(f"{group['title']}: обучение {'по IP-телефону' if voice else 'без телефона — задайте номера'}")
    # Прежние обучающие занятия в других группах — дубли: у учеников уже есть новое.
    for item in teacher.call("GET", "/instructor/lessons")[1]:
        if item["title"] == TITLE and item["state"] != "finished" and item["id"] not in created:
            teacher.call("POST", f"/instructor/lessons/{item['id']}/finish", {"reason": "Дубль обучения"})


if __name__ == "__main__":
    main()
