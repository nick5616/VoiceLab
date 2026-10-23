"""Core event subscriptions that end with the widget that made them."""
from PyQt6 import sip


def listen(widget, events, *pairs):
    """Subscribe each (name, fn) to `events` for as long as `widget` lives: they are unsubscribed when it
    is destroyed, and a call already queued for it (events from job threads arrive later) is dropped once
    it is gone. Returns the unsubscribe functions."""
    unsubscribes = []
    for name, fn in pairs:
        def call(*args, fn=fn):
            if not sip.isdeleted(widget):
                fn(*args)
        unsubscribes.append(events.subscribe(name, call))
    widget.destroyed.connect(lambda *_: [u() for u in unsubscribes])
    return unsubscribes
