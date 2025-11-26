import sys
import time
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

# Physics thresholds
MIN_PITCH, MAX_PITCH = 70, 400
MIN_RESONANCE, MAX_RESONANCE = 800, 1400
MIN_WEIGHT, MAX_WEIGHT = -10, -35

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
        # Flatten audio to 1D array
        audio_flat = audio_data.flatten()
        if len(audio_flat) == 0: return None

        # Create Sound object
        sound = parselmouth.Sound(audio_flat, sampling_frequency=SAMPLE_RATE)
        
        # 1. PITCH (Using call)
        # "To Pitch...": timeStep (0=auto), minPitch, maxPitch
        pitch = call(sound, "To Pitch...", 0.0, MIN_PITCH, MAX_PITCH)
        mean_f0 = call(pitch, "Get mean", 0, 0, "Hertz")
        
        if np.isnan(mean_f0): return None

        # 2. RESONANCE (Using call)
        # "To Formant (burg)...": timeStep, maxFormants, maxHz, window, preEmph
        formants = call(sound, "To Formant (burg)...", 0.0, 5, 5500, 0.025, 50)
        midpoint = sound.duration / 2
        
        f1 = call(formants, "Get value at time", 1, midpoint, "Hertz", "Linear")
        f2 = call(formants, "Get value at time", 2, midpoint, "Hertz", "Linear")
        f3 = call(formants, "Get value at time", 3, midpoint, "Hertz", "Linear")
        
        if np.isnan(f1) or np.isnan(f2) or np.isnan(f3):
            dispersion = 1000 
        else:
            dispersion = ((f2 - f1) + (f3 - f2)) / 2

        # 3. WEIGHT (Using call)
        # "To Ltas...": bandwidth
        ltas = call(sound, "To Ltas...", 100)
        # "Get slope": lowFreq, highFreq, energyLow, energyHigh, unit
        slope = call(ltas, "Get slope", 0, 1000, 1000, 4000, "energy")
        
        return {
            "pitch": mean_f0,
            "resonance": dispersion,  
            "weight": slope,          
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
        
        # Metrics
        layout.addLayout(self.create_metric_bar("Resonance (Brightness)", data['resonance'], MIN_RESONANCE, MAX_RESONANCE, "Dark", "Bright"))
        layout.addLayout(self.create_metric_bar("Weight (Buzz)", data['weight'], MAX_WEIGHT, MIN_WEIGHT, "Soft", "Heavy"))
        
        # Pitch Badge
        pitch_val = int(data['pitch'])
        pitch_lbl = QLabel(f"Avg Pitch: {pitch_val} Hz")
        pitch_lbl.setStyleSheet("color: #aaaaaa; font-style: italic;")
        layout.addWidget(pitch_lbl)
        
        self.setLayout(layout)

    def create_metric_bar(self, label_text, value, min_val, max_val, low_label, high_label):
        vbox = QVBoxLayout()
        lbl_row = QHBoxLayout()
        lbl_row.addWidget(QLabel(label_text))
        lbl_row.addStretch()
        vbox.addLayout(lbl_row)
        
        bar_row = QHBoxLayout()
        bar_row.addWidget(QLabel(low_label))
        
        progress = QProgressBar()
        progress.setTextVisible(False)
        progress.setFixedHeight(10)
        
        # Normalize logic
        if min_val < max_val:
            norm = (value - min_val) / (max_val - min_val)
        else:
            norm = (value - min_val) / (max_val - min_val)
            
        clamped = max(0, min(1, norm)) * 100
        progress.setValue(int(clamped))
        
        # Colors
        if clamped < 30: color = "#3498db"
        elif clamped > 70: color = "#e74c3c"
        else: color = "#9b59b6"
            
        progress.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color}; border-radius: 5px; }}")
        
        bar_row.addWidget(progress)
        bar_row.addWidget(QLabel(high_label))
        vbox.addLayout(bar_row)
        
        return vbox

    def play_audio(self):
        sd.play(self.audio_data, SAMPLE_RATE)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voice Lab MVP")
        self.resize(500, 700)
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
        
        # TOGGLE BUTTON
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
                self.status_label.setText("No voice detected. Try again.")
                
        except Exception as e:
            print(f"Error detail: {e}")
            self.status_label.setText("Error during analysis")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())