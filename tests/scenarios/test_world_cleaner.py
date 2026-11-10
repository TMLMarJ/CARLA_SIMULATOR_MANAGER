import queue
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from gui.application import Manager
from core.controller import CarlaController
from core.state_store import DataStorage
from models.scenarios import ActorCategory, ActorOwnership
from scenarios.cleaner import (
    ClearMode,
    StaleClearPlanError,
    WorldCleaner,
    WorldClearReport,
)
from scenarios.synchronization import WorldSynchronizer


class FakeTransform:
    def __init__(self, x=0.0):
        self.location = SimpleNamespace(x=x, y=0.0, z=0.0)
        self.rotation = SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0)


class FakeActor:
    def __init__(self, actor_id, type_id, parent=None, role_name="",
                 destroy_error=None, log=None):
        self.id = actor_id
        self.type_id = type_id
        self.parent = parent
        self.attributes = {"role_name": role_name} if role_name else {}
        self.is_alive = True
        self.is_active = True
        self.is_listening = type_id.startswith("sensor.")
        self.destroy_error = destroy_error
        self.log = log if log is not None else []
        self.world = None
        self.transform = FakeTransform(float(actor_id))

    def get_world(self):
        return self.world

    def get_transform(self):
        return self.transform

    def get_location(self):
        return self.transform.location

    def get_velocity(self):
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)

    def get_acceleration(self):
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)

    def get_angular_velocity(self):
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)

    def get_physics_control(self):
        return None

    def stop(self):
        self.log.append(("stop", self.id))
        self.is_listening = False

    def destroy(self):
        self.log.append(("destroy", self.id))
        if self.destroy_error:
            raise RuntimeError(self.destroy_error)
        self.is_alive = False
        if self.world is not None:
            for actor in self.world.actors:
                if actor.parent is self:
                    actor.is_alive = False
        return True


class FakeWorld:
    def __init__(self, actors=None, world_id=1):
        self.id = world_id
        self.actors = list(actors or [])
        for actor in self.actors:
            actor.world = self

    def get_actors(self):
        return [actor for actor in self.actors if actor.is_alive]

    def get_snapshot(self):
        return SimpleNamespace(frame=1)


