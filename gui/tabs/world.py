import tkinter as tk
import time
import queue
from tkinter import ttk
from PIL import Image, ImageDraw, ImageOps, ImageTk

from preview.map import MapPreviewCache
from utilities.functions import parse_weather_to_dict, parse_weather_to_carla
import messagebox

class CustomWorldAndWeatherFrame(tk.Frame):
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

        # World & Weather Tab
        # World Config
        self.world_control_frame = ttk.LabelFrame(
            self,
            text="World & Map",
            style="Section.TLabelframe",
            padding=(12, 8)
        )
        self.world_control_frame.pack(fill="x", padx=18, pady=(10, 6))
        self.world_control_frame_selection = ttk.Frame(self.world_control_frame, style="new.TFrame")
        self.world_control_frame_image = ttk.Frame(self.world_control_frame, style="new.TFrame")
        self.world_control_frame_selection.grid(row=0, column=0, sticky="nsw", padx=(8, 20), pady=(2, 4))
        self.world_control_frame_image.grid(row=0, column=1, sticky="nsew", padx=8, pady=(2, 4))
        self.world_control_frame.grid_columnconfigure(0, weight=0)
        self.world_control_frame.grid_columnconfigure(1, weight=1)
        self.world_control_frame.grid_rowconfigure(0, weight=1)
        self.map_display = None
        self.display = None
        self.disp_label = None
        self.world_change_pending = False
        self.world_change_check = None
        self.map_preview_cache = MapPreviewCache()
        self.map_preview_check = None
        self._world_control_frame_setup()
        self._poll_map_preview_events()

        # Weather Config
        self.weather_control_frame = ttk.LabelFrame(
            self,
            text="Weather Parameters",
            style="Section.TLabelframe",
            padding=(12, 8)
        )
        self.weather_control_frame.pack(expand=1, fill="both", padx=18, pady=(6, 12))
        self._weather_variables_definition()
        self._weather_control_frame_setup()

    def _world_control_frame_setup(self):
        heading = tk.Label(self.world_control_frame_selection, text="World Control", bg=self.default_color,
                           fg=self.text_color, font=("Arial", 18, "bold"))
        heading.grid(row=0, column=0, sticky="NW", pady=(0, 6))

        heading2 = tk.Label(self.world_control_frame_selection, text="Map select:", bg=self.default_color,
                            fg=self.text_color, font=("Arial", 12, "bold"))
        heading2.grid(row=1, column=0, sticky="W")
        self.selected_map = tk.StringVar()
        select_map_combobox = ttk.Combobox(self.world_control_frame_selection, width=27,
                                           textvariable=self.selected_map)
        select_map_combobox["values"] = list(self.database.world_lib)[:-1]
        select_map_combobox.current(0)
        select_map_combobox.grid(row=2, column=0, sticky="ew")
        select_map_combobox.bind("<<ComboboxSelected>>", self.update_image)

        self.apply_world_button = ttk.Button(
            self.world_control_frame_selection,
            text="Apply",
            command=self._apply_world,
            width=10
        )
        self.apply_world_button.grid(row=3, column=0, sticky="w", pady=(12, 0))

        self.map_display = self._load_map_image()
        self.display = ImageTk.PhotoImage(self.map_display)
        self.disp_label = tk.Label(self.world_control_frame_image, image=self.display, bg=self.panel_color,
                                   highlightthickness=0)
        self.disp_label.grid(row=0, column=0, sticky="nsew")
        self.world_control_frame_image.grid_columnconfigure(0, weight=1)
        self.world_control_frame_image.grid_rowconfigure(0, weight=1)

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
        with self.database.lock:
            weather = dict(self.database.actual_weather_data or {})
        if not weather:
            return

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
        heading = tk.Label(
            self.weather_control_frame,
            text="Weather Control",
            bg=self.default_color,
            fg=self.text_color,
            font=("Arial", 18, "bold")
        )
        heading.grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 6))

        preset_label = tk.Label(
            self.weather_control_frame,
            text="Custom Presets",
            bg=self.default_color,
            fg=self.text_color,
            font=("Arial", 10, "bold")
        )
        preset_label.grid(row=1, column=0, sticky="w", padx=(8, 6), pady=(0, 6))
        self.selected_weather_preset = tk.StringVar()
        select_weather_preset_combobox = ttk.Combobox(
            self.weather_control_frame,
            width=27,
            textvariable=self.selected_weather_preset
        )
        select_weather_preset_combobox["values"] = list(self.weather_presets_data.keys())[:-1]
        select_weather_preset_combobox.current(0)
        select_weather_preset_combobox.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(0, 6))
        select_weather_preset_combobox.bind("<<ComboboxSelected>>", self.update_weather_variables)

        weather_columns = (
            (
                ("Cloudiness (0-100)", self.cloudiness_var, 0, 100, 0.1),
                ("Precipitation Deposits (0-100)", self.precipitation_deposits_var, 0, 100, 0.1),
                ("Sun Azimuth Angle (0-360)", self.sun_azimuth_angle_var, 0, 360, 0.1),
                ("Fog Density (0-100)", self.fog_density_var, 0, 100, 0.1),
                ("Fog Falloff (>0)", self.fog_falloff_var, 0, 1e12, 0.00001),
            ),
            (
                ("Precipitation (0-100)", self.precipitation_var, 0, 100, 0.1),
                ("Wind Intensity (0-100)", self.wind_intensity_var, 0, 100, 0.1),
                ("Sun Altitude (-90-90)", self.sun_altitude_angle_var, -90, 90, 0.1),
                ("Fog Distance (>0)", self.fog_distance_var, 0, 1e12, 0.0001),
                ("Wetness (0-100)", self.wetness_var, 0, 100, 0.1),
            ),
            (
                ("Scattering Intensity (>0)", self.scattering_intensity_var, 0, 1e9, 0.000001),
                ("Mie Scattering Scale (>0)", self.mie_scattering_scale_var, 0, 1e9, 0.0001),
                ("Rayleigh Scattering Scale (>0)", self.rayleigh_scattering_scale_var, 0, 1e9, 0.001),
                ("Dust Storm (0-100)", self.dust_storm_var, 0, 100, 0.1),
            ),
            (

            ),
        )

        for column, weather_items in enumerate(weather_columns):
            column_frame = ttk.Frame(self.weather_control_frame, style="new.TFrame")
            column_frame.grid(row=2, column=column, sticky="new", padx=8, pady=(0, 4))
            column_frame.grid_columnconfigure(0, weight=1)

            for item_index, (label_text, variable, minimum, maximum, increment) in enumerate(weather_items):
                row = item_index * 2
                label = tk.Label(
                    column_frame,
                    text=label_text,
                    bg=self.default_color,
                    fg=self.text_color,
                    font=("Arial", 10)
                )
                label.grid(row=row, column=0, sticky="w", pady=(4, 0))
                spinbox = tk.Spinbox(
                    column_frame,
                    from_=minimum,
                    to=maximum,
                    increment=increment,
                    textvariable=variable,
                    font=("Arial", 10),
                    width=20
                )
                spinbox.grid(row=row + 1, column=0, sticky="ew", pady=(0, 2))

        button_frame = ttk.Frame(self.weather_control_frame, style="new.TFrame")
        button_frame.grid(row=3, column=0, columnspan=3, sticky="e", padx=8, pady=(4, 2))
        apply_button = ttk.Button(
            button_frame,
            text="Apply",
            command=self._apply_weather_data,
            width=10
        )
        apply_button.pack(side="left", padx=(0, 8))
        refresh_button = ttk.Button(
            button_frame,
            text="Refresh",
            command=self.reset_weather,
            width=10
        )
        refresh_button.pack(side="left")

        for column in range(3):
            self.weather_control_frame.grid_columnconfigure(column, weight=1, uniform="weather")

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
        self.map_display = self._load_map_image()
        self.display = ImageTk.PhotoImage(self.map_display)

        self.disp_label.config(image=self.display)
        self.disp_label.image = self.display

    def _load_map_image(self):
        map_image = self.map_preview_cache.load(self.selected_map.get())
        if map_image is None:
            map_image = self._map_preview_placeholder()
        fitted = ImageOps.contain(
            map_image,
            (450, 270),
            Image.Resampling.LANCZOS,
        )
        display_image = Image.new("RGB", (450, 270), (20, 24, 30))
        display_image.paste(
            fitted,
            ((450 - fitted.width) // 2, (270 - fitted.height) // 2),
        )
        return display_image

    @staticmethod
    def _map_preview_placeholder():
        image = Image.new("RGB", (450, 270), (20, 24, 30))
        draw = ImageDraw.Draw(image)
        lines = (
            "Preview not generated",
            "It will be created after this map is loaded.",
        )
        y = 112
        for line in lines:
            bounds = draw.textbbox((0, 0), line)
            width = bounds[2] - bounds[0]
            draw.text(((450 - width) / 2, y), line, fill=(190, 196, 202))
            y += 22
        return image

    def _poll_map_preview_events(self):
        self.map_preview_check = None
        try:
            while True:
                event_name, data = self.database.map_preview_event_queue.get_nowait()
                if (
                    event_name == "map_preview_ready"
                    and self.map_preview_cache.map_names_match(
                        self.selected_map.get(), data.get("map_name")
                    )
                ):
                    self.update_image(None)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.map_preview_check = self.after(100, self._poll_map_preview_events)

    def destroy(self):
        if self.map_preview_check is not None:
            try:
                self.after_cancel(self.map_preview_check)
            except tk.TclError:
                pass
            self.map_preview_check = None
        super().destroy()

    def _apply_world(self):
        if self.world_change_pending:
            return

        manager = self.controller.manager
        if not manager.begin_world_change():
            return

        self.world_change_pending = True
        self.apply_world_button.configure(state="disabled")
        self._wait_for_spectator_close()

    def _wait_for_spectator_close(self):
        self.world_change_check = None
        manager = self.controller.manager

        if (
            not self.winfo_exists() or
            not manager.connected or
            manager.disconnecting or
            manager.closing
        ):
            self._finish_world_change()
            return

        if (
            manager.spectator_preview.is_active()
            or manager.movement_control.is_preview_process_active()
        ):
            self.world_change_check = self.after(
                100,
                self._wait_for_spectator_close
            )
            return

        self._apply_world_after_spectator_close()

    def _apply_world_after_spectator_close(self):
        self.carla.change_world(self.selected_map.get())
        self.world_change_started_at = time.monotonic()
        self._wait_for_world_change()

    def _wait_for_world_change(self):
        self.world_change_check = None
        manager = self.controller.manager
        if (
            not self.winfo_exists()
            or not manager.connected
            or manager.disconnecting
            or manager.closing
        ):
            self._finish_world_change()
            return

        is_finished = self.carla.world_changed_event.is_set()
        if not is_finished and time.monotonic() - self.world_change_started_at < 45.0:
            self.world_change_check = self.after(
                100,
                self._wait_for_world_change,
            )
            return

        try:
            if is_finished:
                self.controller.vehicle_frame.vehicle_table.refresh_table()
                self.controller.actor_frame.actor_table.refresh_table()
                self.controller.static_object_frame.actor_table.refresh_table()
                self.controller.sensor_frame.refresh_table()

            if is_finished and self.database.world_changed:
                self.reset_weather()
                self.controller.vehicle_frame.update_spawn_point()
                self.controller.actor_frame.update_spawn_point()
                self.controller.static_object_frame.update_spawn_point()

                active_preview_frame = self.controller.active_preview_frame
                if active_preview_frame is not None:
                    self.controller._set_active_preview(None)
                    self.controller._set_active_preview(active_preview_frame)
            else:
                error = self.database.world_change_error
                if error is None:
                    error = "Timeout changing map, variables could not be changed."
                messagebox.showerror("Error changing map", error)
        finally:
            self._finish_world_change()

    def _finish_world_change(self):
        self.world_change_pending = False
        if self.winfo_exists() and self.controller.manager.connected:
            self.apply_world_button.configure(state="normal")
        self.controller.manager.finish_world_change()
