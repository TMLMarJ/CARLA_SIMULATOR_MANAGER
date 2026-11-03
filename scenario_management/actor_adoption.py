import uuid
from dataclasses import asdict, dataclass, field

from scenario_management.actor_classifier import ActorClassifier
from scenario_management.models import (
    ActorCategory,
    ActorOrigin,
    ActorOwnership,
    DataSource,
)


class AdoptionStatus:
    SUCCESS = "success"
    ALREADY_MANAGED = "already_managed"
    ACTOR_MISSING = "actor_missing"
    STALE_IDENTITY = "stale_identity"
    UNSUPPORTED_CATEGORY = "unsupported_category"
    UNSAFE_TO_ADOPT = "unsafe_to_adopt"
    OPERATION_CONFLICT = "operation_conflict"
    ERROR = "error"


@dataclass
class ActorAdoptionReport:
    status: str
    runtime_id: int
    world_id: int | None = None
    category: str | None = None
    user_name: str | None = None
    scenario_id: str | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    dependent_external_sensors: int = 0

    @property
    def success(self):
        return self.status in (
            AdoptionStatus.SUCCESS,
            AdoptionStatus.ALREADY_MANAGED,
        )

    def to_dict(self):
        result = asdict(self)
        result["success"] = self.success
        return result


class ActorAdopter:
    SUPPORTED_CATEGORIES = {
        ActorCategory.VEHICLE,
        ActorCategory.PEDESTRIAN,
        ActorCategory.STATIC_OBJECT,
        ActorCategory.SENSOR,
    }
    TRUSTED_STATIC_PROVENANCE = {DataSource.GUI, DataSource.FILE}

    def __init__(self, classifier=None):
        self.classifier = classifier or ActorClassifier

    def expected_identity(self, database, runtime_id):
        runtime_id = int(runtime_id)
        with database.lock:
            info = database.actors_db.actors_db.get(runtime_id)
            if info is None:
                info = database.sensors_db.sensors_db.get(runtime_id)
            if info is None:
                return None
            return {
                "runtime_id": runtime_id,
                "world_id": info.world_id,
                "type_id": str(info.blueprint_id),
                "parent_runtime_id": getattr(
                    info,
                    "parent_runtime_id",
                    None,
                ),
                "attributes": tuple(sorted(
                    (str(key), str(value))
                    for key, value in info.blueprint_attributes.items()
                )),
                "scenario_id": str(info.scenario_id),
            }

    def adopt(self, world, database, expected_identity,
              internal_actor_ids=None):
        if not expected_identity:
            return self._report(
                AdoptionStatus.ACTOR_MISSING,
                -1,
                self._world_id(world),
                "The actor is missing from synchronized data.",
            )
        runtime_id = int(expected_identity["runtime_id"])
        world_id = self._world_id(world)
        if expected_identity.get("world_id") != world_id:
            return self._report(
                AdoptionStatus.STALE_IDENTITY,
                runtime_id,
                world_id,
                "The world episode changed. Refresh World and try again.",
            )

        actor = self._find_actor(world, runtime_id)
        if actor is None:
            return self._report(
                AdoptionStatus.ACTOR_MISSING,
                runtime_id,
                world_id,
                "The actor no longer exists in the current CARLA world.",
            )

        category = self.classifier.classify(actor, internal_actor_ids)
        if category not in self.SUPPORTED_CATEGORIES:
            return self._report(
                AdoptionStatus.UNSUPPORTED_CATEGORY,
                runtime_id,
                world_id,
                f"Actors in category '{category}' cannot be adopted.",
                category,
            )
        if self._identity(actor) != self._expected_runtime_identity(
            expected_identity
        ):
            return self._report(
                AdoptionStatus.STALE_IDENTITY,
                runtime_id,
                world_id,
                "The actor identity changed after it was displayed. Refresh "
                "World and try again.",
                category,
            )

        with database.lock:
            info = (
                database.sensors_db.sensors_db.get(runtime_id)
                if category == ActorCategory.SENSOR
                else database.actors_db.actors_db.get(runtime_id)
            )
            if info is None or info.world_id != world_id:
                return self._report(
                    AdoptionStatus.STALE_IDENTITY,
                    runtime_id,
                    world_id,
                    "The synchronized actor record is stale. Refresh World.",
                    category,
                )
            if info.ownership == ActorOwnership.MANAGED:
                return ActorAdoptionReport(
                    status=AdoptionStatus.ALREADY_MANAGED,
                    runtime_id=runtime_id,
                    world_id=world_id,
                    category=category,
                    user_name=info.user_name,
                    scenario_id=info.scenario_id,
                    message="The actor is already managed by this GUI.",
                )
            if category == ActorCategory.STATIC_OBJECT and not (
                info.data_sources.get("spawn_provenance")
                in self.TRUSTED_STATIC_PROVENANCE
            ):
                return self._report(
                    AdoptionStatus.UNSAFE_TO_ADOPT,
                    runtime_id,
                    world_id,
                    "CARLA cannot confirm whether this external static actor "
                    "is a client-spawned object or native map infrastructure.",
                    category,
                )

            try:
                uuid.UUID(str(info.scenario_id))
            except (TypeError, ValueError, AttributeError):
                info.scenario_id = str(uuid.uuid4())
            if not str(info.user_name).strip():
                prefix = {
                    ActorCategory.VEHICLE: "Vehicle",
                    ActorCategory.PEDESTRIAN: "Pedestrian",
                    ActorCategory.STATIC_OBJECT: "Static",
                    ActorCategory.SENSOR: "Sensor",
                }[category]
                used_names = {
                    str(record.user_name)
                    for record in (
                        list(database.actors_db.actors_db.values())
                        + list(database.sensors_db.sensors_db.values())
                    )
                    if record is not info
                }
                number = 1
                while f"{prefix}_{number}" in used_names:
                    number += 1
                info.user_name = f"{prefix}_{number}"
            database.adopt_runtime_actor(
                runtime_id,
                actor,
                category,
                ActorOrigin.ADOPTED_EXTERNAL,
            )
            warnings = []
            if category in (
                ActorCategory.VEHICLE,
                ActorCategory.PEDESTRIAN,
            ):
                warnings.append(
                    "Existing external control and autopilot state cannot be "
                    "fully detected; other clients may continue controlling "
                    "this actor."
                )
            dependent_external_sensors = sum(
                sensor.parent_runtime_id == runtime_id
                and sensor.ownership == ActorOwnership.EXTERNAL
                for sensor in database.sensors_db.sensors_db.values()
            )
            if dependent_external_sensors:
                warnings.append(
                    f"{dependent_external_sensors} attached external sensor(s) "
                    "remain external and require separate adoption."
                )
            return ActorAdoptionReport(
                status=AdoptionStatus.SUCCESS,
                runtime_id=runtime_id,
                world_id=world_id,
                category=category,
                user_name=info.user_name,
                scenario_id=info.scenario_id,
                message="Actor ownership was adopted by the GUI.",
                warnings=warnings,
                dependent_external_sensors=dependent_external_sensors,
            )

    @classmethod
    def _identity(cls, actor):
        try:
            attributes = tuple(sorted(
                (str(key), str(value))
                for key, value in dict(actor.attributes or {}).items()
            ))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            attributes = ()
        try:
            parent_id = cls._actor_id(actor.parent)
        except (AttributeError, RuntimeError):
            parent_id = None
        return (
            cls._actor_id(actor),
            str(getattr(actor, "type_id", "")),
            parent_id,
            attributes,
        )

    @staticmethod
    def _expected_runtime_identity(expected):
        return (
            int(expected["runtime_id"]),
            str(expected["type_id"]),
            expected.get("parent_runtime_id"),
            tuple(expected.get("attributes", ())),
        )

    @staticmethod
    def _find_actor(world, runtime_id):
        try:
            actor = world.get_actor(runtime_id)
            if actor is not None:
                return actor
        except (AttributeError, RuntimeError):
            pass
        for actor in list(world.get_actors()):
            if ActorAdopter._actor_id(actor) == runtime_id:
                return actor
        return None

    @staticmethod
    def _actor_id(actor):
        try:
            return int(actor.id)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    @staticmethod
    def _world_id(world):
        try:
            return int(world.id)
        except (AttributeError, TypeError, ValueError):
            return None

    @staticmethod
    def _report(status, runtime_id, world_id, message, category=None):
        return ActorAdoptionReport(
            status=status,
            runtime_id=runtime_id,
            world_id=world_id,
            category=category,
            message=message,
        )
