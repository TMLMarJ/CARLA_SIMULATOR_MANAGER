import copy
import unittest
from pathlib import Path
from types import SimpleNamespace

from gui.application import Manager
from core.state_store import DataStorage
from gui.tabs.simulation import ScenarioSaveFrame
from models.scenarios import ActorOrigin, ActorOwnership
from scenarios.converters import (
    blueprint_attribute_current_value,
    blueprint_attributes_to_dict,
    blueprint_value_to_string,
)
from scenarios.paths import (
    sanitize_scenario_filename,
    scenario_path_for_name,
)
from scenarios.restorer import (
    ScenarioLoadReport,
    ScenarioRestorer,
)
from scenarios.cleaner import WorldCleaner
from scenarios.synchronization import WorldSynchronizer
from tests.scenarios.test_scenario_restoration import FakeCarla
from tests.scenarios.test_world_cleaner import FakeActor, FakeWorld


class Attribute:
    def __init__(self, value, kind):
        self.value = value
        self.type = kind
        self.is_modifiable = True


class Variable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class DelayedActor(FakeActor):
    def destroy(self):
        self.log.append(("destroy", self.id))
        self.pending_destroy_reads = 2
        return True


class DelayedWorld(FakeWorld):
    def get_actors(self):
        for actor in self.actors:
            pending = getattr(actor, "pending_destroy_reads", 0)
            if pending:
                actor.pending_destroy_reads -= 1
                if actor.pending_destroy_reads == 0:
                    actor.is_alive = False
        return super().get_actors()


class TransientSensorInventoryWorld(FakeWorld):
    def __init__(self, actors, hidden_sensor_id):
        super().__init__(actors)
        self.hidden_sensor_id = hidden_sensor_id
        self.hide_once = True

    def get_actors(self):
        actors = super().get_actors()
        if self.hide_once:
            self.hide_once = False
            return [
                actor for actor in actors
                if actor.id != self.hidden_sensor_id
            ]
        return actors


class ScenarioPathRegressionTests(unittest.TestCase):
    def test_name_updates_filename_and_preserves_directory_and_format(self):
        self.assertEqual(
            Path("C:/Scenarios/TestScenario2.json"),
            scenario_path_for_name(
                "C:/Scenarios/TestScenario.json",
                "TestScenario2",
                "JSON",
            ),
        )
        self.assertEqual(
            Path("C:/Scenarios/TestScenario2.yaml"),
            scenario_path_for_name(
                "C:/Scenarios/TestScenario.yaml",
                "TestScenario2",
                "YAML",
            ),
        )

    def test_windows_invalid_and_reserved_names_are_sanitized(self):
        self.assertEqual("Bad_Name________", sanitize_scenario_filename(
            'Bad<Name>:"/\\|?*.'
        ))
        self.assertEqual("CON_", sanitize_scenario_filename("CON"))

    def test_manual_filename_override_is_not_replaced_by_name_change(self):
        frame = ScenarioSaveFrame.__new__(ScenarioSaveFrame)
        frame.name_var = Variable("First")
        frame.path_var = Variable("C:/Scenarios/Custom.json")
        frame.format_var = Variable("JSON")
        frame._updating_path = False
        frame._path_filename_manual = False
        frame._overwrite_confirmed_path = None
        frame._on_path_changed()

        frame.name_var.set("Second")
        frame._on_name_changed()

        self.assertEqual("C:/Scenarios/Custom.json", frame.path_var.get())


class ClearConfirmationRegressionTests(unittest.TestCase):
    def test_clear_waits_for_server_confirmation(self):
        actor = DelayedActor(7, "vehicle.test")
        world = DelayedWorld([actor])
        database = SimpleNamespace(
            lock=__import__("threading").RLock(),
            actors_db=SimpleNamespace(actors_db={}, actors={}),
            sensors_db=SimpleNamespace(sensors_db={}, sensors={}),
        )
        cleaner = WorldCleaner()
        plan = cleaner.prepare_plan(world, database, "entire_world")
        report = cleaner.execute_plan(world, plan)

        self.assertEqual("success", report.status)
        self.assertEqual(1, report.removed)
        self.assertEqual(0, report.failed)

    def test_load_message_contains_clear_counts_and_failed_actor_reason(self):
        message = Manager._load_report_message({
            "status": "failed",
            "clear_report": {
                "planned_count": 3,
                "removed": 1,
                "already_missing": 1,
                "failed": 1,
                "errors": [{"runtime_id": 44, "message": "still alive"}],
            },
            "errors": [{"stage": "clear", "message": "Clear failed"}],
        })
        self.assertIn("Planned: 3", message)
        self.assertIn("Confirmed removed: 1", message)
        self.assertIn("Failed actor 44: still alive", message)


