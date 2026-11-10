import copy
import math
from datetime import datetime, timezone

from scenarios.classifier import ActorClassifier
from scenarios.converters import (
    attachment_type_to_string,
    blueprint_attributes_to_dict,
    blueprint_runtime_value_to_string,
    transform_to_dict,
    vehicle_physics_to_dict,
    weather_to_dict,
)
from models.scenarios import (
    ActorCategory,
    ActorCompleteness,
    ActorOrigin,
    ActorOwnership,
    CaptureReport,
    ConfigurationStatus,
    DataSource,
    ScenarioActor,
    ScenarioDocument,
    ScenarioMetadata,
    ScenarioWorld,
)
from movement.vehicle import (
    AUTOPILOT,
    AUTOPILOT_DESTINATION,
    AUTOPILOT_FREE_ROAM,
    NO_CONTROL,
)
from scenarios.synchronization import WorldSynchronizer
from simulation.manager import SimulationManager


class ScenarioCapture:
    SAVED_CATEGORIES = {
        ActorCategory.VEHICLE,
        ActorCategory.PEDESTRIAN,
        ActorCategory.STATIC_OBJECT,
        ActorCategory.SENSOR,
        ActorCategory.WALKER_AI_CONTROLLER,
    }

    def __init__(self, classifier=None):
        self.classifier = classifier or ActorClassifier

    def capture(self, world, database, name, internal_actor_ids=None,
                sync_report=None):
        internal_actor_ids = set(internal_actor_ids or ())
        actors = list(world.get_actors())
        with database.lock:
            actor_records = dict(database.actors_db.actors_db)
            sensor_records = dict(database.sensors_db.sensors_db)
            vehicle_states = copy.deepcopy(database.vehicle_control_states)
            walker_states = copy.deepcopy(database.walker_ai_states)

        records = dict(actor_records)
        records.update(sensor_records)
        categories = {}
        eligible_ids = set()
        for actor in actors:
            runtime_id = self.classifier.actor_id(actor)
            try:
                category = self.classifier.classify(
                    actor,
                    internal_actor_ids,
                )
            except Exception:
                category = None
            categories[runtime_id] = category
            info = records.get(runtime_id)
            if (
                info is not None
                and category in self.SAVED_CATEGORIES
                and self._is_managed_candidate(
                    actor,
                    info,
                    category,
                    records,
                    walker_states,
                )
            ):
                eligible_ids.add(runtime_id)
        scenario_ids = {
            runtime_id: info.scenario_id
            for runtime_id, info in records.items()
            if runtime_id in eligible_ids
        }
        report = CaptureReport(sync_report=dict(sync_report or {}))
        report.warnings.append(
            "Capture is observational, but CARLA does not provide an atomic "
            "world snapshot while external clients are active."
        )
        candidates = []

        for actor in sorted(actors, key=self._actor_sort_key):
            actor_id = self.classifier.actor_id(actor)
            try:
                category = categories.get(actor_id)
                if category == ActorCategory.INTERNAL_PREVIEW:
                    report.skipped_internal += 1
                    continue
                if category == ActorCategory.WORLD_INFRASTRUCTURE:
                    report.skipped_infrastructure += 1
                    continue
                info = records.get(actor_id)
                if info is None:
                    report.skipped_runtime_only += 1
                    report.errors.append({
                        "runtime_id": actor_id,
                        "message": "Actor appeared after world synchronization.",
                    })
                    continue
                if category not in self.SAVED_CATEGORIES:
                    report.skipped_unsupported += 1
                    report.warnings.append(
                        f"Actor {actor_id} ({category}) is outside the "
                        "Scenario Save scope."
                    )
                    continue
                if actor_id not in eligible_ids:
                    report.skipped_external += 1
                    continue
                status = info.configuration_status
                if status == ConfigurationStatus.RUNTIME_ONLY:
                    report.skipped_runtime_only += 1
                    continue
                if status == ConfigurationStatus.UNRESTORABLE:
                    report.skipped_unrestorable += 1
                    continue
                candidate = self._capture_actor(
                    actor,
                    info,
                    category,
                    scenario_ids,
                    vehicle_states,
                    walker_states,
                    ownership=ActorOwnership.MANAGED,
                )
                if candidate is None:
                    report.skipped_unrestorable += 1
                    if self._parent_id(actor) is not None:
                        report.skipped_dependency += 1
                        report.warnings.append(
                            f"Managed actor {actor_id} was omitted because "
                            "its parent is outside the saved managed scope or "
                            "its dependency data is incomplete."
                        )
                    continue
                candidates.append(candidate)
            except Exception as error:
                report.skipped_runtime_only += 1
                report.errors.append({
                    "runtime_id": actor_id,
                    "message": str(error),
                })

        candidates = self._remove_orphaned_dependencies(candidates, report)
        candidates.sort(
            key=lambda item: (
                item.category,
                item.user_name,
                item.scenario_id,
            )
        )
        report.saved = len(candidates)
        report.partial = sum(
            actor.completeness.status == ConfigurationStatus.PARTIAL
            for actor in candidates
        )

        map_name = str(world.get_map().name).split("/")[-1]
        world_data = ScenarioWorld(
            map=map_name,
            captured_world_id=int(world.id),
            weather=weather_to_dict(world.get_weather()),
            simulation=SimulationManager.get_current_settings(world),
        )
        metadata = ScenarioMetadata(
            name=str(name),
            created_at=(
                datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            ),
        )
        return ScenarioDocument(
            metadata=metadata,
            world=world_data,
            actors=candidates,
            capture_report=report,
        )

    def _capture_actor(self, actor, info, category, scenario_ids,
                       vehicle_states, walker_states, ownership=None):
        missing = list(info.missing_fields)
        warnings = list(info.warnings)
        status = info.configuration_status
        runtime_id = int(actor.id)
        blueprint_id = str(actor.type_id)
        if not blueprint_id:
            return None

        current_transform = actor.get_transform()
        parent_id = self._parent_id(actor)
        parent_scenario_id = scenario_ids.get(parent_id)
        ownership = ownership or info.ownership
        attachment_type = None
        data_sources = dict(info.data_sources)

        if category == ActorCategory.SENSOR:
            attachment_type = attachment_type_to_string(
                getattr(info, "attachment_type", None)
            )
            if parent_id is not None:
                if parent_scenario_id is None:
                    return None
                derived_transform = (
                    WorldSynchronizer.derive_relative_transform(actor)
                )
                relative_transform, relative_source, changed = (
                    self._select_sensor_relative_transform(
                        info,
                        derived_transform,
                    )
                )
                if relative_transform is not None:
                    transform = transform_to_dict(
                        relative_transform,
                        space="relative",
                        source=relative_source,
                    )
                    data_sources["relative_transform"] = relative_source
                    if changed:
                        warnings.append(
                            "Managed sensor relative transform differs from "
                            "the stored GUI configuration; current world "
                            "transforms were used."
                        )
                else:
                    return None
            else:
                transform = transform_to_dict(current_transform)
        else:
            transform = transform_to_dict(current_transform)

        if category == ActorCategory.WALKER_AI_CONTROLLER:
            if parent_scenario_id is None:
                return None
            status = ConfigurationStatus.PARTIAL

        try:
            attributes = blueprint_attributes_to_dict(actor)
            data_sources["blueprint_attributes"] = DataSource.SERVER
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            try:
                attributes = {
                    str(key): blueprint_runtime_value_to_string(value)
                    for key, value in sorted(
                        info.blueprint_attributes.items(),
                        key=lambda item: str(item[0]),
                    )
                }
                data_sources["blueprint_attributes"] = DataSource.GUI
                warnings.append(
                    "Runtime blueprint attributes could not be captured; "
                    f"stored GUI values were used: {error}"
                )
            except (AttributeError, TypeError, ValueError) as fallback_error:
                attributes = {}
                status = ConfigurationStatus.PARTIAL
                warnings.append(
                    "Blueprint attributes could not be captured or converted: "
                    f"{fallback_error}"
                )
                data_sources["blueprint_attributes"] = DataSource.UNKNOWN

        physics = None
        if category == ActorCategory.VEHICLE:
            try:
                physics = vehicle_physics_to_dict(
                    actor.get_physics_control(),
                    source=DataSource.SERVER,
                )
                data_sources["physics"] = DataSource.SERVER
                missing = [field for field in missing if field != "physics"]
            except (AttributeError, RuntimeError, TypeError, ValueError) as error:
                status = ConfigurationStatus.PARTIAL
                if "physics" not in missing:
                    missing.append("physics")
                warnings.append(f"Vehicle physics could not be captured: {error}")
                data_sources["physics"] = DataSource.UNKNOWN

        control = self._control_data(
            category,
            ownership,
            runtime_id,
            vehicle_states,
            walker_states,
            parent_id,
        )
        if (
            category == ActorCategory.WALKER_AI_CONTROLLER
            and not self._restorable_walker_controller(control)
        ):
            return None
        if category == ActorCategory.WALKER_AI_CONTROLLER:
            missing = [
                field for field in missing
                if field not in (
                    "control.active",
                    "control.target",
                    "control.max_speed",
                )
            ]
        if (
            control is not None
            and control.get("source") == DataSource.UNKNOWN
            and status == ConfigurationStatus.COMPLETE
        ):
            status = ConfigurationStatus.PARTIAL
            if "control" not in missing:
                missing.append("control")
        if status == ConfigurationStatus.COMPLETE and missing:
            status = ConfigurationStatus.PARTIAL
        return ScenarioActor(
            scenario_id=str(info.scenario_id),
            captured_runtime_id=runtime_id,
            user_name=str(info.user_name),
            category=category,
            blueprint_id=blueprint_id,
            origin=str(
                ActorOrigin.GUI_SPAWNED
                if category == ActorCategory.WALKER_AI_CONTROLLER
                else info.origin
            ),
            ownership=str(ownership),
            transform=transform,
            blueprint_attributes=attributes,
            physics=physics,
            control=control,
            parent_scenario_id=parent_scenario_id,
            attachment_type=attachment_type,
            data_sources={
                str(key): str(value)
                for key, value in sorted(data_sources.items())
            },
            completeness=ActorCompleteness(
                status=status,
                missing_fields=sorted(set(missing)),
                warnings=list(dict.fromkeys(warnings)),
            ),
        )

    @classmethod
    def _select_sensor_relative_transform(cls, info, derived_transform):
        stored_transform = getattr(info, "relative_transform", None)
        stored_source = info.data_sources.get(
            "relative_transform",
            DataSource.UNKNOWN,
        )
        reliable_gui_transform = (
            info.ownership == ActorOwnership.MANAGED
            and stored_transform is not None
            and stored_source in (DataSource.GUI, DataSource.FILE)
        )

        if reliable_gui_transform:
            if (
                derived_transform is None
                or cls._transforms_equivalent(
                    stored_transform,
                    derived_transform,
                )
            ):
                return stored_transform, stored_source, False
            return derived_transform, DataSource.DERIVED, True
        if derived_transform is not None:
            return derived_transform, DataSource.DERIVED, False
        if stored_transform is not None:
            return stored_transform, stored_source, False
        return None, DataSource.UNKNOWN, False

    @classmethod
    def _transforms_equivalent(cls, first, second):
        try:
            location_pairs = (
                (first.location.x, second.location.x),
                (first.location.y, second.location.y),
                (first.location.z, second.location.z),
            )
            rotation_pairs = (
                (first.rotation.pitch, second.rotation.pitch),
                (first.rotation.yaw, second.rotation.yaw),
                (first.rotation.roll, second.rotation.roll),
            )
            return (
                all(
                    math.isclose(
                        float(left),
                        float(right),
                        rel_tol=1e-9,
                        abs_tol=1e-4,
                    )
                    for left, right in location_pairs
                )
                and all(
                    cls._angle_distance(left, right) <= 1e-3
                    for left, right in rotation_pairs
                )
            )
        except (AttributeError, TypeError, ValueError):
            return False

    @staticmethod
    def _angle_distance(first, second):
        difference = (float(first) - float(second) + 180.0) % 360.0 - 180.0
        return abs(difference)

    @staticmethod
    def _control_data(category, ownership, runtime_id, vehicle_states,
                      walker_states, parent_runtime_id=None):
        if category == ActorCategory.VEHICLE:
            state = vehicle_states.get(runtime_id)
            if (
                ownership != ActorOwnership.MANAGED
                or state is None
                or state.get("autopilot_state_source") == "unknown"
            ):
                return {
                    "mode": None,
                    "autopilot_enabled": None,
                    "traffic_manager_port": None,
                    "source": DataSource.UNKNOWN,
                }
            mode = state.get("mode")
            autopilot_enabled = state.get("autopilot_enabled")
            traffic_manager_port = state.get("traffic_manager_port")
            if mode == AUTOPILOT_DESTINATION:
                mode = NO_CONTROL
                autopilot_enabled = False
                traffic_manager_port = None
            elif mode == AUTOPILOT_FREE_ROAM:
                mode = AUTOPILOT
            return {
                "mode": mode,
                "autopilot_enabled": autopilot_enabled,
                "traffic_manager_port": traffic_manager_port,
                "source": DataSource.GUI,
            }
        if category == ActorCategory.PEDESTRIAN:
            state = walker_states.get(runtime_id)
            if (
                ownership != ActorOwnership.MANAGED
                or state is None
                or state.get("state_source") == "unknown"
            ):
                return {
                    "mode": None,
                    "active": None,
                    "speed": None,
                    "random_navigation": None,
                    "source": DataSource.UNKNOWN,
                }
            return {
                "mode": state.get("mode"),
                "active": state.get("active"),
                "speed": state.get("speed"),
                "random_navigation": state.get("random_navigation"),
                "source": DataSource.GUI,
            }
        if category == ActorCategory.WALKER_AI_CONTROLLER:
            state = walker_states.get(parent_runtime_id)
            if (
                ownership == ActorOwnership.MANAGED
                and state is not None
                and state.get("controller_id") == runtime_id
            ):
                return {
                    "active": state.get("active"),
                    "target": copy.deepcopy(state.get("target")),
                    "max_speed": state.get("speed"),
                    "source": DataSource.GUI,
                }
            return {
                "active": None,
                "target": None,
                "max_speed": None,
                "source": DataSource.UNKNOWN,
            }
        return None

    @staticmethod
    def _restorable_walker_controller(control):
        control = control or {}
        target = control.get("target")
        speed = control.get("max_speed")
        return (
            control.get("active") is True
            and isinstance(target, dict)
            and all(axis in target for axis in ("x", "y", "z"))
            and isinstance(speed, (int, float))
            and not isinstance(speed, bool)
            and speed > 0
        )

    def _is_managed_candidate(self, actor, info, category, records,
                              walker_states):
        if info.ownership == ActorOwnership.MANAGED:
            return True
        if category != ActorCategory.WALKER_AI_CONTROLLER:
            return False
        parent_id = self._parent_id(actor)
        parent_info = records.get(parent_id)
        state = walker_states.get(parent_id)
        return (
            parent_info is not None
            and parent_info.ownership == ActorOwnership.MANAGED
            and state is not None
            and state.get("controller_id") == self.classifier.actor_id(actor)
        )

    @staticmethod
    def _remove_orphaned_dependencies(candidates, report):
        changed = True
        remaining = list(candidates)
        while changed:
            changed = False
            saved_ids = {actor.scenario_id for actor in remaining}
            filtered = []
            for actor in remaining:
                if (
                    actor.parent_scenario_id is not None
                    and actor.parent_scenario_id not in saved_ids
                ):
                    report.skipped_unrestorable += 1
                    report.skipped_dependency += 1
                    report.errors.append({
                        "runtime_id": actor.captured_runtime_id,
                        "message": "Parent actor is not restorable.",
                    })
                    changed = True
                else:
                    filtered.append(actor)
            remaining = filtered
        return remaining

    @staticmethod
    def _parent_id(actor):
        try:
            parent = actor.parent
            return None if parent is None else int(parent.id)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    @staticmethod
    def _actor_sort_key(actor):
        try:
            return int(actor.id)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return 2 ** 63
