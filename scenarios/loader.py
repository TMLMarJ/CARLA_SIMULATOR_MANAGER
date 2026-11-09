import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scenarios.converters import (
    VEHICLE_PHYSICS_FIELDS,
    WHEEL_PHYSICS_FIELDS,
    blueprint_value_to_string,
)
from models.scenarios import (
    ActorCategory,
    ActorOwnership,
    ConfigurationStatus,
)
from scenarios.serialization import ScenarioSerializer
from scenarios.validation import ScenarioValidator


class LoadReadiness:
    READY = "ready"
    WARNING = "warning"
    BLOCKED = "blocked"
    UNVERIFIED = "unverified"


class BlueprintAvailability:
    AVAILABLE = "available"
    MISSING = "missing"
    UNVERIFIED = "unverified"


@dataclass
class ScenarioLoadActorPlan:
    scenario_id: str
    user_name: str
    category: str
    blueprint_id: str
    parent_scenario_id: str | None
    order_index: int = 0
    blueprint_status: str = BlueprintAvailability.UNVERIFIED
    status: str = LoadReadiness.READY
    applicable_attributes: list[str] = field(default_factory=list)
    read_only_attributes: list[str] = field(default_factory=list)
    unknown_attributes: list[str] = field(default_factory=list)
    invalid_attributes: list[str] = field(default_factory=list)
    restorable_physics_fields: list[str] = field(default_factory=list)
    read_only_physics_fields: list[str] = field(default_factory=list)
    unverified_physics_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    critical_problems: list[str] = field(default_factory=list)
    unverified_items: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class ScenarioLoadPlan:
    token: str
    source_path: str
    metadata: dict
    target_map: str
    current_map: str
    map_change_required: bool
    map_available: bool | None
    world_id: int | None
    actor_count: int
    source_actor_count: int = 0
    skipped_external: int = 0
    skipped_infrastructure: int = 0
    skipped_internal: int = 0
    skipped_unsupported: int = 0
    skipped_dependencies: int = 0
    skipped_actors: list[dict] = field(default_factory=list)
    creation_order: list[str] = field(default_factory=list)
    actors: list[ScenarioLoadActorPlan] = field(default_factory=list)
    critical_problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unverified_items: list[str] = field(default_factory=list)
    status: str = LoadReadiness.READY
    server_version: str | None = None
    source_sha256: str | None = None

    @property
    def ready_for_execution(self):
        return self.status in (LoadReadiness.READY, LoadReadiness.WARNING)

    def to_dict(self):
        result = asdict(self)
        result["ready_for_execution"] = self.ready_for_execution
        return result


