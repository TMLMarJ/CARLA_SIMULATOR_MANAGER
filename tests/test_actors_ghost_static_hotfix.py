import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from carla_server.carla_server import CarlaController
from datastore.datastore import DataStorage
from gui_contents.spawn_preview import GhostPreview
from gui_contents.vehicles import CustomVehicleFrame
from scenario_management.models import ActorOwnership
from scenario_management.world_synchronizer import WorldSynchronizer


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeTransform:
    def __init__(self, x=0.0):
        self.location = SimpleNamespace(x=x, y=0.0, z=0.0)
        self.rotation = SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0)


class FakeActor:
    def __init__(self, actor_id, type_id):
        self.id = actor_id
        self.type_id = type_id
        self.attributes = {}
        self.parent = None
        self.is_alive = True
        self.is_active = True
        self.transform = FakeTransform(float(actor_id))

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


class FakeWorld:
    def __init__(self, world_id, actors):
        self.id = world_id
        self.actors = list(actors)

    def get_actors(self):
        return list(self.actors)

    def get_snapshot(self):
        return SimpleNamespace(frame=1)


class GhostPreviewTests(unittest.TestCase):
    def test_toggle_blocks_spawn_and_can_restore_preview(self):
        calls = []
        carla_controller = SimpleNamespace(
            spawn_preview_actor=lambda *args, **kwargs: calls.append(
                ("spawn", args, kwargs)
            ) or True,
            destroy_preview_actor=lambda *args, **kwargs: calls.append(
                ("destroy", args, kwargs)
            ) or True,
            set_ghost_transform=lambda _transform: None,
        )
        preview = GhostPreview(
            SimpleNamespace(available_spawn_points=[FakeTransform()]),
            SimpleNamespace(carla=carla_controller),
        )

        self.assertTrue(preview.spawn("vehicle.test", FakeTransform()))
        preview.set_enabled(False)
        preview.destroy()
        self.assertFalse(preview.spawn("vehicle.other", FakeTransform()))
        preview.set_enabled(True)
        self.assertTrue(preview.spawn("vehicle.other", FakeTransform()))

        self.assertEqual(["spawn", "destroy", "spawn"], [c[0] for c in calls])

    def test_fast_spawn_destroy_invalidates_delayed_spawn_request(self):
        controller = object.__new__(CarlaController)
        controller.database = SimpleNamespace(
            lock=threading.Lock(),
            ghost_preview_request_id=None,
            ghost_preview_requested_enabled=False,
        )
        controller.command_queue = queue.Queue()
        controller.scenario_operation_lock = threading.Lock()
        controller.scenario_operation_pending = False
        controller.world_transition_event = threading.Event()
        controller.running = True

        self.assertTrue(controller.spawn_preview_actor(
            "vehicle.test",
            FakeTransform(),
            lambda _image: None,
            request_id="spawn-request",
        ))
        self.assertTrue(controller.destroy_preview_actor("destroy-request"))

        self.assertFalse(controller._ghost_preview_request_is_current(
            "spawn-request",
            enabled=True,
        ))
        self.assertTrue(controller._ghost_preview_request_is_current(
            "destroy-request",
            enabled=False,
        ))

    def test_vehicle_spawn_does_not_require_ghost_actor(self):
        frame = object.__new__(CustomVehicleFrame)
        frame.vehicle_name_var = Value("Vehicle without preview")
        frame.x_position_var = Value(1.0)
        frame.y_position_var = Value(2.0)
        frame.z_position_var = Value(3.0)
        frame.pitch_position_var = Value(0.0)
        frame.yaw_position_var = Value(90.0)
        frame.roll_position_var = Value(0.0)
        frame.choose_class = Value("Car")
        frame.choose_model = Value("Test Model")
        frame.configured_blueprint_attributes = {"color": "1,2,3"}
        frame.configured_physics = None
        frame.pending_requests = {}
        frame.database = SimpleNamespace(
            blueprint_lib=object(),
            blueprint_new_data=None,
            ghost_data=None,
            ghost_physic=None,
        )
        frame.controller = SimpleNamespace(
            carla=SimpleNamespace(spawn_actor=Mock(return_value=True))
        )
        frame.destroy_ghost = Mock()
        frame.reset_tab = Mock()
        frame.spawn_ghost = Mock()

        with patch(
            "gui_contents.vehicles.filter_carla_id_by_type",
            return_value={"Test Model": "vehicle.test"},
        ):
            frame.spawn_vehicle()

        frame.controller.carla.spawn_actor.assert_called_once()
        data = frame.controller.carla.spawn_actor.call_args.args[0]
        self.assertEqual("vehicle.test", data["blueprint_id"])
        self.assertEqual({"color": "1,2,3"}, data["attributes"])


class StaticInfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.database = DataStorage()
        self.synchronizer = WorldSynchronizer()

    def test_native_static_baseline_is_not_registered_as_external(self):
        native = FakeActor(10, "static.prop.streetbarrier")

        report = self.synchronizer.synchronize(
            FakeWorld(2, [native]),
            self.database,
            infrastructure_actor_ids={10},
        )

        self.assertEqual(1, report.skipped_infrastructure)
        self.assertNotIn(10, self.database.actors_db.actors_db)

    def test_later_external_static_actor_is_still_detected(self):
        native = FakeActor(10, "static.prop.streetbarrier")
        external = FakeActor(11, "static.prop.box01")

        self.synchronizer.synchronize(
            FakeWorld(2, [native, external]),
            self.database,
            infrastructure_actor_ids={10},
        )

        info = self.database.actors_db.actors_db[11]
        self.assertEqual(ActorOwnership.EXTERNAL, info.ownership)
        self.assertNotIn(10, self.database.actors_db.actors_db)

    def test_refresh_preserves_managed_static_ownership(self):
        actor = FakeActor(12, "static.prop.box01")
        world = FakeWorld(2, [actor])
        self.synchronizer.synchronize(world, self.database)
        self.database.actors_db.actors_db[12].ownership = (
            ActorOwnership.MANAGED
        )

        self.synchronizer.synchronize(world, self.database)

        self.assertEqual(
            ActorOwnership.MANAGED,
            self.database.actors_db.actors_db[12].ownership,
        )

    def test_controlled_map_load_baseline_contains_only_static_actors(self):
        controller = object.__new__(CarlaController)
        world = FakeWorld(7, [
            FakeActor(20, "static.prop.box01"),
            FakeActor(21, "vehicle.test"),
        ])

        captured = controller._capture_map_infrastructure(world)

        self.assertEqual({20}, captured)
        self.assertEqual({20}, controller.map_infrastructure_actor_ids)
        self.assertEqual(7, controller.map_infrastructure_world_id)

    def test_map_infrastructure_is_protected_from_scenario_operations(self):
        controller = object.__new__(CarlaController)
        controller.database = SimpleNamespace(
            ghost_data=None,
            ghost_camera=None,
        )
        controller.movement_preview_camera = None
        controller.controller = SimpleNamespace(spectator_camera_id=None)
        controller.walker_ai_controllers = {}
        controller.map_infrastructure_actor_ids = {20, 22}
        controller.map_infrastructure_world_id = 7

        internal_ids = controller._collect_internal_actor_ids()

        self.assertEqual({20, 22}, internal_ids)
        self.assertEqual(
            set(),
            controller._map_infrastructure_ids(SimpleNamespace(id=8)),
        )


if __name__ == "__main__":
    unittest.main()
