from utilities.functions import (
    get_pedestrian_blueprint_ids,
    parse_weather_to_dict,
)


class WorldService:
    @staticmethod
    def load_with_retry(client, map_name, shutdown_event, attempts=10):
        try:
            new_world = client.load_world(map_name)
        except RuntimeError:
            print("load_world timeout, waiting for server...")
            new_world = None

            for attempt in range(attempts):
                try:
                    current_world = client.get_world()
                    current_name = (
                        current_world.get_map().name.split("/")[-1]
                    )
                    print(f"Attempt {attempt + 1}: {current_name}")
                    if current_name == map_name:
                        new_world = current_world
                        print("Map changed successfully")
                        break
                except RuntimeError:
                    print(f"Attempt {attempt + 1}: server not ready")
                if shutdown_event.wait(1.0):
                    raise RuntimeError(
                        "World change cancelled during shutdown."
                    )

        if (
            new_world is None
            or new_world.get_map().name.split("/")[-1] != map_name
        ):
            raise RuntimeError(f"Failed to load map: {map_name}")
        return new_world

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
