from __future__ import annotations

from pathlib import Path

from vibe.core.utils.io import read_safe

_TEST_APP_PATH = Path(__file__).with_name("test_app.html")


def load_test_app_html() -> str:
    return read_safe(_TEST_APP_PATH, raise_on_error=True).text
