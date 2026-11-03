import time
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
from pygments.lexers import blueprint

from utilities.functions import filter_carla_id_by_type, return_data_from_transform, wait_and_try
from gui_contents.spawn_preview import VehiclePreviewFrame, GhostPreview
from gui_contents.ActorEditView import EntityEditWindow
from gui_contents.change_model_top import ChangeModelTop
from gui_contents.set_transform_top import ChangeTransformTop
import messagebox
import carla
import numpy as np


class CustomVehicleFrame(tk.Frame):
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

        self.vehicle_preview = GhostPreview(self.database, controller)

        self._vehicle_variables_definition()
        self._vehicle_frame_setup()

    def spawn_ghost(self, blueprint_id="vehicle.audi.a2", transform=None):
        self.vehicle_preview.spawn(blueprint_id=blueprint_id, transform=transform)

    def _vehicle_variables_definition(self):
        self.vehicle_name_var = tk.StringVar(value="Ghost Vehicle")
        self.selected_vehicle = tk.StringVar()
        self.selected_spawnpoint = {
            "x": 0,
            "y": 0,
            "z": 0,
            "yaw": 0,
            "pitch": 0,
            "roll": 0,
        }
        self.x_position_var = tk.DoubleVar(value=self.database.available_spawn_points[0].location.x if self.database.available_spawn_points else 0)
        self.y_position_var = tk.DoubleVar(value=self.database.available_spawn_points[0].location.y if self.database.available_spawn_points else 0)
        self.z_position_var = tk.DoubleVar(value=self.database.available_spawn_points[0].location.z if self.database.available_spawn_points else 0)
        self.yaw_position_var = tk.DoubleVar(value=self.database.available_spawn_points[0].rotation.yaw if self.database.available_spawn_points else 0)
        self.pitch_position_var = tk.DoubleVar(value=self.database.available_spawn_points[0].rotation.pitch if self.database.available_spawn_points else 0)
        self.roll_position_var = tk.DoubleVar(value=self.database.available_spawn_points[0].rotation.roll if self.database.available_spawn_points else 0)
        self.selected_spawn_point = tk.StringVar()

        self.choose_class = tk.StringVar()
        self.choose_model = tk.StringVar()
        self.actor_model = {}

    def _vehicle_frame_setup(self):
        vehicle_classes = ["Car", "Bus", "Van", "Truck", "Motorcycle", "Bicycle"]
        upper_frame = ttk.LabelFrame(
            self,
            text="Spawn Vehicles",
            style="Section.TLabelframe",
            padding=(12, 8)
        )
        lower_frame = ttk.LabelFrame(
            self,
            text="Vehicle Management",
            style="Section.TLabelframe",
            padding=(12, 8)
        )
        upper_frame.pack(side="top", fill="x", padx=18, pady=(6, 12))
        lower_frame.pack(side="bottom", fill="both", expand=True, padx=18, pady=(6, 12))

        upper_frame.grid_columnconfigure(0, weight=1)
        upper_frame.grid_columnconfigure(1, weight=1)
        upper_frame.grid_columnconfigure(2, weight=1)
        lower_frame.grid_columnconfigure(0, weight=1)
        lower_frame.grid_rowconfigure(0, weight=1)
        lower_frame.grid_rowconfigure(1, weight=0)

        heading_frame = ttk.Frame(upper_frame, style="new.TFrame")
        heading_frame.grid(row=0, column=0, sticky="nw", padx=(12, 8), pady=(8, 8))

        self.image_frame = VehiclePreviewFrame(upper_frame, self.vehicle_preview)
        self.image_frame.grid(row=0, column=2, sticky="n", padx=(8, 12), pady=(8, 8))

        location_frame = ttk.Frame(upper_frame, style="new.TFrame")
        location_frame.grid(row=0, column=1, sticky="ew", padx=8, pady=(8, 8))

        button_frame = ttk.Frame(upper_frame, style="new.TFrame")
        button_frame.grid(row=1, column=0, columnspan=3, sticky="n", padx=(0, 12), pady=(0, 8))


        # ================================= Heading ======================================

        heading_frame.grid_columnconfigure(0, weight=1)
        heading_frame.grid_columnconfigure(1, weight=1)

        heading_label = tk.Label(heading_frame, text="Vehicles menu", bg=self.default_color, fg=self.text_color, font=("Arial", 18, "bold"))
        heading_label.grid( row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        # ================================= Vehicle name =================================

        vehicle_name_label = ttk.Label(heading_frame, text="Vehicle Name", background=self.default_color, font=("Arial", 14, "bold"))
        vehicle_name_label.grid( row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        vehicle_name_entry = ttk.Entry(heading_frame, textvariable=self.vehicle_name_var)
        vehicle_name_entry.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 14))

        # ================================= Class + Model =================================

        class_frame = ttk.Frame(heading_frame, style="new.TFrame")
        class_frame.grid(row=3, column=0, sticky="ew", padx=(0, 6))

        model_frame = ttk.Frame(heading_frame, style="new.TFrame")
        model_frame.grid(row=3, column=1, sticky="ew", padx=(6, 0))

        class_frame.grid_columnconfigure(0, weight=1)
        model_frame.grid_columnconfigure(0, weight=1)

        # Class
        class_label = ttk.Label(class_frame, text="Class", background=self.default_color, font=("Arial", 11, "bold"))
        class_label.grid(row=0, column=0, sticky="w", pady=(0, 5))

        actor_class_combobox = ttk.Combobox(class_frame, textvariable=self.choose_class, state="readonly")
        actor_class_combobox["values"] = vehicle_classes
        actor_class_combobox.current(0)
        actor_class_combobox.bind("<<ComboboxSelected>>", self._update_model)
        actor_class_combobox.grid(row=1, column=0, sticky="ew")

        # Model
        model_label = ttk.Label(model_frame, text="Model", background=self.default_color, font=("Arial", 11, "bold"))
        model_label.grid(row=0, column=0, sticky="w", pady=(0, 5))

        self.actor_model = filter_carla_id_by_type("vehicle", self.choose_class.get().lower(), self.database.blueprint_lib)

        self.actor_model_combobox = ttk.Combobox(model_frame, textvariable=self.choose_model, state="readonly")
        self.actor_model_combobox["values"] = list(self.actor_model.keys())
        self.actor_model_combobox.current(0)
        self.actor_model_combobox.bind("<<ComboboxSelected>>", self._on_model_change)
        self.actor_model_combobox.grid(row=1, column=0, sticky="ew")

        # ================================= Spawn point =================================

        spawn_point_label = ttk.Label(heading_frame, text="Spawn Point", background=self.default_color, font=("Arial", 14, "bold"))
        spawn_point_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(14, 6))

        self.available_spawn_points = [f"{i}: {point}" for i, point in enumerate(self.database.available_spawn_points)]

        self.combobox_spawn_points = ttk.Combobox(heading_frame, textvariable=self.selected_spawn_point, state="readonly")
        self.combobox_spawn_points["values"] = self.available_spawn_points
        self.combobox_spawn_points.current(0)
        self.combobox_spawn_points.bind("<<ComboboxSelected>>", self._update_location_from_spawn_point)
        self.combobox_spawn_points.grid(row=5, column=0, columnspan=2, sticky="ew")

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
        y_spinbox = tk.Spinbox(position_frame, from_=-1e9, to=1e9, increment=0.01,
                               textvariable=self.y_position_var)
        y_spinbox.grid(row=4, column=0)

        z_label = ttk.Label(position_frame, text="Z - position", background=self.default_color)
        z_label.grid(row=5, column=0, sticky="nw")
        z_spinbox = tk.Spinbox(position_frame, from_=-1e9, to=1e9, increment=0.01,
                               textvariable=self.z_position_var)
        z_spinbox.grid(row=6, column=0)

        rotation_frame = ttk.Frame(location_frame, style="new.TFrame")
        rotation_frame.grid(row=0, column=1, sticky="nw", padx=(20, 0))

        rotation_label = ttk.Label(rotation_frame, text="Rotation", background=self.default_color,
                                   font=("Arial", 18))
        rotation_label.grid(row=0, column=0, pady=20)

        yaw_label = ttk.Label(rotation_frame, text="Yaw", background=self.default_color)
        yaw_label.grid(row=1, column=0, sticky="nw")
        yaw_spinbox = tk.Spinbox(rotation_frame, from_=-1e9, to=1e9, increment=0.01,
                                 textvariable=self.yaw_position_var)
        yaw_spinbox.grid(row=2, column=0)

        pitch_label = ttk.Label(rotation_frame, text="Pitch", background=self.default_color)
        pitch_label.grid(row=3, column=0, sticky="nw")
        pitch_spinbox = tk.Spinbox(rotation_frame, from_=-1e9, to=1e9, increment=0.01,
                                   textvariable=self.pitch_position_var)
        pitch_spinbox.grid(row=4, column=0)

        roll_label = ttk.Label(rotation_frame, text="Roll", background=self.default_color)
        roll_label.grid(row=5, column=0, sticky="nw")
        roll_spinbox = tk.Spinbox(rotation_frame, from_=-1e9, to=1e9,increment=0.01,
                                  textvariable=self.roll_position_var)
        roll_spinbox.grid(row=6, column=0)

        # ==================================== Table ===================================

        self.vehicle_table = VehicleTable(lower_frame, self.controller)
        self.vehicle_table.grid(row=0, column=0, sticky="nsew", padx=20, pady=(10, 10))

        button_control_frame = ttk.Frame(lower_frame, style="new.TFrame")
        button_control_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 12))
        for index in range(5):
            button_control_frame.grid_columnconfigure(index, weight=1)

        #==================================== Buttons ===================================
        spawn_button = ttk.Button(button_frame, text="Spawn Vehicle", command=self.spawn_vehicle, width=18)
        spawn_button.grid(row=0, column=0, sticky="ew", pady=(0, 8), padx=(0, 10))
        physics_button = ttk.Button(button_frame, text="Edit", command=self.edit_vehicle, width=18)
        physics_button.grid(row=0, column=1, sticky="ew", pady=(0, 8), padx=(0, 10))
        remove_button = ttk.Button(button_control_frame, text="Remove", command=self.remove_vehicle, width=18)
        remove_button.grid(row=0, column=0, sticky="ew", pady=(0, 8), padx=(0, 10))
        change_model_button = ttk.Button(button_control_frame, text="Change Model", command=self.change_model, width=18)
        change_model_button.grid(row=0, column=1, sticky="ew", pady=(0, 8), padx=(0, 10))
        update_location_button = ttk.Button(button_control_frame, text="Update Location", command=self.update_location, width=18)
        update_location_button.grid(row=0, column=2, sticky="ew", pady=(0, 8), padx=(0, 10))
        copy_button = ttk.Button(button_control_frame, text="Copy Vehicle", command=self.copy_vehicle, width=18)
        copy_button.grid(row=0, column=3, sticky="ew", pady=(0, 8), padx=(0, 10))
        movement_button = ttk.Button(
            button_control_frame,
            text="Movement Control",
            command=self.open_movement_control,
            width=18,
        )
        movement_button.grid(row=0, column=4, sticky="ew", pady=(0, 8))

    def open_movement_control(self):
        selected_vehicle = self.vehicle_table.tree.selection()
        if not selected_vehicle:
            return
        vehicle_id = self.vehicle_table.tree.item(
            selected_vehicle[0]
        )["values"][1]
        self.controller.open_movement_control(vehicle_id)

    def update_location(self, event=None):
        if not self.vehicle_table.tree.selection():
            return
        selected_vehicle = self.vehicle_table.tree.selection()
        vehicle_id = self.vehicle_table.tree.item(selected_vehicle)["values"][1]
        current_transform = self.database.actors_db.actors_db[vehicle_id].transform
        top = ChangeTransformTop(self, self.database.world.get_map().get_spawn_points(), current_transform, self.set_new_transform)

    def set_new_transform(self, transform, event=None):
        print(transform)
        selected_vehicle = self.vehicle_table.tree.selection()
        if not selected_vehicle:
            return
        selected_vehicle_id = self.vehicle_table.tree.item(selected_vehicle)["values"][1]
        self.controller.carla.set_actor_transform(transform, selected_vehicle_id)
        self.vehicle_table.tree.item(selected_vehicle, values=(
            self.vehicle_table.tree.item(selected_vehicle)["values"][0],
            self.vehicle_table.tree.item(selected_vehicle)["values"][1],
            self.vehicle_table.tree.item(selected_vehicle)["values"][2],
            f"({transform.location.x:.2f}, {transform.location.y:.2f}, {transform.location.z:.2f})",
            f"({transform.rotation.yaw:.2f}, {transform.rotation.pitch:.2f}, {transform.rotation.roll:.2f})",
            self.vehicle_table.tree.item(selected_vehicle)["values"][5],
            self.vehicle_table.tree.item(selected_vehicle)["values"][6]
        ))

    def copy_vehicle(self, event=None):
        pass

    def change_model(self, event=None):
        if not self.vehicle_table.tree.selection():
            return
        top = ChangeModelTop(self, "vehicle", self.database.blueprint_lib, self.change_spawned_vehicle_model)

    def change_spawned_vehicle_model(self,selected_class, selected_model, event=None):
        selected_vehicle = self.vehicle_table.tree.selection()
        if not selected_vehicle:
            return
        actor_model = filter_carla_id_by_type("vehicle", selected_class, self.database.blueprint_lib)
        blueprint_id = actor_model[selected_model]
        selected_vehicle_id = self.vehicle_table.tree.item(selected_vehicle)["values"][1]
        self.controller.close_movement_control_for_actor(
            selected_vehicle_id,
            "Selected vehicle is being replaced",
        )
        self.controller.carla.change_actor_model(blueprint_id, self.database.actors_db.actors[selected_vehicle_id].id)

        try:
            is_changed = wait_and_try(
                lambda: self.database.spawned_actor_id is not None
            )
            if is_changed:
                veh_id = self.database.actors_db.actors[self.database.spawned_actor_id].id
                new_vehicle_model = selected_class.upper() + ":" + selected_model
                self.vehicle_table.tree.item(selected_vehicle, values=(
                    self.vehicle_table.tree.item(selected_vehicle)["values"][0],
                    veh_id,
                    new_vehicle_model,
                    self.vehicle_table.tree.item(selected_vehicle)["values"][3],
                    self.vehicle_table.tree.item(selected_vehicle)["values"][4],
                    self.vehicle_table.tree.item(selected_vehicle)["values"][5],
                    self.vehicle_table.tree.item(selected_vehicle)["values"][6]
                ))
                self.database.spawned_actor_id = None
        except KeyError:
            veh_id = None
            print("Database update failed. Vehicle not found in database.")

    def _update_model(self, event):
        self.actor_model = filter_carla_id_by_type("vehicle", self.choose_class.get().lower(),
                                                         self.database.blueprint_lib)
        self.actor_model_combobox["values"] = list(self.actor_model.keys())
        self.actor_model_combobox.current(0)
        self.image_frame.update_image()



    def _set_spawn_transform_variables(self, transform):
        x, y, z, yaw, pitch, roll = return_data_from_transform(transform)
        self.x_position_var.set(x)
        self.y_position_var.set(y)
        self.z_position_var.set(z)
        self.yaw_position_var.set(yaw)
        self.pitch_position_var.set(pitch)
        self.roll_position_var.set(roll)

    def _update_location_from_spawn_point(self, event=None):
        selected_value = self.selected_spawn_point.get()
        if not selected_value:
            return
        spawn_point_num = int(str(selected_value).split(":", 1)[0])
        available_spawn_points = self.database.available_spawn_points
        if not 0 <= spawn_point_num < len(available_spawn_points):
            return

        transform = available_spawn_points[spawn_point_num]
        self._set_spawn_transform_variables(transform)

        if self.vehicle_preview:
            self.vehicle_preview.set_transform(transform)

    def update_spawn_point(self):
        self.available_spawn_points = [
            f"{index}: {point}"
            for index, point in enumerate(self.database.available_spawn_points)
        ]
        self.combobox_spawn_points["values"] = self.available_spawn_points
        if not self.database.available_spawn_points:
            self.selected_spawn_point.set("")
            return

        self.combobox_spawn_points.current(0)
        self._set_spawn_transform_variables(
            self.database.available_spawn_points[0]
        )

    def destroy_ghost(self):
        if self.vehicle_preview:
            self.vehicle_preview.destroy()

    def _on_model_change(self, event):
        selected_model = self.actor_model[self.actor_model_combobox.get()]
        if self.vehicle_preview:
            transform = self.vehicle_preview.get_transform()
            self.destroy_ghost()
            self.spawn_ghost(blueprint_id=selected_model, transform=transform)

    def get_vehicle_info(self):
        vehicle_name = self.vehicle_name_var.get()
        x_pos = self.x_position_var.get()
        y_pos = self.y_position_var.get()
        z_pos = self.z_position_var.get()
        yaw_pos = self.yaw_position_var.get()
        pitch_pos = self.pitch_position_var.get()
        roll_pos = self.roll_position_var.get()
        carla_id = self.database.ghost_data.id if self.database.ghost_data is not None else None
        vehicle_info = {
            "name": vehicle_name,
            "carla_id": carla_id,
            "type_id": self.choose_class.get()+":"+self.choose_model.get(),
            "location": f"({x_pos:.2f}, {y_pos:.2f}, {z_pos:.2f})" if self.database.ghost_data is not None else None,
            "rotation": f"({pitch_pos:.2f}, {yaw_pos:.2f}, {roll_pos:.2f})" if self.database.ghost_data is not None else None,
            "sensors": "",  # TODO: Implement sensor information retrieval when the sensor management is implemented
            "is_active": ("Active" if self.database.ghost_data.is_alive else "Inactive") if self.database.ghost_data is not None else "None"
        }
        return vehicle_info

    def spawn_vehicle(self):
        if self.database.ghost_data is not None:
            vehicle_name = self.vehicle_name_var.get()
            transform = carla.Transform(
                carla.Location(
                    x=self.x_position_var.get(),
                    y=self.y_position_var.get(),
                    z=self.z_position_var.get()
                ),
                carla.Rotation(
                    pitch=self.pitch_position_var.get(),
                    yaw=self.yaw_position_var.get(),
                    roll=self.roll_position_var.get()
                )
            )
            sensors = "" #TODO: Implement sensor information retrieval when the sensor management is implemented
            actor_model = filter_carla_id_by_type("vehicle", self.choose_class.get().lower(),
                                                       self.database.blueprint_lib)
            data = {
                "name": vehicle_name,
                "transform": transform,
                "sensors": sensors,
                "blueprint_id": actor_model[self.choose_model.get()]}

            vehicle_data = self.get_vehicle_info()
            self.destroy_ghost()
            self.controller.carla.spawn_actor(data)
            self.reset_tab()
            try:
                is_changed = wait_and_try(
                    lambda: self.database.spawned_actor_id is not None
                )
                if is_changed:
                    veh_id = self.database.actors_db.actors[self.database.spawned_actor_id].id
                    vehicle_data["carla_id"] = veh_id
                    self.vehicle_table.add_vehicle_to_table(vehicle_data)
                    self.database.spawned_actor_id = None
            except KeyError:
                veh_id = None
                print("Database update failed. Vehicle not found in database.")
            self.spawn_ghost()





    def remove_vehicle(self):
        selected_vehicle = self.vehicle_table.tree.selection()
        if selected_vehicle:
            vehicle_id = self.vehicle_table.tree.item(selected_vehicle)["values"][1]
            self.controller.close_movement_control_for_actor(
                vehicle_id,
                "Selected vehicle removed",
            )
            self.controller.carla.remove_actor(vehicle_id)
            self.vehicle_table.tree.delete(selected_vehicle)

    def edit_vehicle(self):
        blueprint = self.actor_model[self.actor_model_combobox.get()]
        top = EntityEditWindow(self, blueprint, None, "vehicle", self.database.blueprint_lib, self.database.ghost_physic, self.apply_vehicle_changes, self.database.blueprint_new_data)

    def apply_vehicle_changes(self, data):
        blueprint_data = data["blueprint"]
        physics = data["physics"]
        self.database.blueprint_new_data = blueprint_data
        print(blueprint_data)
        self.controller.carla.apply_new_physics_and_blueprint_to_ghost(physics)

        is_changed = wait_and_try(
            lambda: self.database.physics_applied == True
        )

        if is_changed:
            self.image_frame.update_image()
            self.database.physics_applied = False

    def reset_tab(self):
        self.vehicle_name_var.set("Ghost Vehicle")
        self.choose_class.set("Car")
        self._update_model(None)
        self.x_position_var.set(self.database.available_spawn_points[0].location.x if self.database.available_spawn_points else 0)
        self.y_position_var.set(self.database.available_spawn_points[0].location.y if self.database.available_spawn_points else 0)
        self.z_position_var.set(self.database.available_spawn_points[0].location.z if self.database.available_spawn_points else 0)
        self.yaw_position_var.set(self.database.available_spawn_points[0].rotation.yaw if self.database.available_spawn_points else 0)
        self.pitch_position_var.set(self.database.available_spawn_points[0].rotation.pitch if self.database.available_spawn_points else 0)
        self.roll_position_var.set(self.database.available_spawn_points[0].rotation.roll if self.database.available_spawn_points else 0)
        self.combobox_spawn_points.current(0)

    def update_vehicle(self):
        self.remove_vehicle()
        self.spawn_vehicle()

    def set_variables_from_table(self, event):
        selected_vehicle = self.vehicle_table.tree.selection()
        if selected_vehicle:
            vehicle_data = self.vehicle_table.tree.item(selected_vehicle)["values"]
            self.vehicle_name_var.set(vehicle_data[0])
            self.choose_class.set(vehicle_data[2].split(":")[0])
            self.choose_model.set(vehicle_data[2].split(":")[1])
            x_location = float(vehicle_data[3].strip("{(())}").split(",")[0])
            y_location = float(vehicle_data[3].strip("{(())}").split(",")[1])
            z_location = float(vehicle_data[3].strip("{(())}").split(",")[2])
            yaw_rotation = float(vehicle_data[4].strip("{(())}").split(",")[1])
            pitch_rotation = float(vehicle_data[4].strip("{(())}").split(",")[0])
            roll_rotation = float(vehicle_data[4].strip("{(())}").split(",")[2])
            self.x_position_var.set(x_location)
            self.y_position_var.set(y_location)
            self.z_position_var.set(z_location)
            self.yaw_position_var.set(yaw_rotation)
            self.pitch_position_var.set(pitch_rotation)
            self.roll_position_var.set(roll_rotation)
            self._update_model(None)



