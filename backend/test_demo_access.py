"""Public demo credentials are explicit ENV data, never operational secrets."""
import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def demo_client():
    spec = importlib.util.spec_from_file_location('frontend_demo_test', Path(__file__).resolve().parents[1] / 'frontend/server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return TestClient(module.app, base_url='http://127.0.0.1:3000')


def test_demo_access_disabled_by_default(demo_client, monkeypatch):
    monkeypatch.delenv('DEMO_ACCESS_ENABLED', raising=False)
    monkeypatch.setenv('BACKEND_TOKEN', 'private-operational-token')
    response = demo_client.get('/api/v1/auth/demo-access')
    assert response.json() == {'enabled': False, 'accounts': []}
    assert response.headers['cache-control'] == 'no-store'


def test_only_explicit_public_fields_are_returned(demo_client, monkeypatch):
    monkeypatch.setenv('DEMO_ACCESS_ENABLED', 'true')
    monkeypatch.setenv('DEMO_ACCOUNTS_JSON', json.dumps([{
        'username': 'demo.student', 'password': 'example-only-password', 'role': 'student',
        'workstation': 'АРМ-1', 'backend_token': 'never-publish-this'}]))
    response = demo_client.get('/api/v1/auth/demo-access')
    assert response.status_code == 200
    value = response.json()['accounts'][0]
    assert value['password'] == 'example-only-password'
    assert value['workstation'] == 'АРМ-1'
    assert 'never-publish-this' not in response.text
    assert set(value) == {'username', 'password', 'role', 'workstation', 'display_name'}


@pytest.mark.parametrize('value', ['broken json', '{}', '[]', '[{"role":"founder"}]'])
def test_invalid_configuration_does_not_expose_raw_values(demo_client, monkeypatch, value):
    monkeypatch.setenv('DEMO_ACCESS_ENABLED', 'true')
    monkeypatch.setenv('DEMO_ACCOUNTS_JSON', value)
    response = demo_client.get('/api/v1/auth/demo-access')
    assert response.status_code == 503
    assert response.json() == {'detail': 'Демонстрационные доступы пока не настроены'}
