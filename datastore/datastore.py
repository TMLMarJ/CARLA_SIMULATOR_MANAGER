from actors import actors
import carla
import queue
import threading

from simulation_manager import SimulationManager
from simulation_dashboard import SimulationDashboardMonitor
from vehicle_control import initial_vehicle_control_state
from walker_control import initial_walker_ai_state

class DataStorage:
    def __init__(self):
        self.actors_db = actors.Actors()
        self.sensors_db = actors.Sensors()
        self.lock = threading.Lock()
        self.sensor_event_queue = queue.Queue()
        self.movement_event_queue = queue.Queue()
        self.vehicle_control_states = {}
        self.walker_ai_states = {}
        self.simulation_event_queue = queue.Queue()
        self.blueprint_lib = None
        self.world = None
        self.ghost_data = None
        self.ghost_camera = None
        self.ghost_spawned = False
        self.ghost_physic = None
        self.world_lib = None
        self.actual_weather_data = None
        self.weather_presets = None
        self.world_changed = False
        self.world_change_error = None
        self.available_spawn_points = []
        self.spawned_actor_id = None
        self.blueprint_new_data = None
        self.physics_applied = False
        self.actual_simulation_settings = None
        self.proposed_simulation_settings = None
        self.simulation_control_state = {
            "state": SimulationManager.DISCONNECTED,
            "tick_controller": SimulationManager.EXTERNAL_CONTROLLER,
            "synchronous_mode": False,
            "remaining_steps": 0,
            "last_frame": None,
            "error": None,
        }
        self.simulation_snapshot = (
            SimulationDashboardMonitor.empty_simulation_snapshot()
        )
        self.actor_statistics = (
            SimulationDashboardMonitor.empty_actor_statistics()
        )
        self.sensor_statistics = (
            SimulationDashboardMonitor.empty_sensor_statistics()
        )
        self.application_status = (
            SimulationDashboardMonitor.empty_application_status()
        )

    def add_actor(self, actor: carla.Actor, name: str):
        self.actors_db.add_actor(actor, name)
        if actor.type_id.startswith("vehicle."):
            self.vehicle_control_states[actor.id] = (
                initial_vehicle_control_state()
            )
        elif actor.type_id.startswith("walker."):
            self.walker_ai_states[actor.id] = initial_walker_ai_state()

    def add_sensor(self, sensor: carla.Sensor, parent_id: int, name: str,
                   blueprint_id: str, relative_transform: carla.Transform,
                   attributes: dict):
        self.sensors_db.add_sensor(
            sensor,
            parent_id,
            name,
            blueprint_id,
            relative_transform,
            attributes
        )

    def data_ready(self):
        return (
            self.blueprint_lib is not None and
            self.world is not None and
            self.world_lib is not None and
            self.actual_weather_data is not None and
            self.weather_presets is not None and
            self.actual_simulation_settings is not None
        )
