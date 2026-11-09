import threading

import carla
import numpy as np
from PIL import Image

IMAGE_SENSOR_TYPES = (
    "sensor.camera.rgb",
    "sensor.camera.depth",
    "sensor.camera.semantic_segmentation",
    "sensor.camera.instance_segmentation",
    "sensor.camera.normals",
    "sensor.camera.optical_flow",
    "sensor.camera.dvs",
    "sensor.camera.cosmos_visualization"
)

VALUE_SENSOR_TYPES = (
    "sensor.other.gnss",
    "sensor.other.imu"
)

EVENT_SENSOR_TYPES = (
    "sensor.other.collision",
    "sensor.other.lane_invasion",
    "sensor.other.obstacle",
    "sensor.other.v2x",
    "sensor.other.v2x_custom"
)

SPATIAL_SENSOR_TYPES = (
    "sensor.lidar.ray_cast",
    "sensor.lidar.ray_cast_semantic",
    "sensor.other.radar"
)

SEMANTIC_LIDAR_COLORS = np.array([
    (255, 255, 255),
    (70, 70, 70),
    (100, 40, 40),
    (55, 90, 80),
    (220, 20, 60),
    (153, 153, 153),
    (157, 234, 50),
    (128, 64, 128),
    (244, 35, 232),
    (107, 142, 35),
    (0, 0, 142),
    (102, 102, 156),
    (220, 220, 0),
    (70, 130, 180),
    (81, 0, 81),
    (150, 100, 100),
    (230, 150, 140),
    (180, 165, 180),
    (250, 170, 30),
    (110, 190, 160),
    (170, 120, 50),
    (45, 60, 150),
    (145, 170, 100)
], dtype=np.float32) / 255.0

class SensorPreviewBuffer:
    def __init__(self, blueprint_id):
        self.blueprint_id = blueprint_id
        self.image = None
        self.error = None
        self.started = False
        self.closed = False
        self.image_revision = 0
        self.lock = threading.Lock()

    def on_sensor_image(self, image):
        with self.lock:
            if self.closed:
                return

        try:
            if self.blueprint_id == "sensor.camera.dvs":
                latest_image = self._create_dvs_image(image)
            else:
                if self.blueprint_id == "sensor.camera.optical_flow":
                    image = image.get_color_coded_flow()

                if self.blueprint_id == "sensor.camera.depth":
                    image.convert(carla.ColorConverter.LogarithmicDepth)
                elif self.blueprint_id == "sensor.camera.semantic_segmentation":
                    image.convert(carla.ColorConverter.CityScapesPalette)
                elif self.blueprint_id != "sensor.camera.optical_flow":
                    image.convert(carla.ColorConverter.Raw)

                array = np.frombuffer(image.raw_data, dtype=np.uint8)
                array = np.reshape(array, (image.height, image.width, 4))

                if self.blueprint_id == "sensor.camera.instance_segmentation":
                    latest_image = self._create_instance_image(array)
                else:
                    rgb_array = array[:, :, :3][:, :, ::-1]
                    latest_image = Image.fromarray(rgb_array)

            with self.lock:
                if self.closed:
                    return
                self.image = latest_image
                self.image_revision += 1
        except Exception as error:
            self.set_error(str(error))

    def _create_dvs_image(self, event_array):
        events = np.frombuffer(event_array.raw_data, dtype=np.dtype([
            ("x", np.uint16),
            ("y", np.uint16),
            ("t", np.int64),
            ("pol", np.bool_)
        ]))
        rgb_array = np.zeros((event_array.height, event_array.width, 3), dtype=np.uint8)
        if len(events) > 0:
            positive = events["pol"]
            rgb_array[events["y"][positive], events["x"][positive], 2] = 255
            rgb_array[events["y"][~positive], events["x"][~positive], 0] = 255
        return Image.fromarray(rgb_array)

    def _create_instance_image(self, array):
        blue = array[:, :, 0].astype(np.uint32)
        green = array[:, :, 1].astype(np.uint32)
        semantic = array[:, :, 2].astype(np.uint32)
        instance_id = green * 256 + blue

        red_color = (instance_id * 37 + semantic * 17) % 256
        green_color = (instance_id * 73 + semantic * 29) % 256
        blue_color = (instance_id * 109 + semantic * 43) % 256
        rgb_array = np.stack((red_color, green_color, blue_color), axis=2).astype(np.uint8)
        rgb_array[(instance_id == 0) & (semantic == 0)] = 0
        return Image.fromarray(rgb_array)

    def set_started(self):
        with self.lock:
            if not self.closed:
                self.started = True

    def set_error(self, error):
        with self.lock:
            if not self.closed:
                self.error = error

    def get_snapshot(self):
        with self.lock:
            image = self.image.copy() if self.image is not None else None
            return self.image_revision, image, self.started, self.error

    def close(self):
        with self.lock:
            self.closed = True
            self.image = None
            self.image_revision += 1

