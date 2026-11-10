# Canonical implementation; the legacy path is a compatibility shim.
import math

from preview.map import MapImageTransform


DESTINATION_EMPTY = "empty"
DESTINATION_UNVALIDATED = "unvalidated"
DESTINATION_VALID = "valid"
DESTINATION_ADJUSTED = "adjusted"
DESTINATION_UNAVAILABLE = "unavailable"
DESTINATION_UNVERIFIED = "unverified"
DESTINATION_ERROR = "error"

CONFIRMABLE_DESTINATION_STATUSES = {
    DESTINATION_VALID,
    DESTINATION_ADJUSTED,
    DESTINATION_UNVERIFIED,
}

VEHICLE_DESTINATION_MAX_SNAP_METERS = 10.0
MAP_WAYPOINT_DISTANCE_METERS = 4.0
WAYPOINT_SELECTION_RADIUS_METERS = 6.0


def empty_destination(actor_id, category, world_id=None):
    return {
        "actor_id": int(actor_id),
        "category": str(category),
        "world_id": world_id,
        "requested": None,
        "adjusted": None,
        "selected": None,
        "target_waypoint": None,
        "validation_status": DESTINATION_EMPTY,
        "message": "No destination selected.",
        "confirmed": False,
    }


def point_from_values(x, y, z):
    values = {}
    for axis, value in (("x", x), ("y", y), ("z", z)):
        if isinstance(value, bool):
            raise ValueError(f"Destination {axis.upper()} must be numeric.")
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Destination {axis.upper()} must be numeric."
            ) from error
        if not math.isfinite(number):
            raise ValueError(
                f"Destination {axis.upper()} must be a finite number."
            )
        values[axis] = number
    return values


def point_from_location(location):
    return point_from_values(location.x, location.y, location.z)


def waypoint_snapshot(waypoint, road_option=None):
    """Return GUI-safe waypoint data without retaining a CARLA proxy."""
    if waypoint is None:
        return None
    transform = waypoint.transform
    rotation = getattr(transform, "rotation", None)
    lane_id = int(getattr(waypoint, "lane_id", 0))
    snapshot = {
        "road_id": int(getattr(waypoint, "road_id", 0)),
        "section_id": int(getattr(waypoint, "section_id", 0)),
        "lane_id": lane_id,
        "lane_direction": 1 if lane_id > 0 else -1 if lane_id < 0 else 0,
        "s": float(getattr(waypoint, "s", 0.0)),
        "is_junction": bool(getattr(waypoint, "is_junction", False)),
        "location": point_from_location(transform.location),
        "yaw": (
            None if rotation is None
            else float(getattr(rotation, "yaw", 0.0))
        ),
    }
    if road_option is not None:
        snapshot["road_option"] = str(road_option)
    return snapshot


def same_waypoint_segment(first, second):
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    return all(
        first.get(field) == second.get(field)
        for field in ("road_id", "section_id", "lane_id")
    )


def waypoint_candidates(points, x, y, radius=None):
    """Return the nearest sample for each plausible driving lane."""
    if not points:
        return []
    x = float(x)
    y = float(y)
    ranked = []
    for point in points:
        try:
            distance = math.hypot(float(point["x"]) - x, float(point["y"]) - y)
        except (KeyError, TypeError, ValueError):
            continue
        ranked.append((distance, point))
    if not ranked:
        return []
    ranked.sort(key=lambda item: item[0])
    limit = (
        WAYPOINT_SELECTION_RADIUS_METERS
        if radius is None else max(0.0, float(radius))
    )
    limit = max(limit, ranked[0][0] + 0.5)
    lanes = {}
    for distance, point in ranked:
        if distance > limit:
            break
        key = (
            int(point.get("road_id", 0)),
            int(point.get("section_id", 0)),
            int(point.get("lane_id", 0)),
        )
        if key not in lanes:
            candidate = dict(point)
            candidate["distance"] = distance
            lanes[key] = candidate
    return sorted(lanes.values(), key=lambda point: point["distance"])


def resolve_vehicle_waypoint(world, point, carla_module,
                             waypoint_selector=None, project_to_road=True):
    world_map = world.get_map()
    if isinstance(waypoint_selector, dict):
        get_waypoint_xodr = getattr(world_map, "get_waypoint_xodr", None)
        if callable(get_waypoint_xodr):
            try:
                waypoint = get_waypoint_xodr(
                    int(waypoint_selector["road_id"]),
                    int(waypoint_selector["lane_id"]),
                    float(waypoint_selector["s"]),
                )
            except (KeyError, TypeError, ValueError):
                waypoint = None
            if waypoint is not None:
                return waypoint
    location = carla_module.Location(**point)
    lane_type = getattr(getattr(carla_module, "LaneType", None), "Driving", None)
    kwargs = {"project_to_road": bool(project_to_road)}
    if lane_type is not None:
        kwargs["lane_type"] = lane_type
    return world_map.get_waypoint(location, **kwargs)


