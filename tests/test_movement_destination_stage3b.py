import unittest
import queue
from types import SimpleNamespace

from gui_contents.movement_control import MovementControlWindow
from movement_control import MovementControlSessionManager
from movement_destination import confirm_destination, destination_result
from tests.test_walker_control import (
    FakeLocation,
    FakeWorld,
    WalkerControlTests,
)


class FakeVariable:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeWidget:
    def __init__(self):
        self.state = None

    def configure(self, **values):
        if "state" in values:
            self.state = values["state"]


class QueueingCarla:
    def __init__(self):
        self.command_queue = queue.Queue()

    def set_walker_ai_speed(self, session_id, actor_id, speed):
        self.command_queue.put(("set_walker_ai_speed", {
            "session_id": session_id,
            "actor_id": actor_id,
            "speed": speed,
        }))


def fake_walker_window(speed, callback):
    window = object.__new__(MovementControlWindow)
    window.closed = False
    window.is_vehicle = False
    window.walker_ai_active = True
    window.walker_speed_var = FakeVariable(f"{speed:.1f}")
    window.walker_mode_var = FakeVariable(WALKER_CONTROL_DESTINATION)
    window.control_state_var = FakeVariable("")
    window.walker_direction_x_var = FakeVariable("1.000")
    window.walker_direction_y_var = FakeVariable("0.000")
    window._walker_speed_state_update = False
    window.walker_speed_edit_dirty = False
    window.walker_speed_pending = False
    window.walker_speed_requested = None
    window.on_walker_speed_changed = callback
    for name in (
        "walker_toggle_button",
        "walker_speed_spinbox",
        "walker_speed_button",
        "walker_mode_combo",
        "walker_mode_button",
        "walker_direction_x_entry",
        "walker_direction_y_entry",
        "walker_manual_button",
    ):
        setattr(window, name, FakeWidget())
    return window
from walker_control import (
    WALKER_CONTROL_DESTINATION,
    WALKER_CONTROL_MANUAL,
    WALKER_CONTROL_RANDOM,
    WALKER_STATUS_ARRIVED,
    WALKER_STATUS_CANCELLED,
    WALKER_STATUS_FAILED,
    WALKER_STATUS_MOVING,
    WALKER_STATUS_STUCK,
)


