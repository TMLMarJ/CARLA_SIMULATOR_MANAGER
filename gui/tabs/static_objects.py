import tkinter as tk
from tkinter import ttk

import carla

from gui.widgets.actor_preview import ActorPreviewFrame, GhostPreview
from gui.widgets.actor_common import (
    clear_tree,
    find_tree_item_by_runtime_id,
    remove_tree_item_by_runtime_id,
)
from gui.windows.actor_editor import EntityEditWindow
from gui.windows.model_picker import ChangeModelTop
from gui.windows.transform_editor import ChangeTransformTop
from utilities.functions import (
    actor_ownership_status,
    actor_sensor_status,
    filter_carla_id_by_type,
    return_data_from_transform,
    wait_and_try,
)


class CustomStaticObjectFrame(tk.Frame):
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

        self.actor_preview = GhostPreview(self.database, controller)

        self._static_object_variables_definition()
        self._static_object_frame_setup()

    def spawn_ghost(self, blueprint_id=None, transform=None):
        if not self.ghost_preview_enabled.get():
            return False
        if not self.actor_model:
            return
        if blueprint_id is None:
            blueprint_id = self.actor_model[self.choose_model.get()]
        return self.actor_preview.spawn(
            blueprint_id=blueprint_id,
            transform=transform or self._current_transform(),
        )

    def _static_object_variables_definition(self):
        self.actor_name_var = tk.StringVar(value="Ghost Static Object")
        first_spawn_point = self.database.available_spawn_points[0] if self.database.available_spawn_points else None
        self.x_position_var = tk.DoubleVar(value=first_spawn_point.location.x if first_spawn_point else 0)
        self.y_position_var = tk.DoubleVar(value=first_spawn_point.location.y if first_spawn_point else 0)
        self.z_position_var = tk.DoubleVar(value=first_spawn_point.location.z if first_spawn_point else 0)
        self.yaw_position_var = tk.DoubleVar(value=first_spawn_point.rotation.yaw if first_spawn_point else 0)
        self.pitch_position_var = tk.DoubleVar(value=first_spawn_point.rotation.pitch if first_spawn_point else 0)
        self.roll_position_var = tk.DoubleVar(value=first_spawn_point.rotation.roll if first_spawn_point else 0)
        self.selected_spawn_point = tk.StringVar()
        self.choose_model = tk.StringVar()
        self.actor_model = {}
        self.ghost_preview_enabled = tk.BooleanVar(value=True)

    def _static_object_frame_setup(self):
        upper_frame = ttk.LabelFrame(
            self, text="Spawn Static Objects", style="Section.TLabelframe", padding=(12, 8)
        )
        lower_frame = ttk.LabelFrame(
            self, text="Static Object Management", style="Section.TLabelframe", padding=(12, 8)
        )
        upper_frame.pack(side="top", fill="x", padx=18, pady=(6, 12))
        lower_frame.pack(side="bottom", fill="both", expand=True, padx=18, pady=(6, 12))

        for column in range(3):
            upper_frame.grid_columnconfigure(column, weight=1)
        lower_frame.grid_columnconfigure(0, weight=1)
        lower_frame.grid_rowconfigure(0, weight=1)

        heading_frame = ttk.Frame(upper_frame, style="new.TFrame")
        heading_frame.grid(row=0, column=0, sticky="nw", padx=(12, 8), pady=(8, 8))
        heading_frame.grid_columnconfigure(0, weight=1)

        location_frame = ttk.Frame(upper_frame, style="new.TFrame")
        location_frame.grid(row=0, column=1, sticky="ew", padx=8, pady=(8, 8))

        self.image_frame = ActorPreviewFrame(upper_frame, self.actor_preview)
        self.image_frame.grid(row=0, column=2, sticky="n", padx=(8, 12), pady=(8, 8))

        button_frame = ttk.Frame(upper_frame, style="new.TFrame")
        button_frame.grid(row=1, column=0, columnspan=3, sticky="n", padx=(0, 12), pady=(0, 8))

        ttk.Label(
            heading_frame,
            text="Static objects menu",
            background=self.default_color,
            foreground=self.text_color,
            font=("Arial", 18, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        ttk.Label(
            heading_frame, text="Object Name", background=self.default_color, font=("Arial", 14, "bold")
        ).grid(row=1, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(heading_frame, textvariable=self.actor_name_var).grid(row=2, column=0, sticky="ew", pady=(0, 14))

        ttk.Label(
            heading_frame, text="Model", background=self.default_color, font=("Arial", 11, "bold")
        ).grid(row=3, column=0, sticky="w", pady=(0, 5))

        self.actor_model = filter_carla_id_by_type("static", "prop", self.database.blueprint_lib)
        self.actor_model_combobox = ttk.Combobox(
            heading_frame, textvariable=self.choose_model, state="readonly"
        )
        self.actor_model_combobox["values"] = list(self.actor_model.keys())
        if self.actor_model:
            self.actor_model_combobox.current(0)
        self.actor_model_combobox.bind("<<ComboboxSelected>>", self._on_model_change)
        self.actor_model_combobox.grid(row=4, column=0, sticky="ew")

        ttk.Label(
            heading_frame, text="Spawn Point", background=self.default_color, font=("Arial", 14, "bold")
        ).grid(row=5, column=0, sticky="w", pady=(14, 6))

        self.available_spawn_points = [
            f"{index}: {point}" for index, point in enumerate(self.database.available_spawn_points)
        ]
        self.combobox_spawn_points = ttk.Combobox(
            heading_frame, textvariable=self.selected_spawn_point, state="readonly"
        )
        self.combobox_spawn_points["values"] = self.available_spawn_points
        if self.available_spawn_points:
            self.combobox_spawn_points.current(0)
        self.combobox_spawn_points.bind("<<ComboboxSelected>>", self._update_location_from_spawn_point)
        self.combobox_spawn_points.grid(row=6, column=0, sticky="ew")

        ttk.Checkbutton(
            heading_frame,
            text="Enable Ghost Preview",
            variable=self.ghost_preview_enabled,
            command=self._toggle_ghost_preview,
        ).grid(row=7, column=0, sticky="w", pady=(10, 0))

        self._create_transform_controls(location_frame)

        self.actor_table = StaticObjectTable(lower_frame, self.controller)
        self.actor_table.grid(row=0, column=0, sticky="nsew", padx=20, pady=(10, 10))

        button_control_frame = ttk.Frame(lower_frame, style="new.TFrame")
        button_control_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 12))
        for column in range(3):
            button_control_frame.grid_columnconfigure(column, weight=1)

        ttk.Button(button_frame, text="Spawn Static Object", command=self.spawn_actor, width=20).grid(
            row=0, column=0, sticky="ew", padx=(0, 10), pady=(0, 8)
        )
        ttk.Button(button_frame, text="Edit", command=self.edit_actor, width=18).grid(
            row=0, column=1, sticky="ew", padx=(0, 10), pady=(0, 8)
        )
        ttk.Button(button_control_frame, text="Remove", command=self.remove_actor, width=18).grid(
            row=0, column=0, sticky="ew", padx=(0, 10), pady=(0, 8)
        )
        ttk.Button(button_control_frame, text="Change Model", command=self.change_model, width=18).grid(
            row=0, column=1, sticky="ew", padx=(0, 10), pady=(0, 8)
        )
        ttk.Button(button_control_frame, text="Update Location", command=self.update_location, width=18).grid(
            row=0, column=2, sticky="ew", padx=(0, 10), pady=(0, 8)
        )

    def _create_transform_controls(self, parent):
        position_frame = ttk.Frame(parent, style="new.TFrame")
        position_frame.grid(row=0, column=0, sticky="nw")
        rotation_frame = ttk.Frame(parent, style="new.TFrame")
        rotation_frame.grid(row=0, column=1, sticky="nw", padx=(20, 0))

        ttk.Label(position_frame, text="Position", background=self.default_color, font=("Arial", 18)).grid(
            row=0, column=0, sticky="nw", pady=20
        )
        position_fields = (
            ("X - position", self.x_position_var),
            ("Y - position", self.y_position_var),
            ("Z - position", self.z_position_var),
        )
        for index, (label, variable) in enumerate(position_fields):
            row = 1 + index * 2
            ttk.Label(position_frame, text=label, background=self.default_color).grid(row=row, column=0, sticky="nw")
            tk.Spinbox(position_frame, from_=-1e9, to=1e9, increment=0.01, textvariable=variable).grid(
                row=row + 1, column=0
            )

        ttk.Label(rotation_frame, text="Rotation", background=self.default_color, font=("Arial", 18)).grid(
            row=0, column=0, pady=20
        )
        rotation_fields = (
            ("Yaw", self.yaw_position_var),
            ("Pitch", self.pitch_position_var),
            ("Roll", self.roll_position_var),
        )
        for index, (label, variable) in enumerate(rotation_fields):
            row = 1 + index * 2
            ttk.Label(rotation_frame, text=label, background=self.default_color).grid(row=row, column=0, sticky="nw")
            tk.Spinbox(rotation_frame, from_=-1e9, to=1e9, increment=0.01, textvariable=variable).grid(
                row=row + 1, column=0
            )

    def _current_transform(self):
        return carla.Transform(
            carla.Location(
                x=self.x_position_var.get(), y=self.y_position_var.get(), z=self.z_position_var.get()
            ),
            carla.Rotation(
                pitch=self.pitch_position_var.get(),
                yaw=self.yaw_position_var.get(),
                roll=self.roll_position_var.get(),
            ),
        )

    def update_location(self, event=None):
        selected_actor = self.actor_table.tree.selection()
        if not selected_actor:
            return
        actor_id = self.actor_table.tree.item(selected_actor)["values"][1]
        current_transform = self.database.actors_db.actors_db[actor_id].transform
        ChangeTransformTop(
            self,
            self.database.world.get_map().get_spawn_points(),
            current_transform,
            self.set_new_transform,
        )

    def set_new_transform(self, transform, event=None):
        selected_actor = self.actor_table.tree.selection()
        if not selected_actor:
            return
        actor_id = self.actor_table.tree.item(selected_actor)["values"][1]
        self.controller.carla.set_actor_transform(transform, actor_id)
        values = list(self.actor_table.tree.item(selected_actor)["values"])
        values[3] = f"({transform.location.x:.2f}, {transform.location.y:.2f}, {transform.location.z:.2f})"
        values[4] = f"({transform.rotation.pitch:.2f}, {transform.rotation.yaw:.2f}, {transform.rotation.roll:.2f})"
        self.actor_table.tree.item(selected_actor, values=values)

    def change_model(self, event=None):
        if self.actor_table.tree.selection():
            ChangeModelTop(self, "static", self.database.blueprint_lib, self.change_spawned_actor_model)

    def change_spawned_actor_model(self, selected_class, selected_model, event=None):
        selected_actor = self.actor_table.tree.selection()
        if not selected_actor:
            return
        models = filter_carla_id_by_type("static", selected_class, self.database.blueprint_lib)
        blueprint_id = models[selected_model]
        actor_id = self.actor_table.tree.item(selected_actor)["values"][1]
        self.database.spawned_actor_id = None
        self.controller.carla.change_actor_model(blueprint_id, actor_id)
        if wait_and_try(lambda: self.database.spawned_actor_id is not None):
            new_actor_id = self.database.spawned_actor_id
            values = list(self.actor_table.tree.item(selected_actor)["values"])
            values[1] = new_actor_id
            values[2] = selected_model
            self.actor_table.tree.item(selected_actor, values=values)
            self.database.spawned_actor_id = None

    def _update_location_from_spawn_point(self, event=None):
        selected_value = self.selected_spawn_point.get()
        if not selected_value:
            return
        spawn_point_number = int(selected_value.split(":", 1)[0])
        transform = self.database.available_spawn_points[spawn_point_number]
        x, y, z, yaw, pitch, roll = return_data_from_transform(transform)
        self.x_position_var.set(x)
        self.y_position_var.set(y)
        self.z_position_var.set(z)
        self.yaw_position_var.set(yaw)
        self.pitch_position_var.set(pitch)
        self.roll_position_var.set(roll)
        self.actor_preview.set_transform(transform)

    def update_spawn_point(self):
        self.available_spawn_points = [
            f"{index}: {point}" for index, point in enumerate(self.database.available_spawn_points)
        ]
        self.combobox_spawn_points["values"] = self.available_spawn_points
        if self.available_spawn_points:
            self.combobox_spawn_points.current(0)

    def _toggle_ghost_preview(self):
        enabled = bool(self.ghost_preview_enabled.get())
        self.actor_preview.set_enabled(enabled)
        if enabled:
            self.spawn_ghost()
        else:
            self.destroy_ghost()

    def is_ghost_preview_enabled(self):
        return bool(self.ghost_preview_enabled.get())

    def destroy_ghost(self):
        self.actor_preview.destroy()
        self.image_frame.update_image()

    def _on_model_change(self, event=None):
        selected_model = self.actor_model.get(self.choose_model.get())
        if selected_model is None:
            return
        if self.is_ghost_preview_enabled():
            transform = (
                self.actor_preview.get_transform() or self._current_transform()
            )
            self.destroy_ghost()
            self.spawn_ghost(blueprint_id=selected_model, transform=transform)

    def get_actor_info(self):
        transform = self._current_transform()
        return {
            "name": self.actor_name_var.get(),
            "carla_id": None,
            "type_id": self.choose_model.get(),
            "location": (
                f"{transform.location.x:.2f}, {transform.location.y:.2f}, {transform.location.z:.2f}"
            ),
            "rotation": (
                f"{transform.rotation.pitch:.2f}, {transform.rotation.yaw:.2f}, {transform.rotation.roll:.2f}"
            ),
            "sensors": "",
            "status": "Managed",
        }

    def spawn_actor(self):
        selected_model = self.choose_model.get()
        if selected_model not in self.actor_model:
            return
        data = {
            "name": self.actor_name_var.get(),
            "transform": self._current_transform(),
            "sensors": "",
            "blueprint_id": self.actor_model[selected_model],
        }
        actor_data = self.get_actor_info()
        self.destroy_ghost()
        self.database.spawned_actor_id = None
        self.controller.carla.spawn_actor(data)
        if wait_and_try(lambda: self.database.spawned_actor_id is not None):
            actor_data["carla_id"] = self.database.spawned_actor_id
            with self.database.lock:
                actor_info = self.database.actors_db.actors_db.get(
                    self.database.spawned_actor_id
                )
                if actor_info is not None:
                    actor_data["name"] = actor_info.user_name
            actor_data["status"] = "Managed"
            self.actor_table.add_actor_to_table(actor_data)
            self.database.spawned_actor_id = None
            self.reset_tab()
        else:
            print("Failed to spawn static object.")
        self.spawn_ghost()

    def remove_actor(self):
        selected_actor = self.actor_table.tree.selection()
        if selected_actor:
            actor_id = self.actor_table.tree.item(selected_actor)["values"][1]
            if self.controller.carla.remove_actor(actor_id):
                self.actor_table.tree.delete(selected_actor)

    def edit_actor(self):
        selected_model = self.actor_model.get(self.choose_model.get())
        if selected_model is None:
            return
        EntityEditWindow(
            self,
            selected_model,
            None,
            "static",
            self.database.blueprint_lib,
            None,
            self.apply_actor_changes,
            self.database.blueprint_new_data,
        )

    def apply_actor_changes(self, data):
        self.database.blueprint_new_data = data["blueprint"]
        if not self.is_ghost_preview_enabled():
            return True
        self.controller.carla.apply_new_physics_and_blueprint_to_ghost(
            data["physics"]
        )
        if wait_and_try(lambda: self.database.physics_applied is True):
            self.image_frame.update_image()
            self.database.physics_applied = False
        return True

    def reset_tab(self):
        self.actor_name_var.set("Ghost Static Object")
        if self.actor_model:
            self.actor_model_combobox.current(0)
        if self.database.available_spawn_points:
            transform = self.database.available_spawn_points[0]
            x, y, z, yaw, pitch, roll = return_data_from_transform(transform)
            self.x_position_var.set(x)
            self.y_position_var.set(y)
            self.z_position_var.set(z)
            self.yaw_position_var.set(yaw)
            self.pitch_position_var.set(pitch)
            self.roll_position_var.set(roll)
            self.combobox_spawn_points.current(0)

    def set_variables_from_table(self, event=None):
        selected_actor = self.actor_table.tree.selection()
        if not selected_actor:
            return
        values = self.actor_table.tree.item(selected_actor)["values"]
        self.actor_name_var.set(values[0])
        self.choose_model.set(values[2])
        location = [float(value) for value in str(values[3]).strip("{()}").split(",")]
        rotation = [float(value) for value in str(values[4]).strip("{()}").split(",")]
        self.x_position_var.set(location[0])
        self.y_position_var.set(location[1])
        self.z_position_var.set(location[2])
        self.pitch_position_var.set(rotation[0])
        self.yaw_position_var.set(rotation[1])
        self.roll_position_var.set(rotation[2])


class StaticObjectTable(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, style="new.TFrame")
        self.controller = controller
        self.database = controller.database

        ttk.Label(self, text="Static Objects", style="TLabel", font=("Arial", 18, "bold")).pack(
            pady=(10, 8), fill="x"
        )
        columns = ("Object Name", "ID", "Model", "Location", "Rotation", "Sensors", "Status")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=18, style="Treeview")
        for column in columns:
            self.tree.heading(column, text="Type ID" if column == "Model" else column)
        self.tree.column("Object Name", width=240, minwidth=180, anchor="center")
        self.tree.column("ID", width=40, minwidth=40, anchor="center")
        self.tree.column("Model", width=240, minwidth=220, anchor="center")
        self.tree.column("Location", width=170, minwidth=140, anchor="center")
        self.tree.column("Rotation", width=170, minwidth=140, anchor="center")
        self.tree.column("Sensors", width=80, minwidth=80, anchor="center")
        self.tree.column("Status", width=80, minwidth=80, anchor="center")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.edit_cell)
        self.initialize_data()

    def edit_cell(self, event):
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        column = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if not row or column != "#1":
            return
        bounds = self.tree.bbox(row, column)
        if not bounds:
            return
        entry = ttk.Entry(self.tree)
        entry.insert(0, self.tree.set(row, column))
        entry.place(x=bounds[0], y=bounds[1], width=bounds[2], height=bounds[3])
        entry.focus()
        entry.select_range(0, tk.END)

        def save(event=None):
            actor_id = int(self.tree.item(row)["values"][1])
            try:
                new_value = self.database.rename_actor(
                    actor_id,
                    entry.get(),
                )
            except (KeyError, ValueError) as error:
                messagebox.showerror(
                    "Rename static object",
                    str(error),
                    parent=self,
                )
                entry.destroy()
                return
            self.tree.set(row, column, new_value)
            entry.destroy()

        entry.bind("<Return>", save)
        entry.bind("<FocusOut>", save)
        entry.bind("<Escape>", lambda event: entry.destroy())

    def add_actor_to_table(self, actor_info):
        self.tree.insert(
            "",
            "end",
            values=(
                actor_info["name"],
                actor_info["carla_id"],
                actor_info["type_id"],
                f"({actor_info['location']})",
                f"({actor_info['rotation']})",
                actor_sensor_status(self.database, actor_info["carla_id"]),
                actor_info.get("status", actor_info.get("is_active", "Unknown")),
            ),
        )

    def remove_actor_from_table(self, actor_info):
        actor_id = actor_info["carla_id"]
        remove_tree_item_by_runtime_id(self.tree, actor_id)

    def update_actor_in_table(self, actor_info):
        actor_id = actor_info["carla_id"]
        item = find_tree_item_by_runtime_id(self.tree, actor_id)
        if item is not None:
            self.tree.item(
                item,
                values=(
                    actor_info["name"],
                    actor_id,
                    actor_info["type_id"],
                    f"({actor_info['location']})",
                    f"({actor_info['rotation']})",
                    actor_sensor_status(self.database, actor_id),
                    actor_info.get("status", actor_info.get("is_active", "Unknown")),
                ),
            )

    def initialize_data(self):
        for actor_info in self.database.actors_db.actors_db.values():
            if actor_info.type_id.startswith("static."):
                location = actor_info.location
                transform = actor_info.transform
                self.add_actor_to_table(
                    {
                        "name": actor_info.user_name,
                        "carla_id": actor_info.carla_id,
                        "type_id": actor_info.type_id,
                        "location": (
                            f"{location.x:.2f}, {location.y:.2f}, {location.z:.2f}"
                            if location is not None else "Unknown"
                        ),
                        "rotation": (
                            f"{transform.rotation.pitch:.2f}, "
                            f"{transform.rotation.yaw:.2f}, "
                            f"{transform.rotation.roll:.2f}"
                            if transform is not None else "Unknown"
                        ),
                        "sensors": "",
                        "status": actor_ownership_status(actor_info),
                    }
                )

    def refresh_table(self):
        clear_tree(self.tree)
        self.initialize_data()
