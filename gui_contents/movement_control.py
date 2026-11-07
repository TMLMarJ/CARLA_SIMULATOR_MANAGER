import tkinter as tk
from tkinter import ttk

from movement_preview import CAMERA_PRESETS
from movement_destination import (
    CONFIRMABLE_DESTINATION_STATUSES,
    DESTINATION_EMPTY,
    DestinationMapTransform,
    nearest_map_point,
)
from vehicle_control import (
    AUTOPILOT_DESTINATION,
    NO_CONTROL,
    VEHICLE_CONTROL_MODES,
    VEHICLE_STATUS_IDLE,
)
from walker_control import (
    DEFAULT_WALKER_SPEED,
    WALKER_CONTROL_MANUAL,
    WALKER_CONTROL_MODES,
)


class MovementControlWindow(tk.Toplevel):
    def __init__(self, parent, actor_id, actor_type, initial_preset,
                 initial_control_state, on_mode_changed,
                 on_walker_ai_changed, on_walker_speed_changed,
                 on_walker_mode_changed, on_walker_manual_control,
                 on_preset_changed, on_current_location,
                 on_destination_validate,
                 on_destination_confirm, on_destination_clear,
                 on_destination_map, on_close, initial_destination=None,
                 actor_location=None):
        super().__init__(parent)
        self.on_preset_changed = on_preset_changed
        self.on_mode_changed = on_mode_changed
        self.on_walker_ai_changed = on_walker_ai_changed
        self.on_walker_speed_changed = on_walker_speed_changed
        self.on_walker_mode_changed = on_walker_mode_changed
        self.on_walker_manual_control = on_walker_manual_control
        self.on_current_location = on_current_location
        self.on_destination_validate = on_destination_validate
        self.on_destination_confirm = on_destination_confirm
        self.on_destination_clear = on_destination_clear
        self.on_destination_map = on_destination_map
        self.on_close = on_close
        self.closed = False
        self.is_vehicle = actor_type.startswith("vehicle.")
        self.actor_location = actor_location
        self.destination_state = initial_destination
        self.destination_map_window = None

        self.title("Movement Control")
        self.geometry("680x760" if not self.is_vehicle else "680x700")
        self.minsize(620, 680 if not self.is_vehicle else 620)
        self.resizable(True, True)
        self.configure(bg="#F7E7D3")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(3, minsize=58)

        initial_mode = initial_control_state.get("mode", NO_CONTROL)
        if initial_mode not in VEHICLE_CONTROL_MODES:
            initial_mode = NO_CONTROL
        self.control_mode_var = tk.StringVar(value=initial_mode)
        self.control_state_var = tk.StringVar(
            value=initial_control_state.get("status", "No controller is active.")
        )
        self.vehicle_navigation_status_var = tk.StringVar(
            value=initial_control_state.get(
                "navigation_status",
                VEHICLE_STATUS_IDLE,
            )
        )
        initial_distance = initial_control_state.get("distance_to_destination")
        self.vehicle_distance_var = tk.StringVar(
            value=(
                "—" if initial_distance is None
                else f"{float(initial_distance):.1f} m"
            )
        )
        self.vehicle_speed_var = tk.StringVar(
            value=f"{float(initial_control_state.get('current_speed', 0.0)):.1f} m/s"
        )
        self.preview_state_var = tk.StringVar(value="Starting preview...")
        self.camera_preset_var = tk.StringVar(value=initial_preset)
        self.walker_ai_active = bool(initial_control_state.get("active", False))
        self.walker_speed_var = tk.StringVar(
            value=f"{initial_control_state.get('speed', DEFAULT_WALKER_SPEED):.1f}"
        )
        self._walker_speed_state_update = False
        self.walker_speed_edit_dirty = False
        self.walker_speed_pending = False
        self.walker_speed_requested = None
        self.walker_speed_var.trace_add("write", self._walker_speed_edited)
        self.walker_mode_var = tk.StringVar(
            value=initial_control_state.get(
                "control_mode",
                WALKER_CONTROL_MANUAL,
            )
        )
        manual_direction = initial_control_state.get("manual_direction") or {}
        self.walker_direction_x_var = tk.StringVar(
            value=f"{float(manual_direction.get('x', 1.0)):.3f}"
        )
        self.walker_direction_y_var = tk.StringVar(
            value=f"{float(manual_direction.get('y', 0.0)):.3f}"
        )

        actor_frame = ttk.LabelFrame(
            self,
            text="Selected Actor",
            style="Section.TLabelframe",
            padding=(10, 8),
        )
        actor_frame.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=14,
            pady=(14, 8),
        )
        ttk.Label(actor_frame, text="Actor ID:").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=3
        )
        ttk.Label(actor_frame, text=str(actor_id)).grid(
            row=0, column=1, sticky="w", pady=3
        )
        ttk.Label(actor_frame, text="Actor type:").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=3
        )
        ttk.Label(actor_frame, text=actor_type).grid(
            row=1, column=1, sticky="w", pady=3
        )

        destination_frame = ttk.LabelFrame(
            self,
            text="Destination",
            style="Section.TLabelframe",
            padding=(10, 8),
        )
        destination_frame.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=14,
            pady=8,
        )
        self.destination_vars = {
            axis: tk.StringVar(value="") for axis in ("x", "y", "z")
        }
        for column, axis in enumerate(("x", "y", "z")):
            ttk.Label(destination_frame, text=f"{axis.upper()}:").grid(
                row=0,
                column=column * 2,
                sticky="w",
                padx=(0 if column == 0 else 10, 4),
                pady=3,
            )
            ttk.Entry(
                destination_frame,
                textvariable=self.destination_vars[axis],
                width=13,
            ).grid(
                row=0,
                column=column * 2 + 1,
                sticky="ew",
                pady=3,
            )
            destination_frame.grid_columnconfigure(column * 2 + 1, weight=1)

        destination_buttons = ttk.Frame(destination_frame)
        destination_buttons.grid(
            row=1,
            column=0,
            columnspan=6,
            sticky="ew",
            pady=(7, 3),
        )
        self.destination_buttons = []
        for text, callback in (
            ("Current position", self._request_current_position),
            ("Validate", self._validate_destination),
            ("Select on map", self._request_destination_map),
            ("Confirm", self._confirm_destination),
            ("Clear", self._clear_destination),
        ):
            button = ttk.Button(
                destination_buttons,
                text=text,
                command=callback,
            )
            button.pack(side="left", padx=(0, 6))
            self.destination_buttons.append(button)
        self.destination_confirm_button = self.destination_buttons[3]
        self.destination_clear_button = self.destination_buttons[4]
        self.destination_status_var = tk.StringVar(
            value="No destination selected."
        )
        ttk.Label(
            destination_frame,
            textvariable=self.destination_status_var,
            wraplength=610,
        ).grid(
            row=2,
            column=0,
            columnspan=6,
            sticky="w",
            pady=(4, 0),
        )

        controls_frame = ttk.LabelFrame(
            self,
            text="Control Session",
            style="Section.TLabelframe",
            padding=(10, 8),
        )
        controls_frame.grid(
            row=2,
            column=0,
            sticky="nsew",
            padx=14,
            pady=8,
        )
        self.control_mode = None
        self.walker_toggle_button = None
        self.walker_speed_spinbox = None
        self.walker_speed_button = None
        self.vehicle_start_destination_button = None
        self.vehicle_cancel_destination_button = None

        if self.is_vehicle:
            ttk.Label(controls_frame, text="Control mode:").grid(
                row=0, column=0, sticky="w", padx=(0, 8), pady=4
            )
            self.control_mode = ttk.Combobox(
                controls_frame,
                textvariable=self.control_mode_var,
                values=VEHICLE_CONTROL_MODES,
                state="readonly",
                width=24,
            )
            self.control_mode.grid(row=0, column=1, sticky="ew", pady=4)
            self.control_mode.bind(
                "<<ComboboxSelected>>",
                self._control_mode_changed,
            )

            navigation_buttons = ttk.Frame(controls_frame)
            navigation_buttons.grid(row=1, column=1, sticky="w", pady=4)
            self.vehicle_start_destination_button = ttk.Button(
                navigation_buttons,
                text="Start Destination",
                command=self._start_vehicle_destination,
            )
            self.vehicle_start_destination_button.pack(side="left", padx=(0, 8))
            self.vehicle_cancel_destination_button = ttk.Button(
                navigation_buttons,
                text="Cancel Destination",
                command=self._cancel_vehicle_destination,
            )
            self.vehicle_cancel_destination_button.pack(side="left")

            ttk.Label(controls_frame, text="Navigation status:").grid(
                row=2, column=0, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Label(
                controls_frame,
                textvariable=self.vehicle_navigation_status_var,
            ).grid(row=2, column=1, sticky="w", pady=4)

            ttk.Label(controls_frame, text="Distance to destination:").grid(
                row=3, column=0, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Label(
                controls_frame,
                textvariable=self.vehicle_distance_var,
            ).grid(row=3, column=1, sticky="w", pady=4)

            ttk.Label(controls_frame, text="Current speed:").grid(
                row=4, column=0, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Label(
                controls_frame,
                textvariable=self.vehicle_speed_var,
            ).grid(row=4, column=1, sticky="w", pady=4)

            ttk.Label(controls_frame, text="Current state:").grid(
                row=5, column=0, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Label(
                controls_frame,
                textvariable=self.control_state_var,
                wraplength=310,
            ).grid(row=5, column=1, sticky="w", pady=4)
            preview_row = 6
        else:
            ttk.Label(controls_frame, text="Control mode:").grid(
                row=0, column=0, sticky="nw", padx=(0, 8), pady=4
            )
            walker_mode_frame = ttk.Frame(controls_frame)
            walker_mode_frame.grid(row=0, column=1, sticky="ew", pady=4)
            self.walker_mode_combo = ttk.Combobox(
                walker_mode_frame,
                textvariable=self.walker_mode_var,
                values=WALKER_CONTROL_MODES,
                state="readonly",
                width=27,
            )
            self.walker_mode_combo.pack(side="left", padx=(0, 8))
            self.walker_mode_button = ttk.Button(
                walker_mode_frame,
                text="Apply mode",
                command=self._apply_walker_mode,
            )
            self.walker_mode_button.pack(side="left")

            ttk.Label(controls_frame, text="Movement status:").grid(
                row=1, column=0, sticky="nw", padx=(0, 8), pady=4
            )
            ttk.Label(
                controls_frame,
                textvariable=self.control_state_var,
                wraplength=440,
            ).grid(row=1, column=1, sticky="w", pady=4)

            ttk.Label(controls_frame, text="Maximum speed:").grid(
                row=2, column=0, sticky="w", padx=(0, 8), pady=4
            )
            speed_frame = ttk.Frame(controls_frame)
            speed_frame.grid(row=2, column=1, sticky="ew", pady=4)
            self.walker_speed_spinbox = ttk.Spinbox(
                speed_frame,
                from_=0.1,
                to=10.0,
                increment=0.1,
                textvariable=self.walker_speed_var,
                width=10,
            )
            self.walker_speed_spinbox.pack(side="left")
            ttk.Label(speed_frame, text="m/s").pack(side="left", padx=(5, 10))
            self.walker_speed_button = ttk.Button(
                speed_frame,
                text="Apply speed",
                command=self._apply_walker_speed,
            )
            self.walker_speed_button.pack(side="left")

            ttk.Label(controls_frame, text="Manual direction:").grid(
                row=3, column=0, sticky="w", padx=(0, 8), pady=4
            )
            manual_frame = ttk.Frame(controls_frame)
            manual_frame.grid(row=3, column=1, sticky="ew", pady=4)
            ttk.Label(manual_frame, text="X").pack(side="left")
            self.walker_direction_x_entry = ttk.Entry(
                manual_frame,
                textvariable=self.walker_direction_x_var,
                width=9,
            )
            self.walker_direction_x_entry.pack(side="left", padx=(4, 8))
            ttk.Label(manual_frame, text="Y").pack(side="left")
            self.walker_direction_y_entry = ttk.Entry(
                manual_frame,
                textvariable=self.walker_direction_y_var,
                width=9,
            )
            self.walker_direction_y_entry.pack(side="left", padx=(4, 8))
            self.walker_manual_button = ttk.Button(
                manual_frame,
                text="Apply manual",
                command=self._apply_walker_manual,
            )
            self.walker_manual_button.pack(side="left")

            self.walker_toggle_button = ttk.Button(
                controls_frame,
                text="Stop / Cancel movement",
                command=self._stop_walker_movement,
            )
            self.walker_toggle_button.grid(
                row=4, column=1, sticky="w", pady=4
            )
            ttk.Label(
                controls_frame,
                text=(
                    "Destination follows a straight XY direction and does not "
                    "provide obstacle avoidance. Random Walk retains the "
                    "existing fallback steering policy."
                ),
                wraplength=500,
            ).grid(
                row=5,
                column=0,
                columnspan=2,
                sticky="w",
                pady=(6, 4),
            )
            preview_row = 6

        ttk.Label(controls_frame, text="Preview state:").grid(
            row=preview_row, column=0, sticky="w", padx=(0, 8), pady=4
        )
        ttk.Label(
            controls_frame,
            textvariable=self.preview_state_var,
            wraplength=310,
        ).grid(row=preview_row, column=1, sticky="w", pady=4)

        ttk.Label(controls_frame, text="Camera preset:").grid(
            row=preview_row + 1, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.camera_preset = ttk.Combobox(
            controls_frame,
            textvariable=self.camera_preset_var,
            values=CAMERA_PRESETS,
            state="readonly",
            width=24,
        )
        self.camera_preset.grid(
            row=preview_row + 1,
            column=1,
            sticky="ew",
            pady=4,
        )
        self.camera_preset.bind(
            "<<ComboboxSelected>>",
            self._camera_preset_changed,
        )
        controls_frame.grid_columnconfigure(1, weight=1)

        self.close_button = ttk.Button(
            self,
            text="Close Preview Session",
            command=self._request_close,
            width=24,
        )
        self.close_button.grid(
            row=3,
            column=0,
            sticky="s",
            pady=(8, 14),
        )

        self.protocol("WM_DELETE_WINDOW", self._request_close)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.set_destination_state(initial_destination)
        if not self.is_vehicle:
            self.set_walker_ai_state(initial_control_state)

    @staticmethod
    def _location_point(location):
        if location is None:
            return None
        try:
            return {
                "x": float(location.x),
                "y": float(location.y),
                "z": float(location.z),
            }
        except (AttributeError, TypeError, ValueError):
            if isinstance(location, dict):
                try:
                    return {
                        axis: float(location[axis])
                        for axis in ("x", "y", "z")
                    }
                except (KeyError, TypeError, ValueError):
                    return None
            return None

    def _set_destination_fields(self, point):
        if point is None:
            for variable in self.destination_vars.values():
                variable.set("")
            return
        for axis in ("x", "y", "z"):
            self.destination_vars[axis].set(f"{float(point[axis]):.3f}")

    def _destination_fields(self):
        return {
            axis: self.destination_vars[axis].get().strip()
            for axis in ("x", "y", "z")
        }

    def _request_current_position(self):
        self.on_current_location()

    def set_current_actor_location(self, location):
        self.actor_location = location
        point = self._location_point(self.actor_location)
        if point is None:
            self.destination_status_var.set(
                "Current actor position is unavailable; refresh the world."
            )
            return
        self._set_destination_fields(point)
        self.destination_status_var.set(
            "Current actor position copied. Validate it before confirming."
        )
        for button in self.destination_buttons:
            button.configure(state="normal")
        self.destination_confirm_button.configure(state="disabled")
        self.destination_clear_button.configure(
            state=(
                "normal"
                if isinstance(self.destination_state, dict)
                and self.destination_state.get("requested") is not None
                else "disabled"
            )
        )

    def _validate_destination(self):
        self.on_destination_validate(self._destination_fields())

    def _confirm_destination(self):
        self.on_destination_confirm()

    def _clear_destination(self):
        self.on_destination_clear()

    def _request_destination_map(self):
        self.on_destination_map()

    def _map_point_selected(self, point):
        self._set_destination_fields(point)
        self.on_destination_validate(point)

    def set_destination_pending(self, message):
        if self.closed:
            return
        self.destination_status_var.set(str(message))
        for button in self.destination_buttons:
            button.configure(state="disabled")

    def set_destination_state(self, destination, error=None):
        if self.closed:
            return
        self.destination_state = destination
        status = (
            DESTINATION_EMPTY
            if not isinstance(destination, dict)
            else destination.get("validation_status", DESTINATION_EMPTY)
        )
        if isinstance(destination, dict) and destination.get("requested"):
            self._set_destination_fields(destination["requested"])
        elif status == DESTINATION_EMPTY:
            self._set_destination_fields(None)
        message = (
            str(error)
            if error
            else (
                destination.get("message", "No destination selected.")
                if isinstance(destination, dict)
                else "No destination selected."
            )
        )
        if isinstance(destination, dict) and destination.get("adjusted"):
            adjusted = destination["adjusted"]
            message += (
                f" Corrected: X={adjusted['x']:.3f}, "
                f"Y={adjusted['y']:.3f}, Z={adjusted['z']:.3f}."
            )
        if isinstance(destination, dict) and destination.get("confirmed"):
            message = f"Confirmed — {message}"
        self.destination_status_var.set(message)
        for button in self.destination_buttons:
            button.configure(state="normal")
        confirmable = (
            status in CONFIRMABLE_DESTINATION_STATUSES
            and not bool(destination.get("confirmed", False))
        ) if isinstance(destination, dict) else False
        self.destination_confirm_button.configure(
            state="normal" if confirmable else "disabled"
        )
        self.destination_clear_button.configure(
            state=(
                "normal"
                if isinstance(destination, dict)
                and destination.get("requested") is not None
                else "disabled"
            )
        )
        if (
            self.destination_map_window is not None
            and self.destination_map_window.winfo_exists()
        ):
            target = None
            if isinstance(destination, dict):
                target = (
                    destination.get("adjusted")
                    or destination.get("requested")
                )
            self.destination_map_window.set_target(target)

    def show_destination_map(self, data):
        if self.closed:
            return
        self.actor_location = data.get("actor_location")
        self.set_destination_state(self.destination_state)
        if (
            self.destination_map_window is not None
            and self.destination_map_window.winfo_exists()
        ):
            self.destination_map_window.update_map(data)
            self.destination_map_window.focus_force()
            return
        self.destination_map_window = DestinationMapWindow(
            self,
            data,
            self._map_point_selected,
            self.destination_state,
        )

    def _control_mode_changed(self, event=None):
        mode = self.control_mode_var.get()
        if mode == AUTOPILOT_DESTINATION:
            self.control_state_var.set(
                "Destination mode selected. Press Start Destination to plan the route."
            )
            return
        self.on_mode_changed(mode)

    def _start_vehicle_destination(self):
        self.control_mode_var.set(AUTOPILOT_DESTINATION)
        self.on_mode_changed(AUTOPILOT_DESTINATION)

    def _cancel_vehicle_destination(self):
        self.control_mode_var.set(NO_CONTROL)
        self.on_mode_changed(NO_CONTROL)

    def _camera_preset_changed(self, event=None):
        self.on_preset_changed(self.camera_preset_var.get())

    def _walker_speed(self):
        try:
            return float(self.walker_speed_var.get())
        except (TypeError, ValueError, tk.TclError):
            self.control_state_var.set("Enter a valid numeric Walker AI speed.")
            return None

    def _walker_speed_edited(self, *_args):
        if not self._walker_speed_state_update:
            self.walker_speed_edit_dirty = True

    def _begin_walker_speed_request(self, speed):
        self.walker_speed_pending = True
        self.walker_speed_requested = float(speed)
        self.walker_speed_edit_dirty = False

    def _toggle_walker_ai(self):
        speed = self._walker_speed()
        if speed is not None:
            self._begin_walker_speed_request(speed)
            self.on_walker_ai_changed(not self.walker_ai_active, speed)

    def _apply_walker_mode(self):
        speed = self._walker_speed()
        if speed is not None:
            self._begin_walker_speed_request(speed)
            self.on_walker_mode_changed(
                self.walker_mode_var.get(),
                speed,
            )

    def _stop_walker_movement(self):
        speed = self._walker_speed()
        if speed is not None:
            self._begin_walker_speed_request(speed)
            self.walker_mode_var.set(WALKER_CONTROL_MANUAL)
            self.on_walker_mode_changed(WALKER_CONTROL_MANUAL, speed)

    def _apply_walker_manual(self):
        speed = self._walker_speed()
        if speed is not None:
            self._begin_walker_speed_request(speed)
            self.on_walker_manual_control(
                self.walker_direction_x_var.get().strip(),
                self.walker_direction_y_var.get().strip(),
                speed,
            )

    def _apply_walker_speed(self):
        speed = self._walker_speed()
        if speed is not None:
            self._begin_walker_speed_request(speed)
            self.on_walker_speed_changed(speed)

    def _update_walker_toggle_text(self):
        if self.walker_toggle_button is not None:
            self.walker_toggle_button.configure(
                text="Stop Walker AI" if self.walker_ai_active else "Start Walker AI"
            )

    def set_preview_state(self, state):
        if not self.closed:
            self.preview_state_var.set(state)

    def set_control_state(self, control_state):
        if self.closed or not self.is_vehicle:
            return
        mode = control_state.get("mode", NO_CONTROL)
        if mode in VEHICLE_CONTROL_MODES:
            self.control_mode_var.set(mode)
        self.control_state_var.set(control_state.get("status", mode))
        self.vehicle_navigation_status_var.set(
            control_state.get("navigation_status", VEHICLE_STATUS_IDLE)
        )
        distance = control_state.get("distance_to_destination")
        self.vehicle_distance_var.set(
            "—" if distance is None else f"{float(distance):.1f} m"
        )
        self.vehicle_speed_var.set(
            f"{float(control_state.get('current_speed', 0.0)):.1f} m/s"
        )
        self.control_mode.configure(state="readonly")
        self.vehicle_start_destination_button.configure(state="normal")
        self.vehicle_cancel_destination_button.configure(state="normal")

    def set_walker_ai_state(self, state, error=False):
        if self.closed or self.is_vehicle:
            return
        self.walker_ai_active = bool(state.get("active", False))
        control_mode = state.get("control_mode", WALKER_CONTROL_MANUAL)
        if control_mode in WALKER_CONTROL_MODES:
            self.walker_mode_var.set(control_mode)
        movement_status = state.get("movement_status", "Idle")
        self.control_state_var.set(
            f"{movement_status} — "
            f"{state.get('status', 'Walker control state is unavailable.')}"
        )
        server_speed = float(state.get("speed", DEFAULT_WALKER_SPEED))
        if error:
            self.walker_speed_pending = False
            self.walker_speed_requested = None
            self.walker_speed_edit_dirty = False
        elif self.walker_speed_pending:
            if (
                self.walker_speed_requested is not None
                and abs(server_speed - self.walker_speed_requested) <= 1e-6
            ):
                self.walker_speed_pending = False
                self.walker_speed_requested = None
                self.walker_speed_edit_dirty = False

        if not self.walker_speed_edit_dirty and not self.walker_speed_pending:
            self._walker_speed_state_update = True
            try:
                self.walker_speed_var.set(f"{server_speed:.1f}")
            finally:
                self._walker_speed_state_update = False
        direction = state.get("manual_direction") or {}
        self.walker_direction_x_var.set(
            f"{float(direction.get('x', 1.0)):.3f}"
        )
        self.walker_direction_y_var.set(
            f"{float(direction.get('y', 0.0)):.3f}"
        )
        if not self.walker_speed_pending:
            self.walker_toggle_button.configure(state="normal")
            self.walker_speed_spinbox.configure(state="normal")
            self.walker_speed_button.configure(state="normal")
            self.walker_mode_combo.configure(state="readonly")
            self.walker_mode_button.configure(state="normal")
            self.walker_direction_x_entry.configure(state="normal")
            self.walker_direction_y_entry.configure(state="normal")
            self.walker_manual_button.configure(state="normal")

    def set_control_pending(self, state):
        if self.closed:
            return
        self.control_state_var.set(state)
        self.control_mode.configure(state="disabled")
        self.vehicle_start_destination_button.configure(state="disabled")
        self.vehicle_cancel_destination_button.configure(state="disabled")

    def set_walker_pending(self, state):
        if self.closed or self.is_vehicle:
            return
        self.control_state_var.set(state)
        self.walker_toggle_button.configure(state="disabled")
        self.walker_speed_spinbox.configure(state="disabled")
        self.walker_speed_button.configure(state="disabled")
        self.walker_mode_combo.configure(state="disabled")
        self.walker_mode_button.configure(state="disabled")
        self.walker_direction_x_entry.configure(state="disabled")
        self.walker_direction_y_entry.configure(state="disabled")
        self.walker_manual_button.configure(state="disabled")

    def set_closing(self, state):
        if self.closed:
            return
        self.preview_state_var.set(state)
        if self.control_mode is not None:
            self.control_mode.configure(state="disabled")
            self.vehicle_start_destination_button.configure(state="disabled")
            self.vehicle_cancel_destination_button.configure(state="disabled")
        if self.walker_toggle_button is not None:
            self.walker_toggle_button.configure(state="disabled")
            self.walker_speed_spinbox.configure(state="disabled")
            self.walker_speed_button.configure(state="disabled")
            self.walker_mode_combo.configure(state="disabled")
            self.walker_mode_button.configure(state="disabled")
            self.walker_direction_x_entry.configure(state="disabled")
            self.walker_direction_y_entry.configure(state="disabled")
            self.walker_manual_button.configure(state="disabled")
        self.camera_preset.configure(state="disabled")
        for button in self.destination_buttons:
            button.configure(state="disabled")
        self.close_button.configure(state="disabled")

    def focus_session(self):
        if not self.closed:
            self.deiconify()
            self.lift()
            self.focus_force()

    def finish_close(self):
        if self.closed:
            return
        self.closed = True
        if (
            self.destination_map_window is not None
            and self.destination_map_window.winfo_exists()
        ):
            self.destination_map_window.destroy()
        self.destroy()

    def _request_close(self):
        if not self.closed:
            self.on_close("Movement Control window closed")

    def _on_destroy(self, event):
        if event.widget is self and not self.closed:
            self.on_close("Movement Control window destroyed")


class DestinationMapWindow(tk.Toplevel):
    def __init__(self, parent, map_data, on_point_selected,
                 destination=None):
        super().__init__(parent)
        self.on_point_selected = on_point_selected
        self.points = []
        self.actor_location = None
        self.target = None
        self.map_transform = None
        self.title("Select Destination on CARLA Map")
        self.geometry("760x620")
        self.minsize(520, 420)
        self.configure(bg="#F7E7D3")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.status_var = tk.StringVar(
            value="Click a road point to select and validate a destination."
        )
        ttk.Label(
            self,
            textvariable=self.status_var,
            wraplength=720,
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        self.canvas = tk.Canvas(
            self,
            background="#FAF4EA",
            highlightthickness=1,
            highlightbackground="#C96D3C",
        )
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.canvas.bind("<Configure>", self._redraw)
        self.canvas.bind("<Button-1>", self._clicked)
        self.update_map(map_data, destination)

    def update_map(self, map_data, destination=None):
        self.points = list(map_data.get("points") or [])
        self.actor_location = map_data.get("actor_location")
        if isinstance(destination, dict):
            self.target = (
                destination.get("adjusted")
                or destination.get("requested")
            )
        map_name = str(map_data.get("map_name") or "CARLA map")
        self.title(f"Select Destination — {map_name}")
        self.after_idle(self._redraw)

    def set_target(self, point):
        self.target = point
        self.after_idle(self._redraw)

    def _redraw(self, _event=None):
        if not self.points or not self.winfo_exists():
            return
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        self.map_transform = DestinationMapTransform(
            self.points,
            width,
            height,
        )
        self.canvas.delete("all")
        lanes = {}
        for point in self.points:
            key = (point.get("road_id", 0), point.get("lane_id", 0))
            lanes.setdefault(key, []).append(point)
        for lane_points in lanes.values():
            lane_points.sort(key=lambda point: float(point.get("s", 0.0)))
            coordinates = []
            for point in lane_points:
                coordinates.extend(
                    self.map_transform.world_to_canvas(
                        point["x"],
                        point["y"],
                    )
                )
            if len(coordinates) >= 4:
                self.canvas.create_line(
                    *coordinates,
                    fill="#8A8178",
                    width=1,
                )
            elif coordinates:
                x, y = coordinates
                self.canvas.create_oval(
                    x - 1,
                    y - 1,
                    x + 1,
                    y + 1,
                    fill="#8A8178",
                    outline="",
                )
        self._draw_marker(self.actor_location, "#2F6FB0", "Actor")
        self._draw_marker(self.target, "#C96D3C", "Destination")

    def _draw_marker(self, point, color, label):
        if not isinstance(point, dict) or self.map_transform is None:
            return
        x, y = self.map_transform.world_to_canvas(point["x"], point["y"])
        radius = 5
        self.canvas.create_oval(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            fill=color,
            outline="white",
            width=1,
        )
        self.canvas.create_text(
            x + 8,
            y - 8,
            text=label,
            fill=color,
            anchor="sw",
        )

    def _clicked(self, event):
        if self.map_transform is None or not self.points:
            return
        left, top = self.map_transform.world_to_canvas(
            self.map_transform.min_x,
            self.map_transform.max_y,
        )
        right, bottom = self.map_transform.world_to_canvas(
            self.map_transform.max_x,
            self.map_transform.min_y,
        )
        canvas_x = min(max(float(event.x), left), right)
        canvas_y = min(max(float(event.y), top), bottom)
        world_x, world_y = self.map_transform.canvas_to_world(
            canvas_x,
            canvas_y,
        )
        nearest = nearest_map_point(self.points, world_x, world_y)
        if nearest is None:
            return
        self.target = {
            "x": world_x,
            "y": world_y,
            "z": float(nearest["z"]),
        }
        self.status_var.set(
            f"Selected X={world_x:.3f}, Y={world_y:.3f}, "
            f"Z={self.target['z']:.3f}; validating..."
        )
        self._redraw()
        self.on_point_selected(dict(self.target))
