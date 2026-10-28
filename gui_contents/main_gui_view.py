import tkinter as tk
from tkinter import ttk

from utilities.functions import filter_carla_id_by_type, return_data_from_transform, wait_and_try
from gui_contents.spawn_preview import VehiclePreviewFrame, GhostPreview
from gui_contents.ActorEditView import EntityEditWindow
from gui_contents.change_model_top import ChangeModelTop
from gui_contents.set_transform_top import ChangeTransformTop
import messagebox
import carla
import numpy as np
from simulation_manager import SimulationManager
from gui_contents.simulation_dashboard_view import SimulationDashboardFrame


class CustomMainViewFrame(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.database = controller.database
        self.carla = controller.carla

        self.default_color = "#F7E7D3"
        self.panel_color = "#F1D0A3"
        self.accent_color = "#C96D3C"
        self.text_color = "#4A2E1D"
        self.subtle_color = "#E7B36C"

        self.canvas = tk.Canvas(
            self,
            background=self.default_color,
            highlightthickness=0,
        )
        self.scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
        )
        self.content_frame = ttk.Frame(self.canvas)
        self.content_window = self.canvas.create_window(
            (0, 0),
            window=self.content_frame,
            anchor="nw",
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.content_frame.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_content)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

        self.connection_frame = ConnectionFrame(self.content_frame, self.controller)
        self.connection_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nw")

        self.spectator_frame = SpectatorFrame(self.content_frame, self.controller)
        self.spectator_frame.grid(row=0, column=1, padx=5, pady=5, sticky="nw")

        self.simulation_frame = SimulationControlFrame(self.content_frame, self.controller)
        self.simulation_frame.grid(
            row=1,
            column=0,
            columnspan=2,
            padx=5,
            pady=5,
            sticky="new",
        )

        self.dashboard_frame = SimulationDashboardFrame(self.content_frame)
        self.dashboard_frame.grid(
            row=2,
            column=0,
            columnspan=2,
            padx=5,
            pady=5,
            sticky="nsew",
        )

        self.content_frame.grid_columnconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(1, weight=1)

    def _update_scroll_region(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_content(self, event):
        self.canvas.itemconfigure(self.content_window, width=event.width)

    def _bind_mousewheel(self, event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, event=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class ConnectionFrame(ttk.Frame):
    def __init__(self, parent, manager):
        super().__init__(parent)

        self.manager = manager

        self.host_var = tk.StringVar(value="127.0.0.1")
        self.port_var = tk.StringVar(value="2000")

        self._create_widgets()

    def _create_widgets(self):
        ttk.Label(
            self,
            text="CARLA Server Connection"
        ).pack(pady=10)

        input_frame = ttk.Frame(self)
        input_frame.pack(pady=10)

        ttk.Label(
            input_frame,
            text="IP Address:"
        ).grid(row=0, column=0, padx=5, pady=5)

        self.host_entry = ttk.Entry(
            input_frame,
            textvariable=self.host_var,
            width=20
        )
        self.host_entry.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(
            input_frame,
            text="Port:"
        ).grid(row=1, column=0, padx=5, pady=5)

        self.port_entry = ttk.Entry(
            input_frame,
            textvariable=self.port_var,
            width=20
        )
        self.port_entry.grid(row=1, column=1, padx=5, pady=5)

        self.connect_button = ttk.Button(
            self,
            text="Connect",
            command=self.connect
        )
        self.connect_button.pack(pady=5, padx=5, side="left")

        self.disconnect_button = ttk.Button(
            self,
            text="Disconnect",
            command=self.manager.disconnect_from_carla,
            state="disabled"
        )
        self.disconnect_button.pack(pady=5, padx=5, side="left")

    def connect(self):
        host = self.host_var.get().strip()

        try:
            port = int(self.port_var.get())

            if not host:
                raise ValueError("IP address cannot be empty")

            if not 1 <= port <= 65535:
                raise ValueError("Port must be between 1 and 65535")

        except ValueError as e:
            messagebox.showerror(
                "Invalid connection settings",
                str(e)
            )
            return

        self.manager.connect_to_carla(host, port)

    def update_connection_state(self, connected, busy=False):
        state = "disabled" if connected or busy else "normal"

        self.connect_button.configure(state=state)
        self.disconnect_button.configure(
            state="normal" if connected and not busy else "disabled"
        )

        self.host_entry.configure(state=state)
        self.port_entry.configure(state=state)


class SimulationControlFrame(ttk.Frame):
    def __init__(self, parent, manager):
        super().__init__(parent)
        self.manager = manager
        self.connected = False
        self.pending = False
        self.control_pending = False
        self.external_busy = False
        self.advanced_visible = False
        self.control_state = SimulationManager.DISCONNECTED
        self.control_synchronous_mode = False
        self.last_control_error = None

        self.status_var = tk.StringVar(value="Disconnected")
        self.mode_var = tk.StringVar(value="")
        self.fixed_delta_var = tk.StringVar(value="")
        self.no_rendering_var = tk.BooleanVar(value=False)
        self.substepping_var = tk.BooleanVar(value=False)
        self.max_substep_delta_var = tk.StringVar(value="")
        self.max_substeps_var = tk.StringVar(value="")
        self.tick_controller_var = tk.StringVar(value="External")
        self.control_status_var = tk.StringVar(value="Disconnected")
        self.frame_status_var = tk.StringVar(value="Last observed frame: -")
        self.step_count_var = tk.StringVar(value="10")
        self.setting_widgets = []

        self._create_widgets()
        self._update_widget_states()

    def _create_widgets(self):
        ttk.Label(self, text="Simulation Control").grid(
            row=0,
            column=0,
            columnspan=4,
            sticky="w",
            padx=8,
            pady=(10, 4),
        )
        ttk.Label(self, text="Status:").grid(
            row=0,
            column=4,
            sticky="e",
            padx=(8, 4),
            pady=(10, 4),
        )
        ttk.Label(self, textvariable=self.status_var).grid(
            row=0,
            column=5,
            sticky="w",
            padx=(0, 8),
            pady=(10, 4),
        )

        settings_frame = ttk.LabelFrame(
            self,
            text="World Settings",
            style="Section.TLabelframe",
            padding=(8, 6),
        )
        settings_frame.grid(
            row=1,
            column=0,
            columnspan=6,
            sticky="ew",
            padx=8,
            pady=(0, 8),
        )

        ttk.Label(settings_frame, text="Simulation mode").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(4, 6),
            pady=4,
        )
        self.mode_combobox = ttk.Combobox(
            settings_frame,
            textvariable=self.mode_var,
            values=("Synchronous", "Asynchronous"),
            state="disabled",
            width=15,
        )
        self.mode_combobox.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(0, 12),
            pady=4,
        )
        self.setting_widgets.append((self.mode_combobox, "readonly"))

        ttk.Label(settings_frame, text="Fixed Delta Seconds (0 = variable)").grid(
            row=0,
            column=2,
            sticky="w",
            padx=(4, 6),
            pady=4,
        )
        self.fixed_delta_entry = ttk.Entry(
            settings_frame,
            textvariable=self.fixed_delta_var,
            width=14,
        )
        self.fixed_delta_entry.grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(0, 12),
            pady=4,
        )
        self.setting_widgets.append((self.fixed_delta_entry, "normal"))

        self.no_rendering_checkbutton = ttk.Checkbutton(
            settings_frame,
            text="No Rendering Mode",
            variable=self.no_rendering_var,
        )
        self.no_rendering_checkbutton.grid(
            row=0,
            column=4,
            sticky="w",
            padx=(4, 12),
            pady=4,
        )
        self.setting_widgets.append((self.no_rendering_checkbutton, "normal"))

        self.advanced_button = ttk.Button(
            settings_frame,
            text="Advanced Settings ▸",
            command=self._toggle_advanced,
        )
        self.advanced_button.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            padx=4,
            pady=(4, 2),
        )

        self.advanced_frame = ttk.Frame(settings_frame)
        self.substepping_checkbutton = ttk.Checkbutton(
            self.advanced_frame,
            text="Enable Substepping",
            variable=self.substepping_var,
        )
        self.substepping_checkbutton.grid(
            row=0,
            column=0,
            sticky="w",
            padx=(4, 12),
            pady=4,
        )
        self.setting_widgets.append((self.substepping_checkbutton, "normal"))

        ttk.Label(self.advanced_frame, text="Max Substep Delta Time").grid(
            row=0,
            column=1,
            sticky="w",
            padx=(4, 6),
            pady=4,
        )
        self.max_substep_delta_entry = ttk.Entry(
            self.advanced_frame,
            textvariable=self.max_substep_delta_var,
            width=14,
        )
        self.max_substep_delta_entry.grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(0, 12),
            pady=4,
        )
        self.setting_widgets.append((self.max_substep_delta_entry, "normal"))

        ttk.Label(self.advanced_frame, text="Max Substeps").grid(
            row=0,
            column=3,
            sticky="w",
            padx=(4, 6),
            pady=4,
        )
        self.max_substeps_entry = ttk.Entry(
            self.advanced_frame,
            textvariable=self.max_substeps_var,
            width=10,
        )
        self.max_substeps_entry.grid(
            row=0,
            column=4,
            sticky="ew",
            padx=(0, 12),
            pady=4,
        )
        self.setting_widgets.append((self.max_substeps_entry, "normal"))

        self.apply_button = ttk.Button(
            settings_frame,
            text="Apply Settings",
            command=self._apply_settings,
        )
        self.apply_button.grid(
            row=2,
            column=4,
            sticky="e",
            padx=(4, 12),
            pady=(4, 2),
        )

        for column in range(5):
            settings_frame.grid_columnconfigure(column, weight=1)

        control_frame = ttk.LabelFrame(
            self,
            text="Tick Control",
            style="Section.TLabelframe",
            padding=(8, 6),
        )
        control_frame.grid(
            row=2,
            column=0,
            columnspan=6,
            sticky="ew",
            padx=8,
            pady=(0, 8),
        )

        ttk.Label(control_frame, text="Tick Controller").grid(
            row=0, column=0, sticky="w", padx=(4, 6), pady=4
        )
        self.tick_controller_combobox = ttk.Combobox(
            control_frame,
            textvariable=self.tick_controller_var,
            values=("GUI", "External"),
            state="disabled",
            width=12,
        )
        self.tick_controller_combobox.grid(
            row=0, column=1, sticky="w", padx=(0, 12), pady=4
        )
        self.tick_controller_combobox.bind(
            "<<ComboboxSelected>>",
            self._change_tick_controller,
        )

        ttk.Label(control_frame, text="Control status:").grid(
            row=0, column=2, sticky="e", padx=(4, 6), pady=4
        )
        ttk.Label(control_frame, textvariable=self.control_status_var).grid(
            row=0, column=3, sticky="w", padx=(0, 12), pady=4
        )
        ttk.Label(control_frame, textvariable=self.frame_status_var).grid(
            row=0, column=4, columnspan=2, sticky="e", padx=(4, 12), pady=4
        )

        self.run_button = ttk.Button(
            control_frame, text="Run", command=self.manager.run_simulation
        )
        self.run_button.grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        self.pause_button = ttk.Button(
            control_frame, text="Pause", command=self.manager.pause_simulation
        )
        self.pause_button.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        self.step_button = ttk.Button(
            control_frame, text="Step", command=self._step_once
        )
        self.step_button.grid(row=1, column=2, sticky="ew", padx=4, pady=4)

        ttk.Label(control_frame, text="Step Count").grid(
            row=1, column=3, sticky="e", padx=(4, 6), pady=4
        )
        self.step_count_entry = ttk.Entry(
            control_frame, textvariable=self.step_count_var, width=10
        )
        self.step_count_entry.grid(
            row=1, column=4, sticky="ew", padx=(0, 4), pady=4
        )
        self.step_n_button = ttk.Button(
            control_frame, text="Step N", command=self._step_many
        )
        self.step_n_button.grid(row=1, column=5, sticky="ew", padx=4, pady=4)

        ttk.Label(
            control_frame,
            text=(
                "GUI control is cooperative: another CARLA client may also "
                "issue ticks. Paused only stops ticks initiated by this GUI."
            ),
            wraplength=900,
        ).grid(
            row=2,
            column=0,
            columnspan=6,
            sticky="w",
            padx=4,
            pady=(4, 2),
        )
        for column in range(6):
            control_frame.grid_columnconfigure(column, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def _toggle_advanced(self):
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.grid(
                row=2,
                column=0,
                columnspan=5,
                sticky="ew",
                padx=0,
                pady=(0, 2),
            )
            self.apply_button.grid_configure(row=3)
            self.advanced_button.configure(text="Advanced Settings ▾")
        else:
            self.advanced_frame.grid_remove()
            self.apply_button.grid_configure(row=2)
            self.advanced_button.configure(text="Advanced Settings ▸")

    def _apply_settings(self):
        try:
            settings = self.get_proposed_settings()
        except ValueError as error:
            messagebox.showerror("Invalid Simulation Settings", str(error))
            return
        self.manager.apply_simulation_settings(settings)

    def _change_tick_controller(self, event=None):
        controller = self.tick_controller_var.get().lower()
        self.manager.set_tick_controller(controller)

    def _step_once(self):
        self.manager.step_simulation(1)

    def _step_many(self):
        try:
            count = int(self.step_count_var.get().strip())
            if count <= 0:
                raise ValueError
        except (tk.TclError, ValueError):
            messagebox.showerror(
                "Invalid Step Count",
                "Step Count must be a positive integer.",
            )
            return
        self.manager.step_simulation(count)

    def get_proposed_settings(self):
        mode = self.mode_var.get()
        if mode not in ("Synchronous", "Asynchronous"):
            raise ValueError("Select Synchronous or Asynchronous mode.")

        try:
            fixed_delta_text = self.fixed_delta_var.get().strip()
            fixed_delta_seconds = (
                float(fixed_delta_text) if fixed_delta_text else None
            )
            max_substep_delta_time = float(
                self.max_substep_delta_var.get().strip()
            )
            max_substeps = int(self.max_substeps_var.get().strip())
        except (tk.TclError, ValueError):
            raise ValueError("Simulation settings must contain valid numbers.")

        settings = {
            "synchronous_mode": mode == "Synchronous",
            "fixed_delta_seconds": fixed_delta_seconds,
            "no_rendering_mode": bool(self.no_rendering_var.get()),
            "substepping": bool(self.substepping_var.get()),
            "max_substep_delta_time": max_substep_delta_time,
            "max_substeps": max_substeps,
        }
        SimulationManager.validate_settings(settings)
        return settings

    def set_connected(self, connected, settings=None, control_state=None):
        self.connected = connected
        self.pending = False
        self.control_pending = False
        self.external_busy = False
        if connected and settings is not None:
            self.load_settings(settings, "Settings loaded")
        elif connected:
            self.status_var.set("Loading settings...")
        else:
            self.status_var.set("Disconnected")
            self._clear_settings()
        if connected and control_state is not None:
            self.update_control_state(control_state)
        elif not connected:
            self.control_state = SimulationManager.DISCONNECTED
            self.control_synchronous_mode = False
            self.control_status_var.set("Disconnected")
            self.frame_status_var.set("Last observed frame: -")
            self.tick_controller_var.set("External")
        self._update_widget_states()

    def set_external_busy(self, busy):
        self.external_busy = busy
        self._update_widget_states()

    def set_pending(self):
        self.pending = True
        self.status_var.set("Applying settings...")
        self._update_widget_states()

    def set_control_pending(self, status):
        self.control_pending = True
        self.control_status_var.set(status)
        self._update_widget_states()

    def update_control_state(self, control_state):
        self.control_pending = False
        self.control_state = control_state["state"]
        self.control_synchronous_mode = control_state["synchronous_mode"]
        controller = control_state["tick_controller"]
        self.tick_controller_var.set(
            "GUI" if controller == SimulationManager.GUI_CONTROLLER else "External"
        )

        status = self.control_state
        remaining_steps = control_state.get("remaining_steps", 0)
        if self.control_state == SimulationManager.SYNC_GUI_STEPPING:
            status = f"{status} ({remaining_steps} remaining)"
        self.control_status_var.set(status)

        last_frame = control_state.get("last_frame")
        self.frame_status_var.set(
            "Last observed frame: -"
            if last_frame is None
            else f"Last observed frame: {last_frame}"
        )

        error = control_state.get("error")
        if error and error != self.last_control_error:
            self.last_control_error = error
            messagebox.showerror(
                "Simulation Tick Error",
                f"The local tick scheduler stopped:\n{error}",
            )
        elif not error:
            self.last_control_error = None
        self._update_widget_states()

    def update_control_observation(self, control_state):
        if self.control_pending or control_state["state"] != self.control_state:
            return
        status = self.control_state
        if self.control_state == SimulationManager.SYNC_GUI_STEPPING:
            status = (
                f"{status} ({control_state.get('remaining_steps', 0)} "
                "remaining)"
            )
        self.control_status_var.set(status)
        last_frame = control_state.get("last_frame")
        self.frame_status_var.set(
            "Last observed frame: -"
            if last_frame is None
            else f"Last observed frame: {last_frame}"
        )

    def show_control_error(self, message):
        self.control_pending = False
        self._update_widget_states()
        messagebox.showerror("Simulation Control Error", message)

    def load_settings(self, settings, status="Settings loaded"):
        self.mode_var.set(
            "Synchronous" if settings["synchronous_mode"] else "Asynchronous"
        )
        fixed_delta_seconds = settings["fixed_delta_seconds"]
        self.fixed_delta_var.set(
            "" if fixed_delta_seconds is None else str(fixed_delta_seconds)
        )
        self.no_rendering_var.set(settings["no_rendering_mode"])
        self.substepping_var.set(settings["substepping"])
        self.max_substep_delta_var.set(settings["max_substep_delta_time"])
        self.max_substeps_var.set(settings["max_substeps"])
        self.pending = False
        self.status_var.set(status)
        self._update_widget_states()

    def show_error(self, message, settings=None):
        if settings is not None:
            self.load_settings(settings, "Apply failed; server values restored")
        else:
            self.pending = False
            self.status_var.set("Settings error")
            self._update_widget_states()
        messagebox.showerror(
            "Simulation Settings Error",
            f"Could not apply or read simulation settings:\n{message}",
        )

    def _clear_settings(self):
        self.mode_var.set("")
        self.fixed_delta_var.set("")
        self.no_rendering_var.set(False)
        self.substepping_var.set(False)
        self.max_substep_delta_var.set("")
        self.max_substeps_var.set("")

    def _update_widget_states(self):
        enabled = self.connected and not self.pending and not self.external_busy
        for widget, enabled_state in self.setting_widgets:
            widget.configure(state=enabled_state if enabled else "disabled")
        self.advanced_button.configure(state="normal" if enabled else "disabled")
        self.apply_button.configure(state="normal" if enabled else "disabled")

        controls_enabled = enabled and not self.control_pending
        controller_state = (
            "readonly"
            if controls_enabled and self.control_synchronous_mode
            else "disabled"
        )
        self.tick_controller_combobox.configure(state=controller_state)

        paused = self.control_state == SimulationManager.SYNC_GUI_PAUSED
        running = self.control_state == SimulationManager.SYNC_GUI_RUNNING
        stepping = self.control_state == SimulationManager.SYNC_GUI_STEPPING
        self.run_button.configure(
            state="normal" if controls_enabled and paused else "disabled"
        )
        self.pause_button.configure(
            state=(
                "normal"
                if controls_enabled and (running or stepping)
                else "disabled"
            ),
            text="Cancel Step N" if stepping else "Pause",
        )
        step_state = "normal" if controls_enabled and paused else "disabled"
        self.step_button.configure(state=step_state)
        self.step_count_entry.configure(state=step_state)
        self.step_n_button.configure(state=step_state)


class SpectatorFrame(ttk.Frame):
    def __init__(self, parent, manager):
        super().__init__(parent)
        self.manager = manager
        self.status_var = tk.StringVar(value="Closed")
        self.resolution_var = tk.StringVar(value="1280x720")
        self.fov_var = tk.DoubleVar(value=90.0)
        self.fps_var = tk.IntVar(value=60)
        self.move_speed_var = tk.DoubleVar(value=10.0)
        self.mouse_sensitivity_var = tk.DoubleVar(value=0.12)
        self.setting_widgets = []

        self._create_widgets()

    def _create_widgets(self):
        ttk.Label(
            self,
            text="Spectator View"
        ).pack(pady=10)

        status_frame = ttk.Frame(self)
        status_frame.pack(pady=10, padx=10, fill="x")
        ttk.Label(status_frame, text="Status:").pack(side="left", padx=(0, 6))
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left")

        settings_frame = ttk.LabelFrame(
            self,
            text="View Settings",
            style="Section.TLabelframe",
            padding=(8, 6)
        )
        settings_frame.pack(fill="x", padx=8, pady=(0, 8))

        settings = (
            ("Resolution", self.resolution_var, ("640x480", "1280x720", "1920x1080")),
            ("FOV", self.fov_var, (20.0, 120.0, 1.0)),
            ("FPS limit", self.fps_var, (10, 120, 5)),
            ("Move speed", self.move_speed_var, (0.5, 100.0, 0.5)),
            ("Mouse sensitivity", self.mouse_sensitivity_var, (0.01, 1.0, 0.01))
        )
        for index, (label, variable, values) in enumerate(settings):
            row = index // 2
            column = (index % 2) * 2
            ttk.Label(settings_frame, text=label).grid(
                row=row,
                column=column,
                sticky="w",
                padx=(4, 6),
                pady=4
            )
            if isinstance(values[0], str):
                widget = ttk.Combobox(
                    settings_frame,
                    textvariable=variable,
                    values=values,
                    state="readonly",
                    width=13
                )
                enabled_state = "readonly"
            else:
                widget = ttk.Spinbox(
                    settings_frame,
                    textvariable=variable,
                    from_=values[0],
                    to=values[1],
                    increment=values[2],
                    width=11
                )
                enabled_state = "normal"
            widget.grid(
                row=row,
                column=column + 1,
                sticky="ew",
                padx=(0, 8),
                pady=4
            )
            self.setting_widgets.append((widget, enabled_state))
            settings_frame.grid_columnconfigure(column + 1, weight=1)

        self.open_button = ttk.Button(
            self,
            text="Open Spectator",
            command=self.manager.open_spectator_preview,
            state="disabled"
        )
        self.open_button.pack(pady=5, padx=5, side="left")

        self.close_button = ttk.Button(
            self,
            text="Close Spectator",
            command=self.manager.close_spectator_preview,
            state="disabled"
        )
        self.close_button.pack(pady=5, padx=5, side="left")

    def update_state(self, state, connected):
        self.status_var.set(state)

        can_open = connected and state in ("Closed", "Error")
        can_close = connected and state in (
            "Starting",
            "Running"
        )
        self.open_button.configure(state="normal" if can_open else "disabled")
        self.close_button.configure(state="normal" if can_close else "disabled")

        settings_enabled = state in ("Closed", "Error")
        for widget, enabled_state in self.setting_widgets:
            widget.configure(
                state=enabled_state if settings_enabled else "disabled"
            )

    def get_preview_config(self):
        try:
            width, height = (
                int(value) for value in self.resolution_var.get().split("x")
            )
            fov = float(self.fov_var.get())
            fps = int(self.fps_var.get())
            move_speed = float(self.move_speed_var.get())
            mouse_sensitivity = float(self.mouse_sensitivity_var.get())
        except (tk.TclError, ValueError):
            raise ValueError("Spectator View settings must contain valid numbers.")

        if (width, height) not in ((640, 480), (1280, 720), (1920, 1080)):
            raise ValueError("Select one of the available resolutions.")
        if not 20.0 <= fov <= 120.0:
            raise ValueError("FOV must be between 20 and 120 degrees.")
        if not 10 <= fps <= 120:
            raise ValueError("FPS limit must be between 10 and 120.")
        if not 0.5 <= move_speed <= 100.0:
            raise ValueError("Move speed must be between 0.5 and 100 m/s.")
        if not 0.01 <= mouse_sensitivity <= 1.0:
            raise ValueError("Mouse sensitivity must be between 0.01 and 1.0.")

        return {
            "width": width,
            "height": height,
            "fov": fov,
            "fps": fps,
            "move_speed": move_speed,
            "mouse_sensitivity": mouse_sensitivity
        }
