"""Pedagogical metadata, kept independent of routing rules."""
from typing import Literal

Difficulty = Literal['basic', 'standard', 'advanced']
DdsProfile = Literal['general', 'fire', 'police', 'medical', 'gas', 'utilities']
DIFFICULTIES = [
    {'id': 'basic', 'title': 'Базовый', 'description': 'Одна учебная ситуация, последовательный сбор явно заданных фактов.'},
    {'id': 'standard', 'title': 'Стандартный', 'description': 'Уточнение неполных сведений и взаимодействие нескольких служб.'},
    {'id': 'advanced', 'title': 'Повышенный', 'description': 'Неоднозначные сведения, сложное поведение заявителя и несколько задач.'},
]
PROFILES = [{'id': key, 'title': title} for key, title in [
    ('general', 'Общий профиль 112'), ('fire', 'Пожарно-спасательная ДДС'),
    ('police', 'Полиция'), ('medical', 'Скорая помощь'), ('gas', 'Аварийная газовая служба'),
    ('utilities', 'ЖКХ и городские службы'),
]]


def metadata(scenario):
    return {key: scenario.get(key, default) for key, default in (
        ('difficulty', 'basic'), ('dds_profile', 'general'), ('learning_objectives', ''))}


def matches(scenario, selection):
    values = metadata(scenario)
    return all(not selection.get(key) or selection[key] == values[key]
               for key in ('difficulty', 'dds_profile'))
