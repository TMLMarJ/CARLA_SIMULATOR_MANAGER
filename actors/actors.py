import carla
from dataclasses import dataclass
from typing import Dict, Type, List

@dataclass
class Actor:
    id: int
    name: str
    location: carla.Location

    def get_position(self):
        return self.location.x , self.location.y, self.location.z

@dataclass
class Vehicle(Actor):
    vehicle_type: str
    throttle: float
    angle: float
    speed: float

@dataclass
class Pedestrian(Actor):
    walking: bool
    speed: float
    angle: float

@dataclass
class StaticObject(Actor):
    object_type: str

class Actors:
    def __init__(self):
        self._actors: Dict[int, Actor] = {}

    def add(self, actor: Actor):
        self._actors[actor.id] = actor

    def remove(self, actor_id: int):
        if actor_id in self._actors:
            del self._actors[actor_id]

    def get(self, actor_id: int) -> Actor | None:
        return self._actors.get(actor_id)

    def get_all(self) -> List[Actor]:
        return list(self._actors.values())

    def get_by_type(self, actor_type: Type[Actor]) -> List[Actor]:
        return [a for a in self._actors.values() if isinstance(a, actor_type)]
