import queue
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from scenarios.restorer import ScenarioRestorer
from scenarios.serialization import ScenarioSerializer
from services.sensors import SensorService
from utilities.sensor_attachments import resolve_attachment_type


class AttachmentType:
    Rigid = object()
    SpringArm = object()
    SpringArmGhost = object()


FAKE_CARLA = SimpleNamespace(AttachmentType=AttachmentType)


class FakeBlueprint:
    id = "sensor.camera.rgb"

    def __iter__(self):
        return iter(())

    def has_attribute(self, _name):
        return False


class SensorAttachmentTests(unittest.TestCase):
    def test_all_supported_attachment_names_map_exactly(self):
        for name in ("Rigid", "SpringArm", "SpringArmGhost"):
            with self.subTest(name=name):
                self.assertIs(
                    getattr(AttachmentType, name),
                    resolve_attachment_type(FAKE_CARLA, name),
                )

    def test_unavailable_attachment_type_is_not_silently_replaced(self):
        incomplete_carla = SimpleNamespace(
            AttachmentType=SimpleNamespace(Rigid=object())
        )
        with self.assertRaisesRegex(RuntimeError, "SpringArm.*unavailable"):
            resolve_attachment_type(incomplete_carla, "SpringArm")

    def test_serialization_preserves_attachment_type(self):
        document = {
            "actors": [{
                "user_name": "Camera",
                "attachment_type": "SpringArm",
            }]
        }
        serializer = ScenarioSerializer()

        for file_format in ("json", "yaml"):
            with self.subTest(file_format=file_format):
                restored = serializer.deserialize(
                    serializer.serialize(document, file_format),
                    file_format,
                )
                self.assertEqual(
                    "SpringArm",
                    restored["actors"][0]["attachment_type"],
                )

    @mock.patch("services.sensors.carla", FAKE_CARLA)
    def test_sensor_service_spawns_and_registers_selected_attachment(self):
        parent = SimpleNamespace(is_alive=True)
        sensor = SimpleNamespace(id=10)
        database = SimpleNamespace(
            lock=threading.Lock(),
            actors_db=SimpleNamespace(actors={7: parent}),
            blueprint_lib=SimpleNamespace(find=lambda _id: FakeBlueprint()),
            world=SimpleNamespace(spawn_actor=mock.Mock(return_value=sensor)),
            add_sensor=mock.Mock(),
            resolve_unique_managed_name=mock.Mock(return_value="Sensor_1"),
            sensor_event_queue=queue.Queue(),
        )
        service = SensorService(database, None, set(), {})

        service.attach({
            "name": "Sensor",
            "parent_id": 7,
            "blueprint_id": "sensor.camera.rgb",
            "transform": object(),
            "attributes": {},
            "attachment_type": "SpringArmGhost",
        })

        self.assertIs(
            AttachmentType.SpringArmGhost,
            database.world.spawn_actor.call_args.kwargs["attachment_type"],
        )
        self.assertEqual(
            "SpringArmGhost",
            database.add_sensor.call_args.kwargs["attachment_type"],
        )
        self.assertEqual("Sensor_1", database.add_sensor.call_args.args[2])

    def test_restorer_defaults_missing_attachment_to_rigid(self):
        restorer = object.__new__(ScenarioRestorer)
        restorer.carla = FAKE_CARLA
        restorer._spawn_transform = lambda _data: object()
        actor = SimpleNamespace(
            id=11,
            type_id="sensor.camera.rgb",
            attributes={},
        )
        world = SimpleNamespace(spawn_actor=mock.Mock(return_value=actor))
        library = SimpleNamespace(find=lambda _id: FakeBlueprint())
        plan = SimpleNamespace(applicable_attributes=set())

        restorer._spawn_one(
            world,
            library,
            {
                "blueprint_id": "sensor.camera.rgb",
                "blueprint_attributes": {},
            },
            SimpleNamespace(id=7),
            plan,
        )

        self.assertIs(
            AttachmentType.Rigid,
            world.spawn_actor.call_args.kwargs["attachment_type"],
        )


if __name__ == "__main__":
    unittest.main()
