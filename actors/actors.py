import carla
from dataclasses import dataclass
from typing import Dict, Type, List

class Actors:
    def __init__(self):
        self.actors: Dict[int, carla.Actor] = {}

    def add_actor(self, actor: carla.Actor):
        self.actors[actor.id] = actor


