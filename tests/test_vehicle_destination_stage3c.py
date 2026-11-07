import queue
import threading
import time
import unittest
from types import SimpleNamespace

import carla

import carla_server.carla_server as carla_server_module
from carla_server.carla_server import CarlaController
from movement_destination import confirm_destination, destination_result
from scenario_management.capture import ScenarioCapture
from scenario_management.models import ActorCategory, ActorOwnership
from vehicle_control import (
    AUTOPILOT,
    AUTOPILOT_DESTINATION,
    AUTOPILOT_FREE_ROAM,
    MANUAL_CONTROL,
    VEHICLE_STATUS_ARRIVED,
    VEHICLE_STATUS_FAILED,
    VEHICLE_STATUS_NAVIGATING,
    VEHICLE_STATUS_STUCK,
    initial_vehicle_control_state,
)


class FakeWaypoint:
    def __init__(self, x, y=0.0, z=0.0):
        self.transform = SimpleNamespace(location=carla.Location(x=x, y=y, z=z))


class FakeMap:
    def get_waypoint(self, location, **_kwargs):
        return FakeWaypoint(location.x, location.y, location.z)


class FakePlanner:
    def __init__(self, world_map, sampling_resolution):
        self.world_map = world_map
        self.sampling_resolution = sampling_resolution

    def trace_route(self, start, destination):
        return [
            (FakeWaypoint(start.x), "LANEFOLLOW"),
            (FakeWaypoint((start.x + destination.x) / 2.0), "STRAIGHT"),
            (FakeWaypoint(destination.x), "LANEFOLLOW"),
        ]


class FakeVehicle:
    def __init__(self):
        self.id = 31
        self.type_id = "vehicle.test"
        self.is_alive = True
        self.location = carla.Location(x=0.0, y=0.0, z=0.0)
        self.velocity = carla.Vector3D(x=0.0, y=0.0, z=0.0)
        self.autopilot_calls = []
        self.controls = []

    def get_location(self):
        return self.location

    def get_velocity(self):
        return self.velocity

    def set_autopilot(self, enabled, port):
        self.autopilot_calls.append((enabled, port))

    def apply_control(self, control):
        self.controls.append(control)


class FakeTrafficManager:
    def __init__(self):
        self.paths = []
        self.sync_modes = []

    def set_synchronous_mode(self, enabled):
        self.sync_modes.append(enabled)

    def set_path(self, actor, path, empty_buffer=True):
        self.paths.append((actor.id, list(path), empty_buffer))


class FakeWorld:
    def __init__(self):
        self.id = 101
        self.map = FakeMap()
        self.settings = SimpleNamespace(synchronous_mode=False)

    def get_map(self):
        return self.map

    def get_settings(self):
        return self.settings


