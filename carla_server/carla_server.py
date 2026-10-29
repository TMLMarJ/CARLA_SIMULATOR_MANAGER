import carla
import threading
import queue
import time
from utilities.functions import *
from simulation_dashboard import SimulationDashboardMonitor
from simulation_manager import SimulationManager
from movement_preview import camera_transform_for_actor
from vehicle_control import (
    AUTOPILOT,
    MANUAL_CONTROL,
    NO_CONTROL,
    VEHICLE_CONTROL_MODES,
)
from walker_control import (
    MAX_WALKER_SPEED,
    MIN_WALKER_SPEED,
    WALKER_AI_ERROR,
    WALKER_AI_RUNNING,
    WALKER_AI_STOPPED,
)

class CarlaController:
    TICK_TIMEOUT_SECONDS = 5.0
    TRAFFIC_MANAGER_PORT = 8000
    MANUAL_CONTROL_HZ = 20.0
    MANUAL_INPUT_TIMEOUT_SECONDS = 0.5
    WALKER_AI_CHECK_INTERVAL_SECONDS = 0.5
    WALKER_AI_TARGET_RETRY_SECONDS = 2.0
    WALKER_AI_REACHED_DISTANCE = 1.0
    WALKER_AI_PROGRESS_DISTANCE = 0.25
    WALKER_AI_STALL_SECONDS = 10.0

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
        self.movement_preview_camera = None
        self.movement_preview_publisher = None
        self.movement_preview_session_id = None
        self.movement_preview_actor_id = None
        self.movement_control_queue = None
        self.manual_control_command = None
        self.manual_control_received_at = None
        self.manual_control_next_apply = 0.0
        self.manual_control_input_fresh = False
        self.traffic_manager = None
        self.walker_ai_controllers = {}
        self.walker_ai_runtime = {}
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
            self._shutdown_vehicle_controls(disable_autopilot=True)
            self._shutdown_walker_ai()
            self.cleanup_movement_preview("controller_error")
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
                0.0
                if self.simulation_manager.should_tick()
                else (0.05 if self._manual_control_is_active() else 0.1)
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
                self._shutdown_vehicle_controls(disable_autopilot=True)
                self._shutdown_walker_ai()
                self.cleanup_movement_preview("world_changed")
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
                        self.database.vehicle_control_states.clear()
                        self.database.walker_ai_states.clear()
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
                    self._update_traffic_manager_sync(world)
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
                    self._update_traffic_manager_sync(world)
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
                if actor_id == self.movement_preview_actor_id:
                    self.cleanup_movement_preview("actor_removed")
                self._destroy_walker_ai(actor_id, remove_state=True)
                self._release_vehicle_control(
                    actor_id,
                    disable_autopilot=True,
                )
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
                        self.database.actors_db.actors_db.pop(actor_id, None)
                        self.database.vehicle_control_states.pop(actor_id, None)
                        self.database.walker_ai_states.pop(actor_id, None)

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
                if actor_id == self.movement_preview_actor_id:
                    self.cleanup_movement_preview("actor_replaced")
                self._destroy_walker_ai(actor_id, remove_state=True)
                self._release_vehicle_control(
                    actor_id,
                    disable_autopilot=True,
                )
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
                                self.database.actors_db.actors_db.pop(actor.id, None)
                                self.database.vehicle_control_states.pop(
                                    actor.id,
                                    None,
                                )
                                self.database.walker_ai_states.pop(actor.id, None)
                                if physics is not None:
                                    new_actor.apply_physics_control(physics)
                                self.database.add_actor(new_actor, name)
                                self.database.spawned_actor_id = new_actor.id
                                print(f"Actor {actor_id} changed to {blueprint_id} successfully.")
                            else:
                                print(f"Failed to change actor {actor_id} to {blueprint_id}.")
                                del self.database.actors_db.actors[actor.id]
                                self.database.actors_db.actors_db.pop(actor.id, None)
                                self.database.vehicle_control_states.pop(
                                    actor.id,
                                    None,
                                )
                                self.database.walker_ai_states.pop(actor.id, None)

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

            if cmd == "start_movement_preview":
                self._start_movement_preview(world, data)

            if cmd == "set_movement_preview_transform":
                self._set_movement_preview_transform(data)

            if cmd == "stop_movement_preview":
                if data["session_id"] == self.movement_preview_session_id:
                    self.cleanup_movement_preview(data["reason"])

            if cmd == "set_vehicle_control_mode":
                self._set_vehicle_control_mode(world, data)

            if cmd == "set_walker_ai_enabled":
                self._set_walker_ai_enabled(world, data)

            if cmd == "set_walker_ai_speed":
                self._set_walker_ai_speed(data)

            self._service_manual_vehicle_control()

            world_snapshot = None
            if self.running and cmd != "stop_simulation_control":
                world_snapshot = self._service_simulation(world)
            if self.running:
                self._service_walker_ai(world, world_snapshot)
            if self.running:
                self._service_dashboard(world, world_snapshot=world_snapshot)

        self._shutdown_vehicle_controls(disable_autopilot=True)
        self._shutdown_walker_ai()
        self.cleanup_movement_preview("controller_stopped")
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
        if self.movement_preview_camera is not None:
            try:
                parent = self.movement_preview_camera.parent
                if (
                    not self.movement_preview_camera.is_alive
                    or parent is None
                    or not parent.is_alive
                ):
                    self.cleanup_movement_preview("actor_unavailable")
            except Exception:
                self.cleanup_movement_preview("camera_unavailable")

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

        movement_camera_id = self._actor_id(self.movement_preview_camera)
        if movement_camera_id is not None:
            internal_actor_ids.add(movement_camera_id)
            streaming_sensor_ids.add(movement_camera_id)

        for walker_controller in getattr(
            self,
            "walker_ai_controllers",
            {},
        ).values():
            controller_id = self._actor_id(walker_controller)
            if controller_id is not None:
                internal_actor_ids.add(controller_id)

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

    def _manual_control_is_active(self):
        actor_id = self.movement_preview_actor_id
        if actor_id is None or self.movement_control_queue is None:
            return False
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            return state is not None and state.get("mode") == MANUAL_CONTROL

    def _publish_vehicle_control_state(self, actor_id, session_id=None,
                                       error=None):
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            state = state.copy() if state is not None else None
        if state is None:
            return
        if session_id is None and actor_id == self.movement_preview_actor_id:
            session_id = self.movement_preview_session_id
        status = "vehicle_control_error" if error else "vehicle_control_state"
        self.database.movement_event_queue.put((status, {
            "session_id": session_id,
            "actor_id": actor_id,
            "control_state": state,
            "message": str(error) if error else None,
        }))

    def _get_managed_vehicle(self, actor_id):
        with self.database.lock:
            actor = self.database.actors_db.actors.get(actor_id)
            state = self.database.vehicle_control_states.get(actor_id)
        if state is None:
            raise RuntimeError(
                "The selected vehicle is not managed by this application."
            )
        if (
            actor is None
            or not getattr(actor, "is_alive", False)
            or not actor.type_id.startswith("vehicle.")
        ):
            raise RuntimeError("The selected vehicle is no longer available.")
        return actor

    def _ensure_traffic_manager(self, world):
        if self.traffic_manager is None:
            self.traffic_manager = self.client.get_trafficmanager(
                self.TRAFFIC_MANAGER_PORT
            )
        self.traffic_manager.set_synchronous_mode(
            bool(world.get_settings().synchronous_mode)
        )
        return self.traffic_manager

    def _update_traffic_manager_sync(self, world):
        if self.traffic_manager is None:
            return
        self.traffic_manager.set_synchronous_mode(
            bool(world.get_settings().synchronous_mode)
        )

    def _safe_stop_vehicle(self, actor, reverse=False):
        actor.apply_control(carla.VehicleControl(
            throttle=0.0,
            steer=0.0,
            brake=1.0,
            hand_brake=False,
            reverse=bool(reverse),
            manual_gear_shift=False,
        ))

    def _set_vehicle_control_mode(self, world, data):
        session_id = data["session_id"]
        actor_id = int(data["actor_id"])
        requested_mode = data["mode"]
        try:
            if requested_mode not in VEHICLE_CONTROL_MODES:
                raise ValueError(f"Unknown vehicle control mode: {requested_mode}")
            if (
                session_id != self.movement_preview_session_id
                or actor_id != self.movement_preview_actor_id
            ):
                raise RuntimeError("The Movement Control session is no longer active.")

            actor = self._get_managed_vehicle(actor_id)
            with self.database.lock:
                previous = self.database.vehicle_control_states[actor_id].copy()
            previous_mode = previous.get("mode", NO_CONTROL)

            if previous_mode == MANUAL_CONTROL and requested_mode != MANUAL_CONTROL:
                self._safe_stop_vehicle(actor, previous.get("reverse", False))
                with self.database.lock:
                    state = self.database.vehicle_control_states[actor_id]
                    state.update({
                        "mode": NO_CONTROL,
                        "status": "Manual Control ended; vehicle stopped safely.",
                        "input_fresh": False,
                    })
            if previous_mode == AUTOPILOT and requested_mode != AUTOPILOT:
                actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                with self.database.lock:
                    state = self.database.vehicle_control_states[actor_id]
                    state.update({
                        "mode": NO_CONTROL,
                        "status": "Autopilot disabled.",
                        "autopilot_enabled": False,
                        "traffic_manager_port": None,
                    })

            if requested_mode == AUTOPILOT:
                self._ensure_traffic_manager(world)
                actor.set_autopilot(True, self.TRAFFIC_MANAGER_PORT)
                status = (
                    f"Autopilot active (Traffic Manager port "
                    f"{self.TRAFFIC_MANAGER_PORT})."
                )
            elif requested_mode == MANUAL_CONTROL:
                self._safe_stop_vehicle(actor, previous.get("reverse", False))
                self.manual_control_command = None
                self.manual_control_received_at = None
                self.manual_control_next_apply = 0.0
                self.manual_control_input_fresh = False
                status = "Manual Control active; waiting for focused Pygame input."
            else:
                status = "No controller is active."

            with self.database.lock:
                state = self.database.vehicle_control_states[actor_id]
                state.update({
                    "mode": requested_mode,
                    "status": status,
                    "autopilot_enabled": requested_mode == AUTOPILOT,
                    "autopilot_state_source": "local",
                    "traffic_manager_port": (
                        self.TRAFFIC_MANAGER_PORT
                        if requested_mode == AUTOPILOT else None
                    ),
                    "input_fresh": False,
                    "error": None,
                })
            self._publish_vehicle_control_state(actor_id, session_id=session_id)
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            with self.database.lock:
                state = self.database.vehicle_control_states.get(actor_id)
                if state is not None:
                    state["status"] = f"Control mode change failed: {error}"
                    state["error"] = str(error)
            self._publish_vehicle_control_state(
                actor_id,
                session_id=session_id,
                error=error,
            )

    def _service_manual_vehicle_control(self):
        if not self._manual_control_is_active():
            return

        latest = None
        try:
            while True:
                latest = self.movement_control_queue.get_nowait()
        except queue.Empty:
            pass
        except (EOFError, OSError, ValueError) as error:
            self._manual_control_failure(error)
            return

        now = time.monotonic()
        if isinstance(latest, dict):
            self.manual_control_command = latest
            self.manual_control_received_at = now
        if now < self.manual_control_next_apply:
            return
        self.manual_control_next_apply = now + 1.0 / self.MANUAL_CONTROL_HZ

        actor_id = self.movement_preview_actor_id
        command = self.manual_control_command
        fresh = (
            command is not None
            and self.manual_control_received_at is not None
            and now - self.manual_control_received_at
                <= self.MANUAL_INPUT_TIMEOUT_SECONDS
            and bool(command.get("focused", False))
        )
        try:
            actor = self._get_managed_vehicle(actor_id)
            if fresh:
                reverse = bool(command.get("reverse", False))
                actor.apply_control(carla.VehicleControl(
                    throttle=max(0.0, min(1.0, float(command.get("throttle", 0.0)))),
                    steer=max(-1.0, min(1.0, float(command.get("steer", 0.0)))),
                    brake=max(0.0, min(1.0, float(command.get("brake", 0.0)))),
                    hand_brake=bool(command.get("hand_brake", False)),
                    reverse=reverse,
                    manual_gear_shift=False,
                ))
            else:
                with self.database.lock:
                    reverse = self.database.vehicle_control_states[actor_id].get(
                        "reverse", False
                    )
                self._safe_stop_vehicle(actor, reverse)

            state_changed = fresh != self.manual_control_input_fresh
            with self.database.lock:
                state = self.database.vehicle_control_states[actor_id]
                if fresh and state.get("reverse") != reverse:
                    state_changed = True
                state.update({
                    "reverse": reverse,
                    "input_fresh": fresh,
                    "status": (
                        "Manual Control active."
                        if fresh else
                        "Manual Control safety stop: input unavailable or unfocused."
                    ),
                    "error": None,
                })
            self.manual_control_input_fresh = fresh
            if state_changed:
                self._publish_vehicle_control_state(actor_id)
        except Exception as error:
            self._manual_control_failure(error)

    def _manual_control_failure(self, error):
        actor_id = self.movement_preview_actor_id
        self.dashboard_monitor.record_error(error)
        if actor_id is None:
            return
        try:
            actor = self._get_managed_vehicle(actor_id)
            with self.database.lock:
                reverse = self.database.vehicle_control_states[actor_id].get(
                    "reverse", False
                )
            self._safe_stop_vehicle(actor, reverse)
        except Exception:
            pass
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            if state is not None:
                state.update({
                    "mode": NO_CONTROL,
                    "status": f"Manual Control stopped: {error}",
                    "input_fresh": False,
                    "error": str(error),
                })
        self._publish_vehicle_control_state(actor_id, error=error)

    def _close_manual_session(self, actor_id):
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            state_copy = state.copy() if state is not None else None
            actor = self.database.actors_db.actors.get(actor_id)
        if state_copy is None or state_copy.get("mode") != MANUAL_CONTROL:
            return
        try:
            if actor is not None and getattr(actor, "is_alive", False):
                self._safe_stop_vehicle(actor, state_copy.get("reverse", False))
        except Exception as error:
            self.dashboard_monitor.record_error(error)
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            if state is not None and state.get("mode") == MANUAL_CONTROL:
                state.update({
                    "mode": NO_CONTROL,
                    "status": "Manual Control ended; vehicle stopped safely.",
                    "input_fresh": False,
                    "error": None,
                })
        self._publish_vehicle_control_state(actor_id)

    def _release_vehicle_control(self, actor_id, disable_autopilot):
        self._close_manual_session(actor_id)
        with self.database.lock:
            actor = self.database.actors_db.actors.get(actor_id)
            state = self.database.vehicle_control_states.get(actor_id)
            state_copy = state.copy() if state is not None else None
        if (
            disable_autopilot
            and state_copy is not None
            and state_copy.get("mode") == AUTOPILOT
            and actor is not None
            and getattr(actor, "is_alive", False)
        ):
            try:
                actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
            except Exception as error:
                self.dashboard_monitor.record_error(error)
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            if state is not None:
                state.update({
                    "mode": NO_CONTROL,
                    "status": "Vehicle control released.",
                    "autopilot_enabled": False,
                    "traffic_manager_port": None,
                    "input_fresh": False,
                })

    def _shutdown_vehicle_controls(self, disable_autopilot):
        try:
            with self.database.lock:
                actor_ids = list(self.database.vehicle_control_states)
            for actor_id in actor_ids:
                self._release_vehicle_control(actor_id, disable_autopilot)
        except Exception as error:
            try:
                self.dashboard_monitor.record_error(error)
            except Exception:
                pass

    def _publish_walker_ai_state(self, walker_id, session_id=None,
                                 error=None):
        with self.database.lock:
            state = self.database.walker_ai_states.get(walker_id)
            state = state.copy() if state is not None else None
        if state is None:
            return
        if session_id is None and walker_id == self.movement_preview_actor_id:
            session_id = self.movement_preview_session_id
        if session_id is None:
            return
        status = "walker_ai_error" if error else "walker_ai_state"
        self.database.movement_event_queue.put((status, {
            "session_id": session_id,
            "actor_id": walker_id,
            "walker_ai_state": state,
            "message": str(error) if error else None,
        }))

    def _get_managed_walker(self, walker_id):
        with self.database.lock:
            walker = self.database.actors_db.actors.get(walker_id)
            state = self.database.walker_ai_states.get(walker_id)
        if state is None:
            raise RuntimeError(
                "The selected pedestrian is not managed by this application."
            )
        if (
            walker is None
            or not getattr(walker, "is_alive", False)
            or not walker.type_id.startswith("walker.")
        ):
            raise RuntimeError("The selected pedestrian is no longer available.")
        return walker

    @staticmethod
    def _walker_distance(first, second):
        return (
            (float(first.x) - float(second.x)) ** 2
            + (float(first.y) - float(second.y)) ** 2
            + (float(first.z) - float(second.z)) ** 2
        ) ** 0.5

    @staticmethod
    def _serialize_location(location):
        if location is None:
            return None
        return {
            "x": float(location.x),
            "y": float(location.y),
            "z": float(location.z),
        }

    def _validate_walker_speed(self, speed):
        value = float(speed)
        if not MIN_WALKER_SPEED <= value <= MAX_WALKER_SPEED:
            raise ValueError(
                f"Walker speed must be between {MIN_WALKER_SPEED:.1f} "
                f"and {MAX_WALKER_SPEED:.1f} m/s."
            )
        return value

    def _get_or_create_walker_controller(self, world, walker):
        controller = self.walker_ai_controllers.get(walker.id)
        if controller is not None and getattr(controller, "is_alive", False):
            return controller, False

        blueprint = world.get_blueprint_library().find("controller.ai.walker")
        controller = world.spawn_actor(
            blueprint,
            carla.Transform(),
            attach_to=walker,
        )
        if controller is None:
            raise RuntimeError("CARLA did not create the Walker AI controller.")
        self.walker_ai_controllers[walker.id] = controller
        self.walker_ai_runtime[walker.id] = {
            "target": None,
            "next_check": 0.0,
            "last_distance": None,
            "last_progress_at": time.monotonic(),
            "last_progress_sim_time": None,
            "last_frame": None,
        }
        with self.database.lock:
            state = self.database.walker_ai_states[walker.id]
            state["controller_id"] = controller.id
        return controller, True

    def _assign_walker_target(self, world, walker, controller,
                              reason="random navigation"):
        runtime = self.walker_ai_runtime.setdefault(walker.id, {})
        target = None
        current_location = walker.get_location()
        for _attempt in range(5):
            candidate = world.get_random_location_from_navigation()
            if candidate is None:
                continue
            target = candidate
            if self._walker_distance(current_location, candidate) > 1.0:
                break

        now = time.monotonic()
        if target is None:
            runtime.update({
                "target": None,
                "next_check": now + self.WALKER_AI_TARGET_RETRY_SECONDS,
                "last_distance": None,
                "last_progress_at": now,
                "last_progress_sim_time": None,
            })
            with self.database.lock:
                state = self.database.walker_ai_states[walker.id]
                state.update({
                    "target": None,
                    "status": (
                        "Walker AI is active but no navigation point is "
                        "currently available; retrying."
                    ),
                    "error": None,
                })
            return False

        controller.go_to_location(target)
        distance = self._walker_distance(current_location, target)
        runtime.update({
            "target": target,
            "next_check": now + self.WALKER_AI_CHECK_INTERVAL_SECONDS,
            "last_distance": distance,
            "last_progress_at": now,
            "last_progress_sim_time": None,
        })
        with self.database.lock:
            state = self.database.walker_ai_states[walker.id]
            state.update({
                "target": self._serialize_location(target),
                "status": f"Walker AI running: {reason}.",
                "error": None,
            })
        return True

    def _set_walker_ai_enabled(self, world, data):
        session_id = data["session_id"]
        walker_id = int(data["actor_id"])
        try:
            if (
                session_id != self.movement_preview_session_id
                or walker_id != self.movement_preview_actor_id
            ):
                raise RuntimeError("The Movement Control session is no longer active.")
            walker = self._get_managed_walker(walker_id)

            if data["enabled"]:
                speed = self._validate_walker_speed(data["speed"])
                controller, controller_created = (
                    self._get_or_create_walker_controller(
                        world,
                        walker,
                    )
                )
                with self.database.lock:
                    was_active = self.database.walker_ai_states[walker_id][
                        "active"
                    ]
                if controller_created or not was_active:
                    controller.start()
                controller.set_max_speed(speed)
                with self.database.lock:
                    state = self.database.walker_ai_states[walker_id]
                    state.update({
                        "mode": WALKER_AI_RUNNING,
                        "active": True,
                        "speed": speed,
                        "controller_id": controller.id,
                        "random_navigation": True,
                        "error": None,
                    })
                runtime = self.walker_ai_runtime.get(walker_id, {})
                if runtime.get("target") is None:
                    self._assign_walker_target(
                        world,
                        walker,
                        controller,
                        "random navigation started",
                    )
            else:
                with self.database.lock:
                    saved_speed = self.database.walker_ai_states[walker_id][
                        "speed"
                    ]
                try:
                    speed = self._validate_walker_speed(data["speed"])
                except (TypeError, ValueError):
                    speed = saved_speed
                controller = self.walker_ai_controllers.get(walker_id)
                if controller is not None and getattr(
                    controller,
                    "is_alive",
                    False,
                ):
                    controller.stop()
                runtime = self.walker_ai_runtime.setdefault(walker_id, {})
                runtime.update({
                    "target": None,
                    "next_check": 0.0,
                    "last_distance": None,
                    "last_progress_at": time.monotonic(),
                    "last_progress_sim_time": None,
                    "last_frame": None,
                })
                with self.database.lock:
                    state = self.database.walker_ai_states[walker_id]
                    state.update({
                        "mode": WALKER_AI_STOPPED,
                        "active": False,
                        "status": "Walker AI is stopped and can be resumed.",
                        "speed": speed,
                        "target": None,
                        "error": None,
                    })
            self._publish_walker_ai_state(walker_id, session_id=session_id)
        except Exception as error:
            self._walker_ai_failure(
                walker_id,
                error,
                session_id=session_id,
            )

    def _set_walker_ai_speed(self, data):
        session_id = data["session_id"]
        walker_id = int(data["actor_id"])
        try:
            if (
                session_id != self.movement_preview_session_id
                or walker_id != self.movement_preview_actor_id
            ):
                raise RuntimeError("The Movement Control session is no longer active.")
            speed = self._validate_walker_speed(data["speed"])
            self._get_managed_walker(walker_id)
            with self.database.lock:
                state = self.database.walker_ai_states[walker_id]
                active = state["active"]
            controller = self.walker_ai_controllers.get(walker_id)
            if active:
                if controller is None or not getattr(controller, "is_alive", False):
                    raise RuntimeError("The Walker AI controller is unavailable.")
                controller.set_max_speed(speed)
            with self.database.lock:
                state = self.database.walker_ai_states[walker_id]
                state["speed"] = speed
                state["status"] = (
                    f"Walker AI speed set to {speed:.1f} m/s."
                    if active else
                    f"Walker AI stopped; speed {speed:.1f} m/s saved."
                )
                state["error"] = None
            self._publish_walker_ai_state(walker_id, session_id=session_id)
        except Exception as error:
            self._walker_ai_failure(
                walker_id,
                error,
                session_id=session_id,
                stop_ai=False,
            )

    def _service_walker_ai(self, world, world_snapshot=None):
        now = time.monotonic()
        with self.database.lock:
            active_ids = [
                walker_id
                for walker_id, state in self.database.walker_ai_states.items()
                if state.get("active")
            ]
        if not active_ids:
            return
        if world_snapshot is None:
            try:
                world_snapshot = world.get_snapshot()
            except Exception:
                world_snapshot = None
        frame = getattr(world_snapshot, "frame", None)
        timestamp = getattr(world_snapshot, "timestamp", None)
        simulation_time = getattr(timestamp, "elapsed_seconds", None)

        for walker_id in active_ids:
            runtime = self.walker_ai_runtime.setdefault(walker_id, {
                "target": None,
                "next_check": 0.0,
                "last_distance": None,
                "last_progress_at": now,
                "last_progress_sim_time": None,
                "last_frame": None,
            })
            if now < runtime.get("next_check", 0.0):
                continue
            runtime["next_check"] = now + self.WALKER_AI_CHECK_INTERVAL_SECONDS
            try:
                walker = self._get_managed_walker(walker_id)
                controller = self.walker_ai_controllers.get(walker_id)
                if controller is None or not getattr(controller, "is_alive", False):
                    raise RuntimeError("The Walker AI controller is unavailable.")

                target = runtime.get("target")
                if target is None:
                    changed = self._assign_walker_target(
                        world,
                        walker,
                        controller,
                        "navigation point acquired",
                    )
                    if changed:
                        self._publish_walker_ai_state(walker_id)
                    continue

                if frame is None or runtime.get("last_frame") == frame:
                    continue
                runtime["last_frame"] = frame

                distance = self._walker_distance(walker.get_location(), target)
                if distance <= self.WALKER_AI_REACHED_DISTANCE:
                    self._assign_walker_target(
                        world,
                        walker,
                        controller,
                        "destination reached; new random destination selected",
                    )
                    self._publish_walker_ai_state(walker_id)
                    continue

                last_distance = runtime.get("last_distance")
                is_stalled = False
                if (
                    simulation_time is not None
                    and runtime.get("last_progress_sim_time") is None
                ):
                    runtime["last_progress_sim_time"] = simulation_time
                if (
                    last_distance is None
                    or distance <= last_distance - self.WALKER_AI_PROGRESS_DISTANCE
                ):
                    runtime["last_distance"] = distance
                    runtime["last_progress_at"] = now
                    if simulation_time is not None:
                        runtime["last_progress_sim_time"] = simulation_time
                elif simulation_time is not None:
                    is_stalled = (
                        simulation_time
                        - runtime.get("last_progress_sim_time", simulation_time)
                        >= self.WALKER_AI_STALL_SECONDS
                    )
                else:
                    is_stalled = (
                        now - runtime.get("last_progress_at", now)
                        >= self.WALKER_AI_STALL_SECONDS
                    )
                if last_distance is not None and is_stalled:
                    self._assign_walker_target(
                        world,
                        walker,
                        controller,
                        "no progress; new random destination selected",
                    )
                    self._publish_walker_ai_state(walker_id)
            except Exception as error:
                self._walker_ai_failure(walker_id, error)

    def _walker_ai_failure(self, walker_id, error, session_id=None,
                           stop_ai=True):
        self.dashboard_monitor.record_error(error)
        if stop_ai:
            self._destroy_walker_ai(walker_id, remove_state=False)
        with self.database.lock:
            state = self.database.walker_ai_states.get(walker_id)
            if state is not None:
                state.update({
                    "mode": (
                        WALKER_AI_ERROR if stop_ai else state.get("mode")
                    ),
                    "active": False if stop_ai else state.get("active", False),
                    "status": f"Walker AI error: {error}",
                    "target": None if stop_ai else state.get("target"),
                    "error": str(error),
                })
        self._publish_walker_ai_state(
            walker_id,
            session_id=session_id,
            error=error,
        )

    def _destroy_walker_ai(self, walker_id, remove_state=False):
        controller = self.walker_ai_controllers.pop(walker_id, None)
        self.walker_ai_runtime.pop(walker_id, None)
        if controller is not None:
            try:
                if getattr(controller, "is_alive", False):
                    controller.stop()
            except Exception as error:
                self.dashboard_monitor.record_error(error)
            try:
                if getattr(controller, "is_alive", False):
                    controller.destroy()
            except Exception as error:
                self.dashboard_monitor.record_error(error)
        with self.database.lock:
            if remove_state:
                self.database.walker_ai_states.pop(walker_id, None)
            else:
                state = self.database.walker_ai_states.get(walker_id)
                if state is not None:
                    state.update({
                        "mode": WALKER_AI_STOPPED,
                        "active": False,
                        "status": "Walker AI controller was released.",
                        "controller_id": None,
                        "target": None,
                    })

    def _shutdown_walker_ai(self):
        try:
            with self.database.lock:
                walker_ids = set(self.database.walker_ai_states)
            walker_ids.update(self.walker_ai_controllers)
            for walker_id in list(walker_ids):
                self._destroy_walker_ai(walker_id, remove_state=False)
        except Exception as error:
            try:
                self.dashboard_monitor.record_error(error)
            except Exception:
                pass

    def _start_movement_preview(self, world, data):
        session_id = data["session_id"]
        publisher = data["publisher"]
        camera = None
        try:
            if self.movement_preview_camera is not None:
                raise RuntimeError("Another Movement Control session is already active.")

            with self.database.lock:
                parent_actor = self.database.actors_db.actors.get(data["actor_id"])
            if parent_actor is None or not parent_actor.is_alive:
                raise RuntimeError("The selected actor is no longer available.")
            if not parent_actor.type_id.startswith(("vehicle.", "walker.")):
                raise RuntimeError(
                    "Movement Preview supports vehicles and pedestrians only."
                )

            camera_blueprint = world.get_blueprint_library().find(
                "sensor.camera.rgb"
            )
            attributes = {
                "image_size_x": 800,
                "image_size_y": 450,
                "fov": 90.0,
                "sensor_tick": 1.0 / 30.0,
                "role_name": "movement_control_preview",
            }
            for name, value in attributes.items():
                if camera_blueprint.has_attribute(name):
                    camera_blueprint.set_attribute(name, str(value))

            transform = camera_transform_for_actor(
                parent_actor.type_id,
                data["preset"],
                carla,
            )
            camera = world.spawn_actor(
                camera_blueprint,
                transform,
                attach_to=parent_actor,
                attachment_type=carla.AttachmentType.Rigid,
            )
            if camera is None:
                raise RuntimeError("CARLA did not create the Movement RGB camera.")

            camera.listen(publisher.publish)
            self.movement_preview_camera = camera
            self.movement_preview_publisher = publisher
            self.movement_preview_session_id = session_id
            self.movement_preview_actor_id = parent_actor.id
            self.movement_control_queue = data["control_queue"]
            self.manual_control_command = None
            self.manual_control_received_at = None
            self.manual_control_input_fresh = False
            self.database.movement_event_queue.put((
                "movement_camera_started",
                {
                    "session_id": session_id,
                    "sensor_id": camera.id,
                    "actor_id": parent_actor.id,
                    "preset": data["preset"],
                },
            ))
            if parent_actor.type_id.startswith("vehicle."):
                self._publish_vehicle_control_state(
                    parent_actor.id,
                    session_id=session_id,
                )
            elif parent_actor.type_id.startswith("walker."):
                self._publish_walker_ai_state(
                    parent_actor.id,
                    session_id=session_id,
                )
        except Exception as error:
            publisher.close()
            if camera is not None:
                try:
                    is_listening = getattr(camera, "is_listening", False)
                    if callable(is_listening):
                        is_listening = is_listening()
                    if is_listening:
                        camera.stop()
                except Exception:
                    pass
                try:
                    if camera.is_alive:
                        camera.destroy()
                except Exception:
                    pass
            self.dashboard_monitor.record_error(error)
            self.database.movement_event_queue.put((
                "movement_camera_error",
                {
                    "session_id": session_id,
                    "message": f"Could not start Movement Preview camera: {error}",
                },
            ))

    def _set_movement_preview_transform(self, data):
        session_id = data["session_id"]
        try:
            if (
                session_id != self.movement_preview_session_id
                or self.movement_preview_camera is None
                or not self.movement_preview_camera.is_alive
            ):
                raise RuntimeError("The Movement Preview camera is not active.")
            with self.database.lock:
                parent_actor = self.database.actors_db.actors.get(
                    self.movement_preview_actor_id
                )
            if parent_actor is None or not parent_actor.is_alive:
                raise RuntimeError("The selected actor is no longer available.")

            transform = camera_transform_for_actor(
                parent_actor.type_id,
                data["preset"],
                carla,
            )
            self.movement_preview_camera.set_transform(transform)
            self.database.movement_event_queue.put((
                "movement_camera_preset_changed",
                {
                    "session_id": session_id,
                    "sensor_id": self.movement_preview_camera.id,
                    "preset": data["preset"],
                },
            ))
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self.database.movement_event_queue.put((
                "movement_camera_error",
                {
                    "session_id": session_id,
                    "message": f"Could not change camera preset: {error}",
                },
            ))

    def cleanup_movement_preview(self, reason="preview_stopped"):
        camera = self.movement_preview_camera
        publisher = self.movement_preview_publisher
        session_id = self.movement_preview_session_id
        actor_id = self.movement_preview_actor_id

        if actor_id is not None:
            self._close_manual_session(actor_id)

        self.movement_preview_camera = None
        self.movement_preview_publisher = None
        self.movement_preview_session_id = None
        self.movement_preview_actor_id = None
        self.movement_control_queue = None
        self.manual_control_command = None
        self.manual_control_received_at = None
        self.manual_control_input_fresh = False

        if publisher is not None:
            publisher.close()
        if camera is not None:
            try:
                is_listening = getattr(camera, "is_listening", False)
                if callable(is_listening):
                    is_listening = is_listening()
                if is_listening:
                    camera.stop()
            except Exception:
                pass
            try:
                if camera.is_alive:
                    camera.destroy()
            except Exception as error:
                self.dashboard_monitor.record_error(error)

        if session_id is not None:
            self.database.movement_event_queue.put((
                "movement_camera_stopped",
                {
                    "session_id": session_id,
                    "reason": reason,
                },
            ))


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

    def start_movement_preview(self, session_id, actor_id, actor_type,
                               preset, publisher, control_queue):
        self.command_queue.put(("start_movement_preview", {
            "session_id": session_id,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "preset": preset,
            "publisher": publisher,
            "control_queue": control_queue,
        }))

    def set_vehicle_control_mode(self, session_id, actor_id, mode):
        self.command_queue.put(("set_vehicle_control_mode", {
            "session_id": session_id,
            "actor_id": actor_id,
            "mode": mode,
        }))

    def set_walker_ai_enabled(self, session_id, actor_id, enabled, speed):
        self.command_queue.put(("set_walker_ai_enabled", {
            "session_id": session_id,
            "actor_id": actor_id,
            "enabled": bool(enabled),
            "speed": speed,
        }))

    def set_walker_ai_speed(self, session_id, actor_id, speed):
        self.command_queue.put(("set_walker_ai_speed", {
            "session_id": session_id,
            "actor_id": actor_id,
            "speed": speed,
        }))

    def set_movement_preview_transform(self, session_id, preset):
        self.command_queue.put(("set_movement_preview_transform", {
            "session_id": session_id,
            "preset": preset,
        }))

    def stop_movement_preview(self, session_id, reason="preview_stopped"):
        self.command_queue.put(("stop_movement_preview", {
            "session_id": session_id,
            "reason": reason,
        }))
