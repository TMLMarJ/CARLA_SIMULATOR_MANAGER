import unittest
from types import SimpleNamespace
from unittest import mock

from core.state_store import DataStorage
from gui.tabs import vehicles
from gui.tabs.vehicles import CustomVehicleFrame, VehicleTable
from gui.widgets.actor_preview import ActorPreviewFrame
from gui.widgets.dashboard import SimulationDashboardFrame
from models.scenarios import ActorOwnership
from scenarios.capture import ScenarioCapture
from scenarios.classifier import ActorClassifier
from scenarios.cleaner import ClearMode, WorldCleaner
from scenarios.restorer import ScenarioRestorer
from scenarios.serialization import ScenarioSerializer
from scenarios.synchronization import WorldSynchronizer
from services.actors import ActorService
from simulation.dashboard import SimulationDashboardMonitor
from tests.world.test_world_synchronization import FakeActor, FakeWorld
from tests.scenarios.test_scenario_serialization import (
    FakeActor as CaptureActor,
    FakeWorld as CaptureWorld,
)
from tests.scenarios.test_world_cleaner import (
    FakeActor as ClearActor,
    FakeWorld as ClearWorld,
)


class FakeVariable:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeVehicleTree:
    def __init__(self):
        self.rows = {}
        self.next_id = 0

    def insert(self, _parent, _index, values, tags=()):
        item = str(self.next_id)
        self.next_id += 1
        self.rows[item] = {"values": tuple(values), "tags": tuple(tags)}
        return item

    def item(self, item, **changes):
        row = self.rows[item]
        row.update(changes)
        return dict(row)


class FakeBlueprint:
    def __init__(self, blueprint_id):
        self.id = blueprint_id
        self.values = {"role_name": "default"}

    def get_attribute(self, name):
        return SimpleNamespace(
            id=name,
            type="String",
            is_modifiable=True,
            as_str=lambda: self.values[name],
            __str__=lambda: self.values[name],
        )

    def set_attribute(self, name, value):
        self.values[name] = str(value)


class FakeBlueprintLibrary:
    def __init__(self, blueprint):
        self.blueprint = blueprint

    def find(self, _blueprint_id):
        return self.blueprint


