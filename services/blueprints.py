class BlueprintService:
    @staticmethod
    def find_checked(blueprint_library, blueprint_id):
        requested_id = str(blueprint_id)
        try:
            blueprint = blueprint_library.find(requested_id)
        except Exception as error:
            raise RuntimeError(
                f"Blueprint '{requested_id}' is unavailable: {error}"
            ) from error

        resolved_id = str(getattr(blueprint, "id", ""))
        if resolved_id != requested_id:
            raise RuntimeError(
                "Blueprint lookup mismatch: requested "
                f"'{requested_id}', resolved '{resolved_id}'."
            )
        return blueprint

    @staticmethod
    def apply_attributes(blueprint, attributes, modifiable_only=False):
        for attribute_name, attribute_value in (attributes or {}).items():
            if not blueprint.has_attribute(attribute_name):
                continue
            if modifiable_only:
                attribute = blueprint.get_attribute(attribute_name)
                if not attribute.is_modifiable:
                    continue
            blueprint.set_attribute(attribute_name, str(attribute_value))
        return blueprint
