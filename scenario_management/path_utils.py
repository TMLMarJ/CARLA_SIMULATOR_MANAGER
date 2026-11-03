from pathlib import Path


class ScenarioPathError(ValueError):
    pass


class UnknownScenarioExtensionError(ScenarioPathError):
    def __init__(self, path, suggested_path, file_format):
        self.path = Path(path)
        self.suggested_path = Path(suggested_path)
        self.file_format = file_format
        super().__init__(
            f"Unknown scenario file extension '{self.path.suffix}'. "
            f"Use '{self.suggested_path.name}' for {file_format.upper()}."
        )


def normalize_scenario_format(file_format):
    normalized = str(file_format or "").strip().lower().lstrip(".")
    if normalized == "yml":
        normalized = "yaml"
    if normalized not in ("json", "yaml"):
        raise ScenarioPathError("Scenario format must be JSON or YAML.")
    return normalized


def normalize_scenario_path(path, file_format, allow_unknown=False):
    """Return a path whose extension agrees with the selected format.

    Known scenario extensions are replaced automatically. Unknown extensions
    require an explicit opt-in so callers cannot silently rename user files.
    """
    normalized_format = normalize_scenario_format(file_format)
    target = Path(path)
    if not str(path).strip():
        raise ScenarioPathError("Scenario path cannot be empty.")

    preferred_extension = ".json" if normalized_format == "json" else ".yaml"
    valid_extensions = (
        {".json"}
        if normalized_format == "json"
        else {".yaml", ".yml"}
    )
    suffix = target.suffix.lower()

    if suffix in valid_extensions:
        if target.suffix == suffix:
            return target
        return target.with_suffix(suffix)
    if not suffix or suffix in {".json", ".yaml", ".yml"}:
        return target.with_suffix(preferred_extension)

    suggested_path = target.with_suffix(preferred_extension)
    if not allow_unknown:
        raise UnknownScenarioExtensionError(
            target,
            suggested_path,
            normalized_format,
        )
    return suggested_path
