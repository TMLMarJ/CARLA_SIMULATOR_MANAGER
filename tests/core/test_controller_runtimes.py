import unittest
import queue
import threading
from types import SimpleNamespace
from unittest import mock

from core.controller import CarlaController
from movement.runtime import MovementRuntime
from scenarios.runtime import ScenarioRuntime
from simulation.runtime import SimulationRuntime


class ControllerRuntimeDelegationTests(unittest.TestCase):
    def test_controller_composes_all_runtime_implementations(self):
        self.assertTrue(issubclass(CarlaController, MovementRuntime))
        self.assertTrue(issubclass(CarlaController, SimulationRuntime))
        self.assertTrue(issubclass(CarlaController, ScenarioRuntime))

    def test_runtime_methods_have_one_canonical_definition(self):
        expected_owners = {
            "_service_vehicle_navigation": MovementRuntime,
            "_service_simulation": SimulationRuntime,
            "_handle_scenario_runtime_command": ScenarioRuntime,
        }

        for method_name, runtime_class in expected_owners.items():
            with self.subTest(method=method_name):
                self.assertNotIn(method_name, CarlaController.__dict__)
                self.assertIs(
                    getattr(CarlaController, method_name),
                    getattr(runtime_class, method_name),
                )

    def test_scenario_commands_are_routed_by_the_scenario_runtime(self):
        self.assertEqual(
            {
                "sync_world",
                "take_actor_ownership",
                "prepare_load_scenario",
                "execute_load_scenario",
                "save_scenario",
                "prepare_clear_world",
                "execute_clear_world",
            },
            set(ScenarioRuntime.SCENARIO_COMMANDS),
        )

    def test_scenario_sync_handler_runs_inline_on_controller_instance(self):
        controller = CarlaController.__new__(CarlaController)
        controller._perform_world_sync = mock.Mock(return_value={})
        world = object()
        blueprint_library = object()

        result = controller._handle_scenario_runtime_command(
            "sync_world",
            "manual",
            world,
            blueprint_library,
        )

        self.assertEqual((world, blueprint_library), result)
        controller._perform_world_sync.assert_called_once_with(
            world,
            reason="manual",
        )

    def test_save_runtime_passes_actual_server_version(self):
        controller = CarlaController.__new__(CarlaController)
        controller.shutdown_event = mock.Mock()
        controller.shutdown_event.is_set.return_value = False
        controller.client = mock.Mock()
        controller.client.get_server_version.return_value = "0.9.15"
        controller.scenario_manager = mock.Mock()
        controller.scenario_manager.save.return_value = {"success": True}
        controller.database = SimpleNamespace(
            scenario_event_queue=queue.Queue(),
        )
        controller.scenario_operation_lock = threading.Lock()
        controller.scenario_operation_pending = True
        controller.scenario_operation_kind = "save"
        controller._collect_internal_actor_ids = mock.Mock(return_value=set())
        controller._perform_world_sync = mock.Mock()
        world = object()
        blueprint_library = object()

        result = controller._handle_scenario_runtime_command(
            "save_scenario",
            {"name": "Test", "path": "test.json", "format": "json"},
            world,
            blueprint_library,
        )

        self.assertEqual((world, blueprint_library), result)
        self.assertEqual(
            "0.9.15",
            controller.scenario_manager.save.call_args.kwargs[
                "server_version"
            ],
        )


if __name__ == "__main__":
    unittest.main()
