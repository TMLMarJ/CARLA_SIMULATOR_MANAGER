import time

from scenario_management.actor_classifier import ActorClassifier
from scenario_management.models import ActorOwnership


class SimulationDashboardMonitor:
    VEHICLES = "Vehicles"
    WALKERS = "Walkers"
    SENSORS = "Sensors"
    OTHER = "Other"

    GUI_MANAGED = "GUI Managed"
    EXTERNAL = "External"
    INTERNAL = "Internal"

    STREAMING = "Streaming"
    LISTENING = "Listening"
    INACTIVE = "Inactive"
    ERROR = "Error"
    UNKNOWN = "Unknown"

    EVENT_SENSOR_TYPES = {
        "sensor.other.collision",
        "sensor.other.lane_invasion",
        "sensor.other.obstacle",
        "sensor.other.v2x",
        "sensor.other.v2x_custom",
    }

    DEFAULT_INTERVALS = {
        "simulation_snapshot": 0.25,
        "actors_and_sensors": 1.0,
        "application_status": 0.5,
    }

    def __init__(self, intervals=None, monotonic_clock=None, wall_clock=None):
        self.intervals = self.DEFAULT_INTERVALS.copy()
        if intervals is not None:
            self.intervals.update(intervals)
        for name, interval in self.intervals.items():
            if interval <= 0:
                raise ValueError(f"Dashboard interval {name} must be positive.")

        self.monotonic_clock = monotonic_clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        self.map_name = None
        self.world_revision = 0
        self.last_error = None
        self.last_successful_update = None
        self._next_update = {
            name: 0.0 for name in self.DEFAULT_INTERVALS
        }

    @staticmethod
    def empty_simulation_snapshot(reason="Not connected", world_revision=0):
        return {
            "valid": False,
            "stale": True,
            "stale_reason": reason,
            "world_revision": world_revision,
            "updated_at": None,
            "frame": None,
            "elapsed_seconds": None,
            "delta_seconds": None,
            "map_name": None,
            "synchronous_mode": None,
            "fixed_delta_seconds": None,
            "tick_controller": None,
            "simulation_control_state": "Disconnected",
            "settings_source": "last_confirmed_server_settings",
        }

    @classmethod
    def empty_actor_statistics(cls, reason="Not connected", world_revision=0):
        return {
            "valid": False,
            "stale": True,
            "stale_reason": reason,
            "world_revision": world_revision,
            "updated_at": None,
            "total": 0,
            "categories": {
                category: 0
                for category in (
                    cls.VEHICLES,
                    cls.WALKERS,
                    cls.SENSORS,
                    cls.OTHER,
                )
            },
            "ownership": {
                ownership: 0
                for ownership in (
                    cls.GUI_MANAGED,
                    cls.EXTERNAL,
                    cls.INTERNAL,
                )
            },
            "actors": [],
        }

    @classmethod
    def empty_sensor_statistics(cls, reason="Not connected", world_revision=0):
        return {
            "valid": False,
            "stale": True,
            "stale_reason": reason,
            "world_revision": world_revision,
            "updated_at": None,
            "total": 0,
            "statuses": {
                status: 0
                for status in (
                    cls.STREAMING,
                    cls.LISTENING,
                    cls.INACTIVE,
                    cls.ERROR,
                    cls.UNKNOWN,
                )
            },
            "ownership": {
                ownership: 0
                for ownership in (
                    cls.GUI_MANAGED,
                    cls.EXTERNAL,
                    cls.INTERNAL,
                )
            },
            "sensors": [],
        }

    @staticmethod
    def empty_application_status(reason="Not connected"):
        return {
            "valid": False,
            "stale": True,
            "stale_reason": reason,
            "updated_at": None,
            "connection_status": "Disconnected",
            "controller_thread_status": "Stopped",
            "pending_commands": 0,
            "last_error": None,
            "last_successful_update": None,
            "viewer_status": "Closed",
        }

    def reset_world(self, database, world, reason="World changed"):
        self.world_revision += 1
        self.map_name = None
        try:
            self.map_name = world.get_map().name.split("/")[-1]
        except Exception as error:
            self.record_error(error)
        self.invalidate(database, reason, connection_status="Connected")
        for name in self._next_update:
            self._next_update[name] = 0.0

    def disconnect(self, database, reason="Disconnected", error=None):
        if error is not None:
            self.record_error(error)
        self.invalidate(database, reason, connection_status="Disconnected")

    def invalidate(self, database, reason, connection_status="Connected"):
        simulation_snapshot = self.empty_simulation_snapshot(
            reason,
            self.world_revision,
        )
        actor_statistics = self.empty_actor_statistics(
            reason,
            self.world_revision,
        )
        sensor_statistics = self.empty_sensor_statistics(
            reason,
            self.world_revision,
        )
        application_status = self.empty_application_status(reason)
        application_status.update({
            "connection_status": connection_status,
            "controller_thread_status": (
                "Running" if connection_status == "Connected" else "Stopped"
            ),
            "last_error": self.last_error,
            "last_successful_update": self.last_successful_update,
        })
        with database.lock:
            database.simulation_snapshot = simulation_snapshot
            database.actor_statistics = actor_statistics
            database.sensor_statistics = sensor_statistics
            database.application_status = application_status

    def update(self, world, database, simulation_manager, pending_commands,
               viewer_status, internal_actor_ids, streaming_sensor_ids,
               sensor_errors=None, world_snapshot=None, force=False):
        now = self.monotonic_clock()
        sensor_errors = sensor_errors or {}

        if force or now >= self._next_update["simulation_snapshot"]:
            self._next_update["simulation_snapshot"] = (
                now + self.intervals["simulation_snapshot"]
            )
            try:
                snapshot = world_snapshot or world.get_snapshot()
                with database.lock:
                    settings = dict(database.actual_simulation_settings or {})
                control_state = simulation_manager.snapshot()
                data = self.build_simulation_snapshot(
                    snapshot,
                    self.map_name,
                    settings,
                    control_state,
                    self.world_revision,
                    self.wall_clock(),
                )
                with database.lock:
                    database.simulation_snapshot = data
                self._mark_success(data["updated_at"])
            except Exception as error:
                self.record_error(error)

        if force or now >= self._next_update["actors_and_sensors"]:
            self._next_update["actors_and_sensors"] = (
                now + self.intervals["actors_and_sensors"]
            )
            try:
                server_actors = list(world.get_actors())
                with database.lock:
                    managed_actor_ids = {
                        actor_id
                        for actor_id, actor_info
                        in database.actors_db.actors_db.items()
                        if getattr(
                            actor_info,
                            "ownership",
                            ActorOwnership.MANAGED,
                        ) == ActorOwnership.MANAGED
                    }
                    managed_sensor_ids = {
                        sensor_id
                        for sensor_id, sensor_info
                        in database.sensors_db.sensors_db.items()
                        if getattr(
                            sensor_info,
                            "ownership",
                            ActorOwnership.MANAGED,
                        ) == ActorOwnership.MANAGED
                    }
                updated_at = self.wall_clock()
                actor_statistics, sensor_statistics = (
                    self.build_actor_and_sensor_statistics(
                        server_actors,
                        managed_actor_ids,
                        managed_sensor_ids,
                        set(internal_actor_ids),
                        set(streaming_sensor_ids),
                        sensor_errors,
                        self.world_revision,
                        updated_at,
                    )
                )
                with database.lock:
                    database.actor_statistics = actor_statistics
                    database.sensor_statistics = sensor_statistics
                self._mark_success(updated_at)
            except Exception as error:
                self.record_error(error)

        if force or now >= self._next_update["application_status"]:
            self._next_update["application_status"] = (
                now + self.intervals["application_status"]
            )
            updated_at = self.wall_clock()
            application_status = {
                "valid": True,
                "stale": False,
                "stale_reason": None,
                "updated_at": updated_at,
                "connection_status": "Connected",
                "controller_thread_status": "Running",
                "pending_commands": int(pending_commands),
                "last_error": self.last_error,
                "last_successful_update": self.last_successful_update,
                "viewer_status": viewer_status,
            }
            with database.lock:
                database.application_status = application_status

    def record_error(self, error):
        self.last_error = str(error)

    def _mark_success(self, updated_at):
        self.last_successful_update = updated_at

    @staticmethod
    def build_simulation_snapshot(snapshot, map_name, settings, control_state,
                                  world_revision, updated_at):
        timestamp = getattr(snapshot, "timestamp", None)
        elapsed_seconds = getattr(timestamp, "elapsed_seconds", None)
        delta_seconds = getattr(timestamp, "delta_seconds", None)
        return {
            "valid": True,
            "stale": False,
            "stale_reason": None,
            "world_revision": world_revision,
            "updated_at": updated_at,
            "frame": int(snapshot.frame),
            "elapsed_seconds": (
                None if elapsed_seconds is None else float(elapsed_seconds)
            ),
            "delta_seconds": (
                None if delta_seconds is None else float(delta_seconds)
            ),
            "map_name": map_name,
            "synchronous_mode": settings.get("synchronous_mode"),
            "fixed_delta_seconds": settings.get("fixed_delta_seconds"),
            "tick_controller": control_state.get("tick_controller"),
            "simulation_control_state": control_state.get("state"),
            "settings_source": "last_confirmed_server_settings",
        }

    @classmethod
    def build_actor_and_sensor_statistics(
        cls,
        actors,
        managed_actor_ids,
        managed_sensor_ids,
        internal_actor_ids,
        streaming_sensor_ids,
        sensor_errors,
        world_revision,
        updated_at,
    ):
        actor_statistics = cls.empty_actor_statistics(
            world_revision=world_revision
        )
        sensor_statistics = cls.empty_sensor_statistics(
            world_revision=world_revision
        )
        for result in (actor_statistics, sensor_statistics):
            result.update({
                "valid": True,
                "stale": False,
                "stale_reason": None,
                "updated_at": updated_at,
            })

        gui_managed_ids = set(managed_actor_ids) | set(managed_sensor_ids)
        for actor in actors:
            actor_id = int(actor.id)
            type_id = str(actor.type_id)
            category = cls.classify_actor(type_id)
            ownership = cls.classify_ownership(
                actor_id,
                gui_managed_ids,
                internal_actor_ids,
            )
            actor_entry = {
                "actor_id": actor_id,
                "type_id": type_id,
                "category": category,
                "ownership": ownership,
            }
            actor_statistics["actors"].append(actor_entry)
            actor_statistics["categories"][category] += 1
            actor_statistics["ownership"][ownership] += 1

            if category != cls.SENSORS:
                continue

            parent_id = cls._get_parent_id(actor)
            status = cls.sensor_status(
                actor,
                ownership,
                actor_id in streaming_sensor_ids,
                sensor_errors.get(actor_id),
            )
            sensor_entry = {
                "actor_id": actor_id,
                "type_id": type_id,
                "parent_id": parent_id,
                "ownership": ownership,
                "status": status,
            }
            sensor_statistics["sensors"].append(sensor_entry)
            sensor_statistics["statuses"][status] += 1
            sensor_statistics["ownership"][ownership] += 1

        actor_statistics["total"] = len(actor_statistics["actors"])
        sensor_statistics["total"] = len(sensor_statistics["sensors"])
        return actor_statistics, sensor_statistics

    @classmethod
    def classify_actor(cls, type_id):
        return ActorClassifier.dashboard_category(type_id)

    @classmethod
    def classify_ownership(cls, actor_id, gui_managed_ids, internal_actor_ids):
        if actor_id in internal_actor_ids:
            return cls.INTERNAL
        if actor_id in gui_managed_ids:
            return cls.GUI_MANAGED
        return cls.EXTERNAL

    @classmethod
    def sensor_status(cls, sensor, ownership, streaming, error=None):
        if error is not None:
            return cls.ERROR
        try:
            is_alive = bool(sensor.is_alive)
        except Exception:
            return cls.ERROR if ownership != cls.EXTERNAL else cls.UNKNOWN
        if not is_alive:
            return cls.INACTIVE
        if ownership == cls.EXTERNAL:
            return cls.UNKNOWN
        if streaming:
            if sensor.type_id in cls.EVENT_SENSOR_TYPES:
                return cls.LISTENING
            return cls.STREAMING
        try:
            return cls.LISTENING if sensor.is_listening() else cls.INACTIVE
        except Exception:
            return cls.ERROR

    @staticmethod
    def _get_parent_id(actor):
        try:
            parent = actor.parent
            return None if parent is None else int(parent.id)
        except Exception:
            return None
