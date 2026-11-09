import carla
import copy
import math
import os
import queue
import socket
import threading
import time

from utilities.functions import *
from preview.movement import camera_transform_for_actor
from movement.destination import (
    DESTINATION_ADJUSTED,
    DESTINATION_ERROR,
    DESTINATION_VALID,
    confirm_destination,
    destination_result,
    empty_destination,
    point_from_location,
    point_from_values,
    resolve_vehicle_waypoint,
    same_waypoint_segment,
    validate_pedestrian_destination,
    validate_vehicle_destination,
    waypoint_snapshot,
)
from movement.vehicle import (
    AUTOPILOT,
    AUTOPILOT_DESTINATION,
    AUTOPILOT_FREE_ROAM,
    MANUAL_CONTROL,
    NO_CONTROL,
    VEHICLE_CONTROL_MODES,
    VEHICLE_STATUS_ARRIVED,
    VEHICLE_STATUS_CANCELLED,
    VEHICLE_STATUS_FAILED,
    VEHICLE_STATUS_IDLE,
    VEHICLE_STATUS_NAVIGATING,
    VEHICLE_STATUS_STUCK,
    normalize_vehicle_control_mode,
)
from movement.walker import (
    MAX_WALKER_SPEED,
    MIN_WALKER_SPEED,
    WALKER_AI_ERROR,
    WALKER_AI_RUNNING,
    WALKER_AI_STOPPED,
    WALKER_CONTROL_DESTINATION,
    WALKER_CONTROL_MANUAL,
    WALKER_CONTROL_MODES,
    WALKER_CONTROL_RANDOM,
    WALKER_STATUS_ARRIVED,
    WALKER_STATUS_CANCELLED,
    WALKER_STATUS_FAILED,
    WALKER_STATUS_IDLE,
    WALKER_STATUS_MOVING,
    WALKER_STATUS_STUCK,
)

_TM_DEBUG_PROCESS_PORTS = {}
_TM_EXCLUSIVE_OWNERS = {}
_TM_EXCLUSIVE_OWNERS_LOCK = threading.Lock()


