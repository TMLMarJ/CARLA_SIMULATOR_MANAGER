import queue


class CommandBus:
    def __init__(self, command_queue=None, priority_command_queue=None):
        self.command_queue = command_queue or queue.Queue()
        self.priority_command_queue = priority_command_queue or queue.Queue()

    def put(self, command):
        self.command_queue.put(command)

    def put_priority(self, command):
        self.priority_command_queue.put(command)

    def get_next(self, timeout):
        try:
            return self.priority_command_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            return self.command_queue.get(timeout=timeout)
        except queue.Empty:
            return None, None


class CommandDispatcher:
    def __init__(self):
        self._handlers = {}

    def register(self, command_name, handler):
        self._handlers[str(command_name)] = handler

    def dispatch(self, command_name, context, data):
        handler = self._handlers.get(command_name)
        if handler is None:
            return False
        handler(context, data)
        return True
