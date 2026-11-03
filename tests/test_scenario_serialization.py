import copy
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from datastore.datastore import DataStorage
from carla_server.carla_server import CarlaController
from gui_contents.main_gui_view import ConnectionFrame, ScenarioSaveFrame
from scenario_management.capture import ScenarioCapture
from scenario_management.converters import (
    vehicle_physics_to_dict,
    weather_to_dict,
)
from scenario_management.models import (
    ActorOwnership,
    ConfigurationStatus,
)
from scenario_management.path_utils import (
    UnknownScenarioExtensionError,
    normalize_scenario_path,
)
from scenario_management.serialization import (
    ScenarioSerializationError,
    ScenarioSerializer,
)
from scenario_management.scenario_manager import ScenarioManager
from scenario_management.validator import (
    ScenarioValidationError,
    ScenarioValidator,
)
from scenario_management.world_synchronizer import WorldSynchronizer


UUID_1 = "11111111-1111-4111-8111-111111111111"
UUID_2 = "22222222-2222-4222-8222-222222222222"


class FakeTransform:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.location = SimpleNamespace(x=x, y=y, z=z)
        self.rotation = SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0)

    def get_matrix(self):
        return [
            [1.0, 0.0, 0.0, self.location.x],
            [0.0, 1.0, 0.0, self.location.y],
            [0.0, 0.0, 1.0, self.location.z],
            [0.0, 0.0, 0.0, 1.0],
        ]

    def get_inverse_matrix(self):
        return [
            [1.0, 0.0, 0.0, -self.location.x],
            [0.0, 1.0, 0.0, -self.location.y],
            [0.0, 0.0, 1.0, -self.location.z],
            [0.0, 0.0, 0.0, 1.0],
        ]


class FakePhysics:
    def __init__(self):
        self.torque_curve = [
            SimpleNamespace(x=0.0, y=400.0),
            SimpleNamespace(x=5000.0, y=500.0),
        ]
        self.max_rpm = 6000.0
        self.moi = 1.2
        self.damping_rate_full_throttle = 0.15
        self.damping_rate_zero_throttle_clutch_engaged = 2.0
        self.damping_rate_zero_throttle_clutch_disengaged = 0.35
        self.use_gear_autobox = True
        self.gear_switch_time = 0.5
        self.clutch_strength = 10.0
        self.final_ratio = 4.0
        self.forward_gears = [
            SimpleNamespace(ratio=3.5, down_ratio=0.4, up_ratio=0.8)
        ]
        self.mass = 1500.0
        self.drag_coefficient = 0.3
        self.center_of_mass = SimpleNamespace(x=0.0, y=0.0, z=-0.2)
        self.steering_curve = [
            SimpleNamespace(x=0.0, y=1.0),
            SimpleNamespace(x=100.0, y=0.5),
        ]
        self.use_sweep_wheel_collision = False
        wheel = SimpleNamespace(
            tire_friction=2.0,
            damping_rate=0.25,
            max_steer_angle=70.0,
            radius=30.0,
            max_brake_torque=1500.0,
            max_handbrake_torque=3000.0,
            long_stiff_value=1000.0,
            lat_stiff_max_load=2.0,
            lat_stiff_value=17.0,
            position=SimpleNamespace(x=1.0, y=1.0, z=0.0),
        )
        self.wheels = [wheel, copy.copy(wheel), copy.copy(wheel), copy.copy(wheel)]


class FakeActor:
    def __init__(self, actor_id, type_id, transform=None, parent=None,
                 role_name=""):
        self.id = actor_id
        self.type_id = type_id
        self.transform = transform or FakeTransform()
        self.parent = parent
        self.attributes = {"role_name": role_name} if role_name else {}
        self.is_alive = True
        self.is_active = True
        self.world = None
        self.physics = FakePhysics()

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
        return self.physics


class FakeWeather:
    cloudiness = 20.0
    precipitation = 0.0
    precipitation_deposits = 0.0
    wind_intensity = 10.0
    sun_azimuth_angle = 15.0
    sun_altitude_angle = 45.0
    fog_density = 0.0
    fog_distance = 0.0
    fog_falloff = 0.2
    wetness = 0.0
    scattering_intensity = 1.0
    mie_scattering_scale = 0.03
    rayleigh_scattering_scale = 0.0331
    dust_storm = 0.0


