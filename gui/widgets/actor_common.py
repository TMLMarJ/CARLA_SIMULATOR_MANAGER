"""Small shared helpers for actor tables."""


def find_tree_item_by_runtime_id(tree, runtime_id, id_column=1):
    for item in tree.get_children():
        if tree.item(item)["values"][id_column] == runtime_id:
            return item
    return None


def remove_tree_item_by_runtime_id(tree, runtime_id, id_column=1):
    item = find_tree_item_by_runtime_id(tree, runtime_id, id_column)
    if item is None:
        return False
    tree.delete(item)
    return True


def clear_tree(tree):
    for item in tree.get_children():
        tree.delete(item)
