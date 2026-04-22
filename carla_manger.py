import carla
import tkinter as tk
import actors.actors as actors
from datastore.datastore import DataStorage
import threading
import queue

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
        AddActorWindow(self)



class AddActorWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)

        self.title("Actor Creator")
        self.geometry("600x400")

        self.database = parent.database
        self.carla = parent.carla

        self.actor_location_x = tk.StringVar()
        self.actor_location_y = tk.StringVar()
        self.actor_location_z = tk.StringVar()

        self.submit_frame = tk.Frame(self)
        self.submit_frame.pack(fill="both", expand=True)
        self.location_x_entry = tk.Entry(self.submit_frame, textvariable=self.actor_location_x)
        self.location_y_entry = tk.Entry(self.submit_frame, textvariable=self.actor_location_y)
        self.location_z_entry = tk.Entry(self.submit_frame, textvariable=self.actor_location_z)

        self.location_x_entry.grid(row=0, column=1)
        self.location_y_entry.grid(row=1, column=1)
        self.location_z_entry.grid(row=2, column=1)

        self.save_button = tk.Button(self, text="Save", command=self.save_and_destroy)
        self.save_button.pack()


    def save_and_destroy(self):
        actor_data = {
            "x": float(self.actor_location_x.get()),
            "y": float(self.actor_location_y.get()),
            "z": float(self.actor_location_z.get()),
        }

        self.carla.spawn_actor(actor_data)
        self.destroy()

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

        while self.running:
            if not self.command_queue.empty():
                cmd, data = self.command_queue.get()

                if cmd == "spawn":
                    print("Spawning actor...")
                    bp = blueprint_library.filter('vehicle.*')[0]
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
                        with self.database.lock:
                            self.database.add_actor(actor)



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



if __name__ == "__main__":
    app = Manager()
    app.mainloop()