class FakeWorld:
    def __init__(self, actors=None, world_id=1):
        self.id = world_id
        self.actors = list(actors or [])
        for actor in self.actors:
            actor.world = self

    def get_actors(self):
        return list(self.actors)

    def get_snapshot(self):
        return SimpleNamespace(frame=42)

    def get_map(self):
        return SimpleNamespace(name="/Game/Carla/Maps/Town03")

    def get_weather(self):
        return FakeWeather()

    def get_settings(self):
        return SimpleNamespace(
            synchronous_mode=False,
            fixed_delta_seconds=None,
            no_rendering_mode=False,
            substepping=True,
            max_substep_delta_time=0.01,
            max_substeps=10,
        )


def valid_document():
    physics = vehicle_physics_to_dict(FakePhysics())
    return {
        "schema": "carla-simulator-manager/scenario",
        "schema_version": 1,
        "metadata": {
            "name": "Łódź — próba",
            "created_at": "2026-10-31T12:00:00Z",
            "carla_version": "0.9.16",
            "application_version": None,
        },
        "world": {
            "map": "Town03",
            "captured_world_id": 1,
            "weather": weather_to_dict(FakeWeather()),
            "simulation": {
                "synchronous_mode": False,
                "fixed_delta_seconds": None,
                "no_rendering_mode": False,
                "substepping": True,
                "max_substep_delta_time": 0.01,
                "max_substeps": 10,
            },
        },
        "actors": [{
            "scenario_id": UUID_1,
            "captured_runtime_id": 7,
            "user_name": "Pojazd_Żółty",
            "category": "vehicle",
            "blueprint_id": "vehicle.test",
            "origin": "gui_spawned",
            "ownership": "managed",
            "transform": {
                "space": "world",
                "location": {"x": 1.0, "y": 2.0, "z": 3.0},
                "rotation": {"pitch": 0.0, "yaw": 90.0, "roll": 0.0},
                "source": "server",
            },
            "blueprint_attributes": {"color": "255,0,0"},
            "physics": physics,
            "control": {
                "mode": "Autopilot",
                "autopilot_enabled": True,
                "traffic_manager_port": 8000,
                "source": "gui",
            },
            "parent_scenario_id": None,
            "attachment_type": None,
            "data_sources": {"transform": "server"},
            "completeness": {
                "status": "complete",
                "missing_fields": [],
                "warnings": [],
            },
        }],
        "capture_report": {
            "saved": 1,
            "partial": 0,
            "skipped_runtime_only": 0,
            "skipped_unrestorable": 0,
            "skipped_internal": 0,
            "skipped_infrastructure": 0,
            "warnings": [],
            "errors": [],
            "sync_report": {},
        },
    }


