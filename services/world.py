from utilities.functions import (
    get_pedestrian_blueprint_ids,
    parse_weather_to_dict,
)
from utilities.map_names import maps_equal


class WorldService:
    @staticmethod
    def load_with_retry(client, map_name, shutdown_event, attempts=10):
        load_error = None
        validation_error = None
        observed_map = None
        observed_episode = None
        try:
            # Do not validate the proxy returned by load_world().  Older CARLA
            # servers can complete the episode switch while that proxy already
            # belongs to the outgoing episode.
            client.load_world(map_name)
        except RuntimeError as error:
            load_error = error

        for attempt in range(attempts):
            try:
                current_world = client.get_world()
                observed_map = current_world.get_map().name
                try:
                    observed_episode = int(current_world.id)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    observed_episode = None
                if maps_equal(observed_map, map_name):
                    return current_world
                validation_error = None
            except RuntimeError as error:
                validation_error = error
            if attempt + 1 < attempts and shutdown_event.wait(1.0):
                raise RuntimeError(
                    "World change cancelled during shutdown."
                ) from (validation_error or load_error)

        details = []
        if observed_map is not None:
            details.append(f"active map is '{observed_map}'")
        if observed_episode is not None:
            details.append(f"active episode is {observed_episode}")
        if load_error is not None:
            details.append(
                f"load_world raised {type(load_error).__name__}: {load_error}"
            )
        if validation_error is not None:
            details.append(
                "fresh-world validation raised "
                f"{type(validation_error).__name__}: {validation_error}"
            )
        suffix = f" ({'; '.join(details)})" if details else ""
        cause = validation_error or load_error
        raise RuntimeError(
            f"Failed to activate map '{map_name}'{suffix}"
        ) from cause

    @staticmethod
    def install(context, simulation_manager):
        new_world = context.world
        new_world_id = int(new_world.id)
        new_blueprint_library = context.blueprint_library
        database = context.database

        with database.lock:
            database.actors_db.clear()
            database.sensors_db.clear()
            database.vehicle_control_states.clear()
            database.walker_ai_states.clear()
            database.spawned_actor_id = None
            database.world = new_world
            database.world_id = new_world_id
            database.world_generation = (
                int(getattr(database, "world_generation", 0)) + 1
            )
            database.blueprint_lib = new_blueprint_library
            database.pedestrian_blueprint_ids = (
                get_pedestrian_blueprint_ids(new_blueprint_library)
            )
        database.sensor_event_queue.put(("sensors_cleared", None))

        new_world_library = context.client.get_available_maps()
        new_weather_data = parse_weather_to_dict(new_world.get_weather())
        new_spawn_points = new_world.get_map().get_spawn_points()
        new_simulation_settings = simulation_manager.get_current_settings(
            new_world
        )
        simulation_manager.settings_changed(
            new_simulation_settings,
            preserve_activity=False,
        )

        with database.lock:
            database.world_lib = new_world_library
            database.actual_weather_data = new_weather_data
            database.available_spawn_points = new_spawn_points
            database.actual_simulation_settings = new_simulation_settings
            database.proposed_simulation_settings = (
                new_simulation_settings.copy()
            )
            database.world_changed = True
        return new_simulation_settings
