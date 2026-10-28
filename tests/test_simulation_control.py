import queue
import threading
import unittest

from carla_server.carla_server import CarlaController
from simulation_dashboard import SimulationDashboardMonitor
from simulation_manager import SimulationManager


SYNC_SETTINGS = {"synchronous_mode": True}
ASYNC_SETTINGS = {"synchronous_mode": False}


class FakeDatabase:
    def __init__(self):
        self.lock = threading.Lock()
        self.simulation_event_queue = queue.Queue()
        self.simulation_control_state = None
        self.actual_simulation_settings = None
        self.proposed_simulation_settings = None


class FakeSettings:
    def __init__(self, synchronous_mode=True):
        self.synchronous_mode = synchronous_mode
        self.fixed_delta_seconds = 0.05
        self.no_rendering_mode = False
        self.substepping = True
        self.max_substep_delta_time = 0.01
        self.max_substeps = 5


class FakeSnapshot:
    def __init__(self, frame):
        self.frame = frame


class FakeWorld:
    def __init__(self, fail_tick=False, synchronous_mode=True):
        self.frame = 0
        self.tick_calls = 0
        self.fail_tick = fail_tick
        self.settings = FakeSettings(synchronous_mode)

    def tick(self, timeout=None):
        self.tick_calls += 1
        if self.fail_tick:
            raise RuntimeError("tick timeout")
        self.frame += 1
        return self.frame

    def get_snapshot(self):
        return FakeSnapshot(self.frame)

    def get_settings(self):
        return self.settings


def make_controller(manager):
    controller = object.__new__(CarlaController)
    controller.simulation_manager = manager
    controller.database = FakeDatabase()
    controller.dashboard_monitor = SimulationDashboardMonitor()
    controller.command_queue = queue.Queue()
    controller.priority_command_queue = queue.Queue()
    controller.shutdown_event = threading.Event()
    controller.running = True
    controller.thread = None
    controller._next_snapshot_check = 0.0
    return controller


class SimulationControlStateTests(unittest.TestCase):
    def test_connected_mode_reflects_world_and_defaults_to_external(self):
        manager = SimulationManager()

        manager.connect(ASYNC_SETTINGS)
        self.assertEqual(SimulationManager.ASYNC_RUNNING, manager.state)

        manager.settings_changed(SYNC_SETTINGS)
        self.assertEqual(SimulationManager.SYNC_EXTERNAL, manager.state)
        self.assertEqual(
            SimulationManager.EXTERNAL_CONTROLLER,
            manager.tick_controller,
        )

    def test_gui_run_pause_and_step_transitions(self):
        manager = SimulationManager()
        manager.connect(SYNC_SETTINGS)
        manager.set_tick_controller(SimulationManager.GUI_CONTROLLER)

        manager.start_running()
        self.assertTrue(manager.should_tick())
        manager.pause()
        self.assertEqual(SimulationManager.SYNC_GUI_PAUSED, manager.state)

        manager.step(3)
        manager.record_tick(10)
        self.assertEqual(2, manager.remaining_steps)
        manager.record_tick(11)
        manager.record_tick(12)
        self.assertEqual(SimulationManager.SYNC_GUI_PAUSED, manager.state)
        self.assertFalse(manager.should_tick())

    def test_step_n_can_be_cancelled(self):
        manager = SimulationManager()
        manager.connect(SYNC_SETTINGS)
        manager.set_tick_controller(SimulationManager.GUI_CONTROLLER)
        manager.step(100)

        manager.record_tick(1)
        manager.pause()

        self.assertEqual(0, manager.remaining_steps)
        self.assertEqual(SimulationManager.SYNC_GUI_PAUSED, manager.state)

    def test_async_mode_never_requests_gui_ticks(self):
        manager = SimulationManager()
        manager.connect(SYNC_SETTINGS)
        manager.set_tick_controller(SimulationManager.GUI_CONTROLLER)
        manager.start_running()

        manager.settings_changed(ASYNC_SETTINGS)

        self.assertEqual(SimulationManager.ASYNC_RUNNING, manager.state)
        self.assertFalse(manager.should_tick())

    def test_world_change_during_run_does_not_resume_previous_activity(self):
        manager = SimulationManager()
        manager.connect(SYNC_SETTINGS)
        manager.set_tick_controller(SimulationManager.GUI_CONTROLLER)
        manager.start_running()

        manager.settings_changed(SYNC_SETTINGS, preserve_activity=False)

        self.assertEqual(SimulationManager.SYNC_GUI_PAUSED, manager.state)
        self.assertFalse(manager.should_tick())

    def test_disconnect_and_reconnect_reset_local_controller(self):
        manager = SimulationManager()
        manager.connect(SYNC_SETTINGS)
        manager.set_tick_controller(SimulationManager.GUI_CONTROLLER)
        manager.start_running()

        manager.disconnect()
        manager.connect(SYNC_SETTINGS)

        self.assertEqual(SimulationManager.SYNC_EXTERNAL, manager.state)
        self.assertEqual(
            SimulationManager.EXTERNAL_CONTROLLER,
            manager.tick_controller,
        )


