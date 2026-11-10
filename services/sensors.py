import carla


class SensorService:
    def __init__(self, database, dashboard_monitor, preview_ids,
                 status_errors):
        self.database = database
        self.dashboard_monitor = dashboard_monitor
        self.preview_ids = preview_ids
        self.status_errors = status_errors

    def _record_error(self, error):
        if self.dashboard_monitor is not None:
            self.dashboard_monitor.record_error(error)

    def attach(self, data):
        sensor = None
        try:
            parent_id = data["parent_id"]
            with self.database.lock:
                parent_actor = self.database.actors_db.actors.get(parent_id)
            if parent_actor is None or not parent_actor.is_alive:
                raise RuntimeError("Parent actor is no longer available.")

            sensor_blueprint = self.database.blueprint_lib.find(
                data["blueprint_id"]
            )
            for attr_name, attr_value in data["attributes"].items():
                if sensor_blueprint.has_attribute(attr_name):
                    attribute = sensor_blueprint.get_attribute(attr_name)
                    if attribute.is_modifiable:
                        sensor_blueprint.set_attribute(
                            attr_name,
                            str(attr_value),
                        )

            sensor = self.database.world.spawn_actor(
                sensor_blueprint,
                data["transform"],
                attach_to=parent_actor,
                attachment_type=carla.AttachmentType.Rigid,
            )
            if sensor is None:
                raise RuntimeError("CARLA did not create the sensor.")

            with self.database.lock:
                self.database.add_sensor(
                    sensor,
                    parent_id,
                    data["name"],
                    data["blueprint_id"],
                    data["transform"],
                    data["attributes"],
                )
            self.status_errors.pop(sensor.id, None)
            self.database.sensor_event_queue.put((
                "sensor_attached",
                sensor.id,
            ))
            print(f"Sensor {data['name']} spawned with ID {sensor.id}")
        except Exception as error:
            self._record_error(error)
            if sensor is not None:
                try:
                    sensor.destroy()
                except Exception:
                    pass
            self.database.sensor_event_queue.put((
                "sensor_error",
                f"Could not add sensor: {error}",
            ))

    def remove(self, sensor_id):
        sensor_id = int(sensor_id)
        try:
            self.stop_preview_actor(sensor_id)
            with self.database.lock:
                sensor = self.database.sensors_db.sensors.get(sensor_id)
                if sensor is None:
                    raise RuntimeError("Sensor is no longer available.")
                sensor.destroy()
                self.database.sensors_db.remove_sensor(sensor_id)
            self.status_errors.pop(sensor_id, None)
            self.database.sensor_event_queue.put((
                "sensor_removed",
                sensor_id,
            ))
            print(f"Sensor {sensor_id} destroyed successfully.")
        except Exception as error:
            self._record_error(error)
            self.database.sensor_event_queue.put((
                "sensor_error",
                f"Could not remove sensor: {error}",
            ))

    def change_transform(self, data):
        sensor_id, transform = data
        sensor = None
        previous_transform = None
        transform_sent = False
        try:
            with self.database.lock:
                sensor = self.database.sensors_db.sensors.get(sensor_id)
                sensor_info = self.database.sensors_db.sensors_db.get(sensor_id)
            if sensor is None or sensor_info is None or not sensor.is_alive:
                raise RuntimeError("Sensor is no longer available.")

            parent_id = sensor_info.parent_id
            previous_transform = sensor_info.relative_transform
            sensor.set_transform(transform)
            transform_sent = True
            self.database.world.wait_for_tick(2.0)

            if (
                not sensor.is_alive
                or sensor.parent is None
                or sensor.parent.id != parent_id
            ):
                raise RuntimeError("Sensor attachment could not be confirmed.")

            with self.database.lock:
                current_info = self.database.sensors_db.sensors_db.get(
                    sensor_id
                )
                if current_info is None:
                    raise RuntimeError("Sensor is no longer available.")
                current_info.relative_transform = transform
            self.database.sensor_event_queue.put((
                "sensor_transform_changed",
                sensor_id,
            ))
            print(f"Sensor {sensor_id} transform changed successfully.")
        except Exception as error:
            self._record_error(error)
            if (
                transform_sent
                and sensor is not None
                and previous_transform is not None
            ):
                try:
                    sensor.set_transform(previous_transform)
                    self.database.world.wait_for_tick(2.0)
                except Exception:
                    pass
            self.database.sensor_event_queue.put((
                "sensor_error",
                f"Could not change sensor transform: {error}",
            ))

    def start_preview(self, data):
        sensor_id = data["sensor_id"]
        preview_buffer = data["preview_buffer"]
        try:
            with self.database.lock:
                sensor = self.database.sensors_db.sensors.get(sensor_id)
            if sensor is None or not sensor.is_alive:
                raise RuntimeError("Sensor is no longer available.")
            if sensor_id in self.preview_ids or sensor.is_listening():
                raise RuntimeError(
                    "This sensor is already being used by another listener."
                )

            sensor.listen(data["image_callback"])
            self.preview_ids.add(sensor_id)
            self.status_errors.pop(sensor_id, None)
            preview_buffer.set_started()
        except Exception as error:
            error_message = f"Could not start sensor preview: {error}"
            self.status_errors[sensor_id] = error_message
            self._record_error(error_message)
            preview_buffer.set_error(error_message)

    def stop_preview_actor(self, sensor_id, strict=False):
        if sensor_id not in self.preview_ids:
            return True
        sensor = self.database.sensors_db.sensors.get(sensor_id)
        if sensor is not None:
            try:
                if sensor.is_alive and sensor.is_listening():
                    sensor.stop()
            except Exception as error:
                if strict:
                    raise RuntimeError(
                        f"Could not stop sensor preview {sensor_id}."
                    ) from error
                print(f"Error stopping sensor preview {sensor_id}: {error}")
                self.preview_ids.discard(sensor_id)
                return False
        self.preview_ids.discard(sensor_id)
        return True

    def cleanup_previews(self, strict=False):
        for sensor_id in list(self.preview_ids):
            self.stop_preview_actor(sensor_id, strict=strict)
        return True

    def remove_for_parent(self, parent_id):
        sensor_ids = [
            sensor_id
            for sensor_id, sensor_info
            in self.database.sensors_db.sensors_db.items()
            if sensor_info.parent_id == parent_id
        ]
        for sensor_id in sensor_ids:
            self.stop_preview_actor(sensor_id)
            sensor = self.database.sensors_db.sensors.get(sensor_id)
            if sensor is not None:
                try:
                    sensor.destroy()
                except Exception as error:
                    print(f"Error destroying sensor {sensor_id}: {error}")
            self.database.sensors_db.remove_sensor(sensor_id)
            self.status_errors.pop(sensor_id, None)
        if sensor_ids:
            self.database.sensor_event_queue.put((
                "sensors_cleared",
                sensor_ids,
            ))
