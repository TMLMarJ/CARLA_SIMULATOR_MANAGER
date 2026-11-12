import queue
import threading

from core.carla_pythonapi import load_global_route_planner
from core.command_bus import CommandBus, CommandDispatcher
from core.contracts import RuntimeContext
from movement.runtime import MovementRuntime
from movement.destination import (
    resolve_vehicle_waypoint,
    same_waypoint_segment,
    waypoint_snapshot,
)
from scenarios.runtime import ScenarioRuntime
from simulation.runtime import SimulationRuntime

GlobalRoutePlanner = None
from utilities.functions import *
from simulation.dashboard import SimulationDashboardMonitor
from simulation.manager import SimulationManager
from scenarios.manager import ScenarioManager
from scenarios.adoption import (
    ActorAdopter,
    ActorAdoptionReport,
    AdoptionStatus,
)
from scenarios.paths import (
    ScenarioPathError,
    normalize_scenario_path,
)
from scenarios.synchronization import WorldSynchronizer
from scenarios.cleaner import (
    ClearMode,
    WorldCleaner,
    WorldClearReport,
)
from preview.map import MapPreviewCache, MapPreviewGenerator
from services.actors import ActorService
from services.blueprints import BlueprintService
from services.sensors import SensorService
from services.weather import apply_weather
from services.world import WorldService

