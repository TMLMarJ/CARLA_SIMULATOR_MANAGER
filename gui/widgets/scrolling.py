import tkinter as tk


MOUSEWHEEL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")


def mousewheel_steps(event):
    """Return positive steps for wheel-up and negative for wheel-down."""
    button = getattr(event, "num", None)
    if button == 4:
        return 1
    if button == 5:
        return -1

    try:
        delta = float(getattr(event, "delta", 0))
    except (TypeError, ValueError, OverflowError):
        return 0
    if delta == 0:
        return 0

    magnitude = max(1, int(abs(delta) / 120))
    return magnitude if delta > 0 else -magnitude


def vertical_scroll_units(event):
    """Translate Windows/Wayland/X11 wheel events to Tk scroll units."""
    return -mousewheel_steps(event)


def bind_mousewheel(widget, callback):
    """Bind all supported wheel events once and return removable bind ids."""
    return {
        sequence: widget.bind(sequence, callback, add="+")
        for sequence in MOUSEWHEEL_EVENTS
    }


def unbind_mousewheel(widget, bindings):
    for sequence, bind_id in tuple((bindings or {}).items()):
        if bind_id:
            try:
                widget.unbind(sequence, bind_id)
            except tk.TclError:
                pass

