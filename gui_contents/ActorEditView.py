import tkinter as tk
from typing import List, Any

import carla
from tkinter import messagebox, ttk
from gui_contents.color_picker import ColorPicker
from utilities.functions import rgb_to_hex, hex_to_rgb


class EntityEditWindow(tk.Toplevel):
    def __init__(self, parent, blueprint, entity=None, type="actor",
                 blueprint_library=None, physic=None, on_apply=None,
                 blueprint_data=None, show_blueprint=True,
                 show_physics=True):
        super().__init__(parent)

        self.blueprint = blueprint_library.find(blueprint)
        self.entity = entity
        self.type = type
        self.show_blueprint = bool(show_blueprint)
        self.supports_vehicle_physics = type == "vehicle" and show_physics
        self.on_apply = on_apply
        self.row = None
        self.column = None
        if physic is None:
            self.physic = carla.VehiclePhysicsControl()
        else:
            self.physic = physic
        self.attribute_vars = {}
        if self.show_blueprint and blueprint_data is not None:
            for attr_id, attr_info in blueprint_data.items():
                if self.blueprint.has_attribute(attr_id):
                    attribute = self.blueprint.get_attribute(attr_id)
                    if attribute.is_modifiable:
                        self.blueprint.set_attribute(attr_id, str(attr_info))
        self.physics_vars = {
            "torque_curve": [],
            "max_rpm": tk.DoubleVar(value=self.physic.max_rpm),
            "moi": tk.DoubleVar(value=self.physic.moi),
            "damping_rate_full_throttle": tk.DoubleVar(value=self.physic.damping_rate_full_throttle),
            "damping_rate_zero_throttle_clutch_engaged": tk.DoubleVar(value=self.physic.damping_rate_zero_throttle_clutch_engaged),
            "damping_rate_zero_throttle_clutch_disengaged": tk.DoubleVar(value=self.physic.damping_rate_zero_throttle_clutch_disengaged),
            "use_gear_autobox": tk.BooleanVar(value=self.physic.use_gear_autobox),
            "gear_switch_time": tk.DoubleVar(value=self.physic.gear_switch_time),
            "clutch_strength": tk.DoubleVar(value=self.physic.clutch_strength),
            "final_ratio": tk.DoubleVar(value=self.physic.final_ratio),
            "mass": tk.DoubleVar(value=self.physic.mass),
            "drag_coefficient": tk.DoubleVar(value=self.physic.drag_coefficient),
            "center_of_mass": {
                "x": tk.DoubleVar(value=self.physic.center_of_mass.x),
                "y": tk.DoubleVar(value=self.physic.center_of_mass.y),
                "z": tk.DoubleVar(value=self.physic.center_of_mass.z)
            },
            "steering_curve": [],
            "use_sweep_wheel_collision": tk.BooleanVar(value=self.physic.use_sweep_wheel_collision),
        }
        self.gear_data = self.physic.forward_gears
        self.tabControl = ttk.Notebook(self)
        self.BlueprintTab = ttk.Frame(self.tabControl)
        self.PhysicsTab = ttk.Frame(self.tabControl)
        self.GearsTab = ttk.Frame(self.tabControl)
        self.TorqueTab = ttk.Frame(self.tabControl)
        self.SteeringTab = ttk.Frame(self.tabControl)
        self.WheelsTab = ttk.Frame(self.tabControl)

        if self.show_blueprint:
            self.tabControl.add(self.BlueprintTab, text="Blueprint")
        if self.supports_vehicle_physics:
            self.tabControl.add(self.PhysicsTab, text="Physics")
            self.tabControl.add(self.GearsTab, text="Gears")
            self.tabControl.add(self.TorqueTab, text="Torque Curve")
            self.tabControl.add(self.SteeringTab, text="Steering Curve")
            self.tabControl.add(self.WheelsTab, text="Wheels")
        self.tabControl.pack(expand=1, fill="both")

        self.blueprint_parameters_frame = ttk.Frame(self.BlueprintTab)
        self.physics_parameters_frame = ttk.Frame(self.PhysicsTab)
        self.wheels_parameters_frame = ttk.Frame(self.WheelsTab)
        self.gears_parameters_frame = ttk.Frame(self.GearsTab)
        self.torque_parameters_frame = ttk.Frame(self.TorqueTab)
        self.steering_parameters_frame = ttk.Frame(self.SteeringTab)

        if self.show_blueprint:
            self.blueprint_parameters_frame.grid(row=0, column=0, sticky="nsew")
            self.create_blueprint_section()
        if self.supports_vehicle_physics:
            self.physics_parameters_frame.grid(row=0, column=0, sticky="nsew")
            self.create_physic_section()
            self.wheels_parameters_frame.grid(row=0, column=0, sticky="nsew")
            self.wheels_table = self.create_wheels_section()
            self.gears_parameters_frame.grid(row=0, column=0, sticky="nsew")
            self.gears_table = self.create_gears_section()
            self.torque_parameters_frame.grid(row=0, column=0, sticky="nsew")
            self.torque_table = self.create_torque_section()
            self.steering_parameters_frame.grid(row=0, column=0, sticky="nsew")
            self.steering_table = self.create_steering_section()

        self.button_frame = ttk.Frame(self)
        self.button_frame.pack(side="bottom", fill="x", pady=10)
        self.apply_button = ttk.Button(self.button_frame, text="Apply", command=self.apply)
        self.apply_button.pack(side="right", padx=10)
        self.cancel_button = ttk.Button(self.button_frame, text="Cancel", command=self.cancel)
        self.cancel_button.pack(side="right", padx=10)

    def cancel(self):
        self.destroy()

    def create_blueprint_section(self):
        heading_frame = ttk.Frame(self.blueprint_parameters_frame)
        heading_frame.grid(row=0, column=0, columnspan=2, sticky="nsew")
        ttk.Label(heading_frame, text="Blueprint Parameters", font=("Arial", 12, "bold")).pack(side="left", padx=20, pady=10)
        parameters_frame = ttk.Frame(self.blueprint_parameters_frame)
        parameters_frame.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.row = 0
        self.column = 0
        for attribute in self.blueprint:
            if not attribute.is_modifiable:
                continue

            self.create_attribute_widget(
                attribute,
                parameters_frame
            )
            self.row += 1
            if self.row == 8:
                self.row = 0
                self.column += 1

    def create_physic_section(self):
        heading_frame = ttk.Frame(self.physics_parameters_frame)
        heading_frame.grid(row=0, column=0, columnspan=2, sticky="nsew")
        ttk.Label(heading_frame, text="Physics Parameters", font=("Arial", 12, "bold")).pack(side="left", padx=20, pady=10)

        parameters_frame = ttk.Frame(self.physics_parameters_frame)
        parameters_frame.grid(row=1, column=0, columnspan=2, sticky="nsew")

        # Max RPM
        self.arial_bold_ = ("Arial", 10, "bold")
        ttk.Label(parameters_frame, text="Max RPM", font=self.arial_bold_).grid(row=0, column=0, sticky="w", padx=20)
        ttk.Spinbox(parameters_frame, from_=0, to=1e6, textvariable=self.physics_vars["max_rpm"]).grid(row=1, column=0, sticky="w", padx=20)

        # MOI
        ttk.Label(parameters_frame, text="MOI [kg·m^2]", font=self.arial_bold_).grid(row=2, column=0, sticky="w", padx=20)
        ttk.Spinbox(parameters_frame, from_=0, to=1e6, textvariable=self.physics_vars["moi"]).grid(row=3, column=0, sticky="w", padx=20)

        # Damping Rate Full Throttle
        ttk.Label(parameters_frame, text="Damping Rate Full Throttle", font=self.arial_bold_).grid(row=4, column=0, sticky="w", padx=20)
        ttk.Spinbox(parameters_frame, from_=0, to=1e6, textvariable=self.physics_vars["damping_rate_full_throttle"]).grid(row=5, column=0, sticky="w", padx=20)

        # Damping Rate Zero Throttle Clutch Engaged
        ttk.Label(parameters_frame, text="Damping Rate Zero Throttle\nClutch Engaged", font=self.arial_bold_).grid(row=6, column=0, sticky="w", padx=20)
        ttk.Spinbox(parameters_frame, from_=0, to=1e6, textvariable=self.physics_vars["damping_rate_zero_throttle_clutch_engaged"]).grid(row=7, column=0, sticky="w", padx=20)

        # Damping Rate Zero Throttle Clutch Disengaged
        ttk.Label(parameters_frame, text="Damping Rate Zero Throttle\nClutch Disengaged", font=self.arial_bold_).grid(row=8, column=0, sticky="w", padx=20)
        ttk.Spinbox(parameters_frame, from_=0, to=1e6, textvariable=self.physics_vars["damping_rate_zero_throttle_clutch_disengaged"]).grid(row=9, column=0, sticky="w", padx=20)

        # Use Gear Autobox
        ttk.Label(parameters_frame, text="Use Gear Autobox", font=self.arial_bold_).grid(row=0, column=2, sticky="w", padx=20)
        ttk.Checkbutton(parameters_frame, variable=self.physics_vars["use_gear_autobox"]).grid(row=1, column=2, sticky="w", padx=20)

        # Use Sweep Wheel Collision
        ttk.Label(parameters_frame, text="Use Sweep Wheel Collision", font=self.arial_bold_).grid(row=2, column=2, sticky="w", padx=20)
        ttk.Checkbutton(parameters_frame, variable=self.physics_vars["use_sweep_wheel_collision"]).grid(row=3, column=2, sticky="w", padx=20)

        # Gear Switch Time
        ttk.Label(parameters_frame, text="Gear Switch Time [s]", font=self.arial_bold_).grid(row=0, column=1, sticky="w", padx=20)
        ttk.Spinbox(parameters_frame, from_=0, to=1e6, textvariable=self.physics_vars["gear_switch_time"]).grid(row=1, column=1, sticky="w", padx=20)

        # Clutch Strength
        ttk.Label(parameters_frame, text="Clutch Strength [kg·m^2/s]", font=self.arial_bold_).grid(row=2, column=1, sticky="w", padx=20)
        ttk.Spinbox(parameters_frame, from_=0, to=1e6, textvariable=self.physics_vars["clutch_strength"]).grid(row=3, column=1, sticky="w", padx=20)

        # Final Ratio
        ttk.Label(parameters_frame, text="Final Ratio", font=self.arial_bold_).grid(row=4, column=1, sticky="w", padx=20)
        ttk.Spinbox(parameters_frame, from_=0, to=1e6, textvariable=self.physics_vars["final_ratio"]).grid(row=5, column=1, sticky="w", padx=20)

        # Mass
        ttk.Label(parameters_frame, text="Mass [kg]", font=self.arial_bold_).grid(row=6, column=1, sticky="w", padx=20)
        ttk.Spinbox(parameters_frame, from_=0, to=1e6, textvariable=self.physics_vars["mass"]).grid(row=7, column=1, sticky="w", padx=20)

        # Drag Coefficient
        ttk.Label(parameters_frame, text="Drag Coefficient", font=self.arial_bold_).grid(row=8, column=1, sticky="w", padx=20)
        ttk.Spinbox(parameters_frame, from_=0, to=1e6, textvariable=self.physics_vars["drag_coefficient"]).grid(row=9, column=1, sticky="w", padx=20)

        # Center of Mass
        ttk.Label(parameters_frame, text="Center of Mass [m]", font=self.arial_bold_).grid(row=4, column=2, sticky="w", padx=20)
        ttk.Label(parameters_frame, text="X", font=self.arial_bold_).grid(row=5, column=2, sticky="w", padx=30)
        ttk.Spinbox(parameters_frame, from_=-1e6, to=1e6, textvariable=self.physics_vars["center_of_mass"]["x"]).grid(row=6, column=2, sticky="w", padx=30)
        ttk.Label(parameters_frame, text="Y", font=self.arial_bold_).grid(row=7, column=2, sticky="w", padx=30)
        ttk.Spinbox(parameters_frame, from_=-1e6, to=1e6, textvariable=self.physics_vars["center_of_mass"]["y"]).grid(row=8, column=2, sticky="w", padx=30)
        ttk.Label(parameters_frame, text="Z", font=self.arial_bold_).grid(row=9, column=2, sticky="w", padx=30)
        ttk.Spinbox(parameters_frame, from_=-1e6, to=1e6, textvariable=self.physics_vars["center_of_mass"]["z"]).grid(row=10, column=2, sticky="w", padx=30)

    def create_gears_section(self):
        # Gear Control
        gear_table = GearManagerTable(self.gears_parameters_frame, self.gear_data)
        gear_table.grid(row=0, column=0, sticky="w", padx=20)
        return gear_table

    def create_torque_section(self):
        # Torque Curve
        torque_table = TorqueCurveTable(self.torque_parameters_frame, self.physic.torque_curve)
        torque_table.grid(row=0, column=0, sticky="w", padx=20)
        return torque_table

    def create_steering_section(self):
        # Steering Curve
        steering_table = SteeringCurveTable(self.steering_parameters_frame, self.physic.steering_curve)
        steering_table.grid(row=0, column=0, sticky="w", padx=20)
        return steering_table

    def create_wheels_section(self):
        wheel_table = WheelsMenu(self.wheels_parameters_frame, self.physic.wheels)
        wheel_table.grid(row=0, column=0, sticky="w", padx=20)
        return wheel_table

    def create_attribute_widget(self, attribute, parent_frame):
        def format_name(name):
            return str(name).replace("_", " ").title()

        arial_bold_ = ("Arial", 10, "bold")
        if attribute.type == carla.ActorAttributeType.Bool:
            default_value = attribute.as_bool()
            var = tk.BooleanVar(value=default_value)

            ttk.Label(parent_frame, text=format_name(attribute.id),
                      font=arial_bold_).grid(row=self.row, column=self.column, sticky="w", padx=20)
            self.row += 1
            ttk.Checkbutton(parent_frame, variable=var).grid(row=self.row, column=self.column, sticky="w", padx=20)

            self.attribute_vars[attribute.id] = {
                "type": attribute.type,
                "var": var
            }

        elif attribute.type == carla.ActorAttributeType.Int:
            default_value = attribute.as_int()
            var = tk.IntVar(value=default_value)

            ttk.Label(parent_frame, text=format_name(attribute.id),
                      font=arial_bold_).grid(row=self.row, column=self.column, sticky="w", padx=20)
            self.row += 1
            spinbox = ttk.Spinbox(parent_frame, from_=-1e6, to=1e6, textvariable=var)
            spinbox.grid(row=self.row, column=self.column, sticky="w", padx=20)
            if "wheel" in format_name(attribute.id).lower() and ("num" in format_name(attribute.id).lower() or "number" in format_name(attribute.id).lower()):
                spinbox.configure(state=tk.DISABLED)
            self.attribute_vars[attribute.id] = {
                "type": attribute.type,
                "var": var
            }

        elif attribute.type == carla.ActorAttributeType.Float:
            default_value = attribute.as_float()
            var = tk.DoubleVar(value=default_value)

            ttk.Label(parent_frame, text=format_name(attribute.id),
                      font=arial_bold_).grid(row=self.row, column=self.column, sticky="w", padx=20)
            self.row += 1
            spinbox = ttk.Spinbox(parent_frame, from_=-1e6, to=1e6, textvariable=var, format="%.2f")
            spinbox.grid(row=self.row, column=self.column, sticky="w", padx=20)
            if "wheel" in format_name(attribute.id).lower() and ("num" in format_name(attribute.id).lower() or "number" in format_name(attribute.id).lower()):
                spinbox.configure(state=tk.DISABLED)
            self.attribute_vars[attribute.id] = {
                "type": attribute.type,
                "var": var
            }

        elif attribute.type == carla.ActorAttributeType.String:
            default_value = attribute.as_str()
            var = tk.StringVar(value=default_value)

            ttk.Label(parent_frame, text=format_name(attribute.id),
                      font=arial_bold_).grid(row=self.row, column=self.column, sticky="w", padx=20)
            self.row += 1
            ttk.Entry(parent_frame, textvariable=var).grid(row=self.row, column=self.column, sticky="w", padx=20)

            self.attribute_vars[attribute.id] = {
                "type": attribute.type,
                "var": var
            }

        elif attribute.type == carla.ActorAttributeType.RGBColor:
            default_value = attribute.as_color()
            default_value_hex = rgb_to_hex(f"{default_value.r},{default_value.g},{default_value.b}")

            ttk.Label(parent_frame, text=format_name(attribute.id),
                      font=arial_bold_).grid(row=self.row, column=self.column, sticky="w", padx=20)
            self.row += 1
            color_picker = ColorPicker(parent_frame, color=default_value_hex)
            color_picker.grid(row=self.row, column=self.column, sticky="w", padx=20)
            self.attribute_vars[attribute.id] = {
                "type": attribute.type,
                "widget": color_picker
            }

    def get_data(self):
        # Blueprint data
        blueprint_data = {}

        for attr_id, attr_info in self.attribute_vars.items():
            if attr_info["type"] == carla.ActorAttributeType.Bool:
                blueprint_data[attr_id] = attr_info["var"].get()
            elif attr_info["type"] == carla.ActorAttributeType.Int:
                blueprint_data[attr_id] = attr_info["var"].get()
            elif attr_info["type"] == carla.ActorAttributeType.Float:
                blueprint_data[attr_id] = float(attr_info["var"].get())
            elif attr_info["type"] == carla.ActorAttributeType.String:
                blueprint_data[attr_id] = attr_info["var"].get()
            elif attr_info["type"] == carla.ActorAttributeType.RGBColor:
                blueprint_data[attr_id] = hex_to_rgb(attr_info["widget"].color)

        if not self.supports_vehicle_physics:
            return {"blueprint": blueprint_data, "physics": None}

        # Physics data
        max_rpm = self.physics_vars["max_rpm"].get()
        moi = self.physics_vars["moi"].get()
        damping_rate_full_throttle = self.physics_vars["damping_rate_full_throttle"].get()
        damping_rate_zero_throttle_clutch_engaged = self.physics_vars["damping_rate_zero_throttle_clutch_engaged"].get()
        damping_rate_zero_throttle_clutch_disengaged = self.physics_vars["damping_rate_zero_throttle_clutch_disengaged"].get()
        use_gear_autobox = self.physics_vars["use_gear_autobox"].get()
        gear_switch_time = self.physics_vars["gear_switch_time"].get()
        clutch_strength = self.physics_vars["clutch_strength"].get()
        final_ratio = self.physics_vars["final_ratio"].get()
        mass = self.physics_vars["mass"].get()
        drag_coefficient = self.physics_vars["drag_coefficient"].get()
        center_of_mass = carla.Location(
            x=float(self.physics_vars["center_of_mass"]["x"].get()),
            y=float(self.physics_vars["center_of_mass"]["y"].get()),
            z=float(self.physics_vars["center_of_mass"]["z"].get())
        )
        use_sweep_wheel_collision = self.physics_vars["use_sweep_wheel_collision"].get()
        torque_curve = self.torque_table.get_data()
        gears = self.gears_table.get_data()
        steering = self.steering_table.get_data()
        wheels = self.wheels_table.get_data()

        # Preserve CARLA fields not exposed by this editor by updating the
        # control object that was read from the actor.
        physics_data = self.physic
        physics_data.torque_curve = torque_curve
        physics_data.max_rpm = max_rpm
        physics_data.moi = moi
        physics_data.damping_rate_full_throttle = damping_rate_full_throttle
        physics_data.damping_rate_zero_throttle_clutch_engaged = damping_rate_zero_throttle_clutch_engaged
        physics_data.damping_rate_zero_throttle_clutch_disengaged = damping_rate_zero_throttle_clutch_disengaged
        physics_data.use_gear_autobox = use_gear_autobox
        physics_data.gear_switch_time = gear_switch_time
        physics_data.clutch_strength = clutch_strength
        physics_data.final_ratio = final_ratio
        physics_data.forward_gears = gears
        physics_data.mass = mass
        physics_data.drag_coefficient = drag_coefficient
        physics_data.center_of_mass = center_of_mass
        physics_data.steering_curve = steering
        physics_data.use_sweep_wheel_collision = use_sweep_wheel_collision
        physics_data.wheels = wheels
        return {"blueprint": blueprint_data, "physics": physics_data}

    def apply(self):
        try:
            data = self.get_data()
            accepted = self.on_apply(data) if self.on_apply else True
        except (tk.TclError, TypeError, ValueError, RuntimeError) as error:
            messagebox.showerror(
                "Invalid actor configuration",
                str(error),
                parent=self,
            )
            return
        if accepted is not False:
            self.destroy()

