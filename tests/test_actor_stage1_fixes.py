import inspect
import queue
import unittest
from types import SimpleNamespace
from unittest import mock

from carla_manager import MainView, Manager
from carla_server.carla_server import CarlaController
from datastore.datastore import DataStorage
from gui_contents.vehicles import CustomVehicleFrame
from gui_contents import vehicles
from gui_contents.sensors import CustomSensorFrame
from scenario_management.models import ActorOwnership
from utilities.functions import actor_ownership_status, actor_sensor_status


def physics_control(mass=1500.0, gear_ratios=(3.0, 2.0)):
    gears = [
        SimpleNamespace(ratio=ratio, down_ratio=0.5, up_ratio=0.8)
        for ratio in gear_ratios
    ]
    wheels = [
        SimpleNamespace(
            tire_friction=2.0,
            damping_rate=0.25,
            max_steer_angle=35.0,
            radius=30.0,
            max_brake_torque=1500.0,
            max_handbrake_torque=3000.0,
        )
        for _ in range(4)
    ]
    return SimpleNamespace(
        torque_curve=[SimpleNamespace(x=0.0, y=400.0)],
        max_rpm=6000.0,
        moi=1.0,
        damping_rate_full_throttle=0.15,
        damping_rate_zero_throttle_clutch_engaged=2.0,
        damping_rate_zero_throttle_clutch_disengaged=0.35,
        use_gear_autobox=True,
        gear_switch_time=0.5,
        clutch_strength=10.0,
        final_ratio=4.0,
        forward_gears=gears,
        mass=mass,
        drag_coefficient=0.3,
        center_of_mass=SimpleNamespace(x=0.0, y=0.0, z=-0.2),
        steering_curve=[SimpleNamespace(x=0.0, y=1.0)],
        use_sweep_wheel_collision=False,
        wheels=wheels,
    )


