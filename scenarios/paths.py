import re
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


WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def sanitize_scenario_filename(name, fallback="Scenario"):
    """Return a Windows-safe filename stem without changing the scenario name."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or ""))
    text = text.strip().rstrip(". ")
    if not text:
        text = str(fallback).strip() or "Scenario"
    if text.upper() in WINDOWS_RESERVED_NAMES:
        text += "_"
    return text


def scenario_path_for_name(path, name, file_format):
    """Build an auto-synchronized path while preserving the selected folder."""
    normalized_format = normalize_scenario_format(file_format)
    extension = ".json" if normalized_format == "json" else ".yaml"
    filename = sanitize_scenario_filename(name) + extension
    current = str(path or "").strip()
    return (Path(current).parent / filename) if current else Path(filename)


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
