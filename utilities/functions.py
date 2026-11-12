from typing import Dict
import time

import carla
from models.scenarios import ActorOwnership


def actor_ownership_status(actor_info):
    """Return the user-facing status from the synchronized ownership model."""
    ownership = getattr(actor_info, "ownership", None)
    if ownership == ActorOwnership.MANAGED:
        return "Managed"
    if ownership == ActorOwnership.EXTERNAL:
        return "External"
    if ownership == ActorOwnership.INTERNAL:
        return "Internal"
    return "Unknown"


def actor_sensor_status(database, actor_id):
    """Return whether the actor has a sensor attached in synchronized data."""
    try:
        actor_id = int(actor_id)
    except (TypeError, ValueError):
        return "No"
    with database.lock:
        has_sensor = any(
            getattr(sensor_info, "parent_id", None) == actor_id
            for sensor_info in database.sensors_db.sensors_db.values()
        )
    return "Yes" if has_sensor else "No"


def get_pedestrian_blueprint_ids(blueprint_lib):
    """Return available pedestrian blueprint IDs in stable ID order."""
    if blueprint_lib is None:
        return []
    return sorted(
        (
            str(blueprint.id)
            for blueprint in blueprint_lib.filter("walker.pedestrian.*")
        ),
        key=str,
    )


def pedestrian_blueprint_options(blueprint_ids):
    """Build the GUI label-to-ID mapping without using list indexes."""
    return {
        str(blueprint_id): str(blueprint_id)
        for blueprint_id in sorted(blueprint_ids, key=str)
    }


def resolve_blueprint_selection(options, selected_value):
    """Resolve one GUI value to its exact full blueprint ID."""
    try:
        return options[str(selected_value)]
    except (KeyError, TypeError):
        raise ValueError(
            f"Selected blueprint is unavailable: {selected_value!r}."
        ) from None


def select_available_blueprint_id(blueprint_ids, selected_blueprint_id=None):
    """Preserve an ID across refreshes, otherwise select the first ID."""
    available = sorted((str(value) for value in blueprint_ids), key=str)
    selected = (
        str(selected_blueprint_id)
        if selected_blueprint_id is not None
        else None
    )
    if selected in available:
        return selected
    return available[0] if available else ""


def filter_carla_id_by_type(actor: str, type_base: str, blueprint_lib: carla.BlueprintLibrary):
    if actor == "walker":
        return pedestrian_blueprint_options(
            get_pedestrian_blueprint_ids(blueprint_lib)
        )

    filtered = blueprint_lib.filter(f'{actor}.*')
    selected = {}
    for bp in filtered:
        if actor == "static":
            bp_id = bp.id
            blueprint_parts = str(bp_id).split('.')
            if len(blueprint_parts) < 3 or blueprint_parts[1] != "prop":
                continue
            model_name = " ".join(blueprint_parts[2:])
            selected[model_name.replace('_', ' ').title()] = bp_id
        elif bp.has_attribute('base_type'):
            if bp.get_attribute('base_type').as_str() == type_base or bp.get_attribute('base_type').as_str() == type_base.capitalize():
                bp_id = bp.id
                base, brand, model = str(bp_id).split('.')
                key = brand.capitalize() + " " + model.upper()
                selected[key] = bp_id
    return selected


def parse_weather_to_dict(weather: carla.WeatherParameters):
    weather_dict = {}
    weather_dict["cloudiness"] = weather.cloudiness
    weather_dict["precipitation"] = weather.precipitation
    weather_dict["wind_intensity"] = weather.wind_intensity
    weather_dict["precipitation_deposits"] = weather.precipitation_deposits
    weather_dict["sun_azimuth_angle"] = weather.sun_azimuth_angle
    weather_dict["sun_altitude_angle"] = weather.sun_altitude_angle
    weather_dict["fog_density"] = weather.fog_density
    weather_dict["fog_distance"] = weather.fog_distance
    weather_dict["fog_falloff"] = weather.fog_falloff
    weather_dict["wetness"] = weather.wetness
    weather_dict["scattering_intensity"] = weather.scattering_intensity
    weather_dict["mie_scattering_scale"] = weather.mie_scattering_scale
    weather_dict["rayleigh_scattering_scale"] = weather.rayleigh_scattering_scale
    weather_dict["dust_storm"] = weather.dust_storm
    return weather_dict

def parse_weather_to_carla(weather: Dict[str, object]) -> carla.WeatherParameters:
    carla_weather = carla.WeatherParameters()
    for key, value in weather.items():
        if hasattr(carla_weather, key):
            setattr(carla_weather, key, value)
    return carla_weather


def weather_parameter_presets(weather_parameters_type):
    """Return only real preset instances exposed by WeatherParameters."""
    return {
        name: value
        for name, value in vars(weather_parameters_type).items()
        if isinstance(value, weather_parameters_type)
    }

def wait_and_try(condition, timeout=10, interval=1):
    start = time.time()
    while time.time() - start < timeout:
        if condition():
            return True
        time.sleep(interval)
    return False

def return_data_from_transform(transform: carla.Transform):
    return transform.location.x, transform.location.y, transform.location.z, transform.rotation.yaw, transform.rotation.pitch, transform.rotation.roll


def hex_to_rgb(hex_color: str):
    hex_color = hex_color.lstrip('#')

    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    return f"{r},{g},{b}"

def rgb_to_hex(rgb: str):
    r, g, b = map(int, rgb.strip("rgb()").split(","))
    return f"#{r:02x}{g:02x}{b:02x}"
