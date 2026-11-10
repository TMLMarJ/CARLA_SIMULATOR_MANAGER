import unittest

import app
from core.controller import CarlaController
from gui.application import Manager
from preview.movement import run_movement_preview
from preview.spectator import run_spectator_preview


class ApplicationArchitectureTests(unittest.TestCase):
    def test_entrypoint_exports_canonical_manager(self):
        self.assertIs(app.Manager, Manager)

    def test_controller_uses_status_callback_without_manager_dependency(self):
        controller = CarlaController.__new__(CarlaController)
        controller._spectator_status_provider = lambda: {
            "state": "Running",
            "camera_id": 42,
        }

        self.assertEqual(
            controller._spectator_runtime_status(),
            {"state": "Running", "camera_id": 42},
        )

    def test_controller_status_callback_has_safe_fallback(self):
        controller = CarlaController.__new__(CarlaController)

        self.assertEqual(
            controller._spectator_runtime_status(),
            {"state": "Unknown", "camera_id": None},
        )

    def test_preview_process_targets_are_top_level_importable_functions(self):
        for target in (run_movement_preview, run_spectator_preview):
            self.assertNotIn("<locals>", target.__qualname__)
            self.assertTrue(target.__module__.startswith("preview."))


if __name__ == "__main__":
    unittest.main()
