"""Deterministic, rubric-driven evaluation of a completed training card.

This module intentionally contains no domain rules.  A rubric is configurable
training data, not an official emergency-service regulation.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TEXT_FIELDS = frozenset(
    {
        "caller_name",
        "caller_status",
        "phone",
        "supplied_phone",
        "scene_phone",
        "country",
        "region",
        "city",
        "district",
        "area",
        "object",
        "street",
        "house",
        "building",
        "structure",
        "apartment",
        "entrance",
        "floor",
        "code",
        "address_note",
        "description",
        "incident_type",
        "classifier_id",
        "classifier_version",
        "classifier_group",
        "place",
        "sign",
        "detail",
    }
)
FLAG_FIELDS = frozenset(
    {
        "injured",
        "no_access",
        "threat_to_people",
        "offense",
        "injured_offsite",
        "gasification",
        "refused",
        "no_contact",
        "interrupted",
    }
)
SERVICE_FIELD = "services"
CARD_FIELDS = TEXT_FIELDS | FLAG_FIELDS | {SERVICE_FIELD}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SPACE = re.compile(r"\s+")

LIMITATIONS = [
    "Балл по эталону не учитывает семантику, грамматику и качество голосового взаимодействия; ИИ-разбор запрашивается отдельно.",
    "Настраиваемая рубрика является учебной конфигурацией, а не официальным регламентом 112.",
]


def normalize(value: str) -> str:
    """Apply the complete and deliberately small rubric-v1 normalization."""
    return SPACE.sub(" ", unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")).strip()


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str = Field(min_length=1, max_length=200)
    field: str
    expected: list[str] = Field(min_length=1, max_length=100)
    mode: Literal["equals", "contains_all", "set_equals"]
    weight: int = Field(default=1, ge=1, le=100)

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        if not SAFE_ID.fullmatch(value):
            raise ValueError("criterion id must be a safe ASCII identifier of 1..64 characters")
        return value

    @field_validator("label")
    @classmethod
    def non_blank_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("criterion label must not be blank")
        return value

    @field_validator("field")
    @classmethod
    def known_card_field(cls, value: str) -> str:
        if value not in CARD_FIELDS:
            raise ValueError("criterion field is not an evaluable card field")
        return value

    @field_validator("expected")
    @classmethod
    def bounded_unique_expectations(cls, values: list[str]) -> list[str]:
        normalized: set[str] = set()
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("expected values must be non-blank strings")
            if len(value) > 1000:
                raise ValueError("expected values must not exceed 1000 characters")
            key = normalize(value)
            if key in normalized:
                raise ValueError("expected values must be unique after normalization")
            normalized.add(key)
        return values

    @model_validator(mode="after")
    def valid_field_mode_pair(self) -> "Criterion":
        if self.field != SERVICE_FIELD and len(self.expected) > 20:
            raise ValueError('non-service criteria allow at most 20 expected values')
        if self.field in TEXT_FIELDS and self.mode not in {"equals", "contains_all"}:
            raise ValueError("text fields support equals or contains_all")
        if self.field in FLAG_FIELDS:
            if self.mode != "equals":
                raise ValueError("boolean fields support equals only")
            normalized = [normalize(value) for value in self.expected]
            if len(normalized) != 1 or normalized[0] not in {"true", "false"}:
                raise ValueError("boolean fields require expected ['true'] or ['false']")
        if self.field == SERVICE_FIELD and self.mode != "set_equals":
            raise ValueError("services supports set_equals only")
        return self


DEFAULT_TIME_LIMIT_SECONDS = 180
DEFAULT_RESPONSE_LIMIT_SECONDS = 30


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=120)
    # Заказчик на Q&A назвал два разных норматива: 30 секунд на реакцию
    # (открыть карточку) и 3 минуты на её обработку. Это разные отрезки времени,
    # поэтому они хранятся и проверяются отдельно.
    time_limit_seconds: int = Field(default=DEFAULT_TIME_LIMIT_SECONDS, ge=1, le=86400)
    response_limit_seconds: int = Field(default=DEFAULT_RESPONSE_LIMIT_SECONDS, ge=1, le=86400)
    criteria: list[Criterion] = Field(min_length=1, max_length=30)

    @field_validator("title")
    @classmethod
    def non_blank_title(cls, value: str) -> str:
        if len(value.strip()) < 3:
            raise ValueError("rubric title must contain at least 3 non-whitespace characters")
        return value

    @field_validator("criteria")
    @classmethod
    def unique_criterion_ids(cls, values: list[Criterion]) -> list[Criterion]:
        ids = [criterion.id for criterion in values]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion ids must be unique")
        return values


def _actual(criterion: Criterion, card: dict[str, Any]) -> str | list[str] | bool:
    value = card.get(criterion.field)
    if criterion.field in TEXT_FIELDS:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(f"card field {criterion.field!r} must be a string")
        return value
    if criterion.field in FLAG_FIELDS:
        if value is None:
            return False
        if not isinstance(value, bool):
            raise ValueError(f"card field {criterion.field!r} must be a boolean")
        return value
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("card field 'services' must be a list of strings")
    return list(value)


def _passed(criterion: Criterion, actual: str | list[str] | bool) -> bool:
    expected = [normalize(value) for value in criterion.expected]
    if criterion.field in FLAG_FIELDS:
        return actual is (expected[0] == "true")
    if criterion.mode == "equals":
        assert isinstance(actual, str)
        return normalize(actual) in expected
    if criterion.mode == "contains_all":
        assert isinstance(actual, str)
        normalized_actual = normalize(actual)
        return all(value in normalized_actual for value in expected)
    assert isinstance(actual, list)
    return {normalize(value) for value in actual} == set(expected)


def _recommendation(criterion: Criterion, passed: bool) -> str:
    if passed:
        return "Критерий выполнен."
    expected = ", ".join(criterion.expected)
    if criterion.field == SERVICE_FIELD:
        return f"Проверьте выбор служб в учебной карточке; ожидаемый набор: {expected}."
    if criterion.field in FLAG_FIELDS:
        state = "да" if normalize(criterion.expected[0]) == "true" else "нет"
        return f"Проверьте признак «{criterion.label}» и установите значение «{state}» по условиям сценария."
    if criterion.mode == "contains_all":
        return f"Дополните поле «{criterion.label}»: должны присутствовать элементы: {expected}."
    return f"Проверьте поле «{criterion.label}»; допустимое ожидаемое значение: {expected}."


def evaluate(rubric: dict[str, Any] | None, card: dict[str, Any], elapsed_seconds: int,
             response_seconds: int | None = None) -> dict[str, Any]:
    """Evaluate a card without side effects, inference, fuzzy matching, or an LLM."""
    if isinstance(elapsed_seconds, bool) or not isinstance(elapsed_seconds, int) or elapsed_seconds < 0:
        raise ValueError("elapsed_seconds must be a non-negative integer")
    if response_seconds is not None and (isinstance(response_seconds, bool)
                                         or not isinstance(response_seconds, int) or response_seconds < 0):
        raise ValueError("response_seconds must be a non-negative integer")
    if not isinstance(card, dict):
        raise ValueError("card must be an object")
    if rubric is None:
        return {
            "version": "rubric-v1",
            "status": "not_configured",
            "rubric_title": None,
            "score_percent": None,
            "earned_weight": 0,
            "total_weight": 0,
            "criteria": [],
            "timing": {
                "elapsed_seconds": elapsed_seconds,
                "limit_seconds": None,
                "within_limit": None,
                "response_seconds": response_seconds,
                "response_limit_seconds": None,
                "response_within_limit": None,
            },
            "limitations": list(LIMITATIONS),
        }

    parsed = Rubric.model_validate(rubric)
    results = []
    earned_weight = 0
    total_weight = sum(criterion.weight for criterion in parsed.criteria)
    for criterion in parsed.criteria:
        actual = _actual(criterion, card)
        passed = _passed(criterion, actual)
        if passed:
            earned_weight += criterion.weight
        results.append(
            {
                "id": criterion.id,
                "label": criterion.label,
                "field": criterion.field,
                "expected": list(criterion.expected),
                "actual": actual,
                "passed": passed,
                "weight": criterion.weight,
                "recommendation": _recommendation(criterion, passed),
            }
        )

    return {
        "version": "rubric-v1",
        "status": "evaluated",
        "rubric_title": parsed.title,
        "score_percent": round(earned_weight * 100 / total_weight, 2),
        "earned_weight": earned_weight,
        "total_weight": total_weight,
        "criteria": results,
        "timing": {
            "elapsed_seconds": elapsed_seconds,
            "limit_seconds": parsed.time_limit_seconds,
            "within_limit": elapsed_seconds <= parsed.time_limit_seconds,
            "response_seconds": response_seconds,
            "response_limit_seconds": parsed.response_limit_seconds,
            # Неизмеренная реакция остаётся неизвестной, а не засчитанной.
            "response_within_limit": None if response_seconds is None
            else response_seconds <= parsed.response_limit_seconds,
        },
        "limitations": list(LIMITATIONS),
    }
