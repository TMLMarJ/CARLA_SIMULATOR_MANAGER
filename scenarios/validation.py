import math
import uuid
from collections.abc import Mapping

from scenarios.converters import (
    VEHICLE_PHYSICS_FIELDS,
    WEATHER_FIELDS,
    WHEEL_PHYSICS_FIELDS,
)
from models.scenarios import (
    ActorCategory,
    ActorOrigin,
    ActorOwnership,
    ConfigurationStatus,
    DataSource,
    SCENARIO_SCHEMA,
    SCENARIO_SCHEMA_VERSION,
    ScenarioDocument,
)


class ScenarioValidationError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("Scenario validation failed: " + "; ".join(self.errors))


class ScenarioValidator:
    CATEGORIES = {
        ActorCategory.VEHICLE,
        ActorCategory.PEDESTRIAN,
        ActorCategory.STATIC_OBJECT,
        ActorCategory.SENSOR,
        ActorCategory.WALKER_AI_CONTROLLER,
        ActorCategory.OTHER,
    }
    OWNERSHIPS = {
        ActorOwnership.MANAGED,
        ActorOwnership.EXTERNAL,
    }
    ORIGINS = {
        ActorOrigin.GUI_SPAWNED,
        ActorOrigin.SERVER_DISCOVERED,
        ActorOrigin.SCENARIO_LOADED,
        ActorOrigin.ADOPTED_EXTERNAL,
    }
    DATA_SOURCES = {
        DataSource.SERVER,
        DataSource.GUI,
        DataSource.FILE,
        DataSource.DERIVED,
        DataSource.UNKNOWN,
        "local",
    }
    SIMULATION_FIELDS = {
        "synchronous_mode": bool,
        "fixed_delta_seconds": (int, float, type(None)),
        "no_rendering_mode": bool,
        "substepping": bool,
        "max_substep_delta_time": (int, float),
        "max_substeps": int,
    }

    def validate(self, document):
        data = (
            document.to_dict()
            if isinstance(document, ScenarioDocument)
            else document
        )
        errors = []
        if not isinstance(data, Mapping):
            raise ScenarioValidationError(["Document must be an object."])

        self._validate_header(data, errors)
        self._validate_metadata(data.get("metadata"), errors)
        self._validate_world(data.get("world"), errors)
        actors = data.get("actors")
        if not isinstance(actors, list):
            errors.append("actors must be a list.")
            actors = []
        self._validate_actors(actors, errors)
        self._validate_capture_report(
            data.get("capture_report"),
            actors,
            errors,
        )
        self._validate_finite(data, "$", errors)

        if errors:
            raise ScenarioValidationError(errors)
        return data

    @staticmethod
    def _validate_header(data, errors):
        if data.get("schema") != SCENARIO_SCHEMA:
            errors.append(f"schema must be '{SCENARIO_SCHEMA}'.")
        version = data.get("schema_version")
        if isinstance(version, bool) or version != SCENARIO_SCHEMA_VERSION:
            errors.append(
                f"schema_version must be {SCENARIO_SCHEMA_VERSION}."
            )

    @staticmethod
    def _validate_metadata(metadata, errors):
        if not isinstance(metadata, Mapping):
            errors.append("metadata must be an object.")
            return
        for field in ("name", "created_at", "carla_version"):
            if not isinstance(metadata.get(field), str) or not metadata[field]:
                errors.append(f"metadata.{field} must be a non-empty string.")
        application_version = metadata.get("application_version")
        if application_version is not None and not isinstance(
            application_version,
            str,
        ):
            errors.append("metadata.application_version must be string or null.")

    def _validate_world(self, world, errors):
        if not isinstance(world, Mapping):
            errors.append("world must be an object.")
            return
        if not isinstance(world.get("map"), str) or not world["map"]:
            errors.append("world.map must be a non-empty string.")
        world_id = world.get("captured_world_id")
        if world_id is not None and (
            isinstance(world_id, bool) or not isinstance(world_id, int)
        ):
            errors.append("world.captured_world_id must be integer or null.")
        weather = world.get("weather")
        if not isinstance(weather, Mapping) or not weather:
            errors.append("world.weather must be a non-empty object.")
        else:
            for field in WEATHER_FIELDS:
                if field not in weather:
                    errors.append(f"world.weather.{field} is required.")
                elif not self._is_finite_number(weather[field]):
                    errors.append(f"world.weather.{field} must be finite.")
        simulation = world.get("simulation")
        if not isinstance(simulation, Mapping):
            errors.append("world.simulation must be an object.")
            return
        for field, expected_type in self.SIMULATION_FIELDS.items():
            value = simulation.get(field)
            if not isinstance(value, expected_type) or (
                field in (
                    "fixed_delta_seconds",
                    "max_substep_delta_time",
                    "max_substeps",
                )
                and isinstance(value, bool)
            ):
                errors.append(
                    f"world.simulation.{field} has an invalid type."
                )
        fixed_delta = simulation.get("fixed_delta_seconds")
        if fixed_delta is not None and self._is_finite_number(fixed_delta):
            if fixed_delta < 0:
                errors.append(
                    "world.simulation.fixed_delta_seconds cannot be negative."
                )
        max_delta = simulation.get("max_substep_delta_time")
        if self._is_finite_number(max_delta) and max_delta <= 0:
            errors.append(
                "world.simulation.max_substep_delta_time must be positive."
            )
        max_substeps = simulation.get("max_substeps")
        if (
            isinstance(max_substeps, int)
            and not isinstance(max_substeps, bool)
            and max_substeps <= 0
        ):
            errors.append("world.simulation.max_substeps must be positive.")

    def _validate_actors(self, actors, errors):
        ids = set()
        names = set()
        actors_by_id = {}
        for index, actor in enumerate(actors):
            path = f"actors[{index}]"
            if not isinstance(actor, Mapping):
                errors.append(f"{path} must be an object.")
                continue
            scenario_id = actor.get("scenario_id")
            if not self._valid_uuid(scenario_id):
                errors.append(f"{path}.scenario_id must be a valid UUID.")
            elif scenario_id in ids:
                errors.append(f"Duplicate scenario_id: {scenario_id}.")
            else:
                ids.add(scenario_id)
                actors_by_id[scenario_id] = actor
            name = actor.get("user_name")
            if not isinstance(name, str) or not name:
                errors.append(f"{path}.user_name must be a non-empty string.")
            elif name in names:
                errors.append(f"Duplicate user_name: {name}.")
            else:
                names.add(name)
            runtime_id = actor.get("captured_runtime_id")
            if isinstance(runtime_id, bool) or not isinstance(runtime_id, int):
                errors.append(f"{path}.captured_runtime_id must be an integer.")
            if actor.get("category") not in self.CATEGORIES:
                errors.append(f"{path}.category is unsupported.")
            if actor.get("ownership") not in self.OWNERSHIPS:
                errors.append(f"{path}.ownership is unsupported.")
            if actor.get("origin") not in self.ORIGINS:
                errors.append(f"{path}.origin is unsupported.")
            if not isinstance(actor.get("blueprint_id"), str) or not actor.get(
                "blueprint_id"
            ):
                errors.append(f"{path}.blueprint_id must be non-empty.")
            self._validate_transform(actor.get("transform"), path, errors)
            self._validate_string_map(
                actor.get("blueprint_attributes"),
                f"{path}.blueprint_attributes",
                errors,
            )
            self._validate_sources(actor.get("data_sources"), path, errors)
            self._validate_completeness(actor.get("completeness"), path, errors)
            self._validate_physics(actor, path, errors)
            self._validate_control(
                actor.get("control"),
                actor.get("category"),
                path,
                errors,
            )
            attachment_type = actor.get("attachment_type")
            if attachment_type is not None and not isinstance(
                attachment_type,
                str,
            ):
                errors.append(
                    f"{path}.attachment_type must be string or null."
                )

        for scenario_id, actor in actors_by_id.items():
            parent_id = actor.get("parent_scenario_id")
            if parent_id is not None:
                if not self._valid_uuid(parent_id):
                    errors.append(
                        f"Actor {scenario_id} has invalid parent_scenario_id."
                    )
                elif parent_id not in actors_by_id:
                    errors.append(
                        f"Actor {scenario_id} references a missing parent."
                    )
                elif parent_id == scenario_id:
                    errors.append(f"Actor {scenario_id} is its own parent.")
            if (
                actor.get("category") == ActorCategory.WALKER_AI_CONTROLLER
                and parent_id is None
            ):
                errors.append(
                    f"Walker AI controller {scenario_id} requires a parent."
                )
        self._validate_parent_cycles(actors_by_id, errors)

    @staticmethod
    def _validate_transform(transform, path, errors):
        if not isinstance(transform, Mapping):
            errors.append(f"{path}.transform must be an object.")
            return
        if transform.get("space") not in ("world", "relative"):
            errors.append(f"{path}.transform.space is invalid.")
        location = transform.get("location")
        rotation = transform.get("rotation")
        ScenarioValidator._validate_vector(
            location,
            ("x", "y", "z"),
            f"{path}.transform.location",
            errors,
        )
        ScenarioValidator._validate_vector(
            rotation,
            ("pitch", "yaw", "roll"),
            f"{path}.transform.rotation",
            errors,
        )
        if transform.get("source") not in ScenarioValidator.DATA_SOURCES:
            errors.append(f"{path}.transform.source is invalid.")

    @staticmethod
    def _validate_vector(value, fields, path, errors):
        if not isinstance(value, Mapping):
            errors.append(f"{path} must be an object.")
            return
        for field in fields:
            item = value.get(field)
            if (
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
            ):
                errors.append(f"{path}.{field} must be finite.")

    @staticmethod
    def _validate_string_map(value, path, errors):
        if not isinstance(value, Mapping):
            errors.append(f"{path} must be an object.")
        elif any(not isinstance(key, str) or not isinstance(item, str)
                 for key, item in value.items()):
            errors.append(f"{path} must contain string keys and values.")

    def _validate_sources(self, sources, path, errors):
        if not isinstance(sources, Mapping):
            errors.append(f"{path}.data_sources must be an object.")
            return
        for field, source in sources.items():
            if not isinstance(field, str) or source not in self.DATA_SOURCES:
                errors.append(f"{path}.data_sources contains invalid data.")

    @staticmethod
    def _validate_completeness(completeness, path, errors):
        if not isinstance(completeness, Mapping):
            errors.append(f"{path}.completeness must be an object.")
            return
        status = completeness.get("status")
        if status not in (
            ConfigurationStatus.COMPLETE,
            ConfigurationStatus.PARTIAL,
        ):
            errors.append(
                f"{path}.completeness.status must be complete or partial."
            )
        missing = completeness.get("missing_fields")
        warnings = completeness.get("warnings")
        if not isinstance(missing, list) or any(
            not isinstance(item, str) for item in missing
        ):
            errors.append(f"{path}.completeness.missing_fields is invalid.")
        if not isinstance(warnings, list) or any(
            not isinstance(item, str) for item in warnings
        ):
            errors.append(f"{path}.completeness.warnings is invalid.")
        if status == ConfigurationStatus.COMPLETE and missing:
            errors.append(f"{path} is complete but has missing_fields.")

    def _validate_physics(self, actor, path, errors):
        physics = actor.get("physics")
        if physics is None:
            return
        if actor.get("category") != ActorCategory.VEHICLE:
            errors.append(f"{path}.physics is only valid for vehicles.")
            return
        if not isinstance(physics, Mapping):
            errors.append(f"{path}.physics must be an object or null.")
            return
        if physics.get("source") not in self.DATA_SOURCES:
            errors.append(f"{path}.physics.source is invalid.")
        for field in VEHICLE_PHYSICS_FIELDS:
            if field not in physics:
                errors.append(f"{path}.physics.{field} is required.")
            elif field in (
                "use_gear_autobox",
                "use_sweep_wheel_collision",
            ):
                if not isinstance(physics[field], bool):
                    errors.append(f"{path}.physics.{field} must be boolean.")
            elif not self._is_finite_number(physics[field]):
                errors.append(f"{path}.physics.{field} must be finite.")
        self._validate_vector(
            physics.get("center_of_mass"),
            ("x", "y", "z"),
            f"{path}.physics.center_of_mass",
            errors,
        )
        for curve in ("torque_curve", "steering_curve"):
            values = physics.get(curve)
            if not isinstance(values, list):
                errors.append(f"{path}.physics.{curve} must be a list.")
            else:
                for index, point in enumerate(values):
                    self._validate_vector(
                        point,
                        ("x", "y"),
                        f"{path}.physics.{curve}[{index}]",
                        errors,
                    )
        gears = physics.get("forward_gears")
        if not isinstance(gears, list):
            errors.append(f"{path}.physics.forward_gears must be a list.")
        elif any(
            not isinstance(gear, Mapping)
            or set(gear) != {"ratio", "down_ratio", "up_ratio"}
            or any(not self._is_finite_number(value) for value in gear.values())
            for gear in gears
        ):
            errors.append(f"{path}.physics.forward_gears is invalid.")
        wheels = physics.get("wheels")
        if not isinstance(wheels, list):
            errors.append(f"{path}.physics.wheels must be a list.")
        else:
            required = set(WHEEL_PHYSICS_FIELDS) | {"position"}
            for index, wheel in enumerate(wheels):
                if not isinstance(wheel, Mapping) or set(wheel) != required:
                    errors.append(
                        f"{path}.physics.wheels[{index}] is invalid."
                    )
                    continue
                for field in WHEEL_PHYSICS_FIELDS:
                    if not self._is_finite_number(wheel[field]):
                        errors.append(
                            f"{path}.physics.wheels[{index}].{field} "
                            "must be finite."
                        )
                self._validate_vector(
                    wheel["position"],
                    ("x", "y", "z"),
                    f"{path}.physics.wheels[{index}].position",
                    errors,
                )

    def _validate_control(self, control, category, path, errors):
        if control is None:
            return
        if not isinstance(control, Mapping):
            errors.append(f"{path}.control must be an object or null.")
            return
        if control.get("source") not in self.DATA_SOURCES:
            errors.append(f"{path}.control.source is invalid.")
        mode = control.get("mode")
        if mode is not None and not isinstance(mode, str):
            errors.append(f"{path}.control.mode is invalid.")
        autopilot = control.get("autopilot_enabled")
        if autopilot is not None and not isinstance(autopilot, bool):
            errors.append(f"{path}.control.autopilot_enabled is invalid.")
        port = control.get("traffic_manager_port")
        if port is not None and (
            isinstance(port, bool) or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            errors.append(f"{path}.control.traffic_manager_port is invalid.")
        if category == ActorCategory.PEDESTRIAN:
            active = control.get("active")
            random_navigation = control.get("random_navigation")
            speed = control.get("speed")
            if active is not None and not isinstance(active, bool):
                errors.append(f"{path}.control.active is invalid.")
            if random_navigation is not None and not isinstance(
                random_navigation,
                bool,
            ):
                errors.append(
                    f"{path}.control.random_navigation is invalid."
                )
            if speed is not None and not self._is_finite_number(speed):
                errors.append(f"{path}.control.speed is invalid.")
        if category == ActorCategory.WALKER_AI_CONTROLLER:
            max_speed = control.get("max_speed")
            if max_speed is not None and not self._is_finite_number(max_speed):
                errors.append(f"{path}.control.max_speed is invalid.")

    @staticmethod
    def _validate_parent_cycles(actors_by_id, errors):
        visiting = set()
        visited = set()

        def visit(scenario_id):
            if scenario_id in visiting:
                errors.append(f"Parent dependency cycle at {scenario_id}.")
                return
            if scenario_id in visited:
                return
            visiting.add(scenario_id)
            parent_id = actors_by_id[scenario_id].get("parent_scenario_id")
            if parent_id in actors_by_id:
                visit(parent_id)
            visiting.remove(scenario_id)
            visited.add(scenario_id)

        for scenario_id in actors_by_id:
            visit(scenario_id)

    @staticmethod
    def _validate_capture_report(report, actors, errors):
        if not isinstance(report, Mapping):
            errors.append("capture_report must be an object.")
            return
        for field in (
            "saved",
            "partial",
            "skipped_external",
            "skipped_unsupported",
            "skipped_dependency",
            "skipped_runtime_only",
            "skipped_unrestorable",
            "skipped_internal",
            "skipped_infrastructure",
        ):
            value = report.get(field, 0)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                errors.append(f"capture_report.{field} must be non-negative.")
        for field in ("warnings", "errors"):
            if not isinstance(report.get(field), list):
                errors.append(f"capture_report.{field} must be a list.")
        if report.get("saved") != len(actors):
            errors.append("capture_report.saved must match actors length.")
        actual_partial = sum(
            isinstance(actor, Mapping)
            and isinstance(actor.get("completeness"), Mapping)
            and actor["completeness"].get("status")
            == ConfigurationStatus.PARTIAL
            for actor in actors
        )
        if report.get("partial") != actual_partial:
            errors.append(
                "capture_report.partial must match partial actor count."
            )

    @staticmethod
    def _validate_finite(value, path, errors):
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"{path} contains a non-finite number.")
        elif isinstance(value, Mapping):
            for key, item in value.items():
                ScenarioValidator._validate_finite(
                    item,
                    f"{path}.{key}",
                    errors,
                )
        elif isinstance(value, list):
            for index, item in enumerate(value):
                ScenarioValidator._validate_finite(
                    item,
                    f"{path}[{index}]",
                    errors,
                )

    @staticmethod
    def _valid_uuid(value):
        if not isinstance(value, str):
            return False
        try:
            return str(uuid.UUID(value)) == value.lower()
        except (ValueError, AttributeError):
            return False

    @staticmethod
    def _is_finite_number(value):
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
        )
