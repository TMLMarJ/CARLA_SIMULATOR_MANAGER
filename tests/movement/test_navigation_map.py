import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from core.controller import CarlaController
from gui.widgets.navigation_map import (
    NavigationMapViewport,
    heading_triangle,
    route_locations,
)
from movement.destination import waypoint_candidates
from movement.session import MovementControlSessionManager


class NavigationMapViewportTests(unittest.TestCase):
    def setUp(self):
        self.viewport = NavigationMapViewport(
            bounds=(-100.0, -50.0, 100.0, 50.0),
            image_size=(800, 600),
            margin=40,
            canvas_size=(400, 300),
        )

    def test_world_canvas_round_trip_and_marker_position(self):
        canvas = self.viewport.world_to_canvas(25.0, -10.0)
        restored = self.viewport.canvas_to_world(*canvas)

        self.assertAlmostEqual(25.0, restored[0])
        self.assertAlmostEqual(-10.0, restored[1])
        self.assertGreater(canvas[0], self.viewport.canvas_width / 2.0)

    def test_known_canvas_axes_match_carla_world_axes(self):
        origin = self.viewport.world_to_canvas(0.0, 0.0)
        positive_x = self.viewport.world_to_canvas(10.0, 0.0)
        positive_y = self.viewport.world_to_canvas(0.0, 10.0)
        negative_y = self.viewport.world_to_canvas(0.0, -10.0)

        self.assertEqual((200.0, 150.0), origin)
        self.assertGreater(positive_x[0], origin[0])
        self.assertGreater(positive_y[1], origin[1])
        self.assertLess(negative_y[1], origin[1])

    def test_zoom_keeps_world_point_under_cursor_and_pan_moves_it(self):
        cursor = self.viewport.world_to_canvas(30.0, 10.0)
        before = self.viewport.canvas_to_world(*cursor)

        self.assertTrue(self.viewport.zoom_at(1.2, *cursor))
        after = self.viewport.canvas_to_world(*cursor)
        self.assertAlmostEqual(before[0], after[0])
        self.assertAlmostEqual(before[1], after[1])

        marker_before = self.viewport.world_to_canvas(30.0, 10.0)
        self.viewport.pan(15.0, -8.0)
        marker_after = self.viewport.world_to_canvas(30.0, 10.0)
        self.assertAlmostEqual(15.0, marker_after[0] - marker_before[0])
        self.assertAlmostEqual(-8.0, marker_after[1] - marker_before[1])

    def test_heading_marker_uses_carla_yaw(self):
        east = heading_triangle(100.0, 100.0, 0.0)
        right_turn = heading_triangle(100.0, 100.0, 90.0)
        west = heading_triangle(100.0, 100.0, 180.0)
        left_turn = heading_triangle(100.0, 100.0, -90.0)

        self.assertGreater(east[0], 100.0)
        self.assertAlmostEqual(100.0, east[1])
        self.assertAlmostEqual(100.0, right_turn[0])
        self.assertGreater(right_turn[1], 100.0)
        self.assertLess(west[0], 100.0)
        self.assertLess(left_turn[1], 100.0)


class NavigationMapDataTests(unittest.TestCase):
    def test_clicks_on_left_and_right_select_corresponding_lane(self):
        points = [
            {
                "x": 0.0, "y": 3.0, "z": 0.0,
                "road_id": 8, "section_id": 0, "lane_id": 1,
                "s": 15.0, "yaw": 0.0,
            },
            {
                "x": 0.0, "y": -3.0, "z": 0.0,
                "road_id": 8, "section_id": 0, "lane_id": -1,
                "s": 15.0, "yaw": 180.0,
            },
        ]

        right = waypoint_candidates(points, 0.0, 3.0, radius=1.0)
        left = waypoint_candidates(points, 0.0, -3.0, radius=1.0)

        self.assertEqual([1], [candidate["lane_id"] for candidate in right])
        self.assertEqual([-1], [candidate["lane_id"] for candidate in left])

    def test_waypoint_selection_keeps_opposite_lanes_separate(self):
        points = [
            {
                "x": 10.0, "y": 1.0, "z": 0.0,
                "road_id": 4, "section_id": 0, "lane_id": 1,
                "s": 10.0, "yaw": 0.0,
            },
            {
                "x": 10.0, "y": -1.0, "z": 0.0,
                "road_id": 4, "section_id": 0, "lane_id": -1,
                "s": 10.0, "yaw": 180.0,
            },
        ]

        candidates = waypoint_candidates(points, 10.0, 0.0)

        self.assertEqual({-1, 1}, {item["lane_id"] for item in candidates})
        self.assertEqual({0.0, 180.0}, {item["yaw"] for item in candidates})

    def test_route_updates_and_clears_without_fallback_line(self):
        route = {
            "waypoints": [
                {"location": {"x": 0.0, "y": 0.0, "z": 0.0}},
                {"location": {"x": 5.0, "y": 2.0, "z": 0.0}},
            ]
        }
        self.assertEqual(2, len(route_locations(route)))
        self.assertEqual([], route_locations(None))
        self.assertEqual([], route_locations({"waypoints": []}))