class EgoVehicleTests(unittest.TestCase):
    def make_spawn_frame(self, ego=False, configured_attributes=None):
        frame = object.__new__(CustomVehicleFrame)
        frame.vehicle_name_var = FakeVariable("Vehicle")
        frame.choose_class = FakeVariable("Car")
        frame.choose_model = FakeVariable("Model")
        frame.set_as_ego_vehicle = FakeVariable(ego)
        frame.configured_blueprint_attributes = dict(
            configured_attributes or {}
        )
        frame.configured_physics = None
        frame.pending_requests = {}
        frame.database = SimpleNamespace(
            blueprint_lib=object(),
            blueprint_new_data=None,
            ghost_physic=None,
        )
        frame.controller = SimpleNamespace(
            carla=SimpleNamespace(spawn_actor=mock.Mock(return_value=True))
        )
        frame._current_transform = mock.Mock(return_value=object())
        frame.destroy_ghost = mock.Mock()
        frame.reset_tab = mock.Mock()
        frame.spawn_ghost = mock.Mock()
        return frame

    @mock.patch.object(
        vehicles,
        "filter_carla_id_by_type",
        return_value={"Model": "vehicle.test.model"},
    )
    def test_spawn_regular_vehicle_preserves_standard_role(self, _filter):
        frame = self.make_spawn_frame(
            ego=False,
            configured_attributes={"role_name": "autopilot"},
        )

        frame.spawn_vehicle()

        data = frame.controller.carla.spawn_actor.call_args.args[0]
        self.assertEqual("autopilot", data["attributes"]["role_name"])

    @mock.patch.object(
        vehicles,
        "filter_carla_id_by_type",
        return_value={"Model": "vehicle.test.model"},
    )
    def test_spawn_allows_multiple_ego_vehicles_with_hero_role(self, _filter):
        frame = self.make_spawn_frame(ego=True)

        frame.spawn_vehicle()
        frame.spawn_vehicle()

        self.assertEqual(2, frame.controller.carla.spawn_actor.call_count)
        for call in frame.controller.carla.spawn_actor.call_args_list:
            self.assertEqual("hero", call.args[0]["attributes"]["role_name"])

    def test_hero_and_ego_roles_are_detected_from_runtime_attributes(self):
        for role_name in ("hero", "ego", " HERO "):
            with self.subTest(role_name=role_name):
                actor = SimpleNamespace(
                    type_id="vehicle.test.model",
                    attributes={"role_name": role_name},
                )
                self.assertTrue(ActorClassifier.is_ego_vehicle(actor))
        self.assertFalse(ActorClassifier.is_ego_vehicle(SimpleNamespace(
            type_id="vehicle.test.model",
            attributes={"role_name": "autopilot"},
        )))

    def test_world_sync_detects_external_ego_and_removes_stale_marker(self):
        database = DataStorage()
        synchronizer = WorldSynchronizer()
        actor = FakeActor(10, "vehicle.test", role_name="ego")
        world = FakeWorld(1, [actor])

        synchronizer.synchronize(world, database)

        info = database.actors_db.actors_db[10]
        self.assertTrue(info.is_ego_vehicle)
        self.assertEqual(ActorOwnership.EXTERNAL, info.ownership)

        world.actors = []
        synchronizer.synchronize(world, database)
        self.assertNotIn(10, database.actors_db.actors_db)

    def test_vehicle_table_marks_multiple_ego_without_replacing_status(self):
        table = object.__new__(VehicleTable)
        table.tree = FakeVehicleTree()
        table.database = DataStorage()

        for actor_id, status in ((1, "Managed"), (2, "External")):
            table.add_vehicle_to_table({
                "name": f"Vehicle_{actor_id}",
                "carla_id": actor_id,
                "type_id": "vehicle.test.model",
                "location": "0, 0, 0",
                "rotation": "0, 0, 0",
                "status": status,
                "is_ego_vehicle": True,
            })

        rows = list(table.tree.rows.values())
        self.assertEqual(2, len(rows))
        self.assertTrue(all(VehicleTable.EGO_TAG in row["tags"] for row in rows))
        self.assertEqual(["Managed", "External"], [row["values"][6] for row in rows])
        self.assertTrue(all(row["values"][0].endswith(" [EGO]") for row in rows))

    def test_dashboard_marks_gui_managed_and_external_ego(self):
        actors = [
            SimpleNamespace(
                id=1,
                type_id="vehicle.test.one",
                attributes={"role_name": "hero"},
            ),
            SimpleNamespace(
                id=2,
                type_id="vehicle.test.two",
                attributes={"role_name": "ego"},
            ),
        ]
        actor_stats, _sensor_stats = (
            SimulationDashboardMonitor.build_actor_and_sensor_statistics(
                actors,
                managed_actor_ids={1},
                managed_sensor_ids=set(),
                internal_actor_ids=set(),
                streaming_sensor_ids=set(),
                sensor_errors={},
                world_revision=1,
                updated_at=1.0,
            )
        )

        rows = SimulationDashboardFrame.actor_rows(actor_stats)
        self.assertEqual(2, rows["Vehicles"]["Ego"])
        self.assertEqual(1, rows["Vehicles"]["GUI Managed"])
        self.assertEqual(1, rows["Vehicles"]["External"])

    def test_dashboard_external_actor_list_labels_ego(self):
        dashboard = object.__new__(SimulationDashboardFrame)
        dashboard.external_actor_choices = {}
        dashboard.external_actor_var = FakeVariable("")
        dashboard.external_actor_combo = mock.Mock()
        dashboard._external_actor_selected = mock.Mock()

        dashboard._update_external_actors({
            "actors": [{
                "actor_id": 17,
                "type_id": "vehicle.test.model",
                "ownership": "External",
                "is_ego_vehicle": True,
            }]
        })

        label = next(iter(dashboard.external_actor_choices))
        self.assertTrue(label.endswith(" [EGO]"))
        self.assertEqual(label, dashboard.external_actor_var.get())

    def test_blueprint_replacement_preserves_exact_ego_role_only(self):
        ego_info = SimpleNamespace(blueprint_attributes={"role_name": "ego"})
        regular_info = SimpleNamespace(
            blueprint_attributes={"role_name": "autopilot"}
        )

        self.assertEqual(
            {"color": "1,2,3", "role_name": "ego"},
            ActorService.replacement_attributes(
                ego_info,
                {"color": "1,2,3", "role_name": "default"},
            ),
        )
        self.assertEqual(
            {"color": "1,2,3"},
            ActorService.replacement_attributes(
                regular_info,
                {"color": "1,2,3"},
            ),
        )

    def test_scenario_roundtrip_and_restore_preserve_multiple_ego_roles(self):
        document = {
            "actors": [
                {"scenario_id": "one", "blueprint_attributes": {"role_name": "hero"}},
                {"scenario_id": "two", "blueprint_attributes": {"role_name": "ego"}},
            ]
        }
        serializer = ScenarioSerializer()
        loaded = serializer.deserialize(serializer.serialize(document, "json"), "json")
        self.assertEqual(document, loaded)

        spawned_roles = []
        blueprint = FakeBlueprint("vehicle.test.model")
        world = SimpleNamespace()

        def spawn_actor(_blueprint, _transform, **_kwargs):
            role_name = blueprint.values["role_name"]
            spawned_roles.append(role_name)
            return SimpleNamespace(
                id=len(spawned_roles),
                type_id="vehicle.test.model",
                attributes={"role_name": role_name},
            )

        world.spawn_actor = spawn_actor
        restorer = object.__new__(ScenarioRestorer)
        restorer._spawn_transform = lambda _data: object()
        plan = SimpleNamespace(applicable_attributes={"role_name"})
        for role_name in ("hero", "ego"):
            restorer._spawn_one(
                world,
                FakeBlueprintLibrary(blueprint),
                {
                    "blueprint_id": "vehicle.test.model",
                    "blueprint_attributes": {"role_name": role_name},
                },
                None,
                plan,
            )

        self.assertEqual(["hero", "ego"], spawned_roles)

    def test_scenario_save_captures_only_managed_ego_roles(self):
        database = DataStorage()
        managed_hero = CaptureActor(1, "vehicle.test.hero")
        managed_hero.attributes["role_name"] = "hero"
        managed_ego = CaptureActor(2, "vehicle.test.ego")
        managed_ego.attributes["role_name"] = "ego"
        external_ego = CaptureActor(3, "vehicle.test.external")
        external_ego.attributes["role_name"] = "hero"
        world = CaptureWorld([managed_hero, managed_ego, external_ego])
        database.add_actor(managed_hero, "Hero")
        database.add_actor(managed_ego, "Ego")
        database.add_actor(
            external_ego,
            "External Ego",
            ownership=ActorOwnership.EXTERNAL,
        )

        document = ScenarioCapture().capture(world, database, "Ego scope")

        roles = [
            actor.blueprint_attributes["role_name"]
            for actor in document.actors
        ]
        self.assertCountEqual(["hero", "ego"], roles)
        self.assertEqual(1, document.capture_report.skipped_external)

    def test_scenario_load_clear_preserves_existing_external_ego(self):
        database = DataStorage()
        external_ego = ClearActor(
            10,
            "vehicle.test.external_ego",
            role_name="ego",
        )
        external_regular = ClearActor(11, "vehicle.test.external_regular")
        managed_ego = ClearActor(
            12,
            "vehicle.test.managed_ego",
            role_name="hero",
        )
        world = ClearWorld([external_ego, external_regular, managed_ego])
        database.add_actor(
            external_ego,
            "External Ego",
            ownership=ActorOwnership.EXTERNAL,
        )
        database.add_actor(
            external_regular,
            "External regular",
            ownership=ActorOwnership.EXTERNAL,
        )
        database.add_actor(managed_ego, "Managed Ego")

        plan = WorldCleaner().prepare_plan(
            world,
            database,
            ClearMode.SCENARIO_REPLACE,
        )

        self.assertEqual({11, 12}, {item.runtime_id for item in plan.items})
        self.assertTrue(any(
            entry.get("runtime_id") == 10
            and entry.get("reason") == "external_ego_preserved"
            for entry in plan.skipped
        ))

    def test_preview_container_keeps_fixed_geometry_when_content_changes(self):
        label = mock.Mock()
        preview = mock.Mock()
        with (
            mock.patch("tkinter.Frame.__init__", return_value=None) as frame_init,
            mock.patch.object(ActorPreviewFrame, "pack_propagate") as propagate,
            mock.patch("gui.widgets.actor_preview.tk.Label", return_value=label),
            mock.patch.object(ActorPreviewFrame, "bind"),
            mock.patch.object(ActorPreviewFrame, "update_image"),
            mock.patch.object(ActorPreviewFrame, "_schedule_refresh"),
        ):
            ActorPreviewFrame(object(), preview)

        self.assertEqual(280, frame_init.call_args.kwargs["width"])
        self.assertEqual(210, frame_init.call_args.kwargs["height"])
        propagate.assert_called_once_with(False)
        label.pack.assert_called_once_with(fill="both", expand=True)


if __name__ == "__main__":
    unittest.main()
