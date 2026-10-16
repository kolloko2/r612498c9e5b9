"""Keep historical operator tests explicit while the product defaults to DDS."""
import pytest


@pytest.fixture(autouse=True)
def historical_operator_profile(monkeypatch):
    # The old operator implementation remains covered. DDS release tests remove
    # this flag explicitly to verify the real default and its API boundaries.
    monkeypatch.setenv('OPERATOR_112_ENABLED', 'true')