class MovementRuntime:
    NAVIGATION_DEBUG_ENV = "CARLA_MANAGER_NAV_DEBUG"
    TRAFFIC_MANAGER_DEBUG_ENV = "CARLA_MANAGER_TM_DEBUG"
    NAVIGATION_DEBUG_INTERVAL_SECONDS = 5.0
    NAVIGATION_DEBUG_TRAJECTORY_LIMIT = 128

    def _configure_traffic_manager(self):
        default_port = int(type(self).TRAFFIC_MANAGER_PORT)
        raw_port = str(os.environ.get("CARLA_MANAGER_TM_PORT", "")).strip()
        raw_exclusive = str(
            os.environ.get("CARLA_MANAGER_TM_EXCLUSIVE", "")
        ).strip().casefold()
        exclusive_values = {
            "1", "true", "yes", "on",
        }
        shared_values = {"", "0", "false", "no", "off"}
        self.traffic_manager_exclusive = raw_exclusive in exclusive_values
        self.traffic_manager_configuration_error = (
            None
            if raw_exclusive in exclusive_values | shared_values
            else (
                "CARLA_MANAGER_TM_EXCLUSIVE must be one of: "
                "1/true/yes/on or 0/false/no/off."
            )
        )
        try:
            configured_port = default_port if not raw_port else int(raw_port)
            if not 1 <= configured_port <= 65535:
                raise ValueError("port must be in range 1..65535")
        except (TypeError, ValueError) as error:
            configured_port = default_port
            if self.traffic_manager_configuration_error is None:
                self.traffic_manager_configuration_error = (
                    "CARLA_MANAGER_TM_PORT is invalid: " + str(error)
                )
        if (
            self.traffic_manager_configuration_error is None
            and self.traffic_manager_exclusive
            and configured_port == default_port
        ):
            self.traffic_manager_configuration_error = (
                "CARLA_MANAGER_TM_EXCLUSIVE requires an explicitly reserved "
                f"port different from the shared default {default_port}."
            )
        self.TRAFFIC_MANAGER_PORT = configured_port
        self.traffic_manager_exclusive_owned = False
        self.traffic_manager_owner_controller_id = None

    def _traffic_manager_mode(self):
        return (
            "exclusive"
            if getattr(self, "traffic_manager_exclusive", False)
            else "shared"
        )

    @staticmethod
    def _exclusive_tm_port_is_available(port):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_EXCLUSIVEADDRUSE,
                    1,
                )
            probe.bind(("127.0.0.1", int(port)))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def _claim_exclusive_tm_contract(self):
        port = int(self.TRAFFIC_MANAGER_PORT)
        controller_id = id(self)
        with _TM_EXCLUSIVE_OWNERS_LOCK:
            owner = _TM_EXCLUSIVE_OWNERS.get(port)
            if owner is not None and owner != controller_id:
                raise RuntimeError(
                    f"Exclusive Traffic Manager port {port} is already "
                    "owned by another controller in this GUI process."
                )
            if not self._exclusive_tm_port_is_available(port):
                raise RuntimeError(
                    f"Exclusive Traffic Manager port {port} is already in "
                    "use. Ownership cannot be proven; refusing to attach or "
                    "shut down the unknown instance."
                )
            _TM_EXCLUSIVE_OWNERS[port] = controller_id

    def _release_exclusive_tm_contract(self):
        port = int(self.TRAFFIC_MANAGER_PORT)
        with _TM_EXCLUSIVE_OWNERS_LOCK:
            if _TM_EXCLUSIVE_OWNERS.get(port) == id(self):
                _TM_EXCLUSIVE_OWNERS.pop(port, None)
        self.traffic_manager_exclusive_owned = False
        self.traffic_manager_owner_controller_id = None

    def _shutdown_exclusive_traffic_manager(self, reason, strict=True):
        if not getattr(self, "traffic_manager_exclusive", False):
            return False
        traffic_manager = self.traffic_manager
        if traffic_manager is None:
            self._release_exclusive_tm_contract()
            return False
        owned = (
            getattr(self, "traffic_manager_exclusive_owned", False)
            and getattr(self, "traffic_manager_owner_controller_id", None)
            == id(self)
        )
        if not owned:
            error = RuntimeError(
                "Exclusive Traffic Manager proxy has unknown ownership; "
                "refusing to call shut_down()."
            )
            self._tm_debug_event(
                "tm_shutdown_error",
                reason=reason,
                message=str(error),
            )
            if strict:
                raise error
            self.dashboard_monitor.record_error(error)
            return False
        self._tm_debug_event("tm_shutdown_begin", reason=reason)
        try:
            traffic_manager.shut_down()
        except Exception as error:
            self._tm_debug_event(
                "tm_shutdown_error",
                reason=reason,
                message=str(error),
            )
            if strict:
                raise RuntimeError(
                    "Could not stop the application-owned Traffic Manager; "
                    "world transition was cancelled."
                ) from error
            self.dashboard_monitor.record_error(error)
            return False
        self._release_exclusive_tm_contract()
        self.traffic_manager = None
        self.traffic_manager_world_id = None
        self._tm_debug_event("tm_shutdown_complete", reason=reason)
        return True

    def _movement_command_is_stale(self, command, data, world):
        if command not in self.MOVEMENT_WORLD_COMMANDS:
            return False
        transition = getattr(self, "world_transition_event", None)
        if transition is not None and transition.is_set():
            return True
        if not isinstance(data, dict):
            return True
        command_world_id = data.get("world_id")
        command_generation = data.get("world_generation")
        current_world_id = self._world_id(world)
        if command_world_id is None or current_world_id is None:
            return False
        try:
            with self.database.lock:
                current_generation = self.database.world_generation
            return (
                int(command_world_id) != current_world_id
                or command_generation is None
                or int(command_generation) != int(current_generation)
            )
        except (TypeError, ValueError):
            return True

    def _movement_world_token(self):
        with self.database.lock:
            world_id = getattr(self.database, "world_id", None)
            generation = getattr(self.database, "world_generation", None)
        try:
            return int(world_id), int(generation)
        except (TypeError, ValueError):
            return None, None

    def _movement_world_id(self):
        world_id, _generation = self._movement_world_token()
        return world_id

    def _queue_movement_command(self, command, data):
        transition = getattr(self, "world_transition_event", None)
        if transition is not None and transition.is_set():
            return False
        world_id, world_generation = self._movement_world_token()
        if world_id is None or world_generation is None:
            return False
        payload = dict(data)
        payload["world_id"] = world_id
        payload["world_generation"] = world_generation
        if transition is not None and transition.is_set():
            return False
        self.command_queue.put((command, payload))
        return True

    def _manual_control_is_active(self):
        actor_id = self.movement_preview_actor_id
        if actor_id is None or self.movement_control_queue is None:
            return False
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            return state is not None and state.get("mode") == MANUAL_CONTROL

    def _publish_vehicle_control_state(self, actor_id, session_id=None,
                                       error=None):
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            state = state.copy() if state is not None else None
        if state is None:
            return
        if session_id is None and actor_id == self.movement_preview_actor_id:
            session_id = self.movement_preview_session_id
        if session_id is None:
            return
        world_id, world_generation = self._movement_world_token()
        status = "vehicle_control_error" if error else "vehicle_control_state"
        self.database.movement_event_queue.put((status, {
            "session_id": session_id,
            "actor_id": actor_id,
            "world_id": world_id,
            "world_generation": world_generation,
            "control_state": state,
            "message": str(error) if error else None,
        }))

    def _get_managed_vehicle(self, actor_id):
        with self.database.lock:
            actor = self.database.actors_db.actors.get(actor_id)
            state = self.database.vehicle_control_states.get(actor_id)
        if state is None:
            raise RuntimeError(
                "The selected vehicle is not managed by this application."
            )
        if (
            actor is None
            or not getattr(actor, "is_alive", False)
            or not actor.type_id.startswith("vehicle.")
        ):
            raise RuntimeError("The selected vehicle is no longer available.")
        return actor

    def _ensure_traffic_manager(self, world):
        configuration_error = getattr(
            self,
            "traffic_manager_configuration_error",
            None,
        )
        if configuration_error is not None:
            raise RuntimeError(configuration_error)
        created_wrapper = self.traffic_manager is None
        debug_map_name = None
        if self._tm_debug_enabled():
            try:
                debug_map_name = str(world.get_map().name)
            except Exception:
                debug_map_name = "<unavailable>"
        self._tm_debug_event(
            "tm_initialize_begin",
            world=world,
            map_name=debug_map_name,
            wrapper_cached=not created_wrapper,
        )
        if self.traffic_manager is None:
            exclusive = getattr(self, "traffic_manager_exclusive", False)
            if exclusive:
                self._claim_exclusive_tm_contract()
            try:
                self.traffic_manager = self.client.get_trafficmanager(
                    self.TRAFFIC_MANAGER_PORT
                )
            except Exception:
                if exclusive:
                    self._release_exclusive_tm_contract()
                raise
            if exclusive:
                self.traffic_manager_exclusive_owned = True
                self.traffic_manager_owner_controller_id = id(self)
            self._tm_debug_event(
                "tm_proxy_acquired",
                world=world,
                map_name=debug_map_name,
                wrapper_created=True,
                ownership_contract=(
                    "application_exclusive"
                    if exclusive else "shared_unknown"
                ),
            )
        world_sync = bool(world.get_settings().synchronous_mode)
        self.traffic_manager.set_synchronous_mode(world_sync)
        self.traffic_manager_last_sync = world_sync
        self.traffic_manager_world_id = self._world_id(world)
        self._tm_debug_event(
            "tm_sync_applied",
            world=world,
            map_name=debug_map_name,
            requested_sync=world_sync,
        )
        previous = None
        if self._tm_debug_enabled():
            previous = _TM_DEBUG_PROCESS_PORTS.get(self.TRAFFIC_MANAGER_PORT)
            _TM_DEBUG_PROCESS_PORTS[self.TRAFFIC_MANAGER_PORT] = {
                "controller_id": id(self),
                "client_id": id(self.client),
                "python_tm_id": id(self.traffic_manager),
                "world_id": self.traffic_manager_world_id,
            }
        self._tm_debug_event(
            "tm_initialize_complete",
            world=world,
            map_name=debug_map_name,
            wrapper_created=created_wrapper,
            process_port_seen_before=previous is not None,
            previous_process_port=previous,
        )
        return self.traffic_manager

    def _update_traffic_manager_sync(self, world):
        if self.traffic_manager is None:
            return
        world_sync = bool(world.get_settings().synchronous_mode)
        self.traffic_manager.set_synchronous_mode(world_sync)
        self.traffic_manager_last_sync = world_sync
        self.traffic_manager_world_id = self._world_id(world)

    def _invalidate_traffic_manager_reference(self):
        """Drop only this client's proxy; never reset the shared TM server."""
        was_initialized = self.traffic_manager is not None
        self._tm_debug_event(
            "tm_local_reference_cleanup",
            was_initialized=was_initialized,
        )
        self.traffic_manager = None
        self.traffic_manager_world_id = None
        return was_initialized

    @classmethod
    def _tm_debug_enabled(cls):
        value = str(os.environ.get(cls.TRAFFIC_MANAGER_DEBUG_ENV, "")).strip()
        return value.casefold() in {"1", "true", "yes", "on"}

    def _tm_debug_event(self, event, world=None, map_name=None, **details):
        """Emit sparse worker-side TM lifecycle diagnostics."""
        if not self._tm_debug_enabled():
            return
        with self.database.lock:
            generation = getattr(self.database, "world_generation", None)
            settings = getattr(
                self.database,
                "actual_simulation_settings",
                None,
            )
        world_id = self._world_id(world)
        if world_id is None:
            world_id = getattr(self, "traffic_manager_world_id", None)
        if map_name is None and world is not None:
            try:
                map_name = str(world.get_map().name)
            except Exception:
                map_name = None
        world_sync = (
            settings.get("synchronous_mode")
            if isinstance(settings, dict) else None
        )
        fixed_delta = (
            settings.get("fixed_delta_seconds")
            if isinstance(settings, dict) else None
        )
        payload = {
            "event": str(event),
            "process_id": os.getpid(),
            "controller_id": id(self),
            "client_id": id(self.client) if self.client is not None else None,
            "world_id": world_id,
            "world_generation": generation,
            "map": map_name,
            "tm_port": self.TRAFFIC_MANAGER_PORT,
            "tm_mode": self._traffic_manager_mode(),
            "python_tm_id": (
                id(self.traffic_manager)
                if self.traffic_manager is not None else None
            ),
            "tm_ownership": (
                "application_exclusive_contract"
                if getattr(self, "traffic_manager_exclusive_owned", False)
                else "unknown_not_exposed_by_carla_api"
            ),
            "world_sync": world_sync,
            "fixed_delta_seconds": fixed_delta,
            "tm_last_sync": getattr(
                self,
                "traffic_manager_last_sync",
                None,
            ),
            "navigation_runtime_count": len(getattr(
                self,
                "vehicle_navigation_runtime",
                {},
            )),
            "movement_session_active": (
                getattr(self, "movement_preview_session_id", None) is not None
            ),
        }
        payload.update(details)
        fields = " ".join(
            f"{key}={value!r}" for key, value in payload.items()
        )
        print(f"[TM DEBUG] {fields}")

    def _safe_stop_vehicle(self, actor, reverse=False):
        actor.apply_control(carla.VehicleControl(
            throttle=0.0,
            steer=0.0,
            brake=1.0,
            hand_brake=False,
            reverse=bool(reverse),
            manual_gear_shift=False,
        ))

    @staticmethod
    def _vehicle_xy_distance(first, second):
        return math.hypot(
            float(first.x) - float(second.x),
            float(first.y) - float(second.y),
        )

    @staticmethod
    def _vehicle_speed(actor):
        velocity = actor.get_velocity()
        return math.sqrt(
            float(velocity.x) ** 2
            + float(velocity.y) ** 2
            + float(velocity.z) ** 2
        )

    @classmethod
    def _navigation_debug_enabled(cls):
        value = str(os.environ.get(cls.NAVIGATION_DEBUG_ENV, "")).strip()
        return value.casefold() in {"1", "true", "yes", "on"}

    @staticmethod
    def _navigation_debug_waypoint(waypoint):
        if not isinstance(waypoint, dict):
            return None
        return {
            field: waypoint.get(field)
            for field in ("road_id", "section_id", "lane_id", "s")
        }

    def _traffic_manager_actions_snapshot(self, actor):
        getter = getattr(self.traffic_manager, "get_all_actions", None)
        if not callable(getter):
            return {"available": False, "count": None, "next": None}
        try:
            actions = list(getter(actor) or [])
            first = actions[0] if actions else None
            if isinstance(first, (tuple, list)) and len(first) >= 2:
                next_action = {
                    "road_option": str(first[0]),
                    "waypoint": waypoint_snapshot(first[1]),
                }
            else:
                next_action = None
            return {
                "available": True,
                "count": len(actions),
                "next": next_action,
            }
        except Exception as error:
            return {
                "available": False,
                "count": None,
                "next": None,
                "error": str(error),
            }

    def _update_vehicle_navigation_debug(self, actor_id, actor, runtime,
                                         location, current_waypoint,
                                         route_index, now, force=False):
        if not self._navigation_debug_enabled():
            return
        trajectory = runtime.setdefault("actual_trajectory", [])
        point = point_from_location(location)
        if (
            not trajectory
            or math.hypot(
                point["x"] - trajectory[-1]["x"],
                point["y"] - trajectory[-1]["y"],
            ) >= 1.0
        ):
            trajectory.append(point)
            del trajectory[:-self.NAVIGATION_DEBUG_TRAJECTORY_LIMIT]

        next_log = float(runtime.get("next_debug_log", 0.0))
        if not force and now < next_log:
            return
        runtime["next_debug_log"] = (
            now + self.NAVIGATION_DEBUG_INTERVAL_SECONDS
        )
        planned_route = runtime.get("route") or []
        route_waypoints = runtime.get("route_waypoints") or []
        nearest_route_index = max(
            0,
            min(int(route_index), len(planned_route)),
        )
        remaining_route_index = max(
            0,
            min(
                int(runtime.get("max_route_index", nearest_route_index)),
                len(planned_route),
            ),
        )
        deviation = min(
            (
                self._vehicle_xy_distance(location, route_point)
                for route_point in planned_route
            ),
            default=None,
        )
        current_snapshot = waypoint_snapshot(current_waypoint)
        tm_actions = self._traffic_manager_actions_snapshot(actor)
        diagnostics = {
            "enabled": True,
            "actor_id": int(actor_id),
            "session_id": self.movement_preview_session_id,
            "mode": AUTOPILOT_DESTINATION,
            "executor": "TrafficManager.set_path(carla.Location[])",
            "autopilot_enabled": True,
            "path_submission_id": runtime.get("path_submission_id"),
            "path_submission_count": runtime.get("path_submission_count", 1),
            "planned_route": {
                "waypoint_count": len(planned_route),
                "start": self._navigation_debug_waypoint(
                    route_waypoints[0] if route_waypoints else None
                ),
                "target": self._navigation_debug_waypoint(
                    runtime.get("target_waypoint")
                ),
            },
            "remaining_route": {
                "start_index": remaining_route_index,
                "nearest_route_index": nearest_route_index,
                "waypoint_count": max(
                    0,
                    len(planned_route) - remaining_route_index,
                ),
                "next": self._navigation_debug_waypoint(
                    route_waypoints[remaining_route_index]
                    if remaining_route_index < len(route_waypoints) else None
                ),
            },
            "actual_trajectory": list(trajectory),
            "current_waypoint": self._navigation_debug_waypoint(
                current_snapshot
            ),
            "distance_to_planned_route": deviation,
            "traffic_manager_actions": tm_actions,
        }
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            if state is not None:
                state["navigation_debug"] = diagnostics
        tm_next = tm_actions.get("next") or {}
        tm_next_waypoint = tm_next.get("waypoint") or {}
        print(
            "[NAV DEBUG] "
            f"actor={actor_id} mode={AUTOPILOT_DESTINATION!r} "
            f"submission={runtime.get('path_submission_id')} "
            f"planned={len(planned_route)} "
            f"remaining={max(0, len(planned_route) - remaining_route_index)} "
            f"nearest_index={nearest_route_index} "
            f"progress_index={remaining_route_index} "
            f"current={current_snapshot.get('road_id') if current_snapshot else None}/"
            f"{current_snapshot.get('lane_id') if current_snapshot else None} "
            f"deviation={None if deviation is None else round(deviation, 2)}m "
            f"tm_actions={tm_actions.get('count')} "
            f"tm_next={tm_next_waypoint.get('road_id')}/"
            f"{tm_next_waypoint.get('lane_id')}"
        )

    @classmethod
    def _nearest_route_index(cls, location, route):
        if not route:
            return 0
        return min(
            range(len(route)),
            key=lambda index: cls._vehicle_xy_distance(location, route[index]),
        )

    def _vehicle_destination_for_world(self, actor_id, world):
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            destination = None if state is None else state.get("destination")
            state_world_id = None if state is None else state.get("world_id")
        if not isinstance(destination, dict) or not destination.get("confirmed"):
            raise RuntimeError("Confirm a valid Destination before starting navigation.")
        world_id = self._world_id(world)
        destination_world_id = destination.get("world_id")
        destination_actor_id = destination.get("actor_id")
        try:
            state_world_id = int(state_world_id)
            destination_world_id = int(destination_world_id)
            destination_actor_id = int(destination_actor_id)
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                "The selected Destination has invalid actor or world metadata."
            ) from error
        if (
            world_id is None
            or state_world_id != world_id
            or destination_world_id != world_id
            or destination_actor_id != actor_id
        ):
            raise RuntimeError(
                "The selected Destination belongs to a different actor or world."
            )
        selected = destination.get("selected")
        if not isinstance(selected, dict):
            raise RuntimeError("The confirmed Destination has no usable point.")
        try:
            return carla.Location(
                x=float(selected["x"]),
                y=float(selected["y"]),
                z=float(selected["z"]),
            ), destination
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("The confirmed Destination coordinates are invalid.") from error

    def _clear_vehicle_navigation_path(self, actor):
        if self.traffic_manager is None:
            return
        try:
            self.traffic_manager.set_path(actor, [], True)
        except Exception as error:
            self.dashboard_monitor.record_error(error)

    def _cancel_vehicle_navigation(self, actor_id, actor=None,
                                   status=VEHICLE_STATUS_CANCELLED,
                                   stop_vehicle=False, clear_path=True):
        runtime_store = getattr(self, "vehicle_navigation_runtime", None)
        if runtime_store is None:
            runtime_store = {}
            self.vehicle_navigation_runtime = runtime_store
        runtime = runtime_store.pop(actor_id, None)
        if (
            clear_path
            and actor is not None
            and getattr(actor, "is_alive", False)
        ):
            self._clear_vehicle_navigation_path(actor)
        if (
            stop_vehicle
            and actor is not None
            and getattr(actor, "is_alive", False)
        ):
            actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
            self._safe_stop_vehicle(actor)
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            if state is not None:
                state.update({
                    "navigation_status": status,
                    "route": None,
                    "navigation_debug": None,
                    "distance_to_destination": (
                        state.get("distance_to_destination")
                        if runtime is not None else None
                    ),
                })

    def _activate_vehicle_destination(self, world, actor, actor_id,
                                      destination_location, destination,
                                      path, target_waypoint,
                                      route_waypoints):
        traffic_manager = self._ensure_traffic_manager(world)
        execution_path = [
            carla.Location(
                x=float(point.x),
                y=float(point.y),
                z=float(point.z),
            )
            for point in path
        ]
        actor.set_autopilot(True, self.TRAFFIC_MANAGER_PORT)
        traffic_manager.set_path(actor, execution_path, True)
        current_location = actor.get_location()
        target_snapshot = waypoint_snapshot(target_waypoint)
        destination = dict(destination)
        destination["target_waypoint"] = target_snapshot
        route_target = target_waypoint.transform.location
        distance = self._vehicle_xy_distance(current_location, route_target)
        world_id = self._world_id(world)
        now = time.monotonic()
        runtime_store = getattr(self, "vehicle_navigation_runtime", None)
        if runtime_store is None:
            runtime_store = {}
            self.vehicle_navigation_runtime = runtime_store
        submission_counts = getattr(
            self,
            "vehicle_navigation_submission_counts",
            None,
        )
        if submission_counts is None:
            submission_counts = {}
            self.vehicle_navigation_submission_counts = submission_counts
        submission_counts[actor_id] = submission_counts.get(actor_id, 0) + 1
        submission_sequence = int(getattr(
            self,
            "vehicle_navigation_submission_sequence",
            0,
        )) + 1
        self.vehicle_navigation_submission_sequence = submission_sequence
        runtime_store[actor_id] = {
            "world_id": world_id,
            "destination": carla.Location(
                x=float(route_target.x),
                y=float(route_target.y),
                z=float(route_target.z),
            ),
            "route": tuple(path),
            "target_waypoint": target_snapshot,
            "route_waypoints": route_waypoints,
            "max_route_index": self._nearest_route_index(current_location, path),
            "best_distance": distance,
            "next_check": 0.0,
            "last_location": current_location,
            "last_progress_time": now,
            "last_frame": None,
            "arrival_braking": False,
            "path_submission_id": submission_sequence,
            "path_submission_count": submission_counts[actor_id],
            "actual_trajectory": [point_from_location(current_location)],
            "next_debug_log": 0.0,
        }
        with self.database.lock:
            state = self.database.vehicle_control_states[actor_id]
            state.update({
                "mode": AUTOPILOT_DESTINATION,
                "control_mode": AUTOPILOT_DESTINATION,
                "status": "Destination navigation is active.",
                "navigation_status": VEHICLE_STATUS_NAVIGATING,
                "destination": destination,
                "route": {
                    "kind": "planned",
                    "waypoint_count": len(path),
                    "sampling_resolution": self.VEHICLE_NAVIGATION_SAMPLING_RESOLUTION,
                    "waypoints": route_waypoints,
                },
                "target_waypoint": target_snapshot,
                "distance_to_destination": distance,
                "current_speed": self._vehicle_speed(actor),
                "world_id": world_id,
                "autopilot_enabled": True,
                "autopilot_state_source": "local",
                "traffic_manager_port": self.TRAFFIC_MANAGER_PORT,
                "input_fresh": False,
                "error": None,
                "navigation_debug": None,
            })
        if self._navigation_debug_enabled():
            self._update_vehicle_navigation_debug(
                actor_id,
                actor,
                runtime_store[actor_id],
                current_location,
                world.get_map().get_waypoint(
                    current_location,
                    project_to_road=True,
                    lane_type=getattr(
                        carla.LaneType,
                        "Driving",
                        carla.LaneType.Any,
                    ),
                ),
                runtime_store[actor_id]["max_route_index"],
                now,
                force=True,
            )

    def _service_vehicle_navigation(self, world, world_snapshot=None):
        transition = getattr(self, "world_transition_event", None)
        if transition is not None and transition.is_set():
            return
        runtime_store = getattr(self, "vehicle_navigation_runtime", None)
        if not runtime_store:
            return
        world_id = self._world_id(world)
        frame = getattr(world_snapshot, "frame", None)
        now = time.monotonic()

        for actor_id, runtime in list(runtime_store.items()):
            if runtime.get("world_id") != world_id:
                runtime_store.pop(actor_id, None)
                with self.database.lock:
                    state = self.database.vehicle_control_states.get(actor_id)
                    if state is not None:
                        state.update({
                            "navigation_status": VEHICLE_STATUS_CANCELLED,
                            "status": (
                                "Destination navigation cancelled because "
                                "the CARLA world changed."
                            ),
                            "route": None,
                            "autopilot_enabled": False,
                            "error": None,
                        })
                continue
            if frame is not None and runtime.get("last_frame") == frame:
                continue
            runtime["last_frame"] = frame
            if now < runtime.get("next_check", 0.0):
                continue
            runtime["next_check"] = now + self.VEHICLE_NAVIGATION_CHECK_INTERVAL_SECONDS
            actor = None
            try:
                actor = self._get_managed_vehicle(actor_id)
                with self.database.lock:
                    state = self.database.vehicle_control_states.get(actor_id)
                    mode = None if state is None else state.get("mode")
                if mode != AUTOPILOT_DESTINATION:
                    runtime_store.pop(actor_id, None)
                    continue

                location = actor.get_location()
                destination = runtime["destination"]
                distance = self._vehicle_xy_distance(location, destination)
                speed = self._vehicle_speed(actor)
                current_waypoint = world.get_map().get_waypoint(
                    location,
                    project_to_road=True,
                    lane_type=getattr(
                        carla.LaneType,
                        "Driving",
                        carla.LaneType.Any,
                    ),
                )
                current_waypoint_snapshot = waypoint_snapshot(current_waypoint)
                on_target_segment = same_waypoint_segment(
                    current_waypoint_snapshot,
                    runtime["target_waypoint"],
                )
                route_index = self._nearest_route_index(location, runtime["route"])
                made_progress = route_index > runtime["max_route_index"]
                if made_progress:
                    runtime["max_route_index"] = route_index
                if (
                    distance
                    <= runtime["best_distance"]
                    - self.VEHICLE_DESTINATION_PROGRESS_DISTANCE
                ):
                    runtime["best_distance"] = distance
                    made_progress = True
                if made_progress:
                    runtime["last_location"] = location
                    runtime["last_progress_time"] = now

                self._update_vehicle_navigation_debug(
                    actor_id,
                    actor,
                    runtime,
                    location,
                    current_waypoint,
                    route_index,
                    now,
                )

                if (
                    not runtime["arrival_braking"]
                    and distance <= self.VEHICLE_DESTINATION_ARRIVAL_DISTANCE
                    and on_target_segment
                ):
                    actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                    actor.apply_control(carla.VehicleControl(
                        throttle=0.0,
                        steer=0.0,
                        brake=0.6,
                        hand_brake=False,
                        manual_gear_shift=False,
                    ))
                    runtime["arrival_braking"] = True

                navigation_status = VEHICLE_STATUS_NAVIGATING
                status = "Destination navigation is active."
                if runtime["arrival_braking"]:
                    status = "Destination reached; vehicle is braking."
                    if (
                        distance <= self.VEHICLE_DESTINATION_STOP_DISTANCE
                        and speed <= self.VEHICLE_DESTINATION_STOP_SPEED
                    ):
                        actor.apply_control(carla.VehicleControl(
                            throttle=0.0,
                            steer=0.0,
                            brake=1.0,
                            hand_brake=True,
                            manual_gear_shift=False,
                        ))
                        navigation_status = VEHICLE_STATUS_ARRIVED
                        status = "Destination reached; vehicle stopped."
                        runtime_store.pop(actor_id, None)
                    elif speed <= self.VEHICLE_DESTINATION_STOP_SPEED:
                        actor.apply_control(carla.VehicleControl(
                            throttle=0.0,
                            steer=0.0,
                            brake=1.0,
                            hand_brake=True,
                            manual_gear_shift=False,
                        ))
                        navigation_status = VEHICLE_STATUS_FAILED
                        status = (
                            "Vehicle stopped beyond the Destination tolerance."
                        )
                        runtime_store.pop(actor_id, None)
                elif (
                    now - runtime["last_progress_time"]
                    >= self.VEHICLE_DESTINATION_STUCK_SECONDS
                ):
                    actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                    self._safe_stop_vehicle(actor)
                    navigation_status = VEHICLE_STATUS_STUCK
                    status = "Destination navigation stopped: vehicle is stuck."
                    runtime_store.pop(actor_id, None)

                with self.database.lock:
                    state = self.database.vehicle_control_states.get(actor_id)
                    if state is not None:
                        state.update({
                            "navigation_status": navigation_status,
                            "status": status,
                            "distance_to_destination": distance,
                            "current_speed": speed,
                            "current_waypoint": current_waypoint_snapshot,
                            "autopilot_enabled": (
                                navigation_status == VEHICLE_STATUS_NAVIGATING
                                and not runtime["arrival_braking"]
                            ),
                            "error": (
                                status
                                if navigation_status == VEHICLE_STATUS_FAILED
                                else None
                            ),
                        })
                self._publish_vehicle_control_state(actor_id)
            except Exception as error:
                self.dashboard_monitor.record_error(error)
                runtime_store.pop(actor_id, None)
                if actor is not None and getattr(actor, "is_alive", False):
                    try:
                        actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                        self._clear_vehicle_navigation_path(actor)
                        self._safe_stop_vehicle(actor)
                    except Exception as cleanup_error:
                        self.dashboard_monitor.record_error(cleanup_error)
                with self.database.lock:
                    state = self.database.vehicle_control_states.get(actor_id)
                    if state is not None:
                        state.update({
                            "navigation_status": VEHICLE_STATUS_FAILED,
                            "status": f"Destination navigation failed: {error}",
                            "autopilot_enabled": False,
                            "error": str(error),
                        })
                self._publish_vehicle_control_state(actor_id, error=error)

    def _set_vehicle_control_mode(self, world, data):
        session_id = data["session_id"]
        actor_id = int(data["actor_id"])
        requested_mode = normalize_vehicle_control_mode(data["mode"])
        actor = None
        destination_activation_started = False
        try:
            if requested_mode not in VEHICLE_CONTROL_MODES:
                raise ValueError(f"Unknown vehicle control mode: {requested_mode}")
            if (
                session_id != self.movement_preview_session_id
                or actor_id != self.movement_preview_actor_id
            ):
                raise RuntimeError("The Movement Control session is no longer active.")

            actor = self._get_managed_vehicle(actor_id)
            with self.database.lock:
                previous = self.database.vehicle_control_states[actor_id].copy()
            previous_mode = normalize_vehicle_control_mode(
                previous.get("mode", NO_CONTROL)
            )

            prepared_destination = None
            if requested_mode == AUTOPILOT_DESTINATION:
                destination_location, destination = (
                    self._vehicle_destination_for_world(actor_id, world)
                )
                route, target_waypoint, route_waypoints = self._plan_vehicle_route(
                    world,
                    actor,
                    destination_location,
                    destination.get("target_waypoint"),
                )
                prepared_destination = (
                    destination_location,
                    destination,
                    route,
                    target_waypoint,
                    route_waypoints,
                )

            if previous_mode == MANUAL_CONTROL and requested_mode != MANUAL_CONTROL:
                self._safe_stop_vehicle(actor, previous.get("reverse", False))
                with self.database.lock:
                    state = self.database.vehicle_control_states[actor_id]
                    state.update({
                        "mode": NO_CONTROL,
                        "status": "Manual Control ended; vehicle stopped safely.",
                        "input_fresh": False,
                    })
            if (
                previous_mode in (AUTOPILOT_FREE_ROAM, AUTOPILOT_DESTINATION)
                and requested_mode != previous_mode
            ):
                actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                if previous_mode == AUTOPILOT_DESTINATION:
                    self._cancel_vehicle_navigation(actor_id, actor=actor)
                with self.database.lock:
                    state = self.database.vehicle_control_states[actor_id]
                    state.update({
                        "mode": NO_CONTROL,
                        "status": "Autopilot disabled.",
                        "autopilot_enabled": False,
                        "traffic_manager_port": None,
                    })

            if requested_mode == AUTOPILOT_FREE_ROAM:
                self._ensure_traffic_manager(world)
                self._clear_vehicle_navigation_path(actor)
                actor.set_autopilot(True, self.TRAFFIC_MANAGER_PORT)
                self._tm_debug_event(
                    "free_roam_activated",
                    world=world,
                    actor_id=actor_id,
                    autopilot_enabled=True,
                )
                status = (
                    f"Autopilot Free Roam active (Traffic Manager port "
                    f"{self.TRAFFIC_MANAGER_PORT})."
                )
                navigation_status = (
                    VEHICLE_STATUS_CANCELLED
                    if previous_mode == AUTOPILOT_DESTINATION
                    else VEHICLE_STATUS_IDLE
                )
            elif requested_mode == AUTOPILOT_DESTINATION:
                destination_activation_started = True
                self._activate_vehicle_destination(
                    world,
                    actor,
                    actor_id,
                    *prepared_destination,
                )
                self._publish_vehicle_control_state(actor_id, session_id=session_id)
                return
            elif requested_mode == MANUAL_CONTROL:
                self._safe_stop_vehicle(actor, previous.get("reverse", False))
                self.manual_control_command = None
                self.manual_control_received_at = None
                self.manual_control_next_apply = 0.0
                self.manual_control_input_fresh = False
                status = "Manual Control active; waiting for focused Pygame input."
                navigation_status = (
                    VEHICLE_STATUS_CANCELLED
                    if previous_mode == AUTOPILOT_DESTINATION
                    else VEHICLE_STATUS_IDLE
                )
            else:
                if previous_mode == AUTOPILOT_DESTINATION:
                    self._safe_stop_vehicle(actor)
                status = "No controller is active."
                navigation_status = (
                    VEHICLE_STATUS_CANCELLED
                    if previous_mode == AUTOPILOT_DESTINATION
                    else VEHICLE_STATUS_IDLE
                )

            with self.database.lock:
                state = self.database.vehicle_control_states[actor_id]
                state.update({
                    "mode": requested_mode,
                    "control_mode": requested_mode,
                    "status": status,
                    "navigation_status": navigation_status,
                    "route": None,
                    "navigation_debug": None,
                    "autopilot_enabled": requested_mode == AUTOPILOT_FREE_ROAM,
                    "autopilot_state_source": "local",
                    "traffic_manager_port": (
                        self.TRAFFIC_MANAGER_PORT
                        if requested_mode == AUTOPILOT_FREE_ROAM else None
                    ),
                    "input_fresh": False,
                    "error": None,
                })
            self._publish_vehicle_control_state(actor_id, session_id=session_id)
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            if (
                requested_mode == AUTOPILOT_DESTINATION
                and destination_activation_started
                and actor is not None
                and getattr(actor, "is_alive", False)
            ):
                try:
                    actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                    self._clear_vehicle_navigation_path(actor)
                    self._safe_stop_vehicle(actor)
                except Exception as cleanup_error:
                    self.dashboard_monitor.record_error(cleanup_error)
                self.vehicle_navigation_runtime.pop(actor_id, None)
            with self.database.lock:
                state = self.database.vehicle_control_states.get(actor_id)
                if state is not None:
                    state["status"] = f"Control mode change failed: {error}"
                    state["error"] = str(error)
                    if requested_mode == AUTOPILOT_DESTINATION:
                        state["navigation_status"] = VEHICLE_STATUS_FAILED
            self._publish_vehicle_control_state(
                actor_id,
                session_id=session_id,
                error=error,
            )

    def _service_manual_vehicle_control(self):
        if not self._manual_control_is_active():
            return

        latest = None
        try:
            while True:
                latest = self.movement_control_queue.get_nowait()
        except queue.Empty:
            pass
        except (EOFError, OSError, ValueError) as error:
            self._manual_control_failure(error)
            return

        now = time.monotonic()
        if isinstance(latest, dict):
            self.manual_control_command = latest
            self.manual_control_received_at = now
        if now < self.manual_control_next_apply:
            return
        self.manual_control_next_apply = now + 1.0 / self.MANUAL_CONTROL_HZ

        actor_id = self.movement_preview_actor_id
        command = self.manual_control_command
        fresh = (
            command is not None
            and self.manual_control_received_at is not None
            and now - self.manual_control_received_at
                <= self.MANUAL_INPUT_TIMEOUT_SECONDS
            and bool(command.get("focused", False))
        )
        try:
            actor = self._get_managed_vehicle(actor_id)
            if fresh:
                reverse = bool(command.get("reverse", False))
                actor.apply_control(carla.VehicleControl(
                    throttle=max(0.0, min(1.0, float(command.get("throttle", 0.0)))),
                    steer=max(-1.0, min(1.0, float(command.get("steer", 0.0)))),
                    brake=max(0.0, min(1.0, float(command.get("brake", 0.0)))),
                    hand_brake=bool(command.get("hand_brake", False)),
                    reverse=reverse,
                    manual_gear_shift=False,
                ))
            else:
                with self.database.lock:
                    reverse = self.database.vehicle_control_states[actor_id].get(
                        "reverse", False
                    )
                self._safe_stop_vehicle(actor, reverse)

            state_changed = fresh != self.manual_control_input_fresh
            with self.database.lock:
                state = self.database.vehicle_control_states[actor_id]
                if fresh and state.get("reverse") != reverse:
                    state_changed = True
                state.update({
                    "reverse": reverse,
                    "input_fresh": fresh,
                    "status": (
                        "Manual Control active."
                        if fresh else
                        "Manual Control safety stop: input unavailable or unfocused."
                    ),
                    "error": None,
                })
            self.manual_control_input_fresh = fresh
            if state_changed:
                self._publish_vehicle_control_state(actor_id)
        except Exception as error:
            self._manual_control_failure(error)

    def _manual_control_failure(self, error):
        actor_id = self.movement_preview_actor_id
        self.dashboard_monitor.record_error(error)
        if actor_id is None:
            return
        try:
            actor = self._get_managed_vehicle(actor_id)
            with self.database.lock:
                reverse = self.database.vehicle_control_states[actor_id].get(
                    "reverse", False
                )
            self._safe_stop_vehicle(actor, reverse)
        except Exception:
            pass
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            if state is not None:
                state.update({
                    "mode": NO_CONTROL,
                    "control_mode": NO_CONTROL,
                    "status": f"Manual Control stopped: {error}",
                    "input_fresh": False,
                    "error": str(error),
                })
        self._publish_vehicle_control_state(actor_id, error=error)

    def _close_manual_session(self, actor_id):
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            state_copy = state.copy() if state is not None else None
            actor = self.database.actors_db.actors.get(actor_id)
        if state_copy is None or state_copy.get("mode") != MANUAL_CONTROL:
            return
        try:
            if actor is not None and getattr(actor, "is_alive", False):
                self._safe_stop_vehicle(actor, state_copy.get("reverse", False))
        except Exception as error:
            self.dashboard_monitor.record_error(error)
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            if state is not None and state.get("mode") == MANUAL_CONTROL:
                state.update({
                    "mode": NO_CONTROL,
                    "control_mode": NO_CONTROL,
                    "status": "Manual Control ended; vehicle stopped safely.",
                    "input_fresh": False,
                    "error": None,
                })
        self._publish_vehicle_control_state(actor_id)

    def _release_vehicle_control(self, actor_id, disable_autopilot,
                                 clear_path=True):
        self._tm_debug_event(
            "vehicle_control_release_begin",
            actor_id=actor_id,
            disable_autopilot=bool(disable_autopilot),
            clear_path=bool(clear_path),
        )
        self._close_manual_session(actor_id)
        with self.database.lock:
            actor = self.database.actors_db.actors.get(actor_id)
            state = self.database.vehicle_control_states.get(actor_id)
            state_copy = state.copy() if state is not None else None
        # Normal mode changes may clear a custom path while the actor is still
        # registered.  World-transition cleanup deliberately skips set_path:
        # no TM command carrying an old actor proxy may outlive load_world().
        self._cancel_vehicle_navigation(
            actor_id,
            actor=actor,
            status=VEHICLE_STATUS_CANCELLED,
            stop_vehicle=False,
            clear_path=clear_path,
        )
        if (
            disable_autopilot
            and state_copy is not None
            and normalize_vehicle_control_mode(state_copy.get("mode")) in (
                AUTOPILOT_FREE_ROAM,
                AUTOPILOT_DESTINATION,
            )
            and actor is not None
            and getattr(actor, "is_alive", False)
        ):
            try:
                self._tm_debug_event(
                    "autopilot_disable_begin",
                    actor_id=actor_id,
                )
                actor.set_autopilot(False, self.TRAFFIC_MANAGER_PORT)
                self._tm_debug_event(
                    "autopilot_disable_complete",
                    actor_id=actor_id,
                )
            except Exception as error:
                self.dashboard_monitor.record_error(error)
        with self.database.lock:
            state = self.database.vehicle_control_states.get(actor_id)
            if state is not None:
                state.update({
                    "mode": NO_CONTROL,
                    "control_mode": NO_CONTROL,
                    "status": "Vehicle control released.",
                    "navigation_status": VEHICLE_STATUS_CANCELLED,
                    "route": None,
                    "autopilot_enabled": False,
                    "traffic_manager_port": None,
                    "input_fresh": False,
                })
        self._tm_debug_event(
            "vehicle_control_release_complete",
            actor_id=actor_id,
        )

    def _shutdown_vehicle_controls(self, disable_autopilot,
                                   clear_paths=True):
        try:
            with self.database.lock:
                actor_ids = set(self.database.vehicle_control_states)
            actor_ids.update(getattr(self, "vehicle_navigation_runtime", {}))
            for actor_id in actor_ids:
                self._release_vehicle_control(
                    actor_id,
                    disable_autopilot,
                    clear_path=clear_paths,
                )
        except Exception as error:
            try:
                self.dashboard_monitor.record_error(error)
            except Exception:
                pass
        finally:
            getattr(self, "vehicle_navigation_runtime", {}).clear()
            getattr(self, "vehicle_navigation_submission_counts", {}).clear()
            self.vehicle_navigation_submission_sequence = 0
            self.movement_navigation_map_cache = None

    def _publish_walker_ai_state(self, walker_id, session_id=None,
                                 error=None):
        with self.database.lock:
            state = self.database.walker_ai_states.get(walker_id)
            state = state.copy() if state is not None else None
        if state is None:
            return
        if session_id is None and walker_id == self.movement_preview_actor_id:
            session_id = self.movement_preview_session_id
        if session_id is None:
            return
        world_id, world_generation = self._movement_world_token()
        status = "walker_ai_error" if error else "walker_ai_state"
        self.database.movement_event_queue.put((status, {
            "session_id": session_id,
            "actor_id": walker_id,
            "world_id": world_id,
            "world_generation": world_generation,
            "walker_ai_state": state,
            "message": str(error) if error else None,
        }))

    def _get_managed_walker(self, walker_id):
        with self.database.lock:
            walker = self.database.actors_db.actors.get(walker_id)
            state = self.database.walker_ai_states.get(walker_id)
        if state is None:
            raise RuntimeError(
                "The selected pedestrian is not managed by this application."
            )
        if (
            walker is None
            or not getattr(walker, "is_alive", False)
            or not walker.type_id.startswith("walker.")
        ):
            raise RuntimeError("The selected pedestrian is no longer available.")
        return walker

    @staticmethod
    def _walker_distance(first, second):
        return (
            (float(first.x) - float(second.x)) ** 2
            + (float(first.y) - float(second.y)) ** 2
            + (float(first.z) - float(second.z)) ** 2
        ) ** 0.5

    @staticmethod
    def _walker_xy_distance(first, second):
        return (
            (float(first.x) - float(second.x)) ** 2
            + (float(first.y) - float(second.y)) ** 2
        ) ** 0.5

    @staticmethod
    def _serialize_location(location):
        if location is None:
            return None
        return {
            "x": float(location.x),
            "y": float(location.y),
            "z": float(location.z),
        }

    def _validate_walker_speed(self, speed):
        value = float(speed)
        if not MIN_WALKER_SPEED <= value <= MAX_WALKER_SPEED:
            raise ValueError(
                f"Walker speed must be between {MIN_WALKER_SPEED:.1f} "
                f"and {MAX_WALKER_SPEED:.1f} m/s."
            )
        return value

    @staticmethod
    def _walker_fallback_direction(current_location, target):
        delta_x = float(target.x) - float(current_location.x)
        delta_y = float(target.y) - float(current_location.y)
        length = (delta_x ** 2 + delta_y ** 2) ** 0.5
        if length <= 1e-6:
            return carla.Vector3D(x=1.0, y=0.0, z=0.0)
        return carla.Vector3D(
            x=delta_x / length,
            y=delta_y / length,
            z=0.0,
        )

    @classmethod
    def _walker_initial_fallback_direction(cls, walker, location, target):
        try:
            forward = walker.get_transform().get_forward_vector()
            length = (
                float(forward.x) ** 2 + float(forward.y) ** 2
            ) ** 0.5
            if length > 1e-6:
                return carla.Vector3D(
                    x=float(forward.x) / length,
                    y=float(forward.y) / length,
                    z=0.0,
                )
        except (AttributeError, RuntimeError):
            pass
        return cls._walker_fallback_direction(location, target)

    @staticmethod
    def _apply_walker_fallback_control(walker, direction, speed):
        walker.apply_control(carla.WalkerControl(
            direction,
            float(speed),
            False,
        ))

    def _service_walker_fallback(self, world, walker_id, walker,
                                 simulation_time):
        runtime = self.walker_ai_runtime[walker_id]
        now = time.monotonic()
        location = walker.get_location()
        direction = runtime.get("fallback_direction")
        if direction is None:
            target = runtime.get("target") or location
            direction = self._walker_fallback_direction(location, target)

        last_location = runtime.get("fallback_last_location")
        progressed = (
            last_location is None
            or self._walker_distance(location, last_location) >= 0.05
        )
        if progressed:
            runtime["fallback_last_location"] = location
            runtime["fallback_last_progress_at"] = now
            if simulation_time is not None:
                runtime["fallback_last_progress_sim_time"] = simulation_time

        progress_sim_time = runtime.get("fallback_last_progress_sim_time")
        if simulation_time is not None and progress_sim_time is not None:
            stalled = (
                simulation_time - progress_sim_time
                >= self.WALKER_AI_FALLBACK_STALL_SECONDS
            )
        else:
            stalled = (
                now - runtime.get("fallback_last_progress_at", now)
                >= self.WALKER_AI_FALLBACK_STALL_SECONDS
            )

        origin = runtime.get("fallback_origin") or location
        leg_finished = (
            self._walker_distance(location, origin)
            >= self.WALKER_AI_FALLBACK_LEG_DISTANCE
        )
        status_changed = False
        if stalled:
            direction = carla.Vector3D(
                x=-float(direction.y),
                y=float(direction.x),
                z=0.0,
            )
            runtime.update({
                "fallback_origin": location,
                "fallback_last_location": location,
                "fallback_last_progress_at": now,
                "fallback_last_progress_sim_time": simulation_time,
            })
            with self.database.lock:
                self.database.walker_ai_states[walker_id]["status"] = (
                    "Walker AI fallback steering changed direction after "
                    "detecting an obstacle."
                )
            status_changed = True
        elif leg_finished:
            target = world.get_random_location_from_navigation()
            if target is not None:
                runtime["target"] = target
                direction = self._walker_fallback_direction(location, target)
                with self.database.lock:
                    state = self.database.walker_ai_states[walker_id]
                    state["target"] = self._serialize_location(target)
                    state["status"] = (
                        "Walker AI fallback selected a new random direction."
                    )
                status_changed = True
            else:
                direction = carla.Vector3D(
                    x=-float(direction.y),
                    y=float(direction.x),
                    z=0.0,
                )
            runtime.update({
                "fallback_origin": location,
                "fallback_last_location": location,
                "fallback_last_progress_at": now,
                "fallback_last_progress_sim_time": simulation_time,
            })

        runtime["fallback_direction"] = direction
        with self.database.lock:
            saved_speed = self.database.walker_ai_states[walker_id]["speed"]
        speed = runtime.get("speed", saved_speed)
        runtime["speed"] = speed
        self._apply_walker_fallback_control(walker, direction, speed)
        return status_changed

    def _start_walker_direct_control(self, world, walker, speed):
        now = time.monotonic()
        location = walker.get_location()
        target = None
        for _attempt in range(5):
            candidate = world.get_random_location_from_navigation()
            if candidate is None:
                continue
            target = candidate
            if self._walker_distance(location, candidate) > 1.0:
                break

        direction_target = target or location
        direction = self._walker_initial_fallback_direction(
            walker,
            location,
            direction_target,
        )
        runtime = self.walker_ai_runtime.setdefault(walker.id, {})
        runtime.update({
            "world_id": self._world_id(world),
            "navigation_mode": WALKER_CONTROL_RANDOM,
            "speed": speed,
            "target": target,
            "next_check": now + self.WALKER_AI_CHECK_INTERVAL_SECONDS,
            "last_distance": None,
            "last_progress_at": now,
            "last_progress_sim_time": None,
            "last_frame": None,
            "fallback_active": True,
            "fallback_direction": direction,
            "fallback_origin": location,
            "fallback_last_location": location,
            "fallback_last_progress_at": now,
            "fallback_last_progress_sim_time": None,
        })
        with self.database.lock:
            state = self.database.walker_ai_states[walker.id]
            state.update({
                "mode": WALKER_AI_RUNNING,
                "active": True,
                "speed": speed,
                "controller_id": None,
                "target": self._serialize_location(target),
                "random_navigation": True,
                "state_source": "gui",
                "status": (
                    "Walker autopilot running with direct control."
                    if target is not None else
                    "Walker autopilot running; no navigation point is "
                    "available, using the actor heading."
                ),
                "error": None,
                "control_mode": WALKER_CONTROL_RANDOM,
                "movement_status": WALKER_STATUS_MOVING,
            })
        self._apply_walker_fallback_control(walker, direction, speed)

    def _start_walker_destination_control(self, world, walker, speed):
        with self.database.lock:
            state = self.database.walker_ai_states[walker.id]
            destination = state.get("destination")
        if (
            not isinstance(destination, dict)
            or not destination.get("confirmed")
            or not isinstance(destination.get("selected"), dict)
        ):
            raise ValueError(
                "Confirm a pedestrian destination before starting this mode."
            )
        if destination.get("world_id") != int(world.id):
            raise ValueError(
                "The selected destination belongs to another world episode."
            )
        selected = destination["selected"]
        target = carla.Location(
            x=float(selected["x"]),
            y=float(selected["y"]),
            z=float(selected["z"]),
        )
        location = walker.get_location()
        distance = self._walker_xy_distance(location, target)
        if distance <= self.WALKER_DESTINATION_ARRIVAL_DISTANCE:
            self._apply_walker_fallback_control(
                walker,
                carla.Vector3D(x=0.0, y=0.0, z=0.0),
                0.0,
            )
            with self.database.lock:
                state = self.database.walker_ai_states[walker.id]
                state.update({
                    "mode": WALKER_AI_STOPPED,
                    "active": False,
                    "control_mode": WALKER_CONTROL_DESTINATION,
                    "movement_status": WALKER_STATUS_ARRIVED,
                    "target": self._serialize_location(target),
                    "random_navigation": False,
                    "status": "Arrived at the selected destination.",
                    "error": None,
                })
            return

        now = time.monotonic()
        direction = self._walker_fallback_direction(location, target)
        runtime = self.walker_ai_runtime.setdefault(walker.id, {})
        runtime.update({
            "world_id": self._world_id(world),
            "navigation_mode": WALKER_CONTROL_DESTINATION,
            "speed": speed,
            "target": target,
            "next_check": now + self.WALKER_AI_CHECK_INTERVAL_SECONDS,
            "last_distance": distance,
            "last_progress_at": now,
            "last_progress_sim_time": None,
            "last_frame": None,
            "fallback_active": True,
            "fallback_direction": direction,
            "fallback_origin": location,
            "fallback_last_location": location,
            "fallback_last_progress_at": now,
            "fallback_last_progress_sim_time": None,
        })
        with self.database.lock:
            state = self.database.walker_ai_states[walker.id]
            state.update({
                "mode": WALKER_AI_RUNNING,
                "active": True,
                "speed": speed,
                "controller_id": None,
                "target": self._serialize_location(target),
                "random_navigation": False,
                "control_mode": WALKER_CONTROL_DESTINATION,
                "movement_status": WALKER_STATUS_MOVING,
                "state_source": "gui",
                "status": (
                    f"Moving to destination ({distance:.2f} m remaining)."
                ),
                "error": None,
            })
        self._apply_walker_fallback_control(walker, direction, speed)

    def _set_walker_control_mode(self, world, data):
        session_id = data["session_id"]
        walker_id = int(data["actor_id"])
        try:
            if (
                session_id != self.movement_preview_session_id
                or walker_id != self.movement_preview_actor_id
            ):
                raise RuntimeError(
                    "The Movement Control session is no longer active."
                )
            requested_mode = str(data["mode"])
            if requested_mode not in WALKER_CONTROL_MODES:
                raise ValueError(
                    f"Unknown pedestrian control mode: {requested_mode}"
                )
            walker = self._get_managed_walker(walker_id)
            with self.database.lock:
                previous = self.database.walker_ai_states[walker_id].copy()
                saved_speed = previous["speed"]
            try:
                speed = self._validate_walker_speed(data.get("speed"))
            except (TypeError, ValueError):
                if requested_mode != WALKER_CONTROL_MANUAL:
                    raise
                speed = saved_speed

            if requested_mode == WALKER_CONTROL_RANDOM:
                self._start_walker_direct_control(world, walker, speed)
            elif requested_mode == WALKER_CONTROL_DESTINATION:
                self._start_walker_destination_control(world, walker, speed)
            else:
                was_destination = (
                    previous.get("control_mode")
                    == WALKER_CONTROL_DESTINATION
                    and previous.get("active")
                )
                runtime = self.walker_ai_runtime.setdefault(walker_id, {})
                runtime.update({
                    "navigation_mode": WALKER_CONTROL_MANUAL,
                    "target": None,
                    "fallback_active": False,
                    "fallback_direction": None,
                })
                self._apply_walker_fallback_control(
                    walker,
                    carla.Vector3D(x=0.0, y=0.0, z=0.0),
                    0.0,
                )
                with self.database.lock:
                    state = self.database.walker_ai_states[walker_id]
                    state.update({
                        "mode": WALKER_AI_STOPPED,
                        "active": False,
                        "control_mode": WALKER_CONTROL_MANUAL,
                        "movement_status": (
                            WALKER_STATUS_CANCELLED
                            if was_destination else WALKER_STATUS_IDLE
                        ),
                        "status": (
                            "Destination movement cancelled."
                            if was_destination else
                            "Manual control selected; pedestrian stopped."
                        ),
                        "speed": speed,
                        "target": None,
                        "random_navigation": False,
                        "state_source": "gui",
                        "error": None,
                    })
            self._publish_walker_ai_state(walker_id, session_id=session_id)
        except Exception as error:
            self._walker_ai_failure(
                walker_id,
                error,
                session_id=session_id,
            )

    def _set_walker_manual_control(self, data):
        session_id = data["session_id"]
        walker_id = int(data["actor_id"])
        try:
            if (
                session_id != self.movement_preview_session_id
                or walker_id != self.movement_preview_actor_id
            ):
                raise RuntimeError(
                    "The Movement Control session is no longer active."
                )
            walker = self._get_managed_walker(walker_id)
            speed = self._validate_walker_speed(data["speed"])
            direction_x = float(data["direction_x"])
            direction_y = float(data["direction_y"])
            length = (direction_x ** 2 + direction_y ** 2) ** 0.5
            if not math.isfinite(length) or length <= 1e-6:
                raise ValueError("Manual direction X/Y must be non-zero.")
            direction_x /= length
            direction_y /= length
            self.walker_ai_runtime.pop(walker_id, None)
            direction = carla.Vector3D(
                x=direction_x,
                y=direction_y,
                z=0.0,
            )
            self._apply_walker_fallback_control(walker, direction, speed)
            with self.database.lock:
                state = self.database.walker_ai_states[walker_id]
                state.update({
                    "mode": WALKER_AI_STOPPED,
                    "active": False,
                    "control_mode": WALKER_CONTROL_MANUAL,
                    "movement_status": WALKER_STATUS_MOVING,
                    "manual_direction": {
                        "x": direction_x,
                        "y": direction_y,
                    },
                    "speed": speed,
                    "target": None,
                    "random_navigation": False,
                    "status": "Manual WalkerControl command is active.",
                    "state_source": "gui",
                    "error": None,
                })
            self._publish_walker_ai_state(walker_id, session_id=session_id)
        except Exception as error:
            self._walker_ai_failure(
                walker_id,
                error,
                session_id=session_id,
                stop_ai=False,
            )

    def _set_walker_ai_enabled(self, world, data):
        compatibility_data = dict(data)
        compatibility_data["mode"] = (
            WALKER_CONTROL_RANDOM
            if data.get("enabled") else WALKER_CONTROL_MANUAL
        )
        self._set_walker_control_mode(world, compatibility_data)

    def _movement_destination_actor(self, data):
        session_id = data["session_id"]
        actor_id = int(data["actor_id"])
        if (
            session_id != self.movement_preview_session_id
            or actor_id != self.movement_preview_actor_id
        ):
            raise RuntimeError(
                "The Movement Control session is no longer active."
            )
        with self.database.lock:
            actor = self.database.actors_db.actors.get(actor_id)
            actor_info = self.database.actors_db.actors_db.get(actor_id)
            current_world_id = getattr(self.database, "world_id", None)
            current_generation = getattr(
                self.database,
                "world_generation",
                None,
            )
        try:
            stale = (
                int(data["world_id"]) != int(current_world_id)
                or int(data["world_generation"]) != int(current_generation)
                or actor_info is None
                or int(actor_info.world_id) != int(current_world_id)
            )
        except (KeyError, TypeError, ValueError):
            stale = True
        if stale:
            raise RuntimeError(
                "The Movement Control command belongs to a stale world episode."
            )
        if actor is None or not bool(actor_info.is_alive):
            raise RuntimeError("The selected actor is no longer available.")
        if not self.database.is_actor_managed(actor_id):
            raise RuntimeError("Destination requires a GUI-managed actor.")
        actor_type = str(actor_info.type_id)
        if actor_type.startswith("vehicle."):
            category = "vehicle"
        elif actor_type.startswith("walker."):
            category = "pedestrian"
        else:
            raise RuntimeError(
                "Destination supports vehicles and pedestrians only."
            )
        return session_id, actor_id, actor, category

    def _publish_movement_destination(self, status, session_id, actor_id,
                                      **values):
        world_id, world_generation = self._movement_world_token()
        self.database.movement_event_queue.put((status, {
            "session_id": session_id,
            "actor_id": actor_id,
            "world_id": world_id,
            "world_generation": world_generation,
            **values,
        }))

    def _validate_movement_destination(self, world, data):
        session_id = data.get("session_id")
        actor_id = int(data.get("actor_id"))
        try:
            session_id, actor_id, _actor, category = (
                self._movement_destination_actor(data)
            )
            requested = point_from_values(
                data.get("x"),
                data.get("y"),
                data.get("z"),
            )
            if category == "vehicle":
                validation = validate_vehicle_destination(
                    world,
                    requested,
                    carla,
                    waypoint_selector=data.get("waypoint"),
                )
            else:
                validation = validate_pedestrian_destination(requested)
            validation_status, requested, adjusted, message = validation
            destination = destination_result(
                actor_id,
                category,
                int(world.id),
                requested,
                validation_status,
                message,
                adjusted=adjusted,
            )
            if category == "vehicle" and validation_status in (
                DESTINATION_VALID,
                DESTINATION_ADJUSTED,
            ):
                target_waypoint = resolve_vehicle_waypoint(
                    world,
                    adjusted or requested,
                    carla,
                    waypoint_selector=data.get("waypoint"),
                    project_to_road=True,
                )
                destination["target_waypoint"] = waypoint_snapshot(
                    target_waypoint
                )
            destination = self.database.set_movement_destination(
                actor_id,
                destination,
            )
            self._publish_movement_destination(
                "movement_destination_validation",
                session_id,
                actor_id,
                destination=destination,
            )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            category = "unknown"
            try:
                with self.database.lock:
                    actor = self.database.actors_db.actors.get(actor_id)
                category = (
                    "vehicle"
                    if actor is not None
                    and actor.type_id.startswith("vehicle.")
                    else "pedestrian"
                )
                destination = empty_destination(
                    actor_id,
                    category,
                    int(world.id),
                )
                destination.update({
                    "validation_status": DESTINATION_ERROR,
                    "message": f"Destination validation failed: {error}",
                })
                self.database.set_movement_destination(
                    actor_id,
                    destination,
                )
            except Exception:
                destination = None
            self._publish_movement_destination(
                "movement_destination_error",
                session_id,
                actor_id,
                destination=destination,
                message=str(error),
            )

    def _confirm_movement_destination(self, world, data):
        session_id = data.get("session_id")
        actor_id = int(data.get("actor_id"))
        try:
            session_id, actor_id, _actor, _category = (
                self._movement_destination_actor(data)
            )
            destination = self.database.get_movement_destination(actor_id)
            if destination is None or destination.get("world_id") != int(world.id):
                raise ValueError(
                    "Validate a destination in the current world first."
                )
            destination = confirm_destination(destination)
            destination = self.database.set_movement_destination(
                actor_id,
                destination,
            )
            self._publish_movement_destination(
                "movement_destination_confirmed",
                session_id,
                actor_id,
                destination=destination,
            )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self._publish_movement_destination(
                "movement_destination_error",
                session_id,
                actor_id,
                destination=self.database.get_movement_destination(actor_id),
                message=str(error),
            )

    def _clear_movement_destination(self, world, data):
        session_id = data.get("session_id")
        actor_id = int(data.get("actor_id"))
        try:
            session_id, actor_id, actor, category = (
                self._movement_destination_actor(data)
            )
            if category == "vehicle":
                with self.database.lock:
                    state = self.database.vehicle_control_states.get(actor_id)
                    active_destination = (
                        state is not None
                        and state.get("mode") == AUTOPILOT_DESTINATION
                    )
                if active_destination:
                    self._cancel_vehicle_navigation(
                        actor_id,
                        actor=actor,
                        status=VEHICLE_STATUS_CANCELLED,
                        stop_vehicle=True,
                    )
                    with self.database.lock:
                        state = self.database.vehicle_control_states.get(actor_id)
                        if state is not None:
                            state.update({
                                "mode": NO_CONTROL,
                                "control_mode": NO_CONTROL,
                                "status": "Destination navigation cancelled.",
                                "autopilot_enabled": False,
                                "traffic_manager_port": None,
                            })
                    self._publish_vehicle_control_state(
                        actor_id,
                        session_id=session_id,
                    )
            destination = empty_destination(
                actor_id,
                category,
                int(world.id),
            )
            destination = self.database.set_movement_destination(
                actor_id,
                destination,
            )
            self._publish_movement_destination(
                "movement_destination_cleared",
                session_id,
                actor_id,
                destination=destination,
            )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self._publish_movement_destination(
                "movement_destination_error",
                session_id,
                actor_id,
                message=str(error),
            )

    def _get_movement_destination_map(self, world, data):
        session_id = data.get("session_id")
        actor_id = int(data.get("actor_id"))
        try:
            session_id, actor_id, actor, category = (
                self._movement_destination_actor(data)
            )
            carla_map = world.get_map()
            world_id = int(world.id)
            cached_map = getattr(self, "movement_navigation_map_cache", None)
            if (
                isinstance(cached_map, dict)
                and cached_map.get("world_id") == world_id
            ):
                map_data = copy.deepcopy(cached_map["data"])
            else:
                map_data = self.map_preview_generator.prepare_navigation_map(
                    carla_map,
                    map_name=str(carla_map.name),
                    cancelled=self.world_transition_event.is_set,
                )
                self.movement_navigation_map_cache = {
                    "world_id": world_id,
                    "data": copy.deepcopy(map_data),
                }
            actor_snapshot = self._movement_navigation_snapshot(
                actor_id,
                actor,
                category,
                world_id,
            )
            self._publish_movement_destination(
                "movement_destination_map",
                session_id,
                actor_id,
                **map_data,
                actor_snapshot=actor_snapshot,
                destination=actor_snapshot.get("destination"),
                route=actor_snapshot.get("route"),
                open_map=bool(data.get("open_map", True)),
            )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self._publish_movement_destination(
                "movement_destination_map_error",
                session_id,
                actor_id,
                message=str(error),
            )

    def _movement_navigation_snapshot(self, actor_id, actor, category,
                                      world_id):
        transform = actor.get_transform()
        location = transform.location
        with self.database.lock:
            states = (
                self.database.vehicle_control_states
                if category == "vehicle"
                else self.database.walker_ai_states
            )
            state = copy.deepcopy(states.get(actor_id) or {})
        return {
            "actor_id": int(actor_id),
            "world_id": int(world_id),
            "category": category,
            "location": point_from_location(location),
            "yaw": float(getattr(transform.rotation, "yaw", 0.0)),
            "status": str(state.get("status", "")),
            "navigation_status": str(
                state.get("navigation_status")
                or state.get("movement_status")
                or "Idle"
            ),
            "destination": state.get("destination"),
            "target_waypoint": state.get("target_waypoint"),
            "route": state.get("route"),
            "navigation_debug": state.get("navigation_debug"),
        }

    def _get_movement_navigation_snapshot(self, world, data):
        session_id = data.get("session_id")
        actor_id = int(data.get("actor_id"))
        try:
            session_id, actor_id, actor, category = (
                self._movement_destination_actor(data)
            )
            snapshot = self._movement_navigation_snapshot(
                actor_id,
                actor,
                category,
                int(world.id),
            )
            self._publish_movement_destination(
                "movement_navigation_snapshot",
                session_id,
                actor_id,
                snapshot=snapshot,
            )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self._publish_movement_destination(
                "movement_navigation_snapshot_error",
                session_id,
                actor_id,
                message=str(error),
            )

    def _get_movement_actor_location(self, data):
        session_id = data.get("session_id")
        actor_id = int(data.get("actor_id"))
        try:
            session_id, actor_id, actor, _category = (
                self._movement_destination_actor(data)
            )
            self._publish_movement_destination(
                "movement_actor_location",
                session_id,
                actor_id,
                actor_location=point_from_location(actor.get_location()),
            )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self._publish_movement_destination(
                "movement_destination_error",
                session_id,
                actor_id,
                message=str(error),
            )

    def _set_walker_ai_speed(self, data):
        session_id = data["session_id"]
        walker_id = int(data["actor_id"])
        try:
            if (
                session_id != self.movement_preview_session_id
                or walker_id != self.movement_preview_actor_id
            ):
                raise RuntimeError("The Movement Control session is no longer active.")
            speed = self._validate_walker_speed(data["speed"])
            self._get_managed_walker(walker_id)
            with self.database.lock:
                state = self.database.walker_ai_states[walker_id]
                previous_speed = state["speed"]
                active = state["active"]
                control_mode = state.get(
                    "control_mode",
                    WALKER_CONTROL_MANUAL,
                )
                movement_status = state.get("movement_status")
                manual_direction = dict(
                    state.get("manual_direction") or {}
                )
                state["speed"] = speed
            runtime = self.walker_ai_runtime.get(walker_id)
            previous_runtime_speed = (
                None if runtime is None else runtime.get("speed")
            )
            if runtime is not None:
                runtime["speed"] = speed
            controller = self.walker_ai_controllers.get(walker_id)
            try:
                if active:
                    active_runtime = runtime or {}
                    if active_runtime.get("fallback_active"):
                        direction = active_runtime.get("fallback_direction")
                        if direction is not None:
                            walker = self._get_managed_walker(walker_id)
                            self._apply_walker_fallback_control(
                                walker,
                                direction,
                                speed,
                            )
                    elif controller is None or not getattr(
                        controller,
                        "is_alive",
                        False,
                    ):
                        raise RuntimeError("The Walker AI controller is unavailable.")
                    else:
                        controller.set_max_speed(speed)
                elif (
                    control_mode == WALKER_CONTROL_MANUAL
                    and movement_status == WALKER_STATUS_MOVING
                ):
                    walker = self._get_managed_walker(walker_id)
                    self._apply_walker_fallback_control(
                        walker,
                        carla.Vector3D(
                            x=float(manual_direction.get("x", 1.0)),
                            y=float(manual_direction.get("y", 0.0)),
                            z=0.0,
                        ),
                        speed,
                    )
            except Exception:
                with self.database.lock:
                    state = self.database.walker_ai_states.get(walker_id)
                    if state is not None:
                        state["speed"] = previous_speed
                if runtime is not None:
                    if previous_runtime_speed is None:
                        runtime.pop("speed", None)
                    else:
                        runtime["speed"] = previous_runtime_speed
                raise
            with self.database.lock:
                state = self.database.walker_ai_states[walker_id]
                state["state_source"] = "gui"
                state["status"] = (
                    f"Walker AI speed set to {speed:.1f} m/s."
                    if active else
                    (
                        f"Manual WalkerControl speed set to {speed:.1f} m/s."
                        if movement_status == WALKER_STATUS_MOVING else
                        f"Pedestrian idle; speed {speed:.1f} m/s saved."
                    )
                )
                state["error"] = None
            self._publish_walker_ai_state(walker_id, session_id=session_id)
        except Exception as error:
            self._walker_ai_failure(
                walker_id,
                error,
                session_id=session_id,
                stop_ai=False,
            )

    def _service_walker_destination(self, walker_id, walker,
                                    simulation_time):
        runtime = self.walker_ai_runtime[walker_id]
        target = runtime.get("target")
        if target is None:
            raise RuntimeError("Destination runtime target is unavailable.")
        location = walker.get_location()
        distance = self._walker_xy_distance(location, target)
        now = time.monotonic()
        if distance <= self.WALKER_DESTINATION_ARRIVAL_DISTANCE:
            self._apply_walker_fallback_control(
                walker,
                carla.Vector3D(x=0.0, y=0.0, z=0.0),
                0.0,
            )
            runtime["fallback_active"] = False
            with self.database.lock:
                state = self.database.walker_ai_states[walker_id]
                state.update({
                    "mode": WALKER_AI_STOPPED,
                    "active": False,
                    "movement_status": WALKER_STATUS_ARRIVED,
                    "status": "Arrived at the selected destination.",
                    "error": None,
                })
            return True

        last_distance = runtime.get("last_distance")
        progressed = (
            last_distance is None
            or distance
            <= last_distance - self.WALKER_DESTINATION_PROGRESS_DISTANCE
        )
        if progressed:
            runtime["last_distance"] = distance
            runtime["last_progress_at"] = now
            if simulation_time is not None:
                runtime["last_progress_sim_time"] = simulation_time

        progress_sim_time = runtime.get("last_progress_sim_time")
        if simulation_time is not None and progress_sim_time is not None:
            stalled = (
                simulation_time - progress_sim_time
                >= self.WALKER_AI_FALLBACK_STALL_SECONDS
            )
        else:
            stalled = (
                now - runtime.get("last_progress_at", now)
                >= self.WALKER_AI_FALLBACK_STALL_SECONDS
            )
        if stalled:
            self._apply_walker_fallback_control(
                walker,
                carla.Vector3D(x=0.0, y=0.0, z=0.0),
                0.0,
            )
            runtime["fallback_active"] = False
            with self.database.lock:
                state = self.database.walker_ai_states[walker_id]
                state.update({
                    "mode": WALKER_AI_STOPPED,
                    "active": False,
                    "movement_status": WALKER_STATUS_STUCK,
                    "status": (
                        "Pedestrian is Stuck; destination movement stopped."
                    ),
                    "error": None,
                })
            return True

        direction = self._walker_fallback_direction(location, target)
        runtime["fallback_direction"] = direction
        with self.database.lock:
            state = self.database.walker_ai_states[walker_id]
            saved_speed = state["speed"]
            state["movement_status"] = WALKER_STATUS_MOVING
            state["status"] = (
                f"Moving to destination ({distance:.2f} m remaining)."
            )
        speed = runtime.get("speed", saved_speed)
        runtime["speed"] = speed
        self._apply_walker_fallback_control(walker, direction, speed)
        return True

    def _service_walker_ai(self, world, world_snapshot=None):
        transition = getattr(self, "world_transition_event", None)
        if transition is not None and transition.is_set():
            return
        now = time.monotonic()
        current_world_id = self._world_id(world)
        with self.database.lock:
            active_ids = [
                walker_id
                for walker_id, state in self.database.walker_ai_states.items()
                if state.get("active")
            ]
        if not active_ids:
            return
        if world_snapshot is None:
            try:
                world_snapshot = world.get_snapshot()
            except Exception:
                world_snapshot = None
        frame = getattr(world_snapshot, "frame", None)
        timestamp = getattr(world_snapshot, "timestamp", None)
        simulation_time = getattr(timestamp, "elapsed_seconds", None)

        for walker_id in active_ids:
            with self.database.lock:
                current_speed = self.database.walker_ai_states[walker_id][
                    "speed"
                ]
            runtime = self.walker_ai_runtime.setdefault(walker_id, {
                "world_id": current_world_id,
                "target": None,
                "speed": current_speed,
                "next_check": 0.0,
                "last_distance": None,
                "last_progress_at": now,
                "last_progress_sim_time": None,
                "last_frame": None,
                "fallback_active": False,
                "fallback_direction": None,
                "fallback_origin": None,
                "fallback_last_location": None,
                "fallback_last_progress_at": None,
                "fallback_last_progress_sim_time": None,
            })
            runtime_world_id = runtime.get("world_id")
            if (
                runtime_world_id is not None
                and current_world_id is not None
                and int(runtime_world_id) != current_world_id
            ):
                self.walker_ai_runtime.pop(walker_id, None)
                continue
            if now < runtime.get("next_check", 0.0):
                continue
            runtime["next_check"] = now + self.WALKER_AI_CHECK_INTERVAL_SECONDS
            try:
                walker = self._get_managed_walker(walker_id)
                if frame is None or runtime.get("last_frame") == frame:
                    continue
                runtime["last_frame"] = frame

                with self.database.lock:
                    control_mode = self.database.walker_ai_states[
                        walker_id
                    ].get("control_mode", WALKER_CONTROL_RANDOM)
                if control_mode == WALKER_CONTROL_DESTINATION:
                    changed = self._service_walker_destination(
                        walker_id,
                        walker,
                        simulation_time,
                    )
                else:
                    changed = self._service_walker_fallback(
                        world,
                        walker_id,
                        walker,
                        simulation_time,
                    )
                if changed:
                    self._publish_walker_ai_state(walker_id)
            except Exception as error:
                self._walker_ai_failure(walker_id, error)

    def _walker_ai_failure(self, walker_id, error, session_id=None,
                           stop_ai=True):
        self.dashboard_monitor.record_error(error)
        if stop_ai:
            self._destroy_walker_ai(walker_id, remove_state=False)
        with self.database.lock:
            state = self.database.walker_ai_states.get(walker_id)
            if state is not None:
                state.update({
                    "mode": (
                        WALKER_AI_ERROR if stop_ai else state.get("mode")
                    ),
                    "active": False if stop_ai else state.get("active", False),
                    "status": f"Walker AI error: {error}",
                    "target": None if stop_ai else state.get("target"),
                    "error": str(error),
                    "movement_status": WALKER_STATUS_FAILED,
                })
        self._publish_walker_ai_state(
            walker_id,
            session_id=session_id,
            error=error,
        )

    def _destroy_walker_ai(self, walker_id, remove_state=False):
        try:
            with self.database.lock:
                walker = self.database.actors_db.actors.get(walker_id)
            if walker is not None and getattr(walker, "is_alive", False):
                self._apply_walker_fallback_control(
                    walker,
                    carla.Vector3D(x=0.0, y=0.0, z=0.0),
                    0.0,
                )
        except Exception as error:
            self.dashboard_monitor.record_error(error)
        controller = self.walker_ai_controllers.pop(walker_id, None)
        self.walker_ai_runtime.pop(walker_id, None)
        if controller is not None:
            try:
                if getattr(controller, "is_alive", False):
                    controller.stop()
            except Exception as error:
                self.dashboard_monitor.record_error(error)
            try:
                if getattr(controller, "is_alive", False):
                    controller.destroy()
            except Exception as error:
                self.dashboard_monitor.record_error(error)
        with self.database.lock:
            if remove_state:
                self.database.walker_ai_states.pop(walker_id, None)
            else:
                state = self.database.walker_ai_states.get(walker_id)
                if state is not None:
                    was_active = bool(state.get("active"))
                    state.update({
                        "mode": WALKER_AI_STOPPED,
                        "active": False,
                        "status": "Walker AI controller was released.",
                        "controller_id": None,
                        "target": None,
                        "movement_status": (
                            WALKER_STATUS_CANCELLED
                            if was_active else
                            state.get("movement_status", WALKER_STATUS_IDLE)
                        ),
                    })

    def _shutdown_walker_ai(self):
        try:
            with self.database.lock:
                walker_ids = set(self.database.walker_ai_states)
            walker_ids.update(self.walker_ai_controllers)
            walker_ids.update(self.walker_ai_runtime)
            for walker_id in list(walker_ids):
                self._destroy_walker_ai(walker_id, remove_state=False)
        except Exception as error:
            try:
                self.dashboard_monitor.record_error(error)
            except Exception:
                pass
        finally:
            getattr(self, "walker_ai_controllers", {}).clear()
            getattr(self, "walker_ai_runtime", {}).clear()

    def _start_movement_preview(self, world, data):
        session_id = data["session_id"]
        publisher = data["publisher"]
        camera = None
        try:
            if self.movement_preview_camera is not None:
                raise RuntimeError("Another Movement Control session is already active.")

            with self.database.lock:
                parent_actor = self.database.actors_db.actors.get(data["actor_id"])
                actor_info = self.database.actors_db.actors_db.get(data["actor_id"])
                current_world_id = getattr(self.database, "world_id", None)
                current_generation = getattr(
                    self.database,
                    "world_generation",
                    None,
                )
            try:
                stale = (
                    int(data["world_id"]) != int(current_world_id)
                    or int(data["world_generation"]) != int(current_generation)
                    or actor_info is None
                    or int(actor_info.world_id) != int(current_world_id)
                )
            except (KeyError, TypeError, ValueError):
                stale = True
            if stale:
                raise RuntimeError(
                    "Movement Preview request belongs to a stale world episode."
                )
            if parent_actor is None or not bool(actor_info.is_alive):
                raise RuntimeError("The selected actor is no longer available.")
            actor_type = str(actor_info.type_id)
            if not actor_type.startswith(("vehicle.", "walker.")):
                raise RuntimeError(
                    "Movement Preview supports vehicles and pedestrians only."
                )

            camera_blueprint = world.get_blueprint_library().find(
                "sensor.camera.rgb"
            )
            attributes = {
                "image_size_x": 800,
                "image_size_y": 450,
                "fov": 90.0,
                "sensor_tick": 1.0 / 30.0,
                "role_name": "movement_control_preview",
            }
            for name, value in attributes.items():
                if camera_blueprint.has_attribute(name):
                    camera_blueprint.set_attribute(name, str(value))

            transform = camera_transform_for_actor(
                actor_type,
                data["preset"],
                carla,
            )
            camera = world.spawn_actor(
                camera_blueprint,
                transform,
                attach_to=parent_actor,
                attachment_type=carla.AttachmentType.Rigid,
            )
            if camera is None:
                raise RuntimeError("CARLA did not create the Movement RGB camera.")

            camera.listen(publisher.publish)
            self.movement_preview_camera = camera
            self.movement_preview_publisher = publisher
            self.movement_preview_session_id = session_id
            self.movement_preview_actor_id = int(actor_info.runtime_id)
            self.movement_preview_world_id = self._world_id(world)
            self.movement_preview_world_generation = int(current_generation)
            self.movement_control_queue = data["control_queue"]
            self.manual_control_command = None
            self.manual_control_received_at = None
            self.manual_control_input_fresh = False
            self.database.movement_event_queue.put((
                "movement_camera_started",
                {
                    "session_id": session_id,
                    "world_id": self._world_id(world),
                    "world_generation": int(current_generation),
                    "sensor_id": camera.id,
                    "actor_id": int(actor_info.runtime_id),
                    "preset": data["preset"],
                },
            ))
            if actor_type.startswith("vehicle."):
                self._publish_vehicle_control_state(
                    int(actor_info.runtime_id),
                    session_id=session_id,
                )
            elif actor_type.startswith("walker."):
                self._publish_walker_ai_state(
                    int(actor_info.runtime_id),
                    session_id=session_id,
                )
        except Exception as error:
            publisher.close()
            if camera is not None:
                try:
                    is_listening = getattr(camera, "is_listening", False)
                    if callable(is_listening):
                        is_listening = is_listening()
                    if is_listening:
                        camera.stop()
                except Exception:
                    pass
                try:
                    if camera.is_alive:
                        camera.destroy()
                except Exception:
                    pass
            self.dashboard_monitor.record_error(error)
            self.database.movement_event_queue.put((
                "movement_camera_error",
                {
                    "session_id": session_id,
                    "world_id": data.get("world_id"),
                    "message": f"Could not start Movement Preview camera: {error}",
                },
            ))

    def _set_movement_preview_transform(self, data):
        session_id = data["session_id"]
        try:
            if (
                session_id != self.movement_preview_session_id
                or self.movement_preview_camera is None
                or not self.movement_preview_camera.is_alive
            ):
                raise RuntimeError("The Movement Preview camera is not active.")
            with self.database.lock:
                parent_actor = self.database.actors_db.actors.get(
                    self.movement_preview_actor_id
                )
            if parent_actor is None or not parent_actor.is_alive:
                raise RuntimeError("The selected actor is no longer available.")

            transform = camera_transform_for_actor(
                parent_actor.type_id,
                data["preset"],
                carla,
            )
            self.movement_preview_camera.set_transform(transform)
            self.database.movement_event_queue.put((
                "movement_camera_preset_changed",
                {
                    "session_id": session_id,
                    "world_id": self.movement_preview_world_id,
                    "sensor_id": self.movement_preview_camera.id,
                    "preset": data["preset"],
                },
            ))
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self.database.movement_event_queue.put((
                "movement_camera_error",
                {
                    "session_id": session_id,
                    "world_id": data.get("world_id"),
                    "message": f"Could not change camera preset: {error}",
                },
            ))

    def cleanup_movement_preview(self, reason="preview_stopped"):
        camera = self.movement_preview_camera
        publisher = self.movement_preview_publisher
        session_id = self.movement_preview_session_id
        actor_id = self.movement_preview_actor_id
        world_id = getattr(self, "movement_preview_world_id", None)
        world_generation = getattr(
            self,
            "movement_preview_world_generation",
            None,
        )
        self._tm_debug_event(
            "movement_preview_cleanup_begin",
            reason=reason,
            session_id=session_id,
            actor_id=actor_id,
            preview_world_id=world_id,
            preview_world_generation=world_generation,
            camera_present=camera is not None,
        )

        if actor_id is not None:
            self._close_manual_session(actor_id)

        self.movement_preview_camera = None
        self.movement_preview_publisher = None
        self.movement_preview_session_id = None
        self.movement_preview_actor_id = None
        self.movement_preview_world_id = None
        self.movement_preview_world_generation = None
        self.movement_control_queue = None
        self.manual_control_command = None
        self.manual_control_received_at = None
        self.manual_control_input_fresh = False

        if publisher is not None:
            publisher.close()
        if camera is not None:
            try:
                is_listening = getattr(camera, "is_listening", False)
                if callable(is_listening):
                    is_listening = is_listening()
                if is_listening:
                    camera.stop()
            except Exception:
                pass
            try:
                if camera.is_alive:
                    camera.destroy()
            except Exception as error:
                self.dashboard_monitor.record_error(error)

        if session_id is not None:
            self.database.movement_event_queue.put((
                "movement_camera_stopped",
                {
                    "session_id": session_id,
                    "world_id": world_id,
                    "world_generation": world_generation,
                    "reason": reason,
                },
            ))
        self._tm_debug_event(
            "movement_preview_cleanup_complete",
            reason=reason,
            session_id=session_id,
            actor_id=actor_id,
            preview_world_id=world_id,
            preview_world_generation=world_generation,
        )


