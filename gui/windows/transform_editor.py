import tkinter as tk
from typing import List, Any

import carla
from tkinter import ttk
from utilities.functions import return_data_from_transform

class ChangeTransformTop(tk.Toplevel):
    def __init__(self, parent, available_spawn_points, current_transform, on_transform_change_callback):
        super().__init__(parent)
        self.title("Change Transform")
        self.geometry("420x470")
        self.resizable(False, True)
        self.available_spawn_points = available_spawn_points
        self.current_transform = current_transform
        self.transform_change_callback = on_transform_change_callback
        self.default_color = "#F7E7D3"
        self.panel_color = "#F1D0A3"
        self.accent_color = "#C96D3C"
        self.text_color = "#4A2E1D"
        self.subtle_color = "#E7B36C"
        self.configure(bg=self.default_color)
        self.selected_spawn_point = tk.StringVar()
        self.location_x = tk.DoubleVar(value=current_transform.location.x)
        self.location_y = tk.DoubleVar(value=current_transform.location.y)
        self.location_z = tk.DoubleVar(value=current_transform.location.z)
        self.rotation_yaw = tk.DoubleVar(value=current_transform.rotation.yaw)
        self.rotation_pitch = tk.DoubleVar(value=current_transform.rotation.pitch)
        self.rotation_roll = tk.DoubleVar(value=current_transform.rotation.roll)

        self._create_widgets()

    def _create_widgets(self):
        heading_frame = ttk.Frame(self)
        spawn_points_frame = ttk.Frame(self)
        location_rotation_frame = ttk.Frame(self)
        button_frame = ttk.Frame(self)

        heading_frame.pack(side=tk.TOP)
        spawn_points_frame.pack(side=tk.TOP)
        location_rotation_frame.pack(side=tk.TOP)
        button_frame.pack(side=tk.BOTTOM)

        ttk.Label(heading_frame, text="Transform Edit", font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 5))

        spawn_point_label = ttk.Label(spawn_points_frame, text="Spawn Point", background=self.default_color,
                                      font=("Arial", 10, "bold"))
        spawn_point_label.grid(row=0, column=0, columnspan=2, sticky="w", pady=(14, 6))

        self.available_spawn_points_str = [f"{i}: {point}" for i, point in enumerate(self.available_spawn_points)]

        self.combobox_spawn_points = ttk.Combobox(spawn_points_frame, textvariable=self.selected_spawn_point,
                                                  state="readonly")
        self.combobox_spawn_points["values"] = self.available_spawn_points_str
        self.combobox_spawn_points.current(0)
        self.combobox_spawn_points.bind("<<ComboboxSelected>>", self._update_location_from_spawn_point)
        self.combobox_spawn_points.grid(row=1, column=0, columnspan=2, sticky="ew")

        position_frame = ttk.Frame(location_rotation_frame, style="new.TFrame")
        position_frame.grid(row=0, column=0, sticky="nw")

        position_label = ttk.Label(position_frame, text="Position", background=self.default_color,
                                   font=("Arial", 18))
        position_label.grid(row=0, column=0, sticky="nw", pady=20)

        x_label = ttk.Label(position_frame, text="X - position", background=self.default_color)
        x_label.grid(row=1, column=0, sticky="nw")
        x_spinbox = tk.Spinbox(position_frame, from_=-1e9, to=1e9, increment=0.01,
                               textvariable=self.location_x)
        x_spinbox.grid(row=2, column=0)

        y_label = ttk.Label(position_frame, text="Y - position", background=self.default_color)
        y_label.grid(row=3, column=0, sticky="nw")
        y_spinbox = tk.Spinbox(position_frame, from_=-1e9, to=1e9, increment=0.01,
                               textvariable=self.location_y)
        y_spinbox.grid(row=4, column=0)

        z_label = ttk.Label(position_frame, text="Z - position", background=self.default_color)
        z_label.grid(row=5, column=0, sticky="nw")
        z_spinbox = tk.Spinbox(position_frame, from_=-1e9, to=1e9, increment=0.01,
                               textvariable=self.location_z)
        z_spinbox.grid(row=6, column=0)

        rotation_frame = ttk.Frame(location_rotation_frame, style="new.TFrame")
        rotation_frame.grid(row=0, column=1, sticky="nw", padx=(20, 0))

        rotation_label = ttk.Label(rotation_frame, text="Rotation", background=self.default_color,
                                   font=("Arial", 18))
        rotation_label.grid(row=0, column=0, pady=20)

        yaw_label = ttk.Label(rotation_frame, text="Yaw", background=self.default_color)
        yaw_label.grid(row=1, column=0, sticky="nw")
        yaw_spinbox = tk.Spinbox(rotation_frame, from_=-1e9, to=1e9, increment=0.01,
                                 textvariable=self.rotation_yaw)
        yaw_spinbox.grid(row=2, column=0)

        pitch_label = ttk.Label(rotation_frame, text="Pitch", background=self.default_color)
        pitch_label.grid(row=3, column=0, sticky="nw")
        pitch_spinbox = tk.Spinbox(rotation_frame, from_=-1e9, to=1e9, increment=0.01,
                                   textvariable=self.rotation_pitch)
        pitch_spinbox.grid(row=4, column=0)

        roll_label = ttk.Label(rotation_frame, text="Roll", background=self.default_color)
        roll_label.grid(row=5, column=0, sticky="nw")
        roll_spinbox = tk.Spinbox(rotation_frame, from_=-1e9, to=1e9, increment=0.01,
                                  textvariable=self.rotation_roll)
        roll_spinbox.grid(row=6, column=0)

        change_button = ttk.Button(button_frame, text="Change transform", command=self.change_transform)
        change_button.pack(side="left", padx=(0, 5))
        cancel_button = ttk.Button(button_frame, text="Cancel", command=self.destroy)
        cancel_button.pack(side="left", padx=(5, 0))

    def _update_location_from_spawn_point(self, event):
        spawn_point_num = int(str(self.selected_spawn_point.get()).split(":")[0])
        available_spawn_points = self.available_spawn_points
        for i, spawn_point in enumerate(available_spawn_points):
            if i == spawn_point_num:
                transform = spawn_point
                break
            else:
                transform = carla.Transform(carla.Location(x=0, y=0, z=0),carla.Rotation(pitch=0, roll=0, yaw=0))

        x, y, z, yaw, pitch, roll = return_data_from_transform(transform)

        self.location_x.set(x)
        self.location_y.set(y)
        self.location_z.set(z)
        self.rotation_yaw.set(yaw)
        self.rotation_pitch.set(pitch)
        self.rotation_roll.set(roll)

    def change_transform(self):
        new_transform = carla.Transform(
            carla.Location(
                x=self.location_x.get(),
                y=self.location_y.get(),
                z=self.location_z.get(),
            ),
            carla.Rotation(
                pitch=self.rotation_pitch.get(),
                yaw=self.rotation_yaw.get(),
                roll=self.rotation_roll.get()
            )
        )
        self.transform_change_callback(new_transform)
        self.destroy()