class VehicleTable(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, style="new.TFrame")
        self.controller = controller
        self.database = controller.database
        self.carla = controller.carla

        self.default_color = "#F7E7D3"
        self.panel_color = "#F1D0A3"
        self.accent_color = "#C96D3C"
        self.text_color = "#4A2E1D"
        self.subtle_color = "#E7B36C"

        title = ttk.Label(
            self,
            text="Vehicles",
            style="TLabel",
            font=("Arial", 18, "bold")
        )
        title.pack(pady=(10, 8), fill="x")

        columns = (
            "Vehicle Name",
            "ID",
            "Model",
            "Location",
            "Rotation",
            "Sensors",
            "Status"
        )

        self.tree = ttk.Treeview(
            self,
            columns=columns,
            show="headings",
            height=18,
            style="Treeview",
        )

        self.tree.heading("Vehicle Name", text="Vehicle Name")
        self.tree.heading("ID", text="ID")
        self.tree.heading("Model", text="Type ID")
        self.tree.heading("Location", text="Location")
        self.tree.heading("Rotation", text="Rotation")
        self.tree.heading("Sensors", text="Sensors")
        self.tree.heading("Status", text="Status")

        self.tree.column("Vehicle Name", width=240, minwidth=180, anchor="center")
        self.tree.column("ID", width=40, minwidth=40, anchor="center")
        self.tree.column("Model", width=240, minwidth=220, anchor="center")
        self.tree.column("Location", width=170, minwidth=140, anchor="center")
        self.tree.column("Rotation", width=170, minwidth=140, anchor="center")
        self.tree.column("Sensors", width=80, minwidth=120, anchor="center")
        self.tree.column("Status", width=80, minwidth=120, anchor="center")

        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self.edit_cell)

    def edit_cell(self, event):
        region = self.tree.identify("region", event.x, event.y)

        if region != "cell":
            return

        column = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)

        if not row or not column:
            return

        if column != "#1":
            return

        bbox = self.tree.bbox(row, column)

        if not bbox:
            return

        x, y, width, height = bbox

        value = self.tree.set(row, column)

        entry = ttk.Entry(self.tree)
        entry.insert(0, value)

        entry.place(x=x, y=y, width=width, height=height)
        entry.focus()
        entry.select_range(0, tk.END)

        def save(event=None):
            new_value = entry.get()
            try:
                new_value = str(new_value)
            except ValueError:
                entry.destroy()
                return
            self.tree.set(row, column, new_value)
            vehicle_id = self.tree.item(row)["values"][1]
            self.controller.database.actors_db.actors_db[vehicle_id].user_name = new_value
            entry.destroy()

        entry.bind("<Return>", save)
        entry.bind("<FocusOut>", save)
        entry.bind("<Escape>", lambda event: entry.destroy())

    def add_vehicle_to_table(self, vehicle_info):
        name = vehicle_info["name"]
        vehicle_id = vehicle_info["carla_id"]
        model = vehicle_info["type_id"]
        location = f"({vehicle_info['location']})",
        rotation = f"({vehicle_info['rotation']})",
        sensors = ""    #TODO: Implement sensor information retrieval when the sensor management is implemented
        status = vehicle_info["is_active"]
        self.tree.insert("", "end", values=(name, vehicle_id, model, location, rotation, sensors, status))

    def remove_vehicle_from_table(self, vehicle_info):
        vehicle_id = vehicle_info["carla_id"]
        for item in self.tree.get_children():
            if self.tree.item(item)["values"][1] == vehicle_id:
                self.tree.delete(item)
                break

    def update_vehicle_in_table(self, vehicle_info):
        vehicle_id = vehicle_info["carla_id"]
        for item in self.tree.get_children():
            if self.tree.item(item)["values"][1] == vehicle_id:
                name = vehicle_info["name"]
                model = vehicle_info["type_id"]
                location = f"({vehicle_info['location']})",
                rotation = f"({vehicle_info['rotation']})",
                sensors = ""    #TODO: Implement sensor information retrieval when the sensor management is implemented
                status = vehicle_info["is_active"]
                self.tree.item(item, values=(name, vehicle_id, model, location, rotation, sensors, status))
                break

    def initialize_data(self):
        for actor_id, actor_info in self.database.actors_db.actors_db.items():
            if "vehicle" in actor_info.type_id:
                location = actor_info.location
                transform = actor_info.transform
                vehicle_info = {
                    "name": actor_info.user_name,
                    "carla_id": actor_info.carla_id,
                    "type_id": actor_info.type_id,
                    "location": (
                        f"({location.x:.2f}, {location.y:.2f}, {location.z:.2f})"
                        if location is not None else "Unknown"
                    ),
                    "rotation": (
                        f"({transform.rotation.pitch:.2f}, "
                        f"{transform.rotation.yaw:.2f}, "
                        f"{transform.rotation.roll:.2f})"
                        if transform is not None else "Unknown"
                    ),
                    "sensors": "",  # TODO: Implement sensor information retrieval when the sensor management is implemented
                    "is_active": ("Active" if actor_info.is_alive else "Inactive")
                }
                self.add_vehicle_to_table(vehicle_info)

    def refresh_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.initialize_data()
