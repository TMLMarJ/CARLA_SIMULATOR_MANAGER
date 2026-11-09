import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from PIL import Image, ImageTk

from preview.sensor_buffers import (
    EVENT_SENSOR_TYPES,
    IMAGE_SENSOR_TYPES,
    SEMANTIC_LIDAR_COLORS,
    SPATIAL_SENSOR_TYPES,
    VALUE_SENSOR_TYPES,
    SensorEventsPreviewBuffer,
    SensorPreviewBuffer,
    SensorValuesPreviewBuffer,
    SpatialSensorPreviewBuffer,
)

class SensorPreviewTop(tk.Toplevel):
    refresh_interval_ms = 50

    def __init__(self, parent, controller, sensor_id, sensor_name,
                 blueprint_id, on_close):
        super().__init__(parent)
        self.controller = controller
        self.sensor_id = sensor_id
        self.on_close = on_close
        self.preview_buffer = SensorPreviewBuffer(blueprint_id)
        self.refresh_job = None
        self.last_image_revision = -1
        self.closed = False

        self.title(f"Sensor Preview - {sensor_name}")
        self.geometry("900x650")
        self.minsize(480, 360)
        self.configure(bg="#F7E7D3")

        info_frame = ttk.Frame(self, style="new.TFrame")
        info_frame.pack(fill="x", padx=14, pady=(12, 8))
        self.name_label = ttk.Label(info_frame, text=f"Name: {sensor_name}")
        self.name_label.pack(side="left", padx=(0, 20))
        ttk.Label(info_frame, text=f"ID: {sensor_id}").pack(side="left", padx=(0, 20))
        ttk.Label(info_frame, text=f"Type: {blueprint_id}").pack(side="left")

        self.image_label = tk.Label(
            self,
            text="Waiting for image...",
            background="#1E1E1E",
            foreground="#FFFFFF",
            anchor="center"
        )
        self.image_label.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.bind("<Configure>", self._on_resize, add="+")
        self.controller.carla.start_sensor_preview(
            sensor_id,
            self.preview_buffer.on_sensor_image,
            self.preview_buffer
        )
        self._schedule_refresh()

    def update_display_name(self, sensor_name):
        self.title(f"Sensor Preview - {sensor_name}")
        self.name_label.configure(text=f"Name: {sensor_name}")

    def _schedule_refresh(self):
        if not self.closed and self.winfo_exists():
            self.refresh_job = self.after(
                self.refresh_interval_ms,
                self._refresh_image
            )

    def _refresh_image(self):
        self.refresh_job = None
        revision, image, started, error = self.preview_buffer.get_snapshot()

        if error is not None:
            messagebox.showerror("Sensor preview error", error, parent=self)
            self.close()
            return

        if revision != self.last_image_revision and image is not None:
            self.last_image_revision = revision
            width = max(1, self.image_label.winfo_width() - 8)
            height = max(1, self.image_label.winfo_height() - 8)
            image.thumbnail((width, height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            self.image_label.configure(image=photo, text="")
            self.image_label.image = photo
        elif not started:
            self.image_label.configure(text="Starting preview...")

        self._schedule_refresh()

    def _on_resize(self, event):
        if event.widget is self:
            self.last_image_revision = -1

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.refresh_job is not None:
            self.after_cancel(self.refresh_job)
            self.refresh_job = None
        self.preview_buffer.close()
        if self.controller.carla is not None:
            self.controller.carla.stop_sensor_preview(self.sensor_id)
        self.image_label.configure(image="", text="")
        self.image_label.image = None
        self.on_close()
        self.destroy()

    def _on_destroy(self, event):
        if event.widget is self and not self.closed:
            self.close()

class SensorValuesPreviewTop(tk.Toplevel):
    refresh_interval_ms = 100

    def __init__(self, parent, controller, sensor_id, sensor_name,
                 blueprint_id, on_close):
        super().__init__(parent)
        self.controller = controller
        self.sensor_id = sensor_id
        self.on_close = on_close
        self.preview_buffer = SensorValuesPreviewBuffer(blueprint_id)
        self.refresh_job = None
        self.last_revision = -1
        self.closed = False
        self.value_vars = {}

        self.title(f"Sensor Preview - {sensor_name}")
        self.geometry("620x430")
        self.minsize(520, 340)
        self.configure(bg="#F7E7D3")

        info_frame = ttk.Frame(self, style="new.TFrame")
        info_frame.pack(fill="x", padx=14, pady=(12, 8))
        self.name_label = ttk.Label(info_frame, text=f"Name: {sensor_name}")
        self.name_label.pack(side="left", padx=(0, 20))
        ttk.Label(info_frame, text=f"ID: {sensor_id}").pack(side="left", padx=(0, 20))
        ttk.Label(info_frame, text=f"Type: {blueprint_id}").pack(side="left")

        self.values_frame = ttk.LabelFrame(
            self,
            text="Current values",
            style="Section.TLabelframe",
            padding=(16, 12)
        )
        self.values_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.status_var = tk.StringVar(value="Starting preview...")
        ttk.Label(self.values_frame, textvariable=self.status_var).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=6, pady=6
        )
        self.values_frame.grid_columnconfigure(1, weight=1)

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.controller.carla.start_sensor_preview(
            sensor_id,
            self.preview_buffer.on_sensor_data,
            self.preview_buffer
        )
        self._schedule_refresh()

    def update_display_name(self, sensor_name):
        self.title(f"Sensor Preview - {sensor_name}")
        self.name_label.configure(text=f"Name: {sensor_name}")

    def _schedule_refresh(self):
        if not self.closed and self.winfo_exists():
            self.refresh_job = self.after(self.refresh_interval_ms, self._refresh_values)

    def _refresh_values(self):
        self.refresh_job = None
        revision, values, started, error = self.preview_buffer.get_snapshot()

        if error is not None:
            messagebox.showerror("Sensor preview error", error, parent=self)
            self.close()
            return

        if revision != self.last_revision and values:
            self.last_revision = revision
            self.status_var.set("")
            for label, value in values:
                if label not in self.value_vars:
                    row = len(self.value_vars) + 1
                    ttk.Label(self.values_frame, text=label).grid(
                        row=row, column=0, sticky="w", padx=(6, 24), pady=5
                    )
                    self.value_vars[label] = tk.StringVar()
                    ttk.Label(
                        self.values_frame,
                        textvariable=self.value_vars[label]
                    ).grid(row=row, column=1, sticky="w", padx=6, pady=5)
                self.value_vars[label].set(value)
        elif not started:
            self.status_var.set("Starting preview...")
        elif not values:
            self.status_var.set("Waiting for data...")

        self._schedule_refresh()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.refresh_job is not None:
            self.after_cancel(self.refresh_job)
            self.refresh_job = None
        self.preview_buffer.close()
        if self.controller.carla is not None:
            self.controller.carla.stop_sensor_preview(self.sensor_id)
        self.on_close()
        self.destroy()

    def _on_destroy(self, event):
        if event.widget is self and not self.closed:
            self.close()

class SensorEventsPreviewTop(tk.Toplevel):
    refresh_interval_ms = 100

    def __init__(self, parent, controller, sensor_id, sensor_name,
                 blueprint_id, on_close):
        super().__init__(parent)
        self.controller = controller
        self.sensor_id = sensor_id
        self.on_close = on_close
        self.preview_buffer = SensorEventsPreviewBuffer(blueprint_id)
        self.refresh_job = None
        self.last_revision = -1
        self.closed = False

        self.title(f"Sensor Preview - {sensor_name}")
        self.geometry("900x520")
        self.minsize(620, 360)
        self.configure(bg="#F7E7D3")

        info_frame = ttk.Frame(self, style="new.TFrame")
        info_frame.pack(fill="x", padx=14, pady=(12, 8))
        self.name_label = ttk.Label(info_frame, text=f"Name: {sensor_name}")
        self.name_label.pack(side="left", padx=(0, 20))
        ttk.Label(info_frame, text=f"ID: {sensor_id}").pack(side="left", padx=(0, 20))
        ttk.Label(info_frame, text=f"Type: {blueprint_id}").pack(side="left")

        table_frame = ttk.LabelFrame(
            self,
            text="Recent events",
            style="Section.TLabelframe",
            padding=(10, 8)
        )
        table_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        columns = self._get_columns(blueprint_id)
        self.tree = ttk.Treeview(
            table_frame,
            columns=tuple(column[0] for column in columns),
            show="headings",
            style="Treeview"
        )
        for column_id, heading, width in columns:
            self.tree.heading(column_id, text=heading)
            self.tree.column(column_id, width=width, minwidth=80, anchor="center")

        vertical_scrollbar = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.tree.yview
        )
        horizontal_scrollbar = ttk.Scrollbar(
            table_frame,
            orient="horizontal",
            command=self.tree.xview
        )
        self.tree.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")

        self.status_var = tk.StringVar(value="Starting preview...")
        ttk.Label(table_frame, textvariable=self.status_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.controller.carla.start_sensor_preview(
            sensor_id,
            self.preview_buffer.on_sensor_data,
            self.preview_buffer
        )
        self._schedule_refresh()

    def _get_columns(self, blueprint_id):
        if blueprint_id == "sensor.other.collision":
            return (
                ("Frame", "Frame", 90),
                ("Actor ID", "Actor ID", 90),
                ("Actor Type", "Actor Type", 250),
                ("Impulse", "Normal Impulse", 330)
            )
        if blueprint_id == "sensor.other.lane_invasion":
            return (
                ("Frame", "Frame", 100),
                ("Markings", "Crossed Markings", 600)
            )
        if blueprint_id == "sensor.other.obstacle":
            return (
                ("Frame", "Frame", 90),
                ("Actor ID", "Actor ID", 90),
                ("Actor Type", "Actor Type", 300),
                ("Distance", "Distance", 130)
            )
        return (
            ("Frame", "Frame", 90),
            ("Power", "Power", 120),
            ("Message", "Message", 600)
        )

    def update_display_name(self, sensor_name):
        self.title(f"Sensor Preview - {sensor_name}")
        self.name_label.configure(text=f"Name: {sensor_name}")

    def _schedule_refresh(self):
        if not self.closed and self.winfo_exists():
            self.refresh_job = self.after(self.refresh_interval_ms, self._refresh_events)

    def _refresh_events(self):
        self.refresh_job = None
        revision, events, started, error = self.preview_buffer.get_snapshot()

        if error is not None:
            messagebox.showerror("Sensor preview error", error, parent=self)
            self.close()
            return

        if revision != self.last_revision:
            self.last_revision = revision
            for item in self.tree.get_children():
                self.tree.delete(item)
            for event in events:
                self.tree.insert("", "end", values=event)
            if events:
                self.tree.yview_moveto(1.0)
                self.status_var.set(f"Showing the last {len(events)} event(s).")
        elif not started:
            self.status_var.set("Starting preview...")
        elif not events:
            self.status_var.set("Waiting for events...")

        self._schedule_refresh()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.refresh_job is not None:
            self.after_cancel(self.refresh_job)
            self.refresh_job = None
        self.preview_buffer.close()
        if self.controller.carla is not None:
            self.controller.carla.stop_sensor_preview(self.sensor_id)
        self.on_close()
        self.destroy()

    def _on_destroy(self, event):
        if event.widget is self and not self.closed:
            self.close()

class SpatialSensorPreviewTop(tk.Toplevel):
    refresh_interval_ms = 200

    def __init__(self, parent, controller, sensor_id, sensor_name,
                 blueprint_id, on_close):
        super().__init__(parent)
        self.controller = controller
        self.sensor_id = sensor_id
        self.blueprint_id = blueprint_id
        self.on_close = on_close
        self.preview_buffer = SpatialSensorPreviewBuffer(blueprint_id)
        self.refresh_job = None
        self.last_revision = -1
        self.closed = False

        self.title(f"Sensor Preview - {sensor_name}")
        self.geometry("980x720")
        self.minsize(620, 460)
        self.configure(bg="#F7E7D3")

        info_frame = ttk.Frame(self, style="new.TFrame")
        info_frame.pack(fill="x", padx=14, pady=(12, 8))
        self.name_label = ttk.Label(info_frame, text=f"Name: {sensor_name}")
        self.name_label.pack(side="left", padx=(0, 20))
        ttk.Label(info_frame, text=f"ID: {sensor_id}").pack(side="left", padx=(0, 20))
        ttk.Label(info_frame, text=f"Type: {blueprint_id}").pack(side="left")
        self.point_count_var = tk.StringVar(value="Starting preview...")
        ttk.Label(info_frame, textvariable=self.point_count_var).pack(side="right")

        plot_frame = ttk.Frame(self, style="new.TFrame")
        plot_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self.figure = Figure(figsize=(8, 6), dpi=100, facecolor="#F7E7D3")
        self.axes = self.figure.add_subplot(111, projection="3d")
        self.axes.set_facecolor("#FFF8EF")
        self.axes.set_xlabel("X - forward [m]")
        self.axes.set_ylabel("Y - right [m]")
        self.axes.set_zlabel("Z - up [m]")
        self.axes.view_init(elev=22, azim=-60)
        self.axes.grid(True)
        self._set_initial_limits()

        if blueprint_id == "sensor.lidar.ray_cast_semantic":
            self.scatter = self.axes.scatter(
                [], [], [],
                color=np.empty((0, 3)),
                s=1,
                depthshade=False
            )
        elif blueprint_id == "sensor.other.radar":
            self.scatter = self.axes.scatter(
                [], [], [],
                c=np.empty(0),
                cmap="coolwarm",
                vmin=-20.0,
                vmax=20.0,
                s=12,
                depthshade=False
            )
        else:
            self.scatter = self.axes.scatter(
                [], [], [],
                c=np.empty(0),
                cmap="viridis",
                vmin=0.0,
                vmax=1.0,
                s=1,
                depthshade=False
            )

        self.figure.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.controller.carla.start_sensor_preview(
            sensor_id,
            self.preview_buffer.on_sensor_data,
            self.preview_buffer
        )
        self._schedule_refresh()

    def _set_initial_limits(self):
        sensor_range = 100.0
        with self.controller.database.lock:
            sensor_info = self.controller.database.sensors_db.sensors_db.get(self.sensor_id)
            if sensor_info is not None:
                try:
                    sensor_range = float(sensor_info.attributes.get("range", sensor_range))
                except (TypeError, ValueError):
                    pass

        if self.blueprint_id == "sensor.other.radar":
            self.axes.set_xlim(0.0, sensor_range)
            self.axes.set_ylim(-sensor_range / 2.0, sensor_range / 2.0)
            self.axes.set_zlim(-sensor_range / 2.0, sensor_range / 2.0)
        else:
            self.axes.set_xlim(-sensor_range, sensor_range)
            self.axes.set_ylim(-sensor_range, sensor_range)
            vertical_range = min(sensor_range, 50.0)
            self.axes.set_zlim(-vertical_range, vertical_range)

    def update_display_name(self, sensor_name):
        self.title(f"Sensor Preview - {sensor_name}")
        self.name_label.configure(text=f"Name: {sensor_name}")

    def _schedule_refresh(self):
        if not self.closed and self.winfo_exists():
            self.refresh_job = self.after(self.refresh_interval_ms, self._refresh_plot)

    def _refresh_plot(self):
        self.refresh_job = None
        revision, points, colors, total_points, started, error = (
            self.preview_buffer.get_snapshot()
        )

        if error is not None:
            messagebox.showerror("Sensor preview error", error, parent=self)
            self.close()
            return

        if revision != self.last_revision:
            self.last_revision = revision
            self.scatter._offsets3d = (
                points[:, 0],
                points[:, 1],
                points[:, 2]
            )
            if self.blueprint_id == "sensor.lidar.ray_cast_semantic":
                self.scatter.set_color(colors)
            else:
                self.scatter.set_array(colors)
            self.canvas.draw_idle()

            displayed_points = len(points)
            if displayed_points < total_points:
                self.point_count_var.set(
                    f"Points: {displayed_points} of {total_points}"
                )
            else:
                self.point_count_var.set(f"Points: {displayed_points}")
        elif not started:
            self.point_count_var.set("Starting preview...")

        self._schedule_refresh()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.refresh_job is not None:
            self.after_cancel(self.refresh_job)
            self.refresh_job = None
        self.preview_buffer.close()
        if self.controller.carla is not None:
            self.controller.carla.stop_sensor_preview(self.sensor_id)
        self.canvas.get_tk_widget().destroy()
        self.figure.clear()
        self.on_close()
        self.destroy()

    def _on_destroy(self, event):
        if event.widget is self and not self.closed:
            self.close()

