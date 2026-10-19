"""Import every module under `src/app`, so a module that crashes on import fails CI.

pytest only loads the modules its tests touch. Without this, a broken model or helper that
nothing imports yet (e.g. a bad column default) would pass CI unnoticed.
"""

import importlib
import pkgutil

import pytest

import app


def _app_modules() -> list[str]:
    return sorted(
        module.name
        for module in pkgutil.walk_packages(app.__path__, prefix="app.")
        # Test modules are collected by pytest itself.
        if ".tests" not in module.name
    )


@pytest.mark.parametrize("module_name", _app_modules())
def test_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)