class ActorStageOneFixTests(unittest.TestCase):
    def test_vehicle_dialogs_use_tkinter_messagebox(self):
        self.assertEqual("tkinter.messagebox", vehicles.messagebox.__name__)

    def test_main_view_delegates_actor_table_refresh_to_manager(self):
        view = MainView.__new__(MainView)
        view.manager = SimpleNamespace(_refresh_actor_tables=mock.Mock())

        view.refresh_actor_tables()

        view.manager._refresh_actor_tables.assert_called_once_with()

    def test_existing_vehicle_physics_uses_runtime_apply_without_replacement(self):
        frame = CustomVehicleFrame.__new__(CustomVehicleFrame)
        frame.pending_requests = {}
        apply_physics = mock.Mock(return_value=True)
        frame.controller = SimpleNamespace(
            carla=SimpleNamespace(apply_vehicle_physics=apply_physics)
        )
        physics = physics_control()

        accepted = frame._apply_existing_vehicle_physics(
            17,
            {"blueprint": {}, "physics": physics},
        )

        self.assertTrue(accepted)
        apply_physics.assert_called_once()
        args = apply_physics.call_args.args
        self.assertEqual(17, args[0])
        self.assertIs(physics, args[1])
        self.assertEqual(
            "apply_physics",
            frame.pending_requests[args[2]]["operation"],
        )

    def test_status_comes_from_ownership(self):
        self.assertEqual(
            "Managed",
            actor_ownership_status(SimpleNamespace(ownership=ActorOwnership.MANAGED)),
        )
        self.assertEqual(
            "External",
            actor_ownership_status(SimpleNamespace(ownership=ActorOwnership.EXTERNAL)),
        )
        self.assertEqual(
            "Internal",
            actor_ownership_status(SimpleNamespace(ownership=ActorOwnership.INTERNAL)),
        )

    def test_actor_sensor_status_uses_parent_relationship(self):
        database = DataStorage()
        database.sensors_db.sensors_db[25] = SimpleNamespace(parent_id=17)

        self.assertEqual("Yes", actor_sensor_status(database, 17))
        self.assertEqual("No", actor_sensor_status(database, 18))

    def test_external_movement_control_offers_ownership_takeover(self):
        manager = Manager.__new__(Manager)
        manager.connected = True
        manager.disconnecting = False
        manager.closing = False
        manager.world_changing = False
        manager.pending_movement_control_actor_id = None
        manager.database = DataStorage()
        actor = SimpleNamespace(
            id=17,
            is_alive=True,
            type_id="vehicle.test.model",
        )
        manager.database.actors_db.actors[17] = actor
        manager.database.actors_db.actors_db[17] = SimpleNamespace(
            ownership=ActorOwnership.EXTERNAL,
        )
        take_ownership = mock.Mock(return_value=True)
        manager.carla = SimpleNamespace(
            take_actor_ownership=take_ownership,
        )
        adoption_status = mock.Mock()
        dashboard = SimpleNamespace(
            set_adoption_pending=mock.Mock(),
            adoption_status_var=SimpleNamespace(set=adoption_status),
        )
        manager.main_view = SimpleNamespace(
            main_view=SimpleNamespace(dashboard_frame=dashboard),
        )
        manager.movement_control = SimpleNamespace(open=mock.Mock())

        with mock.patch(
            "carla_manager.messagebox.askyesno",
            return_value=True,
        ) as askyesno:
            manager.open_movement_control(17)

        askyesno.assert_called_once()
        take_ownership.assert_called_once_with(17)
        self.assertEqual(17, manager.pending_movement_control_actor_id)
        manager.movement_control.open.assert_not_called()

    def test_successful_movement_takeover_opens_control(self):
        manager = Manager.__new__(Manager)
        manager.connected = True
        manager.disconnecting = False
        manager.closing = False
        manager.pending_movement_control_actor_id = 17
        manager.database = DataStorage()
        manager.database.scenario_event_queue.put((
            "actor_ownership_result",
            {
                "status": "success",
                "success": True,
                "runtime_id": 17,
                "message": "Ownership acquired.",
                "warnings": [],
            },
        ))
        dashboard = SimpleNamespace(
            set_adoption_pending=mock.Mock(),
            show_adoption_result=mock.Mock(),
        )
        manager.main_view = SimpleNamespace(
            main_view=SimpleNamespace(dashboard_frame=dashboard),
        )
        manager._refresh_actor_tables = mock.Mock()
        manager._refresh_dashboard = mock.Mock()
        manager.open_movement_control = mock.Mock()

        with mock.patch("carla_manager.messagebox.showinfo"):
            manager._check_scenario_events()

        self.assertIsNone(manager.pending_movement_control_actor_id)
        manager.open_movement_control.assert_called_once_with(17)

    def test_physics_verification_includes_mass_and_forward_gear_order(self):
        requested = physics_control()
        self.assertEqual(
            [],
            CarlaController._physics_mismatches(requested, physics_control()),
        )
        self.assertIn(
            "mass",
            CarlaController._physics_mismatches(
                requested,
                physics_control(mass=1400.0),
            ),
        )
        self.assertIn(
            "forward_gears",
            CarlaController._physics_mismatches(
                requested,
                physics_control(gear_ratios=(2.0, 3.0)),
            ),
        )

    def test_model_change_command_carries_attributes_and_physics(self):
        controller = CarlaController.__new__(CarlaController)
        controller.command_queue = queue.Queue()
        controller._scenario_mutation_blocked = lambda operation: False
        controller._managed_operation_allowed = lambda actor_id: True
        physics = physics_control()

        accepted = controller.change_actor_model(
            "vehicle.test.model",
            17,
            attributes={"role_name": "ego"},
            physics=physics,
            request_id="request-1",
        )

        self.assertTrue(accepted)
        command, data = controller.command_queue.get_nowait()
        self.assertEqual("change_actor_model", command)
        self.assertEqual("vehicle.test.model", data["blueprint_id"])
        self.assertEqual({"role_name": "ego"}, data["attributes"])
        self.assertIs(physics, data["physics"])
        self.assertEqual("request-1", data["request_id"])

    def test_model_change_destroys_old_actor_before_spawning_replacement(self):
        source = inspect.getsource(CarlaController._run_controller)
        start = source.index('if cmd == "change_actor_model":')
        end = source.index('if cmd == "set_actor_transform":', start)
        change_model_source = source[start:end]

        destroy_position = change_model_source.index("old_actor.destroy()")
        unregister_position = change_model_source.index(
            "self.database.actors_db.remove_actor(actor_id)"
        )
        spawn_position = change_model_source.index(
            "new_actor = self._spawn_actor_checked("
        )

        self.assertLess(destroy_position, spawn_position)
        self.assertLess(unregister_position, spawn_position)

    def test_actor_spawn_does_not_move_spectator(self):
        source = inspect.getsource(CarlaController._run_controller)
        start = source.index('if cmd == "spawn":')
        end = source.index('if cmd == "change_world":', start)

        self.assertNotIn("get_spectator", source[start:end])
        self.assertNotIn("spectator.set_transform", source[start:end])

    def test_sensor_table_has_separate_ownership_column(self):
        setup_source = inspect.getsource(CustomSensorFrame._sensor_frame_setup)
        refresh_source = inspect.getsource(CustomSensorFrame.refresh_table)

        self.assertIn('"Ownership"', setup_source)
        self.assertIn("actor_ownership_status(sensor_info)", refresh_source)
        self.assertIn('"Active" if sensor_info.is_alive', refresh_source)

    def test_sensor_rename_changes_only_logical_name(self):
        database = DataStorage()
        sensor_info = SimpleNamespace(
            user_name="Front camera",
            carla_id=25,
            blueprint_id="sensor.camera.rgb",
            parent_id=17,
            data_sources={},
        )
        database.sensors_db.sensors_db[25] = sensor_info

        renamed = database.rename_sensor(25, "  Main camera  ")

        self.assertEqual("Main camera", renamed)
        self.assertEqual("Main camera", sensor_info.user_name)
        self.assertEqual(25, sensor_info.carla_id)
        self.assertEqual("sensor.camera.rgb", sensor_info.blueprint_id)
        self.assertEqual(17, sensor_info.parent_id)
        self.assertEqual("gui", sensor_info.data_sources["user_name"])


if __name__ == "__main__":
    unittest.main()
