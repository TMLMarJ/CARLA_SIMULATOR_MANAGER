import queue
import multiprocessing

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
from gui_contents.pedestrians import CustomPedestrianFrame
from gui_contents.static_objects import CustomStaticObjectFrame
from gui_contents.sensors import CustomSensorFrame
from gui_contents.StyleSettings import settings
from gui_contents.main_gui_view import CustomMainViewFrame
from spectator_preview import SpectatorPreviewManager

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
        self.world_changing = False
        self.spectator_preview = SpectatorPreviewManager()
        self.spectator_state = "Closed"
        self.spectator_check_job = None

        self.main_view = None
        self.create_main_window()
        self.check_spectator_preview()

    def create_main_window(self):
        self.main_view = MainView(self)
        self.main_view.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self):
        if self.closing:
            return
        print("Closing app...")
        self.closing = True
        self.close_spectator_preview()
        if self.carla is None:
            self.finish_application_close()
            return
        self.disconnect_from_carla()

    def connect_to_carla(self, host="127.0.0.1", port=2000):
        if self.carla is not None:
            return
        self.spectator_preview.poll_events()
        if self.spectator_preview.is_active():
            messagebox.showinfo(
                "Spectator View",
                "Wait for Spectator View to close before connecting again."
            )
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
                        if self.spectator_preview.is_active():
                            self.update_spectator_state("Stopping")
                        else:
                            self.update_spectator_state("Closed")

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
        self.world_changing = False
        self.spectator_preview.request_stop()

        self.main_view.clear_connected_views()

        self.carla = None

        self.database = DataStorage()
        self.main_view.database = self.database
        self.main_view.actor_manager = actors.Actors()
        self.main_view.main_view.connection_frame.update_connection_state(False)

        if self.closing:
            self.finish_application_close()
        elif self.spectator_preview.is_active():
            self.update_spectator_state("Stopping")
        else:
            self.update_spectator_state("Closed")

    def disconnect_from_carla(self):
        print("DEBUG: disconnect_from_carla CALLED")
        if self.carla is None or self.disconnecting:
            return

        print("Disconnecting from CARLA")
        self.disconnecting = True
        self.connected = False
        self.world_changing = False
        self.main_view.tabControl.unbind("<<NotebookTabChanged>>")
        self.main_view.actors_tab_control.unbind("<<NotebookTabChanged>>")
        self.main_view.tabControl.select(self.main_view.main_tab)
        self.main_view.set_tabs_enabled(False)
        self.close_spectator_preview()
        self.carla.shutdown()

    def open_spectator_preview(self):
        if (
            not self.connected or
            self.disconnecting or
            self.closing or
            self.world_changing or
            self.carla is None
        ):
            return

        try:
            preview_config = (
                self.main_view.main_view.spectator_frame.get_preview_config()
            )
            started = self.spectator_preview.start(
                self.carla.host,
                self.carla.port,
                preview_config
            )
        except ValueError as error:
            messagebox.showerror(
                "Invalid Spectator View Settings",
                str(error)
            )
            return
        except Exception as error:
            self.update_spectator_state("Error")
            messagebox.showerror(
                "Spectator View Error",
                f"Could not start Spectator View:\n{error}"
            )
            return

        if started:
            self.update_spectator_state("Starting")

    def close_spectator_preview(self):
        if self.spectator_preview.request_stop():
            self.update_spectator_state("Stopping")

    def check_spectator_preview(self):
        self.spectator_check_job = None

        for status, data in self.spectator_preview.poll_events():
            if status == "starting":
                self.update_spectator_state("Starting")
            elif status == "running":
                self.update_spectator_state("Running")
            elif status == "error":
                self.update_spectator_state("Error")
                if (
                    not self.closing and
                    not self.disconnecting and
                    not self.world_changing
                ):
                    messagebox.showerror(
                        "Spectator View Error",
                        f"Spectator View stopped because of an error:\n{data}"
                    )
            elif status == "closed":
                reason = data.get("reason") if isinstance(data, dict) else data
                exit_code = data.get("exit_code") if isinstance(data, dict) else None
                if reason == "process_exited" and exit_code not in (None, 0):
                    self.update_spectator_state("Error")
                    if (
                        not self.closing and
                        not self.disconnecting and
                        not self.world_changing
                    ):
                        messagebox.showerror(
                            "Spectator View Error",
                            f"Spectator View process exited with code {exit_code}."
                        )
                elif reason != "error" or self.spectator_state != "Error":
                    self.update_spectator_state("Closed")

        if self.spectator_preview.stop_timed_out(10.0):
            stopped = self.spectator_preview.force_stop()
            if stopped:
                if self.closing or self.disconnecting or self.world_changing:
                    self.update_spectator_state("Closed")
                else:
                    self.update_spectator_state("Error")
                    messagebox.showerror(
                        "Spectator View Error",
                        "Spectator View did not close in time and was terminated."
                    )

        if self.winfo_exists():
            self.spectator_check_job = self.after(
                100,
                self.check_spectator_preview
            )

    def update_spectator_state(self, state):
        self.spectator_state = state
        if self.main_view is not None:
            self.main_view.main_view.spectator_frame.update_state(
                state,
                self.connected and
                not self.disconnecting and
                not self.world_changing and
                not self.closing
            )

    def begin_world_change(self):
        if not self.connected or self.disconnecting or self.closing:
            return False
        self.world_changing = True
        if self.spectator_preview.request_stop():
            self.update_spectator_state("Stopping")
        else:
            self.update_spectator_state(self.spectator_state)
        return True

    def finish_world_change(self):
        self.world_changing = False
        if self.connected and not self.spectator_preview.is_active():
            self.update_spectator_state("Closed")
        else:
            self.update_spectator_state(self.spectator_state)

    def finish_application_close(self):
        self.spectator_preview.poll_events()
        if self.spectator_preview.is_active():
            if self.spectator_preview.stop_timed_out(10.0):
                if not self.spectator_preview.force_stop():
                    self.after(100, self.finish_application_close)
                    return
            else:
                self.after(100, self.finish_application_close)
                return

        if self.spectator_check_job is not None:
            self.after_cancel(self.spectator_check_job)
            self.spectator_check_job = None
        self.destroy()


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
        self.actors_tab = ttk.Frame(self.tabControl)
        self.sensors_tab = ttk.Frame(self.tabControl)
        self.movement_tab = ttk.Frame(self.tabControl)

        self.actors_tab_control = ttk.Notebook(self.actors_tab)
        self.vehicle_tab = ttk.Frame(self.actors_tab_control)
        self.actor_tab = ttk.Frame(self.actors_tab_control)
        self.static_object_tab = ttk.Frame(self.actors_tab_control)

        self._control_tab_setup()
        self.tabControl.pack(expand=1, fill="both", padx=18, pady=(10, 18))
        self.actors_tab_control.pack(expand=1, fill="both", padx=8, pady=8)

        self.main_view = CustomMainViewFrame(self.main_tab, self.manager)
        self.main_view.pack(expand=1, fill="both")

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

    def _control_tab_setup(self):
        self.tabControl.add(self.main_tab, text="Main View")
        self.tabControl.add(self.world_weather_tab, text="World & Weather")
        self.tabControl.add(self.actors_tab, text="Actors")
        self.tabControl.add(self.sensors_tab, text="Sensors")
        self.tabControl.add(self.movement_tab, text="Movement")

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

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = Manager()
    app.mainloop()
