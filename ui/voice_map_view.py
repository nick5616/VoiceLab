"""The Voice Map view: draws voice_map.MapSession's grid for the chosen metric, with Rebuild and Clear."""
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QMessageBox, QVBoxLayout, QWidget

from voice_map import HINT, REBUILD_TOOLTIP, range_change_text
from ui import theme
from ui.components import Button, Caption, Collapsible, Headline, Label, Select
from ui.live_graph import style_plot
from ui.practice.widgets import SizedPlot
from ui.takes import listen

THIN_TIP = "Thin means very little voice backs that note, so it may be noise rather than a note you sang."
MORE_HEADER_PX = 28             # px: the click target of "More options"
CLEAR_QUESTION = "Clear the Voice Map? Everything it has gathered is forgotten."


class VoiceMapView(QWidget):
    """Pitch x level map of the core's MapSession; "Rebuild from takes" asks the host (rebuild_requested)."""
    rebuild_requested = pyqtSignal()

    def __init__(self, core, confirm=None):
        super().__init__()
        self.core = core
        self.confirm = confirm or (lambda text: QMessageBox.question(self, "Clear Voice Map", text)
                                   == QMessageBox.StandardButton.Yes)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.XL)
        left = QVBoxLayout()
        left.setSpacing(theme.M)
        self.range_label = Headline("", level="heading")
        self.support_label = Caption("", tone="secondary")
        self.support_label.setToolTip(THIN_TIP)
        self.change_label = Label("", role="body", tone="accent", wrap=True)
        self.range_now = None       # (low, high) MIDI of the range as last drawn
        self.metric = Select()
        self.update_metric_choices()
        self.metric.currentIndexChanged.connect(self.redraw)
        self.scale_label = Caption("", tone="secondary")
        hint = Caption(HINT)
        rebuild_btn = Button("Rebuild from takes", variant="secondary", icon="ph.arrows-clockwise")
        rebuild_btn.setToolTip(REBUILD_TOOLTIP)
        rebuild_btn.clicked.connect(self.rebuild_requested)
        self.more = Collapsible("More options", key="practice.map_more")
        self.more.header.setMinimumHeight(MORE_HEADER_PX)
        clear_btn = Button("Clear map", variant="secondary", icon="ph.trash")
        clear_btn.setToolTip("Forget everything the map has gathered. You will be asked first.")
        clear_btn.clicked.connect(self.clear)
        self.more.add(clear_btn)
        left.addWidget(self.range_label)
        left.addWidget(self.support_label)
        left.addWidget(self.change_label)
        left.addWidget(Label("Colour shows", role="label", tone="muted"))
        left.addWidget(self.metric)
        left.addWidget(self.scale_label)
        buttons = QHBoxLayout()
        buttons.addWidget(rebuild_btn)
        buttons.addStretch(1)
        left.addLayout(buttons)
        left.addWidget(self.more)
        left.addStretch(1)
        left.addWidget(hint)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(320)
        layout.addWidget(left_widget)

        self.plot = SizedPlot()
        self.image = pg.ImageItem(np.zeros(self.session.data.counts.shape))
        lut = pg.colormap.get("viridis").getLookupTable(nPts=256, alpha=True)
        lut[0] = (0, 0, 0, 0)   # index 0 = empty cell, drawn transparent
        self.image.setLookupTable(lut)
        self.plot.addItem(self.image)
        layout.addWidget(self.plot, 1)
        theme.changed.connect(self.restyle)
        self.restyle()
        self.redraw()
        self.change_label.hide()
        listen(self, core, {"voice_map_changed": self._changed})

    def restyle(self, *_):
        t = theme.current()
        self.plot.setBackground(t["surface"])
        style_plot(self.plot.plotItem, left_width=56)
        self.plot.getAxis("left").setLabel("Loudness (relative)", color=t["text_muted"])

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
        self.metric.set_options(choices, current)
        if self.metric.currentIndex() < 0:
            self.metric.setCurrentIndex(0)
        if self.metric.currentData() != current and hasattr(self, "image"):
            self.redraw()

    def frame_added(self, added):
        """After the core counted a live frame: redraw now and then while shown."""
        if added:
            self.update_metric_choices()
            if self.session.due() and self.isVisible():
                self.redraw()

    def redraw(self, *_):
        self.update_metric_choices()
        grid = self.session.grid(self.metric.currentData())
        self.image.setImage(grid["values"], levels=grid["levels"], autoLevels=False)
        self.image.setRect(QRectF(*grid["rect"]))
        self.plot.getAxis("bottom").setTicks([grid["ticks"]])
        self.scale_label.setText(grid["scale_text"])
        head, _, support = grid["range_text"].partition("\n")
        self.range_label.setText(head)
        self.support_label.setText(support)
        self.support_label.setVisible(bool(support))
        self.range_now = self.data.range_midi()
        self.plot.setXRange(*grid["x_range"], padding=0)
        self.plot.setYRange(*grid["y_range"], padding=0)

    def showEvent(self, event):
        self.redraw()
        super().showEvent(event)

    def clear(self):
        if self.confirm(CLEAR_QUESTION):
            self.core.clear_voice_map()

    def _changed(self):
        before = self.range_now
        self.update_metric_choices()
        self.redraw()
        self.change_label.setText(range_change_text(before, self.range_now))
        self.change_label.show()

    def save(self):
        self.session.save()

