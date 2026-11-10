import os
import tkinter as tk
from tkinter import ttk

from preview.movement import CAMERA_PRESETS
from movement.destination import (
    CONFIRMABLE_DESTINATION_STATUSES,
    DESTINATION_EMPTY,
    waypoint_candidates,
)
from gui.widgets.navigation_map import COMPACT, INTERACTIVE, NavigationMap
from movement.vehicle import (
    AUTOPILOT_DESTINATION,
    NO_CONTROL,
    VEHICLE_CONTROL_MODES,
    VEHICLE_STATUS_IDLE,
)
from movement.walker import (
    DEFAULT_WALKER_SPEED,
    WALKER_CONTROL_MANUAL,
    WALKER_CONTROL_MODES,
)


class MovementControlWindow(tk.Toplevel):
    BASIC_AGENT_EXECUTOR = "basic_agent"
    BASIC_AGENT_TARGET_SPEED_ENV = (
        "CARLA_MANAGER_BASIC_AGENT_TARGET_SPEED"
    )
    BASIC_AGENT_ADAPTIVE_SPEED_ENV = (
        "CARLA_MANAGER_BASIC_AGENT_ADAPTIVE_SPEED"
    )
    BASIC_AGENT_DEFAULT_SPEED = 40.0

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
        self.actor_snapshot = {
            "actor_id": int(actor_id),
            "location": self._location_point(actor_location),
            "yaw": 0.0,
        }
        self.destination_state = initial_destination
        self.navigation_route = initial_control_state.get("route")
        self.navigation_map_data = None
        self.destination_map_window = None
        self.control_pending = False
        self.destination_executor = self._destination_executor(
            initial_control_state
        )
        initial_basic_agent_settings = self._initial_basic_agent_settings(
            initial_control_state
        )
        self.basic_agent_maximum_speed_var = tk.StringVar(
            value=self._format_basic_agent_speed(
                initial_basic_agent_settings["maximum_speed"]
            )
        )
        self.basic_agent_adaptive_speed_var = tk.BooleanVar(
            value=initial_basic_agent_settings["adaptive_speed"]
        )
        self.basic_agent_settings_locked = bool(
            initial_control_state.get(
                "basic_agent_settings_locked",
                False,
            )
        )

        self.title("Movement Control")
        self.geometry("760x780" if not self.is_vehicle else "760x760")
        self.minsize(700, 690)
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
        actor_frame.grid_columnconfigure(2, weight=1)
        self.navigation_map = NavigationMap(
            actor_frame,
            mode=COMPACT,
            width=280,
            height=145,
        )
        self.navigation_map.grid(
            row=0,
            column=2,
            rowspan=3,
            sticky="nsew",
            padx=(18, 0),
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
        self.basic_agent_settings_frame = None
        self.basic_agent_maximum_speed_spinbox = None
        self.basic_agent_adaptive_speed_checkbutton = None

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

            self.basic_agent_settings_frame = ttk.Frame(controls_frame)
            self.basic_agent_settings_frame.grid(
                row=2,
                column=1,
                sticky="w",
                pady=4,
            )
            ttk.Label(
                self.basic_agent_settings_frame,
                text="Maximum speed:",
            ).pack(side="left", padx=(0, 8))
            self.basic_agent_maximum_speed_spinbox = ttk.Spinbox(
                self.basic_agent_settings_frame,
                from_=10,
                to=90,
                increment=5,
                textvariable=self.basic_agent_maximum_speed_var,
                width=7,
            )
            self.basic_agent_maximum_speed_spinbox.pack(side="left")
            ttk.Label(
                self.basic_agent_settings_frame,
                text="km/h",
            ).pack(side="left", padx=(5, 18))
            self.basic_agent_adaptive_speed_checkbutton = ttk.Checkbutton(
                self.basic_agent_settings_frame,
                text="Adaptive speed",
                variable=self.basic_agent_adaptive_speed_var,
            )
            self.basic_agent_adaptive_speed_checkbutton.pack(side="left")

            ttk.Label(controls_frame, text="Navigation status:").grid(
                row=3, column=0, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Label(
                controls_frame,
                textvariable=self.vehicle_navigation_status_var,
            ).grid(row=3, column=1, sticky="w", pady=4)

            ttk.Label(controls_frame, text="Distance to destination:").grid(
                row=4, column=0, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Label(
                controls_frame,
                textvariable=self.vehicle_distance_var,
            ).grid(row=4, column=1, sticky="w", pady=4)

            ttk.Label(controls_frame, text="Current speed:").grid(
                row=5, column=0, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Label(
                controls_frame,
                textvariable=self.vehicle_speed_var,
            ).grid(row=5, column=1, sticky="w", pady=4)

            ttk.Label(controls_frame, text="Current state:").grid(
                row=6, column=0, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Label(
                controls_frame,
                textvariable=self.control_state_var,
                wraplength=310,
            ).grid(row=6, column=1, sticky="w", pady=4)
            preview_row = 7
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
        if self.is_vehicle:
            self._apply_basic_agent_settings_state(initial_control_state)
        else:
            self.set_walker_ai_state(initial_control_state)

    @classmethod
    def _destination_executor(cls, control_state):
        executor = control_state.get("destination_executor")
        route = control_state.get("route")
        if not executor and isinstance(route, dict):
            executor = route.get("executor")
        if not executor:
            executor = str(os.environ.get(
                "CARLA_MANAGER_DESTINATION_EXECUTOR",
                "basic_agent",
            )).strip().casefold() or "basic_agent"
        return str(executor).strip().casefold()

    @classmethod
    def _environment_basic_agent_settings(cls):
        raw_speed = str(os.environ.get(
            cls.BASIC_AGENT_TARGET_SPEED_ENV,
            "",
        )).strip()
        try:
            maximum_speed = (
                float(raw_speed)
                if raw_speed else cls.BASIC_AGENT_DEFAULT_SPEED
            )
        except (TypeError, ValueError):
            maximum_speed = cls.BASIC_AGENT_DEFAULT_SPEED
        if not 10.0 <= maximum_speed <= 90.0:
            maximum_speed = cls.BASIC_AGENT_DEFAULT_SPEED

        raw_adaptive = str(os.environ.get(
            cls.BASIC_AGENT_ADAPTIVE_SPEED_ENV,
            "1",
        )).strip().casefold()
        adaptive_speed = raw_adaptive not in {
            "0", "false", "no", "off",
        }
        return {
            "maximum_speed": maximum_speed,
            "adaptive_speed": adaptive_speed,
        }

    @classmethod
    def _initial_basic_agent_settings(cls, control_state):
        settings = control_state.get("basic_agent_settings")
        if not isinstance(settings, dict):
            return cls._environment_basic_agent_settings()
        defaults = cls._environment_basic_agent_settings()
        try:
            maximum_speed = float(settings.get(
                "maximum_speed",
                defaults["maximum_speed"],
            ))
        except (TypeError, ValueError):
            maximum_speed = defaults["maximum_speed"]
        if not 10.0 <= maximum_speed <= 90.0:
            maximum_speed = defaults["maximum_speed"]
        adaptive_speed = settings.get(
            "adaptive_speed",
            defaults["adaptive_speed"],
        )
        if not isinstance(adaptive_speed, bool):
            adaptive_speed = defaults["adaptive_speed"]
        return {
            "maximum_speed": maximum_speed,
            "adaptive_speed": adaptive_speed,
        }

    @staticmethod
    def _format_basic_agent_speed(speed):
        speed = float(speed)
        return str(int(speed)) if speed.is_integer() else f"{speed:g}"

    def _basic_agent_settings_from_controls(self):
        if self.destination_executor != self.BASIC_AGENT_EXECUTOR:
            return None
        try:
            maximum_speed = float(
                self.basic_agent_maximum_speed_var.get()
            )
        except (TypeError, ValueError, tk.TclError):
            self.control_state_var.set(
                "Maximum speed must be a number from 10 to 90 km/h."
            )
            return False
        if not 10.0 <= maximum_speed <= 90.0:
            self.control_state_var.set(
                "Maximum speed must be between 10 and 90 km/h."
            )
            return False
        return {
            "maximum_speed": maximum_speed,
            "adaptive_speed": bool(
                self.basic_agent_adaptive_speed_var.get()
            ),
        }

    def _refresh_basic_agent_settings_controls(self):
        if self.basic_agent_settings_frame is None:
            return
        visible = self.destination_executor == self.BASIC_AGENT_EXECUTOR
        if visible:
            self.basic_agent_settings_frame.grid()
        else:
            self.basic_agent_settings_frame.grid_remove()
        state = (
            "disabled"
            if (
                not visible
                or self.basic_agent_settings_locked
                or self.control_pending
                or self.closed
            )
            else "normal"
        )
        self.basic_agent_maximum_speed_spinbox.configure(state=state)
        self.basic_agent_adaptive_speed_checkbutton.configure(state=state)

    def _apply_basic_agent_settings_state(self, control_state):
        self.destination_executor = self._destination_executor(control_state)
        settings = control_state.get("basic_agent_settings")
        if isinstance(settings, dict):
            normalized = self._initial_basic_agent_settings(control_state)
            self.basic_agent_maximum_speed_var.set(
                self._format_basic_agent_speed(
                    normalized["maximum_speed"]
                )
            )
            self.basic_agent_adaptive_speed_var.set(
                normalized["adaptive_speed"]
            )
        self.basic_agent_settings_locked = bool(
            control_state.get("basic_agent_settings_locked", False)
        )
        self._refresh_basic_agent_settings_controls()

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
            self.destination_map_window.set_navigation(
                self.actor_snapshot,
                self.destination_state,
                self.navigation_route,
            )

        self.navigation_map.set_navigation(
            self.actor_snapshot,
            self.destination_state,
            self.navigation_route,
        )

    def show_destination_map(self, data):
        if self.closed:
            return
        actor_snapshot = data.get("actor_snapshot")
        if isinstance(actor_snapshot, dict):
            self.actor_snapshot = actor_snapshot
            self.actor_location = actor_snapshot.get("location")
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

    def set_navigation_map(self, data):
        if self.closed:
            return
        self.navigation_map_data = dict(data)
        self.actor_snapshot = data.get("actor_snapshot", self.actor_snapshot)
        self.navigation_route = data.get("route", self.navigation_route)
        self.navigation_map.set_map_data(data)
        self.navigation_map.set_navigation(
            self.actor_snapshot,
            self.destination_state,
            self.navigation_route,
        )

    def set_navigation_snapshot(self, snapshot):
        if self.closed or not isinstance(snapshot, dict):
            return
        self.actor_snapshot = snapshot
        self.actor_location = snapshot.get("location")
        if "destination" in snapshot:
            self.destination_state = snapshot.get("destination")
        self.navigation_route = snapshot.get("route")
        self.navigation_map.set_navigation(
            self.actor_snapshot,
            self.destination_state,
            self.navigation_route,
        )
        if (
            self.destination_map_window is not None
            and self.destination_map_window.winfo_exists()
        ):
            self.destination_map_window.set_navigation(
                self.actor_snapshot,
                self.destination_state,
                self.navigation_route,
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
        basic_agent_settings = self._basic_agent_settings_from_controls()
        if basic_agent_settings is False:
            return
        self.control_mode_var.set(AUTOPILOT_DESTINATION)
        if basic_agent_settings is None:
            self.on_mode_changed(AUTOPILOT_DESTINATION)
        else:
            self.on_mode_changed(
                AUTOPILOT_DESTINATION,
                basic_agent_settings,
            )

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
        self.control_pending = False
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
        self.navigation_route = control_state.get("route")
        if "destination" in control_state:
            self.destination_state = control_state.get("destination")
        self.navigation_map.set_navigation(
            self.actor_snapshot,
            self.destination_state,
            self.navigation_route,
        )
        if (
            self.destination_map_window is not None
            and self.destination_map_window.winfo_exists()
        ):
            self.destination_map_window.set_navigation(
                self.actor_snapshot,
                self.destination_state,
                self.navigation_route,
            )
        self.control_mode.configure(state="readonly")
        self.vehicle_start_destination_button.configure(state="normal")
        self.vehicle_cancel_destination_button.configure(state="normal")
        self._apply_basic_agent_settings_state(control_state)

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
        self.control_pending = True
        self.control_state_var.set(state)
        self.control_mode.configure(state="disabled")
        self.vehicle_start_destination_button.configure(state="disabled")
        self.vehicle_cancel_destination_button.configure(state="disabled")
        self._refresh_basic_agent_settings_controls()

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
        self.control_pending = True
        self.basic_agent_settings_locked = False
        self.preview_state_var.set(state)
        if self.control_mode is not None:
            self.control_mode.configure(state="disabled")
            self.vehicle_start_destination_button.configure(state="disabled")
            self.vehicle_cancel_destination_button.configure(state="disabled")
            self._refresh_basic_agent_settings_controls()
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
        self.actor_snapshot = None
        self.destination = destination
        self.route = None
        self.candidates = []
        self.title("Select Destination on CARLA Map")
        self.geometry("900x720")
        self.minsize(620, 480)
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
        self.navigation_map = NavigationMap(
            self,
            mode=INTERACTIVE,
            on_map_click=self._map_clicked,
        )
        self.navigation_map.grid(
            row=1, column=0, sticky="nsew", padx=12, pady=(0, 8)
        )
        self.lane_frame = ttk.Frame(self)
        self.lane_frame.grid(
            row=2, column=0, sticky="ew", padx=12, pady=(0, 12)
        )
        ttk.Label(self.lane_frame, text="Driving waypoint:").pack(
            side="left", padx=(0, 6)
        )
        self.lane_var = tk.StringVar()
        self.lane_combo = ttk.Combobox(
            self.lane_frame,
            textvariable=self.lane_var,
            state="readonly",
            width=65,
        )
        self.lane_combo.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(
            self.lane_frame,
            text="Use selected waypoint",
            command=self._use_selected_candidate,
        ).pack(side="left")
        self.lane_frame.grid_remove()
        self.update_map(map_data, destination)

    def update_map(self, map_data, destination=None):
        self.points = list(map_data.get("points") or [])
        self.actor_snapshot = map_data.get(
            "actor_snapshot", self.actor_snapshot
        )
        if destination is not None:
            self.destination = destination
        elif map_data.get("destination") is not None:
            self.destination = map_data.get("destination")
        self.route = map_data.get("route", self.route)
        map_name = str(map_data.get("map_name") or "CARLA map")
        self.title(f"Select Destination — {map_name}")
        self.navigation_map.set_map_data(map_data)
        self.navigation_map.set_navigation(
            self.actor_snapshot,
            self.destination,
            self.route,
        )

    def set_target(self, point):
        self.destination = None if point is None else {"selected": point}
        self.navigation_map.set_navigation(
            self.actor_snapshot,
            self.destination,
            self.route,
        )

    def set_navigation(self, actor_snapshot, destination, route):
        self.actor_snapshot = actor_snapshot
        self.destination = destination
        self.route = route
        self.navigation_map.set_navigation(
            actor_snapshot,
            destination,
            route,
        )

    @staticmethod
    def _candidate_label(candidate):
        yaw = candidate.get("yaw")
        yaw_text = "?" if yaw is None else f"{float(yaw):.1f} degrees"
        return (
            f"road {candidate.get('road_id')} / lane {candidate.get('lane_id')} / "
            f"section {candidate.get('section_id', 0)} | direction {yaw_text} | "
            f"distance {float(candidate.get('distance', 0.0)):.2f} m"
        )

    def _map_clicked(self, world_x, world_y):
        self.candidates = waypoint_candidates(
            self.points,
            world_x,
            world_y,
        )
        if not self.candidates:
            self.lane_frame.grid_remove()
            self.status_var.set(
                "No Driving waypoint is available near the selected position."
            )
            return
        labels = [self._candidate_label(item) for item in self.candidates]
        self.lane_combo.configure(values=labels)
        self.lane_var.set(labels[0])
        if len(self.candidates) > 1:
            self.lane_frame.grid()
            self.status_var.set(
                "Several Driving lanes are nearby. Choose the intended lane "
                "and direction, then confirm the waypoint."
            )
            self.set_target(self.candidates[0])
            return
        self.lane_frame.grid_remove()
        self._select_candidate(self.candidates[0])

    def _use_selected_candidate(self):
        if not self.candidates:
            return
        try:
            index = list(self.lane_combo["values"]).index(self.lane_var.get())
        except ValueError:
            index = 0
        self._select_candidate(self.candidates[index])

    def _select_candidate(self, candidate):
        target = dict(candidate)
        target.pop("distance", None)
        target["waypoint"] = dict(target)
        self.set_target(target)
        yaw = target.get("yaw")
        yaw_text = "unknown" if yaw is None else f"{float(yaw):.1f} degrees"
        self.status_var.set(
            f"Selected road {target.get('road_id')}, lane "
            f"{target.get('lane_id')}, direction {yaw_text}; validating..."
        )
        self.on_point_selected(target)
