import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.controller import CarlaController
from gui.tabs.world import CustomWorldAndWeatherFrame
from movement.destination import confirm_destination, destination_result
from movement.session import MovementControlSessionManager
from preview.movement import LatestFramePublisher
from movement.vehicle import (
    AUTOPILOT_DESTINATION,
    AUTOPILOT_FREE_ROAM,
    initial_vehicle_control_state,
)
from tests.movement import test_walker_control as walker_test_support

FakeLocation = walker_test_support.FakeLocation
FakeWorld = walker_test_support.FakeWorld
from movement.walker import (
    WALKER_CONTROL_DESTINATION,
    WALKER_CONTROL_RANDOM,
)


class MovementSessionCompositionTests(unittest.TestCase):
    def test_session_keeps_injected_window_factory(self):
        window_factory = Mock()

        session = MovementControlSessionManager(
            SimpleNamespace(),
            window_factory=window_factory,
        )

        self.assertIs(window_factory, session.window_factory)


class MovementWorldTransitionTests(unittest.TestCase):
    def setUp(self):
        walker_test_support.WalkerControlTests.setUp(self)
        self.controller.world_transition_event = threading.Event()
        self.controller.command_queue = queue.Queue()
        self.world = FakeWorld([
            FakeLocation(8.0, 2.0),
            FakeLocation(12.0, 3.0),
        ])
        self.world.id = 1
        self.controller.database.world = self.world
        self.controller.database.world_id = 1
        self.controller.database.world_generation = 1

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

    def test_gui_command_queue_does_not_read_live_world_proxy(self):
        class StaleWorldProxy:
            @property
            def id(self):
                raise AssertionError("stale CARLA episode proxy was accessed")

        self.controller.database.world = StaleWorldProxy()
        self.controller.database.world_id = 1
        self.controller.database.world_generation = 3

        accepted = self.controller._queue_movement_command(
            "get_movement_navigation_snapshot",
            {"session_id": "session", "actor_id": 21},
        )

        self.assertTrue(accepted)
        _command, data = self.controller.command_queue.get_nowait()
        self.assertEqual((1, 3), (
            data["world_id"],
            data["world_generation"],
        ))

    def test_stale_destination_command_is_rejected_before_actor_proxy_access(self):
        class StaleActorProxy:
            def __getattr__(self, _name):
                raise AssertionError("stale CARLA actor proxy was accessed")

        self.controller.movement_preview_session_id = "session"
        self.controller.movement_preview_actor_id = 21
        self.controller.database.world_id = 1
        self.controller.database.world_generation = 4
        self.controller.database.actors_db.actors[21] = StaleActorProxy()
        self.controller.database.actors_db.actors_db = {
            21: SimpleNamespace(
                world_id=1,
                is_alive=True,
                type_id="walker.pedestrian.test",
            ),
        }

        with self.assertRaisesRegex(RuntimeError, "stale world episode"):
            self.controller._movement_destination_actor({
                "session_id": "session",
                "actor_id": 21,
                "world_id": 1,
                "world_generation": 3,
            })

    def test_new_world_command_is_stamped_and_old_command_is_stale(self):
        self.controller.database.world = SimpleNamespace(id=2)
        self.controller.database.world_id = 2
        self.controller.database.world_generation = 2
        accepted = self.controller._queue_movement_command(
            "set_walker_control_mode",
            {"session_id": "new-session", "actor_id": 21},
        )
        command, data = self.controller.command_queue.get_nowait()

        self.assertTrue(accepted)
        self.assertEqual(2, data["world_id"])
        self.assertEqual(2, data["world_generation"])
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
            "world_generation": 1,
            "walker_ai_state": {"movement_status": "Moving"},
        }))
        window = SimpleNamespace(
            set_walker_ai_state=Mock(),
        )
        session = object.__new__(MovementControlSessionManager)
        session.manager = SimpleNamespace(
            database=SimpleNamespace(
                lock=threading.Lock(),
                world_id=2,
                world_generation=2,
                movement_event_queue=events,
            )
        )
        session.session_id = "session"
        session.world_id = 1
        session.world_generation = 1
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
            world_id=1,
            world_generation=1,
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
            "core.controller.parse_weather_to_dict",
            return_value={"cloudiness": 0.0},
        ):
            recovered, recovered_library, episode_changed = (
                controller._recover_world_after_failed_change(1)
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


class WorkerWorldChangeCleanupTests(unittest.TestCase):
    class Vehicle:
        def __init__(self):
            self.id = 7
            self.is_alive = True
            self.autopilot_calls = []

        def set_autopilot(self, enabled, port):
            self.autopilot_calls.append(
                (enabled, port, threading.get_ident())
            )

    class WalkerController:
        def __init__(self):
            self.id = 81
            self.is_alive = True
            self.stop_threads = []
            self.destroy_threads = []

        def stop(self):
            self.stop_threads.append(threading.get_ident())

        def destroy(self):
            self.destroy_threads.append(threading.get_ident())
            self.is_alive = False

    def make_controller(self, vehicle_mode=None, walker_active=False):
        controller = object.__new__(CarlaController)
        vehicle = self.Vehicle()
        walker = SimpleNamespace(id=21, is_alive=True)
        vehicle_states = {}
        if vehicle_mode is not None:
            state = initial_vehicle_control_state()
            state.update({
                "mode": vehicle_mode,
                "control_mode": vehicle_mode,
            })
            vehicle_states[7] = state
        walker_states = {21: {"active": True}} if walker_active else {}
        controller.database = SimpleNamespace(
            lock=threading.Lock(),
            actors_db=SimpleNamespace(actors={7: vehicle, 21: walker}),
            vehicle_control_states=vehicle_states,
            walker_ai_states=walker_states,
            world_changed=False,
            world_change_error=None,
            scenario_event_queue=queue.Queue(),
            simulation_event_queue=queue.Queue(),
            movement_event_queue=queue.Queue(),
        )
        controller.world_transition_event = threading.Event()
        controller.world_changed_event = threading.Event()
        controller.shutdown_event = threading.Event()
        rebound_tm = SimpleNamespace(set_synchronous_mode=Mock())
        controller.rebound_traffic_manager = rebound_tm
        controller.client = SimpleNamespace(
            set_timeout=Mock(),
            get_trafficmanager=Mock(return_value=rebound_tm),
        )
        new_world = SimpleNamespace(
            id=2,
            get_blueprint_library=lambda: object(),
            get_actors=lambda: [],
            get_settings=lambda: SimpleNamespace(synchronous_mode=False),
        )
        controller.world_service = SimpleNamespace(
            load_with_retry=Mock(return_value=new_world),
            install=Mock(return_value={"synchronous_mode": False}),
        )
        controller.simulation_manager = SimpleNamespace(
            stop_local_ticks=Mock(),
        )
        controller.dashboard_monitor = SimpleNamespace(
            invalidate=Mock(),
            reset_world=Mock(),
            record_error=Mock(),
        )
        controller.cleanup_movement_preview = Mock()
        controller.cleanup_sensor_previews = Mock()
        controller.cleanup_preview = Mock()
        controller.sensor_status_errors = {}
        controller._publish_simulation_control_state = Mock()
        controller._perform_world_sync = Mock(return_value={})
        controller._queue_map_preview_generation = Mock()
        controller._cancel_vehicle_navigation = Mock()
        controller._apply_walker_fallback_control = Mock()
        controller.traffic_manager = None
        controller.traffic_manager_world_id = 1
        controller.vehicle_navigation_runtime = {}
        controller.vehicle_navigation_submission_counts = {}
        controller.vehicle_navigation_submission_sequence = 0
        controller.movement_navigation_map_cache = {"world_id": 1}
        walker_controller = self.WalkerController()
        controller.walker_ai_controllers = (
            {21: walker_controller} if walker_active else {}
        )
        controller.walker_ai_runtime = {21: {}} if walker_active else {}
        context = SimpleNamespace(
            world=SimpleNamespace(id=1),
            blueprint_library=object(),
        )
        return controller, context, vehicle, walker_controller

    def run_change_in_worker(self, controller, context):
        failure = []

        def run():
            try:
                controller._dispatch_change_world(context, "Town02")
            except Exception as error:  # pragma: no cover - assertion aid
                failure.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], failure)
        return worker.ident

    def test_change_world_disables_active_autopilot_in_worker(self):
        main_thread = threading.get_ident()
        for mode in (AUTOPILOT_FREE_ROAM, AUTOPILOT_DESTINATION):
            with self.subTest(mode=mode):
                controller, context, vehicle, _walker = (
                    self.make_controller(vehicle_mode=mode)
                )

                worker_thread = self.run_change_in_worker(
                    controller,
                    context,
                )

                self.assertEqual(
                    [(False, controller.TRAFFIC_MANAGER_PORT, worker_thread)],
                    vehicle.autopilot_calls,
                )
                self.assertNotEqual(main_thread, worker_thread)
                controller.cleanup_movement_preview.assert_called_once_with(
                    "world_changed",
                    strict=True,
                )

    def test_tm_debug_brackets_cleanup_and_load_world_rpc(self):
        controller, context, _vehicle, _walker = self.make_controller(
            vehicle_mode=AUTOPILOT_FREE_ROAM,
        )
        controller._tm_debug_event = Mock()

        self.run_change_in_worker(controller, context)

        events = [
            call.args[0]
            for call in controller._tm_debug_event.call_args_list
        ]
        expected = [
            "load_world_begin",
            "world_cleanup_begin",
            "vehicle_control_release_begin",
            "autopilot_disable_begin",
            "autopilot_disable_complete",
            "vehicle_control_release_complete",
            "tm_local_reference_cleanup",
            "world_cleanup_complete",
            "load_world_rpc_begin",
            "load_world_rpc_complete",
            "load_world_complete",
        ]
        positions = [events.index(event) for event in expected]
        self.assertEqual(sorted(positions), positions)

    def test_exclusive_tm_stops_before_load_world(self):
        controller, context, vehicle, _walker = self.make_controller(
            vehicle_mode=AUTOPILOT_FREE_ROAM,
        )
        events = []
        traffic_manager = SimpleNamespace(
            shut_down=Mock(side_effect=lambda: events.append("tm_shutdown")),
        )
        controller.TRAFFIC_MANAGER_PORT = 8051
        controller.traffic_manager_exclusive = True
        controller.traffic_manager_exclusive_owned = True
        controller.traffic_manager_owner_controller_id = id(controller)
        controller.traffic_manager = traffic_manager
        new_world = controller.world_service.load_with_retry.return_value
        controller.world_service.load_with_retry.side_effect = (
            lambda *_args: events.append("load_world") or new_world
        )

        self.run_change_in_worker(controller, context)

        self.assertEqual(["tm_shutdown", "load_world"], events)
        self.assertEqual([(False, 8051)], [
            (enabled, port)
            for enabled, port, _thread_id in vehicle.autopilot_calls
        ])
        traffic_manager.shut_down.assert_called_once_with()

    def test_exclusive_tm_shutdown_failure_cancels_load_world(self):
        controller, context, _vehicle, _walker = self.make_controller(
            vehicle_mode=AUTOPILOT_FREE_ROAM,
        )
        controller.TRAFFIC_MANAGER_PORT = 8051
        controller.traffic_manager_exclusive = True
        controller.traffic_manager_exclusive_owned = True
        controller.traffic_manager_owner_controller_id = id(controller)
        controller.traffic_manager = SimpleNamespace(
            shut_down=Mock(side_effect=RuntimeError("shutdown failed")),
        )

        self.run_change_in_worker(controller, context)

        controller.world_service.load_with_retry.assert_not_called()
        self.assertTrue(controller.world_changed_event.is_set())
        self.assertFalse(controller.world_transition_event.is_set())
        self.assertIn(
            "world transition was cancelled",
            controller.database.world_change_error,
        )

    def test_active_shared_tm_cancels_load_world_without_global_shutdown(self):
        controller, context, _vehicle, _walker = self.make_controller(
            vehicle_mode=AUTOPILOT_FREE_ROAM,
        )
        shared_tm = SimpleNamespace(shut_down=Mock())
        controller.traffic_manager_exclusive = False
        controller.traffic_manager = shared_tm

        self.run_change_in_worker(controller, context)

        controller.world_service.load_with_retry.assert_not_called()
        shared_tm.shut_down.assert_not_called()
        self.assertTrue(controller.world_changed_event.is_set())
        self.assertFalse(controller.world_transition_event.is_set())
        self.assertIn(
            "shared Traffic Manager",
            controller.database.world_change_error,
        )

    def test_movement_teardown_failure_cancels_load_world(self):
        controller, context, _vehicle, _walker = self.make_controller(
            vehicle_mode=AUTOPILOT_DESTINATION,
        )
        controller.cleanup_movement_preview.side_effect = RuntimeError(
            "movement preview teardown failed"
        )

        self.run_change_in_worker(controller, context)

        controller.world_service.load_with_retry.assert_not_called()
        self.assertTrue(controller.world_changed_event.is_set())
        self.assertFalse(controller.world_transition_event.is_set())
        self.assertFalse(controller.database.world_changed)
        self.assertIn(
            "movement preview teardown failed",
            controller.database.world_change_error,
        )

    def test_vehicle_control_teardown_failure_cancels_load_world(self):
        controller, context, vehicle, _walker = self.make_controller(
            vehicle_mode=AUTOPILOT_DESTINATION,
        )
        vehicle.set_autopilot = Mock(
            side_effect=RuntimeError("old episode rejected autopilot RPC")
        )

        self.run_change_in_worker(controller, context)

        controller.world_service.load_with_retry.assert_not_called()
        self.assertTrue(controller.world_changed_event.is_set())
        self.assertFalse(controller.world_transition_event.is_set())
        self.assertIn(
            "Could not disable autopilot",
            controller.database.world_change_error,
        )

    def test_change_world_drops_local_tm_and_navigation_runtime(self):
        controller, context, _vehicle, _walker = self.make_controller(
            vehicle_mode=AUTOPILOT_DESTINATION,
        )
        old_tm = SimpleNamespace(shut_down=Mock())
        controller.TRAFFIC_MANAGER_PORT = 8051
        controller.traffic_manager_exclusive = True
        controller.traffic_manager_exclusive_owned = True
        controller.traffic_manager_owner_controller_id = id(controller)
        controller.traffic_manager = old_tm
        controller.vehicle_navigation_runtime[7] = {"world_id": 1}
        controller.vehicle_navigation_submission_counts[7] = 2
        controller.vehicle_navigation_submission_sequence = 2

        self.run_change_in_worker(controller, context)

        self.assertIsNone(controller.traffic_manager)
        self.assertIsNone(controller.traffic_manager_world_id)
        controller.client.get_trafficmanager.assert_not_called()
        self.assertEqual({}, controller.vehicle_navigation_runtime)
        self.assertEqual({}, controller.vehicle_navigation_submission_counts)
        self.assertEqual(0, controller.vehicle_navigation_submission_sequence)
        self.assertIsNone(controller.movement_navigation_map_cache)
        old_tm.shut_down.assert_called_once_with()

    def test_no_old_actor_or_tm_rpc_runs_after_load_world(self):
        controller, context, vehicle, _walker = self.make_controller(
            vehicle_mode=AUTOPILOT_DESTINATION,
        )
        episode = {"loaded": False}

        class GuardedVehicle:
            id = vehicle.id

            @property
            def is_alive(self):
                if episode["loaded"]:
                    raise AssertionError("old actor accessed after load_world")
                return True

            def set_autopilot(self, enabled, port):
                if episode["loaded"]:
                    raise AssertionError("old actor RPC after load_world")

        controller.database.actors_db.actors[vehicle.id] = GuardedVehicle()
        controller.TRAFFIC_MANAGER_PORT = 8051
        controller.traffic_manager_exclusive = True
        controller.traffic_manager_exclusive_owned = True
        controller.traffic_manager_owner_controller_id = id(controller)
        controller.traffic_manager = SimpleNamespace(shut_down=Mock())
        new_world = controller.world_service.load_with_retry.return_value

        def load_world(*_args):
            self.assertEqual({}, controller.database.actors_db.actors)
            self.assertEqual({}, controller.database.vehicle_control_states)
            episode["loaded"] = True
            return new_world

        controller.world_service.load_with_retry.side_effect = load_world
        controller.client.get_trafficmanager.side_effect = AssertionError(
            "TM rebound during episode transition"
        )

        self.run_change_in_worker(controller, context)

        self.assertTrue(episode["loaded"])
        controller.client.get_trafficmanager.assert_not_called()

    def test_open_movement_camera_is_destroyed_before_load_world(self):
        controller, context, _vehicle, _walker = self.make_controller()
        episode = {"loaded": False}
        camera_events = []

        class GuardedCamera:
            id = 90
            is_listening = True

            @property
            def is_alive(self):
                if episode["loaded"]:
                    raise AssertionError("old camera accessed after load_world")
                return True

            def stop(self):
                if episode["loaded"]:
                    raise AssertionError("old camera stopped after load_world")
                camera_events.append("stop")

            def destroy(self):
                if episode["loaded"]:
                    raise AssertionError("old camera destroyed after load_world")
                camera_events.append("destroy")

        controller.cleanup_movement_preview = (
            CarlaController.cleanup_movement_preview.__get__(controller)
        )
        controller.movement_preview_camera = GuardedCamera()
        controller.movement_preview_publisher = SimpleNamespace(close=Mock())
        controller.movement_preview_session_id = "session"
        controller.movement_preview_actor_id = 7
        controller.movement_preview_world_id = 1
        controller.movement_preview_world_generation = 1
        controller.movement_control_queue = queue.Queue()
        new_world = controller.world_service.load_with_retry.return_value

        def load_world(*_args):
            episode["loaded"] = True
            return new_world

        controller.world_service.load_with_retry.side_effect = load_world

        self.run_change_in_worker(controller, context)

        self.assertEqual(["stop", "destroy"], camera_events)
        self.assertIsNone(controller.movement_preview_camera)
        self.assertTrue(episode["loaded"])

    def test_closed_movement_publisher_ignores_stale_camera_callback(self):
        controller, _context, _vehicle, _walker = self.make_controller()
        frame_queue = queue.Queue(maxsize=1)
        publisher = LatestFramePublisher(frame_queue)
        camera = SimpleNamespace(
            id=90,
            is_listening=True,
            is_alive=True,
            stop=Mock(),
            destroy=Mock(),
        )
        controller.cleanup_movement_preview = (
            CarlaController.cleanup_movement_preview.__get__(controller)
        )
        controller.movement_preview_camera = camera
        controller.movement_preview_publisher = publisher
        controller.movement_preview_session_id = "session"
        controller.movement_preview_actor_id = 7
        controller.movement_preview_world_id = 1
        controller.movement_preview_world_generation = 1
        controller.movement_control_queue = queue.Queue()

        controller.cleanup_movement_preview("world_changed", strict=True)
        publisher.publish(SimpleNamespace(
            raw_data=b"stale",
            width=1,
            height=1,
            frame=1,
        ))

        self.assertTrue(frame_queue.empty())
        camera.stop.assert_called_once_with()
        camera.destroy.assert_called_once_with()

    def test_stale_stop_preview_command_is_rejected(self):
        controller, _context, _vehicle, _walker = self.make_controller()
        controller.database.world_id = 2
        controller.database.world_generation = 2

        self.assertTrue(controller._movement_command_is_stale(
            "stop_movement_preview",
            {"world_id": 1, "world_generation": 1},
            SimpleNamespace(id=2),
        ))

    def test_change_world_stops_walker_ai_in_worker(self):
        controller, context, _vehicle, walker_controller = (
            self.make_controller(walker_active=True)
        )

        worker_thread = self.run_change_in_worker(controller, context)

        self.assertEqual([worker_thread], walker_controller.stop_threads)
        self.assertEqual([worker_thread], walker_controller.destroy_threads)
        self.assertEqual({}, controller.walker_ai_controllers)
        self.assertEqual({}, controller.walker_ai_runtime)

    def test_change_world_tolerates_missing_controlled_actor(self):
        controller, context, vehicle, _walker = self.make_controller(
            vehicle_mode=AUTOPILOT_FREE_ROAM,
        )
        controller.database.actors_db.actors.pop(vehicle.id)

        self.run_change_in_worker(controller, context)

        self.assertEqual([], vehicle.autopilot_calls)
        self.assertTrue(controller.world_changed_event.is_set())


