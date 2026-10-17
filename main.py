import sys

import numpy as np
import pyqtgraph as pg
import sounddevice as sd
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QScrollArea,
                             QProgressBar, QFrame, QSizePolicy, QTabWidget)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont
import qtawesome as qta

from analysis import SAMPLE_RATE, vocal_tract_length_cm
from engine import LiveEngine, TakeAnalysisWorker, UPDATE_INTERVAL
from live_views import PitchView, SpectrumView, PitchResonancePlane, pitch_label

# --- DISPLAY RANGES ---
# (key, label, min, max, low label, high label, value format)
METRICS = [
    ("delta_f", "Resonance (ΔF)", 900, 1300, "Dark", "Bright", "{:.0f} Hz"),
    ("h1h2", "Fold Contact (H1*–H2*)", -5, 15, "Heavy", "Light", "{:+.1f} dB"),
    ("cpp", "Clarity (CPP)", 8, 30, "Breathy", "Clear", "{:.1f} dB"),
    ("ring", "Ring (2–4 kHz)", -30, 0, "Muffled", "Ringing", "{:+.1f} dB"),
]
MIN_PITCH, MAX_PITCH = 60, 1200
MIN_CONTOUR, MAX_CONTOUR = 0.5, 5    # semitones
LEVEL_FLOOR = -60                     # dBFS
LIVE_VIEW_HEIGHT = 220
# Exponential smoothing for live meters (~200 ms time constant).
METER_SMOOTHING = 1 - np.exp(-UPDATE_INTERVAL / 0.2)

BAR_COLORS = ("#3498db", "#9b59b6", "#e74c3c")
DISABLED_COLOR = "#555"


def bar_style(color):
    return (f"QProgressBar {{ background-color: #444; border-radius: 4px; }} "
            f"QProgressBar::chunk {{ background-color: {color}; border-radius: 4px; }}")


def bar_position(value, min_val, max_val):
    norm = (value - min_val) / (max_val - min_val)
    return max(0, min(1, norm)) * 100


def bar_color(position):
    return BAR_COLORS[0] if position < 33 else BAR_COLORS[1] if position < 66 else BAR_COLORS[2]


class MetricBar(QWidget):
    """Labelled bar between two descriptors; updatable for live use."""

    def __init__(self, label, min_val, max_val, low_label, high_label, fmt="{:.1f}"):
        super().__init__()
        self.label, self.min_val, self.max_val, self.fmt = label, min_val, max_val, fmt
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 5, 0, 5)

        self.title = QLabel(label)
        self.title.setStyleSheet("font-size: 11px; color: #aaa;")
        vbox.addWidget(self.title)

        bar_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        low, high = QLabel(low_label), QLabel(high_label)
        for lbl in (low, high):
            lbl.setStyleSheet("color: #ddd; font-size: 11px;")
            lbl.setFixedWidth(60)
        high.setAlignment(Qt.AlignmentFlag.AlignRight)
        bar_row.addWidget(low)
        bar_row.addWidget(self.progress)
        bar_row.addWidget(high)
        vbox.addLayout(bar_row)

    def set_value(self, value, note=None, display=None):
        if value is None or np.isnan(value):
            self.title.setText(f"{self.label} — {note or 'no reading'}")
            self.progress.setValue(0)
            self.progress.setStyleSheet(bar_style(DISABLED_COLOR))
            return
        position = bar_position(value, self.min_val, self.max_val)
        text = display or self.fmt.format(value)
        self.title.setText(f"{self.label} ({text})" + (f" — {note}" if note else ""))
        self.progress.setValue(int(position))
        self.progress.setStyleSheet(bar_style(DISABLED_COLOR if note else bar_color(position)))


