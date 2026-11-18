import queue
import unittest
from types import SimpleNamespace
from unittest import mock

from core.controller import CarlaController
from core.state_store import DataStorage
from gui.application import Manager
from models.scenarios import ActorOwnership


class FakeActor:
    def __init__(self, actor_id, type_id="vehicle.test"):
        self.id = actor_id
        self.type_id = type_id
        self.parent = None
        self.attributes = {}
        self.is_alive = True
        self.is_active = True
        self.transform = SimpleNamespace(
            location=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            rotation=SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0),
        )
        self.world = None

    def get_world(self):
        return self.world

    def get_transform(self):
        return self.transform

    def get_location(self):
        return self.transform.location

    def get_velocity(self):
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)

    get_acceleration = get_velocity
    get_angular_velocity = get_velocity

    def get_physics_control(self):
        return SimpleNamespace(mass=1000.0)


class FakeMap:
    def __init__(self, name, spawn_points):
        self.name = name
        self._spawn_points = list(spawn_points)

    def get_spawn_points(self):
        return list(self._spawn_points)


class FakeWorld:
    def __init__(self, world_id, map_name, blueprints, spawn_points,
                 actors=None):
        self.id = world_id
        self._map = FakeMap(map_name, spawn_points)
        self._blueprints = blueprints
        self._actors = list(actors or ())
        for actor in self._actors:
            actor.world = self

    def get_map(self):
        return self._map

    def get_blueprint_library(self):
        return self._blueprints

    def get_weather(self):
        return SimpleNamespace(**{
            name: 0.0
            for name in (
                "cloudiness", "precipitation", "wind_intensity",
                "precipitation_deposits", "sun_azimuth_angle",
                "sun_altitude_angle", "fog_density", "fog_distance",
                "fog_falloff", "wetness", "scattering_intensity",
                "mie_scattering_scale", "rayleigh_scattering_scale",
                "dust_storm",
            )
        })

    def get_actors(self):
        return list(self._actors)

    def get_snapshot(self):
        return SimpleNamespace(frame=101)


