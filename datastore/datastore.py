from actors import actors
import carla
import queue
import threading

class DataStorage:
    def __init__(self):
        self.actors_db = actors.Actors()
        self.lock = threading.Lock()
        self.command_queue = queue.Queue()
        self.blueprint_lib = None
        self.world = None
        self.ghost_data = None
        self.world_lib = None
        self.actual_weather_data = None
        self.weather_presets = None

    def add_actor(self, actor: carla.Actor):
        self.actors_db.add_actor(actor)

    def data_ready(self):
        return (
            self.blueprint_lib is not None and
            self.world is not None and
            self.world_lib is not None and
            self.actual_weather_data is not None and
            self.weather_presets is not None
        )