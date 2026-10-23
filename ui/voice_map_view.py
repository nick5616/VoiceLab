"""The Voice Map view: draws voice_map.MapSession's grid for the chosen metric."""
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from voice_map import HINT, REBUILD_TOOLTIP
from ui.meters import BUTTON_STYLE


class VoiceMapView(QWidget):
    """Pitch x level map of the core's MapSession; "Rebuild from takes" asks the host (rebuild_requested)."""
    rebuild_requested = pyqtSignal()

    def __init__(self, core):
        super().__init__()
        self.core = core
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        self.metric = QComboBox()
        self.update_metric_choices()
        self.metric.setStyleSheet("QComboBox { background: #1e1e1e; color: white; }")
        self.metric.currentIndexChanged.connect(self.redraw)
        self.range_label = QLabel()
        self.range_label.setWordWrap(True)
        self.range_label.setStyleSheet("color: white; font-size: 13px;")
        hint = QLabel(HINT)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaa; font-size: 11px;")
        clear_btn = QPushButton("Clear map")
        clear_btn.setStyleSheet(BUTTON_STYLE)
        clear_btn.clicked.connect(self.clear)
        rebuild_btn = QPushButton("Rebuild from takes")
        rebuild_btn.setStyleSheet(BUTTON_STYLE)
        rebuild_btn.setToolTip(REBUILD_TOOLTIP)
        rebuild_btn.clicked.connect(self.rebuild_requested)
        self.scale_label = QLabel()
        self.scale_label.setWordWrap(True)
        self.scale_label.setStyleSheet("color: #ddd; font-size: 11px;")
        for w in (self.metric, self.scale_label, self.range_label, hint):
            left.addWidget(w)
        left.addStretch()
        left.addWidget(rebuild_btn)
        left.addWidget(clear_btn)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(230)
        layout.addWidget(left_widget)

        self.plot = pg.PlotWidget(background="#1e1e1e")
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
        self.plot.getAxis("left").setLabel("Level dBFS")
        self.image = pg.ImageItem(np.zeros(self.session.data.counts.shape))
        lut = pg.colormap.get("viridis").getLookupTable(nPts=256, alpha=True)
        lut[0] = (0, 0, 0, 0)   # index 0 = empty cell, drawn transparent
        self.image.setLookupTable(lut)
        self.plot.addItem(self.image)
        layout.addWidget(self.plot)
        self.redraw()
        core.events.subscribe("voice_map_changed", self._changed)

    @property
    def session(self):
        return self.core.voice_map

    @property
    def data(self):
        return self.session.data

    @property
    def mode(self):
        return self.session.mode

    def update_metric_choices(self):
        """Refill the picker if the offered metrics changed, keeping the selection."""
        choices = self.session.metric_choices()
        if [key for key, _ in choices] == [self.metric.itemData(i) for i in range(self.metric.count())]:
            return
        current = self.metric.currentData()
        self.metric.blockSignals(True)
        self.metric.clear()
        for key, label in choices:
            self.metric.addItem(label, key)
        self.metric.setCurrentIndex(max(0, self.metric.findData(current)))
        self.metric.blockSignals(False)
        if self.metric.currentData() != current and hasattr(self, "image"):
            self.redraw()

    def frame_added(self, added):
        """After the core counted a live frame: redraw now and then while shown."""
        if added:
            self.update_metric_choices()
            if self.session.due() and self.isVisible():
                self.redraw()

    def redraw(self):
        self.update_metric_choices()
        grid = self.session.grid(self.metric.currentData())
        self.image.setImage(grid["values"], levels=grid["levels"], autoLevels=False)
        self.image.setRect(QRectF(*grid["rect"]))
        self.plot.getAxis("bottom").setTicks([grid["ticks"]])
        self.scale_label.setText(grid["scale_text"])
        self.range_label.setText(grid["range_text"])
        self.plot.setXRange(*grid["x_range"], padding=0)
        self.plot.setYRange(*grid["y_range"], padding=0)

    def showEvent(self, event):
        self.redraw()
        super().showEvent(event)

    def clear(self):
        self.core.clear_voice_map()

    def _changed(self):
        self.update_metric_choices()
        self.redraw()

    def save(self):
        self.session.save()
