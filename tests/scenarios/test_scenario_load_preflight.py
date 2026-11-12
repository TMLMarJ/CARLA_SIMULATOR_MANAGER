import copy
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.controller import CarlaController
from scenarios.loader import (
    BlueprintAvailability,
    LoadReadiness,
    ScenarioLoader,
)
from scenarios.serialization import ScenarioSerializer
from scenarios.validation import ScenarioValidationError
from tests.scenarios.test_scenario_serialization import UUID_2, valid_document


class FakeAttribute:
    def __init__(self, attribute_type="String", modifiable=True):
        self.type = attribute_type
        self.is_modifiable = modifiable
        self.recommended_values = []


class FakeBlueprint:
    def __init__(self, blueprint_id, attributes=None):
        self.id = blueprint_id
        self.attributes = attributes or {}

    def has_attribute(self, name):
        return name in self.attributes

    def get_attribute(self, name):
        return self.attributes[name]

    def set_attribute(self, _name, _value):
        raise AssertionError("Preflight must not set blueprint attributes.")


class FakeBlueprintLibrary:
    def __init__(self, blueprints):
        self.blueprints = {item.id: item for item in blueprints}

    def find(self, blueprint_id):
        if blueprint_id not in self.blueprints:
            raise RuntimeError("blueprint not found")
        return self.blueprints[blueprint_id]


class FakeWorld:
    def __init__(self, world_id=12, map_name="/Game/Carla/Maps/Town03"):
        self.id = world_id
        self.map_name = map_name
        self.read_calls = []

    def get_map(self):
        self.read_calls.append("get_map")
        return SimpleNamespace(name=self.map_name)

    def spawn_actor(self, *_args, **_kwargs):
        raise AssertionError("Preflight must not spawn actors.")

    def try_spawn_actor(self, *_args, **_kwargs):
        raise AssertionError("Preflight must not spawn actors.")

    def apply_settings(self, *_args, **_kwargs):
        raise AssertionError("Preflight must not change settings.")


def blueprint_library(color_modifiable=True):
    return FakeBlueprintLibrary([
        FakeBlueprint("vehicle.test", {
            "color": FakeAttribute("RGBColor", color_modifiable),
        }),
        FakeBlueprint("sensor.camera.rgb", {
            "image_size_x": FakeAttribute("Int", True),
        }),
    ])


def add_sensor(document):
    sensor = copy.deepcopy(document["actors"][0])
    sensor.update({
        "scenario_id": UUID_2,
        "captured_runtime_id": 8,
        "user_name": "Sensor_1",
        "category": "sensor",
        "blueprint_id": "sensor.camera.rgb",
        "transform": {
            "space": "relative",
            "location": {"x": 1.0, "y": 0.0, "z": 2.0},
            "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
            "source": "gui",
        },
        "blueprint_attributes": {"image_size_x": "800"},
        "physics": None,
        "control": None,
        "parent_scenario_id": document["actors"][0]["scenario_id"],
        "attachment_type": "Rigid",
    })
    document["actors"].append(sensor)
    document["capture_report"]["saved"] = 2
    return sensor