class ScenarioLoader:
    """Builds an observational load plan without mutating the CARLA world."""

    ATTACHMENT_TYPES = {
        "Rigid",
        "SpringArm",
        "SpringArmGhost",
    }
    REQUIRED_MISSING_FIELDS = {
        "blueprint",
        "blueprint_id",
        "transform",
        "parent",
        "parent_scenario_id",
        "relative_transform",
    }
    LOAD_CATEGORIES = {
        ActorCategory.VEHICLE,
        ActorCategory.PEDESTRIAN,
        ActorCategory.STATIC_OBJECT,
        ActorCategory.SENSOR,
        ActorCategory.WALKER_AI_CONTROLLER,
    }

    def __init__(self, serializer=None, validator=None):
        self.serializer = serializer or ScenarioSerializer()
        self.validator = validator or ScenarioValidator()

    def prepare(self, path, world, available_maps, blueprint_library,
                server_version=None):
        source = Path(path)
        document, source_sha256 = self.serializer.read_snapshot(source)
        document = self.validator.validate(document)
        eligible_actors, skipped_actors = self._scenario_scope(
            document["actors"]
        )

        target_map = document["world"]["map"]
        current_map = str(world.get_map().name)
        map_change_required = not self.maps_equal(target_map, current_map)
        map_available = None if available_maps is None else any(
            self.maps_equal(target_map, available)
            for available in available_maps
        )
        plan = ScenarioLoadPlan(
            token=str(uuid.uuid4()),
            source_path=str(source.resolve()),
            metadata=dict(document["metadata"]),
            target_map=target_map,
            current_map=current_map,
            map_change_required=map_change_required,
            map_available=map_available,
            world_id=self._world_id(world),
            actor_count=len(eligible_actors),
            source_actor_count=len(document["actors"]),
            skipped_external=sum(
                item["reason"] == "external" for item in skipped_actors
            ),
            skipped_infrastructure=sum(
                item["reason"] == "world_infrastructure"
                for item in skipped_actors
            ),
            skipped_internal=sum(
                item["reason"] == "internal_preview"
                for item in skipped_actors
            ),
            skipped_unsupported=sum(
                item["reason"] == "unsupported_category"
                for item in skipped_actors
            ),
            skipped_dependencies=sum(
                item["reason"] == "excluded_parent"
                for item in skipped_actors
            ),
            skipped_actors=skipped_actors,
            server_version=(
                str(server_version) if server_version is not None else None
            ),
            source_sha256=source_sha256,
        )
        if map_available is False:
            plan.critical_problems.append(
                f"Target map '{target_map}' is not available on the server."
            )
        elif map_available is None:
            plan.unverified_items.append(
                f"Availability of target map '{target_map}' could not be "
                "verified."
            )

        if server_version is None:
            plan.unverified_items.append(
                "CARLA server version could not be verified."
            )

        if skipped_actors:
            plan.warnings.append(
                f"{len(skipped_actors)} actor(s) from the source document "
                "are outside the managed Scenario Load scope and will not "
                "be spawned."
            )

        captured_version = document["metadata"].get("carla_version")
        physics_verified = (
            server_version is not None
            and str(server_version) == str(captured_version)
        )
        if server_version and str(server_version) != str(captured_version):
            plan.unverified_items.append(
                "Scenario CARLA version "
                f"'{captured_version}' differs from server version "
                f"'{server_version}'; API compatibility must be rechecked."
            )

        ordered = self._topological_order(eligible_actors)
        actor_by_id = {
            actor["scenario_id"]: actor for actor in eligible_actors
        }
        for order_index, scenario_id in enumerate(ordered):
            actor = actor_by_id[scenario_id]
            item = self._prepare_actor(
                actor,
                actor_by_id,
                blueprint_library,
                map_change_required,
                physics_verified,
            )
            item.order_index = order_index
            plan.actors.append(item)
            plan.creation_order.append(scenario_id)
            plan.critical_problems.extend(
                f"{scenario_id}: {problem}"
                for problem in item.critical_problems
            )
            plan.warnings.extend(
                f"{scenario_id}: {warning}"
                for warning in item.warnings
            )
            plan.unverified_items.extend(
                f"{scenario_id}: {message}"
                for message in item.unverified_items
            )

        self._finish_plan_status(plan)
        return plan

    @classmethod
    def _scenario_scope(cls, actors):
        eligible = {}
        skipped = []
        for actor in actors:
            scenario_id = actor["scenario_id"]
            category = actor["category"]
            ownership = actor["ownership"]
            if category == ActorCategory.WORLD_INFRASTRUCTURE:
                reason = "world_infrastructure"
            elif (
                category == ActorCategory.INTERNAL_PREVIEW
                or ownership == ActorOwnership.INTERNAL
            ):
                reason = "internal_preview"
            elif ownership != ActorOwnership.MANAGED:
                reason = "external"
            elif category not in cls.LOAD_CATEGORIES:
                reason = "unsupported_category"
            else:
                eligible[scenario_id] = actor
                continue
            skipped.append({
                "scenario_id": scenario_id,
                "user_name": actor.get("user_name"),
                "category": category,
                "ownership": ownership,
                "reason": reason,
            })

        changed = True
        while changed:
            changed = False
            for scenario_id, actor in list(eligible.items()):
                parent_id = actor.get("parent_scenario_id")
                if parent_id is not None and parent_id not in eligible:
                    skipped.append({
                        "scenario_id": scenario_id,
                        "user_name": actor.get("user_name"),
                        "category": actor["category"],
                        "ownership": actor["ownership"],
                        "reason": "excluded_parent",
                        "parent_scenario_id": parent_id,
                    })
                    del eligible[scenario_id]
                    changed = True
        return list(eligible.values()), skipped

    def _prepare_actor(self, actor, actor_by_id, blueprint_library,
                       map_change_required, physics_verified):
        item = ScenarioLoadActorPlan(
            scenario_id=actor["scenario_id"],
            user_name=actor["user_name"],
            category=actor["category"],
            blueprint_id=actor["blueprint_id"],
            parent_scenario_id=actor.get("parent_scenario_id"),
        )
        blueprint = self._find_blueprint(
            blueprint_library,
            actor["blueprint_id"],
        )
        if map_change_required:
            item.blueprint_status = BlueprintAvailability.UNVERIFIED
            item.unverified_items.append(
                f"Blueprint '{actor['blueprint_id']}' must be rechecked "
                "after loading the target map."
            )
        elif blueprint is None:
            item.blueprint_status = BlueprintAvailability.MISSING
            item.critical_problems.append(
                f"Required blueprint '{actor['blueprint_id']}' is missing."
            )
        else:
            item.blueprint_status = BlueprintAvailability.AVAILABLE

        if blueprint is not None:
            self._inspect_attributes(
                actor.get("blueprint_attributes", {}),
                blueprint,
                item,
            )
        elif actor.get("blueprint_attributes") and map_change_required:
            item.unverified_items.append(
                "Blueprint attributes cannot be verified before the target "
                "map is active."
            )

        self._inspect_dependency(actor, actor_by_id, item)
        self._inspect_completeness(actor, item)
        self._inspect_physics(actor, item, physics_verified)
        self._inspect_control(actor, item)
        self._finish_actor_status(item)
        return item

    def _inspect_attributes(self, saved_attributes, blueprint, item):
        for name, value in saved_attributes.items():
            try:
                exists = bool(blueprint.has_attribute(name))
            except Exception as error:
                item.unverified_items.append(
                    f"Attribute '{name}' could not be inspected: {error}"
                )
                continue
            if not exists:
                item.unknown_attributes.append(name)
                item.warnings.append(
                    f"Attribute '{name}' is unknown for blueprint "
                    f"'{item.blueprint_id}' and will need to be omitted."
                )
                continue
            try:
                attribute = blueprint.get_attribute(name)
                is_modifiable = bool(attribute.is_modifiable)
            except Exception as error:
                item.unverified_items.append(
                    f"Attribute '{name}' metadata is unavailable: {error}"
                )
                continue
            if not is_modifiable:
                item.read_only_attributes.append(name)
                item.warnings.append(
                    f"Attribute '{name}' is read-only and will be omitted."
                )
            elif not self._valid_attribute_value(attribute, value):
                item.invalid_attributes.append(name)
                item.warnings.append(
                    f"Attribute '{name}' has a value incompatible with its "
                    "declared CARLA type and will need to be omitted."
                )
            else:
                item.applicable_attributes.append(name)

    def _inspect_dependency(self, actor, actor_by_id, item):
        parent_id = actor.get("parent_scenario_id")
        category = actor["category"]
        transform_space = actor["transform"].get("space")
        if parent_id is not None:
            parent = actor_by_id[parent_id]
            if category == ActorCategory.WALKER_AI_CONTROLLER and (
                parent["category"] != ActorCategory.PEDESTRIAN
            ):
                item.critical_problems.append(
                    "Walker AI controller parent must be a pedestrian."
                )
            if category == ActorCategory.SENSOR and transform_space != "relative":
                item.critical_problems.append(
                    "Attached sensor requires a relative transform."
                )
        elif transform_space == "relative":
            item.critical_problems.append(
                "Relative transform requires parent_scenario_id."
            )

        if category == ActorCategory.SENSOR and parent_id is not None:
            attachment = actor.get("attachment_type")
            if attachment is None:
                item.warnings.append(
                    "Sensor attachment type is unknown; Stage 5.2 must apply "
                    "an explicit fallback policy."
                )
            elif attachment not in self.ATTACHMENT_TYPES:
                item.warnings.append(
                    f"Sensor attachment type '{attachment}' is not recognized; "
                    "restoration will use the documented Rigid fallback."
                )

    def _inspect_completeness(self, actor, item):
        completeness = actor["completeness"]
        missing = list(completeness.get("missing_fields", ()))
        if completeness.get("status") == ConfigurationStatus.PARTIAL:
            item.warnings.append("Actor configuration is marked partial.")
        item.warnings.extend(completeness.get("warnings", ()))
        for field_name in missing:
            if field_name in self.REQUIRED_MISSING_FIELDS:
                item.critical_problems.append(
                    f"Required restoration field is missing: {field_name}."
                )
            else:
                item.warnings.append(
                    f"Optional restoration field is missing: {field_name}."
                )

    @staticmethod
    def _inspect_physics(actor, item, physics_verified):
        physics = actor.get("physics")
        if physics is None:
            if actor["category"] == ActorCategory.VEHICLE:
                item.warnings.append(
                    "Vehicle physics are unavailable; blueprint defaults "
                    "will have to be retained."
                )
            return
        scalar_and_nested_fields = list(VEHICLE_PHYSICS_FIELDS) + [
            "center_of_mass",
            "torque_curve",
            "steering_curve",
            "forward_gears",
        ]
        target_fields = (
            item.restorable_physics_fields
            if physics_verified
            else item.unverified_physics_fields
        )
        target_fields.extend(scalar_and_nested_fields)
        for index, _wheel in enumerate(physics.get("wheels", ())):
            target_fields.extend(
                f"wheels[{index}].{name}" for name in WHEEL_PHYSICS_FIELDS
            )
            item.read_only_physics_fields.append(
                f"wheels[{index}].position"
            )
        if item.read_only_physics_fields:
            item.warnings.append(
                "Wheel position is diagnostic/read-only and will not be "
                "applied during restoration."
            )
        if item.unverified_physics_fields:
            item.unverified_items.append(
                "Vehicle physics field compatibility is unverified for the "
                "connected CARLA server version."
            )

    @staticmethod
    def _inspect_control(actor, item):
        if actor["category"] not in (
            ActorCategory.VEHICLE,
            ActorCategory.PEDESTRIAN,
            ActorCategory.WALKER_AI_CONTROLLER,
        ):
            return
        control = actor.get("control")
        if control is None:
            item.warnings.append(
                "Control state is unknown and will not be inferred."
            )
            return
        if actor["category"] == ActorCategory.VEHICLE:
            if control.get("autopilot_enabled") is None:
                item.warnings.append(
                    "Autopilot state is unknown and will not be inferred."
                )
            if (
                control.get("autopilot_enabled") is True
                and control.get("traffic_manager_port") is None
            ):
                item.warnings.append(
                    "Traffic Manager port is unknown for enabled autopilot."
                )
        if actor["category"] == ActorCategory.WALKER_AI_CONTROLLER:
            if control.get("active") is None:
                item.warnings.append(
                    "Walker AI active state is unknown and will not be inferred."
                )

    @staticmethod
    def _finish_actor_status(item):
        if item.critical_problems:
            item.status = LoadReadiness.BLOCKED
        elif item.unverified_items:
            item.status = LoadReadiness.UNVERIFIED
        elif item.warnings:
            item.status = LoadReadiness.WARNING
        else:
            item.status = LoadReadiness.READY

    @staticmethod
    def _finish_plan_status(plan):
        if plan.critical_problems:
            plan.status = LoadReadiness.BLOCKED
        elif plan.unverified_items:
            plan.status = LoadReadiness.UNVERIFIED
        elif plan.warnings:
            plan.status = LoadReadiness.WARNING
        else:
            plan.status = LoadReadiness.READY

    @classmethod
    def _topological_order(cls, actors):
        actor_by_id = {actor["scenario_id"]: actor for actor in actors}
        depth_cache = {}

        def depth(scenario_id):
            if scenario_id in depth_cache:
                return depth_cache[scenario_id]
            parent_id = actor_by_id[scenario_id].get("parent_scenario_id")
            value = 0 if parent_id is None else depth(parent_id) + 1
            depth_cache[scenario_id] = value
            return value

        category_priority = {
            ActorCategory.SENSOR: 1,
            ActorCategory.WALKER_AI_CONTROLLER: 2,
        }
        indexed = list(enumerate(actors))
        indexed.sort(key=lambda entry: (
            depth(entry[1]["scenario_id"]),
            category_priority.get(entry[1]["category"], 0),
            entry[0],
        ))
        return [actor["scenario_id"] for _index, actor in indexed]

    @staticmethod
    def _find_blueprint(library, blueprint_id):
        if library is None:
            return None
        try:
            blueprint = library.find(str(blueprint_id))
        except Exception:
            return None
        if str(getattr(blueprint, "id", "")) != str(blueprint_id):
            return None
        return blueprint

    @staticmethod
    def _valid_attribute_value(attribute, value):
        try:
            blueprint_value_to_string(attribute, value)
            return True
        except (AttributeError, TypeError, ValueError):
            return False

    @staticmethod
    def map_key(map_name):
        normalized = str(map_name or "").replace("\\", "/").rstrip("/")
        return normalized.rsplit("/", 1)[-1]

    @classmethod
    def maps_equal(cls, first, second):
        first_text = str(first or "").replace("\\", "/").rstrip("/")
        second_text = str(second or "").replace("\\", "/").rstrip("/")
        return first_text == second_text or (
            bool(first_text)
            and bool(second_text)
            and cls.map_key(first_text) == cls.map_key(second_text)
        )

    @staticmethod
    def _world_id(world):
        try:
            return int(world.id)
        except (AttributeError, TypeError, ValueError):
            return None
