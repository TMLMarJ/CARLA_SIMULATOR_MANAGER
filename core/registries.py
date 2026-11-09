from typing import Dict

import carla

from models.actors import ActorInfo, SensorInfo


class Actors:
    def __init__(self):
        self.actors: Dict[int, carla.Actor] = {}
        self.actors_db: Dict[int, ActorInfo] = {}

    def add_actor(self, actor: carla.Actor, name: str, **metadata):
        actor_info = ActorInfo(actor, name, **metadata)
        self.actors[actor.id] = actor
        self.actors_db[actor.id] = actor_info
        return actor_info

    def remove_actor(self, actor_id: int):
        self.actors.pop(actor_id, None)
        return self.actors_db.pop(actor_id, None)

    def clear(self):
        self.actors.clear()
        self.actors_db.clear()


class Sensors:
    def __init__(self):
        self.sensors: Dict[int, carla.Sensor] = {}
        self.sensors_db: Dict[int, SensorInfo] = {}

    def add_sensor(self, sensor: carla.Sensor, parent_id: int, name: str,
                   blueprint_id: str, relative_transform: carla.Transform,
                   attributes: dict, **metadata):
        sensor_info = SensorInfo(
            sensor,
            parent_id,
            name,
            blueprint_id,
            relative_transform,
            attributes,
            **metadata,
        )
        self.sensors[sensor.id] = sensor
        self.sensors_db[sensor.id] = sensor_info
        return sensor_info

    def remove_sensor(self, sensor_id: int):
        self.sensors.pop(sensor_id, None)
        return self.sensors_db.pop(sensor_id, None)

    def clear(self):
        self.sensors.clear()
        self.sensors_db.clear()
