import queue
import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from core.controller import CarlaController
from core.state_store import DataStorage
from models.scenarios import (
    ActorCategory,
    ActorOrigin,
    ActorOwnership,
    ConfigurationStatus,
)
from scenarios.classifier import ActorClassifier
from scenarios.cleaner import WorldCleaner
from scenarios.synchronization import WorldSynchronizer


class FakeTransform:
    def __init__(self, x=0.0):
        self.location = SimpleNamespace(x=x, y=0.0, z=0.0)
        self.rotation = SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0)


class FakeWorld:
    def __init__(self, world_id, actors=None):
        self.id = world_id
        self.actors = list(actors or [])
        for actor in self.actors:
            actor.world = self

    def get_actors(self):
        return list(self.actors)

    def get_snapshot(self):
        return SimpleNamespace(frame=123)


class FakeActor:
    def __init__(self, actor_id, type_id, parent=None, role_name="",
                 physics_available=True):
        self.id = actor_id
        self.type_id = type_id
        self.parent = parent
        self.attributes = {"role_name": role_name} if role_name else {}
        self.is_alive = True
        self.is_active = True
        self.world = None
        self.transform = FakeTransform(float(actor_id))
        self.physics_available = physics_available

    def get_world(self):
        return self.world

    def get_transform(self):
        return self.transform

    def get_location(self):
        return self.transform.location

    def get_velocity(self):
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)

    def get_acceleration(self):
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)

    def get_angular_velocity(self):
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)

    def get_physics_control(self):
        if not self.physics_available:
            raise RuntimeError("physics unavailable")
        return SimpleNamespace(mass=1000.0)


class BrokenIdActor(FakeActor):
    @property
    def id(self):
        raise RuntimeError("actor proxy expired")

    @id.setter
    def id(self, _value):
        pass


class ActorClassifierTests(unittest.TestCase):
    def test_categories_and_preview_lifecycle_markers(self):
        cases = (
            ("vehicle.test", ActorCategory.VEHICLE),
            ("walker.pedestrian.0001", ActorCategory.PEDESTRIAN),
            ("static.prop.box01", ActorCategory.STATIC_OBJECT),
            ("sensor.camera.depth", ActorCategory.SENSOR),
            ("controller.ai.walker", ActorCategory.WALKER_AI_CONTROLLER),
            ("traffic.traffic_light", ActorCategory.WORLD_INFRASTRUCTURE),
            ("custom.actor", ActorCategory.OTHER),
        )
        for type_id, expected in cases:
            with self.subTest(type_id=type_id):
                self.assertEqual(
                    expected,
                    ActorClassifier.classify(FakeActor(1, type_id)),
                )

        rgb = FakeActor(2, "sensor.camera.rgb")
        self.assertEqual(ActorCategory.SENSOR, ActorClassifier.classify(rgb))
        self.assertEqual(
            ActorCategory.INTERNAL_PREVIEW,
            ActorClassifier.classify(rgb, internal_actor_ids={2}),
        )
        spectator = FakeActor(
            3,
            "sensor.camera.rgb",
            role_name="spectator_preview",
        )
        self.assertEqual(
            ActorCategory.INTERNAL_PREVIEW,
            ActorClassifier.classify(spectator),
        )


