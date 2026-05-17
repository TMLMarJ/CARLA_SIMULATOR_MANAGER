import carla
import tkinter as tk
import actors.actors as actors
from datastore.datastore import DataStorage
import threading
import queue
from tkinter import ttk
import utilities.functions as utils
from PIL import Image, ImageTk
import numpy as np

class Manager(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("CARLA Manager")
        self.geometry("600x400")

        self.database = DataStorage()
        try:
            self.carla = CarlaController(self.database)
            self.carla.start()
        except Exception as e:
            raise e

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
        self.actor_manager = actors.Actors()

        self.database = parent.database
        self.carla = parent.carla

        self.add_actor_button = tk.Button(self, text="Add Actor", command=self.add_actor_window)
        self.add_actor_button.pack()

    def add_actor_window(self):
        add_actor = AddActorWindow(self)
        add_actor.protocol("WM_DELETE_WINDOW", add_actor.on_close)



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

class CarlaController:
    def __init__(self, database):
        self.command_queue = queue.Queue()
        self.running = True
        self.database = database
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        client = carla.Client('localhost', 2000)
        world = client.get_world()
        blueprint_library = world.get_blueprint_library()
        self.database.blueprint_lib = blueprint_library
        self.database.world = world
        while self.running:
            if not self.command_queue.empty():
                cmd, data = self.command_queue.get()

                if cmd == "spawn":
                    print("Spawning actor...")
                    bp = blueprint_library.find(data["bp_id"])
                    print(data["bp_id"])
                    transform = carla.Transform(
                        carla.Location(
                            x=data["x"],
                            y=data["y"],
                            z=data["z"]
                        )
                    )

                    actor = world.try_spawn_actor(bp, transform)

                    if actor is None:
                        print("Spawn failed")
                    else:
                        spectator = world.get_spectator()

                        transform = actor.get_transform()
                        spectator.set_transform(transform)
                        with self.database.lock:
                            self.database.add_actor(actor)

                if cmd == "spawn_ghost":
                    bp = blueprint_library.find(data["bp_id"])
                    transform = carla.Transform(
                        carla.Location(
                            x=data["x"],
                            y=data["y"],
                            z=data["z"]
                        )
                    )

                    actor = world.try_spawn_actor(bp, transform)
                    if actor is not None:
                        actor.set_simulate_physics(False)
                        self.database.ghost_data = actor.id



    def shutdown(self):
        print("Shutting down CARLA...")

        self.running = False
        self.thread.join(timeout=2)

        with self.database.lock:
            for actor in self.database.actors_db.actors.values():
                try:
                    actor.destroy()
                except:
                    pass

            self.database.actors_db.actors.clear()

    def spawn_actor(self, actor_data):
        self.command_queue.put(("spawn", actor_data))

    def spawn_ghost(self, actor_data):
        self.command_queue.put(("spawn_ghost", actor_data))


if __name__ == "__main__":
    app = Manager()
    app.mainloop()
