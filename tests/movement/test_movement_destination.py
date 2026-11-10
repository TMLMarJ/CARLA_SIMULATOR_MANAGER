import unittest
from types import SimpleNamespace

from core.state_store import DataStorage
from movement.destination import (
    DESTINATION_ADJUSTED,
    DESTINATION_UNAVAILABLE,
    DESTINATION_UNVERIFIED,
    DESTINATION_VALID,
    DestinationMapTransform,
    destination_result,
    nearest_map_point,
    point_from_values,
    validate_pedestrian_destination,
    validate_vehicle_destination,
)
from movement.session import MovementControlSessionManager
from scenarios.synchronization import WorldSynchronizer
from tests.scenarios.test_world_cleaner import FakeActor, FakeWorld


class Location:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = x
        self.y = y
        self.z = z


class FakeCarla:
    Location = Location
    LaneType = SimpleNamespace(Driving="Driving")


class Waypoint:
    def __init__(self, x, y, z):
        self.transform = SimpleNamespace(location=Location(x, y, z))


class WaypointMap:
    def __init__(self, exact=None, projected=None, xodr=None):
        self.exact = exact
        self.projected = projected
        self.xodr = xodr or {}
        self.calls = []

    def get_waypoint(self, _location, project_to_road, lane_type):
        self.calls.append((project_to_road, lane_type))
        return self.projected if project_to_road else self.exact

    def get_waypoint_xodr(self, road_id, lane_id, distance):
        self.calls.append((road_id, lane_id, distance))
        return self.xodr.get((road_id, lane_id))


class DestinationWorld:
    def __init__(self, world_map):
        self.world_map = world_map

    def get_map(self):
        return self.world_map


class CoordinateValidationTests(unittest.TestCase):
    def test_xyz_requires_finite_numeric_values(self):
        self.assertEqual(
            {"x": 1.0, "y": -2.5, "z": 3.25},
            point_from_values("1", "-2.5", 3.25),
        )
        for values in (("bad", 0, 0), (0, float("nan"), 0), (True, 0, 0)):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    point_from_values(*values)

    def test_vehicle_point_on_driving_lane_is_valid(self):
        world_map = WaypointMap(exact=Waypoint(1, 2, 0.2))
        status, requested, adjusted, _message = (
            validate_vehicle_destination(
                DestinationWorld(world_map),
                {"x": 1, "y": 2, "z": 0.2},
                FakeCarla,
            )
        )
        self.assertEqual(DESTINATION_VALID, status)
        self.assertEqual({"x": 1.0, "y": 2.0, "z": 0.2}, requested)
        self.assertIsNone(adjusted)
        self.assertEqual([(False, "Driving")], world_map.calls)

    def test_vehicle_point_can_be_adjusted_to_nearby_road(self):
        world_map = WaypointMap(projected=Waypoint(3, 4, 0))
        status, _requested, adjusted, message = validate_vehicle_destination(
            DestinationWorld(world_map),
            {"x": 0, "y": 0, "z": 0},
            FakeCarla,
        )
        self.assertEqual(DESTINATION_ADJUSTED, status)
        self.assertEqual({"x": 3.0, "y": 4.0, "z": 0.0}, adjusted)
        self.assertIn("5.00 m", message)

    def test_vehicle_point_without_nearby_road_is_unavailable(self):
        status, _requested, adjusted, _message = validate_vehicle_destination(
            DestinationWorld(WaypointMap(projected=Waypoint(30, 0, 0))),
            {"x": 0, "y": 0, "z": 0},
            FakeCarla,
        )
        self.assertEqual(DESTINATION_UNAVAILABLE, status)
        self.assertIsNone(adjusted)

    def test_explicit_lane_selector_uses_opendrive_waypoint(self):
        selected = Waypoint(10, -2, 0)
        world_map = WaypointMap(xodr={(7, -1): selected})

        status, requested, adjusted, _message = validate_vehicle_destination(
            DestinationWorld(world_map),
            {"x": 10, "y": -2, "z": 0},
            FakeCarla,
            waypoint_selector={"road_id": 7, "lane_id": -1, "s": 12.5},
        )

        self.assertEqual(DESTINATION_VALID, status)
        self.assertEqual({"x": 10.0, "y": -2.0, "z": 0.0}, requested)
        self.assertIsNone(adjusted)
        self.assertEqual([(7, -1, 12.5)], world_map.calls)

    def test_pedestrian_point_is_explicitly_unverified(self):
        status, requested, adjusted, message = validate_pedestrian_destination(
            {"x": 1, "y": 2, "z": 3}
        )
        self.assertEqual(DESTINATION_UNVERIFIED, status)
        self.assertEqual({"x": 1.0, "y": 2.0, "z": 3.0}, requested)
        self.assertIsNone(adjusted)
        self.assertIn("does not expose", message)


class MapTransformTests(unittest.TestCase):
    def setUp(self):
        self.points = [
            {"x": -100.0, "y": -50.0, "z": 1.0},
            {"x": 100.0, "y": 50.0, "z": 2.0},
        ]

    def test_world_canvas_round_trip_and_y_orientation(self):
        transform = DestinationMapTransform(self.points, 800, 400)
        canvas = transform.world_to_canvas(25.0, 10.0)
        restored = transform.canvas_to_world(*canvas)
        self.assertAlmostEqual(25.0, restored[0])
        self.assertAlmostEqual(10.0, restored[1])
        self.assertGreater(
            transform.world_to_canvas(0, 50)[1],
            transform.world_to_canvas(0, -50)[1],
        )

    def test_resize_preserves_world_coordinates(self):
        transform = DestinationMapTransform(self.points, 400, 400)
        first = transform.canvas_to_world(
            *transform.world_to_canvas(-25.0, 30.0)
        )
        transform.resize(900, 500)
        second = transform.canvas_to_world(
            *transform.world_to_canvas(-25.0, 30.0)
        )
        self.assertAlmostEqual(first[0], second[0])
        self.assertAlmostEqual(first[1], second[1])

    def test_nearest_sample_provides_click_height(self):
        nearest = nearest_map_point(self.points, 90.0, 40.0)
        self.assertEqual(2.0, nearest["z"])


class DestinationLifecycleTests(unittest.TestCase):
    def test_destination_survives_session_close_and_is_removed_with_actor(self):
        actor = FakeActor(7, "vehicle.test")
        world = FakeWorld([actor], world_id=5)
        database = DataStorage()
        database.add_actor(actor, "Vehicle")
        destination = destination_result(
            actor.id,
            "vehicle",
            world.id,
            {"x": 10, "y": 20, "z": 0},
            DESTINATION_VALID,
            "Valid",
        )
        database.set_movement_destination(actor.id, destination)

        session = MovementControlSessionManager.__new__(
            MovementControlSessionManager
        )
        session.manager = SimpleNamespace(database=database)
        session._reset_session()
        self.assertEqual(
            destination,
            database.get_movement_destination(actor.id),
        )

        actor.is_alive = False
        WorldSynchronizer().synchronize(
            world,
            database,
            reason="actor_removed",
        )
        self.assertIsNone(database.get_movement_destination(actor.id))

    def test_map_change_state_clear_invalidates_destinations(self):
        database = DataStorage()
        database.vehicle_control_states[10] = {"destination": {"x": 1}}
        database.walker_ai_states[11] = {"destination": {"x": 2}}
        with database.lock:
            database.vehicle_control_states.clear()
            database.walker_ai_states.clear()
        self.assertIsNone(database.get_movement_destination(10))
        self.assertIsNone(database.get_movement_destination(11))


if __name__ == "__main__":
    unittest.main()
