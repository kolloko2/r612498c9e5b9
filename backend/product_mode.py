"""Product scope for the DDS training release."""
import os

from fastapi import HTTPException


def operator_112_enabled():
    return os.getenv('OPERATOR_112_ENABLED', 'false').lower() == 'true'


def require_dds_lesson(value):
    if not operator_112_enabled() and (
            value.get('mode', 'fill') != 'actions' or value.get('allow_mode_switch')):
        raise HTTPException(409, 'Доступны только занятия диспетчера ДДС без переключения режима')
