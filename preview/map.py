# Canonical implementation; the legacy path is a compatibility shim.
import hashlib
import math
import re
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo


def carla_yaw_to_image_direction(yaw):
    """Return a unit image vector for a CARLA world yaw angle."""
    angle = math.radians(float(yaw))
    return math.cos(angle), math.sin(angle)


class MapImageTransform:
    """Shared CARLA-world to image transform.

    CARLA +X is drawn to the right and CARLA +Y is drawn downward.  CARLA is
    left-handed (+X forward, +Y right) and raster +Y also points downward, so
    keeping the Y sign preserves the visual sense of left and right turns.
    Every renderer, marker and click conversion must use this transform or its
    inverse rather than applying a local axis correction.
    """
    def __init__(self, bounds, image_size, margin):
        self.bounds = tuple(float(value) for value in bounds)
        self.image_size = tuple(int(value) for value in image_size)
        self.margin = float(margin)
        min_x, min_y, max_x, max_y = self.bounds
        width, height = self.image_size
        drawable_width = max(1.0, width - 2.0 * self.margin)
        drawable_height = max(1.0, height - 2.0 * self.margin)
        span_x = max(max_x - min_x, 1e-9)
        span_y = max(max_y - min_y, 1e-9)
        self.scale = min(drawable_width / span_x, drawable_height / span_y)
        used_width = (max_x - min_x) * self.scale
        used_height = (max_y - min_y) * self.scale
        self.offset_x = (width - used_width) / 2.0 - min_x * self.scale
        self.offset_y = (height - used_height) / 2.0 - min_y * self.scale

    @property
    def values(self):
        return self.scale, self.offset_x, self.offset_y

    def world_to_image(self, x, y):
        return (
            float(x) * self.scale + self.offset_x,
            float(y) * self.scale + self.offset_y,
        )

    def image_to_world(self, x, y):
        return (
            (float(x) - self.offset_x) / self.scale,
            (float(y) - self.offset_y) / self.scale,
        )


