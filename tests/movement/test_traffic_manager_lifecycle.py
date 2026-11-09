import threading
import unittest
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.controller import CarlaController
from core.state_store import DataStorage
from models.scenarios import ActorCategory
from movement.vehicle import AUTOPILOT_DESTINATION, AUTOPILOT_FREE_ROAM
from movement import runtime as movement_runtime_module


class FakeTrafficManager:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.sync_modes = []
        self.shutdown_calls = 0

    def set_path(self, actor, path, empty_buffer):
        self.events.append(("path", actor.id, list(path), empty_buffer))

    def set_synchronous_mode(self, enabled):
        self.sync_modes.append(bool(enabled))

    def shut_down(self):
        self.shutdown_calls += 1


class FakeVehicle:
    def __init__(self, events):
        self.id = 7
        self.is_alive = True
        self.type_id = "vehicle.test"
        self.events = events

    def set_autopilot(self, enabled, port):
        self.events.append(("autopilot", bool(enabled), int(port)))


def lifecycle_controller(mode=AUTOPILOT_DESTINATION):
    controller = object.__new__(CarlaController)
    events = []
    vehicle = FakeVehicle(events)
    controller.database = SimpleNamespace(
        lock=threading.Lock(),
        actors_db=SimpleNamespace(actors={vehicle.id: vehicle}),
        vehicle_control_states={
            vehicle.id: {
                "mode": mode,
                "control_mode": mode,
                "distance_to_destination": None,
            },
        },
    )
    controller.dashboard_monitor = SimpleNamespace(record_error=Mock())
    controller.traffic_manager = FakeTrafficManager(events)
    controller.traffic_manager_world_id = 1
    controller.vehicle_navigation_runtime = {
        vehicle.id: {"world_id": 1},
    }
    controller.vehicle_navigation_submission_counts = {vehicle.id: 1}
    controller.vehicle_navigation_submission_sequence = 1
    controller.movement_navigation_map_cache = {"world_id": 1}
    controller._close_manual_session = Mock()
    return controller, vehicle, events