class SerializerTests(unittest.TestCase):
    def setUp(self):
        self.serializer = ScenarioSerializer()
        self.validator = ScenarioValidator()

    def test_json_and_yaml_are_equivalent_and_preserve_unicode(self):
        document = valid_document()
        json_text = self.serializer.serialize(document, "json")
        yaml_text = self.serializer.serialize(document, "yaml")

        self.assertEqual(document, json.loads(json_text))
        self.assertEqual(
            json.loads(json_text),
            self.serializer.deserialize(yaml_text, "yaml"),
        )
        self.assertIn("Pojazd_Żółty", json_text)
        self.assertIn("Łódź", yaml_text)

    def test_json_and_yaml_files_read_back_equivalently(self):
        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "świat.json"
            yaml_path = Path(directory) / "świat.yaml"
            self.serializer.write_atomic(valid_document(), json_path, "json")
            self.serializer.write_atomic(valid_document(), yaml_path, "yaml")

            self.assertEqual(
                self.serializer.read(json_path),
                self.serializer.read(yaml_path),
            )

    def test_selected_format_replaces_known_mismatched_extension(self):
        self.assertEqual(
            Path("scenario.yaml"),
            normalize_scenario_path("scenario.json", "yaml"),
        )
        self.assertEqual(
            Path("scenario.json"),
            normalize_scenario_path("scenario.yaml", "json"),
        )

    def test_path_without_extension_and_yml_are_supported(self):
        self.assertEqual(
            Path("scenario.json"),
            normalize_scenario_path("scenario", "json"),
        )
        self.assertEqual(
            Path("scenario.yml"),
            normalize_scenario_path("scenario.yml", "yaml"),
        )

    def test_path_with_multiple_dots_only_changes_final_extension(self):
        self.assertEqual(
            Path("run.2026.10.yaml"),
            normalize_scenario_path("run.2026.10.json", "yaml"),
        )

    def test_unknown_extension_requires_explicit_confirmation(self):
        with self.assertRaises(UnknownScenarioExtensionError):
            normalize_scenario_path("scenario.backup", "json")
        self.assertEqual(
            Path("scenario.json"),
            normalize_scenario_path(
                "scenario.backup",
                "json",
                allow_unknown=True,
            ),
        )

    def test_atomic_writer_never_writes_yaml_under_json_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            requested = Path(directory) / "scenario.json"
            result = self.serializer.write_atomic(
                valid_document(),
                requested,
                "yaml",
            )
            actual = Path(result["path"])

            self.assertEqual(".yaml", actual.suffix)
            self.assertFalse(requested.exists())
            self.assertEqual(
                valid_document(),
                self.serializer.read(actual, "yaml"),
            )

    def test_atomic_write_does_not_replace_existing_file_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "scenario.json"
            target.write_text("original", encoding="utf-8")
            with mock.patch.object(os, "replace", side_effect=OSError("denied")):
                with self.assertRaises(ScenarioSerializationError):
                    self.serializer.write_atomic(valid_document(), target, "json")
            self.assertEqual("original", target.read_text(encoding="utf-8"))

    def test_missing_yaml_library_has_clear_error(self):
        original_import = __import__

        def import_without_yaml(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("missing")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=import_without_yaml):
            with self.assertRaisesRegex(
                ScenarioSerializationError,
                "requires PyYAML",
            ):
                self.serializer.serialize(valid_document(), "yaml")

    def test_full_vehicle_physics_is_serialized_and_valid(self):
        document = valid_document()
        physics = document["actors"][0]["physics"]

        self.assertEqual(4, len(physics["wheels"]))
        self.assertEqual(1, len(physics["forward_gears"]))
        self.assertIn("lat_stiff_value", physics["wheels"][0])
        self.assertIn("use_sweep_wheel_collision", physics)
        self.validator.validate(document)


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.validator = ScenarioValidator()

    def assert_invalid(self, document, text):
        with self.assertRaises(ScenarioValidationError) as raised:
            self.validator.validate(document)
        self.assertIn(text, str(raised.exception))

    def test_invalid_uuid(self):
        document = valid_document()
        document["actors"][0]["scenario_id"] = "not-a-uuid"
        self.assert_invalid(document, "valid UUID")

    def test_invalid_schema_version(self):
        document = valid_document()
        document["schema_version"] = 999
        self.assert_invalid(document, "schema_version must be 1")

    def test_invalid_parent_and_duplicate_ids(self):
        document = valid_document()
        second = copy.deepcopy(document["actors"][0])
        second["user_name"] = "Sensor_1"
        second["category"] = "sensor"
        second["physics"] = None
        second["scenario_id"] = UUID_1
        second["parent_scenario_id"] = UUID_2
        document["actors"].append(second)

        self.assert_invalid(document, "Duplicate scenario_id")

        second["scenario_id"] = UUID_2
        second["parent_scenario_id"] = "33333333-3333-4333-8333-333333333333"
        self.assert_invalid(document, "missing parent")

    def test_non_finite_number(self):
        document = valid_document()
        document["actors"][0]["transform"]["location"]["x"] = math.nan
        self.assert_invalid(document, "must be finite")

    def test_duplicate_names_and_parent_cycle(self):
        document = valid_document()
        second = copy.deepcopy(document["actors"][0])
        second["scenario_id"] = UUID_2
        second["category"] = "sensor"
        second["physics"] = None
        second["parent_scenario_id"] = UUID_1
        document["actors"][0]["parent_scenario_id"] = UUID_2
        document["actors"].append(second)

        self.assert_invalid(document, "Duplicate user_name")
        second["user_name"] = "Sensor_1"
        self.assert_invalid(document, "dependency cycle")

    def test_invalid_physics_and_control_types(self):
        document = valid_document()
        document["actors"][0]["physics"]["mass"] = "heavy"
        document["actors"][0]["control"]["autopilot_enabled"] = "yes"

        self.assert_invalid(document, "physics.mass must be finite")
        self.assert_invalid(document, "autopilot_enabled is invalid")


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.database = DataStorage()
        self.synchronizer = WorldSynchronizer()
        self.capture = ScenarioCapture()

    def capture_world(self, world, internal_ids=None):
        report = self.synchronizer.synchronize(
            world,
            self.database,
            internal_actor_ids=internal_ids,
        )
        return self.capture.capture(
            world,
            self.database,
            "Świat testowy",
            internal_actor_ids=internal_ids,
            sync_report=report.to_dict(),
        )

    def test_moving_vehicle_uses_current_transform_and_unknown_autopilot(self):
        vehicle = FakeActor(1, "vehicle.test", FakeTransform(x=1.0))
        world = FakeWorld([vehicle])
        self.synchronizer.synchronize(world, self.database)
        vehicle.transform = FakeTransform(x=99.0)

        document = self.capture.capture(world, self.database, "Moving")
        actor = document.actors[0]

        self.assertEqual(99.0, actor.transform["location"]["x"])
        self.assertIsNone(actor.control["autopilot_enabled"])
        self.assertEqual("unknown", actor.control["source"])
        self.assertEqual(ConfigurationStatus.PARTIAL,
                         actor.completeness.status)

    def test_external_sensor_has_parent_and_derived_relative_transform(self):
        vehicle = FakeActor(10, "vehicle.test", FakeTransform(x=10.0))
        sensor = FakeActor(
            11,
            "sensor.camera.rgb",
            FakeTransform(x=12.0),
            parent=vehicle,
        )

        document = self.capture_world(FakeWorld([vehicle, sensor]))
        records = {actor.captured_runtime_id: actor for actor in document.actors}

        self.assertEqual(
            records[10].scenario_id,
            records[11].parent_scenario_id,
        )
        self.assertEqual("relative", records[11].transform["space"])
        self.assertEqual("derived", records[11].transform["source"])
        self.assertEqual(ActorOwnership.EXTERNAL, records[11].ownership)
        self.assertIsNone(records[11].attachment_type)

    def test_managed_sensor_prefers_equivalent_gui_relative_transform(self):
        vehicle = FakeActor(12, "vehicle.test", FakeTransform(x=10.0))
        sensor = FakeActor(
            13,
            "sensor.camera.rgb",
            FakeTransform(x=12.0),
            parent=vehicle,
        )
        world = FakeWorld([vehicle, sensor])
        stored_transform = FakeTransform(x=2.00005)
        with self.database.lock:
            self.database.add_actor(vehicle, "Vehicle GUI")
            self.database.add_sensor(
                sensor,
                vehicle.id,
                "Camera GUI",
                sensor.type_id,
                stored_transform,
                {},
            )

        document = self.capture_world(world)
        record = next(
            actor
            for actor in document.actors
            if actor.captured_runtime_id == sensor.id
        )

        self.assertEqual("gui", record.transform["source"])
        self.assertEqual(2.00005, record.transform["location"]["x"])
        self.assertEqual("gui", record.data_sources["relative_transform"])

    def test_managed_sensor_uses_derived_transform_after_external_change(self):
        vehicle = FakeActor(14, "vehicle.test", FakeTransform(x=10.0))
        sensor = FakeActor(
            15,
            "sensor.camera.rgb",
            FakeTransform(x=13.0),
            parent=vehicle,
        )
        world = FakeWorld([vehicle, sensor])
        with self.database.lock:
            self.database.add_actor(vehicle, "Vehicle GUI")
            self.database.add_sensor(
                sensor,
                vehicle.id,
                "Camera GUI",
                sensor.type_id,
                FakeTransform(x=2.0),
                {},
            )

        document = self.capture_world(world)
        record = next(
            actor
            for actor in document.actors
            if actor.captured_runtime_id == sensor.id
        )

        self.assertEqual("derived", record.transform["source"])
        self.assertEqual(3.0, record.transform["location"]["x"])
        self.assertTrue(any(
            "differs from the stored GUI configuration" in warning
            for warning in record.completeness.warnings
        ))

    def test_runtime_only_unrestorable_and_internal_are_omitted(self):
        runtime_only = FakeActor(20, "custom.runtime")
        unrestorable = FakeActor(21, "custom.unrestorable")
        preview = FakeActor(
            22,
            "sensor.camera.rgb",
            role_name="spectator_preview",
        )
        world = FakeWorld([runtime_only, unrestorable, preview])
        self.synchronizer.synchronize(world, self.database)
        self.database.actors_db.actors_db[20].configuration_status = (
            ConfigurationStatus.RUNTIME_ONLY
        )
        self.database.actors_db.actors_db[21].configuration_status = (
            ConfigurationStatus.UNRESTORABLE
        )

        document = self.capture.capture(world, self.database, "Filtered")

        self.assertEqual([], document.actors)
        self.assertEqual(1, document.capture_report.skipped_runtime_only)
        self.assertEqual(1, document.capture_report.skipped_unrestorable)
        self.assertEqual(1, document.capture_report.skipped_internal)

    def test_managed_pedestrian_control_static_and_walker_controller(self):
        walker = FakeActor(30, "walker.pedestrian.0001")
        static = FakeActor(31, "static.prop.box01")
        controller = FakeActor(32, "controller.ai.walker", parent=walker)
        world = FakeWorld([walker, static, controller])
        with self.database.lock:
            self.database.add_actor(walker, "Pieszy")
            self.database.add_actor(static, "Skrzynia")
            self.database.walker_ai_states[30].update({
                "active": True,
                "speed": 2.5,
                "random_navigation": True,
            })

        document = self.capture_world(world)
        records = {actor.captured_runtime_id: actor for actor in document.actors}

        self.assertTrue(records[30].control["active"])
        self.assertEqual(2.5, records[30].control["speed"])
        self.assertIsNone(records[31].control)
        self.assertEqual(
            records[30].scenario_id,
            records[32].parent_scenario_id,
        )
        self.assertIsNone(records[32].control["active"])


