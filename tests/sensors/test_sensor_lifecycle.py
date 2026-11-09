import queue
import unittest
from types import SimpleNamespace
from unittest import mock

from core.controller import CarlaController
from core.state_store import DataStorage
from preview.sensor_buffers import SensorEventsPreviewBuffer


def sensor_controller():
    controller = CarlaController.__new__(CarlaController)
    controller.database = DataStorage()
    controller.sensor_preview_ids = set()
    controller.sensor_status_errors = {}
    controller.command_queue = queue.Queue()
    controller._scenario_mutation_blocked = mock.Mock(return_value=False)
    controller._managed_operation_allowed = mock.Mock(return_value=True)
    return controller


class SensorLifecycleTests(unittest.TestCase):
    def test_closed_event_buffer_ignores_late_sensor_callbacks(self):
        buffer = SensorEventsPreviewBuffer("sensor.other.collision")
        event = SimpleNamespace(
            frame=12,
            other_actor=SimpleNamespace(id=7, type_id="vehicle.test"),
            normal_impulse=SimpleNamespace(x=1.0, y=2.0, z=3.0),
        )
        buffer.on_sensor_data(event)

        revision, events, _started, error = buffer.get_snapshot()
        self.assertEqual(1, revision)
        self.assertEqual(1, len(events))
        self.assertIsNone(error)

        buffer.close()
        buffer.on_sensor_data(event)
        closed_revision, closed_events, _started, error = buffer.get_snapshot()
        self.assertEqual(2, closed_revision)
        self.assertEqual([], closed_events)
        self.assertIsNone(error)

    def test_removing_parent_sensors_stops_destroys_and_unregisters_children(self):
        controller = sensor_controller()
        child_a = mock.Mock(is_alive=True)
        child_a.is_listening.return_value = True
        child_b = mock.Mock(is_alive=True)
        child_b.is_listening.return_value = False
        unrelated = mock.Mock(is_alive=True)
        controller.database.sensors_db.sensors.update({
            10: child_a,
            11: child_b,
            99: unrelated,
        })
        controller.database.sensors_db.sensors_db.update({
            10: SimpleNamespace(parent_id=7),
            11: SimpleNamespace(parent_id=7),
            99: SimpleNamespace(parent_id=8),
        })
        controller.sensor_preview_ids.update({10, 11, 99})
        controller.sensor_status_errors.update({10: "old", 11: "old", 99: "keep"})

        controller.remove_sensors_for_parent(7)

        child_a.stop.assert_called_once_with()
        child_b.stop.assert_not_called()
        child_a.destroy.assert_called_once_with()
        child_b.destroy.assert_called_once_with()
        unrelated.destroy.assert_not_called()
        self.assertEqual({99}, set(controller.database.sensors_db.sensors))
        self.assertEqual({99}, set(controller.database.sensors_db.sensors_db))
        self.assertEqual({99}, controller.sensor_preview_ids)
        self.assertEqual({99: "keep"}, controller.sensor_status_errors)
        self.assertEqual(
            ("sensors_cleared", [10, 11]),
            controller.database.sensor_event_queue.get_nowait(),
        )

    def test_sensor_registry_is_cleaned_even_when_runtime_destroy_fails(self):
        controller = sensor_controller()
        sensor = mock.Mock(is_alive=True)
        sensor.is_listening.return_value = False
        sensor.destroy.side_effect = RuntimeError("server unavailable")
        controller.database.sensors_db.sensors[10] = sensor
        controller.database.sensors_db.sensors_db[10] = SimpleNamespace(parent_id=7)

        controller.remove_sensors_for_parent(7)

        self.assertNotIn(10, controller.database.sensors_db.sensors)
        self.assertNotIn(10, controller.database.sensors_db.sensors_db)
        self.assertEqual(
            ("sensors_cleared", [10]),
            controller.database.sensor_event_queue.get_nowait(),
        )

    def test_cleanup_sensor_previews_stops_every_active_preview(self):
        controller = sensor_controller()
        controller.sensor_preview_ids.update({10, 11})
        controller.stop_sensor_preview_actor = mock.Mock(
            side_effect=controller.sensor_preview_ids.discard
        )

        controller.cleanup_sensor_previews()

        self.assertEqual(set(), controller.sensor_preview_ids)
        self.assertCountEqual(
            [mock.call(10), mock.call(11)],
            controller.stop_sensor_preview_actor.call_args_list,
        )

    def test_remove_sensor_queues_command_for_managed_sensor(self):
        controller = sensor_controller()

        accepted = controller.remove_sensor(25)

        self.assertTrue(accepted)
        controller._managed_operation_allowed.assert_called_once_with(25, sensor=True)
        self.assertEqual(("remove_sensor", 25), controller.command_queue.get_nowait())

    def test_remove_sensor_rejects_external_sensor_without_queuing(self):
        controller = sensor_controller()
        controller._managed_operation_allowed.return_value = False

        accepted = controller.remove_sensor(25)

        self.assertFalse(accepted)
        self.assertTrue(controller.command_queue.empty())

    def test_scenario_operation_blocks_sensor_mutation_before_ownership_check(self):
        controller = sensor_controller()
        controller._scenario_mutation_blocked.return_value = True

        accepted = controller.change_sensor_transform(25, object())

        self.assertFalse(accepted)
        controller._managed_operation_allowed.assert_not_called()
        self.assertTrue(controller.command_queue.empty())


if __name__ == "__main__":
    unittest.main()
