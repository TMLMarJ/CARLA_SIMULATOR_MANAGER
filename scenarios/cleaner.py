import time
import uuid
from dataclasses import asdict, dataclass, field

from scenarios.classifier import ActorClassifier
from models.scenarios import (
    ActorCategory,
    ActorOwnership,
)


class ClearMode:
    MANAGED = "managed"
    ENTIRE_WORLD = "entire_world"
    SCENARIO_REPLACE = "scenario_replace"

    @classmethod
    def normalize(cls, mode):
        normalized = str(mode or "").strip().lower()
        aliases = {
            "managed actors only": cls.MANAGED,
            "entire world": cls.ENTIRE_WORLD,
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in (
            cls.MANAGED,
            cls.ENTIRE_WORLD,
            cls.SCENARIO_REPLACE,
        ):
            raise ValueError(
                "Clear mode must be Managed Actors Only, Entire World or "
                "the internal scenario_replace policy."
            )
        return normalized


class StaleClearPlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActorIdentity:
    runtime_id: int
    type_id: str
    parent_runtime_id: int | None
    role_name: str
    attributes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ClearPlanItem:
    runtime_id: int
    category: str
    ownership: str
    type_id: str
    parent_runtime_id: int | None
    identity: ActorIdentity

    def to_dict(self):
        return {
            "runtime_id": self.runtime_id,
            "category": self.category,
            "ownership": self.ownership,
            "type_id": self.type_id,
            "parent_runtime_id": self.parent_runtime_id,
        }


@dataclass
class WorldClearPlan:
    token: str
    world_id: int
    mode: str
    items: list[ClearPlanItem] = field(default_factory=list)
    cascade_dependencies: list[ClearPlanItem] = field(default_factory=list)
    observed_children: dict[int, ActorIdentity] = field(default_factory=dict)
    skipped: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def planned_count(self):
        return len(self.items)

    def to_dict(self):
        counts = {
            ActorCategory.VEHICLE: 0,
            ActorCategory.PEDESTRIAN: 0,
            ActorCategory.STATIC_OBJECT: 0,
            ActorCategory.SENSOR: 0,
            ActorCategory.WALKER_AI_CONTROLLER: 0,
        }
        for item in self.items:
            if item.category in counts:
                counts[item.category] += 1
        external_ids = {
            item.runtime_id
            for item in self.items + self.cascade_dependencies
            if item.ownership == ActorOwnership.EXTERNAL
        }
        return {
            "token": self.token,
            "world_id": self.world_id,
            "mode": self.mode,
            "planned_count": self.planned_count,
            "vehicles": counts[ActorCategory.VEHICLE],
            "pedestrians": counts[ActorCategory.PEDESTRIAN],
            "static_objects": counts[ActorCategory.STATIC_OBJECT],
            "sensors": counts[ActorCategory.SENSOR],
            "walker_ai_controllers": counts[
                ActorCategory.WALKER_AI_CONTROLLER
            ],
            "external_count": len(external_ids),
            "skipped_infrastructure": sum(
                entry.get("reason") == "world_infrastructure"
                for entry in self.skipped
            ),
            "preserved_external_static": sum(
                entry.get("reason") == "external_static_preserved"
                for entry in self.skipped
            ),
            "cascade_external_count": len(self.cascade_dependencies),
            "cascade_dependencies": [
                item.to_dict() for item in self.cascade_dependencies
            ],
            "warnings": list(self.warnings),
            "skipped": list(self.skipped),
            "items": [item.to_dict() for item in self.items],
            "world_already_clear": not self.items,
        }


@dataclass
class WorldClearReport:
    mode: str
    planned_count: int
    removed: int = 0
    already_missing: int = 0
    skipped: int = 0
    failed: int = 0
    removed_vehicles: int = 0
    removed_pedestrians: int = 0
    removed_managed_static_objects: int = 0
    removed_sensors: int = 0
    removed_walker_ai_controllers: int = 0
    preserved_external_static: int = 0
    skipped_infrastructure: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    sync_report: dict = field(default_factory=dict)
    status: str = "success"

    def to_dict(self):
        return asdict(self)

    @classmethod
    def cancelled(cls, mode, planned_count, warning=None):
        return cls(
            mode=mode,
            planned_count=planned_count,
            warnings=[warning] if warning else [],
            status="cancelled",
        )


class WorldCleaner:
    DESTROY_CONFIRM_TIMEOUT = 1.0
    DESTROY_CONFIRM_INTERVAL = 0.05

    CLEARABLE_CATEGORIES = {
        ActorCategory.VEHICLE,
        ActorCategory.PEDESTRIAN,
        ActorCategory.STATIC_OBJECT,
        ActorCategory.SENSOR,
        ActorCategory.WALKER_AI_CONTROLLER,
    }

    def __init__(self, classifier=None):
        self.classifier = classifier or ActorClassifier

    def prepare_plan(self, world, database, mode, internal_actor_ids=None,
                     infrastructure_actor_ids=None):
        mode = ClearMode.normalize(mode)
        world_id = self._world_id(world)
        internal_actor_ids = set(internal_actor_ids or ())
        infrastructure_actor_ids = set(infrastructure_actor_ids or ())
        actors = list(world.get_actors())
        with database.lock:
            records = dict(database.actors_db.actors_db)
            records.update(database.sensors_db.sensors_db)
            actor_refs = dict(database.actors_db.actors)
            actor_refs.update(database.sensors_db.sensors)

        inventory = {}
        dependency_inventory = {}
        skipped = []
        for actor in actors:
            actor_id = self.classifier.actor_id(actor)
            if actor_id is None:
                skipped.append({
                    "runtime_id": None,
                    "reason": "invalid_runtime_id",
                })
                continue
            if not self._is_alive(actor):
                skipped.append({
                    "runtime_id": actor_id,
                    "reason": "already_missing",
                })
                continue
            category = self.classifier.classify(actor, internal_actor_ids)
            if (
                actor_id in infrastructure_actor_ids
                and category == ActorCategory.STATIC_OBJECT
            ):
                skipped.append({
                    "runtime_id": actor_id,
                    "reason": "world_infrastructure",
                })
                continue
            if category == ActorCategory.INTERNAL_PREVIEW:
                skipped.append({
                    "runtime_id": actor_id,
                    "reason": "internal_preview",
                })
                continue
            if category == ActorCategory.WORLD_INFRASTRUCTURE:
                skipped.append({
                    "runtime_id": actor_id,
                    "reason": "world_infrastructure",
                })
                continue
            record = records.get(actor_id)
            ownership = ActorOwnership.EXTERNAL
            if self._record_matches_actor(
                record,
                actor_refs.get(actor_id),
                actor,
                world_id,
            ):
                ownership = getattr(
                    record,
                    "ownership",
                    ActorOwnership.EXTERNAL,
                )
            item = self._plan_item(actor, category, ownership)
            dependency_inventory[actor_id] = item
            if category not in self.CLEARABLE_CATEGORIES:
                skipped.append({
                    "runtime_id": actor_id,
                    "reason": "unsupported_category",
                })
                continue
            inventory[actor_id] = item

        if mode == ClearMode.ENTIRE_WORLD:
            target_ids = set(inventory)
        elif mode == ClearMode.SCENARIO_REPLACE:
            target_ids = {
                actor_id
                for actor_id, item in inventory.items()
                if (
                    item.category == ActorCategory.PEDESTRIAN
                    or (
                        item.category == ActorCategory.VEHICLE
                        and not (
                            item.ownership == ActorOwnership.EXTERNAL
                            and self.classifier.is_ego_role_name(
                                item.identity.role_name
                            )
                        )
                    )
                    or (
                        item.category == ActorCategory.STATIC_OBJECT
                        and item.ownership == ActorOwnership.MANAGED
                    )
                    or (
                        item.category == ActorCategory.SENSOR
                        and item.ownership == ActorOwnership.MANAGED
                    )
                )
            }
            changed = True
            while changed:
                changed = False
                for actor_id, item in inventory.items():
                    if actor_id in target_ids:
                        continue
                    if (
                        item.category in (
                            ActorCategory.SENSOR,
                            ActorCategory.WALKER_AI_CONTROLLER,
                        )
                        and item.parent_runtime_id in target_ids
                    ):
                        target_ids.add(actor_id)
                        changed = True
            skipped.extend(
                {
                    "runtime_id": actor_id,
                    "reason": "external_static_preserved",
                }
                for actor_id, item in sorted(inventory.items())
                if (
                    item.category == ActorCategory.STATIC_OBJECT
                    and item.ownership == ActorOwnership.EXTERNAL
                )
            )
            skipped.extend(
                {
                    "runtime_id": actor_id,
                    "reason": "external_ego_preserved",
                }
                for actor_id, item in sorted(inventory.items())
                if (
                    item.category == ActorCategory.VEHICLE
                    and item.ownership == ActorOwnership.EXTERNAL
                    and self.classifier.is_ego_role_name(
                        item.identity.role_name
                    )
                )
            )
        else:
            target_ids = {
                actor_id
                for actor_id, item in inventory.items()
                if item.ownership == ActorOwnership.MANAGED
            }
            target_ids.update(
                actor_id
                for actor_id, item in inventory.items()
                if (
                    item.category == ActorCategory.WALKER_AI_CONTROLLER
                    and item.parent_runtime_id in target_ids
                )
            )

        items = [inventory[actor_id] for actor_id in sorted(target_ids)]
        cascade_dependencies = [
            item
            for actor_id, item in sorted(dependency_inventory.items())
            if (
                actor_id not in target_ids
                and item.parent_runtime_id in target_ids
                and item.ownership == ActorOwnership.EXTERNAL
            )
        ]
        observed_children = {
            actor_id: item.identity
            for actor_id, item in dependency_inventory.items()
            if item.parent_runtime_id in target_ids
        }
        warnings = []
        if mode == ClearMode.ENTIRE_WORLD:
            warnings.append(
                "Entire World also removes qualifying actors created by "
                "other CARLA clients."
            )
        elif mode == ClearMode.SCENARIO_REPLACE:
            preserved_static = sum(
                entry.get("reason") == "external_static_preserved"
                for entry in skipped
            )
            preserved_ego = sum(
                entry.get("reason") == "external_ego_preserved"
                for entry in skipped
            )
            warnings.append(
                "Scenario replacement selectively removes vehicles and "
                "pedestrians, managed static objects, and dependent sensors "
                "or controllers; external Ego vehicles are preserved."
            )
            if preserved_static:
                warnings.append(
                    f"{preserved_static} external static object(s) will be "
                    "preserved on the current map."
                )
            if preserved_ego:
                warnings.append(
                    f"{preserved_ego} external Ego vehicle(s) will be "
                    "preserved during Scenario Load."
                )
        if cascade_dependencies:
            warnings.append(
                f"{len(cascade_dependencies)} external child actor(s) may "
                "be destroyed by CARLA together with a managed parent."
            )
        return WorldClearPlan(
            token=str(uuid.uuid4()),
            world_id=world_id,
            mode=mode,
            items=items,
            cascade_dependencies=cascade_dependencies,
            observed_children=observed_children,
            skipped=skipped,
            warnings=warnings,
        )

    def validate_plan(self, world, plan, internal_actor_ids=None,
                      infrastructure_actor_ids=None):
        if self._world_id(world) != plan.world_id:
            raise StaleClearPlanError(
                "The CARLA world episode changed after preflight. Run "
                "preflight again."
            )
        internal_actor_ids = set(internal_actor_ids or ())
        infrastructure_actor_ids = set(infrastructure_actor_ids or ())
        current = {
            self.classifier.actor_id(actor): actor
            for actor in list(world.get_actors())
            if (
                self.classifier.actor_id(actor) is not None
                and self._is_alive(actor)
            )
        }
        expected = {
            item.runtime_id: item.identity
            for item in plan.items + plan.cascade_dependencies
        }
        for actor_id, identity in expected.items():
            actor = current.get(actor_id)
            if actor is None:
                continue
            if self._identity(actor) != identity:
                raise StaleClearPlanError(
                    f"Actor {actor_id} changed identity after preflight. "
                    "Run preflight again."
                )

        target_ids = {item.runtime_id for item in plan.items}
        for actor_id, actor in current.items():
            category = self.classifier.classify(actor, internal_actor_ids)
            if (
                actor_id in infrastructure_actor_ids
                and category == ActorCategory.STATIC_OBJECT
            ):
                continue
            if category in (
                ActorCategory.INTERNAL_PREVIEW,
                ActorCategory.WORLD_INFRASTRUCTURE,
            ):
                continue
            identity = self._identity(actor)
            if (
                identity.parent_runtime_id in target_ids
                and actor_id not in plan.observed_children
            ):
                raise StaleClearPlanError(
                    f"Actor {actor_id} became a child of a planned actor "
                    "after preflight. Run preflight again."
                )
        return current

    def execute_plan(self, world, plan, internal_actor_ids=None,
                     infrastructure_actor_ids=None):
        current = self.validate_plan(
            world,
            plan,
            internal_actor_ids,
            infrastructure_actor_ids,
        )
        initial_ids = {
            actor_id
            for actor_id, actor in current.items()
            if self._is_alive(actor)
        }
        planned_ids = {item.runtime_id for item in plan.items}
        preliminary_errors = {}
        destroy_requested = set()

        for item in self._destruction_order(plan.items):
            actor = current.get(item.runtime_id)
            if actor is None or not self._is_alive(actor):
                continue
            try:
                self._stop_before_destroy(actor, item.category)
                destroy_result = actor.destroy()
                destroy_requested.add(item.runtime_id)
                if destroy_result is False:
                    preliminary_errors[item.runtime_id] = (
                        "CARLA actor.destroy() returned False."
                    )
            except Exception as error:
                preliminary_errors[item.runtime_id] = str(error)

        try:
            final_ids = self._wait_for_destroyed_ids(
                world,
                destroy_requested,
            )
        except Exception as error:
            return WorldClearReport(
                mode=plan.mode,
                planned_count=plan.planned_count,
                skipped=len(plan.skipped),
                failed=plan.planned_count,
                preserved_external_static=sum(
                    entry.get("reason") == "external_static_preserved"
                    for entry in plan.skipped
                ),
                skipped_infrastructure=sum(
                    entry.get("reason") == "world_infrastructure"
                    for entry in plan.skipped
                ),
                warnings=list(plan.warnings),
                errors=[{"runtime_id": None, "message": str(error)}],
                status="failed",
            )

        report = WorldClearReport(
            mode=plan.mode,
            planned_count=plan.planned_count,
            skipped=len(plan.skipped),
            warnings=list(plan.warnings),
            preserved_external_static=sum(
                entry.get("reason") == "external_static_preserved"
                for entry in plan.skipped
            ),
            skipped_infrastructure=sum(
                entry.get("reason") == "world_infrastructure"
                for entry in plan.skipped
            ),
        )
        items_by_id = {item.runtime_id: item for item in plan.items}
        for actor_id in sorted(planned_ids):
            if actor_id not in initial_ids:
                report.already_missing += 1
            elif actor_id not in final_ids:
                report.removed += 1
                self._record_removed_category(
                    report,
                    items_by_id[actor_id],
                )
                if actor_id in preliminary_errors:
                    report.warnings.append(
                        f"Actor {actor_id} disappeared despite destroy error: "
                        f"{preliminary_errors[actor_id]}"
                    )
            else:
                report.failed += 1
                report.errors.append({
                    "runtime_id": actor_id,
                    "message": preliminary_errors.get(
                        actor_id,
                        "Actor still exists after destroy().",
                    ),
                })

        if report.failed == 0:
            report.status = "success"
        elif report.removed or report.already_missing:
            report.status = "partial_success"
        else:
            report.status = "failed"
        return report

    def _wait_for_destroyed_ids(self, world, requested_ids):
        deadline = time.monotonic() + self.DESTROY_CONFIRM_TIMEOUT
        while True:
            final_ids = {
                self.classifier.actor_id(actor)
                for actor in list(world.get_actors())
                if (
                    self.classifier.actor_id(actor) is not None
                    and self._is_alive(actor)
                )
            }
            if not (set(requested_ids) & final_ids):
                return final_ids
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return final_ids
            time.sleep(min(self.DESTROY_CONFIRM_INTERVAL, remaining))

    @staticmethod
    def _record_removed_category(report, item):
        if item.category == ActorCategory.VEHICLE:
            report.removed_vehicles += 1
        elif item.category == ActorCategory.PEDESTRIAN:
            report.removed_pedestrians += 1
        elif item.category == ActorCategory.STATIC_OBJECT:
            report.removed_managed_static_objects += 1
        elif item.category == ActorCategory.SENSOR:
            report.removed_sensors += 1
        elif item.category == ActorCategory.WALKER_AI_CONTROLLER:
            report.removed_walker_ai_controllers += 1

    def _destruction_order(self, items):
        by_id = {item.runtime_id: item for item in items}

        def depth(item):
            value = 0
            parent_id = item.parent_runtime_id
            visited = set()
            while parent_id in by_id and parent_id not in visited:
                visited.add(parent_id)
                value += 1
                parent_id = by_id[parent_id].parent_runtime_id
            return value

        category_priority = {
            ActorCategory.WALKER_AI_CONTROLLER: 0,
            ActorCategory.SENSOR: 1,
        }
        return sorted(
            items,
            key=lambda item: (
                category_priority.get(item.category, 2),
                -depth(item),
                item.runtime_id,
            ),
        )

    @classmethod
    def _plan_item(cls, actor, category, ownership):
        identity = cls._identity(actor)
        return ClearPlanItem(
            runtime_id=identity.runtime_id,
            category=category,
            ownership=ownership,
            type_id=identity.type_id,
            parent_runtime_id=identity.parent_runtime_id,
            identity=identity,
        )

    @classmethod
    def _identity(cls, actor):
        actor_id = ActorClassifier.actor_id(actor)
        if actor_id is None:
            raise ValueError("Actor does not expose a valid runtime ID.")
        try:
            attributes = tuple(sorted(
                (str(key), str(value))
                for key, value in dict(actor.attributes or {}).items()
            ))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            attributes = ()
        try:
            parent = actor.parent
            parent_id = ActorClassifier.actor_id(parent)
        except (AttributeError, RuntimeError):
            parent_id = None
        return ActorIdentity(
            runtime_id=actor_id,
            type_id=str(getattr(actor, "type_id", "")),
            parent_runtime_id=parent_id,
            role_name=ActorClassifier.role_name(actor),
            attributes=attributes,
        )

    @staticmethod
    def _is_alive(actor):
        try:
            return bool(actor.is_alive)
        except (AttributeError, RuntimeError):
            return True

    @classmethod
    def _record_matches_actor(cls, record, actor_ref, current_actor,
                              world_id):
        if record is None or actor_ref is None:
            return False
        if getattr(record, "world_id", None) != world_id:
            return False
        if str(getattr(record, "blueprint_id", "")) != str(
            getattr(current_actor, "type_id", "")
        ):
            return False
        try:
            if not actor_ref.is_alive:
                return False
        except (AttributeError, RuntimeError):
            return False
        return cls._identity(actor_ref) == cls._identity(current_actor)

    @staticmethod
    def _stop_before_destroy(actor, category):
        if category == ActorCategory.WALKER_AI_CONTROLLER:
            try:
                actor.stop()
            except (AttributeError, RuntimeError):
                pass
        elif category == ActorCategory.SENSOR:
            try:
                listening = actor.is_listening
                if callable(listening):
                    listening = listening()
                if listening:
                    actor.stop()
            except (AttributeError, RuntimeError, TypeError):
                pass

    @staticmethod
    def _world_id(world):
        try:
            return int(world.id)
        except (AttributeError, TypeError, ValueError) as error:
            raise RuntimeError(
                "CARLA world does not expose a valid episode ID."
            ) from error
