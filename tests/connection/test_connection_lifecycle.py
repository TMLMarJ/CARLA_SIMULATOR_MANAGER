import inspect
import queue
import unittest
from types import SimpleNamespace
from unittest import mock

from core.state_store import DataStorage
from gui.application import Manager
from gui.tabs.world import CustomWorldAndWeatherFrame


def connection_manager():
    connection_frame = SimpleNamespace(
        update_connection_state=mock.Mock(),
        show_sync_result=mock.Mock(),
        set_sync_pending=mock.Mock(),
    )
    dashboard_frame = SimpleNamespace(
        show_context=mock.Mock(),
        clear_context=mock.Mock(),
    )
    scenario_frame = SimpleNamespace(set_connected=mock.Mock())
    simulation_frame = SimpleNamespace(
        scenario_frame=scenario_frame,
        set_connected=mock.Mock(),
        set_external_busy=mock.Mock(),
    )
    main_view = SimpleNamespace(
        main_view=SimpleNamespace(
            connection_frame=connection_frame,
            dashboard_frame=dashboard_frame,
        ),
        simulation_control_frame=simulation_frame,
        initialize_connected_views=mock.Mock(),
        clear_connected_views=mock.Mock(),
        tabControl=SimpleNamespace(
            unbind=mock.Mock(),
            select=mock.Mock(),
        ),
        actors_tab_control=SimpleNamespace(unbind=mock.Mock()),
        main_tab=object(),
        set_tabs_enabled=mock.Mock(),
    )
    manager = Manager.__new__(Manager)
    manager.carla = None
    manager.database = DataStorage()
    manager.main_view = main_view
    manager.connected = False
    manager.disconnecting = False
    manager.closing = False
    manager.world_changing = False
    manager.scenario_saving = False
    manager.scenario_clearing = False
    manager.scenario_loading = False
    manager.scenario_load_plan = None
    manager.pending_movement_control_actor_id = None
    manager.spectator_preview = SimpleNamespace(
        poll_events=mock.Mock(),
        is_active=mock.Mock(return_value=False),
        request_stop=mock.Mock(),
    )
    manager.movement_control = SimpleNamespace(
        request_close=mock.Mock(),
        handle_disconnect=mock.Mock(),
    )
    manager.after = mock.Mock()
    manager.update_spectator_state = mock.Mock()
    manager.close_spectator_preview = mock.Mock()
    manager.finish_application_close = mock.Mock()
    return manager