class SensorValuesPreviewBuffer:
    def __init__(self, blueprint_id):
        self.blueprint_id = blueprint_id
        self.values = []
        self.error = None
        self.started = False
        self.closed = False
        self.revision = 0
        self.lock = threading.Lock()

    def on_sensor_data(self, data):
        with self.lock:
            if self.closed:
                return

        try:
            if self.blueprint_id == "sensor.other.gnss":
                values = [
                    ("Latitude", f"{data.latitude:.8f} deg"),
                    ("Longitude", f"{data.longitude:.8f} deg"),
                    ("Altitude", f"{data.altitude:.3f} m"),
                    ("Frame", str(data.frame))
                ]
            else:
                values = [
                    ("Accelerometer X", f"{data.accelerometer.x:.4f} m/s^2"),
                    ("Accelerometer Y", f"{data.accelerometer.y:.4f} m/s^2"),
                    ("Accelerometer Z", f"{data.accelerometer.z:.4f} m/s^2"),
                    ("Gyroscope X", f"{data.gyroscope.x:.5f} rad/s"),
                    ("Gyroscope Y", f"{data.gyroscope.y:.5f} rad/s"),
                    ("Gyroscope Z", f"{data.gyroscope.z:.5f} rad/s"),
                    ("Compass", f"{data.compass:.5f} rad / {np.degrees(data.compass):.2f} deg"),
                    ("Frame", str(data.frame))
                ]

            with self.lock:
                if self.closed:
                    return
                self.values = values
                self.revision += 1
        except Exception as error:
            self.set_error(str(error))

    def set_started(self):
        with self.lock:
            if not self.closed:
                self.started = True

    def set_error(self, error):
        with self.lock:
            if not self.closed:
                self.error = error

    def get_snapshot(self):
        with self.lock:
            return self.revision, list(self.values), self.started, self.error

    def close(self):
        with self.lock:
            self.closed = True
            self.values = []
            self.revision += 1

class SensorEventsPreviewBuffer:
    max_events = 30

    def __init__(self, blueprint_id):
        self.blueprint_id = blueprint_id
        self.events = []
        self.error = None
        self.started = False
        self.closed = False
        self.revision = 0
        self.lock = threading.Lock()

    def on_sensor_data(self, data):
        with self.lock:
            if self.closed:
                return

        try:
            rows = self._create_rows(data)
            if not rows:
                return
            with self.lock:
                if self.closed:
                    return
                self.events.extend(rows)
                self.events = self.events[-self.max_events:]
                self.revision += 1
        except Exception as error:
            self.set_error(str(error))

    def _create_rows(self, data):
        if self.blueprint_id == "sensor.other.collision":
            actor = data.other_actor
            impulse = data.normal_impulse
            return [(
                data.frame,
                actor.id if actor is not None else "-",
                actor.type_id if actor is not None else "Unknown",
                f"X: {impulse.x:.3f}, Y: {impulse.y:.3f}, Z: {impulse.z:.3f}"
            )]

        if self.blueprint_id == "sensor.other.lane_invasion":
            markings = ", ".join(
                str(marking.type).replace("LaneMarkingType.", "")
                for marking in data.crossed_lane_markings
            )
            return [(data.frame, markings or "Unknown")]

        if self.blueprint_id == "sensor.other.obstacle":
            actor = data.other_actor
            return [(
                data.frame,
                actor.id if actor is not None else "-",
                actor.type_id if actor is not None else "Unknown",
                f"{data.distance:.3f} m"
            )]

        rows = []
        for message in data:
            message_data = str(message.get())
            rows.append((data.frame, f"{message.power:.2f} dBm", message_data))
        return rows

    def set_started(self):
        with self.lock:
            if not self.closed:
                self.started = True

    def set_error(self, error):
        with self.lock:
            if not self.closed:
                self.error = error

    def get_snapshot(self):
        with self.lock:
            return self.revision, list(self.events), self.started, self.error

    def close(self):
        with self.lock:
            self.closed = True
            self.events = []
            self.revision += 1