class SensorOwnershipRegressionTests(unittest.TestCase):
    def test_two_loaded_sensors_remain_managed_when_inventory_lags(self):
        parent = FakeActor(1, "vehicle.test")
        first = FakeActor(2, "sensor.camera.rgb", parent=parent)
        second = FakeActor(3, "sensor.lidar.ray_cast", parent=parent)
        world = TransientSensorInventoryWorld(
            [parent, first, second],
            hidden_sensor_id=second.id,
        )
        database = DataStorage()
        database.add_actor(
            parent,
            "Vehicle",
            ownership=ActorOwnership.MANAGED,
            origin=ActorOrigin.SCENARIO_LOADED,
        )
        for sensor, name in ((first, "Camera"), (second, "Lidar")):
            database.add_sensor(
                sensor,
                parent.id,
                name,
                sensor.type_id,
                sensor.transform,
                sensor.attributes,
                ownership=ActorOwnership.MANAGED,
                origin=ActorOrigin.SCENARIO_LOADED,
            )

        WorldSynchronizer().synchronize(
            world,
            database,
            reason="after_load_scenario",
        )

        self.assertEqual({2, 3}, set(database.sensors_db.sensors_db))
        self.assertTrue(database.is_sensor_managed(first.id))
        self.assertTrue(database.is_sensor_managed(second.id))

        WorldSynchronizer().synchronize(
            world,
            database,
            reason="refresh",
        )
        self.assertTrue(database.is_sensor_managed(second.id))


class ScenarioRestoreRegressionTests(unittest.TestCase):
    def setUp(self):
        self.restorer = ScenarioRestorer(
            None, None, None, None, FakeCarla
        )
        self.transform = {
            "space": "world",
            "location": {"x": 1.0, "y": 2.0, "z": 3.0},
            "rotation": {"pitch": 4.0, "yaw": 5.0, "roll": 6.0},
            "source": "server",
        }

    def test_vehicle_offset_is_load_only_and_does_not_accumulate(self):
        data = {
            "blueprint_id": "vehicle.test",
            "transform": copy.deepcopy(self.transform),
        }
        first = self.restorer._spawn_transform(data)
        second = self.restorer._spawn_transform(data)

        self.assertAlmostEqual(3.2, first.location.z)
        self.assertAlmostEqual(3.2, second.location.z)
        self.assertEqual(3.0, data["transform"]["location"]["z"])
        self.assertEqual(1.0, first.location.x)
        self.assertEqual(5.0, first.rotation.yaw)

    def test_non_vehicle_transform_has_no_offset(self):
        for blueprint_id in (
            "walker.pedestrian.0001",
            "static.prop.bench",
            "sensor.camera.rgb",
        ):
            result = self.restorer._spawn_transform({
                "blueprint_id": blueprint_id,
                "transform": copy.deepcopy(self.transform),
            })
            self.assertEqual(3.0, result.location.z)


class BlueprintAttributeRegressionTests(unittest.TestCase):
    def test_supported_types_convert_to_carla_text(self):
        color = SimpleNamespace(r=12, g=34, b=56)
        cases = (
            (Attribute(True, "Bool"), True, "true"),
            (Attribute(12, "Int"), "12", "12"),
            (Attribute(1.25, "Float"), "1.250", "1.25"),
            (Attribute("value", "String"), "value", "value"),
            (Attribute(color, "RGBColor"), color, "12,34,56"),
            (Attribute(color, "RGBColor"), "Color(r=12, g=34, b=56)",
             "12,34,56"),
            (Attribute(color, "RGBColor"), "Color(12, 34, 56)",
             "12,34,56"),
        )
        for attribute, value, expected in cases:
            with self.subTest(kind=attribute.type, value=value):
                self.assertEqual(
                    expected,
                    blueprint_value_to_string(attribute, value),
                )

    def test_current_rgb_value_does_not_use_as_str(self):
        attribute = Attribute(SimpleNamespace(r=1, g=2, b=3), "RGBColor")
        self.assertEqual("1,2,3", blueprint_attribute_current_value(attribute))

    def test_capture_serializes_runtime_color_as_csv(self):
        actor = SimpleNamespace(attributes={
            "color": SimpleNamespace(r=7, g=8, b=9),
            "enabled": True,
        })
        self.assertEqual(
            {"color": "7,8,9", "enabled": "true"},
            blueprint_attributes_to_dict(actor),
        )

    def test_invalid_type_is_not_silently_stringified(self):
        with self.assertRaisesRegex(ValueError, "String attribute"):
            blueprint_value_to_string(Attribute("", "String"), object())
        with self.assertRaisesRegex(ValueError, "not an integer"):
            blueprint_value_to_string(Attribute(0, "Int"), 1.5)

    def test_attribute_warning_prevents_false_full_success(self):
        report = ScenarioLoadReport(
            name="Scenario",
            source_path="scenario.json",
            source_map="Town01",
            target_map="Town01",
            map_changed=False,
            planned=1,
            restored=1,
            warnings=["Blueprint attribute was not applied."],
        )
        result = ScenarioRestorer._finish(report, None, None)
        self.assertEqual("partial_success", result.report.status)


if __name__ == "__main__":
    unittest.main()
