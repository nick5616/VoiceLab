"""Design tokens for VoiceLab's UI: colours (dark and light), zone, register and series colours,
spacing, radii, elevation and type, plus the app stylesheet and a hook that fires on theme change.

Every colour pairing that carries text is checked in tests/test_ui_components.py (WCAG 2.1 AA).
Zone, register and series colours were validated with the dataviz skill's palette method
(OKLab Delta E x100 under Machado 2009 protan/deutan simulation); the numbers are in ZONE_NOTES.
"""
import os

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette

# --- COLOURS ---
# Slate greys with a faint blue cast, like Voice Tools. Each step is a distinct job: bg (window),
# surface (cards), surface_raised (popups, chips, inputs), border (hairlines, decorative),
# border_strong (input outlines; >= 3:1 against surface, WCAG 1.4.11).
DARK = {
    "bg": "#15181d",
    "surface": "#1d2129",
    "surface_raised": "#272c36",
    "border": "#333a46",
    "border_strong": "#6b768b",
    "text": "#eef1f6",
    "text_secondary": "#b7bfcc",
    "text_muted": "#929cab",            # >= 4.5:1 on every surface: captions are small text
    "accent": "#9590f2",                # violet: away from the pink/blue zones and the teal registers
    "accent_text": "#16142e",           # text on an accent fill
    "accent_soft": "#2f2d4f",           # selected rows, hover washes
    "success": "#4cc38a",
    "warning": "#e8b04a",
    "error": "#f0716b",
    "info": "#5cc3d6",
    "on_target": "#4cc38a",
    "off_target": "#f0a050",            # amber, not red: off target is information, not an error
    "recording": "#ff5a5f",
    "shadow": "#000000",
    "scrim": "#0b0d10",
}
LIGHT = {
    "bg": "#eceff3",
    "surface": "#f8f9fb",
    "surface_raised": "#ffffff",
    "border": "#d8dde5",
    "border_strong": "#858fa0",
    "text": "#171b23",
    "text_secondary": "#454e5c",
    "text_muted": "#5d6676",
    "accent": "#5a50d0",
    "accent_text": "#ffffff",
    "accent_soft": "#e6e4fa",
    "success": "#1a7f4e",
    "warning": "#9a6400",
    "error": "#c63a3a",
    "info": "#0f7488",
    "on_target": "#1a7f4e",
    "off_target": "#a85a00",
    "recording": "#d93a3f",
    "shadow": "#1b2230",
    "scrim": "#e3e7ec",
}

# Masculine / overlap / feminine is a diverging scale: two poles that read as opposite (cool blue,
# warm pink) and a neutral midpoint with a faint lavender cast that sits closest to the surface,
# so it reads as "neither" (dataviz diverging rule: no hue at the midpoint).
#   fill: the zone's band, a wash. Pastel in light mode, deep in dark mode (the dark-mode
#         equivalent of a pastel). Identity never rests on the fill alone: every band carries its
#         text label, bands are split by 2 px surface gaps, and the edge colour marks swatches.
#   edge: the zone as a mark (legend swatch, chip dot, graph boundary): >= 3:1 on the surface.
#         The overlap edge is a light (dark mode) or dark (light mode) neutral, so it differs from
#         both poles in lightness and survives protan/deutan vision (pink alone collapses to grey).
#   text: the zone label drawn on its fill: >= 4.5:1 on it.
# Chosen by search over OKLCH steps with the dataviz validator; results in ZONE_NOTES.
ZONE_COLORS = {
    "dark": {
        "masculine": {"fill": "#3e5f8a", "edge": "#5591dd", "text": "#e3edfb"},   # edge OKLCH 0.65/0.13/255
        "overlap": {"fill": "#2a282e", "edge": "#c7bfd8", "text": "#d4cde2"},     # edge OKLCH 0.82/0.035/300
        "feminine": {"fill": "#814a60", "edge": "#cc6f9d", "text": "#fbe5ee"},    # edge OKLCH 0.66/0.13/350
        "neutral": {"fill": "#2a303b", "edge": "#6b768b", "text": "#d2d8e2"},     # zones with other ids
    },
    "light": {
        "masculine": {"fill": "#b3d4ff", "edge": "#316cb5", "text": "#163a66"},   # edge OKLCH 0.53/0.13/255
        "overlap": {"fill": "#f9f7ff", "edge": "#453e53", "text": "#453e53"},     # edge OKLCH 0.38/0.035/300
        "feminine": {"fill": "#f1b8cd", "edge": "#b1537b", "text": "#5e2139"},    # edge OKLCH 0.57/0.13/355
        "neutral": {"fill": "#e6e9ee", "edge": "#858fa0", "text": "#353d4a"},
    },
}
# Validator results (dataviz scripts/validate_palette.py, OKLab dE x100, Machado 2009 severity 1;
# CVD target 8 / floor 6 with secondary encoding, normal-vision floor 15):
ZONE_NOTES = {
    "dark": "edges, all pairs: CVD dE 9.9, normal 19.1; fills, adjacent: CVD 16.9, normal 21.1",
    "light": "edges, all pairs: CVD dE 9.3, normal 18.5; fills, adjacent: CVD 11.6, normal 13.5 "
             "(pastel washes; labels and gaps carry identity)",
}
ZONE_LABELS = {"masculine": "typically masculine", "overlap": "overlap / androgynous",
               "feminine": "typically feminine"}      # spec 02's display labels
