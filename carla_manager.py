import carla
import tkinter as tk
import actors.actors as actors
from datastore.datastore import DataStorage
from carla_server import carla_server
from tkinter import *
from tkinter import ttk
import utilities.functions as utils
from PIL import Image, ImageTk
import numpy as np

from utilities.functions import parse_weather_to_dict, parse_weather_to_carla


class Manager(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("CARLA Manager")
        self.geometry("600x800")

        self.database = DataStorage()
        try:
            self.carla = carla_server.CarlaController(self.database)
            self.carla.start()
        except Exception as e:
            raise e

        self.main_view = None

        self.after(5000, self.create_main_window)

    def create_main_window(self):
        self.main_view = MainView(self)
        self.main_view.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self.on_close)


    def on_close(self):
        print("Closing app...")

        self.carla.shutdown()

        self.destroy()


class MainView(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        # Databases definition
        self.actor_manager = actors.Actors()
        self.database = parent.database

        # Carla connection
        self.carla = parent.carla

        # Constants
        self.default_color = "#D2F7FA"
        style = ttk.Style()
        style.configure("TFrame", background=self.default_color)

        # Tabs definitions
        self.tabControl = ttk.Notebook(self)
        self.main_tab = ttk.Frame(self.tabControl)
        self.world_weather_tab = ttk.Frame(self.tabControl)
        self.vehicle_tab = ttk.Frame(self.tabControl)
        self.actor_tab = ttk.Frame(self.tabControl)
        self.sensors_tab = ttk.Frame(self.tabControl)
        self.simulation_tab = ttk.Frame(self.tabControl)
        self._control_tab_setup()
        self.tabControl.pack(expand=1, fill="both")
        style.configure("new.TFrame", background=self.default_color)

        # World & Weather Tab
        # World Config
        self.world_control_frame = ttk.Frame(self.world_weather_tab, style="new.TFrame")
        self.world_control_frame.pack(expand=1, fill="both")
        self.world_control_frame_selection = ttk.Frame(self.world_control_frame, style="new.TFrame")
        self.world_control_frame_image = ttk.Frame(self.world_control_frame, style="new.TFrame")
        self.world_control_frame_selection.grid(row=0, column=0)
        self.world_control_frame_image.grid(row=0, column=1)
        self.map_display = None
        self.display = None
        self.disp_label = None
        self._world_control_frame_setup()

        # Weather Config
        self.weather_control_frame = ttk.Frame(self.world_weather_tab, style="new.TFrame")
        self.weather_control_frame.pack(expand=1, fill="both")
        self._weather_variables_definition()


        self._weather_control_frame_setup()




        # self.add_actor_button = tk.Button(self, text="Add Actor", command=self.add_actor_window)
        # self.add_actor_button.pack()

    # def add_actor_window(self):
    #     add_actor = AddActorWindow(self)
    #     add_actor.protocol("WM_DELETE_WINDOW", add_actor.on_close)

    def _control_tab_setup(self):
        self.tabControl.add(self.main_tab, text="Main View")
        self.tabControl.add(self.world_weather_tab, text="World & Weather")
        self.tabControl.add(self.vehicle_tab, text="Vehicles")
        self.tabControl.add(self.actor_tab, text="Actors")
        self.tabControl.add(self.sensors_tab, text="Sensors")
        self.tabControl.add(self.simulation_tab, text="Simulation Parameters")

    def _world_control_frame_setup(self):
        heading = tk.Label(self.world_control_frame_selection, text="World Control", bg=self.default_color, font=("Arial", 18))
        heading.grid(row=0, column=0, sticky="NW")

        heading2 = tk.Label(self.world_control_frame_selection, text="Map select:", bg=self.default_color, font=("Arial", 12))
        heading2.grid(row=1, column=0, sticky="W")
        self.selected_map = tk.StringVar()
        select_map_combobox = ttk.Combobox(self.world_control_frame_selection, width=27, textvariable=self.selected_map)
        select_map_combobox["values"] = list(self.database.world_lib)[:-1]
        select_map_combobox.current(0)
        select_map_combobox.grid(row=2, column=0)
        select_map_combobox.bind("<<ComboboxSelected>>", self.update_image)


        apply_button = ttk.Button(self.world_control_frame_selection, text="Apply", command=self._apply_world, width=10)
        apply_button.grid(row=3, column=0, pady=10)

        self.map_display = Image.open("images/maps/"+self.selected_map.get()+".png")
        self.display = ImageTk.PhotoImage(self.map_display)
        self.disp_label = tk.Label(self.world_control_frame_image, image=self.display)
        self.disp_label.grid(row=0, column=0, pady=10, padx=20)

    def _weather_variables_definition(self):
        weather = self.database.actual_weather_data

        self.weather_presets_data = self.database.weather_presets
        self.cloudiness_var = tk.DoubleVar(value=weather["cloudiness"])
        self.precipitation_var = tk.DoubleVar(value=weather["precipitation"])
        self.precipitation_deposits_var = tk.DoubleVar(value=weather["precipitation_deposits"])
        self.wind_intensity_var = tk.DoubleVar(value=weather["wind_intensity"])
        self.sun_azimuth_angle_var = tk.DoubleVar(value=weather["sun_azimuth_angle"])
        self.sun_altitude_angle_var = tk.DoubleVar(value=weather["sun_altitude_angle"])
        self.fog_density_var = tk.DoubleVar(value=weather["fog_density"])
        self.fog_distance_var = tk.DoubleVar(value=weather["fog_distance"])
        self.fog_falloff_var = tk.DoubleVar(value=weather["fog_falloff"])
        self.wetness_var = tk.DoubleVar(value=weather["wetness"])
        self.scattering_intensity_var = tk.DoubleVar(value=weather["scattering_intensity"])
        self.mie_scattering_scale_var = tk.DoubleVar(value=weather["mie_scattering_scale"])
        self.rayleigh_scattering_scale_var = tk.DoubleVar(value=weather["rayleigh_scattering_scale"])
        self.dust_storm_var = tk.DoubleVar(value=weather["dust_storm"])




    def _weather_control_frame_setup(self):
        # ===================================== Heading ========================================
        row = 0
        col = 0
        heading = tk.Label(self.weather_control_frame, text="Weather Control", bg=self.default_color,
                           font=("Arial", 18))
        heading.grid(row=row, column=col, sticky="NW")

        # ===================================== Weather preset ========================================
        row = 1
        label_weather_presets = tk.Label(self.weather_control_frame, text="Custom Presets", bg=self.default_color,
                                         font=("Arial", 12))
        label_weather_presets.grid(row=row, column=col, sticky="W")
        self.selected_weather_preset = tk.StringVar()
        row += 1
        select_weather_preset_combobox = ttk.Combobox(self.weather_control_frame, width=27, textvariable=self.selected_weather_preset)
        select_weather_preset_combobox["values"] = list(self.weather_presets_data.keys())[:-1]
        select_weather_preset_combobox.current(0)
        select_weather_preset_combobox.grid(row=row, column=col, sticky="W")
        select_weather_preset_combobox.bind("<<ComboboxSelected>>", self.update_weather_variables)

        # ===================================== Cloudiness =====================================
        row += 1
        label_cloudiness = tk.Label(self.weather_control_frame, text="Cloudiness (0-100)", bg=self.default_color,
                                    font=("Arial", 12))
        label_cloudiness.grid(row=row, column=col, sticky="W")
        spinbox_cloudiness = tk.Spinbox(self.weather_control_frame, from_=0, to=100, increment=0.1,
                                        textvariable=self.cloudiness_var)
        row += 1
        spinbox_cloudiness.grid(row=row, column=col, sticky="W")
        # ================================ Precipitation Deposits ============================
        row += 1
        label_precipitation_deposits = tk.Label(self.weather_control_frame, text="Precipitation Deposits (0-100)",
                                                bg=self.default_color, font=("Arial", 12))
        label_precipitation_deposits.grid(row=row, column=col, sticky="W", pady=(8, 0))
        spinbox_precipitation_deposits = tk.Spinbox(self.weather_control_frame, from_=0, to=100, increment=0.1,
                                                    textvariable=self.precipitation_deposits_var)
        row += 1
        spinbox_precipitation_deposits.grid(row=row, column=col, sticky="W")
        # =============================== Sun azimuth angle ==================================
        row += 1
        label_sun_azimuth_angle = tk.Label(self.weather_control_frame, text="Sun Azimuth Angle (0-360)",
                                           bg=self.default_color, font=("Arial", 12))
        label_sun_azimuth_angle.grid(row=row, column=col, sticky="W", pady=(8, 0))
        spinbox_sun_azimuth_angle = tk.Spinbox(self.weather_control_frame, from_=0, to=360, increment=0.1,
                                               textvariable=self.sun_azimuth_angle_var)
        row += 1
        spinbox_sun_azimuth_angle.grid(row=row, column=col, sticky="W")
        # =============================== Fog density ========================================
        row += 1
        label_fog_density = tk.Label(self.weather_control_frame, text="Fog Density (0-100)",
                                     bg=self.default_color, font=("Arial", 12))
        label_fog_density.grid(row=row, column=col, sticky="W", pady=(8, 0))
        spinbox_fog_density = tk.Spinbox(self.weather_control_frame, from_=0, to=100, increment=0.1,
                                         textvariable=self.fog_density_var)
        row += 1
        spinbox_fog_density.grid(row=row, column=col, sticky="W")
        # =============================== Fog falloff ========================================
        row += 1
        label_fog_falloff = tk.Label(self.weather_control_frame, text="Fog Falloff (>0)",
                                     bg=self.default_color, font=("Arial", 12))
        label_fog_falloff.grid(row=row, column=col, sticky="W", pady=(8, 0))
        spinbox_fog_falloff = tk.Spinbox(self.weather_control_frame, from_=0, to=1e12, increment=1,
                                         textvariable=self.fog_falloff_var)
        row += 1
        spinbox_fog_falloff.grid(row=row, column=col, sticky="W")
        # ================================= Scattering intensity =============================
        row += 1
        label_scattering_intensity = tk.Label(self.weather_control_frame, text="Scattering Intensity (>0)",
                                               bg=self.default_color, font=("Arial", 12))
        label_scattering_intensity.grid(row=row, column=col, sticky="W", pady=(8, 0))
        spinbox_scattering_intensity = tk.Spinbox(self.weather_control_frame, from_=0, to=1e9, increment=1,
                                                  textvariable=self.scattering_intensity_var)
        row += 1
        spinbox_scattering_intensity.grid(row=row, column=col, sticky="W")
        # ================================= Mie scattering Scale =============================
        row += 1
        label_mie_scattering_scale = tk.Label(self.weather_control_frame, text="Mie Scattering Scale (>0)",
                                              bg=self.default_color, font=("Arial", 12))
        label_mie_scattering_scale.grid(row=row, column=col, sticky="W", pady=(8, 0))
        spinbox_mie_scattering_scale = tk.Spinbox(self.weather_control_frame, from_=0, to=1e9, increment=1,
                                                  textvariable=self.mie_scattering_scale_var)
        row += 1
        spinbox_mie_scattering_scale.grid(row=row, column=col, sticky="W")
        # ================================= Precipitation ====================================
        row = 3
        col = 1
        label_precipitation = tk.Label(self.weather_control_frame, text="Precipitation (0-100)",
                                       bg=self.default_color, font=("Arial", 12))
        label_precipitation.grid(row=row, column=col, sticky="W", pady=(8, 0), padx=(30, 0))
        spinbox_precipitation = tk.Spinbox(self.weather_control_frame, from_=0, to=100, increment=0.1,
                                           textvariable=self.precipitation_var)
        row += 1
        spinbox_precipitation.grid(row=row, column=col, sticky="W", padx=(30, 0))
        # ================================= Wind intensity ==================================
        row += 1
        label_wind_intensity = tk.Label(self.weather_control_frame, text="Wind Intensity (0-100)",
                                        bg=self.default_color, font=("Arial", 12))
        label_wind_intensity.grid(row=row, column=col, sticky="W", pady=(8,0), padx=(30, 0))
        spinbox_wind_intensity = tk.Spinbox(self.weather_control_frame, from_=0, to=100, increment=0.1,
                                            textvariable=self.wind_intensity_var)
        row += 1
        spinbox_wind_intensity.grid(row=row, column=col, sticky="W", padx=(30, 0))
        # =============================== Sun altitude angle ======================================
        row += 1
        label_sun_altitude_angle = tk.Label(self.weather_control_frame, text="Sun Altitude (-90-90)",
                                            bg=self.default_color, font=("Arial", 12))
        label_sun_altitude_angle.grid(row=row, column=col, sticky="W", pady=(8,0), padx=(30, 0))
        spinbox_sun_altitude_angle = tk.Spinbox(self.weather_control_frame, from_=-90, to=90, increment=0.1,
                                                textvariable=self.sun_altitude_angle_var)
        row += 1
        spinbox_sun_altitude_angle.grid(row=row, column=col, sticky="W", padx=(30, 0))
        # ================================== Fog distance ========================================
        row += 1
        label_fog_density = tk.Label(self.weather_control_frame, text="Fog Density (>0)",
                                     bg=self.default_color, font=("Arial", 12))
        label_fog_density.grid(row=row, column=col, sticky="W", pady=(8,0), padx=(30, 0))
        spinbox_fog_density = tk.Spinbox(self.weather_control_frame, from_=0, to=1e12, increment=1,
                                         textvariable=self.fog_density_var)
        row += 1
        spinbox_fog_density.grid(row=row, column=col, sticky="W", padx=(30, 0))
        # ================================= Wetness =============================================
        row += 1
        label_wetness = tk.Label(self.weather_control_frame, text="Wetness (0-100)",
                                 bg=self.default_color, font=("Arial", 12))
        label_wetness.grid(row=row, column=col, sticky="W", pady=(8,0), padx=(30, 0))
        spinbox_wetness = tk.Spinbox(self.weather_control_frame, from_=0, to=100, increment=0.1,
                                     textvariable=self.wetness_var)
        row += 1
        spinbox_wetness.grid(row=row, column=col, sticky="W", padx=(30, 0))
        # =============================== Rayleigh scattering scale ============================
        row += 1
        label_rayleigh_scattering_scale = tk.Label(self.weather_control_frame, text="Rayleigh Scattering Scale (>0)",
                                                   bg=self.default_color, font=("Arial", 12))
        label_rayleigh_scattering_scale.grid(row=row, column=col, sticky="W", pady=(8,0), padx=(30, 0))
        spinbox_rayleigh_scattering_scale = tk.Spinbox(self.weather_control_frame, from_=0, to=100, increment=0.1,
                                                       textvariable=self.rayleigh_scattering_scale_var)
        row += 1
        spinbox_rayleigh_scattering_scale.grid(row=row, column=col, sticky="W", padx=(30, 0))
        # =============================== Dust storm ==========================================
        row += 1
        label_dust_storm = tk.Label(self.weather_control_frame, text="Dust Storm (0-100)",
                                    bg=self.default_color, font=("Arial", 12))
        label_dust_storm.grid(row=row, column=col, sticky="W", pady=(8,0), padx=(30, 0))
        spinbox_dust_storm = tk.Spinbox(self.weather_control_frame, from_=0, to=100, increment=0.1,
                                        textvariable=self.dust_storm_var)
        row += 1
        spinbox_dust_storm.grid(row=row, column=col, sticky="W", padx=(30, 0))

        # =================================Apply button ========================================
        row += 1
        col = 0
        apply_button = ttk.Button(self.weather_control_frame, text="Apply", command=self._apply_weather_data, width=10)
        apply_button.grid(row=row, column=col, pady=10)

    def update_weather_variables(self, event):
        new_weather_preset = self.weather_presets_data[self.selected_weather_preset.get()]
        weather_data = parse_weather_to_dict(new_weather_preset)

        self.cloudiness_var.set(weather_data["cloudiness"])
        self.precipitation_var.set(weather_data["precipitation"])
        self.precipitation_deposits_var.set(weather_data["precipitation_deposits"])
        self.wind_intensity_var.set(weather_data["wind_intensity"])
        self.sun_azimuth_angle_var.set(weather_data["sun_azimuth_angle"])
        self.sun_altitude_angle_var.set(weather_data["sun_altitude_angle"])
        self.fog_density_var.set(weather_data["fog_density"])
        self.fog_distance_var.set(weather_data["fog_distance"])
        self.fog_falloff_var.set(weather_data["fog_falloff"])
        self.wetness_var.set(weather_data["wetness"])
        self.scattering_intensity_var.set(weather_data["scattering_intensity"])
        self.mie_scattering_scale_var.set(weather_data["mie_scattering_scale"])
        self.rayleigh_scattering_scale_var.set(weather_data["rayleigh_scattering_scale"])
        self.dust_storm_var.set(weather_data["dust_storm"])


    def _apply_world(self):
        self.carla.change_world(self.selected_map.get())
        self._weather_variables_definition()

    def _apply_weather_data(self):
        weather_dict = {}
        weather_dict["cloudiness"] = self.cloudiness_var.get()
        weather_dict["precipitation"] = self.precipitation_var.get()
        weather_dict["wind_intensity"] = self.wind_intensity_var.get()
        weather_dict["precipitation_deposits"] = self.precipitation_deposits_var.get()
        weather_dict["sun_azimuth_angle"] = self.sun_azimuth_angle_var.get()
        weather_dict["sun_altitude_angle"] = self.sun_altitude_angle_var.get()
        weather_dict["fog_density"] = self.fog_density_var.get()
        weather_dict["fog_distance"] = self.fog_distance_var.get()
        weather_dict["fog_falloff"] = self.fog_falloff_var.get()
        weather_dict["wetness"] = self.wetness_var.get()
        weather_dict["scattering_intensity"] = self.scattering_intensity_var.get()
        weather_dict["mie_scattering_scale"] = self.mie_scattering_scale_var.get()
        weather_dict["rayleigh_scattering_scale"] = self.rayleigh_scattering_scale_var.get()
        weather_dict["dust_storm"] = self.dust_storm_var.get()

        self.carla.change_weather(parse_weather_to_carla(weather_dict))

    def update_image(self, event):
        self.map_display = Image.open(
            "images/maps/" + self.selected_map.get() + ".png"
        )

        self.display = ImageTk.PhotoImage(self.map_display)

        self.disp_label.config(image=self.display)
        self.disp_label.image = self.display




class AddActorWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)

        self.title("Actor Creator")
        self.geometry("1200x900")

        # Carla connection
        self.database = parent.database
        self.carla = parent.carla
        self.vehicle_classes = ["Car", "Bus", "Van", "Truck", "Motorcycle", "Bicycle"]

        self.latest_frame = None
        self.running = True
        self.camera = None
        self.starting_loc =(0, 0, -500)

        # tk variables definition
        self.actor_location_x = tk.StringVar()
        self.actor_location_x.set(str(self.starting_loc[0]))
        # self.actor_location_x.trace_add("write", self.update_ghost)

        self.actor_location_y = tk.StringVar()
        self.actor_location_y.set(str(self.starting_loc[1]))
        # self.actor_location_y.trace_add("write", self.update_ghost)

        self.actor_location_z = tk.StringVar()
        self.actor_location_z.set(str(self.starting_loc[2]))
        # self.actor_location_z.trace_add("write", self.update_ghost)

        self.choose_class = tk.StringVar()
        self.choose_model = tk.StringVar()

        # Main frame
        self.submit_frame = tk.Frame(self)
        self.submit_frame.pack(fill="both", expand=True)
        # Spectator
        self.label_camera = tk.Label(self.submit_frame)
        self.setup_viewer()
        # Actor location entry
        self.location_x_entry = tk.Entry(self.submit_frame, textvariable=self.actor_location_x)
        self.location_x_entry.bind("<Return>", self.update_ghost)

        self.location_y_entry = tk.Entry(self.submit_frame, textvariable=self.actor_location_y)
        self.location_y_entry.bind("<Return>", self.update_ghost)

        self.location_z_entry = tk.Entry(self.submit_frame, textvariable=self.actor_location_z)
        self.location_z_entry.bind("<Return>", self.update_ghost)
        # Actor class selection
        self.actor_class_combobox = ttk.Combobox(self.submit_frame, width=27, textvariable=self.choose_class)
        self.actor_class_combobox["values"] = self.vehicle_classes
        self.actor_class_combobox.current(0)
        self.actor_class_combobox.bind("<<ComboboxSelected>>", self.update_model)
        # Actor selection by class
        self.actor_model = utils.filter_carla_id_by_type("vehicle", self.choose_class.get().lower(), self.database.blueprint_lib)
        self.actor_model_combobox = ttk.Combobox(self.submit_frame, width=27, textvariable=self.choose_model)
        self.actor_model_combobox["values"] = list(self.actor_model.keys())
        self.actor_model_combobox.current(0)
        self.actor_model_combobox.bind("<<ComboboxSelected>>", self.update_ghost)

        self.location_x_entry.grid(row=0, column=1)
        self.location_y_entry.grid(row=1, column=1)
        self.location_z_entry.grid(row=2, column=1)
        self.actor_class_combobox.grid(row=3, column=1)
        self.actor_model_combobox.grid(row=4, column=1)
        self.label_camera.grid(row=5, column=1)

        self.save_button = tk.Button(self, text="Save", command=self.save_and_destroy)
        self.save_button.pack()

        self.spawn_ghost()


    def save_and_destroy(self):
        actor_data = {
            "x": float(self.actor_location_x.get()),
            "y": float(self.actor_location_y.get()),
            "z": float(self.actor_location_z.get()),
            "bp_id": str(self.actor_model[str(self.choose_model.get())]),
        }
        ghost_id = self.database.ghost_data
        ghost = self.database.world.get_actor(ghost_id)

        if ghost is not None:
            ghost.destroy()
        self.carla.spawn_actor(actor_data)
        self.destroy()


    def update_model(self, event):
        self.actor_model = utils.filter_carla_id_by_type("vehicle", self.choose_class.get().lower(),
                                                         self.database.blueprint_lib)
        self.actor_model_combobox["values"] = list(self.actor_model.keys())
        self.actor_model_combobox.current(0)

    def on_close(self):
        ghost_id = self.database.ghost_data
        ghost = self.database.world.get_actor(ghost_id)

        if ghost is not None:
            ghost.destroy()
            self.database.ghost_data = None

        self.running = False

        self.camera.stop()
        self.camera.destroy()

        self.destroy()


    def spawn_ghost(self):
        try:
            actor_data = {
                "x": float(self.actor_location_x.get()),
                "y": float(self.actor_location_y.get()),
                "z": float(self.actor_location_z.get()),
                "bp_id": str(self.actor_model[str(self.choose_model.get())]),
            }
        except Exception as e:
            actor_data = {
                "x": 0,
                "y": 0,
                "z": 0,
                "bp_id": str(self.actor_model[str(self.choose_model.get())]),
            }

        self.carla.spawn_ghost(actor_data)

    def update_ghost(self, event, *args):
        ghost_id = self.database.ghost_data
        ghost = self.database.world.get_actor(ghost_id)

        if ghost is not None:
            ghost.destroy()
            self.database.world.tick()

        self.spawn_ghost()

    def setup_viewer(self):
        camera_bp = self.database.blueprint_lib.find("sensor.camera.rgb")

        camera_bp.set_attribute("image_size_x", "800")
        camera_bp.set_attribute("image_size_y", "600")

        self.camera = self.database.world.spawn_actor(
            camera_bp,
            transform=carla.Transform(
                carla.Location(
                    x=self.starting_loc[0]+4,
                    y=self.starting_loc[1]+1,
                    z=self.starting_loc[2]+2
                ),
                carla.Rotation(yaw=200, pitch=-10, roll=0)
            )
        )

        self.camera.listen(self.process_image)
        self.update_view()

    def process_image(self, image):

        array = np.frombuffer(
            image.raw_data,
            dtype=np.uint8
        )

        array = array.reshape(
            (image.height, image.width, 4)
        )

        array = array[:, :, :3]
        array = array[:, :, ::-1]

        self.latest_frame = array

    def update_view(self):

        if not self.running:
            return
        try:
            transform = carla.Transform(
                    carla.Location(
                        x=float(self.actor_location_x.get())+4,
                        y=float(self.actor_location_y.get())+1,
                        z=float(self.actor_location_z.get())+2,
                    ),
                    carla.Rotation(yaw=200, pitch=-10, roll=0)
                )
        except Exception as e:
            transform = self.camera.get_transform()
        self.camera.set_transform(transform)

        if self.latest_frame is not None:
            img = Image.fromarray(self.latest_frame)

            imgtk = ImageTk.PhotoImage(image=img)

            self.label_camera.imgtk = imgtk
            self.label_camera.configure(image=imgtk)

        self.after(500, self.update_view)




if __name__ == "__main__":
    app = Manager()
    app.mainloop()