class GearManagerTable(ttk.Frame):
    def __init__(self, parent, gears):
        super().__init__(parent)
        self.gears = gears
        self.default_color = "#F7E7D3"
        self.panel_color = "#F1D0A3"
        self.accent_color = "#C96D3C"
        self.text_color = "#4A2E1D"
        self.subtle_color = "#E7B36C"

        title = ttk.Label(self, text="Gear Control", style="TLabel", font=("Arial", 12, "bold"))
        title.pack(side="top", fill="x", pady=(10, 8))
        self.table_frame = ttk.Frame(self)
        self.table_frame.pack(fill="both", expand=True)

        columns = ("Gear", "Ratio", "Down Ratio", "Up Ratio")
        self.treeview = ttk.Treeview(self.table_frame, columns=columns, show="headings", height=10, style="Treeview", selectmode="browse")
        self.treeview.heading("Gear", text="Gear")
        self.treeview.heading("Ratio", text="Ratio")
        self.treeview.heading("Down Ratio", text="Down Ratio")
        self.treeview.heading("Up Ratio", text="Up Ratio")

        self.add_button = self.treeview.insert("", "end", values=("+ Add Gear", "", "", ""))
        for gear in gears:
            self.add_gear(gear)

        self.treeview.bind("<Double-1>", self.edit_cell)
        self.treeview.bind("<Delete>", self.delete_selected)

        self.treeview.pack(side="left", fill="both", expand=True)

        self.button_frame = ttk.Frame(self.table_frame)
        self.button_frame.pack(side="right", fill="y", padx=(10, 0))

        self.up_button = ttk.Button(self.button_frame, text="▲", command=self.move_up)
        self.up_button.pack(side="top", pady=(0, 5))
        self.down_button = ttk.Button(self.button_frame, text="▼", command=self.move_down)
        self.down_button.pack(side="top")

        self.add_gear_button = ttk.Button(
            self.button_frame,
            text="Add",
            command=lambda: self.add_gear(carla.GearPhysicsControl()),
        )
        self.add_gear_button.pack(side="top", pady=(10, 5))
        self.delete_gear_button = ttk.Button(
            self.button_frame,
            text="Remove",
            command=self.delete_selected,
        )
        self.delete_gear_button.pack(side="top")


    def add_gear(self, gear):
        gear_number = len(self._gear_items()) + 1

        item = self.treeview.insert("", self.treeview.index(self.add_button), values=(gear_number, gear.ratio, gear.down_ratio, gear.up_ratio))

        return item

    def _gear_items(self):
        return [
            item for item in self.treeview.get_children()
            if item != self.add_button
        ]

    def edit_cell(self, event):
        region = self.treeview.identify("region", event.x, event.y)

        if region != "cell":
            return

        column = self.treeview.identify_column(event.x)
        row = self.treeview.identify_row(event.y)

        if not row or not column:
            return

        if row == self.add_button:
            self.add_gear(carla.GearPhysicsControl())
            return

        if column == "#1":
            return

        bbox = self.treeview.bbox(row, column)

        if not bbox:
            return

        x, y, width, height = bbox

        value = self.treeview.set(row, column)

        entry = ttk.Entry(self.treeview)
        entry.insert(0, value)

        entry.place(x=x, y=y, width=width, height=height)
        entry.focus()
        entry.select_range(0, tk.END)

        def save(event=None):
            new_value = entry.get()
            try:
                new_value = float(new_value)
            except ValueError:
                entry.destroy()
                return
            self.treeview.set(row, column, new_value)
            entry.destroy()

        entry.bind("<Return>", save)
        entry.bind("<FocusOut>", save)
        entry.bind("<Escape>", lambda event: entry.destroy())

    def delete_selected(self, event=None):
        selected = self.treeview.selection()

        if not selected:
            return

        for item in selected:
            if item == self.add_button:
                continue
            self.treeview.delete(item)

        self.renumber_gears()

    def renumber_gears(self):
        number = 1
        for item in self._gear_items():
            self.treeview.set(item, "#1", number)
            number += 1

    def move_up(self):
        selected = self.treeview.selection()

        if not selected:
            return

        item = selected[0]

        if item == self.add_button:
            return

        items = self._gear_items()
        index = items.index(item)

        if index > 0:
            previous_item = items[index - 1]
            self.treeview.move(item, "", self.treeview.index(previous_item))

        self.renumber_gears()

    def move_down(self):
        selected = self.treeview.selection()

        if not selected:
            return

        item = selected[0]

        if item == self.add_button:
            return

        items = self._gear_items()
        index = items.index(item)

        if index < len(items) - 1:
            self.treeview.move(item, "", self.treeview.index(item) + 1)

        self.renumber_gears()

    def get_data(self):
        gears = []
        for item in self._gear_items():
            ratio = float(self.treeview.set(item, "#2"))
            down_ratio = float(self.treeview.set(item, "#3"))
            up_ratio = float(self.treeview.set(item, "#4"))
            gears.append(carla.GearPhysicsControl(ratio, down_ratio, up_ratio))
        return gears