ZONE_SHORT = {"masculine": "Masculine", "overlap": "Overlap", "feminine": "Feminine"}

# Registers are ordered (M1 chest -> mix -> M2 head), so they take one hue in lightness steps
# (dataviz: ordinal = one hue, monotone L): deeper teal = heavier, paler = lighter. Teal keeps
# them apart from the pink/blue zones. Fry sits outside the order and is a plain grey.
REGISTER_COLORS = {
    "dark": {"chest": "#00847e", "mix": "#33a6a0", "head": "#73d1ca", "fry": "#7d8594"},   # L .55/.66/.80
    "light": {"chest": "#005652", "mix": "#00847e", "head": "#43b2ac", "fry": "#9aa1ad"},  # L .40/.55/.70
}
# Graph series. Pitch is drawn over the zone bands, so it wears ink rather than a hue; formants
# are the dataviz reference palette's first three slots (validated all-pairs in both modes);
# the compare overlay is the de-emphasis grey.
SERIES_COLORS = {
    "dark": {"pitch": "#eef1f6", "f1": "#3987e5", "f2": "#d95926", "f3": "#199e70",
             "compare": "#8e97a6", "target": "#4cc38a"},
    "light": {"pitch": "#1f2633", "f1": "#2a78d6", "f2": "#eb6834", "f3": "#1baf7a",
              "compare": "#8a93a3", "target": "#1a7f4e"},
}

# --- SPACE, SHAPE, ELEVATION ---
SPACE = (4, 8, 12, 16, 24, 32)          # px; the only gaps and paddings components use
XS, S, M, L, XL, XXL = SPACE
RADIUS = {"small": 4, "medium": 8, "large": 12, "pill": 999}    # px: chips, controls, cards
# Elevation is a border in dark mode (shadows vanish on dark grey) and a soft shadow in light:
# level -> (border token or None, shadow blur px, shadow y px, shadow alpha 0-255).
ELEVATION = {
    0: (None, 0, 0, 0),                 # flush with its parent
    1: ("border", 0, 0, 0),             # cards
    2: ("border", 18, 4, 60),           # popovers, toasts
}

# --- TYPE ---
# Windows system font; Segoe UI Variable on Windows 11, Segoe UI before. Sizes are logical px,
# so they scale with the display (150% at 4K). Body 14 px follows the Windows type ramp
# (Fluent "Body" 14 px; unverified) and keeps desktop density.
FONT_FAMILIES = {
    "text": ["Segoe UI Variable Text", "Segoe UI", "Arial"],
    "display": ["Segoe UI Variable Display", "Segoe UI", "Arial"],
}
TYPE = {                                # role: (px, weight, family)
    "display": (30, 600, "display"),    # one big plain sentence per screen
    "title": (22, 600, "display"),
    "heading": (16, 600, "text"),
    "body": (14, 400, "text"),
    "label": (13, 600, "text"),         # buttons, tabs, overlines
    "caption": (12, 400, "text"),
    "numeric": (14, 400, "text"),       # tabular figures, for columns and live readouts
    "figure": (28, 600, "display"),     # a big standalone number (BigNumber, card values)
}
FONT_FILES = ("segoeui.ttf", "seguisb.ttf", "segoeuib.ttf", "segoeuil.ttf")  # loaded when the
#                                       platform has no font database (offscreen screenshots)

# --- STATE ---
THEMES = ("dark", "light")
reduce_motion = os.environ.get("VOICELAB_REDUCE_MOTION", "") not in ("", "0")  # anim helpers skip to the end


class _Hub(QObject):
    changed = pyqtSignal(str)


_hub = _Hub()
changed = _hub.changed                  # changed.connect(fn): fn(name) after every theme switch
_state = {"requested": "dark", "app": None, "fonts": False, "watching": False}


class Theme:
    """One resolved theme: colour tokens by key (theme["text"]), plus zone, register and series colours."""

    def __init__(self, name):
        self.name = name
        self.dark = name == "dark"
        self.colors = dict(DARK if self.dark else LIGHT)
        self.zones = ZONE_COLORS[name]
        self.register = REGISTER_COLORS[name]
        self.series = SERIES_COLORS[name]

    def __getitem__(self, key):
        return self.colors[key]

    def q(self, key, alpha=None):
        """A token as a QColor, optionally with alpha 0-255."""
        color = QColor(self.colors[key])
        if alpha is not None:
            color.setAlpha(alpha)
        return color

    def zone(self, zone_id):
        """fill / edge / text for a zone id; unknown ids get the neutral zone."""
        return self.zones.get(zone_id) or self.zones["neutral"]


_current = Theme("dark")


def current():
    """The active Theme."""
    return _current


def color(key, alpha=None):
    """A colour token of the active theme as a QColor."""
    return _current.q(key, alpha)


