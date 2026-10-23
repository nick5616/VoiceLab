"""Motion helpers on QVariantAnimation / QPropertyAnimation. Every helper jumps straight to its end
state when theme.reduce_motion is set, and returns the running animation (or None).

Durations follow common UI motion guidance: 100-300 ms for small transitions, exits a little
quicker than entrances, ease-out for things arriving (Material Design "Speed" and NN/g
"Animation duration"; both from memory, unverified). The exact values are own judgement.
"""
from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QObject, QPropertyAnimation, QVariantAnimation
from PyQt6.QtWidgets import QGraphicsOpacityEffect

from ui import theme

ENTER_MS = 200          # fades and expanding sections
EXIT_MS = 150           # leaving is quicker than arriving
MOVE_MS = 220           # markers gliding to a new value
PULSE_MS = 1400         # one breath of the recording pulse; slow enough to stay calm
SHIMMER_MS = 1300       # one sweep of a skeleton's shimmer
SPIN_MS = 900           # one turn of a spinner
EASE_OUT = QEasingCurve.Type.OutCubic
WIDGET_MAX = 16777215   # QWIDGETSIZE_MAX
_unowned = set()        # tweens without an owner, kept alive until they finish


def _new(owner):
    """A QVariantAnimation that dies with its owner, so it never calls into a deleted widget."""
    if isinstance(owner, QObject):
        return QVariantAnimation(owner)
    anim = QVariantAnimation()
    _unowned.add(anim)
    anim.finished.connect(lambda: _unowned.discard(anim))
    return anim


def _guard(fn):
    """Call fn, ignoring a widget deleted mid-animation (an exception in a slot aborts PyQt)."""
    def call(*args):
        try:
            fn(*args)
        except RuntimeError:
            pass
    return call


def _keep(owner, name, anim):
    """Hold a reference on the owner (so the animation isn't collected) and stop any earlier
    animation of the same name, so a new value interrupts the old glide."""
    anims = owner.__dict__.setdefault("_ui_anims", {})
    old = anims.get(name)
    if old is not None and old is not anim:
        try:
            old.stop()
        except RuntimeError:        # already deleted on the C++ side
            pass
    anims[name] = anim
    return anim


def stop(owner, name):
    """Stop a named animation started on `owner` by these helpers."""
    anim = owner.__dict__.get("_ui_anims", {}).pop(name, None)
    if anim is not None:
        try:
            anim.stop()
        except RuntimeError:
            pass


def running(owner, name):
    """True while the named animation on `owner` runs."""
    anim = owner.__dict__.get("_ui_anims", {}).get(name)
    try:
        return anim is not None and anim.state() == QAbstractAnimation.State.Running
    except RuntimeError:
        return False


def tween(value_setter, start, end, ms=MOVE_MS, owner=None, name="tween", easing=EASE_OUT):
    """Animate a float from start to end, calling value_setter(v) each frame. With `owner`, a new
    tween of the same name replaces the running one (start from where it got to)."""
    if theme.reduce_motion or start is None or start == end or ms <= 0:
        if owner is not None:
            stop(owner, name)
        value_setter(end)
        return None
    anim = _new(owner)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setDuration(int(ms))
    anim.setEasingCurve(easing)
    anim.valueChanged.connect(_guard(lambda v: value_setter(float(v))))
    if owner is not None:
        _keep(owner, name, anim)
    anim.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)
    return anim


def _opacity(widget):
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    return effect


def _drop_effect(widget):
    """Opacity effects cost a render pass each; remove them once a fade is over."""
    try:
        widget.setGraphicsEffect(None)
    except RuntimeError:
        pass


def fade_in(widget, ms=ENTER_MS):
    """Show the widget and fade it from transparent to opaque."""
    stop(widget, "fade")
    widget.show()
    if theme.reduce_motion:
        _drop_effect(widget)
        return None
    effect = _opacity(widget)
    effect.setOpacity(0.0)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(int(ms))
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(EASE_OUT)
    anim.finished.connect(_guard(lambda: _drop_effect(widget)))
    _keep(widget, "fade", anim)
    anim.start()
    return anim


def fade_out(widget, ms=EXIT_MS, then=None):
    """Fade the widget out, hide it, then call `then()`."""
    stop(widget, "fade")

    def done():
        widget.hide()
        _drop_effect(widget)
        if then:
            then()

    if theme.reduce_motion or not widget.isVisible():
        done()
        return None
    effect = _opacity(widget)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(int(ms))
    anim.setStartValue(effect.opacity())
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.InCubic)
    anim.finished.connect(_guard(done))
    _keep(widget, "fade", anim)
    anim.start()
    return anim


def slide_down(widget, ms=ENTER_MS):
    """Expand a hidden section from zero height to its natural height."""
    stop(widget, "slide")
    widget.setMaximumHeight(0 if not theme.reduce_motion else WIDGET_MAX)
    widget.show()
    if theme.reduce_motion:
        return None
    target = max(widget.sizeHint().height(), widget.minimumSizeHint().height())

    def finish():
        widget.setMaximumHeight(WIDGET_MAX)

    anim = tween(lambda v: widget.setMaximumHeight(int(v)), 0, target, ms, owner=widget, name="slide")
    if anim is None:
        finish()
    else:
        anim.finished.connect(_guard(finish))
    return anim


def collapse(widget, ms=EXIT_MS, then=None):
    """Shrink a section to zero height, hide it, then call `then()`."""
    stop(widget, "slide")

    def done():
        widget.hide()
        widget.setMaximumHeight(WIDGET_MAX)
        if then:
            then()

    if theme.reduce_motion or not widget.isVisible():
        done()
        return None
    anim = tween(lambda v: widget.setMaximumHeight(int(v)), widget.height(), 0, ms, owner=widget, name="slide")
    if anim is None:
        done()
    else:
        anim.finished.connect(_guard(done))
    return anim


def loop(widget, attr, ms, name=None):
    """Run widget.<attr> from 0 to 1 forever (repainting each frame); for pulses, shimmers and
    spinners. Returns None (and leaves attr at 0) under reduce_motion."""
    name = name or attr
    stop(widget, name)
    setattr(widget, attr, 0.0)
    if theme.reduce_motion:
        widget.update()
        return None

    def step(v):
        setattr(widget, attr, float(v))
        widget.update()

    anim = QVariantAnimation(widget)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(int(ms))
    anim.setLoopCount(-1)
    anim.valueChanged.connect(_guard(step))
    _keep(widget, name, anim)
    anim.start()
    return anim


def pulse(widget, ms=PULSE_MS):
    """A slow breathing pulse: sets widget.pulse_phase 0-1 and repaints (the recording button)."""
    return loop(widget, "pulse_phase", ms)


def shimmer(widget, ms=SHIMMER_MS):
    """A light sweep across a placeholder: sets widget.shimmer_phase 0-1 and repaints."""
    return loop(widget, "shimmer_phase", ms)


def spin(widget, ms=SPIN_MS):
    """A spinner's rotation: sets widget.spin_phase 0-1 and repaints."""
    return loop(widget, "spin_phase", ms)
