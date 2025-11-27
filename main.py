import sys
import numpy as np
import sounddevice as sd
import parselmouth
from parselmouth.praat import call
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QScrollArea, 
                             QProgressBar, QFrame)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
import qtawesome as qta

# --- CONFIGURATION ---
SAMPLE_RATE = 44100
CHANNELS = 1

# --- PHYSICS RANGES (TUNED) ---
# Pitch: Human voice fundamental range
MIN_PITCH, MAX_PITCH_DISP = 80, 300
PITCH_FLOOR, PITCH_CEILING = 75, 500

# Resonance: Average of F1+F2+F3
# Lower = Darker, Higher = Brighter
MIN_RES, MAX_RES = 1000, 1600     

# Weight: Spectral Slope (dB per octave)
# -35dB (Steep drop-off) = Light/Breathy
# -5dB (Flat) = Heavy/Buzzy
MIN_WEIGHT, MAX_WEIGHT = -35, -5  

# Contour: Standard Deviation of Pitch
MIN_CONTOUR, MAX_CONTOUR = 5, 50

# Volume: We normalize to 70dB, so this is just for display
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

        # 1. PRE-PROCESS: CREATE SOUND
        sound = parselmouth.Sound(audio_flat, sampling_frequency=SAMPLE_RATE)
        
        # 2. AUTO-NORMALIZE VOLUME
        # This fixes "Weight" varying just because you leaned back.
        # We scale intensity to 70dB standard.
        call(sound, "Scale intensity...", 70)
        
        # 3. GENERATE OBJECTS
        pitch_obj = call(sound, "To Pitch...", 0.0, PITCH_FLOOR, PITCH_CEILING)
        formant_obj = call(sound, "To Formant (burg)...", 0.0, 5, 5500, 0.025, 50)
        ltas_obj = call(sound, "To Ltas...", 100)
        
        # 4. THE "VOWEL HUNTER" LOOP
        # We iterate through frames to find "Voiced" segments only.
        
        frames_f0 = []
        frames_res = []
        
        # Check every 20ms
        duration = sound.duration
        time_step = 0.02 
        times = np.arange(0, duration, time_step)
        
        for t in times:
            # Get Pitch at this moment
            f0 = call(pitch_obj, "Get value at time...", t, "Hertz", "Linear")
            
            # IS IT VOICED? (Not NaN and not 0)
            if not np.isnan(f0) and f0 > 0:
                frames_f0.append(f0)
                
                # Get Resonance (F1/F2/F3) ONLY if voiced
                f1 = call(formant_obj, "Get value at time...", 1, t, "Hertz", "Linear")
                f2 = call(formant_obj, "Get value at time...", 2, t, "Hertz", "Linear")
                f3 = call(formant_obj, "Get value at time...", 3, t, "Hertz", "Linear")
                
                if not np.isnan(f1) and not np.isnan(f2) and not np.isnan(f3):
                    # Average of top 3 formants captures the "Color" well
                    frames_res.append((f1 + f2 + f3) / 3)

        # 5. CALCULATE ROBUST METRICS (Median)
        if len(frames_f0) < 5: 
            return None # Not enough valid voice data
            
        final_pitch = np.median(frames_f0)
        final_contour = np.std(frames_f0)
        final_resonance = np.median(frames_res)
        
        # Weight (Spectral Slope)
        # Note: Ltas includes silence, but since we normalized volume, it's safer.
        # Ideally we'd splice audio, but this is computationally cheaper and stable enough with normalization.
        final_weight = call(ltas_obj, "Get slope", 0, 1000, 1000, 4000, "energy")
        
        # Volume (Raw intensity before we normalized it, for user feedback)
        # We create a temp sound to check original volume
        raw_sound = parselmouth.Sound(audio_flat, sampling_frequency=SAMPLE_RATE)
        final_volume = call(raw_sound, "Get intensity (dB)")

        return {
            "pitch": final_pitch,
            "contour": final_contour,
            "resonance": final_resonance,
            "weight": final_weight,
            "volume": final_volume,
            "audio": audio_flat
        }

