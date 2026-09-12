import carla
import threading
import queue
from utilities.functions import *

class CarlaController:
    def __init__(self, database):
        self.command_queue = queue.Queue()
        self.running = True
        self.database = database
        self.thread = None
        self.data_ready = False
        self.client = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        self.client = carla.Client('localhost', 2000)
        world = self.client.get_world()


        settings = world.get_settings()
        settings.no_rendering_mode = False
        world.apply_settings(settings)

        blueprint_library = world.get_blueprint_library()
        self.database.blueprint_lib = blueprint_library
        self.database.world = world
        weather = world.get_weather()
        weather_data = parse_weather_to_dict(weather)
        weather_presets = {
            name: value
            for name, value in vars(carla.WeatherParameters).items()
            if isinstance(value, carla.WeatherParameters)
        }
        self.database.weather_presets = weather_presets
        self.database.actual_weather_data = weather_data
        self.database.world_lib = self.client.get_available_maps()
        while self.running:
            if not self.command_queue.empty():
                cmd, data = self.command_queue.get()

                if cmd == "spawn":
                    print("Spawning actor...")
                    bp = blueprint_library.find(data["bp_id"])
                    print(data["bp_id"])
                    transform = carla.Transform(
                        carla.Location(
                            x=data["x"],
                            y=data["y"],
                            z=data["z"]
                        )
                    )

                    actor = world.try_spawn_actor(bp, transform)

                    if actor is None:
                        print("Spawn failed")
                    else:
                        spectator = world.get_spectator()

                        transform = actor.get_transform()
                        spectator.set_transform(transform)
                        with self.database.lock:
                            self.database.add_actor(actor)

                if cmd == "spawn_ghost":
                    bp = blueprint_library.find(data["bp_id"])
                    transform = carla.Transform(
                        carla.Location(
                            x=data["x"],
                            y=data["y"],
                            z=data["z"]
                        )
                    )

                    actor = world.try_spawn_actor(bp, transform)
                    if actor is not None:
                        actor.set_simulate_physics(False)
                        self.database.ghost_data = actor.id

                if cmd == "change_world":
                    self.client.set_timeout(30.0)

                    try:
                        world = self.client.load_world(data)
                    except RuntimeError as e:
                        print("Timeout during world loading")
                        world = self.client.get_world()

                        if data in world.get_map().name:
                            print("Map changed successfully")
                        else:
                            raise e

                    self.client = carla.Client('localhost', 2000)
                    self.client.set_timeout(5.0)
                    world = self.client.get_world()
                    blueprint_library = world.get_blueprint_library()
                    self.database.blueprint_lib = blueprint_library
                    self.database.world = world
                    self.database.world_lib = self.client.get_available_maps()
                    weather = world.get_weather()
                    weather_data = parse_weather_to_dict(weather)
                    self.database.actual_weather_data = weather_data

                if cmd == "change_weather":
                    world = self.client.get_world()
                    world.set_weather(data)




    def shutdown(self):
        print("Shutting down CARLA...")

        self.running = False
        self.thread.join(timeout=2)

        with self.database.lock:
            for actor in self.database.actors_db.actors.values():
                try:
                    actor.destroy()
                except:
                    pass

            self.database.actors_db.actors.clear()

    def spawn_actor(self, actor_data):
        self.command_queue.put(("spawn", actor_data))

    def spawn_ghost(self, actor_data):
        self.command_queue.put(("spawn_ghost", actor_data))

    def change_world(self, new_world):
        self.command_queue.put(("change_world", new_world))

    def change_weather(self, new_weather):
        self.command_queue.put(("change_weather", new_weather))