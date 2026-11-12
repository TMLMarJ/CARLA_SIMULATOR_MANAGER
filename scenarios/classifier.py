from models.scenarios import ActorCategory


class ActorClassifier:
    EGO_ROLE_NAMES = {"hero", "ego"}
    INTERNAL_ROLE_NAMES = {
        "movement_control_preview",
        "spectator_preview",
    }
    INFRASTRUCTURE_PREFIXES = (
        "traffic.",
    )
    INFRASTRUCTURE_TYPE_IDS = {
        "spectator",
    }

    @classmethod
    def classify(cls, actor, internal_actor_ids=None):
        actor_id = cls.actor_id(actor)
        if actor_id is not None and actor_id in set(internal_actor_ids or ()):
            return ActorCategory.INTERNAL_PREVIEW

        role_name = cls.role_name(actor)
        if role_name in cls.INTERNAL_ROLE_NAMES:
            return ActorCategory.INTERNAL_PREVIEW

        return cls.classify_type_id(str(getattr(actor, "type_id", "")))

    @classmethod
    def classify_type_id(cls, type_id):
        type_id = str(type_id or "")
        if type_id == "controller.ai.walker":
            return ActorCategory.WALKER_AI_CONTROLLER
        if type_id.startswith("vehicle."):
            return ActorCategory.VEHICLE
        if type_id.startswith("walker.pedestrian."):
            return ActorCategory.PEDESTRIAN
        if type_id.startswith("sensor."):
            return ActorCategory.SENSOR
        # A static.* runtime actor cannot be identified as native map
        # infrastructure from its blueprint ID alone. Keep it visible as a
        # Static Object; scenario scope is decided from trusted ownership.
        if type_id.startswith("static."):
            return ActorCategory.STATIC_OBJECT
        if (
            type_id in cls.INFRASTRUCTURE_TYPE_IDS
            or type_id.startswith(cls.INFRASTRUCTURE_PREFIXES)
        ):
            return ActorCategory.WORLD_INFRASTRUCTURE
        return ActorCategory.OTHER

    @staticmethod
    def actor_id(actor):
        try:
            return int(actor.id)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    @staticmethod
    def role_name(actor):
        try:
            attributes = actor.attributes or {}
            return str(attributes.get("role_name", "")).strip().lower()
        except (AttributeError, RuntimeError, TypeError):
            return ""

    @classmethod
    def is_ego_role_name(cls, role_name):
        return str(role_name or "").strip().lower() in cls.EGO_ROLE_NAMES

    @classmethod
    def is_ego_vehicle(cls, actor):
        type_id = str(getattr(actor, "type_id", ""))
        return (
            type_id.startswith("vehicle.")
            and cls.is_ego_role_name(cls.role_name(actor))
        )

    @classmethod
    def dashboard_category(cls, type_id):
        category = cls.classify_type_id(type_id)
        if category == ActorCategory.VEHICLE:
            return "Vehicles"
        if category == ActorCategory.PEDESTRIAN:
            return "Walkers"
        if category == ActorCategory.SENSOR:
            return "Sensors"
        return "Other"
