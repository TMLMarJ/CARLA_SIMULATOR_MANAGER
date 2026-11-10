import queue
import unittest
from types import SimpleNamespace
from unittest import mock

from core.command_bus import CommandBus, CommandDispatcher
from core.contracts import RuntimeContext
from core.controller import CarlaController
from core.state_store import DataStorage
from services.sensors import SensorService
from services.world import WorldService


class CommandBusTests(unittest.TestCase):
    def test_priority_and_normal_queues_are_fifo(self):
        bus = CommandBus()
        bus.put(("normal-1", None))
        bus.put(("normal-2", None))
        bus.put_priority(("priority-1", None))
        bus.put_priority(("priority-2", None))

        self.assertEqual(("priority-1", None), bus.get_next(0.0))
        self.assertEqual(("priority-2", None), bus.get_next(0.0))
        self.assertEqual(("normal-1", None), bus.get_next(0.0))
        self.assertEqual(("normal-2", None), bus.get_next(0.0))

    def test_empty_bus_preserves_no_command_result(self):
        self.assertEqual((None, None), CommandBus().get_next(0.0))

    def test_controller_fallback_uses_existing_public_queues(self):
        controller = CarlaController.__new__(CarlaController)
        controller.command_queue = queue.Queue()
        controller.priority_command_queue = queue.Queue()
        controller.command_queue.put(("normal", None))
        controller.priority_command_queue.put(("priority", None))

        self.assertEqual(
            ("priority", None),
            controller._get_next_command(0.0),
        )
        self.assertEqual(
            ("normal", None),
            controller._get_next_command(0.0),
        )


class CommandDispatcherTests(unittest.TestCase):
    def test_unknown_command_is_not_handled(self):
        dispatcher = CommandDispatcher()

        self.assertFalse(dispatcher.dispatch("unknown", object(), None))

    def test_handler_runs_inline_and_exceptions_are_not_hidden(self):
        dispatcher = CommandDispatcher()
        context = object()
        calls = []

        def handler(actual_context, data):
            calls.append((actual_context, data))
            raise RuntimeError("handler failed")

        dispatcher.register("command", handler)
        with self.assertRaisesRegex(RuntimeError, "handler failed"):
            dispatcher.dispatch("command", context, {"value": 1})
        self.assertEqual([(context, {"value": 1})], calls)


class ServiceDelegationTests(unittest.TestCase):
    def test_spawn_handler_delegates_with_existing_defaults(self):
        controller = CarlaController.__new__(CarlaController)
        controller.actor_service = mock.Mock()
        controller.database = SimpleNamespace(
            blueprint_new_data={"color": "1,2,3"},
            ghost_physic=object(),
        )
        controller._record_actor_operation_result = mock.Mock()
        context = RuntimeContext(None, controller.database, object(), object())
        data = {"blueprint_id": "vehicle.test"}

        controller._dispatch_spawn(context, data)

        controller.actor_service.spawn.assert_called_once_with(
            context,
            data,
            controller.database.blueprint_new_data,
            controller.database.ghost_physic,
            controller._record_actor_operation_result,
        )

    def test_sensor_handler_delegates_without_new_thread(self):
        controller = CarlaController.__new__(CarlaController)
        controller.sensor_service = mock.Mock()
        payload = {"sensor_id": 7}

        controller._dispatch_start_sensor_preview(None, payload)

        controller.sensor_service.start_preview.assert_called_once_with(
            payload
        )


class DomainServiceTests(unittest.TestCase):
    def test_world_load_retries_after_timeout_without_new_thread(self):
        world = mock.Mock()
        world.get_map.return_value.name = "/Game/Carla/Maps/Town01"
        client = mock.Mock()
        client.load_world.side_effect = RuntimeError("timeout")
        client.get_world.return_value = world
        shutdown_event = mock.Mock()
        shutdown_event.wait.return_value = False

        loaded = WorldService.load_with_retry(
            client,
            "Town01",
            shutdown_event,
        )

        self.assertIs(world, loaded)
        client.load_world.assert_called_once_with("Town01")

    def test_sensor_attach_updates_registry_and_event_queue(self):
        database = DataStorage()
        parent = mock.Mock(id=1, is_alive=True)
        database.actors_db.actors[parent.id] = parent
        blueprint = mock.Mock()
        blueprint.has_attribute.return_value = False
        database.blueprint_lib = mock.Mock()
        database.blueprint_lib.find.return_value = blueprint
        sensor = mock.Mock(id=2, type_id="sensor.camera.rgb")
        sensor.is_alive = True
        sensor.is_active = True
        sensor.attributes = {}
        sensor.parent = parent
        sensor.get_world.return_value = SimpleNamespace(id=7)
        sensor.get_transform.return_value = object()
        sensor.get_location.return_value = object()
        database.world = mock.Mock()
        database.world.spawn_actor.return_value = sensor
        service = SensorService(database, mock.Mock(), set(), {})
        transform = object()

        service.attach({
            "parent_id": parent.id,
            "blueprint_id": sensor.type_id,
            "attributes": {},
            "transform": transform,
            "name": "Camera",
        })

        self.assertIs(sensor, database.sensors_db.sensors[sensor.id])
        self.assertEqual(
            ("sensor_attached", sensor.id),
            database.sensor_event_queue.get_nowait(),
        )


if __name__ == "__main__":
    unittest.main()
