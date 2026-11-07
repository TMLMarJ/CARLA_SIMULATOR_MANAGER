from actors import actors
import carla
import queue
import threading
import copy

from simulation_manager import SimulationManager
from simulation_dashboard import SimulationDashboardMonitor
from vehicle_control import (
    initial_vehicle_control_state,
    unknown_vehicle_control_state,
)
from walker_control import initial_walker_ai_state
from scenario_management.models import ActorOwnership
from movement_destination import empty_destination

class DataStorage:
    def __init__(self):
        self.actors_db = actors.Actors()
        self.sensors_db = actors.Sensors()
        self.lock = threading.Lock()
        self.sensor_event_queue = queue.Queue()
        self.actor_event_queue = queue.Queue()
        self.movement_event_queue = queue.Queue()
        self.vehicle_control_states = {}
        self.walker_ai_states = {}
        self.simulation_event_queue = queue.Queue()
        self.scenario_event_queue = queue.Queue()
        self.blueprint_lib = None
        self.pedestrian_blueprint_ids = []
        self.world = None
        self.ghost_data = None
        self.ghost_camera = None
        self.ghost_spawned = False
        self.ghost_physic = None
        self.ghost_preview_request_id = None
        self.ghost_preview_requested_enabled = False
        self.world_lib = None
        self.actual_weather_data = None
        self.weather_presets = None
        self.world_changed = False
        self.world_change_error = None
        self.available_spawn_points = []
        self.spawned_actor_id = None
        self.actor_operation_result = None
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

    def add_actor(self, actor: carla.Actor, name: str, **metadata):
        actor_info = self.actors_db.add_actor(actor, name, **metadata)
        if (
            actor_info.ownership == ActorOwnership.MANAGED
            and actor.type_id.startswith("vehicle.")
        ):
            state = initial_vehicle_control_state()
            state["destination"] = empty_destination(
                actor.id,
                "vehicle",
                actor_info.world_id,
            )
            state["world_id"] = actor_info.world_id
            self.vehicle_control_states[actor.id] = state
        elif (
            actor_info.ownership == ActorOwnership.MANAGED
            and actor.type_id.startswith("walker.")
        ):
            state = initial_walker_ai_state()
            state["destination"] = empty_destination(
                actor.id,
                "pedestrian",
                actor_info.world_id,
            )
            self.walker_ai_states[actor.id] = state
        return actor_info

    def get_movement_destination(self, actor_id):
        actor_id = int(actor_id)
        with self.lock:
            state = self.vehicle_control_states.get(actor_id)
            if state is None:
                state = self.walker_ai_states.get(actor_id)
            destination = None if state is None else state.get("destination")
            return copy.deepcopy(destination)

    def set_movement_destination(self, actor_id, destination):
        actor_id = int(actor_id)
        with self.lock:
            state = self.vehicle_control_states.get(actor_id)
            if state is None:
                state = self.walker_ai_states.get(actor_id)
            if state is None:
                raise KeyError(
                    f"Actor {actor_id} has no managed movement state."
                )
            state["destination"] = copy.deepcopy(destination)
            return copy.deepcopy(state["destination"])

    def add_sensor(self, sensor: carla.Sensor, parent_id: int, name: str,
                   blueprint_id: str, relative_transform: carla.Transform,
                   attributes: dict, **metadata):
        return self.sensors_db.add_sensor(
            sensor,
            parent_id,
            name,
            blueprint_id,
            relative_transform,
            attributes,
            **metadata,
        )

    def is_actor_managed(self, actor_id):
        actor_info = self.actors_db.actors_db.get(int(actor_id))
        return (
            actor_info is not None
            and actor_info.ownership == ActorOwnership.MANAGED
        )

    def is_sensor_managed(self, sensor_id):
        sensor_info = self.sensors_db.sensors_db.get(int(sensor_id))
        return (
            sensor_info is not None
            and sensor_info.ownership == ActorOwnership.MANAGED
        )

    def rename_sensor(self, sensor_id, name):
        """Change only the application's logical sensor name."""
        sensor_id = int(sensor_id)
        normalized_name = str(name).strip()
        if not normalized_name:
            raise ValueError("Sensor name cannot be empty.")
        with self.lock:
            sensor_info = self.sensors_db.sensors_db.get(sensor_id)
            if sensor_info is None:
                raise KeyError(f"Sensor {sensor_id} is no longer available.")
            sensor_info.user_name = normalized_name
            sensor_info.data_sources["user_name"] = "gui"
        return normalized_name

    def adopt_runtime_actor(self, actor_id, actor, category, origin):
        """Update the existing synchronized record without replacing actor."""
        actor_id = int(actor_id)
        if category == "sensor":
            info = self.sensors_db.sensors_db[actor_id]
            info.update_runtime(actor, preserve_relative=True)
        else:
            info = self.actors_db.actors_db[actor_id]
            info.update_runtime(actor)
        info.ownership = ActorOwnership.MANAGED
        info.origin = origin
        info.data_sources["ownership"] = "gui"
        info.data_sources["adoption"] = "server"

        if category == "vehicle":
            state = unknown_vehicle_control_state()
            state["destination"] = empty_destination(
                actor_id,
                category,
                info.world_id,
            )
            state["world_id"] = info.world_id
            self.vehicle_control_states[actor_id] = state
        elif category == "pedestrian":
            state = initial_walker_ai_state()
            state.update({
                "mode": "Unknown",
                "status": (
                    "Control state is unknown for an adopted external actor."
                ),
                "state_source": "unknown",
            })
            state["destination"] = empty_destination(
                actor_id,
                category,
                info.world_id,
            )
            self.walker_ai_states[actor_id] = state
        return info

    def data_ready(self):
        return (
            self.blueprint_lib is not None and
            self.world is not None and
            self.world_lib is not None and
            self.actual_weather_data is not None and
            self.weather_presets is not None and
            self.actual_simulation_settings is not None
        )
