import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from preview.map import MapImageTransform, MapPreviewCache, MapPreviewGenerator


class FakeWaypoint:
    def __init__(self, x, y, distance, road_id=1, lane_id=1, lane_width=3.5):
        self.transform = SimpleNamespace(
            location=SimpleNamespace(x=x, y=y, z=0.0),
            rotation=SimpleNamespace(yaw=0.0 if lane_id > 0 else 180.0),
        )
        self.section_id = 0
        self.is_junction = False
        self.s = distance
        self.road_id = road_id
        self.lane_id = lane_id
        self.lane_width = lane_width


class FakeMap:
    name = "/Game/Carla/Maps/TownTest"

    def __init__(self, waypoints):
        self.waypoints = waypoints
        self.generate_calls = 0

    def generate_waypoints(self, distance):
        self.generate_calls += 1
        return list(self.waypoints)


class MapPreviewGeneratorTests(unittest.TestCase):
    def test_known_world_axes_have_expected_image_orientation(self):
        transform = MapImageTransform(
            (-100.0, -50.0, 100.0, 50.0),
            (800, 600),
            40,
        )

        self.assertEqual((400.0, 300.0), transform.world_to_image(0.0, 0.0))
        self.assertEqual((436.0, 300.0), transform.world_to_image(10.0, 0.0))
        self.assertEqual((400.0, 336.0), transform.world_to_image(0.0, 10.0))
        self.assertEqual((400.0, 264.0), transform.world_to_image(0.0, -10.0))

    def test_image_to_world_known_points_and_round_trip(self):
        transform = MapImageTransform(
            (-100.0, -50.0, 100.0, 50.0),
            (800, 600),
            40,
        )

        self.assertEqual((10.0, 0.0), transform.image_to_world(436.0, 300.0))
        self.assertEqual((0.0, 10.0), transform.image_to_world(400.0, 336.0))
        for point in ((-75.0, -25.0), (0.0, 0.0), (82.5, 37.25)):
            restored = transform.image_to_world(
                *transform.world_to_image(*point)
            )
            self.assertAlmostEqual(point[0], restored[0])
            self.assertAlmostEqual(point[1], restored[1])

    def test_world_coordinates_preserve_scale_and_carla_handedness(self):
        transform = MapPreviewGenerator.calculate_transform(
            (0, 0, 100, 50), (800, 600), 50
        )
        first = MapPreviewGenerator.world_to_image((0, 0), transform)
        x_point = MapPreviewGenerator.world_to_image((10, 0), transform)
        y_point = MapPreviewGenerator.world_to_image((0, 10), transform)

        self.assertAlmostEqual(x_point[0] - first[0], 70.0)
        self.assertAlmostEqual(y_point[1] - first[1], 70.0)
        self.assertGreaterEqual(first[0], 50.0)
        self.assertLessEqual(y_point[1], 550.0)

    def test_generates_road_image_and_does_not_join_distant_points(self):
        fake_map = FakeMap([
            FakeWaypoint(0, 0, 0),
            FakeWaypoint(2.5, 0, 2.5),
            FakeWaypoint(100, 100, 100),
        ])
        generator = MapPreviewGenerator(width=200, height=150, margin=10)

        segments = generator.extract_lane_segments(fake_map)
        image = generator.render_segments(segments)

        self.assertEqual([len(segment) for segment in segments], [2, 1])
        self.assertEqual(image.size, (200, 150))
        self.assertNotEqual(image.getbbox(), None)
        self.assertTrue(any(
            pixel == generator.ROAD_COLOR
            for pixel in image.get_flattened_data()
        ))

    def test_empty_geometry_returns_placeholder_without_error(self):
        generator = MapPreviewGenerator(width=160, height=120)
        image = generator.generate(FakeMap([]))

        self.assertEqual(image.size, (160, 120))
        self.assertTrue(any(
            pixel != generator.BACKGROUND_COLOR
            for pixel in image.get_flattened_data()
        ))

    def test_navigation_map_reuses_renderer_and_exposes_transform_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_map = FakeMap([
                FakeWaypoint(0, 0, 0, lane_id=1),
                FakeWaypoint(10, 0, 10, lane_id=1),
                FakeWaypoint(10, -3.5, 10, lane_id=-1),
            ])
            generator = MapPreviewGenerator(
                width=200,
                height=150,
                margin=10,
                cache=MapPreviewCache(directory),
            )

            data = generator.prepare_navigation_map(fake_map)

            self.assertTrue(Path(data["map_image_path"]).is_file())
            self.assertEqual([200, 150], data["map_image_size"])
            self.assertEqual(10, data["map_margin"])
            self.assertEqual(3, len(data["points"]))
            self.assertEqual({-1, 1}, {
                point["lane_id"] for point in data["points"]
            })


class MapPreviewCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.cache = MapPreviewCache(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_save_and_reload_cache(self):
        source = Image.new("RGB", (20, 10), (10, 20, 30))
        path = self.cache.save("/Game/Maps/Town01", source)

        loaded = self.cache.load("/Game/Maps/Town01")

        self.assertTrue(path.is_file())
        self.assertEqual(loaded.size, source.size)
        self.assertEqual(loaded.getpixel((0, 0)), (10, 20, 30))

    def test_corrupted_cache_is_ignored(self):
        path = self.cache.path_for("Town01")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a png")

        self.assertIsNone(self.cache.load("Town01"))

        fake_map = FakeMap([FakeWaypoint(0, 0, 0)])
        fake_map.name = "Town01"
        generated_path, generated = MapPreviewGenerator(
            cache=self.cache
        ).generate_if_missing(fake_map)

        self.assertTrue(generated)
        self.assertEqual(generated_path, path)
        self.assertIsNotNone(self.cache.load("Town01"))

    def test_existing_cache_is_not_generated_again(self):
        fake_map = FakeMap([FakeWaypoint(0, 0, 0)])
        generator = MapPreviewGenerator(cache=self.cache)
        self.cache.save(fake_map.name, Image.new("RGB", (5, 5)))

        path, generated = generator.generate_if_missing(fake_map)

        self.assertTrue(path.is_file())
        self.assertFalse(generated)
        self.assertEqual(fake_map.generate_calls, 0)

    def test_cache_without_transform_revision_is_regenerated(self):
        fake_map = FakeMap([FakeWaypoint(0, 0, 0)])
        path = self.cache.path_for(fake_map.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (5, 5)).save(path, format="PNG")

        generated_path, generated = MapPreviewGenerator(
            cache=self.cache
        ).generate_if_missing(fake_map)

        self.assertTrue(generated)
        self.assertEqual(path, generated_path)
        self.assertTrue(self.cache.has_current_transform(path))

    def test_short_name_resolves_single_full_name_cache(self):
        self.cache.save(
            "/Game/Carla/Maps/Town01",
            Image.new("RGB", (5, 5)),
        )
        self.assertIsNotNone(self.cache.load("Town01"))

    def test_stale_result_is_rejected_for_other_selected_map(self):
        self.assertFalse(MapPreviewCache.map_names_match(
            "Town02", "/Game/Carla/Maps/Town01"
        ))
        self.assertTrue(MapPreviewCache.map_names_match(
            "Town01", "/Game/Carla/Maps/Town01"
        ))

    def test_file_names_are_safe_and_collision_resistant(self):
        first = self.cache.path_for("/Game/A/Town")
        second = self.cache.path_for("/Game/B/Town")

        self.assertNotEqual(first.name, second.name)
        self.assertEqual(first.parent, Path(self.temporary_directory.name))
        self.assertNotIn("/", first.name)

    def test_default_cache_path_is_independent_from_working_directory(self):
        cache = MapPreviewCache()
        project_root = Path(__file__).resolve().parents[2]

        self.assertEqual(
            cache.directory,
            project_root / "resources" / "maps" / "generated",
        )
        self.assertEqual(
            cache.legacy_directories,
            (project_root / "images" / "maps" / "generated",),
        )


if __name__ == "__main__":
    unittest.main()
