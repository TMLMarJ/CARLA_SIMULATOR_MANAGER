import math
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from preview.map import MapImageTransform, carla_yaw_to_image_direction


COMPACT = "compact"
INTERACTIVE = "interactive"


def heading_triangle(x, y, yaw, length=11.0, width=7.0):
    forward = carla_yaw_to_image_direction(yaw)
    side = (-forward[1], forward[0])
    tip = (x + forward[0] * length, y + forward[1] * length)
    rear_x = x - forward[0] * length * 0.55
    rear_y = y - forward[1] * length * 0.55
    return (
        tip[0], tip[1],
        rear_x + side[0] * width, rear_y + side[1] * width,
        rear_x - side[0] * width, rear_y - side[1] * width,
    )


def route_locations(route):
    if isinstance(route, dict):
        route = route.get("waypoints")
    result = []
    for waypoint in route or []:
        point = waypoint.get("location", waypoint) if isinstance(
            waypoint, dict
        ) else None
        if isinstance(point, dict) and "x" in point and "y" in point:
            result.append(point)
    return result


class NavigationMapViewport:
    MIN_SCALE = 0.15
    MAX_SCALE = 4.0

    def __init__(self, bounds, image_size, margin, canvas_size=(1, 1)):
        self.map_transform = MapImageTransform(bounds, image_size, margin)
        self.image_width, self.image_height = self.map_transform.image_size
        self.canvas_width = max(1.0, float(canvas_size[0]))
        self.canvas_height = max(1.0, float(canvas_size[1]))
        self.view_scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.fit()

    def resize_canvas(self, width, height, preserve_center=True):
        center = None
        if preserve_center:
            center = self.canvas_to_world(
                self.canvas_width / 2.0,
                self.canvas_height / 2.0,
            )
        self.canvas_width = max(1.0, float(width))
        self.canvas_height = max(1.0, float(height))
        if center is None:
            self.fit()
        else:
            self.center_on(*center)

    def fit(self):
        self.view_scale = min(
            self.canvas_width / self.image_width,
            self.canvas_height / self.image_height,
        )
        self.offset_x = (
            self.canvas_width - self.image_width * self.view_scale
        ) / 2.0
        self.offset_y = (
            self.canvas_height - self.image_height * self.view_scale
        ) / 2.0

    def world_to_canvas(self, x, y):
        image_x, image_y = self.map_transform.world_to_image(x, y)
        return (
            self.offset_x + image_x * self.view_scale,
            self.offset_y + image_y * self.view_scale,
        )

    def canvas_to_world(self, x, y):
        image_x = (float(x) - self.offset_x) / self.view_scale
        image_y = (float(y) - self.offset_y) / self.view_scale
        return self.map_transform.image_to_world(image_x, image_y)

    def center_on(self, x, y):
        image_x, image_y = self.map_transform.world_to_image(x, y)
        self.offset_x = self.canvas_width / 2.0 - image_x * self.view_scale
        self.offset_y = self.canvas_height / 2.0 - image_y * self.view_scale

    def zoom_at(self, factor, canvas_x, canvas_y):
        old_scale = self.view_scale
        new_scale = min(
            self.MAX_SCALE,
            max(self.MIN_SCALE, old_scale * float(factor)),
        )
        if abs(new_scale - old_scale) <= 1e-12:
            return False
        image_x = (float(canvas_x) - self.offset_x) / old_scale
        image_y = (float(canvas_y) - self.offset_y) / old_scale
        self.view_scale = new_scale
        self.offset_x = float(canvas_x) - image_x * new_scale
        self.offset_y = float(canvas_y) - image_y * new_scale
        return True

    def pan(self, delta_x, delta_y):
        self.offset_x += float(delta_x)
        self.offset_y += float(delta_y)


