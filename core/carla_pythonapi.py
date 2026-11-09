"""Resolve optional modules shipped with the local CARLA PythonAPI."""

# Canonical implementation; the legacy path is a compatibility shim.

import importlib
import os
import sys
from pathlib import Path


CARLA_PYTHONAPI_ENV = "CARLA_PYTHONAPI_PATH"
CARLA_ROOT_ENV = "CARLA_ROOT"


def _configured_root():
    pythonapi_path = os.environ.get(CARLA_PYTHONAPI_ENV)
    if pythonapi_path:
        return Path(pythonapi_path).expanduser(), CARLA_PYTHONAPI_ENV
    carla_root = os.environ.get(CARLA_ROOT_ENV)
    if carla_root:
        return Path(carla_root).expanduser(), CARLA_ROOT_ENV
    return None, None


def resolve_carla_pythonapi_path():
    """Return the explicitly configured directory containing `agents`."""
    configured, source = _configured_root()
    if configured is None:
        return None, (
            "CARLA GlobalRoutePlanner is unavailable. Set "
            f"{CARLA_PYTHONAPI_ENV} to the local CARLA 0.9.16 "
            "PythonAPI/carla directory (or set CARLA_ROOT to the CARLA "
            "installation directory), then restart the application."
        )

    candidates = [
        configured,
        configured / "PythonAPI" / "carla",
        configured / "carla",
    ]
    checked = []
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            candidate = candidate.absolute()
        if candidate in checked:
            continue
        checked.append(candidate)
        planner_file = (
            candidate
            / "agents"
            / "navigation"
            / "global_route_planner.py"
        )
        if planner_file.is_file():
            return candidate, None

    return None, (
        f"CARLA GlobalRoutePlanner is unavailable. {source} points to "
        f"'{configured}', but the official CARLA 0.9.16 file "
        "agents/navigation/global_route_planner.py was not found. Configure "
        f"{CARLA_PYTHONAPI_ENV} with the PythonAPI/carla directory and "
        "restart the application."
    )


def load_global_route_planner():
    """Configure the local PythonAPI path, then import CARLA's planner."""
    configured, _source = _configured_root()
    pythonapi_path, error = resolve_carla_pythonapi_path()
    if pythonapi_path is None:
        if configured is None:
            try:
                module = importlib.import_module(
                    "agents.navigation.global_route_planner"
                )
                return getattr(module, "GlobalRoutePlanner"), None
            except (AttributeError, ImportError, OSError):
                pass
        return None, error

    path_text = str(pythonapi_path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)
    importlib.invalidate_caches()
    try:
        module = importlib.import_module(
            "agents.navigation.global_route_planner"
        )
        planner = getattr(module, "GlobalRoutePlanner")
    except (AttributeError, ImportError, OSError) as import_error:
        return None, (
            "CARLA GlobalRoutePlanner could not be imported from "
            f"'{pythonapi_path}': {import_error}. Verify that the configured "
            "PythonAPI belongs to CARLA 0.9.16 and restart the application."
        )
    module_file = getattr(module, "__file__", None)
    try:
        Path(module_file).resolve().relative_to(pythonapi_path)
    except (AttributeError, OSError, TypeError, ValueError):
        return None, (
            "A different 'agents.navigation.global_route_planner' module is "
            "already loaded outside the configured CARLA PythonAPI directory. "
            "Remove the conflicting package or configure "
            f"{CARLA_PYTHONAPI_ENV} before starting the application, then "
            "restart it."
        )
    return planner, None