class ScenarioLoadPreflightTests(unittest.TestCase):
    def setUp(self):
        self.loader = ScenarioLoader()
        self.world = FakeWorld()
        self.maps = ["/Game/Carla/Maps/Town03", "/Game/Carla/Maps/Town04"]

    def write_document(self, document, suffix=".json"):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / f"scenario{suffix}"
        path.write_text(
            ScenarioSerializer().serialize(
                document,
                "json" if suffix == ".json" else "yaml",
            ),
            encoding="utf-8",
        )
        return path

    def prepare(self, document, library=None, suffix=".json"):
        return self.loader.prepare(
            self.write_document(document, suffix),
            self.world,
            self.maps,
            library or blueprint_library(),
            server_version="0.9.16",
        )

    def test_reads_valid_json_and_yaml(self):
        json_plan = self.prepare(valid_document(), suffix=".json")
        yaml_plan = self.prepare(valid_document(), suffix=".yaml")
        self.assertEqual(LoadReadiness.WARNING, json_plan.status)
        self.assertEqual(json_plan.actor_count, yaml_plan.actor_count)
        self.assertEqual(json_plan.target_map, yaml_plan.target_map)

    def test_server_version_difference_warns_but_does_not_block_load(self):
        document = valid_document()
        path = self.write_document(document)

        plan = self.loader.prepare(
            path,
            self.world,
            self.maps,
            blueprint_library(),
            server_version="0.9.15",
        )

        self.assertTrue(plan.ready_for_execution)
        self.assertEqual(LoadReadiness.WARNING, plan.status)
        self.assertTrue(any(
            "differs from server version" in warning
            for warning in plan.warnings
        ))

    def test_rejects_unsupported_schema_version(self):
        document = valid_document()
        document["schema_version"] = 99
        with self.assertRaises(ScenarioValidationError):
            self.prepare(document)

    def test_missing_map_blocks_plan(self):
        document = valid_document()
        document["world"]["map"] = "Town99"
        plan = self.prepare(document)
        self.assertFalse(plan.map_available)
        self.assertEqual(LoadReadiness.BLOCKED, plan.status)

    def test_missing_blueprint_on_current_map_blocks_actor(self):
        plan = self.prepare(valid_document(), FakeBlueprintLibrary([]))
        self.assertEqual(BlueprintAvailability.MISSING, plan.actors[0].blueprint_status)
        self.assertEqual(LoadReadiness.BLOCKED, plan.status)

    def test_blueprint_on_target_map_is_unverified(self):
        document = valid_document()
        document["world"]["map"] = "Town04"
        plan = self.prepare(document, FakeBlueprintLibrary([]))
        self.assertEqual(
            BlueprintAvailability.UNVERIFIED,
            plan.actors[0].blueprint_status,
        )
        self.assertEqual(LoadReadiness.UNVERIFIED, plan.status)
        self.assertFalse(plan.ready_for_execution)

    def test_parent_is_ordered_before_attached_sensor(self):
        document = valid_document()
        sensor = add_sensor(document)
        document["actors"].reverse()
        plan = self.prepare(document)
        self.assertLess(
            plan.creation_order.index(sensor["parent_scenario_id"]),
            plan.creation_order.index(sensor["scenario_id"]),
        )

    def test_missing_parent_is_rejected_by_existing_validator(self):
        document = valid_document()
        sensor = add_sensor(document)
        sensor["parent_scenario_id"] = "33333333-3333-4333-8333-333333333333"
        with self.assertRaises(ScenarioValidationError):
            self.prepare(document)

    def test_read_only_attribute_is_reported_but_not_blocking(self):
        plan = self.prepare(valid_document(), blueprint_library(False))
        self.assertEqual(["color"], plan.actors[0].read_only_attributes)
        self.assertEqual(LoadReadiness.WARNING, plan.status)

    def test_unknown_control_state_is_not_inferred(self):
        document = valid_document()
        document["actors"][0]["control"]["autopilot_enabled"] = None
        plan = self.prepare(document)
        self.assertTrue(any(
            "Autopilot state is unknown" in warning
            for warning in plan.actors[0].warnings
        ))

    def test_preflight_does_not_call_world_mutators(self):
        plan = self.prepare(valid_document())
        self.assertEqual(["get_map"], self.world.read_calls)
        self.assertEqual(1, plan.actor_count)

    def test_plan_is_invalidated_after_episode_change(self):
        plan = self.prepare(valid_document())
        controller = CarlaController.__new__(CarlaController)
        controller.scenario_operation_lock = threading.Lock()
        controller.scenario_operation_kind = None
        controller.active_load_plan = plan
        controller.database = SimpleNamespace(
            scenario_event_queue=queue.Queue(),
        )
        changed_world = FakeWorld(world_id=plan.world_id + 1)
        self.assertTrue(
            controller._invalidate_load_plan_if_episode_changed(changed_world)
        )
        self.assertIsNone(controller.active_load_plan)
        event, payload = controller.database.scenario_event_queue.get_nowait()
        self.assertEqual("scenario_load_plan_invalidated", event)
        self.assertEqual("world_episode_changed", payload["reason"])


if __name__ == "__main__":
    unittest.main()