class MapPreviewCache:
    TRANSFORM_METADATA_KEY = "carla_simulator_manager_map_transform"
    TRANSFORM_REVISION = "carla_xy_raster_v2"

    def __init__(self, directory=None):
        project_root = Path(__file__).resolve().parent.parent
        if directory is None:
            self.directory = project_root / "resources" / "maps" / "generated"
            self.legacy_directories = (
                project_root / "images" / "maps" / "generated",
            )
        else:
            self.directory = Path(directory)
            self.legacy_directories = ()

    @staticmethod
    def normalize_map_name(map_name):
        return str(map_name or "").strip().replace("\\", "/")

    @classmethod
    def short_map_name(cls, map_name):
        normalized = cls.normalize_map_name(map_name).rstrip("/")
        return normalized.rsplit("/", 1)[-1]

    @classmethod
    def map_names_match(cls, first, second):
        first_name = cls.short_map_name(first).casefold()
        second_name = cls.short_map_name(second).casefold()
        return bool(first_name and first_name == second_name)

    def path_for(self, map_name):
        normalized = self.normalize_map_name(map_name)
        short_name = self.short_map_name(normalized) or "map"
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", short_name).strip("._")
        safe_name = safe_name or "map"
        digest = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()[:12]
        return self.directory / f"{safe_name}-{digest}.png"

    @staticmethod
    def _read_valid_image(path):
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                return image.convert("RGB")
        except (FileNotFoundError, OSError, ValueError):
            return None

    def find(self, map_name):
        directories = (self.directory, *self.legacy_directories)
        exact_name = self.path_for(map_name).name
        for directory in directories:
            exact_path = directory / exact_name
            if self._read_valid_image(exact_path) is not None:
                return exact_path

        short_name = self.short_map_name(map_name) or "map"
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", short_name).strip("._")
        safe_name = safe_name or "map"
        candidates = []
        for directory in directories:
            try:
                paths = directory.glob(f"{safe_name}-*.png")
                for path in paths:
                    if self._read_valid_image(path) is not None:
                        candidates.append(path)
            except OSError:
                continue
        return candidates[0] if len(candidates) == 1 else None

    def load(self, map_name):
        path = self.find(map_name)
        return None if path is None else self._read_valid_image(path)

    @classmethod
    def has_current_transform(cls, path):
        try:
            with Image.open(path) as image:
                return (
                    image.info.get(cls.TRANSFORM_METADATA_KEY)
                    == cls.TRANSFORM_REVISION
                )
        except (FileNotFoundError, OSError, ValueError):
            return False

    def save(self, map_name, image):
        path = self.path_for(map_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(".tmp.png")
        try:
            metadata = PngInfo()
            metadata.add_text(
                self.TRANSFORM_METADATA_KEY,
                self.TRANSFORM_REVISION,
            )
            image.convert("RGB").save(
                temporary_path,
                format="PNG",
                pnginfo=metadata,
            )
            temporary_path.replace(path)
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        return path


class MapPreviewGenerator:
    BACKGROUND_COLOR = (20, 24, 30)
    ROAD_COLOR = (220, 224, 228)
    ROAD_CENTER_COLOR = (168, 176, 184)
    TEXT_COLOR = (190, 196, 202)

    def __init__(self, width=800, height=600, margin=36,
                 sampling_distance=2.5, cache=None):
        self.width = int(width)
        self.height = int(height)
        self.margin = int(margin)
        self.sampling_distance = float(sampling_distance)
        self.cache = cache or MapPreviewCache()

    @staticmethod
    def calculate_transform(bounds, image_size, margin):
        return MapImageTransform(bounds, image_size, margin).values

    @staticmethod
    def world_to_image(point, transform):
        scale, offset_x, offset_y = transform
        x, y = point
        return x * scale + offset_x, y * scale + offset_y

    def extract_navigation_geometry(self, carla_map, cancelled=None):
        waypoints = carla_map.generate_waypoints(self.sampling_distance)
        lanes = defaultdict(list)
        navigation_points = []
        for waypoint in waypoints or []:
            if cancelled is not None and cancelled():
                raise RuntimeError("Map preview generation was cancelled.")
            try:
                location = waypoint.transform.location
                x = float(location.x)
                y = float(location.y)
                lane_width = max(0.1, float(waypoint.lane_width))
                road_id = int(waypoint.road_id)
                lane_id = int(waypoint.lane_id)
                section_id = int(getattr(waypoint, "section_id", 0))
                distance = float(getattr(waypoint, "s", 0.0))
            except (AttributeError, TypeError, ValueError, OverflowError):
                continue
            if not all(math.isfinite(value) for value in (x, y, lane_width, distance)):
                continue
            lanes[(road_id, lane_id)].append((distance, x, y, lane_width))
            rotation = getattr(waypoint.transform, "rotation", None)
            navigation_points.append({
                "x": x,
                "y": y,
                "z": float(getattr(location, "z", 0.0)),
                "road_id": road_id,
                "section_id": section_id,
                "lane_id": lane_id,
                "s": distance,
                "lane_width": lane_width,
                "yaw": (
                    None if rotation is None
                    else float(getattr(rotation, "yaw", 0.0))
                ),
                "is_junction": bool(getattr(waypoint, "is_junction", False)),
            })

        segments = []
        for points in lanes.values():
            points.sort(key=lambda item: item[0])
            current = []
            previous = None
            for distance, x, y, lane_width in points:
                point = (x, y, lane_width)
                if previous is not None:
                    gap = math.hypot(x - previous[0], y - previous[1])
                    maximum_gap = max(
                        self.sampling_distance * 4.0,
                        lane_width * 3.0,
                        previous[2] * 3.0,
                    )
                    if gap > maximum_gap:
                        if current:
                            segments.append(current)
                        current = []
                if not current or (x, y) != current[-1][:2]:
                    current.append(point)
                previous = point
            if current:
                segments.append(current)
        return segments, navigation_points

    def extract_lane_segments(self, carla_map, cancelled=None):
        segments, _points = self.extract_navigation_geometry(
            carla_map,
            cancelled=cancelled,
        )
        return segments

    @staticmethod
    def bounds_for_segments(segments):
        points = [point for segment in segments for point in segment]
        if not points:
            return None
        return (
            min(x - width / 2.0 for x, _y, width in points),
            min(y - width / 2.0 for _x, y, width in points),
            max(x + width / 2.0 for x, _y, width in points),
            max(y + width / 2.0 for _x, y, width in points),
        )

    def render_segments(self, segments):
        image = Image.new(
            "RGB", (self.width, self.height), self.BACKGROUND_COLOR
        )
        draw = ImageDraw.Draw(image)
        points = [point for segment in segments for point in segment]
        if not points:
            message = "No road geometry available"
            text_bounds = draw.textbbox((0, 0), message)
            text_width = text_bounds[2] - text_bounds[0]
            text_height = text_bounds[3] - text_bounds[1]
            draw.text(
                ((self.width - text_width) / 2, (self.height - text_height) / 2),
                message,
                fill=self.TEXT_COLOR,
            )
            return image

        min_x, min_y, max_x, max_y = self.bounds_for_segments(segments)
        transform = self.calculate_transform(
            (min_x, min_y, max_x, max_y),
            (self.width, self.height),
            self.margin,
        )
        scale = transform[0]

        for segment in segments:
            screen_points = [
                self.world_to_image((x, y), transform)
                for x, y, _width in segment
            ]
            average_width = sum(point[2] for point in segment) / len(segment)
            pixel_width = max(1, int(round(average_width * scale)))
            if len(screen_points) == 1:
                x, y = screen_points[0]
                radius = pixel_width / 2.0
                draw.ellipse(
                    (x - radius, y - radius, x + radius, y + radius),
                    fill=self.ROAD_COLOR,
                )
            else:
                draw.line(
                    screen_points,
                    fill=self.ROAD_COLOR,
                    width=pixel_width,
                    joint="curve",
                )
                if pixel_width >= 5:
                    draw.line(
                        screen_points,
                        fill=self.ROAD_CENTER_COLOR,
                        width=1,
                        joint="curve",
                    )
        return image

    def prepare_navigation_map(self, carla_map, map_name=None, cancelled=None):
        resolved_name = str(map_name or getattr(carla_map, "name", ""))
        segments, points = self.extract_navigation_geometry(
            carla_map,
            cancelled=cancelled,
        )
        bounds = self.bounds_for_segments(segments)
        if bounds is None:
            raise RuntimeError("The active CARLA map has no driving geometry.")
        cached_path = self.cache.find(resolved_name)
        if (
            cached_path is None
            or not self.cache.has_current_transform(cached_path)
        ):
            image = self.render_segments(segments)
            if cancelled is not None and cancelled():
                raise RuntimeError("Map preview generation was cancelled.")
            cached_path = self.cache.save(resolved_name, image)
        return {
            "map_name": resolved_name,
            "map_image_path": str(cached_path),
            "map_bounds": list(bounds),
            "map_image_size": [self.width, self.height],
            "map_margin": self.margin,
            "points": points,
        }

    def generate(self, carla_map, cancelled=None):
        return self.render_segments(
            self.extract_lane_segments(carla_map, cancelled=cancelled)
        )

    def generate_if_missing(self, carla_map, map_name=None, cancelled=None):
        resolved_name = str(map_name or getattr(carla_map, "name", ""))
        cached_path = self.cache.find(resolved_name)
        if (
            cached_path is not None
            and self.cache.has_current_transform(cached_path)
        ):
            return cached_path, False
        image = self.generate(carla_map, cancelled=cancelled)
        if cancelled is not None and cancelled():
            raise RuntimeError("Map preview generation was cancelled.")
        return self.cache.save(resolved_name, image), True
