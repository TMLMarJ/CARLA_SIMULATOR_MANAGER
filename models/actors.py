import uuid

import carla

from scenarios.classifier import ActorClassifier
from models.scenarios import (
    ActorCategory,
    ActorOrigin,
    ActorOwnership,
    ConfigurationStatus,
    DataSource,
)


def _new_scenario_id():
    return str(uuid.uuid4())


def _actor_world(actor):
    try:
        return actor.get_world()
    except (AttributeError, RuntimeError):
        return None


def _world_id(actor, fallback=None):
    world = _actor_world(actor)
    try:
        return int(world.id)
    except (AttributeError, TypeError, ValueError):
        return fallback


def _actor_attributes(actor):
    try:
        return {
            str(name): str(value)
            for name, value in dict(actor.attributes or {}).items()
        }
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _read(actor, attribute, default=None, call=False):
    try:
        value = getattr(actor, attribute)
        return value() if call else value
    except (AttributeError, RuntimeError, TypeError):
        return default


class ActorInfo:
    def __init__(
        self,
        actor: carla.Actor,
        user_name: str,
        scenario_id=None,
        world_id=None,
        ownership=ActorOwnership.MANAGED,
        origin=ActorOrigin.GUI_SPAWNED,
        category=None,
        blueprint_attributes=None,
        configuration_status=ConfigurationStatus.COMPLETE,
        missing_fields=None,
        warnings=None,
        parent_scenario_id=None,
        data_sources=None,
    ):
        self.user_name = str(user_name)
        self.scenario_id = str(scenario_id or _new_scenario_id())
        self.world_id = world_id
        self.ownership = ownership
        self.origin = origin
        self.category = category
        self.blueprint_attributes = dict(blueprint_attributes or {})
        self.configuration_status = configuration_status
        self.missing_fields = list(missing_fields or [])
        self.warnings = list(warnings or [])
        self.parent_scenario_id = parent_scenario_id
        self.data_sources = dict(data_sources or {})
        if origin == ActorOrigin.GUI_SPAWNED:
            self.data_sources.setdefault("user_name", DataSource.GUI)
            self.data_sources.setdefault("control", DataSource.GUI)
        self.physics_control = None
        self.last_seen_frame = None
        self.update_runtime(actor)

    def update_runtime(self, actor, frame=None):
        self.carla_id = int(actor.id)
        self.runtime_id = self.carla_id
        self.type_id = str(actor.type_id)
        self.blueprint_id = self.type_id
        if self.category is None:
            self.category = ActorClassifier.classify_type_id(self.type_id)

        self.world = _actor_world(actor)
        self.world_id = _world_id(actor, self.world_id)
        self.is_alive = bool(_read(actor, "is_alive", False))
        self.is_active = bool(_read(actor, "is_active", False))
        parent = _read(actor, "parent")
        self.has_parent = parent if parent is not None else False
        self.parent_runtime_id = (
            ActorClassifier.actor_id(parent) if parent is not None else None
        )
        self.bounding_box = _read(actor, "bounding_box")
        self.acceleration = _read(actor, "get_acceleration", call=True)
        self.angular_velocity = _read(
            actor,
            "get_angular_velocity",
            call=True,
        )
        self.location = _read(actor, "get_location", call=True)
        self.transform = _read(actor, "get_transform", call=True)
        self.velocity = _read(actor, "get_velocity", call=True)
        self.last_seen_frame = frame
        self.data_sources.update({
            "runtime": DataSource.SERVER,
            "transform": (
                DataSource.SERVER
                if self.transform is not None else DataSource.UNKNOWN
            ),
            "parent": DataSource.SERVER,
        })

        attributes = _actor_attributes(actor)
        if attributes is not None:
            self.blueprint_attributes = attributes
            self.data_sources["blueprint_attributes"] = DataSource.SERVER
        else:
            self.data_sources.setdefault(
                "blueprint_attributes",
                DataSource.UNKNOWN,
            )

        self.is_ego_vehicle = (
            self.category == ActorCategory.VEHICLE
            and ActorClassifier.is_ego_role_name(
                self.blueprint_attributes.get("role_name")
            )
        )

        if self.category == ActorCategory.VEHICLE:
            physics = _read(actor, "get_physics_control", call=True)
            if physics is not None:
                self.physics_control = physics
                self.data_sources["physics"] = DataSource.SERVER
            else:
                self.data_sources.setdefault("physics", DataSource.UNKNOWN)
        return self

    def get_actor_info(self):
        return {
            "user_name": self.user_name,
            "scenario_id": self.scenario_id,
            "world_id": self.world_id,
            "ownership": self.ownership,
            "origin": self.origin,
            "category": self.category,
            "carla_id": self.carla_id,
            "type_id": self.type_id,
            "is_alive": self.is_alive,
            "is_active": self.is_active,
            "has_parent": self.has_parent,
            "bounding_box": self.bounding_box,
            "acceleration": self.acceleration,
            "angular_velocity": self.angular_velocity,
            "location": self.location,
            "transform": self.transform,
            "velocity": self.velocity,
            "world": self.world,
        }

    def __str__(self):
        string = ""
        string += f"Actor Info Object defined as {self.user_name}\n"
        string += f"Carla ID: {self.carla_id}\n"
        string += f"Scenario ID: {self.scenario_id}\n"
        string += f"Type ID: {self.type_id}\n"
        string += f"Is Active: {self.is_active}\n"
        string += f"Is Alive: {self.is_alive}\n"
        string += f"Has Parent: {self.has_parent is not False}\n"
        string += f"Acceleration: {self.acceleration}\n"
        string += f"Angular Velocity: {self.angular_velocity}\n"
        string += f"Location: {self.location}\n"
        string += f"Transform: {self.transform}\n"
        string += f"Velocity: {self.velocity}\n"
        return string


