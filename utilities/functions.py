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