class NavigationRefreshTests(unittest.TestCase):
    def test_open_rejects_actor_snapshot_from_previous_world(self):
        database = SimpleNamespace(
            lock=threading.Lock(),
            world_id=12,
            world_generation=4,
            vehicle_control_states={7: {}},
            walker_ai_states={},
            actors_db=SimpleNamespace(actors_db={
                7: SimpleNamespace(
                    world_id=11,
                    location=SimpleNamespace(x=1.0, y=2.0, z=0.0),
                ),
            }),
        )
        session = MovementControlSessionManager(
            SimpleNamespace(database=database),
            window_factory=Mock(),
        )
        session.preview.start = Mock(
            side_effect=AssertionError("preview must not start")
        )

        self.assertFalse(session.open(7, "vehicle.test"))
        self.assertIsNone(session.session_id)
        session.preview.start.assert_not_called()

    def test_refresh_is_rate_limited_and_stops_when_session_closes(self):
        session = MovementControlSessionManager.__new__(
            MovementControlSessionManager
        )
        carla_controller = SimpleNamespace(
            get_movement_navigation_snapshot=Mock(return_value=True)
        )
        database = SimpleNamespace(
            lock=threading.Lock(),
            world_id=11,
            world_generation=3,
        )
        session.manager = SimpleNamespace(
            carla=carla_controller,
            database=database,
        )
        session.closing = False
        session.session_id = "session"
        session.actor_id = 7
        session.world_id = 11
        session.world_generation = 3
        session.navigation_snapshot_pending = False
        session.navigation_snapshot_next_at = 0.0

        self.assertTrue(session._request_navigation_snapshot(now=10.0))
        self.assertFalse(session._request_navigation_snapshot(now=10.1))
        session.navigation_snapshot_pending = False
        self.assertFalse(session._request_navigation_snapshot(now=10.2))
        self.assertTrue(session._request_navigation_snapshot(now=10.3))

        session.navigation_snapshot_pending = False
        session.closing = True
        self.assertFalse(session._request_navigation_snapshot(now=11.0))
        self.assertEqual(
            2,
            carla_controller.get_movement_navigation_snapshot.call_count,
        )

    def test_refresh_stops_when_world_generation_changes(self):
        session = MovementControlSessionManager.__new__(
            MovementControlSessionManager
        )
        carla_controller = SimpleNamespace(
            get_movement_navigation_snapshot=Mock(return_value=True)
        )
        database = SimpleNamespace(
            lock=threading.Lock(),
            world_id=11,
            world_generation=4,
        )
        session.manager = SimpleNamespace(
            carla=carla_controller,
            database=database,
        )
        session.closing = False
        session.session_id = "session"
        session.actor_id = 7
        session.world_id = 11
        session.world_generation = 3
        session.navigation_snapshot_pending = False
        session.navigation_snapshot_next_at = 0.0
        session.request_close = Mock()

        self.assertFalse(session._request_navigation_snapshot(now=10.0))
        session.request_close.assert_called_once_with(
            "CARLA world episode changed"
        )
        carla_controller.get_movement_navigation_snapshot.assert_not_called()

    def test_pending_refresh_does_not_hide_world_generation_change(self):
        session = MovementControlSessionManager.__new__(
            MovementControlSessionManager
        )
        carla_controller = SimpleNamespace(
            get_movement_navigation_snapshot=Mock(return_value=True)
        )
        session.manager = SimpleNamespace(
            carla=carla_controller,
            database=SimpleNamespace(
                lock=threading.Lock(),
                world_id=11,
                world_generation=4,
            ),
        )
        session.closing = False
        session.session_id = "session"
        session.actor_id = 7
        session.world_id = 11
        session.world_generation = 3
        session.navigation_snapshot_pending = True
        session.navigation_snapshot_next_at = 0.0
        session.request_close = Mock()

        self.assertFalse(session._request_navigation_snapshot(now=10.0))
        session.request_close.assert_called_once_with(
            "CARLA world episode changed"
        )
        carla_controller.get_movement_navigation_snapshot.assert_not_called()

    def test_initial_actor_location_is_copied_to_primitive_snapshot(self):
        location = SimpleNamespace(x=1.25, y=-2.5, z=0.75)

        snapshot = MovementControlSessionManager._location_snapshot(location)

        self.assertEqual(
            {"x": 1.25, "y": -2.5, "z": 0.75},
            snapshot,
        )
        self.assertIsNot(snapshot, location)

    def test_late_snapshot_from_previous_generation_is_ignored(self):
        session = MovementControlSessionManager.__new__(
            MovementControlSessionManager
        )
        event_queue = queue.Queue()
        event_queue.put((
            "movement_navigation_snapshot",
            {
                "session_id": "session",
                "world_id": 11,
                "world_generation": 2,
                "snapshot": {"actor_id": 7},
            },
        ))
        database = SimpleNamespace(
            lock=threading.Lock(),
            movement_event_queue=event_queue,
            world_id=11,
            world_generation=3,
        )
        session.manager = SimpleNamespace(database=database)
        session.session_id = "session"
        session.world_id = 11
        session.world_generation = 3
        session.window = SimpleNamespace(
            set_navigation_snapshot=Mock()
        )
        session.closing = False
        session.navigation_snapshot_pending = True

        session._poll_controller_events()

        session.window.set_navigation_snapshot.assert_not_called()
        self.assertTrue(session.navigation_snapshot_pending)


