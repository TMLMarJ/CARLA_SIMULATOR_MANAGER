def unique_logical_name(requested_name, existing_names, default_name):
    """Resolve a GUI logical name using the smallest available suffix."""
    base_name = str(requested_name or "").strip()
    if not base_name:
        base_name = str(default_name or "Object").strip() or "Object"

    existing = {str(name) for name in (existing_names or ())}
    if base_name not in existing:
        return base_name

    suffix = 1
    while f"{base_name}_{suffix}" in existing:
        suffix += 1
    return f"{base_name}_{suffix}"


def default_logical_name(blueprint_id):
    type_id = str(blueprint_id or "")
    if type_id.startswith("vehicle."):
        return "Vehicle"
    if type_id.startswith("walker.pedestrian."):
        return "Pedestrian"
    if type_id.startswith("static."):
        return "Static Object"
    if type_id.startswith("sensor."):
        return "Sensor"
    return "Actor"