class ControllerSaveCommandTests(unittest.TestCase):
    def test_save_command_uses_existing_controller_queue(self):
        controller = object.__new__(CarlaController)
        controller.scenario_operation_lock = mock.MagicMock()
        controller.scenario_operation_lock.__enter__.return_value = None
        controller.scenario_operation_pending = False
        controller.shutdown_event = mock.MagicMock()
        controller.shutdown_event.is_set.return_value = False
        controller.running = True
        controller.command_queue = mock.MagicMock()
        controller.database = SimpleNamespace(
            scenario_event_queue=mock.MagicMock()
        )

        self.assertTrue(controller.save_scenario("Test", "test.json", "json"))
        controller.command_queue.put.assert_called_once_with((
            "save_scenario",
            {"name": "Test", "path": "test.json", "format": "json"},
        ))

    def test_second_scenario_operation_is_rejected(self):
        controller = object.__new__(CarlaController)
        controller.scenario_operation_lock = mock.MagicMock()
        controller.scenario_operation_lock.__enter__.return_value = None
        controller.scenario_operation_pending = True
        controller.shutdown_event = mock.MagicMock()
        controller.running = True
        controller.command_queue = mock.MagicMock()
        controller.database = SimpleNamespace(
            scenario_event_queue=mock.MagicMock()
        )

        self.assertFalse(controller.save_scenario("Test", "test.json", "json"))
        controller.command_queue.put.assert_not_called()
        controller.database.scenario_event_queue.put.assert_called_once()

    def test_save_command_normalizes_mismatched_extension_before_queueing(self):
        controller = object.__new__(CarlaController)
        controller.scenario_operation_lock = mock.MagicMock()
        controller.scenario_operation_lock.__enter__.return_value = None
        controller.scenario_operation_pending = False
        controller.shutdown_event = mock.MagicMock()
        controller.shutdown_event.is_set.return_value = False
        controller.running = True
        controller.command_queue = mock.MagicMock()
        controller.database = SimpleNamespace(
            scenario_event_queue=mock.MagicMock()
        )

        self.assertTrue(
            controller.save_scenario("Test", "test.json", "yaml")
        )
        payload = controller.command_queue.put.call_args.args[0][1]
        self.assertEqual("test.yaml", payload["path"])