class CarlaController(MovementRuntime, SimulationRuntime, ScenarioRuntime):
    TICK_TIMEOUT_SECONDS = 5.0
    TRAFFIC_MANAGER_PORT = 8000
    APPLICATION_TRAFFIC_MANAGER_PORT = 8050
    MANUAL_CONTROL_HZ = 20.0
    MANUAL_INPUT_TIMEOUT_SECONDS = 0.5
    VEHICLE_NAVIGATION_CHECK_INTERVAL_SECONDS = 0.5
    VEHICLE_NAVIGATION_SAMPLING_RESOLUTION = 2.0
    VEHICLE_DESTINATION_ARRIVAL_DISTANCE = 3.0
    VEHICLE_DESTINATION_STOP_DISTANCE = 6.0
    VEHICLE_DESTINATION_STOP_SPEED = 0.5
    VEHICLE_DESTINATION_STUCK_SPEED = 0.3
    VEHICLE_DESTINATION_STUCK_SECONDS = 60.0
    VEHICLE_DESTINATION_PROGRESS_DISTANCE = 2.0
    WALKER_AI_CHECK_INTERVAL_SECONDS = 0.5
    WALKER_AI_FALLBACK_STALL_SECONDS = 2.0
    WALKER_AI_FALLBACK_LEG_DISTANCE = 4.0
    WALKER_DESTINATION_ARRIVAL_DISTANCE = 0.6
    WALKER_DESTINATION_PROGRESS_DISTANCE = 0.05
    MOVEMENT_WORLD_COMMANDS = frozenset({
        "start_movement_preview",
        "stop_movement_preview",
        "set_movement_preview_transform",
        "set_vehicle_control_mode",
        "set_walker_ai_enabled",
        "set_walker_ai_speed",
        "set_walker_control_mode",
        "set_walker_manual_control",
        "validate_movement_destination",
        "confirm_movement_destination",
        "clear_movement_destination",
        "get_movement_destination_map",
        "get_movement_actor_location",
        "get_movement_navigation_snapshot",
    })

    def __init__(self, database, spectator_status_provider=None,
                 host="127.0.0.1", port=2000):
        if callable(spectator_status_provider):
            self._spectator_status_provider = spectator_status_provider
        elif spectator_status_provider is not None:
            legacy_controller = spectator_status_provider
            self._spectator_status_provider = lambda: {
                "state": getattr(legacy_controller, "spectator_state", "Unknown"),
                "camera_id": getattr(legacy_controller, "spectator_camera_id", None),
            }
        else:
            self._spectator_status_provider = None
        self.command_bus = CommandBus()
        self.command_queue = self.command_bus.command_queue
        self.priority_command_queue = self.command_bus.priority_command_queue
        self.connection_queue = queue.Queue()
        self.running = True
        self.shutdown_event = threading.Event()
        self.database = database
        self.thread = None
        self.data_ready = False
        self.client = None
        self.world_changed_event = threading.Event()
        self.world_transition_event = threading.Event()
        self.host = host
        self.port = port
        self._configure_traffic_manager()
        self.shutdown_request = False
        self.sensor_preview_ids = set()
        self.sensor_status_errors = {}
        self.movement_preview_camera = None
        self.movement_preview_publisher = None
        self.movement_preview_session_id = None
        self.movement_preview_actor_id = None
        self.movement_preview_world_id = None
        self.movement_preview_world_generation = None
        self.movement_control_queue = None
        self.manual_control_command = None
        self.manual_control_received_at = None
        self.manual_control_next_apply = 0.0
        self.manual_control_input_fresh = False
        self.traffic_manager = None
        self.traffic_manager_world_id = None
        self.traffic_manager_last_sync = None
        self.vehicle_navigation_runtime = {}
        self.vehicle_navigation_submission_counts = {}
        self.vehicle_navigation_submission_sequence = 0
        self.movement_navigation_map_cache = None
        self.walker_ai_controllers = {}
        self.walker_ai_runtime = {}
        self.map_infrastructure_actor_ids = set(
            getattr(database, "reconnect_infrastructure_actor_ids", ())
        )
        self.map_infrastructure_world_id = getattr(
            database,
            "reconnect_infrastructure_world_id",
            None,
        )
        self.map_infrastructure_map_name = getattr(
            database,
            "reconnect_infrastructure_map_name",
            None,
        )
        self.simulation_manager = SimulationManager()
        self.dashboard_monitor = SimulationDashboardMonitor()
        self.world_synchronizer = WorldSynchronizer()
        self.actor_adopter = ActorAdopter()
        self.world_cleaner = WorldCleaner()
        self.map_preview_generator = MapPreviewGenerator()
        self.scenario_manager = ScenarioManager(self.world_cleaner)
        self.scenario_operation_lock = threading.Lock()
        self.scenario_operation_pending = False
        self.scenario_operation_kind = None
        self.active_clear_plan = None
        self.active_load_plan = None
        self.load_execution_queued = False
        self.clear_execution_queued = False
        self._next_snapshot_check = 0.0
        self.blueprint_service = BlueprintService()
        self.actor_service = ActorService(
            self.database,
            self.blueprint_service,
        )
        self.sensor_service = SensorService(
            self.database,
            self.dashboard_monitor,
            self.sensor_preview_ids,
            self.sensor_status_errors,
        )
        self.world_service = WorldService()
        self.command_dispatcher = CommandDispatcher()
        self._register_service_handlers()

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    @staticmethod
    def _find_blueprint_checked(blueprint_library, blueprint_id):
        return BlueprintService.find_checked(
            blueprint_library,
            blueprint_id,
        )

    @classmethod
    def _spawn_actor_checked(cls, world, blueprint_library, blueprint_id,
                             transform, try_spawn=True, attributes=None):
        service = ActorService(None)
        return service.spawn_checked(
            world,
            blueprint_library,
            blueprint_id,
            transform,
            try_spawn=try_spawn,
            attributes=attributes,
        )

    def _record_actor_operation_result(self, operation, blueprint_id,
                                       actor=None, error=None,
                                       previous_actor_id=None, request_id=None,
                                       warnings=None, sensor_id_map=None):
        result = {
            "operation": operation,
            "success": error is None,
            "requested_blueprint_id": str(blueprint_id),
            "actual_type_id": (
                str(actor.type_id) if actor is not None else None
            ),
            "actor_id": actor.id if actor is not None else None,
            "previous_actor_id": previous_actor_id,
            "error": str(error) if error is not None else None,
            "request_id": request_id,
            "warnings": list(warnings or []),
            "sensor_id_map": dict(sensor_id_map or {}),
        }
        with self.database.lock:
            self.database.actor_operation_result = result
        actor_event_queue = getattr(self.database, "actor_event_queue", None)
        if actor_event_queue is not None:
            actor_event_queue.put(("actor_operation_result", result))
        return result

    @staticmethod
    def _physics_value(value):
        return ActorService.physics_value(value)

    @classmethod
    def _physics_snapshot(cls, physics):
        return ActorService.physics_snapshot(physics)

    @classmethod
    def _physics_mismatches(cls, requested, actual, tolerance=1e-3):
        return ActorService.physics_mismatches(
            requested,
            actual,
            tolerance,
        )

    @classmethod
    def _apply_vehicle_physics_checked(cls, actor, physics):
        return ActorService.apply_vehicle_physics_checked(actor, physics)

    def _register_service_handlers(self):
        handlers = {
            "spawn": self._dispatch_spawn,
            "change_world": self._dispatch_change_world,
            "change_weather": self._dispatch_change_weather,
            "remove_actor": self._dispatch_remove_actor,
            "set_actor_transform": self._dispatch_set_actor_transform,
            "attach_sensor": self._dispatch_attach_sensor,
            "remove_sensor": self._dispatch_remove_sensor,
            "change_sensor_transform": self._dispatch_change_sensor_transform,
            "start_sensor_preview": self._dispatch_start_sensor_preview,
            "stop_sensor_preview": self._dispatch_stop_sensor_preview,
        }
        for command_name, handler in handlers.items():
            self.command_dispatcher.register(command_name, handler)

    def _dispatch_spawn(self, context, data):
        self.actor_service.spawn(
            context,
            data,
            self.database.blueprint_new_data,
            self.database.ghost_physic,
            self._record_actor_operation_result,
        )

    def _dispatch_change_world(self, context, map_name):
        self.world_transition_event.set()
        self._tm_debug_event(
            "load_world_begin",
            world=context.world,
            map_name=map_name,
        )
        with self.database.lock:
            previous_world_id = getattr(self.database, "world_id", None)
        self._tm_debug_event(
            "world_cleanup_begin",
            world=context.world,
            map_name=map_name,
        )
        try:
            self._shutdown_vehicle_controls(
                disable_autopilot=True,
                clear_paths=False,
                strict=True,
            )
            self._shutdown_walker_ai(strict=True)
            self.cleanup_movement_preview(
                "world_changed",
                strict=True,
            )
            self.cleanup_sensor_previews(strict=True)
            self.cleanup_preview(strict=True)
            self._prepare_traffic_manager_for_world_change()
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self.database.world_change_error = str(error)
            self.database.world_changed = False
            self.world_transition_event.clear()
            self.world_changed_event.set()
            return
        self._invalidate_traffic_manager_reference()
        self.sensor_status_errors.clear()
        self._invalidate_database_episode_references()
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
        self._tm_debug_event(
            "world_cleanup_complete",
            world=context.world,
            map_name=map_name,
        )
        context.world = None
        context.blueprint_library = None

        try:
            self._tm_debug_event(
                "load_world_rpc_begin",
                world=context.world,
                map_name=map_name,
            )
            new_world = self.world_service.load_with_retry(
                self.client,
                map_name,
                self.shutdown_event,
            )
            self._tm_debug_event(
                "load_world_rpc_complete",
                world=new_world,
                map_name=map_name,
            )
            context.world = new_world
            context.blueprint_library = new_world.get_blueprint_library()
            self._capture_map_infrastructure(new_world)

            new_settings = self.world_service.install(
                context,
                self.simulation_manager,
            )
            self._tm_debug_event(
                "load_world_complete",
                world=new_world,
                map_name=map_name,
            )
            self.dashboard_monitor.reset_world(
                self.database,
                new_world,
            )
            try:
                self._perform_world_sync(
                    new_world,
                    reason="map_changed",
                )
            except Exception as sync_error:
                self.dashboard_monitor.record_error(sync_error)
                self.database.scenario_event_queue.put((
                    "world_sync_error",
                    {
                        "message": str(sync_error),
                        "reason": "map_changed",
                    },
                ))
            self.database.simulation_event_queue.put((
                "simulation_settings_loaded",
                new_settings,
            ))
            self._publish_simulation_control_state(force=True)
            self._queue_map_preview_generation(new_world)
        except Exception as error:
            error_message = str(error)
            try:
                (
                    recovered_world,
                    recovered_blueprints,
                    episode_changed,
                ) = self._recover_world_after_failed_change(previous_world_id)
                context.world = recovered_world
                context.blueprint_library = recovered_blueprints
                recovery_state = (
                    "CARLA switched to a different world episode; "
                    "local references were rebuilt."
                    if episode_changed else
                    "The previous world episode is still active and "
                    "was synchronized."
                )
                error_message = f"{error_message} {recovery_state}"
            except Exception as recovery_error:
                self.dashboard_monitor.record_error(recovery_error)
                error_message = (
                    f"{error_message} Server state recovery failed: "
                    f"{recovery_error}"
                )
            self.database.world_change_error = error_message
            self.dashboard_monitor.record_error(error)
            print(f"Error changing map: {error_message}")
        finally:
            self.client.set_timeout(5.0)
            self.world_changed_event.set()
            self.world_transition_event.clear()

    def _dispatch_change_weather(self, context, data):
        context.world = apply_weather(context.client, data)

    def _dispatch_remove_actor(self, _context, actor_id):
        if actor_id == self.movement_preview_actor_id:
            self.cleanup_movement_preview("actor_removed")
        self._destroy_walker_ai(actor_id, remove_state=True)
        self._release_vehicle_control(
            actor_id,
            disable_autopilot=True,
        )
        self.actor_service.remove(
            actor_id,
            self.sensor_service.remove_for_parent,
        )

    def _dispatch_set_actor_transform(self, _context, data):
        self.actor_service.set_transform(data)

    def _dispatch_attach_sensor(self, _context, data):
        self.sensor_service.attach(data)

    def _dispatch_remove_sensor(self, _context, data):
        self.sensor_service.remove(data)

    def _dispatch_change_sensor_transform(self, _context, data):
        self.sensor_service.change_transform(data)

    def _dispatch_start_sensor_preview(self, _context, data):
        self.sensor_service.start_preview(data)

    def _dispatch_stop_sensor_preview(self, _context, data):
        self.sensor_service.stop_preview_actor(data)

    def run(self):
        try:
            self._run_controller()
        except Exception as error:
            self._shutdown_vehicle_controls(
                disable_autopilot=True,
                clear_paths=False,
            )
            self._shutdown_walker_ai()
            self.cleanup_movement_preview("controller_error")
            self._shutdown_exclusive_traffic_manager(
                "controller_error",
                strict=False,
            )
            self._invalidate_traffic_manager_reference()
            self._invalidate_database_world_reference()
            with self.scenario_operation_lock:
                self.active_load_plan = None
                if self.scenario_operation_kind in (
                    "load_preflight",
                    "load",
                ):
                    self.scenario_operation_pending = False
                    self.scenario_operation_kind = None
                    self.load_execution_queued = False
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
            self._tm_debug_event(
                "tm_configuration",
                configuration_error=(
                    self.traffic_manager_configuration_error
                ),
            )
            if self.traffic_manager_configuration_error is not None:
                raise RuntimeError(self.traffic_manager_configuration_error)
            self.client = carla.Client(self.host, self.port)
            self.client.set_timeout(5.0)
            world = self.client.get_world()
            world_id = self._world_id(world)
            if world_id is None:
                raise RuntimeError(
                    "CARLA returned a world without a valid episode ID."
                )

            simulation_settings = self.simulation_manager.get_current_settings(world)
            self.simulation_manager.connect(simulation_settings)

            blueprint_library = world.get_blueprint_library()
            self.database.blueprint_lib = blueprint_library
            self.database.pedestrian_blueprint_ids = (
                get_pedestrian_blueprint_ids(blueprint_library)
            )
            with self.database.lock:
                self.database.world = world
                self.database.world_id = world_id
                self.database.world_generation = (
                    int(getattr(self.database, "world_generation", 0)) + 1
                )
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
            sync_report = self._synchronize_initial_world(world)
            self._tm_debug_event("connect", world=world)
            self.connection_queue.put(("connected", sync_report))
            self._queue_map_preview_generation(world)

        except Exception as e:
            self._invalidate_database_world_reference()
            self.data_ready = False
            self.running = False
            self.dashboard_monitor.disconnect(
                self.database,
                "Connection failed",
                e,
            )
            self.connection_queue.put(("connection_error", str(e)))
            return

        runtime_context = RuntimeContext(
            client=self.client,
            database=self.database,
            world=world,
            blueprint_library=blueprint_library,
        )

        while self.running:
            queue_timeout = (
                0.0
                if self.simulation_manager.should_tick()
                else (
                    0.05
                    if (
                        self._manual_control_is_active()
                        or self._basic_agent_control_is_active()
                    )
                    else 0.1
                )
            )
            cmd, data = self._get_next_command(queue_timeout)

            if self._movement_command_is_stale(cmd, data, world):
                continue

            service_handled = self.command_dispatcher.dispatch(
                cmd,
                runtime_context,
                data,
            )
            if service_handled:
                world = runtime_context.world
                blueprint_library = runtime_context.blueprint_library
                cmd = None

            if cmd == "shutdown":
                print("DEBUG: SHUTDOWN COMMAND RECEIVED")
                self.simulation_manager.disconnect()
                self._publish_simulation_control_state(force=True)
                self.running = False
                break

            if cmd == "spawn":
                requested_blueprint_id = str(data["blueprint_id"])
                transform = data["transform"]
                request_id = data.get("request_id")
                blueprint_attributes = data.get(
                    "attributes",
                    self.database.blueprint_new_data,
                )
                requested_physics = data.get(
                    "physics",
                    self.database.ghost_physic,
                )
                self.database.blueprint_new_data = None
                actor = None
                try:
                    actor = self._spawn_actor_checked(
                        world,
                        blueprint_library,
                        requested_blueprint_id,
                        transform,
                        attributes=blueprint_attributes,
                    )
                    actual_physics = None
                    if (
                        requested_physics is not None
                        and actor.type_id.startswith("vehicle.")
                    ):
                        actual_physics = self._apply_vehicle_physics_checked(
                            actor,
                            requested_physics,
                        )
                    with self.database.lock:
                        actor_info = self.database.add_actor(actor, data["name"])
                        if actual_physics is not None:
                            actor_info.physics_control = actual_physics
                        self.database.spawned_actor_id = actor.id
                    self._record_actor_operation_result(
                        "spawn",
                        requested_blueprint_id,
                        actor=actor,
                        request_id=request_id,
                    )
                except Exception as error:
                    if actor is not None:
                        with self.database.lock:
                            actor_is_registered = (
                                actor.id in self.database.actors_db.actors
                            )
                        if (
                            not actor_is_registered
                            and getattr(actor, "is_alive", False)
                        ):
                            actor.destroy()
                    self._record_actor_operation_result(
                        "spawn",
                        requested_blueprint_id,
                        error=error,
                        request_id=request_id,
                    )

            if cmd == "change_world":
                self.world_transition_event.set()
                self._shutdown_vehicle_controls(
                    disable_autopilot=True,
                    clear_paths=False,
                )
                self._shutdown_walker_ai()
                self._invalidate_traffic_manager_reference()
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
                    self._capture_map_infrastructure(new_world)

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
                        self.database.pedestrian_blueprint_ids = (
                            get_pedestrian_blueprint_ids(
                                new_blueprint_library
                            )
                        )
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
                    try:
                        self._perform_world_sync(
                            new_world,
                            reason="map_changed",
                        )
                    except Exception as sync_error:
                        self.dashboard_monitor.record_error(sync_error)
                        self.database.scenario_event_queue.put((
                            "world_sync_error",
                            {
                                "message": str(sync_error),
                                "reason": "map_changed",
                            },
                        ))
                    self.database.simulation_event_queue.put((
                        "simulation_settings_loaded",
                        new_simulation_settings,
                    ))
                    self._publish_simulation_control_state(force=True)
                    self._queue_map_preview_generation(new_world)
                except Exception as e:
                    error_message = str(e)
                    try:
                        (
                            world,
                            blueprint_library,
                            episode_changed,
                        ) = self._recover_world_after_failed_change(world)
                        recovery_state = (
                            "CARLA switched to a different world episode; "
                            "local references were rebuilt."
                            if episode_changed else
                            "The previous world episode is still active and "
                            "was synchronized."
                        )
                        error_message = f"{error_message} {recovery_state}"
                    except Exception as recovery_error:
                        self.dashboard_monitor.record_error(recovery_error)
                        error_message = (
                            f"{error_message} Server state recovery failed: "
                            f"{recovery_error}"
                        )
                    self.database.world_change_error = error_message
                    self.dashboard_monitor.record_error(e)
                    print(f"Error changing map: {error_message}")
                finally:
                    self.client.set_timeout(5.0)
                    self.world_changed_event.set()
                    self.world_transition_event.clear()

            if cmd == "generate_map_preview":
                self._generate_map_preview(world, data)

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

            if cmd in self.SCENARIO_COMMANDS:
                world, blueprint_library = (
                    self._handle_scenario_runtime_command(
                        cmd, data, world, blueprint_library
                    )
                )


            if cmd == "spawn_preview_actor":
                if not self._ghost_preview_request_is_current(
                    data.get("request_id"),
                    enabled=True,
                ):
                    continue
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
                    if not self._ghost_preview_request_is_current(
                        data.get("request_id"),
                        enabled=True,
                    ):
                        actor.destroy()
                        continue
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
                    if self._ghost_preview_request_is_current(
                        data.get("request_id"),
                        enabled=True,
                    ):
                        self.database.ghost_spawned = True
                    else:
                        self.cleanup_preview()

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
                self.cleanup_preview()

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
                        if (
                            self.database.ghost_physic is not None
                            and new_ghost_actor.type_id.startswith("vehicle.")
                        ):
                            new_ghost_actor.apply_physics_control(
                                self.database.ghost_physic
                            )
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
                try:
                    if self.database.ghost_data is None:
                        raise RuntimeError("Preview actor is no longer available.")
                    ghost_actor = self.database.ghost_data
                    if physics_control is not None:
                        ghost_actor.apply_physics_control(physics_control)
                        self.database.ghost_physic = physics_control
                    ghost_type = ghost_actor.type_id
                    self.command_queue.put(("update_preview_actor_model", ghost_type))
                    self.database.physics_applied = True
                    result = {"success": True}
                except Exception as error:
                    self.dashboard_monitor.record_error(error)
                    result = {"success": False, "error": str(error)}
                self.database.actor_event_queue.put((
                    "ghost_configuration_applied",
                    result,
                ))

            if cmd == "get_vehicle_edit_data":
                actor_id = int(data["actor_id"])
                request_id = data.get("request_id")
                try:
                    with self.database.lock:
                        actor = self.database.actors_db.actors.get(actor_id)
                        actor_info = self.database.actors_db.actors_db.get(actor_id)
                    if actor is None or actor_info is None or not actor.is_alive:
                        raise RuntimeError("Vehicle is no longer available.")
                    if not actor.type_id.startswith("vehicle."):
                        raise RuntimeError("Selected actor is not a vehicle.")
                    physics = actor.get_physics_control()
                    attributes = {
                        str(name): str(value)
                        for name, value in dict(actor.attributes or {}).items()
                    }
                    with self.database.lock:
                        actor_info.physics_control = physics
                        actor_info.blueprint_attributes = dict(attributes)
                    self.database.actor_event_queue.put((
                        "vehicle_edit_data",
                        {
                            "success": True,
                            "request_id": request_id,
                            "actor_id": actor_id,
                            "blueprint_id": actor.type_id,
                            "attributes": attributes,
                            "physics": physics,
                        },
                    ))
                except Exception as error:
                    self.database.actor_event_queue.put((
                        "vehicle_edit_data",
                        {
                            "success": False,
                            "request_id": request_id,
                            "actor_id": actor_id,
                            "error": str(error),
                        },
                    ))

            if cmd == "apply_vehicle_physics":
                actor_id = int(data["actor_id"])
                request_id = data.get("request_id")
                try:
                    with self.database.lock:
                        actor = self.database.actors_db.actors.get(actor_id)
                        actor_info = self.database.actors_db.actors_db.get(actor_id)
                    if actor is None or actor_info is None or not actor.is_alive:
                        raise RuntimeError("Vehicle is no longer available.")
                    actual = self._apply_vehicle_physics_checked(
                        actor,
                        data["physics"],
                    )
                    with self.database.lock:
                        actor_info.physics_control = actual
                        actor_info.update_runtime(actor)
                    self.database.actor_event_queue.put((
                        "vehicle_physics_applied",
                        {
                            "success": True,
                            "request_id": request_id,
                            "actor_id": actor_id,
                        },
                    ))
                except Exception as error:
                    self.dashboard_monitor.record_error(error)
                    self.database.actor_event_queue.put((
                        "vehicle_physics_applied",
                        {
                            "success": False,
                            "request_id": request_id,
                            "actor_id": actor_id,
                            "error": str(error),
                        },
                    ))

            if cmd == "change_actor_model":
                if isinstance(data, dict):
                    blueprint_id = data["blueprint_id"]
                    actor_id = data["actor_id"]
                    blueprint_attributes = data.get("attributes")
                    requested_physics = data.get("physics")
                    request_id = data.get("request_id")
                else:
                    blueprint_id, actor_id = data
                    blueprint_attributes = None
                    requested_physics = None
                    request_id = None
                requested_blueprint_id = str(blueprint_id)
                actor_id = int(actor_id)
                old_actor = None
                new_actor = None
                new_sensors = []
                old_actor_removed = False
                warnings = []
                sensor_id_map = {}
                try:
                    self._find_blueprint_checked(
                        blueprint_library,
                        requested_blueprint_id,
                    )
                    with self.database.lock:
                        old_actor = self.database.actors_db.actors.get(
                            actor_id
                        )
                        actor_info = self.database.actors_db.actors_db.get(
                            actor_id
                        )
                    if old_actor is None or actor_info is None:
                        raise RuntimeError(
                            f"Actor {actor_id} is no longer available."
                        )

                    blueprint_attributes = (
                        self.actor_service.replacement_attributes(
                            actor_info,
                            blueprint_attributes,
                        )
                    )

                    transform = old_actor.get_transform()
                    name = actor_info.user_name
                    preserved_metadata = {
                        "scenario_id": actor_info.scenario_id,
                        "ownership": actor_info.ownership,
                        "origin": actor_info.origin,
                        "configuration_status": (
                            actor_info.configuration_status
                        ),
                        "missing_fields": list(actor_info.missing_fields),
                        "warnings": list(actor_info.warnings),
                        "parent_scenario_id": actor_info.parent_scenario_id,
                        "data_sources": dict(actor_info.data_sources),
                    }
                    preserved_physics = (
                        old_actor.get_physics_control()
                        if old_actor.type_id.startswith("vehicle.")
                        else None
                    )
                    with self.database.lock:
                        attached_sensors = [
                            (
                                sensor_id,
                                self.database.sensors_db.sensors.get(sensor_id),
                                sensor_info,
                            )
                            for sensor_id, sensor_info
                            in self.database.sensors_db.sensors_db.items()
                            if sensor_info.parent_id == actor_id
                        ]

                    transform.location.z += 0.5
                    if actor_id == self.movement_preview_actor_id:
                        self.cleanup_movement_preview("actor_replaced")
                    self._destroy_walker_ai(actor_id, remove_state=True)
                    self._release_vehicle_control(
                        actor_id,
                        disable_autopilot=True,
                    )
                    for old_sensor_id, old_sensor, _ in attached_sensors:
                        self.stop_sensor_preview_actor(old_sensor_id)
                        try:
                            if old_sensor is not None and old_sensor.is_alive:
                                old_sensor.destroy()
                        except Exception as sensor_error:
                            warnings.append(
                                f"Old sensor {old_sensor_id} cleanup failed: "
                                f"{sensor_error}"
                            )

                    old_actor.destroy()
                    try:
                        old_actor_alive = bool(old_actor.is_alive)
                    except RuntimeError:
                        old_actor_alive = False
                    if old_actor_alive:
                        raise RuntimeError(
                            f"Failed to destroy actor {actor_id}."
                        )
                    old_actor_removed = True
                    with self.database.lock:
                        self.database.actors_db.remove_actor(actor_id)
                        self.database.vehicle_control_states.pop(actor_id, None)
                        self.database.walker_ai_states.pop(actor_id, None)
                        for old_sensor_id, _, _ in attached_sensors:
                            self.database.sensors_db.remove_sensor(old_sensor_id)

                    new_actor = self._spawn_actor_checked(
                        world,
                        blueprint_library,
                        requested_blueprint_id,
                        transform,
                        try_spawn=False,
                        attributes=blueprint_attributes,
                    )
                    physics = (
                        requested_physics
                        if requested_physics is not None
                        else preserved_physics
                    )
                    actual_physics = None
                    if physics is not None and new_actor.type_id.startswith("vehicle."):
                        try:
                            actual_physics = self._apply_vehicle_physics_checked(
                                new_actor,
                                physics,
                            )
                        except Exception as physics_error:
                            actual_physics = new_actor.get_physics_control()
                            warnings.append(
                                "Physics from the previous vehicle was incompatible "
                                "with the selected model; CARLA defaults were kept: "
                                f"{physics_error}"
                            )

                    for old_sensor_id, old_sensor, sensor_info in attached_sensors:
                        sensor_blueprint = self._find_blueprint_checked(
                            blueprint_library,
                            sensor_info.blueprint_id,
                        )
                        for attribute_name, attribute_value in (
                            sensor_info.blueprint_attributes.items()
                        ):
                            if sensor_blueprint.has_attribute(attribute_name):
                                attribute = sensor_blueprint.get_attribute(attribute_name)
                                if attribute.is_modifiable:
                                    sensor_blueprint.set_attribute(
                                        attribute_name,
                                        str(attribute_value),
                                    )
                        attachment = getattr(
                            carla.AttachmentType,
                            str(sensor_info.attachment_type),
                            carla.AttachmentType.Rigid,
                        )
                        new_sensor = world.spawn_actor(
                            sensor_blueprint,
                            sensor_info.relative_transform,
                            attach_to=new_actor,
                            attachment_type=attachment,
                        )
                        if new_sensor is None:
                            raise RuntimeError(
                                f"Could not restore attached sensor {old_sensor_id}."
                            )
                        new_sensors.append((
                            old_sensor_id,
                            old_sensor,
                            new_sensor,
                            sensor_info,
                        ))

                    with self.database.lock:
                        new_actor_info = self.database.add_actor(
                            new_actor,
                            name,
                            **preserved_metadata,
                        )
                        if actual_physics is not None:
                            new_actor_info.physics_control = actual_physics
                        for old_sensor_id, _, new_sensor, sensor_info in new_sensors:
                            self.database.add_sensor(
                                new_sensor,
                                new_actor.id,
                                sensor_info.user_name,
                                sensor_info.blueprint_id,
                                sensor_info.relative_transform,
                                sensor_info.blueprint_attributes,
                                scenario_id=sensor_info.scenario_id,
                                ownership=sensor_info.ownership,
                                origin=sensor_info.origin,
                                configuration_status=sensor_info.configuration_status,
                                missing_fields=list(sensor_info.missing_fields),
                                warnings=list(sensor_info.warnings),
                                parent_scenario_id=sensor_info.parent_scenario_id,
                                attachment_type=sensor_info.attachment_type,
                                data_sources=dict(sensor_info.data_sources),
                            )
                            sensor_id_map[old_sensor_id] = new_sensor.id
                        self.database.spawned_actor_id = new_actor.id
                    if sensor_id_map:
                        self.database.sensor_event_queue.put((
                            "sensors_replaced",
                            sensor_id_map,
                        ))
                    self._record_actor_operation_result(
                        "change_model",
                        requested_blueprint_id,
                        actor=new_actor,
                        previous_actor_id=actor_id,
                        request_id=request_id,
                        warnings=warnings,
                        sensor_id_map=sensor_id_map,
                    )
                except Exception as error:
                    for _, _, new_sensor, _ in new_sensors:
                        try:
                            if new_sensor.is_alive:
                                new_sensor.destroy()
                        except Exception:
                            pass
                    if new_actor is not None:
                        with self.database.lock:
                            new_actor_is_registered = (
                                new_actor.id
                                in self.database.actors_db.actors
                            )
                        if (
                            not new_actor_is_registered
                            and getattr(new_actor, "is_alive", False)
                        ):
                            new_actor.destroy()
                    if old_actor_removed:
                        error = RuntimeError(
                            "The previous actor was removed, but the new model "
                            f"could not be spawned: {error}"
                        )
                    self._record_actor_operation_result(
                        "change_model",
                        requested_blueprint_id,
                        error=error,
                        previous_actor_id=actor_id,
                        request_id=request_id,
                    )

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
                sensor_id = int(data)
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

            if cmd == "set_walker_control_mode":
                self._set_walker_control_mode(world, data)

            if cmd == "set_walker_manual_control":
                self._set_walker_manual_control(data)

            if cmd == "validate_movement_destination":
                self._validate_movement_destination(world, data)

            if cmd == "confirm_movement_destination":
                self._confirm_movement_destination(world, data)

            if cmd == "clear_movement_destination":
                self._clear_movement_destination(world, data)

            if cmd == "get_movement_destination_map":
                self._get_movement_destination_map(world, data)

            if cmd == "get_movement_actor_location":
                self._get_movement_actor_location(data)

            if cmd == "get_movement_navigation_snapshot":
                self._get_movement_navigation_snapshot(world, data)

            if not self.world_transition_event.is_set():
                self._service_manual_vehicle_control()
                if not self.simulation_manager.synchronous_mode:
                    self._service_basic_agent_control(world)

            world_snapshot = None
            if self.running and cmd != "stop_simulation_control":
                world_snapshot = self._service_simulation(world)
            if self.running and not self.world_transition_event.is_set():
                if (
                    self.simulation_manager.synchronous_mode
                    and world_snapshot is not None
                ):
                    self._service_basic_agent_control(
                        world,
                        frame=getattr(world_snapshot, "frame", None),
                        force=True,
                    )
                self._service_vehicle_navigation(world, world_snapshot)
                self._service_walker_ai(world, world_snapshot)
            if self.running:
                self._service_dashboard(world, world_snapshot=world_snapshot)

        self._tm_debug_event("disconnect_begin", world=world)
        self._shutdown_vehicle_controls(
            disable_autopilot=True,
            clear_paths=False,
        )
        self._shutdown_walker_ai()
        self.cleanup_movement_preview("controller_stopped")
        self.cleanup_sensor_previews()
        self.cleanup_preview()
        self._shutdown_exclusive_traffic_manager(
            "disconnect",
            strict=False,
        )
        self._invalidate_traffic_manager_reference()
        self._invalidate_database_world_reference()
        self.simulation_manager.disconnect()
        self._publish_simulation_control_state(force=True)
        self.data_ready = False
        self.dashboard_monitor.disconnect(self.database)
        self._tm_debug_event("disconnect_complete")
        self.connection_queue.put(("disconnected", None))







    def _get_next_command(self, timeout):
        command_bus = getattr(self, "command_bus", None)
        if command_bus is None:
            command_bus = CommandBus(
                self.command_queue,
                self.priority_command_queue,
            )
        return command_bus.get_next(timeout)

    def _queue_map_preview_generation(self, world):
        try:
            carla_map = world.get_map()
            self.command_queue.put(("generate_map_preview", {
                "world_id": self._world_id(world),
                "map_name": str(carla_map.name),
            }))
        except Exception as error:
            self.database.map_preview_event_queue.put((
                "map_preview_failed",
                {"map_name": "", "message": str(error)},
            ))

    def _generate_map_preview(self, world, data):
        map_name = str(data.get("map_name", ""))
        if self.shutdown_event.is_set():
            return
        if self._world_id(world) != data.get("world_id"):
            return
        try:
            carla_map = world.get_map()
            if not MapPreviewCache.map_names_match(
                map_name, getattr(carla_map, "name", "")
            ):
                return
            path, generated = self.map_preview_generator.generate_if_missing(
                carla_map,
                map_name=map_name,
                cancelled=self.shutdown_event.is_set,
            )
            self.database.map_preview_event_queue.put((
                "map_preview_ready",
                {
                    "map_name": map_name,
                    "path": str(path),
                    "generated": generated,
                },
            ))
        except Exception as error:
            if not self.shutdown_event.is_set():
                self.database.map_preview_event_queue.put((
                    "map_preview_failed",
                    {"map_name": map_name, "message": str(error)},
                ))

    @staticmethod
    def _world_id(world):
        try:
            return int(world.id)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    def _invalidate_database_world_reference(self):
        with self.database.lock:
            self.database.world = None
            self.database.world_id = None
            self.database.world_generation = (
                int(getattr(self.database, "world_generation", 0)) + 1
            )

    def _invalidate_database_episode_references(self):
        """Detach every Python proxy owned by the outgoing CARLA episode."""
        self._invalidate_database_world_reference()
        with self.database.lock:
            actors_db = getattr(self.database, "actors_db", None)
            if actors_db is not None:
                clear = getattr(actors_db, "clear", None)
                if callable(clear):
                    clear()
                else:
                    getattr(actors_db, "actors", {}).clear()
                    getattr(actors_db, "actors_db", {}).clear()
            sensors_db = getattr(self.database, "sensors_db", None)
            if sensors_db is not None:
                clear = getattr(sensors_db, "clear", None)
                if callable(clear):
                    clear()
                else:
                    getattr(sensors_db, "sensors", {}).clear()
                    getattr(sensors_db, "sensors_db", {}).clear()
            getattr(self.database, "vehicle_control_states", {}).clear()
            getattr(self.database, "walker_ai_states", {}).clear()
            if hasattr(self.database, "spawned_actor_id"):
                self.database.spawned_actor_id = None
            if hasattr(self.database, "ghost_camera"):
                self.database.ghost_camera = None
            if hasattr(self.database, "ghost_data"):
                self.database.ghost_data = None
            if hasattr(self.database, "ghost_physic"):
                self.database.ghost_physic = None
            if hasattr(self.database, "ghost_spawned"):
                self.database.ghost_spawned = False
        sensor_queue = getattr(self.database, "sensor_event_queue", None)
        if sensor_queue is not None:
            sensor_queue.put(("sensors_cleared", None))

    def _recover_world_after_failed_change(self, previous_world_id):
        current_world = self.client.get_world()
        current_world_id = self._world_id(current_world)
        if current_world_id is None:
            raise RuntimeError(
                "CARLA returned a world without a valid episode ID."
            )
        blueprint_library = current_world.get_blueprint_library()
        episode_changed = current_world_id != previous_world_id
        if episode_changed:
            self._capture_map_infrastructure(current_world)

        if episode_changed:
            # Preview listeners are stopped before load_world().  Do not touch
            # their old CARLA proxies after the client has adopted a new episode.
            self.sensor_status_errors.clear()
            with self.database.lock:
                self.database.actors_db.clear()
                self.database.sensors_db.clear()
                self.database.vehicle_control_states.clear()
                self.database.walker_ai_states.clear()
                self.database.spawned_actor_id = None
            self.database.sensor_event_queue.put(("sensors_cleared", None))

        settings = self.simulation_manager.get_current_settings(current_world)
        self.simulation_manager.settings_changed(
            settings,
            preserve_activity=False,
        )
        with self.database.lock:
            self.database.world = current_world
            self.database.world_id = current_world_id
            self.database.world_generation = (
                int(getattr(self.database, "world_generation", 0)) + 1
            )
            self.database.blueprint_lib = blueprint_library
            self.database.pedestrian_blueprint_ids = (
                get_pedestrian_blueprint_ids(blueprint_library)
            )
            self.database.world_lib = self.client.get_available_maps()
            self.database.actual_weather_data = parse_weather_to_dict(
                current_world.get_weather()
            )
            self.database.available_spawn_points = (
                current_world.get_map().get_spawn_points()
            )
            self.database.actual_simulation_settings = settings
            self.database.proposed_simulation_settings = settings.copy()

        self.dashboard_monitor.reset_world(
            self.database,
            current_world,
            "World change recovery",
        )
        self._perform_world_sync(
            current_world,
            reason="map_change_recovery",
        )
        self._publish_simulation_control_state(force=True)
        return current_world, blueprint_library, episode_changed

    def _capture_map_infrastructure(self, world):
        world_id = self._world_id(world)
        try:
            map_name = str(world.get_map().name)
        except (AttributeError, RuntimeError, TypeError):
            map_name = None
        actor_ids = set()
        try:
            actors = list(world.get_actors())
        except (AttributeError, RuntimeError, TypeError):
            actors = []
        for actor in actors:
            try:
                type_id = str(actor.type_id)
                role_name = self.world_synchronizer.classifier.role_name(actor)
                if type_id.startswith("static.") and not role_name:
                    actor_ids.add(int(actor.id))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
        self.map_infrastructure_actor_ids = actor_ids
        self.map_infrastructure_world_id = world_id
        self.map_infrastructure_map_name = map_name
        return set(actor_ids)

    def _synchronize_initial_world(self, world):
        """Build the episode baseline before the first Connect/Reconnect sync."""
        world_id = self._world_id(world)
        try:
            map_name = str(world.get_map().name)
        except (AttributeError, RuntimeError, TypeError):
            map_name = None
        if (
            self.map_infrastructure_world_id != world_id
            or self.map_infrastructure_map_name != map_name
        ):
            self._capture_map_infrastructure(world)
        return self._perform_world_sync(
            world,
            reason="initial_connection",
            publish=False,
        )

    def _map_infrastructure_ids(self, world=None):
        if world is not None and (
            getattr(self, "map_infrastructure_world_id", None)
            != self._world_id(world)
        ):
            return set()
        return set(getattr(self, "map_infrastructure_actor_ids", set()))

    def shutdown(self):
        print("Shutting down CARLA...")
        cancelled_plan = None
        operation_lock = getattr(self, "scenario_operation_lock", None)
        if operation_lock is not None:
            with operation_lock:
                active_plan = getattr(self, "active_clear_plan", None)
                self.active_load_plan = None
                if self.scenario_operation_kind in (
                    "load_preflight",
                    "load",
                ):
                    self.scenario_operation_pending = False
                    self.scenario_operation_kind = None
                    self.load_execution_queued = False
                if active_plan is not None:
                    cancelled_plan = active_plan
                    self.active_clear_plan = None
                    self.clear_execution_queued = False
                    self.scenario_operation_pending = False
                    self.scenario_operation_kind = None
        if cancelled_plan is not None:
            self.database.scenario_event_queue.put((
                "world_clear_completed",
                WorldClearReport.cancelled(
                    cancelled_plan.mode,
                    cancelled_plan.planned_count,
                    "Clear World was cancelled during disconnect.",
                ).to_dict(),
            ))
        self.shutdown_event.set()
        self.running = False
        if self.thread is None or not self.thread.is_alive():
            return
        self.command_queue.put(("shutdown", None))

    def cleanup_preview(self, strict=False):
        actor_service = getattr(self, "actor_service", None)
        if actor_service is None:
            actor_service = ActorService(self.database)
        return actor_service.cleanup_preview(strict=strict)

    def _ghost_preview_request_is_current(self, request_id, enabled):
        transition = getattr(self, "world_transition_event", None)
        if enabled and transition is not None and transition.is_set():
            return False
        with self.database.lock:
            return (
                request_id is not None
                and request_id == self.database.ghost_preview_request_id
                and bool(self.database.ghost_preview_requested_enabled)
                == bool(enabled)
            )

    def remove_sensors_for_parent(self, parent_id):
        self._get_sensor_service().remove_for_parent(parent_id)

    def stop_sensor_preview_actor(self, sensor_id):
        self._get_sensor_service().stop_preview_actor(sensor_id)

    def cleanup_sensor_previews(self, strict=False):
        return self._get_sensor_service().cleanup_previews(strict=strict)

    def _get_sensor_service(self):
        sensor_service = getattr(self, "sensor_service", None)
        if sensor_service is None:
            sensor_service = SensorService(
                self.database,
                getattr(self, "dashboard_monitor", None),
                self.sensor_preview_ids,
                self.sensor_status_errors,
            )
            self.sensor_service = sensor_service
        return sensor_service

    def _spectator_runtime_status(self):
        provider = getattr(self, "_spectator_status_provider", None)
        if provider is None:
            return {"state": "Unknown", "camera_id": None}
        try:
            status = provider() or {}
        except Exception:
            return {"state": "Unknown", "camera_id": None}
        if not isinstance(status, dict):
            return {"state": "Unknown", "camera_id": None}
        return {
            "state": status.get("state", "Unknown"),
            "camera_id": status.get("camera_id"),
        }

    def _plan_vehicle_route(self, world, actor, destination_location,
                            target_waypoint_snapshot=None):
        global GlobalRoutePlanner
        if GlobalRoutePlanner is None:
            GlobalRoutePlanner, planner_error = load_global_route_planner()
            if GlobalRoutePlanner is None:
                raise RuntimeError(planner_error)
        carla_map = world.get_map()
        lane_type = getattr(carla.LaneType, "Driving", carla.LaneType.Any)
        start_waypoint = carla_map.get_waypoint(
            actor.get_location(),
            project_to_road=True,
            lane_type=lane_type,
        )
        target_waypoint = resolve_vehicle_waypoint(
            world,
            {
                "x": float(destination_location.x),
                "y": float(destination_location.y),
                "z": float(destination_location.z),
            },
            carla,
            waypoint_selector=target_waypoint_snapshot,
            project_to_road=True,
        )
        if start_waypoint is None or target_waypoint is None:
            raise RuntimeError("No driving lane is available near the vehicle or Destination.")
        if (
            isinstance(target_waypoint_snapshot, dict)
            and not same_waypoint_segment(
                waypoint_snapshot(target_waypoint),
                target_waypoint_snapshot,
            )
        ):
            raise RuntimeError(
                "The selected Destination lane could not be resolved for "
                "route planning."
            )

        planner = GlobalRoutePlanner(
            carla_map,
            self.VEHICLE_NAVIGATION_SAMPLING_RESOLUTION,
        )
        traced_route = planner.trace_route(
            start_waypoint.transform.location,
            target_waypoint.transform.location,
        )
        if not traced_route:
            raise RuntimeError("GlobalRoutePlanner could not find a driveable route.")

        path = []
        route_waypoints = []
        for route_item in traced_route:
            waypoint = route_item[0] if isinstance(route_item, (tuple, list)) else route_item
            road_option = (
                route_item[1]
                if isinstance(route_item, (tuple, list)) and len(route_item) > 1
                else None
            )
            location = waypoint.transform.location
            point = carla.Location(
                x=float(location.x),
                y=float(location.y),
                z=float(location.z),
            )
            if not path or self._vehicle_xy_distance(path[-1], point) > 0.05:
                path.append(point)
                route_waypoints.append(waypoint_snapshot(waypoint, road_option))
        if len(path) < 2:
            raise RuntimeError("The planned route does not contain enough waypoints.")
        if not same_waypoint_segment(
            route_waypoints[-1],
            waypoint_snapshot(target_waypoint),
        ):
            raise RuntimeError(
                "GlobalRoutePlanner ended on a different driving lane than "
                "the selected Destination waypoint."
            )
        return path, target_waypoint, route_waypoints, traced_route

    def _managed_operation_allowed(self, actor_id, sensor=False):
        actor_id = int(actor_id)
        with self.database.lock:
            if sensor:
                allowed = self.database.is_sensor_managed(actor_id)
            else:
                allowed = self.database.is_actor_managed(actor_id)
        if allowed:
            return True
        actor_kind = "sensor" if sensor else "actor"
        self.database.scenario_event_queue.put((
            "world_sync_operation_blocked",
            {
                "message": (
                    f"The selected {actor_kind} is external and cannot be "
                    "modified until ownership is taken."
                ),
                "actor_id": actor_id,
            },
        ))
        return False

    def spawn_actor(self, actor_data):
        if self._scenario_mutation_blocked("spawn actor"):
            return False
        self.command_queue.put(("spawn", actor_data))
        return True

    def sync_world(self, reason="manual"):
        self.command_queue.put(("sync_world", reason))

    def save_scenario(self, name, path, file_format):
        try:
            target = normalize_scenario_path(path, file_format)
        except ScenarioPathError as error:
            self.database.scenario_event_queue.put((
                "scenario_save_error",
                {"message": str(error)},
            ))
            return False
        with self.scenario_operation_lock:
            if self.scenario_operation_pending:
                event_name = (
                    "scenario_operation_blocked"
                    if getattr(self, "scenario_operation_kind", None) == "clear"
                    else "scenario_save_error"
                )
                self.database.scenario_event_queue.put((event_name, {
                    "message": "Another scenario operation is pending."
                }))
                return False
            if self.shutdown_event.is_set() or not self.running:
                self.database.scenario_event_queue.put((
                    "scenario_save_error",
                    {"message": "CARLA is disconnecting."},
                ))
                return False
            self.scenario_operation_pending = True
            self.scenario_operation_kind = "save"
            self.active_load_plan = None
        self.command_queue.put(("save_scenario", {
            "name": str(name),
            "path": str(target),
            "format": str(file_format),
        }))
        return True

    def prepare_clear_world(self, mode):
        try:
            normalized_mode = ClearMode.normalize(mode)
        except ValueError as error:
            self.database.scenario_event_queue.put((
                "world_clear_error",
                {"message": str(error), "phase": "preflight"},
            ))
            return False
        with self.scenario_operation_lock:
            if self.scenario_operation_pending:
                self.database.scenario_event_queue.put((
                    "scenario_operation_blocked",
                    {
                        "message": "Another scenario operation is pending.",
                    },
                ))
                return False
            if self.shutdown_event.is_set() or not self.running:
                self.database.scenario_event_queue.put((
                    "world_clear_error",
                    {
                        "message": "CARLA is disconnecting.",
                        "phase": "preflight",
                    },
                ))
                return False
            self.scenario_operation_pending = True
            self.scenario_operation_kind = "clear"
            self.active_load_plan = None
            self.active_clear_plan = None
            self.clear_execution_queued = False
        self.command_queue.put(("prepare_clear_world", normalized_mode))
        return True

    def execute_clear_world(self, token, confirmed=True):
        with self.scenario_operation_lock:
            plan = self.active_clear_plan
            if (
                not self.scenario_operation_pending
                or plan is None
                or plan.token != str(token)
                or self.clear_execution_queued
            ):
                self.database.scenario_event_queue.put((
                    "world_clear_error",
                    {
                        "message": (
                            "The Clear World plan is no longer valid. Run "
                            "preflight again."
                        ),
                        "phase": "execute",
                    },
                ))
                return False
            self.clear_execution_queued = True
        self.command_queue.put(("execute_clear_world", {
            "token": str(token),
            "confirmed": bool(confirmed),
            "mode": plan.mode,
        }))
        return True

    def change_world(self, new_world):
        with self.scenario_operation_lock:
            if self.scenario_operation_pending:
                self.database.scenario_event_queue.put((
                    "scenario_operation_blocked",
                    {
                        "message": (
                            "A scenario operation is pending; the map was "
                            "not changed."
                        ),
                    },
                ))
                return False
            self.active_load_plan = None
        self.world_transition_event.set()
        self.world_changed_event.clear()
        self.database.world_changed = False
        self.database.world_change_error = None
        self.command_queue.put(("change_world", new_world))
        return True

    def prepare_load_scenario(self, path):
        source_path = str(path or "").strip()
        if not source_path:
            self.database.scenario_event_queue.put((
                "scenario_load_preflight_error",
                {"message": "Scenario path is required.", "path": source_path},
            ))
            return False
        with self.scenario_operation_lock:
            if self.scenario_operation_pending:
                self.database.scenario_event_queue.put((
                    "scenario_operation_blocked",
                    {"message": "Another scenario operation is pending."},
                ))
                return False
            if self.shutdown_event.is_set() or not self.running:
                self.database.scenario_event_queue.put((
                    "scenario_load_preflight_error",
                    {
                        "message": "CARLA is disconnecting.",
                        "path": source_path,
                    },
                ))
                return False
            self.active_load_plan = None
            self.load_execution_queued = False
            self.scenario_operation_pending = True
            self.scenario_operation_kind = "load_preflight"
        self.command_queue.put((
            "prepare_load_scenario",
            {"path": source_path},
        ))
        return True

    def execute_load_scenario(self, plan_token, confirmed=True):
        with self.scenario_operation_lock:
            plan = self.active_load_plan
            if (
                self.scenario_operation_pending
                or plan is None
                or plan.token != str(plan_token)
                or self.load_execution_queued
            ):
                self.database.scenario_event_queue.put((
                    "scenario_load_error",
                    {
                        "message": (
                            "The Scenario Load plan is no longer valid or "
                            "another scenario operation is pending."
                        ),
                        "phase": "preflight",
                    },
                ))
                return False
            if self.shutdown_event.is_set() or not self.running:
                self.database.scenario_event_queue.put((
                    "scenario_load_error",
                    {
                        "message": "CARLA is disconnecting.",
                        "phase": "preflight",
                    },
                ))
                return False
            self.scenario_operation_pending = True
            self.scenario_operation_kind = "load"
            self.load_execution_queued = True
        self.command_queue.put((
            "execute_load_scenario",
            {
                "token": str(plan_token),
                "confirmed": bool(confirmed),
            },
        ))
        return True

    def change_weather(self, new_weather):
        if self._scenario_mutation_blocked("change weather"):
            return False
        self.command_queue.put(("change_weather", new_weather))
        return True

    def get_simulation_settings(self):
        self.command_queue.put(("get_simulation_settings", None))

    def apply_simulation_settings(self, settings):
        if self._scenario_mutation_blocked("apply simulation settings"):
            return False
        self.command_queue.put(("apply_simulation_settings", settings))
        return True

    def set_tick_controller(self, controller):
        if self._scenario_mutation_blocked("change tick controller"):
            return False
        self.command_queue.put(("set_tick_controller", controller))
        return True

    def run_simulation(self):
        if self._scenario_mutation_blocked("run simulation"):
            return False
        self.command_queue.put(("run_simulation", None))
        return True

    def pause_simulation(self):
        self.priority_command_queue.put(("pause_simulation", None))

    def step_simulation(self, count=1):
        if self._scenario_mutation_blocked("step simulation"):
            return False
        self.command_queue.put(("step_simulation", count))
        return True

    def stop_simulation_control(self):
        self.priority_command_queue.put(("stop_simulation_control", None))

    def spawn_preview_actor(self, blueprint_id, transform, image_callback,
                            request_id=None):
        if self._scenario_mutation_blocked("spawn preview actor"):
            return False
        transition = getattr(self, "world_transition_event", None)
        if (
            (transition is not None and transition.is_set())
            or not getattr(self, "running", True)
        ):
            return False
        request_id = str(request_id or "")
        if not request_id:
            return False
        with self.database.lock:
            self.database.ghost_preview_request_id = request_id
            self.database.ghost_preview_requested_enabled = True
        self.command_queue.put(("spawn_preview_actor", {
            "blueprint_id": blueprint_id,
            "transform": transform,
            "image_callback": image_callback,
            "request_id": request_id,
        }))
        return True

    def set_ghost_transform(self, transform):
        self.command_queue.put(("set_ghost_transform", transform))

    def destroy_preview_actor(self, request_id=None):
        request_id = str(request_id or "")
        if not request_id:
            return False
        with self.database.lock:
            self.database.ghost_preview_request_id = request_id
            self.database.ghost_preview_requested_enabled = False
        self.command_queue.put(("destroy_preview_actor", {
            "request_id": request_id,
        }))
        return True

    def update_preview_actor_model(self, blueprint_id):
        self.command_queue.put(
            ("update_preview_actor_model", blueprint_id)
        )

    def remove_actor(self, actor_id):
        if self._scenario_mutation_blocked("remove actor"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        self.command_queue.put(("remove_actor", actor_id))
        return True

    def apply_new_physics_and_blueprint_to_ghost(self, physics_control):
        self.command_queue.put(("apply_new_physics_and_blueprint_to_ghost", physics_control))

    def get_vehicle_edit_data(self, actor_id, request_id=None):
        if not self._managed_operation_allowed(actor_id):
            return False
        self.command_queue.put(("get_vehicle_edit_data", {
            "actor_id": int(actor_id),
            "request_id": request_id,
        }))
        return True

    def apply_vehicle_physics(self, actor_id, physics, request_id=None):
        if self._scenario_mutation_blocked("apply vehicle physics"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        self.command_queue.put(("apply_vehicle_physics", {
            "actor_id": int(actor_id),
            "physics": physics,
            "request_id": request_id,
        }))
        return True

    def change_actor_model(self, blueprint_id, actor_id, attributes=None,
                           physics=None, request_id=None):
        if self._scenario_mutation_blocked("change actor model"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        self.command_queue.put(("change_actor_model", {
            "blueprint_id": blueprint_id,
            "actor_id": int(actor_id),
            "attributes": attributes,
            "physics": physics,
            "request_id": request_id,
        }))
        return True

    def set_actor_transform(self, new_transform, actor_id):
        if self._scenario_mutation_blocked("change actor transform"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        self.command_queue.put(("set_actor_transform", [new_transform, actor_id]))
        return True

    def attach_sensor(self, sensor_data):
        if self._scenario_mutation_blocked("attach sensor"):
            return False
        self.command_queue.put(("attach_sensor", sensor_data))
        return True

    def remove_sensor(self, sensor_id):
        if self._scenario_mutation_blocked("remove sensor"):
            return False
        if not self._managed_operation_allowed(sensor_id, sensor=True):
            return False
        self.command_queue.put(("remove_sensor", sensor_id))
        return True

    def change_sensor_transform(self, sensor_id, transform):
        if self._scenario_mutation_blocked("change sensor transform"):
            return False
        if not self._managed_operation_allowed(sensor_id, sensor=True):
            return False
        self.command_queue.put(("change_sensor_transform", [sensor_id, transform]))
        return True

    def start_sensor_preview(self, sensor_id, image_callback, preview_buffer):
        if self._scenario_mutation_blocked("start sensor preview"):
            preview_buffer.set_error("A scenario operation is pending.")
            return False
        if not self._managed_operation_allowed(sensor_id, sensor=True):
            preview_buffer.set_error(
                "External sensors cannot be previewed until ownership "
                "takeover is implemented."
            )
            return False
        self.command_queue.put(("start_sensor_preview", {
            "sensor_id": sensor_id,
            "image_callback": image_callback,
            "preview_buffer": preview_buffer
        }))
        return True

    def stop_sensor_preview(self, sensor_id):
        self.command_queue.put(("stop_sensor_preview", sensor_id))

    def start_movement_preview(self, session_id, actor_id, actor_type,
                               preset, publisher, control_queue):
        if self._scenario_mutation_blocked("start movement preview"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command("start_movement_preview", {
            "session_id": session_id,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "preset": preset,
            "publisher": publisher,
            "control_queue": control_queue,
        })

    def set_vehicle_control_mode(self, session_id, actor_id, mode,
                                 basic_agent_settings=None):
        if self._scenario_mutation_blocked("change vehicle control"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        payload = {
            "session_id": session_id,
            "actor_id": actor_id,
            "mode": mode,
        }
        if basic_agent_settings is not None:
            payload["basic_agent_settings"] = dict(basic_agent_settings)
        return self._queue_movement_command(
            "set_vehicle_control_mode",
            payload,
        )

    def set_walker_ai_enabled(self, session_id, actor_id, enabled, speed):
        if self._scenario_mutation_blocked("change pedestrian control"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command("set_walker_ai_enabled", {
            "session_id": session_id,
            "actor_id": actor_id,
            "enabled": bool(enabled),
            "speed": speed,
        })

    def set_walker_ai_speed(self, session_id, actor_id, speed):
        if self._scenario_mutation_blocked("change pedestrian speed"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command("set_walker_ai_speed", {
            "session_id": session_id,
            "actor_id": actor_id,
            "speed": speed,
        })

    def set_walker_control_mode(self, session_id, actor_id, mode, speed):
        if self._scenario_mutation_blocked("change pedestrian control mode"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command("set_walker_control_mode", {
            "session_id": session_id,
            "actor_id": int(actor_id),
            "mode": str(mode),
            "speed": speed,
        })

    def set_walker_manual_control(self, session_id, actor_id, direction_x,
                                  direction_y, speed):
        if self._scenario_mutation_blocked("apply pedestrian manual control"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command("set_walker_manual_control", {
            "session_id": session_id,
            "actor_id": int(actor_id),
            "direction_x": direction_x,
            "direction_y": direction_y,
            "speed": speed,
        })

    def validate_movement_destination(self, session_id, actor_id, x, y, z,
                                      waypoint=None):
        if self._scenario_mutation_blocked("validate movement destination"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command("validate_movement_destination", {
            "session_id": session_id,
            "actor_id": int(actor_id),
            "x": x,
            "y": y,
            "z": z,
            "waypoint": waypoint,
        })

    def confirm_movement_destination(self, session_id, actor_id):
        if self._scenario_mutation_blocked("confirm movement destination"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command("confirm_movement_destination", {
            "session_id": session_id,
            "actor_id": int(actor_id),
        })

    def clear_movement_destination(self, session_id, actor_id):
        if self._scenario_mutation_blocked("clear movement destination"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command("clear_movement_destination", {
            "session_id": session_id,
            "actor_id": int(actor_id),
        })

    def get_movement_destination_map(self, session_id, actor_id,
                                     open_map=True):
        if self._scenario_mutation_blocked("prepare destination map"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command("get_movement_destination_map", {
            "session_id": session_id,
            "actor_id": int(actor_id),
            "open_map": bool(open_map),
        })

    def get_movement_actor_location(self, session_id, actor_id):
        if self._scenario_mutation_blocked("read actor destination position"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command("get_movement_actor_location", {
            "session_id": session_id,
            "actor_id": int(actor_id),
        })

    def get_movement_navigation_snapshot(self, session_id, actor_id):
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command(
            "get_movement_navigation_snapshot",
            {
                "session_id": session_id,
                "actor_id": int(actor_id),
            },
        )

    def take_actor_ownership(self, runtime_id):
        try:
            runtime_id = int(runtime_id)
        except (TypeError, ValueError):
            return False
        expected = self.actor_adopter.expected_identity(
            self.database,
            runtime_id,
        )
        if expected is None:
            self.database.scenario_event_queue.put((
                "actor_ownership_result",
                ActorAdoptionReport(
                    status=AdoptionStatus.ACTOR_MISSING,
                    runtime_id=runtime_id,
                    message="The actor is missing from synchronized data.",
                ).to_dict(),
            ))
            return False
        with self.scenario_operation_lock:
            if (
                self.scenario_operation_pending
                or self.shutdown_event.is_set()
                or not self.running
            ):
                self.database.scenario_event_queue.put((
                    "actor_ownership_result",
                    ActorAdoptionReport(
                        status=AdoptionStatus.OPERATION_CONFLICT,
                        runtime_id=runtime_id,
                        world_id=expected.get("world_id"),
                        message=(
                            "Take Ownership is unavailable while another "
                            "scenario operation or disconnect is pending."
                        ),
                    ).to_dict(),
                ))
                return False
            self.scenario_operation_pending = True
            self.scenario_operation_kind = "adoption"
        self.command_queue.put(("take_actor_ownership", {
            "runtime_id": runtime_id,
            "expected_identity": expected,
        }))
        return True

    def _scenario_mutation_blocked(self, operation):
        with self.scenario_operation_lock:
            blocked = self.scenario_operation_pending
        if blocked:
            self.database.scenario_event_queue.put((
                "scenario_operation_blocked",
                {
                    "message": (
                        f"Cannot {operation} while a scenario operation is "
                        "pending."
                    ),
                },
            ))
        return blocked

    def set_movement_preview_transform(self, session_id, preset):
        return self._queue_movement_command("set_movement_preview_transform", {
            "session_id": session_id,
            "preset": preset,
        })

    def stop_movement_preview(self, session_id, reason="preview_stopped"):
        world_id, world_generation = self._movement_world_token()
        self.command_queue.put(("stop_movement_preview", {
            "session_id": session_id,
            "world_id": world_id,
            "world_generation": world_generation,
            "reason": reason,
        }))
        return True