class SpatialSensorPreviewBuffer:
    max_points = 10000

    def __init__(self, blueprint_id):
        self.blueprint_id = blueprint_id
        self.points = np.empty((0, 3), dtype=np.float32)
        self.colors = np.empty(0, dtype=np.float32)
        self.total_points = 0
        self.error = None
        self.started = False
        self.closed = False
        self.revision = 0
        self.lock = threading.Lock()

    def on_sensor_data(self, data):
        with self.lock:
            if self.closed:
                return

        try:
            if self.blueprint_id == "sensor.lidar.ray_cast":
                points, colors, total_points = self._decode_lidar(data)
            elif self.blueprint_id == "sensor.lidar.ray_cast_semantic":
                points, colors, total_points = self._decode_semantic_lidar(data)
            else:
                points, colors, total_points = self._decode_radar(data)

            with self.lock:
                if self.closed:
                    return
                self.points = points
                self.colors = colors
                self.total_points = total_points
                self.revision += 1
        except Exception as error:
            self.set_error(str(error))

    def _decode_lidar(self, measurement):
        data = np.frombuffer(measurement.raw_data, dtype=np.float32)
        data = np.reshape(data, (-1, 4))
        sampled_data = self._sample_data(data)
        return (
            sampled_data[:, :3].copy(),
            sampled_data[:, 3].copy(),
            len(data)
        )

    def _decode_semantic_lidar(self, measurement):
        data = np.frombuffer(measurement.raw_data, dtype=np.dtype([
            ("x", np.float32),
            ("y", np.float32),
            ("z", np.float32),
            ("cos_inc_angle", np.float32),
            ("object_idx", np.uint32),
            ("object_tag", np.uint32)
        ]))
        sampled_data = self._sample_data(data)
        points = np.column_stack((
            sampled_data["x"],
            sampled_data["y"],
            sampled_data["z"]
        )).astype(np.float32, copy=False)
        tags = sampled_data["object_tag"]
        colors = np.full((len(tags), 3), 0.5, dtype=np.float32)
        known_tags = tags < len(SEMANTIC_LIDAR_COLORS)
        colors[known_tags] = SEMANTIC_LIDAR_COLORS[tags[known_tags]]
        return points, colors, len(data)

    def _decode_radar(self, measurement):
        data = np.frombuffer(measurement.raw_data, dtype=np.float32)
        data = np.reshape(data, (-1, 4))
        sampled_data = self._sample_data(data)

        velocity = sampled_data[:, 0]
        azimuth = sampled_data[:, 1]
        altitude = sampled_data[:, 2]
        depth = sampled_data[:, 3]
        horizontal_depth = depth * np.cos(altitude)
        points = np.column_stack((
            horizontal_depth * np.cos(azimuth),
            horizontal_depth * np.sin(azimuth),
            depth * np.sin(altitude)
        )).astype(np.float32, copy=False)
        return points, velocity.copy(), len(data)

    def _sample_data(self, data):
        if len(data) <= self.max_points:
            return data
        step = int(np.ceil(len(data) / self.max_points))
        return data[::step][:self.max_points]

    def set_started(self):
        with self.lock:
            if not self.closed:
                self.started = True

    def set_error(self, error):
        with self.lock:
            if not self.closed:
                self.error = error

    def get_snapshot(self):
        with self.lock:
            return (
                self.revision,
                self.points.copy(),
                self.colors.copy(),
                self.total_points,
                self.started,
                self.error
            )

    def close(self):
        with self.lock:
            self.closed = True
            self.points = np.empty((0, 3), dtype=np.float32)
            self.colors = np.empty(0, dtype=np.float32)
            self.total_points = 0
            self.revision += 1