class TorqueCurveTable(ttk.Frame):
    def __init__(self, parent, torque_curve: List[carla.Vector2D]):
        super().__init__(parent)
        self.torque_curve = torque_curve
        self.default_color = "#F7E7D3"
        self.panel_color = "#F1D0A3"
        self.accent_color = "#C96D3C"
        self.text_color = "#4A2E1D"
        self.subtle_color = "#E7B36C"

        title = ttk.Label(self, text="Torque Curve", style="TLabel", font=("Arial", 12, "bold"))
        title.pack(side="top", fill="x", pady=(10, 8))

        self.table_frame = ttk.Frame(self)
        self.table_frame.pack(fill="both", expand=True)

        columns = ("RPM", "Torque")
        self.treeview = ttk.Treeview(self.table_frame, columns=columns, show="headings", height=10, style="Treeview", selectmode="browse")
        self.treeview.heading("RPM", text="RPM")
        self.treeview.heading("Torque", text="Torque")

        self.add_button = self.treeview.insert("", "end", values=("+ Add Point", ""))

        for point in torque_curve:
            self.add_point([point.x, point.y])

        self.treeview.bind("<Double-1>", self.edit_cell)
        self.treeview.bind("<Delete>", self.delete_selected)


        self.treeview.pack(side="left", fill="both", expand=True)

        self.button_frame = ttk.Frame(self.table_frame)
        self.button_frame.pack(side="right", fill="y", padx=(10, 0))

        self.up_button = ttk.Button(self.button_frame, text="▲", command=self.move_up)
        self.up_button.pack(side="top", pady=(0, 5))
        self.down_button = ttk.Button(self.button_frame, text="▼", command=self.move_down)
        self.down_button.pack(side="top")

    def add_point(self, point):
        item = self.treeview.insert("", self.treeview.index(self.add_button), values=(point[0], point[1]))
        return item

    def edit_cell(self, event):
        region = self.treeview.identify("region", event.x, event.y)

        if region != "cell":
            return

        column = self.treeview.identify_column(event.x)
        row = self.treeview.identify_row(event.y)

        if not row or not column:
            return

        if row == self.add_button:
            self.add_point([0, 0])
            return

        bbox = self.treeview.bbox(row, column)

        if not bbox:
            return

        x, y, width, height = bbox

        value = self.treeview.set(row, column)

        entry = ttk.Entry(self.treeview)
        entry.insert(0, value)

        entry.place(x=x, y=y, width=width, height=height)
        entry.focus()
        entry.select_range(0, tk.END)

        def save(event=None):
            new_value = entry.get()
            try:
                new_value = float(new_value)
            except ValueError:
                entry.destroy()
                return
            self.treeview.set(row, column, new_value)
            entry.destroy()

        entry.bind("<Return>", save)
        entry.bind("<FocusOut>", save)
        entry.bind("<Escape>", lambda event: entry.destroy())

    def delete_selected(self, event=None):
        selected = self.treeview.selection()

        if not selected:
            return

        for item in selected:
            if item == self.add_button:
                continue
            self.treeview.delete(item)

    def move_up(self, event=None):
        selected = self.treeview.selection()

        if not selected:
            return

        item = selected[0]

        if item == self.add_button:
            return

        index = self.treeview.index(item)

        if index >0:
            self.treeview.move(item, "", index - 1)


    def move_down(self, event=None):
        selected = self.treeview.selection()

        if not selected:
            return

        item = selected[0]

        if item == self.add_button:
            return

        index = self.treeview.index(item)

        if index < len(self.treeview.get_children()) - 2:
            self.treeview.move(item, "", index + 1)

    def get_data(self):
        torque_curve = []
        for item in self.treeview.get_children():
            if item == self.add_button:
                continue
            rpm = float(self.treeview.set(item, "RPM"))
            torque = float(self.treeview.set(item, "Torque"))
            torque_curve.append(carla.Vector2D(rpm, torque))
        return torque_curve