class WorldChangeGuiBoundaryTests(unittest.TestCase):
    def test_world_change_waits_for_movement_preview_process(self):
        manager = SimpleNamespace(
            connected=True,
            disconnecting=False,
            closing=False,
            spectator_preview=SimpleNamespace(is_active=lambda: False),
            movement_control=SimpleNamespace(
                is_preview_process_active=lambda: True,
            ),
        )
        frame = SimpleNamespace(
            world_change_check=object(),
            controller=SimpleNamespace(manager=manager),
            winfo_exists=lambda: True,
            after=Mock(return_value="after-id"),
            _wait_for_spectator_close=Mock(),
            _finish_world_change=Mock(),
            _apply_world_after_spectator_close=Mock(),
        )

        CustomWorldAndWeatherFrame._wait_for_spectator_close(frame)

        frame.after.assert_called_once_with(
            100,
            frame._wait_for_spectator_close,
        )
        frame._apply_world_after_spectator_close.assert_not_called()

    def test_weather_refresh_uses_worker_cached_data_not_world_rpc(self):
        weather = {
            "cloudiness": 1.0,
            "precipitation": 2.0,
            "precipitation_deposits": 3.0,
            "wind_intensity": 4.0,
            "sun_azimuth_angle": 5.0,
            "sun_altitude_angle": 6.0,
            "fog_density": 7.0,
            "fog_distance": 8.0,
            "fog_falloff": 9.0,
            "wetness": 10.0,
            "scattering_intensity": 11.0,
            "mie_scattering_scale": 12.0,
            "rayleigh_scattering_scale": 13.0,
            "dust_storm": 14.0,
        }
        world = Mock()
        frame = SimpleNamespace(
            database=SimpleNamespace(
                lock=threading.Lock(),
                actual_weather_data=weather,
                world=world,
            )
        )
        for name in weather:
            setattr(frame, f"{name}_var", Mock())

        CustomWorldAndWeatherFrame.reset_weather(frame)

        world.get_weather.assert_not_called()
        for name, value in weather.items():
            variable = getattr(frame, f"{name}_var")
            self.assertEqual(value, variable.set.call_args_list[-1].kwargs["value"])


if __name__ == "__main__":
    unittest.main()
