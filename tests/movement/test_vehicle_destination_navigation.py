import math
import os
import queue
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


class FakeBasicAgent:
    instances = []

    def __init__(self, vehicle, **kwargs):
        self.vehicle = vehicle
        self.kwargs = kwargs
        self.plan = None
        self.run_step_calls = 0
        self.done_result = False
        self.run_error = None
        self.control = carla.VehicleControl(throttle=0.25, steer=0.1)
        self.target_speeds = []
        self.instances.append(self)

    def set_global_plan(self, plan):
        self.plan = plan

    def set_target_speed(self, speed):
        self.target_speeds.append(float(speed))

    def run_step(self):
        self.run_step_calls += 1
        if self.run_error is not None:
            raise self.run_error
        return self.control

    def done(self):
        return self.done_result


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

    def get_transform(self):
        return SimpleNamespace(
            location=self.location,
            rotation=SimpleNamespace(yaw=17.5),
        )

    def set_autopilot(self, enabled, port):
        self.autopilot_calls.append((enabled, port))
        if self.events is not None:
            self.events.append(("set_autopilot", enabled, port))

    def apply_control(self, control):
        self.controls.append(control)


class FakeTrafficManager:
    def __init__(self):
        self.paths = []
        self.routes = []
        self.sync_modes = []
        self.actions = []
        self.next_action = None
        self.auto_lane_changes = []
        self.random_left_lane_changes = []
        self.random_right_lane_changes = []
        self.events = None

    def set_synchronous_mode(self, enabled):
        self.sync_modes.append(enabled)

    def set_path(self, actor, path, empty_buffer=True):
        self.paths.append((actor.id, list(path), empty_buffer))
        if self.events is not None:
            self.events.append(("set_path", actor.id, len(path)))

    def set_route(self, actor, route, empty_buffer=True):
        self.routes.append((actor.id, list(route), empty_buffer))
        if self.events is not None:
            self.events.append(("set_route", actor.id, len(route)))

    def auto_lane_change(self, actor, enabled):
        self.auto_lane_changes.append((actor.id, bool(enabled)))
        if self.events is not None:
            self.events.append(("auto_lane_change", actor.id, bool(enabled)))

    def random_left_lanechange_percentage(self, actor, percentage):
        self.random_left_lane_changes.append((actor.id, percentage))
        if self.events is not None:
            self.events.append((
                "random_left_lanechange_percentage",
                actor.id,
                percentage,
            ))

    def random_right_lanechange_percentage(self, actor, percentage):
        self.random_right_lane_changes.append((actor.id, percentage))
        if self.events is not None:
            self.events.append((
                "random_right_lanechange_percentage",
                actor.id,
                percentage,
            ))

    def get_all_actions(self, _actor):
        return list(self.actions)

    def get_next_action(self, _actor):
        return self.next_action


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
        FakeBasicAgent.instances.clear()
        self.executor_environment = patch.dict(
            os.environ,
            {"CARLA_MANAGER_DESTINATION_EXECUTOR": "path"},
        )
        self.executor_environment.start()
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
        self.controller.simulation_manager = SimpleNamespace(
            synchronous_mode=False,
            last_frame=None,
        )
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
        self.executor_environment.stop()

    def set_mode(self, mode, basic_agent_settings=None):
        data = {
            "session_id": "session",
            "actor_id": self.vehicle.id,
            "mode": mode,
        }
        if basic_agent_settings is not None:
            data["basic_agent_settings"] = basic_agent_settings
        self.controller._set_vehicle_control_mode(self.world, data)

    def set_basic_agent_mode(self, settings=None):
        with (
            patch.dict(
                os.environ,
                {"CARLA_MANAGER_DESTINATION_EXECUTOR": "basic_agent"},
            ),
            patch(
                "movement.runtime.load_basic_agent",
                return_value=(FakeBasicAgent, None),
            ),
        ):
            self.set_mode(
                AUTOPILOT_DESTINATION,
                basic_agent_settings=settings,
            )
        return (
            FakeBasicAgent.instances[-1]
            if FakeBasicAgent.instances else None
        )

    def test_destination_executor_defaults_to_basic_agent(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CARLA_MANAGER_DESTINATION_EXECUTOR", None)
            self.assertEqual(
                "basic_agent",
                self.controller._destination_executor(),
            )

    def test_destination_executor_accepts_explicit_legacy_values(self):
        for executor in ("path", "route"):
            with self.subTest(executor=executor), patch.dict(
                os.environ,
                {"CARLA_MANAGER_DESTINATION_EXECUTOR": executor},
            ):
                self.assertEqual(
                    executor,
                    self.controller._destination_executor(),
                )

    def test_destination_executor_rejects_invalid_explicit_value(self):
        with patch.dict(
            os.environ,
            {"CARLA_MANAGER_DESTINATION_EXECUTOR": "unsupported"},
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "CARLA_MANAGER_DESTINATION_EXECUTOR",
            ):
                self.controller._destination_executor()

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
        self.assertEqual([], self.traffic_manager.routes)
        self.assertEqual([(True, 8000)], self.vehicle.autopilot_calls)

    def test_vehicle_is_registered_before_custom_path_is_submitted(self):
        self.set_mode(AUTOPILOT_DESTINATION)

        self.assertEqual(
            [
                "set_autopilot",
                "auto_lane_change",
                "random_left_lanechange_percentage",
                "random_right_lanechange_percentage",
                "set_path",
            ],
            [event[0] for event in self.execution_events[:5]],
        )

    def test_destination_applies_per_actor_lane_change_profile(self):
        other_vehicle = FakeVehicle()
        other_vehicle.id = 99

        self.set_mode(AUTOPILOT_DESTINATION)

        self.assertEqual(
            [(self.vehicle.id, False)],
            self.traffic_manager.auto_lane_changes,
        )
        self.assertEqual(
            [(self.vehicle.id, 0)],
            self.traffic_manager.random_left_lane_changes,
        )
        self.assertEqual(
            [(self.vehicle.id, 0)],
            self.traffic_manager.random_right_lane_changes,
        )
        configured_actor_ids = {
            actor_id
            for calls in (
                self.traffic_manager.auto_lane_changes,
                self.traffic_manager.random_left_lane_changes,
                self.traffic_manager.random_right_lane_changes,
            )
            for actor_id, _value in calls
        }
        self.assertEqual({self.vehicle.id}, configured_actor_ids)
        self.assertNotIn(other_vehicle.id, configured_actor_ids)

        actor_id, path, empty_buffer = self.traffic_manager.paths[-1]
        self.assertEqual(self.vehicle.id, actor_id)
        self.assertTrue(empty_buffer)
        self.assertEqual([0.0, 15.0, 30.0], [point.x for point in path])

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
            "path",
            diagnostics["executor"],
        )
        self.assertEqual(
            "TrafficManager.set_path(carla.Location[])",
            diagnostics["traffic_manager_executor"],
        )
        self.assertEqual(0, diagnostics["instruction_count"])
        self.assertEqual([], diagnostics["instructions"])

    def test_debug_records_only_first_confirmed_route_divergence(self):
        current_action_waypoint = FakeWaypoint(6.0, road_id=8, lane_id=-1)
        next_action_waypoint = FakeWaypoint(12.0, road_id=9, lane_id=-1)
        self.traffic_manager.next_action = (
            "RoadOption.RIGHT",
            current_action_waypoint,
        )
        self.traffic_manager.actions = [
            ("RoadOption.LEFT", next_action_waypoint),
        ]

        with (
            patch.dict(os.environ, {"CARLA_MANAGER_NAV_DEBUG": "1"}),
            patch("builtins.print") as debug_print,
        ):
            self.set_mode(AUTOPILOT_DESTINATION)
            self.vehicle.location = carla.Location(x=5.0, y=10.0, z=0.0)
            runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
            for frame in range(1, 5):
                runtime["next_check"] = 0.0
                self.controller._service_vehicle_navigation(
                    self.world,
                    SimpleNamespace(frame=frame),
                )

        diagnostics = self.controller.database.vehicle_control_states[
            self.vehicle.id
        ]["navigation_debug"]
        report = diagnostics["first_divergence"]
        self.assertEqual(AUTOPILOT_DESTINATION, report["mode"])
        self.assertEqual({"x": 5.0, "y": 10.0, "z": 0.0}, report["position"])
        self.assertEqual(17.5, report["yaw"])
        self.assertEqual(1, report["current_waypoint"]["road_id"])
        self.assertEqual(1, report["current_waypoint"]["lane_id"])
        self.assertGreaterEqual(report["distance_to_planned_route"], 5.0)
        self.assertEqual(3, report["confirmation_samples"])
        self.assertEqual(0, report["progress_index"])
        self.assertEqual(1, report["target"]["road_id"])
        self.assertEqual(
            "LANEFOLLOW",
            report["expected_route_fragment"][0]["road_option"],
        )
        self.assertEqual(
            "RoadOption.LEFT",
            report["traffic_manager_actions"]["current"]["road_option"],
        )
        self.assertEqual(
            "RoadOption.RIGHT",
            report["traffic_manager_actions"]["next"]["road_option"],
        )
        divergence_logs = [
            call
            for call in debug_print.call_args_list
            if call.args and str(call.args[0]).startswith("[NAV DIVERGENCE]")
        ]
        self.assertEqual(1, len(divergence_logs))

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

    def test_raw_planner_route_preserves_waypoints_and_road_options(self):
        self.set_mode(AUTOPILOT_DESTINATION)

        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        raw_plan = runtime["raw_plan"]
        self.assertIsInstance(raw_plan, list)
        self.assertEqual(
            [0.0, 15.0, 30.0],
            [waypoint.transform.location.x for waypoint, _option in raw_plan],
        )
        self.assertEqual(
            ["LANEFOLLOW", "STRAIGHT", "LANEFOLLOW"],
            [option for _waypoint, option in raw_plan],
        )
        self.assertTrue(all(
            isinstance(waypoint, FakeWaypoint)
            for waypoint, _option in raw_plan
        ))
        self.assertFalse(runtime["has_lane_changes"])

    def test_lane_change_classification_uses_raw_road_options(self):
        waypoint = FakeWaypoint(0.0)

        self.assertFalse(self.controller._destination_route_has_lane_changes([
            (waypoint, "LANEFOLLOW"),
            (waypoint, "LEFT"),
            (waypoint, "RIGHT"),
        ]))
        self.assertTrue(self.controller._destination_route_has_lane_changes([
            (waypoint, "LANEFOLLOW"),
            (waypoint, 5),
        ]))
        self.assertTrue(self.controller._destination_route_has_lane_changes([
            (waypoint, "RoadOption.CHANGELANERIGHT"),
        ]))

    def test_raw_plan_is_not_published_to_vehicle_control_state(self):
        self.set_mode(AUTOPILOT_DESTINATION)

        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertIn("raw_plan", runtime)
        self.assertNotIn("raw_plan", state)
        self.assertNotIn("has_lane_changes", state)
        self.assertNotIn("raw_plan", state["route"])
        self.assertNotIn("has_lane_changes", state["route"])
        self.assertTrue(all(
            isinstance(waypoint, dict)
            for waypoint in state["route"]["waypoints"]
        ))

    def test_cancelling_destination_discards_raw_plan(self):
        self.set_mode(AUTOPILOT_DESTINATION)
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        self.assertIn("raw_plan", runtime)

        self.set_mode(NO_CONTROL)

        self.assertNotIn(
            self.vehicle.id,
            self.controller.vehicle_navigation_runtime,
        )

    def test_basic_agent_receives_original_global_plan(self):
        agent = self.set_basic_agent_mode()

        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertIs(agent.plan, runtime["raw_plan"])
        self.assertIs(self.vehicle, agent.vehicle)
        self.assertIs(self.world.map, agent.kwargs["map_inst"])
        self.assertEqual("basic_agent", runtime["executor"])
        self.assertFalse(state["autopilot_enabled"])
        self.assertIsNone(state["traffic_manager_port"])

    def test_basic_agent_uses_default_target_speed_of_40_kmh(self):
        agent = self.set_basic_agent_mode()

        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        self.assertEqual(40.0, agent.kwargs["target_speed"])
        self.assertEqual(40.0, runtime["basic_agent_target_speed"])

    def test_basic_agent_uses_per_destination_gui_settings(self):
        agent = self.set_basic_agent_mode({
            "maximum_speed": 65.0,
            "adaptive_speed": False,
        })

        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(65.0, agent.kwargs["target_speed"])
        self.assertEqual(65.0, runtime["basic_agent_target_speed"])
        self.assertIsNone(runtime["basic_agent_speed_profile"])
        self.assertEqual({
            "maximum_speed": 65.0,
            "adaptive_speed": False,
        }, state["basic_agent_settings"])
        self.assertTrue(state["basic_agent_settings_locked"])

    def test_valid_gui_settings_take_precedence_over_invalid_environment(self):
        with patch.dict(os.environ, {
            "CARLA_MANAGER_BASIC_AGENT_TARGET_SPEED": "invalid",
            "CARLA_MANAGER_BASIC_AGENT_ADAPTIVE_SPEED": "invalid",
        }):
            agent = self.set_basic_agent_mode({
                "maximum_speed": 50.0,
                "adaptive_speed": True,
            })

        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        self.assertEqual(50.0, agent.kwargs["target_speed"])
        self.assertIsNotNone(runtime["basic_agent_speed_profile"])

    def test_basic_agent_rejects_gui_speed_outside_allowed_range(self):
        self.set_basic_agent_mode({
            "maximum_speed": 95.0,
            "adaptive_speed": True,
        })

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertNotIn(
            self.vehicle.id,
            self.controller.vehicle_navigation_runtime,
        )
        self.assertEqual(VEHICLE_STATUS_FAILED, state["navigation_status"])
        self.assertIn("between 10 and 90", state["error"])

    def test_basic_agent_rejects_non_boolean_gui_adaptive_setting(self):
        self.set_basic_agent_mode({
            "maximum_speed": 40.0,
            "adaptive_speed": 1,
        })

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertNotIn(
            self.vehicle.id,
            self.controller.vehicle_navigation_runtime,
        )
        self.assertEqual(VEHICLE_STATUS_FAILED, state["navigation_status"])
        self.assertIn("must be boolean", state["error"])

    def test_two_vehicles_keep_independent_basic_agent_settings(self):
        self.set_basic_agent_mode({
            "maximum_speed": 35.0,
            "adaptive_speed": True,
        })
        first_runtime = self.controller.vehicle_navigation_runtime[
            self.vehicle.id
        ]

        second_vehicle = FakeVehicle()
        second_vehicle.id = 32
        second_state = initial_vehicle_control_state()
        second_state["world_id"] = self.world.id
        second_destination = destination_result(
            second_vehicle.id,
            "vehicle",
            self.world.id,
            {"x": 30.0, "y": 0.0, "z": 0.0},
            "valid",
            "Road destination is valid.",
        )
        second_state["destination"] = confirm_destination(
            second_destination
        )
        self.controller.database.actors_db.actors[second_vehicle.id] = (
            second_vehicle
        )
        self.controller.database.vehicle_control_states[
            second_vehicle.id
        ] = second_state
        self.controller.movement_preview_actor_id = second_vehicle.id
        with (
            patch.dict(
                os.environ,
                {"CARLA_MANAGER_DESTINATION_EXECUTOR": "basic_agent"},
            ),
            patch(
                "movement.runtime.load_basic_agent",
                return_value=(FakeBasicAgent, None),
            ),
        ):
            self.controller._set_vehicle_control_mode(self.world, {
                "session_id": "session",
                "actor_id": second_vehicle.id,
                "mode": AUTOPILOT_DESTINATION,
                "basic_agent_settings": {
                    "maximum_speed": 60.0,
                    "adaptive_speed": False,
                },
            })

        second_runtime = self.controller.vehicle_navigation_runtime[
            second_vehicle.id
        ]
        self.assertEqual(35.0, first_runtime["basic_agent_target_speed"])
        self.assertIsNotNone(first_runtime["basic_agent_speed_profile"])
        self.assertEqual(60.0, second_runtime["basic_agent_target_speed"])
        self.assertIsNone(second_runtime["basic_agent_speed_profile"])

    def test_basic_agent_target_speed_can_be_configured_from_environment(self):
        with (
            patch.dict(
                os.environ,
                {
                    "CARLA_MANAGER_DESTINATION_EXECUTOR": "basic_agent",
                    "CARLA_MANAGER_BASIC_AGENT_TARGET_SPEED": "55.5",
                },
            ),
            patch(
                "movement.runtime.load_basic_agent",
                return_value=(FakeBasicAgent, None),
            ),
        ):
            self.set_mode(AUTOPILOT_DESTINATION)

        agent = FakeBasicAgent.instances[-1]
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        self.assertEqual(55.5, agent.kwargs["target_speed"])
        self.assertEqual(55.5, runtime["basic_agent_target_speed"])

    def test_invalid_basic_agent_target_speed_fails_activation_safely(self):
        with (
            patch.dict(
                os.environ,
                {
                    "CARLA_MANAGER_DESTINATION_EXECUTOR": "basic_agent",
                    "CARLA_MANAGER_BASIC_AGENT_TARGET_SPEED": "not-a-speed",
                },
            ),
            patch(
                "movement.runtime.load_basic_agent",
                return_value=(FakeBasicAgent, None),
            ),
        ):
            self.set_mode(AUTOPILOT_DESTINATION)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual([], FakeBasicAgent.instances)
        self.assertNotIn(
            self.vehicle.id,
            self.controller.vehicle_navigation_runtime,
        )
        self.assertEqual(VEHICLE_STATUS_FAILED, state["navigation_status"])
        self.assertIn(
            "CARLA_MANAGER_BASIC_AGENT_TARGET_SPEED",
            state["error"],
        )

    def test_basic_agent_speed_setting_does_not_affect_path_executor(self):
        with patch.dict(
            os.environ,
            {
                "CARLA_MANAGER_DESTINATION_EXECUTOR": "path",
                "CARLA_MANAGER_BASIC_AGENT_TARGET_SPEED": "not-a-speed",
            },
        ):
            self.set_mode(
                AUTOPILOT_DESTINATION,
                basic_agent_settings={
                    "maximum_speed": 500,
                    "adaptive_speed": "invalid",
                },
            )

        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        self.assertEqual("path", runtime["executor"])
        self.assertIsNone(runtime["basic_agent_target_speed"])
        self.assertEqual(VEHICLE_STATUS_NAVIGATING, (
            self.controller.database.vehicle_control_states[self.vehicle.id]
            ["navigation_status"]
        ))

    def test_adaptive_speed_keeps_cruise_speed_on_straight_route(self):
        route = [
            carla.Location(x=float(x), y=0.0, z=0.0)
            for x in range(0, 62, 2)
        ]

        profile = self.controller._basic_agent_speed_profile(route, 40.0)

        self.assertTrue(profile)
        self.assertTrue(all(speed == 40.0 for speed in profile))

    def test_adaptive_speed_reduces_speed_before_sharp_curve(self):
        straight = [
            carla.Location(x=float(x), y=0.0, z=0.0)
            for x in range(-30, 1, 3)
        ]
        curve = [
            carla.Location(
                x=10.0 * math.cos(math.radians(angle)),
                y=10.0 + 10.0 * math.sin(math.radians(angle)),
                z=0.0,
            )
            for angle in range(-80, 1, 10)
        ]
        exit_straight = [
            carla.Location(x=10.0, y=float(y), z=0.0)
            for y in range(13, 43, 3)
        ]
        route = straight + curve + exit_straight

        profile = self.controller._basic_agent_speed_profile(route, 40.0)

        self.assertLess(profile[len(straight) - 3], 40.0)
        self.assertLessEqual(
            min(profile[len(straight):len(straight) + len(curve)]),
            20.0,
        )

    def test_adaptive_speed_stays_low_through_roundabout_arc(self):
        route = [
            carla.Location(
                x=12.0 * math.cos(math.radians(angle)),
                y=12.0 * math.sin(math.radians(angle)),
                z=0.0,
            )
            for angle in range(0, 271, 10)
        ]

        profile = self.controller._basic_agent_speed_profile(route, 40.0)

        self.assertTrue(all(speed <= 20.0 for speed in profile[4:-4]))

    def test_adaptive_speed_returns_smoothly_to_cruise_after_curve(self):
        next_speed = self.controller._basic_agent_limited_speed(
            current_speed=15.0,
            desired_speed=40.0,
            elapsed=0.5,
        )

        self.assertGreater(next_speed, 15.0)
        self.assertLess(next_speed, 40.0)
        self.assertEqual(18.0, next_speed)

    def test_adaptive_speed_does_not_brake_hard_for_lane_change(self):
        route = [
            carla.Location(x=0.0, y=0.0, z=0.0),
            carla.Location(x=5.0, y=0.0, z=0.0),
            carla.Location(x=10.0, y=0.0, z=0.0),
            carla.Location(x=15.0, y=0.5, z=0.0),
            carla.Location(x=20.0, y=1.75, z=0.0),
            carla.Location(x=25.0, y=3.0, z=0.0),
            carla.Location(x=30.0, y=3.5, z=0.0),
            carla.Location(x=35.0, y=3.5, z=0.0),
            carla.Location(x=40.0, y=3.5, z=0.0),
        ]

        profile = self.controller._basic_agent_speed_profile(route, 40.0)

        self.assertGreaterEqual(min(profile), 25.0)

    def test_adaptive_speed_handles_short_route_and_plan_end(self):
        route = [
            carla.Location(x=0.0, y=0.0, z=0.0),
            carla.Location(x=4.0, y=0.0, z=0.0),
        ]

        profile = self.controller._basic_agent_speed_profile(route, 40.0)
        index = self.controller._basic_agent_profile_index(
            carla.Location(x=4.0, y=0.0, z=0.0),
            route,
            0,
        )

        self.assertEqual((20.0, 20.0), profile)
        self.assertEqual(1, index)

    def test_adaptive_speed_uses_conservative_cap_when_off_route(self):
        agent = self.set_basic_agent_mode()
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        runtime["basic_agent_speed_profile"] = (40.0, 40.0, 40.0)
        runtime["basic_agent_applied_speed"] = 40.0
        runtime["basic_agent_speed_updated_at"] = time.monotonic() - 0.5
        self.vehicle.location = carla.Location(x=0.0, y=20.0, z=0.0)

        self.controller._service_basic_agent_control(self.world, force=True)

        self.assertTrue(agent.target_speeds)
        self.assertLess(agent.target_speeds[-1], 40.0)

    def test_disabled_adaptive_speed_preserves_constant_agent_speed(self):
        with (
            patch.dict(
                os.environ,
                {
                    "CARLA_MANAGER_DESTINATION_EXECUTOR": "basic_agent",
                    "CARLA_MANAGER_BASIC_AGENT_ADAPTIVE_SPEED": "0",
                },
            ),
            patch(
                "movement.runtime.load_basic_agent",
                return_value=(FakeBasicAgent, None),
            ),
        ):
            self.set_mode(AUTOPILOT_DESTINATION)

        agent = FakeBasicAgent.instances[-1]
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        self.controller._service_basic_agent_control(self.world, force=True)

        self.assertIsNone(runtime["basic_agent_speed_profile"])
        self.assertEqual([], agent.target_speeds)
        self.assertEqual(1, agent.run_step_calls)

    def test_adaptive_speed_is_applied_before_basic_agent_run_step(self):
        agent = self.set_basic_agent_mode()
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        runtime["basic_agent_speed_profile"] = (20.0, 20.0, 40.0)
        runtime["basic_agent_applied_speed"] = 40.0
        runtime["basic_agent_speed_updated_at"] = time.monotonic() - 0.5
        observations = []
        original_run_step = agent.run_step

        def observed_run_step():
            observations.append(list(agent.target_speeds))
            return original_run_step()

        agent.run_step = observed_run_step
        self.controller._service_basic_agent_control(self.world, force=True)

        self.assertTrue(agent.target_speeds)
        self.assertLess(agent.target_speeds[-1], 40.0)
        self.assertEqual(agent.target_speeds, observations[0])

    def test_basic_agent_does_not_submit_tm_path_or_route(self):
        self.set_basic_agent_mode()

        self.assertEqual([(False, 8000)], self.vehicle.autopilot_calls)
        self.assertEqual([], self.traffic_manager.paths)
        self.assertEqual([], self.traffic_manager.routes)
        self.assertIsNone(self.controller.traffic_manager)

    def test_basic_agent_async_control_runs_at_twenty_hertz(self):
        agent = self.set_basic_agent_mode()
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]

        self.controller._service_basic_agent_control(self.world)
        self.controller._service_basic_agent_control(self.world)

        self.assertEqual(1, agent.run_step_calls)
        self.assertIs(agent.control, self.vehicle.controls[-1])
        runtime["next_agent_step"] = 0.0
        self.controller._service_basic_agent_control(self.world)
        self.assertEqual(2, agent.run_step_calls)

    def test_basic_agent_sync_control_runs_once_per_frame_before_tick(self):
        agent = self.set_basic_agent_mode()
        self.controller.simulation_manager.synchronous_mode = True
        self.controller.simulation_manager.last_frame = 10

        self.controller.before_simulation_tick(self.world)
        self.controller.before_simulation_tick(self.world)
        self.assertEqual(1, agent.run_step_calls)

        self.controller.simulation_manager.last_frame = 11
        self.controller.before_simulation_tick(self.world)
        self.assertEqual(2, agent.run_step_calls)

    def test_basic_agent_done_is_only_an_auxiliary_signal(self):
        agent = self.set_basic_agent_mode()
        agent.done_result = True

        self.controller._service_basic_agent_control(self.world)
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        self.assertTrue(runtime["agent_done"])

        runtime["next_check"] = 0.0
        self.controller._service_vehicle_navigation(
            self.world,
            SimpleNamespace(frame=70),
        )
        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_NAVIGATING, state["navigation_status"])
        self.assertIn("confirming", state["status"])
        self.assertIn(
            self.vehicle.id,
            self.controller.vehicle_navigation_runtime,
        )

    def test_basic_agent_run_step_failure_stops_destination(self):
        agent = self.set_basic_agent_mode()
        agent.run_error = RuntimeError("agent step failed")

        self.controller._service_basic_agent_control(self.world)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertNotIn(
            self.vehicle.id,
            self.controller.vehicle_navigation_runtime,
        )
        self.assertEqual(VEHICLE_STATUS_FAILED, state["navigation_status"])
        self.assertFalse(state["autopilot_enabled"])
        self.assertFalse(state["basic_agent_settings_locked"])
        self.assertIn("agent step failed", state["error"])

    def test_basic_agent_apply_control_failure_stops_destination(self):
        self.set_basic_agent_mode()

        with patch.object(
            self.vehicle,
            "apply_control",
            side_effect=RuntimeError("apply control failed"),
        ):
            self.controller._service_basic_agent_control(self.world)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertNotIn(
            self.vehicle.id,
            self.controller.vehicle_navigation_runtime,
        )
        self.assertEqual(VEHICLE_STATUS_FAILED, state["navigation_status"])
        self.assertFalse(state["autopilot_enabled"])
        self.assertIn("apply control failed", state["error"])

    def test_basic_agent_to_free_roam_detaches_agent_before_enabling_tm(self):
        agent = self.set_basic_agent_mode()
        self.controller._service_basic_agent_control(self.world, force=True)
        calls_before_transition = agent.run_step_calls
        original_set_autopilot = self.vehicle.set_autopilot

        def guarded_set_autopilot(enabled, port):
            if enabled:
                self.assertNotIn(
                    self.vehicle.id,
                    self.controller.vehicle_navigation_runtime,
                )
            original_set_autopilot(enabled, port)

        self.vehicle.set_autopilot = guarded_set_autopilot
        self.set_mode(AUTOPILOT_FREE_ROAM)
        self.controller._service_basic_agent_control(self.world, force=True)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(AUTOPILOT_FREE_ROAM, state["mode"])
        self.assertTrue(state["autopilot_enabled"])
        self.assertEqual(calls_before_transition, agent.run_step_calls)
        self.assertEqual((True, 8000), self.vehicle.autopilot_calls[-1])

    def test_basic_agent_to_manual_stops_agent_and_replaces_last_control(self):
        agent = self.set_basic_agent_mode()
        self.controller._service_basic_agent_control(self.world, force=True)
        self.assertIs(agent.control, self.vehicle.controls[-1])

        self.set_mode(MANUAL_CONTROL)
        calls_after_transition = agent.run_step_calls
        self.controller._service_basic_agent_control(self.world, force=True)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(MANUAL_CONTROL, state["mode"])
        self.assertEqual(calls_after_transition, agent.run_step_calls)
        self.assertEqual(1.0, self.vehicle.controls[-1].brake)

    def test_basic_agent_to_no_control_stops_agent_and_vehicle(self):
        agent = self.set_basic_agent_mode()
        self.controller._service_basic_agent_control(self.world, force=True)

        self.set_mode(NO_CONTROL)
        calls_after_transition = agent.run_step_calls
        self.controller._service_basic_agent_control(self.world, force=True)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(NO_CONTROL, state["mode"])
        self.assertFalse(state["basic_agent_settings_locked"])
        self.assertEqual(calls_after_transition, agent.run_step_calls)
        self.assertEqual(1.0, self.vehicle.controls[-1].brake)

    def test_restarting_basic_agent_destination_replaces_runtime(self):
        first_agent = self.set_basic_agent_mode()
        first_runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        self.controller._service_basic_agent_control(self.world, force=True)
        first_calls = first_agent.run_step_calls

        second_agent = self.set_basic_agent_mode()
        second_runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        self.controller._service_basic_agent_control(self.world, force=True)

        self.assertIsNot(first_runtime, second_runtime)
        self.assertIs(second_agent, second_runtime["agent"])
        self.assertIsNot(first_runtime["raw_plan"], second_runtime["raw_plan"])
        self.assertEqual(first_calls, first_agent.run_step_calls)
        self.assertEqual(1, second_agent.run_step_calls)

    def test_failed_basic_agent_restart_cannot_resume_previous_agent(self):
        first_agent = self.set_basic_agent_mode()
        self.controller._service_basic_agent_control(self.world, force=True)
        first_calls = first_agent.run_step_calls

        class FailingBasicAgent(FakeBasicAgent):
            def set_global_plan(self, plan):
                raise RuntimeError("replacement initialization failed")

        with (
            patch.dict(
                os.environ,
                {"CARLA_MANAGER_DESTINATION_EXECUTOR": "basic_agent"},
            ),
            patch(
                "movement.runtime.load_basic_agent",
                return_value=(FailingBasicAgent, None),
            ),
        ):
            self.set_mode(AUTOPILOT_DESTINATION)

        self.controller._service_basic_agent_control(self.world, force=True)
        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertNotIn(
            self.vehicle.id,
            self.controller.vehicle_navigation_runtime,
        )
        self.assertEqual(first_calls, first_agent.run_step_calls)
        self.assertEqual(VEHICLE_STATUS_FAILED, state["navigation_status"])
        self.assertIn("replacement initialization failed", state["error"])

    def test_basic_agent_release_removes_runtime_before_actor_cleanup(self):
        agent = self.set_basic_agent_mode()
        cleanup_observations = []

        def remove_actor(actor_id, _remove_sensors):
            cleanup_observations.append((
                actor_id,
                actor_id in self.controller.vehicle_navigation_runtime,
            ))

        self.controller.movement_preview_actor_id = -1
        self.controller.actor_service = SimpleNamespace(remove=remove_actor)
        self.controller.sensor_service = SimpleNamespace(
            remove_for_parent=lambda _actor_id: None
        )
        with patch.object(
            self.controller,
            "_destroy_walker_ai",
            return_value=None,
        ):
            self.controller._dispatch_remove_actor(None, self.vehicle.id)

        self.controller._service_basic_agent_control(self.world, force=True)
        self.assertEqual([(self.vehicle.id, False)], cleanup_observations)
        self.assertEqual(0, agent.run_step_calls)

    def test_basic_agent_world_teardown_prevents_later_agent_steps(self):
        agent = self.set_basic_agent_mode()
        self.controller.world_transition_event.set()

        self.controller._shutdown_vehicle_controls(
            disable_autopilot=True,
            clear_paths=False,
            strict=True,
        )
        self.controller._service_basic_agent_control(self.world, force=True)

        self.assertNotIn(
            self.vehicle.id,
            self.controller.vehicle_navigation_runtime,
        )
        self.assertEqual(0, agent.run_step_calls)

    def test_clear_world_lifecycle_releases_basic_agent_before_destroy(self):
        agent = self.set_basic_agent_mode()
        plan = SimpleNamespace(items=[SimpleNamespace(
            runtime_id=self.vehicle.id,
            category=ActorCategory.VEHICLE,
        )])
        with (
            patch.object(self.controller, "cleanup_movement_preview"),
            patch.object(self.controller, "cleanup_sensor_previews"),
            patch.object(self.controller, "cleanup_preview"),
        ):
            self.controller._prepare_clear_lifecycle(plan)

        self.controller._service_basic_agent_control(self.world, force=True)
        self.assertNotIn(
            self.vehicle.id,
            self.controller.vehicle_navigation_runtime,
        )
        self.assertEqual(0, agent.run_step_calls)

    def test_basic_agent_arrival_removes_runtime_and_stops_control_cycle(self):
        agent = self.set_basic_agent_mode()
        self.vehicle.location = carla.Location(x=30.0, y=0.0, z=0.0)
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        runtime["next_check"] = 0.0

        self.controller._service_vehicle_navigation(
            self.world,
            SimpleNamespace(frame=91),
        )
        self.controller._service_basic_agent_control(self.world, force=True)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_ARRIVED, state["navigation_status"])
        self.assertFalse(state["basic_agent_settings_locked"])
        self.assertNotIn(
            self.vehicle.id,
            self.controller.vehicle_navigation_runtime,
        )
        self.assertEqual(0, agent.run_step_calls)
        self.assertEqual(1.0, self.vehicle.controls[-1].brake)

    def test_closing_preview_keeps_basic_agent_destination_active(self):
        agent = self.set_basic_agent_mode()

        self.controller.cleanup_movement_preview("window_closed")
        self.controller._service_basic_agent_control(self.world, force=True)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(AUTOPILOT_DESTINATION, state["mode"])
        self.assertEqual(VEHICLE_STATUS_NAVIGATING, state["navigation_status"])
        self.assertIn(self.vehicle.id, self.controller.vehicle_navigation_runtime)
        self.assertEqual(1, agent.run_step_calls)

    def test_route_instructions_reduce_waypoint_runs_to_decisions(self):
        route_waypoints = [
            {"road_option": "RoadOption.LANEFOLLOW"},
            {"road_option": "RoadOption.LEFT"},
            {"road_option": "LEFT"},
            {"road_option": "LANEFOLLOW"},
            {"road_option": "LEFT"},
            {"road_option": "RIGHT"},
            {"road_option": "RIGHT"},
            {"road_option": "STRAIGHT"},
            {"road_option": "LANEFOLLOW"},
        ]

        instructions = self.controller._destination_route_instructions(
            route_waypoints
        )

        self.assertEqual(
            ["Left", "Left", "Right", "Straight"],
            instructions,
        )

    def test_route_instructions_accept_numeric_lanefollow(self):
        instructions = self.controller._destination_route_instructions([
            {"road_option": 4},
            {"road_option": "4"},
        ])

        self.assertEqual([], instructions)

    def test_route_instructions_extract_one_left_turn(self):
        instructions = self.controller._destination_route_instructions([
            {"road_option": 4},
            {"road_option": 1},
            {"road_option": 1},
            {"road_option": 4},
        ])

        self.assertEqual(["Left"], instructions)

    def test_route_instructions_extract_one_right_turn(self):
        instructions = self.controller._destination_route_instructions([
            {"road_option": 4},
            {"road_option": 2},
            {"road_option": 2},
            {"road_option": 4},
        ])

        self.assertEqual(["Right"], instructions)

    def test_route_instructions_extract_several_junctions(self):
        instructions = self.controller._destination_route_instructions([
            {"road_option": 4},
            {"road_option": 1},
            {"road_option": 4},
            {"road_option": 3},
            {"road_option": 4},
            {"road_option": 2},
            {"road_option": 4},
        ])

        self.assertEqual(["Left", "Straight", "Right"], instructions)

    def test_route_instructions_reject_lane_change_without_false_turn(self):
        with self.assertRaisesRegex(RuntimeError, "CHANGELANELEFT"):
            self.controller._destination_route_instructions([
                {"road_option": 4},
                {"road_option": 5},
                {"road_option": 4},
            ])

    def test_route_executor_uses_only_nonempty_set_route(self):
        with (
            patch.dict(
                os.environ,
                {"CARLA_MANAGER_DESTINATION_EXECUTOR": "route"},
            ),
            patch.dict(os.environ, {"CARLA_MANAGER_NAV_DEBUG": "1"}),
            patch("builtins.print"),
        ):
            self.set_mode(AUTOPILOT_DESTINATION)

        nonempty_paths = [path for _actor, path, _empty in self.traffic_manager.paths if path]
        nonempty_routes = [route for _actor, route, _empty in self.traffic_manager.routes if route]
        self.assertEqual([], nonempty_paths)
        self.assertEqual([["Straight"]], nonempty_routes)
        self.assertEqual([], self.traffic_manager.paths[0][1])
        runtime = self.controller.vehicle_navigation_runtime[self.vehicle.id]
        self.assertEqual("route", runtime["executor"])
        self.assertEqual(("Straight",), runtime["route_instructions"])
        diagnostics = self.controller.database.vehicle_control_states[
            self.vehicle.id
        ]["navigation_debug"]
        self.assertEqual("route", diagnostics["executor"])
        self.assertEqual(1, diagnostics["instruction_count"])
        self.assertEqual(["Straight"], diagnostics["instructions"])

    def test_route_executor_accepts_lanefollow_only_plan(self):
        class LaneFollowPlanner(FakePlanner):
            def trace_route(self, start, destination):
                return [
                    (self.world_map.get_waypoint(start), 4),
                    (FakeWaypoint(15.0), 4),
                    (self.world_map.get_waypoint(destination), 4),
                ]

        carla_server_module.GlobalRoutePlanner = LaneFollowPlanner
        with patch.dict(
            os.environ,
            {"CARLA_MANAGER_DESTINATION_EXECUTOR": "route"},
        ):
            self.set_mode(AUTOPILOT_DESTINATION)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_NAVIGATING, state["navigation_status"])
        self.assertEqual([], self.traffic_manager.routes[-1][1])
        self.assertEqual(0, state["route"]["instruction_count"])

    def test_switching_executor_replaces_previous_route_buffer(self):
        with patch.dict(
            os.environ,
            {"CARLA_MANAGER_DESTINATION_EXECUTOR": "route"},
        ):
            self.set_mode(AUTOPILOT_DESTINATION)

        self.set_mode(AUTOPILOT_DESTINATION)

        self.assertEqual([], self.traffic_manager.routes[-1][1])
        self.assertEqual(
            [0.0, 15.0, 30.0],
            [point.x for point in self.traffic_manager.paths[-1][1]],
        )
        clear_route_index = max(
            index
            for index, event in enumerate(self.execution_events)
            if event[0] == "set_route" and event[2] == 0
        )
        final_path_index = max(
            index
            for index, event in enumerate(self.execution_events)
            if event[0] == "set_path" and event[2] > 0
        )
        self.assertLess(clear_route_index, final_path_index)

    def test_route_executor_is_cleared_for_all_control_mode_transitions(self):
        with patch.dict(
            os.environ,
            {"CARLA_MANAGER_DESTINATION_EXECUTOR": "route"},
        ):
            for next_mode in (
                MANUAL_CONTROL,
                NO_CONTROL,
                AUTOPILOT_FREE_ROAM,
            ):
                self.set_mode(AUTOPILOT_DESTINATION)
                route_submissions = len(self.traffic_manager.routes)

                self.set_mode(next_mode)

                self.assertGreater(
                    len(self.traffic_manager.routes),
                    route_submissions,
                )
                self.assertEqual([], self.traffic_manager.routes[-1][1])
                self.assertNotIn(
                    self.vehicle.id,
                    self.controller.vehicle_navigation_runtime,
                )

    def test_route_executor_rejects_planned_lane_change(self):
        class LaneChangePlanner(FakePlanner):
            def trace_route(self, start, destination):
                return [
                    (self.world_map.get_waypoint(start), "LANEFOLLOW"),
                    (FakeWaypoint(10.0, y=3.5, lane_id=2), "CHANGELANELEFT"),
                    (self.world_map.get_waypoint(destination), "LANEFOLLOW"),
                ]

        carla_server_module.GlobalRoutePlanner = LaneChangePlanner
        with patch.dict(
            os.environ,
            {"CARLA_MANAGER_DESTINATION_EXECUTOR": "route"},
        ):
            self.set_mode(AUTOPILOT_DESTINATION)

        state = self.controller.database.vehicle_control_states[self.vehicle.id]
        self.assertEqual(VEHICLE_STATUS_FAILED, state["navigation_status"])
        self.assertIn("cannot represent", state["error"])
        self.assertIn("CHANGELANELEFT", state["error"])
        self.assertEqual([], self.vehicle.autopilot_calls)
        self.assertEqual([], self.traffic_manager.paths)
        self.assertEqual([], self.traffic_manager.routes)

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
        self.assertEqual(
            [(self.vehicle.id, False), (self.vehicle.id, True)],
            self.traffic_manager.auto_lane_changes,
        )
        self.assertEqual(
            [(self.vehicle.id, 0), (self.vehicle.id, 0)],
            self.traffic_manager.random_left_lane_changes,
        )
        self.assertEqual(
            [(self.vehicle.id, 0), (self.vehicle.id, 0)],
            self.traffic_manager.random_right_lane_changes,
        )

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
