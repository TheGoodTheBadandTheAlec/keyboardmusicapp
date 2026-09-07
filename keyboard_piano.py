"""
Keyboard Piano
--------------
Windows-first, one-file desktop app that turns a computer keyboard into a
polyphonic musical surface.

Default profile: ANSI TKL / Tenkeyless (87-key).

Controls:
    Space          Hold sustain
    GUI toggle     Latch sustain on/off
    Left Shift     Temporary octave down
    Right Shift    Temporary octave up
    Escape         Panic / stop all notes

Features:
    Sample-based SoundFont instruments through FluidSynth
    128 General MIDI programs when a SoundFont is loaded
    Generated-synth fallback if FluidSynth/SoundFont is unavailable
    WAV recording to ./recordings
    10 keyboard profiles

Notes:
- The app uses the `keyboard` package for global key events.
- Inside this app, piano keystrokes are consumed while Capture is ON so they
  cannot accidentally change Scale/Root/Instrument/UI controls.
- It does NOT suppress normal OS shortcuts or typing in OTHER applications.
- Turn Capture OFF before typing elsewhere.
- Some unusual keyboards / ISO layouts may report a few keys differently.
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np
import sounddevice as sd
import keyboard

try:
    import fluidsynth
    FLUIDSYNTH_IMPORT_ERROR = None
except Exception as _fluidsynth_exc:
    fluidsynth = None
    FLUIDSYNTH_IMPORT_ERROR = str(_fluidsynth_exc)

from PySide6.QtCore import QObject, Qt, Signal, QEvent, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# Musical utilities
# ---------------------------------------------------------------------------

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

SCALES = {
    "Chromatic": list(range(12)),
    "Major": [0, 2, 4, 5, 7, 9, 11],
    "Natural Minor": [0, 2, 3, 5, 7, 8, 10],
    "Pentatonic Major": [0, 2, 4, 7, 9],
    "Pentatonic Minor": [0, 3, 5, 7, 10],
}

INSTRUMENTS = [
    "Grand Piano", "Bright Piano", "Electric Piano", "Honky Tonk",
    "Harpsichord", "Clavinet",
    "Hammond Organ", "Church Organ", "Accordion",
    "Nylon Guitar", "Steel Guitar", "Muted Guitar",
    "Marimba", "Vibraphone", "Steel Drum", "Music Box", "Celesta", "Kalimba", "Sitar",
    "String Ensemble", "Choir Pad", "Brass Section", "Trumpet", "Flute", "Clarinet",
    "Warm Pad", "Analog Pad", "Space Pad", "Glass Pad",
    "Synth Lead", "Square Lead", "Saw Lead", "Theremin",
    "Synth Bass", "Reese Bass", "Wobble Bass",
    "Bell", "FM Glass", "Pluck Synth", "8-Bit / Chiptune",
]


GM_INSTRUMENTS = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet",
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics",
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2", "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)",
    "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bag Pipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]

GM_PROGRAM_BY_NAME = {name: i for i, name in enumerate(GM_INSTRUMENTS)}


def midi_name(note: int) -> str:
    note = max(0, min(127, note))
    octave = note // 12 - 1
    return f"{NOTE_NAMES[note % 12]}{octave}"


def scale_note(index: int, root_pc: int, base_midi: int, scale: List[int]) -> int:
    degree_count = len(scale)
    octave = index // degree_count
    degree = index % degree_count
    root_base = base_midi - (base_midi % 12) + root_pc

    # Keep the generated root near the requested base note.
    while root_base < base_midi:
        root_base += 12
    while root_base - 12 >= base_midi:
        root_base -= 12

    return root_base + octave * 12 + scale[degree]


# ---------------------------------------------------------------------------
# Keyboard profile definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KeySpec:
    key_id: str
    label: str
    width: float = 1.0


GAP = KeySpec("__GAP__", "", 0.55)

CONTROL_KEYS = {
    "ESC",
    "SPACE",
    "LSHIFT",
    "RSHIFT",
    "LCTRL",
    "RCTRL",
    "LALT",
    "RALT",
    "LWIN",
    "RWIN",
    "MENU",
}

# Widths are visual only.
WIDE = {
    "BACKSPACE": 2.0,
    "TAB": 1.5,
    "CAPS": 1.75,
    "ENTER": 2.25,
    "LSHIFT": 2.25,
    "RSHIFT": 2.75,
    "LCTRL": 1.3,
    "RCTRL": 1.3,
    "LALT": 1.3,
    "RALT": 1.3,
    "LWIN": 1.3,
    "RWIN": 1.3,
    "MENU": 1.3,
    "SPACE": 6.2,
    "NUM0": 2.0,
    "NUMPLUS": 1.0,
    "NUMENTER": 1.0,
}


def K(key_id: str, label: Optional[str] = None, width: Optional[float] = None) -> KeySpec:
    return KeySpec(key_id, label or key_id, width if width is not None else WIDE.get(key_id, 1.0))


def common_function_row(include_nav3: bool = True):
    row = [K("ESC", "Esc"), GAP]
    row += [K(f"F{i}", f"F{i}") for i in range(1, 5)]
    row += [GAP]
    row += [K(f"F{i}", f"F{i}") for i in range(5, 9)]
    row += [GAP]
    row += [K(f"F{i}", f"F{i}") for i in range(9, 13)]
    if include_nav3:
        row += [GAP, K("PRINT", "Prt"), K("SCROLL", "Scr"), K("PAUSE", "Pse")]
    return row


ANSI_MAIN_ROWS = [
    [
        K("GRAVE", "`"), K("1"), K("2"), K("3"), K("4"), K("5"), K("6"),
        K("7"), K("8"), K("9"), K("0"), K("MINUS", "-"), K("EQUAL", "="),
        K("BACKSPACE", "Backspace"),
    ],
    [
        K("TAB", "Tab"),
        K("Q"), K("W"), K("E"), K("R"), K("T"), K("Y"), K("U"), K("I"),
        K("O"), K("P"), K("LBRACKET", "["), K("RBRACKET", "]"),
        K("BACKSLASH", "\\", 1.5),
    ],
    [
        K("CAPS", "Caps"),
        K("A"), K("S"), K("D"), K("F"), K("G"), K("H"), K("J"), K("K"),
        K("L"), K("SEMICOLON", ";"), K("APOSTROPHE", "'"),
        K("ENTER", "Enter"),
    ],
    [
        K("LSHIFT", "Shift"),
        K("Z"), K("X"), K("C"), K("V"), K("B"), K("N"), K("M"),
        K("COMMA", ","), K("PERIOD", "."), K("SLASH", "/"),
        K("RSHIFT", "Shift"),
    ],
    [
        K("LCTRL", "Ctrl"), K("LWIN", "Win"), K("LALT", "Alt"),
        K("SPACE", "Space"),
        K("RALT", "Alt"), K("RWIN", "Win"), K("MENU", "Menu"), K("RCTRL", "Ctrl"),
    ],
]

ISO_MAIN_ROWS = [
    [
        K("GRAVE", "`"), K("1"), K("2"), K("3"), K("4"), K("5"), K("6"),
        K("7"), K("8"), K("9"), K("0"), K("MINUS", "-"), K("EQUAL", "="),
        K("BACKSPACE", "Backspace"),
    ],
    [
        K("TAB", "Tab"),
        K("Q"), K("W"), K("E"), K("R"), K("T"), K("Y"), K("U"), K("I"),
        K("O"), K("P"), K("LBRACKET", "["), K("RBRACKET", "]"),
    ],
    [
        K("CAPS", "Caps"),
        K("A"), K("S"), K("D"), K("F"), K("G"), K("H"), K("J"), K("K"),
        K("L"), K("SEMICOLON", ";"), K("APOSTROPHE", "'"),
        K("ISO_HASH", "#"), K("ENTER", "Enter", 1.5),
    ],
    [
        K("LSHIFT", "Shift", 1.4), K("ISO_102", "\\"),
        K("Z"), K("X"), K("C"), K("V"), K("B"), K("N"), K("M"),
        K("COMMA", ","), K("PERIOD", "."), K("SLASH", "/"),
        K("RSHIFT", "Shift", 2.3),
    ],
    [
        K("LCTRL", "Ctrl"), K("LWIN", "Win"), K("LALT", "Alt"),
        K("SPACE", "Space"),
        K("RALT", "AltGr"), K("RWIN", "Win"), K("MENU", "Menu"), K("RCTRL", "Ctrl"),
    ],
]


def tkl_rows(iso: bool = False):
    main = ISO_MAIN_ROWS if iso else ANSI_MAIN_ROWS
    return [
        common_function_row(True),
        main[0] + [GAP, K("INSERT", "Ins"), K("HOME", "Home"), K("PAGEUP", "PgUp")],
        main[1] + [GAP, K("DELETE", "Del"), K("END", "End"), K("PAGEDOWN", "PgDn")],
        main[2],
        main[3] + [GAP, GAP, K("UP", "↑")],
        main[4] + [GAP, K("LEFT", "←"), K("DOWN", "↓"), K("RIGHT", "→")],
    ]


def full_rows(iso: bool = False):
    main = ISO_MAIN_ROWS if iso else ANSI_MAIN_ROWS
    return [
        common_function_row(True),
        main[0] + [
            GAP, K("INSERT", "Ins"), K("HOME", "Home"), K("PAGEUP", "PgUp"),
            GAP, K("NUMLOCK", "Num"), K("NUMDIV", "/"), K("NUMMUL", "*"), K("NUMMINUS", "-")
        ],
        main[1] + [
            GAP, K("DELETE", "Del"), K("END", "End"), K("PAGEDOWN", "PgDn"),
            GAP, K("NUM7", "7"), K("NUM8", "8"), K("NUM9", "9"), K("NUMPLUS", "+")
        ],
        main[2] + [
            GAP, GAP, GAP, GAP,
            GAP, K("NUM4", "4"), K("NUM5", "5"), K("NUM6", "6"), K("NUMPLUS2", "+")
        ],
        main[3] + [
            GAP, GAP, K("UP", "↑"), GAP,
            GAP, K("NUM1", "1"), K("NUM2", "2"), K("NUM3", "3"), K("NUMENTER", "Ent")
        ],
        main[4] + [
            GAP, K("LEFT", "←"), K("DOWN", "↓"), K("RIGHT", "→"),
            GAP, K("NUM0", "0"), K("NUMDEC", "."), K("NUMENTER2", "Ent")
        ],
    ]


def compact_75_rows():
    return [
        [K("ESC", "Esc")] + [K(f"F{i}", f"F{i}") for i in range(1, 13)] + [K("DELETE", "Del")],
        ANSI_MAIN_ROWS[0] + [K("HOME", "Home")],
        ANSI_MAIN_ROWS[1] + [K("PAGEUP", "PgUp")],
        ANSI_MAIN_ROWS[2] + [K("PAGEDOWN", "PgDn")],
        ANSI_MAIN_ROWS[3][:-1] + [K("RSHIFT", "Shift", 1.7), K("UP", "↑")],
        [
            K("LCTRL", "Ctrl"), K("LWIN", "Win"), K("LALT", "Alt"),
            K("SPACE", "Space", 5.0), K("RALT", "Alt"),
            K("LEFT", "←"), K("DOWN", "↓"), K("RIGHT", "→")
        ],
    ]


def compact_65_rows():
    return [
        ANSI_MAIN_ROWS[0] + [K("DELETE", "Del")],
        ANSI_MAIN_ROWS[1] + [K("HOME", "Home")],
        ANSI_MAIN_ROWS[2] + [K("PAGEUP", "PgUp")],
        ANSI_MAIN_ROWS[3][:-1] + [K("RSHIFT", "Shift", 1.7), K("UP", "↑")],
        [
            K("LCTRL", "Ctrl"), K("LWIN", "Win"), K("LALT", "Alt"),
            K("SPACE", "Space", 5.0), K("RALT", "Alt"),
            K("LEFT", "←"), K("DOWN", "↓"), K("RIGHT", "→")
        ],
    ]


def compact_60_rows():
    return ANSI_MAIN_ROWS


def compact_96_rows():
    # A compressed full-size arrangement: all core functions are present.
    return [
        [K("ESC", "Esc")] + [K(f"F{i}", f"F{i}") for i in range(1, 13)]
        + [K("DELETE", "Del"), K("HOME", "Home"), K("PAGEUP", "PgUp")],
        ANSI_MAIN_ROWS[0] + [K("NUMLOCK", "Num"), K("NUMDIV", "/"), K("NUMMUL", "*"), K("NUMMINUS", "-")],
        ANSI_MAIN_ROWS[1] + [K("NUM7", "7"), K("NUM8", "8"), K("NUM9", "9"), K("NUMPLUS", "+")],
        ANSI_MAIN_ROWS[2] + [K("NUM4", "4"), K("NUM5", "5"), K("NUM6", "6"), K("NUMPLUS2", "+")],
        ANSI_MAIN_ROWS[3][:-1] + [K("RSHIFT", "Shift", 1.7), K("UP", "↑"),
                                 K("NUM1", "1"), K("NUM2", "2"), K("NUM3", "3"), K("NUMENTER", "Ent")],
        [
            K("LCTRL", "Ctrl"), K("LWIN", "Win"), K("LALT", "Alt"),
            K("SPACE", "Space", 4.4), K("RALT", "Alt"),
            K("LEFT", "←"), K("DOWN", "↓"), K("RIGHT", "→"),
            K("NUM0", "0"), K("NUMDEC", "."), K("NUMENTER2", "Ent")
        ],
    ]


def compact_1800_rows():
    return [
        [K("ESC", "Esc")] + [K(f"F{i}", f"F{i}") for i in range(1, 13)]
        + [K("PRINT", "Prt"), K("PAUSE", "Pse")],
        ANSI_MAIN_ROWS[0] + [GAP, K("NUMLOCK", "Num"), K("NUMDIV", "/"), K("NUMMUL", "*"), K("NUMMINUS", "-")],
        ANSI_MAIN_ROWS[1] + [GAP, K("NUM7", "7"), K("NUM8", "8"), K("NUM9", "9"), K("NUMPLUS", "+")],
        ANSI_MAIN_ROWS[2] + [GAP, K("NUM4", "4"), K("NUM5", "5"), K("NUM6", "6"), K("NUMPLUS2", "+")],
        ANSI_MAIN_ROWS[3] + [GAP, K("UP", "↑"), K("NUM1", "1"), K("NUM2", "2"), K("NUM3", "3"), K("NUMENTER", "Ent")],
        ANSI_MAIN_ROWS[4] + [GAP, K("LEFT", "←"), K("DOWN", "↓"), K("RIGHT", "→"), K("NUM0", "0"), K("NUMDEC", ".")],
    ]


def laptop_rows():
    return [
        [K("ESC", "Esc")] + [K(f"F{i}", f"F{i}") for i in range(1, 13)],
        ANSI_MAIN_ROWS[0],
        ANSI_MAIN_ROWS[1],
        ANSI_MAIN_ROWS[2],
        ANSI_MAIN_ROWS[3][:-1] + [K("RSHIFT", "Shift", 1.8), K("UP", "↑")],
        [
            K("LCTRL", "Ctrl"), K("LWIN", "Win"), K("LALT", "Alt"),
            K("SPACE", "Space", 4.8), K("RALT", "Alt"),
            K("LEFT", "←"), K("DOWN", "↓"), K("RIGHT", "→")
        ],
    ]


PROFILES: Dict[str, Dict] = {
    "ansi_tkl_87": {
        "name": "ANSI TKL / Tenkeyless (87) — TEST DEFAULT",
        "rows": tkl_rows(False),
    },
    "iso_tkl_88": {
        "name": "ISO TKL / Tenkeyless (88)",
        "rows": tkl_rows(True),
    },
    "ansi_full_104": {
        "name": "ANSI Full Size (104)",
        "rows": full_rows(False),
    },
    "iso_full_105": {
        "name": "ISO Full Size (105)",
        "rows": full_rows(True),
    },
    "ansi_75": {
        "name": "75% Compact",
        "rows": compact_75_rows(),
    },
    "ansi_65": {
        "name": "65% Compact",
        "rows": compact_65_rows(),
    },
    "ansi_60": {
        "name": "60% Compact",
        "rows": compact_60_rows(),
    },
    "compact_96_98": {
        "name": "96% / 98% Compact",
        "rows": compact_96_rows(),
    },
    "compact_1800": {
        "name": "1800 Compact",
        "rows": compact_1800_rows(),
    },
    "laptop_compact": {
        "name": "Laptop / Compact Standard",
        "rows": laptop_rows(),
    },
}


# ---------------------------------------------------------------------------
# Keyboard event normalization
# ---------------------------------------------------------------------------

# Standard PC Set-1 scan codes for the common ANSI area.
SCAN_TO_ID = {
    1: "ESC",
    2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6",
    8: "7", 9: "8", 10: "9", 11: "0",
    12: "MINUS", 13: "EQUAL", 14: "BACKSPACE", 15: "TAB",
    16: "Q", 17: "W", 18: "E", 19: "R", 20: "T", 21: "Y",
    22: "U", 23: "I", 24: "O", 25: "P",
    26: "LBRACKET", 27: "RBRACKET", 28: "ENTER", 29: "LCTRL",
    30: "A", 31: "S", 32: "D", 33: "F", 34: "G", 35: "H",
    36: "J", 37: "K", 38: "L", 39: "SEMICOLON", 40: "APOSTROPHE",
    41: "GRAVE", 42: "LSHIFT", 43: "BACKSLASH",
    44: "Z", 45: "X", 46: "C", 47: "V", 48: "B", 49: "N",
    50: "M", 51: "COMMA", 52: "PERIOD", 53: "SLASH",
    54: "RSHIFT", 56: "LALT", 57: "SPACE", 58: "CAPS",
    59: "F1", 60: "F2", 61: "F3", 62: "F4", 63: "F5", 64: "F6",
    65: "F7", 66: "F8", 67: "F9", 68: "F10",
    69: "NUMLOCK", 70: "SCROLL",
    87: "F11", 88: "F12",
}

NUMPAD_SCAN = {
    55: "NUMMUL",
    71: "NUM7", 72: "NUM8", 73: "NUM9", 74: "NUMMINUS",
    75: "NUM4", 76: "NUM5", 77: "NUM6", 78: "NUMPLUS",
    79: "NUM1", 80: "NUM2", 81: "NUM3",
    82: "NUM0", 83: "NUMDEC",
}

NAME_TO_ID = {
    "esc": "ESC", "escape": "ESC",
    "backspace": "BACKSPACE",
    "tab": "TAB",
    "enter": "ENTER",
    "caps lock": "CAPS",
    "space": "SPACE",
    "left shift": "LSHIFT",
    "right shift": "RSHIFT",
    "shift": "LSHIFT",
    "left ctrl": "LCTRL",
    "right ctrl": "RCTRL",
    "ctrl": "LCTRL",
    "left alt": "LALT",
    "right alt": "RALT",
    "alt": "LALT",
    "windows": "LWIN",
    "left windows": "LWIN",
    "right windows": "RWIN",
    "menu": "MENU",
    "insert": "INSERT",
    "delete": "DELETE",
    "home": "HOME",
    "end": "END",
    "page up": "PAGEUP",
    "page down": "PAGEDOWN",
    "up": "UP",
    "down": "DOWN",
    "left": "LEFT",
    "right": "RIGHT",
    "print screen": "PRINT",
    "scroll lock": "SCROLL",
    "pause": "PAUSE",
    "num lock": "NUMLOCK",
    "decimal": "NUMDEC",
    "add": "NUMPLUS",
    "subtract": "NUMMINUS",
    "multiply": "NUMMUL",
    "divide": "NUMDIV",
    "`": "GRAVE",
    "-": "MINUS",
    "=": "EQUAL",
    "[": "LBRACKET",
    "]": "RBRACKET",
    "\\": "BACKSLASH",
    ";": "SEMICOLON",
    "'": "APOSTROPHE",
    ",": "COMMA",
    ".": "PERIOD",
    "/": "SLASH",
}
for i in range(1, 13):
    NAME_TO_ID[f"f{i}"] = f"F{i}"


def resolve_key_id(event) -> Optional[str]:
    name = (event.name or "").lower()
    scan = event.scan_code

    # Navigation names take priority over overlapping keypad scan codes.
    if name in {
        "insert", "delete", "home", "end", "page up", "page down",
        "up", "down", "left", "right"
    }:
        return NAME_TO_ID.get(name)

    # Distinguish numpad numbers/operators by their traditional scan codes.
    if scan in NUMPAD_SCAN:
        if name in {"7", "8", "9", "4", "5", "6", "1", "2", "3", "0",
                    "decimal", ".", "add", "+", "subtract", "-", "multiply", "*"}:
            return NUMPAD_SCAN[scan]

    if name in NAME_TO_ID:
        return NAME_TO_ID[name]

    if scan in SCAN_TO_ID:
        return SCAN_TO_ID[scan]

    if len(name) == 1 and name.isalpha():
        return name.upper()

    if len(name) == 1 and name.isdigit():
        return name

    return None


# ---------------------------------------------------------------------------
# Synth engine
# ---------------------------------------------------------------------------

class SynthEngine:
    """
    Low-latency polyphonic synthesizer with multiple generated instruments.
    Audio recording captures the exact stereo output to WAV without external
    sample files or extra dependencies.
    """

    def __init__(self, sample_rate: int = 48000, blocksize: int = 256):
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.volume = 0.32
        self.instrument = "Grand Piano"
        self.notes: Dict[int, Dict] = {}

        # High-quality sample engine. If unavailable, the original generated
        # synthesizer remains as a fallback so the app still launches.
        self.fs = None
        self.sfid: Optional[int] = None
        self.soundfont_path: Optional[Path] = None
        self.using_soundfont = False
        self.gm_program = 0
        if fluidsynth is not None:
            try:
                # We render FluidSynth ourselves through sounddevice so recording
                # captures exactly the same audio the user hears.
                self.fs = fluidsynth.Synth(gain=0.65, samplerate=self.sample_rate)
            except Exception:
                self.fs = None
        self.sustain = False
        self.lock = threading.RLock()

        self.record_lock = threading.RLock()
        self.recording = False
        self.recorded_chunks: List[np.ndarray] = []
        self.record_started_at: Optional[float] = None
        self.last_recording_path: Optional[Path] = None

        self.stream = sd.OutputStream(
            samplerate=self.sample_rate,
            blocksize=self.blocksize,
            channels=2,
            dtype="float32",
            latency="low",
            callback=self._callback,
        )
        self.stream.start()

    def set_volume(self, value: float):
        self.volume = max(0.0, min(1.0, value))

    def instrument_names(self) -> List[str]:
        return GM_INSTRUMENTS if self.using_soundfont else INSTRUMENTS

    def engine_description(self) -> str:
        if self.using_soundfont and self.soundfont_path:
            return f"SoundFont: {self.soundfont_path.name}"
        if self.fs is None:
            return "Generated fallback (FluidSynth unavailable)"
        return "Generated fallback (load a .sf2/.sf3 SoundFont)"

    def auto_load_soundfont(self, folder: Path) -> Optional[str]:
        candidates = []
        preferred = [
            "MuseScore_General.sf3", "MuseScore_General.sf2",
            "GeneralUser_GS.sf2", "GeneralUser GS.sf2",
        ]
        for name in preferred:
            p = folder / name
            if p.exists():
                candidates.append(p)
        if not candidates:
            candidates.extend(sorted(folder.glob("*.sf3")))
            candidates.extend(sorted(folder.glob("*.sf2")))
        if not candidates:
            return None
        try:
            self.load_soundfont(candidates[0])
            return str(candidates[0])
        except Exception:
            return None

    def load_soundfont(self, path: Path):
        if self.fs is None:
            detail = FLUIDSYNTH_IMPORT_ERROR or "FluidSynth could not be initialized"
            raise RuntimeError(
                "FluidSynth is not available. Install the native FluidSynth library "
                f"and pyfluidsynth first. Detail: {detail}"
            )
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)

        with self.lock:
            self.panic()
            new_id = self.fs.sfload(str(path), 1)
            if new_id is None or int(new_id) < 0:
                raise RuntimeError(f"FluidSynth could not load {path.name}")
            old_id = self.sfid
            self.sfid = int(new_id)
            self.soundfont_path = path
            self.using_soundfont = True
            self.gm_program = 0
            self.fs.program_select(0, self.sfid, 0, self.gm_program)
            # Give compatible SoundFonts some room/reverb without washing out attacks.
            try:
                self.fs.cc(0, 91, 35)  # reverb send
                self.fs.cc(0, 93, 18)  # chorus send
            except Exception:
                pass
            if old_id is not None and old_id != self.sfid:
                try:
                    self.fs.sfunload(old_id, 0)
                except Exception:
                    pass

    def set_instrument(self, name: str):
        if self.using_soundfont and name in GM_PROGRAM_BY_NAME:
            with self.lock:
                self.gm_program = GM_PROGRAM_BY_NAME[name]
                self.instrument = name
                if self.fs is not None and self.sfid is not None:
                    # Stop old-patch notes before switching programs.
                    try:
                        self.fs.cc(0, 123, 0)
                        self.fs.cc(0, 120, 0)
                    except Exception:
                        pass
                    self.fs.program_select(0, self.sfid, 0, self.gm_program)
            return
        if name in INSTRUMENTS:
            self.instrument = name

    def note_on(self, note: int, velocity: float = 1.0):
        if not 0 <= note <= 127:
            return
        if self.using_soundfont and self.fs is not None:
            with self.lock:
                self.fs.noteon(0, int(note), max(1, min(127, int(velocity * 112))))
            return
        freq = 440.0 * (2.0 ** ((note - 69) / 12.0))
        with self.lock:
            self.notes[note] = {
                "freq": freq,
                "age": 0.0,
                "velocity": velocity,
                "held": True,
                "released": False,
                "release_age": 0.0,
                "instrument": self.instrument,
            }

    def note_off(self, note: int):
        if self.using_soundfont and self.fs is not None:
            with self.lock:
                self.fs.noteoff(0, int(note))
            return
        with self.lock:
            state = self.notes.get(note)
            if not state:
                return
            state["held"] = False
            if not self.sustain:
                state["released"] = True
                state["release_age"] = 0.0

    def set_sustain(self, enabled: bool):
        if self.using_soundfont and self.fs is not None:
            with self.lock:
                self.sustain = enabled
                self.fs.cc(0, 64, 127 if enabled else 0)
            return
        with self.lock:
            self.sustain = enabled
            if not enabled:
                for state in self.notes.values():
                    if not state["held"] and not state["released"]:
                        state["released"] = True
                        state["release_age"] = 0.0

    def panic(self):
        with self.lock:
            self.notes.clear()
            if self.fs is not None:
                try:
                    self.fs.cc(0, 64, 0)
                    self.fs.cc(0, 123, 0)
                    self.fs.cc(0, 120, 0)
                except Exception:
                    pass

    def start_recording(self):
        with self.record_lock:
            self.recorded_chunks = []
            self.recording = True
            self.record_started_at = time.monotonic()

    def stop_recording(self, output_dir: Path) -> Optional[Path]:
        with self.record_lock:
            if not self.recording:
                return self.last_recording_path
            self.recording = False
            chunks = self.recorded_chunks
            self.recorded_chunks = []
            self.record_started_at = None

        if not chunks:
            return None

        audio = np.concatenate(chunks, axis=0)
        audio = np.clip(audio, -1.0, 1.0)
        pcm = (audio * 32767.0).astype(np.int16)

        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = output_dir / f"keyboard_piano_{stamp}.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm.tobytes())

        self.last_recording_path = path
        return path

    def close(self):
        try:
            self.panic()
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        if self.fs is not None:
            try:
                self.fs.delete()
            except Exception:
                pass

    @staticmethod
    def _release_multiplier(state: Dict, t: np.ndarray, release_time: float) -> np.ndarray:
        if not state["released"]:
            return np.ones_like(t)
        rt = state["release_age"] + t
        return np.exp(-rt / max(0.01, release_time))

    def _voice(self, state: Dict, t: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
        age = state["age"]
        freq = state["freq"]
        inst = state["instrument"]
        at = age + t

        if inst == "Grand Piano":
            attack = np.minimum(1.0, at / 0.006)
            env = attack * np.exp(-at / 4.8)
            wave_data = (
                1.00 * np.sin(2 * np.pi * freq * at)
                + 0.42 * np.exp(-at / 1.9) * np.sin(2 * np.pi * freq * 2.00 * at)
                + 0.22 * np.exp(-at / 1.4) * np.sin(2 * np.pi * freq * 3.01 * at)
                + 0.11 * np.exp(-at / 1.0) * np.sin(2 * np.pi * freq * 4.03 * at)
                + 0.05 * np.exp(-at / 0.8) * np.sin(2 * np.pi * freq * 5.05 * at)
            )
            release = 0.20

        elif inst == "Electric Piano":
            attack = np.minimum(1.0, at / 0.012)
            env = attack * np.exp(-at / 6.5)
            trem = 1.0 + 0.06 * np.sin(2 * np.pi * 4.8 * at)
            wave_data = trem * (
                np.sin(2 * np.pi * freq * at)
                + 0.28 * np.sin(2 * np.pi * freq * 2.0 * at)
                + 0.14 * np.exp(-at / 2.2) * np.sin(2 * np.pi * freq * 4.0 * at)
            )
            release = 0.35

        elif inst == "Hammond Organ":
            attack = np.minimum(1.0, at / 0.02)
            env = attack * 0.92
            wave_data = (
                0.90 * np.sin(2 * np.pi * freq * at)
                + 0.48 * np.sin(2 * np.pi * freq * 2.0 * at)
                + 0.28 * np.sin(2 * np.pi * freq * 3.0 * at)
                + 0.15 * np.sin(2 * np.pi * freq * 4.0 * at)
            )
            release = 0.28

        elif inst == "Warm Pad":
            attack = np.minimum(1.0, at / 0.45)
            env = attack * 0.82
            detune = 0.004
            wave_data = (
                0.55 * np.sin(2 * np.pi * freq * at)
                + 0.23 * np.sin(2 * np.pi * freq * (1.0 + detune) * at)
                + 0.23 * np.sin(2 * np.pi * freq * (1.0 - detune) * at)
                + 0.14 * np.sin(2 * np.pi * freq * 2.0 * at)
            )
            release = 1.8

        elif inst == "Synth Lead":
            attack = np.minimum(1.0, at / 0.008)
            env = attack * 0.90
            wave_data = np.zeros_like(at)
            for harmonic in range(1, 7):
                wave_data += (1.0 / harmonic) * np.sin(2 * np.pi * freq * harmonic * at)
            release = 0.18

        elif inst == "Bell":
            attack = np.minimum(1.0, at / 0.003)
            env = attack * np.exp(-at / 5.0)
            mod = 2.4 * np.exp(-at / 1.8) * np.sin(2 * np.pi * freq * 2.71 * at)
            wave_data = (
                np.sin(2 * np.pi * freq * at + mod)
                + 0.32 * np.sin(2 * np.pi * freq * 3.97 * at)
                + 0.16 * np.sin(2 * np.pi * freq * 6.11 * at)
            )
            release = 0.8

        elif inst == "Marimba":
            attack = np.minimum(1.0, at / 0.004)
            env = attack * np.exp(-at / 1.25)
            wave_data = (
                np.sin(2 * np.pi * freq * at)
                + 0.34 * np.sin(2 * np.pi * freq * 3.0 * at)
                + 0.12 * np.sin(2 * np.pi * freq * 5.0 * at)
            )
            release = 0.18

        elif inst == "Synth Bass":
            attack = np.minimum(1.0, at / 0.008)
            env = attack * np.exp(-at / 7.0)
            sub = freq * 0.5
            wave_data = (
                0.85 * np.sin(2 * np.pi * sub * at)
                + 0.55 * np.sin(2 * np.pi * freq * at)
                + 0.18 * np.sin(2 * np.pi * freq * 2.0 * at)
            )
            release = 0.30

        elif inst == "Bright Piano":
            attack = np.minimum(1.0, at / 0.004)
            env = attack * np.exp(-at / 3.7)
            wave_data = (0.92*np.sin(2*np.pi*freq*at)
                         + 0.48*np.exp(-at/1.5)*np.sin(2*np.pi*freq*2*at)
                         + 0.31*np.exp(-at/1.1)*np.sin(2*np.pi*freq*3*at)
                         + 0.18*np.exp(-at/0.8)*np.sin(2*np.pi*freq*5*at))
            release = 0.18

        elif inst == "Honky Tonk":
            attack = np.minimum(1.0, at / 0.004)
            env = attack * np.exp(-at / 3.5)
            d = 0.008
            wave_data = (0.55*np.sin(2*np.pi*freq*at)
                         + 0.30*np.sin(2*np.pi*freq*(1+d)*at)
                         + 0.30*np.sin(2*np.pi*freq*(1-d)*at)
                         + 0.18*np.sin(2*np.pi*freq*2*at))
            release = 0.16

        elif inst == "Harpsichord":
            attack = np.minimum(1.0, at / 0.002)
            env = attack * np.exp(-at / 2.4)
            wave_data = sum((0.75/h)*np.sin(2*np.pi*freq*h*at) for h in range(1, 7))
            release = 0.09

        elif inst == "Clavinet":
            attack = np.minimum(1.0, at / 0.002)
            env = attack * np.exp(-at / 1.1)
            wave_data = (0.45*np.sign(np.sin(2*np.pi*freq*at))
                         + 0.35*np.sin(2*np.pi*freq*at)
                         + 0.15*np.sin(2*np.pi*freq*3*at))
            release = 0.08

        elif inst == "Church Organ":
            attack = np.minimum(1.0, at / 0.05)
            env = attack * 0.90
            wave_data = (0.72*np.sin(2*np.pi*freq*at) + 0.42*np.sin(2*np.pi*freq*2*at)
                         + 0.28*np.sin(2*np.pi*freq*4*at) + 0.17*np.sin(2*np.pi*freq*8*at)
                         + 0.10*np.sin(2*np.pi*freq*0.5*at))
            release = 0.70

        elif inst == "Accordion":
            attack = np.minimum(1.0, at / 0.04)
            env = attack * 0.86
            vib = 0.003*np.sin(2*np.pi*5.2*at)
            wave_data = (0.65*np.sin(2*np.pi*freq*(1+vib)*at)
                         + 0.35*np.sin(2*np.pi*freq*2*at)
                         + 0.20*np.sin(2*np.pi*freq*3*at))
            release = 0.32

        elif inst == "Nylon Guitar":
            attack = np.minimum(1.0, at / 0.003)
            env = attack * np.exp(-at / 2.7)
            wave_data = (0.88*np.sin(2*np.pi*freq*at)
                         + 0.24*np.exp(-at/0.9)*np.sin(2*np.pi*freq*2*at)
                         + 0.10*np.exp(-at/0.6)*np.sin(2*np.pi*freq*3*at))
            release = 0.13

        elif inst == "Steel Guitar":
            attack = np.minimum(1.0, at / 0.002)
            env = attack * np.exp(-at / 3.0)
            wave_data = (0.72*np.sin(2*np.pi*freq*at)
                         + 0.32*np.exp(-at/1.4)*np.sin(2*np.pi*freq*2*at)
                         + 0.22*np.exp(-at/1.0)*np.sin(2*np.pi*freq*3*at)
                         + 0.12*np.exp(-at/0.7)*np.sin(2*np.pi*freq*5*at))
            release = 0.15

        elif inst == "Muted Guitar":
            attack = np.minimum(1.0, at / 0.0015)
            env = attack * np.exp(-at / 0.65)
            wave_data = (0.72*np.sin(2*np.pi*freq*at)
                         + 0.24*np.sin(2*np.pi*freq*2*at)
                         + 0.10*np.sign(np.sin(2*np.pi*freq*at)))
            release = 0.06

        elif inst == "Vibraphone":
            attack = np.minimum(1.0, at / 0.004)
            env = attack * np.exp(-at / 4.2)
            trem = 0.82 + 0.18*np.sin(2*np.pi*5.5*at)
            wave_data = trem*(0.82*np.sin(2*np.pi*freq*at) + 0.28*np.sin(2*np.pi*freq*4*at))
            release = 0.55

        elif inst == "Steel Drum":
            attack = np.minimum(1.0, at / 0.003)
            env = attack * np.exp(-at / 2.3)
            wave_data = (0.72*np.sin(2*np.pi*freq*at)
                         + 0.42*np.sin(2*np.pi*freq*2.02*at)
                         + 0.23*np.sin(2*np.pi*freq*3.97*at))
            release = 0.24

        elif inst == "Music Box":
            attack = np.minimum(1.0, at / 0.002)
            env = attack * np.exp(-at / 3.1)
            wave_data = (0.65*np.sin(2*np.pi*freq*at) + 0.38*np.sin(2*np.pi*freq*3*at)
                         + 0.24*np.sin(2*np.pi*freq*5*at) + 0.11*np.sin(2*np.pi*freq*7*at))
            release = 0.45

        elif inst == "Celesta":
            attack = np.minimum(1.0, at / 0.003)
            env = attack * np.exp(-at / 4.0)
            wave_data = (0.78*np.sin(2*np.pi*freq*at) + 0.31*np.sin(2*np.pi*freq*2.8*at)
                         + 0.18*np.sin(2*np.pi*freq*5.6*at))
            release = 0.52

        elif inst == "Kalimba":
            attack = np.minimum(1.0, at / 0.002)
            env = attack * np.exp(-at / 1.8)
            wave_data = (0.80*np.sin(2*np.pi*freq*at) + 0.32*np.sin(2*np.pi*freq*2.03*at)
                         + 0.16*np.sin(2*np.pi*freq*5.1*at))
            release = 0.18

        elif inst == "Sitar":
            attack = np.minimum(1.0, at / 0.002)
            env = attack * np.exp(-at / 3.7)
            wave_data = (0.46*np.sin(2*np.pi*freq*at)
                         + 0.18*np.sign(np.sin(2*np.pi*freq*at))
                         + 0.18*np.sin(2*np.pi*freq*2.01*at)
                         + 0.12*np.sin(2*np.pi*freq*3.98*at))
            release = 0.22

        elif inst == "String Ensemble":
            attack = np.minimum(1.0, at / 0.18)
            env = attack * 0.84
            vib = 0.0025*np.sin(2*np.pi*5.1*at)
            wave_data = (0.46*np.sin(2*np.pi*freq*(1+vib)*at)
                         + 0.27*np.sin(2*np.pi*freq*2*at)
                         + 0.16*np.sin(2*np.pi*freq*3*at)
                         + 0.12*np.sin(2*np.pi*freq*0.5*at))
            release = 1.10

        elif inst == "Choir Pad":
            attack = np.minimum(1.0, at / 0.32)
            env = attack * 0.80
            vib = 0.004*np.sin(2*np.pi*4.6*at)
            wave_data = (0.58*np.sin(2*np.pi*freq*(1+vib)*at)
                         + 0.20*np.sin(2*np.pi*freq*2*at)
                         + 0.10*np.sin(2*np.pi*freq*3*at))
            release = 1.65

        elif inst == "Brass Section":
            attack = np.minimum(1.0, at / 0.06)
            env = attack * 0.88
            wave_data = sum((0.75/h)*np.sin(2*np.pi*freq*h*at) for h in range(1, 6))
            release = 0.42

        elif inst == "Trumpet":
            attack = np.minimum(1.0, at / 0.035)
            env = attack * 0.84
            vib = 0.0025*np.sin(2*np.pi*5.8*at)
            wave_data = (0.56*np.sin(2*np.pi*freq*(1+vib)*at)
                         + 0.35*np.sin(2*np.pi*freq*2*at)
                         + 0.23*np.sin(2*np.pi*freq*3*at)
                         + 0.13*np.sin(2*np.pi*freq*4*at))
            release = 0.25

        elif inst == "Flute":
            attack = np.minimum(1.0, at / 0.07)
            env = attack * 0.82
            vib = 0.0035*np.sin(2*np.pi*5.0*at)
            wave_data = 0.92*np.sin(2*np.pi*freq*(1+vib)*at) + 0.08*np.sin(2*np.pi*freq*2*at)
            release = 0.34

        elif inst == "Clarinet":
            attack = np.minimum(1.0, at / 0.05)
            env = attack * 0.84
            wave_data = (0.80*np.sin(2*np.pi*freq*at)
                         + 0.28*np.sin(2*np.pi*freq*3*at)
                         + 0.12*np.sin(2*np.pi*freq*5*at))
            release = 0.30

        elif inst == "Analog Pad":
            attack = np.minimum(1.0, at / 0.28)
            env = attack * 0.78
            saw = sum((1.0/h)*np.sin(2*np.pi*freq*h*at) for h in range(1, 7))
            wave_data = 0.55*saw + 0.30*np.sin(2*np.pi*freq*0.997*at)
            release = 1.45

        elif inst == "Space Pad":
            attack = np.minimum(1.0, at / 0.65)
            env = attack * 0.76
            slow = 0.5 + 0.5*np.sin(2*np.pi*0.18*at)
            wave_data = (0.42*np.sin(2*np.pi*freq*at)
                         + 0.24*np.sin(2*np.pi*freq*1.005*at)
                         + 0.20*np.sin(2*np.pi*freq*0.995*at)
                         + 0.14*slow*np.sin(2*np.pi*freq*2*at))
            release = 2.8

        elif inst == "Glass Pad":
            attack = np.minimum(1.0, at / 0.42)
            env = attack * 0.74
            wave_data = (0.56*np.sin(2*np.pi*freq*at)
                         + 0.24*np.sin(2*np.pi*freq*2.71*at)
                         + 0.13*np.sin(2*np.pi*freq*4.17*at))
            release = 2.2

        elif inst == "Square Lead":
            attack = np.minimum(1.0, at / 0.004)
            env = attack * 0.76
            wave_data = np.sign(np.sin(2*np.pi*freq*at))
            release = 0.12

        elif inst == "Saw Lead":
            attack = np.minimum(1.0, at / 0.004)
            env = attack * 0.78
            wave_data = sum((1.0/h)*np.sin(2*np.pi*freq*h*at) for h in range(1, 10))
            release = 0.14

        elif inst == "Theremin":
            attack = np.minimum(1.0, at / 0.08)
            env = attack * 0.76
            vib = 0.012*np.sin(2*np.pi*5.7*at)
            wave_data = (0.82*np.sin(2*np.pi*freq*(1+vib)*at)
                         + 0.18*np.sin(2*np.pi*freq*2*(1+vib)*at))
            release = 0.65

        elif inst == "Reese Bass":
            attack = np.minimum(1.0, at / 0.015)
            env = attack * np.exp(-at / 8.0)
            d = 0.008
            wave_data = (0.38*np.sin(2*np.pi*freq*(1-d)*at)
                         + 0.38*np.sin(2*np.pi*freq*(1+d)*at)
                         + 0.34*np.sign(np.sin(2*np.pi*freq*at))
                         + 0.30*np.sin(2*np.pi*freq*0.5*at))
            release = 0.42

        elif inst == "Wobble Bass":
            attack = np.minimum(1.0, at / 0.01)
            env = attack * np.exp(-at / 7.0)
            wobble = 0.45 + 0.55*np.sin(2*np.pi*3.2*at)
            wave_data = (0.50*np.sin(2*np.pi*freq*0.5*at)
                         + wobble*0.45*np.sign(np.sin(2*np.pi*freq*at)))
            release = 0.28

        elif inst == "FM Glass":
            attack = np.minimum(1.0, at / 0.002)
            env = attack * np.exp(-at / 4.6)
            mod = 4.0*np.exp(-at/2.5)*np.sin(2*np.pi*freq*2.414*at)
            wave_data = np.sin(2*np.pi*freq*at + mod)
            release = 0.72

        elif inst == "Pluck Synth":
            attack = np.minimum(1.0, at / 0.001)
            env = attack * np.exp(-at / 0.72)
            wave_data = (0.55*np.sign(np.sin(2*np.pi*freq*at))
                         + 0.35*np.sin(2*np.pi*freq*at)
                         + 0.12*np.sin(2*np.pi*freq*2*at))
            release = 0.07

        else:  # 8-Bit / Chiptune
            attack = np.minimum(1.0, at / 0.002)
            env = attack * 0.72
            wave_data = np.sign(np.sin(2 * np.pi * freq * at))
            release = 0.08

        env *= self._release_multiplier(state, t, release)
        return wave_data, env, release

    def _callback(self, outdata, frames, time_info, status):
        if self.using_soundfont and self.fs is not None:
            try:
                with self.lock:
                    raw = np.asarray(self.fs.get_samples(frames))
                # pyFluidSynth returns interleaved signed-16-bit stereo samples.
                stereo = raw.reshape(-1, 2).astype(np.float32) / 32768.0
                stereo *= self.volume
                stereo = np.clip(stereo, -1.0, 1.0)
                outdata[:] = stereo
                with self.record_lock:
                    if self.recording:
                        self.recorded_chunks.append(stereo.copy())
                return
            except Exception:
                # A transient SoundFont error should not crash the audio callback.
                outdata.fill(0)
                return

        t = np.arange(frames, dtype=np.float64) / self.sample_rate
        mixed = np.zeros(frames, dtype=np.float64)
        remove = []

        with self.lock:
            for note, state in list(self.notes.items()):
                wave_data, envelope, release_time = self._voice(state, t)
                mixed += state["velocity"] * envelope * wave_data
                state["age"] += frames / self.sample_rate

                if state["released"]:
                    state["release_age"] += frames / self.sample_rate
                    if state["release_age"] > max(1.25, release_time * 4.5):
                        remove.append(note)
                elif state["age"] > 30.0 and not state["held"]:
                    remove.append(note)

            for note in remove:
                self.notes.pop(note, None)

        mixed = np.tanh(mixed * 0.40) * self.volume
        stereo = np.column_stack((mixed, mixed)).astype(np.float32)
        outdata[:] = stereo

        with self.record_lock:
            if self.recording:
                self.recorded_chunks.append(stereo.copy())


# ---------------------------------------------------------------------------
# Qt keyboard bridge
# ---------------------------------------------------------------------------

class KeyboardBridge(QObject):
    pressed = Signal(str)
    released = Signal(str)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class KeyboardPiano(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Keyboard Piano — Whole Keyboard Instrument")
        self.resize(1450, 780)

        self.synth = SynthEngine()
        self.synth.auto_load_soundfont(Path(__file__).resolve().parent)
        self.file_dialog_open = False
        self.bridge = KeyboardBridge()
        self.bridge.pressed.connect(self.handle_key_down)
        self.bridge.released.connect(self.handle_key_up)

        self.profile_id = "ansi_tkl_87"
        self.capture_enabled = True
        self.base_midi = 36  # C2
        self.octave_shift = 0
        self.temp_octave_shift = 0
        self.sustain_held = False
        self.sustain_latched = False
        self.held_ids = set()
        self.active_key_notes: Dict[str, int] = {}
        self.key_buttons: Dict[str, QPushButton] = {}
        self.current_mapping: Dict[str, int] = {}
        self.hook = None
        self.recordings_dir = Path(__file__).resolve().parent / "recordings"
        self.record_started_ui: Optional[float] = None

        self._build_ui()
        QApplication.instance().installEventFilter(self)
        self.rebuild_keyboard()
        self._install_keyboard_hook()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        title = QLabel("Keyboard Piano")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        outer.addWidget(title)

        subtitle = QLabel(
            "Every available non-control key becomes part of one continuous musical surface. "
            "Default profile is ANSI TKL / Tenkeyless. Load a SoundFont for high-quality sampled instruments."
        )
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        controls = QHBoxLayout()

        controls.addWidget(QLabel("Keyboard:"))
        self.profile_combo = QComboBox()
        for profile_id, profile in PROFILES.items():
            self.profile_combo.addItem(profile["name"], profile_id)
        self.profile_combo.setCurrentIndex(0)
        self.profile_combo.currentIndexChanged.connect(self.profile_changed)
        controls.addWidget(self.profile_combo, 2)

        controls.addWidget(QLabel("Scale:"))
        self.scale_combo = QComboBox()
        self.scale_combo.addItems(SCALES.keys())
        self.scale_combo.currentIndexChanged.connect(self.rebuild_mapping_only)
        controls.addWidget(self.scale_combo)

        controls.addWidget(QLabel("Root:"))
        self.root_combo = QComboBox()
        self.root_combo.addItems(NOTE_NAMES)
        self.root_combo.currentIndexChanged.connect(self.rebuild_mapping_only)
        controls.addWidget(self.root_combo)

        controls.addWidget(QLabel("Instrument:"))
        self.instrument_combo = QComboBox()
        self.instrument_combo.addItems(self.synth.instrument_names())
        self.instrument_combo.currentTextChanged.connect(self.instrument_changed)
        controls.addWidget(self.instrument_combo)

        self.capture_box = QCheckBox("Capture keyboard")
        self.capture_box.setChecked(True)
        self.capture_box.toggled.connect(self.set_capture)
        controls.addWidget(self.capture_box)

        panic = QPushButton("PANIC / STOP ALL")
        panic.clicked.connect(self.panic)
        controls.addWidget(panic)

        outer.addLayout(controls)

        controls2 = QHBoxLayout()

        controls2.addWidget(QLabel("Volume"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(32)
        self.volume_slider.valueChanged.connect(
            lambda v: self.synth.set_volume(v / 100.0)
        )
        controls2.addWidget(self.volume_slider, 2)

        self.octave_label = QLabel("Octave shift: 0")
        controls2.addWidget(self.octave_label)

        octave_down = QPushButton("Octave -")
        octave_down.clicked.connect(lambda: self.shift_octave(-1))
        controls2.addWidget(octave_down)

        octave_up = QPushButton("Octave +")
        octave_up.clicked.connect(lambda: self.shift_octave(1))
        controls2.addWidget(octave_up)

        octave_reset = QPushButton("Reset")
        octave_reset.clicked.connect(self.reset_octave)
        controls2.addWidget(octave_reset)

        self.sustain_toggle_button = QPushButton("Toggle Sustain: OFF")
        self.sustain_toggle_button.setCheckable(True)
        self.sustain_toggle_button.toggled.connect(self.set_sustain_latched)
        controls2.addWidget(self.sustain_toggle_button)

        outer.addLayout(controls2)

        record_row = QHBoxLayout()
        self.record_button = QPushButton("● Start Recording")
        self.record_button.clicked.connect(self.toggle_recording)
        record_row.addWidget(self.record_button)

        self.record_time_label = QLabel("REC 00:00")
        record_row.addWidget(self.record_time_label)

        self.open_recordings_button = QPushButton("Open Recordings Folder")
        self.open_recordings_button.clicked.connect(self.open_recordings_folder)
        record_row.addWidget(self.open_recordings_button)

        self.engine_label = QLabel(self.synth.engine_description())
        record_row.addWidget(self.engine_label)
        self.load_soundfont_button = QPushButton("Load SoundFont (.sf2/.sf3)")
        self.load_soundfont_button.clicked.connect(self.load_soundfont_ui)
        record_row.addWidget(self.load_soundfont_button)
        record_row.addStretch(1)
        outer.addLayout(record_row)

        self.record_timer = QTimer(self)
        self.record_timer.setInterval(250)
        self.record_timer.timeout.connect(self.update_record_timer)

        # Prevent piano keystrokes from changing whichever GUI control was last clicked.
        # Mouse interaction still works normally. While Capture is ON, the eventFilter
        # below also consumes Qt key events before controls can act on them.
        for control in (
            self.profile_combo, self.scale_combo, self.root_combo, self.instrument_combo,
            self.capture_box, panic, self.volume_slider, octave_down, octave_up,
            octave_reset, self.sustain_toggle_button, self.record_button,
            self.open_recordings_button, self.load_soundfont_button
        ):
            control.setFocusPolicy(Qt.NoFocus)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        outer.addWidget(separator)

        self.keyboard_area = QWidget()
        self.keyboard_layout = QVBoxLayout(self.keyboard_area)
        self.keyboard_layout.setSpacing(5)
        self.keyboard_layout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.keyboard_area, 1)

        self.status = QLabel(
            "Controls: Space = hold sustain | Toggle Sustain = latch sustain | "
            "Left Shift = octave down while held | Right Shift = octave up while held | "
            "Esc = stop all notes"
        )
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    def eventFilter(self, obj, event):
        # Critical focus bug fix: when Capture is active, the global keyboard hook
        # is the instrument input. Qt must not simultaneously interpret those same
        # keys as combo-box search, arrow navigation, button activation, etc.
        if self.capture_enabled and not self.file_dialog_open and event.type() in (
            QEvent.KeyPress,
            QEvent.KeyRelease,
            QEvent.ShortcutOverride,
        ):
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def load_soundfont_ui(self):
        previous_capture = self.capture_enabled
        self.capture_enabled = False
        self.file_dialog_open = True
        try:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Load SoundFont",
                str(Path(__file__).resolve().parent),
                "SoundFont files (*.sf2 *.sf3);;All files (*.*)",
            )
        finally:
            self.file_dialog_open = False
            self.capture_enabled = previous_capture

        if not path:
            return

        self.panic()
        try:
            self.synth.load_soundfont(Path(path))
        except Exception as exc:
            self.status.setText(f"SoundFont load failed: {exc}")
            return

        self.instrument_combo.blockSignals(True)
        self.instrument_combo.clear()
        self.instrument_combo.addItems(self.synth.instrument_names())
        self.instrument_combo.setCurrentIndex(0)
        self.instrument_combo.blockSignals(False)
        self.synth.set_instrument(self.instrument_combo.currentText())
        self.engine_label.setText(self.synth.engine_description())
        self.status.setText(
            f"Loaded {Path(path).name}. You now have 128 sample-based General MIDI instruments."
        )

    def instrument_changed(self, name: str):
        self.synth.set_instrument(name)
        self.status.setText(f"Instrument: {name}")

    def toggle_recording(self):
        if not self.synth.recording:
            self.synth.start_recording()
            self.record_started_ui = time.monotonic()
            self.record_button.setText("■ Stop & Save Recording")
            self.record_time_label.setText("REC 00:00")
            self.record_timer.start()
            self.status.setText("Recording started — the app's stereo synth output is being captured.")
            return

        self.record_timer.stop()
        path = self.synth.stop_recording(self.recordings_dir)
        self.record_started_ui = None
        self.record_button.setText("● Start Recording")
        self.record_time_label.setText("REC 00:00")
        if path:
            self.status.setText(f"Recording saved: {path}")
        else:
            self.status.setText("Recording stopped; no audio frames were captured.")

    def update_record_timer(self):
        if self.record_started_ui is None:
            self.record_time_label.setText("REC 00:00")
            return
        elapsed = int(time.monotonic() - self.record_started_ui)
        minutes, seconds = divmod(elapsed, 60)
        self.record_time_label.setText(f"REC {minutes:02d}:{seconds:02d}")

    def open_recordings_folder(self):
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(self.recordings_dir))
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", str(self.recordings_dir)])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", str(self.recordings_dir)])
        except Exception as exc:
            self.status.setText(f"Recordings folder: {self.recordings_dir} ({exc})")

    def _install_keyboard_hook(self):
        def callback(event):
            if not self.capture_enabled:
                return
            key_id = resolve_key_id(event)
            if not key_id:
                return

            if event.event_type == keyboard.KEY_DOWN:
                self.bridge.pressed.emit(key_id)
            elif event.event_type == keyboard.KEY_UP:
                self.bridge.released.emit(key_id)

        self.hook = keyboard.hook(callback, suppress=False)

    def profile_changed(self):
        self.profile_id = self.profile_combo.currentData()
        self.panic()
        self.rebuild_keyboard()

    def set_capture(self, enabled: bool):
        self.capture_enabled = enabled
        if not enabled:
            self.panic()
            self.status.setText("Capture OFF — keyboard events are ignored.")
        else:
            self.status.setText(
                "Capture ON. Space = hold sustain | Toggle Sustain = latch sustain | "
                "Left Shift = octave down | Right Shift = octave up | Esc = stop all notes"
            )

    def shift_octave(self, delta: int):
        self.octave_shift = max(-3, min(3, self.octave_shift + delta))
        self.octave_label.setText(f"Octave shift: {self.octave_shift:+d}")
        self.panic()
        self.rebuild_mapping_only()

    def reset_octave(self):
        self.octave_shift = 0
        self.octave_label.setText("Octave shift: 0")
        self.panic()
        self.rebuild_mapping_only()

    def _apply_sustain_state(self):
        """Sustain stays active while Space is held OR latch mode is enabled."""
        self.synth.set_sustain(self.sustain_held or self.sustain_latched)

    def set_sustain_latched(self, enabled: bool):
        self.sustain_latched = bool(enabled)
        self.sustain_toggle_button.setText(
            "Toggle Sustain: ON" if self.sustain_latched else "Toggle Sustain: OFF"
        )
        self._apply_sustain_state()

        if self.sustain_latched:
            self.status.setText(
                "Toggle Sustain ON — sustain stays active without holding Space."
            )
        else:
            self.status.setText(
                "Toggle Sustain OFF — Space still works as normal hold sustain."
            )

    def panic(self):
        self.synth.panic()
        self.sustain_held = False
        self.sustain_latched = False
        self.synth.set_sustain(False)

        if hasattr(self, "sustain_toggle_button"):
            self.sustain_toggle_button.blockSignals(True)
            self.sustain_toggle_button.setChecked(False)
            self.sustain_toggle_button.setText("Toggle Sustain: OFF")
            self.sustain_toggle_button.blockSignals(False)

        self.held_ids.clear()
        self.active_key_notes.clear()
        self.temp_octave_shift = 0
        for button in self.key_buttons.values():
            self._style_key_button(button, active=False)

    def _clear_keyboard_layout(self):
        while self.keyboard_layout.count():
            item = self.keyboard_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            layout = item.layout()
            if layout:
                while layout.count():
                    sub = layout.takeAt(0)
                    w = sub.widget()
                    if w:
                        w.deleteLater()

    def profile_key_ids(self) -> List[str]:
        rows = PROFILES[self.profile_id]["rows"]
        result = []
        seen = set()
        for row in reversed(rows):  # bottom-to-top gives a more instrument-like range
            for spec in row:
                if spec.key_id == "__GAP__":
                    continue
                if spec.key_id not in seen:
                    result.append(spec.key_id)
                    seen.add(spec.key_id)
        return result

    def playable_key_ids(self) -> List[str]:
        return [k for k in self.profile_key_ids() if k not in CONTROL_KEYS]

    def rebuild_mapping_only(self):
        scale = SCALES[self.scale_combo.currentText()]
        root_pc = self.root_combo.currentIndex()
        base = self.base_midi + self.octave_shift * 12

        playable = self.playable_key_ids()
        mapping = {}
        for index, key_id in enumerate(playable):
            note = scale_note(index, root_pc, base, scale)
            if 0 <= note <= 127:
                mapping[key_id] = note

        self.current_mapping = mapping
        self.refresh_button_labels()

    def rebuild_keyboard(self):
        self._clear_keyboard_layout()
        self.key_buttons = {}

        rows = PROFILES[self.profile_id]["rows"]
        for row_specs in rows:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(4)

            for spec in row_specs:
                if spec.key_id == "__GAP__":
                    spacer = QWidget()
                    spacer.setFixedWidth(int(22 * spec.width))
                    row_layout.addWidget(spacer)
                    continue

                button = QPushButton(spec.label)
                button.setMinimumHeight(52)
                button.setMinimumWidth(int(44 * spec.width))
                button.setMaximumWidth(int(62 * spec.width))
                button.setEnabled(False)
                self._style_key_button(button, active=False)
                self.key_buttons[spec.key_id] = button
                row_layout.addWidget(button, int(spec.width * 10))

            row_layout.addStretch(1)
            self.keyboard_layout.addLayout(row_layout)

        self.keyboard_layout.addStretch(1)
        self.rebuild_mapping_only()

    def refresh_button_labels(self):
        rows = PROFILES[self.profile_id]["rows"]
        label_by_id = {}
        for row in rows:
            for spec in row:
                if spec.key_id != "__GAP__":
                    label_by_id[spec.key_id] = spec.label

        for key_id, button in self.key_buttons.items():
            base_label = label_by_id.get(key_id, key_id)
            if key_id == "SPACE":
                button.setText(f"{base_label}\nHold Sustain")
            elif key_id == "LSHIFT":
                button.setText(f"{base_label}\nOct -")
            elif key_id == "RSHIFT":
                button.setText(f"{base_label}\nOct +")
            elif key_id == "ESC":
                button.setText(f"{base_label}\nPanic")
            elif key_id in self.current_mapping:
                button.setText(f"{base_label}\n{midi_name(self.current_mapping[key_id])}")
            else:
                button.setText(base_label)

    def _style_key_button(self, button: QPushButton, active: bool):
        if active:
            button.setStyleSheet(
                """
                QPushButton {
                    background: #f0f0f0;
                    color: #111;
                    border: 3px solid #111;
                    border-radius: 6px;
                    font-weight: bold;
                    padding: 3px;
                }
                """
            )
        else:
            button.setStyleSheet(
                """
                QPushButton {
                    background: #2c2c2c;
                    color: #f3f3f3;
                    border: 1px solid #666;
                    border-radius: 6px;
                    padding: 3px;
                }
                """
            )

    def handle_key_down(self, key_id: str):
        if key_id in self.held_ids:
            return  # ignore OS auto-repeat

        self.held_ids.add(key_id)

        button = self.key_buttons.get(key_id)
        if button:
            self._style_key_button(button, active=True)

        if key_id == "ESC":
            self.panic()
            return

        if key_id == "SPACE":
            self.sustain_held = True
            self._apply_sustain_state()
            return

        if key_id == "LSHIFT":
            self.temp_octave_shift -= 12
            return

        if key_id == "RSHIFT":
            self.temp_octave_shift += 12
            return

        # Ignore keys that are not physically present in the selected profile.
        if key_id not in self.key_buttons:
            return

        note = self.current_mapping.get(key_id)
        if note is None:
            return

        note += self.temp_octave_shift
        if not 0 <= note <= 127:
            return

        self.active_key_notes[key_id] = note
        self.synth.note_on(note, 1.0)

        self.status.setText(
            f"{key_id} → {midi_name(note)} (MIDI {note}) | "
            f"{PROFILES[self.profile_id]['name']}"
        )

    def handle_key_up(self, key_id: str):
        self.held_ids.discard(key_id)

        button = self.key_buttons.get(key_id)
        if button:
            self._style_key_button(button, active=False)

        if key_id == "SPACE":
            self.sustain_held = False
            self._apply_sustain_state()
            return

        if key_id == "LSHIFT":
            self.temp_octave_shift += 12
            return

        if key_id == "RSHIFT":
            self.temp_octave_shift -= 12
            return

        note = self.active_key_notes.pop(key_id, None)
        if note is not None:
            self.synth.note_off(note)

    def closeEvent(self, event):
        try:
            if self.synth.recording:
                self.synth.stop_recording(self.recordings_dir)
        except Exception:
            pass
        try:
            if self.hook is not None:
                keyboard.unhook(self.hook)
        except Exception:
            pass
        self.synth.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    try:
        window = KeyboardPiano()
    except Exception as exc:
        # Most startup failures here are audio-device related.
        print("Could not start Keyboard Piano.")
        print(f"Error: {exc}")
        print(
            "\nCheck that Windows has a working default audio output device, "
            "then restart the app."
        )
        raise

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
