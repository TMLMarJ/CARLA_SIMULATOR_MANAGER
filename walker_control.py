WALKER_AI_STOPPED = "Stopped"
WALKER_AI_RUNNING = "Running"
WALKER_AI_ERROR = "Error"

WALKER_CONTROL_MANUAL = "Manual"
WALKER_CONTROL_RANDOM = "Automatic - Random Walk"
WALKER_CONTROL_DESTINATION = "Automatic - Destination"
WALKER_CONTROL_MODES = (
    WALKER_CONTROL_MANUAL,
    WALKER_CONTROL_RANDOM,
    WALKER_CONTROL_DESTINATION,
)

WALKER_STATUS_IDLE = "Idle"
WALKER_STATUS_MOVING = "Moving"
WALKER_STATUS_ARRIVED = "Arrived"
WALKER_STATUS_STUCK = "Stuck"
WALKER_STATUS_FAILED = "Failed"
WALKER_STATUS_CANCELLED = "Cancelled"

DEFAULT_WALKER_SPEED = 1.4
MIN_WALKER_SPEED = 0.1
MAX_WALKER_SPEED = 10.0


def initial_walker_ai_state():
    return {
        "mode": WALKER_AI_STOPPED,
        "active": False,
        "status": "Walker AI is stopped.",
        "speed": DEFAULT_WALKER_SPEED,
        "controller_id": None,
        "target": None,
        "random_navigation": False,
        "error": None,
        "destination": None,
        "control_mode": WALKER_CONTROL_MANUAL,
        "movement_status": WALKER_STATUS_IDLE,
        "manual_direction": {"x": 1.0, "y": 0.0},
    }
