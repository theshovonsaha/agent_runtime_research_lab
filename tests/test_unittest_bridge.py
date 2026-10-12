"""Expose pytest-style function tests to unittest discovery without duplication."""
from __future__ import annotations

import importlib.util
import inspect
import itertools
import sys
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


TEST_DIR = Path(__file__).resolve().parent


def _module_for(path: Path) -> ModuleType:
    module = sys.modules.get(path.stem)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load test module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def _parameter_sets(function):
    names: tuple[str, ...] = ()
    variants: list[dict[str, Any]] = [{}]
    for marker in getattr(function, "pytestmark", ()):
        if marker.name != "parametrize":
            continue
        raw_names, raw_values = marker.args
        parameter_names = (
            tuple(name.strip() for name in raw_names.split(","))
            if isinstance(raw_names, str) else tuple(raw_names)
        )
        expanded = []
        for value in raw_values:
            values = getattr(value, "values", value)
            if len(parameter_names) == 1 and not isinstance(values, tuple):
                values = (values,)
            current = dict(zip(parameter_names, values))
            expanded.extend({**variant, **current} for variant in variants)
        names = (*names, *parameter_names)
        variants = expanded
    return variants or ([{}] if not names else [])


def _invoke(function, parameters):
    arguments = dict(parameters)
    fixtures = set(inspect.signature(function).parameters) - arguments.keys()
    monkeypatch = None
    if fixtures == {"monkeypatch"}:
        monkeypatch = pytest.MonkeyPatch()
        arguments["monkeypatch"] = monkeypatch
    elif fixtures:
        raise RuntimeError(
            f"unittest bridge does not support fixtures {sorted(fixtures)} "
            f"for {function.__module__}.{function.__name__}"
        )
    try:
        function(**arguments)
    finally:
        if monkeypatch is not None:
            monkeypatch.undo()


def load_tests(loader, standard_tests, pattern):
    """Add every free pytest test (including parametrized cases) to unittest."""
    suite = unittest.TestSuite([standard_tests])
    for path in sorted(TEST_DIR.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        module = _module_for(path)
        for name, function in sorted(vars(module).items()):
            if (
                not name.startswith("test_")
                or not inspect.isfunction(function)
                or function.__module__ != module.__name__
            ):
                continue
            parameter_sets = _parameter_sets(function)
            methods = {}
            for index, parameters in enumerate(parameter_sets):
                def run(self, test_function=function, test_parameters=parameters):
                    _invoke(test_function, test_parameters)

                run.__name__ = f"test_case_{index:03d}"
                methods[run.__name__] = run
            wrapper = type(
                f"UnittestBridge_{path.stem}_{name}",
                (unittest.TestCase,),
                methods,
            )
            suite.addTests(loader.loadTestsFromTestCase(wrapper))
    return suite
