import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from datastore.datastore import DataStorage
from scenario_management.scenario_loader import ScenarioLoader
from scenario_management.scenario_restorer import ScenarioRestorer
from scenario_management.serialization import ScenarioSerializer
from scenario_management.validator import ScenarioValidator
from scenario_management.world_cleaner import WorldCleaner
from tests.test_scenario_serialization import FakePhysics, UUID_2, valid_document


class Value:
    def __init__(self, value, kind="String", modifiable=True):
        self.value = str(value)
        self.type = kind
        self.is_modifiable = modifiable

    def __str__(self):
        return self.value


class Blueprint:
    def __init__(self, blueprint_id, attributes=None):
        self.id = blueprint_id
        self.attributes = attributes or {}

    def has_attribute(self, name):
        return name in self.attributes

    def get_attribute(self, name):
        return self.attributes[name]

    def set_attribute(self, name, value):
        self.attributes[name].value = str(value)


class Library:
    def __init__(self, blueprints):
        self.items = {item.id: item for item in blueprints}

    def find(self, blueprint_id):
        if blueprint_id not in self.items:
            raise RuntimeError(f"missing blueprint {blueprint_id}")
        return self.items[blueprint_id]


class Location:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z


class Rotation:
    def __init__(self, pitch=0.0, yaw=0.0, roll=0.0):
        self.pitch, self.yaw, self.roll = pitch, yaw, roll


class Transform:
    def __init__(self, location=None, rotation=None):
        self.location = location or Location()
        self.rotation = rotation or Rotation()


class GearPhysicsControl:
    def __init__(self):
        self.ratio = self.down_ratio = self.up_ratio = 0.0


class FakeCarla:
    Location = Location
    Rotation = Rotation
    Transform = Transform
    Vector2D = lambda **values: SimpleNamespace(**values)
    Vector3D = lambda **values: SimpleNamespace(**values)
    GearPhysicsControl = GearPhysicsControl
    AttachmentType = SimpleNamespace(
        Rigid="Rigid",
        SpringArm="SpringArm",
        SpringArmGhost="SpringArmGhost",
    )


class Actor:
    def __init__(self, actor_id, type_id, world, transform=None, parent=None,
                 attributes=None, destroy_error=None):
        self.id = actor_id
        self.type_id = type_id
        self.world = world
        self.parent = parent
        self.transform = transform or Transform()
        self.attributes = dict(attributes or {})
        self.destroy_error = destroy_error
        self.is_alive = True
        self.is_active = True
        self.is_listening = type_id.startswith("sensor.")
        self.physics = copy.deepcopy(FakePhysics())
        self.autopilot = False

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
        return copy.deepcopy(self.physics)

    def apply_physics_control(self, physics):
        self.physics = copy.deepcopy(physics)

    def set_autopilot(self, enabled, _port):
        self.autopilot = bool(enabled)

    def start(self):
        pass

    def stop(self):
        self.is_listening = False

    def go_to_location(self, target):
        self.target = target

    def set_max_speed(self, speed):
        self.max_speed = speed

    def destroy(self):
        if self.destroy_error:
            raise RuntimeError(self.destroy_error)
        self.is_alive = False
        return True


class World:
    def __init__(self, world_id, map_name, library, actors=None,
                 fail_blueprints=None, spawn_log=None):
        self.id = world_id
        self.map_name = map_name
        self.library = library
        self.actors = list(actors or [])
        self.fail_blueprints = set(fail_blueprints or ())
        self.spawn_log = spawn_log if spawn_log is not None else []
        self.next_id = 100
        for actor in self.actors:
            actor.world = self

    def get_map(self):
        return SimpleNamespace(name=self.map_name)

    def get_actors(self):
        return [actor for actor in self.actors if actor.is_alive]

    def spawn_actor(self, blueprint, transform, **kwargs):
        if blueprint.id in self.fail_blueprints:
            raise RuntimeError(f"spawn failed for {blueprint.id}")
        actor = Actor(
            self.next_id,
            blueprint.id,
            self,
            transform,
            kwargs.get("attach_to"),
            {name: str(value) for name, value in blueprint.attributes.items()},
        )
        self.next_id += 1
        self.actors.append(actor)
        self.spawn_log.append((blueprint.id, actor.parent.id if actor.parent else None))
        return actor


def library(include_vehicle=True, include_sensor=True):
    blueprints = []
    if include_vehicle:
        blueprints.append(Blueprint("vehicle.test", {
            "color": Value("0,0,0", "RGBColor"),
        }))
    if include_sensor:
        blueprints.append(Blueprint("sensor.camera.rgb", {
            "image_size_x": Value("640", "Int"),
        }))
    return Library(blueprints)


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
    document["capture_report"]["saved"] = len(document["actors"])
    return sensor