class FakeVariable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeWidget:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)


class ScenarioGuiStateTests(unittest.TestCase):
    def make_frame(self, path, file_format):
        frame = object.__new__(ScenarioSaveFrame)
        frame.path_var = FakeVariable(path)
        frame.format_var = FakeVariable(file_format)
        return frame

    def test_format_change_updates_existing_path(self):
        frame = self.make_frame("scenario.json", "YAML")
        frame._on_format_changed()
        self.assertEqual("scenario.yaml", frame.path_var.get())

        frame.format_var.set("JSON")
        frame._on_format_changed()
        self.assertEqual("scenario.json", frame.path_var.get())

    def test_connected_disconnected_and_saving_widget_states(self):
        frame = self.make_frame("scenario.json", "JSON")
        frame.connected = False
        frame.pending = False
        frame.name_entry = FakeWidget()
        frame.path_entry = FakeWidget()
        frame.browse_button = FakeWidget()
        frame.save_button = FakeWidget()
        frame.clear_button = FakeWidget()
        frame.format_combo = FakeWidget()
        frame.clear_mode_combo = FakeWidget()

        frame._update_widget_state()
        self.assertEqual("disabled", frame.save_button.options["state"])

        frame.connected = True
        frame._update_widget_state()
        self.assertEqual("normal", frame.save_button.options["state"])
        self.assertEqual("readonly", frame.format_combo.options["state"])

        frame.pending = True
        frame._update_widget_state()
        self.assertEqual("disabled", frame.save_button.options["state"])

    def test_sync_status_covers_pending_success_and_error_states(self):
        frame = object.__new__(ConnectionFrame)
        frame.sync_status_var = FakeVariable("")
        frame.refresh_world_button = FakeWidget()
        frame.manager = SimpleNamespace(connected=True, world_changing=False)

        frame.set_sync_pending()
        self.assertEqual("Synchronizing...", frame.sync_status_var.get())
        self.assertEqual(
            "disabled",
            frame.refresh_world_button.options["state"],
        )

        frame.show_sync_result({
            "added": 2,
            "updated": 3,
            "removed": 1,
            "errors": [],
        })
        self.assertIn("+2 / ~3 / -1", frame.sync_status_var.get())
        self.assertEqual("normal", frame.refresh_world_button.options["state"])

        frame.show_sync_error("connection lost")
        self.assertIn("connection lost", frame.sync_status_var.get())


class ScenarioManagerTests(unittest.TestCase):
    def test_end_to_end_save_captures_valid_current_world(self):
        vehicle = FakeActor(100, "vehicle.test", FakeTransform(x=15.0))
        world = FakeWorld([vehicle])
        database = DataStorage()
        synchronizer = WorldSynchronizer()
        manager = ScenarioManager()

        def synchronize(current_world, reason, publish):
            self.assertEqual("before_save", reason)
            self.assertFalse(publish)
            return synchronizer.synchronize(
                current_world,
                database,
                reason=reason,
            ).to_dict()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            result = manager.save(
                world,
                database,
                set(),
                "End to end",
                path,
                "json",
                synchronize=synchronize,
            )
            document = ScenarioSerializer.read(path)

        self.assertTrue(result["success"])
        self.assertEqual(1, result["saved"])
        self.assertEqual(15.0, document["actors"][0]["transform"]["location"]["x"])
        ScenarioValidator().validate(document)


if __name__ == "__main__":
    unittest.main()
