"""Опубликовать учебные памятки ДДС для всех групп преподавателя (идемпотентно).

    python tools/seed_materials.py --base-url https://127.0.0.1:3000 --password <пароль преподавателя> --insecure
"""
import argparse
import json
import ssl
from pathlib import Path

from seed_demo import Client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--teacher", default="prepod")
    parser.add_argument("--password", required=True)
    parser.add_argument("--insecure", action="store_true", help="не проверять сертификат (учебный стенд)")
    args = parser.parse_args()
    teacher = Client(args.base_url)
    if args.insecure:
        teacher.context = ssl._create_unverified_context()
    teacher.call("POST", "/auth/login", {"username": args.teacher, "password": args.password})
    materials = json.loads((Path(__file__).parent / "data" / "materials.json").read_text(encoding="utf-8"))
    groups = [group["id"] for group in teacher.call("GET", "/instructor/groups")[1]]
    existing = {item["title"]: item for item in teacher.call("GET", "/instructor/materials")[1]}
    for material in materials:
        body = {**material, "group_ids": groups[:100], "published": True}
        if material["title"] in existing:
            current = existing[material["title"]]
            teacher.call("PUT", f"/instructor/materials/{current['id']}", {**body, "revision": current["revision"]})
            print("обновлено:", material["title"])
        else:
            teacher.call("POST", "/instructor/materials", body)
            print("опубликовано:", material["title"])


if __name__ == "__main__":
    main()
