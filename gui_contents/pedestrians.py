import tkinter as tk
from tkinter import messagebox, ttk

from utilities.functions import (
    filter_carla_id_by_type,
    get_pedestrian_blueprint_ids,
    pedestrian_blueprint_options,
    resolve_blueprint_selection,
    return_data_from_transform,
    select_available_blueprint_id,
    wait_and_try,
)
from gui_contents.spawn_preview import ActorPreviewFrame, GhostPreview
from gui_contents.ActorEditView import EntityEditWindow
from gui_contents.change_model_top import ChangeModelTop
from gui_contents.set_transform_top import ChangeTransformTop
import carla


class CustomPedestrianFrame(tk.Frame):
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

        self._pedestrian_variables_definition()
        self._pedestrian_frame_setup()

    def spawn_ghost(self, blueprint_id=None, transform=None):
        if blueprint_id is None:
            try:
                blueprint_id = self._selected_blueprint_id()
            except ValueError:
                return
        self.actor_preview.spawn(blueprint_id=blueprint_id, transform=transform)

    def _pedestrian_variables_definition(self):
        self.actor_name_var = tk.StringVar(value="Ghost Pedestrian")
        self.selected_actor = tk.StringVar()
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

        self.choose_model = tk.StringVar()
        self.actor_model = {}

    def _available_pedestrian_blueprint_ids(self):
        blueprint_ids = list(
            getattr(self.database, "pedestrian_blueprint_ids", [])
        )
        if not blueprint_ids:
            blueprint_ids = get_pedestrian_blueprint_ids(
                self.database.blueprint_lib
            )
        return blueprint_ids

    def _selected_blueprint_id(self):
        return resolve_blueprint_selection(
            self.actor_model,
            self.choose_model.get(),
        )

    def _refresh_blueprint_models(self, preserve_selection=True):
        previous_blueprint_id = None
        if preserve_selection:
            try:
                previous_blueprint_id = self._selected_blueprint_id()
            except ValueError:
                selected_value = self.choose_model.get()
                if str(selected_value).startswith("walker.pedestrian."):
                    previous_blueprint_id = selected_value

        blueprint_ids = self._available_pedestrian_blueprint_ids()
        self.actor_model = pedestrian_blueprint_options(blueprint_ids)
        self.actor_model_combobox["values"] = list(self.actor_model.keys())
        selected_id = select_available_blueprint_id(
            blueprint_ids,
            previous_blueprint_id,
        )
        self.choose_model.set(selected_id)
        return selected_id

    def _pedestrian_frame_setup(self):
        upper_frame = ttk.LabelFrame(
            self,
            text="Spawn Pedestrians",
            style="Section.TLabelframe",
            padding=(12, 8)
        )
        lower_frame = ttk.LabelFrame(
            self,
            text="Pedestrian Management",
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

        self.image_frame = ActorPreviewFrame(upper_frame, self.actor_preview)
        self.image_frame.grid(row=0, column=2, sticky="n", padx=(8, 12), pady=(8, 8))

        location_frame = ttk.Frame(upper_frame, style="new.TFrame")
        location_frame.grid(row=0, column=1, sticky="ew", padx=8, pady=(8, 8))

        button_frame = ttk.Frame(upper_frame, style="new.TFrame")
        button_frame.grid(row=1, column=0, columnspan=3, sticky="n", padx=(0, 12), pady=(0, 8))


        # ================================= Heading ======================================

        heading_frame.grid_columnconfigure(0, weight=1)
        heading_frame.grid_columnconfigure(1, weight=1)

        heading_label = tk.Label(heading_frame, text="Pedestrians spawn menu", bg=self.default_color, fg=self.text_color, font=("Arial", 18, "bold"))
        heading_label.grid( row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        # ================================= Pedestrian name =================================

        vehicle_name_label = ttk.Label(heading_frame, text="Pedestrian Name", background=self.default_color, font=("Arial", 14, "bold"))
        vehicle_name_label.grid( row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        vehicle_name_entry = ttk.Entry(heading_frame, textvariable=self.actor_name_var)
        vehicle_name_entry.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 14))

        # ================================= Class + Model =================================

        model_frame = ttk.Frame(heading_frame, style="new.TFrame")
        model_frame.grid(row=3, column=0, sticky="ew", padx=(6, 0))

        model_frame.grid_columnconfigure(0, weight=1)


        # Model
        model_label = ttk.Label(model_frame, text="Model", background=self.default_color, font=("Arial", 11, "bold"))
        model_label.grid(row=0, column=0, sticky="w", pady=(0, 5))

        self.actor_model_combobox = ttk.Combobox(model_frame, textvariable=self.choose_model, state="readonly")
        self._refresh_blueprint_models(preserve_selection=False)
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

        self.actor_table = ActorTable(lower_frame, self.controller)
        self.actor_table.grid(row=0, column=0, sticky="nsew", padx=20, pady=(10, 10))

        button_control_frame = ttk.Frame(lower_frame, style="new.TFrame")
        button_control_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 12))
        for index in range(5):
            button_control_frame.grid_columnconfigure(index, weight=1)

        #==================================== Buttons ===================================
        spawn_button = ttk.Button(button_frame, text="Spawn Pedestrian", command=self.spawn_actor, width=18)
        spawn_button.grid(row=0, column=0, sticky="ew", pady=(0, 8), padx=(0, 10))
        edit_button = ttk.Button(button_frame, text="Edit", command=self.edit_actor, width=18)
        edit_button.grid(row=0, column=1, sticky="ew", pady=(0, 8), padx=(0, 10))
        remove_button = ttk.Button(button_control_frame, text="Remove", command=self.remove_actor, width=18)
        remove_button.grid(row=0, column=0, sticky="ew", pady=(0, 8), padx=(0, 10))
        change_model_button = ttk.Button(button_control_frame, text="Change Model", command=self.change_model, width=18)
        change_model_button.grid(row=0, column=1, sticky="ew", pady=(0, 8), padx=(0, 10))
        update_location_button = ttk.Button(button_control_frame, text="Update Location", command=self.update_location, width=18)
        update_location_button.grid(row=0, column=2, sticky="ew", pady=(0, 8), padx=(0, 10))
        copy_button = ttk.Button(button_control_frame, text="Copy Pedestrian", command=self.copy_actor, width=18)
        copy_button.grid(row=0, column=3, sticky="ew", pady=(0, 8), padx=(0, 10))
        movement_button = ttk.Button(
            button_control_frame,
            text="Movement Control",
            command=self.open_movement_control,
            width=18,
        )
        movement_button.grid(row=0, column=4, sticky="ew", pady=(0, 8))

    def open_movement_control(self):
        selected_actor = self.actor_table.tree.selection()
        if not selected_actor:
            return
        actor_id = self.actor_table.tree.item(selected_actor[0])["values"][1]
        self.controller.open_movement_control(actor_id)

    def update_location(self, event=None):
        if not self.actor_table.tree.selection():
            return
        selected_actor = self.actor_table.tree.selection()
        actor_id = self.actor_table.tree.item(selected_actor)["values"][1]
        current_transform = self.database.actors_db.actors_db[actor_id].transform
        top = ChangeTransformTop(self, self.database.world.get_map().get_spawn_points(), current_transform, self.set_new_transform)

    def set_new_transform(self, transform, event=None):
        print(transform)
        selected_actor = self.actor_table.tree.selection()
        if not selected_actor:
            return

        selected_actor_id = self.actor_table.tree.item(selected_actor)["values"][1]
        self.controller.carla.set_actor_transform(transform, selected_actor_id)
        self.actor_table.tree.item(selected_actor, values=(
            self.actor_table.tree.item(selected_actor)["values"][0],
            self.actor_table.tree.item(selected_actor)["values"][1],
            self.actor_table.tree.item(selected_actor)["values"][2],
            f"({transform.location.x:.2f}, {transform.location.y:.2f}, {transform.location.z:.2f})",
            f"({transform.rotation.yaw:.2f}, {transform.rotation.pitch:.2f}, {transform.rotation.roll:.2f})",
            self.actor_table.tree.item(selected_actor)["values"][5],
            self.actor_table.tree.item(selected_actor)["values"][6]
        ))

    def copy_actor(self, event=None):
        pass

    def change_model(self, event=None):
        if not self.actor_table.tree.selection():
            return
        top = ChangeModelTop(self, "walker", self.database.blueprint_lib, self.change_spawned_actor_model)

    def change_spawned_actor_model(self, selected_class, selected_model, event=None):
        selected_actor = self.actor_table.tree.selection()
        if not selected_actor:
            return
        actor_model = filter_carla_id_by_type(
            "walker",
            selected_class,
            self.database.blueprint_lib,
        )
        try:
            blueprint_id = resolve_blueprint_selection(
                actor_model,
                selected_model,
            )
        except ValueError as error:
            messagebox.showerror("Change Pedestrian Model", str(error))
            return
        selected_actor_id = self.actor_table.tree.item(selected_actor)["values"][1]
        self.controller.close_movement_control_for_actor(
            selected_actor_id,
            "Selected pedestrian is being replaced",
        )
        with self.database.lock:
            self.database.spawned_actor_id = None
            self.database.actor_operation_result = None
        self.controller.carla.change_actor_model(
            blueprint_id,
            selected_actor_id,
        )

        is_changed = wait_and_try(
            lambda: self.database.actor_operation_result is not None
        )
        with self.database.lock:
            result = self.database.actor_operation_result
        if not is_changed or result is None:
            messagebox.showerror(
                "Change Pedestrian Model",
                "Timed out while changing the pedestrian model.",
            )
            return
        if not result["success"]:
            messagebox.showerror(
                "Change Pedestrian Model",
                result["error"],
            )
            self.actor_table.refresh_table()
            return

        try:
            if (
                result["requested_blueprint_id"] != blueprint_id
                or result["actual_type_id"] != blueprint_id
            ):
                raise RuntimeError(
                    "Pedestrian model verification failed after replacement."
                )
            actor_id = result["actor_id"]
            with self.database.lock:
                actor = self.database.actors_db.actors[actor_id]
            if actor.type_id != blueprint_id:
                raise RuntimeError(
                    "DataStorage contains a pedestrian with an unexpected "
                    f"type: '{actor.type_id}'."
                )
            self.actor_table.tree.item(selected_actor, values=(
                self.actor_table.tree.item(selected_actor)["values"][0],
                actor_id,
                actor.type_id,
                self.actor_table.tree.item(selected_actor)["values"][3],
                self.actor_table.tree.item(selected_actor)["values"][4],
                self.actor_table.tree.item(selected_actor)["values"][5],
                self.actor_table.tree.item(selected_actor)["values"][6]
            ))
            with self.database.lock:
                self.database.spawned_actor_id = None
        except (KeyError, RuntimeError) as error:
            messagebox.showerror("Change Pedestrian Model", str(error))
            self.actor_table.refresh_table()

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

        if self.actor_preview:
            self.actor_preview.set_transform(transform)

    def update_spawn_point(self):
        self.combobox_spawn_points["values"] = [f"{i}:{point}" for i, point in enumerate(self.database.available_spawn_points)]
        if self.database.available_spawn_points:
            self.combobox_spawn_points.current(0)
        else:
            self.selected_spawn_point.set("")
        self._refresh_blueprint_models(preserve_selection=True)

    def destroy_ghost(self):
        if self.actor_preview:
            self.actor_preview.destroy()
            self.image_frame.update_image()

    def _on_model_change(self, event):
        try:
            selected_model = self._selected_blueprint_id()
        except ValueError as error:
            messagebox.showerror("Pedestrian Model", str(error))
            return
        if self.actor_preview:
            transform = self.actor_preview.get_transform()
            self.destroy_ghost()
            self.spawn_ghost(blueprint_id=selected_model, transform=transform)

    def get_actor_info(self):
        actor_name = self.actor_name_var.get()
        x_pos = self.x_position_var.get()
        y_pos = self.y_position_var.get()
        z_pos = self.z_position_var.get()
        yaw_pos = self.yaw_position_var.get()
        pitch_pos = self.pitch_position_var.get()
        roll_pos = self.roll_position_var.get()
        actor_info = {
            "name": actor_name,
            "carla_id": None,
            "type_id": self._selected_blueprint_id(),
            "location": f"{x_pos:.2f}, {y_pos:.2f}, {z_pos:.2f}",
            "rotation": f"{pitch_pos:.2f}, {yaw_pos:.2f}, {roll_pos:.2f}",
            "sensors": "",  # TODO: Implement sensor information retrieval when the sensor management is implemented
            "is_active": "Pending",
        }
        return actor_info

    def spawn_actor(self):
        try:
            blueprint_id = self._selected_blueprint_id()
        except ValueError as error:
            messagebox.showerror("Spawn Pedestrian", str(error))
            return

        actor_name = self.actor_name_var.get()
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
        data = {
            "name": actor_name,
            "transform": transform,
            "sensors": "",  # TODO: Implement sensor information retrieval.
            "blueprint_id": blueprint_id,
        }

        actor_data = self.get_actor_info()
        self.destroy_ghost()
        with self.database.lock:
            self.database.spawned_actor_id = None
            self.database.actor_operation_result = None
        self.controller.carla.spawn_actor(data)

        is_spawned = wait_and_try(
            lambda: self.database.actor_operation_result is not None
        )
        with self.database.lock:
            result = self.database.actor_operation_result
        if not is_spawned or result is None:
            messagebox.showerror(
                "Spawn Pedestrian",
                "Timed out while spawning the pedestrian.",
            )
        elif not result["success"]:
            messagebox.showerror("Spawn Pedestrian", result["error"])
        elif (
            result["requested_blueprint_id"] != blueprint_id
            or result["actual_type_id"] != blueprint_id
        ):
            messagebox.showerror(
                "Spawn Pedestrian",
                "Pedestrian blueprint verification failed after spawn.",
            )
        else:
            actor_id = result["actor_id"]
            with self.database.lock:
                actor = self.database.actors_db.actors.get(actor_id)
            if actor is None or actor.type_id != blueprint_id:
                messagebox.showerror(
                    "Spawn Pedestrian",
                    "DataStorage does not contain the verified pedestrian.",
                )
            else:
                actor_data["carla_id"] = actor_id
                actor_data["type_id"] = actor.type_id
                actor_data["is_active"] = "Active"
                self.actor_table.add_actor_to_table(actor_data)
                with self.database.lock:
                    self.database.spawned_actor_id = None
                self.reset_tab()

        self.spawn_ghost()





    def remove_actor(self):
        selected_actor = self.actor_table.tree.selection()
        if selected_actor:
            actor_id = self.actor_table.tree.item(selected_actor)["values"][1]
            self.controller.close_movement_control_for_actor(
                actor_id,
                "Selected pedestrian removed",
            )
            self.controller.carla.remove_actor(actor_id)
            self.actor_table.tree.delete(selected_actor)

    def edit_actor(self):
        try:
            selected_model = self._selected_blueprint_id()
        except ValueError:
            return
        EntityEditWindow(
            self,
            selected_model,
            None,
            "walker",
            self.database.blueprint_lib,
            None,
            self.apply_actor_changes,
            self.database.blueprint_new_data,
        )

    def apply_actor_changes(self, data):
        self.database.blueprint_new_data = data["blueprint"]
        self.controller.carla.apply_new_physics_and_blueprint_to_ghost(data["physics"])
        if wait_and_try(lambda: self.database.physics_applied is True):
            self.image_frame.update_image()
            self.database.physics_applied = False

    def reset_tab(self):
        self.actor_name_var.set("Ghost Pedestrian")
        self.x_position_var.set(self.database.available_spawn_points[0].location.x if self.database.available_spawn_points else 0)
        self.y_position_var.set(self.database.available_spawn_points[0].location.y if self.database.available_spawn_points else 0)
        self.z_position_var.set(self.database.available_spawn_points[0].location.z if self.database.available_spawn_points else 0)
        self.yaw_position_var.set(self.database.available_spawn_points[0].rotation.yaw if self.database.available_spawn_points else 0)
        self.pitch_position_var.set(self.database.available_spawn_points[0].rotation.pitch if self.database.available_spawn_points else 0)
        self.roll_position_var.set(self.database.available_spawn_points[0].rotation.roll if self.database.available_spawn_points else 0)
        self.combobox_spawn_points.current(0)

    def set_variables_from_table(self, event):
        selected_actor = self.actor_table.tree.selection()
        if selected_actor:
            actor_data = self.actor_table.tree.item(selected_actor)["values"]
            self.actor_name_var.set(actor_data[0])
            self.choose_model.set(actor_data[2])
            x_location = float(actor_data[3].strip("{()}").split(",")[0])
            y_location = float(actor_data[3].strip("{()}").split(",")[1])
            z_location = float(actor_data[3].strip("{()}").split(",")[2])
            yaw_rotation = float(actor_data[4].strip("{()}").split(",")[1])
            pitch_rotation = float(actor_data[4].strip("{()}").split(",")[0])
            roll_rotation = float(actor_data[4].strip("{()}").split(",")[2])
            self.x_position_var.set(x_location)
            self.y_position_var.set(y_location)
            self.z_position_var.set(z_location)
            self.yaw_position_var.set(yaw_rotation)
            self.pitch_position_var.set(pitch_rotation)
            self.roll_position_var.set(roll_rotation)



