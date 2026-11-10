def apply_weather(client, weather):
    world = client.get_world()
    world.set_weather(weather)
    return world
