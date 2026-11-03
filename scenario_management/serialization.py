import json
import hashlib
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from scenario_management.models import ScenarioDocument
from scenario_management.path_utils import (
    ScenarioPathError,
    normalize_scenario_format,
    normalize_scenario_path,
)


class ScenarioSerializationError(RuntimeError):
    pass


class ScenarioSerializer:
    JSON = "json"
    YAML = "yaml"
    FORMATS = {JSON, YAML, "yml"}

    def serialize(self, document, file_format):
        data = (
            document.to_dict()
            if isinstance(document, ScenarioDocument)
            else document
        )
        normalized = self._normalize_format(file_format)
        try:
            if normalized == self.JSON:
                return json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                ) + "\n"
            yaml = self._load_yaml()
            return yaml.safe_dump(
                data,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        except ScenarioSerializationError:
            raise
        except (TypeError, ValueError) as error:
            raise ScenarioSerializationError(
                f"Scenario contains non-serializable data: {error}"
            ) from error

    def deserialize(self, content, file_format):
        normalized = self._normalize_format(file_format)
        try:
            if normalized == self.JSON:
                return json.loads(content)
            return self._load_yaml().safe_load(content)
        except ScenarioSerializationError:
            raise
        except Exception as error:
            raise ScenarioSerializationError(
                f"Could not parse scenario data: {error}"
            ) from error

    def write_atomic(self, document, path, file_format=None):
        original_target = Path(path)
        normalized = self._normalize_format(
            file_format or original_target.suffix.lstrip(".")
        )
        try:
            target = normalize_scenario_path(
                original_target,
                normalized,
            )
        except ScenarioPathError as error:
            raise ScenarioSerializationError(str(error)) from error
        content = self.serialize(document, normalized)
        parent = target.parent
        temporary_path = None
        try:
            if not parent.exists():
                raise FileNotFoundError(
                    f"Destination directory does not exist: {parent}"
                )
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, target)
            temporary_path = None
        except Exception as error:
            raise ScenarioSerializationError(
                f"Could not write scenario file '{target}': {error}"
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return {
            "path": str(target.resolve()),
            "format": normalized,
            "bytes": len(content.encode("utf-8")),
        }

    @classmethod
    def read(cls, path, file_format=None):
        document, _digest = cls.read_snapshot(path, file_format)
        return document

    @classmethod
    def read_snapshot(cls, path, file_format=None):
        target = Path(path)
        try:
            raw_content = target.read_bytes()
            content = raw_content.decode("utf-8")
        except (OSError, UnicodeError) as error:
            raise ScenarioSerializationError(
                f"Could not read scenario file '{target}': {error}"
            ) from error
        document = cls().deserialize(
            content,
            file_format or target.suffix.lstrip("."),
        )
        if not isinstance(document, Mapping):
            raise ScenarioSerializationError(
                "Scenario document root must be an object."
            )
        return document, hashlib.sha256(raw_content).hexdigest()

    @classmethod
    def _normalize_format(cls, file_format):
        try:
            return normalize_scenario_format(file_format)
        except ScenarioPathError as error:
            raise ScenarioSerializationError(str(error)) from error

    @staticmethod
    def _load_yaml():
        try:
            import yaml
        except ImportError as error:
            raise ScenarioSerializationError(
                "YAML support requires PyYAML. Install project dependencies "
                "and try again."
            ) from error
        return yaml
