"""Pedagogical event categories used to group training scenarios."""
from typing import Literal

CategoryId = Literal['fire', 'traffic', 'medical', 'utilities', 'public', 'other']
CATEGORIES = [
    {'id': 'fire', 'title': 'Пожары'}, {'id': 'traffic', 'title': 'ДТП и транспорт'},
    {'id': 'medical', 'title': 'Медицина'}, {'id': 'utilities', 'title': 'ЖКХ и инженерные сети'},
    {'id': 'public', 'title': 'Общественная безопасность'}, {'id': 'other', 'title': 'Прочие / без категории'},
]
