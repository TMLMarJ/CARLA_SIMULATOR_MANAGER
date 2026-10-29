import unittest
from types import SimpleNamespace

from gui_contents.vehicles import CustomVehicleFrame


class FakeVariable:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeCombobox:
    def __init__(self):
        self.values = ()
        self.current_index = None

    def __setitem__(self, key, value):
        if key == "values":
            self.values = value

    def current(self, index):
        self.current_index = index


def spawn_point(x, y, z, yaw, pitch, roll):
    return SimpleNamespace(
        location=SimpleNamespace(x=x, y=y, z=z),
        rotation=SimpleNamespace(
            yaw=yaw,
            pitch=pitch,
            roll=roll,
        ),
    )


class VehicleSpawnPointRefreshTests(unittest.TestCase):
    def test_map_refresh_updates_combobox_and_all_transform_fields(self):
        frame = object.__new__(CustomVehicleFrame)
        point = spawn_point(11.0, -4.5, 2.0, 90.0, 3.0, -1.0)
        frame.database = SimpleNamespace(available_spawn_points=[point])
        frame.combobox_spawn_points = FakeCombobox()
        frame.selected_spawn_point = FakeVariable("old map value")
        frame.x_position_var = FakeVariable()
        frame.y_position_var = FakeVariable()
        frame.z_position_var = FakeVariable()
        frame.yaw_position_var = FakeVariable()
        frame.pitch_position_var = FakeVariable()
        frame.roll_position_var = FakeVariable()

        frame.update_spawn_point()

        self.assertEqual(0, frame.combobox_spawn_points.current_index)
        self.assertEqual(1, len(frame.combobox_spawn_points.values))
        self.assertEqual(11.0, frame.x_position_var.get())
        self.assertEqual(-4.5, frame.y_position_var.get())
        self.assertEqual(2.0, frame.z_position_var.get())
        self.assertEqual(90.0, frame.yaw_position_var.get())
        self.assertEqual(3.0, frame.pitch_position_var.get())
        self.assertEqual(-1.0, frame.roll_position_var.get())

    def test_empty_map_clears_spawn_point_without_selecting_index(self):
        frame = object.__new__(CustomVehicleFrame)
        frame.database = SimpleNamespace(available_spawn_points=[])
        frame.combobox_spawn_points = FakeCombobox()
        frame.selected_spawn_point = FakeVariable("old map value")

        frame.update_spawn_point()

        self.assertEqual("", frame.selected_spawn_point.get())
        self.assertIsNone(frame.combobox_spawn_points.current_index)


if __name__ == "__main__":
    unittest.main()
