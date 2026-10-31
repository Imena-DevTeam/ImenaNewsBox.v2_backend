"""`.env.example` lists every setting and nothing else, so it can't drift from the code.

The checks only ever read variable names, never values, so no secret from `.env` can end up
in a test report.
"""

import re
from pathlib import Path

import pytest

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"
# Read by docker-compose.yml, not by the app.
COMPOSE_ONLY = {
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "DB_PORT",
    "DB_TEST_PORT",
    "API_PORT",
}
# An active `NAME=value` line; commented-out examples don't count.
_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)


def _names(path: Path) -> list[str]:
    return _ASSIGNMENT.findall(path.read_text(encoding="utf-8", errors="replace"))


def test_env_example_lists_every_setting_once() -> None:
    names = _names(EXAMPLE)
    settings = {name.upper() for name in Settings.model_fields}

    assert sorted(name for name in set(names) if names.count(name) > 1) == []
    assert sorted(settings - set(names)) == [], "settings missing from .env.example"
    assert sorted(set(names) - settings - COMPOSE_ONLY) == [], "unknown variables"


def test_env_example_lists_every_compose_variable() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    # `$${...}` is escaped: the container's shell expands it, not compose.
    used = set(re.findall(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)", compose))

    assert sorted(used - set(_names(EXAMPLE))) == []
    assert sorted(COMPOSE_ONLY - used) == [], "COMPOSE_ONLY names a variable compose dropped"


def test_your_env_matches_env_example() -> None:
    env = ROOT / ".env"
    if not env.exists():
        pytest.skip("no .env here (as in CI)")
    names, expected = _names(env), _names(EXAMPLE)

    assert sorted(set(expected) - set(names)) == [], "missing from .env"
    assert sorted(set(names) - set(expected)) == [], "in .env but not in .env.example"
    assert names == expected, "same variables, but in a different order"
