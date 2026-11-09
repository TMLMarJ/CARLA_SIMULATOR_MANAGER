import unittest
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


if __name__ == "__main__":
    unittest.main()
