import carla
import tkinter as tk
from PIL import Image, ImageTk
import numpy as np

class VehiclePreview:
    def __init__(self, database, controller):
        self.database = database
        self.controller = controller
        self.ghost = None
        self.camera = None
        self.image = None
        self.blueprint = None
        self.transform = None

    @property
    def world(self):
        return self.database.world

    def spawn(self, blueprint_id="vehicle.audi.a2", transform=None):
        if transform is None:
            transform = self.database.available_spawn_points[0]
        self.controller.carla.spawn_preview_vehicle(blueprint_id, transform)



    def on_camera_image(self, image):
        image.convert(carla.ColorConverter.Raw)
        array = np.frombuffer(image.raw_data, dtype=np.uint8)
        array = np.reshape(array, (image.height, image.width, 4))

        array = array[:, :, :3]
        array = array[:, :, ::-1]

        self.image = Image.fromarray(array)

    def set_transform(self, transform):
        self.controller.carla.set_ghost_transform(transform)

    def get_transform(self):
        self.ghost = self.database.ghost_data
        if self.ghost is not None:
            return self.ghost.get_transform()
        else:
            return None


    def destroy(self):
        self.controller.carla.destroy_preview_vehicle()
        self.image = None

    def update_model(self, blueprint_id):
        self.controller.carla.update_preview_vehicle_model(blueprint_id)

class VehiclePreviewFrame(tk.Frame):
    def __init__(self, parent, preview):
        super().__init__(parent)

        self.preview = preview

        self.camera_label = tk.Label(
            self,
            text="No view",
            anchor="center",
        )

        self.camera_label.pack(fill="both", expand=True)

        self.bind("<Configure>", self.on_resize)

        self.update_image()

    def on_resize(self, event):
        self.update_image()

    def update_image(self):
        if self.preview.image is not None:
            image = self.preview.image.copy()
            image.thumbnail((280, 210))

            photo = ImageTk.PhotoImage(image)

            self.camera_label.configure(image=photo, text="")

            self.camera_label.image = photo


