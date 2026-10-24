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