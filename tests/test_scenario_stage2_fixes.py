import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from datastore.datastore import DataStorage
from scenario_management.capture import ScenarioCapture
from scenario_management.models import (
    ActorCategory,
    ActorOrigin,
    ActorOwnership,
)
from scenario_management.scenario_loader import ScenarioLoader
from scenario_management.scenario_restorer import ScenarioRestorer
from scenario_management.serialization import ScenarioSerializer
from scenario_management.validator import ScenarioValidator
from scenario_management.world_cleaner import ClearMode, WorldCleaner
from scenario_management.world_synchronizer import WorldSynchronizer
from tests.test_scenario_restoration import (
    FakeCarla,
    World,
    add_sensor,
    library,
)
from tests.test_scenario_serialization import (
    FakeActor,
    FakeWeather,
    FakeWorld,
    valid_document,
)
from tests.test_world_cleaner import (
    FakeActor as ClearActor,
    FakeWorld as ClearWorld,
)


class ScenarioStageTwoFixTests(unittest.TestCase):
    def setUp(self):
        self.serializer = ScenarioSerializer()
        self.validator = ScenarioValidator()
        self.loader = ScenarioLoader(self.serializer, self.validator)
        self.cleaner = WorldCleaner()
        self.restorer = ScenarioRestorer(
            self.loader,
            self.serializer,
            self.validator,
            self.cleaner,
            FakeCarla,
        )

    def write_document(self, document):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "scenario.json"
        path.write_text(
            self.serializer.serialize(document, "json"),
            encoding="utf-8",
        )
        return path

    def execute_load(self, document, world, database=None):
        database = database or DataStorage()
        plan = self.loader.prepare(
            self.write_document(document),
            world,
            ["Town03"],
            world.library,
            server_version="0.9.16",
        )
        result = self.restorer.execute(
            plan,
            True,
            world,
            database,
            world.library,
            ["Town03"],
            "0.9.16",
            lambda: set(),
            lambda _plan: None,
            lambda _target: self.fail("unexpected map change"),
            lambda _world, _data: None,
            lambda _world, _actor, _data, _parent: [],
            lambda active_world: WorldSynchronizer().synchronize(
                active_world,
                database,
                reason="stage2_test",
            ).to_dict(),
        )
        return result, database

    def test_adopted_external_origin_is_valid_and_saved(self):
        adopted = FakeActor(1, "vehicle.adopted")
        external = FakeActor(2, "vehicle.external")
        world = FakeWorld([adopted, external])
        database = DataStorage()
        database.add_actor(
            adopted,
            "Adopted vehicle",
            ownership=ActorOwnership.MANAGED,
            origin=ActorOrigin.ADOPTED_EXTERNAL,
        )
        database.add_actor(
            external,
            "External vehicle",
            ownership=ActorOwnership.EXTERNAL,
            origin=ActorOrigin.SERVER_DISCOVERED,
        )

        document = ScenarioCapture().capture(world, database, "Adoption")

        self.validator.validate(document)
        self.assertEqual(1, document.capture_report.saved)
        self.assertEqual(1, document.capture_report.skipped_external)
        self.assertEqual(ActorOrigin.ADOPTED_EXTERNAL, document.actors[0].origin)
        self.assertEqual(ActorOwnership.MANAGED, document.actors[0].ownership)

    def test_external_sensor_of_managed_actor_is_not_saved(self):
        parent = FakeActor(10, "vehicle.adopted")
        sensor = FakeActor(11, "sensor.camera.rgb", parent=parent)
        world = FakeWorld([parent, sensor])
        database = DataStorage()
        database.add_actor(
            parent,
            "Managed parent",
            ownership=ActorOwnership.MANAGED,
            origin=ActorOrigin.ADOPTED_EXTERNAL,
        )
        database.add_sensor(
            sensor,
            parent.id,
            "External camera",
            sensor.type_id,
            sensor.transform,
            {},
            ownership=ActorOwnership.EXTERNAL,
            origin=ActorOrigin.SERVER_DISCOVERED,
        )

        document = ScenarioCapture().capture(world, database, "Sensor scope")

        self.assertEqual([10], [item.captured_runtime_id for item in document.actors])
        self.assertEqual(1, document.capture_report.skipped_external)

    def test_managed_sensor_with_external_parent_is_omitted_as_dependency(self):
        parent = FakeActor(20, "vehicle.external")
        sensor = FakeActor(21, "sensor.camera.rgb", parent=parent)
        world = FakeWorld([parent, sensor])
        database = DataStorage()
        database.add_actor(
            parent,
            "External parent",
            ownership=ActorOwnership.EXTERNAL,
            origin=ActorOrigin.SERVER_DISCOVERED,
        )
        database.add_sensor(
            sensor,
            parent.id,
            "Managed camera",
            sensor.type_id,
            sensor.transform,
            {},
            ownership=ActorOwnership.MANAGED,
        )

        document = ScenarioCapture().capture(world, database, "Dependency")

        self.assertEqual([], document.actors)
        self.assertEqual(1, document.capture_report.skipped_external)
        self.assertEqual(1, document.capture_report.skipped_dependency)
        self.assertEqual(1, document.capture_report.skipped_unrestorable)

    def test_loaded_sensor_stays_managed_after_world_sync(self):
        document = valid_document()
        sensor_data = add_sensor(document)
        world = World(1, "Town03", library())

        result, database = self.execute_load(document, world)
        sensor_info = next(iter(database.sensors_db.sensors_db.values()))
        parent_info = next(iter(database.actors_db.actors_db.values()))

        self.assertEqual("success", result.report.status)
        self.assertEqual(ActorOwnership.MANAGED, sensor_info.ownership)
        self.assertEqual(ActorOrigin.SCENARIO_LOADED, sensor_info.origin)
        self.assertEqual(sensor_data["scenario_id"], sensor_info.scenario_id)
        self.assertEqual(sensor_data["user_name"], sensor_info.user_name)
        self.assertEqual(parent_info.carla_id, sensor_info.parent_id)
        WorldSynchronizer().synchronize(world, database, reason="refresh")
        self.assertEqual(
            ActorOwnership.MANAGED,
            next(iter(database.sensors_db.sensors_db.values())).ownership,
        )

    def test_clear_ignores_dead_proxy_and_preserves_external_static(self):
        class StaleProxyWorld(ClearWorld):
            def get_actors(self):
                return list(self.actors)

        managed = ClearActor(30, "vehicle.managed")
        external_static = ClearActor(31, "static.prop.box01")
        world = StaleProxyWorld([managed, external_static])
        database = DataStorage()
        database.add_actor(managed, "Managed")
        database.add_actor(
            external_static,
            "External static",
            ownership=ActorOwnership.EXTERNAL,
        )

        plan = self.cleaner.prepare_plan(
            world,
            database,
            ClearMode.SCENARIO_REPLACE,
        )
        report = self.cleaner.execute_plan(world, plan)

        self.assertEqual("success", report.status)
        self.assertFalse(managed.is_alive)
        self.assertTrue(external_static.is_alive)
        self.assertEqual(1, report.preserved_external_static)

    def test_registration_failure_destroys_unregistered_spawn(self):
        world = World(1, "Town03", library())
        database = DataStorage()

        with mock.patch.object(
            database,
            "add_actor",
            side_effect=RuntimeError("registration failed"),
        ):
            result, _ = self.execute_load(valid_document(), world, database)

        self.assertEqual("failed", result.report.status)
        self.assertEqual(1, result.report.failed)
        self.assertEqual({}, database.actors_db.actors_db)
        self.assertEqual([], world.get_actors())
        self.assertEqual(
            valid_document()["actors"][0]["scenario_id"],
            result.report.errors[0]["scenario_id"],
        )
        self.assertEqual(
            valid_document()["actors"][0]["user_name"],
            result.report.errors[0]["user_name"],
        )
        self.assertEqual("vehicle.test", result.report.errors[0]["blueprint_id"])
        self.assertIn("registration failed", result.report.errors[0]["message"])

    def test_save_load_save_preserves_logical_configuration(self):
        original = valid_document()
        add_sensor(original)
        world = World(1, "Town03", library())
        result, database = self.execute_load(original, world)
        world.get_weather = lambda: FakeWeather()
        world.get_settings = lambda: SimpleNamespace(
            synchronous_mode=False,
            fixed_delta_seconds=None,
            no_rendering_mode=False,
            substepping=True,
            max_substep_delta_time=0.01,
            max_substeps=10,
        )

        saved_again = ScenarioCapture().capture(
            world,
            database,
            "Round trip",
        )
        self.validator.validate(saved_again)
        original_by_id = {
            item["scenario_id"]: item for item in original["actors"]
        }
        saved_by_id = {
            item.scenario_id: item for item in saved_again.actors
        }

        self.assertEqual("success", result.report.status)
        self.assertEqual(set(original_by_id), set(saved_by_id))
        for scenario_id, before in original_by_id.items():
            after = saved_by_id[scenario_id]
            self.assertEqual(before["user_name"], after.user_name)
            self.assertEqual(before["blueprint_id"], after.blueprint_id)
            self.assertEqual(before["parent_scenario_id"], after.parent_scenario_id)
            self.assertEqual(before["blueprint_attributes"], after.blueprint_attributes)
            self.assertEqual(ActorOwnership.MANAGED, after.ownership)
        vehicle_after = saved_by_id[original["actors"][0]["scenario_id"]]
        vehicle_before = original["actors"][0]
        for field in (
            "mass",
            "max_rpm",
            "moi",
            "forward_gears",
            "torque_curve",
            "steering_curve",
            "wheels",
        ):
            self.assertEqual(vehicle_before["physics"][field], vehicle_after.physics[field])


if __name__ == "__main__":
    unittest.main()
