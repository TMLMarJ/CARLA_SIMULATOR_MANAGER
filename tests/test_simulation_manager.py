import json
import unittest

from simulation_manager import SimulationManager


class FakeWorldSettings:
    def __init__(self):
        self.synchronous_mode = False
        self.fixed_delta_seconds = 0.0
        self.no_rendering_mode = False
        self.substepping = True
        self.max_substep_delta_time = 0.01
        self.max_substeps = 10
        self.max_culling_distance = 12345.0

    def copy(self):
        copied = FakeWorldSettings()
        copied.__dict__.update(self.__dict__)
        return copied


class FakeWorld:
    def __init__(self, ignore_field=None):
        self.settings = FakeWorldSettings()
        self.ignore_field = ignore_field
        self.apply_calls = 0

    def get_settings(self):
        return self.settings.copy()

    def apply_settings(self, settings):
        self.apply_calls += 1
        previous_value = None
        if self.ignore_field is not None:
            previous_value = getattr(self.settings, self.ignore_field)
        self.settings = settings.copy()
        if self.ignore_field is not None:
            setattr(self.settings, self.ignore_field, previous_value)


class SimulationManagerTests(unittest.TestCase):
    def test_current_settings_are_serializable(self):
        settings = SimulationManager.get_current_settings(FakeWorld())

        serialized = json.dumps(settings)

        self.assertIn('"synchronous_mode": false', serialized)
        self.assertEqual(0.0, settings["fixed_delta_seconds"])

    def test_prepare_changes_only_requested_fields(self):
        world = FakeWorld()

        settings = SimulationManager.prepare_modified_settings(
            world,
            {"no_rendering_mode": True},
        )

        self.assertTrue(settings.no_rendering_mode)
        self.assertFalse(settings.synchronous_mode)
        self.assertEqual(12345.0, settings.max_culling_distance)

    def test_apply_reads_settings_back_and_confirms_them(self):
        world = FakeWorld()
        changes = {
            "synchronous_mode": True,
            "fixed_delta_seconds": 0.05,
            "no_rendering_mode": True,
            "substepping": True,
            "max_substep_delta_time": 0.01,
            "max_substeps": 5,
        }

        confirmed = SimulationManager.apply_settings(world, changes)

        self.assertEqual(1, world.apply_calls)
        self.assertEqual(changes, confirmed)
        self.assertEqual(12345.0, world.settings.max_culling_distance)

    def test_zero_and_none_are_both_valid_variable_time_steps(self):
        base_settings = SimulationManager.get_current_settings(FakeWorld())

        SimulationManager.validate_settings(base_settings)
        base_settings["fixed_delta_seconds"] = None
        SimulationManager.validate_settings(base_settings)

    def test_rejects_incompatible_substepping_values(self):
        settings = SimulationManager.get_current_settings(FakeWorld())
        settings["fixed_delta_seconds"] = 0.11

        with self.assertRaisesRegex(ValueError, "max_substep_delta_time"):
            SimulationManager.validate_settings(settings)

    def test_rejects_invalid_numeric_values(self):
        settings = SimulationManager.get_current_settings(FakeWorld())
        settings["max_substeps"] = 0

        with self.assertRaisesRegex(ValueError, "positive integer"):
            SimulationManager.validate_settings(settings)

    def test_detects_when_server_does_not_confirm_a_change(self):
        world = FakeWorld(ignore_field="no_rendering_mode")

        with self.assertRaisesRegex(RuntimeError, "no_rendering_mode"):
            SimulationManager.apply_settings(
                world,
                {"no_rendering_mode": True},
            )


if __name__ == "__main__":
    unittest.main()
