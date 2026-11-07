import queue
import uuid
from tkinter import messagebox

from gui_contents.movement_control import MovementControlWindow
from movement_preview import LatestFramePublisher, MovementPreviewProcess
from vehicle_control import (
    NO_CONTROL,
    initial_vehicle_control_state,
    normalize_vehicle_control_mode,
)
from walker_control import initial_walker_ai_state


class MovementControlSessionManager:
    def __init__(self, manager):
        self.manager = manager
        self.preview = MovementPreviewProcess()
        self.window = None
        self.publisher = None
        self.session_id = None
        self.actor_id = None
        self.actor_type = None
        self.world_id = None
        self.camera_started = False
        self.camera_stopped = True
        self.closing = False
        self.error_shown = False

    def open(self, actor_id, actor_type):
        if self.window is not None or self.session_id is not None:
            if self.window is not None:
                self.window.focus_session()
            return False

        self.session_id = uuid.uuid4().hex
        self.actor_id = int(actor_id)
        self.actor_type = str(actor_type)
        self.camera_started = False
        self.camera_stopped = False
        self.closing = False
        self.error_shown = False
        initial_preset = "Third Person"
        with self.manager.database.lock:
            world = self.manager.database.world
            try:
                self.world_id = int(world.id)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                self.world_id = None
            if self.actor_type.startswith("vehicle."):
                control_state = self.manager.database.vehicle_control_states.get(
                    self.actor_id
                )
            else:
                control_state = self.manager.database.walker_ai_states.get(
                    self.actor_id
                )
            if control_state is not None:
                control_state = control_state.copy()
                if self.actor_type.startswith("vehicle."):
                    normalized_mode = normalize_vehicle_control_mode(
                        control_state.get("mode", NO_CONTROL)
                    )
                    control_state["mode"] = normalized_mode
                    control_state["control_mode"] = normalized_mode
            actor_info = self.manager.database.actors_db.actors_db.get(
                self.actor_id
            )
            actor_location = (
                None if actor_info is None else actor_info.location
            )
        if control_state is None:
            control_state = (
                initial_vehicle_control_state()
                if self.actor_type.startswith("vehicle.")
                else initial_walker_ai_state()
            )

        if not self.preview.start():
            self._reset_session()
            return False

        self.publisher = LatestFramePublisher(self.preview.frame_queue)
        self.window = MovementControlWindow(
            self.manager,
            self.actor_id,
            self.actor_type,
            initial_preset,
            control_state,
            self.change_control_mode,
            self.change_walker_ai_active,
            self.change_walker_speed,
            self.change_walker_control_mode,
            self.apply_walker_manual_control,
            self.change_camera_preset,
            self.request_actor_location,
            self.validate_destination,
            self.confirm_destination,
            self.clear_destination,
            self.request_destination_map,
            self.request_close,
            initial_destination=control_state.get("destination"),
            actor_location=actor_location,
        )
        self.manager.carla.start_movement_preview(
            self.session_id,
            self.actor_id,
            self.actor_type,
            initial_preset,
            self.publisher,
            self.preview.control_queue,
        )
        return True

    def change_control_mode(self, mode):
        if (
            self.closing
            or self.manager.carla is None
            or not self.actor_type.startswith("vehicle.")
        ):
            return
        if self.window is not None:
            self.window.set_control_pending(f"Switching to {mode}...")
        self.manager.carla.set_vehicle_control_mode(
            self.session_id,
            self.actor_id,
            mode,
        )

    def change_walker_ai_active(self, enabled, speed):
        if (
            self.closing
            or self.manager.carla is None
            or not self.actor_type.startswith("walker.")
        ):
            return
        if self.window is not None:
            action = "Starting" if enabled else "Stopping"
            self.window.set_walker_pending(f"{action} Walker AI...")
        self.manager.carla.set_walker_ai_enabled(
            self.session_id,
            self.actor_id,
            enabled,
            speed,
        )

    def change_walker_speed(self, speed):
        if (
            self.closing
            or self.manager.carla is None
            or not self.actor_type.startswith("walker.")
        ):
            return
        if self.window is not None:
            self.window.set_walker_pending("Applying Walker AI speed...")
        self.manager.carla.set_walker_ai_speed(
            self.session_id,
            self.actor_id,
            speed,
        )

    def change_walker_control_mode(self, mode, speed):
        if (
            self.closing
            or self.manager.carla is None
            or not self.actor_type.startswith("walker.")
        ):
            return
        if self.window is not None:
            self.window.set_walker_pending(f"Switching to {mode}...")
        self.manager.carla.set_walker_control_mode(
            self.session_id,
            self.actor_id,
            mode,
            speed,
        )

    def apply_walker_manual_control(self, direction_x, direction_y, speed):
        if (
            self.closing
            or self.manager.carla is None
            or not self.actor_type.startswith("walker.")
        ):
            return
        if self.window is not None:
            self.window.set_walker_pending("Applying manual WalkerControl...")
        self.manager.carla.set_walker_manual_control(
            self.session_id,
            self.actor_id,
            direction_x,
            direction_y,
            speed,
        )

    def change_camera_preset(self, preset):
        if self.closing or self.manager.carla is None:
            return
        if self.window is not None:
            self.window.set_preview_state(f"Applying camera preset: {preset}...")
        self.manager.carla.set_movement_preview_transform(
            self.session_id,
            preset,
        )

    def validate_destination(self, point):
        if self.closing or self.manager.carla is None:
            return
        if self.window is not None:
            self.window.set_destination_pending("Validating destination...")
        self.manager.carla.validate_movement_destination(
            self.session_id,
            self.actor_id,
            point.get("x"),
            point.get("y"),
            point.get("z"),
        )

    def request_actor_location(self):
        if self.closing or self.manager.carla is None:
            return
        if self.window is not None:
            self.window.set_destination_pending(
                "Reading current actor position..."
            )
        self.manager.carla.get_movement_actor_location(
            self.session_id,
            self.actor_id,
        )

    def confirm_destination(self):
        if self.closing or self.manager.carla is None:
            return
        if self.window is not None:
            self.window.set_destination_pending("Confirming destination...")
        self.manager.carla.confirm_movement_destination(
            self.session_id,
            self.actor_id,
        )

    def clear_destination(self):
        if self.closing or self.manager.carla is None:
            return
        if self.window is not None:
            self.window.set_destination_pending("Clearing destination...")
        self.manager.carla.clear_movement_destination(
            self.session_id,
            self.actor_id,
        )

    def request_destination_map(self):
        if self.closing or self.manager.carla is None:
            return
        if self.window is not None:
            self.window.set_destination_pending("Preparing CARLA road map...")
        self.manager.carla.get_movement_destination_map(
            self.session_id,
            self.actor_id,
        )

    def request_close(self, reason="Preview session closed"):
        if self.session_id is None:
            return
        if not self.closing:
            self.closing = True
            if self.publisher is not None:
                self.publisher.close()
            self.preview.request_stop()
            if self.window is not None:
                self.window.set_closing(reason)
            if self.manager.carla is not None:
                self.manager.carla.stop_movement_preview(
                    self.session_id,
                    reason,
                )
            else:
                self.camera_stopped = True

    def close_for_actor(self, actor_id, reason="Selected actor removed"):
        if self.actor_id == int(actor_id):
            self.request_close(reason)

    def poll(self):
        for status, data in self.preview.poll_events():
            if status == "running" and self.window is not None and not self.closing:
                self.window.set_preview_state(
                    "Pygame ready; starting RGB camera..."
                )
            elif status == "error":
                self._show_error(f"Could not run Movement Preview: {data}")
                self.request_close("Movement Preview error")
            elif status == "closed" and not self.closing:
                reason = data.get("reason", "closed") if isinstance(data, dict) else data
                self.request_close(f"Pygame preview closed: {reason}")

        self._poll_controller_events()

        if self.closing and self.preview.stop_timed_out(5.0):
            self.preview.force_stop()

        if (
            self.closing
            and not self.preview.is_active()
            and self.camera_stopped
        ):
            self._finish_close()

    def _poll_controller_events(self):
        event_queue = self.manager.database.movement_event_queue
        try:
            while True:
                status, data = event_queue.get_nowait()
                if not isinstance(data, dict):
                    continue
                if data.get("session_id") != self.session_id:
                    continue
                event_world_id = data.get("world_id")
                if status != "movement_camera_stopped":
                    try:
                        with self.manager.database.lock:
                            current_world_id = int(
                                self.manager.database.world.id
                            )
                    except (
                        AttributeError,
                        RuntimeError,
                        TypeError,
                        ValueError,
                    ):
                        current_world_id = None
                    try:
                        event_world_mismatch = (
                            event_world_id is not None
                            and self.world_id is not None
                            and int(event_world_id) != self.world_id
                        )
                    except (TypeError, ValueError):
                        event_world_mismatch = True
                    if (
                        event_world_mismatch
                    ) or (
                        current_world_id is not None
                        and self.world_id is not None
                        and current_world_id != self.world_id
                    ):
                        continue
                if status == "movement_camera_started":
                    self.camera_started = True
                    if self.window is not None and not self.closing:
                        self.window.set_preview_state(
                            f"Preview active (camera ID: {data['sensor_id']})."
                        )
                elif status == "movement_camera_preset_changed":
                    if self.window is not None and not self.closing:
                        self.window.set_preview_state(
                            f"Preview active — {data['preset']} camera."
                        )
                elif status == "vehicle_control_state":
                    if self.window is not None and not self.closing:
                        self.window.set_control_state(data["control_state"])
                elif status == "vehicle_control_error":
                    if self.window is not None and not self.closing:
                        self.window.set_control_state(data["control_state"])
                    self._show_error(
                        data.get("message", "Vehicle control error"),
                        once=False,
                    )
                elif status == "walker_ai_state":
                    if self.window is not None and not self.closing:
                        self.window.set_walker_ai_state(
                            data["walker_ai_state"]
                        )
                elif status == "walker_ai_error":
                    if self.window is not None and not self.closing:
                        self.window.set_walker_ai_state(
                            data["walker_ai_state"],
                            error=True,
                        )
                    self._show_error(
                        data.get("message", "Walker AI error"),
                        once=False,
                    )
                elif status in (
                    "movement_destination_validation",
                    "movement_destination_confirmed",
                    "movement_destination_cleared",
                ):
                    if self.window is not None and not self.closing:
                        self.window.set_destination_state(
                            data.get("destination")
                        )
                elif status == "movement_destination_error":
                    if self.window is not None and not self.closing:
                        self.window.set_destination_state(
                            data.get("destination"),
                            error=data.get("message"),
                        )
                elif status == "movement_destination_map":
                    if self.window is not None and not self.closing:
                        self.window.show_destination_map(data)
                elif status == "movement_destination_map_error":
                    if self.window is not None and not self.closing:
                        self.window.set_destination_state(
                            self.manager.database.get_movement_destination(
                                self.actor_id
                            ),
                            error=data.get("message"),
                        )
                elif status == "movement_actor_location":
                    if self.window is not None and not self.closing:
                        self.window.set_current_actor_location(
                            data.get("actor_location")
                        )
                elif status == "movement_camera_error":
                    self.camera_stopped = True
                    self._show_error(data.get("message", "Movement camera error"))
                    self.request_close("Movement camera error")
                elif status == "movement_camera_stopped":
                    self.camera_stopped = True
                    if not self.closing:
                        self.request_close(data.get("reason", "Movement camera stopped"))
        except queue.Empty:
            pass

    def _show_error(self, message, once=True):
        if (
            (once and self.error_shown)
            or self.manager.closing
            or self.manager.disconnecting
        ):
            return
        if once:
            self.error_shown = True
        messagebox.showerror("Movement Control", message, parent=self.window)

    def handle_disconnect(self):
        if self.session_id is None:
            return
        self.camera_stopped = True
        self.request_close("CARLA disconnected")
        self.preview.poll_events()
        if self.preview.is_active():
            self.preview.force_stop()
        self._finish_close()

    def is_active(self):
        return self.session_id is not None or self.preview.is_active()

    def _finish_close(self):
        if self.window is not None:
            self.window.finish_close()
        self._reset_session()

    def _reset_session(self):
        self.window = None
        self.publisher = None
        self.session_id = None
        self.actor_id = None
        self.actor_type = None
        self.world_id = None
        self.camera_started = False
        self.camera_stopped = True
        self.closing = False
        self.error_shown = False
