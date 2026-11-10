import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.controller import CarlaController
from gui.windows.movement_control import MovementControlWindow
from movement.session import MovementControlSessionManager
from movement.vehicle import AUTOPILOT_DESTINATION


class FakeVariable:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def settings_window():
    window = object.__new__(MovementControlWindow)
    window.closed = False
    window.control_pending = False
    window.destination_executor = "basic_agent"
    window.basic_agent_settings_locked = False
    window.basic_agent_settings_frame = Mock()
    window.basic_agent_maximum_speed_spinbox = Mock()
    window.basic_agent_adaptive_speed_checkbutton = Mock()
    window.basic_agent_maximum_speed_var = FakeVariable("40")
    window.basic_agent_adaptive_speed_var = FakeVariable(True)
    window.control_state_var = FakeVariable("")
    window.control_mode_var = FakeVariable("")
    window.on_mode_changed = Mock()
    return window


class BasicAgentGuiSettingsTests(unittest.TestCase):
    def test_gui_defaults_to_basic_agent_executor_without_environment(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CARLA_MANAGER_DESTINATION_EXECUTOR", None)
            executor = MovementControlWindow._destination_executor({})

        self.assertEqual("basic_agent", executor)

    def test_gui_defaults_are_40_kmh_with_adaptation_enabled(self):
        with patch.dict(os.environ, {
            "CARLA_MANAGER_BASIC_AGENT_TARGET_SPEED": "",
            "CARLA_MANAGER_BASIC_AGENT_ADAPTIVE_SPEED": "",
        }):
            settings = MovementControlWindow._initial_basic_agent_settings({})

        self.assertEqual({
            "maximum_speed": 40.0,
            "adaptive_speed": True,
        }, settings)

    def test_environment_values_are_gui_defaults(self):
        with patch.dict(os.environ, {
            "CARLA_MANAGER_BASIC_AGENT_TARGET_SPEED": "55",
            "CARLA_MANAGER_BASIC_AGENT_ADAPTIVE_SPEED": "0",
        }):
            settings = MovementControlWindow._initial_basic_agent_settings({})

        self.assertEqual({
            "maximum_speed": 55.0,
            "adaptive_speed": False,
        }, settings)

    def test_start_destination_passes_both_gui_settings(self):
        window = settings_window()
        window.basic_agent_maximum_speed_var.set("60")
        window.basic_agent_adaptive_speed_var.set(False)

        window._start_vehicle_destination()

        window.on_mode_changed.assert_called_once_with(
            AUTOPILOT_DESTINATION,
            {
                "maximum_speed": 60.0,
                "adaptive_speed": False,
            },
        )

    def test_invalid_gui_speed_does_not_start_destination(self):
        window = settings_window()
        window.basic_agent_maximum_speed_var.set("95")

        window._start_vehicle_destination()

        window.on_mode_changed.assert_not_called()
        self.assertIn("between 10 and 90", window.control_state_var.get())

    def test_runtime_lock_disables_and_terminal_state_unlocks_controls(self):
        window = settings_window()
        active_state = {
            "destination_executor": "basic_agent",
            "basic_agent_settings": {
                "maximum_speed": 50.0,
                "adaptive_speed": True,
            },
            "basic_agent_settings_locked": True,
        }

        window._apply_basic_agent_settings_state(active_state)

        self.assertTrue(window.basic_agent_settings_locked)
        window.basic_agent_maximum_speed_spinbox.configure.assert_called_with(
            state="disabled"
        )
        window.basic_agent_adaptive_speed_checkbutton.configure.assert_called_with(
            state="disabled"
        )

        terminal_state = dict(active_state)
        terminal_state["basic_agent_settings_locked"] = False
        window._apply_basic_agent_settings_state(terminal_state)

        self.assertFalse(window.basic_agent_settings_locked)
        window.basic_agent_maximum_speed_spinbox.configure.assert_called_with(
            state="normal"
        )
        window.basic_agent_adaptive_speed_checkbutton.configure.assert_called_with(
            state="normal"
        )

    def test_reopened_window_uses_persisted_settings_and_runtime_lock(self):
        control_state = {
            "destination_executor": "basic_agent",
            "basic_agent_settings": {
                "maximum_speed": 45.0,
                "adaptive_speed": False,
            },
            "basic_agent_settings_locked": True,
        }
        settings = MovementControlWindow._initial_basic_agent_settings(
            control_state
        )
        window = settings_window()

        window._apply_basic_agent_settings_state(control_state)

        self.assertEqual(45.0, settings["maximum_speed"])
        self.assertFalse(settings["adaptive_speed"])
        self.assertEqual("45", window.basic_agent_maximum_speed_var.get())
        self.assertFalse(window.basic_agent_adaptive_speed_var.get())
        self.assertTrue(window.basic_agent_settings_locked)

    def test_controls_are_hidden_for_non_basic_executor(self):
        window = settings_window()

        window._apply_basic_agent_settings_state({
            "destination_executor": "route",
            "basic_agent_settings_locked": False,
        })

        window.basic_agent_settings_frame.grid_remove.assert_called_once_with()
        window.basic_agent_maximum_speed_spinbox.configure.assert_called_with(
            state="disabled"
        )

    def test_session_forwards_settings_to_controller_queue_api(self):
        controller = Mock()
        session = MovementControlSessionManager.__new__(
            MovementControlSessionManager
        )
        session.manager = SimpleNamespace(carla=controller)
        session.closing = False
        session.actor_type = "vehicle.test"
        session.window = Mock()
        session.session_id = "session"
        session.actor_id = 7
        settings = {
            "maximum_speed": 50.0,
            "adaptive_speed": True,
        }

        session.change_control_mode(
            AUTOPILOT_DESTINATION,
            settings,
        )

        controller.set_vehicle_control_mode.assert_called_once_with(
            "session",
            7,
            AUTOPILOT_DESTINATION,
            basic_agent_settings=settings,
        )

    def test_controller_copies_settings_into_worker_command(self):
        controller = object.__new__(CarlaController)
        controller._scenario_mutation_blocked = Mock(return_value=False)
        controller._managed_operation_allowed = Mock(return_value=True)
        controller._queue_movement_command = Mock(return_value=True)
        settings = {
            "maximum_speed": 70.0,
            "adaptive_speed": False,
        }

        self.assertTrue(controller.set_vehicle_control_mode(
            "session",
            8,
            AUTOPILOT_DESTINATION,
            basic_agent_settings=settings,
        ))

        controller._queue_movement_command.assert_called_once_with(
            "set_vehicle_control_mode",
            {
                "session_id": "session",
                "actor_id": 8,
                "mode": AUTOPILOT_DESTINATION,
                "basic_agent_settings": settings,
            },
        )


if __name__ == "__main__":
    unittest.main()