class VehicleDestinationStage3CTests(unittest.TestCase):
    def setUp(self):
        self.previous_planner = carla_server_module.GlobalRoutePlanner
        carla_server_module.GlobalRoutePlanner = FakePlanner
        self.vehicle = FakeVehicle()
        self.world = FakeWorld()
        self.traffic_manager = FakeTrafficManager()
        state = initial_vehicle_control_state()
        state["world_id"] = self.world.id
        destination = destination_result(
            self.vehicle.id,
            "vehicle",
            self.world.id,
            {"x": 20.0, "y": 0.0, "z": 0.0},
            "valid",
            "Road destination is valid.",
        )
        state["destination"] = confirm_destination(destination)

        self.controller = object.__new__(CarlaController)
        self.controller.database = SimpleNamespace(
            lock=threading.Lock(),
            actors_db=SimpleNamespace(actors={self.vehicle.id: self.vehicle}),
            vehicle_control_states={self.vehicle.id: state},
            movement_event_queue=queue.Queue(),
            world=self.world,
        )
        self.controller.dashboard_monitor = SimpleNamespace(
            record_error=lambda _error: None
        )
        self.controller.client = SimpleNamespace(
            get_trafficmanager=lambda _port: self.traffic_manager
        )
        self.controller.traffic_manager = None
        self.controller.vehicle_navigation_runtime = {}
        self.controller.world_transition_event = threading.Event()
        self.controller.movement_preview_session_id = "session"
        self.controller.movement_preview_actor_id = self.vehicle.id
        self.controller.movement_preview_world_id = self.world.id
        self.controller.movement_preview_camera = None
        self.controller.movement_preview_publisher = None
        self.controller.movement_control_queue = queue.Queue(maxsize=1)
        self.controller.manual_control_command = None
        self.controller.manual_control_received_at = None
        self.controller.manual_control_next_apply = 0.0
        self.controller.manual_control_input_fresh = False

    def tearDown(self):
        carla_server_module.GlobalRoutePlanner = self.previous_planner

    def set_mode(self, mode):
        self.controller._set_vehicle_control_mode(self.world, {
            "session_id": "session",
            "actor_id": self.vehicle.id,
            "mode": mode,
        })

    def test_route_is_converted_to_locations_and_sent_to_traffic_manager(self):
        self.set_mode(AUTOPILOT_DESTINATION)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(AUTOPILOT_DESTINATION, state["mode"])
        self.assertEqual(VEHICLE_STATUS_NAVIGATING, state["navigation_status"])
        self.assertEqual(3, state["route"]["waypoint_count"])
        path = self.traffic_manager.paths[-1][1]
        self.assertTrue(all(isinstance(point, carla.Location) for point in path))
        self.assertEqual([(True, 8000)], self.vehicle.autopilot_calls)

    def test_missing_global_route_planner_does_not_enable_autopilot(self):
        carla_server_module.GlobalRoutePlanner = None

        self.set_mode(AUTOPILOT_DESTINATION)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_FAILED, state["navigation_status"])
        self.assertFalse(state["autopilot_enabled"])
        self.assertEqual([], self.vehicle.autopilot_calls)

    def test_empty_route_does_not_enable_autopilot(self):
        class EmptyPlanner(FakePlanner):
            def trace_route(self, start, destination):
                return []

        carla_server_module.GlobalRoutePlanner = EmptyPlanner

        self.set_mode(AUTOPILOT_DESTINATION)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_FAILED, state["navigation_status"])
        self.assertEqual([], self.vehicle.autopilot_calls)

    def test_destination_to_manual_cancels_route_and_disables_autopilot(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        self.set_mode(MANUAL_CONTROL)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(MANUAL_CONTROL, state["mode"])
        self.assertNotIn(self.vehicle.id, self.controller.vehicle_navigation_runtime)
        self.assertIn((False, 8000), self.vehicle.autopilot_calls)
        self.assertEqual([], self.traffic_manager.paths[-1][1])

    def test_destination_to_free_roam_replaces_custom_path(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        self.set_mode(AUTOPILOT_FREE_ROAM)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(AUTOPILOT_FREE_ROAM, state["mode"])
        self.assertTrue(state["autopilot_enabled"])
        self.assertEqual([], self.traffic_manager.paths[-1][1])

    def test_arrival_disables_autopilot_and_stops_vehicle(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        self.vehicle.location = carla.Location(x=20.0, y=0.0, z=0.0)
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        runtime["next_check"] = 0.0
        snapshot = SimpleNamespace(frame=1)

        self.controller._service_vehicle_navigation(self.world, snapshot)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_ARRIVED, state["navigation_status"])
        self.assertFalse(state["autopilot_enabled"])
        self.assertEqual((False, 8000), self.vehicle.autopilot_calls[-1])
        self.assertEqual(1.0, self.vehicle.controls[-1].brake)

    def test_no_progress_marks_vehicle_stuck_after_long_threshold(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        runtime["next_check"] = 0.0
        runtime["last_progress_time"] = (
            time.monotonic()
            - self.controller.VEHICLE_DESTINATION_STUCK_SECONDS
            - 1.0
        )

        self.controller._service_vehicle_navigation(
            self.world,
            SimpleNamespace(frame=4),
        )

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_STUCK, state["navigation_status"])
        self.assertFalse(state["autopilot_enabled"])
        self.assertNotIn(self.vehicle.id, self.controller.vehicle_navigation_runtime)

    def test_runtime_from_previous_world_is_discarded(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        new_world = FakeWorld()
        new_world.id = 202

        self.controller._service_vehicle_navigation(
            new_world,
            SimpleNamespace(frame=2),
        )

        self.assertNotIn(self.vehicle.id, self.controller.vehicle_navigation_runtime)

    def test_world_transition_pauses_navigation_service(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        self.vehicle.location = carla.Location(x=20.0, y=0.0, z=0.0)
        control_count = len(self.vehicle.controls)
        self.controller.world_transition_event.set()

        self.controller._service_vehicle_navigation(
            self.world,
            SimpleNamespace(frame=3),
        )

        self.assertEqual(control_count, len(self.vehicle.controls))
        self.assertIn(self.vehicle.id, self.controller.vehicle_navigation_runtime)

    def test_actor_release_cancels_destination_runtime(self):
        self.set_mode(AUTOPILOT_DESTINATION)

        self.controller._release_vehicle_control(
            self.vehicle.id,
            disable_autopilot=True,
        )

        self.assertNotIn(self.vehicle.id, self.controller.vehicle_navigation_runtime)
        self.assertEqual((False, 8000), self.vehicle.autopilot_calls[-1])

    def test_closing_preview_keeps_destination_navigation_active(self):
        self.set_mode(AUTOPILOT_DESTINATION)

        self.controller.cleanup_movement_preview("window_closed")

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(AUTOPILOT_DESTINATION, state["mode"])
        self.assertEqual(VEHICLE_STATUS_NAVIGATING, state["navigation_status"])
        self.assertIn(self.vehicle.id, self.controller.vehicle_navigation_runtime)

    def test_scenario_capture_does_not_persist_destination_navigation(self):
        self.set_mode(AUTOPILOT_DESTINATION)

        control = ScenarioCapture._control_data(
            ActorCategory.VEHICLE,
            ActorOwnership.MANAGED,
            self.vehicle.id,
            self.controller.database.vehicle_control_states,
            {},
        )

        self.assertFalse(control["autopilot_enabled"])
        self.assertNotEqual(AUTOPILOT_DESTINATION, control["mode"])

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        state["mode"] = AUTOPILOT_FREE_ROAM
        control = ScenarioCapture._control_data(
            ActorCategory.VEHICLE,
            ActorOwnership.MANAGED,
            self.vehicle.id,
            self.controller.database.vehicle_control_states,
            {},
        )
        self.assertEqual(AUTOPILOT, control["mode"])


if __name__ == "__main__":
    unittest.main()
