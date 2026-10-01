import tkinter as tk
import carla
from tkinter import ttk
from gui_contents.color_picker import ColorPicker
from utilities.functions import rgb_to_hex, hex_to_rgb


class EntityEditWindow(tk.Toplevel):
    def __init__(self, parent, blueprint, entity=None, type="actor", blueprint_library=None, physic = None):
        super().__init__(parent)

        self.blueprint = blueprint_library.find(blueprint)
        self.entity = entity
        self.type = type
        self.row = None
        self.column = None
        if physic is None:
            self.physic = carla.VehiclePhysicsControl()
        else:
            self.physic = physic

        self.attribute_vars = {}
        self.physics_vars = {
            "torque_curve": [], #TODO: na koniec
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

        self.tabControl.add(self.BlueprintTab, text="Blueprint")
        self.tabControl.add(self.PhysicsTab, text="Physics")
        self.tabControl.add(self.GearsTab, text="Gears")
        self.tabControl.add(self.TorqueTab, text="Torque Curve")
        self.tabControl.add(self.SteeringTab, text="Steering Curve")
        self.tabControl.add(self.WheelsTab, text="Wheels")
        self.tabControl.pack(expand=1, fill="both")


        self.blueprint_parameters_frame = ttk.Frame(self.BlueprintTab)
        self.physics_parameters_frame = ttk.Frame(self.PhysicsTab)
        self.wheels_parameters_frame = ttk.Frame(self.WheelsTab)

        self.blueprint_parameters_frame.grid(row=0, column=0, sticky="nsew")
        self.create_blueprint_section()
        self.physics_parameters_frame.grid(row=1, column=0, sticky="nsew")
        self.create_physic_section()
        self.wheels_parameters_frame.grid(row=2, column=0, sticky="nsew")
        self.create_wheels_section()

    def create_blueprint_section(self):
        heading_frame = ttk.Frame(self.blueprint_parameters_frame)
        heading_frame.grid(row=0, column=0, columnspan=2, sticky="nsew")
        ttk.Label(heading_frame, text="Blueprint Parameters", font=("Arial", 12, "bold")).pack(side="left", padx=20, pady=10)
        parameters_frame = ttk.Frame(self.blueprint_parameters_frame)
        parameters_frame.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.row = 0
        self.column = 0
        for attribute in self.blueprint:

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

        # Torque Curve
        ttk.Label(parameters_frame, text="Torque Curve", font=self.arial_bold_).grid(row=14, column=0, sticky="w", padx=20)
        ttk.Label(parameters_frame, text="(Not Implemented)", font=("Arial", 10, "italic")).grid(row=15, column=0, sticky="w", padx=20)

        # Steering Curve
        ttk.Label(parameters_frame, text="Steering Curve", font=self.arial_bold_).grid(row=16, column=0, sticky="w", padx=20)
        ttk.Label(parameters_frame, text="(Not Implemented)", font=("Arial", 10, "italic")).grid(row=17, column=0, sticky="w", padx=20)

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

        # Gear Control
        gear_table = GearManagerTable(parameters_frame, self.gear_data)
        gear_table.grid(row=11, column=0, sticky="w", padx=20, columnspan=2)



    def create_wheels_section(self):
        pass

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
            ttk.Spinbox(parent_frame, from_=-1e6, to=1e6, textvariable=var).grid(row=self.row, column=self.column, sticky="w", padx=20)

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
            ttk.Spinbox(parent_frame, from_=-1e6, to=1e6, textvariable=var, format="%.2f").grid(row=self.row, column=self.column, sticky="w", padx=20)

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
            var = hex_to_rgb(color_picker.color)

            self.attribute_vars[attribute.id] = {
                "type": attribute.type,
                "var": var
            }

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

        columns = ("Gear", "Ratio", "Down Ratio", "Up Ratio")
        self.treeview = ttk.Treeview(self, columns=columns, show="headings", height=10, style="Treeview", selectmode="browse")
        self.treeview.heading("Gear", text="Gear")
        self.treeview.heading("Ratio", text="Ratio")
        self.treeview.heading("Down Ratio", text="Down Ratio")
        self.treeview.heading("Up Ratio", text="Up Ratio")

        for gear in gears:
            self.add_gear(gear)

        self.add_button = self.treeview.insert("", "end", values=("+ Add Gear", "", "", ""))
        self.treeview.bind("<Double-1>", self.edit_cell)
        self.treeview.bind("<Delete>", self.delete_selected)

        self.treeview.pack(fill="both", expand=True)

    def add_gear(self, gear):
        gear_number = len(self.treeview.get_children())

        item = self.treeview.insert("", self.treeview.index(self.add_button), values=(gear_number, gear.ratio, gear.down_ratio, gear.up_ratio))

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
        for item in self.treeview.get_children():
            if item == self.add_button:
                continue
            self.treeview.set(item, "#1", number)
            number += 1