class ExternalWorldRefreshTests(unittest.TestCase):
    def setUp(self):
        self.database = DataStorage()
        self.old_blueprints = mock.Mock(name="old_blueprints")
        self.old_blueprints.filter.return_value = []
        self.new_blueprints = mock.Mock(name="new_blueprints")
        self.new_blueprints.filter.return_value = []
        self.old_actor = FakeActor(7)
        self.old_world = FakeWorld(
            11,
            "/Game/Carla/Maps/Town01",
            self.old_blueprints,
            ["old-spawn"],
            [self.old_actor],
        )
        self.new_actor = FakeActor(7)
        self.new_world = FakeWorld(
            12,
            "/Game/Carla/Maps/Town02",
            self.new_blueprints,
            ["new-spawn-a", "new-spawn-b"],
            [self.new_actor],
        )
        self.database.world = self.old_world
        self.database.world_id = 11
        self.database.world_generation = 4
        self.database.blueprint_lib = self.old_blueprints
        self.database.available_spawn_points = ["old-spawn"]

        self.controller = CarlaController(self.database)
        self.controller.client = mock.Mock()
        self.controller.client.get_world.return_value = self.new_world
        self.controller.client.get_available_maps.return_value = [
            "/Game/Carla/Maps/Town01",
            "/Game/Carla/Maps/Town02",
        ]
        self.controller.simulation_manager = mock.Mock()
        self.controller.simulation_manager.get_current_settings.return_value = {
            "synchronous_mode": False,
        }
        self.controller.dashboard_monitor = mock.Mock()
        self.controller._service_dashboard = mock.Mock()
        self.controller._capture_map_infrastructure = mock.Mock(return_value=set())
        self.controller._queue_map_preview_generation = mock.Mock()
        self.controller._publish_simulation_control_state = mock.Mock()
        self.controller._shutdown_vehicle_controls = mock.Mock()
        self.controller._shutdown_walker_ai = mock.Mock()
        self.controller.cleanup_movement_preview = mock.Mock(return_value=True)
        self.controller.cleanup_sensor_previews = mock.Mock(return_value=True)
        self.controller.cleanup_preview = mock.Mock(return_value=True)
        self.controller.scenario_operation_lock = mock.MagicMock()
        self.controller.active_load_plan = None
        self.controller.active_clear_plan = None
        self.controller.scenario_operation_kind = None
        self.controller.scenario_operation_pending = False
        self.controller.load_execution_queued = False
        self.controller.clear_execution_queued = False

    def _last_sync_report(self):
        reports = []
        while True:
            try:
                event, data = self.database.scenario_event_queue.get_nowait()
            except queue.Empty:
                break
            if event == "world_sync_completed":
                reports.append(data)
        return reports[-1]

    def test_refresh_without_episode_change_preserves_generation_and_resources(self):
        self.controller.client.get_world.return_value = self.old_world
        self.controller._perform_world_sync = mock.Mock(return_value={
            "added": 0,
            "updated": 1,
            "removed": 0,
            "errors": [],
        })

        world, blueprints = self.controller._refresh_world_from_server(
            self.old_world,
            self.old_blueprints,
        )

        self.assertIs(self.old_world, world)
        self.assertIs(self.old_blueprints, blueprints)
        self.assertEqual(4, self.database.world_generation)
        self.controller.cleanup_movement_preview.assert_not_called()
        self.assertFalse(self._last_sync_report()["episode_changed"])

    def test_external_map_change_rebuilds_complete_state_and_drops_stale_ids(self):
        self.controller.world_synchronizer.synchronize(
            self.old_world,
            self.database,
        )
        old_info = self.database.actors_db.actors_db[7]
        old_info.ownership = ActorOwnership.MANAGED
        old_scenario_id = old_info.scenario_id
        self.database.reconnect_actor_hints[(12, 7, "vehicle.test")] = {
            "identity": {},
        }

        world, blueprints = self.controller._refresh_world_from_server(
            self.old_world,
            self.old_blueprints,
        )

        self.assertIs(self.new_world, world)
        self.assertIs(self.new_blueprints, blueprints)
        self.assertEqual(12, self.database.world_id)
        self.assertGreater(self.database.world_generation, 4)
        self.assertIs(self.new_world, self.database.world)
        self.assertIs(self.new_blueprints, self.database.blueprint_lib)
        self.assertEqual(
            ["new-spawn-a", "new-spawn-b"],
            self.database.available_spawn_points,
        )
        refreshed = self.database.actors_db.actors_db[7]
        self.assertEqual(ActorOwnership.EXTERNAL, refreshed.ownership)
        self.assertNotEqual(old_scenario_id, refreshed.scenario_id)
        self.assertEqual({}, self.database.reconnect_actor_hints)
        self.controller.cleanup_movement_preview.assert_called_once()
        report = self._last_sync_report()
        self.assertTrue(report["episode_changed"])
        self.assertEqual(11, report["previous_world_id"])
        self.assertEqual(12, report["world_id"])

    def test_same_map_reload_is_detected_by_episode_id(self):
        self.new_world._map.name = self.old_world._map.name

        self.controller._refresh_world_from_server(
            self.old_world,
            self.old_blueprints,
        )

        self.assertTrue(self._last_sync_report()["episode_changed"])
        self.assertEqual(12, self.database.world_id)

    def test_refresh_converges_on_episode_that_changes_during_snapshot(self):
        newest_blueprints = mock.Mock(name="newest_blueprints")
        newest_blueprints.filter.return_value = []
        newest_world = FakeWorld(
            13,
            "/Game/Carla/Maps/Town03",
            newest_blueprints,
            ["newest-spawn"],
        )
        self.controller.client.get_world.side_effect = [
            self.new_world,
            newest_world,
            newest_world,
        ]

        world, blueprints = self.controller._refresh_world_from_server(
            self.old_world,
            self.old_blueprints,
        )

        self.assertIs(newest_world, world)
        self.assertIs(newest_blueprints, blueprints)
        self.assertEqual(13, self.database.world_id)
        self.assertEqual(["newest-spawn"], self.database.available_spawn_points)

    def test_shared_traffic_manager_is_not_reset(self):
        shared_tm = mock.Mock()
        self.controller.traffic_manager = shared_tm
        self.controller.traffic_manager_exclusive = False

        self.controller._refresh_world_from_server(
            self.old_world,
            self.old_blueprints,
        )

        shared_tm.shut_down.assert_not_called()
        self.assertIsNone(self.controller.traffic_manager)

    def test_rpc_failure_keeps_old_world_and_generation(self):
        self.controller.client.get_world.side_effect = RuntimeError("timeout")

        with self.assertRaisesRegex(RuntimeError, "timeout"):
            self.controller._refresh_world_from_server(
                self.old_world,
                self.old_blueprints,
            )

        self.assertIs(self.old_world, self.database.world)
        self.assertEqual(11, self.database.world_id)
        self.assertEqual(4, self.database.world_generation)
        self.controller.cleanup_movement_preview.assert_not_called()

    def test_snapshot_rpc_failure_does_not_install_partial_episode(self):
        self.controller.client.get_available_maps.side_effect = RuntimeError(
            "maps timeout"
        )

        with self.assertRaisesRegex(RuntimeError, "maps timeout"):
            self.controller._refresh_world_from_server(
                self.old_world,
                self.old_blueprints,
            )

        self.assertIs(self.old_world, self.database.world)
        self.assertIs(self.old_blueprints, self.database.blueprint_lib)
        self.assertEqual(["old-spawn"], self.database.available_spawn_points)
        self.assertEqual(4, self.database.world_generation)
        self.controller.cleanup_sensor_previews.assert_not_called()

    def test_gui_rejects_report_from_old_world_generation(self):
        manager = Manager.__new__(Manager)
        manager.database = self.database
        self.database.world_id = 12
        self.database.world_generation = 8

        self.assertFalse(manager._world_sync_report_is_current({
            "world_id": 12,
            "world_generation": 7,
        }))
        self.assertFalse(manager._world_sync_report_is_current({
            "world_id": 11,
            "world_generation": 8,
        }))
        self.assertTrue(manager._world_sync_report_is_current({
            "world_id": 12,
            "world_generation": 8,
        }))


if __name__ == "__main__":
    unittest.main()
