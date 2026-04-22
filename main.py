import sys
import numpy as np
import sounddevice as sd
import parselmouth
from parselmouth.praat import call
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QScrollArea, 
                             QProgressBar, QFrame)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor
import qtawesome as qta

# Matplotlib integration
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

# --- CONFIGURATION ---
SAMPLE_RATE = 44100
CHANNELS = 1

# --- PHYSICS RANGES ---
MIN_PITCH, MAX_PITCH_DISP = 70, 300
MIN_F1, MAX_F1 = 300, 900       # jaw/register openness
MIN_F2, MAX_F2 = 700, 2500      # tongue position → brightness
MIN_WEIGHT, MAX_WEIGHT = -35, -5
MIN_CONTOUR, MAX_CONTOUR = 2, 60
MIN_VOL, MAX_VOL = 40, 90

class AudioRecorder(QThread):
    finished_recording = pyqtSignal(np.ndarray)

    def __init__(self):
        super().__init__()
        self.recording = False
        self.audio_data = []

    def run(self):
        self.recording = True
        self.audio_data = []
        def callback(indata, frames, time, status):
            if self.recording:
                self.audio_data.append(indata.copy())
            else:
                raise sd.CallbackStop()
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, callback=callback):
            while self.recording:
                sd.sleep(50)
        if self.audio_data:
            full_recording = np.concatenate(self.audio_data, axis=0)
            self.finished_recording.emit(full_recording)

    def stop(self):
        self.recording = False

class VoiceAnalyzer:
    @staticmethod
    def analyze(audio_data):
        audio_flat = audio_data.flatten()
        if len(audio_flat) == 0: return None

        sound = parselmouth.Sound(audio_flat, sampling_frequency=SAMPLE_RATE)
        call(sound, "Scale intensity...", 70) # Auto-normalize
        
        # 1. TIME SERIES DATA (For Graphs)
        # We need data arrays that match the time domain
        pitch = call(sound, "To Pitch...", 0.0, 75, 500)
        # 5000Hz max formant is better for male/androgynous voices than 5500
        formants = call(sound, "To Formant (burg)...", 0.0, 5, 5000, 0.025, 50) 
        
        # Extract tracks
        t_vals = np.linspace(0, sound.duration, num=100)
        f0_track = []
        f1_track = []
        f2_track = []
        f3_track = []
        
        valid_f1_samples = []
        valid_f2_samples = []

        for t in t_vals:
            # Pitch
            p = call(pitch, "Get value at time...", t, "Hertz", "Linear")
            f0_track.append(p if not np.isnan(p) else np.nan)

            # Formants
            _f1 = call(formants, "Get value at time...", 1, t, "Hertz", "Linear")
            _f2 = call(formants, "Get value at time...", 2, t, "Hertz", "Linear")
            _f3 = call(formants, "Get value at time...", 3, t, "Hertz", "Linear")

            f1_track.append(_f1)
            f2_track.append(_f2)
            f3_track.append(_f3)

            # Only keep voiced frames with valid formants for summary stats
            if not np.isnan(p):
                if not np.isnan(_f1):
                    valid_f1_samples.append(_f1)
                if not np.isnan(_f2):
                    valid_f2_samples.append(_f2)

        # 2. SUMMARY STATS (Median for stability)
        voiced_count = np.sum(~np.isnan(f0_track))
        if voiced_count < 5 or len(valid_f1_samples) < 5:
            return {"error": f"Not enough voiced audio ({int(voiced_count)} voiced frames). Try speaking louder or longer."}

        final_pitch = np.nanmedian(f0_track)
        final_contour = np.nanstd([v for v in f0_track if not np.isnan(v)])
        final_f1 = np.median(valid_f1_samples)
        final_f2 = np.median(valid_f2_samples) if len(valid_f2_samples) >= 5 else None

        # Build voiced-only audio: strip unvoiced frames so LTAS/intensity aren't
        # biased by fricatives, stops, or silence
        num_frames = call(pitch, "Get number of frames")
        voiced_segments = []
        in_voiced = False
        seg_start = 0.0
        for i in range(1, num_frames + 1):
            t = call(pitch, "Get time from frame number...", i)
            v = call(pitch, "Get value in frame...", i, "Hertz")
            is_v = not np.isnan(v)
            if is_v and not in_voiced:
                in_voiced = True
                seg_start = t
            elif not is_v and in_voiced:
                in_voiced = False
                voiced_segments.append((seg_start, t))
        if in_voiced:
            voiced_segments.append((seg_start, sound.duration))

        voiced_chunks = [
            audio_flat[int(s * SAMPLE_RATE):int(e * SAMPLE_RATE)]
            for s, e in voiced_segments
        ]
        voiced_audio = np.concatenate(voiced_chunks) if voiced_chunks else audio_flat
        voiced_sound = parselmouth.Sound(voiced_audio, sampling_frequency=SAMPLE_RATE)
        call(voiced_sound, "Scale intensity...", 70)

        # Weight (Slope) — voiced only
        ltas = call(voiced_sound, "To Ltas...", 100)
        final_weight = call(ltas, "Get slope", 0, 1000, 1000, 4000, "energy")

        # Volume — voiced only
        final_volume = call(voiced_sound, "Get intensity (dB)")

        return {
            "pitch": final_pitch,
            "contour": final_contour,
            "f1": final_f1,
            "f2": final_f2,
            "weight": final_weight,
            "volume": final_volume,
            "audio": audio_flat,
            "tracks": {
                "times": t_vals,
                "f0": f0_track,
                "f1": f1_track,
                "f2": f2_track,
                "f3": f3_track
            }
        }

