import pytest

from settings import Settings


@pytest.fixture(autouse=True)
def _no_settings_from_shell(monkeypatch):
    """Tests build their own Settings; a SAMPLE_MODE or ZETARIS_MODE exported in the shell must not leak in."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