class SteeringCurveTable(ttk.Frame):
    def __init__(self, parent, steering_curve: List[carla.Vector2D]):
        super().__init__(parent)
        self.steering_curve = steering_curve
        self.default_color = "#F7E7D3"
        self.panel_color = "#F1D0A3"
        self.accent_color = "#C96D3C"
        self.text_color = "#4A2E1D"
        self.subtle_color = "#E7B36C"

        title = ttk.Label(self, text="Steering Curve", style="TLabel", font=("Arial", 12, "bold"))
        title.pack(side="top", fill="x", pady=(10, 8))

        self.table_frame = ttk.Frame(self)
        self.table_frame.pack(fill="both", expand=True)

        columns = ("Speed", "Steering Angle")
        self.treeview = ttk.Treeview(self.table_frame, columns=columns, show="headings", height=10, style="Treeview", selectmode="browse")
        self.treeview.heading("Speed", text="Speed")
        self.treeview.heading("Steering Angle", text="Steering Angle")

        self.add_button = self.treeview.insert("", "end", values=("+ Add Point", ""))

        for point in steering_curve:
            self.add_point([point.x, point.y])

        self.treeview.bind("<Double-1>", self.edit_cell)
        self.treeview.bind("<Delete>", self.delete_selected)


        self.treeview.pack(side="left", fill="both", expand=True)

        self.button_frame = ttk.Frame(self.table_frame)
        self.button_frame.pack(side="right", fill="y", padx=(10, 0))

        self.up_button = ttk.Button(self.button_frame, text="▲", command=self.move_up)
        self.up_button.pack(side="top", pady=(0, 5))
        self.down_button = ttk.Button(self.button_frame, text="▼", command=self.move_down)
        self.down_button.pack(side="top")

    def add_point(self, point):
        item = self.treeview.insert("", self.treeview.index(self.add_button), values=(point[0], point[1]))
        return item

    def edit_cell(self, event):
        region = self.treeview.identify("region", event.x, event.y)

        if region != "cell":
            return

        column = self.treeview.identify_column(event.x)
        row = self.treeview.identify_row(event.y)

        if not row or not column:
            return

        if row == self.add_button:
            self.add_point([0, 0])
            return

        bbox = self.treeview.bbox(row, column)

        if not bbox:
            return

        x, y, width, height = bbox

        value = self.treeview.set(row, column)

        entry = ttk.Entry(self.treeview)
        entry.insert(0, value)

        entry.place(x=x, y=y, width=width, height=height)
        entry.focus()
        entry.select_range(0, tk.END)

        def save(event=None):
            new_value = entry.get()
            try:
                new_value = float(new_value)
            except ValueError:
                entry.destroy()
                return
            self.treeview.set(row, column, new_value)
            entry.destroy()

        entry.bind("<Return>", save)
        entry.bind("<FocusOut>", save)
        entry.bind("<Escape>", lambda event: entry.destroy())

    def delete_selected(self, event=None):
        selected = self.treeview.selection()

        if not selected:
            return

        for item in selected:
            if item == self.add_button:
                continue
            self.treeview.delete(item)

    def move_up(self, event=None):
        selected = self.treeview.selection()

        if not selected:
            return

        item = selected[0]

        if item == self.add_button:
            return

        index = self.treeview.index(item)

        if index > 0:
            self.treeview.move(item, "", index - 1)

    def move_down(self, event=None):
        selected = self.treeview.selection()

        if not selected:
            return

        item = selected[0]

        if item == self.add_button:
            return

        index = self.treeview.index(item)

        if index < len(self.treeview.get_children()) - 2:
            self.treeview.move(item, "", index + 1)

    def get_data(self):
        steering_curve = []
        for item in self.treeview.get_children():
            if item == self.add_button:
                continue
            speed = float(self.treeview.set(item, "Speed"))
            steering_angle = float(self.treeview.set(item, "Steering Angle"))
            steering_curve.append(carla.Vector2D(speed, steering_angle))
        return steering_curve