class AnalysisGraph(FigureCanvasQTAgg):
    def __init__(self, tracks):
        self.fig = Figure(figsize=(5, 3), dpi=100, facecolor='#2b2b2b')
        super().__init__(self.fig)
        self.tracks = tracks
        self.plot_data()

    def plot_data(self):
        t = self.tracks['times']
        f0 = self.tracks['f0']
        f1 = self.tracks['f1']
        f2 = self.tracks['f2']
        f3 = self.tracks['f3']

        # Setup Axes
        gs = self.fig.add_gridspec(2, 1, height_ratios=[1, 2])
        ax_pitch = self.fig.add_subplot(gs[0])
        ax_res = self.fig.add_subplot(gs[1], sharex=ax_pitch)

        # Style
        for ax in [ax_pitch, ax_res]:
            ax.set_facecolor('#1e1e1e')
            ax.tick_params(axis='x', colors='#888')
            ax.tick_params(axis='y', colors='#888')
            for spine in ax.spines.values():
                spine.set_color('#444')

        # Plot Pitch
        ax_pitch.plot(t, f0, color='#3498db', linewidth=2, label='Pitch')
        ax_pitch.set_ylabel("Hz", color='#888', fontsize=8)
        ax_pitch.grid(True, color='#333')
        
        # Plot Formants
        ax_res.plot(t, f3, color='#e74c3c', linewidth=1.5, alpha=0.8, label='F3')
        ax_res.plot(t, f2, color='#f1c40f', linewidth=1.5, alpha=0.8, label='F2')
        ax_res.plot(t, f1, color='#2ecc71', linewidth=1.5, alpha=0.8, label='F1')
        ax_res.set_ylabel("Formants Hz", color='#888', fontsize=8)
        ax_res.grid(True, color='#333')
        
        # Legend
        ax_res.legend(loc='upper right', fontsize='x-small', facecolor='#333', labelcolor='white')

        # Hover Interaction
        self.cursor_line_p = ax_pitch.axvline(x=0, color='white', alpha=0.5)
        self.cursor_line_r = ax_res.axvline(x=0, color='white', alpha=0.5)
        self.text_p = ax_pitch.text(0.02, 0.9, "", transform=ax_pitch.transAxes, color='white', fontsize=8)
        self.text_r = ax_res.text(0.02, 0.9, "", transform=ax_res.transAxes, color='white', fontsize=8)
        
        self.mpl_connect('motion_notify_event', self.on_hover)
        self.fig.tight_layout()

    def on_hover(self, event):
        if event.inaxes:
            x = event.xdata
            # Update visual cursor
            self.cursor_line_p.set_xdata([x])
            self.cursor_line_r.set_xdata([x])
            
            # Find closest index
            idx = (np.abs(self.tracks['times'] - x)).argmin()
            
            # Update Text
            p_val = self.tracks['f0'][idx]
            f1_val = self.tracks['f1'][idx]
            f2_val = self.tracks['f2'][idx]
            f3_val = self.tracks['f3'][idx]
            
            if not np.isnan(p_val):
                self.text_p.set_text(f"Pitch: {int(p_val)} Hz")
            else:
                self.text_p.set_text("Unvoiced")
                
            if not np.isnan(f1_val):
                self.text_r.set_text(f"F1:{int(f1_val)} F2:{int(f2_val)} F3:{int(f3_val)}")
            
            self.draw()

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
        layout.addLayout(self.create_metric_bar(f"Pitch ({int(data['pitch'])} Hz)", data['pitch'], MIN_PITCH, MAX_PITCH_DISP, "Low", "High"))
        layout.addLayout(self.create_metric_bar(f"Register / F1 ({int(data['f1'])} Hz)", data['f1'], MIN_F1, MAX_F1, "Closed", "Open"))
        if data['f2'] is not None:
            layout.addLayout(self.create_metric_bar(f"Brightness / F2 ({int(data['f2'])} Hz)", data['f2'], MIN_F2, MAX_F2, "Dark", "Bright"))
        layout.addLayout(self.create_metric_bar("Tonal Weight", data['weight'], MIN_WEIGHT, MAX_WEIGHT, "Heavy", "Light"))
        layout.addLayout(self.create_metric_bar("Pitch Contour", data['contour'], MIN_CONTOUR, MAX_CONTOUR, "Monotone", "Dynamic"))
        
        self.setLayout(layout)

    def create_metric_bar(self, label_text, value, min_val, max_val, low_label, high_label):
        vbox = QVBoxLayout()
        vbox.setContentsMargins(0, 5, 0, 5)
        
        lbl = QLabel(label_text)
        lbl.setStyleSheet("font-size: 11px; color: #aaa;")
        vbox.addWidget(lbl)
        
        bar_row = QHBoxLayout()
        progress = QProgressBar()
        progress.setTextVisible(False)
        progress.setFixedHeight(8)
        
        if max_val != min_val:
            norm = (value - min_val) / (max_val - min_val)
        else:
            norm = 0.5
            
        clamped = max(0, min(1, norm)) * 100
        progress.setValue(int(clamped))
        
        # Color
        if clamped < 33: color = "#3498db"
        elif clamped < 66: color = "#9b59b6"
        else: color = "#e74c3c"
        progress.setStyleSheet(f"QProgressBar {{ background-color: #444; border-radius: 4px; }} QProgressBar::chunk {{ background-color: {color}; border-radius: 4px; }}")
        
        bar_row.addWidget(QLabel(low_label))
        bar_row.addWidget(progress)
        bar_row.addWidget(QLabel(high_label))
        vbox.addLayout(bar_row)
        return vbox

    def play_audio(self):
        sd.play(self.audio_data, SAMPLE_RATE)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voice Lab - Debugger")
        self.resize(600, 900)
        self.setStyleSheet("background-color: #1e1e1e;")
        
        self.take_count = 0
        self.is_recording = False 
        self.recorder = AudioRecorder()
        self.recorder.finished_recording.connect(self.process_recording)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
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
            self.record_btn.setFixedSize(80, 80)
            self.record_btn.setIconSize(qta.QtCore.QSize(40, 40))

    def toggle_recording(self):
        if not self.is_recording:
            self.is_recording = True
            self.status_label.setText("Recording...")
            self.status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.update_record_button_style(recording=True)
            self.recorder.start()
        else:
            self.is_recording = False
            self.status_label.setText("Processing...")
            self.status_label.setStyleSheet("color: #f1c40f;")
            self.update_record_button_style(recording=False)
            self.recorder.stop()

    def process_recording(self, audio_data):
        try:
            results = VoiceAnalyzer.analyze(audio_data)
            if results and "error" not in results:
                self.take_count += 1
                card = TakeCard(results, self.take_count)
                self.scroll_layout.insertWidget(0, card)
                self.status_label.setText("Done")
                self.status_label.setStyleSheet("color: #888;")
            elif results and "error" in results:
                self.status_label.setText(results["error"])
                self.status_label.setStyleSheet("color: #e74c3c;")
            else:
                self.status_label.setText("Analysis failed — check mic input.")
                self.status_label.setStyleSheet("color: #e74c3c;")
        except Exception as e:
            print(f"Error: {e}")
            self.status_label.setText(f"Error: {e}")
            self.status_label.setStyleSheet("color: #e74c3c;")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())