class WorldCleanerTests(unittest.TestCase):
    def setUp(self):
        self.cleaner = WorldCleaner()
        self.database = DataStorage()

    def register_actor(self, actor, ownership=ActorOwnership.MANAGED):
        self.database.add_actor(
            actor,
            f"Actor_{actor.id}",
            ownership=ownership,
        )

    def register_sensor(self, actor, ownership=ActorOwnership.MANAGED):
        self.database.add_sensor(
            actor,
            actor.parent.id if actor.parent else None,
            f"Sensor_{actor.id}",
            actor.type_id,
            FakeTransform(),
            actor.attributes,
            ownership=ownership,
        )

    def test_empty_world_is_already_clear(self):
        plan = self.cleaner.prepare_plan(
            FakeWorld(), self.database, ClearMode.MANAGED
        )
        self.assertEqual(0, plan.planned_count)
        self.assertTrue(plan.to_dict()["world_already_clear"])

    def test_managed_vehicle_is_planned_and_removed(self):
        vehicle = FakeActor(1, "vehicle.test")
        world = FakeWorld([vehicle])
        self.register_actor(vehicle)

        plan = self.cleaner.prepare_plan(world, self.database, "managed")
        report = self.cleaner.execute_plan(world, plan)

        self.assertEqual([1], [item.runtime_id for item in plan.items])
        self.assertEqual(1, report.removed)
        self.assertEqual("success", report.status)

    def test_external_vehicle_is_not_in_managed_plan(self):
        vehicle = FakeActor(2, "vehicle.test")
        world = FakeWorld([vehicle])
        self.register_actor(vehicle, ActorOwnership.EXTERNAL)
        plan = self.cleaner.prepare_plan(world, self.database, "managed")
        self.assertEqual([], plan.items)
        self.assertTrue(vehicle.is_alive)

    def test_managed_clear_preserves_independent_external_actor(self):
        managed = FakeActor(3, "vehicle.test")
        external = FakeActor(4, "walker.pedestrian.0001")
        world = FakeWorld([managed, external])
        self.register_actor(managed)
        self.register_actor(external, ActorOwnership.EXTERNAL)

        plan = self.cleaner.prepare_plan(world, self.database, "managed")
        self.cleaner.execute_plan(world, plan)

        self.assertFalse(managed.is_alive)
        self.assertTrue(external.is_alive)

    def test_entire_world_includes_managed_and_external(self):
        managed = FakeActor(5, "vehicle.test")
        external = FakeActor(6, "walker.pedestrian.0001")
        world = FakeWorld([managed, external])
        self.register_actor(managed)
        self.register_actor(external, ActorOwnership.EXTERNAL)

        plan = self.cleaner.prepare_plan(world, self.database, "entire_world")
        report = self.cleaner.execute_plan(world, plan)

        self.assertEqual({5, 6}, {item.runtime_id for item in plan.items})
        self.assertEqual(2, report.removed)
        self.assertTrue(any("other CARLA clients" in w for w in plan.warnings))

    def test_sensor_is_destroyed_before_parent(self):
        log = []
        vehicle = FakeActor(7, "vehicle.test", log=log)
        sensor = FakeActor(8, "sensor.camera.rgb", parent=vehicle, log=log)
        world = FakeWorld([vehicle, sensor])
        self.register_actor(vehicle)
        self.register_sensor(sensor)

        plan = self.cleaner.prepare_plan(world, self.database, "managed")
        self.cleaner.execute_plan(world, plan)

        self.assertLess(log.index(("destroy", 8)), log.index(("destroy", 7)))

    def test_external_sensor_dependency_is_reported_not_reowned(self):
        vehicle = FakeActor(9, "vehicle.test")
        sensor = FakeActor(10, "sensor.camera.rgb", parent=vehicle)
        world = FakeWorld([vehicle, sensor])
        self.register_actor(vehicle)
        self.register_sensor(sensor, ActorOwnership.EXTERNAL)

        plan = self.cleaner.prepare_plan(world, self.database, "managed")
        data = plan.to_dict()

        self.assertEqual([9], [item.runtime_id for item in plan.items])
        self.assertEqual([10], [item.runtime_id for item in plan.cascade_dependencies])
        self.assertEqual(1, data["cascade_external_count"])
        self.assertEqual(ActorOwnership.EXTERNAL,
                         self.database.sensors_db.sensors_db[10].ownership)
        self.cleaner.execute_plan(world, plan)
        self.assertFalse(sensor.is_alive)

    def test_existing_unsupported_child_is_recorded_as_cascade_dependency(self):
        vehicle = FakeActor(101, "vehicle.test")
        child = FakeActor(102, "custom.dynamic", parent=vehicle)
        world = FakeWorld([vehicle, child])
        self.register_actor(vehicle)

        plan = self.cleaner.prepare_plan(world, self.database, "managed")

        self.assertEqual(
            [102],
            [item.runtime_id for item in plan.cascade_dependencies],
        )
        report = self.cleaner.execute_plan(world, plan)
        self.assertEqual("success", report.status)

    def test_walker_controller_is_included_and_destroyed_first(self):
        log = []
        walker = FakeActor(11, "walker.pedestrian.0001", log=log)
        controller = FakeActor(
            12, "controller.ai.walker", parent=walker, log=log
        )
        world = FakeWorld([walker, controller])
        self.register_actor(walker)
        self.register_actor(controller, ActorOwnership.EXTERNAL)

        plan = self.cleaner.prepare_plan(world, self.database, "managed")
        self.cleaner.execute_plan(world, plan)

        self.assertEqual({11, 12}, {item.runtime_id for item in plan.items})
        self.assertLess(log.index(("destroy", 12)), log.index(("destroy", 11)))
        self.assertIn(("stop", 12), log)

    def test_infrastructure_is_excluded(self):
        light = FakeActor(13, "traffic.traffic_light")
        plan = self.cleaner.prepare_plan(
            FakeWorld([light]), self.database, "entire_world"
        )
        self.assertEqual([], plan.items)
        self.assertEqual(1, plan.to_dict()["skipped_infrastructure"])

    def test_static_map_baseline_is_preserved_by_entire_world_clear(self):
        native = FakeActor(131, "static.prop.streetbarrier")
        plan = self.cleaner.prepare_plan(
            FakeWorld([native]),
            self.database,
            "entire_world",
            infrastructure_actor_ids={131},
        )

        self.assertEqual([], plan.items)
        self.assertTrue(native.is_alive)
        self.assertEqual(1, plan.to_dict()["skipped_infrastructure"])

    def test_spectator_is_excluded(self):
        spectator = FakeActor(14, "spectator")
        plan = self.cleaner.prepare_plan(
            FakeWorld([spectator]), self.database, "entire_world"
        )
        self.assertEqual([], plan.items)
        self.assertEqual(1, plan.to_dict()["skipped_infrastructure"])

    def test_internal_preview_is_excluded(self):
        preview = FakeActor(
            15, "sensor.camera.rgb", role_name="spectator_preview"
        )
        plan = self.cleaner.prepare_plan(
            FakeWorld([preview]), self.database, "entire_world"
        )
        self.assertEqual([], plan.items)
        self.assertEqual("internal_preview", plan.skipped[0]["reason"])

    def test_cancelled_report_has_no_removals(self):
        report = WorldClearReport.cancelled("managed", 3)
        self.assertEqual("cancelled", report.status)
        self.assertEqual(0, report.removed)

    def test_world_episode_change_invalidates_plan(self):
        vehicle = FakeActor(16, "vehicle.test")
        world = FakeWorld([vehicle], world_id=1)
        self.register_actor(vehicle)
        plan = self.cleaner.prepare_plan(world, self.database, "managed")
        world.id = 2
        with self.assertRaises(StaleClearPlanError):
            self.cleaner.execute_plan(world, plan)

    def test_actor_removed_after_preflight_is_already_missing(self):
        vehicle = FakeActor(17, "vehicle.test")
        world = FakeWorld([vehicle])
        self.register_actor(vehicle)
        plan = self.cleaner.prepare_plan(world, self.database, "managed")
        vehicle.is_alive = False

        report = self.cleaner.execute_plan(world, plan)
        self.assertEqual(1, report.already_missing)
        self.assertEqual("success", report.status)

    def test_new_independent_actor_after_preflight_is_not_removed(self):
        vehicle = FakeActor(18, "vehicle.test")
        world = FakeWorld([vehicle])
        self.register_actor(vehicle)
        plan = self.cleaner.prepare_plan(world, self.database, "managed")
        newcomer = FakeActor(19, "vehicle.test")
        newcomer.world = world
        world.actors.append(newcomer)

        self.cleaner.execute_plan(world, plan)
        self.assertTrue(newcomer.is_alive)

    def test_new_child_dependency_after_preflight_aborts(self):
        vehicle = FakeActor(20, "vehicle.test")
        world = FakeWorld([vehicle])
        self.register_actor(vehicle)
        plan = self.cleaner.prepare_plan(world, self.database, "managed")
        sensor = FakeActor(21, "sensor.camera.rgb", parent=vehicle)
        sensor.world = world
        world.actors.append(sensor)

        with self.assertRaises(StaleClearPlanError):
            self.cleaner.execute_plan(world, plan)
        self.assertTrue(vehicle.is_alive)

    def test_runtime_id_reuse_with_changed_identity_aborts(self):
        vehicle = FakeActor(22, "vehicle.first")
        world = FakeWorld([vehicle])
        self.register_actor(vehicle)
        plan = self.cleaner.prepare_plan(world, self.database, "managed")
        vehicle.is_alive = False
        replacement = FakeActor(22, "vehicle.second")
        replacement.world = world
        world.actors.append(replacement)

        with self.assertRaises(StaleClearPlanError):
            self.cleaner.execute_plan(world, plan)
        self.assertTrue(replacement.is_alive)

    def test_destroy_error_does_not_stop_other_actors(self):
        broken = FakeActor(23, "vehicle.test", destroy_error="denied")
        valid = FakeActor(24, "vehicle.test")
        world = FakeWorld([broken, valid])
        self.register_actor(broken)
        self.register_actor(valid)
        plan = self.cleaner.prepare_plan(world, self.database, "managed")

        report = self.cleaner.execute_plan(world, plan)
        self.assertEqual(1, report.removed)
        self.assertEqual(1, report.failed)
        self.assertEqual("partial_success", report.status)
        self.assertEqual(23, report.errors[0]["runtime_id"])

    def test_sync_after_clear_updates_storage_and_preserves_external(self):
        managed = FakeActor(25, "vehicle.test")
        external = FakeActor(26, "vehicle.test")
        world = FakeWorld([managed, external])
        self.register_actor(managed)
        self.register_actor(external, ActorOwnership.EXTERNAL)
        plan = self.cleaner.prepare_plan(world, self.database, "managed")
        self.cleaner.execute_plan(world, plan)

        WorldSynchronizer().synchronize(world, self.database)

        self.assertNotIn(25, self.database.actors_db.actors_db)
        self.assertIn(26, self.database.actors_db.actors_db)
        self.assertEqual(
            ActorOwnership.EXTERNAL,
            self.database.actors_db.actors_db[26].ownership,
        )


