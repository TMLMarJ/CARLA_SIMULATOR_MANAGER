import json
import threading
import unittest

from simulation.dashboard import SimulationDashboardMonitor


class FakeActor:
    def __init__(self, actor_id, type_id, is_alive=True, listening=False,
                 parent=None, listening_error=None):
        self.id = actor_id
        self.type_id = type_id
        self.is_alive = is_alive
        self.parent = parent
        self.listening = listening
        self.listening_error = listening_error

    def is_listening(self):
        if self.listening_error is not None:
            raise RuntimeError(self.listening_error)
        return self.listening


class FakeTimestamp:
    def __init__(self, elapsed_seconds=12.5, delta_seconds=0.05):
        self.elapsed_seconds = elapsed_seconds
        self.delta_seconds = delta_seconds


class FakeSnapshot:
    def __init__(self, frame=42, elapsed_seconds=12.5, delta_seconds=0.05):
        self.frame = frame
        self.timestamp = FakeTimestamp(elapsed_seconds, delta_seconds)


class FakeMap:
    name = "/Game/Carla/Maps/Town03"


class FakeWorld:
    def __init__(self, actors=None, snapshot=None):
        self.actors = list(actors or [])
        self.snapshot = snapshot or FakeSnapshot()
        self.snapshot_calls = 0
        self.actor_calls = 0

    def get_map(self):
        return FakeMap()

    def get_snapshot(self):
        self.snapshot_calls += 1
        return self.snapshot

    def get_actors(self):
        self.actor_calls += 1
        return list(self.actors)


class FakeActorRegistry:
    def __init__(self, actor_ids=()):
        self.actors_db = {actor_id: object() for actor_id in actor_ids}


class FakeSensorRegistry:
    def __init__(self, sensor_ids=()):
        self.sensors_db = {sensor_id: object() for sensor_id in sensor_ids}


class FakeDatabase:
    def __init__(self, actor_ids=(), sensor_ids=()):
        self.lock = threading.Lock()
        self.actors_db = FakeActorRegistry(actor_ids)
        self.sensors_db = FakeSensorRegistry(sensor_ids)
        self.actual_simulation_settings = {
            "synchronous_mode": True,
            "fixed_delta_seconds": 0.05,
        }
        self.simulation_control_state = {
            "state": "Sync GUI Paused",
            "tick_controller": "gui",
        }
        self.simulation_snapshot = None
        self.actor_statistics = None
        self.sensor_statistics = None
        self.application_status = None


class FakeSimulationManager:
    def snapshot(self):
        return {
            "state": "Sync GUI Paused",
            "tick_controller": "gui",
        }


class FakeClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class ActorClassificationTests(unittest.TestCase):
    def test_every_actor_type_maps_to_one_category(self):
        classify = SimulationDashboardMonitor.classify_actor

        self.assertEqual("Vehicles", classify("vehicle.tesla.model3"))
        self.assertEqual("Walkers", classify("walker.pedestrian.0001"))
        self.assertEqual("Sensors", classify("sensor.camera.rgb"))
        self.assertEqual("Other", classify("traffic.traffic_light"))

    def test_ownership_uses_existing_ids_and_internal_ids_have_priority(self):
        classify = SimulationDashboardMonitor.classify_ownership

        self.assertEqual("GUI Managed", classify(1, {1}, set()))
        self.assertEqual("External", classify(2, {1}, set()))
        self.assertEqual("Internal", classify(1, {1}, {1}))

    def test_statistics_count_only_actors_present_on_server(self):
        actors = [
            FakeActor(1, "vehicle.audi.a2"),
            FakeActor(3, "walker.pedestrian.0001"),
            FakeActor(4, "sensor.camera.rgb"),
        ]

        actor_stats, sensor_stats = (
            SimulationDashboardMonitor.build_actor_and_sensor_statistics(
                actors,
                managed_actor_ids={1, 2},  # ID 2 was removed externally.
                managed_sensor_ids=set(),
                internal_actor_ids={4},
                streaming_sensor_ids={4},
                sensor_errors={},
                world_revision=1,
                updated_at=100.0,
            )
        )

        self.assertEqual(3, actor_stats["total"])
        self.assertNotIn(2, [actor["actor_id"] for actor in actor_stats["actors"]])
        self.assertEqual(1, actor_stats["ownership"]["GUI Managed"])
        self.assertEqual(1, actor_stats["ownership"]["External"])
        self.assertEqual(1, actor_stats["ownership"]["Internal"])
        self.assertEqual("Streaming", sensor_stats["sensors"][0]["status"])


class SensorStatusTests(unittest.TestCase):
    def test_status_uses_known_listener_and_preview_information(self):
        monitor = SimulationDashboardMonitor
        event_sensor = FakeActor(
            1,
            "sensor.other.collision",
            listening=True,
        )

        self.assertEqual(
            monitor.LISTENING,
            monitor.sensor_status(event_sensor, monitor.GUI_MANAGED, False),
        )
        self.assertEqual(
            monitor.LISTENING,
            monitor.sensor_status(event_sensor, monitor.GUI_MANAGED, True),
        )
        self.assertEqual(
            monitor.STREAMING,
            monitor.sensor_status(
                FakeActor(3, "sensor.camera.rgb", listening=True),
                monitor.GUI_MANAGED,
                True,
            ),
        )
        self.assertEqual(
            monitor.UNKNOWN,
            monitor.sensor_status(event_sensor, monitor.EXTERNAL, False),
        )
        self.assertEqual(
            monitor.INACTIVE,
            monitor.sensor_status(
                FakeActor(2, "sensor.camera.rgb", is_alive=False),
                monitor.GUI_MANAGED,
                False,
            ),
        )
        self.assertEqual(
            monitor.ERROR,
            monitor.sensor_status(
                event_sensor,
                monitor.GUI_MANAGED,
                False,
                error="preview failed",
            ),
        )

    def test_event_sensor_without_event_is_not_an_error(self):
        sensor = FakeActor(
            1,
            "sensor.other.collision",
            listening=True,
        )

        status = SimulationDashboardMonitor.sensor_status(
            sensor,
            SimulationDashboardMonitor.GUI_MANAGED,
            False,
        )

        self.assertEqual(SimulationDashboardMonitor.LISTENING, status)


class DashboardUpdateTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.wall_clock = FakeClock(1000.0)
        self.monitor = SimulationDashboardMonitor(
            monotonic_clock=self.clock,
            wall_clock=self.wall_clock,
        )
        self.database = FakeDatabase(actor_ids={1}, sensor_ids={2})
        self.world = FakeWorld([
            FakeActor(1, "vehicle.audi.a2"),
            FakeActor(2, "sensor.camera.rgb", listening=True, parent=FakeActor(1, "vehicle.audi.a2")),
        ])

    def update(self, force=False):
        self.monitor.update(
            self.world,
            self.database,
            FakeSimulationManager(),
            pending_commands=3,
            viewer_status="Running",
            internal_actor_ids=set(),
            streaming_sensor_ids={2},
            force=force,
        )

    def test_update_creates_serializable_snapshot_and_latest_statistics(self):
        self.monitor.reset_world(self.database, self.world)
        self.update(force=True)

        snapshot = self.database.simulation_snapshot
        self.assertEqual(42, snapshot["frame"])
        self.assertEqual(12.5, snapshot["elapsed_seconds"])
        self.assertEqual(0.05, snapshot["delta_seconds"])
        self.assertEqual("Town03", snapshot["map_name"])
        self.assertTrue(snapshot["synchronous_mode"])
        self.assertEqual("gui", snapshot["tick_controller"])
        self.assertEqual("Sync GUI Paused", snapshot["simulation_control_state"])
        self.assertEqual(2, self.database.actor_statistics["total"])
        self.assertEqual(1, self.database.sensor_statistics["total"])
        self.assertEqual(3, self.database.application_status["pending_commands"])
        json.dumps(snapshot)
        json.dumps(self.database.actor_statistics)
        json.dumps(self.database.sensor_statistics)
        json.dumps(self.database.application_status)

    def test_refresh_intervals_prevent_repeated_full_actor_queries(self):
        self.monitor.reset_world(self.database, self.world)
        self.update()
        self.update()

        self.assertEqual(1, self.world.actor_calls)
        self.assertEqual(1, self.world.snapshot_calls)

        self.clock.value = 0.25
        self.update()
        self.assertEqual(1, self.world.actor_calls)
        self.assertEqual(2, self.world.snapshot_calls)

        self.clock.value = 1.0
        self.update()
        self.assertEqual(2, self.world.actor_calls)

    def test_world_reset_invalidates_old_data_and_changes_revision(self):
        self.monitor.reset_world(self.database, self.world)
        self.update(force=True)
        old_revision = self.database.simulation_snapshot["world_revision"]

        new_world = FakeWorld([FakeActor(9, "vehicle.lincoln.mkz_2020")])
        self.monitor.reset_world(self.database, new_world)

        self.assertFalse(self.database.simulation_snapshot["valid"])
        self.assertTrue(self.database.actor_statistics["stale"])
        self.assertEqual(0, self.database.actor_statistics["total"])
        self.assertGreater(
            self.database.simulation_snapshot["world_revision"],
            old_revision,
        )

    def test_disconnect_marks_all_dashboard_data_stale(self):
        self.monitor.reset_world(self.database, self.world)
        self.update(force=True)

        self.monitor.disconnect(self.database, "Disconnected by user")

        self.assertTrue(self.database.simulation_snapshot["stale"])
        self.assertTrue(self.database.actor_statistics["stale"])
        self.assertTrue(self.database.sensor_statistics["stale"])
        self.assertEqual(
            "Disconnected",
            self.database.application_status["connection_status"],
        )
        self.assertEqual(
            "Stopped",
            self.database.application_status["controller_thread_status"],
        )

    def test_reconnect_replaces_old_world_data_with_a_new_revision(self):
        self.monitor.reset_world(self.database, self.world)
        self.update(force=True)
        previous_revision = self.database.simulation_snapshot["world_revision"]

        self.monitor.disconnect(self.database, "Connection lost")
        new_world = FakeWorld([
            FakeActor(20, "vehicle.tesla.model3"),
            FakeActor(21, "walker.pedestrian.0001"),
        ], FakeSnapshot(frame=1, elapsed_seconds=0.05, delta_seconds=0.05))
        self.world = new_world
        self.monitor.reset_world(self.database, new_world, "Reconnected")
        self.update(force=True)

        self.assertGreater(
            self.database.simulation_snapshot["world_revision"],
            previous_revision,
        )
        self.assertEqual(1, self.database.simulation_snapshot["frame"])
        self.assertEqual(2, self.database.actor_statistics["total"])
        self.assertEqual(
            {20, 21},
            {
                actor["actor_id"]
                for actor in self.database.actor_statistics["actors"]
            },
        )
        self.assertEqual(
            "Connected",
            self.database.application_status["connection_status"],
        )


if __name__ == "__main__":
    unittest.main()
