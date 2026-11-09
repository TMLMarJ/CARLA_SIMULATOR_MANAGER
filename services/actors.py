from services.blueprints import BlueprintService


class ActorService:
    def __init__(self, database, blueprint_service=None):
        self.database = database
        self.blueprints = blueprint_service or BlueprintService()

    def spawn_checked(self, world, blueprint_library, blueprint_id,
                      transform, try_spawn=True, attributes=None):
        requested_id = str(blueprint_id)
        blueprint = self.blueprints.find_checked(
            blueprint_library,
            requested_id,
        )
        self.blueprints.apply_attributes(blueprint, attributes)

        spawn = world.try_spawn_actor if try_spawn else world.spawn_actor
        actor = spawn(blueprint, transform)
        if actor is None:
            raise RuntimeError(
                f"CARLA could not spawn blueprint '{requested_id}'."
            )

        actual_id = str(getattr(actor, "type_id", ""))
        if actual_id != requested_id:
            try:
                if getattr(actor, "is_alive", False):
                    actor.destroy()
            finally:
                raise RuntimeError(
                    "Spawned actor blueprint mismatch: requested "
                    f"'{requested_id}', created '{actual_id}'."
                )
        return actor

    @staticmethod
    def physics_value(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value

    @classmethod
    def physics_snapshot(cls, physics):
        scalar_fields = (
            "max_rpm", "moi", "damping_rate_full_throttle",
            "damping_rate_zero_throttle_clutch_engaged",
            "damping_rate_zero_throttle_clutch_disengaged",
            "use_gear_autobox", "gear_switch_time", "clutch_strength",
            "final_ratio", "mass", "drag_coefficient",
            "use_sweep_wheel_collision",
        )
        snapshot = {
            name: cls.physics_value(getattr(physics, name, None))
            for name in scalar_fields
        }
        center = getattr(physics, "center_of_mass", None)
        snapshot["center_of_mass"] = tuple(
            cls.physics_value(getattr(center, axis, None))
            for axis in ("x", "y", "z")
        )
        for name in ("torque_curve", "steering_curve"):
            snapshot[name] = [
                (cls.physics_value(point.x), cls.physics_value(point.y))
                for point in getattr(physics, name, [])
            ]
        snapshot["forward_gears"] = [
            tuple(cls.physics_value(getattr(gear, name, None)) for name in (
                "ratio", "down_ratio", "up_ratio"
            ))
            for gear in getattr(physics, "forward_gears", [])
        ]
        snapshot["wheels"] = [
            tuple(cls.physics_value(getattr(wheel, name, None)) for name in (
                "tire_friction", "damping_rate", "max_steer_angle",
                "radius", "max_brake_torque", "max_handbrake_torque",
            ))
            for wheel in getattr(physics, "wheels", [])
        ]
        return snapshot

    @classmethod
    def physics_mismatches(cls, requested, actual, tolerance=1e-3):
        requested_snapshot = cls.physics_snapshot(requested)
        actual_snapshot = cls.physics_snapshot(actual)

        def equal(left, right):
            if isinstance(left, (int, float)) and isinstance(
                right,
                (int, float),
            ):
                return abs(float(left) - float(right)) <= tolerance
            if isinstance(left, (list, tuple)) and isinstance(
                right,
                (list, tuple),
            ):
                return len(left) == len(right) and all(
                    equal(a, b) for a, b in zip(left, right)
                )
            return left == right

        return [
            name for name, value in requested_snapshot.items()
            if not equal(value, actual_snapshot.get(name))
        ]

    @classmethod
    def apply_vehicle_physics_checked(cls, actor, physics):
        actor.apply_physics_control(physics)
        actual = actor.get_physics_control()
        mismatches = cls.physics_mismatches(physics, actual)
        if mismatches:
            raise RuntimeError(
                "CARLA returned different physics values for: "
                + ", ".join(mismatches)
            )
        return actual

    def spawn(self, context, data, default_attributes, default_physics,
              result_callback):
        requested_blueprint_id = str(data["blueprint_id"])
        transform = data["transform"]
        request_id = data.get("request_id")
        blueprint_attributes = data.get("attributes", default_attributes)
        requested_physics = data.get("physics", default_physics)
        self.database.blueprint_new_data = None
        actor = None
        try:
            actor = self.spawn_checked(
                context.world,
                context.blueprint_library,
                requested_blueprint_id,
                transform,
                attributes=blueprint_attributes,
            )
            actual_physics = None
            if (
                requested_physics is not None
                and actor.type_id.startswith("vehicle.")
            ):
                actual_physics = self.apply_vehicle_physics_checked(
                    actor,
                    requested_physics,
                )
            with self.database.lock:
                actor_info = self.database.add_actor(actor, data["name"])
                if actual_physics is not None:
                    actor_info.physics_control = actual_physics
                self.database.spawned_actor_id = actor.id
            result_callback(
                "spawn",
                requested_blueprint_id,
                actor=actor,
                request_id=request_id,
            )
        except Exception as error:
            if actor is not None:
                with self.database.lock:
                    actor_is_registered = (
                        actor.id in self.database.actors_db.actors
                    )
                if (
                    not actor_is_registered
                    and getattr(actor, "is_alive", False)
                ):
                    actor.destroy()
            result_callback(
                "spawn",
                requested_blueprint_id,
                error=error,
                request_id=request_id,
            )

    def set_transform(self, data):
        transform, actor_id = data
        with self.database.lock:
            if actor_id in self.database.actors_db.actors:
                actor = self.database.actors_db.actors[actor_id]
                actor.set_transform(transform)
                self.database.actors_db.actors_db[actor_id].transform = transform

    def remove(self, actor_id, remove_sensors):
        with self.database.lock:
            if actor_id not in self.database.actors_db.actors:
                return
            remove_sensors(actor_id)
            actor = self.database.actors_db.actors[actor_id]
            try:
                actor.destroy()
                print(f"Actor {actor_id} destroyed successfully.")
            except Exception as error:
                print(f"Error destroying actor {actor_id}: {error}")
            del self.database.actors_db.actors[actor_id]
            self.database.actors_db.actors_db.pop(actor_id, None)
            self.database.vehicle_control_states.pop(actor_id, None)
            self.database.walker_ai_states.pop(actor_id, None)

    def cleanup_preview(self):
        if self.database.ghost_camera is not None:
            try:
                self.database.ghost_camera.stop()
                self.database.ghost_camera.destroy()
            except Exception as error:
                print(f"Error destroying ghost camera: {error}")
            finally:
                self.database.ghost_camera = None

        if self.database.ghost_data is not None:
            try:
                self.database.ghost_data.destroy()
            except Exception as error:
                print(f"Error destroying ghost: {error}")
            finally:
                self.database.ghost_data = None

        self.database.ghost_physic = None
        self.database.ghost_spawned = False