class ControllerClearTests(unittest.TestCase):
    def make_controller(self):
        controller = object.__new__(CarlaController)
        controller.scenario_operation_lock = threading.Lock()
        controller.scenario_operation_pending = False
        controller.scenario_operation_kind = None
        controller.active_clear_plan = None
        controller.clear_execution_queued = False
        controller.shutdown_event = threading.Event()
        controller.running = True
        controller.command_queue = mock.MagicMock()
        controller.database = SimpleNamespace(
            scenario_event_queue=queue.Queue()
        )
        controller.thread = None
        return controller

    def test_prepare_uses_existing_queue_and_blocks_save(self):
        controller = self.make_controller()
        self.assertTrue(controller.prepare_clear_world("Managed Actors Only"))
        controller.command_queue.put.assert_called_once_with((
            "prepare_clear_world", "managed"
        ))
        self.assertFalse(
            controller.save_scenario("Scenario", "scenario.json", "json")
        )

    def test_execute_requires_matching_one_time_token(self):
        controller = self.make_controller()
        plan = SimpleNamespace(token="token", mode="managed", planned_count=1)
        controller.scenario_operation_pending = True
        controller.active_clear_plan = plan

        self.assertFalse(controller.execute_clear_world("wrong"))
        self.assertTrue(controller.execute_clear_world("token", False))
        self.assertFalse(controller.execute_clear_world("token", True))

    def test_disconnect_invalidates_waiting_plan(self):
        controller = self.make_controller()
        controller.scenario_operation_pending = True
        controller.active_clear_plan = SimpleNamespace(
            token="token", mode="managed", planned_count=2
        )

        controller.shutdown()

        self.assertIsNone(controller.active_clear_plan)
        self.assertFalse(controller.scenario_operation_pending)
        status, report = controller.database.scenario_event_queue.get_nowait()
        self.assertEqual("world_clear_completed", status)
        self.assertEqual("cancelled", report["status"])

    def test_map_change_is_rejected_while_plan_waits(self):
        controller = self.make_controller()
        controller.scenario_operation_pending = True
        controller.world_changed_event = threading.Event()
        controller.database.world_changed = False
        controller.database.world_change_error = None

        self.assertFalse(controller.change_world("Town04"))
        controller.command_queue.put.assert_not_called()

    def test_lifecycle_cleanup_uses_existing_preview_and_control_helpers(self):
        controller = object.__new__(CarlaController)
        controller.cleanup_movement_preview = mock.MagicMock()
        controller.cleanup_sensor_previews = mock.MagicMock()
        controller.cleanup_preview = mock.MagicMock()
        controller.stop_sensor_preview_actor = mock.MagicMock()
        controller._destroy_walker_ai = mock.MagicMock()
        controller._release_vehicle_control = mock.MagicMock()
        controller.database = SimpleNamespace(
            lock=threading.Lock(),
            vehicle_control_states={1: {}},
            walker_ai_states={2: {}},
        )
        plan = SimpleNamespace(items=[
            SimpleNamespace(runtime_id=1, category=ActorCategory.VEHICLE),
            SimpleNamespace(runtime_id=2, category=ActorCategory.PEDESTRIAN),
            SimpleNamespace(runtime_id=3, category=ActorCategory.SENSOR),
        ])

        controller._prepare_clear_lifecycle(plan)

        controller.cleanup_movement_preview.assert_called_once_with(
            "clear_world"
        )
        controller.cleanup_sensor_previews.assert_called_once_with()
        controller.cleanup_preview.assert_called_once_with()
        controller.stop_sensor_preview_actor.assert_called_once_with(3)
        controller._destroy_walker_ai.assert_called_once_with(
            2, remove_state=False
        )
        controller._release_vehicle_control.assert_called_once_with(
            1, disable_autopilot=True
        )
        self.assertIn(1, controller.database.vehicle_control_states)
        self.assertIn(2, controller.database.walker_ai_states)


