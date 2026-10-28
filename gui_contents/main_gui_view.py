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

        self.connection_frame = ConnectionFrame(self, self.controller)
        self.connection_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nw")

        self.spectator_frame = SpectatorFrame(self, self.controller)
        self.spectator_frame.grid(row=0, column=1, padx=5, pady=5, sticky="nw")


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

    def update_connection_state(self, connected):
        state = "disabled" if connected else "normal"

        self.connect_button.configure(state=state)
        self.disconnect_button.configure(
            state="normal" if connected else "disabled"
        )

        self.host_entry.configure(state=state)
        self.port_entry.configure(state=state)


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
