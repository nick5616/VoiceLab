"""The targets editor as a panel (Settings embeds it; it also fits in a dialog): a range per metric shown on
its zones, presets and "start from your baseline", the baseline itself, and the voice goal. targets.py
decides the ranges and texts; apply() saves them through the profile and tells the core."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QVBoxLayout, QWidget

import targets
from targets import TARGET_EDIT
from ui import theme
from ui.components import Button, Caption, Card, FlowGrid, Label, Select, Toggle, WrapRow, ZoneBar
from ui.takes import listen

ROW_COLUMN = 380                # px: narrowest target card


class TargetRow(Card):
    """One target: on/off, low and high, the range drawn on the metric's zones with your baseline, and
    why the range is what it is."""

    changed = pyqtSignal()

    def __init__(self, profile, key, info):
        super().__init__(padding=theme.M)
        self.key = key
        self.note = info["note"]
        lo_lim, hi_lim, decimals, unit = TARGET_EDIT[key]
        self.toggle = Toggle(info["on"], targets.PLAIN_NAMES.get(key, info["label"]))
        if info["tooltip"]:
            self.toggle.setToolTip(info["tooltip"])
        self.toggle.toggled.connect(self._changed)
        self.spins = []
        for value in info["range"]:
            spin = QDoubleSpinBox()
            spin.setRange(lo_lim, hi_lim)
            spin.setDecimals(decimals)
            spin.setSingleStep(targets.step(key))
            spin.setValue(value)
            spin.setFont(theme.font("numeric"))
            spin.setMinimumWidth(84)
            spin.valueChanged.connect(self._changed)
            self.spins.append(spin)
        top = QHBoxLayout()
        top.setSpacing(theme.S)
        top.addWidget(self.toggle)
        top.addStretch(1)
        top.addWidget(self.spins[0])
        top.addWidget(Label("to", role="caption", tone="muted"))
        top.addWidget(self.spins[1])
        top.addWidget(Label(unit, role="caption", tone="muted"))
        self.body.addLayout(top)
        spec = targets.bar_spec(profile, key)
        self.bar = ZoneBar.from_spec(spec, fmt="{:.0f}" if decimals == 0 else "{:.1f}")
        if spec.get("ends"):
            self.bar.set_end_labels(*spec["ends"])
            self.bar.set_ticks([])
        self.bar.set_baseline(profile.baseline_value(key))
        self.add(self.bar)
        self.hint = Caption("", tone="secondary")
        self.add(self.hint)
        self._sync()

    def value(self):
        """(low, high), or None with the target off."""
        return (self.spins[0].value(), self.spins[1].value()) if self.toggle.isChecked() else None

    def fill(self, rng, note):
        self.toggle.set_checked(True)
        for spin, value in zip(self.spins, rng):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self.note = note
        self._changed()

    def _changed(self, *_):
        self._sync()
        self.changed.emit()

    def _sync(self):
        on = self.toggle.isChecked()
        values = sorted(s.value() for s in self.spins)
        self.bar.set_target(values if on else None)
        for spin in self.spins:
            spin.setEnabled(on)
        text = targets.hint(self.key, values, self.note)
        self.hint.setText(text if on else ("Off · " + text if text else "Off"))


class TargetsEditor(QWidget):
    """Target ranges and the baseline, as a panel. apply() saves; saved() after it. With `buttons` it has
    its own Save and Revert buttons."""

    saved = pyqtSignal()

    def __init__(self, core, buttons=True, parent=None):
        super().__init__(parent)
        self.core = core
        self.dirty = False
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(theme.L)
        self.goal = Caption("", tone="secondary")
        box.addWidget(self.goal)
        self.start_from = Select([])
        self.start_from.changed.connect(self._start_from)
        intro = Caption("Switch a metric on to give it a target range. Presets are typical values, and "
                        "yours may differ: starting from your baseline keeps steps small.", tone="secondary")
        box.addWidget(intro)
        box.addWidget(WrapRow([Label("Start from", role="label", tone="muted"), self.start_from]))
        self.grid = FlowGrid(min_column_width=ROW_COLUMN, spacing=theme.M, max_column_width=640)
        box.addWidget(self.grid)
        baseline = Card("Baseline", subtitle="The take your changes are measured against.", padding=theme.M)
        self.baseline_label = Label("", role="body", tone="secondary", wrap=True)
        baseline.add(self.baseline_label)
        self.clear_btn = Button("Clear baseline", "danger", small=True)
        self.clear_btn.clicked.connect(self.clear_baseline)
        baseline.add(self.clear_btn)
        baseline.body.setAlignment(self.clear_btn, Qt.AlignmentFlag.AlignLeft)
        box.addWidget(baseline)
        self.save_btn = self.revert_btn = None
        if buttons:
            self.save_btn = Button("Save targets", "primary", icon="ph.check")
            self.save_btn.clicked.connect(self.apply)
            self.revert_btn = Button("Revert", "ghost")
            self.revert_btn.clicked.connect(self.reload)
            box.addWidget(WrapRow([self.save_btn, self.revert_btn]))
        box.addStretch(1)
        self.rows = {}
        listen(self, core, {"profile_changed": self._profile_changed})
        self.reload()

    def reload(self, *_):
        """Show the profile's targets again (discarding edits)."""
        profile = self.core.profile
        self.goal.setText(targets.goal_text(profile))
        self.start_from.set_options([(choice, text) for text, choice in targets.start_options(profile)])
        for row in self.rows.values():
            row.deleteLater()
        self.grid.items = []
        self.rows = {}
        for key, info in targets.rows(profile).items():
            row = TargetRow(profile, key, info)
            row.changed.connect(self._edited)
            self.rows[key] = row
            self.grid.add(row)
        self.baseline_label.setText(targets.baseline_text(profile))
        self.clear_btn.setEnabled(bool(profile.baseline))
        self._set_dirty(False)

    def _profile_changed(self):
        if not self.dirty:
            self.reload()
        else:
            self.goal.setText(targets.goal_text(self.core.profile))
            self.baseline_label.setText(targets.baseline_text(self.core.profile))

    def _start_from(self, choice):
        """Fill the ranges from your baseline or a preset; you can still edit each."""
        for key, (rng, note) in targets.start_from(self.core.profile, choice).items():
            self.rows[key].fill(rng, note)
        self.start_from.blockSignals(True)
        self.start_from.setCurrentIndex(0)
        self.start_from.blockSignals(False)

    def _edited(self):
        self._set_dirty(True)

    def _set_dirty(self, dirty):
        self.dirty = dirty
        if self.save_btn is not None:
            self.save_btn.setEnabled(dirty)
            self.revert_btn.setEnabled(dirty)

    def ranges(self):
        return {key: row.value() for key, row in self.rows.items()}

    def apply(self):
        """Save the targets to the profile and let everything that shows them update."""
        targets.save(self.core.profile, self.ranges())
        self._set_dirty(False)
        self.core.refresh_profile("Targets saved")
        self.saved.emit()

    def clear_baseline(self):
        self.core.profile.clear_baseline()
        self.core.refresh_profile("Baseline cleared")
