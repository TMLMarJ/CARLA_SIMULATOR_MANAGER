import copy
import unittest
from unittest import mock

from datastore.datastore import DataStorage
from scenario_management.capture import ScenarioCapture
from scenario_management.models import ActorOwnership
from scenario_management.scenario_loader import ScenarioLoader
from scenario_management.scenario_restorer import ScenarioRestorer
from scenario_management.serialization import ScenarioSerializer
from scenario_management.validator import ScenarioValidator
from scenario_management.world_cleaner import ClearMode, WorldCleaner
from tests.test_scenario_restoration import (
    Actor,
    FakeCarla,
    Library,
    World,
    library,
)
from tests.test_scenario_serialization import (
    FakeActor as CaptureActor,
    FakeWorld as CaptureWorld,
    valid_document,
)
from tests.test_world_cleaner import (
    FakeActor as ClearActor,
    FakeWorld as ClearWorld,
)


class ScenarioScopeCaptureTests(unittest.TestCase):
    def setUp(self):
        self.database = DataStorage()
        self.capture = ScenarioCapture()

    def capture_world(self, actors):
        return self.capture.capture(
            CaptureWorld(actors),
            self.database,
            "Managed scope",
        )

    def register(self, actor, ownership=ActorOwnership.MANAGED):
        self.database.add_actor(
            actor,
            f"Actor_{actor.id}",
            ownership=ownership,
        )

    def test_save_includes_managed_and_omits_external(self):
        managed = CaptureActor(1, "vehicle.managed")
        external = CaptureActor(2, "vehicle.external")
        world = CaptureWorld([managed, external])
        self.database.add_actor(managed, "Managed")
        self.database.add_actor(
            external,
            "External",
            ownership=ActorOwnership.EXTERNAL,
        )

        document = self.capture.capture(world, self.database, "Scope")

        self.assertEqual([1], [item.captured_runtime_id for item in document.actors])
        self.assertEqual(1, document.capture_report.skipped_external)

    def test_managed_static_object_is_saved(self):
        actor = CaptureActor(3, "static.prop.box01")
        world = CaptureWorld([actor])
        self.database.add_actor(actor, "Box")

        document = self.capture.capture(world, self.database, "Static")

        self.assertEqual(1, document.capture_report.saved)
        self.assertEqual("static_object", document.actors[0].category)

    def test_external_static_object_is_not_saved(self):
        actor = CaptureActor(4, "static.prop.box01")
        world = CaptureWorld([actor])
        self.database.add_actor(
            actor,
            "External box",
            ownership=ActorOwnership.EXTERNAL,
        )

        document = self.capture.capture(world, self.database, "Static")

        self.assertEqual([], document.actors)
        self.assertEqual(1, document.capture_report.skipped_external)

    def test_native_infrastructure_is_not_saved(self):
        light = CaptureActor(5, "traffic.traffic_light")

        document = self.capture_world([light])

        self.assertEqual([], document.actors)
        self.assertEqual(1, document.capture_report.skipped_infrastructure)


