import tkinter.messagebox as tkinter_messagebox
import unittest
from types import SimpleNamespace
from unittest import mock

from gui.tabs import home, simulation, world
from gui.tabs.home import ConnectionFrame
from gui.widgets.scrolling import (
    MOUSEWHEEL_EVENTS,
    bind_mousewheel,
    mousewheel_steps,
    unbind_mousewheel,
    vertical_scroll_units,
)
from preview.movement import MovementPreviewProcess
from preview.spectator import SpectatorPreviewManager


class FakeBindingWidget:
    def __init__(self):
        self.bound = []
        self.unbound = []

    def bind(self, sequence, callback, add=None):
        self.bound.append((sequence, callback, add))
        return f"binding-{sequence}"

    def unbind(self, sequence, bind_id):
        self.unbound.append((sequence, bind_id))


class WheelCompatibilityTests(unittest.TestCase):
    def test_windows_wayland_and_x11_events_have_consistent_direction(self):
        self.assertEqual(1, mousewheel_steps(SimpleNamespace(delta=120)))
        self.assertEqual(-1, mousewheel_steps(SimpleNamespace(delta=-120)))
        self.assertEqual(1, mousewheel_steps(SimpleNamespace(delta=1)))
        self.assertEqual(-1, mousewheel_steps(SimpleNamespace(delta=-1)))
        self.assertEqual(1, mousewheel_steps(SimpleNamespace(num=4)))
        self.assertEqual(-1, mousewheel_steps(SimpleNamespace(num=5)))
        self.assertEqual(-1, vertical_scroll_units(SimpleNamespace(num=4)))
        self.assertEqual(1, vertical_scroll_units(SimpleNamespace(num=5)))

    def test_supported_bindings_are_registered_and_removed_once(self):
        widget = FakeBindingWidget()
        callback = mock.Mock()

        bindings = bind_mousewheel(widget, callback)
        unbind_mousewheel(widget, bindings)

        self.assertEqual(list(MOUSEWHEEL_EVENTS), [item[0] for item in widget.bound])
        self.assertTrue(all(item[2] == "+" for item in widget.bound))
        self.assertEqual(3, len(widget.unbound))


class TkDialogCompatibilityTests(unittest.TestCase):
    def test_gui_tabs_use_tkinter_messagebox(self):
        self.assertIs(home.messagebox, tkinter_messagebox)
        self.assertIs(simulation.messagebox, tkinter_messagebox)
        self.assertIs(world.messagebox, tkinter_messagebox)

    def test_invalid_connection_uses_tk_dialog_on_gui_callback(self):
        frame = ConnectionFrame.__new__(ConnectionFrame)
        frame.host_var = SimpleNamespace(get=lambda: "127.0.0.1")
        frame.port_var = SimpleNamespace(get=lambda: "invalid")
        frame.manager = mock.Mock()

        with mock.patch("gui.tabs.home.messagebox.showerror") as showerror:
            frame.connect()

        showerror.assert_called_once()
        frame.manager.connect_to_carla.assert_not_called()


class FakeIpcQueue:
    def __init__(self):
        self.cancelled = False
        self.closed = False

    def cancel_join_thread(self):
        self.cancelled = True

    def close(self):
        self.closed = True


class FailingProcess:
    def __init__(self):
        self.closed = False

    def start(self):
        raise OSError("spawn unavailable")

    def close(self):
        self.closed = True


class FailingContext:
    def __init__(self):
        self.queues = []
        self.process = FailingProcess()

    def Event(self):
        return mock.Mock()

    def Queue(self, *args, **kwargs):
        ipc_queue = FakeIpcQueue()
        self.queues.append(ipc_queue)
        return ipc_queue

    def Process(self, *args, **kwargs):
        return self.process


class PreviewSpawnCompatibilityTests(unittest.TestCase):
    def test_movement_spawn_failure_releases_ipc_resources(self):
        context = FailingContext()
        with mock.patch(
            "preview.movement.multiprocessing.get_context",
            return_value=context,
        ):
            preview = MovementPreviewProcess()

        self.assertFalse(preview.start())
        self.assertIsNone(preview.process)
        self.assertTrue(context.process.closed)
        self.assertTrue(all(queue.closed for queue in context.queues))

    def test_spectator_spawn_failure_releases_ipc_resources(self):
        context = FailingContext()
        with mock.patch(
            "preview.spectator.multiprocessing.get_context",
            return_value=context,
        ):
            preview = SpectatorPreviewManager()

        with self.assertRaisesRegex(OSError, "spawn unavailable"):
            preview.start("127.0.0.1", 2000)
        self.assertIsNone(preview.process)
        self.assertTrue(context.process.closed)
        self.assertTrue(all(queue.closed for queue in context.queues))


if __name__ == "__main__":
    unittest.main()
