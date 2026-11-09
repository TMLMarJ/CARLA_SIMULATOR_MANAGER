import queue
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import carla

import core.controller as carla_server_module
from core.controller import CarlaController
from models.scenarios import ActorCategory, ActorOwnership
from movement.destination import confirm_destination, destination_result
from movement.vehicle import (
    AUTOPILOT,
    AUTOPILOT_DESTINATION,
    AUTOPILOT_FREE_ROAM,
    MANUAL_CONTROL,
    NO_CONTROL,
    VEHICLE_STATUS_ARRIVED,
    VEHICLE_STATUS_CANCELLED,
    VEHICLE_STATUS_FAILED,
    VEHICLE_STATUS_NAVIGATING,
    VEHICLE_STATUS_STUCK,
    initial_vehicle_control_state,
)
from scenarios.capture import ScenarioCapture


class FakeWaypoint:
    def __init__(self, x, y=0.0, z=0.0, road_id=1, lane_id=1,
                 section_id=0, is_junction=False):
        self.road_id = road_id
        self.lane_id = lane_id
        self.section_id = section_id
        self.s = x
        self.is_junction = is_junction
        self.transform = SimpleNamespace(
            location=carla.Location(x=x, y=y, z=z),
            rotation=SimpleNamespace(yaw=0.0 if lane_id > 0 else 180.0),
        )


class FakeMap:
    def __init__(self):
        self.xodr_calls = []

    def get_waypoint(self, location, **_kwargs):
        near_junction = 19.0 <= location.x <= 21.0
        return FakeWaypoint(
            location.x,
            location.y,
            location.z,
            road_id=2 if near_junction else 1,
            lane_id=1 if location.y >= 0.0 else -1,
            is_junction=near_junction,
        )

    def get_waypoint_xodr(self, road_id, lane_id, distance):
        self.xodr_calls.append((road_id, lane_id, distance))
        return FakeWaypoint(
            distance,
            1.0 if lane_id > 0 else -1.0,
            road_id=road_id,
            lane_id=lane_id,
        )


