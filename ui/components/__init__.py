"""VoiceLab's component kit: themed Qt widgets for the redesigned UI (see ui/gallery.py).

One module per family: layout, text, data, chips, controls, feedback and moments; base holds the
shared plumbing. Everything is re-exported here, so screens import from ui.components.
"""
from ui.components.base import Label, ThemedWidget
from ui.components.chips import Badge, Chip, ConfidenceChip, StatusDot
from ui.components.controls import (Button, IconButton, NavRail, RecordButton, SegmentedControl, Select,
                                    Tabs, Toggle)
from ui.components.data import BigNumber, MetricCard, ShareBar, Sparkline, ZoneBar
from ui.components.feedback import (ActivityCenter, Banner, EmptyState, ProgressStrip, Skeleton, Spinner,
                                    Toast, ToastStack)
from ui.components.layout import Card, Collapsible, Divider, FlowGrid, Section, WrapRow
from ui.components.moments import MomentChip
from ui.components.text import Caption, ElidedLabel, HelpTip, Headline

__all__ = [
    "Label", "ThemedWidget",
    "Card", "Section", "Collapsible", "FlowGrid", "WrapRow", "Divider",
    "Headline", "Caption", "HelpTip", "ElidedLabel",
    "ZoneBar", "MetricCard", "BigNumber", "ShareBar", "Sparkline",
    "ConfidenceChip", "Chip", "Badge", "StatusDot",
    "Button", "IconButton", "SegmentedControl", "Tabs", "RecordButton", "NavRail", "Toggle", "Select",
    "Spinner", "ProgressStrip", "ActivityCenter", "Skeleton", "Toast", "ToastStack", "Banner", "EmptyState",
    "MomentChip",
]
