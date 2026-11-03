import queue
import unittest
from types import SimpleNamespace
from unittest import mock

from carla_manager import Manager


def load_plan(**overrides):
    plan = {
        "token": "load-token",
        "metadata": {"name": "GUI scenario"},
        "target_map": "Town04",
        "current_map": "Town03",
        "map_change_required": True,
        "actor_count": 2,
        "actors": [
            {"category": "vehicle"},
            {"category": "sensor"},
        ],
        "status": "warning",
        "critical_problems": [],
        "warnings": ["Optional control state is unknown."],
        "unverified_items": ["Blueprint requires target-map verification."],
    }
    plan.update(overrides)
    return plan


class ScenarioLoadGuiTests(unittest.TestCase):
    def make_manager(self):
        manager = object.__new__(Manager)
        manager.connected = True
        manager.disconnecting = False
        manager.closing = False
        manager.scenario_loading = True
        manager.scenario_load_plan = None
        manager.carla = mock.MagicMock()
        manager.carla.execute_load_scenario.return_value = True
        manager.movement_control = mock.MagicMock()
        manager.spectator_preview = mock.MagicMock()
        manager.database = SimpleNamespace(scenario_event_queue=queue.Queue())
        scenario_frame = mock.MagicMock()
        simulation_frame = mock.MagicMock()
        simulation_frame.scenario_frame = scenario_frame
        manager.main_view = SimpleNamespace(
            simulation_control_frame=simulation_frame,
            main_view=SimpleNamespace(
                connection_frame=mock.MagicMock(),
                dashboard_frame=mock.MagicMock(),
            ),
        )
        manager._refresh_after_scenario_load = mock.MagicMock()
        return manager, scenario_frame, simulation_frame

    @mock.patch(
        "carla_manager.filedialog.askopenfilename",
        return_value="C:/scenarios/town.yaml",
    )
    def test_file_selection_starts_preflight(self, choose_file):
        manager, frame, simulation_frame = self.make_manager()
        manager.scenario_loading = False
        manager._simulation_control_available = mock.MagicMock(
            return_value=True
        )
        manager.carla.prepare_load_scenario.return_value = True

        manager.load_scenario()

        manager.carla.prepare_load_scenario.assert_called_once_with(
            "C:/scenarios/town.yaml"
        )
        self.assertTrue(manager.scenario_loading)
        simulation_frame.set_external_busy.assert_called_once_with(True)
        frame.set_load_pending.assert_called_once_with()
        self.assertIn("*.yml", choose_file.call_args.kwargs["filetypes"][0][1])

    @mock.patch("carla_manager.messagebox.askyesno", return_value=True)
    def test_confirmation_executes_received_token(self, _confirm):
        manager, frame, _simulation_frame = self.make_manager()
        plan = load_plan()

        manager._handle_load_preflight(plan)

        manager.carla.execute_load_scenario.assert_called_once_with(
            "load-token",
            confirmed=True,
        )
        frame.set_load_pending.assert_called_once_with("Loading scenario...")

    @mock.patch("carla_manager.messagebox.askyesno", return_value=False)
    def test_cancellation_does_not_call_execute(self, _confirm):
        manager, frame, simulation_frame = self.make_manager()

        manager._handle_load_preflight(load_plan())

        manager.carla.execute_load_scenario.assert_not_called()
        self.assertFalse(manager.scenario_loading)
        simulation_frame.set_external_busy.assert_called_once_with(False)
        frame.show_load_cancelled.assert_called_once_with()

    @mock.patch("carla_manager.messagebox.showerror")
    @mock.patch("carla_manager.messagebox.askyesno")
    def test_critical_preflight_blocks_execution(self, confirm, show_error):
        manager, frame, _simulation_frame = self.make_manager()

        manager._handle_load_preflight(load_plan(
            critical_problems=["Required blueprint is missing."],
            status="blocked",
        ))

        confirm.assert_not_called()
        manager.carla.execute_load_scenario.assert_not_called()
        frame.show_load_error.assert_called_once()
        show_error.assert_called_once()

    @mock.patch("carla_manager.messagebox.showerror")
    def test_preflight_error_unlocks_gui(self, _show_error):
        manager, frame, simulation_frame = self.make_manager()
        manager.database.scenario_event_queue.put((
            "scenario_load_preflight_error",
            {"message": "Invalid JSON."},
        ))

        manager._check_scenario_events()

        self.assertFalse(manager.scenario_loading)
        simulation_frame.set_external_busy.assert_called_once_with(False)
        frame.show_load_error.assert_called_once_with("Invalid JSON.")

    @mock.patch("carla_manager.messagebox.showwarning")
    def test_partial_success_uses_warning_and_unlocks(self, show_warning):
        manager, frame, simulation_frame = self.make_manager()
        report = {
            "name": "GUI scenario",
            "status": "partial_success",
            "planned": 2,
            "restored": 1,
            "skipped": 0,
            "failed": 1,
            "map_changed": True,
            "warnings": ["One actor was not restored."],
            "errors": [{
                "stage": "actor_spawn",
                "scenario_id": "actor-2",
                "message": "Spawn failed.",
            }],
            "clear_report": {"status": "success"},
        }
        manager.database.scenario_event_queue.put((
            "scenario_load_completed",
            report,
        ))

        manager._check_scenario_events()

        self.assertFalse(manager.scenario_loading)
        simulation_frame.set_external_busy.assert_called_once_with(False)
        frame.show_load_report.assert_called_once_with(report)
        manager._refresh_after_scenario_load.assert_called_once_with(report)
        show_warning.assert_called_once()
        self.assertIn(
            "previous world may already have been replaced",
            show_warning.call_args.args[1],
        )


if __name__ == "__main__":
    unittest.main()