class NavigationSnapshotRuntimeTests(unittest.TestCase):
    def test_snapshot_contains_position_orientation_and_navigation_data(self):
        controller = object.__new__(CarlaController)
        controller.database = SimpleNamespace(
            lock=threading.Lock(),
            vehicle_control_states={7: {
                "status": "Navigating",
                "navigation_status": "Navigating",
                "destination": {"selected": {"x": 5.0, "y": 2.0, "z": 0.0}},
                "route": {"waypoints": []},
            }},
            walker_ai_states={},
        )
        actor = SimpleNamespace(
            get_transform=lambda: SimpleNamespace(
                location=SimpleNamespace(x=1.0, y=2.0, z=0.5),
                rotation=SimpleNamespace(yaw=135.0),
            )
        )

        snapshot = controller._movement_navigation_snapshot(
            7, actor, "vehicle", 11
        )

        self.assertEqual(7, snapshot["actor_id"])
        self.assertEqual({"x": 1.0, "y": 2.0, "z": 0.5}, snapshot["location"])
        self.assertEqual(135.0, snapshot["yaw"])
        self.assertEqual("Navigating", snapshot["navigation_status"])
        self.assertIsNotNone(snapshot["destination"])

    def test_missing_actor_returns_snapshot_error_instead_of_gui_rpc(self):
        controller = object.__new__(CarlaController)
        controller.database = SimpleNamespace(
            lock=threading.Lock(),
            world_id=11,
            world_generation=3,
            actors_db=SimpleNamespace(actors={}, actors_db={}),
            vehicle_control_states={},
            walker_ai_states={},
            movement_event_queue=queue.Queue(),
            is_actor_managed=lambda _actor_id: True,
        )
        controller.dashboard_monitor = SimpleNamespace(
            record_error=lambda _error: None
        )
        controller.movement_preview_session_id = "session"
        controller.movement_preview_actor_id = 7

        controller._get_movement_navigation_snapshot(
            SimpleNamespace(id=11),
            {
                "session_id": "session",
                "actor_id": 7,
                "world_id": 11,
                "world_generation": 3,
            },
        )

        status, data = controller.database.movement_event_queue.get_nowait()
        self.assertEqual("movement_navigation_snapshot_error", status)
        self.assertEqual(7, data["actor_id"])


if __name__ == "__main__":
    unittest.main()
