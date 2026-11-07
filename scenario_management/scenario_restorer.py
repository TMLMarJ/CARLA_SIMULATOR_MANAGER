from dataclasses import asdict, dataclass, field

from scenario_management.converters import (
    apply_vehicle_physics_from_dict,
    blueprint_attribute_current_value,
    blueprint_attribute_kind,
    blueprint_value_to_string,
    transform_from_dict,
)
from scenario_management.models import (
    ActorCategory,
    ActorOrigin,
    ActorOwnership,
    DataSource,
)
from scenario_management.world_cleaner import ClearMode


class StaleLoadPlanError(RuntimeError):
    pass


@dataclass
class ScenarioLoadReport:
    name: str
    source_path: str
    source_map: str
    target_map: str
    map_changed: bool
    planned: int
    source_actors: int = 0
    scope_skipped: int = 0
    restored: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    clear_report: dict = field(default_factory=dict)
    sync_report: dict = field(default_factory=dict)
    status: str = "failed"

    def add_error(self, stage, message, scenario_id=None, user_name=None,
                  blueprint_id=None):
        self.errors.append({
            "scenario_id": scenario_id,
            "user_name": user_name,
            "blueprint_id": blueprint_id,
            "stage": str(stage),
            "message": str(message),
        })

    def to_dict(self):
        return asdict(self)

    @classmethod
    def cancelled(cls, plan):
        return cls(
            name=str(plan.metadata.get("name", "")),
            source_path=plan.source_path,
            source_map=plan.current_map,
            target_map=plan.target_map,
            map_changed=False,
            planned=plan.actor_count,
            source_actors=plan.source_actor_count,
            scope_skipped=len(plan.skipped_actors),
            status="cancelled",
            warnings=["Scenario Load was cancelled by the user."],
        )


@dataclass
class ScenarioLoadExecutionResult:
    report: ScenarioLoadReport
    world: object
    blueprint_library: object


