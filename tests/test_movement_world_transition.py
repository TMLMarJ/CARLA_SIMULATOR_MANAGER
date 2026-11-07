import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from carla_server.carla_server import CarlaController
from movement_control import MovementControlSessionManager
from movement_destination import confirm_destination, destination_result
from tests.test_walker_control import (
    FakeLocation,
    FakeWorld,
    WalkerControlTests,
)
from walker_control import (
    WALKER_CONTROL_DESTINATION,
    WALKER_CONTROL_RANDOM,
)


class MovementWorldTransitionTests(unittest.TestCase):
    def setUp(self):
        WalkerControlTests.setUp(self)
        self.controller.world_transition_event = threading.Event()
        self.controller.command_queue = queue.Queue()
        self.world = FakeWorld([
            FakeLocation(8.0, 2.0),
            FakeLocation(12.0, 3.0),
        ])
        self.world.id = 1
        self.controller.database.world = self.world

    def set_destination(self):
        destination = destination_result(
            self.walker.id,
            "pedestrian",
            self.world.id,
            {"x": 10.0, "y": 0.0, "z": 0.0},
            "unverified",
            "Test destination.",
        )
        self.controller.database.walker_ai_states[21]["destination"] = (
            confirm_destination(destination)
        )

    def set_mode(self, mode):
        self.controller._set_walker_control_mode(self.world, {
            "session_id": "session",
            "actor_id": 21,
            "mode": mode,
            "speed": 1.5,
        })

    def test_active_destination_is_not_serviced_during_world_transition(self):
        self.set_destination()
        self.set_mode(WALKER_CONTROL_DESTINATION)
        control_count = len(self.walker.controls)
        self.controller.world_transition_event.set()

        self.controller._service_walker_ai(
            self.world,
            SimpleNamespace(frame=2),
        )

        self.assertEqual(control_count, len(self.walker.controls))

    def test_active_random_walk_is_not_serviced_during_world_transition(self):
        self.set_mode(WALKER_CONTROL_RANDOM)
        control_count = len(self.walker.controls)
        self.controller.world_transition_event.set()

        self.controller._service_walker_ai(
            self.world,
            SimpleNamespace(frame=2),
        )

        self.assertEqual(control_count, len(self.walker.controls))

    def test_runtime_from_previous_world_is_not_applied_to_reused_actor_id(self):
        self.set_destination()
        self.set_mode(WALKER_CONTROL_DESTINATION)
        control_count = len(self.walker.controls)
        new_world = FakeWorld([])
        new_world.id = 2
        runtime = self.controller.walker_ai_runtime[21]
        runtime["next_check"] = 0.0

        self.controller._service_walker_ai(
            new_world,
            SimpleNamespace(frame=2),
        )

        self.assertNotIn(21, self.controller.walker_ai_runtime)
        self.assertEqual(control_count, len(self.walker.controls))

    def test_pending_movement_command_is_rejected_during_transition(self):
        self.controller.world_transition_event.set()

        accepted = self.controller._queue_movement_command(
            "set_walker_control_mode",
            {"session_id": "session", "actor_id": 21},
        )

        self.assertFalse(accepted)
        self.assertTrue(self.controller.command_queue.empty())

    def test_new_world_command_is_stamped_and_old_command_is_stale(self):
        self.controller.database.world = SimpleNamespace(id=2)
        accepted = self.controller._queue_movement_command(
            "set_walker_control_mode",
            {"session_id": "new-session", "actor_id": 21},
        )
        command, data = self.controller.command_queue.get_nowait()

        self.assertTrue(accepted)
        self.assertEqual(2, data["world_id"])
        self.assertFalse(
            self.controller._movement_command_is_stale(
                command,
                data,
                SimpleNamespace(id=2),
            )
        )
        self.assertTrue(
            self.controller._movement_command_is_stale(
                command,
                {**data, "world_id": 1},
                SimpleNamespace(id=2),
            )
        )

    def test_old_world_gui_event_is_ignored(self):
        events = queue.Queue()
        events.put(("walker_ai_state", {
            "session_id": "session",
            "world_id": 1,
            "walker_ai_state": {"movement_status": "Moving"},
        }))
        window = SimpleNamespace(
            set_walker_ai_state=Mock(),
        )
        session = object.__new__(MovementControlSessionManager)
        session.manager = SimpleNamespace(
            database=SimpleNamespace(
                lock=threading.Lock(),
                world=SimpleNamespace(id=2),
                movement_event_queue=events,
            )
        )
        session.session_id = "session"
        session.world_id = 1
        session.window = window
        session.closing = False
        session.actor_id = 21

        session._poll_controller_events()

        window.set_walker_ai_state.assert_not_called()

    def test_timeout_recovery_adopts_actual_server_episode(self):
        previous_world = SimpleNamespace(id=1)
        current_map = SimpleNamespace(get_spawn_points=lambda: ["spawn"])
        library = SimpleNamespace(filter=lambda _pattern: [])
        current_world = SimpleNamespace(
            id=2,
            get_blueprint_library=lambda: library,
            get_weather=lambda: object(),
            get_map=lambda: current_map,
        )
        cleared = []
        database = SimpleNamespace(
            lock=threading.Lock(),
            world=previous_world,
            actors_db=SimpleNamespace(clear=lambda: cleared.append("actors")),
            sensors_db=SimpleNamespace(clear=lambda: cleared.append("sensors")),
            vehicle_control_states={1: {}},
            walker_ai_states={21: {}},
            spawned_actor_id=21,
            sensor_event_queue=queue.Queue(),
        )
        controller = object.__new__(CarlaController)
        controller.database = database
        controller.client = SimpleNamespace(
            get_world=lambda: current_world,
            get_available_maps=lambda: ["Town01", "Town02"],
        )
        controller.simulation_manager = SimpleNamespace(
            get_current_settings=lambda _world: {"synchronous_mode": False},
            settings_changed=Mock(),
        )
        controller.dashboard_monitor = SimpleNamespace(reset_world=Mock())
        controller.cleanup_sensor_previews = Mock()
        controller.cleanup_preview = Mock()
        controller.sensor_status_errors = {7: "old"}
        controller._perform_world_sync = Mock(return_value={})
        controller._publish_simulation_control_state = Mock()

        with patch(
            "carla_server.carla_server.parse_weather_to_dict",
            return_value={"cloudiness": 0.0},
        ):
            recovered, recovered_library, episode_changed = (
                controller._recover_world_after_failed_change(previous_world)
            )

        self.assertIs(current_world, recovered)
        self.assertIs(library, recovered_library)
        self.assertTrue(episode_changed)
        self.assertIs(current_world, database.world)
        self.assertEqual({}, database.walker_ai_states)
        self.assertEqual(["actors", "sensors"], cleared)
        controller._perform_world_sync.assert_called_once_with(
            current_world,
            reason="map_change_recovery",
        )


if __name__ == "__main__":
    unittest.main()
