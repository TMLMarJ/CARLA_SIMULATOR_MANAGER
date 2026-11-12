import carla
import time

from models.scenarios import ActorCategory
from scenarios.adoption import ActorAdoptionReport, AdoptionStatus
from scenarios.cleaner import StaleClearPlanError, WorldClearReport
from scenarios.converters import WEATHER_FIELDS
from scenarios.restorer import ScenarioLoadReport
from movement.vehicle import (
    AUTOPILOT,
    AUTOPILOT_DESTINATION,
    AUTOPILOT_FREE_ROAM,
    MANUAL_CONTROL,
    NO_CONTROL,
    VEHICLE_STATUS_IDLE,
    VEHICLE_STATUS_NAVIGATING,
)
from movement.walker import (
    WALKER_AI_RUNNING,
    WALKER_AI_STOPPED,
    WALKER_CONTROL_DESTINATION,
    WALKER_CONTROL_RANDOM,
    WALKER_STATUS_MOVING,
)
from utilities.functions import (
    get_pedestrian_blueprint_ids,
    parse_weather_to_dict,
)


class ScenarioRuntime:
    SCENARIO_COMMANDS = frozenset(('sync_world', 'take_actor_ownership', 'prepare_load_scenario', 'execute_load_scenario', 'save_scenario', 'prepare_clear_world', 'execute_clear_world'))

    def _handle_scenario_runtime_command(self, cmd, data, world, blueprint_library):
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
                if result.report.map_changed:
                    self._queue_map_preview_generation(world)
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
                try:
                    server_version = self.client.get_server_version()
                except Exception as error:
                    raise RuntimeError(
                        "Could not determine the CARLA server version; "
                        "scenario was not saved."
                    ) from error
                result = self.scenario_manager.save(
                    world,
                    self.database,
                    self._collect_internal_actor_ids(),
                    data["name"],
                    data["path"],
                    data["format"],
                    synchronize=self._perform_world_sync,
                    cancel_check=self.shutdown_event.is_set,
                    server_version=server_version,
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
                    self._collect_internal_actor_ids(
                        include_map_infrastructure=False,
                    ),
                    self._map_infrastructure_ids(world),
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
                        self._collect_internal_actor_ids(
                            include_map_infrastructure=False,
                        ),
                        self._map_infrastructure_ids(world),
                    )
                    self._prepare_clear_lifecycle(plan)
                    report = self.world_cleaner.execute_plan(
                        world,
                        plan,
                        self._collect_internal_actor_ids(
                            include_map_infrastructure=False,
                        ),
                        self._map_infrastructure_ids(world),
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
        return world, blueprint_library

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

        spectator_camera_id = self._spectator_runtime_status().get("camera_id")
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
        with self.database.lock:
            current_world = getattr(self.database, "world", None)
        self._tm_debug_event(
            "scenario_load_world_begin",
            world=current_world,
            map_name=str(target_map),
        )
        self.database.scenario_event_queue.put((
            "scenario_load_destructive_started",
            {"target_map": str(target_map), "map_change": True},
        ))
        self._shutdown_vehicle_controls(
            disable_autopilot=True,
            clear_paths=False,
        )
        self._shutdown_walker_ai()
        self.cleanup_movement_preview("scenario_load")
        self.cleanup_sensor_previews()
        self.cleanup_preview()
        self.simulation_manager.stop_local_ticks()
        self._publish_simulation_control_state(force=True)
        self.client.set_timeout(30.0)
        new_world = None
        new_library = None
        operation_stage = "traffic_manager_shutdown"
        try:
            self._shutdown_exclusive_traffic_manager(
                "scenario_load",
                strict=True,
            )
            self._invalidate_traffic_manager_reference()
            operation_stage = "resolve_map"
            self._tm_debug_event(
                "scenario_load_world_rpc_begin",
                world=current_world,
                map_name=str(target_map),
            )
            available_maps = self.client.get_available_maps()
            map_name = self.scenario_manager.loader.resolve_map(
                target_map,
                available_maps,
            )
            if map_name is None:
                raise RuntimeError(
                    f"Target map '{target_map}' is not available on the server."
                )
            operation_stage = "activate_map"
            new_world = self.world_service.load_with_retry(
                self.client,
                map_name,
                self.shutdown_event,
            )
            self._tm_debug_event(
                "scenario_load_world_rpc_complete",
                world=new_world,
                map_name=map_name,
            )
            if new_world is None or not self.scenario_manager.loader.maps_equal(
                target_map,
                new_world.get_map().name,
            ):
                raise RuntimeError(
                    f"CARLA did not load target map '{target_map}'."
                )
            operation_stage = "blueprint_library"
            new_library = new_world.get_blueprint_library()
            operation_stage = "map_infrastructure"
            self._capture_map_infrastructure(new_world)
            operation_stage = "world_settings"
            settings = self.simulation_manager.get_current_settings(new_world)
            self.simulation_manager.settings_changed(
                settings,
                preserve_activity=False,
            )
            weather_data = parse_weather_to_dict(new_world.get_weather())
            operation_stage = "server_metadata"
            available_maps = self.client.get_available_maps()
            try:
                server_version = self.client.get_server_version()
            except Exception:
                server_version = None
            operation_stage = "install_world_state"
            with self.database.lock:
                self.database.actors_db.clear()
                self.database.sensors_db.clear()
                self.database.vehicle_control_states.clear()
                self.database.walker_ai_states.clear()
                self.database.spawned_actor_id = None
                self.database.world = new_world
                self.database.world_id = int(new_world.id)
                self.database.world_generation = (
                    int(getattr(self.database, "world_generation", 0)) + 1
                )
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
            self._tm_debug_event(
                "scenario_load_world_complete",
                world=new_world,
                map_name=str(target_map),
            )
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
            diagnostic_error = RuntimeError(
                "Scenario Load world change failed "
                f"[stage={operation_stage}, map='{target_map}', "
                f"error={type(error).__name__}]: {error}"
            )
            if new_world is not None:
                diagnostic_error.changed_world = new_world
                diagnostic_error.blueprint_library = new_library
            raise diagnostic_error from error
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
                    remove_state=False,
                )
            elif item.category == ActorCategory.VEHICLE:
                self._release_vehicle_control(
                    item.runtime_id,
                    disable_autopilot=True,
                )

        # Do not remove control state optimistically.  The final world sync
        # removes state only for actors that actually disappeared; failed
        # destroys must leave a coherent managed actor in the local store.

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