def system_scheme():
    """'dark' or 'light' from the OS; dark when unknown (VoiceLab's default)."""
    app = QGuiApplication.instance()
    if app is None:
        return "dark"
    return "light" if app.styleHints().colorScheme() == Qt.ColorScheme.Light else "dark"


def set_theme(name):
    """Switch to 'dark', 'light' or 'system'; restyles the app and fires `changed` when it differs."""
    global _current
    if name not in THEMES + ("system",):
        raise ValueError(f"unknown theme {name!r}")
    _state["requested"] = name
    resolved = system_scheme() if name == "system" else name
    if resolved == _current.name:
        return _current
    _current = Theme(resolved)
    if _state["app"] is not None:
        _apply_to(_state["app"])
    changed.emit(resolved)
    return _current


def set_reduce_motion(on):
    """Turn motion off (anim helpers jump to their end state) or back on."""
    global reduce_motion
    reduce_motion = bool(on)


def ensure_fonts():
    """Load Segoe UI from the Windows fonts folder when Qt sees no system fonts (offscreen runs)."""
    if _state["fonts"] or QGuiApplication.instance() is None:
        return
    _state["fonts"] = True
    if any(f.startswith("Segoe UI") for f in QFontDatabase.families()):
        return
    folder = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in FONT_FILES:
        path = os.path.join(folder, name)
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)


def font(role="body", size=None, weight=None):
    """A QFont for a type role; `size` (px) and `weight` override the role's."""
    ensure_fonts()
    px, w, family = TYPE[role]
    f = QFont()
    f.setFamilies(FONT_FAMILIES[family])
    f.setPixelSize(size or px)
    f.setWeight(QFont.Weight(weight or w))
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if role == "numeric" and hasattr(QFont, "setFeature"):
        f.setFeature(QFont.Tag("tnum"), 1)
    return f


def palette():
    """A QPalette from the active theme, for stock Qt widgets."""
    c, pal = _current, QPalette()
    roles = QPalette.ColorRole
    for role, key in ((roles.Window, "bg"), (roles.Base, "surface_raised"), (roles.AlternateBase, "surface"),
                      (roles.WindowText, "text"), (roles.Text, "text"), (roles.ButtonText, "text"),
                      (roles.Button, "surface_raised"), (roles.ToolTipBase, "surface_raised"),
                      (roles.ToolTipText, "text"), (roles.Highlight, "accent"),
                      (roles.HighlightedText, "accent_text"), (roles.PlaceholderText, "text_muted"),
                      (roles.Link, "accent"), (roles.Mid, "border"), (roles.Dark, "bg")):
        pal.setColor(role, c.q(key))
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, c.q("text_muted"))
    return pal


def qss():
    """The app-wide stylesheet for stock widgets (tooltips, menus, inputs, scroll bars). Fonts
    come from the app font, not from here: a stylesheet font would override components' fonts."""
    c = _current.colors
    r = RADIUS
    return f"""
QToolTip {{ background: {c['surface_raised']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: {r['small']}px; padding: {XS}px {S}px; }}
QMenu {{ background: {c['surface_raised']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: {r['medium']}px; padding: {XS}px; }}
QMenu::item {{ padding: {S - 2}px {L}px; border-radius: {r['small']}px; }}
QMenu::item:selected {{ background: {c['accent_soft']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: {XS}px {S}px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {c['border']}; border-radius: 3px; min-height: 32px; min-width: 32px; }}
QScrollBar::handle:hover {{ background: {c['border_strong']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{ background: {c['surface_raised']};
    color: {c['text']}; border: 1px solid {c['border_strong']}; border-radius: {r['medium']}px;
    padding: {XS + 1}px {S}px; selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {c['accent']}; }}
QComboBox {{ background: {c['surface_raised']}; color: {c['text']}; border: 1px solid {c['border_strong']};
    border-radius: {r['medium']}px; padding: {XS + 1}px {S}px; min-height: 22px; }}
QComboBox:hover {{ border-color: {c['text_muted']}; }}
QComboBox:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['surface_raised']}; color: {c['text']};
    border: 1px solid {c['border']}; selection-background-color: {c['accent_soft']};
    selection-color: {c['text']}; outline: none; }}
QPushButton {{ background: {c['surface_raised']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: {r['medium']}px; padding: {S - 2}px {M}px; }}
QPushButton:hover {{ border-color: {c['border_strong']}; }}
QPushButton:disabled {{ color: {c['text_muted']}; }}
QCheckBox, QRadioButton, QLabel {{ color: {c['text']}; background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['accent']}; width: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
"""


def _apply_to(app):
    app.setPalette(palette())
    app.setStyleSheet(qss())


def apply(app=None):
    """Give the app the theme's font, palette and stylesheet, and keep them in step with set_theme."""
    app = app or QGuiApplication.instance()
    ensure_fonts()
    app.setFont(font("body"))
    _state["app"] = app
    _apply_to(app)
    if not _state["watching"] and hasattr(app.styleHints(), "colorSchemeChanged"):
        _state["watching"] = True
        app.styleHints().colorSchemeChanged.connect(_os_scheme_changed)


def _os_scheme_changed(*_):
    if _state["requested"] == "system":
        set_theme("system")
