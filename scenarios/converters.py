"""Conversions from CARLA runtime objects to schema-v1 primitive values."""

import math
import re


VEHICLE_PHYSICS_FIELDS = (
    "max_rpm",
    "moi",
    "damping_rate_full_throttle",
    "damping_rate_zero_throttle_clutch_engaged",
    "damping_rate_zero_throttle_clutch_disengaged",
    "use_gear_autobox",
    "gear_switch_time",
    "clutch_strength",
    "final_ratio",
    "mass",
    "drag_coefficient",
    "use_sweep_wheel_collision",
)

WHEEL_PHYSICS_FIELDS = (
    "tire_friction",
    "damping_rate",
    "max_steer_angle",
    "radius",
    "max_brake_torque",
    "max_handbrake_torque",
    "long_stiff_value",
    "lat_stiff_max_load",
    "lat_stiff_value",
)

WEATHER_FIELDS = (
    "cloudiness",
    "precipitation",
    "precipitation_deposits",
    "wind_intensity",
    "sun_azimuth_angle",
    "sun_altitude_angle",
    "fog_density",
    "fog_distance",
    "fog_falloff",
    "wetness",
    "scattering_intensity",
    "mie_scattering_scale",
    "rayleigh_scattering_scale",
    "dust_storm",
)


def vector2_to_dict(value):
    return {"x": float(value.x), "y": float(value.y)}


def vector3_to_dict(value):
    return {
        "x": float(value.x),
        "y": float(value.y),
        "z": float(value.z),
    }


def transform_to_dict(transform, space="world", source="server"):
    if transform is None:
        return None
    return {
        "space": str(space),
        "location": vector3_to_dict(transform.location),
        "rotation": {
            "pitch": float(transform.rotation.pitch),
            "yaw": float(transform.rotation.yaw),
            "roll": float(transform.rotation.roll),
        },
        "source": str(source),
    }


def weather_to_dict(weather):
    return {
        field: float(getattr(weather, field))
        for field in WEATHER_FIELDS
    }


def blueprint_attributes_to_dict(actor):
    attributes = dict(actor.attributes or {})
    return {
        str(name): blueprint_runtime_value_to_string(attributes[name])
        for name in sorted(attributes, key=str)
    }


def blueprint_attribute_kind(attribute):
    return str(getattr(attribute, "type", "String")).split(".")[-1].lower()


def _rgb_components(value):
    if all(hasattr(value, name) for name in ("r", "g", "b")):
        values = [value.r, value.g, value.b]
    elif isinstance(value, dict):
        values = [value[name] for name in ("r", "g", "b")]
    elif isinstance(value, (list, tuple)) and len(value) == 3:
        values = list(value)
    elif isinstance(value, str):
        text = value.strip()
        color_match = re.fullmatch(
            r"Color\s*\(\s*(?:r\s*=\s*)?([^,]+),"
            r"\s*(?:g\s*=\s*)?([^,]+),"
            r"\s*(?:b\s*=\s*)?([^)]+)\s*\)",
            text,
            flags=re.IGNORECASE,
        )
        values = (
            list(color_match.groups())
            if color_match
            else [part.strip() for part in text.split(",")]
        )
    else:
        raise ValueError("RGBColor requires r, g and b components.")
    if len(values) != 3:
        raise ValueError("RGBColor requires exactly three components.")
    try:
        components = [int(value) for value in values]
    except (TypeError, ValueError) as error:
        raise ValueError("RGBColor components must be integers.") from error
    if not all(0 <= value <= 255 for value in components):
        raise ValueError("RGBColor components must be in range 0..255.")
    return components


def blueprint_value_to_string(attribute, value):
    """Convert a saved value to the textual format expected by CARLA."""
    kind = blueprint_attribute_kind(attribute)
    if kind in ("bool", "boolean"):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower()
        raise ValueError("Bool value must be true or false.")
    if kind in ("int", "integer"):
        if isinstance(value, bool):
            raise ValueError("Bool is not a valid Int value.")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("Int value is not an integer.")
        try:
            return str(int(value))
        except (TypeError, ValueError) as error:
            raise ValueError("Int value is not an integer.") from error
    if kind in ("float", "double"):
        if isinstance(value, bool):
            raise ValueError("Bool is not a valid Float value.")
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError("Float value is not numeric.") from error
        if not math.isfinite(number):
            raise ValueError("Float value must be finite.")
        return format(number, ".15g")
    if kind in ("rgbcolor", "rgb_color", "color"):
        return ",".join(str(component) for component in _rgb_components(value))
    if kind in ("string", "str"):
        if not isinstance(value, str):
            raise ValueError("String attribute requires a string value.")
        return value
    if not isinstance(value, str):
        raise ValueError(
            f"Unsupported CARLA attribute type '{kind}' requires text input."
        )
    return value


