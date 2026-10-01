import carla
import threading
import queue
from utilities.functions import *

class CarlaController:
    def __init__(self, database, controller):
        self.controller = controller
        self.command_queue = queue.Queue()
        self.running = True
        self.database = database
        self.thread = None
        self.data_ready = False
        self.client = None
        self.world_changed_event = threading.Event()

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
        self.database.available_spawn_points = world.get_map().get_spawn_points()
        while self.running:
            if not self.command_queue.empty():
                cmd, data = self.command_queue.get()

                if cmd == "spawn":
                    print("Spawning actor...")
                    bp = blueprint_library.find(data["blueprint_id"])
                    print(data["blueprint_id"])
                    transform = data["transform"]

                    actor = world.try_spawn_actor(bp, transform)

                    if actor is None:
                        print("Spawn failed")
                    else:
                        spectator = world.get_spectator()

                        transform = actor.get_transform()
                        spectator.set_transform(transform)
                        with self.database.lock:
                            self.database.add_actor(actor, data["name"])
                            self.database.spawned_actor_id = actor.id
                            print(f"Actor {data['name']} spawned with ID {actor.id}")

                if cmd == "change_world":
                    for actor in self.database.actors_db.actors.values():
                        try:
                            actor.destroy()
                        except:
                            pass
                    self.world_changed_event.clear()
                    self.database.world_changed = False
                    self.client.set_timeout(30.0)

                    try:
                        world = self.client.load_world(data)
                    except RuntimeError:
                        print("load_world timeout, waiting for server...")

                        world = None

                        for i in range(10):
                            try:
                                world = self.client.get_world()

                                map_name = world.get_map().name.split("/")[-1]

                                print(f"Attempt {i + 1}: {map_name}")

                                if map_name == data:
                                    print("Map changed successfully")
                                    break

                            except RuntimeError as e:
                                print(f"Attempt {i + 1}: server not ready")

                            time.sleep(1)

                        if world is None or world.get_map().name.split("/")[-1] != data:
                            raise RuntimeError(f"Failed to load map: {data}")

                    finally:
                        self.client.set_timeout(5.0)

                    self.client.set_timeout(5.0)
                    self.database.world = world
                    self.database.blueprint_lib = world.get_blueprint_library()
                    self.database.world_lib = self.client.get_available_maps()
                    weather = world.get_weather()
                    weather_data = parse_weather_to_dict(weather)
                    self.database.actual_weather_data = weather_data
                    self.database.available_spawn_points = world.get_map().get_spawn_points()
                    self.database.world_changed = True
                    self.world_changed_event.set()

                if cmd == "change_weather":
                    world = self.client.get_world()
                    world.set_weather(data)

                if cmd == "spawn_preview_vehicle":
                    self.database.ghost_spawned = False
                    bp = blueprint_library.find(data["blueprint_id"])
                    transform = carla.Transform(
                        carla.Location(
                            x=data["transform"].location.x,
                            y=data["transform"].location.y,
                            z=data["transform"].location.z
                        )
                    )

                    actor = world.try_spawn_actor(bp, transform)
                    if actor is not None:
                        self.database.ghost_physic = actor.get_physics_control()
                        actor.set_simulate_physics(False)
                        self.database.ghost_data = actor
                        actor.set_enable_gravity(False)
                        camera_bp = self.database.blueprint_lib.find('sensor.camera.rgb')

                        camera_bp.set_attribute("image_size_x", "640")
                        camera_bp.set_attribute("image_size_y", "480")
                        camera_bp.set_attribute("fov", "70")

                        camera_transform = carla.Transform(
                            carla.Location(
                                x=transform.location.x + 5,
                                y=transform.location.y + 3,
                                z=transform.location.z + 2
                            ),
                            carla.Rotation(
                                pitch=-15.0,
                                roll=0.0,
                                yaw=220.0
                            )
                        )

                        self.database.ghost_camera = self.client.get_world().spawn_actor(camera_bp, camera_transform)

                        self.database.ghost_camera.listen(self.controller.main_view.vehicle_frame.vehicle_preview.on_camera_image)
                        self.database.ghost_spawned = True

                if cmd == "set_ghost_transform":
                    transform = data
                    if self.database.ghost_data is not None:
                        ghost_actor = self.database.ghost_data
                        if ghost_actor.is_alive:
                            ghost_actor.set_transform(transform)
                            camera = self.database.ghost_camera
                            if camera is not None:
                                camera_transform = carla.Transform(
                                    carla.Location(
                                        x=transform.location.x + 5,
                                        y=transform.location.y + 3,
                                        z=transform.location.z + 2
                                    ),
                                    carla.Rotation(
                                        pitch=-15.0,
                                        roll=0.0,
                                        yaw=220.0
                                    )
                                )
                                camera.set_transform(camera_transform)
                            self.database.ghost_data = ghost_actor
                            self.database.ghost_camera = camera

                if cmd == "destroy_preview_vehicle":
                    if self.database.ghost_camera is not None:
                        self.database.ghost_camera.stop()
                        self.database.ghost_camera.destroy()
                        self.database.ghost_camera = None

                    if self.database.ghost_data is not None:
                        self.database.ghost_data.destroy()
                        self.database.ghost_physic = None
                        self.database.ghost_data = None

                if cmd == "update_preview_vehicle_model":
                    if self.database.ghost_data is not None:
                        ghost_actor = self.database.ghost_data
                        transform = ghost_actor.get_transform()
                        ghost_actor.destroy()
                        bp = blueprint_library.find(data)
                        new_ghost_actor = world.try_spawn_actor(bp, transform)
                        if new_ghost_actor is not None:
                            new_ghost_actor.set_simulate_physics(False)
                            new_ghost_actor.set_enable_gravity(False)
                            self.database.ghost_data = new_ghost_actor

                if cmd == "remove_actor":
                    actor_id = data
                    with self.database.lock:
                        if actor_id in self.database.actors_db.actors:
                            actor = self.database.actors_db.actors[actor_id]
                            try:
                                actor.destroy()
                                print(f"Actor {actor_id} destroyed successfully.")
                            except Exception as e:
                                print(f"Error destroying actor {actor_id}: {e}")
                            del self.database.actors_db.actors[actor_id]




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

    def change_world(self, new_world):
        self.world_changed_event.clear()
        self.command_queue.put(("change_world", new_world))

    def change_weather(self, new_weather):
        self.command_queue.put(("change_weather", new_weather))

    def spawn_preview_vehicle(self, blueprint_id, transform):
        self.command_queue.put(("spawn_preview_vehicle", {
            "blueprint_id": blueprint_id,
            "transform": transform,
        }))

    def set_ghost_transform(self, transform):
        self.command_queue.put(("set_ghost_transform", transform))

    def destroy_preview_vehicle(self):
        self.command_queue.put(("destroy_preview_vehicle", None))

    def update_preview_vehicle_model(self, blueprint_id):
        self.command_queue.put(
            ("update_preview_vehicle_model", blueprint_id)
        )

    def remove_actor(self, actor_id):
        self.command_queue.put(("remove_actor", actor_id))