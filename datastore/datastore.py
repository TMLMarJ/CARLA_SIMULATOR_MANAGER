from actors import actors
import carla
import queue
import threading

class DataStorage:
    def __init__(self):
        self.actors_db = actors.Actors()
        self.sensors_db = actors.Sensors()
        self.lock = threading.Lock()
        self.command_queue = queue.Queue()
        self.sensor_event_queue = queue.Queue()
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

    def add_actor(self, actor: carla.Actor, name: str):
        self.actors_db.add_actor(actor, name)

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
            self.weather_presets is not None
        )
