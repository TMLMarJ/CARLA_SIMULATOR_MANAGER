import carla
import math
import threading
import queue
import time
from carla_pythonapi import load_global_route_planner

GlobalRoutePlanner = None
from utilities.functions import *
from simulation_dashboard import SimulationDashboardMonitor
from simulation_manager import SimulationManager
from scenario_management.scenario_manager import ScenarioManager
from scenario_management.actor_adoption import (
    ActorAdopter,
    ActorAdoptionReport,
    AdoptionStatus,
)
from scenario_management.path_utils import (
    ScenarioPathError,
    normalize_scenario_path,
)
from scenario_management.models import ActorCategory
from scenario_management.scenario_restorer import ScenarioLoadReport
from scenario_management.world_synchronizer import WorldSynchronizer
from scenario_management.converters import WEATHER_FIELDS
from scenario_management.world_cleaner import (
    ClearMode,
    StaleClearPlanError,
    WorldCleaner,
    WorldClearReport,
)
from movement_preview import camera_transform_for_actor
from movement_destination import (
    DESTINATION_ERROR,
    MAP_WAYPOINT_DISTANCE_METERS,
    confirm_destination,
    destination_result,
    empty_destination,
    point_from_location,
    point_from_values,
    validate_pedestrian_destination,
    validate_vehicle_destination,
)
from vehicle_control import (
    AUTOPILOT,
    AUTOPILOT_DESTINATION,
    AUTOPILOT_FREE_ROAM,
    MANUAL_CONTROL,
    NO_CONTROL,
    VEHICLE_CONTROL_MODES,
    VEHICLE_STATUS_ARRIVED,
    VEHICLE_STATUS_CANCELLED,
    VEHICLE_STATUS_FAILED,
    VEHICLE_STATUS_IDLE,
    VEHICLE_STATUS_NAVIGATING,
    VEHICLE_STATUS_STUCK,
    normalize_vehicle_control_mode,
)
from walker_control import (
    MAX_WALKER_SPEED,
    MIN_WALKER_SPEED,
    WALKER_AI_ERROR,
    WALKER_AI_RUNNING,
    WALKER_AI_STOPPED,
    WALKER_CONTROL_DESTINATION,
    WALKER_CONTROL_MANUAL,
    WALKER_CONTROL_MODES,
    WALKER_CONTROL_RANDOM,
    WALKER_STATUS_ARRIVED,
    WALKER_STATUS_CANCELLED,
    WALKER_STATUS_FAILED,
    WALKER_STATUS_IDLE,
    WALKER_STATUS_MOVING,
    WALKER_STATUS_STUCK,
)