class WheelsMenu(ttk.Frame):
    def __init__(self, parent, wheels: List[carla.WheelPhysicsControl]):
        super().__init__(parent)
        self.wheels = wheels

        self.arial_10 = ("Arial", 10)
        self.heading_frame = ttk.Frame(self)
        self.heading_frame.pack(side="top", fill="x", pady=(10, 8))
        ttk.Label(self.heading_frame, text="Wheels", font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w", padx=20)
        self.number_of_wheels = len(self.wheels)
        if self.number_of_wheels == 4:
            self.wheel_options = ["FL", "FR", "RL", "RR"]
        elif self.number_of_wheels == 6:
            self.wheel_options = ["FL", "FR", "ML", "MR", "RL", "RR"]
        elif self.number_of_wheels == 2:
            self.wheel_options = ["Front", "Rear"]
        else:
            self.wheel_options = [f"Wheel {i + 1}" for i in range(self.number_of_wheels)]

        self.wheel_frame = ttk.Frame(self)
        self.wheel_frame.pack(side="top", fill="x", pady=(10, 8))

        self.default_color = "#F7E7D3"
        self.panel_color = "#F1D0A3"
        self.accent_color = "#C96D3C"
        self.text_color = "#4A2E1D"
        self.subtle_color = "#E7B36C"

        column_specs = (
            ("Wheel", "Wheel", 70),
            ("Tire Friction", "Tire\nFriction", 80),
            ("Damping Rate", "Damping\nRate", 80),
            ("Max Steer Angle", "Max Steer\nAngle", 90),
            ("Radius", "Radius", 60),
            ("Max Brake Torque", "Max Brake\nTorque", 85),
            ("Max Handbrake Torque", "Max\nHandbrake\nTorque", 95)
        )
        columns = tuple(column for column, _, _ in column_specs)
        self.treeview = ttk.Treeview(self, columns=columns, selectmode="browse", show="headings", style="Treeview")
        for column, heading, width in column_specs:
            self.treeview.heading(column, text=heading, anchor="center")
            self.treeview.column(column, width=width, minwidth=width, stretch=False, anchor="center")

        for wheel in wheels:
            self.add_wheel(wheel)

        self.treeview.bind("<Double-1>", self.edit_cell)
        self.treeview.pack(side="top", fill="x", pady=(10, 8))

    def add_wheel(self, wheel):
        wheel_index = len(self.treeview.get_children())
        wheel_name = self.wheel_options[wheel_index] if wheel_index < len(self.wheel_options) else f"Wheel {wheel_index + 1}"
        item = self.treeview.insert("", "end", values=(
            wheel_name,
            wheel.tire_friction,
            wheel.damping_rate,
            wheel.max_steer_angle,
            wheel.radius,
            wheel.max_brake_torque,
            wheel.max_handbrake_torque
        ))
        return item

    def edit_cell(self, event):
        region = self.treeview.identify("region", event.x, event.y)

        if region != "cell":
            return

        column = self.treeview.identify_column(event.x)
        row = self.treeview.identify_row(event.y)

        if not row or not column:
            return

        if column == "#1":
            return

        bbox = self.treeview.bbox(row, column)

        if not bbox:
            return

        x, y, width, height = bbox

        value = self.treeview.set(row, column)

        entry = ttk.Entry(self.treeview)
        entry.insert(0, value)

        entry.place(x=x, y=y, width=width, height=height)
        entry.focus()
        entry.select_range(0, tk.END)

        def save(event=None):
            new_value = entry.get()
            try:
                new_value = float(new_value)
            except ValueError:
                entry.destroy()
                return
            self.treeview.set(row, column, new_value)
            entry.destroy()

        entry.bind("<Return>", save)
        entry.bind("<FocusOut>", save)
        entry.bind("<Escape>", lambda event: entry.destroy())

    def get_data(self):
        wheels = []
        for item in self.treeview.get_children():
            wheel_index = self.treeview.index(item)
            tire_friction = float(self.treeview.set(item, "Tire Friction"))
            damping_rate = float(self.treeview.set(item, "Damping Rate"))
            max_steer_angle = float(self.treeview.set(item, "Max Steer Angle"))
            radius = float(self.treeview.set(item, "Radius"))
            max_brake_torque = float(self.treeview.set(item, "Max Brake Torque"))
            max_handbrake_torque = float(self.treeview.set(item, "Max Handbrake Torque"))

            wheel = carla.WheelPhysicsControl(
                tire_friction=tire_friction,
                damping_rate=damping_rate,
                max_steer_angle=max_steer_angle,
                radius=radius,
                max_brake_torque=max_brake_torque,
                max_handbrake_torque=max_handbrake_torque
            )
            wheels.append(wheel)
        return wheels





