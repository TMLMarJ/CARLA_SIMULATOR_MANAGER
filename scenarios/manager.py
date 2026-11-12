from scenarios.capture import ScenarioCapture
from scenarios.paths import normalize_scenario_path
from scenarios.loader import ScenarioLoader
from scenarios.restorer import ScenarioRestorer
from scenarios.serialization import ScenarioSerializer
from scenarios.validation import ScenarioValidator
from scenarios.cleaner import WorldCleaner

import carla


class ScenarioManager:
    def __init__(self, world_cleaner=None):
        self.capture = ScenarioCapture()
        self.validator = ScenarioValidator()
        self.serializer = ScenarioSerializer()
        self.loader = ScenarioLoader(self.serializer, self.validator)
        self.restorer = ScenarioRestorer(
            self.loader,
            self.serializer,
            self.validator,
            world_cleaner or WorldCleaner(),
            carla,
        )

    def save(self, world, database, internal_actor_ids, name, path,
             file_format, synchronize, cancel_check=None,
             server_version=None):
        target = normalize_scenario_path(path, file_format)
        sync_report = synchronize(
            world,
            reason="before_save",
            publish=False,
        )
        document = self.capture.capture(
            world,
            database,
            name,
            internal_actor_ids=internal_actor_ids,
            sync_report=sync_report,
            server_version=server_version,
        )
        self.validator.validate(document)
        if cancel_check is not None and cancel_check():
            raise RuntimeError("Scenario save cancelled during disconnect.")
        file_result = self.serializer.write_atomic(
            document,
            target,
            file_format,
        )
        report = document.capture_report
        return {
            "success": True,
            "name": document.metadata.name,
            "path": file_result["path"],
            "format": file_result["format"],
            "bytes": file_result["bytes"],
            "saved": report.saved,
            "partial": report.partial,
            "skipped_external": report.skipped_external,
            "skipped_unsupported": report.skipped_unsupported,
            "skipped_dependency": report.skipped_dependency,
            "skipped_runtime_only": report.skipped_runtime_only,
            "skipped_unrestorable": report.skipped_unrestorable,
            "skipped_internal": report.skipped_internal,
            "skipped_infrastructure": report.skipped_infrastructure,
            "warnings": list(report.warnings),
            "errors": list(report.errors),
        }

    def prepare_load(self, path, world, available_maps, blueprint_library,
                     server_version=None):
        return self.loader.prepare(
            path,
            world,
            available_maps,
            blueprint_library,
            server_version=server_version,
        )

    def execute_load(self, plan, confirmed, world, database,
                     blueprint_library, available_maps, server_version,
                     internal_actor_ids, before_clear, change_world,
                     apply_world_settings, restore_control, synchronize):
        return self.restorer.execute(
            plan,
            confirmed,
            world,
            database,
            blueprint_library,
            available_maps,
            server_version,
            internal_actor_ids,
            before_clear,
            change_world,
            apply_world_settings,
            restore_control,
            synchronize,
        )
