import tkinter as tk
from tkinter import ttk

from movement_preview import CAMERA_PRESETS
from vehicle_control import NO_CONTROL, VEHICLE_CONTROL_MODES
from walker_control import DEFAULT_WALKER_SPEED


class MovementControlWindow(tk.Toplevel):
    def __init__(self, parent, actor_id, actor_type, initial_preset,
                 initial_control_state, on_mode_changed,
                 on_walker_ai_changed, on_walker_speed_changed,
                 on_preset_changed, on_close):
        super().__init__(parent)
        self.on_preset_changed = on_preset_changed
        self.on_mode_changed = on_mode_changed
        self.on_walker_ai_changed = on_walker_ai_changed
        self.on_walker_speed_changed = on_walker_speed_changed
        self.on_close = on_close
        self.closed = False
        self.is_vehicle = actor_type.startswith("vehicle.")

        self.title("Movement Control")
        self.geometry("520x480" if not self.is_vehicle else "500x430")
        self.minsize(480, 440 if not self.is_vehicle else 400)
        self.resizable(True, True)
        self.configure(bg="#F7E7D3")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, minsize=58)

        initial_mode = initial_control_state.get("mode", NO_CONTROL)
        if initial_mode not in VEHICLE_CONTROL_MODES:
            initial_mode = NO_CONTROL
        self.control_mode_var = tk.StringVar(value=initial_mode)
        self.control_state_var = tk.StringVar(
            value=initial_control_state.get("status", "No controller is active.")
        )
        self.preview_state_var = tk.StringVar(value="Starting preview...")
        self.camera_preset_var = tk.StringVar(value=initial_preset)
        self.walker_ai_active = bool(initial_control_state.get("active", False))
        self.walker_speed_var = tk.StringVar(
            value=f"{initial_control_state.get('speed', DEFAULT_WALKER_SPEED):.1f}"
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

        controls_frame = ttk.LabelFrame(
            self,
            text="Control Session",
            style="Section.TLabelframe",
            padding=(10, 8),
        )
        controls_frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=14,
            pady=8,
        )
        self.control_mode = None
        self.walker_toggle_button = None
        self.walker_speed_spinbox = None
        self.walker_speed_button = None

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

            ttk.Label(controls_frame, text="Current state:").grid(
                row=1, column=0, sticky="w", padx=(0, 8), pady=4
            )
            ttk.Label(
                controls_frame,
                textvariable=self.control_state_var,
                wraplength=310,
            ).grid(row=1, column=1, sticky="w", pady=4)
            preview_row = 2
        else:
            ttk.Label(controls_frame, text="Walker AI state:").grid(
                row=0, column=0, sticky="nw", padx=(0, 8), pady=4
            )
            ttk.Label(
                controls_frame,
                textvariable=self.control_state_var,
                wraplength=320,
            ).grid(row=0, column=1, sticky="w", pady=4)

            ttk.Label(controls_frame, text="Maximum speed:").grid(
                row=1, column=0, sticky="w", padx=(0, 8), pady=4
            )
            speed_frame = ttk.Frame(controls_frame)
            speed_frame.grid(row=1, column=1, sticky="ew", pady=4)
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

            self.walker_toggle_button = ttk.Button(
                controls_frame,
                command=self._toggle_walker_ai,
            )
            self.walker_toggle_button.grid(
                row=2,
                column=1,
                sticky="w",
                pady=4,
            )
            ttk.Label(
                controls_frame,
                text=(
                    "Random navigation is enabled. After reaching a destination, "
                    "the walker receives another map navigation point."
                ),
                wraplength=390,
            ).grid(
                row=3,
                column=0,
                columnspan=2,
                sticky="w",
                pady=(6, 4),
            )
            self._update_walker_toggle_text()
            preview_row = 4

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
            row=2,
            column=0,
            sticky="s",
            pady=(8, 14),
        )

        self.protocol("WM_DELETE_WINDOW", self._request_close)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _control_mode_changed(self, event=None):
        self.on_mode_changed(self.control_mode_var.get())

    def _camera_preset_changed(self, event=None):
        self.on_preset_changed(self.camera_preset_var.get())

    def _walker_speed(self):
        try:
            return float(self.walker_speed_var.get())
        except (TypeError, ValueError, tk.TclError):
            self.control_state_var.set("Enter a valid numeric Walker AI speed.")
            return None

    def _toggle_walker_ai(self):
        speed = self._walker_speed()
        if speed is not None:
            self.on_walker_ai_changed(not self.walker_ai_active, speed)

    def _apply_walker_speed(self):
        speed = self._walker_speed()
        if speed is not None:
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
        self.control_mode.configure(state="readonly")

    def set_walker_ai_state(self, state):
        if self.closed or self.is_vehicle:
            return
        self.walker_ai_active = bool(state.get("active", False))
        self.control_state_var.set(
            state.get("status", "Walker AI state is unavailable.")
        )
        self.walker_speed_var.set(
            f"{state.get('speed', DEFAULT_WALKER_SPEED):.1f}"
        )
        self._update_walker_toggle_text()
        self.walker_toggle_button.configure(state="normal")
        self.walker_speed_spinbox.configure(state="normal")
        self.walker_speed_button.configure(state="normal")

    def set_control_pending(self, state):
        if self.closed:
            return
        self.control_state_var.set(state)
        self.control_mode.configure(state="disabled")

    def set_walker_pending(self, state):
        if self.closed or self.is_vehicle:
            return
        self.control_state_var.set(state)
        self.walker_toggle_button.configure(state="disabled")
        self.walker_speed_spinbox.configure(state="disabled")
        self.walker_speed_button.configure(state="disabled")

    def set_closing(self, state):
        if self.closed:
            return
        self.preview_state_var.set(state)
        if self.control_mode is not None:
            self.control_mode.configure(state="disabled")
        if self.walker_toggle_button is not None:
            self.walker_toggle_button.configure(state="disabled")
            self.walker_speed_spinbox.configure(state="disabled")
            self.walker_speed_button.configure(state="disabled")
        self.camera_preset.configure(state="disabled")
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
        self.destroy()

    def _request_close(self):
        if not self.closed:
            self.on_close("Movement Control window closed")

    def _on_destroy(self, event):
        if event.widget is self and not self.closed:
            self.on_close("Movement Control window destroyed")
