NO_CONTROL = "No Control"
MANUAL_CONTROL = "Manual Control"
AUTOPILOT = "Autopilot"
UNKNOWN_CONTROL = "Unknown"

VEHICLE_CONTROL_MODES = (NO_CONTROL, MANUAL_CONTROL, AUTOPILOT)


def initial_vehicle_control_state():
    return {
        "mode": NO_CONTROL,
        "status": "No controller is active.",
        "autopilot_enabled": False,
        "autopilot_state_source": "local",
        "traffic_manager_port": None,
        "reverse": False,
        "input_fresh": False,
        "error": None,
    }


def unknown_vehicle_control_state():
    return {
        "mode": UNKNOWN_CONTROL,
        "status": "Control state is unknown for an external actor.",
        "autopilot_enabled": None,
        "autopilot_state_source": "unknown",
        "traffic_manager_port": None,
        "reverse": False,
        "input_fresh": False,
        "error": None,
    }
