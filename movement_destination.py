import math


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


def empty_destination(actor_id, category, world_id=None):
    return {
        "actor_id": int(actor_id),
        "category": str(category),
        "world_id": world_id,
        "requested": None,
        "adjusted": None,
        "selected": None,
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
                                 max_snap_distance=None):
    requested = point_from_values(point["x"], point["y"], point["z"])
    threshold = (
        VEHICLE_DESTINATION_MAX_SNAP_METERS
        if max_snap_distance is None else float(max_snap_distance)
    )
    location = carla_module.Location(**requested)
    world_map = world.get_map()
    lane_type = getattr(getattr(carla_module, "LaneType", None), "Driving", None)
    kwargs = {"project_to_road": False}
    if lane_type is not None:
        kwargs["lane_type"] = lane_type
    waypoint = world_map.get_waypoint(location, **kwargs)
    if waypoint is not None:
        road_point = point_from_location(waypoint.transform.location)
        return DESTINATION_VALID, requested, None, (
            "Destination is on a driving lane. A valid waypoint does not "
            "guarantee that a complete route exists."
        )

    kwargs["project_to_road"] = True
    waypoint = world_map.get_waypoint(location, **kwargs)
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
        world_width = max(self.max_x - self.min_x, 1e-9)
        world_height = max(self.max_y - self.min_y, 1e-9)
        drawable_width = max(self.width - 2.0 * self.padding, 1.0)
        drawable_height = max(self.height - 2.0 * self.padding, 1.0)
        self.scale = min(
            drawable_width / world_width,
            drawable_height / world_height,
        )
        rendered_width = world_width * self.scale
        rendered_height = world_height * self.scale
        self.offset_x = (self.width - rendered_width) / 2.0
        self.offset_y = (self.height - rendered_height) / 2.0

    def world_to_canvas(self, x, y):
        return (
            self.offset_x + (float(x) - self.min_x) * self.scale,
            self.offset_y + (self.max_y - float(y)) * self.scale,
        )

    def canvas_to_world(self, canvas_x, canvas_y):
        return (
            self.min_x + (float(canvas_x) - self.offset_x) / self.scale,
            self.max_y - (float(canvas_y) - self.offset_y) / self.scale,
        )


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