class ScenarioRestorationTests(unittest.TestCase):
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

    def write(self, document):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "scenario.json"
        path.write_text(self.serializer.serialize(document, "json"), encoding="utf-8")
        return path

    def prepare(self, document, world, maps=None):
        return self.loader.prepare(
            self.write(document),
            world,
            maps or ["Town03", "Town04"],
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

    def test_same_map_replaces_world_and_preserves_logical_metadata(self):
        lib = library()
        old = Actor(1, "vehicle.old", None)
        world = World(1, "Town03", lib, [old])
        plan = self.prepare(valid_document(), world)

        result = self.execute(plan, world)

        self.assertEqual("success", result.report.status)
        self.assertFalse(old.is_alive)
        info = next(iter(self.database.actors_db.actors_db.values()))
        self.assertEqual(valid_document()["actors"][0]["scenario_id"], info.scenario_id)
        self.assertEqual("managed", info.ownership)
        self.assertEqual("scenario_loaded", info.origin)

    def test_map_change_revalidates_target_blueprints(self):
        old_world = World(1, "Town03", Library([]))
        target = World(2, "Town04", library())
        document = valid_document()
        document["world"]["map"] = "Town04"
        plan = self.prepare(document, old_world)

        result = self.execute(
            plan,
            old_world,
            lambda _target: (target, target.library, ["Town04"], "0.9.16"),
        )

        self.assertTrue(result.report.map_changed)
        self.assertEqual("success", result.report.status)
        self.assertIs(target, result.world)

    def test_clear_failure_stops_before_spawn(self):
        lib = library()
        old = Actor(1, "vehicle.old", None, destroy_error="busy")
        world = World(1, "Town03", lib, [old])
        plan = self.prepare(valid_document(), world)
        result = self.execute(plan, world)
        self.assertEqual("failed", result.report.status)
        self.assertEqual([], world.spawn_log)
        self.assertTrue(any(error["stage"] == "clear" for error in result.report.errors))

    def test_stale_episode_is_rejected_without_destruction(self):
        world = World(1, "Town03", library())
        actor = Actor(1, "vehicle.old", world)
        world.actors.append(actor)
        plan = self.prepare(valid_document(), world)
        world.id = 2
        result = self.execute(plan, world)
        self.assertTrue(actor.is_alive)
        self.assertEqual("failed", result.report.status)

    def test_missing_blueprint_after_map_change_stops_restoration(self):
        old_world = World(1, "Town03", Library([]))
        target = World(2, "Town04", Library([]))
        document = valid_document()
        document["world"]["map"] = "Town04"
        plan = self.prepare(document, old_world)
        result = self.execute(
            plan,
            old_world,
            lambda _target: (target, target.library, ["Town04"], "0.9.16"),
        )
        self.assertEqual("failed", result.report.status)
        self.assertTrue(any(
            error["stage"] == "post_map_validation"
            for error in result.report.errors
        ))

    def test_parent_is_spawned_before_sensor(self):
        document = valid_document()
        add_sensor(document)
        document["actors"].reverse()
        log = []
        world = World(1, "Town03", library(), spawn_log=log)
        result = self.execute(self.prepare(document, world), world)
        self.assertEqual("success", result.report.status)
        self.assertEqual("vehicle.test", log[0][0])
        self.assertEqual(log[0][1], None)
        self.assertEqual(log[0][0], "vehicle.test")
        self.assertEqual(100, log[1][1])

    def test_parent_spawn_failure_skips_sensor(self):
        document = valid_document()
        add_sensor(document)
        world = World(
            1,
            "Town03",
            library(),
            fail_blueprints={"vehicle.test"},
        )
        result = self.execute(self.prepare(document, world), world)
        self.assertEqual("failed", result.report.status)
        self.assertEqual(1, result.report.failed)
        self.assertEqual(1, result.report.skipped)
        self.assertEqual([], world.spawn_log)

    def test_partial_success_keeps_successful_actors(self):
        document = valid_document()
        second = copy.deepcopy(document["actors"][0])
        second["scenario_id"] = UUID_2
        second["captured_runtime_id"] = 9
        second["user_name"] = "Vehicle_2"
        second["blueprint_id"] = "vehicle.fail"
        document["actors"].append(second)
        document["capture_report"]["saved"] = 2
        lib = library()
        lib.items["vehicle.fail"] = Blueprint("vehicle.fail", {
            "color": Value("0,0,0", "RGBColor"),
        })
        world = World(1, "Town03", lib, fail_blueprints={"vehicle.fail"})
        result = self.execute(self.prepare(document, world), world)
        self.assertEqual("partial_success", result.report.status)
        self.assertEqual(1, result.report.restored)
        self.assertEqual(1, result.report.failed)

    def test_vehicle_physics_are_merged_and_applied(self):
        document = valid_document()
        document["actors"][0]["physics"]["mass"] = 2345.0
        document["actors"][0]["physics"]["max_rpm"] = 7654.0
        world = World(1, "Town03", library())
        result = self.execute(self.prepare(document, world), world)
        actor = next(iter(self.database.actors_db.actors.values()))
        self.assertEqual("success", result.report.status)
        self.assertEqual(2345.0, actor.physics.mass)
        self.assertEqual(7654.0, actor.physics.max_rpm)


if __name__ == "__main__":
    unittest.main()
