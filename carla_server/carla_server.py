import carla
import threading
import queue
import time
from utilities.functions import *
from simulation_dashboard import SimulationDashboardMonitor
from simulation_manager import SimulationManager

class CarlaController:
    TICK_TIMEOUT_SECONDS = 5.0

    def __init__(self, database, controller, host="127.0.0.1", port=2000):
        self.controller = controller
        self.command_queue = queue.Queue()
        self.priority_command_queue = queue.Queue()
        self.connection_queue = queue.Queue()
        self.running = True
        self.shutdown_event = threading.Event()
        self.database = database
        self.thread = None
        self.data_ready = False
        self.client = None
        self.world_changed_event = threading.Event()
        self.host = host
        self.port = port
        self.shutdown_request = False
        self.sensor_preview_ids = set()
        self.sensor_status_errors = {}
        self.simulation_manager = SimulationManager()
        self.dashboard_monitor = SimulationDashboardMonitor()
        self._next_snapshot_check = 0.0

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            self._run_controller()
        except Exception as error:
            self.running = False
            self.data_ready = False
            self.simulation_manager.disconnect()
            self.dashboard_monitor.disconnect(
                self.database,
                "Controller stopped after an unexpected error",
                error,
            )
            self._publish_simulation_control_state(force=True)
            self.connection_queue.put(("connection_error", str(error)))

    def _run_controller(self):
        try:
            self.client = carla.Client(self.host, self.port)
            self.client.set_timeout(5.0)
            world = self.client.get_world()

            simulation_settings = self.simulation_manager.get_current_settings(world)
            self.simulation_manager.connect(simulation_settings)

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
            self.database.actual_simulation_settings = simulation_settings
            self.database.proposed_simulation_settings = simulation_settings.copy()
            self.database.simulation_control_state = (
                self.simulation_manager.snapshot()
            )

            self.data_ready = True
            self.dashboard_monitor.reset_world(
                self.database,
                world,
                "Initial connection",
            )
            self._service_dashboard(world, force=True)
            self.connection_queue.put(("connected", None))

        except Exception as e:
            self.data_ready = False
            self.running = False
            self.dashboard_monitor.disconnect(
                self.database,
                "Connection failed",
                e,
            )
            self.connection_queue.put(("connection_error", str(e)))
            return

        while self.running:
            queue_timeout = (
                0.0 if self.simulation_manager.should_tick() else 0.1
            )
            cmd, data = self._get_next_command(queue_timeout)

            if cmd == "shutdown":
                print("DEBUG: SHUTDOWN COMMAND RECEIVED")
                self.simulation_manager.disconnect()
                self._publish_simulation_control_state(force=True)
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
                self.simulation_manager.stop_local_ticks()
                self._publish_simulation_control_state(force=True)
                self.dashboard_monitor.invalidate(
                    self.database,
                    "World changing",
                    connection_status="Connected",
                )
                self.world_changed_event.clear()
                self.database.world_changed = False
                self.database.world_change_error = None
                self.client.set_timeout(30.0)

                try:
                    try:
                        new_world = self.client.load_world(data)
                    except RuntimeError:
                        print("load_world timeout, waiting for server...")
                        new_world = None

                        for i in range(10):
                            try:
                                current_world = self.client.get_world()
                                map_name = current_world.get_map().name.split("/")[-1]
                                print(f"Attempt {i + 1}: {map_name}")
                                if map_name == data:
                                    new_world = current_world
                                    print("Map changed successfully")
                                    break
                            except RuntimeError:
                                print(f"Attempt {i + 1}: server not ready")
                            if self.shutdown_event.wait(1.0):
                                raise RuntimeError(
                                    "World change cancelled during shutdown."
                                )

                    if (
                        new_world is None or
                        new_world.get_map().name.split("/")[-1] != data
                    ):
                        raise RuntimeError(f"Failed to load map: {data}")

                    new_blueprint_library = new_world.get_blueprint_library()
                    world = new_world
                    blueprint_library = new_blueprint_library

                    self.cleanup_sensor_previews()
                    self.cleanup_preview()
                    self.sensor_status_errors.clear()
                    with self.database.lock:
                        self.database.actors_db.clear()
                        self.database.sensors_db.clear()
                        self.database.spawned_actor_id = None
                        self.database.world = new_world
                        self.database.blueprint_lib = new_blueprint_library
                    self.database.sensor_event_queue.put(("sensors_cleared", None))

                    new_world_library = self.client.get_available_maps()
                    new_weather = new_world.get_weather()
                    new_weather_data = parse_weather_to_dict(new_weather)
                    new_spawn_points = new_world.get_map().get_spawn_points()
                    new_simulation_settings = (
                        self.simulation_manager.get_current_settings(new_world)
                    )
                    self.simulation_manager.settings_changed(
                        new_simulation_settings,
                        preserve_activity=False,
                    )

                    with self.database.lock:
                        self.database.world_lib = new_world_library
                        self.database.actual_weather_data = new_weather_data
                        self.database.available_spawn_points = new_spawn_points
                        self.database.actual_simulation_settings = new_simulation_settings
                        self.database.proposed_simulation_settings = (
                            new_simulation_settings.copy()
                        )
                        self.database.world_changed = True
                    self.dashboard_monitor.reset_world(
                        self.database,
                        new_world,
                    )
                    self.database.simulation_event_queue.put((
                        "simulation_settings_loaded",
                        new_simulation_settings,
                    ))
                    self._publish_simulation_control_state(force=True)
                except Exception as e:
                    self.database.world_change_error = str(e)
                    self.dashboard_monitor.record_error(e)
                    print(f"Error changing map: {e}")
                finally:
                    self.client.set_timeout(5.0)
                    self.world_changed_event.set()

            if cmd == "change_weather":
                world = self.client.get_world()
                world.set_weather(data)

            if cmd == "get_simulation_settings":
                try:
                    current_settings = (
                        self.simulation_manager.get_current_settings(world)
                    )
                    with self.database.lock:
                        self.database.actual_simulation_settings = current_settings
                        self.database.proposed_simulation_settings = (
                            current_settings.copy()
                        )
                    self.simulation_manager.settings_changed(
                        current_settings,
                        preserve_activity=True,
                    )
                    self.database.simulation_event_queue.put((
                        "simulation_settings_loaded",
                        current_settings,
                    ))
                    self._publish_simulation_control_state(force=True)
                except Exception as e:
                    self.database.simulation_event_queue.put((
                        "simulation_settings_error",
                        {"message": str(e), "settings": None},
                    ))

            if cmd == "apply_simulation_settings":
                confirmed_settings = None
                try:
                    confirmed_settings = self.simulation_manager.apply_settings(
                        world,
                        data,
                    )
                    with self.database.lock:
                        self.database.actual_simulation_settings = confirmed_settings
                        self.database.proposed_simulation_settings = (
                            confirmed_settings.copy()
                        )
                    self.simulation_manager.settings_changed(
                        confirmed_settings,
                        preserve_activity=True,
                    )
                    self.database.simulation_event_queue.put((
                        "simulation_settings_applied",
                        confirmed_settings,
                    ))
                    self._publish_simulation_control_state(force=True)
                except Exception as e:
                    try:
                        confirmed_settings = (
                            self.simulation_manager.get_current_settings(world)
                        )
                        with self.database.lock:
                            self.database.actual_simulation_settings = (
                                confirmed_settings
                            )
                            self.database.proposed_simulation_settings = (
                                confirmed_settings.copy()
                            )
                        self.simulation_manager.settings_changed(
                            confirmed_settings,
                            preserve_activity=True,
                        )
                    except Exception:
                        pass
                    self.database.simulation_event_queue.put((
                        "simulation_settings_error",
                        {
                            "message": str(e),
                            "settings": confirmed_settings,
                        },
                    ))
                    self._publish_simulation_control_state(force=True)

            if cmd == "set_tick_controller":
                self._handle_simulation_control_command(
                    self.simulation_manager.set_tick_controller,
                    data,
                )

            if cmd == "run_simulation":
                self._handle_tick_start_command(
                    world,
                    self.simulation_manager.start_running,
                )

            if cmd == "pause_simulation":
                self._handle_simulation_control_command(
                    self.simulation_manager.pause,
                )

            if cmd == "step_simulation":
                self._handle_tick_start_command(
                    world,
                    self.simulation_manager.step,
                    data,
                )

            if cmd == "stop_simulation_control":
                self.simulation_manager.stop_local_ticks()
                self._publish_simulation_control_state(force=True)

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
                        self.remove_sensors_for_parent(actor_id)
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
                        self.remove_sensors_for_parent(actor_id)
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

            if cmd == "attach_sensor":
                sensor = None
                try:
                    parent_id = data["parent_id"]
                    with self.database.lock:
                        parent_actor = self.database.actors_db.actors.get(parent_id)
                    if parent_actor is None or not parent_actor.is_alive:
                        raise RuntimeError("Parent actor is no longer available.")

                    sensor_blueprint = self.database.blueprint_lib.find(data["blueprint_id"])
                    for attr_name, attr_value in data["attributes"].items():
                        if sensor_blueprint.has_attribute(attr_name):
                            attribute = sensor_blueprint.get_attribute(attr_name)
                            if attribute.is_modifiable:
                                sensor_blueprint.set_attribute(attr_name, str(attr_value))

                    sensor = self.database.world.spawn_actor(
                        sensor_blueprint,
                        data["transform"],
                        attach_to=parent_actor,
                        attachment_type=carla.AttachmentType.Rigid
                    )
                    if sensor is None:
                        raise RuntimeError("CARLA did not create the sensor.")

                    with self.database.lock:
                        self.database.add_sensor(
                            sensor,
                            parent_id,
                            data["name"],
                            data["blueprint_id"],
                            data["transform"],
                            data["attributes"]
                        )
                    self.sensor_status_errors.pop(sensor.id, None)
                    self.database.sensor_event_queue.put(("sensor_attached", sensor.id))
                    print(f"Sensor {data['name']} spawned with ID {sensor.id}")
                except Exception as e:
                    self.dashboard_monitor.record_error(e)
                    if sensor is not None:
                        try:
                            sensor.destroy()
                        except Exception:
                            pass
                    self.database.sensor_event_queue.put(("sensor_error", f"Could not add sensor: {e}"))

            if cmd == "remove_sensor":
                sensor_id = data
                try:
                    self.stop_sensor_preview_actor(sensor_id)
                    with self.database.lock:
                        sensor = self.database.sensors_db.sensors.get(sensor_id)
                        if sensor is None:
                            raise RuntimeError("Sensor is no longer available.")
                        sensor.destroy()
                        self.database.sensors_db.remove_sensor(sensor_id)
                    self.sensor_status_errors.pop(sensor_id, None)
                    self.database.sensor_event_queue.put(("sensor_removed", sensor_id))
                    print(f"Sensor {sensor_id} destroyed successfully.")
                except Exception as e:
                    self.dashboard_monitor.record_error(e)
                    self.database.sensor_event_queue.put(("sensor_error", f"Could not remove sensor: {e}"))

            if cmd == "change_sensor_transform":
                sensor_id, transform = data
                sensor = None
                previous_transform = None
                transform_sent = False
                try:
                    with self.database.lock:
                        sensor = self.database.sensors_db.sensors.get(sensor_id)
                        sensor_info = self.database.sensors_db.sensors_db.get(sensor_id)
                    if sensor is None or sensor_info is None or not sensor.is_alive:
                        raise RuntimeError("Sensor is no longer available.")

                    parent_id = sensor_info.parent_id
                    previous_transform = sensor_info.relative_transform
                    sensor.set_transform(transform)
                    transform_sent = True
                    self.database.world.wait_for_tick(2.0)

                    if not sensor.is_alive or sensor.parent is None or sensor.parent.id != parent_id:
                        raise RuntimeError("Sensor attachment could not be confirmed.")

                    with self.database.lock:
                        current_sensor_info = self.database.sensors_db.sensors_db.get(sensor_id)
                        if current_sensor_info is None:
                            raise RuntimeError("Sensor is no longer available.")
                        current_sensor_info.relative_transform = transform
                    self.database.sensor_event_queue.put(("sensor_transform_changed", sensor_id))
                    print(f"Sensor {sensor_id} transform changed successfully.")
                except Exception as e:
                    self.dashboard_monitor.record_error(e)
                    if transform_sent and sensor is not None and previous_transform is not None:
                        try:
                            sensor.set_transform(previous_transform)
                            self.database.world.wait_for_tick(2.0)
                        except Exception:
                            pass
                    self.database.sensor_event_queue.put((
                        "sensor_error",
                        f"Could not change sensor transform: {e}"
                    ))

            if cmd == "start_sensor_preview":
                sensor_id = data["sensor_id"]
                preview_buffer = data["preview_buffer"]
                try:
                    with self.database.lock:
                        sensor = self.database.sensors_db.sensors.get(sensor_id)
                    if sensor is None or not sensor.is_alive:
                        raise RuntimeError("Sensor is no longer available.")
                    if sensor_id in self.sensor_preview_ids or sensor.is_listening():
                        raise RuntimeError("This sensor is already being used by another listener.")

                    sensor.listen(data["image_callback"])
                    self.sensor_preview_ids.add(sensor_id)
                    self.sensor_status_errors.pop(sensor_id, None)
                    preview_buffer.set_started()
                except Exception as e:
                    error_message = f"Could not start sensor preview: {e}"
                    self.sensor_status_errors[sensor_id] = error_message
                    self.dashboard_monitor.record_error(error_message)
                    preview_buffer.set_error(error_message)

            if cmd == "stop_sensor_preview":
                self.stop_sensor_preview_actor(data)

            world_snapshot = None
            if self.running and cmd != "stop_simulation_control":
                world_snapshot = self._service_simulation(world)
            if self.running:
                self._service_dashboard(world, world_snapshot=world_snapshot)

        self.cleanup_sensor_previews()
        self.cleanup_preview()
        self.simulation_manager.disconnect()
        self._publish_simulation_control_state(force=True)
        self.data_ready = False
        self.dashboard_monitor.disconnect(self.database)
        self.connection_queue.put(("disconnected", None))







    def _handle_simulation_control_command(self, callback, *args):
        try:
            callback(*args)
            self._publish_simulation_control_state(force=True)
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self.database.simulation_event_queue.put((
                "simulation_control_command_error",
                str(error),
            ))
            self._publish_simulation_control_state(force=True)

    def _handle_tick_start_command(self, world, callback, *args):
        try:
            current_settings = self.simulation_manager.get_current_settings(
                world
            )
            with self.database.lock:
                previous_settings = self.database.actual_simulation_settings
                settings_changed = previous_settings != current_settings
                self.database.actual_simulation_settings = current_settings
                self.database.proposed_simulation_settings = (
                    current_settings.copy()
                )
            self.simulation_manager.settings_changed(
                current_settings,
                preserve_activity=True,
            )
            if settings_changed:
                self.database.simulation_event_queue.put((
                    "simulation_settings_loaded",
                    current_settings,
                ))
            if not current_settings["synchronous_mode"]:
                raise RuntimeError(
                    "CARLA is currently asynchronous. Apply Synchronous "
                    "mode before starting GUI ticks."
                )
            callback(*args)
            self._publish_simulation_control_state(force=True)
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self.database.simulation_event_queue.put((
                "simulation_control_command_error",
                str(error),
            ))
            self._publish_simulation_control_state(force=True)

    def _get_next_command(self, timeout):
        try:
            return self.priority_command_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            return self.command_queue.get(timeout=timeout)
        except queue.Empty:
            return None, None

    def _service_simulation(self, world):
        if self.simulation_manager.state == SimulationManager.ERROR:
            return None
        if self.simulation_manager.should_tick():
            previous_state = self.simulation_manager.state
            try:
                self.before_simulation_tick(world)
                frame = world.tick(self.TICK_TIMEOUT_SECONDS)
                self.simulation_manager.record_tick(frame)
                self.after_simulation_tick(world, frame)
                force_publish = self.simulation_manager.state != previous_state
                self._publish_simulation_control_state(force=force_publish)
            except Exception as error:
                self.simulation_manager.fail(error)
                self.dashboard_monitor.record_error(error)
                self._publish_simulation_control_state(force=True)
            return None

        now = time.monotonic()
        if now < self._next_snapshot_check:
            return None
        self._next_snapshot_check = now + 0.1
        try:
            snapshot = world.get_snapshot()
            frame = getattr(snapshot, "frame", None)
            if frame != self.simulation_manager.last_frame:
                self.simulation_manager.record_observed_frame(frame)
                self._publish_simulation_control_state()
            return snapshot
        except Exception as error:
            self.simulation_manager.fail(error)
            self.dashboard_monitor.record_error(error)
            self._publish_simulation_control_state(force=True)
            return None

    def _service_dashboard(self, world, world_snapshot=None, force=False):
        internal_actor_ids = set()
        streaming_sensor_ids = set(self.sensor_preview_ids)

        for actor in (self.database.ghost_data, self.database.ghost_camera):
            actor_id = self._actor_id(actor)
            if actor_id is not None:
                internal_actor_ids.add(actor_id)
        ghost_camera_id = self._actor_id(self.database.ghost_camera)
        if ghost_camera_id is not None:
            streaming_sensor_ids.add(ghost_camera_id)

        spectator_camera_id = getattr(
            self.controller,
            "spectator_camera_id",
            None,
        )
        if spectator_camera_id is not None:
            internal_actor_ids.add(spectator_camera_id)
            streaming_sensor_ids.add(spectator_camera_id)

        viewer_status = getattr(self.controller, "spectator_state", "Unknown")
        self.dashboard_monitor.update(
            world,
            self.database,
            self.simulation_manager,
            self.command_queue.qsize() + self.priority_command_queue.qsize(),
            viewer_status,
            internal_actor_ids,
            streaming_sensor_ids,
            self.sensor_status_errors,
            world_snapshot=world_snapshot,
            force=force,
        )

    @staticmethod
    def _actor_id(actor):
        if actor is None:
            return None
        try:
            return int(actor.id)
        except Exception:
            return None

    def before_simulation_tick(self, world):
        """Extension point for future operations immediately before a GUI tick."""
        pass

    def after_simulation_tick(self, world, frame):
        """Extension point for future operations immediately after a GUI tick."""
        pass

    def _publish_simulation_control_state(self, force=False):
        control_state = self.simulation_manager.snapshot()
        with self.database.lock:
            self.database.simulation_control_state = control_state

        if not force:
            return
        self.database.simulation_event_queue.put((
            "simulation_control_state",
            control_state,
        ))

    def shutdown(self):
        print("Shutting down CARLA...")
        self.shutdown_event.set()
        self.running = False
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

    def remove_sensors_for_parent(self, parent_id):
        sensor_ids = [
            sensor_id
            for sensor_id, sensor_info in self.database.sensors_db.sensors_db.items()
            if sensor_info.parent_id == parent_id
        ]
        for sensor_id in sensor_ids:
            self.stop_sensor_preview_actor(sensor_id)
            sensor = self.database.sensors_db.sensors.get(sensor_id)
            if sensor is not None:
                try:
                    sensor.destroy()
                except Exception as e:
                    print(f"Error destroying sensor {sensor_id}: {e}")
            self.database.sensors_db.remove_sensor(sensor_id)
            self.sensor_status_errors.pop(sensor_id, None)
        if sensor_ids:
            self.database.sensor_event_queue.put(("sensors_cleared", sensor_ids))

    def stop_sensor_preview_actor(self, sensor_id):
        if sensor_id not in self.sensor_preview_ids:
            return
        sensor = self.database.sensors_db.sensors.get(sensor_id)
        if sensor is not None:
            try:
                if sensor.is_alive and sensor.is_listening():
                    sensor.stop()
            except Exception as e:
                print(f"Error stopping sensor preview {sensor_id}: {e}")
        self.sensor_preview_ids.discard(sensor_id)

    def cleanup_sensor_previews(self):
        for sensor_id in list(self.sensor_preview_ids):
            self.stop_sensor_preview_actor(sensor_id)


    def spawn_actor(self, actor_data):
        self.command_queue.put(("spawn", actor_data))

    def change_world(self, new_world):
        self.world_changed_event.clear()
        self.database.world_changed = False
        self.database.world_change_error = None
        self.command_queue.put(("change_world", new_world))

    def change_weather(self, new_weather):
        self.command_queue.put(("change_weather", new_weather))

    def get_simulation_settings(self):
        self.command_queue.put(("get_simulation_settings", None))

    def apply_simulation_settings(self, settings):
        self.command_queue.put(("apply_simulation_settings", settings))

    def set_tick_controller(self, controller):
        self.command_queue.put(("set_tick_controller", controller))

    def run_simulation(self):
        self.command_queue.put(("run_simulation", None))

    def pause_simulation(self):
        self.priority_command_queue.put(("pause_simulation", None))

    def step_simulation(self, count=1):
        self.command_queue.put(("step_simulation", count))

    def stop_simulation_control(self):
        self.priority_command_queue.put(("stop_simulation_control", None))

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

    def attach_sensor(self, sensor_data):
        self.command_queue.put(("attach_sensor", sensor_data))

    def remove_sensor(self, sensor_id):
        self.command_queue.put(("remove_sensor", sensor_id))

    def change_sensor_transform(self, sensor_id, transform):
        self.command_queue.put(("change_sensor_transform", [sensor_id, transform]))

    def start_sensor_preview(self, sensor_id, image_callback, preview_buffer):
        self.command_queue.put(("start_sensor_preview", {
            "sensor_id": sensor_id,
            "image_callback": image_callback,
            "preview_buffer": preview_buffer
        }))

    def stop_sensor_preview(self, sensor_id):
        self.command_queue.put(("stop_sensor_preview", sensor_id))