class NavigationMap(ttk.Frame):
    def __init__(self, parent, mode=COMPACT, on_map_click=None,
                 width=250, height=150):
        super().__init__(parent)
        if mode not in (COMPACT, INTERACTIVE):
            raise ValueError(f"Unknown navigation map mode: {mode}")
        self.mode = mode
        self.on_map_click = on_map_click
        self.map_data = None
        self.viewport = None
        self.source_image = None
        self.photo_image = None
        self.loaded_path = None
        self.actor_snapshot = None
        self.destination = None
        self.route = None
        self._rendered_scale = None
        self._drag_start = None
        self._drag_last = None

        if self.mode == INTERACTIVE:
            toolbar = ttk.Frame(self)
            toolbar.pack(fill="x", pady=(0, 5))
            ttk.Button(toolbar, text="Reset view", command=self.reset_view).pack(
                side="left", padx=(0, 6)
            )
            ttk.Button(toolbar, text="Fit map", command=self.fit_map).pack(
                side="left"
            )
            ttk.Label(
                toolbar,
                text="Wheel: zoom  |  Drag: pan  |  Click: select lane",
            ).pack(side="right")

        self.canvas = tk.Canvas(
            self,
            width=width,
            height=height,
            background="#14181E",
            highlightthickness=1,
            highlightbackground="#C96D3C",
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._configured)
        if self.mode == INTERACTIVE:
            self.canvas.bind("<MouseWheel>", self._mousewheel)
            self.canvas.bind("<Button-4>", self._mousewheel)
            self.canvas.bind("<Button-5>", self._mousewheel)
            self.canvas.bind("<ButtonPress-1>", self._press)
            self.canvas.bind("<B1-Motion>", self._drag)
            self.canvas.bind("<ButtonRelease-1>", self._release)

    def set_map_data(self, data):
        if not isinstance(data, dict):
            return
        path = data.get("map_image_path")
        bounds = data.get("map_bounds")
        image_size = data.get("map_image_size")
        if not path or not bounds or not image_size:
            return
        if path != self.loaded_path:
            with Image.open(path) as image:
                self.source_image = image.convert("RGB")
            self.loaded_path = path
            self._rendered_scale = None
        self.map_data = dict(data)
        self.viewport = NavigationMapViewport(
            bounds,
            image_size,
            data.get("map_margin", 0),
            (max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)),
        )
        self.actor_snapshot = data.get("actor_snapshot", self.actor_snapshot)
        self.destination = data.get("destination", self.destination)
        self.route = data.get("route", self.route)
        if self.mode == COMPACT and self._actor_point() is not None:
            self.viewport.view_scale = min(
                self.viewport.MAX_SCALE,
                max(self.viewport.view_scale * 2.5, 0.7),
            )
            point = self._actor_point()
            self.viewport.center_on(point["x"], point["y"])
        self._redraw(resize_image=True)

    def set_navigation(self, actor_snapshot=None, destination=None, route=None):
        if actor_snapshot is not None:
            self.actor_snapshot = actor_snapshot
        self.destination = destination
        self.route = route
        if self.viewport is None:
            return
        if self.mode == COMPACT:
            point = self._actor_point()
            if point is not None:
                self.viewport.center_on(point["x"], point["y"])
        self._redraw(resize_image=False)

    def clear_navigation(self):
        self.actor_snapshot = None
        self.destination = None
        self.route = None
        self._redraw(resize_image=False)

    def fit_map(self):
        if self.viewport is not None:
            self.viewport.fit()
            self._redraw(resize_image=True)

    def reset_view(self):
        self.fit_map()

    def _configured(self, event):
        if self.viewport is None:
            return
        self.viewport.resize_canvas(event.width, event.height, preserve_center=False)
        if self.mode == COMPACT:
            self.viewport.view_scale = min(
                self.viewport.MAX_SCALE,
                max(self.viewport.view_scale * 2.5, 0.7),
            )
            point = self._actor_point()
            if point is not None:
                self.viewport.center_on(point["x"], point["y"])
        self._redraw(resize_image=True)

    def _actor_point(self):
        if not isinstance(self.actor_snapshot, dict):
            return None
        location = self.actor_snapshot.get("location", self.actor_snapshot)
        return location if isinstance(location, dict) else None

    def _destination_point(self):
        if not isinstance(self.destination, dict):
            return None
        target_waypoint = self.destination.get("target_waypoint")
        if isinstance(target_waypoint, dict) and isinstance(
            target_waypoint.get("location"), dict
        ):
            return target_waypoint["location"]
        return (
            self.destination.get("selected")
            or self.destination.get("adjusted")
            or self.destination.get("requested")
        )

    def _route_points(self):
        return route_locations(self.route)

    def _redraw(self, resize_image=False):
        if self.viewport is None or self.source_image is None:
            return
        if resize_image or self._rendered_scale != self.viewport.view_scale:
            width = max(1, int(round(
                self.source_image.width * self.viewport.view_scale
            )))
            height = max(1, int(round(
                self.source_image.height * self.viewport.view_scale
            )))
            resampling = getattr(Image, "Resampling", Image)
            resized = self.source_image.resize(
                (width, height),
                resampling.LANCZOS,
            )
            self.photo_image = ImageTk.PhotoImage(resized)
            self._rendered_scale = self.viewport.view_scale
        self.canvas.delete("all")
        self.canvas.create_image(
            self.viewport.offset_x,
            self.viewport.offset_y,
            image=self.photo_image,
            anchor="nw",
            tags=("map",),
        )
        route_coordinates = []
        for point in self._route_points():
            route_coordinates.extend(
                self.viewport.world_to_canvas(point["x"], point["y"])
            )
        if len(route_coordinates) >= 4:
            self.canvas.create_line(
                *route_coordinates,
                fill="#2E9CCA",
                width=3 if self.mode == INTERACTIVE else 2,
                smooth=True,
                tags=("route",),
            )
        destination = self._destination_point()
        if isinstance(destination, dict):
            x, y = self.viewport.world_to_canvas(
                destination["x"], destination["y"]
            )
            radius = 6 if self.mode == INTERACTIVE else 4
            self.canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill="#E36B3A", outline="white", width=1,
                tags=("destination",),
            )
        actor = self._actor_point()
        if isinstance(actor, dict):
            x, y = self.viewport.world_to_canvas(actor["x"], actor["y"])
            yaw = float((self.actor_snapshot or {}).get("yaw", 0.0))
            self.canvas.create_polygon(
                *heading_triangle(x, y, yaw),
                fill="#FFD166",
                outline="#1B3A4B",
                width=1,
                tags=("actor",),
            )

    def _mousewheel(self, event):
        if self.viewport is None:
            return
        if getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            factor = 1.0 / 1.2
        else:
            factor = 1.2
        if self.viewport.zoom_at(factor, event.x, event.y):
            self._redraw(resize_image=True)

    def _press(self, event):
        self._drag_start = (float(event.x), float(event.y))
        self._drag_last = self._drag_start

    def _drag(self, event):
        if self.viewport is None or self._drag_last is None:
            return
        current = (float(event.x), float(event.y))
        self.viewport.pan(
            current[0] - self._drag_last[0],
            current[1] - self._drag_last[1],
        )
        self._drag_last = current
        self._redraw(resize_image=False)

    def _release(self, event):
        if self.viewport is None or self._drag_start is None:
            return
        current = (float(event.x), float(event.y))
        distance = math.hypot(
            current[0] - self._drag_start[0],
            current[1] - self._drag_start[1],
        )
        self._drag_start = None
        self._drag_last = None
        if distance <= 4.0 and callable(self.on_map_click):
            self.on_map_click(*self.viewport.canvas_to_world(*current))