class PedestrianDestinationControlTests(unittest.TestCase):
    def setUp(self):
        WalkerControlTests.setUp(self)
        self.world = FakeWorld([
            FakeLocation(8.0, 2.0),
            FakeLocation(12.0, 3.0),
        ])
        self.world.id = 1

    def set_destination(self, x=10.0, y=0.0, z=0.0):
        destination = destination_result(
            self.walker.id,
            "pedestrian",
            self.world.id,
            {"x": x, "y": y, "z": z},
            "unverified",
            "Coordinates accepted with CARLA navmesh limitation.",
        )
        self.controller.database.walker_ai_states[21]["destination"] = (
            confirm_destination(destination)
        )

    def set_mode(self, mode, speed=1.5):
        self.controller._set_walker_control_mode(self.world, {
            "session_id": "session",
            "actor_id": 21,
            "mode": mode,
            "speed": speed,
        })

    @staticmethod
    def snapshot(frame, elapsed_seconds):
        return SimpleNamespace(
            frame=frame,
            timestamp=SimpleNamespace(elapsed_seconds=elapsed_seconds),
        )

    def service(self, frame=2, elapsed_seconds=1.0):
        runtime = self.controller.walker_ai_runtime[21]
        runtime["next_check"] = 0.0
        self.controller._service_walker_ai(
            self.world,
            self.snapshot(frame, elapsed_seconds),
        )

    def test_transitions_manual_random_destination_random_manual(self):
        self.set_mode(WALKER_CONTROL_RANDOM)
        state = self.controller.database.walker_ai_states[21]
        self.assertEqual(WALKER_CONTROL_RANDOM, state["control_mode"])
        self.assertEqual(WALKER_STATUS_MOVING, state["movement_status"])

        self.set_destination()
        self.set_mode(WALKER_CONTROL_DESTINATION)
        self.assertEqual(
            WALKER_CONTROL_DESTINATION,
            state["control_mode"],
        )
        self.assertFalse(state["random_navigation"])

        self.set_mode(WALKER_CONTROL_RANDOM)
        self.assertEqual(WALKER_CONTROL_RANDOM, state["control_mode"])
        self.assertTrue(state["random_navigation"])

        self.set_mode(WALKER_CONTROL_MANUAL)
        self.assertEqual(WALKER_CONTROL_MANUAL, state["control_mode"])
        self.assertFalse(state["active"])
        self.assertEqual(0.0, self.walker.controls[-1].speed)

    def test_destination_direction_is_updated_toward_target(self):
        self.set_destination(10.0, 0.0)
        self.set_mode(WALKER_CONTROL_DESTINATION, speed=2.0)
        self.walker.location = FakeLocation(1.0, 2.0)

        self.service()

        control = self.walker.controls[-1]
        self.assertGreater(control.direction.x, 0.0)
        self.assertLess(control.direction.y, 0.0)
        self.assertAlmostEqual(2.0, control.speed)

    def test_destination_speed_change_persists_across_service_updates(self):
        self.set_destination(10.0, 0.0)
        self.set_mode(WALKER_CONTROL_DESTINATION, speed=1.2)
        runtime = self.controller.walker_ai_runtime[21]
        original_target = runtime["target"]

        self.controller._set_walker_ai_speed({
            "session_id": "session",
            "actor_id": 21,
            "speed": 2.6,
        })
        self.assertAlmostEqual(2.6, self.walker.controls[-1].speed, places=5)
        self.assertAlmostEqual(2.6, runtime["speed"])

        self.walker.location = FakeLocation(1.0, 0.0)
        self.service(frame=3, elapsed_seconds=1.5)

        state = self.controller.database.walker_ai_states[21]
        self.assertAlmostEqual(2.6, self.walker.controls[-1].speed, places=5)
        self.assertAlmostEqual(2.6, state["speed"])
        self.assertIs(original_target, runtime["target"])
        self.assertEqual(WALKER_STATUS_MOVING, state["movement_status"])

    def test_gui_edit_survives_stale_event_and_reaches_all_destination_layers(self):
        speed_a = 1.2
        speed_b = 2.6
        self.set_destination(10.0, 0.0)
        self.set_mode(WALKER_CONTROL_DESTINATION, speed=speed_a)
        self.controller.database.world = self.world
        while not self.controller.database.movement_event_queue.empty():
            self.controller.database.movement_event_queue.get_nowait()

        queueing_carla = QueueingCarla()
        session = object.__new__(MovementControlSessionManager)
        session.manager = SimpleNamespace(
            carla=queueing_carla,
            database=self.controller.database,
        )
        session.session_id = "session"
        session.actor_id = 21
        session.actor_type = self.walker.type_id
        session.world_id = self.world.id
        session.closing = False
        window = fake_walker_window(speed_a, session.change_walker_speed)
        session.window = window

        window.walker_speed_var.set(f"{speed_b:.1f}")
        window._walker_speed_edited()
        self.controller._publish_walker_ai_state(21, session_id="session")
        session._poll_controller_events()
        self.assertEqual(f"{speed_b:.1f}", window.walker_speed_var.get())

        original_target = self.controller.walker_ai_runtime[21]["target"]
        window._apply_walker_speed()
        command, data = queueing_carla.command_queue.get_nowait()
        self.assertEqual("set_walker_ai_speed", command)
        self.assertAlmostEqual(speed_b, data["speed"])
        self.controller._set_walker_ai_speed(data)

        for frame, elapsed_seconds in ((3, 1.0), (4, 1.4), (5, 1.8)):
            runtime = self.controller.walker_ai_runtime[21]
            runtime["next_check"] = 0.0
            self.controller._service_walker_ai(
                self.world,
                self.snapshot(frame, elapsed_seconds),
            )
        session._poll_controller_events()

        state = self.controller.database.walker_ai_states[21]
        runtime = self.controller.walker_ai_runtime[21]
        self.assertAlmostEqual(speed_b, state["speed"])
        self.assertAlmostEqual(speed_b, runtime["speed"])
        self.assertAlmostEqual(
            speed_b,
            self.walker.controls[-1].speed,
            places=5,
        )
        self.assertEqual(f"{speed_b:.1f}", window.walker_speed_var.get())
        self.assertFalse(window.walker_speed_pending)
        self.assertIs(original_target, runtime["target"])
        self.assertEqual(WALKER_STATUS_MOVING, state["movement_status"])

        reopened = fake_walker_window(speed_a, lambda _speed: None)
        reopened.set_walker_ai_state(state)
        self.assertEqual(f"{speed_b:.1f}", reopened.walker_speed_var.get())

    def test_arrival_stops_pedestrian_and_persists_arrived(self):
        self.set_destination(2.0, 0.0)
        self.set_mode(WALKER_CONTROL_DESTINATION)
        self.walker.location = FakeLocation(2.0, 0.0)

        self.service()

        state = self.controller.database.walker_ai_states[21]
        self.assertEqual(WALKER_STATUS_ARRIVED, state["movement_status"])
        self.assertFalse(state["active"])
        self.assertEqual(0.0, self.walker.controls[-1].speed)

    def test_arrival_stops_control_without_losing_configured_speed(self):
        self.set_destination(2.0, 0.0)
        self.set_mode(WALKER_CONTROL_DESTINATION, speed=2.4)
        self.controller._set_walker_ai_speed({
            "session_id": "session",
            "actor_id": 21,
            "speed": 2.8,
        })
        self.walker.location = FakeLocation(2.0, 0.0)

        self.service(frame=4, elapsed_seconds=2.0)

        state = self.controller.database.walker_ai_states[21]
        self.assertEqual(WALKER_STATUS_ARRIVED, state["movement_status"])
        self.assertAlmostEqual(2.8, state["speed"])
        self.assertEqual(0.0, self.walker.controls[-1].speed)

    def test_no_progress_marks_stuck_without_random_retarget(self):
        self.set_destination(10.0, 0.0)
        self.set_mode(WALKER_CONTROL_DESTINATION)
        runtime = self.controller.walker_ai_runtime[21]
        runtime["last_progress_sim_time"] = 0.0
        original_target = runtime["target"]

        self.service(elapsed_seconds=3.0)

        state = self.controller.database.walker_ai_states[21]
        self.assertEqual(WALKER_STATUS_STUCK, state["movement_status"])
        self.assertFalse(state["active"])
        self.assertIs(original_target, runtime["target"])
        self.assertEqual(0.0, self.walker.controls[-1].speed)

    def test_switch_to_manual_cancels_destination_and_prevents_overwrite(self):
        self.set_destination(10.0, 0.0)
        self.set_mode(WALKER_CONTROL_DESTINATION)
        self.set_mode(WALKER_CONTROL_MANUAL)
        state = self.controller.database.walker_ai_states[21]
        self.assertEqual(WALKER_STATUS_CANCELLED, state["movement_status"])

        self.controller._set_walker_manual_control({
            "session_id": "session",
            "actor_id": 21,
            "direction_x": 0.0,
            "direction_y": 1.0,
            "speed": 1.2,
        })
        control_count = len(self.walker.controls)
        self.controller._service_walker_ai(
            self.world,
            self.snapshot(5, 5.0),
        )
        self.assertEqual(control_count, len(self.walker.controls))
        self.assertFalse(state["active"])
        self.assertEqual(WALKER_CONTROL_MANUAL, state["control_mode"])

    def test_closing_window_does_not_stop_destination_mode(self):
        self.set_destination(10.0, 0.0)
        self.set_mode(WALKER_CONTROL_DESTINATION)

        self.controller.cleanup_movement_preview("window_closed")

        state = self.controller.database.walker_ai_states[21]
        self.assertTrue(state["active"])
        self.assertEqual(WALKER_CONTROL_DESTINATION, state["control_mode"])

    def test_destination_without_confirmed_target_fails_safely(self):
        self.set_mode(WALKER_CONTROL_DESTINATION)
        state = self.controller.database.walker_ai_states[21]
        self.assertEqual(WALKER_STATUS_FAILED, state["movement_status"])
        self.assertFalse(state["active"])
        self.assertEqual(0.0, self.walker.controls[-1].speed)


if __name__ == "__main__":
    unittest.main()
