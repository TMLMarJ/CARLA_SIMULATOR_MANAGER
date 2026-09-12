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

from utilities.functions import parse_weather_to_dict, parse_weather_to_carla, wait_and_try, return_data_from_transform


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
        connection_established = wait_and_try(
            self.database.data_ready,
            10,
            0.5
        )

        if connection_established:
            self.create_main_window()
        else:
            messagebox.showerror("CARLA Manager", "Could not connect to carla server")
            self.destroy()


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

        self.world_weather_frame = CustomWorldAndWeatherFrame(self.world_weather_tab, self)
        self.world_weather_frame.pack(expand=1, fill="both")

        self.vehicle_frame = CustomVehicleFrame(self.vehicle_tab, self)
        self.vehicle_frame.pack(expand=1, fill="both")
        self.tabControl.bind("<<NotebookTabChanged>>", self.on_tab_changed)


    def _control_tab_setup(self):
        self.tabControl.add(self.main_tab, text="Main View")
        self.tabControl.add(self.world_weather_tab, text="World & Weather")
        self.tabControl.add(self.vehicle_tab, text="Vehicles")
        self.tabControl.add(self.actor_tab, text="Actors")
        self.tabControl.add(self.sensors_tab, text="Sensors")
        self.tabControl.add(self.simulation_tab, text="Simulation Parameters")

    def on_tab_changed(self, event):
        notebook = event.widget
        selected_tab = notebook.select()

        if selected_tab == str(self.vehicle_tab):
            self.vehicle_frame.spawn_ghost()
        else:
            self.vehicle_frame.destroy_ghost()




if __name__ == "__main__":
    app = Manager()
    app.mainloop()
