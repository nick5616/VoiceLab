"""The dialog for editing profile targets."""
import numpy as np
from PyQt6.QtWidgets import (QVBoxLayout,
    QPushButton,
    QLabel,
    QDialog,
    QGridLayout,
    QCheckBox,
    QDoubleSpinBox,
    QDialogButtonBox)
from PyQt6.QtCore import Qt

from analysis import hz_to_note
from profile_store import TARGET_KEYS, target_from_value
from ui_common import BUTTON_STYLE, METRIC_DEFS, format_value

# Targets dialog: key -> (spinbox min, max, decimals, unit)
TARGET_EDIT = {
    "pitch": (50, 1200, 0, "Hz"),
    "delta_f": (800, 1500, 0, "Hz"),
    "h1h2": (-20, 30, 1, "dB"),
    "cpp": (0, 50, 1, "dB"),
    "ring": (-50, 10, 1, "dB"),
    "pitch_range": (0, 24, 1, "st"),
    "register": (0, 100, 0, "%"),      # head share of voiced time
}


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
        grid = QGridLayout()
        self.rows = {}
        for row, key in enumerate(TARGET_KEYS):
            lo_lim, hi_lim, decimals, unit = TARGET_EDIT[key]
            check = QCheckBox(METRIC_DEFS[key][0])
            if key == "register":
                check.setToolTip("Share of voiced time in head voice (M2). A target of 70-100% means "
                                 "mostly head-leaning. The register is an estimate until you label takes.")
            spins = []
            for _ in range(2):
                spin = QDoubleSpinBox()
                spin.setRange(lo_lim, hi_lim)
                spin.setDecimals(decimals)
                spin.setSingleStep(10 ** -decimals if decimals else (5 if unit == "Hz" else 1))
                spins.append(spin)
            hint = QLabel()
            hint.setStyleSheet("color: #aaa;")
            current = profile.target(key) or self._default_range(key)
            check.setChecked(profile.target(key) is not None)
            spins[0].setValue(current[0])
            spins[1].setValue(current[1])
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

    def _default_range(self, key):
        """Starting range for an unset target: around the baseline if there is one."""
        base = self.profile.baseline_value(key)
        if base is not None:
            return target_from_value(key, base)
        lo, hi = METRIC_DEFS[key][1:3]
        mid = np.sqrt(lo * hi) if key == "pitch" else (lo + hi) / 2
        return target_from_value(key, mid)

    def _update_pitch_hint(self):
        _, spins, hint = self.rows["pitch"]
        hint.setText(" – ".join(hz_to_note(s.value())[0] for s in spins))

    def _show_baseline(self):
        b = self.profile.baseline
        if not b:
            self.baseline_label.setText("No baseline set. Use “Set as baseline” on a take of your "
                                        "everyday or starting voice.")
            return
        parts = [f"Recorded {b.get('recorded', '?')}"]
        if b.get("pitch"):
            parts.append(format_value("pitch", b["pitch"]))
        if b.get("delta_f"):
            parts.append(f"ΔF {b['delta_f']:.0f} Hz")
        self.baseline_label.setText(" · ".join(parts))

    def _clear_baseline(self):
        self.profile.clear_baseline()
        self._show_baseline()

    def accept(self):
        for key, (check, spins, _) in self.rows.items():
            self.profile.set_target(key, (spins[0].value(), spins[1].value()) if check.isChecked() else None)
        super().accept()
