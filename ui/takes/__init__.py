"""The take report, the Takes screen, the dashboard's latest-take panel and recent list, and the targets
editor (phase 10 spec 06). Widgets draw what the core and take_view / report decide.

Shared plumbing: listen() subscribes a widget to core events and unsubscribes when it is destroyed, and
report_for() keeps each take's report until the take, its comparison or the profile changes.
"""
from PyQt6 import sip

CARD_EVENTS = ("take_added", "take_updated", "take_removed", "takes_loaded", "profile_changed",
               "progress_changed", "busy")      # the core events every spec 06 widget may follow
_reports = {}                   # (id(core), uid) -> (key, report)


def listen(widget, core, handlers):
    """Subscribe handlers ({event: fn}) to the core's events for as long as `widget` lives. A handler
    never runs once the widget is deleted, and every subscription is dropped when it is destroyed."""
    offs = []
    for name, fn in handlers.items():
        offs.append(core.events.subscribe(name, _guard(widget, fn)))
    widget.destroyed.connect(lambda *_: [off() for off in offs])
    return offs


def _guard(widget, fn):
    def call(*args):
        if not sip.isdeleted(widget):
            fn(*args)
    return call


def _profile_key(profile):
    keys = ("targets", "baseline", "voice_goal", "formant_ceiling", "vowel_baseline", "register_version")
    return repr([getattr(profile, k, None) for k in keys])


def report_for(core, uid):
    """core.report(uid), kept until the take (its tracks, register or title), the take it is compared
    with, or the profile changes. None for an unknown take."""
    data = core.take(uid)
    if data is None:
        return None
    previous = core.previous_take(data)
    key = (id(data.get("tracks")), id(data.get("register")), repr(data.get("confidence")), id(previous),
           _profile_key(core.profile), data.get("analysis_version"))
    cached = _reports.get((id(core), data.get("uid")))
    if cached and cached[0] == key:
        return cached[1]
    rep = core.report(data)
    _reports[(id(core), data.get("uid"))] = (key, rep)
    return rep
