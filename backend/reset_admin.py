"""Сброс доступа технического администратора на сервере.

Запуск в контейнере Backend (та же база, что у приложения):
    docker compose exec backend python reset_admin.py --username tech.admin

Создаёт администратора или задаёт существующему новый пароль, снимает блокировку,
второй фактор и прежние сессии. Новый пароль печатается один раз; в журнал аудита
пишется событие без пароля. Пароль можно передать через ENV RESET_ADMIN_PASSWORD.
"""
import argparse
import os
import secrets
import string
import sys

from accounts import Accounts, USERNAME_RE
from database import connect_database


class _Store:
    def __init__(self, target: str):
        self.db = connect_database(target)


def generate_password() -> str:
    # Без похожих символов (0/O, 1/l/I): пароль диктуют и вводят вручную.
    alphabet = "".join(ch for ch in string.ascii_letters + string.digits if ch not in "0O1lI")
    return "-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--username", default="tech.admin")
    parser.add_argument("--display-name", default="Технический администратор")
    args = parser.parse_args(argv)
    username = args.username.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        parser.error("username must be 3-40 safe ASCII characters")
    password = os.environ.get("RESET_ADMIN_PASSWORD") or generate_password()
    if len(password) < 12:
        parser.error("password must contain at least 12 characters")
    target = os.getenv("DATABASE_URL") or os.getenv("DIALOGUE_DB", "dialogue.sqlite3")
    user = Accounts(_Store(target)).reset_admin(username, password, args.display_name.strip() or username)
    print(f"Администратор: {user['username']} ({user['display_name']})")
    if "RESET_ADMIN_PASSWORD" not in os.environ:
        print(f"Новый пароль: {password}")
    print("Прежние сессии, блокировка входа и второй фактор этой учётной записи сброшены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
