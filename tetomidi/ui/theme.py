"""Colors and palettes sampled from the tetoMIDI reference design."""

PAGE_BG = "#f3f4f6"       # very light grey outside the app frame
SIDEBAR_BG = "#000000"
MAIN_BG = "#211f20"       # dark grey work area
ROLL_BG = "#040507"
TEXT = "#ffffff"
TEXT_SOFT = "#e4e4e4"
TEXT_MUTED = "#a3a1a2"
TEXT_FAINT = "#6f6d6e"
PILL = "#000000"
PILL_HOVER = "#2a2829"
PILL_PRESSED = "#3a3738"
PLAY = "#56bb6b"
PLAY_HOVER = "#6acb7e"
PAUSE = "#e0a13a"
PAUSE_HOVER = "#ecb255"
STOP = "#ea473e"
STOP_HOVER = "#f05a52"
NAV_HOVER = "#161516"
NAV_ACTIVE = "#1f1d1e"
ACCENT = "#5492c3"
ERROR = "#ff7b72"
WARN = "#e0a13a"
KEY_WHITE = "#e6e6e6"
KEY_WHITE_EDGE = "#8f8f8f"
KEY_BLACK = "#2b2b2b"
LANE_LINE = "#0c1016"
KEY_LABEL_WHITE = "#2a2a2a"        # letter printed on a white piano key
KEY_LABEL_BLACK = "#f0f0f0"        # ... on a black key
KEY_LABEL_CTRL_WHITE = "#1f5f99"   # keys that are pressed with CTRL (88-key range outside 36-96)
KEY_LABEL_CTRL_BLACK = "#8cc3ea"
PAD = "#15181e"

# name -> (note body, note edge, lit key / active note)
NOTE_PALETTES = {
    "blue": ("#4b84af", "#8cc3ea", "#5492c3"),
    "cyan": ("#2fa6b8", "#8aeaf5", "#35c0d4"),
    "green": ("#4aa86a", "#a0e8b8", "#56bb6b"),
    "pink": ("#b85a8c", "#f2aad0", "#d46aa5"),
    "amber": ("#b88a3a", "#f2d594", "#d9a441"),
    "azure": ("#2b6fd6", "#9cc8ff", "#4c9bff"),   # bright blue for the falling notes
}

# mini player (colours from the reference screenshot: lime play button, dark round controls)
LIME = "#d4f050"
LIME_HOVER = "#e4ff78"
LIME_GLYPH = "#101010"
MINI_PLAY = "#3b3a3c"          # play / pause button of the mini player: charcoal, not pitch black
MINI_PLAY_HOVER = "#4b4a4d"
MINI_PLAY_GLYPH = "#f2f2f2"
CONTROL = "#1a191a"
CONTROL_HOVER = "#2e2c2d"
CONTROL_GLYPH = "#a9a7a8"
CONSOLE_BG = "#0b0a0b"
CONSOLE_TEXT = "#e6e6e6"
