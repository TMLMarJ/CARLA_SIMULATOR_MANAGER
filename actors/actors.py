import carla
from dataclasses import dataclass
from typing import Dict, Type, List

class Actors:
    def __init__(self):
        self.actors: Dict[int, carla.Actor] = {}
        self.actors_db: Dict[int, ActorInfo] = {}

    def add_actor(self, actor: carla.Actor):
        self.actors[actor.id] = actor

    def add_actor_info(self, actor: carla.Actor, user_id: int, user_name: str):
        self.actors_db[actor.id] = ActorInfo(actor, user_id, user_name)


class ActorInfo:
    def __init__(self, actor: carla.Actor, user_id: int, user_name: str):
        self.user_id = user_id
        self.user_name = user_name
        self.carla_id = actor.id
        self.type_id = actor.type_id
        self.is_alive = actor.is_alive
        self.is_active = actor.is_active
        self.has_parent = actor.parent if actor.parent is not None else False
        self.bounding_box = actor.bounding_box
        self.acceleration = actor.get_acceleration()
        self.angular_velocity = actor.get_angular_velocity()
        self.location = actor.get_location()
        self.transform = actor.get_transform()
        self.velocity = actor.get_velocity()
        self.world = actor.get_world()

    def get_actor_info(self):
        actor_info = {"user_id": self.user_id,
                      "user_name": self.user_name,
                      "carla_id": self.carla_id,
                      "type_id": self.type_id,
                      "is_alive": self.is_alive,
                      "is_active": self.is_active,
                      "has_parent": self.has_parent,
                      "bounding_box": self.bounding_box,
                      "acceleration": self.acceleration,
                      "angular_velocity": self.angular_velocity,
                      "location": self.location,
                      "transform": self.transform,
                      "velocity": self.velocity,
                      "world": self.world}
        return actor_info

    def __str__(self):
        string = ""
        string += f"Actor Info Object defined as {self.user_id}::{self.user_name}\n"
        string += f"Carla ID: {self.carla_id}\n"
        string += f"Type ID: {self.type_id}\n"
        string += f"Is Active: {self.is_active}\n"
        string += f"Is Alive: {self.is_alive}\n"
        string += f"Has Parent: {self.has_parent is not None}\n"
        string += f"Acceleration: {self.acceleration}\n"
        string += f"Angular Velocity: {self.angular_velocity}\n"
        string += f"Location: {self.location}\n"
        string += f"Transform: {self.transform}\n"
        string += f"Velocity: {self.velocity}\n"
        return string

