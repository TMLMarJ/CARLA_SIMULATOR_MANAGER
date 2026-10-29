import queue
import threading
import unittest
from types import SimpleNamespace

from carla_server.carla_server import CarlaController
from walker_control import WALKER_AI_RUNNING, WALKER_AI_STOPPED
from walker_control import initial_walker_ai_state


class FakeLocation:
    def __init__(self, x, y=0.0, z=0.0):
        self.x = x
        self.y = y
        self.z = z


class FakeWalker:
    def __init__(self):
        self.id = 21
        self.type_id = "walker.pedestrian.test"
        self.is_alive = True
        self.location = FakeLocation(0.0)
        self.controls = []

    def get_location(self):
        return self.location

    def apply_control(self, control):
        self.controls.append(control)


class FakeWalkerController:
    def __init__(self):
        self.id = 81
        self.is_alive = True
        self.start_count = 0
        self.stop_count = 0
        self.destroy_count = 0
        self.speeds = []
        self.targets = []

    def start(self):
        self.start_count += 1

    def stop(self):
        self.stop_count += 1

    def set_max_speed(self, speed):
        self.speeds.append(speed)

    def go_to_location(self, target):
        self.targets.append(target)

    def destroy(self):
        self.destroy_count += 1
        self.is_alive = False


class FakeWorld:
    def __init__(self, navigation_points):
        self.navigation_points = list(navigation_points)
        self.controller = FakeWalkerController()
        self.spawn_count = 0
        self.frame = 1

    def get_blueprint_library(self):
        return SimpleNamespace(find=lambda blueprint_id: blueprint_id)

    def spawn_actor(self, blueprint, transform, attach_to):
        self.spawn_count += 1
        self.attached_to = attach_to
        return self.controller

    def get_random_location_from_navigation(self):
        if not self.navigation_points:
            return None
        return self.navigation_points.pop(0)

    def get_snapshot(self):
        return SimpleNamespace(frame=self.frame)


