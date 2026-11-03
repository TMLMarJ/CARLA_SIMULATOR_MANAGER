import unittest
from unittest import mock

from datastore.datastore import DataStorage
from scenario_management.actor_adoption import ActorAdopter, AdoptionStatus
from scenario_management.capture import ScenarioCapture
from scenario_management.models import ActorOrigin, ActorOwnership
from scenario_management.world_synchronizer import WorldSynchronizer
from tests.test_scenario_serialization import FakeActor, FakeWorld


class ExternalActorAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.database = DataStorage()
        self.synchronizer = WorldSynchronizer()
        self.adopter = ActorAdopter()

    def prepare(self, actors):
        world = FakeWorld(actors)
        self.synchronizer.synchronize(world, self.database)
        return world

    def adopt(self, world, actor_id, internal_ids=None):
        expected = self.adopter.expected_identity(self.database, actor_id)
        return self.adopter.adopt(
            world,
            self.database,
            expected,
            internal_actor_ids=internal_ids,
        )

    def test_external_vehicle_is_adopted(self):
        vehicle = FakeActor(1, "vehicle.test")
        world = self.prepare([vehicle])

        report = self.adopt(world, vehicle.id)

        self.assertEqual(AdoptionStatus.SUCCESS, report.status)
        self.assertEqual(
            ActorOwnership.MANAGED,
            self.database.actors_db.actors_db[vehicle.id].ownership,
        )

    def test_runtime_and_logical_identity_are_preserved(self):
        vehicle = FakeActor(2, "vehicle.test")
        world = self.prepare([vehicle])
        original = self.database.actors_db.actors_db[vehicle.id]
        scenario_id = original.scenario_id

        report = self.adopt(world, vehicle.id)
        adopted = self.database.actors_db.actors_db[vehicle.id]

        self.assertEqual(vehicle.id, adopted.runtime_id)
        self.assertEqual(scenario_id, adopted.scenario_id)
        self.assertEqual(ActorOrigin.ADOPTED_EXTERNAL, adopted.origin)
        self.assertEqual(scenario_id, report.scenario_id)

    def test_changed_identity_is_rejected(self):
        vehicle = FakeActor(3, "vehicle.first")
        world = self.prepare([vehicle])
        expected = self.adopter.expected_identity(self.database, vehicle.id)
        vehicle.type_id = "vehicle.reused"

        report = self.adopter.adopt(world, self.database, expected)

        self.assertEqual(AdoptionStatus.STALE_IDENTITY, report.status)
        self.assertEqual(
            ActorOwnership.EXTERNAL,
            self.database.actors_db.actors_db[vehicle.id].ownership,
        )

    def test_infrastructure_internal_and_unverified_static_are_rejected(self):
        light = FakeActor(4, "traffic.traffic_light")
        preview = FakeActor(
            5,
            "sensor.camera.rgb",
            role_name="spectator_preview",
        )
        static = FakeActor(6, "static.prop.box01")
        world = self.prepare([light, preview, static])

        def live_identity(actor):
            _runtime_id, type_id, parent_id, attributes = (
                self.adopter._identity(actor)
            )
            return {
                "runtime_id": actor.id,
                "world_id": world.id,
                "type_id": type_id,
                "parent_runtime_id": parent_id,
                "attributes": attributes,
            }

        light_report = self.adopter.adopt(
            world,
            self.database,
            live_identity(light),
        )
        preview_report = self.adopter.adopt(
            world,
            self.database,
            live_identity(preview),
            internal_actor_ids={preview.id},
        )
        static_report = self.adopt(world, static.id)

        self.assertEqual(
            AdoptionStatus.UNSUPPORTED_CATEGORY,
            light_report.status,
        )
        self.assertEqual(
            AdoptionStatus.UNSUPPORTED_CATEGORY,
            preview_report.status,
        )
        self.assertEqual(
            AdoptionStatus.UNSAFE_TO_ADOPT,
            static_report.status,
        )

    def test_world_sync_preserves_adopted_ownership(self):
        vehicle = FakeActor(7, "vehicle.test")
        world = self.prepare([vehicle])
        self.adopt(world, vehicle.id)

        self.synchronizer.synchronize(world, self.database)

        info = self.database.actors_db.actors_db[vehicle.id]
        self.assertEqual(ActorOwnership.MANAGED, info.ownership)
        self.assertEqual(ActorOrigin.ADOPTED_EXTERNAL, info.origin)

    def test_attached_sensor_is_not_adopted_automatically(self):
        vehicle = FakeActor(8, "vehicle.test")
        sensor = FakeActor(
            9,
            "sensor.camera.rgb",
            parent=vehicle,
        )
        world = self.prepare([vehicle, sensor])

        report = self.adopt(world, vehicle.id)

        self.assertEqual(1, report.dependent_external_sensors)
        self.assertEqual(
            ActorOwnership.EXTERNAL,
            self.database.sensors_db.sensors_db[sensor.id].ownership,
        )

    def test_adopted_vehicle_qualifies_for_scenario_save(self):
        vehicle = FakeActor(10, "vehicle.test")
        world = self.prepare([vehicle])
        self.adopt(world, vehicle.id)

        document = ScenarioCapture().capture(
            world,
            self.database,
            "Adopted vehicle",
        )

        self.assertEqual([10], [
            actor.captured_runtime_id for actor in document.actors
        ])
        self.assertEqual("managed", document.actors[0].ownership)
        self.assertIsNone(document.actors[0].control["autopilot_enabled"])

    def test_adoption_does_not_change_actor_control(self):
        vehicle = FakeActor(11, "vehicle.test")
        vehicle.set_autopilot = mock.MagicMock()
        vehicle.apply_control = mock.MagicMock()
        vehicle.set_transform = mock.MagicMock()
        world = self.prepare([vehicle])

        self.adopt(world, vehicle.id)

        vehicle.set_autopilot.assert_not_called()
        vehicle.apply_control.assert_not_called()
        vehicle.set_transform.assert_not_called()


if __name__ == "__main__":
    unittest.main()
