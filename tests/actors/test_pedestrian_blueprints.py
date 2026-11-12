import unittest
from types import SimpleNamespace

from core.controller import CarlaController
from core.contracts import RuntimeContext
from core.state_store import DataStorage
from gui.tabs.pedestrians import CustomPedestrianFrame
from gui.windows.model_picker import ChangeModelTop
from services.actors import ActorService
from utilities.functions import (
    filter_carla_id_by_type,
    get_pedestrian_blueprint_ids,
    pedestrian_blueprint_options,
    resolve_blueprint_selection,
    select_available_blueprint_id,
)


class FakeVariable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeBlueprint:
    def __init__(self, blueprint_id):
        self.id = blueprint_id

    def has_attribute(self, _name):
        return False


class FakeBlueprintLibrary:
    def __init__(self, blueprint_ids):
        self.blueprints = {
            blueprint_id: FakeBlueprint(blueprint_id)
            for blueprint_id in blueprint_ids
        }
        self.filter_patterns = []
        self.find_calls = []

    def filter(self, pattern):
        self.filter_patterns.append(pattern)
        if pattern != "walker.pedestrian.*":
            return []
        return list(self.blueprints.values())

    def find(self, blueprint_id):
        self.find_calls.append(blueprint_id)
        if blueprint_id not in self.blueprints:
            raise RuntimeError("blueprint not found")
        return self.blueprints[blueprint_id]


class FakeActor:
    next_id = 100

    def __init__(self, type_id):
        self.id = FakeActor.next_id
        FakeActor.next_id += 1
        self.type_id = type_id
        self.is_alive = True
        self.destroy_count = 0

    def destroy(self):
        self.destroy_count += 1
        self.is_alive = False


class FakeLocation:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = x
        self.y = y
        self.z = z


class FakeRotation:
    def __init__(self, pitch=0.0, yaw=0.0, roll=0.0):
        self.pitch = pitch
        self.yaw = yaw
        self.roll = roll


class FakeTransform:
    def __init__(self, location=None, rotation=None):
        self.location = location or FakeLocation()
        self.rotation = rotation or FakeRotation()


class FakeWorld:
    def __init__(self, actual_type_id=None, first_try_fails=False,
                 spawn_returns_none=False):
        self.actual_type_id = actual_type_id
        self.first_try_fails = first_try_fails
        self.spawn_returns_none = spawn_returns_none
        self.spawned_blueprint_ids = []
        self.spawn_transforms = []
        self.try_blueprints = []
        self.spawn_blueprints = []
        self.try_spawn_calls = 0
        self.spawn_calls = 0
        self.last_actor = None

    def _spawn(self, blueprint, transform):
        self.spawned_blueprint_ids.append(blueprint.id)
        self.spawn_transforms.append(transform)
        self.last_actor = FakeActor(self.actual_type_id or blueprint.id)
        return self.last_actor

    def try_spawn_actor(self, blueprint, transform):
        self.try_spawn_calls += 1
        self.try_blueprints.append(blueprint)
        if self.first_try_fails:
            self.first_try_fails = False
            return None
        return self._spawn(blueprint, transform)

    def spawn_actor(self, blueprint, transform):
        self.spawn_calls += 1
        self.spawn_blueprints.append(blueprint)
        if self.spawn_returns_none:
            return None
        return self._spawn(blueprint, transform)