class ScenarioRestorer:
    """Executes one validated replacement of the current CARLA world."""

    VEHICLE_SPAWN_Z_OFFSET = 0.2

    def __init__(self, loader, serializer, validator, world_cleaner,
                 carla_module):
        self.loader = loader
        self.serializer = serializer
        self.validator = validator
        self.world_cleaner = world_cleaner
        self.carla = carla_module

    def execute(self, plan, confirmed, world, database, blueprint_library,
                available_maps, server_version, internal_actor_ids,
                before_clear, change_world, apply_world_settings,
                restore_control, synchronize):
        if not confirmed:
            return ScenarioLoadExecutionResult(
                ScenarioLoadReport.cancelled(plan),
                world,
                blueprint_library,
            )

        report = ScenarioLoadReport(
            name=str(plan.metadata.get("name", "")),
            source_path=plan.source_path,
            source_map=plan.current_map,
            target_map=plan.target_map,
            map_changed=False,
            planned=plan.actor_count,
            source_actors=plan.source_actor_count,
            scope_skipped=len(plan.skipped_actors),
        )
        if plan.skipped_actors:
            report.warnings.append(
                f"{len(plan.skipped_actors)} source actor(s) were omitted "
                "by the managed Scenario Load scope."
            )
        destructive_started = False
        operation_stage = "preflight"
        try:
            if self._world_id(world) != plan.world_id:
                raise StaleLoadPlanError(
                    "The CARLA world episode changed after Load preflight."
                )
            fresh_plan = self.loader.prepare(
                plan.source_path,
                world,
                available_maps,
                blueprint_library,
                server_version=server_version,
            )
            self._verify_source(plan, fresh_plan)
            self._require_executable_preflight(
                fresh_plan,
                allow_map_dependent=fresh_plan.map_change_required,
            )
            document, digest = self.serializer.read_snapshot(plan.source_path)
            self.validator.validate(document)
            if digest != plan.source_sha256:
                raise StaleLoadPlanError(
                    "Scenario file changed after Load preflight."
                )

            if fresh_plan.map_change_required:
                destructive_started = True
                operation_stage = "map_change"
                world, blueprint_library, available_maps, server_version = (
                    change_world(fresh_plan.target_map)
                )
                report.map_changed = True
                post_plan = self.loader.prepare(
                    plan.source_path,
                    world,
                    available_maps,
                    blueprint_library,
                    server_version=server_version,
                )
                self._verify_source(plan, post_plan)
                try:
                    self._require_executable_preflight(post_plan)
                except Exception as error:
                    report.add_error("post_map_validation", error)
                    return self._finish(report, world, blueprint_library,
                                        synchronize)
                target_clear = self.world_cleaner.prepare_plan(
                    world,
                    database,
                    ClearMode.SCENARIO_REPLACE,
                    internal_actor_ids(),
                )
                if target_clear.planned_count:
                    report.add_error(
                        "post_map_validation",
                        "The newly loaded map contains qualifying dynamic "
                        "actors that were not part of the approved plan. "
                        "Restoration was stopped without deleting them.",
                    )
                    return self._finish(report, world, blueprint_library,
                                        synchronize)
                execution_plan = post_plan
            else:
                operation_stage = "clear"
                clear_plan = self.world_cleaner.prepare_plan(
                    world,
                    database,
                    ClearMode.SCENARIO_REPLACE,
                    internal_actor_ids(),
                )
                self.world_cleaner.validate_plan(
                    world,
                    clear_plan,
                    internal_actor_ids(),
                )
                unsafe_cascade = [
                    item for item in clear_plan.cascade_dependencies
                    if item.category not in (
                        ActorCategory.SENSOR,
                        ActorCategory.WALKER_AI_CONTROLLER,
                    )
                ]
                if unsafe_cascade:
                    report.add_error(
                        "clear",
                        "Selective Clear would cascade into preserved actors; "
                        "Load was stopped before modifying the world.",
                    )
                    return self._finish(
                        report,
                        world,
                        blueprint_library,
                    )
                destructive_started = True
                before_clear(clear_plan)
                clear_report = self.world_cleaner.execute_plan(
                    world,
                    clear_plan,
                    internal_actor_ids(),
                )
                report.clear_report = clear_report.to_dict()
                if clear_report.status != "success":
                    report.add_error(
                        "clear",
                        "Clear World did not complete successfully; actor "
                        "restoration was not started.",
                    )
                    return self._finish(report, world, blueprint_library,
                                        synchronize)
                remaining_plan = self.world_cleaner.prepare_plan(
                    world,
                    database,
                    ClearMode.SCENARIO_REPLACE,
                    internal_actor_ids(),
                )
                if remaining_plan.planned_count:
                    report.clear_report["remaining_after_clear"] = (
                        remaining_plan.planned_count
                    )
                    report.add_error(
                        "clear",
                        "Qualifying actors appeared or remained after the "
                        "approved Clear operation. Load did not expand the "
                        "deletion scope and actor restoration was stopped.",
                    )
                    return self._finish(report, world, blueprint_library,
                                        synchronize)
                execution_plan = fresh_plan

            operation_stage = "world_settings"
            try:
                apply_world_settings(world, document["world"])
            except Exception as error:
                report.add_error("world_settings", error)
                return self._finish(report, world, blueprint_library,
                                    synchronize)

            self._restore_actors(
                document,
                execution_plan,
                world,
                blueprint_library,
                database,
                restore_control,
                report,
            )
            return self._finish(report, world, blueprint_library, synchronize)
        except StaleLoadPlanError as error:
            report.add_error("preflight", error)
        except Exception as error:
            changed_world = getattr(error, "changed_world", None)
            if changed_world is not None:
                world = changed_world
                blueprint_library = getattr(
                    error,
                    "blueprint_library",
                    blueprint_library,
                )
                report.map_changed = True
            report.add_error(
                operation_stage,
                error,
            )
        return self._finish(
            report,
            world,
            blueprint_library,
            synchronize if destructive_started else None,
        )

    def _restore_actors(self, document, load_plan, world, blueprint_library,
                        database, restore_control, report):
        actor_data = {
            actor["scenario_id"]: actor for actor in document["actors"]
        }
        actor_plans = {item.scenario_id: item for item in load_plan.actors}
        runtime_by_scenario = {}
        failed_scenarios = set()

        for scenario_id in load_plan.creation_order:
            data = actor_data[scenario_id]
            parent_id = data.get("parent_scenario_id")
            if parent_id in failed_scenarios or (
                parent_id is not None and parent_id not in runtime_by_scenario
            ):
                report.skipped += 1
                failed_scenarios.add(scenario_id)
                report.add_error(
                    (
                        "sensor_attachment"
                        if data["category"] == ActorCategory.SENSOR
                        else "actor_spawn"
                    ),
                    "Parent actor was not restored.",
                    scenario_id,
                    data.get("user_name"),
                    data.get("blueprint_id"),
                )
                continue
            if (
                data["category"] == ActorCategory.WALKER_AI_CONTROLLER
                and not self._walker_controller_is_restorable(data)
            ):
                report.skipped += 1
                failed_scenarios.add(scenario_id)
                report.warnings.append(
                    f"{scenario_id}: Walker AI controller was skipped because "
                    "its active state, target or speed is incomplete."
                )
                continue

            actor = None
            try:
                actor, spawn_warnings = self._spawn_one(
                    world,
                    blueprint_library,
                    data,
                    runtime_by_scenario.get(parent_id),
                    actor_plans[scenario_id],
                )
                report.warnings.extend(
                    f"{scenario_id}: {warning}" for warning in spawn_warnings
                )
                self._register_actor(
                    database,
                    actor,
                    data,
                    runtime_by_scenario.get(parent_id),
                    spawn_warnings,
                )
                runtime_by_scenario[scenario_id] = actor
                report.restored += 1
            except Exception as error:
                if actor is not None:
                    self._discard_actor(database, actor)
                runtime_by_scenario.pop(scenario_id, None)
                report.failed += 1
                failed_scenarios.add(scenario_id)
                report.add_error(
                    (
                        "sensor_attachment"
                        if data["category"] == ActorCategory.SENSOR
                        and parent_id is not None
                        else "actor_spawn"
                    ),
                    error,
                    scenario_id,
                    data.get("user_name"),
                    data.get("blueprint_id"),
                )
                continue

            if data["category"] == ActorCategory.VEHICLE and data.get("physics"):
                try:
                    warnings = apply_vehicle_physics_from_dict(
                        actor,
                        data["physics"],
                        self.carla,
                    )
                    report.warnings.extend(
                        f"{scenario_id}: {warning}" for warning in warnings
                    )
                except Exception as error:
                    report.add_error(
                        "physics",
                        error,
                        scenario_id,
                        data.get("user_name"),
                        data.get("blueprint_id"),
                    )

            try:
                warnings = restore_control(
                    world,
                    actor,
                    data,
                    runtime_by_scenario.get(parent_id),
                )
                report.warnings.extend(
                    f"{scenario_id}: {warning}" for warning in warnings
                )
            except Exception as error:
                report.add_error(
                    "control",
                    error,
                    scenario_id,
                    data.get("user_name"),
                    data.get("blueprint_id"),
                )
                if data["category"] == ActorCategory.WALKER_AI_CONTROLLER:
                    self._discard_actor(database, actor)
                    runtime_by_scenario.pop(scenario_id, None)
                    report.restored -= 1
                    report.failed += 1
                    failed_scenarios.add(scenario_id)

    def _spawn_one(self, world, library, data, parent, actor_plan):
        blueprint_id = str(data["blueprint_id"])
        blueprint = library.find(blueprint_id)
        if str(getattr(blueprint, "id", "")) != blueprint_id:
            raise RuntimeError("Blueprint lookup returned a different ID.")
        originals = {}
        applied = {}
        warnings = []
        try:
            for name, value in data.get("blueprint_attributes", {}).items():
                if name not in actor_plan.applicable_attributes:
                    warnings.append(
                        f"Blueprint attribute '{name}' was omitted."
                    )
                    continue
                attribute = None
                try:
                    attribute = blueprint.get_attribute(name)
                    originals[name] = blueprint_attribute_current_value(
                        attribute
                    )
                    converted = blueprint_value_to_string(attribute, value)
                    blueprint.set_attribute(name, converted)
                    applied[name] = (attribute, converted, value)
                except Exception as error:
                    attribute_kind = (
                        blueprint_attribute_kind(attribute)
                        if attribute is not None else "unknown"
                    )
                    warnings.append(
                        f"Blueprint '{blueprint_id}' attribute '{name}' "
                        f"(type={attribute_kind}, "
                        f"input={value!r}) was not applied: {error}"
                    )
            transform = self._spawn_transform(data)
            kwargs = {}
            if parent is not None:
                attachment_name = data.get("attachment_type")
                if attachment_name not in (
                    "Rigid", "SpringArm", "SpringArmGhost"
                ):
                    attachment_name = "Rigid"
                    warnings.append(
                        "Unknown attachment type; Rigid fallback was used."
                    )
                kwargs = {
                    "attach_to": parent,
                    "attachment_type": getattr(
                        self.carla.AttachmentType,
                        attachment_name,
                    ),
                }
            actor = world.spawn_actor(blueprint, transform, **kwargs)
            if actor is None:
                raise RuntimeError("CARLA returned no actor for spawn request.")
            if str(getattr(actor, "type_id", "")) != str(data["blueprint_id"]):
                try:
                    actor.destroy()
                finally:
                    raise RuntimeError("Spawned actor has an unexpected blueprint.")
            runtime_attributes = dict(getattr(actor, "attributes", {}) or {})
            for name, (attribute, expected, source_value) in applied.items():
                if name not in runtime_attributes:
                    continue
                try:
                    actual = blueprint_value_to_string(
                        attribute,
                        runtime_attributes[name],
                    )
                    if actual != expected:
                        warnings.append(
                            f"Blueprint '{blueprint_id}' attribute '{name}' "
                            f"(type={blueprint_attribute_kind(attribute)}, "
                            f"input={source_value!r}) verification mismatch: "
                            f"requested {expected!r}, CARLA reports {actual!r}."
                        )
                except Exception as error:
                    warnings.append(
                        f"Blueprint '{blueprint_id}' attribute '{name}' "
                        f"(type={blueprint_attribute_kind(attribute)}, "
                        f"input={source_value!r}) could not be verified: {error}"
                    )
            return actor, warnings
        finally:
            for name, value in originals.items():
                try:
                    blueprint.set_attribute(name, value)
                except Exception:
                    pass

    def _spawn_transform(self, data):
        transform = transform_from_dict(data["transform"], self.carla)
        if str(data.get("blueprint_id", "")).startswith("vehicle."):
            transform.location.z += self.VEHICLE_SPAWN_Z_OFFSET
        return transform

    def _register_actor(self, database, actor, data, parent, runtime_warnings):
        completeness = data["completeness"]
        metadata = {
            "scenario_id": data["scenario_id"],
            "world_id": int(actor.get_world().id),
            "ownership": ActorOwnership.MANAGED,
            "origin": ActorOrigin.SCENARIO_LOADED,
            "category": data["category"],
            "configuration_status": completeness["status"],
            "missing_fields": list(completeness.get("missing_fields", ())),
            "warnings": list(completeness.get("warnings", ()))
                        + list(runtime_warnings),
            "parent_scenario_id": data.get("parent_scenario_id"),
            "data_sources": {
                **dict(data.get("data_sources", {})),
                "scenario": DataSource.FILE,
                "user_name": DataSource.FILE,
            },
        }
        if data["category"] == ActorCategory.SENSOR:
            with database.lock:
                database.add_sensor(
                    actor,
                    None if parent is None else int(parent.id),
                    data["user_name"],
                    data["blueprint_id"],
                    transform_from_dict(data["transform"], self.carla),
                    data.get("blueprint_attributes", {}),
                    attachment_type=data.get("attachment_type") or "Rigid",
                    **metadata,
                )
        else:
            with database.lock:
                database.add_actor(
                    actor,
                    data["user_name"],
                    blueprint_attributes=data.get("blueprint_attributes", {}),
                    **metadata,
                )

    @staticmethod
    def _discard_actor(database, actor):
        try:
            if getattr(actor, "is_alive", False):
                actor.destroy()
        finally:
            with database.lock:
                if str(getattr(actor, "type_id", "")).startswith("sensor."):
                    database.sensors_db.remove_sensor(actor.id)
                else:
                    database.actors_db.remove_actor(actor.id)
                    database.vehicle_control_states.pop(actor.id, None)
                    database.walker_ai_states.pop(actor.id, None)

    @staticmethod
    def _walker_controller_is_restorable(data):
        control = data.get("control") or {}
        target = control.get("target")
        speed = control.get("max_speed")
        return (
            control.get("active") is True
            and isinstance(target, dict)
            and all(name in target for name in ("x", "y", "z"))
            and isinstance(speed, (int, float))
            and not isinstance(speed, bool)
            and speed > 0
        )

    @staticmethod
    def _verify_source(approved, fresh):
        if approved.source_sha256 != fresh.source_sha256:
            raise StaleLoadPlanError(
                "Scenario file changed after Load preflight."
            )
        if approved.target_map != fresh.target_map:
            raise StaleLoadPlanError(
                "Scenario target map changed after Load preflight."
            )

    @staticmethod
    def _require_executable_preflight(plan, allow_map_dependent=False):
        if plan.critical_problems:
            raise RuntimeError("; ".join(plan.critical_problems))
        if not plan.unverified_items:
            return
        allowed_markers = (
            "must be rechecked after loading the target map",
            "cannot be verified before the target map is active",
        )
        unresolved = [
            item for item in plan.unverified_items
            if not allow_map_dependent
            or not any(marker in item for marker in allowed_markers)
        ]
        if unresolved:
            raise RuntimeError(
                "Load preflight contains unresolved items: "
                + "; ".join(unresolved)
            )

    @staticmethod
    def _finish(report, world, blueprint_library, synchronize=None):
        if synchronize is not None:
            try:
                report.sync_report = synchronize(world)
            except Exception as error:
                report.add_error("synchronization", error)
        if report.status != "cancelled":
            if report.errors:
                report.status = (
                    "partial_success" if report.restored else "failed"
                )
            elif (
                report.skipped
                or report.scope_skipped
                or report.failed
                or report.warnings
            ):
                report.status = "partial_success"
            else:
                report.status = "success"
        return ScenarioLoadExecutionResult(report, world, blueprint_library)

    @staticmethod
    def _world_id(world):
        try:
            return int(world.id)
        except (AttributeError, TypeError, ValueError) as error:
            raise RuntimeError("CARLA world has no valid episode ID.") from error
