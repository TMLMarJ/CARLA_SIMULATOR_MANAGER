import carla
import threading
import queue
import time
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
    WALKER_AI_FALLBACK_STALL_SECONDS = 2.0
    WALKER_AI_FALLBACK_LEG_DISTANCE = 4.0

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
                                       previous_actor_id=None):
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
        }
        with self.database.lock:
            self.database.actor_operation_result = result
        return result

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

            if cmd == "shutdown":
                print("DEBUG: SHUTDOWN COMMAND RECEIVED")
                self.simulation_manager.disconnect()
                self._publish_simulation_control_state(force=True)
                self.running = False
                break

            if cmd == "spawn":
                requested_blueprint_id = str(data["blueprint_id"])
                transform = data["transform"]
                blueprint_attributes = self.database.blueprint_new_data
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
                    if self.database.ghost_physic is not None:
                        actor.apply_physics_control(self.database.ghost_physic)
                    spectator = world.get_spectator()

                    transform = actor.get_transform()
                    spectator.set_transform(transform)
                    with self.database.lock:
                        self.database.add_actor(actor, data["name"])
                        self.database.spawned_actor_id = actor.id
                    self._record_actor_operation_result(
                        "spawn",
                        requested_blueprint_id,
                        actor=actor,
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
                    )

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
                requested_blueprint_id = str(blueprint_id)
                actor_id = int(actor_id)
                old_actor = None
                new_actor = None
                try:
                    # Validate the requested ID before destroying the old actor.
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

                    if actor_id == self.movement_preview_actor_id:
                        self.cleanup_movement_preview("actor_replaced")
                    self._destroy_walker_ai(actor_id, remove_state=True)
                    self._release_vehicle_control(
                        actor_id,
                        disable_autopilot=True,
                    )
                    self.remove_sensors_for_parent(actor_id)

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
                    physics = (
                        old_actor.get_physics_control()
                        if old_actor.type_id.startswith("vehicle.")
                        else None
                    )
                    old_actor.destroy()
                    is_destroyed = wait_and_try(
                        lambda: not old_actor.is_alive,
                        timeout=5,
                        interval=0.5,
                    )
                    if not is_destroyed:
                        raise RuntimeError(
                            f"Failed to destroy actor {actor_id}."
                        )

                    with self.database.lock:
                        self.database.actors_db.actors.pop(actor_id, None)
                        self.database.actors_db.actors_db.pop(actor_id, None)
                        self.database.vehicle_control_states.pop(
                            actor_id,
                            None,
                        )
                        self.database.walker_ai_states.pop(actor_id, None)

                    transform.location.z += 0.5
                    new_actor = self._spawn_actor_checked(
                        world,
                        blueprint_library,
                        requested_blueprint_id,
                        transform,
                        try_spawn=False,
                    )
                    if physics is not None:
                        new_actor.apply_physics_control(physics)
                    with self.database.lock:
                        self.database.add_actor(
                            new_actor,
                            name,
                            **preserved_metadata,
                        )
                        self.database.spawned_actor_id = new_actor.id
                    self._record_actor_operation_result(
                        "change_model",
                        requested_blueprint_id,
                        actor=new_actor,
                        previous_actor_id=actor_id,
                    )
                except Exception as error:
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
                    self._record_actor_operation_result(
                        "change_model",
                        requested_blueprint_id,
                        error=error,
                        previous_actor_id=actor_id,
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

    def _collect_internal_actor_ids(self):
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
        return internal_actor_ids

    def _perform_world_sync(self, world, reason, publish=True):
        report = self.world_synchronizer.synchronize(
            world,
            self.database,
            self._collect_internal_actor_ids(),
            reason=reason,
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
                            "mode": AUTOPILOT,
                            "status": f"Autopilot active (port {port}).",
                            "autopilot_enabled": True,
                            "autopilot_state_source": "file",
                            "traffic_manager_port": port,
                        })
            elif mode == MANUAL_CONTROL:
                with self.database.lock:
                    self.database.vehicle_control_states[actor_id].update({
                        "mode": MANUAL_CONTROL,
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
            speed = self.database.walker_ai_states[walker_id]["speed"]
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
            })
        self._apply_walker_fallback_control(walker, direction, speed)

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
                self._start_walker_direct_control(world, walker, speed)
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
                    "fallback_active": False,
                    "fallback_direction": None,
                    "fallback_origin": None,
                    "fallback_last_location": None,
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
                        "status": "Walker AI is stopped and can be resumed.",
                        "speed": speed,
                        "target": None,
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
                runtime = self.walker_ai_runtime.get(walker_id, {})
                if runtime.get("fallback_active"):
                    direction = runtime.get("fallback_direction")
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
            with self.database.lock:
                state = self.database.walker_ai_states[walker_id]
                state["speed"] = speed
                state["state_source"] = "gui"
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
                "fallback_active": False,
                "fallback_direction": None,
                "fallback_origin": None,
                "fallback_last_location": None,
                "fallback_last_progress_at": None,
                "fallback_last_progress_sim_time": None,
            })
            if now < runtime.get("next_check", 0.0):
                continue
            runtime["next_check"] = now + self.WALKER_AI_CHECK_INTERVAL_SECONDS
            try:
                walker = self._get_managed_walker(walker_id)
                if frame is None or runtime.get("last_frame") == frame:
                    continue
                runtime["last_frame"] = frame

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
                    "modified until ownership takeover is implemented."
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

    def spawn_preview_actor(self, blueprint_id, transform, image_callback):
        if self._scenario_mutation_blocked("spawn preview actor"):
            return False
        self.command_queue.put(("spawn_preview_actor", {
            "blueprint_id": blueprint_id,
            "transform": transform,
            "image_callback": image_callback,
        }))
        return True

    def set_ghost_transform(self, transform):
        self.command_queue.put(("set_ghost_transform", transform))

    def destroy_preview_actor(self):
        self.command_queue.put(("destroy_preview_actor", None))

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

    def change_actor_model(self, blueprint_id, actor_id):
        if self._scenario_mutation_blocked("change actor model"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        self.command_queue.put(("change_actor_model", [blueprint_id, actor_id]))
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
        self.command_queue.put(("start_movement_preview", {
            "session_id": session_id,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "preset": preset,
            "publisher": publisher,
            "control_queue": control_queue,
        }))
        return True

    def set_vehicle_control_mode(self, session_id, actor_id, mode):
        if self._scenario_mutation_blocked("change vehicle control"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        self.command_queue.put(("set_vehicle_control_mode", {
            "session_id": session_id,
            "actor_id": actor_id,
            "mode": mode,
        }))
        return True

    def set_walker_ai_enabled(self, session_id, actor_id, enabled, speed):
        if self._scenario_mutation_blocked("change pedestrian control"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        self.command_queue.put(("set_walker_ai_enabled", {
            "session_id": session_id,
            "actor_id": actor_id,
            "enabled": bool(enabled),
            "speed": speed,
        }))
        return True

    def set_walker_ai_speed(self, session_id, actor_id, speed):
        if self._scenario_mutation_blocked("change pedestrian speed"):
            return False
        if not self._managed_operation_allowed(actor_id):
            return False
        self.command_queue.put(("set_walker_ai_speed", {
            "session_id": session_id,
            "actor_id": actor_id,
            "speed": speed,
        }))
        return True

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
        self.command_queue.put(("set_movement_preview_transform", {
            "session_id": session_id,
            "preset": preset,
        }))

    def stop_movement_preview(self, session_id, reason="preview_stopped"):
        self.command_queue.put(("stop_movement_preview", {
            "session_id": session_id,
            "reason": reason,
        }))
