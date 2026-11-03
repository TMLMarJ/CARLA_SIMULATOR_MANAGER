import math

from scenario_management.actor_classifier import ActorClassifier
from scenario_management.models import (
    ActorCategory,
    ActorOrigin,
    ActorOwnership,
    ConfigurationStatus,
    DataSource,
    WorldSyncReport,
)


class WorldSynchronizer:
    NAME_PREFIXES = {
        ActorCategory.VEHICLE: "Vehicle",
        ActorCategory.PEDESTRIAN: "Pedestrian",
        ActorCategory.SENSOR: "Sensor",
        ActorCategory.STATIC_OBJECT: "Static",
    }

    def __init__(self, classifier=None):
        self.classifier = classifier or ActorClassifier

    def synchronize(self, world, database, internal_actor_ids=None,
                    reason="manual"):
        from actors.actors import ActorInfo, SensorInfo

        world_id = self._world_id(world)
        report = WorldSyncReport(reason=reason, world_id=world_id)
        actors = list(world.get_actors())
        frame = self._snapshot_frame(world)
        internal_actor_ids = set(internal_actor_ids or ())
        inventory = []

        for actor in actors:
            actor_id = None
            try:
                actor_id = self.classifier.actor_id(actor)
                category = self.classifier.classify(
                    actor,
                    internal_actor_ids,
                )
                if category == ActorCategory.INTERNAL_PREVIEW:
                    report.skipped_internal += 1
                    continue
                if category == ActorCategory.WORLD_INFRASTRUCTURE:
                    report.skipped_infrastructure += 1
                    continue
                if actor_id is None:
                    raise ValueError("Actor does not expose a valid runtime ID.")
                inventory.append((actor_id, category, actor))
            except Exception as error:
                report.add_error(actor_id, error)

        inventory.sort(key=lambda item: item[0])
        actor_inventory_ids = {
            actor_id
            for actor_id, category, _actor in inventory
            if category != ActorCategory.SENSOR
        }
        sensor_inventory_ids = {
            actor_id
            for actor_id, category, _actor in inventory
            if category == ActorCategory.SENSOR
        }

        with database.lock:
            self._remove_stale_records(
                database,
                world_id,
                actor_inventory_ids,
                sensor_inventory_ids,
                report,
            )
            used_names = self._used_names(database)
            existing_actors = dict(database.actors_db.actors_db)
            existing_sensors = dict(database.sensors_db.sensors_db)

        for actor_id, category, actor in inventory:
            try:
                if category == ActorCategory.SENSOR:
                    existing = existing_sensors.get(actor_id)
                    if existing is not None and existing.world_id == world_id:
                        refreshed = self._refresh_sensor_record(
                            existing,
                            actor,
                            frame,
                        )
                        with database.lock:
                            database.sensors_db.sensors_db[actor_id] = refreshed
                            database.sensors_db.sensors[actor_id] = actor
                        report.updated += 1
                    else:
                        parent_id = self._parent_id(actor)
                        relative_transform = self.derive_relative_transform(
                            actor,
                        )
                        status, missing, warnings = (
                            self._external_completeness(
                                category,
                                actor,
                                relative_transform,
                            )
                        )
                        name = self._next_name(category, used_names)
                        sensor_info = SensorInfo(
                            actor,
                            parent_id,
                            name,
                            str(actor.type_id),
                            relative_transform,
                            self._attributes(actor),
                            world_id=world_id,
                            ownership=ActorOwnership.EXTERNAL,
                            origin=ActorOrigin.SERVER_DISCOVERED,
                            configuration_status=status,
                            missing_fields=missing,
                            warnings=warnings,
                            attachment_type=None,
                            data_sources={
                                "user_name": DataSource.DERIVED,
                                "control": DataSource.UNKNOWN,
                                "relative_transform": (
                                    DataSource.DERIVED
                                    if relative_transform is not None
                                    else DataSource.UNKNOWN
                                ),
                                "attachment_type": DataSource.UNKNOWN,
                            },
                        )
                        sensor_info.last_seen_frame = frame
                        with database.lock:
                            database.sensors_db.sensors_db[actor_id] = sensor_info
                            database.sensors_db.sensors[actor_id] = actor
                        report.added += 1
                else:
                    existing = existing_actors.get(actor_id)
                    if existing is not None and existing.world_id == world_id:
                        refreshed = self._refresh_actor_record(
                            existing,
                            actor,
                            category,
                            frame,
                        )
                        with database.lock:
                            database.actors_db.actors_db[actor_id] = refreshed
                            database.actors_db.actors[actor_id] = actor
                        report.updated += 1
                    else:
                        status, missing, warnings = (
                            self._external_completeness(category, actor)
                        )
                        name = self._next_name(category, used_names)
                        actor_info = ActorInfo(
                            actor,
                            name,
                            world_id=world_id,
                            ownership=ActorOwnership.EXTERNAL,
                            origin=ActorOrigin.SERVER_DISCOVERED,
                            category=category,
                            configuration_status=status,
                            missing_fields=missing,
                            warnings=warnings,
                            data_sources={
                                "user_name": DataSource.DERIVED,
                                "control": DataSource.UNKNOWN,
                            },
                        )
                        actor_info.last_seen_frame = frame
                        with database.lock:
                            database.actors_db.actors_db[actor_id] = actor_info
                            database.actors_db.actors[actor_id] = actor
                        report.added += 1
            except Exception as error:
                report.add_error(actor_id, error)

        with database.lock:
            self._resolve_parent_relationships(database)

        for sensor_id in report.removed_sensor_ids:
            database.sensor_event_queue.put(("sensor_removed", sensor_id))
        return report

    @staticmethod
    def _refresh_actor_record(existing, actor, category, frame):
        from actors.actors import ActorInfo

        refreshed = ActorInfo(
            actor,
            existing.user_name,
            scenario_id=existing.scenario_id,
            world_id=existing.world_id,
            ownership=existing.ownership,
            origin=existing.origin,
            category=category,
            blueprint_attributes=existing.blueprint_attributes,
            configuration_status=existing.configuration_status,
            missing_fields=existing.missing_fields,
            warnings=existing.warnings,
            parent_scenario_id=existing.parent_scenario_id,
            data_sources=existing.data_sources,
        )
        refreshed.last_seen_frame = frame
        if (
            not refreshed.blueprint_attributes
            and existing.blueprint_attributes
            and existing.data_sources.get("blueprint_attributes")
            in (DataSource.GUI, DataSource.FILE)
        ):
            refreshed.blueprint_attributes = dict(
                existing.blueprint_attributes
            )
            refreshed.data_sources["blueprint_attributes"] = (
                existing.data_sources["blueprint_attributes"]
            )
        if (
            refreshed.physics_control is None
            and existing.physics_control is not None
        ):
            refreshed.physics_control = existing.physics_control
            refreshed.data_sources["physics"] = existing.data_sources.get(
                "physics",
                DataSource.GUI,
            )
        return refreshed

    @staticmethod
    def _refresh_sensor_record(existing, sensor, frame):
        from actors.actors import SensorInfo

        refreshed = SensorInfo(
            sensor,
            WorldSynchronizer._parent_id(sensor),
            existing.user_name,
            str(sensor.type_id),
            existing.relative_transform,
            existing.blueprint_attributes,
            scenario_id=existing.scenario_id,
            world_id=existing.world_id,
            ownership=existing.ownership,
            origin=existing.origin,
            category=ActorCategory.SENSOR,
            configuration_status=existing.configuration_status,
            missing_fields=existing.missing_fields,
            warnings=existing.warnings,
            parent_scenario_id=existing.parent_scenario_id,
            attachment_type=existing.attachment_type,
            data_sources=existing.data_sources,
        )
        refreshed.last_seen_frame = frame
        if (
            not refreshed.blueprint_attributes
            and existing.blueprint_attributes
            and existing.data_sources.get("blueprint_attributes")
            in (DataSource.GUI, DataSource.FILE)
        ):
            refreshed.blueprint_attributes = dict(
                existing.blueprint_attributes
            )
            refreshed.attributes = refreshed.blueprint_attributes
            refreshed.data_sources["blueprint_attributes"] = (
                existing.data_sources["blueprint_attributes"]
            )
        return refreshed

    @staticmethod
    def _world_id(world):
        try:
            return int(world.id)
        except (AttributeError, TypeError, ValueError) as error:
            raise RuntimeError(
                "CARLA world does not expose a valid episode ID."
            ) from error

    @staticmethod
    def _snapshot_frame(world):
        try:
            return int(world.get_snapshot().frame)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    @staticmethod
    def _attributes(actor):
        try:
            return {
                str(name): str(value)
                for name, value in dict(actor.attributes or {}).items()
            }
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return {}

    @staticmethod
    def _parent_id(actor):
        try:
            parent = actor.parent
            return None if parent is None else int(parent.id)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    @staticmethod
    def _used_names(database):
        return {
            info.user_name
            for registry in (
                database.actors_db.actors_db,
                database.sensors_db.sensors_db,
            )
            for info in registry.values()
        }

    @classmethod
    def _next_name(cls, category, used_names):
        prefix = cls.NAME_PREFIXES.get(category, "Actor")
        number = 1
        while f"{prefix}_{number}" in used_names:
            number += 1
        name = f"{prefix}_{number}"
        used_names.add(name)
        return name

    @staticmethod
    def _remove_stale_records(database, world_id, actor_ids, sensor_ids,
                              report):
        stale_actor_ids = [
            actor_id
            for actor_id, info in database.actors_db.actors_db.items()
            if info.world_id != world_id or actor_id not in actor_ids
        ]
        stale_sensor_ids = [
            sensor_id
            for sensor_id, info in database.sensors_db.sensors_db.items()
            if info.world_id != world_id or sensor_id not in sensor_ids
        ]

        for sensor_id in stale_sensor_ids:
            database.sensors_db.remove_sensor(sensor_id)
            report.removed_sensor_ids.append(sensor_id)
        for actor_id in stale_actor_ids:
            database.actors_db.remove_actor(actor_id)
            database.vehicle_control_states.pop(actor_id, None)
            database.walker_ai_states.pop(actor_id, None)
            report.removed_actor_ids.append(actor_id)
        report.removed += len(stale_actor_ids) + len(stale_sensor_ids)

    @staticmethod
    def _external_completeness(category, actor, relative_transform=None):
        missing = []
        warnings = []
        status = ConfigurationStatus.COMPLETE

        if category == ActorCategory.VEHICLE:
            missing.extend([
                "control.autopilot_enabled",
                "control.traffic_manager_port",
            ])
            warnings.append(
                "CARLA does not expose Traffic Manager registration."
            )
            try:
                physics = actor.get_physics_control()
            except (AttributeError, RuntimeError):
                physics = None
            if physics is None:
                missing.append("physics")
                warnings.append("Vehicle physics could not be read.")
            status = ConfigurationStatus.PARTIAL
        elif category == ActorCategory.PEDESTRIAN:
            missing.append("control.intent")
            warnings.append(
                "The active walker controller cannot be inferred reliably."
            )
            status = ConfigurationStatus.PARTIAL
        elif category == ActorCategory.SENSOR:
            missing.append("attachment_type")
            warnings.append("Sensor attachment type is not exposed by CARLA.")
            if relative_transform is None:
                missing.append("relative_transform")
            status = ConfigurationStatus.PARTIAL
        elif category == ActorCategory.WALKER_AI_CONTROLLER:
            missing.extend([
                "control.active",
                "control.target",
                "control.max_speed",
            ])
            warnings.append(
                "Walker AI controller state is not exposed by CARLA."
            )
            status = ConfigurationStatus.PARTIAL
        return status, missing, warnings

    @staticmethod
    def _resolve_parent_relationships(database):
        records = {}
        records.update(database.actors_db.actors_db)
        records.update(database.sensors_db.sensors_db)
        parent_warning = "Parent actor is unavailable or excluded from sync."
        for info in records.values():
            parent_id = getattr(info, "parent_runtime_id", None)
            parent_info = records.get(parent_id)
            info.parent_scenario_id = (
                parent_info.scenario_id if parent_info is not None else None
            )
            if parent_id is not None and parent_info is None:
                if "parent" not in info.missing_fields:
                    info.missing_fields.append("parent")
                if parent_warning not in info.warnings:
                    info.warnings.append(parent_warning)
                info.configuration_status = ConfigurationStatus.UNRESTORABLE
            elif parent_info is not None and parent_warning in info.warnings:
                info.missing_fields = [
                    field for field in info.missing_fields if field != "parent"
                ]
                info.warnings = [
                    warning
                    for warning in info.warnings
                    if warning != parent_warning
                ]
                info.configuration_status = (
                    ConfigurationStatus.PARTIAL
                    if info.missing_fields
                    else ConfigurationStatus.COMPLETE
                )

    @classmethod
    def derive_relative_transform(cls, actor):
        try:
            parent = actor.parent
            if parent is None:
                return None
            parent_transform = parent.get_transform()
            actor_transform = actor.get_transform()
            inverse = parent_transform.get_inverse_matrix()
            child = actor_transform.get_matrix()
            matrix = cls._multiply_matrices(inverse, child)

            import carla

            pitch_value = max(-1.0, min(1.0, float(matrix[2][0])))
            return carla.Transform(
                carla.Location(
                    x=float(matrix[0][3]),
                    y=float(matrix[1][3]),
                    z=float(matrix[2][3]),
                ),
                carla.Rotation(
                    pitch=math.degrees(math.asin(pitch_value)),
                    yaw=math.degrees(math.atan2(
                        float(matrix[1][0]),
                        float(matrix[0][0]),
                    )),
                    roll=math.degrees(math.atan2(
                        -float(matrix[2][1]),
                        float(matrix[2][2]),
                    )),
                ),
            )
        except (AttributeError, ImportError, IndexError, RuntimeError,
                TypeError, ValueError):
            return None

    @staticmethod
    def _multiply_matrices(first, second):
        return [
            [
                sum(float(first[row][index]) * float(second[index][column])
                    for index in range(4))
                for column in range(4)
            ]
            for row in range(4)
        ]
