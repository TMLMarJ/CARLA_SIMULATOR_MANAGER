import time
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
from utilities.functions import filter_carla_id_by_type, return_data_from_transform, wait_and_try
from gui_contents.vehicle_spawn_preview import VehiclePreviewFrame, VehiclePreview
import messagebox
import carla
import numpy as np


class CustomVehicleFrame(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.database = controller.database
        self.carla = controller.carla

        self.default_color = "#D2F7FA"
        style = ttk.Style()
        style.configure("TFrame", background=self.default_color)
        self.vehicle_preview = VehiclePreview(self.database.world)

        # Vehicle Tab
        self.vehicle_control_frame = ttk.Frame(self, style="new.TFrame")
        self.vehicle_control_frame.pack(expand=1, fill="both")
        self._vehicle_variables_definition()
        self._vehicle_frame_setup()

    def spawn_ghost(self):
        self.vehicle_preview.spawn()
        self.after(500, self.image_frame.update_image)

    def _vehicle_variables_definition(self):
        self.selected_vehicle = tk.StringVar()
        self.selected_spawnpoint = {
            "x": 0,
            "y": 0,
            "z": 0,
            "yaw": 0,
            "pitch": 0,
            "roll": 0,
        }
        self.x_position_var = tk.DoubleVar()
        self.y_position_var = tk.DoubleVar()
        self.z_position_var = tk.DoubleVar()
        self.yaw_position_var = tk.DoubleVar()
        self.pitch_position_var = tk.DoubleVar()
        self.roll_position_var = tk.DoubleVar()
        self.selected_spawn_point = tk.StringVar()

        self.choose_class = tk.StringVar()
        self.choose_model = tk.StringVar()
        self.actor_model = {}

    def _vehicle_frame_setup(self):
        vehicle_classes = ["Car", "Bus", "Van", "Truck", "Motorcycle", "Bicycle"]
        upper_frame = ttk.Frame(self.vehicle_control_frame, style="new.TFrame")
        lower_frame = ttk.Frame(self.vehicle_control_frame, style="new.TFrame")
        upper_frame.pack(side="top", fill="x")
        lower_frame.pack(side="bottom", fill="x")

        heading_frame = ttk.Frame(upper_frame, style="new.TFrame")
        heading_frame.grid(row=0, column=0, sticky="nw")

        self.image_frame = VehiclePreviewFrame(upper_frame, self.vehicle_preview)
        self.image_frame.grid(row=0, column=1, sticky="ne", padx=20)

        location_frame = ttk.Frame(upper_frame, style="new.TFrame")
        location_frame.grid(row=1, column=0, sticky="nw", pady=20)

        button_frame = ttk.Frame(upper_frame, style="new.TFrame")
        button_frame.grid(row=1, column=1, sticky="ne",padx=20, pady=20)

        # ================================= Heading ======================================
        heading_label = tk.Label(heading_frame, text="Vehicles menu", bg=self.default_color,
                           font=("Arial", 18))
        heading_label.grid(row=0, column=0)

        # Actor class selection
        actor_class_combobox = ttk.Combobox(heading_frame, width=27, textvariable=self.choose_class)
        actor_class_combobox["values"] = vehicle_classes
        actor_class_combobox.current(0)
        actor_class_combobox.bind("<<ComboboxSelected>>", self._update_model)

        # Actor selection by class
        self.actor_model = filter_carla_id_by_type("vehicle", self.choose_class.get().lower(),
                                                         self.database.blueprint_lib)
        self.actor_model_combobox = ttk.Combobox(heading_frame, width=27, textvariable=self.choose_model)
        self.actor_model_combobox["values"] = list(self.actor_model.keys())
        self.actor_model_combobox.current(0)
        self.actor_model_combobox.bind("<<ComboboxSelected>>", self._on_model_change)

        actor_class_combobox.grid(row=1, column=0)
        self.actor_model_combobox.grid(row=2, column=0)

        self.available_spawn_points = self.database.world.get_map().get_spawn_points()
        self.available_spawn_points = [f"{i}:{point}" for i, point in enumerate(self.available_spawn_points)]
        spawn_point_label = ttk.Label(heading_frame, text="Spawn Points", background=self.default_color,
                                      font=("Arial", 16))
        spawn_point_label.grid(row=3, column=0, pady=10)

        self.combobox_spawn_points = ttk.Combobox(heading_frame, width=27, textvariable=self.selected_spawn_point)
        self.combobox_spawn_points["values"] = self.available_spawn_points
        self.combobox_spawn_points.current(0)
        self.combobox_spawn_points.bind("<<ComboboxSelected>>", self._update_location_from_spawn_point)
        self.combobox_spawn_points.grid(row=4, column=0)

        #==================================== Location ===================================
        position_frame = ttk.Frame(location_frame, style="new.TFrame")
        position_frame.grid(row=0, column=0, sticky="nw")

        position_label = ttk.Label(position_frame, text="Position", background=self.default_color,
                                   font=("Arial", 18))
        position_label.grid(row=0, column=0, sticky="nw", pady=20)

        x_label = ttk.Label(position_frame, text="X - position", background=self.default_color)
        x_label.grid(row=1, column=0, sticky="nw")
        x_spinbox = tk.Spinbox(position_frame, from_=-1e9, to=1e9, increment=0.01,
                                        textvariable=self.x_position_var)
        x_spinbox.grid(row=2, column=0)

        y_label = ttk.Label(position_frame, text="Y - position", background=self.default_color)
        y_label.grid(row=3, column=0, sticky="nw")
        y_spinbox = tk.Spinbox(position_frame, from_=1e9, to=1e9, increment=0.01,
                               textvariable=self.y_position_var)
        y_spinbox.grid(row=4, column=0)

        z_label = ttk.Label(position_frame, text="Z - position", background=self.default_color)
        z_label.grid(row=5, column=0, sticky="nw")
        z_spinbox = tk.Spinbox(position_frame, from_=1e9, to=1e9, increment=0.01,
                               textvariable=self.z_position_var)
        z_spinbox.grid(row=6, column=0)

        rotation_frame = ttk.Frame(location_frame, style="new.TFrame")
        rotation_frame.grid(row=0, column=1, sticky="nw", padx=(20, 0))

        rotation_label = ttk.Label(rotation_frame, text="Rotation", background=self.default_color,
                                   font=("Arial", 18))
        rotation_label.grid(row=0, column=0, pady=20)

        yaw_label = ttk.Label(rotation_frame, text="Yaw", background=self.default_color)
        yaw_label.grid(row=1, column=0, sticky="nw")
        yaw_spinbox = tk.Spinbox(rotation_frame, from_=1e9, to=1e9, increment=0.01,
                                 textvariable=self.yaw_position_var)
        yaw_spinbox.grid(row=2, column=0)

        pitch_label = ttk.Label(rotation_frame, text="Pitch", background=self.default_color)
        pitch_label.grid(row=3, column=0, sticky="nw")
        pitch_spinbox = tk.Spinbox(rotation_frame, from_=1e9, to=1e9, increment=0.01,
                                   textvariable=self.pitch_position_var)
        pitch_spinbox.grid(row=4, column=0)

        roll_label = ttk.Label(rotation_frame, text="Roll", background=self.default_color)
        roll_label.grid(row=5, column=0, sticky="nw")
        roll_spinbox = tk.Spinbox(rotation_frame, from_=1e9, to=1e9,increment=0.01,
                                  textvariable=self.roll_position_var)
        roll_spinbox.grid(row=6, column=0)

    def _update_model(self, event):
        self.actor_model = filter_carla_id_by_type("vehicle", self.choose_class.get().lower(),
                                                         self.database.blueprint_lib)
        self.actor_model_combobox["values"] = list(self.actor_model.keys())
        self.actor_model_combobox.current(0)
        self.image_frame.update_image()

    def _update_location_from_spawn_point(self, event):
        spawn_point_num = int(str(self.selected_spawn_point.get()).split(":")[0])
        available_spawn_points = self.database.world.get_map().get_spawn_points()
        for i, spawn_point in enumerate(available_spawn_points):
            if i == spawn_point_num:
                transform = spawn_point
                break
            else:
                transform = carla.Transform(carla.Location(x=0, y=0, z=0),carla.Rotation(pitch=0, roll=0, yaw=0))

        x, y, z, yaw, pitch, roll = return_data_from_transform(transform)

        self.x_position_var.set(x)
        self.y_position_var.set(y)
        self.z_position_var.set(z)
        self.yaw_position_var.set(yaw)
        self.pitch_position_var.set(pitch)
        self.roll_position_var.set(roll)

        selected_model = self.actor_model[self.actor_model_combobox.get()]
        self.vehicle_preview.destroy()
        self.vehicle_preview.spawn(blueprint_id=selected_model, transform=transform)
        self.after(500, self.image_frame.update_image)

    def update_spawn_point(self):
        self.combobox_spawn_points["values"] = [f"{i}:{point}" for i, point in enumerate(self.database.world.get_map().get_spawn_points())]
        self.combobox_spawn_points.current(0)

    def destroy_ghost(self):
        if self.vehicle_preview:
            self.vehicle_preview.destroy()

    def _on_model_change(self, event):
        selected_model = self.actor_model[self.actor_model_combobox.get()]
        if self.vehicle_preview:
            transform = self.vehicle_preview.get_transform()
            self.vehicle_preview.destroy()
            self.vehicle_preview.spawn(blueprint_id=selected_model, transform=transform)
            self.after(500, self.image_frame.update_image)

