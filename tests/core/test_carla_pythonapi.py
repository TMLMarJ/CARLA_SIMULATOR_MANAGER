import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.carla_pythonapi import (
    CARLA_PYTHONAPI_ENV,
    CARLA_ROOT_ENV,
    load_global_route_planner,
)


class CarlaPythonApiTests(unittest.TestCase):
    def setUp(self):
        self.original_environment = {
            CARLA_PYTHONAPI_ENV: os.environ.get(CARLA_PYTHONAPI_ENV),
            CARLA_ROOT_ENV: os.environ.get(CARLA_ROOT_ENV),
        }
        self.original_sys_path = list(sys.path)
        self.original_agent_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "agents" or name.startswith("agents.")
        }

    def tearDown(self):
        for name, value in self.original_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        sys.path[:] = self.original_sys_path
        for name in list(sys.modules):
            if name == "agents" or name.startswith("agents."):
                sys.modules.pop(name, None)
        sys.modules.update(self.original_agent_modules)

    @staticmethod
    def _remove_loaded_agents():
        for name in list(sys.modules):
            if name == "agents" or name.startswith("agents."):
                sys.modules.pop(name, None)

    def test_imports_official_planner_from_configured_pythonapi(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            carla_root = Path(temp_directory)
            package = carla_root / "PythonAPI" / "carla" / "agents" / "navigation"
            package.mkdir(parents=True)
            (package.parent / "__init__.py").write_text("", encoding="utf-8")
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "global_route_planner.py").write_text(
                "class GlobalRoutePlanner:\n    configured = True\n",
                encoding="utf-8",
            )
            os.environ[CARLA_PYTHONAPI_ENV] = str(carla_root)
            os.environ.pop(CARLA_ROOT_ENV, None)
            self._remove_loaded_agents()

            planner, error = load_global_route_planner()

            self.assertIsNone(error)
            self.assertTrue(planner.configured)
            self.assertIn(
                str(carla_root / "PythonAPI" / "carla"),
                sys.path,
            )

    def test_missing_configuration_returns_actionable_error(self):
        os.environ.pop(CARLA_PYTHONAPI_ENV, None)
        os.environ.pop(CARLA_ROOT_ENV, None)
        self._remove_loaded_agents()

        with patch(
            "core.carla_pythonapi.importlib.import_module",
            side_effect=ImportError("agents unavailable"),
        ):
            planner, error = load_global_route_planner()

        self.assertIsNone(planner)
        self.assertIn(CARLA_PYTHONAPI_ENV, error)
        self.assertIn("PythonAPI/carla", error)

    def test_invalid_configuration_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            os.environ[CARLA_PYTHONAPI_ENV] = temp_directory
            os.environ.pop(CARLA_ROOT_ENV, None)

            planner, error = load_global_route_planner()

        self.assertIsNone(planner)
        self.assertIn("global_route_planner.py was not found", error)

    def test_configured_path_rejects_already_loaded_foreign_agents_module(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            package = root / "PythonAPI" / "carla" / "agents" / "navigation"
            package.mkdir(parents=True)
            (package / "global_route_planner.py").write_text(
                "class GlobalRoutePlanner:\n    pass\n",
                encoding="utf-8",
            )
            os.environ[CARLA_PYTHONAPI_ENV] = str(root)
            os.environ.pop(CARLA_ROOT_ENV, None)
            foreign_module = types.ModuleType(
                "agents.navigation.global_route_planner"
            )
            foreign_module.__file__ = str(root / "foreign" / "planner.py")
            foreign_module.GlobalRoutePlanner = object
            sys.modules[
                "agents.navigation.global_route_planner"
            ] = foreign_module

            planner, error = load_global_route_planner()

        self.assertIsNone(planner)
        self.assertIn("already loaded outside", error)


if __name__ == "__main__":
    unittest.main()