class WorldSynchronizerTests(unittest.TestCase):
    def setUp(self):
        self.database = DataStorage()
        self.synchronizer = WorldSynchronizer()

    def sync(self, world, internal_ids=None):
        return self.synchronizer.synchronize(
            world,
            self.database,
            internal_actor_ids=internal_ids,
        )

    def test_empty_world_is_a_successful_noop(self):
        report = self.sync(FakeWorld(1))

        self.assertTrue(report.success)
        self.assertEqual(0, report.added)
        self.assertEqual({}, self.database.actors_db.actors_db)
        self.assertEqual({}, self.database.sensors_db.sensors_db)

    def test_external_vehicle_and_pedestrian_are_registered_read_only(self):
        vehicle = FakeActor(10, "vehicle.test")
        walker = FakeActor(11, "walker.pedestrian.0001")

        report = self.sync(FakeWorld(1, [vehicle, walker]))

        self.assertEqual(2, report.added)
        vehicle_info = self.database.actors_db.actors_db[10]
        walker_info = self.database.actors_db.actors_db[11]
        self.assertEqual("Vehicle_1", vehicle_info.user_name)
        self.assertEqual("Pedestrian_1", walker_info.user_name)
        self.assertEqual(ActorOwnership.EXTERNAL, vehicle_info.ownership)
        self.assertEqual(ActorOrigin.SERVER_DISCOVERED, walker_info.origin)
        self.assertEqual(ConfigurationStatus.PARTIAL,
                         vehicle_info.configuration_status)
        uuid.UUID(vehicle_info.scenario_id)
        self.assertNotIn(10, self.database.vehicle_control_states)
        self.assertNotIn(11, self.database.walker_ai_states)

    def test_external_sensor_preserves_parent_relationship(self):
        vehicle = FakeActor(20, "vehicle.test")
        sensor = FakeActor(21, "sensor.camera.rgb", parent=vehicle)

        self.sync(FakeWorld(1, [vehicle, sensor]))

        sensor_info = self.database.sensors_db.sensors_db[21]
        parent_info = self.database.actors_db.actors_db[20]
        self.assertEqual("Sensor_1", sensor_info.user_name)
        self.assertEqual(20, sensor_info.parent_runtime_id)
        self.assertEqual(parent_info.scenario_id,
                         sensor_info.parent_scenario_id)
        self.assertIsNone(sensor_info.attachment_type)
        self.assertIn("attachment_type", sensor_info.missing_fields)

    def test_refresh_preserves_name_uuid_ownership_and_has_no_duplicates(self):
        vehicle = FakeActor(30, "vehicle.test")
        world = FakeWorld(1, [vehicle])
        self.sync(world)
        info = self.database.actors_db.actors_db[30]
        original_id = info.scenario_id
        info.user_name = "My Car"
        info.ownership = ActorOwnership.MANAGED

        second = self.sync(world)

        refreshed = self.database.actors_db.actors_db[30]
        self.assertEqual(0, second.added)
        self.assertEqual(1, second.updated)
        self.assertEqual(original_id, refreshed.scenario_id)
        self.assertEqual("My Car", refreshed.user_name)
        self.assertEqual(ActorOwnership.MANAGED, refreshed.ownership)
        self.assertEqual(1, len(self.database.actors_db.actors_db))

    def test_reconnect_handoff_restores_managed_actor_without_proxy_reuse(self):
        original = FakeActor(31, "vehicle.test")
        world = FakeWorld(7, [original])
        self.sync(world)
        original_info = self.database.actors_db.actors_db[31]
        original_info.user_name = "Reconnect car"
        original_info.ownership = ActorOwnership.MANAGED

        state = self.database.export_reconnect_state(
            infrastructure_world_id=7,
            infrastructure_actor_ids={900, 901},
        )
        reconnected_database = DataStorage(reconnect_state=state)
        replacement_proxy = FakeActor(31, "vehicle.test")
        reconnected_world = FakeWorld(7, [replacement_proxy])

        report = self.synchronizer.synchronize(
            reconnected_world,
            reconnected_database,
        )

        restored = reconnected_database.actors_db.actors_db[31]
        self.assertEqual(1, report.added)
        self.assertEqual(ActorOwnership.MANAGED, restored.ownership)
        self.assertEqual("Reconnect car", restored.user_name)
        self.assertIs(replacement_proxy,
                      reconnected_database.actors_db.actors[31])
        self.assertIsNot(original,
                         reconnected_database.actors_db.actors[31])
        self.assertIn(31, reconnected_database.vehicle_control_states)
        self.assertEqual({900, 901},
                         reconnected_database.reconnect_infrastructure_actor_ids)
        clear_plan = WorldCleaner().prepare_plan(
            reconnected_world,
            reconnected_database,
            "managed",
            infrastructure_actor_ids=(900, 901),
        )
        self.assertEqual([31], [
            item.runtime_id for item in clear_plan.items
        ])

    def test_reconnect_handoff_is_rejected_for_a_different_episode(self):
        actor = FakeActor(32, "vehicle.test")
        self.sync(FakeWorld(7, [actor]))
        self.database.actors_db.actors_db[32].ownership = ActorOwnership.MANAGED
        state = self.database.export_reconnect_state()
        reconnected_database = DataStorage(reconnect_state=state)

        self.synchronizer.synchronize(
            FakeWorld(8, [FakeActor(32, "vehicle.test")]),
            reconnected_database,
        )

        self.assertEqual(
            ActorOwnership.EXTERNAL,
            reconnected_database.actors_db.actors_db[32].ownership,
        )

    def test_reconnect_handoff_rejects_reused_id_with_different_identity(self):
        original = FakeActor(
            33,
            "vehicle.test",
            role_name="gui-a-car",
        )
        self.sync(FakeWorld(7, [original]))
        self.database.actors_db.actors_db[33].ownership = ActorOwnership.MANAGED
        state = self.database.export_reconnect_state()
        reconnected_database = DataStorage(reconnect_state=state)
        reused_id = FakeActor(
            33,
            "vehicle.test",
            role_name="other-client-car",
        )

        self.synchronizer.synchronize(
            FakeWorld(7, [reused_id]),
            reconnected_database,
        )

        self.assertEqual(
            ActorOwnership.EXTERNAL,
            reconnected_database.actors_db.actors_db[33].ownership,
        )

    def test_handoff_is_local_to_one_gui_process(self):
        actor = FakeActor(34, "vehicle.test", role_name="gui-a-car")
        world = FakeWorld(7, [actor])
        self.sync(world)
        self.database.actors_db.actors_db[34].ownership = ActorOwnership.MANAGED
        gui_a_state = self.database.export_reconnect_state()

        gui_a_database = DataStorage(reconnect_state=gui_a_state)
        gui_b_database = DataStorage()
        gui_a_proxy = FakeActor(34, "vehicle.test", role_name="gui-a-car")
        gui_b_proxy = FakeActor(34, "vehicle.test", role_name="gui-a-car")
        self.synchronizer.synchronize(
            FakeWorld(7, [gui_a_proxy]),
            gui_a_database,
        )
        self.synchronizer.synchronize(
            FakeWorld(7, [gui_b_proxy]),
            gui_b_database,
        )

        self.assertEqual(
            ActorOwnership.MANAGED,
            gui_a_database.actors_db.actors_db[34].ownership,
        )
        self.assertEqual(
            ActorOwnership.EXTERNAL,
            gui_b_database.actors_db.actors_db[34].ownership,
        )

    def test_actor_added_by_other_client_is_external_on_next_sync(self):
        world = FakeWorld(7, [FakeActor(35, "vehicle.test")])
        self.sync(world)
        external = FakeActor(36, "walker.pedestrian.0001")
        external.world = world
        world.actors.append(external)

        report = self.sync(world)

        self.assertEqual(1, report.added)
        self.assertEqual(
            ActorOwnership.EXTERNAL,
            self.database.actors_db.actors_db[36].ownership,
        )

    def test_infrastructure_baseline_wins_over_managed_handoff_hint(self):
        static_actor = FakeActor(37, "static.prop.box01")
        self.sync(FakeWorld(7, [static_actor]))
        self.database.actors_db.actors_db[37].ownership = ActorOwnership.MANAGED
        state = self.database.export_reconnect_state()
        reconnected_database = DataStorage(reconnect_state=state)

        report = self.synchronizer.synchronize(
            FakeWorld(7, [FakeActor(37, "static.prop.box01")]),
            reconnected_database,
            infrastructure_actor_ids={37},
        )

        self.assertEqual(1, report.skipped_infrastructure)
        self.assertNotIn(37, reconnected_database.actors_db.actors_db)

    def test_initial_sync_reuses_matching_infrastructure_baseline(self):
        controller = object.__new__(CarlaController)
        controller.map_infrastructure_world_id = 9
        controller.map_infrastructure_actor_ids = {100}
        controller.map_infrastructure_map_name = None
        controller._capture_map_infrastructure = mock.Mock()
        controller._perform_world_sync = mock.Mock(return_value="report")
        world = FakeWorld(9, [FakeActor(100, "static.prop.box01")])

        result = controller._synchronize_initial_world(world)

        self.assertEqual("report", result)
        controller._capture_map_infrastructure.assert_not_called()
        controller._perform_world_sync.assert_called_once_with(
            world,
            reason="initial_connection",
            publish=False,
        )

    def test_infrastructure_capture_keeps_role_named_static_actor_visible(self):
        controller = object.__new__(CarlaController)
        controller.world_synchronizer = WorldSynchronizer()
        native_prop = FakeActor(101, "static.prop.box01")
        user_prop = FakeActor(
            102,
            "static.prop.box01",
            role_name="scenario-prop",
        )
        world = FakeWorld(10, [native_prop, user_prop])

        actor_ids = controller._capture_map_infrastructure(world)

        self.assertEqual({101}, actor_ids)

    def test_actor_removed_by_other_client_is_removed_from_registry(self):
        actor = FakeActor(40, "vehicle.test")
        self.sync(FakeWorld(1, [actor]))

        report = self.sync(FakeWorld(1))

        self.assertEqual([40], report.removed_actor_ids)
        self.assertNotIn(40, self.database.actors_db.actors_db)

    def test_episode_change_does_not_match_reused_runtime_id(self):
        first = FakeActor(50, "vehicle.test")
        self.sync(FakeWorld(1, [first]))
        first_scenario_id = self.database.actors_db.actors_db[50].scenario_id

        second = FakeActor(50, "vehicle.test")
        report = self.sync(FakeWorld(2, [second]))

        second_info = self.database.actors_db.actors_db[50]
        self.assertEqual(1, report.removed)
        self.assertEqual(1, report.added)
        self.assertNotEqual(first_scenario_id, second_info.scenario_id)
        self.assertEqual(2, second_info.world_id)

    def test_internal_preview_and_infrastructure_are_excluded(self):
        ghost = FakeActor(60, "vehicle.test")
        preview = FakeActor(
            61,
            "sensor.camera.rgb",
            role_name="spectator_preview",
        )
        regular_camera = FakeActor(62, "sensor.camera.rgb")
        traffic_light = FakeActor(63, "traffic.traffic_light")

        report = self.sync(
            FakeWorld(1, [ghost, preview, regular_camera, traffic_light]),
            internal_ids={60},
        )

        self.assertEqual(2, report.skipped_internal)
        self.assertEqual(1, report.skipped_infrastructure)
        self.assertEqual({62}, set(self.database.sensors_db.sensors_db))

    def test_missing_parent_and_unknown_control_remain_explicit(self):
        missing_parent = FakeActor(99, "vehicle.missing")
        sensor = FakeActor(71, "sensor.other.gnss", parent=missing_parent)
        vehicle = FakeActor(
            70,
            "vehicle.test",
            physics_available=False,
        )

        self.sync(FakeWorld(1, [vehicle, sensor]))

        vehicle_info = self.database.actors_db.actors_db[70]
        sensor_info = self.database.sensors_db.sensors_db[71]
        self.assertIn("control.autopilot_enabled",
                      vehicle_info.missing_fields)
        self.assertIn("physics", vehicle_info.missing_fields)
        self.assertEqual(ConfigurationStatus.UNRESTORABLE,
                         sensor_info.configuration_status)
        self.assertIn("parent", sensor_info.missing_fields)

        self.sync(FakeWorld(1, [vehicle, missing_parent, sensor]))
        restored_sensor = self.database.sensors_db.sensors_db[71]
        self.assertEqual(ConfigurationStatus.PARTIAL,
                         restored_sensor.configuration_status)
        self.assertNotIn("parent", restored_sensor.missing_fields)
        self.assertEqual(
            self.database.actors_db.actors_db[99].scenario_id,
            restored_sensor.parent_scenario_id,
        )

    def test_one_expired_actor_proxy_does_not_abort_inventory(self):
        valid = FakeActor(80, "walker.pedestrian.0001")
        broken = BrokenIdActor(81, "vehicle.test")

        report = self.sync(FakeWorld(1, [broken, valid]))

        self.assertEqual(1, report.added)
        self.assertEqual(1, len(report.errors))
        self.assertIn(80, self.database.actors_db.actors_db)


class OwnershipGuardTests(unittest.TestCase):
    def test_external_registration_does_not_authorize_mutation(self):
        database = DataStorage()
        synchronizer = WorldSynchronizer()
        actor = FakeActor(90, "vehicle.test")
        synchronizer.synchronize(FakeWorld(1, [actor]), database)
        controller = object.__new__(CarlaController)
        controller.database = database
        controller.command_queue = queue.Queue()
        controller.scenario_operation_lock = threading.Lock()
        controller.scenario_operation_pending = False

        self.assertFalse(controller.remove_actor(90))
        self.assertTrue(controller.command_queue.empty())
        event, data = database.scenario_event_queue.get_nowait()
        self.assertEqual("world_sync_operation_blocked", event)
        self.assertEqual(90, data["actor_id"])


if __name__ == "__main__":
    unittest.main()
