import copy
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from core.controller import CarlaController
from core.registries import Actors
from core.state_store import DataStorage
from gui.main_view import MainView
from gui.styles import settings
from gui.windows.movement_control import MovementControlWindow
from models.scenarios import ActorOwnership
from movement.session import MovementControlSessionManager
from preview.spectator import SpectatorPreviewManager


class Manager(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("CARLA Manager")
        self.geometry("1180x950")
        self.minsize(1020, 680)
        self.configure(bg="#F7E7D3")

        style = ttk.Style(self)

        if "Autumn" not in style.theme_names():
            style.theme_create(
                "Autumn",
                parent="clam",
                settings=settings
            )

        style.theme_use("Autumn")

        self.database = DataStorage()
        self.carla = None
        self.connected = False
        self.closing = False
        self.disconnecting = False
        self.world_changing = False
        self.scenario_saving = False
        self.scenario_clearing = False
        self.scenario_loading = False
        self.scenario_load_plan = None
        self.spectator_preview = SpectatorPreviewManager()
        self.spectator_state = "Closed"
        self.spectator_camera_id = None
        self.spectator_check_job = None
        self.simulation_check_job = None
        self.movement_control = MovementControlSessionManager(
            self,
            window_factory=MovementControlWindow,
        )
        self.pending_movement_control_actor_id = None

        self.main_view = None
        self.create_main_window()
        self.check_spectator_preview()
        self.check_simulation_events()

    def create_main_window(self):
        self.main_view = MainView(self)
        self.main_view.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def get_spectator_runtime_status(self):
        return {
            "state": self.spectator_state,
            "camera_id": self.spectator_camera_id,
        }

    def on_close(self):
        if self.closing:
            return
        print("Closing app...")
        self.closing = True
        self.movement_control.request_close("Application closing")
        self.close_spectator_preview()
        if self.carla is None:
            self.finish_application_close()
            return
        self.disconnect_from_carla()

    def connect_to_carla(self, host="127.0.0.1", port=2000):
        if self.carla is not None:
            return
        self.spectator_preview.poll_events()
        if self.spectator_preview.is_active():
            messagebox.showinfo(
                "Spectator View",
                "Wait for Spectator View to close before connecting again."
            )
            return

        self.connected = False
        self.main_view.main_view.connection_frame.update_connection_state(
            False,
            busy=True,
        )
        self.main_view.main_view.dashboard_frame.show_context("Connecting")

        self.carla = CarlaController(
            self.database,
            self.get_spectator_runtime_status,
            host,
            port
        )

        self.carla.start()
        self.after(100, self.check_connection)

    def check_connection(self):
        if self.carla is None:
            return

        try:
            while True:
                status, data = self.carla.connection_queue.get_nowait()

                if status == "connected":
                    if not self.disconnecting and not self.closing:
                        self.connected = True
                        print("Connected to CARLA")

                        self.main_view.initialize_connected_views()
                        self.main_view.main_view.connection_frame.update_connection_state(
                            True
                        )
                        self.main_view.simulation_control_frame.scenario_frame.set_connected(
                            True
                        )
                        if isinstance(data, dict):
                            self.main_view.main_view.connection_frame.show_sync_result(
                                data
                            )
                        self.main_view.main_view.dashboard_frame.clear_context()
                        self.main_view.simulation_control_frame.set_connected(
                            True,
                            self.database.actual_simulation_settings,
                            self.database.simulation_control_state,
                        )
                        if self.spectator_preview.is_active():
                            self.update_spectator_state("Stopping")
                        else:
                            self.update_spectator_state("Closed")

                elif status == "connection_error":
                    self.connected = False
                    print(f"Connection error: {data}")

                    if not self.closing:
                        messagebox.showerror("CARLA Connection Error",
                                             f"Could not connect to CARLA:\n{data}")
                    self.finish_disconnection(error=data)
                    return

                elif status == "disconnected":
                    print("DEBUG: DISCONNECTED EVENT RECEIVED")
                    self.finish_disconnection()
                    return
        except queue.Empty:
            pass

        self.after(100, self.check_connection)

    def finish_disconnection(self, error=None):
        print("Disconnected from CARLA")

        reconnect_state = self.database.export_reconnect_state(
            infrastructure_world_id=getattr(
                self.carla,
                "map_infrastructure_world_id",
                None,
            ),
            infrastructure_actor_ids=getattr(
                self.carla,
                "map_infrastructure_actor_ids",
                (),
            ),
            infrastructure_map_name=getattr(
                self.carla,
                "map_infrastructure_map_name",
                None,
            ),
        )

        self.connected = False
        self.disconnecting = False
        self.world_changing = False
        self.scenario_saving = False
        self.scenario_clearing = False
        self.scenario_loading = False
        self.scenario_load_plan = None
        self.spectator_preview.request_stop()
        self.movement_control.handle_disconnect()

        self.main_view.clear_connected_views()
        self.main_view.simulation_control_frame.set_connected(False)

        self.carla = None

        self.database = DataStorage(reconnect_state=reconnect_state)
        self.main_view.database = self.database
        self.main_view.actor_manager = Actors()
        self.main_view.main_view.connection_frame.update_connection_state(False)
        self.main_view.simulation_control_frame.scenario_frame.set_connected(
            False
        )
        self.main_view.main_view.dashboard_frame.show_context(
            "Error" if error else "Disconnected",
            error,
        )

        if self.closing:
            self.finish_application_close()
        elif self.spectator_preview.is_active():
            self.update_spectator_state("Stopping")
        else:
            self.update_spectator_state("Closed")

    def disconnect_from_carla(self):
        print("DEBUG: disconnect_from_carla CALLED")
        if self.carla is None or self.disconnecting:
            return

        print("Disconnecting from CARLA")
        self.disconnecting = True
        self.connected = False
        self.world_changing = False
        self.pending_movement_control_actor_id = None
        self.main_view.tabControl.unbind("<<NotebookTabChanged>>")
        self.main_view.actors_tab_control.unbind("<<NotebookTabChanged>>")
        self.main_view.tabControl.select(self.main_view.main_tab)
        self.main_view.set_tabs_enabled(False)
        self.main_view.simulation_control_frame.set_connected(False)
        self.main_view.simulation_control_frame.scenario_frame.set_connected(
            False
        )
        self.main_view.main_view.dashboard_frame.show_context("Disconnected")
        self.movement_control.request_close("CARLA disconnecting")
        self.close_spectator_preview()
        self.carla.stop_simulation_control()
        self.carla.shutdown()

    def apply_simulation_settings(self, settings):
        if (
            not self.connected
            or self.disconnecting
            or self.closing
            or self.world_changing
            or self.scenario_loading
            or self.carla is None
        ):
            return

        with self.database.lock:
            self.database.proposed_simulation_settings = settings.copy()
        self.main_view.simulation_control_frame.set_pending()
        self.carla.apply_simulation_settings(settings)

    def set_tick_controller(self, controller):
        if not self._simulation_control_available():
            return
        self.main_view.simulation_control_frame.set_control_pending(
            "Changing tick controller..."
        )
        self.carla.set_tick_controller(controller)

    def run_simulation(self):
        if not self._simulation_control_available():
            return
        self.main_view.simulation_control_frame.set_control_pending(
            "Starting GUI ticks..."
        )
        self.carla.run_simulation()

    def pause_simulation(self):
        if not self._simulation_control_available():
            return
        self.main_view.simulation_control_frame.set_control_pending(
            "Stopping after the current tick..."
        )
        self.carla.pause_simulation()

    def step_simulation(self, count=1):
        if not self._simulation_control_available():
            return
        self.main_view.simulation_control_frame.set_control_pending(
            f"Requesting {count} tick{'s' if count != 1 else ''}..."
        )
        self.carla.step_simulation(count)

    def _simulation_control_available(self):
        return (
            self.connected
            and not self.disconnecting
            and not self.closing
            and not self.world_changing
            and not self.scenario_saving
            and not self.scenario_clearing
            and not self.scenario_loading
            and self.carla is not None
        )

    def refresh_world(self):
        if not self._simulation_control_available():
            return
        self.main_view.main_view.connection_frame.set_sync_pending()
        self.carla.sync_world("manual")

    def confirm_take_actor_ownership(self, runtime_id, type_id):
        if not self._simulation_control_available():
            return
        confirmed = messagebox.askyesno(
            "Take Ownership",
            "Adopt this external actor into GUI management?\n\n"
            f"Runtime ID: {runtime_id}\n"
            f"Type: {type_id}\n\n"
            "The actor will qualify for Scenario Save and Managed Clear. "
            "Other CARLA clients can still control or delete it, and its "
            "current external control state may be unknown.",
        )
        if not confirmed:
            return
        self._request_actor_ownership(runtime_id)

    def _request_actor_ownership(self, runtime_id, movement_control=False):
        if not self.carla.take_actor_ownership(runtime_id):
            return False
        if movement_control:
            self.pending_movement_control_actor_id = int(runtime_id)
        dashboard = self.main_view.main_view.dashboard_frame
        dashboard.set_adoption_pending(True)
        dashboard.adoption_status_var.set("Taking ownership...")
        return True

    def save_scenario(self, name, path, file_format):
        if not self._simulation_control_available():
            return
        accepted = self.carla.save_scenario(name, path, file_format)
        if accepted:
            self.scenario_saving = True
            self.main_view.simulation_control_frame.scenario_frame.set_pending()

    def prepare_clear_world(self, mode):
        if not self._simulation_control_available():
            return
        accepted = self.carla.prepare_clear_world(mode)
        if accepted:
            self.scenario_clearing = True
            self.main_view.simulation_control_frame.set_external_busy(True)
            self.main_view.simulation_control_frame.scenario_frame.set_clear_pending()
            self.main_view.main_view.dashboard_frame.show_context(
                "Clear World preflight"
            )

    def load_scenario(self):
        if not self._simulation_control_available():
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="Load CARLA scenario",
            filetypes=(
                ("CARLA scenarios", "*.json *.yaml *.yml"),
                ("JSON scenario", "*.json"),
                ("YAML scenario", "*.yaml *.yml"),
                ("All files", "*.*"),
            ),
        )
        if not path:
            return

        accepted = self.carla.prepare_load_scenario(path)
        if not accepted:
            return
        self.scenario_loading = True
        self.scenario_load_plan = None
        simulation_frame = self.main_view.simulation_control_frame
        simulation_frame.set_external_busy(True)
        simulation_frame.scenario_frame.set_load_pending()
        self.main_view.main_view.dashboard_frame.show_context(
            "Scenario Load preflight"
        )

    @staticmethod
    def _load_preflight_message(plan):
        actors = plan.get("actors") or []
        sensor_count = sum(
            1 for actor in actors
            if str(actor.get("category", "")).lower() == "sensor"
        )
        metadata = plan.get("metadata") or {}
        lines = [
            f"Scenario: {metadata.get('name') or '-'}",
            f"Scenario map: {plan.get('target_map') or '-'}",
            f"Current map: {plan.get('current_map') or '-'}",
            "Map change required: "
            f"{'yes' if plan.get('map_change_required') else 'no'}",
            f"Actors: {plan.get('actor_count', 0)}",
            f"Sensors: {sensor_count}",
            f"Skipped external: {plan.get('skipped_external', 0)}",
            "Skipped infrastructure: "
            f"{plan.get('skipped_infrastructure', 0)}",
            f"Readiness: {plan.get('status', 'unknown')}",
            "Clear policy: selective scenario replacement.",
        ]
        if plan.get("map_change_required"):
            lines.append(
                "Changing the map replaces the complete CARLA world, "
                "including external static objects."
            )
        else:
            lines.append(
                "External static objects are preserved on the current map."
            )
        warnings = plan.get("warnings") or []
        critical = plan.get("critical_problems") or []
        unverified = plan.get("unverified_items") or []
        if warnings:
            lines.extend(["", "Warnings:"])
            lines.extend(f"- {item}" for item in warnings)
        if critical:
            lines.extend(["", "Critical problems (Load is blocked):"])
            lines.extend(f"- {item}" for item in critical)
        if unverified:
            lines.extend(["", "Unverified items:"])
            lines.extend(f"- {item}" for item in unverified)
        return "\n".join(lines)

    @classmethod
    def _load_confirmation_message(cls, plan):
        lines = [
            cls._load_preflight_message(plan),
            "",
            "This operation replaces the managed scenario configuration. "
            "On the current map it removes all vehicles and pedestrians, "
            "managed static objects, and dependent sensors/controllers.",
            "There is no automatic rollback.",
        ]
        if plan.get("map_change_required"):
            lines.extend([
                "",
                "MAP CHANGE WARNING: loading the target map itself removes "
                "all actors from the current map.",
            ])
        else:
            lines.append(
                "External static objects are preserved on the current map."
            )
        lines.extend(["", "Continue with Scenario Load?"])
        return "\n".join(lines)

    def _handle_load_preflight(self, plan):
        frame = self.main_view.simulation_control_frame.scenario_frame
        self.scenario_load_plan = plan
        frame.show_load_preflight(plan)
        if plan.get("critical_problems"):
            self._finish_load_gui()
            frame.show_load_error("Preflight found critical problems.")
            messagebox.showerror(
                "Load Scenario preflight",
                self._load_preflight_message(plan),
            )
            return

        confirmed = messagebox.askyesno(
            "Load Scenario � destructive operation",
            self._load_confirmation_message(plan),
        )
        if not confirmed:
            self._finish_load_gui()
            frame.show_load_cancelled()
            return

        controller = self.carla
        if (
            controller is None
            or not self.connected
            or self.disconnecting
            or self.closing
        ):
            self._finish_load_gui()
            frame.show_load_error("The CARLA connection is no longer available.")
            return
        accepted = controller.execute_load_scenario(
            plan.get("token"),
            confirmed=True,
        )
        if accepted:
            frame.set_load_pending("Loading scenario...")
            self.main_view.main_view.dashboard_frame.show_context(
                "Loading Scenario"
            )
        else:
            self._finish_load_gui()
            frame.show_load_error(
                "The preflight plan is no longer valid. Run Load again."
            )

    def _finish_load_gui(self):
        self.scenario_loading = False
        self.scenario_load_plan = None
        if self.connected and not self.disconnecting and not self.closing:
            self.main_view.simulation_control_frame.set_external_busy(False)
            self.main_view.main_view.connection_frame.update_connection_state(
                True
            )
            self.main_view.main_view.dashboard_frame.clear_context()

    @staticmethod
    def _load_report_message(report):
        lines = [
            f"Status: {report.get('status', 'failed')}",
            f"Scenario: {report.get('name') or '-'}",
            f"Planned actors: {report.get('planned', 0)}",
            f"Source actors omitted by scope: "
            f"{report.get('scope_skipped', 0)}",
            f"Restored: {report.get('restored', 0)}",
            f"Skipped: {report.get('skipped', 0)}",
            f"Failed: {report.get('failed', 0)}",
            "Map changed: "
            f"{'yes' if report.get('map_changed') else 'no'}",
        ]
        clear_report = report.get("clear_report") or {}
        if clear_report:
            lines.extend([
                "",
                "Clear before Load:",
                f"- Planned: {clear_report.get('planned_count', 0)}",
                f"- Confirmed removed: {clear_report.get('removed', 0)}",
                f"- Already missing: "
                f"{clear_report.get('already_missing', 0)}",
                f"- Failed: {clear_report.get('failed', 0)}",
            ])
            if clear_report.get("remaining_after_clear"):
                lines.append(
                    "- Still qualifying after Clear: "
                    f"{clear_report['remaining_after_clear']}"
                )
            for error in clear_report.get("errors") or []:
                runtime_id = error.get("runtime_id")
                target = (
                    f"actor {runtime_id}"
                    if runtime_id is not None else "world inventory"
                )
                lines.append(
                    f"- Failed {target}: "
                    f"{error.get('message', 'Unknown error')}"
                )
        warnings = report.get("warnings") or []
        if warnings:
            lines.extend(["", "Warnings:"])
            lines.extend(f"- {item}" for item in warnings)
        errors = report.get("errors") or []
        if errors:
            lines.extend(["", "Errors:"])
            for error in errors:
                actor_details = [
                    value for value in (
                        error.get("user_name"),
                        error.get("blueprint_id"),
                        error.get("scenario_id"),
                    )
                    if value
                ]
                suffix = (
                    f" [{' | '.join(actor_details)}]"
                    if actor_details else ""
                )
                lines.append(
                    f"- {error.get('stage', 'unknown')}{suffix}: "
                    f"{error.get('message', 'Unknown error')}"
                )
        destructive_stages = {
            "clear", "map_change", "post_map_validation", "world_settings",
            "actor_spawn", "physics", "sensor_attachment", "control",
            "synchronization",
        }
        if (
            report.get("map_changed")
            or report.get("clear_report")
            or any(error.get("stage") in destructive_stages for error in errors)
        ) and report.get("status") != "success":
            lines.extend([
                "",
                "IMPORTANT: the previous world may already have been replaced; "
                "automatic rollback is not available.",
            ])
        return "\n".join(lines)

    @staticmethod
    def _clear_preflight_message(plan):
        mode = (
            "Managed Actors Only"
            if plan.get("mode") == "managed"
            else "Entire World"
        )
        lines = [
            f"Mode: {mode}",
            f"World episode: {plan.get('world_id')}",
            "",
            f"Vehicles: {plan.get('vehicles', 0)}",
            f"Pedestrians: {plan.get('pedestrians', 0)}",
            f"Static objects: {plan.get('static_objects', 0)}",
            f"Sensors: {plan.get('sensors', 0)}",
            "Walker AI controllers: "
            f"{plan.get('walker_ai_controllers', 0)}",
            f"External actors affected: {plan.get('external_count', 0)}",
            "Skipped infrastructure: "
            f"{plan.get('skipped_infrastructure', 0)}",
        ]
        cascade_count = plan.get("cascade_external_count", 0)
        if cascade_count:
            lines.extend([
                "",
                f"Cascade warning: {cascade_count} external child actor(s) "
                "may be removed together with their parent.",
            ])
        warnings = plan.get("warnings") or []
        if warnings:
            lines.extend(["", "Warnings:"])
            lines.extend(f"- {warning}" for warning in warnings)
        if plan.get("mode") == "entire_world":
            lines.extend([
                "",
                "WARNING: This also removes qualifying actors created by "
                "other CARLA clients.",
            ])
        lines.extend(["", "Continue with this exact preflight plan?"])
        return "\n".join(lines)

    def _handle_clear_preflight(self, plan):
        frame = self.main_view.simulation_control_frame.scenario_frame
        if plan.get("world_already_clear"):
            self.scenario_clearing = False
            self.main_view.simulation_control_frame.set_external_busy(False)
            self.main_view.main_view.dashboard_frame.clear_context()
            frame.show_clear_report({
                "status": "success",
                "planned_count": 0,
                "removed": 0,
                "already_missing": 0,
                "failed": 0,
            })
            messagebox.showinfo("Clear World", "World already clear")
            return

        frame.show_clear_preflight(plan)
        confirmation_message = (
            "Are you sure you want to remove all supported actors from the "
            "current world, including actors created by other CARLA clients? "
            "This action may affect other users connected to the server."
            if plan.get("mode") == "entire_world"
            else (
                "Are you sure you want to remove all GUI-managed actors from "
                "the current world?"
            )
        )
        confirmed = messagebox.askyesno(
            "Confirm Clear World",
            confirmation_message,
        )
        if confirmed:
            self.movement_control.request_close("World is being cleared")
            self.close_spectator_preview()
            frame.set_clear_pending("Clearing approved actors...")
        else:
            frame.set_clear_pending("Cancelling Clear World...")
        self.carla.execute_clear_world(
            plan.get("token"),
            confirmed=confirmed,
        )

    def _finish_clear_gui(self):
        self.scenario_clearing = False
        if self.connected and not self.disconnecting and not self.closing:
            self.main_view.simulation_control_frame.set_external_busy(False)
            self.main_view.main_view.connection_frame.update_connection_state(
                True
            )
            self.main_view.main_view.dashboard_frame.clear_context()

    def check_simulation_events(self):
        self.simulation_check_job = None

        try:
            while True:
                status, data = self.database.simulation_event_queue.get_nowait()

                if status in (
                    "simulation_settings_loaded",
                    "simulation_settings_applied",
                ):
                    if self.connected and not self.disconnecting and not self.closing:
                        label = (
                            "Settings applied"
                            if status == "simulation_settings_applied"
                            else "Settings loaded"
                        )
                        self.main_view.simulation_control_frame.load_settings(
                            data,
                            label,
                        )
                elif status == "simulation_settings_error":
                    if self.connected and not self.disconnecting and not self.closing:
                        self.main_view.simulation_control_frame.show_error(
                            data["message"],
                            data["settings"],
                        )
                elif status == "simulation_control_state":
                    if self.connected and not self.disconnecting and not self.closing:
                        self.main_view.simulation_control_frame.update_control_state(
                            data
                        )
                elif status == "simulation_control_command_error":
                    if self.connected and not self.disconnecting and not self.closing:
                        self.main_view.simulation_control_frame.show_control_error(
                            data
                        )
        except queue.Empty:
            pass

        self._check_scenario_events()

        self._refresh_dashboard()
        self.movement_control.poll()

        if self.winfo_exists():
            self.simulation_check_job = self.after(
                100,
                self.check_simulation_events,
            )

    def _check_scenario_events(self):
        try:
            while True:
                status, data = self.database.scenario_event_queue.get_nowait()
                if status == "world_sync_completed":
                    if self.connected and not self.disconnecting and not self.closing:
                        self._refresh_actor_tables()
                        self.main_view.main_view.connection_frame.show_sync_result(
                            data
                        )
                elif status == "world_sync_error":
                    if self.connected and not self.disconnecting and not self.closing:
                        message = (
                            data.get("message", "Unknown synchronization error")
                            if isinstance(data, dict)
                            else str(data)
                        )
                        self.main_view.main_view.connection_frame.show_sync_error(
                            message
                        )
                elif status == "world_sync_operation_blocked":
                    if self.connected and not self.disconnecting and not self.closing:
                        messagebox.showinfo(
                            "External actor",
                            data.get("message", "The operation is unavailable."),
                        )
                elif status == "actor_ownership_result":
                    if self.connected and not self.disconnecting and not self.closing:
                        runtime_id = data.get("runtime_id")
                        continue_with_movement = (
                            self.pending_movement_control_actor_id is not None
                            and runtime_id is not None
                            and int(runtime_id)
                            == self.pending_movement_control_actor_id
                        )
                        if continue_with_movement:
                            self.pending_movement_control_actor_id = None
                        self._refresh_actor_tables()
                        self._refresh_dashboard()
                        dashboard = self.main_view.main_view.dashboard_frame
                        dashboard.set_adoption_pending(False)
                        dashboard.show_adoption_result(data)
                        warnings = data.get("warnings") or []
                        details = data.get("message", "")
                        if warnings:
                            details += "\n\nWarnings:\n" + "\n".join(
                                f"- {warning}" for warning in warnings
                            )
                        if data.get("status") == "success":
                            messagebox.showinfo("Take Ownership", details)
                        elif data.get("status") == "already_managed":
                            messagebox.showinfo("Take Ownership", details)
                        else:
                            messagebox.showwarning("Take Ownership", details)
                        if continue_with_movement and data.get("success"):
                            self.open_movement_control(runtime_id)
                elif status == "scenario_operation_blocked":
                    if not self.closing:
                        messagebox.showinfo(
                            "Scenario operation",
                            data.get(
                                "message",
                                "Another scenario operation is pending.",
                            ),
                        )
                elif status == "scenario_save_completed":
                    self.scenario_saving = False
                    if self.connected and not self.disconnecting and not self.closing:
                        self._refresh_actor_tables()
                        self.main_view.simulation_control_frame.scenario_frame.show_result(
                            data
                        )
                        messagebox.showinfo(
                            "Scenario saved",
                            f"Saved {data.get('saved', 0)} actors to:\n"
                            f"{data.get('path', '')}\n\n"
                            f"Partial: {data.get('partial', 0)}\n"
                            "Skipped: "
                            f"{sum(data.get(field, 0) for field in ('skipped_runtime_only', 'skipped_unrestorable', 'skipped_external', 'skipped_unsupported'))}",
                        )
                elif status == "scenario_save_error":
                    self.scenario_saving = False
                    if not self.closing:
                        message = data.get("message", "Unknown save error")
                        self.main_view.simulation_control_frame.scenario_frame.show_error(
                            message
                        )
                        messagebox.showerror("Save Scenario", message)
                elif status == "world_clear_preflight_ready":
                    if self.connected and not self.disconnecting and not self.closing:
                        self._handle_clear_preflight(data)
                elif status == "world_clear_completed":
                    self._finish_clear_gui()
                    if not self.closing:
                        frame = (
                            self.main_view.simulation_control_frame.scenario_frame
                        )
                        frame.show_clear_report(data)
                        if data.get("status") != "cancelled":
                            self._refresh_actor_tables()
                            title = (
                                "Clear World completed"
                                if data.get("status") == "success"
                                else "Clear World result"
                            )
                            messagebox.showinfo(
                                title,
                                "Status: "
                                f"{data.get('status', 'failed')}\n"
                                f"Planned: {data.get('planned_count', 0)}\n"
                                f"Removed: {data.get('removed', 0)}\n"
                                "Already missing: "
                                f"{data.get('already_missing', 0)}\n"
                                f"Failed: {data.get('failed', 0)}",
                            )
                elif status == "world_clear_error":
                    self._finish_clear_gui()
                    if not self.closing:
                        message = data.get(
                            "message",
                            "Unknown Clear World error",
                        )
                        self.main_view.simulation_control_frame.scenario_frame.show_clear_error(
                            message
                        )
                        messagebox.showerror("Clear World", message)
                elif status == "scenario_load_preflight_completed":
                    if (
                        self.scenario_loading
                        and self.connected
                        and not self.disconnecting
                        and not self.closing
                    ):
                        self._handle_load_preflight(data)
                elif status == "scenario_load_preflight_error":
                    self._finish_load_gui()
                    if not self.closing:
                        message = data.get(
                            "message",
                            "Scenario Load preflight failed.",
                        )
                        frame = (
                            self.main_view.simulation_control_frame.scenario_frame
                        )
                        frame.show_load_error(message)
                        messagebox.showerror(
                            "Load Scenario preflight",
                            message,
                        )
                elif status == "scenario_load_plan_invalidated":
                    if self.scenario_loading:
                        self._finish_load_gui()
                        if not self.closing:
                            message = data.get(
                                "reason",
                                "The Scenario Load plan is no longer valid.",
                            )
                            frame = (
                                self.main_view.simulation_control_frame.scenario_frame
                            )
                            frame.show_load_error(message)
                elif status == "scenario_load_destructive_started":
                    if self.connected and not self.disconnecting and not self.closing:
                        self.movement_control.request_close("Scenario Load")
                        if self.spectator_preview.request_stop():
                            self.update_spectator_state("Stopping")
                        self.main_view.main_view.dashboard_frame.show_context(
                            "Loading Scenario"
                        )
                elif status == "scenario_load_completed":
                    self._finish_load_gui()
                    if not self.closing:
                        frame = (
                            self.main_view.simulation_control_frame.scenario_frame
                        )
                        frame.show_load_report(data)
                        if (
                            self.connected
                            and not self.disconnecting
                            and data.get("status") != "cancelled"
                        ):
                            self._refresh_after_scenario_load(data)
                        message = self._load_report_message(data)
                        status_value = data.get("status", "failed")
                        if status_value == "success":
                            messagebox.showinfo("Load Scenario completed", message)
                        elif status_value == "partial_success":
                            messagebox.showwarning(
                                "Load Scenario partially completed",
                                message,
                            )
                        elif status_value == "cancelled":
                            messagebox.showinfo("Load Scenario cancelled", message)
                        else:
                            messagebox.showerror("Load Scenario failed", message)
                elif status == "scenario_load_error":
                    self._finish_load_gui()
                    if not self.closing:
                        message = data.get(
                            "message",
                            "Scenario Load failed.",
                        )
                        self.main_view.simulation_control_frame.scenario_frame.show_load_error(
                            message
                        )
                        messagebox.showerror(
                            "Load Scenario",
                            message,
                        )
        except queue.Empty:
            pass

    def _refresh_actor_tables(self):
        table_paths = (
            (self.main_view.vehicle_frame, "vehicle_table"),
            (self.main_view.actor_frame, "actor_table"),
            (self.main_view.static_object_frame, "actor_table"),
        )
        for frame, table_name in table_paths:
            if frame is not None:
                getattr(frame, table_name).refresh_table()
        if self.main_view.sensor_frame is not None:
            self.main_view.sensor_frame.refresh_table()

    def _refresh_after_scenario_load(self, report):
        self._refresh_actor_tables()
        world_frame = self.main_view.world_weather_frame
        if world_frame is not None:
            weather = self.database.actual_weather_data
            for field in (
                "cloudiness", "precipitation", "precipitation_deposits",
                "wind_intensity", "sun_azimuth_angle", "sun_altitude_angle",
                "fog_density", "fog_distance", "fog_falloff", "wetness",
                "scattering_intensity", "mie_scattering_scale",
                "rayleigh_scattering_scale", "dust_storm",
            ):
                if field in weather:
                    getattr(world_frame, f"{field}_var").set(weather[field])
            target_map = str(report.get("target_map") or "")
            if target_map:
                world_frame.selected_map.set(
                    target_map.replace("\\", "/").rsplit("/", 1)[-1]
                )
                try:
                    world_frame.update_image(None)
                except (FileNotFoundError, OSError):
                    pass
        for actor_frame in (
            self.main_view.vehicle_frame,
            self.main_view.actor_frame,
            self.main_view.static_object_frame,
        ):
            if actor_frame is not None:
                actor_frame.update_spawn_point()
        self._refresh_dashboard()

    def _refresh_dashboard(self):
        if self.main_view is None:
            return
        with self.database.lock:
            dashboard_data = (
                copy.deepcopy(self.database.simulation_snapshot),
                copy.deepcopy(self.database.actor_statistics),
                copy.deepcopy(self.database.sensor_statistics),
                copy.deepcopy(self.database.application_status),
            )
            control_state = copy.deepcopy(
                self.database.simulation_control_state
            )
        self.main_view.main_view.dashboard_frame.refresh(*dashboard_data)
        if self.connected and not self.disconnecting and not self.closing:
            self.main_view.simulation_control_frame.update_control_observation(
                control_state
            )

    def open_spectator_preview(self):
        if (
            not self.connected or
            self.disconnecting or
            self.closing or
            self.world_changing or
            self.carla is None
        ):
            return

        try:
            preview_config = (
                self.main_view.main_view.spectator_frame.get_preview_config()
            )
            started = self.spectator_preview.start(
                self.carla.host,
                self.carla.port,
                preview_config
            )
        except ValueError as error:
            messagebox.showerror(
                "Invalid Spectator View Settings",
                str(error)
            )
            return
        except Exception as error:
            self.update_spectator_state("Error")
            messagebox.showerror(
                "Spectator View Error",
                f"Could not start Spectator View:\n{error}"
            )
            return

        if started:
            self.update_spectator_state("Starting")

    def open_movement_control(self, actor_id):
        if (
            not self.connected
            or self.disconnecting
            or self.closing
            or self.world_changing
            or self.carla is None
        ):
            return

        with self.database.lock:
            actor_id = int(actor_id)
            actor_info = self.database.actors_db.actors_db.get(actor_id)
            runtime_actor = self.database.actors_db.actors.get(actor_id)
            actor_is_alive = bool(getattr(
                actor_info,
                "is_alive",
                getattr(runtime_actor, "is_alive", False),
            ))
            actor_type = str(getattr(
                actor_info,
                "type_id",
                getattr(runtime_actor, "type_id", ""),
            ))
        if actor_info is None or not actor_is_alive:
            messagebox.showerror(
                "Movement Control",
                "The selected actor is no longer available.",
            )
            return
        if actor_info is not None and actor_info.ownership == ActorOwnership.EXTERNAL:
            confirmed = messagebox.askyesno(
                "Movement Control",
                "The selected actor is External and must be adopted before "
                "Movement Control can be used.\n\n"
                f"Runtime ID: {actor_id}\n"
                f"Type: {actor_type}\n\n"
                "Take ownership now?",
                parent=self,
            )
            if confirmed:
                self._request_actor_ownership(
                    actor_id,
                    movement_control=True,
                )
            return
        if actor_info is not None and actor_info.ownership != ActorOwnership.MANAGED:
            messagebox.showinfo(
                "Movement Control",
                "This actor is not GUI-managed and cannot use Movement Control.",
                parent=self,
            )
            return
        if not actor_type.startswith(("vehicle.", "walker.")):
            messagebox.showerror(
                "Movement Control",
                "Movement Control supports vehicles and pedestrians only.",
            )
            return
        self.movement_control.open(actor_id, actor_type)

    def close_movement_control_for_actor(self, actor_id, reason):
        self.movement_control.close_for_actor(actor_id, reason)

    def close_spectator_preview(self):
        if self.spectator_preview.request_stop():
            self.update_spectator_state("Stopping")

    def check_spectator_preview(self):
        self.spectator_check_job = None

        for status, data in self.spectator_preview.poll_events():
            if status == "starting":
                self.spectator_camera_id = None
                self.update_spectator_state("Starting")
            elif status == "running":
                if isinstance(data, dict):
                    self.spectator_camera_id = data.get("sensor_id")
                self.update_spectator_state("Running")
            elif status == "error":
                self.spectator_camera_id = None
                self.update_spectator_state("Error")
                if (
                    not self.closing and
                    not self.disconnecting and
                    not self.world_changing
                ):
                    messagebox.showerror(
                        "Spectator View Error",
                        f"Spectator View stopped because of an error:\n{data}"
                    )
            elif status == "closed":
                self.spectator_camera_id = None
                reason = data.get("reason") if isinstance(data, dict) else data
                exit_code = data.get("exit_code") if isinstance(data, dict) else None
                if reason == "process_exited" and exit_code not in (None, 0):
                    self.update_spectator_state("Error")
                    if (
                        not self.closing and
                        not self.disconnecting and
                        not self.world_changing
                    ):
                        messagebox.showerror(
                            "Spectator View Error",
                            f"Spectator View process exited with code {exit_code}."
                        )
                elif reason != "error" or self.spectator_state != "Error":
                    self.update_spectator_state("Closed")

        if self.spectator_preview.stop_timed_out(10.0):
            stopped = self.spectator_preview.force_stop()
            if stopped:
                if self.closing or self.disconnecting or self.world_changing:
                    self.update_spectator_state("Closed")
                else:
                    self.update_spectator_state("Error")
                    messagebox.showerror(
                        "Spectator View Error",
                        "Spectator View did not close in time and was terminated."
                    )

        if self.winfo_exists():
            self.spectator_check_job = self.after(
                100,
                self.check_spectator_preview
            )

    def update_spectator_state(self, state):
        self.spectator_state = state
        if self.main_view is not None:
            self.main_view.main_view.spectator_frame.update_state(
                state,
                self.connected and
                not self.disconnecting and
                not self.world_changing and
                not self.closing
            )

    def begin_world_change(self):
        if (
            not self.connected
            or self.disconnecting
            or self.closing
            or self.scenario_saving
            or self.scenario_clearing
            or self.scenario_loading
        ):
            return False
        confirmed = messagebox.askyesno(
            "Confirm Map Change",
            "Loading a new map will reset the current simulation world and "
            "remove its existing actors. This may also affect other users "
            "connected to the CARLA server. Do you want to continue?",
        )
        if not confirmed:
            return False
        self.world_changing = True
        self.main_view.main_view.connection_frame.set_sync_pending()
        self.main_view.simulation_control_frame.scenario_frame.set_connected(
            False
        )
        self.movement_control.request_close("World is changing")
        self.carla.stop_simulation_control()
        self.main_view.simulation_control_frame.set_external_busy(True)
        self.main_view.main_view.dashboard_frame.show_context("Loading World")
        if self.spectator_preview.request_stop():
            self.update_spectator_state("Stopping")
        else:
            self.update_spectator_state(self.spectator_state)
        return True

    def finish_world_change(self):
        self.world_changing = False
        self.main_view.main_view.connection_frame.update_connection_state(True)
        self.main_view.simulation_control_frame.scenario_frame.set_connected(
            True
        )
        self.main_view.simulation_control_frame.set_external_busy(False)
        self.main_view.main_view.dashboard_frame.clear_context()
        if self.connected and not self.spectator_preview.is_active():
            self.update_spectator_state("Closed")
        else:
            self.update_spectator_state(self.spectator_state)

    def finish_application_close(self):
        self.movement_control.poll()
        if self.movement_control.is_active():
            self.after(100, self.finish_application_close)
            return

        self.spectator_preview.poll_events()
        if self.spectator_preview.is_active():
            if self.spectator_preview.stop_timed_out(10.0):
                if not self.spectator_preview.force_stop():
                    self.after(100, self.finish_application_close)
                    return
            else:
                self.after(100, self.finish_application_close)
                return

        if self.spectator_check_job is not None:
            self.after_cancel(self.spectator_check_job)
            self.spectator_check_job = None
        if self.simulation_check_job is not None:
            self.after_cancel(self.simulation_check_job)
            self.simulation_check_job = None
        self.destroy()

