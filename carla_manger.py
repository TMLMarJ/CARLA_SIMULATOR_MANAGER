import carla
import tkinter as tk
import actors.actors as actors

client = carla.Client('localhost', 2000)
client.set_timeout(5.0)

world = client.get_world()
map_name = world.get_map().name

root = tk.Tk()
root.title("CARLA Map")

label = tk.Label(root, text=f"Actual map: {map_name}", font=("Arial", 16))
label.pack(padx=20, pady=20)

root.mainloop()