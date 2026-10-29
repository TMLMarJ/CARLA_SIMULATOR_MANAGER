import queue
import threading
import unittest
from types import SimpleNamespace

from carla_server.carla_server import CarlaController
from movement_preview import (
    CAMERA_PRESETS,
    LatestFramePublisher,
    ManualControlSmoother,
    camera_transform_for_actor,
)
from vehicle_control import initial_vehicle_control_state


class FakeLocation:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z


class FakeRotation:
    def __init__(self, pitch, yaw, roll):
        self.pitch = pitch
        self.yaw = yaw
        self.roll = roll


class FakeTransform:
    def __init__(self, location, rotation):
        self.location = location
        self.rotation = rotation


class FakeCarla:
    Location = FakeLocation
    Rotation = FakeRotation
    Transform = FakeTransform


class FakeImage:
    def __init__(self, value, frame):
        self.raw_data = bytes([value]) * 8
        self.width = 2
        self.height = 1
        self.frame = frame


class FakeBlueprint:
    def __init__(self):
        self.attributes = {}

    def has_attribute(self, name):
        return True

    def set_attribute(self, name, value):
        self.attributes[name] = value


class FakeCamera:
    def __init__(self):
        self.id = 501
        self.is_alive = True
        self.is_listening = False
        self.parent = None
        self.callback = None
        self.transforms = []
        self.destroyed = False

    def listen(self, callback):
        self.callback = callback
        self.is_listening = True

    def stop(self):
        self.is_listening = False

    def set_transform(self, transform):
        self.transforms.append(transform)

    def destroy(self):
        self.destroyed = True
        self.is_alive = False


class FakeWorld:
    def __init__(self, camera):
        self.camera = camera
        self.blueprint = FakeBlueprint()

    def get_blueprint_library(self):
        return SimpleNamespace(find=lambda _blueprint_id: self.blueprint)

    def spawn_actor(self, _blueprint, _transform, attach_to,
                    attachment_type):
        self.camera.parent = attach_to
        return self.camera


class FakePublisher:
    def __init__(self):
        self.closed = False

    def publish(self, _image):
        pass

    def close(self):
        self.closed = True


class MovementCameraPresetTests(unittest.TestCase):
    def test_all_presets_create_relative_transforms(self):
        transforms = [
            camera_transform_for_actor("vehicle.test", preset, FakeCarla)
            for preset in CAMERA_PRESETS
        ]

        self.assertEqual(3, len(transforms))
        self.assertLess(transforms[1].location.x, 0.0)
        self.assertEqual(-90.0, transforms[2].rotation.pitch)
        self.assertGreater(transforms[2].location.z, transforms[1].location.z)

    def test_walker_third_person_view_is_closer_than_vehicle_view(self):
        vehicle = camera_transform_for_actor(
            "vehicle.test", "Third Person", FakeCarla
        )
        walker = camera_transform_for_actor(
            "walker.pedestrian.test", "Third Person", FakeCarla
        )

        self.assertGreater(walker.location.x, vehicle.location.x)
        self.assertLess(walker.location.z, vehicle.location.z)

    def test_unknown_preset_is_rejected(self):
        with self.assertRaises(ValueError):
            camera_transform_for_actor("vehicle.test", "Unknown", FakeCarla)


class LatestFramePublisherTests(unittest.TestCase):
    def test_queue_never_grows_beyond_one_and_keeps_latest_frame(self):
        frame_queue = queue.Queue(maxsize=1)
        publisher = LatestFramePublisher(frame_queue)

        publisher.publish(FakeImage(1, 10))
        publisher.publish(FakeImage(2, 11))

        self.assertEqual(1, frame_queue.qsize())
        raw_data, width, height, frame = frame_queue.get_nowait()
        self.assertEqual(bytes([2]) * 8, raw_data)
        self.assertEqual((2, 1, 11), (width, height, frame))

    def test_closed_publisher_ignores_late_callbacks(self):
        frame_queue = queue.Queue(maxsize=1)
        publisher = LatestFramePublisher(frame_queue)
        publisher.close()

        publisher.publish(FakeImage(3, 12))

        self.assertTrue(frame_queue.empty())


