import queue

import carla
import tkinter as tk

import messagebox

import actors.actors as actors
from datastore.datastore import DataStorage
from carla_server import carla_server
from tkinter import *
from tkinter import ttk
import utilities.functions as utils
from PIL import Image, ImageTk
import numpy as np

from gui_contents.world_and_weather import CustomWorldAndWeatherFrame
from gui_contents.vehicles import CustomVehicleFrame
from gui_contents.StyleSettings import settings
from gui_contents.main_gui_view import CustomMainViewFrame

from utilities.functions import parse_weather_to_dict, parse_weather_to_carla, wait_and_try, return_data_from_transform


class Manager(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("CARLA Manager")
        self.geometry("1180x950")
        self.minsize(1020, 680)
        self.configure(bg="#F7E7D3")

        style = ttk.Style(self)

        if "Autumn" not in style.theme_names():
            style.theme_create(
                "Autumn",
                parent="clam",
                settings=settings
            )

        style.theme_use("Autumn")

        self.database = DataStorage()
        self.carla = None
        self.connected = False
        self.closing = False
        self.disconnecting = False

        self.main_view = None
        self.create_main_window()



    def create_main_window(self):
        self.main_view = MainView(self)
        self.main_view.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self):
        print("Closing app...")
        if self.carla is None:
            self.destroy()
            return
        self.closing = True
        self.disconnect_from_carla()

    def connect_to_carla(self, host="127.0.0.1", port=2000):
        if self.carla is not None:
            return

        self.connected = False

        self.carla = carla_server.CarlaController(
            self.database,
            self,
            host,
            port
        )

        self.carla.start()
        self.after(100, self.check_connection)

    def check_connection(self):
        if self.carla is None:
            return

        try:
            while True:
                status, data = self.carla.connection_queue.get_nowait()

                if status == "connected":
                    if not self.disconnecting and not self.closing:
                        self.connected = True
                        print("Connected to CARLA")

                        self.main_view.initialize_connected_views()
                        self.main_view.main_view.connection_frame.update_connection_state(True)

                elif status == "connection_error":
                    self.connected = False
                    print(f"Connection error: {data}")

                    if not self.closing:
                        messagebox.showerror("CARLA Connection Error",
                                             f"Could not connect to CARLA:\n{data}")
                    self.finish_disconnection()
                    return

                elif status == "disconnected":
                    print("DEBUG: DISCONNECTED EVENT RECEIVED")
                    self.finish_disconnection()
                    return
        except queue.Empty:
            pass

        self.after(100, self.check_connection)

    def finish_disconnection(self):
        print("Disconnected from CARLA")

        self.connected = False
        self.disconnecting = False

        self.main_view.clear_connected_views()

        self.carla = None

        self.database = DataStorage()
        self.main_view.database = self.database
        self.main_view.actor_manager = actors.Actors()
        self.main_view.main_view.connection_frame.update_connection_state(False)

        if self.closing:
            self.destroy()



    def disconnect_from_carla(self):
        print("DEBUG: disconnect_from_carla CALLED")
        if self.carla is None or self.disconnecting:
            return

        print("Disconnecting from CARLA")
        self.disconnecting = True
        self.connected = False
        self.main_view.tabControl.unbind("<<NotebookTabChanged>>")
        self.main_view.tabControl.select(self.main_view.main_tab)
        self.main_view.set_tabs_enabled(False)
        self.carla.shutdown()


class MainView(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        # Databases definition
        self.actor_manager = actors.Actors()
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
        self.vehicle_tab = ttk.Frame(self.tabControl)
        self.actor_tab = ttk.Frame(self.tabControl)
        self.sensors_tab = ttk.Frame(self.tabControl)
        self.simulation_tab = ttk.Frame(self.tabControl)
        self.movement_tab = ttk.Frame(self.tabControl)

        self._control_tab_setup()
        self.tabControl.pack(expand=1, fill="both", padx=18, pady=(10, 18))

        self.main_view = CustomMainViewFrame(self.main_tab, self.manager)
        self.main_view.pack(expand=1, fill="both")

        self.world_weather_frame = None
        self.vehicle_frame = None

    @property
    def carla(self):
        return self.manager.carla

    @property
    def connected(self):
        return self.manager.connected

    def _control_tab_setup(self):
        self.tabControl.add(self.main_tab, text="Main View")
        self.tabControl.add(self.world_weather_tab, text="World & Weather")
        self.tabControl.add(self.vehicle_tab, text="Vehicles")
        self.tabControl.add(self.actor_tab, text="Actors")
        self.tabControl.add(self.sensors_tab, text="Sensors")
        self.tabControl.add(self.movement_tab, text="Movement")
        self.tabControl.add(self.simulation_tab, text="Simulation Parameters")

        self.set_tabs_enabled(False)

    def on_tab_changed(self, event):
        if not self.connected or self.vehicle_frame is None:
            return
        notebook = event.widget
        selected_tab = notebook.select()

        if selected_tab == str(self.vehicle_tab):
            self.vehicle_frame.spawn_ghost()
        else:
            self.vehicle_frame.destroy_ghost()

    def set_tabs_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"

        for tab_id in self.tabControl.tabs()[1:]:
            self.tabControl.tab(tab_id, state=state)

    def initialize_connected_views(self):
        self.world_weather_frame = CustomWorldAndWeatherFrame(self.world_weather_tab, self)
        self.world_weather_frame.pack(expand=True, fill="both")

        self.vehicle_frame = CustomVehicleFrame(self.vehicle_tab, self)
        self.vehicle_frame.pack(expand=True, fill="both")

        self.tabControl.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        self.set_tabs_enabled(True)

    def clear_connected_views(self):
        self.tabControl.unbind("<<NotebookTabChanged>>")

        self.tabControl.select(self.main_tab)
        self.set_tabs_enabled(False)

        if self.world_weather_frame is not None:
            self.world_weather_frame.destroy()
            self.world_weather_frame = None

        if self.vehicle_frame is not None:
            self.vehicle_frame.destroy()
            self.vehicle_frame = None





if __name__ == "__main__":
    app = Manager()
    app.mainloop()
