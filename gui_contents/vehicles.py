import queue
import tkinter as tk
import uuid
from tkinter import messagebox, ttk
from PIL import Image, ImageTk
from pygments.lexers import blueprint

from utilities.functions import (
    actor_ownership_status,
    actor_sensor_status,
    filter_carla_id_by_type,
    return_data_from_transform,
)
from gui_contents.spawn_preview import VehiclePreviewFrame, GhostPreview
from gui_contents.ActorEditView import EntityEditWindow
from gui_contents.change_model_top import ChangeModelTop
from gui_contents.set_transform_top import ChangeTransformTop
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
        self.actor_event_check = None
        self.pending_requests = {}
        self.configured_physics = None
        self.configured_blueprint_attributes = {}

        self._vehicle_variables_definition()
        self._vehicle_frame_setup()
        self.check_actor_events()

    def spawn_ghost(self, blueprint_id=None, transform=None):
        if not self.ghost_preview_enabled.get():
            return False
        if blueprint_id is None:
            blueprint_id = self.actor_model.get(self.choose_model.get())
        if blueprint_id is None:
            return False
        return self.vehicle_preview.spawn(
            blueprint_id=blueprint_id,
            transform=transform or self._current_transform(),
        )

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
        self.ghost_preview_enabled = tk.BooleanVar(value=True)

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

        ttk.Checkbutton(
            heading_frame,
            text="Enable Ghost Preview",
            variable=self.ghost_preview_enabled,
            command=self._toggle_ghost_preview,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))

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
        physics_button = ttk.Button(button_frame, text="Edit Spawn Setup", command=self.edit_vehicle, width=18)
        physics_button.grid(row=0, column=1, sticky="ew", pady=(0, 8), padx=(0, 10))
        remove_button = ttk.Button(button_control_frame, text="Remove", command=self.remove_vehicle, width=18)
        remove_button.grid(row=0, column=0, sticky="ew", pady=(0, 8), padx=(0, 10))
        change_model_button = ttk.Button(button_control_frame, text="Change Model", command=self.change_model, width=18)
        change_model_button.grid(row=0, column=1, sticky="ew", pady=(0, 8), padx=(0, 10))
        edit_physics_button = ttk.Button(
            button_control_frame,
            text="Edit Physics",
            command=self.edit_spawned_vehicle_physics,
            width=18,
        )
        edit_physics_button.grid(row=0, column=2, sticky="ew", pady=(0, 8), padx=(0, 10))
        update_location_button = ttk.Button(button_control_frame, text="Update Location", command=self.update_location, width=18)
        update_location_button.grid(row=0, column=3, sticky="ew", pady=(0, 8), padx=(0, 10))
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
        if not self.controller.carla.set_actor_transform(
            transform,
            selected_vehicle_id,
        ):
            return
        self.vehicle_table.tree.item(selected_vehicle, values=(
            self.vehicle_table.tree.item(selected_vehicle)["values"][0],
            self.vehicle_table.tree.item(selected_vehicle)["values"][1],
            self.vehicle_table.tree.item(selected_vehicle)["values"][2],
            f"({transform.location.x:.2f}, {transform.location.y:.2f}, {transform.location.z:.2f})",
            f"({transform.rotation.yaw:.2f}, {transform.rotation.pitch:.2f}, {transform.rotation.roll:.2f})",
            self.vehicle_table.tree.item(selected_vehicle)["values"][5],
            self.vehicle_table.tree.item(selected_vehicle)["values"][6]
        ))

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
        request_id = self._new_request("change_model_config", {
            "actor_id": int(selected_vehicle_id),
            "blueprint_id": blueprint_id,
        })
        if not self.controller.carla.get_vehicle_edit_data(
            selected_vehicle_id,
            request_id,
        ):
            self.pending_requests.pop(request_id, None)

    def _update_model(self, event):
        self.database.blueprint_new_data = None
        self.configured_blueprint_attributes = {}
        self.configured_physics = None
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

    def _current_transform(self):
        return carla.Transform(
            carla.Location(
                x=self.x_position_var.get(),
                y=self.y_position_var.get(),
                z=self.z_position_var.get(),
            ),
            carla.Rotation(
                pitch=self.pitch_position_var.get(),
                yaw=self.yaw_position_var.get(),
                roll=self.roll_position_var.get(),
            ),
        )

    def _toggle_ghost_preview(self):
        enabled = bool(self.ghost_preview_enabled.get())
        self.vehicle_preview.set_enabled(enabled)
        if enabled:
            self.spawn_ghost()
        else:
            self.destroy_ghost()

    def is_ghost_preview_enabled(self):
        return bool(self.ghost_preview_enabled.get())

    def destroy_ghost(self):
        if self.vehicle_preview:
            self.vehicle_preview.destroy()

    def _on_model_change(self, event):
        self.database.blueprint_new_data = None
        self.configured_blueprint_attributes = {}
        self.configured_physics = None
        selected_model = self.actor_model[self.actor_model_combobox.get()]
        if self.is_ghost_preview_enabled():
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
        vehicle_info = {
            "name": vehicle_name,
            "carla_id": None,
            "type_id": self.choose_class.get()+":"+self.choose_model.get(),
            "location": f"({x_pos:.2f}, {y_pos:.2f}, {z_pos:.2f})",
            "rotation": f"({pitch_pos:.2f}, {yaw_pos:.2f}, {roll_pos:.2f})",
            "sensors": "",  # TODO: Implement sensor information retrieval when the sensor management is implemented
            "status": "Managed"
        }
        return vehicle_info

    def spawn_vehicle(self):
        vehicle_name = self.vehicle_name_var.get()
        transform = self._current_transform()
        actor_model = filter_carla_id_by_type(
            "vehicle",
            self.choose_class.get().lower(),
            self.database.blueprint_lib,
        )
        if self.choose_model.get() not in actor_model:
            return
        data = {
            "name": vehicle_name,
            "transform": transform,
            "sensors": "",
            "blueprint_id": actor_model[self.choose_model.get()],
            "attributes": dict(
                self.configured_blueprint_attributes
                or self.database.blueprint_new_data
                or {}
            ),
            "physics": (
                self.configured_physics
                if self.configured_physics is not None
                else self.database.ghost_physic
            ),
        }
        request_id = self._new_request("spawn")
        data["request_id"] = request_id
        self.destroy_ghost()
        if not self.controller.carla.spawn_actor(data):
            self.pending_requests.pop(request_id, None)
            self.spawn_ghost(
                blueprint_id=data["blueprint_id"],
                transform=transform,
            )
            return
        self.reset_tab()
        self.spawn_ghost()





    def remove_vehicle(self):
        selected_vehicle = self.vehicle_table.tree.selection()
        if selected_vehicle:
            vehicle_id = self.vehicle_table.tree.item(selected_vehicle)["values"][1]
            self.controller.close_movement_control_for_actor(
                vehicle_id,
                "Selected vehicle removed",
            )
            if self.controller.carla.remove_actor(vehicle_id):
                self.vehicle_table.tree.delete(selected_vehicle)

    def edit_vehicle(self):
        blueprint = self.actor_model[self.actor_model_combobox.get()]
        EntityEditWindow(
            self,
            blueprint,
            None,
            "vehicle",
            self.database.blueprint_lib,
            self.database.ghost_physic,
            self.apply_vehicle_changes,
            self.database.blueprint_new_data,
        )

    def edit_spawned_vehicle_physics(self):
        selected = self.vehicle_table.tree.selection()
        if not selected:
            messagebox.showinfo(
                "Edit Physics",
                "Select an existing vehicle in the table first.",
                parent=self,
            )
            return
        actor_id = int(self.vehicle_table.tree.item(selected[0])["values"][1])
        request_id = self._new_request("edit_physics", {"actor_id": actor_id})
        if not self.controller.carla.get_vehicle_edit_data(actor_id, request_id):
            self.pending_requests.pop(request_id, None)

    def apply_vehicle_changes(self, data):
        blueprint_data = data["blueprint"]
        physics = data["physics"]
        self.database.blueprint_new_data = blueprint_data
        self.configured_blueprint_attributes = dict(blueprint_data or {})
        self.configured_physics = physics
        if self.is_ghost_preview_enabled() and self.database.ghost_data is not None:
            self.controller.carla.apply_new_physics_and_blueprint_to_ghost(
                physics
            )
        return True

    def _new_request(self, operation, data=None):
        request_id = str(uuid.uuid4())
        request = {"operation": operation}
        request.update(data or {})
        self.pending_requests[request_id] = request
        return request_id

    def _apply_existing_vehicle_physics(self, actor_id, data):
        request_id = self._new_request("apply_physics", {"actor_id": actor_id})
        if not self.controller.carla.apply_vehicle_physics(
            actor_id,
            data["physics"],
            request_id,
        ):
            self.pending_requests.pop(request_id, None)
            return False
        return True

    def _request_vehicle_replacement(self, actor_id, blueprint_id,
                                     attributes, physics):
        self.controller.close_movement_control_for_actor(
            actor_id,
            "Selected vehicle is being replaced",
        )
        request_id = self._new_request("replace", {"actor_id": actor_id})
        accepted = self.controller.carla.change_actor_model(
            blueprint_id,
            actor_id,
            attributes=attributes,
            physics=physics,
            request_id=request_id,
        )
        if not accepted:
            self.pending_requests.pop(request_id, None)
        return accepted

    def check_actor_events(self):
        try:
            while True:
                status, data = self.database.actor_event_queue.get_nowait()
                if status == "ghost_configuration_applied":
                    if data.get("success"):
                        self.image_frame.update_image()
                        self.database.physics_applied = False
                    else:
                        messagebox.showerror(
                            "Vehicle configuration",
                            data.get("error", "Could not configure preview vehicle."),
                            parent=self,
                        )
                    continue

                request_id = data.get("request_id") if isinstance(data, dict) else None
                request = self.pending_requests.pop(request_id, None)
                if request is None:
                    continue

                if status == "vehicle_edit_data":
                    if not data.get("success"):
                        messagebox.showerror(
                            "Edit vehicle",
                            data.get("error", "Could not read vehicle data."),
                            parent=self,
                        )
                        continue
                    if request["operation"] == "change_model_config":
                        blueprint_id = request["blueprint_id"]
                        callback = lambda edited: self._confirm_model_replacement(
                            request["actor_id"], blueprint_id, edited
                        )
                        blueprint_data = None
                        show_blueprint = True
                        show_physics = False
                    else:
                        blueprint_id = data["blueprint_id"]
                        callback = lambda edited: self._apply_existing_vehicle_physics(
                            data["actor_id"],
                            edited,
                        )
                        blueprint_data = None
                        show_blueprint = False
                        show_physics = True
                    EntityEditWindow(
                        self,
                        blueprint_id,
                        None,
                        "vehicle",
                        self.database.blueprint_lib,
                        data["physics"],
                        callback,
                        blueprint_data,
                        show_blueprint,
                        show_physics,
                    )
                elif status == "vehicle_physics_applied":
                    if data.get("success"):
                        messagebox.showinfo(
                            "Vehicle physics",
                            "Physics was applied and verified against CARLA.",
                            parent=self,
                        )
                    else:
                        messagebox.showerror(
                            "Vehicle physics",
                            data.get("error", "CARLA rejected the physics settings."),
                            parent=self,
                        )
                elif status == "actor_operation_result":
                    self.database.spawned_actor_id = None
                    self.controller.refresh_actor_tables()
                    if data.get("success"):
                        warnings = data.get("warnings") or []
                        if warnings:
                            messagebox.showwarning(
                                "Vehicle replaced with warnings",
                                "\n".join(warnings),
                                parent=self,
                            )
                    else:
                        messagebox.showerror(
                            "Vehicle operation failed",
                            data.get("error", "Unknown CARLA error."),
                            parent=self,
                        )
        except queue.Empty:
            pass
        except Exception as error:
            messagebox.showerror(
                "Actors table update",
                f"Could not process an actor operation result: {error}",
                parent=self,
            )
        finally:
            if self.winfo_exists():
                self.actor_event_check = self.after(100, self.check_actor_events)

    def _confirm_model_replacement(self, actor_id, blueprint_id, data):
        confirmed = messagebox.askyesno(
            "Replace vehicle model",
            "Changing the model replaces the runtime actor. Movement control "
            "and active sensor previews will be stopped; attached sensors will "
            "be recreated. Continue?",
            parent=self,
        )
        if not confirmed:
            return False
        return self._request_vehicle_replacement(
            actor_id,
            blueprint_id,
            data["blueprint"],
            data["physics"],
        )

    def destroy(self):
        if self.actor_event_check is not None:
            self.after_cancel(self.actor_event_check)
            self.actor_event_check = None
        super().destroy()

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
        sensors = actor_sensor_status(self.database, vehicle_id)
        status = vehicle_info.get("status", vehicle_info.get("is_active", "Unknown"))
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
                sensors = actor_sensor_status(self.database, vehicle_id)
                status = vehicle_info.get("status", vehicle_info.get("is_active", "Unknown"))
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
                    "status": actor_ownership_status(actor_info)
                }
                self.add_vehicle_to_table(vehicle_info)

    def refresh_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.initialize_data()