class SensorInfo:
    def __init__(
        self,
        sensor: carla.Sensor,
        parent_id: int | None,
        user_name: str,
        blueprint_id: str,
        relative_transform: carla.Transform | None,
        attributes: dict,
        scenario_id=None,
        world_id=None,
        ownership=ActorOwnership.MANAGED,
        origin=ActorOrigin.GUI_SPAWNED,
        category=ActorCategory.SENSOR,
        configuration_status=ConfigurationStatus.COMPLETE,
        missing_fields=None,
        warnings=None,
        parent_scenario_id=None,
        attachment_type="Rigid",
        data_sources=None,
    ):
        self.sensor = sensor
        self.user_name = str(user_name)
        self.scenario_id = str(scenario_id or _new_scenario_id())
        self.world_id = world_id
        self.ownership = ownership
        self.origin = origin
        self.category = category
        self.blueprint_id = str(blueprint_id)
        self.type_id = self.blueprint_id
        self.relative_transform = relative_transform
        self.attachment_type = attachment_type
        self.configuration_status = configuration_status
        self.missing_fields = list(missing_fields or [])
        self.warnings = list(warnings or [])
        self.parent_scenario_id = parent_scenario_id
        self.data_sources = dict(data_sources or {})
        if origin == ActorOrigin.GUI_SPAWNED:
            self.data_sources.setdefault("user_name", DataSource.GUI)
            self.data_sources.setdefault("relative_transform", DataSource.GUI)
            self.data_sources.setdefault("attachment_type", DataSource.GUI)
        self.blueprint_attributes = {
            str(name): str(value)
            for name, value in dict(attributes or {}).items()
        }
        self.attributes = self.blueprint_attributes
        self.last_seen_frame = None
        self.parent_id = parent_id
        self.parent_runtime_id = parent_id
        self.update_runtime(sensor)

    def update_runtime(self, sensor, frame=None, preserve_relative=True):
        self.sensor = sensor
        self.carla_id = int(sensor.id)
        self.runtime_id = self.carla_id
        self.type_id = str(sensor.type_id)
        self.blueprint_id = self.type_id
        self.world_id = _world_id(sensor, self.world_id)
        self.is_alive = bool(_read(sensor, "is_alive", False))
        self.is_active = bool(_read(sensor, "is_active", False))
        self.transform = _read(sensor, "get_transform", call=True)
        self.location = _read(sensor, "get_location", call=True)
        try:
            parent = sensor.parent
            self.parent_id = (
                ActorClassifier.actor_id(parent)
                if parent is not None else None
            )
        except (AttributeError, RuntimeError):
            pass
        self.parent_runtime_id = self.parent_id
        self.last_seen_frame = frame
        self.data_sources.update({
            "runtime": DataSource.SERVER,
            "transform": (
                DataSource.SERVER
                if self.transform is not None else DataSource.UNKNOWN
            ),
            "parent": DataSource.SERVER,
        })

        attributes = _actor_attributes(sensor)
        if attributes is not None:
            self.blueprint_attributes = attributes
            self.attributes = self.blueprint_attributes
            self.data_sources["blueprint_attributes"] = DataSource.SERVER
        else:
            self.data_sources.setdefault(
                "blueprint_attributes",
                DataSource.UNKNOWN,
            )
        if not preserve_relative and self.relative_transform is None:
            self.data_sources.setdefault(
                "relative_transform",
                DataSource.UNKNOWN,
            )
        return self