class ActorTable(ttk.Frame):
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
            text="Pedestrians",
            style="TLabel",
            font=("Arial", 18, "bold")
        )
        title.pack(pady=(10, 8), fill="x")

        columns = (
            "Pedestrian Name",
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

        self.tree.heading("Pedestrian Name", text="Pedestrian Name")
        self.tree.heading("ID", text="ID")
        self.tree.heading("Model", text="Type ID")
        self.tree.heading("Location", text="Location")
        self.tree.heading("Rotation", text="Rotation")
        self.tree.heading("Sensors", text="Sensors")
        self.tree.heading("Status", text="Status")

        self.tree.column("Pedestrian Name", width=240, minwidth=180, anchor="center")
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
            actor_id = self.tree.item(row)["values"][1]
            self.controller.database.actors_db.actors_db[actor_id].user_name = new_value
            entry.destroy()

        entry.bind("<Return>", save)
        entry.bind("<FocusOut>", save)
        entry.bind("<Escape>", lambda event: entry.destroy())

    def add_actor_to_table(self, actor_info):
        name = actor_info["name"]
        actor_id = actor_info["carla_id"]
        model = actor_info["type_id"]
        location = f"({actor_info['location']})"
        rotation = f"({actor_info['rotation']})"
        sensors = ""    #TODO: Implement sensor information retrieval when the sensor management is implemented
        status = actor_info["is_active"]
        self.tree.insert("", "end", values=(name, actor_id, model, location, rotation, sensors, status))

    def remove_actor_from_table(self, actor_info):
        actor_id = actor_info["carla_id"]
        for item in self.tree.get_children():
            if self.tree.item(item)["values"][1] == actor_id:
                self.tree.delete(item)
                break

    def update_actor_in_table(self, actor_info):
        actor_id = actor_info["carla_id"]
        for item in self.tree.get_children():
            if self.tree.item(item)["values"][1] == actor_id:
                name = actor_info["name"]
                model = actor_info["type_id"]
                location = f"({actor_info['location']})",
                rotation = f"({actor_info['rotation']})",
                sensors = ""    #TODO: Implement sensor information retrieval when the sensor management is implemented
                status = actor_info["is_active"]
                self.tree.item(item, values=(name, actor_id, model, location, rotation, sensors, status))
                break

    def initialize_data(self):
        for actor_id, actor_info in self.database.actors_db.actors_db.items():
            if actor_info.type_id.startswith("walker.pedestrian."):
                pedestrian_info = {
                    "name": actor_info.user_name,
                    "carla_id": actor_info.carla_id,
                    "type_id": actor_info.type_id,
                    "location": f"({actor_info.location.x:.2f}, {actor_info.location.y:.2f}, {actor_info.location.z:.2f})",
                    "rotation": f"({actor_info.transform.rotation.pitch:.2f}, {actor_info.transform.rotation.yaw:.2f}, {actor_info.transform.rotation.roll:.2f})",
                    "sensors": "",  # TODO: Implement sensor information retrieval when the sensor management is implemented
                    "is_active": ("Active" if actor_info.is_alive else "Inactive")
                }
                self.add_actor_to_table(pedestrian_info)

    def refresh_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.initialize_data()
