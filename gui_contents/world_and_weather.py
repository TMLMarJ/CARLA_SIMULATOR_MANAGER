import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

from utilities.functions import parse_weather_to_dict, parse_weather_to_carla, wait_and_try
import messagebox

class CustomWorldAndWeatherFrame(tk.Frame):
    def __init__(self, parent, controller):
        print("XXXX")
        super().__init__(parent)
        self.controller = controller
        self.database = controller.database
        self.carla = controller.carla
        self.default_color = "#D2F7FA"
        style = ttk.Style()
        style.configure("TFrame", background=self.default_color)

        # World & Weather Tab
        # World Config
        self.world_control_frame = ttk.Frame(self, style="new.TFrame")
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
        self.weather_control_frame = ttk.Frame(self, style="new.TFrame")
        self.weather_control_frame.pack(expand=1, fill="both")
        self._weather_variables_definition()
        self._weather_control_frame_setup()

    def _world_control_frame_setup(self):
        heading = tk.Label(self.world_control_frame_selection, text="World Control", bg=self.default_color,
                           font=("Arial", 18))
        heading.grid(row=0, column=0, sticky="NW")

        heading2 = tk.Label(self.world_control_frame_selection, text="Map select:", bg=self.default_color,
                            font=("Arial", 12))
        heading2.grid(row=1, column=0, sticky="W")
        self.selected_map = tk.StringVar()
        select_map_combobox = ttk.Combobox(self.world_control_frame_selection, width=27,
                                           textvariable=self.selected_map)
        select_map_combobox["values"] = list(self.database.world_lib)[:-1]
        select_map_combobox.current(0)
        select_map_combobox.grid(row=2, column=0)
        select_map_combobox.bind("<<ComboboxSelected>>", self.update_image)

        apply_button = ttk.Button(self.world_control_frame_selection, text="Apply", command=self._apply_world,
                                  width=10)
        apply_button.grid(row=3, column=0, pady=10)

        self.map_display = Image.open("images/maps/" + self.selected_map.get() + ".png")
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

    def reset_weather(self):
        weather = self.database.world.get_weather()
        weather = parse_weather_to_dict(weather)

        self.cloudiness_var.set(0.0)
        self.precipitation_var.set(0.0)
        self.precipitation_deposits_var.set(0.0)
        self.wind_intensity_var.set(0.0)
        self.sun_azimuth_angle_var.set(0.0)
        self.sun_altitude_angle_var.set(0.0)
        self.fog_density_var.set(0.0)
        self.fog_distance_var.set(0.0)
        self.fog_falloff_var.set(0.0)
        self.wetness_var.set(0.0)
        self.scattering_intensity_var.set(0.0)
        self.mie_scattering_scale_var.set(0.0)
        self.rayleigh_scattering_scale_var.set(0.0)
        self.dust_storm_var.set(0.0)

        self.cloudiness_var.set(value=weather["cloudiness"])
        self.precipitation_var.set(value=weather["precipitation"])
        self.precipitation_deposits_var.set(value=weather["precipitation_deposits"])
        self.wind_intensity_var.set(value=weather["wind_intensity"])
        self.sun_azimuth_angle_var.set(value=weather["sun_azimuth_angle"])
        self.sun_altitude_angle_var.set(value=weather["sun_altitude_angle"])
        self.fog_density_var.set(value=weather["fog_density"])
        self.fog_distance_var.set(value=weather["fog_distance"])
        self.fog_falloff_var.set(value=weather["fog_falloff"])
        self.wetness_var.set(value=weather["wetness"])
        self.scattering_intensity_var.set(value=weather["scattering_intensity"])
        self.mie_scattering_scale_var.set(value=weather["mie_scattering_scale"])
        self.rayleigh_scattering_scale_var.set(value=weather["rayleigh_scattering_scale"])
        self.dust_storm_var.set(value=weather["dust_storm"])

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
        select_weather_preset_combobox = ttk.Combobox(self.weather_control_frame, width=27,
                                                      textvariable=self.selected_weather_preset)
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
        spinbox_fog_falloff = tk.Spinbox(self.weather_control_frame, from_=0, to=1e12, increment=0.00001,
                                         textvariable=self.fog_falloff_var)
        row += 1
        spinbox_fog_falloff.grid(row=row, column=col, sticky="W")
        # ================================= Scattering intensity =============================
        row += 1
        label_scattering_intensity = tk.Label(self.weather_control_frame, text="Scattering Intensity (>0)",
                                              bg=self.default_color, font=("Arial", 12))
        label_scattering_intensity.grid(row=row, column=col, sticky="W", pady=(8, 0))
        spinbox_scattering_intensity = tk.Spinbox(self.weather_control_frame, from_=0, to=1e9, increment=0.000001,
                                                  textvariable=self.scattering_intensity_var)
        row += 1
        spinbox_scattering_intensity.grid(row=row, column=col, sticky="W")
        # ================================= Mie scattering Scale =============================
        row += 1
        label_mie_scattering_scale = tk.Label(self.weather_control_frame, text="Mie Scattering Scale (>0)",
                                              bg=self.default_color, font=("Arial", 12))
        label_mie_scattering_scale.grid(row=row, column=col, sticky="W", pady=(8, 0))
        spinbox_mie_scattering_scale = tk.Spinbox(self.weather_control_frame, from_=0, to=1e9, increment=0.0001,
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
        label_wind_intensity.grid(row=row, column=col, sticky="W", pady=(8, 0), padx=(30, 0))
        spinbox_wind_intensity = tk.Spinbox(self.weather_control_frame, from_=0, to=100, increment=0.1,
                                            textvariable=self.wind_intensity_var)
        row += 1
        spinbox_wind_intensity.grid(row=row, column=col, sticky="W", padx=(30, 0))
        # =============================== Sun altitude angle ======================================
        row += 1
        label_sun_altitude_angle = tk.Label(self.weather_control_frame, text="Sun Altitude (-90-90)",
                                            bg=self.default_color, font=("Arial", 12))
        label_sun_altitude_angle.grid(row=row, column=col, sticky="W", pady=(8, 0), padx=(30, 0))
        spinbox_sun_altitude_angle = tk.Spinbox(self.weather_control_frame, from_=-90, to=90, increment=0.1,
                                                textvariable=self.sun_altitude_angle_var)
        row += 1
        spinbox_sun_altitude_angle.grid(row=row, column=col, sticky="W", padx=(30, 0))
        # ================================== Fog distance ========================================
        row += 1
        label_fog_density = tk.Label(self.weather_control_frame, text="Fog Density (>0)",
                                     bg=self.default_color, font=("Arial", 12))
        label_fog_density.grid(row=row, column=col, sticky="W", pady=(8, 0), padx=(30, 0))
        spinbox_fog_density = tk.Spinbox(self.weather_control_frame, from_=0, to=1e12, increment=0.0001,
                                         textvariable=self.fog_density_var)
        row += 1
        spinbox_fog_density.grid(row=row, column=col, sticky="W", padx=(30, 0))
        # ================================= Wetness =============================================
        row += 1
        label_wetness = tk.Label(self.weather_control_frame, text="Wetness (0-100)",
                                 bg=self.default_color, font=("Arial", 12))
        label_wetness.grid(row=row, column=col, sticky="W", pady=(8, 0), padx=(30, 0))
        spinbox_wetness = tk.Spinbox(self.weather_control_frame, from_=0, to=100, increment=0.1,
                                     textvariable=self.wetness_var)
        row += 1
        spinbox_wetness.grid(row=row, column=col, sticky="W", padx=(30, 0))
        # =============================== Rayleigh scattering scale ============================
        row += 1
        label_rayleigh_scattering_scale = tk.Label(self.weather_control_frame,
                                                   text="Rayleigh Scattering Scale (>0)",
                                                   bg=self.default_color, font=("Arial", 12))
        label_rayleigh_scattering_scale.grid(row=row, column=col, sticky="W", pady=(8, 0), padx=(30, 0))
        spinbox_rayleigh_scattering_scale = tk.Spinbox(self.weather_control_frame, from_=0, to=1e9, increment=0.001,
                                                       textvariable=self.rayleigh_scattering_scale_var)
        row += 1
        spinbox_rayleigh_scattering_scale.grid(row=row, column=col, sticky="W", padx=(30, 0))
        # =============================== Dust storm ==========================================
        row += 1
        label_dust_storm = tk.Label(self.weather_control_frame, text="Dust Storm (0-100)",
                                    bg=self.default_color, font=("Arial", 12))
        label_dust_storm.grid(row=row, column=col, sticky="W", pady=(8, 0), padx=(30, 0))
        spinbox_dust_storm = tk.Spinbox(self.weather_control_frame, from_=0, to=100, increment=0.1,
                                        textvariable=self.dust_storm_var)
        row += 1
        spinbox_dust_storm.grid(row=row, column=col, sticky="W", padx=(30, 0))

        # =================================Apply button ========================================
        row += 1
        col = 0
        apply_button = ttk.Button(self.weather_control_frame, text="Apply", command=self._apply_weather_data,
                                  width=10)
        apply_button.grid(row=row, column=col, pady=10)

        col += 1
        refresh_button = ttk.Button(self.weather_control_frame, text="Refresh", command=self.reset_weather,
                                    width=10)
        refresh_button.grid(row=row, column=col, pady=10)

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

    def _apply_world(self):
        self.carla.change_world(self.selected_map.get())
        is_changed = wait_and_try(
            lambda: str(self.database.world.get_map().name).split("/")[-1] == self.selected_map.get(),
        )
        if is_changed:
            self.reset_weather()
            self.controller.vehicle_frame.update_spawn_point()
        else:
            messagebox.showerror("Error changing map", "Timeout changing map, variables could not be changed, refresh values manually!")

    