class ScenarioReplaceClearTests(unittest.TestCase):
    def setUp(self):
        self.database = DataStorage()
        self.cleaner = WorldCleaner()

    def register(self, actor, ownership):
        self.database.add_actor(
            actor,
            f"Actor_{actor.id}",
            ownership=ownership,
        )

    def test_removes_managed_and_external_vehicles_and_pedestrians(self):
        actors = [
            ClearActor(10, "vehicle.managed"),
            ClearActor(11, "vehicle.external"),
            ClearActor(12, "walker.pedestrian.0001"),
            ClearActor(13, "walker.pedestrian.0002"),
        ]
        world = ClearWorld(actors)
        self.register(actors[0], ActorOwnership.MANAGED)
        self.register(actors[1], ActorOwnership.EXTERNAL)
        self.register(actors[2], ActorOwnership.MANAGED)
        self.register(actors[3], ActorOwnership.EXTERNAL)

        plan = self.cleaner.prepare_plan(
            world,
            self.database,
            ClearMode.SCENARIO_REPLACE,
        )
        report = self.cleaner.execute_plan(world, plan)

        self.assertEqual({10, 11, 12, 13}, {
            item.runtime_id for item in plan.items
        })
        self.assertEqual(2, report.removed_vehicles)
        self.assertEqual(2, report.removed_pedestrians)

    def test_preserves_external_static_object(self):
        external_static = ClearActor(14, "static.prop.box01")
        world = ClearWorld([external_static])
        self.register(external_static, ActorOwnership.EXTERNAL)

        plan = self.cleaner.prepare_plan(
            world,
            self.database,
            ClearMode.SCENARIO_REPLACE,
        )
        report = self.cleaner.execute_plan(world, plan)

        self.assertEqual([], plan.items)
        self.assertTrue(external_static.is_alive)
        self.assertEqual(1, report.preserved_external_static)

    def test_dependent_sensor_and_walker_controller_are_planned(self):
        walker = ClearActor(15, "walker.pedestrian.0001")
        sensor = ClearActor(16, "sensor.camera.rgb", parent=walker)
        controller = ClearActor(17, "controller.ai.walker", parent=walker)
        world = ClearWorld([walker, sensor, controller])

        plan = self.cleaner.prepare_plan(
            world,
            self.database,
            ClearMode.SCENARIO_REPLACE,
        )

        self.assertEqual({15, 16, 17}, {
            item.runtime_id for item in plan.items
        })


class ScenarioScopeRestorationTests(unittest.TestCase):
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
        self.database = DataStorage()
        self.paths = []

    def write(self, document):
        import tempfile
        from pathlib import Path

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "scenario.json"
        path.write_text(
            self.serializer.serialize(document, "json"),
            encoding="utf-8",
        )
        return path

    def prepare(self, document, world):
        return self.loader.prepare(
            self.write(document),
            world,
            ["Town03", "Town04"],
            world.library,
            server_version="0.9.16",
        )

    def execute(self, plan, world, change_world=None):
        return self.restorer.execute(
            plan,
            True,
            world,
            self.database,
            world.library,
            ["Town03", "Town04"],
            "0.9.16",
            lambda: set(),
            lambda _plan: None,
            change_world or (lambda _target: self.fail("unexpected map change")),
            lambda _world, _data: None,
            lambda _world, _actor, _data, _parent: [],
            lambda _world: {"success": True},
        )

    def test_same_map_preserved_external_static_is_not_clear_failure(self):
        external_static = Actor(30, "static.prop.box01", None)
        world = World(1, "Town03", library(), [external_static])

        result = self.execute(self.prepare(valid_document(), world), world)

        self.assertTrue(external_static.is_alive)
        self.assertEqual("success", result.report.status)
        self.assertEqual(
            1,
            result.report.clear_report["preserved_external_static"],
        )

    def test_map_change_does_not_execute_clear(self):
        old_world = World(1, "Town03", Library([]))
        target = World(2, "Town04", library())
        document = valid_document()
        document["world"]["map"] = "Town04"
        plan = self.prepare(document, old_world)
        self.cleaner.execute_plan = mock.MagicMock(
            side_effect=AssertionError("Clear must not execute after load_world")
        )

        result = self.execute(
            plan,
            old_world,
            lambda _target: (
                target,
                target.library,
                ["Town04"],
                "0.9.16",
            ),
        )

        self.assertEqual("success", result.report.status)
        self.cleaner.execute_plan.assert_not_called()

    def test_legacy_external_actor_is_skipped_without_spawn(self):
        document = copy.deepcopy(valid_document())
        document["actors"][0]["ownership"] = ActorOwnership.EXTERNAL
        world = World(1, "Town03", library())
        plan = self.prepare(document, world)

        result = self.execute(plan, world)

        self.assertEqual(0, plan.actor_count)
        self.assertEqual(1, plan.skipped_external)
        self.assertEqual([], world.spawn_log)
        self.assertEqual(1, result.report.scope_skipped)
        self.assertEqual("partial_success", result.report.status)


if __name__ == "__main__":
    unittest.main()
