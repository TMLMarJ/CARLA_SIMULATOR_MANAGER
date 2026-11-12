ATTACHMENT_TYPE_NAMES = (
    "Rigid",
    "SpringArm",
    "SpringArmGhost",
)


def resolve_attachment_type(carla_module, attachment_name):
    """Resolve a supported attachment name without silently substituting it."""
    name = str(attachment_name or "Rigid")
    if name not in ATTACHMENT_TYPE_NAMES:
        raise ValueError(f"Unsupported sensor attachment type: {name}.")

    attachment_types = getattr(carla_module, "AttachmentType", None)
    if attachment_types is None or not hasattr(attachment_types, name):
        raise RuntimeError(
            f"Sensor attachment type '{name}' is unavailable in this "
            "CARLA Python API."
        )
    return getattr(attachment_types, name)
