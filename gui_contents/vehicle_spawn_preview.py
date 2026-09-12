import carla
import tkinter as tk
from PIL import Image, ImageTk
import numpy as np

class VehiclePreview:
    def __init__(self, world):
        self.world = world
        self.ghost = None
        self.camera = None
        self.image = None
        self.blueprint = None
        self.transform = None

    def spawn(self, blueprint_id="vehicle.audi.a2", transform=None):
        if transform is None:
            transform = self.world.get_map().get_spawn_points()[0]
        blueprint_lib = self.world.get_blueprint_library()
        self.blueprint = blueprint_lib.find(blueprint_id)

        self.transform = transform

        self.ghost = self.world.try_spawn_actor(self.blueprint, self.transform)

        if self.ghost is None:
            return

        self.ghost.set_simulate_physics(enabled=False)

        self.ghost.set_enable_gravity(False)

        for wheel in self.ghost.get_physics_control().wheels:
            wheel.max_steer_angle = 0

        camera_bp = blueprint_lib.find('sensor.camera.rgb')

        camera_bp.set_attribute("image_size_x", "640")
        camera_bp.set_attribute("image_size_y", "480")
        camera_bp.set_attribute("fov", "70")

        camera_transform = carla.Transform(
            carla.Location(
                x=transform.location.x + 5,
                y=transform.location.y + 3,
                z=transform.location.z + 2
            ),
            carla.Rotation(
                pitch=-15.0,
                roll=0.0,
                yaw=220.0
            )
        )

        self.camera = self.world.spawn_actor(camera_bp, camera_transform)

        self.camera.listen(self._on_camera_image)

    def _on_camera_image(self, image):
        image.convert(carla.ColorConverter.Raw)
        array = np.frombuffer(image.raw_data, dtype=np.uint8)
        array = np.reshape(array, (image.height, image.width, 4))

        array = array[:, :, :3]
        array = array[:, :, ::-1]

        self.image = Image.fromarray(array)

    def set_transform(self, transform):
        if self.ghost is None:
            return

        self.transform = transform
        self.ghost.set_transform(transform)
        self._update_camera()

    def get_transform(self):
        if self.ghost is not None:
            return self.ghost.get_transform()
        else:
            return None

    def _update_camera(self):
        if self.ghost is None or self.camera is None:
            return

        vehicle_transform = self.ghost.get_transform()

        camera_location = carla.Location(
            x=vehicle_transform.location.x+5,
            y=vehicle_transform.location.y+3,
            z=vehicle_transform.location.z+2
        )

        camera_rotation = carla.Rotation(
            pitch=-15.0,
            yaw=220.0,
            roll=0.0
        )

        self.camera.set_transform(carla.Transform(
            camera_location,
            camera_rotation
        ))

    def destroy(self):

        if self.camera is not None:
            self.camera.stop()
            self.camera.destroy()
            self.camera = None

        if self.ghost is not None:
            self.ghost.destroy()
            self.ghost = None

        self.image = None

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

        self.update_image()

    def update_image(self):
        if self.preview.image is not None:
            image = self.preview.image.copy()
            image.thumbnail((160, 120))

            photo = ImageTk.PhotoImage(image)

            self.camera_label.configure(image=photo, text="")

            self.camera_label.image = photo