class LivePanel(QFrame):
    """Tabbed live views and meters fed by the LiveEngine."""

    def __init__(self, spectrum_freqs):
        super().__init__()
        self.setStyleSheet("QFrame { background-color: #2b2b2b; border-radius: 10px; } "
                           "QLabel { color: #ffffff; }")
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel("Live")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.note_label = QLabel("—")
        self.note_label.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.note_label)
        layout.addLayout(header)

        self.pitch_view = PitchView()
        self.spectrum_view = SpectrumView(spectrum_freqs)
        self.plane = PitchResonancePlane()
        self.tabs = QTabWidget()
        self.tabs.setFixedHeight(LIVE_VIEW_HEIGHT + 30)
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: none; }
            QTabBar::tab { background: #333; color: #aaa; padding: 5px 14px; border-radius: 4px; margin-right: 4px; }
            QTabBar::tab:selected { background: #3498db; color: white; }
        """)
        self.tabs.addTab(self.pitch_view, "Pitch")
        self.tabs.addTab(self.spectrum_view, "Spectrum")
        self.tabs.addTab(self.plane, "Pitch × Resonance")
        layout.addWidget(self.tabs)

        self.meters = {key: MetricBar(label, lo, hi, low, high, fmt)
                       for key, label, lo, hi, low, high, fmt in METRICS}
        self.smoothed = {key: np.nan for key in self.meters}
        self.smoothed_log_f0 = np.nan
        for meter in self.meters.values():
            layout.addWidget(meter)
        self.level_meter = MetricBar("Input Level", LEVEL_FLOOR, 0, "Quiet", "Loud", "{:.0f} dBFS")
        layout.addWidget(self.level_meter)

    @staticmethod
    def _smooth(prev, value):
        return value if np.isnan(prev) else prev + METER_SMOOTHING * (value - prev)

    def update_frame(self, frame):
        f0 = frame["f0"]
        self.pitch_view.update_frame(frame)
        self.spectrum_view.update_frame(frame)
        self.level_meter.set_value(frame["level"])

        if np.isnan(f0):
            self.note_label.setText("—")
            self.plane.update_point(np.nan, np.nan)
            return
        self.note_label.setText(pitch_label(f0))
        self.smoothed_log_f0 = self._smooth(self.smoothed_log_f0, np.log2(f0))
        for key, meter in self.meters.items():
            value = frame[key]
            if not np.isnan(value):
                self.smoothed[key] = self._smooth(self.smoothed[key], value)
            smoothed = self.smoothed[key]
            if key == "delta_f" and not frame.get("resonance_reliable"):
                meter.set_value(None, note="pitch too high to measure")
            elif key == "delta_f" and not np.isnan(smoothed):
                meter.set_value(smoothed, display=f"{smoothed:.0f} Hz, "
                                                  f"≈{vocal_tract_length_cm(smoothed):.1f} cm tract")
            else:
                meter.set_value(smoothed)

        resonance = self.smoothed["delta_f"] if frame["resonance_reliable"] else np.nan
        self.plane.update_point(2 ** self.smoothed_log_f0, resonance)


class AnalysisGraph(pg.GraphicsLayoutWidget):
    """Pitch track on top, F1-F3 below, with a hover readout."""

    def __init__(self, tracks):
        super().__init__()
        self.setBackground("#2b2b2b")
        self.tracks = tracks
        t = tracks["times"]

        self.ax_pitch = self.addPlot(row=0, col=0)
        self.ax_res = self.addPlot(row=1, col=0)
        self.ci.layout.setRowStretchFactor(0, 1)
        self.ci.layout.setRowStretchFactor(1, 2)
        self.ax_res.setXLink(self.ax_pitch)
        self.ax_pitch.setLabel("left", "Hz")
        self.ax_res.setLabel("left", "Formants Hz")
        for ax in (self.ax_pitch, self.ax_res):
            ax.showGrid(x=True, y=True, alpha=0.2)
            ax.setMouseEnabled(x=False, y=False)
            ax.hideButtons()

        self.ax_pitch.plot(t, tracks["f0"], pen=pg.mkPen("#3498db", width=2), connect="finite")
        self.ax_res.addLegend(offset=(-10, 10))
        for key, color in (("f3", "#e74c3c"), ("f2", "#f1c40f"), ("f1", "#2ecc71")):
            self.ax_res.plot(t, tracks[key], pen=pg.mkPen(color, width=1.5),
                             connect="finite", name=key.upper())

        self.cursors = [pg.InfiniteLine(angle=90, pen=pg.mkPen("w", width=1)) for _ in range(2)]
        self.ax_pitch.addItem(self.cursors[0])
        self.ax_res.addItem(self.cursors[1])
        self.text_p = pg.TextItem(color="w", anchor=(0, 0))
        self.text_r = pg.TextItem(color="w", anchor=(0, 0))
        self.ax_pitch.addItem(self.text_p, ignoreBounds=True)
        self.ax_res.addItem(self.text_r, ignoreBounds=True)
        self.scene().sigMouseMoved.connect(self.on_hover)

    def on_hover(self, pos):
        for ax in (self.ax_pitch, self.ax_res):
            if ax.sceneBoundingRect().contains(pos):
                x = ax.vb.mapSceneToView(pos).x()
                break
        else:
            return
        for cursor in self.cursors:
            cursor.setValue(x)
        idx = int(np.abs(self.tracks["times"] - x).argmin())
        p_val = self.tracks["f0"][idx]
        f1, f2, f3 = (self.tracks[k][idx] for k in ("f1", "f2", "f3"))

        self.text_p.setText(pitch_label(p_val) if not np.isnan(p_val) else "Unvoiced")
        self.text_p.setPos(self.ax_pitch.vb.viewRange()[0][0], self.ax_pitch.vb.viewRange()[1][1])
        if not np.isnan(f1):
            self.text_r.setText(f"F1:{f1:.0f} F2:{f2:.0f} F3:{f3:.0f}")
            self.text_r.setPos(self.ax_res.vb.viewRange()[0][0], self.ax_res.vb.viewRange()[1][1])


class TakeCard(QFrame):
    def __init__(self, data, count):
        super().__init__()
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setStyleSheet("""
            QFrame { background-color: #2b2b2b; border-radius: 10px; margin-bottom: 10px; }
            QLabel { color: #ffffff; }
        """)

        self.audio_data = data['audio']
        layout = QVBoxLayout()

        # Header
        header = QHBoxLayout()
        title = QLabel(f"Take #{count}")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        play_btn = QPushButton(qta.icon('fa5s.play'), " Play")
        play_btn.clicked.connect(self.play_audio)
        play_btn.setStyleSheet("background-color: #4CAF50; color: white; padding: 5px; border-radius: 5px;")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(play_btn)
        layout.addLayout(header)

        # --- GRAPH ---
        graph = AnalysisGraph(data['tracks'])
        graph.setFixedHeight(200)
        layout.addWidget(graph)

        # --- METRIC BARS ---
        pitch_bar = MetricBar("Pitch", np.log2(MIN_PITCH), np.log2(MAX_PITCH), "Low", "High")
        pitch_bar.set_value(np.log2(data['pitch']), display=f"{data['note']} {data['cents']:+d}¢ · {data['pitch']:.0f} Hz")
        layout.addWidget(pitch_bar)

        for key, label, lo, hi, low, high, fmt in METRICS:
            bar = MetricBar(label, lo, hi, low, high, fmt)
            if key == "delta_f" and data["delta_f"] is not None:
                bar.set_value(data["delta_f"], display=f"{data['delta_f']:.0f} Hz, ≈{data['vtl']:.1f} cm tract")
            elif key == "delta_f":
                bar.set_value(None, note=data["resonance_note"])
            else:
                bar.set_value(data[key])
            layout.addWidget(bar)

        contour = MetricBar("Pitch Movement", MIN_CONTOUR, MAX_CONTOUR, "Monotone", "Dynamic", "{:.1f} st")
        contour.set_value(data['contour'])
        layout.addWidget(contour)

        level = MetricBar("Level", LEVEL_FLOOR, 0, "Quiet", "Loud", "{:.0f} dBFS, uncalibrated")
        level.set_value(data['level'])
        layout.addWidget(level)

        self.setLayout(layout)

    def play_audio(self):
        sd.play(self.audio_data, SAMPLE_RATE)


class MainWindow(QMainWindow):
    def __init__(self, engine):
        super().__init__()
        self.setWindowTitle("Voice Lab")
        self.resize(650, 1000)
        self.setStyleSheet("background-color: #1e1e1e;")

        self.take_count = 0
        self.is_recording = False
        self.engine = engine
        self.analysis_worker = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.live_panel = LivePanel(engine.analyzer.spectrum_freqs)
        self.engine.frame_ready.connect(self.live_panel.update_frame)
        main_layout.addWidget(self.live_panel)

        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.scroll_content)
        scroll.setStyleSheet("border: none;")

        controls = QFrame()
        controls.setStyleSheet("background-color: #252525; border-top: 1px solid #333;")
        controls_layout = QVBoxLayout(controls)

        self.status_label = QLabel("Ready to Record")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #888; font-size: 14px;")

        self.record_btn = QPushButton()
        self.record_btn.setFixedSize(80, 80)
        self.record_btn.setIconSize(QSize(40, 40))
        self.update_record_button_style(recording=False)
        self.record_btn.clicked.connect(self.toggle_recording)

        controls_layout.addWidget(self.status_label)
        controls_layout.addWidget(self.record_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(scroll)
        main_layout.addWidget(controls)

    def update_record_button_style(self, recording):
        if recording:
            self.record_btn.setIcon(qta.icon('fa5s.stop', color='white'))
            self.record_btn.setStyleSheet("QPushButton { background-color: #333; border-radius: 40px; border: 2px solid #555; }")
        else:
            self.record_btn.setIcon(qta.icon('fa5s.microphone', color='white'))
            self.record_btn.setStyleSheet("QPushButton { background-color: #e74c3c; border-radius: 40px; border: 2px solid #c0392b; }")

    def toggle_recording(self):
        if not self.is_recording:
            self.is_recording = True
            self.status_label.setText("Recording...")
            self.status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.update_record_button_style(recording=True)
            self.engine.start_take()
        else:
            self.is_recording = False
            self.status_label.setText("Processing...")
            self.status_label.setStyleSheet("color: #f1c40f;")
            self.update_record_button_style(recording=False)
            self.record_btn.setEnabled(False)
            self.analysis_worker = TakeAnalysisWorker(self.engine.stop_take())
            self.analysis_worker.finished_analysis.connect(self.process_results)
            self.analysis_worker.start()

    def process_results(self, results):
        self.record_btn.setEnabled(True)
        if "error" in results:
            self.status_label.setText(results["error"])
            self.status_label.setStyleSheet("color: #e74c3c;")
            return
        # Calibrate the live formant tracker to the ceiling that fit this take.
        self.engine.analyzer.formant_ceiling = results["formant_ceiling"]
        self.take_count += 1
        if results["delta_f"] is not None:
            self.live_panel.plane.add_take(self.take_count, results["pitch"], results["delta_f"])
        card = TakeCard(results, self.take_count)
        self.scroll_layout.insertWidget(0, card)
        self.status_label.setText("Done")
        self.status_label.setStyleSheet("color: #888;")

    def closeEvent(self, event):
        self.engine.stop()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    engine = LiveEngine()
    window = MainWindow(engine)
    window.show()
    engine.start()
    sys.exit(app.exec())
