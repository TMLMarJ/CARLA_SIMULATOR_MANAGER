import tkinter as tk
from tkinter import ttk

from core.registries import Actors
from gui.tabs.home import CustomMainViewFrame
from gui.tabs.pedestrians import CustomPedestrianFrame
from gui.tabs.sensors import CustomSensorFrame
from gui.tabs.simulation import SimulationControlFrame
from gui.tabs.static_objects import CustomStaticObjectFrame
from gui.tabs.vehicles import CustomVehicleFrame
from gui.tabs.world import CustomWorldAndWeatherFrame


class MainView(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        # Databases definition
        self.actor_manager = Actors()
        self.manager = parent
        self.database = self.manager.database

        # Constants
        self.default_color = "#F7E7D3"
        self.panel_color = "#F1D0A3"
        self.accent_color = "#C96D3C"
        self.text_color = "#4A2E1D"
        self.subtle_color = "#E7B36C"

        # Tabs definitions
        self.tabControl = ttk.Notebook(self)
        self.main_tab = ttk.Frame(self.tabControl)
        self.world_weather_tab = ttk.Frame(self.tabControl)
        self.actors_tab = ttk.Frame(self.tabControl)
        self.sensors_tab = ttk.Frame(self.tabControl)
        self.simulation_control_tab = ttk.Frame(self.tabControl)

        self.actors_tab_control = ttk.Notebook(self.actors_tab)
        self.vehicle_tab = ttk.Frame(self.actors_tab_control)
        self.actor_tab = ttk.Frame(self.actors_tab_control)
        self.static_object_tab = ttk.Frame(self.actors_tab_control)

        self._control_tab_setup()
        self.tabControl.pack(expand=1, fill="both", padx=18, pady=(10, 18))
        self.actors_tab_control.pack(expand=1, fill="both", padx=8, pady=8)

        self.main_view = CustomMainViewFrame(self.main_tab, self.manager)
        self.main_view.pack(expand=1, fill="both")

        self.simulation_control_frame = SimulationControlFrame(
            self.simulation_control_tab,
            self.manager,
        )
        self.simulation_control_frame.pack(
            expand=True,
            fill="both",
            padx=8,
            pady=8,
        )

        self.world_weather_frame = None
        self.vehicle_frame = None
        self.actor_frame = None
        self.static_object_frame = None
        self.sensor_frame = None
        self.active_preview_frame = None

    @property
    def carla(self):
        return self.manager.carla

    @property
    def connected(self):
        return self.manager.connected

    def open_movement_control(self, actor_id):
        self.manager.open_movement_control(actor_id)

    def close_movement_control_for_actor(self, actor_id, reason):
        self.manager.close_movement_control_for_actor(actor_id, reason)

    def refresh_actor_tables(self):
        """Refresh all Actors views through the owning Manager."""
        self.manager._refresh_actor_tables()

    def _control_tab_setup(self):
        self.tabControl.add(self.main_tab, text="Main View")
        self.tabControl.add(self.world_weather_tab, text="World & Weather")
        self.tabControl.add(self.actors_tab, text="Actors")
        self.tabControl.add(self.sensors_tab, text="Sensors")
        self.tabControl.add(
            self.simulation_control_tab,
            text="Simulation Control",
        )

        self.actors_tab_control.add(self.vehicle_tab, text="Vehicles")
        self.actors_tab_control.add(self.actor_tab, text="Pedestrians")
        self.actors_tab_control.add(self.static_object_tab, text="Static Objects")
        self.actors_tab_control.select(self.vehicle_tab)

        self.set_tabs_enabled(False)

    def on_tab_changed(self, event):
        if (
            not self.connected
            or self.vehicle_frame is None
            or self.actor_frame is None
            or self.static_object_frame is None
        ):
            return
        selected_tab = self.tabControl.select()
        if selected_tab == str(self.actors_tab):
            target_preview_frame = self._selected_actor_frame()
        else:
            target_preview_frame = None

        self._set_active_preview(target_preview_frame)

    def on_actor_tab_changed(self, event):
        if not self.connected or self.tabControl.select() != str(self.actors_tab):
            return
        self._set_active_preview(self._selected_actor_frame())

    def _selected_actor_frame(self):
        selected_tab = self.actors_tab_control.select()
        if selected_tab == str(self.vehicle_tab):
            return self.vehicle_frame
        if selected_tab == str(self.actor_tab):
            return self.actor_frame
        if selected_tab == str(self.static_object_tab):
            return self.static_object_frame
        return None

    def _set_active_preview(self, target_preview_frame):
        if target_preview_frame is self.active_preview_frame:
            return

        if self.active_preview_frame is not None:
            self.active_preview_frame.destroy_ghost()

        self.active_preview_frame = target_preview_frame
        if self.active_preview_frame is not None:
            self.active_preview_frame.spawn_ghost()

    def set_tabs_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"

        for tab_id in self.tabControl.tabs()[1:]:
            self.tabControl.tab(tab_id, state=state)

    def initialize_connected_views(self):
        self.world_weather_frame = CustomWorldAndWeatherFrame(self.world_weather_tab, self)
        self.world_weather_frame.pack(expand=True, fill="both")

        self.vehicle_frame = CustomVehicleFrame(self.vehicle_tab, self)
        self.vehicle_frame.pack(expand=True, fill="both")

        self.actor_frame = CustomPedestrianFrame(self.actor_tab, self)
        self.actor_frame.pack(expand=True, fill="both")

        self.static_object_frame = CustomStaticObjectFrame(self.static_object_tab, self)
        self.static_object_frame.pack(expand=True, fill="both")

        self.sensor_frame = CustomSensorFrame(self.sensors_tab, self)
        self.sensor_frame.pack(expand=True, fill="both")

        self.actors_tab_control.select(self.vehicle_tab)
        self.tabControl.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        self.actors_tab_control.bind("<<NotebookTabChanged>>", self.on_actor_tab_changed)

        self.set_tabs_enabled(True)

    def clear_connected_views(self):
        self.tabControl.unbind("<<NotebookTabChanged>>")
        self.actors_tab_control.unbind("<<NotebookTabChanged>>")
        self.active_preview_frame = None

        self.tabControl.select(self.main_tab)
        self.set_tabs_enabled(False)

        if self.world_weather_frame is not None:
            self.world_weather_frame.destroy()
            self.world_weather_frame = None

        if self.vehicle_frame is not None:
            self.vehicle_frame.destroy()
            self.vehicle_frame = None

        if self.actor_frame is not None:
            self.actor_frame.destroy()
            self.actor_frame = None

        if self.static_object_frame is not None:
            self.static_object_frame.destroy()
            self.static_object_frame = None

        if self.sensor_frame is not None:
            self.sensor_frame.destroy()
            self.sensor_frame = None

