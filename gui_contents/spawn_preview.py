import carla
import tkinter as tk
import threading
from PIL import Image, ImageTk
import numpy as np

class GhostPreview:
    def __init__(self, database, controller):
        self.database = database
        self.controller = controller
        self.ghost = None
        self.camera = None
        self.image = None
        self.blueprint = None
        self.transform = None
        self._image_lock = threading.Lock()
        self._image_revision = 0

    @property
    def world(self):
        return self.database.world

    def spawn(self, blueprint_id="vehicle.audi.a2", transform=None):
        if transform is None:
            transform = self.database.available_spawn_points[0]
        self.controller.carla.spawn_preview_actor(
            blueprint_id,
            transform,
            self.on_camera_image,
        )



    def on_camera_image(self, image):
        image.convert(carla.ColorConverter.Raw)
        array = np.frombuffer(image.raw_data, dtype=np.uint8)
        array = np.reshape(array, (image.height, image.width, 4))

        array = array[:, :, :3]
        array = array[:, :, ::-1]

        latest_image = Image.fromarray(array)
        with self._image_lock:
            self.image = latest_image
            self._image_revision += 1

    def get_image_snapshot(self):
        with self._image_lock:
            if self.image is None:
                return self._image_revision, None
            return self._image_revision, self.image.copy()

    def set_transform(self, transform):
        self.controller.carla.set_ghost_transform(transform)

    def get_transform(self):
        self.ghost = self.database.ghost_data
        if self.ghost is not None:
            return self.ghost.get_transform()
        else:
            return None


    def destroy(self):
        self.controller.carla.destroy_preview_actor()
        with self._image_lock:
            self.image = None
            self._image_revision += 1

    def update_model(self, blueprint_id):
        self.controller.carla.update_preview_actor_model(blueprint_id)

class ActorPreviewFrame(tk.Frame):
    refresh_interval_ms = 100

    def __init__(self, parent, preview):
        super().__init__(parent)

        self.preview = preview
        self._last_image_revision = -1
        self._refresh_job = None

        self.camera_label = tk.Label(
            self,
            text="No view",
            anchor="center",
        )

        self.camera_label.pack(fill="both", expand=True)

        self.bind("<Configure>", self.on_resize)
        self.bind("<Destroy>", self._on_destroy, add="+")

        self.update_image()
        self._schedule_refresh()

    def on_resize(self, event):
        self.update_image()

    def update_image(self, force=False):
        revision, image = self.preview.get_image_snapshot()
        if not force and revision == self._last_image_revision:
            return
        self._last_image_revision = revision

        if image is not None:
            image.thumbnail((280, 210))

            photo = ImageTk.PhotoImage(image)

            self.camera_label.configure(image=photo, text="")

            self.camera_label.image = photo
        else:
            self.camera_label.configure(image="", text="No view")
            self.camera_label.image = None

    def _schedule_refresh(self):
        if self.winfo_exists():
            self._refresh_job = self.after(self.refresh_interval_ms, self._refresh_preview)

    def _refresh_preview(self):
        self._refresh_job = None
        self.update_image()
        self._schedule_refresh()

    def _on_destroy(self, event):
        if event.widget is self and self._refresh_job is not None:
            self.after_cancel(self._refresh_job)
            self._refresh_job = None


# Backwards-compatible alias for code which still imports the old widget name.
VehiclePreviewFrame = ActorPreviewFrame


