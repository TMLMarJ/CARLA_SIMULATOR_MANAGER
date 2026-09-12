from typing import Dict
import time

import carla


def filter_carla_id_by_type(actor: str, type_base: str, blueprint_lib: carla.BlueprintLibrary):
    filtered = blueprint_lib.filter(f'{actor}.*')
    selected = {}
    for bp in filtered:
        if bp.has_attribute('base_type'):
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

def wait_and_try(condition, timeout=10, interval=1):
    start = time.time()
    while time.time() - start < timeout:
        if condition():
            return True
        time.sleep(interval)
    return False

def return_data_from_transform(transform: carla.Transform):
    return transform.location.x, transform.location.y, transform.location.z, transform.rotation.yaw, transform.rotation.pitch, transform.rotation.roll


