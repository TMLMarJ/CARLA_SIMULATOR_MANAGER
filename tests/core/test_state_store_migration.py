import unittest

from core.registries import Actors, Sensors
from core.state_store import DataStorage
from models.actors import ActorInfo, SensorInfo
from models.scenarios import ActorOwnership
from tests.world.test_world_synchronization import FakeActor, FakeWorld


class StateStoreMigrationTests(unittest.TestCase):
    def test_state_store_uses_canonical_registries(self):
        database = DataStorage()

        self.assertIsInstance(database.actors_db, Actors)
        self.assertIsInstance(database.sensors_db, Sensors)

    def test_actor_registry_preserves_runtime_reference_and_model_identity(self):
        actor = FakeActor(101, "vehicle.test")
        FakeWorld(1, [actor])
        database = DataStorage()

        info = database.add_actor(actor, "Vehicle")

        self.assertIs(actor, database.actors_db.actors[actor.id])
        self.assertIs(info, database.actors_db.actors_db[actor.id])
        self.assertIsInstance(info, ActorInfo)
        self.assertEqual(ActorOwnership.MANAGED, info.ownership)

    def test_sensor_registry_preserves_runtime_reference_and_model_identity(self):
        parent = FakeActor(101, "vehicle.test")
        sensor = FakeActor(102, "sensor.camera.rgb", parent=parent)
        FakeWorld(1, [parent, sensor])
        database = DataStorage()

        info = database.add_sensor(
            sensor,
            parent.id,
            "Camera",
            sensor.type_id,
            sensor.get_transform(),
            sensor.attributes,
        )

        self.assertIs(sensor, database.sensors_db.sensors[sensor.id])
        self.assertIs(info, database.sensors_db.sensors_db[sensor.id])
        self.assertIsInstance(info, SensorInfo)
        self.assertEqual(ActorOwnership.MANAGED, info.ownership)

    def test_removing_missing_registry_entry_keeps_previous_semantics(self):
        database = DataStorage()

        self.assertIsNone(database.actors_db.remove_actor(999))
        self.assertIsNone(database.sensors_db.remove_sensor(999))


if __name__ == "__main__":
    unittest.main()
