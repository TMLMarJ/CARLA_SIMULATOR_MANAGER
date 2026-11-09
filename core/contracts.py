from dataclasses import dataclass
from typing import Any


@dataclass
class RuntimeContext:
    client: Any
    database: Any
    world: Any
    blueprint_library: Any