class ConnectionLifecycleTests(unittest.TestCase):
    def test_connect_starts_controller_for_requested_endpoint_and_polls(self):
        manager = connection_manager()
        controller = mock.Mock()

        with mock.patch(
            "gui.application.CarlaController",
            return_value=controller,
        ) as controller_type:
            manager.connect_to_carla("carla-host", 2500)

        controller_type.assert_called_once_with(
            manager.database,
            manager.get_spectator_runtime_status,
            "carla-host",
            2500,
        )
        controller.start.assert_called_once_with()
        manager.after.assert_called_once_with(100, manager.check_connection)
        manager.main_view.main_view.connection_frame.update_connection_state.assert_called_once_with(
            False,
            busy=True,
        )

    def test_connect_is_blocked_while_spectator_process_is_active(self):
        manager = connection_manager()
        manager.spectator_preview.is_active.return_value = True

        with (
            mock.patch("gui.application.CarlaController") as controller_type,
            mock.patch("gui.application.messagebox.showinfo") as showinfo,
        ):
            manager.connect_to_carla()

        controller_type.assert_not_called()
        showinfo.assert_called_once()
        manager.after.assert_not_called()

    def test_connection_error_finishes_cleanup_without_rescheduling_poll(self):
        manager = connection_manager()
        manager.carla = SimpleNamespace(connection_queue=queue.Queue())
        manager.carla.connection_queue.put(("connection_error", "timeout"))
        manager.finish_disconnection = mock.Mock()

        with mock.patch("gui.application.messagebox.showerror") as showerror:
            manager.check_connection()

        showerror.assert_called_once()
        manager.finish_disconnection.assert_called_once_with(error="timeout")
        manager.after.assert_not_called()

    def test_finish_disconnection_replaces_state_store_and_rebinds_view(self):
        manager = connection_manager()
        old_database = manager.database
        manager.carla = object()
        manager.connected = True
        manager.disconnecting = True
        manager.world_changing = True
        manager.scenario_saving = True
        manager.scenario_clearing = True
        manager.scenario_loading = True
        manager.scenario_load_plan = object()

        manager.finish_disconnection(error="connection lost")

        self.assertIsNone(manager.carla)
        self.assertIsNot(manager.database, old_database)
        self.assertIs(manager.main_view.database, manager.database)
        self.assertFalse(manager.connected)
        self.assertFalse(manager.disconnecting)
        self.assertFalse(manager.world_changing)
        self.assertFalse(manager.scenario_saving)
        self.assertFalse(manager.scenario_clearing)
        self.assertFalse(manager.scenario_loading)
        self.assertIsNone(manager.scenario_load_plan)
        manager.spectator_preview.request_stop.assert_called_once_with()
        manager.movement_control.handle_disconnect.assert_called_once_with()
        manager.main_view.clear_connected_views.assert_called_once_with()
        manager.main_view.main_view.dashboard_frame.show_context.assert_called_once_with(
            "Error",
            "connection lost",
        )

    def test_finish_disconnection_carries_only_reconnect_metadata(self):
        manager = connection_manager()
        manager.carla = SimpleNamespace(
            map_infrastructure_world_id=4,
            map_infrastructure_actor_ids={10, 11},
            map_infrastructure_map_name="Town04",
        )
        manager.database.export_reconnect_state = mock.Mock(return_value={
            "managed_actor_hints": {},
            "world_generation": 5,
            "infrastructure_world_id": 4,
            "infrastructure_map_name": "Town04",
            "infrastructure_actor_ids": (10, 11),
        })

        manager.finish_disconnection()

        self.assertEqual(4, manager.database.reconnect_infrastructure_world_id)
        self.assertEqual("Town04",
                         manager.database.reconnect_infrastructure_map_name)
        self.assertEqual(5, manager.database.world_generation)
        self.assertEqual(
            {10, 11},
            manager.database.reconnect_infrastructure_actor_ids,
        )

    def test_disconnect_requests_resource_shutdown_only_once(self):
        manager = connection_manager()
        controller = SimpleNamespace(
            stop_simulation_control=mock.Mock(),
            shutdown=mock.Mock(),
        )
        manager.carla = controller
        manager.connected = True

        manager.disconnect_from_carla()
        manager.disconnect_from_carla()

        self.assertTrue(manager.disconnecting)
        self.assertFalse(manager.connected)
        manager.movement_control.request_close.assert_called_once_with(
            "CARLA disconnecting"
        )
        manager.close_spectator_preview.assert_called_once_with()
        controller.stop_simulation_control.assert_called_once_with()
        controller.shutdown.assert_called_once_with()

    @mock.patch("gui.application.messagebox.askyesno", return_value=False)
    def test_map_change_cancellation_has_no_cleanup_side_effects(self, ask):
        manager = connection_manager()
        manager.connected = True
        manager.carla = mock.Mock()

        accepted = manager.begin_world_change()

        self.assertFalse(accepted)
        self.assertFalse(manager.world_changing)
        ask.assert_called_once_with(
            "Confirm Map Change",
            "Loading a new map will reset the current simulation world and "
            "remove its existing actors. This may also affect other users "
            "connected to the CARLA server. Do you want to continue?",
        )
        manager.movement_control.request_close.assert_not_called()
        manager.carla.stop_simulation_control.assert_not_called()
        manager.spectator_preview.request_stop.assert_not_called()
        manager.main_view.simulation_control_frame.set_external_busy.assert_not_called()

    def test_map_change_confirmation_precedes_cleanup(self):
        manager = connection_manager()
        manager.connected = True
        manager.carla = mock.Mock()
        calls = []
        manager.movement_control.request_close.side_effect = (
            lambda _reason: calls.append("movement_cleanup")
        )
        manager.carla.stop_simulation_control.side_effect = (
            lambda: calls.append("simulation_stop")
        )

        with mock.patch(
            "gui.application.messagebox.askyesno",
            side_effect=lambda *_args: calls.append("confirmation") or True,
        ) as ask:
            accepted = manager.begin_world_change()

        self.assertTrue(accepted)
        self.assertTrue(manager.world_changing)
        self.assertEqual(
            ["confirmation", "movement_cleanup", "simulation_stop"],
            calls,
        )
        ask.assert_called_once()

    def test_world_tab_stops_before_queueing_change_when_not_confirmed(self):
        frame = object.__new__(CustomWorldAndWeatherFrame)
        frame.world_change_pending = False
        frame.apply_world_button = mock.Mock()
        frame._wait_for_spectator_close = mock.Mock()
        manager = SimpleNamespace(begin_world_change=mock.Mock(return_value=False))
        frame.controller = SimpleNamespace(manager=manager)

        frame._apply_world()

        manager.begin_world_change.assert_called_once_with()
        self.assertFalse(frame.world_change_pending)
        frame.apply_world_button.configure.assert_not_called()
        frame._wait_for_spectator_close.assert_not_called()

    def test_scenario_load_has_its_own_single_confirmation_path(self):
        load_source = inspect.getsource(Manager._handle_load_preflight)

        self.assertEqual(1, load_source.count("messagebox.askyesno"))
        self.assertNotIn("begin_world_change", load_source)


if __name__ == "__main__":
    unittest.main()
