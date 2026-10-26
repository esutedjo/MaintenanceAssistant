from pathlib import Path

import pytest

from settings import Settings, get_settings
from tools import llm_recordings


@pytest.fixture(autouse=True)
def _no_settings_from_shell(monkeypatch):
    """Tests build their own Settings; a SAMPLE_MODE or ZETARIS_MODE exported in the shell must not leak in."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()


@pytest.fixture(autouse=True, scope="session")
def _no_recorded_llm_replies(tmp_path_factory):
    """Tests pin the deterministic path; tests/test_llm_recordings.py runs the golden briefs with the recordings."""
    saved = llm_recordings.RECORDINGS
    llm_recordings.RECORDINGS = Path(tmp_path_factory.mktemp("no-recordings")) / "llm_recordings.json"
    yield
    llm_recordings.RECORDINGS = saved
