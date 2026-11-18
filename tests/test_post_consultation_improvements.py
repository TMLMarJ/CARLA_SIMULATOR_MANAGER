import queue
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from core.controller import CarlaController
from gui.windows.actor_editor import (
    CurvePreview,
    SteeringCurveTable,
    TorqueCurveTable,
)
from gui.windows.movement_control import MovementControlWindow


class DisconnectRegressionTests(unittest.TestCase):
    @staticmethod
    def controller(shutting_down):
        controller = CarlaController.__new__(CarlaController)
        controller.shutdown_event = threading.Event()
        if shutting_down:
            controller.shutdown_event.set()
        controller._run_controller = mock.Mock(
            side_effect=RuntimeError("server did not respond")
        )
        controller._shutdown_vehicle_controls = mock.Mock()
        controller._shutdown_walker_ai = mock.Mock()
        controller.cleanup_movement_preview = mock.Mock()
        controller.cleanup_sensor_previews = mock.Mock()
        controller.cleanup_preview = mock.Mock()
        controller._shutdown_exclusive_traffic_manager = mock.Mock()
        controller._invalidate_traffic_manager_reference = mock.Mock()
        controller._invalidate_database_world_reference = mock.Mock()
        controller.scenario_operation_lock = threading.Lock()
        controller.active_load_plan = None
        controller.scenario_operation_kind = None
        controller.load_execution_queued = False
        controller.running = True
        controller.data_ready = True
        controller.simulation_manager = mock.Mock()
        controller.dashboard_monitor = mock.Mock()
        controller.database = object()
        controller._publish_simulation_control_state = mock.Mock()
        controller.connection_queue = queue.Queue()
        return controller

    def test_intentional_disconnect_does_not_report_connection_error(self):
        controller = self.controller(shutting_down=True)

        controller.run()

        self.assertEqual(("disconnected", None), controller.connection_queue.get_nowait())
        self.assertTrue(controller.connection_queue.empty())
        controller.dashboard_monitor.disconnect.assert_called_once_with(
            controller.database,
            "Disconnected by user",
        )

    def test_unexpected_server_loss_preserves_error_diagnostics(self):
        controller = self.controller(shutting_down=False)

        controller.run()

        status, message = controller.connection_queue.get_nowait()
        self.assertEqual("connection_error", status)
        self.assertEqual("server did not respond", message)
        controller.dashboard_monitor.disconnect.assert_called_once()
        self.assertEqual(
            "server did not respond",
            str(controller.dashboard_monitor.disconnect.call_args.args[2]),
        )


class PresentationRegressionTests(unittest.TestCase):
    def test_vehicle_speed_is_converted_from_mps_to_kph(self):
        self.assertEqual(
            "42.5 km/h",
            MovementControlWindow._format_vehicle_speed(42.5 / 3.6),
        )

    def test_curve_points_are_sorted_scaled_and_invalid_rows_are_ignored(self):
        rows = [(10, 0.5), ("bad", 2), (0, 1), (float("nan"), 3)]

        self.assertEqual(
            [(0.0, 1.0), (36.0, 0.5)],
            CurvePreview.normalized_points(rows, x_scale=3.6),
        )
        self.assertEqual([], CurvePreview.normalized_points([]))

    def test_table_refresh_pushes_current_rows_to_preview(self):
        fake_tree = SimpleNamespace(
            get_children=mock.Mock(return_value=("first", "add")),
            set=mock.Mock(side_effect=lambda item, column: {
                ("first", "RPM"): "1500",
                ("first", "Torque"): "320",
            }[(item, column)]),
        )
        table = TorqueCurveTable.__new__(TorqueCurveTable)
        table.treeview = fake_tree
        table.add_button = "add"
        table.preview = mock.Mock()

        table._refresh_plot()

        table.preview.update_points.assert_called_once_with([("1500", "320")])

    def test_steering_refresh_preserves_carla_kph_values(self):
        fake_tree = SimpleNamespace(
            get_children=mock.Mock(return_value=("first", "add")),
            set=mock.Mock(side_effect=lambda item, column: {
                ("first", "Speed"): "10",
                ("first", "Steering Angle"): "0.7",
            }[(item, column)]),
        )
        table = SteeringCurveTable.__new__(SteeringCurveTable)
        table.treeview = fake_tree
        table.add_button = "add"
        table.preview = mock.Mock()

        table._refresh_plot()

        table.preview.update_points.assert_called_once_with([("10", "0.7")])


if __name__ == "__main__":
    unittest.main()
