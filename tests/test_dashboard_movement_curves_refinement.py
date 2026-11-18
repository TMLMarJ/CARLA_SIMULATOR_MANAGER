import queue
import unittest

from gui.windows.actor_editor import CurvePreview
from preview.movement import (
    MovementPreviewProcess,
    normalize_vehicle_telemetry,
    toggle_hud_visibility,
)
from simulation.dashboard import SimulationFpsTracker


class SimulationFpsTrackerTests(unittest.TestCase):
    def test_fixed_and_variable_steps_use_real_frame_progress(self):
        for frames in ((100, 105), (100, 103)):
            with self.subTest(frames=frames):
                tracker = SimulationFpsTracker()
                self.assertIsNone(tracker.observe(frames[0], 10.0))
                fps = tracker.observe(frames[1], 10.25)
                self.assertAlmostEqual((frames[1] - frames[0]) / 0.25, fps)

    def test_multiple_ticks_and_paused_decay_are_measured(self):
        tracker = SimulationFpsTracker(window_seconds=1.0)
        tracker.observe(10, 0.0)
        self.assertEqual(20.0, tracker.observe(20, 0.5))
        tracker.observe(20, 1.0)
        self.assertEqual(0.0, tracker.observe(20, 1.5))

    def test_reset_invalid_and_episode_frame_rollback_need_new_sample(self):
        tracker = SimulationFpsTracker()
        self.assertIsNone(tracker.observe(None, 0.0))
        tracker.observe(20, 1.0)
        self.assertIsNone(tracker.observe(1, 2.0))
        tracker.reset()
        self.assertIsNone(tracker.observe(2, 3.0))


class MovementHudTelemetryTests(unittest.TestCase):
    def test_actual_state_is_normalized_for_hud(self):
        telemetry = normalize_vehicle_telemetry({
            "mode": "Autopilot - Destination",
            "current_speed": 42.5 / 3.6,
            "gear": 3,
            "throttle": 1.4,
            "brake": -0.1,
            "steer": -0.25,
            "hand_brake": True,
        })

        self.assertAlmostEqual(42.5, telemetry["speed_kmh"])
        self.assertEqual(1.0, telemetry["throttle"])
        self.assertEqual(0.0, telemetry["brake"])
        self.assertEqual(-0.25, telemetry["steer"])
        self.assertEqual(3, telemetry["gear"])
        self.assertTrue(telemetry["hand_brake"])

    def test_h_key_toggles_visibility_and_telemetry_queue_keeps_latest(self):
        self.assertFalse(toggle_hud_visibility(True, "h"))
        self.assertTrue(toggle_hud_visibility(False, "H"))
        self.assertTrue(toggle_hud_visibility(True, "w"))

        process = MovementPreviewProcess.__new__(MovementPreviewProcess)
        process.process = object()
        process.telemetry_queue = queue.Queue(maxsize=1)
        process.publish_telemetry({"gear": 1})
        process.publish_telemetry({"gear": 2})
        self.assertEqual(2, process.telemetry_queue.get_nowait()["gear"])


class FakeCanvas:
    def __init__(self):
        self.lines = []
        self.texts = []

    def delete(self, _target):
        self.lines.clear()
        self.texts.clear()

    def winfo_width(self):
        return 500

    def winfo_height(self):
        return 300

    def create_line(self, *coordinates, **options):
        self.lines.append((coordinates, options))

    def create_text(self, *coordinates, **options):
        self.texts.append((coordinates, options))

    def create_oval(self, *coordinates, **options):
        pass


class CurvePreviewGridTests(unittest.TestCase):
    def preview(self, points):
        preview = object.__new__(CurvePreview)
        preview.canvas = FakeCanvas()
        preview.x_label = "RPM"
        preview.y_label = "Torque [N.m]"
        preview.points = points
        return preview

    def test_nice_ticks_cover_small_repeated_and_reversed_ranges(self):
        for minimum, maximum in ((5, 5), (0.00001, 0.00002), (10, -2)):
            axis_minimum, axis_maximum, ticks = CurvePreview.axis_ticks(
                minimum, maximum
            )
            self.assertLess(axis_minimum, axis_maximum)
            self.assertGreaterEqual(len(ticks), 2)
            self.assertLessEqual(axis_minimum, min(minimum, maximum))
            self.assertGreaterEqual(axis_maximum, max(minimum, maximum))

    def test_grid_and_tick_labels_are_drawn_below_curve(self):
        preview = self.preview([(1000.0, 200.0), (2000.0, 300.0)])

        preview.redraw()

        grid_lines = [
            line for line in preview.canvas.lines
            if line[1].get("fill") == "#E7DDD3"
        ]
        tick_labels = [
            text for _position, options in preview.canvas.texts
            if options.get("fill") == "#6E5A4D"
            for text in (options.get("text"),)
        ]
        self.assertGreaterEqual(len(grid_lines), 4)
        self.assertGreaterEqual(len(tick_labels), 4)
        self.assertEqual("#C96D3C", preview.canvas.lines[-1][1]["fill"])

    def test_empty_and_invalid_rows_remain_safe(self):
        preview = self.preview([])
        preview.redraw()

        self.assertIn(
            "No valid curve data",
            [options.get("text") for _position, options in preview.canvas.texts],
        )
        self.assertEqual([], CurvePreview.normalized_points([("bad", None)]))


if __name__ == "__main__":
    unittest.main()
