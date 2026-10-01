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
        self.geometry("1180x1000")
        self.minsize(1020, 900)
        self.configure(bg="#F7E7D3")

        style = ttk.Style(self)

        if "Autumn" not in style.theme_names():
            style.theme_create(
                "Autumn",
                parent="clam",
                settings={

                    # =========================================================
                    # FRAMES
                    # =========================================================
                    "TFrame": {
                        "configure": {
                            "background": "#F7E7D3"
                        }
                    },

                    "new.TFrame": {
                        "configure": {
                            "background": "#F7E7D3"
                        }
                    },

                    # =========================================================
                    # NOTEBOOK
                    # =========================================================
                    "TNotebook": {
                        "configure": {
                            "background": "#F7E7D3",
                            "borderwidth": 0
                        }
                    },

                    "TNotebook.Tab": {
                        "configure": {
                            "background": "#E7B36C",
                            "foreground": "#4A2E1D",
                            "padding": [14, 8],
                            "borderwidth": 0
                        },
                        "map": {
                            "background": [
                                ("selected", "#F1D0A3"),
                                ("active", "#E7B36C")
                            ],
                            "foreground": [
                                ("selected", "#4A2E1D"),
                                ("active", "#4A2E1D")
                            ]
                        }
                    },

                    # =========================================================
                    # LABEL
                    # =========================================================
                    "TLabel": {
                        "configure": {
                            "background": "#F7E7D3",
                            "foreground": "#4A2E1D"
                        }
                    },

                    # =========================================================
                    # BUTTON
                    # =========================================================
                    "TButton": {
                        "configure": {
                            "background": "#C96D3C",
                            "foreground": "#FFF8F5",
                            "padding": [14, 8],
                            "borderwidth": 0,
                            "relief": "flat",
                            "font": ("Segoe UI", 9, "bold")
                        },
                        "map": {
                            "background": [
                                ("pressed", "#9E4D27"),
                                ("active", "#B55E2F"),
                                ("disabled", "#D8B9A5")
                            ],
                            "foreground": [
                                ("disabled", "#F0DDD2"),
                                ("pressed", "#FFFFFF"),
                                ("active", "#FFF8F5")
                            ]
                        }
                    },

                    # =========================================================
                    # CHECKBUTTON
                    # =========================================================
                    "TCheckbutton": {
                        "configure": {
                            "background": "#F7E7D3",
                            "foreground": "#4A2E1D",
                            "padding": [4, 4],
                            "borderwidth": 0,
                            "font": ("Segoe UI", 9)
                        },
                        "map": {
                            "background": [
                                ("active", "#F7E7D3")
                            ],
                            "foreground": [
                                ("active", "#C96D3C"),
                                ("disabled", "#A88F82")
                            ]
                        }
                    },

                    # =========================================================
                    # COMBOBOX
                    # =========================================================
                    "TCombobox": {
                        "configure": {
                            "fieldbackground": "#FFF9F3",
                            "background": "#C96D3C",
                            "foreground": "#4A2E1D",
                            "padding": 6,
                            "borderwidth": 0
                        },
                        "map": {
                            "fieldbackground": [
                                ("readonly", "#FFF9F3")
                            ],
                            "selectbackground": [
                                ("focus", "#E7B36C")
                            ],
                            "selectforeground": [
                                ("focus", "#4A2E1D")
                            ]
                        }
                    },

                    # =========================================================
                    # SPINBOX
                    # =========================================================
                    "TSpinbox": {
                        "configure": {
                            "fieldbackground": "#FFF9F3",
                            "background": "#F9D7A8",
                            "foreground": "#4A2E1D",
                            "padding": 4,
                            "borderwidth": 0
                        }
                    },

                    # =========================================================
                    # TREEVIEW
                    # =========================================================
                    "Treeview": {
                        "configure": {
                            "background": "#FFF9F3",
                            "fieldbackground": "#FFF9F3",
                            "foreground": "#4A2E1D",
                            "rowheight": 28,
                            "borderwidth": 0,
                            "padding": 0
                        },
                        "map": {
                            "background": [
                                ("selected", "#E7B36C")
                            ],
                            "foreground": [
                                ("selected", "#4A2E1D")
                            ]
                        }
                    },

                    "Treeview.Heading": {
                        "configure": {
                            "background": "#F1D0A3",
                            "foreground": "#4A2E1D",
                            "padding": [8, 8],
                            "borderwidth": 0,
                            "font": ("Segoe UI", 9, "bold")
                        },
                        "map": {
                            "background": [
                                ("active", "#E7B36C")
                            ],
                            "foreground": [
                                ("active", "#4A2E1D")
                            ]
                        }
                    }
                }
            )

        style.theme_use("Autumn")

        self.database = DataStorage()
        try:
            self.carla = carla_server.CarlaController(self.database, self)
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
        self.main_view.vehicle_frame.destroy_ghost()

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
        self.tabControl.add(self.movement_tab, text="Movement")
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
