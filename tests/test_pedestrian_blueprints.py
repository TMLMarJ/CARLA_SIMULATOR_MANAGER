import unittest
from types import SimpleNamespace

from carla_server.carla_server import CarlaController
from gui_contents.change_model_top import ChangeModelTop
from gui_contents.pedestrians import CustomPedestrianFrame
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


class FakeWorld:
    def __init__(self, actual_type_id=None):
        self.actual_type_id = actual_type_id
        self.spawned_blueprint_ids = []
        self.last_actor = None

    def _spawn(self, blueprint, _transform):
        self.spawned_blueprint_ids.append(blueprint.id)
        self.last_actor = FakeActor(self.actual_type_id or blueprint.id)
        return self.last_actor

    def try_spawn_actor(self, blueprint, transform):
        return self._spawn(blueprint, transform)

    def spawn_actor(self, blueprint, transform):
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