def blueprint_attribute_current_value(attribute):
    """Read an ActorAttribute without asking incompatible types for as_str()."""
    kind = blueprint_attribute_kind(attribute)
    method_names = {
        "bool": "as_bool",
        "boolean": "as_bool",
        "int": "as_int",
        "integer": "as_int",
        "float": "as_float",
        "double": "as_float",
        "rgbcolor": "as_color",
        "rgb_color": "as_color",
        "color": "as_color",
        "string": "as_str",
        "str": "as_str",
    }
    method = getattr(attribute, method_names.get(kind, "as_str"), None)
    if callable(method):
        value = method()
    elif hasattr(attribute, "value"):
        value = attribute.value
    else:
        raise ValueError(
            f"CARLA attribute type '{kind}' cannot be read safely."
        )
    return blueprint_value_to_string(attribute, value)


def blueprint_runtime_value_to_string(value):
    """Serialize actor.attributes values without leaking Python Color reprs."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Blueprint Float value must be finite.")
        return format(value, ".15g")
    if all(hasattr(value, name) for name in ("r", "g", "b")):
        return ",".join(str(component) for component in _rgb_components(value))
    raise ValueError(
        f"Unsupported blueprint runtime value {value!r} "
        f"({type(value).__name__})."
    )


def gear_physics_to_dict(gear):
    return {
        "ratio": float(gear.ratio),
        "down_ratio": float(gear.down_ratio),
        "up_ratio": float(gear.up_ratio),
    }


def wheel_physics_to_dict(wheel):
    result: dict[str, object] = {
        field: float(getattr(wheel, field))
        for field in WHEEL_PHYSICS_FIELDS
    }
    result["position"] = vector3_to_dict(wheel.position)
    return result


def vehicle_physics_to_dict(physics, source="server"):
    if physics is None:
        return None
    result: dict[str, object] = {"source": str(source)}
    for field in VEHICLE_PHYSICS_FIELDS:
        value = getattr(physics, field)
        result[field] = bool(value) if isinstance(value, bool) else float(value)
    result["center_of_mass"] = vector3_to_dict(physics.center_of_mass)
    result["torque_curve"] = [
        vector2_to_dict(point) for point in physics.torque_curve
    ]
    result["steering_curve"] = [
        vector2_to_dict(point) for point in physics.steering_curve
    ]
    result["forward_gears"] = [
        gear_physics_to_dict(gear) for gear in physics.forward_gears
    ]
    result["wheels"] = [
        wheel_physics_to_dict(wheel) for wheel in physics.wheels
    ]
    return result


def attachment_type_to_string(value):
    if value is None:
        return None
    text = str(value)
    return text.rsplit(".", 1)[-1]


def transform_from_dict(data, carla_module):
    location = data["location"]
    rotation = data["rotation"]
    return carla_module.Transform(
        carla_module.Location(
            x=float(location["x"]),
            y=float(location["y"]),
            z=float(location["z"]),
        ),
        carla_module.Rotation(
            pitch=float(rotation["pitch"]),
            yaw=float(rotation["yaw"]),
            roll=float(rotation["roll"]),
        ),
    )


def apply_vehicle_physics_from_dict(actor, data, carla_module):
    """Merge schema-v1 physics into current control, excluding read-only data."""
    if not data:
        return []
    physics = actor.get_physics_control()
    warnings = []
    for field in VEHICLE_PHYSICS_FIELDS:
        if field not in data:
            continue
        try:
            setattr(physics, field, data[field])
        except (AttributeError, RuntimeError, TypeError, ValueError) as error:
            warnings.append(f"Physics field '{field}' was not applied: {error}")

    if "center_of_mass" in data:
        value = data["center_of_mass"]
        physics.center_of_mass = carla_module.Vector3D(
            x=float(value["x"]),
            y=float(value["y"]),
            z=float(value["z"]),
        )
    for field in ("torque_curve", "steering_curve"):
        if field in data:
            setattr(physics, field, [
                carla_module.Vector2D(x=float(point["x"]), y=float(point["y"]))
                for point in data[field]
            ])
    if "forward_gears" in data:
        gears = []
        for saved in data["forward_gears"]:
            gear = carla_module.GearPhysicsControl()
            gear.ratio = float(saved["ratio"])
            gear.down_ratio = float(saved["down_ratio"])
            gear.up_ratio = float(saved["up_ratio"])
            gears.append(gear)
        physics.forward_gears = gears

    if "wheels" in data:
        current_wheels = list(physics.wheels)
        for index, saved in enumerate(data["wheels"]):
            if index >= len(current_wheels):
                warnings.append(
                    f"Wheel {index} was not applied because the spawned "
                    "vehicle exposes fewer wheels."
                )
                continue
            wheel = current_wheels[index]
            for field in WHEEL_PHYSICS_FIELDS:
                if field not in saved:
                    continue
                try:
                    setattr(wheel, field, saved[field])
                except (AttributeError, RuntimeError, TypeError, ValueError) as error:
                    warnings.append(
                        f"Wheel {index} field '{field}' was not applied: {error}"
                    )
        physics.wheels = current_wheels
    actor.apply_physics_control(physics)
    return warnings