class GuiClearTests(unittest.TestCase):
    def test_preflight_message_contains_counts_and_strong_warning(self):
        message = Manager._clear_preflight_message({
            "mode": "entire_world",
            "world_id": 9,
            "vehicles": 2,
            "pedestrians": 1,
            "static_objects": 3,
            "sensors": 4,
            "walker_ai_controllers": 1,
            "external_count": 5,
            "skipped_infrastructure": 6,
            "cascade_external_count": 1,
            "warnings": ["cascade"],
        })
        self.assertIn("Vehicles: 2", message)
        self.assertIn("Skipped infrastructure: 6", message)
        self.assertIn("other CARLA clients", message)
        self.assertIn("Cascade warning", message)

    @mock.patch("gui.application.messagebox.askyesno", return_value=False)
    def test_user_cancellation_executes_plan_as_not_confirmed(self, ask):
        manager = object.__new__(Manager)
        frame = mock.MagicMock()
        manager.main_view = SimpleNamespace(
            simulation_control_frame=SimpleNamespace(scenario_frame=frame)
        )
        manager.carla = mock.MagicMock()
        manager.movement_control = mock.MagicMock()
        manager.close_spectator_preview = mock.MagicMock()
        plan = {
            "token": "token",
            "mode": "managed",
            "world_id": 1,
            "planned_count": 1,
        }

        manager._handle_clear_preflight(plan)

        ask.assert_called_once_with(
            "Confirm Clear World",
            "Are you sure you want to remove all GUI-managed actors from "
            "the current world?",
        )
        manager.carla.execute_clear_world.assert_called_once_with(
            "token", confirmed=False
        )
        manager.movement_control.request_close.assert_not_called()
        manager.close_spectator_preview.assert_not_called()

    @mock.patch("gui.application.messagebox.askyesno", return_value=True)
    def test_entire_world_confirmation_warns_and_executes_once(self, ask):
        manager = object.__new__(Manager)
        frame = mock.MagicMock()
        manager.main_view = SimpleNamespace(
            simulation_control_frame=SimpleNamespace(scenario_frame=frame)
        )
        manager.carla = mock.MagicMock()
        manager.movement_control = mock.MagicMock()
        manager.close_spectator_preview = mock.MagicMock()
        plan = {
            "token": "entire-token",
            "mode": "entire_world",
            "world_id": 1,
            "planned_count": 2,
        }

        manager._handle_clear_preflight(plan)

        ask.assert_called_once_with(
            "Confirm Clear World",
            "Are you sure you want to remove all supported actors from the "
            "current world, including actors created by other CARLA clients? "
            "This action may affect other users connected to the server.",
        )
        manager.carla.execute_clear_world.assert_called_once_with(
            "entire-token",
            confirmed=True,
        )
        manager.movement_control.request_close.assert_called_once_with(
            "World is being cleared"
        )
        manager.close_spectator_preview.assert_called_once_with()

    @mock.patch("gui.application.messagebox.showinfo")
    def test_completed_event_refreshes_gui(self, showinfo):
        manager = object.__new__(Manager)
        manager.connected = True
        manager.disconnecting = False
        manager.closing = False
        manager.scenario_clearing = True
        manager.database = SimpleNamespace(scenario_event_queue=queue.Queue())
        manager.database.scenario_event_queue.put((
            "world_clear_completed",
            {
                "status": "success",
                "planned_count": 1,
                "removed": 1,
                "already_missing": 0,
                "failed": 0,
            },
        ))
        frame = mock.MagicMock()
        simulation_frame = mock.MagicMock()
        simulation_frame.scenario_frame = frame
        connection_frame = mock.MagicMock()
        dashboard_frame = mock.MagicMock()
        manager.main_view = SimpleNamespace(
            simulation_control_frame=simulation_frame,
            main_view=SimpleNamespace(
                connection_frame=connection_frame,
                dashboard_frame=dashboard_frame,
            ),
        )
        manager._refresh_actor_tables = mock.MagicMock()

        manager._check_scenario_events()

        self.assertFalse(manager.scenario_clearing)
        frame.show_clear_report.assert_called_once()
        manager._refresh_actor_tables.assert_called_once_with()
        simulation_frame.set_external_busy.assert_called_once_with(False)
        showinfo.assert_called_once()


if __name__ == "__main__":
    unittest.main()