class PedestrianBlueprintTests(unittest.TestCase):
    def test_blueprints_are_sorted_by_full_id(self):
        library = FakeBlueprintLibrary([
            "walker.pedestrian.0010",
            "walker.pedestrian.0002",
            "walker.pedestrian.0001",
        ])

        blueprint_ids = get_pedestrian_blueprint_ids(library)

        self.assertEqual([
            "walker.pedestrian.0001",
            "walker.pedestrian.0002",
            "walker.pedestrian.0010",
        ], blueprint_ids)
        self.assertEqual(["walker.pedestrian.*"], library.filter_patterns)

    def test_gui_selection_resolves_to_exact_blueprint_id(self):
        options = pedestrian_blueprint_options([
            "walker.pedestrian.0002",
            "walker.pedestrian.0001",
        ])
        frame = object.__new__(CustomPedestrianFrame)
        frame.actor_model = options
        frame.choose_model = FakeVariable("walker.pedestrian.0002")

        selected = frame._selected_blueprint_id()

        self.assertEqual("walker.pedestrian.0002", selected)
        self.assertEqual(
            selected,
            resolve_blueprint_selection(options, selected),
        )

    def test_sorting_does_not_change_selected_blueprint(self):
        unsorted_ids = [
            "walker.pedestrian.0003",
            "walker.pedestrian.0001",
            "walker.pedestrian.0002",
        ]

        selected = select_available_blueprint_id(
            unsorted_ids,
            "walker.pedestrian.0002",
        )

        self.assertEqual("walker.pedestrian.0002", selected)

    def test_spawn_uses_each_requested_pedestrian_model(self):
        blueprint_ids = [
            "walker.pedestrian.0001",
            "walker.pedestrian.0007",
        ]
        library = FakeBlueprintLibrary(blueprint_ids)
        world = FakeWorld()

        actors = [
            CarlaController._spawn_actor_checked(
                world,
                library,
                blueprint_id,
                transform=object(),
            )
            for blueprint_id in blueprint_ids
        ]

        self.assertEqual(blueprint_ids, library.find_calls)
        self.assertEqual(blueprint_ids, world.spawned_blueprint_ids)
        self.assertEqual(blueprint_ids, [actor.type_id for actor in actors])

    def test_spawn_registration_uses_active_episode_not_actor_world_proxy(self):
        blueprint_id = "walker.pedestrian.0001"
        library = FakeBlueprintLibrary([blueprint_id])
        world = FakeWorld()
        world.id = 42
        database = DataStorage()
        service = ActorService(database)
        results = []

        class StaleWorld:
            @property
            def id(self):
                raise RuntimeError("different episode")

        original_spawn = world.try_spawn_actor

        def spawn_with_stale_proxy(blueprint, transform):
            actor = original_spawn(blueprint, transform)
            actor.get_world = lambda: StaleWorld()
            return actor

        world.try_spawn_actor = spawn_with_stale_proxy
        context = RuntimeContext(None, database, world, library)

        service.spawn(
            context,
            {
                "name": "Walker",
                "blueprint_id": blueprint_id,
                "transform": object(),
            },
            None,
            None,
            lambda *args, **kwargs: results.append((args, kwargs)),
        )

        self.assertEqual(1, len(results))
        self.assertIsNone(results[0][1].get("error"))
        actor = world.last_actor
        info = database.actors_db.actors_db[actor.id]
        self.assertEqual(42, info.world_id)
        self.assertTrue(database.is_actor_managed(actor.id))

    def test_pedestrian_collision_fallback_raises_z_once(self):
        blueprint_id = "walker.pedestrian.0001"
        library = FakeBlueprintLibrary([blueprint_id])
        world = FakeWorld(first_try_fails=True)
        transform = FakeTransform(
            FakeLocation(1.0, 2.0, 3.0),
            FakeRotation(4.0, 5.0, 6.0),
        )

        actor = CarlaController._spawn_actor_checked(
            world,
            library,
            blueprint_id,
            transform,
        )

        self.assertEqual(blueprint_id, actor.type_id)
        self.assertEqual([blueprint_id], world.spawned_blueprint_ids)
        self.assertEqual(1, world.try_spawn_calls)
        self.assertEqual(1, world.spawn_calls)
        self.assertIs(world.try_blueprints[0], world.spawn_blueprints[0])
        fallback = world.spawn_transforms[0]
        self.assertIsNot(transform, fallback)
        self.assertEqual((1.0, 2.0, 5.0), (
            fallback.location.x,
            fallback.location.y,
            fallback.location.z,
        ))
        self.assertEqual((4.0, 5.0, 6.0), (
            fallback.rotation.pitch,
            fallback.rotation.yaw,
            fallback.rotation.roll,
        ))
        self.assertEqual(3.0, transform.location.z)

    def test_pedestrian_reports_failure_when_both_attempts_fail(self):
        blueprint_id = "walker.pedestrian.0001"
        world = FakeWorld(
            first_try_fails=True,
            spawn_returns_none=True,
        )

        with self.assertRaisesRegex(RuntimeError, "could not spawn"):
            CarlaController._spawn_actor_checked(
                world,
                FakeBlueprintLibrary([blueprint_id]),
                blueprint_id,
                FakeTransform(),
            )

        self.assertEqual(1, world.try_spawn_calls)
        self.assertEqual(1, world.spawn_calls)

    def test_pedestrian_without_collision_uses_original_transform(self):
        blueprint_id = "walker.pedestrian.0001"
        world = FakeWorld()
        transform = FakeTransform()

        actor = CarlaController._spawn_actor_checked(
            world,
            FakeBlueprintLibrary([blueprint_id]),
            blueprint_id,
            transform,
        )

        self.assertEqual(blueprint_id, actor.type_id)
        self.assertEqual(1, world.try_spawn_calls)
        self.assertEqual(0, world.spawn_calls)
        self.assertIs(transform, world.spawn_transforms[0])

    def test_collision_fallback_is_not_used_for_other_actor_types(self):
        for blueprint_id in ("vehicle.test", "static.prop.box01"):
            with self.subTest(blueprint_id=blueprint_id):
                world = FakeWorld(first_try_fails=True)
                with self.assertRaisesRegex(RuntimeError, "could not spawn"):
                    CarlaController._spawn_actor_checked(
                        world,
                        FakeBlueprintLibrary([blueprint_id]),
                        blueprint_id,
                        FakeTransform(),
                    )
                self.assertEqual(1, world.try_spawn_calls)
                self.assertEqual(0, world.spawn_calls)

    def test_pedestrian_spawn_error_reports_stage_blueprint_and_episode(self):
        blueprint_id = "walker.pedestrian.0001"
        library = FakeBlueprintLibrary([blueprint_id])
        world = FakeWorld()
        world.id = 17
        world.try_spawn_actor = lambda _blueprint, _transform: None

        def reject_spawn(_blueprint, _transform):
            raise RuntimeError("collision at requested transform")

        world.spawn_actor = reject_spawn
        database = DataStorage()
        results = []

        ActorService(database).spawn(
            RuntimeContext(None, database, world, library),
            {
                "name": "Walker",
                "blueprint_id": blueprint_id,
                "transform": FakeTransform(),
            },
            None,
            None,
            lambda *args, **kwargs: results.append((args, kwargs)),
        )

        message = str(results[0][1]["error"])
        self.assertIn("stage=blueprint_and_spawn", message)
        self.assertIn(f"blueprint='{blueprint_id}'", message)
        self.assertIn("episode=17", message)
        self.assertIn("collision at requested transform", message)

    def test_change_model_passes_and_spawns_exact_blueprint_id(self):
        selected_id = "walker.pedestrian.0009"
        callback_values = []
        dialog = object.__new__(ChangeModelTop)
        dialog.choose_model = FakeVariable(selected_id)
        dialog.choose_class = FakeVariable("Pedestrian")
        dialog.on_model_change_callback = (
            lambda selected_class, selected_model: callback_values.append(
                (selected_class, selected_model)
            )
        )
        dialog.destroy = lambda: None

        dialog.change_model()

        self.assertEqual([("pedestrian", selected_id)], callback_values)
        library = FakeBlueprintLibrary([selected_id])
        actor = CarlaController._spawn_actor_checked(
            FakeWorld(),
            library,
            selected_id,
            transform=object(),
            try_spawn=False,
        )
        self.assertEqual(selected_id, actor.type_id)

    def test_map_refresh_preserves_available_selection(self):
        frame = object.__new__(CustomPedestrianFrame)
        frame.database = SimpleNamespace(
            pedestrian_blueprint_ids=[
                "walker.pedestrian.0003",
                "walker.pedestrian.0002",
            ],
            blueprint_lib=None,
        )
        frame.actor_model = pedestrian_blueprint_options([
            "walker.pedestrian.0001",
            "walker.pedestrian.0002",
        ])
        frame.choose_model = FakeVariable("walker.pedestrian.0002")
        frame.actor_model_combobox = {}

        frame._refresh_blueprint_models(preserve_selection=True)

        self.assertEqual("walker.pedestrian.0002", frame.choose_model.get())
        self.assertEqual([
            "walker.pedestrian.0002",
            "walker.pedestrian.0003",
        ], frame.actor_model_combobox["values"])

        frame.database.pedestrian_blueprint_ids = [
            "walker.pedestrian.0004",
            "walker.pedestrian.0003",
        ]
        frame._refresh_blueprint_models(preserve_selection=True)
        self.assertEqual("walker.pedestrian.0003", frame.choose_model.get())

    def test_unavailable_blueprint_reports_clear_error(self):
        library = FakeBlueprintLibrary(["walker.pedestrian.0001"])

        with self.assertRaisesRegex(RuntimeError, "is unavailable"):
            CarlaController._spawn_actor_checked(
                FakeWorld(),
                library,
                "walker.pedestrian.9999",
                transform=object(),
            )

    def test_mismatched_spawn_is_destroyed_and_reported(self):
        requested_id = "walker.pedestrian.0001"
        world = FakeWorld(actual_type_id="walker.pedestrian.0002")
        library = FakeBlueprintLibrary([requested_id])

        with self.assertRaisesRegex(RuntimeError, "blueprint mismatch"):
            CarlaController._spawn_actor_checked(
                world,
                library,
                requested_id,
                transform=object(),
            )

        self.assertFalse(world.last_actor.is_alive)
        self.assertEqual(1, world.last_actor.destroy_count)

    def test_legacy_filter_returns_sorted_full_ids(self):
        library = FakeBlueprintLibrary([
            "walker.pedestrian.0004",
            "walker.pedestrian.0002",
        ])

        options = filter_carla_id_by_type(
            "walker",
            "pedestrian",
            library,
        )

        self.assertEqual([
            "walker.pedestrian.0002",
            "walker.pedestrian.0004",
        ], list(options))


if __name__ == "__main__":
    unittest.main()