class TrafficManagerLifecycleTests(unittest.TestCase):
    def setUp(self):
        movement_runtime_module._TM_DEBUG_PROCESS_PORTS.clear()
        movement_runtime_module._TM_EXCLUSIVE_OWNERS.clear()

    def test_shared_tm_never_shuts_down(self):
        controller = CarlaController(DataStorage())
        traffic_manager = FakeTrafficManager()
        controller.traffic_manager = traffic_manager

        result = controller._shutdown_exclusive_traffic_manager(
            "test",
            strict=True,
        )

        self.assertFalse(result)
        self.assertEqual(0, traffic_manager.shutdown_calls)
        self.assertIs(traffic_manager, controller.traffic_manager)

    def test_exclusive_requires_non_default_reserved_port(self):
        with patch.dict(os.environ, {
            "CARLA_MANAGER_TM_EXCLUSIVE": "1",
            "CARLA_MANAGER_TM_PORT": "8000",
        }, clear=True):
            controller = CarlaController(DataStorage())

        self.assertIn(
            "different from the shared default 8000",
            controller.traffic_manager_configuration_error,
        )

    def test_exclusive_refuses_port_with_unknown_owner(self):
        with patch.dict(os.environ, {
            "CARLA_MANAGER_TM_EXCLUSIVE": "1",
            "CARLA_MANAGER_TM_PORT": "8051",
        }, clear=True):
            controller = CarlaController(DataStorage())
        controller.client = SimpleNamespace(get_trafficmanager=Mock())
        world = SimpleNamespace(
            id=15,
            get_settings=lambda: SimpleNamespace(synchronous_mode=False),
        )

        with (
            patch.object(
                movement_runtime_module.MovementRuntime,
                "_exclusive_tm_port_is_available",
                return_value=False,
            ),
            self.assertRaisesRegex(RuntimeError, "Ownership cannot be proven"),
        ):
            controller._ensure_traffic_manager(world)

        controller.client.get_trafficmanager.assert_not_called()

    def test_exclusive_tm_is_shutdown_once_and_recreated_after_world_change(self):
        with patch.dict(os.environ, {
            "CARLA_MANAGER_TM_EXCLUSIVE": "1",
            "CARLA_MANAGER_TM_PORT": "8051",
        }, clear=True):
            controller = CarlaController(DataStorage())
        first_tm = FakeTrafficManager()
        second_tm = FakeTrafficManager()
        controller.client = SimpleNamespace(
            get_trafficmanager=Mock(side_effect=[first_tm, second_tm]),
        )
        town15 = SimpleNamespace(
            id=15,
            get_settings=lambda: SimpleNamespace(synchronous_mode=False),
        )
        town10 = SimpleNamespace(
            id=10,
            get_settings=lambda: SimpleNamespace(synchronous_mode=False),
        )

        with patch.object(
            movement_runtime_module.MovementRuntime,
            "_exclusive_tm_port_is_available",
            return_value=True,
        ):
            self.assertIs(first_tm, controller._ensure_traffic_manager(town15))
            self.assertTrue(controller._shutdown_exclusive_traffic_manager(
                "world_changed",
            ))
            self.assertFalse(controller._shutdown_exclusive_traffic_manager(
                "world_changed",
            ))
            self.assertIs(second_tm, controller._ensure_traffic_manager(town10))

        self.assertEqual(1, first_tm.shutdown_calls)
        self.assertEqual(0, second_tm.shutdown_calls)
        self.assertEqual([8051, 8051], [
            call.args[0]
            for call in controller.client.get_trafficmanager.call_args_list
        ])

    def test_exclusive_never_shuts_down_proxy_with_unknown_ownership(self):
        with patch.dict(os.environ, {
            "CARLA_MANAGER_TM_EXCLUSIVE": "1",
            "CARLA_MANAGER_TM_PORT": "8051",
        }, clear=True):
            controller = CarlaController(DataStorage())
        traffic_manager = FakeTrafficManager()
        controller.traffic_manager = traffic_manager

        with self.assertRaisesRegex(RuntimeError, "unknown ownership"):
            controller._shutdown_exclusive_traffic_manager("test")

        self.assertEqual(0, traffic_manager.shutdown_calls)

    def test_exclusive_disconnect_allows_new_controller_to_claim_port(self):
        environment = {
            "CARLA_MANAGER_TM_EXCLUSIVE": "1",
            "CARLA_MANAGER_TM_PORT": "8051",
        }
        with patch.dict(os.environ, environment, clear=True):
            first = CarlaController(DataStorage())
            second = CarlaController(DataStorage())
        first_tm = FakeTrafficManager()
        second_tm = FakeTrafficManager()
        first.client = SimpleNamespace(
            get_trafficmanager=Mock(return_value=first_tm),
        )
        second.client = SimpleNamespace(
            get_trafficmanager=Mock(return_value=second_tm),
        )
        world = SimpleNamespace(
            id=15,
            get_settings=lambda: SimpleNamespace(synchronous_mode=False),
        )

        with patch.object(
            movement_runtime_module.MovementRuntime,
            "_exclusive_tm_port_is_available",
            return_value=True,
        ):
            first._ensure_traffic_manager(world)
            first._shutdown_exclusive_traffic_manager("disconnect")
            result = second._ensure_traffic_manager(world)

        self.assertIs(second_tm, result)
        self.assertEqual(1, first_tm.shutdown_calls)
        self.assertTrue(second.traffic_manager_exclusive_owned)

    def test_custom_path_is_cleared_before_autopilot_unregisters(self):
        controller, vehicle, events = lifecycle_controller()

        controller._release_vehicle_control(
            vehicle.id,
            disable_autopilot=True,
        )

        self.assertEqual("path", events[0][0])
        self.assertEqual(("autopilot", False, 8000), events[1])
        self.assertNotIn(vehicle.id, controller.vehicle_navigation_runtime)

    def test_shutdown_clears_free_roam_and_destination_runtime(self):
        for mode in (AUTOPILOT_FREE_ROAM, AUTOPILOT_DESTINATION):
            with self.subTest(mode=mode):
                controller, _vehicle, _events = lifecycle_controller(mode)

                controller._shutdown_vehicle_controls(True)

                self.assertEqual({}, controller.vehicle_navigation_runtime)
                self.assertEqual(
                    {}, controller.vehicle_navigation_submission_counts
                )
                self.assertEqual(0, controller.vehicle_navigation_submission_sequence)
                self.assertIsNone(controller.movement_navigation_map_cache)

    def test_world_transition_unregisters_without_submitting_empty_path(self):
        controller, _vehicle, events = lifecycle_controller()

        controller._shutdown_vehicle_controls(
            True,
            clear_paths=False,
        )

        self.assertEqual([("autopilot", False, 8000)], events)

    def test_world_transition_cleanup_is_idempotent(self):
        controller, _vehicle, events = lifecycle_controller()

        controller._shutdown_vehicle_controls(True, clear_paths=False)
        controller._shutdown_vehicle_controls(True, clear_paths=False)

        self.assertEqual([("autopilot", False, 8000)], events)

    def test_tm_port_and_sync_mode_match_current_world(self):
        controller = object.__new__(CarlaController)
        traffic_manager = FakeTrafficManager()
        requested_ports = []
        controller.client = SimpleNamespace(
            get_trafficmanager=lambda port: (
                requested_ports.append(port) or traffic_manager
            )
        )
        controller.traffic_manager = None
        controller.traffic_manager_world_id = None
        world = SimpleNamespace(
            id=15,
            get_settings=lambda: SimpleNamespace(synchronous_mode=True),
        )

        result = controller._ensure_traffic_manager(world)

        self.assertIs(traffic_manager, result)
        self.assertEqual([controller.TRAFFIC_MANAGER_PORT], requested_ports)
        self.assertEqual([True], traffic_manager.sync_modes)
        self.assertEqual(15, controller.traffic_manager_world_id)

        async_world = SimpleNamespace(
            id=10,
            get_settings=lambda: SimpleNamespace(synchronous_mode=False),
        )
        controller._update_traffic_manager_sync(async_world)
        self.assertEqual([True, False], traffic_manager.sync_modes)
        self.assertEqual(10, controller.traffic_manager_world_id)

    def test_tm_debug_marks_process_port_reuse_across_controllers(self):
        def controller_with(tm, world_id):
            controller = object.__new__(CarlaController)
            controller.database = SimpleNamespace(
                lock=threading.Lock(),
                world_generation=world_id,
                actual_simulation_settings={
                    "synchronous_mode": False,
                    "fixed_delta_seconds": None,
                },
            )
            controller.client = SimpleNamespace(
                get_trafficmanager=Mock(return_value=tm),
            )
            controller.traffic_manager = None
            controller.traffic_manager_world_id = None
            controller.traffic_manager_last_sync = None
            controller.vehicle_navigation_runtime = {}
            controller.movement_preview_session_id = None
            return controller

        first_tm = FakeTrafficManager()
        second_tm = FakeTrafficManager()
        first = controller_with(first_tm, 15)
        second = controller_with(second_tm, 10)
        first_world = SimpleNamespace(
            id=15,
            get_settings=lambda: SimpleNamespace(synchronous_mode=False),
            get_map=lambda: SimpleNamespace(name="Town15"),
        )
        second_world = SimpleNamespace(
            id=10,
            get_settings=lambda: SimpleNamespace(synchronous_mode=False),
            get_map=lambda: SimpleNamespace(name="Town10"),
        )

        with (
            patch.dict(os.environ, {"CARLA_MANAGER_TM_DEBUG": "1"}),
            patch("builtins.print") as output,
        ):
            first._ensure_traffic_manager(first_world)
            first._invalidate_traffic_manager_reference()
            second._ensure_traffic_manager(second_world)

        messages = [call.args[0] for call in output.call_args_list]
        lifecycle_events = [
            message.split("event=", 1)[1].split(" ", 1)[0].strip("'")
            for message in messages
            if "event=" in message
        ]
        self.assertEqual([
            "tm_initialize_begin",
            "tm_proxy_acquired",
            "tm_sync_applied",
            "tm_initialize_complete",
            "tm_local_reference_cleanup",
            "tm_initialize_begin",
            "tm_proxy_acquired",
            "tm_sync_applied",
            "tm_initialize_complete",
        ], lifecycle_events)
        self.assertTrue(any(
            "event='tm_initialize_complete'" in message
            and "process_port_seen_before=True" in message
            for message in messages
        ))
        self.assertTrue(any(
            "tm_ownership='unknown_not_exposed_by_carla_api'" in message
            for message in messages
        ))

    def test_tm_debug_is_silent_by_default(self):
        controller = object.__new__(CarlaController)
        controller.database = SimpleNamespace(
            lock=threading.Lock(),
            world_generation=1,
            actual_simulation_settings=None,
        )
        controller.client = None
        controller.traffic_manager = None
        controller.traffic_manager_world_id = None

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("builtins.print") as output,
        ):
            controller._tm_debug_event("connect")

        output.assert_not_called()

    def test_destroy_actor_releases_control_before_removal(self):
        controller = object.__new__(CarlaController)
        calls = []
        controller.movement_preview_actor_id = None
        controller._destroy_walker_ai = Mock()
        controller._release_vehicle_control = Mock(
            side_effect=lambda *_args, **_kwargs: calls.append("release")
        )
        controller.actor_service = SimpleNamespace(
            remove=lambda *_args: calls.append("remove")
        )
        controller.sensor_service = SimpleNamespace(remove_for_parent=Mock())

        controller._dispatch_remove_actor(None, 7)

        self.assertEqual(["release", "remove"], calls)
        controller._release_vehicle_control.assert_called_once_with(
            7,
            disable_autopilot=True,
        )

    def test_clear_world_releases_only_planned_vehicle(self):
        controller = object.__new__(CarlaController)
        controller.cleanup_movement_preview = Mock()
        controller.cleanup_sensor_previews = Mock()
        controller.cleanup_preview = Mock()
        controller.stop_sensor_preview_actor = Mock()
        controller._destroy_walker_ai = Mock()
        controller._release_vehicle_control = Mock()
        plan = SimpleNamespace(items=[SimpleNamespace(
            category=ActorCategory.VEHICLE,
            runtime_id=7,
        )])

        controller._prepare_clear_lifecycle(plan)

        controller._release_vehicle_control.assert_called_once_with(
            7,
            disable_autopilot=True,
        )

    def test_new_controller_does_not_reuse_tm_proxy_after_reconnect(self):
        first = CarlaController(DataStorage())
        first.traffic_manager = object()
        second = CarlaController(DataStorage())

        self.assertIsNone(second.traffic_manager)
        self.assertIsNone(second.traffic_manager_world_id)

    def test_scenario_map_change_installs_new_world_identity(self):
        controller = object.__new__(CarlaController)
        new_library = SimpleNamespace(filter=lambda _pattern: [])
        new_map = SimpleNamespace(
            name="/Game/Carla/Maps/Town10HD_Opt",
            get_spawn_points=lambda: ["spawn"],
        )
        new_world = SimpleNamespace(
            id=10,
            get_map=lambda: new_map,
            get_blueprint_library=lambda: new_library,
            get_weather=lambda: object(),
            get_settings=lambda: SimpleNamespace(synchronous_mode=False),
        )
        controller.database = SimpleNamespace(
            lock=threading.Lock(),
            scenario_event_queue=SimpleNamespace(put=Mock()),
            sensor_event_queue=SimpleNamespace(put=Mock()),
            actors_db=SimpleNamespace(clear=Mock()),
            sensors_db=SimpleNamespace(clear=Mock()),
            vehicle_control_states={7: {}},
            walker_ai_states={21: {}},
            spawned_actor_id=7,
            world=SimpleNamespace(id=15),
            world_id=15,
            world_generation=4,
        )
        controller.traffic_manager = object()
        controller.traffic_manager_world_id = 15
        controller._shutdown_vehicle_controls = Mock()
        controller._shutdown_walker_ai = Mock()
        controller.cleanup_movement_preview = Mock()
        controller.cleanup_sensor_previews = Mock()
        controller.cleanup_preview = Mock()
        controller._publish_simulation_control_state = Mock()
        controller._capture_map_infrastructure = Mock()
        controller.simulation_manager = SimpleNamespace(
            stop_local_ticks=Mock(),
            get_current_settings=Mock(return_value={
                "synchronous_mode": False,
            }),
            settings_changed=Mock(),
        )
        controller.scenario_manager = SimpleNamespace(loader=SimpleNamespace(
            map_key=lambda _target: "Town10HD_Opt",
            maps_equal=lambda _target, _actual: True,
        ))
        controller.client = SimpleNamespace(
            set_timeout=Mock(),
            load_world=Mock(return_value=new_world),
            get_available_maps=Mock(return_value=["Town10HD_Opt"]),
            get_server_version=Mock(return_value="test"),
            get_trafficmanager=Mock(side_effect=AssertionError(
                "TM must be initialized lazily in the new world"
            )),
        )
        controller.dashboard_monitor = SimpleNamespace(reset_world=Mock())
        controller.sensor_status_errors = {99: "old"}

        with patch(
            "scenarios.runtime.parse_weather_to_dict",
            return_value={"cloudiness": 0.0},
        ):
            world, library, _maps, _version = (
                controller._replace_world_for_load("Town10HD_Opt")
            )

        self.assertIs(new_world, world)
        self.assertIs(new_library, library)
        self.assertIs(new_world, controller.database.world)
        self.assertEqual(10, controller.database.world_id)
        self.assertEqual(5, controller.database.world_generation)
        self.assertIsNone(controller.traffic_manager)
        self.assertIsNone(controller.traffic_manager_world_id)
        controller.client.get_trafficmanager.assert_not_called()
        self.assertEqual({}, controller.database.vehicle_control_states)
        self.assertEqual({}, controller.database.walker_ai_states)


if __name__ == "__main__":
    unittest.main()
