import math


class SimulationManager:
    DISCONNECTED = "Disconnected"
    ASYNC_RUNNING = "Async Running"
    SYNC_EXTERNAL = "Sync External"
    SYNC_GUI_PAUSED = "Sync GUI Paused"
    SYNC_GUI_RUNNING = "Sync GUI Running"
    SYNC_GUI_STEPPING = "Sync GUI Stepping"
    ERROR = "Error"

    GUI_CONTROLLER = "gui"
    EXTERNAL_CONTROLLER = "external"

    SETTINGS_FIELDS = (
        "synchronous_mode",
        "fixed_delta_seconds",
        "no_rendering_mode",
        "substepping",
        "max_substep_delta_time",
        "max_substeps",
    )

    def __init__(self):
        self.state = self.DISCONNECTED
        self.tick_controller = self.EXTERNAL_CONTROLLER
        self.synchronous_mode = False
        self.remaining_steps = 0
        self.last_frame = None
        self.error_message = None

    def connect(self, settings):
        self.tick_controller = self.EXTERNAL_CONTROLLER
        self.last_frame = None
        self.error_message = None
        self.settings_changed(settings, preserve_activity=False)

    def disconnect(self):
        self.synchronous_mode = False
        self.remaining_steps = 0
        self.error_message = None
        self.state = self.DISCONNECTED

    def settings_changed(self, settings, preserve_activity=True):
        synchronous_mode = bool(settings["synchronous_mode"])
        was_active = self.state in (
            self.SYNC_GUI_RUNNING,
            self.SYNC_GUI_STEPPING,
        )
        self.synchronous_mode = synchronous_mode
        self.error_message = None

        if not synchronous_mode:
            self.remaining_steps = 0
            self.state = self.ASYNC_RUNNING
        elif self.tick_controller == self.EXTERNAL_CONTROLLER:
            self.remaining_steps = 0
            self.state = self.SYNC_EXTERNAL
        elif preserve_activity and was_active:
            # Applying an unrelated WorldSettings field must not silently pause
            # an already active local controller.
            pass
        else:
            self.remaining_steps = 0
            self.state = self.SYNC_GUI_PAUSED

    def set_tick_controller(self, controller):
        if controller not in (self.GUI_CONTROLLER, self.EXTERNAL_CONTROLLER):
            raise ValueError("Tick controller must be GUI or External.")
        if self.state == self.DISCONNECTED:
            raise RuntimeError("Simulation is disconnected.")

        self.tick_controller = controller
        self.remaining_steps = 0
        self.error_message = None
        if not self.synchronous_mode:
            self.state = self.ASYNC_RUNNING
        elif controller == self.GUI_CONTROLLER:
            self.state = self.SYNC_GUI_PAUSED
        else:
            self.state = self.SYNC_EXTERNAL

    def start_running(self):
        self._require_gui_paused()
        self.remaining_steps = 0
        self.state = self.SYNC_GUI_RUNNING

    def pause(self):
        if self.state not in (
            self.SYNC_GUI_RUNNING,
            self.SYNC_GUI_STEPPING,
        ):
            raise RuntimeError("The GUI tick controller is not running.")
        self.remaining_steps = 0
        self.state = self.SYNC_GUI_PAUSED

    def step(self, count=1):
        self._require_gui_paused()
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("Step count must be a positive integer.")
        self.remaining_steps = count
        self.state = self.SYNC_GUI_STEPPING

    def stop_local_ticks(self):
        self.remaining_steps = 0
        if self.synchronous_mode and self.tick_controller == self.GUI_CONTROLLER:
            self.state = self.SYNC_GUI_PAUSED

    def should_tick(self):
        return self.state in (
            self.SYNC_GUI_RUNNING,
            self.SYNC_GUI_STEPPING,
        )

    def record_tick(self, frame):
        self.last_frame = frame
        if self.state == self.SYNC_GUI_STEPPING:
            self.remaining_steps -= 1
            if self.remaining_steps <= 0:
                self.remaining_steps = 0
                self.state = self.SYNC_GUI_PAUSED

    def record_observed_frame(self, frame):
        self.last_frame = frame

    def fail(self, message):
        self.remaining_steps = 0
        self.error_message = str(message)
        self.state = self.ERROR

    def snapshot(self):
        return {
            "state": self.state,
            "tick_controller": self.tick_controller,
            "synchronous_mode": self.synchronous_mode,
            "remaining_steps": self.remaining_steps,
            "last_frame": self.last_frame,
            "error": self.error_message,
        }

    def _require_gui_paused(self):
        if self.state != self.SYNC_GUI_PAUSED:
            raise RuntimeError(
                "This operation requires Synchronous / GUI Control / Paused."
            )

    @classmethod
    def get_current_settings(cls, world):
        return cls.settings_to_dict(world.get_settings())

    @classmethod
    def settings_to_dict(cls, settings):
        fixed_delta_seconds = settings.fixed_delta_seconds
        return {
            "synchronous_mode": bool(settings.synchronous_mode),
            "fixed_delta_seconds": (
                None
                if fixed_delta_seconds is None
                else float(fixed_delta_seconds)
            ),
            "no_rendering_mode": bool(settings.no_rendering_mode),
            "substepping": bool(settings.substepping),
            "max_substep_delta_time": float(settings.max_substep_delta_time),
            "max_substeps": int(settings.max_substeps),
        }

    @classmethod
    def validate_settings(cls, settings):
        missing_fields = [
            field for field in cls.SETTINGS_FIELDS if field not in settings
        ]
        if missing_fields:
            raise ValueError(
                "Missing simulation settings: " + ", ".join(missing_fields)
            )

        unknown_fields = set(settings) - set(cls.SETTINGS_FIELDS)
        if unknown_fields:
            raise ValueError(
                "Unsupported simulation settings: "
                + ", ".join(sorted(unknown_fields))
            )

        for field in ("synchronous_mode", "no_rendering_mode", "substepping"):
            if not isinstance(settings[field], bool):
                raise ValueError(f"{field} must be a boolean value.")

        fixed_delta_seconds = settings["fixed_delta_seconds"]
        if fixed_delta_seconds is not None:
            cls._validate_non_negative_number(
                "fixed_delta_seconds",
                fixed_delta_seconds,
            )

        cls._validate_positive_number(
            "max_substep_delta_time",
            settings["max_substep_delta_time"],
        )

        max_substeps = settings["max_substeps"]
        if (
            isinstance(max_substeps, bool)
            or not isinstance(max_substeps, int)
            or max_substeps <= 0
        ):
            raise ValueError("max_substeps must be a positive integer.")

        if (
            settings["substepping"]
            and fixed_delta_seconds is not None
            and fixed_delta_seconds > 0
        ):
            maximum_delta = (
                settings["max_substep_delta_time"] * max_substeps
            )
            if fixed_delta_seconds > maximum_delta + 1e-12:
                raise ValueError(
                    "fixed_delta_seconds must be less than or equal to "
                    "max_substep_delta_time multiplied by max_substeps "
                    "when substepping is enabled."
                )

    @staticmethod
    def _validate_positive_number(name, value):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError(f"{name} must be a positive finite number.")

    @staticmethod
    def _validate_non_negative_number(name, value):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"{name} must be a non-negative finite number.")

    @classmethod
    def prepare_modified_settings(cls, world, changes):
        unknown_fields = set(changes) - set(cls.SETTINGS_FIELDS)
        if unknown_fields:
            raise ValueError(
                "Unsupported simulation settings: "
                + ", ".join(sorted(unknown_fields))
            )

        world_settings = world.get_settings()
        combined_settings = cls.settings_to_dict(world_settings)
        combined_settings.update(changes)
        cls.validate_settings(combined_settings)

        for field, value in changes.items():
            setattr(world_settings, field, value)

        return world_settings

    @classmethod
    def apply_settings(cls, world, changes):
        settings = cls.prepare_modified_settings(world, changes)
        world.apply_settings(settings)
        confirmed_settings = cls.get_current_settings(world)

        mismatched_fields = [
            field
            for field, value in changes.items()
            if not cls._values_equal(value, confirmed_settings[field])
        ]
        if mismatched_fields:
            raise RuntimeError(
                "CARLA did not confirm simulation settings: "
                + ", ".join(mismatched_fields)
            )

        return confirmed_settings

    @staticmethod
    def _values_equal(expected, actual):
        if expected in (None, 0, 0.0) and actual in (None, 0, 0.0):
            return True
        if expected is None or actual is None:
            return expected is actual
        if isinstance(expected, bool) or isinstance(actual, bool):
            return expected is actual
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            return math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-12)
        return expected == actual
