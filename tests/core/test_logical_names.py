import unittest
import threading
from types import SimpleNamespace
from unittest import mock

from core.contracts import RuntimeContext
from core.state_store import DataStorage
from models.scenarios import ActorOwnership
from utilities.logical_names import unique_logical_name
from services.actors import ActorService


def record(runtime_id, name, ownership=ActorOwnership.MANAGED,
           type_id="vehicle.test"):
    return SimpleNamespace(
        runtime_id=runtime_id,
        user_name=name,
        ownership=ownership,
        type_id=type_id,
        data_sources={},
    )


class LogicalNameTests(unittest.TestCase):
    def test_smallest_free_suffix_uses_full_existing_names(self):
        self.assertEqual(
            "Vehicle_2",
            unique_logical_name(
                "Vehicle",
                {"Vehicle", "Vehicle_1", "Vehicle_3"},
                "Vehicle",
            ),
        )
        self.assertEqual(
            "Vehicle_1_1",
            unique_logical_name(
                "Vehicle_1",
                {"Vehicle_1"},
                "Vehicle",
            ),
        )

    def test_empty_name_uses_default_then_resolves_collision(self):
        self.assertEqual(
            "Sensor_2",
            unique_logical_name("", {"Sensor", "Sensor_1"}, "Sensor"),
        )

    def test_database_scope_is_global_for_managed_actors_and_sensors(self):
        database = DataStorage()
        database.actors_db.actors_db[1] = record(1, "Object")
        database.sensors_db.sensors_db[2] = record(
            2,
            "Object_1",
            type_id="sensor.camera.rgb",
        )
        database.actors_db.actors_db[3] = record(
            3,
            "Object_2",
            ownership=ActorOwnership.EXTERNAL,
        )

        self.assertEqual(
            "Object_2",
            database.resolve_unique_managed_name("Object", "Actor"),
        )

    def test_rename_excludes_self_and_resolves_other_managed_names(self):
        database = DataStorage()
        first = record(1, "Vehicle")
        second = record(2, "Vehicle_1")
        database.actors_db.actors_db.update({1: first, 2: second})

        self.assertEqual("Vehicle", database.rename_actor(1, "Vehicle"))
        self.assertEqual("Vehicle_1_1", database.rename_actor(1, "Vehicle_1"))
        self.assertEqual("Vehicle_1_1", first.user_name)

    def test_empty_sensor_rename_uses_default_and_external_is_not_renamed(self):
        database = DataStorage()
        managed = record(5, "Old", type_id="sensor.camera.rgb")
        external = record(
            6,
            "Sensor",
            ownership=ActorOwnership.EXTERNAL,
            type_id="sensor.camera.rgb",
        )
        database.sensors_db.sensors_db.update({5: managed, 6: external})

        self.assertEqual("Sensor", database.rename_sensor(5, ""))
        with self.assertRaisesRegex(ValueError, "GUI-managed"):
            database.rename_sensor(6, "Changed")
        self.assertEqual("Sensor", external.user_name)

    def test_actor_spawn_registers_the_resolved_name(self):
        actor = SimpleNamespace(
            id=9,
            type_id="vehicle.test",
            is_alive=True,
        )
        actor_info = SimpleNamespace(physics_control=None)
        database = SimpleNamespace(
            blueprint_new_data=None,
            ghost_physic=None,
            lock=threading.Lock(),
            actors_db=SimpleNamespace(actors={}),
            resolve_unique_managed_name=mock.Mock(
                return_value="Vehicle_2"
            ),
            add_actor=mock.Mock(return_value=actor_info),
            spawned_actor_id=None,
        )
        service = ActorService(database)
        service.spawn_checked = mock.Mock(return_value=actor)
        results = []

        service.spawn(
            RuntimeContext(
                client=None,
                database=database,
                world=SimpleNamespace(id=17),
                blueprint_library=object(),
            ),
            {
                "name": "Vehicle",
                "blueprint_id": "vehicle.test",
                "transform": object(),
            },
            None,
            None,
            lambda *args, **kwargs: results.append((args, kwargs)),
        )

        self.assertEqual("Vehicle_2", database.add_actor.call_args.args[1])
        self.assertEqual("Vehicle_2", results[0][1]["user_name"])


if __name__ == "__main__":
    unittest.main()
