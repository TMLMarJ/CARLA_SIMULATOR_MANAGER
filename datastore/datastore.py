from actors import actors
import carla
import queue
import threading

class DataStorage:
    def __init__(self):
        self.actors_db = actors.Actors()
        self.lock = threading.Lock()
        self.command_queue = queue.Queue()

    def add_actor(self, actor: carla.Actor):
        self.actors_db.add_actor(actor)