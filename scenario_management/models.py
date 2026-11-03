from dataclasses import asdict, dataclass, field


SCENARIO_SCHEMA = "carla-simulator-manager/scenario"
SCENARIO_SCHEMA_VERSION = 1


class ActorCategory:
    VEHICLE = "vehicle"
    PEDESTRIAN = "pedestrian"
    STATIC_OBJECT = "static_object"
    SENSOR = "sensor"
    WALKER_AI_CONTROLLER = "walker_ai_controller"
    INTERNAL_PREVIEW = "internal_preview"
    WORLD_INFRASTRUCTURE = "world_infrastructure"
    OTHER = "other"


class ActorOwnership:
    MANAGED = "managed"
    EXTERNAL = "external"
    INTERNAL = "internal"


class ActorOrigin:
    GUI_SPAWNED = "gui_spawned"
    SERVER_DISCOVERED = "server_discovered"
    SCENARIO_LOADED = "scenario_loaded"
    INTERNAL_PREVIEW = "internal_preview"
    ADOPTED_EXTERNAL = "adopted_external"


class ConfigurationStatus:
    COMPLETE = "complete"
    PARTIAL = "partial"
    RUNTIME_ONLY = "runtime_only"
    UNRESTORABLE = "unrestorable"


class DataSource:
    SERVER = "server"
    GUI = "gui"
    FILE = "file"
    DERIVED = "derived"
    UNKNOWN = "unknown"


@dataclass
class WorldSyncReport:
    reason: str
    world_id: int | None
    added: int = 0
    updated: int = 0
    removed: int = 0
    skipped_internal: int = 0
    skipped_infrastructure: int = 0
    removed_actor_ids: list[int] = field(default_factory=list)
    removed_sensor_ids: list[int] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def success(self):
        return not self.errors

    def add_error(self, actor_id, message):
        self.errors.append({
            "actor_id": actor_id,
            "message": str(message),
        })

    def to_dict(self):
        result = asdict(self)
        result["success"] = self.success
        return result


@dataclass
class ScenarioMetadata:
    name: str
    created_at: str
    carla_version: str = "0.9.16"
    application_version: str | None = None


@dataclass
class ScenarioWorld:
    map: str
    captured_world_id: int | None
    weather: dict
    simulation: dict


@dataclass
class ActorCompleteness:
    status: str
    missing_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ScenarioActor:
    scenario_id: str
    captured_runtime_id: int
    user_name: str
    category: str
    blueprint_id: str
    origin: str
    ownership: str
    transform: dict
    blueprint_attributes: dict[str, str] = field(default_factory=dict)
    physics: dict | None = None
    control: dict | None = None
    parent_scenario_id: str | None = None
    attachment_type: str | None = None
    data_sources: dict[str, str] = field(default_factory=dict)
    completeness: ActorCompleteness = field(
        default_factory=lambda: ActorCompleteness(
            ConfigurationStatus.COMPLETE
        )
    )


@dataclass
class CaptureReport:
    saved: int = 0
    partial: int = 0
    skipped_external: int = 0
    skipped_unsupported: int = 0
    skipped_dependency: int = 0
    skipped_runtime_only: int = 0
    skipped_unrestorable: int = 0
    skipped_internal: int = 0
    skipped_infrastructure: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    sync_report: dict = field(default_factory=dict)


@dataclass
class ScenarioDocument:
    schema: str = field(default=SCENARIO_SCHEMA, init=False)
    schema_version: int = field(default=SCENARIO_SCHEMA_VERSION, init=False)
    metadata: ScenarioMetadata
    world: ScenarioWorld
    actors: list[ScenarioActor]
    capture_report: CaptureReport

    def to_dict(self):
        return asdict(self)