class FakePlanner:
    def __init__(self, world_map, sampling_resolution):
        self.world_map = world_map
        self.sampling_resolution = sampling_resolution

    def trace_route(self, start, destination):
        return [
            (self.world_map.get_waypoint(start), "LANEFOLLOW"),
            (self.world_map.get_waypoint(carla.Location(
                x=(start.x + destination.x) / 2.0,
                y=(start.y + destination.y) / 2.0,
                z=(start.z + destination.z) / 2.0,
            )), "STRAIGHT"),
            (self.world_map.get_waypoint(destination), "LANEFOLLOW"),
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
        self.events = None

    def get_location(self):
        return self.location

    def get_velocity(self):
        return self.velocity

    def set_autopilot(self, enabled, port):
        self.autopilot_calls.append((enabled, port))
        if self.events is not None:
            self.events.append(("set_autopilot", enabled, port))

    def apply_control(self, control):
        self.controls.append(control)


class FakeTrafficManager:
    def __init__(self):
        self.paths = []
        self.sync_modes = []
        self.actions = []
        self.events = None

    def set_synchronous_mode(self, enabled):
        self.sync_modes.append(enabled)

    def set_path(self, actor, path, empty_buffer=True):
        self.paths.append((actor.id, list(path), empty_buffer))
        if self.events is not None:
            self.events.append(("set_path", actor.id, len(path)))

    def get_all_actions(self, _actor):
        return list(self.actions)


class FakeWorld:
    def __init__(self):
        self.id = 101
        self.map = FakeMap()
        self.settings = SimpleNamespace(synchronous_mode=False)

    def get_map(self):
        return self.map

    def get_settings(self):
        return self.settings


class VehicleDestinationNavigationTests(unittest.TestCase):
    def setUp(self):
        self.previous_planner = carla_server_module.GlobalRoutePlanner
        carla_server_module.GlobalRoutePlanner = FakePlanner
        self.vehicle = FakeVehicle()
        self.world = FakeWorld()
        self.traffic_manager = FakeTrafficManager()
        self.execution_events = []
        self.vehicle.events = self.execution_events
        self.traffic_manager.events = self.execution_events
        state = initial_vehicle_control_state()
        state["world_id"] = self.world.id
        destination = destination_result(
            self.vehicle.id,
            "vehicle",
            self.world.id,
            {"x": 30.0, "y": 0.0, "z": 0.0},
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
        self.controller.vehicle_navigation_submission_counts = {}
        self.controller.vehicle_navigation_submission_sequence = 0
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
        self.assertEqual(3, len(state["route"]["waypoints"]))
        self.assertEqual(1, state["target_waypoint"]["road_id"])
        self.assertEqual(1, state["target_waypoint"]["lane_id"])
        self.assertFalse(state["target_waypoint"]["is_junction"])
        path = self.traffic_manager.paths[-1][1]
        self.assertTrue(all(isinstance(point, carla.Location) for point in path))
        self.assertEqual([(True, 8000)], self.vehicle.autopilot_calls)

    def test_vehicle_is_registered_before_custom_path_is_submitted(self):
        self.set_mode(AUTOPILOT_DESTINATION)

        self.assertEqual(
            ["set_autopilot", "set_path"],
            [event[0] for event in self.execution_events[:2]],
        )

    def test_navigation_service_does_not_resubmit_custom_path(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        submissions = len(self.traffic_manager.paths)
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        runtime["next_check"] = 0.0

        self.controller._service_vehicle_navigation(
            self.world,
            SimpleNamespace(frame=55),
        )

        self.assertEqual(submissions, len(self.traffic_manager.paths))

    def test_execution_path_mutation_cannot_change_planned_route(self):
        original_set_path = self.traffic_manager.set_path

        def mutating_set_path(actor, path, empty_buffer=True):
            original_set_path(actor, path, empty_buffer)
            path.clear()

        self.traffic_manager.set_path = mutating_set_path

        self.set_mode(AUTOPILOT_DESTINATION)

        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(3, len(runtime["route"]))
        self.assertEqual(3, state["route"]["waypoint_count"])
        self.assertEqual(3, len(state["route"]["waypoints"]))

    def test_debug_snapshot_distinguishes_plan_remaining_and_trajectory(self):
        with (
            patch.dict(os.environ, {"CARLA_MANAGER_NAV_DEBUG": "1"}),
            patch("builtins.print"),
        ):
            self.set_mode(AUTOPILOT_DESTINATION)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        diagnostics = state["navigation_debug"]
        self.assertEqual(3, diagnostics["planned_route"]["waypoint_count"])
        self.assertEqual(3, diagnostics["remaining_route"]["waypoint_count"])
        self.assertEqual(1, len(diagnostics["actual_trajectory"]))
        self.assertEqual(
            "TrafficManager.set_path(carla.Location[])",
            diagnostics["executor"],
        )

    def test_destination_near_junction_keeps_terminal_waypoint_identity(self):
        destination = destination_result(
            self.vehicle.id,
            "vehicle",
            self.world.id,
            {"x": 20.0, "y": 0.0, "z": 0.0},
            "valid",
            "Road destination is valid.",
        )
        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        state["destination"] = confirm_destination(destination)

        self.set_mode(AUTOPILOT_DESTINATION)

        self.assertEqual(2, state["target_waypoint"]["road_id"])
        self.assertEqual(1, state["target_waypoint"]["lane_id"])
        self.assertTrue(state["target_waypoint"]["is_junction"])
        self.assertEqual(
            state["target_waypoint"]["road_id"],
            state["route"]["waypoints"][-1]["road_id"],
        )

    def test_planner_uses_selected_opposite_lane_waypoint_not_ambiguous_xyz(self):
        destination = destination_result(
            self.vehicle.id,
            "vehicle",
            self.world.id,
            {"x": 30.0, "y": 0.0, "z": 0.0},
            "valid",
            "Road destination is valid.",
        )
        destination["target_waypoint"] = {
            "road_id": 1,
            "section_id": 0,
            "lane_id": -1,
            "s": 30.0,
            "location": {"x": 30.0, "y": -1.0, "z": 0.0},
            "yaw": 180.0,
        }
        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        state["destination"] = confirm_destination(destination)

        self.set_mode(AUTOPILOT_DESTINATION)

        self.assertEqual([(1, -1, 30.0)], self.world.map.xodr_calls)
        self.assertEqual(-1, state["target_waypoint"]["lane_id"])
        self.assertEqual(-1, state["route"]["waypoints"][-1]["lane_id"])
        self.assertEqual(-1.0, self.traffic_manager.paths[-1][1][-1].y)

    def test_planner_route_order_is_preserved_for_traffic_manager(self):
        self.set_mode(AUTOPILOT_DESTINATION)

        path = self.traffic_manager.paths[-1][1]
        route = self.controller.database.vehicle_control_states[
            self.vehicle.id
        ]["route"]["waypoints"]
        self.assertEqual([0.0, 15.0, 30.0], [point.x for point in path])
        self.assertEqual(
            [0.0, 15.0, 30.0],
            [point["location"]["x"] for point in route],
        )

    def test_route_ending_on_different_lane_is_rejected(self):
        class WrongTerminalLanePlanner(FakePlanner):
            def trace_route(self, start, destination):
                return [
                    (self.world_map.get_waypoint(start), "LANEFOLLOW"),
                    (FakeWaypoint(
                        destination.x,
                        -1.0,
                        lane_id=-1,
                    ), "LANEFOLLOW"),
                ]

        carla_server_module.GlobalRoutePlanner = WrongTerminalLanePlanner

        self.set_mode(AUTOPILOT_DESTINATION)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_FAILED, state["navigation_status"])
        self.assertEqual([], self.vehicle.autopilot_calls)
        self.assertEqual([], self.traffic_manager.paths)

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

    def test_stopping_destination_clears_path_and_brakes_vehicle(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        self.set_mode(NO_CONTROL)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(NO_CONTROL, state["mode"])
        self.assertEqual(VEHICLE_STATUS_CANCELLED, state["navigation_status"])
        self.assertFalse(state["autopilot_enabled"])
        self.assertNotIn(self.vehicle.id, self.controller.vehicle_navigation_runtime)
        self.assertEqual([], self.traffic_manager.paths[-1][1])
        self.assertEqual(1.0, self.vehicle.controls[-1].brake)

    def test_destination_to_free_roam_replaces_custom_path(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        self.set_mode(AUTOPILOT_FREE_ROAM)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(AUTOPILOT_FREE_ROAM, state["mode"])
        self.assertTrue(state["autopilot_enabled"])
        self.assertEqual([], self.traffic_manager.paths[-1][1])

    def test_arrival_disables_autopilot_and_stops_vehicle(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        self.vehicle.location = carla.Location(x=30.0, y=0.0, z=0.0)
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        runtime["next_check"] = 0.0
        snapshot = SimpleNamespace(frame=1)

        self.controller._service_vehicle_navigation(self.world, snapshot)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_ARRIVED, state["navigation_status"])
        self.assertFalse(state["autopilot_enabled"])
        self.assertEqual((False, 8000), self.vehicle.autopilot_calls[-1])
        self.assertEqual(1.0, self.vehicle.controls[-1].brake)

    def test_near_destination_on_opposite_lane_is_not_arrival(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        self.vehicle.location = carla.Location(x=30.0, y=-0.5, z=0.0)
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        runtime["next_check"] = 0.0

        self.controller._service_vehicle_navigation(
            self.world,
            SimpleNamespace(frame=10),
        )

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_NAVIGATING, state["navigation_status"])
        self.assertTrue(state["autopilot_enabled"])
        self.assertFalse(runtime["arrival_braking"])
        self.assertEqual(-1, state["current_waypoint"]["lane_id"])

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

    def test_looping_motion_without_route_progress_is_stopped(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        runtime["next_check"] = 0.0
        runtime["last_progress_time"] = (
            time.monotonic()
            - self.controller.VEHICLE_DESTINATION_STUCK_SECONDS
            - 1.0
        )
        self.vehicle.location = carla.Location(x=0.5, y=0.0, z=0.0)
        self.vehicle.velocity = carla.Vector3D(x=8.0, y=0.0, z=0.0)

        self.controller._service_vehicle_navigation(
            self.world,
            SimpleNamespace(frame=11),
        )

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_STUCK, state["navigation_status"])
        self.assertFalse(state["autopilot_enabled"])

    def test_runtime_from_previous_world_is_discarded(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        new_world = FakeWorld()
        new_world.id = 202

        self.controller._service_vehicle_navigation(
            new_world,
            SimpleNamespace(frame=2),
        )

        self.assertNotIn(self.vehicle.id, self.controller.vehicle_navigation_runtime)
        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_CANCELLED, state["navigation_status"])
        self.assertIsNone(state["route"])

    def test_world_transition_pauses_navigation_service(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        self.vehicle.location = carla.Location(x=30.0, y=0.0, z=0.0)
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