class CooperativeTickSchedulerTests(unittest.TestCase):
    def test_each_scheduler_service_executes_at_most_one_tick(self):
        manager = SimulationManager()
        manager.connect(SYNC_SETTINGS)
        manager.set_tick_controller(SimulationManager.GUI_CONTROLLER)
        manager.step(3)
        controller = make_controller(manager)
        world = FakeWorld()

        controller._service_simulation(world)

        self.assertEqual(1, world.tick_calls)
        self.assertEqual(2, manager.remaining_steps)

        manager.pause()
        controller._service_simulation(world)
        self.assertEqual(1, world.tick_calls)

    def test_tick_error_stops_the_local_scheduler(self):
        manager = SimulationManager()
        manager.connect(SYNC_SETTINGS)
        manager.set_tick_controller(SimulationManager.GUI_CONTROLLER)
        manager.start_running()
        controller = make_controller(manager)

        world = FakeWorld(fail_tick=True)
        controller._service_simulation(world)
        controller._service_simulation(world)

        self.assertEqual(SimulationManager.ERROR, manager.state)
        self.assertFalse(manager.should_tick())
        self.assertEqual("tick timeout", manager.error_message)
        self.assertEqual(1, world.tick_calls)

    def test_pause_has_priority_over_a_regular_command_backlog(self):
        manager = SimulationManager()
        manager.connect(SYNC_SETTINGS)
        manager.set_tick_controller(SimulationManager.GUI_CONTROLLER)
        manager.start_running()
        controller = make_controller(manager)
        for index in range(100):
            controller.command_queue.put(("regular", index))

        controller.pause_simulation()
        command, _ = controller._get_next_command(0.0)

        self.assertEqual("pause_simulation", command)

    def test_stop_control_has_priority_over_a_regular_command_backlog(self):
        controller = make_controller(SimulationManager())
        for index in range(100):
            controller.command_queue.put(("regular", index))

        controller.stop_simulation_control()
        command, _ = controller._get_next_command(0.0)

        self.assertEqual("stop_simulation_control", command)

    def test_run_rechecks_actual_synchronous_mode(self):
        manager = SimulationManager()
        manager.connect(SYNC_SETTINGS)
        manager.set_tick_controller(SimulationManager.GUI_CONTROLLER)
        controller = make_controller(manager)
        world = FakeWorld(synchronous_mode=False)

        controller._handle_tick_start_command(
            world,
            manager.start_running,
        )

        self.assertEqual(SimulationManager.ASYNC_RUNNING, manager.state)
        self.assertFalse(manager.should_tick())
        events = list(controller.database.simulation_event_queue.queue)
        self.assertTrue(any(
            event == "simulation_control_command_error"
            for event, _ in events
        ))

    def test_step_rechecks_actual_synchronous_mode_and_starts_when_valid(self):
        manager = SimulationManager()
        manager.connect(SYNC_SETTINGS)
        manager.set_tick_controller(SimulationManager.GUI_CONTROLLER)
        controller = make_controller(manager)
        world = FakeWorld(synchronous_mode=True)

        controller._handle_tick_start_command(world, manager.step, 3)

        self.assertEqual(SimulationManager.SYNC_GUI_STEPPING, manager.state)
        self.assertEqual(3, manager.remaining_steps)

    def test_non_transition_updates_do_not_grow_event_queue(self):
        manager = SimulationManager()
        manager.connect(SYNC_SETTINGS)
        controller = make_controller(manager)

        for frame in range(100):
            manager.record_observed_frame(frame)
            controller._publish_simulation_control_state(force=False)

        self.assertTrue(controller.database.simulation_event_queue.empty())
        self.assertEqual(
            99,
            controller.database.simulation_control_state["last_frame"],
        )

    def test_shutdown_stops_scheduler_without_waiting_for_backlog(self):
        class AliveThread:
            @staticmethod
            def is_alive():
                return True

        controller = make_controller(SimulationManager())
        controller.thread = AliveThread()
        for index in range(100):
            controller.command_queue.put(("regular", index))

        controller.shutdown()

        self.assertFalse(controller.running)
        self.assertTrue(controller.shutdown_event.is_set())


if __name__ == "__main__":
    unittest.main()
