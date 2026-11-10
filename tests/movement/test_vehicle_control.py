import queue
import threading
import unittest
from types import SimpleNamespace

from core.controller import CarlaController
from movement.vehicle import (
    AUTOPILOT,
    AUTOPILOT_FREE_ROAM,
    MANUAL_CONTROL,
    NO_CONTROL,
    initial_vehicle_control_state,
)


class FakeVehicle:
    def __init__(self):
        self.id = 7
        self.type_id = "vehicle.test"
        self.is_alive = True
        self.controls = []
        self.autopilot_calls = []

    def apply_control(self, control):
        self.controls.append(control)

    def set_autopilot(self, enabled, port):
        self.autopilot_calls.append((enabled, port))


class FakeTrafficManager:
    def __init__(self):
        self.sync_modes = []
        self.paths = []
        self.auto_lane_changes = []
        self.random_left_lane_changes = []
        self.random_right_lane_changes = []

    def set_synchronous_mode(self, enabled):
        self.sync_modes.append(enabled)

    def set_path(self, actor, path, empty_buffer=True):
        self.paths.append((actor.id, list(path), empty_buffer))

    def auto_lane_change(self, actor, enabled):
        self.auto_lane_changes.append((actor.id, bool(enabled)))

    def random_left_lanechange_percentage(self, actor, percentage):
        self.random_left_lane_changes.append((actor.id, percentage))

    def random_right_lanechange_percentage(self, actor, percentage):
        self.random_right_lane_changes.append((actor.id, percentage))


class FakeWorld:
    def __init__(self, synchronous_mode=False):
        self.settings = SimpleNamespace(synchronous_mode=synchronous_mode)

    def get_settings(self):
        return self.settings


class VehicleControlTests(unittest.TestCase):
    def setUp(self):
        self.vehicle = FakeVehicle()
        self.traffic_manager = FakeTrafficManager()
        self.controller = object.__new__(CarlaController)
        self.controller.database = SimpleNamespace(
            lock=threading.Lock(),
            actors_db=SimpleNamespace(actors={7: self.vehicle}),
            vehicle_control_states={7: initial_vehicle_control_state()},
            movement_event_queue=queue.Queue(),
        )
        self.controller.dashboard_monitor = SimpleNamespace(
            record_error=lambda _error: None
        )
        self.controller.client = SimpleNamespace(
            get_trafficmanager=lambda _port: self.traffic_manager
        )
        self.controller.traffic_manager = None
        self.controller.movement_preview_camera = None
        self.controller.movement_preview_publisher = None
        self.controller.movement_preview_session_id = "session"
        self.controller.movement_preview_actor_id = 7
        self.controller.movement_control_queue = queue.Queue(maxsize=1)
        self.controller.manual_control_command = None
        self.controller.manual_control_received_at = None
        self.controller.manual_control_next_apply = 0.0
        self.controller.manual_control_input_fresh = False
        self.world = FakeWorld(synchronous_mode=True)

    def change_mode(self, mode):
        self.controller._set_vehicle_control_mode(self.world, {
            "session_id": "session",
            "actor_id": 7,
            "mode": mode,
        })

    def test_manual_commands_are_applied_and_focus_loss_brakes(self):
        self.change_mode(MANUAL_CONTROL)
        self.controller.movement_control_queue.put_nowait({
            "throttle": 0.4,
            "brake": 0.0,
            "steer": -0.3,
            "hand_brake": False,
            "reverse": True,
            "focused": True,
        })

        self.controller._service_manual_vehicle_control()
        applied = self.vehicle.controls[-1]
        self.assertAlmostEqual(0.4, applied.throttle)
        self.assertAlmostEqual(-0.3, applied.steer)
        self.assertTrue(applied.reverse)

        self.controller.manual_control_next_apply = 0.0
        self.controller.movement_control_queue.put_nowait({"focused": False})
        self.controller._service_manual_vehicle_control()
        stopped = self.vehicle.controls[-1]
        self.assertEqual(0.0, stopped.throttle)
        self.assertEqual(1.0, stopped.brake)

    def test_stale_manual_input_activates_watchdog_brake(self):
        self.change_mode(MANUAL_CONTROL)
        self.controller.manual_control_command = {
            "throttle": 1.0,
            "focused": True,
        }
        self.controller.manual_control_received_at = 0.0
        self.controller.manual_control_next_apply = 0.0

        self.controller._service_manual_vehicle_control()

        stopped = self.vehicle.controls[-1]
        self.assertEqual(0.0, stopped.throttle)
        self.assertEqual(1.0, stopped.brake)

    def test_switching_manual_autopilot_and_back_is_ordered(self):
        self.change_mode(MANUAL_CONTROL)
        self.change_mode(AUTOPILOT)

        state = self.controller.database.vehicle_control_states[7]
        self.assertEqual(AUTOPILOT_FREE_ROAM, state["mode"])
        self.assertEqual([(True, 8000)], self.vehicle.autopilot_calls)
        self.assertEqual([True], self.traffic_manager.sync_modes)
        self.assertEqual([(7, True)], self.traffic_manager.auto_lane_changes)
        self.assertEqual([(7, 0)], self.traffic_manager.random_left_lane_changes)
        self.assertEqual([(7, 0)], self.traffic_manager.random_right_lane_changes)

        self.change_mode(MANUAL_CONTROL)
        self.assertEqual((False, 8000), self.vehicle.autopilot_calls[-1])
        self.assertEqual(
            MANUAL_CONTROL,
            self.controller.database.vehicle_control_states[7]["mode"],
        )

    def test_closing_manual_stops_vehicle_but_closing_autopilot_preserves_it(self):
        self.change_mode(MANUAL_CONTROL)
        self.controller.cleanup_movement_preview("closed")
        state = self.controller.database.vehicle_control_states[7]
        self.assertEqual(NO_CONTROL, state["mode"])
        self.assertEqual(1.0, self.vehicle.controls[-1].brake)

        self.controller.movement_preview_session_id = "session"
        self.controller.movement_preview_actor_id = 7
        self.controller.movement_control_queue = queue.Queue(maxsize=1)
        self.change_mode(AUTOPILOT)
        self.controller.cleanup_movement_preview("closed")
        self.assertEqual(
            AUTOPILOT_FREE_ROAM,
            self.controller.database.vehicle_control_states[7]["mode"],
        )
        self.assertNotIn((False, 8000), self.vehicle.autopilot_calls)

    def test_traffic_manager_tracks_world_sync_mode_without_ticking(self):
        self.change_mode(AUTOPILOT)
        self.world.settings.synchronous_mode = False

        self.controller._update_traffic_manager_sync(self.world)

        self.assertEqual([True, False], self.traffic_manager.sync_modes)


if __name__ == "__main__":
    unittest.main()
