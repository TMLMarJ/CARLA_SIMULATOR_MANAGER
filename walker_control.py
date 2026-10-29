WALKER_AI_STOPPED = "Stopped"
WALKER_AI_RUNNING = "Running"
WALKER_AI_ERROR = "Error"

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
        "random_navigation": True,
        "error": None,
    }
