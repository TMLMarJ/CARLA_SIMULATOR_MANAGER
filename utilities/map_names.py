class AmbiguousMapError(ValueError):
    """Raised when a short map name identifies more than one server map."""


def normalize_map_name(map_name):
    return str(map_name or "").strip().replace("\\", "/").rstrip("/")


def canonical_map_name(map_name):
    """Normalize CARLA's optional /Game/ package-path representation."""
    normalized = normalize_map_name(map_name)
    if normalized.startswith("/Game/"):
        return normalized[len("/Game/"):]
    if normalized.startswith("Game/"):
        return normalized[len("Game/"):]
    return normalized


def map_basename(map_name):
    normalized = canonical_map_name(map_name)
    return normalized.rsplit("/", 1)[-1]


def visible_map_names(available_maps):
    """Return server map identifiers except the technical EmptyMap entry."""
    return [
        map_name
        for map_name in (available_maps or ())
        if map_basename(map_name) != "EmptyMap"
    ]


def is_full_map_name(map_name):
    return "/" in canonical_map_name(map_name)


def maps_equal(first, second):
    """Compare CARLA map identifiers without conflating full paths."""
    first_text = canonical_map_name(first)
    second_text = canonical_map_name(second)
    if not first_text or not second_text:
        return False
    if first_text == second_text:
        return True
    if is_full_map_name(first_text) and is_full_map_name(second_text):
        return False
    return map_basename(first_text) == map_basename(second_text)


def resolve_available_map(requested, available_maps):
    """Return the exact server identifier for an unambiguous request."""
    requested_text = normalize_map_name(requested)
    available = []
    for item in available_maps or ():
        normalized = normalize_map_name(item)
        if normalized and normalized not in available:
            available.append(normalized)

    exact = [item for item in available if item == requested_text]
    if exact:
        return exact[0]

    compatible = [item for item in available if maps_equal(requested_text, item)]
    identities = {}
    for item in compatible:
        identities.setdefault(canonical_map_name(item), item)
    if len(identities) == 1:
        return next(iter(identities.values()))
    if len(identities) > 1:
        raise AmbiguousMapError(
            f"Map name '{requested}' is ambiguous; matching server maps: "
            + ", ".join(compatible)
        )
    return None