class TakeCard(QFrame):
    def __init__(self, data, count):
        super().__init__()
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setStyleSheet("""
            QFrame { 
                background-color: #2b2b2b; 
                border-radius: 10px; 
                margin-bottom: 10px; 
            }
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
        
        # --- SLIDERS ---
        layout.addLayout(self.create_metric_bar("Pitch (High/Low)", data['pitch'], MIN_PITCH, MAX_PITCH_DISP, "Low", "High"))
        layout.addLayout(self.create_metric_bar("Resonance (Bright/Dark)", data['resonance'], MIN_RES, MAX_RES, "Dark", "Bright"))
        layout.addLayout(self.create_metric_bar("Weight (Heavy/Light)", data['weight'], MIN_WEIGHT, MAX_WEIGHT, "Light", "Heavy"))
        layout.addLayout(self.create_metric_bar("Contour (Expressivity)", data['contour'], MIN_CONTOUR, MAX_CONTOUR, "Monotone", "Dynamic"))
        layout.addLayout(self.create_metric_bar("Input Volume", data['volume'], MIN_VOL, MAX_VOL, "Quiet", "Loud"))
        
        self.setLayout(layout)

    def create_metric_bar(self, label_text, value, min_val, max_val, low_label, high_label):
        vbox = QVBoxLayout()
        vbox.setContentsMargins(0, 5, 0, 5)
        
        lbl = QLabel(f"{label_text}: {int(value)}")
        lbl.setStyleSheet("font-size: 11px; color: #aaa;")
        vbox.addWidget(lbl)
        
        bar_row = QHBoxLayout()
        bar_row.addWidget(QLabel(low_label))
        
        progress = QProgressBar()
        progress.setTextVisible(False)
        progress.setFixedHeight(8)
        
        if min_val < max_val:
            norm = (value - min_val) / (max_val - min_val)
        else:
            norm = (value - min_val) / (max_val - min_val)
            
        clamped = max(0, min(1, norm)) * 100
        progress.setValue(int(clamped))
        
        if clamped < 33: color = "#3498db"
        elif clamped < 66: color = "#9b59b6"
        else: color = "#e74c3c"
            
        progress.setStyleSheet(f"""
            QProgressBar {{ background-color: #444; border-radius: 4px; }}
            QProgressBar::chunk {{ background-color: {color}; border-radius: 4px; }}
        """)
        
        bar_row.addWidget(progress)
        bar_row.addWidget(QLabel(high_label))
        vbox.addLayout(bar_row)
        
        return vbox

    def play_audio(self):
        sd.play(self.audio_data, SAMPLE_RATE)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voice Lab - Vowel Hunter")
        self.resize(600, 800)
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
            self.record_btn.setIconSize(qta.QtCore.QSize(30, 30))
            self.record_btn.setFixedSize(80, 80)
            self.record_btn.setStyleSheet("""
                QPushButton {
                    background-color: #333;
                    border-radius: 40px;
                    border: 2px solid #555;
                }
                QPushButton:hover { background-color: #444; }
            """)
        else:
            self.record_btn.setIcon(qta.icon('fa5s.microphone', color='white'))
            self.record_btn.setIconSize(qta.QtCore.QSize(40, 40))
            self.record_btn.setFixedSize(80, 80)
            self.record_btn.setStyleSheet("""
                QPushButton {
                    background-color: #e74c3c;
                    border-radius: 40px;
                    border: 2px solid #c0392b;
                }
                QPushButton:hover { background-color: #c0392b; }
            """)

    def toggle_recording(self):
        if not self.is_recording:
            self.is_recording = True
            self.status_label.setText("Recording... (Click to Stop)")
            self.status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self.update_record_button_style(recording=True)
            self.recorder.start()
        else:
            self.is_recording = False
            self.status_label.setText("Analyzing...")
            self.status_label.setStyleSheet("color: #f1c40f;")
            self.update_record_button_style(recording=False)
            self.recorder.stop()

    def process_recording(self, audio_data):
        try:
            results = VoiceAnalyzer.analyze(audio_data)
            
            if results:
                self.take_count += 1
                card = TakeCard(results, self.take_count)
                self.scroll_layout.insertWidget(0, card)
                self.status_label.setText("Analysis Complete")
                self.status_label.setStyleSheet("color: #888;")
            else:
                self.status_label.setText("No clear voice detected.")
                
        except Exception as e:
            print(f"Error detail: {e}")
            self.status_label.setText("Error during analysis")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())