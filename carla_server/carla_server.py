import carla
import threading
import queue
from utilities.functions import *

class CarlaController:
    def __init__(self, database, controller, host="127.0.0.1", port=2000):
        self.controller = controller
        self.command_queue = queue.Queue()
        self.connection_queue = queue.Queue()
        self.running = True
        self.database = database
        self.thread = None
        self.data_ready = False
        self.client = None
        self.world_changed_event = threading.Event()
        self.host = host
        self.port = port
        self.shutdown_request = False

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            self.client = carla.Client(self.host, self.port)
            self.client.set_timeout(5.0)
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

            self.data_ready = True
            self.connection_queue.put(("connected", None))

        except Exception as e:
            self.data_ready = False
            self.running = False
            self.connection_queue.put(("connection_error", str(e)))
            return

        while self.running:
            try:
                cmd, data = self.command_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if cmd == "shutdown":
                print("DEBUG: SHUTDOWN COMMAND RECEIVED")
                self.running = False
                break

            if cmd == "spawn":
                print("Spawning actor...")
                bp = blueprint_library.find(data["blueprint_id"])
                print(data["blueprint_id"])
                transform = data["transform"]
                if self.database.blueprint_new_data is not None:
                    for attr_name, attr_value in self.database.blueprint_new_data.items():
                        if bp.has_attribute(attr_name):
                            bp.set_attribute(attr_name, str(attr_value))
                    self.database.blueprint_new_data = None

                actor = world.try_spawn_actor(bp, transform)

                if actor is None:
                    print("Spawn failed")
                else:
                    if self.database.ghost_physic is not None:
                        actor.apply_physics_control(self.database.ghost_physic)
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

            if cmd == "spawn_preview_actor":
                # A preview can be requested more than once by GUI events. Always
                # remove the previous actor and camera before creating a new pair.
                self.cleanup_preview()
                self.database.ghost_spawned = False
                bp = blueprint_library.find(data["blueprint_id"])
                transform = carla.Transform(
                    carla.Location(
                        x=data["transform"].location.x,
                        y=data["transform"].location.y,
                        z=data["transform"].location.z
                    ),
                    carla.Rotation(
                        yaw=data["transform"].rotation.yaw,
                        pitch=data["transform"].rotation.pitch,
                        roll=data["transform"].rotation.roll
                    )
                )

                actor = world.try_spawn_actor(bp, transform)
                if actor is None:
                    offset_z = 5
                    transform.location.z += offset_z
                    actor = world.try_spawn_actor(bp, transform)

                if actor is not None:
                    self.database.ghost_data = actor
                    if actor.type_id.startswith("vehicle."):
                        self.database.ghost_physic = actor.get_physics_control()
                    actor.set_simulate_physics(False)
                    actor.set_enable_gravity(False)
                    camera_bp = self.database.blueprint_lib.find('sensor.camera.rgb')

                    camera_bp.set_attribute("image_size_x", "640")
                    camera_bp.set_attribute("image_size_y", "480")
                    camera_bp.set_attribute("fov", "70")
                    camera_bp.set_attribute("sensor_tick", "0.1")

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

                    self.database.ghost_camera.listen(data["image_callback"])
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

            if cmd == "destroy_preview_actor":
                if self.database.ghost_camera is not None:
                    self.database.ghost_camera.stop()
                    self.database.ghost_camera.destroy()
                    self.database.ghost_camera = None

                if self.database.ghost_data is not None:
                    self.database.ghost_data.destroy()
                    self.database.ghost_physic = None
                    self.database.ghost_data = None

            if cmd == "update_preview_actor_model":
                if self.database.ghost_data is not None:
                    ghost_actor = self.database.ghost_data
                    transform = ghost_actor.get_transform()
                    ghost_actor.destroy()
                    bp = blueprint_library.find(data)
                    if self.database.blueprint_new_data is not None:
                        for attr_name, attr_value in self.database.blueprint_new_data.items():
                            if bp.has_attribute(attr_name):
                                print("BEFORE:", attr_name, bp.get_attribute(attr_name))

                                bp.set_attribute(attr_name, str(attr_value))

                                print("INPUT: ", attr_name, attr_value)
                                print("AFTER: ", attr_name, bp.get_attribute(attr_name))
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

            if cmd == "apply_new_physics_and_blueprint_to_ghost":
                self.database.physics_applied = False
                physics_control = data
                if self.database.ghost_data is not None:
                    ghost_actor = self.database.ghost_data
                    if physics_control is not None:
                        ghost_actor.apply_physics_control(physics_control)
                    ghost_type = ghost_actor.type_id
                    self.command_queue.put(("update_preview_actor_model", ghost_type))
                    self.database.physics_applied = True

            if cmd == "change_actor_model":
                blueprint_id, actor_id = data
                with self.database.lock:
                    if actor_id in self.database.actors_db.actors:
                        actor = self.database.actors_db.actors[actor_id]
                        transform = actor.get_transform()
                        name = self.database.actors_db.actors_db[actor_id].user_name
                        physics = actor.get_physics_control() if actor.type_id.startswith("vehicle.") else None
                        actor.destroy()
                        bp = blueprint_library.find(blueprint_id)
                        is_destroyed = wait_and_try(lambda: not actor.is_alive, timeout=5, interval=0.5)
                        if not is_destroyed:
                            print(f"Failed to destroy actor {actor_id}.")
                        if is_destroyed:
                            transform.location.z = transform.location.z + 0.5
                            new_actor = world.spawn_actor(bp, transform)
                            if new_actor is not None:
                                del self.database.actors_db.actors[actor.id]
                                if physics is not None:
                                    new_actor.apply_physics_control(physics)
                                self.database.add_actor(new_actor, name)
                                self.database.spawned_actor_id = new_actor.id
                                print(f"Actor {actor_id} changed to {blueprint_id} successfully.")
                            else:
                                print(f"Failed to change actor {actor_id} to {blueprint_id}.")
                                del self.database.actors_db.actors[actor.id]

            if cmd == "set_actor_transform":
                transform, actor_id = data
                with self.database.lock:
                    if actor_id in self.database.actors_db.actors:
                        actor = self.database.actors_db.actors[actor_id]
                        actor.set_transform(transform)
                        self.database.actors_db.actors_db[actor_id].transform = transform

        self.cleanup_preview()
        self.data_ready = False
        self.connection_queue.put(("disconnected", None))







    def shutdown(self):
        print("Shutting down CARLA...")
        if self.thread is None or not self.thread.is_alive():
            return
        self.command_queue.put(("shutdown", None))

    def cleanup_preview(self):
        if self.database.ghost_camera is not None:
            try:
                self.database.ghost_camera.stop()
                self.database.ghost_camera.destroy()
            except Exception as e:
                print(f"Error destroying ghost camera: {e}")
            finally:
                self.database.ghost_camera = None

        if self.database.ghost_data is not None:
            try:
                self.database.ghost_data.destroy()
            except Exception as e:
                print(f"Error destroying ghost: {e}")
            finally:
                self.database.ghost_data = None

        self.database.ghost_physic = None
        self.database.ghost_spawned = False


    def spawn_actor(self, actor_data):
        self.command_queue.put(("spawn", actor_data))

    def change_world(self, new_world):
        self.world_changed_event.clear()
        self.command_queue.put(("change_world", new_world))

    def change_weather(self, new_weather):
        self.command_queue.put(("change_weather", new_weather))

    def spawn_preview_actor(self, blueprint_id, transform, image_callback):
        self.command_queue.put(("spawn_preview_actor", {
            "blueprint_id": blueprint_id,
            "transform": transform,
            "image_callback": image_callback,
        }))

    def set_ghost_transform(self, transform):
        self.command_queue.put(("set_ghost_transform", transform))

    def destroy_preview_actor(self):
        self.command_queue.put(("destroy_preview_actor", None))

    def update_preview_actor_model(self, blueprint_id):
        self.command_queue.put(
            ("update_preview_actor_model", blueprint_id)
        )

    def remove_actor(self, actor_id):
        self.command_queue.put(("remove_actor", actor_id))

    def apply_new_physics_and_blueprint_to_ghost(self, physics_control):
        self.command_queue.put(("apply_new_physics_and_blueprint_to_ghost", physics_control))

    def change_actor_model(self, blueprint_id, actor_id):
        self.command_queue.put(("change_actor_model", [blueprint_id, actor_id]))

    def set_actor_transform(self, new_transform, actor_id):
        self.command_queue.put(("set_actor_transform", [new_transform, actor_id]))
