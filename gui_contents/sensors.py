import queue
import tkinter as tk
from tkinter import messagebox, ttk

import carla
from gui_contents.color_picker import ColorPicker
from gui_contents.sensors_preview import (
    EVENT_SENSOR_TYPES,
    IMAGE_SENSOR_TYPES,
    SPATIAL_SENSOR_TYPES,
    VALUE_SENSOR_TYPES,
    SensorEventsPreviewTop,
    SensorPreviewTop,
    SensorValuesPreviewTop,
    SpatialSensorPreviewTop
)
from utilities.functions import actor_ownership_status, hex_to_rgb, rgb_to_hex


class CustomSensorFrame(tk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.database = controller.database
        self.event_check = None
        self.preview_windows = {}

        self._sensor_frame_setup()
        self.refresh_table()
        self.check_sensor_events()

    def _sensor_frame_setup(self):
        sensor_frame = ttk.LabelFrame(
            self,
            text="Sensor Management",
            style="Section.TLabelframe",
            padding=(12, 8)
        )
        sensor_frame.pack(fill="both", expand=True, padx=18, pady=(6, 12))
        sensor_frame.grid_columnconfigure(0, weight=1)
        sensor_frame.grid_rowconfigure(0, weight=1)

        columns = (
            "Name",
            "ID",
            "Type",
            "Parent",
            "Transform",
            "Status",
            "Ownership",
        )
        self.tree = ttk.Treeview(
            sensor_frame,
            columns=columns,
            show="headings",
            height=22,
            style="Treeview"
        )
        for column in columns:
            self.tree.heading(column, text=column)

        self.tree.column("Name", width=180, minwidth=120, anchor="center")
        self.tree.column("ID", width=60, minwidth=50, anchor="center")
        self.tree.column("Type", width=260, minwidth=180, anchor="center")
        self.tree.column("Parent", width=220, minwidth=150, anchor="center")
        self.tree.column("Transform", width=360, minwidth=260, anchor="center")
        self.tree.column("Status", width=90, minwidth=80, anchor="center")
        self.tree.column("Ownership", width=100, minwidth=90, anchor="center")

        scrollbar = ttk.Scrollbar(sensor_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Double-1>", self.edit_name)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        scrollbar.grid(row=0, column=1, sticky="ns", pady=8)

        button_frame = ttk.Frame(sensor_frame, style="new.TFrame")
        button_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        ttk.Button(button_frame, text="Add Sensor", command=self.add_sensor, width=18).pack(
            side="left", padx=(0, 10)
        )
        ttk.Button(button_frame, text="Remove Sensor", command=self.remove_sensor, width=18).pack(
            side="left", padx=(0, 10)
        )
        ttk.Button(
            button_frame,
            text="Change Transform",
            command=self.change_transform,
            width=18
        ).pack(
            side="left", padx=(0, 10)
        )
        ttk.Button(
            button_frame,
            text="Preview",
            command=self.preview_sensor,
            width=18
        ).pack(
            side="left"
        )

    def edit_name(self, event):
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
            new_name = entry.get().strip()
            if not new_name:
                messagebox.showerror(
                    "Invalid sensor name",
                    "Sensor name cannot be empty.",
                    parent=self,
                )
                entry.focus()
                return
            sensor_id = int(self.tree.item(row)["values"][1])
            try:
                new_name = self.database.rename_sensor(sensor_id, new_name)
            except (KeyError, ValueError) as error:
                messagebox.showerror(
                    "Rename sensor",
                    str(error),
                    parent=self,
                )
                entry.destroy()
                return
            self.tree.set(row, column, new_name)
            preview_window = self.preview_windows.get(sensor_id)
            if preview_window is not None and preview_window.winfo_exists():
                preview_window.update_display_name(new_name)
            entry.destroy()

        entry.bind("<Return>", save)
        entry.bind("<FocusOut>", save)
        entry.bind("<Escape>", lambda event: entry.destroy())

    def add_sensor(self, parent_id=None):
        AddSensorTop(self, self.controller, parent_id)

    def remove_sensor(self):
        selected_sensor = self.tree.selection()
        if not selected_sensor:
            return
        sensor_id = int(self.tree.item(selected_sensor[0])["values"][1])
        self.controller.carla.remove_sensor(sensor_id)

    def change_transform(self):
        selected_sensor = self.tree.selection()
        if not selected_sensor:
            return
        sensor_id = int(self.tree.item(selected_sensor[0])["values"][1])
        with self.database.lock:
            sensor_info = self.database.sensors_db.sensors_db.get(sensor_id)
            if sensor_info is None:
                return
            current_transform = sensor_info.relative_transform
        ChangeSensorTransformTop(
            self,
            current_transform,
            lambda transform: self.controller.carla.change_sensor_transform(
                sensor_id,
                transform
            )
        )

    def preview_sensor(self):
        selected_sensor = self.tree.selection()
        if not selected_sensor:
            return
        sensor_id = int(self.tree.item(selected_sensor[0])["values"][1])
        with self.database.lock:
            sensor_info = self.database.sensors_db.sensors_db.get(sensor_id)
            if sensor_info is None:
                return
            sensor_name = sensor_info.user_name
            blueprint_id = sensor_info.blueprint_id

        supported_sensor_types = (
            IMAGE_SENSOR_TYPES + VALUE_SENSOR_TYPES + EVENT_SENSOR_TYPES +
            SPATIAL_SENSOR_TYPES
        )
        if blueprint_id not in supported_sensor_types:
            if blueprint_id == "sensor.other.rss":
                message = (
                    "RSS preview is unavailable because this CARLA Python client "
                    "was built without RSS support."
                )
            else:
                message = "Preview is not available for this sensor type."
            messagebox.showinfo(
                "Preview unavailable",
                message,
                parent=self
            )
            return

        preview_window = self.preview_windows.get(sensor_id)
        if preview_window is not None and preview_window.winfo_exists():
            preview_window.lift()
            preview_window.focus_force()
            return

        if blueprint_id in IMAGE_SENSOR_TYPES:
            preview_class = SensorPreviewTop
        elif blueprint_id in VALUE_SENSOR_TYPES:
            preview_class = SensorValuesPreviewTop
        elif blueprint_id in SPATIAL_SENSOR_TYPES:
            preview_class = SpatialSensorPreviewTop
        else:
            preview_class = SensorEventsPreviewTop

        preview_window = preview_class(
            self,
            self.controller,
            sensor_id,
            sensor_name,
            blueprint_id,
            lambda: self.preview_windows.pop(sensor_id, None)
        )
        self.preview_windows[sensor_id] = preview_window

    def close_sensor_preview(self, sensor_id):
        preview_window = self.preview_windows.get(sensor_id)
        if preview_window is not None:
            preview_window.close()

    def close_all_sensor_previews(self):
        for preview_window in list(self.preview_windows.values()):
            preview_window.close()

    def refresh_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        with self.database.lock:
            sensors = list(self.database.sensors_db.sensors_db.values())
            actors = self.database.actors_db.actors_db
            for sensor_info in sensors:
                transform = sensor_info.relative_transform
                parent_info = actors.get(sensor_info.parent_id)
                if parent_info is None:
                    parent = str(sensor_info.parent_id)
                else:
                    parent = f"{parent_info.user_name} [{sensor_info.parent_id}]"
                status = "Active" if sensor_info.is_alive else "Inactive"
                ownership = actor_ownership_status(sensor_info)
                if transform is None:
                    transform_text = "Unknown"
                else:
                    transform_text = (
                        f"({transform.location.x:.2f}, {transform.location.y:.2f}, "
                        f"{transform.location.z:.2f}) / "
                        f"({transform.rotation.pitch:.2f}, "
                        f"{transform.rotation.yaw:.2f}, "
                        f"{transform.rotation.roll:.2f})"
                    )
                self.tree.insert("", "end", values=(
                    sensor_info.user_name,
                    sensor_info.carla_id,
                    sensor_info.blueprint_id,
                    parent,
                    transform_text,
                    status,
                    ownership,
                ))

    def check_sensor_events(self):
        try:
            while True:
                status, data = self.database.sensor_event_queue.get_nowait()
                if status == "sensor_removed":
                    self.close_sensor_preview(data)
                elif status == "sensors_cleared":
                    if data is None:
                        self.close_all_sensor_previews()
                    else:
                        for sensor_id in data:
                            self.close_sensor_preview(sensor_id)
                elif status == "sensors_replaced":
                    for old_sensor_id in data:
                        self.close_sensor_preview(old_sensor_id)
                if status in (
                    "sensor_attached",
                    "sensor_removed",
                    "sensor_transform_changed",
                    "sensors_cleared",
                    "sensors_replaced",
                ):
                    self.controller.refresh_actor_tables()
                elif status == "sensor_error":
                    messagebox.showerror("Sensor error", data, parent=self)
        except queue.Empty:
            pass
        self.event_check = self.after(200, self.check_sensor_events)

    def destroy(self):
        self.close_all_sensor_previews()
        if self.event_check is not None:
            self.after_cancel(self.event_check)
            self.event_check = None
        super().destroy()


class ChangeSensorTransformTop(tk.Toplevel):
    def __init__(self, parent, current_transform, on_transform_change_callback):
        super().__init__(parent)
        self.transform_change_callback = on_transform_change_callback
        self.default_color = "#F7E7D3"

        self.title("Change Sensor Transform")
        self.geometry("520x300")
        self.resizable(False, False)
        self.configure(bg=self.default_color)

        self.transform_vars = {
            "x": tk.DoubleVar(value=current_transform.location.x),
            "y": tk.DoubleVar(value=current_transform.location.y),
            "z": tk.DoubleVar(value=current_transform.location.z),
            "pitch": tk.DoubleVar(value=current_transform.rotation.pitch),
            "yaw": tk.DoubleVar(value=current_transform.rotation.yaw),
            "roll": tk.DoubleVar(value=current_transform.rotation.roll)
        }

        self._create_widgets()
        self.transient(parent.winfo_toplevel())
        self.grab_set()

    def _create_widgets(self):
        transform_frame = ttk.LabelFrame(
            self,
            text="Relative Transform",
            style="Section.TLabelframe",
            padding=(12, 8)
        )
        transform_frame.pack(fill="both", expand=True, padx=18, pady=(14, 8))

        fields = (("X", "x"), ("Y", "y"), ("Z", "z"),
                  ("Pitch", "pitch"), ("Yaw", "yaw"), ("Roll", "roll"))
        for index, (label, key) in enumerate(fields):
            row = index // 3
            column = (index % 3) * 2
            ttk.Label(transform_frame, text=label).grid(
                row=row * 2, column=column, sticky="w", padx=(8, 4), pady=(8, 2)
            )
            ttk.Spinbox(
                transform_frame,
                from_=-1e6,
                to=1e6,
                increment=0.01,
                textvariable=self.transform_vars[key],
                width=14
            ).grid(
                row=row * 2 + 1,
                column=column,
                columnspan=2,
                sticky="ew",
                padx=(8, 12),
                pady=(0, 8)
            )
            transform_frame.grid_columnconfigure(column, weight=1)

        button_frame = ttk.Frame(self, style="new.TFrame")
        button_frame.pack(fill="x", padx=18, pady=(0, 14))
        ttk.Button(button_frame, text="Apply", command=self.apply).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(button_frame, text="Cancel", command=self.destroy).pack(side="right")

    def apply(self):
        try:
            transform = carla.Transform(
                carla.Location(
                    x=float(self.transform_vars["x"].get()),
                    y=float(self.transform_vars["y"].get()),
                    z=float(self.transform_vars["z"].get())
                ),
                carla.Rotation(
                    pitch=float(self.transform_vars["pitch"].get()),
                    yaw=float(self.transform_vars["yaw"].get()),
                    roll=float(self.transform_vars["roll"].get())
                )
            )
        except (tk.TclError, ValueError):
            messagebox.showerror(
                "Invalid transform",
                "Transform values must be numbers.",
                parent=self
            )
            return

        self.transform_change_callback(transform)
        self.destroy()


class AddSensorTop(tk.Toplevel):
    def __init__(self, parent, controller, parent_id=None):
        super().__init__(parent)
        self.controller = controller
        self.database = controller.database
        self.parent_id = parent_id
        self.parent_values = {}
        self.attribute_vars = {}

        self.title("Add Sensor")
        self.geometry("900x760")
        self.minsize(760, 620)
        self.configure(bg="#F7E7D3")

        self.sensor_name_var = tk.StringVar(value="Sensor")
        self.parent_var = tk.StringVar()
        self.blueprint_var = tk.StringVar()
        self.transform_vars = {
            "x": tk.DoubleVar(value=0.0),
            "y": tk.DoubleVar(value=0.0),
            "z": tk.DoubleVar(value=0.0),
            "pitch": tk.DoubleVar(value=0.0),
            "yaw": tk.DoubleVar(value=0.0),
            "roll": tk.DoubleVar(value=0.0)
        }

        self._create_widgets()
        self.transient(parent.winfo_toplevel())
        self.grab_set()

    def _create_widgets(self):
        canvas = tk.Canvas(self, background="#F7E7D3", highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        self.content_frame = ttk.Frame(canvas, style="new.TFrame")
        content_window = canvas.create_window((0, 0), window=self.content_frame, anchor="nw")

        self.content_frame.bind(
            "<Configure>",
            lambda event: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind(
            "<Configure>",
            lambda event: (
                canvas.itemconfigure(content_window, width=event.width),
                canvas.configure(scrollregion=canvas.bbox("all"))
            )
        )
        self.bind("<MouseWheel>", lambda event: canvas.yview_scroll(
            int(-event.delta / 120), "units"
        ))
        canvas.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=12)
        scrollbar.pack(side="right", fill="y", pady=12, padx=(0, 12))

        basic_frame = ttk.LabelFrame(
            self.content_frame, text="Sensor", style="Section.TLabelframe", padding=(12, 8)
        )
        basic_frame.pack(fill="x", padx=8, pady=(4, 8))
        basic_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(basic_frame, text="Name").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(basic_frame, textvariable=self.sensor_name_var).grid(
            row=0, column=1, sticky="ew", padx=8, pady=6
        )

        with self.database.lock:
            actor_info_list = [
                actor_info
                for actor_id, actor_info in self.database.actors_db.actors_db.items()
                if actor_id in self.database.actors_db.actors
            ]
        for actor_info in actor_info_list:
            value = f"{actor_info.carla_id}: {actor_info.user_name} ({actor_info.type_id})"
            self.parent_values[value] = actor_info.carla_id

        ttk.Label(basic_frame, text="Parent").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        parent_combobox = ttk.Combobox(
            basic_frame,
            textvariable=self.parent_var,
            values=list(self.parent_values.keys()),
            state="readonly"
        )
        parent_combobox.grid(row=1, column=1, sticky="ew", padx=8, pady=6)
        if self.parent_values:
            selected_parent = next(
                (value for value, actor_id in self.parent_values.items() if actor_id == self.parent_id),
                next(iter(self.parent_values))
            )
            self.parent_var.set(selected_parent)

        sensor_blueprints = sorted(
            blueprint.id for blueprint in self.database.blueprint_lib.filter("sensor.*")
        )
        ttk.Label(basic_frame, text="Blueprint").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        blueprint_combobox = ttk.Combobox(
            basic_frame,
            textvariable=self.blueprint_var,
            values=sensor_blueprints,
            state="readonly"
        )
        blueprint_combobox.grid(row=2, column=1, sticky="ew", padx=8, pady=6)
        blueprint_combobox.bind("<<ComboboxSelected>>", self._update_attributes)
        if sensor_blueprints:
            blueprint_combobox.current(0)

        self.attributes_frame = ttk.LabelFrame(
            self.content_frame,
            text="Blueprint Parameters",
            style="Section.TLabelframe",
            padding=(12, 8)
        )
        self.attributes_frame.pack(fill="x", padx=8, pady=8)

        transform_frame = ttk.LabelFrame(
            self.content_frame,
            text="Relative Transform",
            style="Section.TLabelframe",
            padding=(12, 8)
        )
        transform_frame.pack(fill="x", padx=8, pady=8)
        fields = (("X", "x"), ("Y", "y"), ("Z", "z"),
                  ("Pitch", "pitch"), ("Yaw", "yaw"), ("Roll", "roll"))
        for index, (label, key) in enumerate(fields):
            row = index // 3
            column = (index % 3) * 2
            ttk.Label(transform_frame, text=label).grid(
                row=row, column=column, sticky="w", padx=(8, 4), pady=8
            )
            ttk.Spinbox(
                transform_frame,
                from_=-1e6,
                to=1e6,
                increment=0.01,
                textvariable=self.transform_vars[key],
                width=16
            ).grid(row=row, column=column + 1, sticky="ew", padx=(0, 12), pady=8)
            transform_frame.grid_columnconfigure(column + 1, weight=1)

        button_frame = ttk.Frame(self.content_frame, style="new.TFrame")
        button_frame.pack(fill="x", padx=8, pady=(8, 12))
        ttk.Button(button_frame, text="Add Sensor", command=self.apply).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(button_frame, text="Cancel", command=self.destroy).pack(side="right")

        self._update_attributes()

    def _update_attributes(self, event=None):
        for widget in self.attributes_frame.winfo_children():
            widget.destroy()
        self.attribute_vars = {}

        blueprint_id = self.blueprint_var.get()
        if not blueprint_id:
            return
        blueprint = self.database.blueprint_lib.find(blueprint_id)
        row = 0
        column = 0
        for attribute in blueprint:
            if not attribute.is_modifiable:
                continue
            self._create_attribute_widget(attribute, row, column)
            column += 2
            if column == 4:
                column = 0
                row += 1

    def _create_attribute_widget(self, attribute, row, column):
        label = str(attribute.id).replace("_", " ").title()
        ttk.Label(self.attributes_frame, text=label).grid(
            row=row * 2, column=column, sticky="w", padx=8, pady=(6, 2)
        )

        if attribute.type == carla.ActorAttributeType.Bool:
            variable = tk.BooleanVar(value=attribute.as_bool())
            widget = ttk.Checkbutton(self.attributes_frame, variable=variable)
            attribute_value = variable
        elif attribute.type == carla.ActorAttributeType.Int:
            variable = tk.IntVar(value=attribute.as_int())
            widget = ttk.Spinbox(
                self.attributes_frame, from_=-1e9, to=1e9, textvariable=variable
            )
            attribute_value = variable
        elif attribute.type == carla.ActorAttributeType.Float:
            variable = tk.DoubleVar(value=attribute.as_float())
            widget = ttk.Spinbox(
                self.attributes_frame,
                from_=-1e9,
                to=1e9,
                increment=0.01,
                textvariable=variable
            )
            attribute_value = variable
        elif attribute.type == carla.ActorAttributeType.RGBColor:
            color = attribute.as_color()
            widget = ColorPicker(
                self.attributes_frame,
                color=rgb_to_hex(f"{color.r},{color.g},{color.b}")
            )
            attribute_value = widget
        else:
            variable = tk.StringVar(value=attribute.as_str())
            recommended_values = list(attribute.recommended_values)
            if recommended_values:
                widget = ttk.Combobox(
                    self.attributes_frame,
                    textvariable=variable,
                    values=recommended_values,
                    state="readonly"
                )
            else:
                widget = ttk.Entry(self.attributes_frame, textvariable=variable)
            attribute_value = variable

        widget.grid(row=row * 2 + 1, column=column, sticky="ew", padx=8, pady=(0, 6))
        self.attributes_frame.grid_columnconfigure(column, weight=1)
        self.attribute_vars[attribute.id] = {
            "type": attribute.type,
            "value": attribute_value
        }

    def apply(self):
        name = self.sensor_name_var.get().strip()
        parent_value = self.parent_var.get()
        blueprint_id = self.blueprint_var.get()
        if not name or parent_value not in self.parent_values or not blueprint_id:
            messagebox.showerror(
                "Invalid sensor data",
                "Enter a name and select a parent actor and sensor blueprint.",
                parent=self
            )
            return

        try:
            transform = carla.Transform(
                carla.Location(
                    x=float(self.transform_vars["x"].get()),
                    y=float(self.transform_vars["y"].get()),
                    z=float(self.transform_vars["z"].get())
                ),
                carla.Rotation(
                    pitch=float(self.transform_vars["pitch"].get()),
                    yaw=float(self.transform_vars["yaw"].get()),
                    roll=float(self.transform_vars["roll"].get())
                )
            )
            attributes = {}
            for attribute_id, attribute_info in self.attribute_vars.items():
                if attribute_info["type"] == carla.ActorAttributeType.RGBColor:
                    attributes[attribute_id] = hex_to_rgb(attribute_info["value"].color)
                else:
                    attributes[attribute_id] = attribute_info["value"].get()
        except (tk.TclError, ValueError) as error:
            messagebox.showerror("Invalid sensor data", str(error), parent=self)
            return

        self.controller.carla.attach_sensor({
            "name": name,
            "parent_id": self.parent_values[parent_value],
            "blueprint_id": blueprint_id,
            "transform": transform,
            "attributes": attributes
        })
        self.destroy()

