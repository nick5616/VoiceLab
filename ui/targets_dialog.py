"""The dialog for editing profile targets; the ranges, notes and texts come from targets.py."""
from PyQt6.QtWidgets import (QVBoxLayout, QPushButton, QLabel, QDialog, QGridLayout, QCheckBox, QComboBox,
                             QDoubleSpinBox, QDialogButtonBox)
from PyQt6.QtCore import Qt

import targets
from targets import TARGET_EDIT
from ui.meters import BUTTON_STYLE


class TargetsDialog(QDialog):
    """Edit target ranges and manage the baseline."""

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.setWindowTitle("Targets & Baseline")
        self.setStyleSheet("""
            QDialog { background-color: #2b2b2b; } QLabel, QCheckBox { color: white; }
            QDoubleSpinBox { background: #1e1e1e; color: white; border: 1px solid #555; padding: 2px; }
        """)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Targets</b> — tick a metric to give it a target range."))
        # Where to start from: your own baseline, or a typical range that is only a starting point.
        self.start_from = QComboBox()
        for text, choice in targets.start_options(profile):
            self.start_from.addItem(text, choice)
        self.start_from.setStyleSheet("QComboBox { background: #1e1e1e; color: white; padding: 3px; }")
        self.start_from.activated.connect(self._start_from)
        layout.addWidget(self.start_from)
        self.notes = {}         # key -> why its range is what it is, shown beside it
        grid = QGridLayout()
        self.rows = {}
        for row, (key, info) in enumerate(targets.rows(profile).items()):
            lo_lim, hi_lim, decimals, unit = TARGET_EDIT[key]
            check = QCheckBox(info["label"])
            if info["tooltip"]:
                check.setToolTip(info["tooltip"])
            spins = []
            for _ in range(2):
                spin = QDoubleSpinBox()
                spin.setRange(lo_lim, hi_lim)
                spin.setDecimals(decimals)
                spin.setSingleStep(targets.step(key))
                spins.append(spin)
            hint = QLabel()
            hint.setStyleSheet("color: #aaa;")
            if info["note"]:
                self.notes[key] = info["note"]
            check.setChecked(info["on"])
            spins[0].setValue(info["range"][0])
            spins[1].setValue(info["range"][1])
            if key == "pitch":
                for spin in spins:
                    spin.valueChanged.connect(self._update_pitch_hint)
            grid.addWidget(check, row, 0)
            grid.addWidget(spins[0], row, 1)
            grid.addWidget(QLabel("to"), row, 2)
            grid.addWidget(spins[1], row, 3)
            grid.addWidget(QLabel(unit), row, 4)
            grid.addWidget(hint, row, 5)
            self.rows[key] = (check, spins, hint)
        layout.addLayout(grid)
        self._update_pitch_hint()
        for key in self.rows:
            self._update_hint(key)

        layout.addSpacing(10)
        layout.addWidget(QLabel("<b>Baseline</b>"))
        self.baseline_label = QLabel()
        self.baseline_label.setWordWrap(True)
        layout.addWidget(self.baseline_label)
        clear_btn = QPushButton("Clear baseline")
        clear_btn.setStyleSheet(BUTTON_STYLE)
        clear_btn.clicked.connect(self._clear_baseline)
        layout.addWidget(clear_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self._show_baseline()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.setStyleSheet(f"QPushButton {{ {BUTTON_STYLE} }}")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _start_from(self, index):
        """Fill the ranges from your baseline or a preset (ticking them); you can still edit each."""
        for key, (rng, note) in targets.start_from(self.profile, self.start_from.itemData(index)).items():
            check, spins, _ = self.rows[key]
            check.setChecked(True)
            for spin, value in zip(spins, rng):
                spin.setValue(value)
            self.notes[key] = note
            self._update_hint(key)

    def _update_hint(self, key):
        _, spins, hint = self.rows[key]
        hint.setText(targets.hint(key, [s.value() for s in spins], self.notes.get(key)))

    def _update_pitch_hint(self):
        self._update_hint("pitch")

    def _show_baseline(self):
        self.baseline_label.setText(targets.baseline_text(self.profile))

    def _clear_baseline(self):
        self.profile.clear_baseline()
        self._show_baseline()

    def accept(self):
        targets.save(self.profile, {key: (spins[0].value(), spins[1].value()) if check.isChecked() else None
                                    for key, (check, spins, _) in self.rows.items()})
        super().accept()
