# Canonical implementation; the legacy path is a compatibility shim.
NO_CONTROL = "No Control"
MANUAL_CONTROL = "Manual Control"
AUTOPILOT = "Autopilot"
AUTOPILOT_FREE_ROAM = "Autopilot - Free Roam"
AUTOPILOT_DESTINATION = "Autopilot - Destination"
UNKNOWN_CONTROL = "Unknown"

VEHICLE_CONTROL_MODES = (
    NO_CONTROL,
    MANUAL_CONTROL,
    AUTOPILOT_FREE_ROAM,
    AUTOPILOT_DESTINATION,
)

VEHICLE_STATUS_IDLE = "Idle"
VEHICLE_STATUS_NAVIGATING = "Navigating"
VEHICLE_STATUS_ARRIVED = "Arrived"
VEHICLE_STATUS_STUCK = "Stuck"
VEHICLE_STATUS_FAILED = "Failed"
VEHICLE_STATUS_CANCELLED = "Cancelled"


def normalize_vehicle_control_mode(mode):
    """Map the pre-Stage-3C Autopilot value to the Free Roam mode."""
    return AUTOPILOT_FREE_ROAM if mode == AUTOPILOT else mode


def initial_vehicle_control_state():
    return {
        "mode": NO_CONTROL,
        "control_mode": NO_CONTROL,
        "status": "No controller is active.",
        "autopilot_enabled": False,
        "autopilot_state_source": "local",
        "traffic_manager_port": None,
        "reverse": False,
        "input_fresh": False,
        "error": None,
        "destination": None,
        "navigation_status": VEHICLE_STATUS_IDLE,
        "route": None,
        "navigation_debug": None,
        "target_waypoint": None,
        "current_waypoint": None,
        "distance_to_destination": None,
        "current_speed": 0.0,
        "world_id": None,
    }


def unknown_vehicle_control_state():
    return {
        "mode": UNKNOWN_CONTROL,
        "control_mode": UNKNOWN_CONTROL,
        "status": "Control state is unknown for an external actor.",
        "autopilot_enabled": None,
        "autopilot_state_source": "unknown",
        "traffic_manager_port": None,
        "reverse": False,
        "input_fresh": False,
        "error": None,
        "destination": None,
        "navigation_status": VEHICLE_STATUS_IDLE,
        "route": None,
        "navigation_debug": None,
        "target_waypoint": None,
        "current_waypoint": None,
        "distance_to_destination": None,
        "current_speed": 0.0,
        "world_id": None,
    }