def point_distance(first, second):
    return math.sqrt(sum(
        (float(first[axis]) - float(second[axis])) ** 2
        for axis in ("x", "y", "z")
    ))


def destination_result(actor_id, category, world_id, requested, status,
                       message, adjusted=None, confirmed=False):
    requested = point_from_values(
        requested["x"], requested["y"], requested["z"]
    )
    adjusted_point = None
    if adjusted is not None:
        adjusted_point = point_from_values(
            adjusted["x"], adjusted["y"], adjusted["z"]
        )
    selected = adjusted_point or requested
    return {
        "actor_id": int(actor_id),
        "category": str(category),
        "world_id": world_id,
        "requested": requested,
        "adjusted": adjusted_point,
        "selected": selected if confirmed else None,
        "target_waypoint": None,
        "validation_status": str(status),
        "message": str(message),
        "confirmed": bool(confirmed),
    }


def confirm_destination(destination):
    if not isinstance(destination, dict):
        raise ValueError("Validate a destination before confirming it.")
    status = destination.get("validation_status")
    if status not in CONFIRMABLE_DESTINATION_STATUSES:
        raise ValueError("The destination is not valid for confirmation.")
    confirmed = dict(destination)
    confirmed["selected"] = (
        destination.get("adjusted") or destination.get("requested")
    )
    confirmed["confirmed"] = True
    confirmed["message"] = (
        f"{destination.get('message', '')} Destination confirmed."
    ).strip()
    return confirmed


def validate_vehicle_destination(world, point, carla_module,
                                 max_snap_distance=None,
                                 waypoint_selector=None):
    requested = point_from_values(point["x"], point["y"], point["z"])
    threshold = (
        VEHICLE_DESTINATION_MAX_SNAP_METERS
        if max_snap_distance is None else float(max_snap_distance)
    )
    waypoint = resolve_vehicle_waypoint(
        world,
        requested,
        carla_module,
        waypoint_selector=waypoint_selector,
        project_to_road=False,
    )
    if waypoint is not None:
        road_point = point_from_location(waypoint.transform.location)
        return DESTINATION_VALID, requested, None, (
            "Destination is on a driving lane. A valid waypoint does not "
            "guarantee that a complete route exists."
        )

    waypoint = resolve_vehicle_waypoint(
        world,
        requested,
        carla_module,
        waypoint_selector=waypoint_selector,
        project_to_road=True,
    )
    if waypoint is None:
        return (
            DESTINATION_UNAVAILABLE,
            requested,
            None,
            "No driving waypoint is available near the destination.",
        )
    adjusted = point_from_location(waypoint.transform.location)
    distance = point_distance(requested, adjusted)
    if distance > threshold:
        return (
            DESTINATION_UNAVAILABLE,
            requested,
            None,
            f"Nearest driving waypoint is {distance:.2f} m away, beyond "
            f"the {threshold:.2f} m correction limit.",
        )
    return (
        DESTINATION_ADJUSTED,
        requested,
        adjusted,
        f"Destination was corrected by {distance:.2f} m to the nearest "
        "driving waypoint. Route availability is not guaranteed.",
    )


def validate_pedestrian_destination(point):
    requested = point_from_values(point["x"], point["y"], point["z"])
    return (
        DESTINATION_UNVERIFIED,
        requested,
        None,
        "CARLA 0.9.16 does not expose an API that validates an arbitrary "
        "point against the pedestrian navigation mesh. Coordinates are "
        "valid, but navigation availability is unverified.",
    )


class DestinationMapTransform:
    """Compatibility wrapper around the shared OpenDRIVE image transform."""
    def __init__(self, points, width, height, padding=24.0):
        if not points:
            raise ValueError("Map preview requires at least one waypoint.")
        self.padding = max(0.0, float(padding))
        self.min_x = min(float(point["x"]) for point in points)
        self.max_x = max(float(point["x"]) for point in points)
        self.min_y = min(float(point["y"]) for point in points)
        self.max_y = max(float(point["y"]) for point in points)
        self.resize(width, height)

    def resize(self, width, height):
        self.width = max(1.0, float(width))
        self.height = max(1.0, float(height))
        self._transform = MapImageTransform(
            (self.min_x, self.min_y, self.max_x, self.max_y),
            (int(self.width), int(self.height)),
            self.padding,
        )
        self.scale = self._transform.scale
        self.offset_x = self._transform.offset_x
        self.offset_y = self._transform.offset_y

    def world_to_canvas(self, x, y):
        return self._transform.world_to_image(x, y)

    def canvas_to_world(self, canvas_x, canvas_y):
        return self._transform.image_to_world(canvas_x, canvas_y)


def nearest_map_point(points, x, y):
    if not points:
        return None
    return min(
        points,
        key=lambda point: (
            (float(point["x"]) - float(x)) ** 2
            + (float(point["y"]) - float(y)) ** 2
        ),
    )
