import time

from simulation.manager import SimulationManager


class SimulationRuntime:
    def _handle_simulation_control_command(self, callback, *args):
        try:
            callback(*args)
            self._publish_simulation_control_state(force=True)
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self.database.simulation_event_queue.put((
                "simulation_control_command_error",
                str(error),
            ))
            self._publish_simulation_control_state(force=True)

    def _handle_tick_start_command(self, world, callback, *args):
        try:
            current_settings = self.simulation_manager.get_current_settings(
                world
            )
            with self.database.lock:
                previous_settings = self.database.actual_simulation_settings
                settings_changed = previous_settings != current_settings
                self.database.actual_simulation_settings = current_settings
                self.database.proposed_simulation_settings = (
                    current_settings.copy()
                )
            self.simulation_manager.settings_changed(
                current_settings,
                preserve_activity=True,
            )
            if settings_changed:
                self.database.simulation_event_queue.put((
                    "simulation_settings_loaded",
                    current_settings,
                ))
            if not current_settings["synchronous_mode"]:
                raise RuntimeError(
                    "CARLA is currently asynchronous. Apply Synchronous "
                    "mode before starting GUI ticks."
                )
            callback(*args)
            self._publish_simulation_control_state(force=True)
        except Exception as error:
            self.dashboard_monitor.record_error(error)
            self.database.simulation_event_queue.put((
                "simulation_control_command_error",
                str(error),
            ))
            self._publish_simulation_control_state(force=True)

    def _service_simulation(self, world):
        if self.simulation_manager.state == SimulationManager.ERROR:
            return None
        if self.simulation_manager.should_tick():
            previous_state = self.simulation_manager.state
            try:
                self.before_simulation_tick(world)
                frame = world.tick(self.TICK_TIMEOUT_SECONDS)
                self.simulation_manager.record_tick(frame)
                self.after_simulation_tick(world, frame)
                force_publish = self.simulation_manager.state != previous_state
                self._publish_simulation_control_state(force=force_publish)
            except Exception as error:
                self.simulation_manager.fail(error)
                self.dashboard_monitor.record_error(error)
                self._publish_simulation_control_state(force=True)
            return None

        now = time.monotonic()
        if now < self._next_snapshot_check:
            return None
        self._next_snapshot_check = now + 0.1
        try:
            snapshot = world.get_snapshot()
            frame = getattr(snapshot, "frame", None)
            if frame != self.simulation_manager.last_frame:
                self.simulation_manager.record_observed_frame(frame)
                self._publish_simulation_control_state()
            return snapshot
        except Exception as error:
            self.simulation_manager.fail(error)
            self.dashboard_monitor.record_error(error)
            self._publish_simulation_control_state(force=True)
            return None

    def _service_dashboard(self, world, world_snapshot=None, force=False):
        self._invalidate_load_plan_if_episode_changed(world)
        if self.movement_preview_camera is not None:
            try:
                parent = self.movement_preview_camera.parent
                if (
                    not self.movement_preview_camera.is_alive
                    or parent is None
                    or not parent.is_alive
                ):
                    self.cleanup_movement_preview("actor_unavailable")
            except Exception:
                self.cleanup_movement_preview("camera_unavailable")

        internal_actor_ids = self._collect_internal_actor_ids()
        streaming_sensor_ids = set(self.sensor_preview_ids)

        ghost_camera_id = self._actor_id(self.database.ghost_camera)
        if ghost_camera_id is not None:
            streaming_sensor_ids.add(ghost_camera_id)

        spectator_status = self._spectator_runtime_status()
        spectator_camera_id = spectator_status["camera_id"]
        if spectator_camera_id is not None:
            streaming_sensor_ids.add(spectator_camera_id)

        movement_camera_id = self._actor_id(self.movement_preview_camera)
        if movement_camera_id is not None:
            streaming_sensor_ids.add(movement_camera_id)

        viewer_status = spectator_status["state"]
        self.dashboard_monitor.update(
            world,
            self.database,
            self.simulation_manager,
            self.command_queue.qsize() + self.priority_command_queue.qsize(),
            viewer_status,
            internal_actor_ids,
            streaming_sensor_ids,
            self.sensor_status_errors,
            world_snapshot=world_snapshot,
            force=force,
        )

    def before_simulation_tick(self, world):
        """Extension point for future operations immediately before a GUI tick."""
        pass

    def after_simulation_tick(self, world, frame):
        """Extension point for future operations immediately after a GUI tick."""
        pass

    def _publish_simulation_control_state(self, force=False):
        control_state = self.simulation_manager.snapshot()
        with self.database.lock:
            self.database.simulation_control_state = control_state

        if not force:
            return
        self.database.simulation_event_queue.put((
            "simulation_control_state",
            control_state,
        ))