class WalkerControlTests(unittest.TestCase):
    def setUp(self):
        self.walker = FakeWalker()
        self.controller = object.__new__(CarlaController)
        self.controller.database = SimpleNamespace(
            lock=threading.Lock(),
            actors_db=SimpleNamespace(actors={21: self.walker}),
            walker_ai_states={21: initial_walker_ai_state()},
            vehicle_control_states={},
            movement_event_queue=queue.Queue(),
        )
        self.controller.dashboard_monitor = SimpleNamespace(
            record_error=lambda _error: None
        )
        self.controller.walker_ai_controllers = {}
        self.controller.walker_ai_runtime = {}
        self.controller.movement_preview_camera = None
        self.controller.movement_preview_publisher = None
        self.controller.movement_preview_session_id = "session"
        self.controller.movement_preview_actor_id = 21
        self.controller.movement_control_queue = None
        self.controller.manual_control_command = None
        self.controller.manual_control_received_at = None
        self.controller.manual_control_input_fresh = False

    def set_enabled(self, world, enabled, speed=1.7):
        self.controller._set_walker_ai_enabled(world, {
            "session_id": "session",
            "actor_id": 21,
            "enabled": enabled,
            "speed": speed,
        })

    def test_start_uses_direct_control_and_assigns_random_target(self):
        target = FakeLocation(8.0, 2.0, 0.0)
        world = FakeWorld([target])

        self.set_enabled(world, True)

        state = self.controller.database.walker_ai_states[21]
        self.assertEqual(WALKER_AI_RUNNING, state["mode"])
        self.assertTrue(state["active"])
        self.assertIsNone(state["controller_id"])
        self.assertEqual(0, world.spawn_count)
        self.assertEqual({}, self.controller.walker_ai_controllers)
        self.assertEqual(1, len(self.walker.controls))
        self.assertAlmostEqual(1.7, self.walker.controls[-1].speed)
        self.assertEqual(8.0, state["target"]["x"])

    def test_stop_and_resume_restarts_direct_control(self):
        world = FakeWorld([FakeLocation(5.0), FakeLocation(9.0)])
        self.set_enabled(world, True)

        self.set_enabled(world, False)
        stopped = self.controller.database.walker_ai_states[21]
        self.assertEqual(WALKER_AI_STOPPED, stopped["mode"])
        self.assertFalse(stopped["active"])
        self.assertEqual(0.0, self.walker.controls[-1].speed)

        self.set_enabled(world, True)
        self.assertEqual(0, world.spawn_count)
        self.assertEqual(0, world.controller.start_count)
        self.assertTrue(
            self.controller.walker_ai_runtime[21]["fallback_active"]
        )
        self.assertAlmostEqual(1.7, self.walker.controls[-1].speed)

    def test_repeated_start_refreshes_direct_target_without_controller(self):
        first_world = FakeWorld([FakeLocation(5.0)])
        self.set_enabled(first_world, True)
        second_world = FakeWorld([FakeLocation(9.0)])
        self.set_enabled(second_world, True)

        self.assertEqual(0, second_world.spawn_count)
        self.assertEqual({}, self.controller.walker_ai_controllers)
        self.assertEqual(
            9.0,
            self.controller.database.walker_ai_states[21]["target"]["x"],
        )

    def test_stop_remains_available_when_speed_field_is_invalid(self):
        world = FakeWorld([FakeLocation(5.0)])
        self.set_enabled(world, True)

        self.controller._set_walker_ai_enabled(world, {
            "session_id": "session",
            "actor_id": 21,
            "enabled": False,
            "speed": "invalid",
        })

        state = self.controller.database.walker_ai_states[21]
        self.assertEqual(WALKER_AI_STOPPED, state["mode"])
        self.assertFalse(state["active"])
        self.assertEqual(0, world.controller.stop_count)
        self.assertEqual(0.0, self.walker.controls[-1].speed)

    def test_speed_can_be_saved_while_stopped_without_creating_controller(self):
        world = FakeWorld([])

        self.controller._set_walker_ai_speed({
            "session_id": "session",
            "actor_id": 21,
            "speed": 2.3,
        })

        state = self.controller.database.walker_ai_states[21]
        self.assertEqual(2.3, state["speed"])
        self.assertEqual(0, world.spawn_count)
        self.assertEqual({}, self.controller.walker_ai_controllers)

    def test_speed_change_is_applied_to_active_direct_control(self):
        world = FakeWorld([FakeLocation(5.0)])
        self.set_enabled(world, True)

        self.controller._set_walker_ai_speed({
            "session_id": "session",
            "actor_id": 21,
            "speed": 2.3,
        })

        self.assertEqual([], world.controller.speeds)
        self.assertAlmostEqual(2.3, self.walker.controls[-1].speed)
        self.assertEqual(
            2.3,
            self.controller.database.walker_ai_states[21]["speed"],
        )

    def test_finishing_direct_leg_assigns_another_target(self):
        first = FakeLocation(5.0)
        second = FakeLocation(12.0)
        world = FakeWorld([first, second])
        self.set_enabled(world, True)
        self.walker.location = FakeLocation(5.0)
        runtime = self.controller.walker_ai_runtime[21]
        runtime["next_check"] = 0.0
        runtime["last_frame"] = 1

        self.controller._service_walker_ai(
            world,
            SimpleNamespace(frame=2),
        )

        self.assertEqual([], world.controller.targets)
        state_target = self.controller.database.walker_ai_states[21]["target"]
        self.assertEqual(12.0, state_target["x"])

    def test_no_navigation_point_uses_actor_heading(self):
        world = FakeWorld([])

        self.set_enabled(world, True)

        state = self.controller.database.walker_ai_states[21]
        self.assertTrue(state["active"])
        self.assertIsNone(state["target"])
        self.assertIn("actor heading", state["status"])
        self.assertEqual(1, len(self.walker.controls))

    def test_unchanged_world_frame_does_not_trigger_stall_reassignment(self):
        first = FakeLocation(5.0)
        second = FakeLocation(12.0)
        world = FakeWorld([first, second])
        self.set_enabled(world, True)
        runtime = self.controller.walker_ai_runtime[21]
        runtime["next_check"] = 0.0
        runtime["last_frame"] = 1
        runtime["last_progress_at"] = 0.0

        self.controller._service_walker_ai(
            world,
            SimpleNamespace(frame=1),
        )

        self.assertEqual(1, len(self.walker.controls))

    def test_advancing_frame_services_direct_control(self):
        first = FakeLocation(5.0)
        second = FakeLocation(12.0)
        world = FakeWorld([first, second])
        self.set_enabled(world, True)
        runtime = self.controller.walker_ai_runtime[21]
        runtime["next_check"] = 0.0
        runtime["last_frame"] = 1

        self.controller._service_walker_ai(
            world,
            SimpleNamespace(frame=2),
        )

        self.assertEqual([], world.controller.targets)
        self.assertEqual(0, world.controller.stop_count)
        self.assertEqual(0, world.controller.destroy_count)
        self.assertNotIn(21, self.controller.walker_ai_controllers)
        self.assertTrue(
            self.controller.walker_ai_runtime[21]["fallback_active"]
        )
        self.assertEqual(2, len(self.walker.controls))
        self.assertIn(
            "direct control",
            self.controller.database.walker_ai_states[21]["status"],
        )

    def test_fallback_speed_change_applies_new_walker_control(self):
        world = FakeWorld([FakeLocation(5.0)])
        self.set_enabled(world, True)
        runtime = self.controller.walker_ai_runtime[21]
        runtime["next_check"] = 0.0
        runtime["last_frame"] = 1
        runtime["last_progress_at"] = 0.0
        self.controller._service_walker_ai(world, SimpleNamespace(frame=2))

        self.controller._set_walker_ai_speed({
            "session_id": "session",
            "actor_id": 21,
            "speed": 2.3,
        })

        self.assertAlmostEqual(2.3, self.walker.controls[-1].speed)

    def test_stop_clears_fallback_walker_control(self):
        world = FakeWorld([FakeLocation(5.0)])
        self.set_enabled(world, True)
        runtime = self.controller.walker_ai_runtime[21]
        runtime["next_check"] = 0.0
        runtime["last_frame"] = 1
        runtime["last_progress_at"] = 0.0
        self.controller._service_walker_ai(world, SimpleNamespace(frame=2))

        self.set_enabled(world, False)

        self.assertEqual(0.0, self.walker.controls[-1].speed)
        self.assertFalse(
            self.controller.walker_ai_runtime[21]["fallback_active"]
        )

    def test_closing_preview_does_not_stop_walker_ai(self):
        world = FakeWorld([FakeLocation(6.0)])
        self.set_enabled(world, True)

        self.controller.cleanup_movement_preview("window_closed")

        state = self.controller.database.walker_ai_states[21]
        self.assertTrue(state["active"])
        self.assertEqual(0, world.controller.stop_count)
        self.assertTrue(world.controller.is_alive)

    def test_actor_cleanup_stops_direct_control_without_controller(self):
        world = FakeWorld([FakeLocation(6.0)])
        self.set_enabled(world, True)

        self.controller._destroy_walker_ai(21, remove_state=True)

        self.assertEqual(0, world.controller.stop_count)
        self.assertEqual(0, world.controller.destroy_count)
        self.assertEqual(0.0, self.walker.controls[-1].speed)
        self.assertNotIn(21, self.controller.walker_ai_controllers)
        self.assertNotIn(21, self.controller.database.walker_ai_states)


if __name__ == "__main__":
    unittest.main()