class CarlaController:
    TICK_TIMEOUT_SECONDS = 5.0
    TRAFFIC_MANAGER_PORT = 8000
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
    })

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
        self.world_transition_event = threading.Event()
        self.host = host
        self.port = port
        self.shutdown_request = False
        self.sensor_preview_ids = set()
        self.sensor_status_errors = {}
        self.movement_preview_camera = None
        self.movement_preview_publisher = None
        self.movement_preview_session_id = None
        self.movement_preview_actor_id = None
        self.movement_preview_world_id = None
        self.movement_control_queue = None
        self.manual_control_command = None
        self.manual_control_received_at = None
        self.manual_control_next_apply = 0.0
        self.manual_control_input_fresh = False
        self.traffic_manager = None
        self.vehicle_navigation_runtime = {}
        self.walker_ai_controllers = {}
        self.walker_ai_runtime = {}
        self.map_infrastructure_actor_ids = set()
        self.map_infrastructure_world_id = None
        self.simulation_manager = SimulationManager()
        self.dashboard_monitor = SimulationDashboardMonitor()
        self.world_synchronizer = WorldSynchronizer()
        self.actor_adopter = ActorAdopter()
        self.world_cleaner = WorldCleaner()
        self.scenario_manager = ScenarioManager(self.world_cleaner)
        self.scenario_operation_lock = threading.Lock()
        self.scenario_operation_pending = False
        self.scenario_operation_kind = None
        self.active_clear_plan = None
        self.active_load_plan = None
        self.load_execution_queued = False
        self.clear_execution_queued = False
        self._next_snapshot_check = 0.0

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    @staticmethod
    def _find_blueprint_checked(blueprint_library, blueprint_id):
        requested_id = str(blueprint_id)
        try:
            blueprint = blueprint_library.find(requested_id)
        except Exception as error:
            raise RuntimeError(
                f"Blueprint '{requested_id}' is unavailable: {error}"
            ) from error

        resolved_id = str(getattr(blueprint, "id", ""))
        if resolved_id != requested_id:
            raise RuntimeError(
                "Blueprint lookup mismatch: requested "
                f"'{requested_id}', resolved '{resolved_id}'."
            )
        return blueprint

    @classmethod
    def _spawn_actor_checked(cls, world, blueprint_library, blueprint_id,
                             transform, try_spawn=True, attributes=None):
        requested_id = str(blueprint_id)
        blueprint = cls._find_blueprint_checked(
            blueprint_library,
            requested_id,
        )

        for attribute_name, attribute_value in (attributes or {}).items():
            if blueprint.has_attribute(attribute_name):
                blueprint.set_attribute(
                    attribute_name,
                    str(attribute_value),
                )

        spawn = world.try_spawn_actor if try_spawn else world.spawn_actor
        actor = spawn(blueprint, transform)
        if actor is None:
            raise RuntimeError(
                f"CARLA could not spawn blueprint '{requested_id}'."
            )

        actual_id = str(getattr(actor, "type_id", ""))
        if actual_id != requested_id:
            try:
                if getattr(actor, "is_alive", False):
                    actor.destroy()
            finally:
                raise RuntimeError(
                    "Spawned actor blueprint mismatch: requested "
                    f"'{requested_id}', created '{actual_id}'."
                )
        return actor

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
        try:
            return float(value)
        except (TypeError, ValueError):
            return value

    @classmethod
    def _physics_snapshot(cls, physics):
        scalar_fields = (
            "max_rpm", "moi", "damping_rate_full_throttle",
            "damping_rate_zero_throttle_clutch_engaged",
            "damping_rate_zero_throttle_clutch_disengaged",
            "use_gear_autobox", "gear_switch_time", "clutch_strength",
            "final_ratio", "mass", "drag_coefficient",
            "use_sweep_wheel_collision",
        )
        snapshot = {
            name: cls._physics_value(getattr(physics, name, None))
            for name in scalar_fields
        }
        center = getattr(physics, "center_of_mass", None)
        snapshot["center_of_mass"] = tuple(
            cls._physics_value(getattr(center, axis, None))
            for axis in ("x", "y", "z")
        )
        for name in ("torque_curve", "steering_curve"):
            snapshot[name] = [
                (cls._physics_value(point.x), cls._physics_value(point.y))
                for point in getattr(physics, name, [])
            ]
        snapshot["forward_gears"] = [
            tuple(cls._physics_value(getattr(gear, name, None)) for name in (
                "ratio", "down_ratio", "up_ratio"
            ))
            for gear in getattr(physics, "forward_gears", [])
        ]
        snapshot["wheels"] = [
            tuple(cls._physics_value(getattr(wheel, name, None)) for name in (
                "tire_friction", "damping_rate", "max_steer_angle",
                "radius", "max_brake_torque", "max_handbrake_torque",
            ))
            for wheel in getattr(physics, "wheels", [])
        ]
        return snapshot

    @classmethod
    def _physics_mismatches(cls, requested, actual, tolerance=1e-3):
        requested_snapshot = cls._physics_snapshot(requested)
        actual_snapshot = cls._physics_snapshot(actual)

        def equal(left, right):
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                return abs(float(left) - float(right)) <= tolerance
            if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
                return len(left) == len(right) and all(
                    equal(a, b) for a, b in zip(left, right)
                )
            return left == right

        return [
            name for name, value in requested_snapshot.items()
            if not equal(value, actual_snapshot.get(name))
        ]

    @classmethod
    def _apply_vehicle_physics_checked(cls, actor, physics):
        actor.apply_physics_control(physics)
        actual = actor.get_physics_control()
        mismatches = cls._physics_mismatches(physics, actual)
        if mismatches:
            raise RuntimeError(
                "CARLA returned different physics values for: "
                + ", ".join(mismatches)
            )
        return actual

    def run(self):
        try:
            self._run_controller()
        except Exception as error:
            self._shutdown_vehicle_controls(disable_autopilot=True)
            self._shutdown_walker_ai()
            self.cleanup_movement_preview("controller_error")
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
            self.client = carla.Client(self.host, self.port)
            self.client.set_timeout(5.0)
            world = self.client.get_world()

            simulation_settings = self.simulation_manager.get_current_settings(world)
            self.simulation_manager.connect(simulation_settings)

            blueprint_library = world.get_blueprint_library()
            self.database.blueprint_lib = blueprint_library
            self.database.pedestrian_blueprint_ids = (
                get_pedestrian_blueprint_ids(blueprint_library)
            )
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
            sync_report = self._perform_world_sync(
                world,
                reason="initial_connection",
                publish=False,
            )
            self.connection_queue.put(("connected", sync_report))

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

            if self._movement_command_is_stale(cmd, data, world):
                continue

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

            if cmd == "sync_world":
                try:
                    self._perform_world_sync(
                        world,
                        reason=(data or "manual"),
                    )
                except Exception as error:
                    self.dashboard_monitor.record_error(error)
                    self.database.scenario_event_queue.put((
                        "world_sync_error",
                        {"message": str(error), "reason": data or "manual"},
                    ))

            if cmd == "take_actor_ownership":
                try:
                    report = self.actor_adopter.adopt(
                        world,
                        self.database,
                        data["expected_identity"],
                        self._collect_internal_actor_ids(),
                    )
                    if report.status in (
                        AdoptionStatus.ACTOR_MISSING,
                        AdoptionStatus.STALE_IDENTITY,
                    ):
                        try:
                            self._perform_world_sync(
                                world,
                                reason="ownership_adoption_stale",
                                publish=False,
                            )
                        except Exception as sync_error:
                            report.warnings.append(
                                f"World refresh failed: {sync_error}"
                            )
                    elif report.success:
                        try:
                            sync_report = self._perform_world_sync(
                                world,
                                reason="ownership_adoption",
                                publish=False,
                            )
                            report.warnings.extend(
                                error["message"]
                                for error in sync_report.get("errors", ())
                            )
                        except Exception as sync_error:
                            report.warnings.append(
                                f"World refresh failed: {sync_error}"
                            )
                except Exception as error:
                    self.dashboard_monitor.record_error(error)
                    report = ActorAdoptionReport(
                        status=AdoptionStatus.ERROR,
                        runtime_id=int(data["runtime_id"]),
                        message=str(error),
                    )
                finally:
                    with self.scenario_operation_lock:
                        if self.scenario_operation_kind == "adoption":
                            self.scenario_operation_pending = False
                            self.scenario_operation_kind = None
                self.database.scenario_event_queue.put((
                    "actor_ownership_result",
                    report.to_dict(),
                ))

            if cmd == "prepare_load_scenario":
                try:
                    if self.shutdown_event.is_set():
                        raise RuntimeError(
                            "Load preflight cancelled during disconnect."
                        )
                    server_version = None
                    try:
                        server_version = self.client.get_server_version()
                    except Exception:
                        pass
                    available_maps = None
                    try:
                        available_maps = self.client.get_available_maps()
                    except Exception:
                        pass
                    plan = self.scenario_manager.prepare_load(
                        data["path"],
                        world,
                        available_maps,
                        blueprint_library,
                        server_version=server_version,
                    )
                    with self.scenario_operation_lock:
                        self.active_load_plan = plan
                    self.database.scenario_event_queue.put((
                        "scenario_load_preflight_completed",
                        plan.to_dict(),
                    ))
                except Exception as error:
                    self.dashboard_monitor.record_error(error)
                    with self.scenario_operation_lock:
                        self.active_load_plan = None
                    self.database.scenario_event_queue.put((
                        "scenario_load_preflight_error",
                        {
                            "message": str(error),
                            "path": data.get("path"),
                        },
                    ))
                finally:
                    with self.scenario_operation_lock:
                        self.scenario_operation_pending = False
                        self.scenario_operation_kind = None

            if cmd == "execute_load_scenario":
                plan = None
                try:
                    with self.scenario_operation_lock:
                        plan = self.active_load_plan
                    if plan is None or plan.token != data.get("token"):
                        raise RuntimeError(
                            "The Scenario Load plan is no longer valid. Run "
                            "preflight again."
                        )
                    available_maps = None
                    server_version = None
                    try:
                        available_maps = self.client.get_available_maps()
                    except Exception:
                        pass
                    try:
                        server_version = self.client.get_server_version()
                    except Exception:
                        pass
                    result = self.scenario_manager.execute_load(
                        plan,
                        bool(data.get("confirmed", False)),
                        world,
                        self.database,
                        blueprint_library,
                        available_maps,
                        server_version,
                        self._collect_internal_actor_ids,
                        self._prepare_load_clear_lifecycle,
                        self._replace_world_for_load,
                        self._apply_scenario_world_settings,
                        self._restore_scenario_control,
                        lambda active_world: self._perform_world_sync(
                            active_world,
                            reason="after_load_scenario",
                            publish=False,
                        ),
                    )
                    world = result.world
                    blueprint_library = result.blueprint_library
                    self.database.scenario_event_queue.put((
                        "scenario_load_completed",
                        result.report.to_dict(),
                    ))
                except Exception as error:
                    self.dashboard_monitor.record_error(error)
                    failed = ScenarioLoadReport(
                        name=(
                            str(plan.metadata.get("name", ""))
                            if plan is not None else ""
                        ),
                        source_path=(plan.source_path if plan is not None else ""),
                        source_map=(plan.current_map if plan is not None else ""),
                        target_map=(plan.target_map if plan is not None else ""),
                        map_changed=False,
                        planned=(plan.actor_count if plan is not None else 0),
                    )
                    failed.add_error("preflight", error)
                    self.database.scenario_event_queue.put((
                        "scenario_load_completed",
                        failed.to_dict(),
                    ))
                finally:
                    self._finish_load_operation()

            if cmd == "save_scenario":
                try:
                    if self.shutdown_event.is_set():
                        raise RuntimeError(
                            "Scenario save cancelled during disconnect."
                        )
                    result = self.scenario_manager.save(
                        world,
                        self.database,
                        self._collect_internal_actor_ids(),
                        data["name"],
                        data["path"],
                        data["format"],
                        synchronize=self._perform_world_sync,
                        cancel_check=self.shutdown_event.is_set,
                    )
                    self.database.scenario_event_queue.put((
                        "scenario_save_completed",
                        result,
                    ))
                except Exception as error:
                    self.dashboard_monitor.record_error(error)
                    self.database.scenario_event_queue.put((
                        "scenario_save_error",
                        {
                            "message": str(error),
                            "path": data.get("path"),
                        },
                    ))
                finally:
                    with self.scenario_operation_lock:
                        self.scenario_operation_pending = False
                        self.scenario_operation_kind = None

            if cmd == "prepare_clear_world":
                try:
                    if self.shutdown_event.is_set():
                        raise RuntimeError(
                            "Clear World preflight cancelled during disconnect."
                        )
                    plan = self.world_cleaner.prepare_plan(
                        world,
                        self.database,
                        data,
                        self._collect_internal_actor_ids(),
                    )
                    with self.scenario_operation_lock:
                        self.active_clear_plan = (
                            plan if plan.planned_count else None
                        )
                        self.clear_execution_queued = False
                        if not plan.planned_count:
                            self.scenario_operation_pending = False
                            self.scenario_operation_kind = None
                    self.database.scenario_event_queue.put((
                        "world_clear_preflight_ready",
                        plan.to_dict(),
                    ))
                except Exception as error:
                    self.dashboard_monitor.record_error(error)
                    self._finish_clear_operation()
                    self.database.scenario_event_queue.put((
                        "world_clear_error",
                        {"message": str(error), "phase": "preflight"},
                    ))

            if cmd == "execute_clear_world":
                plan = None
                try:
                    with self.scenario_operation_lock:
                        plan = self.active_clear_plan
                    if plan is None or plan.token != data.get("token"):
                        raise StaleClearPlanError(
                            "The Clear World plan is no longer valid. Run "
                            "preflight again."
                        )
                    if not data.get("confirmed", False):
                        report = WorldClearReport.cancelled(
                            plan.mode,
                            plan.planned_count,
                            "Clear World was cancelled by the user.",
                        )
                    else:
                        self.world_cleaner.validate_plan(
                            world,
                            plan,
                            self._collect_internal_actor_ids(),
                        )
                        self._prepare_clear_lifecycle(plan)
                        report = self.world_cleaner.execute_plan(
                            world,
                            plan,
                            self._collect_internal_actor_ids(),
                        )
                        try:
                            report.sync_report = self._perform_world_sync(
                                world,
                                reason="after_clear_world",
                                publish=False,
                            )
                        except Exception as sync_error:
                            report.warnings.append(
                                f"Final world synchronization failed: "
                                f"{sync_error}"
                            )
                            report.errors.append({
                                "runtime_id": None,
                                "message": str(sync_error),
                            })
                            if report.status == "success":
                                report.status = "partial_success"
                        self.database.sensor_event_queue.put((
                            "sensors_cleared",
                            None,
                        ))
                    self.database.scenario_event_queue.put((
                        "world_clear_completed",
                        report.to_dict(),
                    ))
                except Exception as error:
                    self.dashboard_monitor.record_error(error)
                    sync_report = {}
                    try:
                        sync_report = self._perform_world_sync(
                            world,
                            reason="clear_world_aborted",
                            publish=False,
                        )
                    except Exception:
                        pass
                    failed_report = WorldClearReport(
                        mode=(plan.mode if plan is not None else data.get("mode", "unknown")),
                        planned_count=(
                            plan.planned_count if plan is not None else 0
                        ),
                        failed=(
                            plan.planned_count if plan is not None else 0
                        ),
                        errors=[{
                            "runtime_id": None,
                            "message": str(error),
                        }],
                        sync_report=sync_report,
                        status="failed",
                    )
                    self.database.scenario_event_queue.put((
                        "world_clear_completed",
                        failed_report.to_dict(),
                    ))
                finally:
                    self._finish_clear_operation()

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

            if not self.world_transition_event.is_set():
                self._service_manual_vehicle_control()

            world_snapshot = None
            if self.running and cmd != "stop_simulation_control":
                world_snapshot = self._service_simulation(world)
            if self.running and not self.world_transition_event.is_set():
                self._service_vehicle_navigation(world, world_snapshot)
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

    @staticmethod
    def _world_id(world):
        try:
            return int(world.id)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    def _movement_command_is_stale(self, command, data, world):
        if command not in self.MOVEMENT_WORLD_COMMANDS:
            return False
        transition = getattr(self, "world_transition_event", None)
        if transition is not None and transition.is_set():
            return True
        if not isinstance(data, dict):
            return True
        command_world_id = data.get("world_id")
        current_world_id = self._world_id(world)
        if command_world_id is None or current_world_id is None:
            return False
        try:
            return int(command_world_id) != current_world_id
        except (TypeError, ValueError):
            return True

    def _movement_world_id(self):
        with self.database.lock:
            world = getattr(self.database, "world", None)
        return self._world_id(world)

    def _queue_movement_command(self, command, data):
        transition = getattr(self, "world_transition_event", None)
        if transition is not None and transition.is_set():
            return False
        world_id = self._movement_world_id()
        if world_id is None:
            return False
        payload = dict(data)
        payload["world_id"] = world_id
        if transition is not None and transition.is_set():
            return False
        self.command_queue.put((command, payload))
        return True

    def _recover_world_after_failed_change(self, previous_world):
        current_world = self.client.get_world()
        current_world_id = self._world_id(current_world)
        if current_world_id is None:
            raise RuntimeError(
                "CARLA returned a world without a valid episode ID."
            )
        previous_world_id = self._world_id(previous_world)
        blueprint_library = current_world.get_blueprint_library()
        episode_changed = current_world_id != previous_world_id
        if episode_changed:
            self._capture_map_infrastructure(current_world)

        if episode_changed:
            self.cleanup_sensor_previews()
            self.cleanup_preview()
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
        self._invalidate_load_plan_if_episode_changed(world)
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

        internal_actor_ids = self._collect_internal_actor_ids()
        streaming_sensor_ids = set(self.sensor_preview_ids)

        ghost_camera_id = self._actor_id(self.database.ghost_camera)
        if ghost_camera_id is not None:
            streaming_sensor_ids.add(ghost_camera_id)

        spectator_camera_id = getattr(
            self.controller, "spectator_camera_id", None
        )
        if spectator_camera_id is not None:
            streaming_sensor_ids.add(spectator_camera_id)

        movement_camera_id = self._actor_id(self.movement_preview_camera)
        if movement_camera_id is not None:
            streaming_sensor_ids.add(movement_camera_id)

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

    def _capture_map_infrastructure(self, world):
        world_id = self._world_id(world)
        actor_ids = set()
        try:
            actors = list(world.get_actors())
        except (AttributeError, RuntimeError, TypeError):
            actors = []
        for actor in actors:
            try:
                if str(actor.type_id).startswith("static."):
                    actor_ids.add(int(actor.id))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
        self.map_infrastructure_actor_ids = actor_ids
        self.map_infrastructure_world_id = world_id
        return set(actor_ids)

    def _map_infrastructure_ids(self, world=None):
        if world is not None and (
            getattr(self, "map_infrastructure_world_id", None)
            != self._world_id(world)
        ):
            return set()
        return set(getattr(self, "map_infrastructure_actor_ids", set()))

    def _collect_internal_actor_ids(self, include_map_infrastructure=True):
        internal_actor_ids = set()
        for actor in (
            self.database.ghost_data,
            self.database.ghost_camera,
            self.movement_preview_camera,
        ):
            actor_id = self._actor_id(actor)
            if actor_id is not None:
                internal_actor_ids.add(actor_id)

        spectator_camera_id = getattr(
            self.controller,
            "spectator_camera_id",
            None,
        )
        if spectator_camera_id is not None:
            try:
                internal_actor_ids.add(int(spectator_camera_id))
            except (TypeError, ValueError):
                pass

        for walker_controller in self.walker_ai_controllers.values():
            controller_id = self._actor_id(walker_controller)
            if controller_id is not None:
                internal_actor_ids.add(controller_id)
        if include_map_infrastructure:
            internal_actor_ids.update(self._map_infrastructure_ids())
        return internal_actor_ids

    def _perform_world_sync(self, world, reason, publish=True):
        report = self.world_synchronizer.synchronize(
            world,
            self.database,
            self._collect_internal_actor_ids(
                include_map_infrastructure=False,
            ),
            reason=reason,
            infrastructure_actor_ids=self._map_infrastructure_ids(world),
        )
        if self.movement_preview_actor_id in report.removed_actor_ids:
            self.cleanup_movement_preview("actor_removed_externally")
        for sensor_id in report.removed_sensor_ids:
            self.stop_sensor_preview_actor(sensor_id)
        self._service_dashboard(world, force=True)
        report_data = report.to_dict()
        if publish:
            self.database.scenario_event_queue.put((
                "world_sync_completed",
                report_data,
            ))
        return report_data

    def _replace_world_for_load(self, target_map):
        self.database.scenario_event_queue.put((
            "scenario_load_destructive_started",
            {"target_map": str(target_map), "map_change": True},
        ))
        self._shutdown_vehicle_controls(disable_autopilot=True)
        self._shutdown_walker_ai()
        self.cleanup_movement_preview("scenario_load")
        self.cleanup_sensor_previews()
        self.cleanup_preview()
        self.simulation_manager.stop_local_ticks()
        self._publish_simulation_control_state(force=True)
        map_name = self.scenario_manager.loader.map_key(target_map)
        self.client.set_timeout(30.0)
        new_world = None
        new_library = None
        try:
            new_world = self.client.load_world(map_name)
            if new_world is None or not self.scenario_manager.loader.maps_equal(
                target_map,
                new_world.get_map().name,
            ):
                raise RuntimeError(
                    f"CARLA did not load target map '{target_map}'."
                )
            new_library = new_world.get_blueprint_library()
            self._capture_map_infrastructure(new_world)
            settings = self.simulation_manager.get_current_settings(new_world)
            self.simulation_manager.settings_changed(
                settings,
                preserve_activity=False,
            )
            weather_data = parse_weather_to_dict(new_world.get_weather())
            available_maps = self.client.get_available_maps()
            try:
                server_version = self.client.get_server_version()
            except Exception:
                server_version = None
            with self.database.lock:
                self.database.actors_db.clear()
                self.database.sensors_db.clear()
                self.database.vehicle_control_states.clear()
                self.database.walker_ai_states.clear()
                self.database.spawned_actor_id = None
                self.database.world = new_world
                self.database.blueprint_lib = new_library
                self.database.pedestrian_blueprint_ids = (
                    get_pedestrian_blueprint_ids(new_library)
                )
                self.database.world_lib = available_maps
                self.database.actual_weather_data = weather_data
                self.database.available_spawn_points = (
                    new_world.get_map().get_spawn_points()
                )
                self.database.actual_simulation_settings = settings
                self.database.proposed_simulation_settings = settings.copy()
            self.sensor_status_errors.clear()
            self.database.sensor_event_queue.put(("sensors_cleared", None))
            self.dashboard_monitor.reset_world(
                self.database,
                new_world,
                "Scenario Load map change",
            )
            return (
                new_world,
                new_library,
                available_maps,
                server_version,
            )
        except Exception as error:
            if new_world is not None:
                error.changed_world = new_world
                error.blueprint_library = new_library
            raise
        finally:
            self.client.set_timeout(5.0)

    def _apply_scenario_world_settings(self, world, world_data):
        self.simulation_manager.stop_local_ticks()
        confirmed = self.simulation_manager.apply_settings(
            world,
            dict(world_data["simulation"]),
        )
        self.simulation_manager.settings_changed(
            confirmed,
            preserve_activity=False,
        )
        weather = world.get_weather()
        for field in WEATHER_FIELDS:
            setattr(weather, field, float(world_data["weather"][field]))
        world.set_weather(weather)
        self._update_traffic_manager_sync(world)
        with self.database.lock:
            self.database.actual_simulation_settings = confirmed
            self.database.proposed_simulation_settings = confirmed.copy()
            self.database.actual_weather_data = parse_weather_to_dict(weather)
        self.database.simulation_event_queue.put((
            "simulation_settings_loaded",
            confirmed,
        ))
        self._publish_simulation_control_state(force=True)

    def _restore_scenario_control(self, world, actor, data, parent):
        warnings = []
        category = data["category"]
        control = data.get("control") or {}
        if category == ActorCategory.VEHICLE:
            mode = control.get("mode")
            actor_id = int(actor.id)
            if mode == AUTOPILOT and control.get("autopilot_enabled") is True:
                port = control.get("traffic_manager_port")
                if port != self.TRAFFIC_MANAGER_PORT:
                    warnings.append(
                        "Autopilot was not enabled because its Traffic "
                        "Manager port is unsupported by the current controller."
                    )
                else:
                    self._ensure_traffic_manager(world)
                    actor.set_autopilot(True, port)
                    with self.database.lock:
                        self.database.vehicle_control_states[actor_id].update({
                            "mode": AUTOPILOT_FREE_ROAM,
                            "control_mode": AUTOPILOT_FREE_ROAM,
                            "status": f"Autopilot Free Roam active (port {port}).",
                            "navigation_status": VEHICLE_STATUS_IDLE,
                            "autopilot_enabled": True,
                            "autopilot_state_source": "file",
                            "traffic_manager_port": port,
                        })
            elif mode == MANUAL_CONTROL:
                with self.database.lock:
                    self.database.vehicle_control_states[actor_id].update({
                        "mode": MANUAL_CONTROL,
                        "control_mode": MANUAL_CONTROL,
                        "status": (
                            "Manual Control restored; open Movement Control "
                            "to provide input."
                        ),
                        "autopilot_enabled": False,
                        "autopilot_state_source": "file",
                        "traffic_manager_port": None,
                        "input_fresh": False,
                    })
            elif mode not in (NO_CONTROL, None):
                warnings.append(
                    f"Unknown vehicle control mode '{mode}' was not restored."
                )
        elif category == ActorCategory.PEDESTRIAN:
            actor_id = int(actor.id)
            with self.database.lock:
                state = self.database.walker_ai_states[actor_id]
                speed = control.get("speed")
                if isinstance(speed, (int, float)) and not isinstance(speed, bool):
                    state["speed"] = float(speed)
                if isinstance(control.get("random_navigation"), bool):
                    state["random_navigation"] = control["random_navigation"]
                state["active"] = False
                state["mode"] = WALKER_AI_STOPPED
                state["status"] = "Walker control restored in stopped state."
            if control.get("active") is True:
                warnings.append(
                    "Pedestrian AI was not started because no explicit target "
                    "is stored on the pedestrian record."
                )
        elif category == ActorCategory.WALKER_AI_CONTROLLER:
            if parent is None:
                raise RuntimeError("Walker AI controller parent is unavailable.")
            target = control["target"]
            speed = float(control["max_speed"])
            try:
                actor.start()
                actor.go_to_location(carla.Location(
                    x=float(target["x"]),
                    y=float(target["y"]),
                    z=float(target["z"]),
                ))
                actor.set_max_speed(speed)
            except Exception:
                try:
                    actor.stop()
                except Exception:
                    pass
                raise
            self.walker_ai_controllers[int(parent.id)] = actor
            with self.database.lock:
                state = self.database.walker_ai_states[int(parent.id)]
                state.update({
                    "mode": WALKER_AI_RUNNING,
                    "active": True,
                    "speed": speed,
                    "controller_id": int(actor.id),
                    "target": dict(target),
                    "random_navigation": False,
                    "status": "Walker AI controller restored from scenario.",
                    "error": None,
                })
        return warnings

    def _prepare_clear_lifecycle(self, plan):
        target_ids = {item.runtime_id for item in plan.items}
        self.cleanup_movement_preview("clear_world")
        self.cleanup_sensor_previews()
        self.cleanup_preview()

        for item in plan.items:
            if item.category == ActorCategory.SENSOR:
                self.stop_sensor_preview_actor(item.runtime_id)
            elif item.category == ActorCategory.WALKER_AI_CONTROLLER:
                continue
            elif item.category == ActorCategory.PEDESTRIAN:
                self._destroy_walker_ai(
                    item.runtime_id,
                    remove_state=True,
                )
            elif item.category == ActorCategory.VEHICLE:
                self._release_vehicle_control(
                    item.runtime_id,
                    disable_autopilot=True,
                )

        with self.database.lock:
            for actor_id in target_ids:
                self.database.vehicle_control_states.pop(actor_id, None)
                self.database.walker_ai_states.pop(actor_id, None)

    def _prepare_load_clear_lifecycle(self, plan):
        self.database.scenario_event_queue.put((
            "scenario_load_destructive_started",
            {"target_map": None, "map_change": False},
        ))
        self._prepare_clear_lifecycle(plan)

    def _finish_clear_operation(self):
        with self.scenario_operation_lock:
            self.active_clear_plan = None
            self.clear_execution_queued = False
            self.scenario_operation_pending = False
            self.scenario_operation_kind = None

    def _finish_load_operation(self):
        with self.scenario_operation_lock:
            self.active_load_plan = None
            self.load_execution_queued = False
            self.scenario_operation_pending = False
            self.scenario_operation_kind = None

    def _invalidate_load_plan_if_episode_changed(self, world):
        invalidated = None
        try:
            current_world_id = int(world.id)
        except (AttributeError, TypeError, ValueError):
            current_world_id = None
        with self.scenario_operation_lock:
            if self.scenario_operation_kind == "load":
                return False
            plan = self.active_load_plan
            if plan is not None and plan.world_id != current_world_id:
                invalidated = plan
                self.active_load_plan = None
        if invalidated is not None:
            self.database.scenario_event_queue.put((
                "scenario_load_plan_invalidated",
                {
                    "token": invalidated.token,
                    "reason": "world_episode_changed",
                    "previous_world_id": invalidated.world_id,
                    "current_world_id": current_world_id,
                },
            ))
        return invalidated is not None

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
        if session_id is None:
            return
        status = "vehicle_control_error" if error else "vehicle_control_state"
        self.database.movement_event_queue.put((status, {
            "session_id": session_id,
            "actor_id": actor_id,
            "world_id": self._movement_world_id(),
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

    @staticmethod
    def _vehicle_xy_distance(first, second):
        return math.hypot(
            float(first.x) - float(second.x),
            float(first.y) - float(second.y),
        )

    @staticmethod
    def _vehicle_speed(actor):
        velocity = actor.get_velocity()
        return math.sqrt(
            float(velocity.x) ** 2
            + float(velocity.y) ** 2
            + float(velocity.z) ** 2
        )

    def _vehicle_destination_for_world(self, actor_id, world):
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            destination = None if state is None else state.get("destination")
            state_world_id = None if state is None else state.get("world_id")
        if not isinstance(destination, dict) or not destination.get("confirmed"):
            raise RuntimeError("Confirm a valid Destination before starting navigation.")
        world_id = self._world_id(world)
        destination_world_id = destination.get("world_id")
        destination_actor_id = destination.get("actor_id")
        try:
            state_world_id = int(state_world_id)
            destination_world_id = int(destination_world_id)
            destination_actor_id = int(destination_actor_id)
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                "The selected Destination has invalid actor or world metadata."
            ) from error
        if (
            world_id is None
            or state_world_id != world_id
            or destination_world_id != world_id
            or destination_actor_id != actor_id
        ):
            raise RuntimeError(
                "The selected Destination belongs to a different actor or world."
            )
        selected = destination.get("selected")
        if not isinstance(selected, dict):
            raise RuntimeError("The confirmed Destination has no usable point.")
        try:
            return carla.Location(
                x=float(selected["x"]),
                y=float(selected["y"]),
                z=float(selected["z"]),
            ), destination
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("The confirmed Destination coordinates are invalid.") from error

    def _plan_vehicle_route(self, world, actor, destination_location):
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
        target_waypoint = carla_map.get_waypoint(
            destination_location,
            project_to_road=True,
            lane_type=lane_type,
        )
        if start_waypoint is None or target_waypoint is None:
            raise RuntimeError("No driving lane is available near the vehicle or Destination.")

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
        for route_item in traced_route:
            waypoint = route_item[0] if isinstance(route_item, (tuple, list)) else route_item
            location = waypoint.transform.location
            point = carla.Location(
                x=float(location.x),
                y=float(location.y),
                z=float(location.z),
            )
            if not path or self._vehicle_xy_distance(path[-1], point) > 0.05:
                path.append(point)
        if len(path) < 2:
            raise RuntimeError("The planned route does not contain enough waypoints.")
        return path, target_waypoint.transform.location

    def _clear_vehicle_navigation_path(self, actor):
        if self.traffic_manager is None:
            return
        try:
            self.traffic_manager.set_path(actor, [], True)
        except Exception as error:
            self.dashboard_monitor.record_error(error)

    def _cancel_vehicle_navigation(self, actor_id, actor=None,
                                   status=VEHICLE_STATUS_CANCELLED,
                                   stop_vehicle=False):
        runtime_store = getattr(self, "vehicle_navigation_runtime", None)
        if runtime_store is None:
            runtime_store = {}
            self.vehicle_navigation_runtime = runtime_store
        runtime = runtime_store.pop(actor_id, None)
        if actor is not None and getattr(actor, "is_alive", False):
            self._clear_vehicle_navigation_path(actor)
            if stop_vehicle:
                actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                self._safe_stop_vehicle(actor)
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            if state is not None:
                state.update({
                    "navigation_status": status,
                    "route": None,
                    "distance_to_destination": (
                        state.get("distance_to_destination")
                        if runtime is not None else None
                    ),
                })

    def _activate_vehicle_destination(self, world, actor, actor_id,
                                      destination_location, destination,
                                      path, route_target):
        traffic_manager = self._ensure_traffic_manager(world)
        traffic_manager.set_path(actor, path, True)
        actor.set_autopilot(True, self.TRAFFIC_MANAGER_PORT)
        current_location = actor.get_location()
        distance = self._vehicle_xy_distance(current_location, route_target)
        world_id = self._world_id(world)
        now = time.monotonic()
        runtime_store = getattr(self, "vehicle_navigation_runtime", None)
        if runtime_store is None:
            runtime_store = {}
            self.vehicle_navigation_runtime = runtime_store
        runtime_store[actor_id] = {
            "world_id": world_id,
            "destination": carla.Location(
                x=float(route_target.x),
                y=float(route_target.y),
                z=float(route_target.z),
            ),
            "route": path,
            "next_check": 0.0,
            "last_location": current_location,
            "last_progress_time": now,
            "last_frame": None,
            "arrival_braking": False,
        }
        with self.database.lock:
            state = self.database.vehicle_control_states[actor_id]
            state.update({
                "mode": AUTOPILOT_DESTINATION,
                "control_mode": AUTOPILOT_DESTINATION,
                "status": "Destination navigation is active.",
                "navigation_status": VEHICLE_STATUS_NAVIGATING,
                "destination": destination,
                "route": {
                    "waypoint_count": len(path),
                    "sampling_resolution": self.VEHICLE_NAVIGATION_SAMPLING_RESOLUTION,
                },
                "distance_to_destination": distance,
                "current_speed": self._vehicle_speed(actor),
                "world_id": world_id,
                "autopilot_enabled": True,
                "autopilot_state_source": "local",
                "traffic_manager_port": self.TRAFFIC_MANAGER_PORT,
                "input_fresh": False,
                "error": None,
            })

    def _service_vehicle_navigation(self, world, world_snapshot=None):
        transition = getattr(self, "world_transition_event", None)
        if transition is not None and transition.is_set():
            return
        runtime_store = getattr(self, "vehicle_navigation_runtime", None)
        if not runtime_store:
            return
        world_id = self._world_id(world)
        frame = getattr(world_snapshot, "frame", None)
        now = time.monotonic()

        for actor_id, runtime in list(runtime_store.items()):
            if runtime.get("world_id") != world_id:
                runtime_store.pop(actor_id, None)
                continue
            if frame is not None and runtime.get("last_frame") == frame:
                continue
            runtime["last_frame"] = frame
            if now < runtime.get("next_check", 0.0):
                continue
            runtime["next_check"] = now + self.VEHICLE_NAVIGATION_CHECK_INTERVAL_SECONDS
            actor = None
            try:
                actor = self._get_managed_vehicle(actor_id)
                with self.database.lock:
                    state = self.database.vehicle_control_states.get(actor_id)
                    mode = None if state is None else state.get("mode")
                if mode != AUTOPILOT_DESTINATION:
                    runtime_store.pop(actor_id, None)
                    continue

                location = actor.get_location()
                destination = runtime["destination"]
                distance = self._vehicle_xy_distance(location, destination)
                speed = self._vehicle_speed(actor)
                moved = self._vehicle_xy_distance(location, runtime["last_location"])
                if moved >= self.VEHICLE_DESTINATION_PROGRESS_DISTANCE:
                    runtime["last_location"] = location
                    runtime["last_progress_time"] = now

                if (
                    not runtime["arrival_braking"]
                    and distance <= self.VEHICLE_DESTINATION_ARRIVAL_DISTANCE
                ):
                    actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                    actor.apply_control(carla.VehicleControl(
                        throttle=0.0,
                        steer=0.0,
                        brake=0.6,
                        hand_brake=False,
                        manual_gear_shift=False,
                    ))
                    runtime["arrival_braking"] = True

                navigation_status = VEHICLE_STATUS_NAVIGATING
                status = "Destination navigation is active."
                if runtime["arrival_braking"]:
                    status = "Destination reached; vehicle is braking."
                    if (
                        distance <= self.VEHICLE_DESTINATION_STOP_DISTANCE
                        and speed <= self.VEHICLE_DESTINATION_STOP_SPEED
                    ):
                        actor.apply_control(carla.VehicleControl(
                            throttle=0.0,
                            steer=0.0,
                            brake=1.0,
                            hand_brake=True,
                            manual_gear_shift=False,
                        ))
                        navigation_status = VEHICLE_STATUS_ARRIVED
                        status = "Destination reached; vehicle stopped."
                        runtime_store.pop(actor_id, None)
                    elif speed <= self.VEHICLE_DESTINATION_STOP_SPEED:
                        actor.apply_control(carla.VehicleControl(
                            throttle=0.0,
                            steer=0.0,
                            brake=1.0,
                            hand_brake=True,
                            manual_gear_shift=False,
                        ))
                        navigation_status = VEHICLE_STATUS_FAILED
                        status = (
                            "Vehicle stopped beyond the Destination tolerance."
                        )
                        runtime_store.pop(actor_id, None)
                elif (
                    speed <= self.VEHICLE_DESTINATION_STUCK_SPEED
                    and now - runtime["last_progress_time"]
                    >= self.VEHICLE_DESTINATION_STUCK_SECONDS
                ):
                    actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                    self._safe_stop_vehicle(actor)
                    navigation_status = VEHICLE_STATUS_STUCK
                    status = "Destination navigation stopped: vehicle is stuck."
                    runtime_store.pop(actor_id, None)

                with self.database.lock:
                    state = self.database.vehicle_control_states.get(actor_id)
                    if state is not None:
                        state.update({
                            "navigation_status": navigation_status,
                            "status": status,
                            "distance_to_destination": distance,
                            "current_speed": speed,
                            "autopilot_enabled": (
                                navigation_status == VEHICLE_STATUS_NAVIGATING
                                and not runtime["arrival_braking"]
                            ),
                            "error": (
                                status
                                if navigation_status == VEHICLE_STATUS_FAILED
                                else None
                            ),
                        })
                self._publish_vehicle_control_state(actor_id)
            except Exception as error:
                self.dashboard_monitor.record_error(error)
                runtime_store.pop(actor_id, None)
                if actor is not None and getattr(actor, "is_alive", False):
                    try:
                        actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                        self._clear_vehicle_navigation_path(actor)
                        self._safe_stop_vehicle(actor)
                    except Exception as cleanup_error:
                        self.dashboard_monitor.record_error(cleanup_error)
                with self.database.lock:
                    state = self.database.vehicle_control_states.get(actor_id)
                    if state is not None:
                        state.update({
                            "navigation_status": VEHICLE_STATUS_FAILED,
                            "status": f"Destination navigation failed: {error}",
                            "autopilot_enabled": False,
                            "error": str(error),
                        })
                self._publish_vehicle_control_state(actor_id, error=error)

    def _set_vehicle_control_mode(self, world, data):
        session_id = data["session_id"]
        actor_id = int(data["actor_id"])
        requested_mode = normalize_vehicle_control_mode(data["mode"])
        actor = None
        destination_activation_started = False
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
            previous_mode = normalize_vehicle_control_mode(
                previous.get("mode", NO_CONTROL)
            )

            prepared_destination = None
            if requested_mode == AUTOPILOT_DESTINATION:
                destination_location, destination = (
                    self._vehicle_destination_for_world(actor_id, world)
                )
                route, route_target = self._plan_vehicle_route(
                    world,
                    actor,
                    destination_location,
                )
                prepared_destination = (
                    destination_location,
                    destination,
                    route,
                    route_target,
                )

            if previous_mode == MANUAL_CONTROL and requested_mode != MANUAL_CONTROL:
                self._safe_stop_vehicle(actor, previous.get("reverse", False))
                with self.database.lock:
                    state = self.database.vehicle_control_states[actor_id]
                    state.update({
                        "mode": NO_CONTROL,
                        "status": "Manual Control ended; vehicle stopped safely.",
                        "input_fresh": False,
                    })
            if (
                previous_mode in (AUTOPILOT_FREE_ROAM, AUTOPILOT_DESTINATION)
                and requested_mode != previous_mode
            ):
                actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                if previous_mode == AUTOPILOT_DESTINATION:
                    self._cancel_vehicle_navigation(actor_id, actor=actor)
                with self.database.lock:
                    state = self.database.vehicle_control_states[actor_id]
                    state.update({
                        "mode": NO_CONTROL,
                        "status": "Autopilot disabled.",
                        "autopilot_enabled": False,
                        "traffic_manager_port": None,
                    })

            if requested_mode == AUTOPILOT_FREE_ROAM:
                self._ensure_traffic_manager(world)
                self._clear_vehicle_navigation_path(actor)
                actor.set_autopilot(True, self.TRAFFIC_MANAGER_PORT)
                status = (
                    f"Autopilot Free Roam active (Traffic Manager port "
                    f"{self.TRAFFIC_MANAGER_PORT})."
                )
                navigation_status = (
                    VEHICLE_STATUS_CANCELLED
                    if previous_mode == AUTOPILOT_DESTINATION
                    else VEHICLE_STATUS_IDLE
                )
            elif requested_mode == AUTOPILOT_DESTINATION:
                destination_activation_started = True
                self._activate_vehicle_destination(
                    world,
                    actor,
                    actor_id,
                    *prepared_destination,
                )
                self._publish_vehicle_control_state(actor_id, session_id=session_id)
                return
            elif requested_mode == MANUAL_CONTROL:
                self._safe_stop_vehicle(actor, previous.get("reverse", False))
                self.manual_control_command = None
                self.manual_control_received_at = None
                self.manual_control_next_apply = 0.0
                self.manual_control_input_fresh = False
                status = "Manual Control active; waiting for focused Pygame input."
                navigation_status = (
                    VEHICLE_STATUS_CANCELLED
                    if previous_mode == AUTOPILOT_DESTINATION
                    else VEHICLE_STATUS_IDLE
                )
            else:
                if previous_mode == AUTOPILOT_DESTINATION:
                    self._safe_stop_vehicle(actor)
                status = "No controller is active."
                navigation_status = (
                    VEHICLE_STATUS_CANCELLED
                    if previous_mode == AUTOPILOT_DESTINATION
                    else VEHICLE_STATUS_IDLE
                )

            with self.database.lock:
                state = self.database.vehicle_control_states[actor_id]
                state.update({
                    "mode": requested_mode,
                    "control_mode": requested_mode,
                    "status": status,
                    "navigation_status": navigation_status,
                    "route": None,
                    "autopilot_enabled": requested_mode == AUTOPILOT_FREE_ROAM,
                    "autopilot_state_source": "local",
                    "traffic_manager_port": (
                        self.TRAFFIC_MANAGER_PORT
                        if requested_mode == AUTOPILOT_FREE_ROAM else None
                    ),
                    "input_fresh": False,
                    "error": None,
                })
            self._publish_vehicle_control_state(actor_id, session_id=session_id)
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            if (
                requested_mode == AUTOPILOT_DESTINATION
                and destination_activation_started
                and actor is not None
                and getattr(actor, "is_alive", False)
            ):
                try:
                    actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                    self._clear_vehicle_navigation_path(actor)
                    self._safe_stop_vehicle(actor)
                except Exception as cleanup_error:
                    self.dashboard_monitor.record_error(cleanup_error)
                self.vehicle_navigation_runtime.pop(actor_id, None)
            with self.database.lock:
                state = self.database.vehicle_control_states.get(actor_id)
                if state is not None:
                    state["status"] = f"Control mode change failed: {error}"
                    state["error"] = str(error)
                    if requested_mode == AUTOPILOT_DESTINATION:
                        state["navigation_status"] = VEHICLE_STATUS_FAILED
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
                    "control_mode": NO_CONTROL,
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
                    "control_mode": NO_CONTROL,
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
            and normalize_vehicle_control_mode(state_copy.get("mode")) in (
                AUTOPILOT_FREE_ROAM,
                AUTOPILOT_DESTINATION,
            )
            and actor is not None
            and getattr(actor, "is_alive", False)
        ):
            try:
                actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
            except Exception as error:
                self.dashboard_monitor.record_error(error)
        self._cancel_vehicle_navigation(
            actor_id,
            actor=actor,
            status=VEHICLE_STATUS_CANCELLED,
            stop_vehicle=False,
        )
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            if state is not None:
                state.update({
                    "mode": NO_CONTROL,
                    "control_mode": NO_CONTROL,
                    "status": "Vehicle control released.",
                    "navigation_status": VEHICLE_STATUS_CANCELLED,
                    "route": None,
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
            "world_id": self._movement_world_id(),
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
    def _walker_xy_distance(first, second):
        return (
            (float(first.x) - float(second.x)) ** 2
            + (float(first.y) - float(second.y)) ** 2
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

    @staticmethod
    def _walker_fallback_direction(current_location, target):
        delta_x = float(target.x) - float(current_location.x)
        delta_y = float(target.y) - float(current_location.y)
        length = (delta_x ** 2 + delta_y ** 2) ** 0.5
        if length <= 1e-6:
            return carla.Vector3D(x=1.0, y=0.0, z=0.0)
        return carla.Vector3D(
            x=delta_x / length,
            y=delta_y / length,
            z=0.0,
        )

    @classmethod
    def _walker_initial_fallback_direction(cls, walker, location, target):
        try:
            forward = walker.get_transform().get_forward_vector()
            length = (
                float(forward.x) ** 2 + float(forward.y) ** 2
            ) ** 0.5
            if length > 1e-6:
                return carla.Vector3D(
                    x=float(forward.x) / length,
                    y=float(forward.y) / length,
                    z=0.0,
                )
        except (AttributeError, RuntimeError):
            pass
        return cls._walker_fallback_direction(location, target)

    @staticmethod
    def _apply_walker_fallback_control(walker, direction, speed):
        walker.apply_control(carla.WalkerControl(
            direction,
            float(speed),
            False,
        ))

    def _service_walker_fallback(self, world, walker_id, walker,
                                 simulation_time):
        runtime = self.walker_ai_runtime[walker_id]
        now = time.monotonic()
        location = walker.get_location()
        direction = runtime.get("fallback_direction")
        if direction is None:
            target = runtime.get("target") or location
            direction = self._walker_fallback_direction(location, target)

        last_location = runtime.get("fallback_last_location")
        progressed = (
            last_location is None
            or self._walker_distance(location, last_location) >= 0.05
        )
        if progressed:
            runtime["fallback_last_location"] = location
            runtime["fallback_last_progress_at"] = now
            if simulation_time is not None:
                runtime["fallback_last_progress_sim_time"] = simulation_time

        progress_sim_time = runtime.get("fallback_last_progress_sim_time")
        if simulation_time is not None and progress_sim_time is not None:
            stalled = (
                simulation_time - progress_sim_time
                >= self.WALKER_AI_FALLBACK_STALL_SECONDS
            )
        else:
            stalled = (
                now - runtime.get("fallback_last_progress_at", now)
                >= self.WALKER_AI_FALLBACK_STALL_SECONDS
            )

        origin = runtime.get("fallback_origin") or location
        leg_finished = (
            self._walker_distance(location, origin)
            >= self.WALKER_AI_FALLBACK_LEG_DISTANCE
        )
        status_changed = False
        if stalled:
            direction = carla.Vector3D(
                x=-float(direction.y),
                y=float(direction.x),
                z=0.0,
            )
            runtime.update({
                "fallback_origin": location,
                "fallback_last_location": location,
                "fallback_last_progress_at": now,
                "fallback_last_progress_sim_time": simulation_time,
            })
            with self.database.lock:
                self.database.walker_ai_states[walker_id]["status"] = (
                    "Walker AI fallback steering changed direction after "
                    "detecting an obstacle."
                )
            status_changed = True
        elif leg_finished:
            target = world.get_random_location_from_navigation()
            if target is not None:
                runtime["target"] = target
                direction = self._walker_fallback_direction(location, target)
                with self.database.lock:
                    state = self.database.walker_ai_states[walker_id]
                    state["target"] = self._serialize_location(target)
                    state["status"] = (
                        "Walker AI fallback selected a new random direction."
                    )
                status_changed = True
            else:
                direction = carla.Vector3D(
                    x=-float(direction.y),
                    y=float(direction.x),
                    z=0.0,
                )
            runtime.update({
                "fallback_origin": location,
                "fallback_last_location": location,
                "fallback_last_progress_at": now,
                "fallback_last_progress_sim_time": simulation_time,
            })

        runtime["fallback_direction"] = direction
        with self.database.lock:
            saved_speed = self.database.walker_ai_states[walker_id]["speed"]
        speed = runtime.get("speed", saved_speed)
        runtime["speed"] = speed
        self._apply_walker_fallback_control(walker, direction, speed)
        return status_changed

    def _start_walker_direct_control(self, world, walker, speed):
        now = time.monotonic()
        location = walker.get_location()
        target = None
        for _attempt in range(5):
            candidate = world.get_random_location_from_navigation()
            if candidate is None:
                continue
            target = candidate
            if self._walker_distance(location, candidate) > 1.0:
                break

        direction_target = target or location
        direction = self._walker_initial_fallback_direction(
            walker,
            location,
            direction_target,
        )
        runtime = self.walker_ai_runtime.setdefault(walker.id, {})
        runtime.update({
            "world_id": self._world_id(world),
            "navigation_mode": WALKER_CONTROL_RANDOM,
            "speed": speed,
            "target": target,
            "next_check": now + self.WALKER_AI_CHECK_INTERVAL_SECONDS,
            "last_distance": None,
            "last_progress_at": now,
            "last_progress_sim_time": None,
            "last_frame": None,
            "fallback_active": True,
            "fallback_direction": direction,
            "fallback_origin": location,
            "fallback_last_location": location,
            "fallback_last_progress_at": now,
            "fallback_last_progress_sim_time": None,
        })
        with self.database.lock:
            state = self.database.walker_ai_states[walker.id]
            state.update({
                "mode": WALKER_AI_RUNNING,
                "active": True,
                "speed": speed,
                "controller_id": None,
                "target": self._serialize_location(target),
                "random_navigation": True,
                "state_source": "gui",
                "status": (
                    "Walker autopilot running with direct control."
                    if target is not None else
                    "Walker autopilot running; no navigation point is "
                    "available, using the actor heading."
                ),
                "error": None,
                "control_mode": WALKER_CONTROL_RANDOM,
                "movement_status": WALKER_STATUS_MOVING,
            })
        self._apply_walker_fallback_control(walker, direction, speed)

    def _start_walker_destination_control(self, world, walker, speed):
        with self.database.lock:
            state = self.database.walker_ai_states[walker.id]
            destination = state.get("destination")
        if (
            not isinstance(destination, dict)
            or not destination.get("confirmed")
            or not isinstance(destination.get("selected"), dict)
        ):
            raise ValueError(
                "Confirm a pedestrian destination before starting this mode."
            )
        if destination.get("world_id") != int(world.id):
            raise ValueError(
                "The selected destination belongs to another world episode."
            )
        selected = destination["selected"]
        target = carla.Location(
            x=float(selected["x"]),
            y=float(selected["y"]),
            z=float(selected["z"]),
        )
        location = walker.get_location()
        distance = self._walker_xy_distance(location, target)
        if distance <= self.WALKER_DESTINATION_ARRIVAL_DISTANCE:
            self._apply_walker_fallback_control(
                walker,
                carla.Vector3D(x=0.0, y=0.0, z=0.0),
                0.0,
            )
            with self.database.lock:
                state = self.database.walker_ai_states[walker.id]
                state.update({
                    "mode": WALKER_AI_STOPPED,
                    "active": False,
                    "control_mode": WALKER_CONTROL_DESTINATION,
                    "movement_status": WALKER_STATUS_ARRIVED,
                    "target": self._serialize_location(target),
                    "random_navigation": False,
                    "status": "Arrived at the selected destination.",
                    "error": None,
                })
            return

        now = time.monotonic()
        direction = self._walker_fallback_direction(location, target)
        runtime = self.walker_ai_runtime.setdefault(walker.id, {})
        runtime.update({
            "world_id": self._world_id(world),
            "navigation_mode": WALKER_CONTROL_DESTINATION,
            "speed": speed,
            "target": target,
            "next_check": now + self.WALKER_AI_CHECK_INTERVAL_SECONDS,
            "last_distance": distance,
            "last_progress_at": now,
            "last_progress_sim_time": None,
            "last_frame": None,
            "fallback_active": True,
            "fallback_direction": direction,
            "fallback_origin": location,
            "fallback_last_location": location,
            "fallback_last_progress_at": now,
            "fallback_last_progress_sim_time": None,
        })
        with self.database.lock:
            state = self.database.walker_ai_states[walker.id]
            state.update({
                "mode": WALKER_AI_RUNNING,
                "active": True,
                "speed": speed,
                "controller_id": None,
                "target": self._serialize_location(target),
                "random_navigation": False,
                "control_mode": WALKER_CONTROL_DESTINATION,
                "movement_status": WALKER_STATUS_MOVING,
                "state_source": "gui",
                "status": (
                    f"Moving to destination ({distance:.2f} m remaining)."
                ),
                "error": None,
            })
        self._apply_walker_fallback_control(walker, direction, speed)

    def _set_walker_control_mode(self, world, data):
        session_id = data["session_id"]
        walker_id = int(data["actor_id"])
        try:
            if (
                session_id != self.movement_preview_session_id
                or walker_id != self.movement_preview_actor_id
            ):
                raise RuntimeError(
                    "The Movement Control session is no longer active."
                )
            requested_mode = str(data["mode"])
            if requested_mode not in WALKER_CONTROL_MODES:
                raise ValueError(
                    f"Unknown pedestrian control mode: {requested_mode}"
                )
            walker = self._get_managed_walker(walker_id)
            with self.database.lock:
                previous = self.database.walker_ai_states[walker_id].copy()
                saved_speed = previous["speed"]
            try:
                speed = self._validate_walker_speed(data.get("speed"))
            except (TypeError, ValueError):
                if requested_mode != WALKER_CONTROL_MANUAL:
                    raise
                speed = saved_speed

            if requested_mode == WALKER_CONTROL_RANDOM:
                self._start_walker_direct_control(world, walker, speed)
            elif requested_mode == WALKER_CONTROL_DESTINATION:
                self._start_walker_destination_control(world, walker, speed)
            else:
                was_destination = (
                    previous.get("control_mode")
                    == WALKER_CONTROL_DESTINATION
                    and previous.get("active")
                )
                runtime = self.walker_ai_runtime.setdefault(walker_id, {})
                runtime.update({
                    "navigation_mode": WALKER_CONTROL_MANUAL,
                    "target": None,
                    "fallback_active": False,
                    "fallback_direction": None,
                })
                self._apply_walker_fallback_control(
                    walker,
                    carla.Vector3D(x=0.0, y=0.0, z=0.0),
                    0.0,
                )
                with self.database.lock:
                    state = self.database.walker_ai_states[walker_id]
                    state.update({
                        "mode": WALKER_AI_STOPPED,
                        "active": False,
                        "control_mode": WALKER_CONTROL_MANUAL,
                        "movement_status": (
                            WALKER_STATUS_CANCELLED
                            if was_destination else WALKER_STATUS_IDLE
                        ),
                        "status": (
                            "Destination movement cancelled."
                            if was_destination else
                            "Manual control selected; pedestrian stopped."
                        ),
                        "speed": speed,
                        "target": None,
                        "random_navigation": False,
                        "state_source": "gui",
                        "error": None,
                    })
            self._publish_walker_ai_state(walker_id, session_id=session_id)
        except Exception as error:
            self._walker_ai_failure(
                walker_id,
                error,
                session_id=session_id,
            )

    def _set_walker_manual_control(self, data):
        session_id = data["session_id"]
        walker_id = int(data["actor_id"])
        try:
            if (
                session_id != self.movement_preview_session_id
                or walker_id != self.movement_preview_actor_id
            ):
                raise RuntimeError(
                    "The Movement Control session is no longer active."
                )
            walker = self._get_managed_walker(walker_id)
            speed = self._validate_walker_speed(data["speed"])
            direction_x = float(data["direction_x"])
            direction_y = float(data["direction_y"])
            length = (direction_x ** 2 + direction_y ** 2) ** 0.5
            if not math.isfinite(length) or length <= 1e-6:
                raise ValueError("Manual direction X/Y must be non-zero.")
            direction_x /= length
            direction_y /= length
            self.walker_ai_runtime.pop(walker_id, None)
            direction = carla.Vector3D(
                x=direction_x,
                y=direction_y,
                z=0.0,
            )
            self._apply_walker_fallback_control(walker, direction, speed)
            with self.database.lock:
                state = self.database.walker_ai_states[walker_id]
                state.update({
                    "mode": WALKER_AI_STOPPED,
                    "active": False,
                    "control_mode": WALKER_CONTROL_MANUAL,
                    "movement_status": WALKER_STATUS_MOVING,
                    "manual_direction": {
                        "x": direction_x,
                        "y": direction_y,
                    },
                    "speed": speed,
                    "target": None,
                    "random_navigation": False,
                    "status": "Manual WalkerControl command is active.",
                    "state_source": "gui",
                    "error": None,
                })
            self._publish_walker_ai_state(walker_id, session_id=session_id)
        except Exception as error:
            self._walker_ai_failure(
                walker_id,
                error,
                session_id=session_id,
                stop_ai=False,
            )

    def _set_walker_ai_enabled(self, world, data):
        compatibility_data = dict(data)
        compatibility_data["mode"] = (
            WALKER_CONTROL_RANDOM
            if data.get("enabled") else WALKER_CONTROL_MANUAL
        )
        self._set_walker_control_mode(world, compatibility_data)

    def _movement_destination_actor(self, data):
        session_id = data["session_id"]
        actor_id = int(data["actor_id"])
        if (
            session_id != self.movement_preview_session_id
            or actor_id != self.movement_preview_actor_id
        ):
            raise RuntimeError(
                "The Movement Control session is no longer active."
            )
        with self.database.lock:
            actor = self.database.actors_db.actors.get(actor_id)
            actor_info = self.database.actors_db.actors_db.get(actor_id)
        if actor is None or actor_info is None or not actor.is_alive:
            raise RuntimeError("The selected actor is no longer available.")
        if not self.database.is_actor_managed(actor_id):
            raise RuntimeError("Destination requires a GUI-managed actor.")
        if actor.type_id.startswith("vehicle."):
            category = "vehicle"
        elif actor.type_id.startswith("walker."):
            category = "pedestrian"
        else:
            raise RuntimeError(
                "Destination supports vehicles and pedestrians only."
            )
        return session_id, actor_id, actor, category

    def _publish_movement_destination(self, status, session_id, actor_id,
                                      **values):
        self.database.movement_event_queue.put((status, {
            "session_id": session_id,
            "actor_id": actor_id,
            "world_id": self._movement_world_id(),
            **values,
        }))

    def _validate_movement_destination(self, world, data):
        session_id = data.get("session_id")
        actor_id = int(data.get("actor_id"))
        try:
            session_id, actor_id, _actor, category = (
                self._movement_destination_actor(data)
            )
            requested = point_from_values(
                data.get("x"),
                data.get("y"),
                data.get("z"),
            )
            if category == "vehicle":
                validation = validate_vehicle_destination(
                    world,
                    requested,
                    carla,
                )
            else:
                validation = validate_pedestrian_destination(requested)
            validation_status, requested, adjusted, message = validation
            destination = destination_result(
                actor_id,
                category,
                int(world.id),
                requested,
                validation_status,
                message,
                adjusted=adjusted,
            )
            destination = self.database.set_movement_destination(
                actor_id,
                destination,
            )
            self._publish_movement_destination(
                "movement_destination_validation",
                session_id,
                actor_id,
                destination=destination,
            )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            category = "unknown"
            try:
                with self.database.lock:
                    actor = self.database.actors_db.actors.get(actor_id)
                category = (
                    "vehicle"
                    if actor is not None
                    and actor.type_id.startswith("vehicle.")
                    else "pedestrian"
                )
                destination = empty_destination(
                    actor_id,
                    category,
                    int(world.id),
                )
                destination.update({
                    "validation_status": DESTINATION_ERROR,
                    "message": f"Destination validation failed: {error}",
                })
                self.database.set_movement_destination(
                    actor_id,
                    destination,
                )
            except Exception:
                destination = None
            self._publish_movement_destination(
                "movement_destination_error",
                session_id,
                actor_id,
                destination=destination,
                message=str(error),
            )

    def _confirm_movement_destination(self, world, data):
        session_id = data.get("session_id")
        actor_id = int(data.get("actor_id"))
        try:
            session_id, actor_id, _actor, _category = (
                self._movement_destination_actor(data)
            )
            destination = self.database.get_movement_destination(actor_id)
            if destination is None or destination.get("world_id") != int(world.id):
                raise ValueError(
                    "Validate a destination in the current world first."
                )
            destination = confirm_destination(destination)
            destination = self.database.set_movement_destination(
                actor_id,
                destination,
            )
            self._publish_movement_destination(
                "movement_destination_confirmed",
                session_id,
                actor_id,
                destination=destination,
            )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self._publish_movement_destination(
                "movement_destination_error",
                session_id,
                actor_id,
                destination=self.database.get_movement_destination(actor_id),
                message=str(error),
            )

    def _clear_movement_destination(self, world, data):
        session_id = data.get("session_id")
        actor_id = int(data.get("actor_id"))
        try:
            session_id, actor_id, actor, category = (
                self._movement_destination_actor(data)
            )
            if category == "vehicle":
                with self.database.lock:
                    state = self.database.vehicle_control_states.get(actor_id)
                    active_destination = (
                        state is not None
                        and state.get("mode") == AUTOPILOT_DESTINATION
                    )
                if active_destination:
                    self._cancel_vehicle_navigation(
                        actor_id,
                        actor=actor,
                        status=VEHICLE_STATUS_CANCELLED,
                        stop_vehicle=True,
                    )
                    with self.database.lock:
                        state = self.database.vehicle_control_states.get(actor_id)
                        if state is not None:
                            state.update({
                                "mode": NO_CONTROL,
                                "control_mode": NO_CONTROL,
                                "status": "Destination navigation cancelled.",
                                "autopilot_enabled": False,
                                "traffic_manager_port": None,
                            })
                    self._publish_vehicle_control_state(
                        actor_id,
                        session_id=session_id,
                    )
            destination = empty_destination(
                actor_id,
                category,
                int(world.id),
            )
            destination = self.database.set_movement_destination(
                actor_id,
                destination,
            )
            self._publish_movement_destination(
                "movement_destination_cleared",
                session_id,
                actor_id,
                destination=destination,
            )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self._publish_movement_destination(
                "movement_destination_error",
                session_id,
                actor_id,
                message=str(error),
            )

    def _get_movement_destination_map(self, world, data):
        session_id = data.get("session_id")
        actor_id = int(data.get("actor_id"))
        try:
            session_id, actor_id, actor, _category = (
                self._movement_destination_actor(data)
            )
            waypoints = world.get_map().generate_waypoints(
                MAP_WAYPOINT_DISTANCE_METERS
            )
            points = []
            seen = set()
            for waypoint in waypoints:
                location = waypoint.transform.location
                key = (
                    round(float(location.x), 3),
                    round(float(location.y), 3),
                    int(getattr(waypoint, "road_id", 0)),
                    int(getattr(waypoint, "lane_id", 0)),
                )
                if key in seen:
                    continue
                seen.add(key)
                points.append({
                    "x": float(location.x),
                    "y": float(location.y),
                    "z": float(location.z),
                    "road_id": key[2],
                    "lane_id": key[3],
                    "s": float(getattr(waypoint, "s", 0.0)),
                })
            if not points:
                raise RuntimeError(
                    "The active CARLA map did not provide road waypoints."
                )
            self._publish_movement_destination(
                "movement_destination_map",
                session_id,
                actor_id,
                map_name=str(world.get_map().name),
                actor_location=point_from_location(actor.get_location()),
                points=points,
            )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self._publish_movement_destination(
                "movement_destination_map_error",
                session_id,
                actor_id,
                message=str(error),
            )

    def _get_movement_actor_location(self, data):
        session_id = data.get("session_id")
        actor_id = int(data.get("actor_id"))
        try:
            session_id, actor_id, actor, _category = (
                self._movement_destination_actor(data)
            )
            self._publish_movement_destination(
                "movement_actor_location",
                session_id,
                actor_id,
                actor_location=point_from_location(actor.get_location()),
            )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self._publish_movement_destination(
                "movement_destination_error",
                session_id,
                actor_id,
                message=str(error),
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
                previous_speed = state["speed"]
                active = state["active"]
                control_mode = state.get(
                    "control_mode",
                    WALKER_CONTROL_MANUAL,
                )
                movement_status = state.get("movement_status")
                manual_direction = dict(
                    state.get("manual_direction") or {}
                )
                state["speed"] = speed
            runtime = self.walker_ai_runtime.get(walker_id)
            previous_runtime_speed = (
                None if runtime is None else runtime.get("speed")
            )
            if runtime is not None:
                runtime["speed"] = speed
            controller = self.walker_ai_controllers.get(walker_id)
            try:
                if active:
                    active_runtime = runtime or {}
                    if active_runtime.get("fallback_active"):
                        direction = active_runtime.get("fallback_direction")
                        if direction is not None:
                            walker = self._get_managed_walker(walker_id)
                            self._apply_walker_fallback_control(
                                walker,
                                direction,
                                speed,
                            )
                    elif controller is None or not getattr(
                        controller,
                        "is_alive",
                        False,
                    ):
                        raise RuntimeError("The Walker AI controller is unavailable.")
                    else:
                        controller.set_max_speed(speed)
                elif (
                    control_mode == WALKER_CONTROL_MANUAL
                    and movement_status == WALKER_STATUS_MOVING
                ):
                    walker = self._get_managed_walker(walker_id)
                    self._apply_walker_fallback_control(
                        walker,
                        carla.Vector3D(
                            x=float(manual_direction.get("x", 1.0)),
                            y=float(manual_direction.get("y", 0.0)),
                            z=0.0,
                        ),
                        speed,
                    )
            except Exception:
                with self.database.lock:
                    state = self.database.walker_ai_states.get(walker_id)
                    if state is not None:
                        state["speed"] = previous_speed
                if runtime is not None:
                    if previous_runtime_speed is None:
                        runtime.pop("speed", None)
                    else:
                        runtime["speed"] = previous_runtime_speed
                raise
            with self.database.lock:
                state = self.database.walker_ai_states[walker_id]
                state["state_source"] = "gui"
                state["status"] = (
                    f"Walker AI speed set to {speed:.1f} m/s."
                    if active else
                    (
                        f"Manual WalkerControl speed set to {speed:.1f} m/s."
                        if movement_status == WALKER_STATUS_MOVING else
                        f"Pedestrian idle; speed {speed:.1f} m/s saved."
                    )
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

    def _service_walker_destination(self, walker_id, walker,
                                    simulation_time):
        runtime = self.walker_ai_runtime[walker_id]
        target = runtime.get("target")
        if target is None:
            raise RuntimeError("Destination runtime target is unavailable.")
        location = walker.get_location()
        distance = self._walker_xy_distance(location, target)
        now = time.monotonic()
        if distance <= self.WALKER_DESTINATION_ARRIVAL_DISTANCE:
            self._apply_walker_fallback_control(
                walker,
                carla.Vector3D(x=0.0, y=0.0, z=0.0),
                0.0,
            )
            runtime["fallback_active"] = False
            with self.database.lock:
                state = self.database.walker_ai_states[walker_id]
                state.update({
                    "mode": WALKER_AI_STOPPED,
                    "active": False,
                    "movement_status": WALKER_STATUS_ARRIVED,
                    "status": "Arrived at the selected destination.",
                    "error": None,
                })
            return True

        last_distance = runtime.get("last_distance")
        progressed = (
            last_distance is None
            or distance
            <= last_distance - self.WALKER_DESTINATION_PROGRESS_DISTANCE
        )
        if progressed:
            runtime["last_distance"] = distance
            runtime["last_progress_at"] = now
            if simulation_time is not None:
                runtime["last_progress_sim_time"] = simulation_time

        progress_sim_time = runtime.get("last_progress_sim_time")
        if simulation_time is not None and progress_sim_time is not None:
            stalled = (
                simulation_time - progress_sim_time
                >= self.WALKER_AI_FALLBACK_STALL_SECONDS
            )
        else:
            stalled = (
                now - runtime.get("last_progress_at", now)
                >= self.WALKER_AI_FALLBACK_STALL_SECONDS
            )
        if stalled:
            self._apply_walker_fallback_control(
                walker,
                carla.Vector3D(x=0.0, y=0.0, z=0.0),
                0.0,
            )
            runtime["fallback_active"] = False
            with self.database.lock:
                state = self.database.walker_ai_states[walker_id]
                state.update({
                    "mode": WALKER_AI_STOPPED,
                    "active": False,
                    "movement_status": WALKER_STATUS_STUCK,
                    "status": (
                        "Pedestrian is Stuck; destination movement stopped."
                    ),
                    "error": None,
                })
            return True

        direction = self._walker_fallback_direction(location, target)
        runtime["fallback_direction"] = direction
        with self.database.lock:
            state = self.database.walker_ai_states[walker_id]
            saved_speed = state["speed"]
            state["movement_status"] = WALKER_STATUS_MOVING
            state["status"] = (
                f"Moving to destination ({distance:.2f} m remaining)."
            )
        speed = runtime.get("speed", saved_speed)
        runtime["speed"] = speed
        self._apply_walker_fallback_control(walker, direction, speed)
        return True

    def _service_walker_ai(self, world, world_snapshot=None):
        transition = getattr(self, "world_transition_event", None)
        if transition is not None and transition.is_set():
            return
        now = time.monotonic()
        current_world_id = self._world_id(world)
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
            with self.database.lock:
                current_speed = self.database.walker_ai_states[walker_id][
                    "speed"
                ]
            runtime = self.walker_ai_runtime.setdefault(walker_id, {
                "world_id": current_world_id,
                "target": None,
                "speed": current_speed,
                "next_check": 0.0,
                "last_distance": None,
                "last_progress_at": now,
                "last_progress_sim_time": None,
                "last_frame": None,
                "fallback_active": False,
                "fallback_direction": None,
                "fallback_origin": None,
                "fallback_last_location": None,
                "fallback_last_progress_at": None,
                "fallback_last_progress_sim_time": None,
            })
            runtime_world_id = runtime.get("world_id")
            if (
                runtime_world_id is not None
                and current_world_id is not None
                and int(runtime_world_id) != current_world_id
            ):
                self.walker_ai_runtime.pop(walker_id, None)
                continue
            if now < runtime.get("next_check", 0.0):
                continue
            runtime["next_check"] = now + self.WALKER_AI_CHECK_INTERVAL_SECONDS
            try:
                walker = self._get_managed_walker(walker_id)
                if frame is None or runtime.get("last_frame") == frame:
                    continue
                runtime["last_frame"] = frame

                with self.database.lock:
                    control_mode = self.database.walker_ai_states[
                        walker_id
                    ].get("control_mode", WALKER_CONTROL_RANDOM)
                if control_mode == WALKER_CONTROL_DESTINATION:
                    changed = self._service_walker_destination(
                        walker_id,
                        walker,
                        simulation_time,
                    )
                else:
                    changed = self._service_walker_fallback(
                        world,
                        walker_id,
                        walker,
                        simulation_time,
                    )
                if changed:
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
                    "movement_status": WALKER_STATUS_FAILED,
                })
        self._publish_walker_ai_state(
            walker_id,
            session_id=session_id,
            error=error,
        )

    def _destroy_walker_ai(self, walker_id, remove_state=False):
        try:
            with self.database.lock:
                walker = self.database.actors_db.actors.get(walker_id)
            if walker is not None and getattr(walker, "is_alive", False):
                self._apply_walker_fallback_control(
                    walker,
                    carla.Vector3D(x=0.0, y=0.0, z=0.0),
                    0.0,
                )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
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
                    was_active = bool(state.get("active"))
                    state.update({
                        "mode": WALKER_AI_STOPPED,
                        "active": False,
                        "status": "Walker AI controller was released.",
                        "controller_id": None,
                        "target": None,
                        "movement_status": (
                            WALKER_STATUS_CANCELLED
                            if was_active else
                            state.get("movement_status", WALKER_STATUS_IDLE)
                        ),
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
            self.movement_preview_world_id = self._world_id(world)
            self.movement_control_queue = data["control_queue"]
            self.manual_control_command = None
            self.manual_control_received_at = None
            self.manual_control_input_fresh = False
            self.database.movement_event_queue.put((
                "movement_camera_started",
                {
                    "session_id": session_id,
                    "world_id": self._world_id(world),
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
                    "world_id": data.get("world_id"),
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
                    "world_id": self.movement_preview_world_id,
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
                    "world_id": data.get("world_id"),
                    "message": f"Could not change camera preset: {error}",
                },
            ))

    def cleanup_movement_preview(self, reason="preview_stopped"):
        camera = self.movement_preview_camera
        publisher = self.movement_preview_publisher
        session_id = self.movement_preview_session_id
        actor_id = self.movement_preview_actor_id
        world_id = getattr(self, "movement_preview_world_id", None)

        if actor_id is not None:
            self._close_manual_session(actor_id)

        self.movement_preview_camera = None
        self.movement_preview_publisher = None
        self.movement_preview_session_id = None
        self.movement_preview_actor_id = None
        self.movement_preview_world_id = None
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
                    "world_id": world_id,
                    "reason": reason,
                },
            ))


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

    def set_vehicle_control_mode(self, session_id, actor_id, mode):
        if self._scenario_mutation_blocked("change vehicle control"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command("set_vehicle_control_mode", {
            "session_id": session_id,
            "actor_id": actor_id,
            "mode": mode,
        })

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

    def validate_movement_destination(self, session_id, actor_id, x, y, z):
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

    def get_movement_destination_map(self, session_id, actor_id):
        if self._scenario_mutation_blocked("prepare destination map"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        return self._queue_movement_command("get_movement_destination_map", {
            "session_id": session_id,
            "actor_id": int(actor_id),
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
        self.command_queue.put(("stop_movement_preview", {
            "session_id": session_id,
            "world_id": self._movement_world_id(),
            "reason": reason,
        }))
        return True