class ManualControlSmootherTests(unittest.TestCase):
    def test_values_change_smoothly_and_reverse_toggles_on_key_edge(self):
        smoother = ManualControlSmoother()

        first = smoother.update({
            "accelerate": True,
            "right": True,
            "reverse": True,
        }, 0.1)
        held = smoother.update({"reverse": True}, 0.1)

        self.assertAlmostEqual(0.16, first["throttle"])
        self.assertAlmostEqual(0.25, first["steer"])
        self.assertTrue(first["reverse"])
        self.assertTrue(held["reverse"])

    def test_focus_loss_immediately_requests_safe_stop(self):
        smoother = ManualControlSmoother()
        smoother.update({"accelerate": True}, 0.2)

        control = smoother.update({}, 0.1, focused=False)

        self.assertEqual(0.0, control["throttle"])
        self.assertEqual(1.0, control["brake"])
        self.assertFalse(control["focused"])


class TemporaryCameraLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.actor = SimpleNamespace(
            id=42,
            type_id="vehicle.test",
            is_alive=True,
        )
        self.camera = FakeCamera()
        self.world = FakeWorld(self.camera)
        self.publisher = FakePublisher()
        self.controller = object.__new__(CarlaController)
        self.controller.database = SimpleNamespace(
            lock=threading.Lock(),
            actors_db=SimpleNamespace(actors={42: self.actor}),
            sensors_db=SimpleNamespace(sensors={77: object()}),
            movement_event_queue=queue.Queue(),
            vehicle_control_states={42: initial_vehicle_control_state()},
        )
        self.controller.dashboard_monitor = SimpleNamespace(
            record_error=lambda _error: None
        )
        self.controller.movement_preview_camera = None
        self.controller.movement_preview_publisher = None
        self.controller.movement_preview_session_id = None
        self.controller.movement_preview_actor_id = None
        self.controller.movement_control_queue = None
        self.controller.manual_control_command = None
        self.controller.manual_control_received_at = None
        self.controller.manual_control_next_apply = 0.0
        self.controller.manual_control_input_fresh = False

    def test_camera_is_temporary_and_preset_reuses_same_sensor(self):
        self.controller._start_movement_preview(self.world, {
            "session_id": "session",
            "actor_id": 42,
            "actor_type": "vehicle.test",
            "preset": "Third Person",
            "publisher": self.publisher,
            "control_queue": queue.Queue(maxsize=1),
        })

        self.assertIs(self.camera, self.controller.movement_preview_camera)
        self.assertEqual({77}, set(self.controller.database.sensors_db.sensors))
        self.controller._set_movement_preview_transform({
            "session_id": "session",
            "preset": "Top View",
        })
        self.assertEqual(1, len(self.camera.transforms))
        self.assertIs(self.camera, self.controller.movement_preview_camera)

    def test_cleanup_stops_only_temporary_camera(self):
        self.controller._start_movement_preview(self.world, {
            "session_id": "session",
            "actor_id": 42,
            "actor_type": "vehicle.test",
            "preset": "Third Person",
            "publisher": self.publisher,
            "control_queue": queue.Queue(maxsize=1),
        })

        self.controller.cleanup_movement_preview("test_finished")

        self.assertTrue(self.publisher.closed)
        self.assertTrue(self.camera.destroyed)
        self.assertEqual({77}, set(self.controller.database.sensors_db.sensors))
        events = []
        while not self.controller.database.movement_event_queue.empty():
            events.append(
                self.controller.database.movement_event_queue.get_nowait()
            )
        self.assertEqual("movement_camera_started", events[0][0])
        stopped = next(
            data for status, data in events
            if status == "movement_camera_stopped"
        )
        self.assertEqual("test_finished", stopped["reason"])


if __name__ == "__main__":
    unittest.main()
