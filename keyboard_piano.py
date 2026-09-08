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
    Instrument-native performance layer (Auto / Keys / Bowed / Fretted / Wind / Drums / Plucked)
    Violin-family bow/string + chord/fingering surface with vibrato/tremolo/staccato
    Guitar-family chord shapes, individual strings, strums, palm mute, harmonics, bends and tunings
    Wind/brass breath-gated fingering with tongue/vibrato/fall/expression
    Drum hits, rolls, flams, drags, accents and hi-hat choke behaviour
    Harp/plucked chord and arpeggio performance surface
    Sample-based SoundFont instruments through FluidSynth
    128 General MIDI programs + layers/drum kits when a SoundFont is loaded
    WAV recording/playback overdub system
    Generated-synth fallback if FluidSynth/SoundFont is unavailable
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


def resource_dir() -> Path:
    """Read-only bundled resources (PyInstaller _MEIPASS when frozen)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def writable_app_dir() -> Path:
    """Persistent files live beside the EXE when frozen, beside the .py otherwise."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


# Help pyFluidSynth find DLLs bundled into a PyInstaller one-file executable.
if sys.platform.startswith("win") and hasattr(os, "add_dll_directory"):
    for _dll_dir in (resource_dir(), resource_dir() / "fluidsynth"):
        try:
            if _dll_dir.exists():
                os.add_dll_directory(str(_dll_dir))
        except Exception:
            pass

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
    QGridLayout,
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

# SoundFont patches fall broadly into two useful sustain behaviours:
#
# 1) naturally-decaying patches — piano, mallets, most plucked instruments, etc.
#    These already get quieter while a note is held, so imposing another volume
#    curve would make them feel unnatural.
#
# 2) loop/sustain patches — organs, strings, winds, pads, leads, Guitar Harmonics,
#    etc. These may remain at almost constant level until Note Off. When sustain
#    is enabled we apply a per-note expression fade to these patches so the timer
#    is a true fade rather than "full volume, then suddenly stop".
# Only patches that reliably decay on their own belong here.
#
# IMPORTANT: electric/processed guitars are intentionally NOT included.
# Many SoundFonts loop those samples while the note is held, so putting them
# in this set produces the exact bad behaviour we want to avoid:
# constant volume -> timeout -> abrupt stop.
NATURAL_DECAY_GM_PROGRAMS = (
    set(range(0, 16))       # pianos + chromatic percussion
    | {24, 25}              # nylon + steel acoustic guitars
    | {28}                  # muted guitar is short/plucked in most GM banks
    | {45, 46, 47}          # pizzicato strings, harp, timpani
    | set(range(104, 109))  # sitar, banjo, shamisen, koto, kalimba
    | set(range(112, 128))  # percussion / one-shot SFX
)

# Explicit list for patches that should always use the app's gradual sustain
# volume envelope even when their SoundFont happens to have some natural decay.
# GM programs are zero-based here.
FORCE_SMART_FADE_GM_PROGRAMS = {
    26,  # Electric Guitar (jazz)
    27,  # Electric Guitar (clean)
    29,  # Overdriven Guitar
    30,  # Distortion Guitar
    31,  # Guitar Harmonics
}

# FluidSynth/pyFluidSynth supports many MIDI channels. We give each active
# SoundFont voice its own channel so fading one sustained note never turns down
# newly-played notes or another instrument layer.
SOUNDFONT_VOICE_CHANNELS = tuple(ch for ch in range(64) if ch != 9)


LAYERED_INSTRUMENTS = {
    "Layer: Piano + Strings": (0, 48),
    "Layer: Piano + Warm Pad": (0, 89),
    "Layer: Electric Piano + Choir": (4, 52),
    "Layer: Electric Piano + Warm Pad": (4, 89),
    "Layer: Organ + Choir": (16, 52),
    "Layer: Organ + Strings": (16, 48),
    "Layer: Harp + Strings": (46, 48),
    "Layer: Nylon Guitar + Strings": (24, 48),
    "Layer: Steel Guitar + Warm Pad": (25, 89),
    "Layer: Vibraphone + New Age Pad": (11, 88),
    "Layer: Bells + Halo Pad": (14, 94),
    "Layer: Brass + Strings": (61, 48),
    "Layer: Flute + Strings": (73, 48),
    "Layer: Choir + Warm Pad": (52, 89),
    "Layer: Saw Lead + Warm Pad": (81, 89),
    "Layer: Synth Bass + Lead": (38, 87),
}

DRUM_KITS = {
    "Drums: Standard Kit": 0,
    "Drums: Room Kit": 8,
    "Drums: Power Kit": 16,
    "Drums: Electronic Kit": 24,
    "Drums: TR-808 Kit": 25,
    "Drums: Jazz Kit": 32,
    "Drums: Brush Kit": 40,
    "Drums: Orchestra Kit": 48,
    "Drums: SFX Kit": 56,
}

RECORD_MODES = [
    ("Regular — record live instrument only", "regular"),
    ("Record playback + live instrument", "mix"),
    ("Playback on, record live instrument only", "live_only"),
]

# ---------------------------------------------------------------------------
# Instrument-native performance layer
# ---------------------------------------------------------------------------
#
# The old app treated every SoundFont preset like a piano patch:
#     physical key -> MIDI note -> instrument sample
#
# The performance layer sits between keyboard input and the audio engine so the
# *control grammar* changes with the selected instrument family.  The default
# "Auto" mode chooses an archetype from the instrument name, while the user can
# override it at any time.
PERFORMANCE_MODES = [
    ("Auto — instrument-native", "auto"),
    ("Keys / Piano Grid", "keys"),
    ("Bowed Strings", "bowed"),
    ("Guitar / Fretted Strings", "fretted"),
    ("Wind / Brass", "wind"),
    ("Drum Kit", "drums"),
    ("Harp / Plucked Array", "plucked"),
]

GUITAR_TUNINGS = {
    "Standard EADGBE": [40, 45, 50, 55, 59, 64],
    "Drop D DADGBE": [38, 45, 50, 55, 59, 64],
    "DADGAD": [38, 45, 50, 55, 57, 62],
    "Open G DGDGBD": [38, 43, 50, 55, 59, 62],
    "Open D DADF#AD": [38, 45, 50, 54, 57, 62],
}

BOWED_TUNINGS = {
    "Violin": ([55, 62, 69, 76], ["G", "D", "A", "E"]),
    "Fiddle": ([55, 62, 69, 76], ["G", "D", "A", "E"]),
    "Viola": ([48, 55, 62, 69], ["C", "G", "D", "A"]),
    "Cello": ([36, 43, 50, 57], ["C", "G", "D", "A"]),
    "Contrabass": ([28, 33, 38, 43], ["E", "A", "D", "G"]),
}

# Instrument-native fingerboard controls.
#
# Fretted strings:
#   Z X C V B N = select string 6 -> 1 for editing
#   ` 1 2 ... 0 - = = fret 0 -> 12 within the current fret bank
#   PageUp/PageDown = shift the edit bank by 12 frets
#   Delete = mute selected string
#   Home = reset all strings open
#
# Bowed strings:
#   Z X C V = select physical string 1 -> 4 for fingering
#   the same fret/position keys set chromatic finger position
#   U/I/O/P/[/]/\\ remain the bow/string selectors.
#
# This makes chord voicings a consequence of independent string fingering
# rather than a tiny fixed list of seven canned chords.
FINGER_POSITION_KEYS = {
    "GRAVE": 0,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
    "7": 7, "8": 8, "9": 9, "0": 10,
    "MINUS": 11,
    "EQUAL": 12,
}

FRETTED_EDIT_KEYS = ["Z", "X", "C", "V", "B", "N"]
BOWED_EDIT_KEYS = ["Z", "X", "C", "V"]

# Full home row = instant stored voicings. The user asked for ten; using the
# whole Caps->Enter row gives 13 memory slots without sacrificing editing
# resolution. Each fretted slot stores up to six independent frets; each bowed
# slot stores four independent finger positions.
VOICING_SLOT_KEYS = [
    "CAPS", "A", "S", "D", "F", "G", "H", "J", "K", "L",
    "SEMICOLON", "APOSTROPHE", "ENTER",
]
VOICING_SLOT_BY_KEY = {key: i for i, key in enumerate(VOICING_SLOT_KEYS)}

# FREE-mode physical fretboard: the whole keyboard is a simultaneous 6 x 12
# string/fret grid. Row 1 = S1 (highest string), row 6 = S6 (lowest string).
# PageUp/PageDown shifts the whole grid by 12 frets/positions.
FREE_STRING_FRET_ROWS = [
    ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"],
    ["GRAVE", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "MINUS"],
    ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "LBRACKET", "RBRACKET"],
    ["A", "S", "D", "F", "G", "H", "J", "K", "L", "SEMICOLON", "APOSTROPHE", "ENTER"],
    ["LSHIFT", "Z", "X", "C", "V", "B", "N", "M", "COMMA", "PERIOD", "SLASH", "RSHIFT"],
    # Bottom/navigation row; deliberately avoids Windows/Menu keys.
    ["LCTRL", "LALT", "SPACE", "RALT", "RCTRL", "LEFT", "DOWN", "RIGHT", "UP", "INSERT", "HOME", "END"],
]
FREE_STRING_FRET_KEY_INFO = {
    key_id: (row_index, fret_index + 1)
    for row_index, row in enumerate(FREE_STRING_FRET_ROWS)
    for fret_index, key_id in enumerate(row)
}

# 13 common chord presets. Root transposes these labels/shapes globally.
COMMON_CHORD_NAMES = [
    "C", "G", "D", "A", "E", "Am", "Em", "Dm", "F", "C7", "G7", "A7", "E7",
]
COMMON_CHORD_PITCH_CLASSES = [
    {0, 4, 7},
    {7, 11, 2},
    {2, 6, 9},
    {9, 1, 4},
    {4, 8, 11},
    {9, 0, 4},
    {4, 7, 11},
    {2, 5, 9},
    {5, 9, 0},
    {0, 4, 7, 10},
    {7, 11, 2, 5},
    {9, 1, 4, 7},
    {4, 8, 11, 2},
]
DEFAULT_GUITAR_CHORD_VOICINGS = [
    [None, 3, 2, 0, 1, 0],
    [3, 2, 0, 0, 0, 3],
    [None, None, 0, 2, 3, 2],
    [None, 0, 2, 2, 2, 0],
    [0, 2, 2, 1, 0, 0],
    [None, 0, 2, 2, 1, 0],
    [0, 2, 2, 0, 0, 0],
    [None, None, 0, 2, 3, 1],
    [1, 3, 3, 2, 1, 1],
    [None, 3, 2, 3, 1, 0],
    [3, 2, 0, 0, 0, 1],
    [None, 0, 2, 0, 2, 0],
    [0, 2, 0, 1, 0, 0],
]

# Restore the original harp interaction: choose a harmonic degree with Z-M,
# then pluck root/3rd/5th/chord or arpeggiate it with the right hand.
HARP_CHORD_KEYS = {
    "Z": 0, "X": 1, "C": 2, "V": 3, "B": 4, "N": 5, "M": 6,
}
HARP_CHORD_LABELS = ["I", "ii", "iii", "IV", "V", "vi", "vii°"]

# Harp FREE mode: one physical key = one harp string/note.
# Control keys are deliberately excluded so Tab/PageUp/PageDown/sustain and
# expressive controls remain usable.
HARP_FREE_KEYS = [
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
    "GRAVE", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "MINUS", "EQUAL",
    "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "LBRACKET", "RBRACKET",
    "A", "S", "D", "F", "G", "H", "J", "K", "L", "SEMICOLON", "APOSTROPHE",
    "Z", "X", "C", "V", "B", "N", "M", "COMMA", "PERIOD", "SLASH",
]
HARP_FREE_KEY_INDEX = {key_id: i for i, key_id in enumerate(HARP_FREE_KEYS)}

# In CHORD mode these seven keys are individual tones of the currently active
# chord voicing, not chord-selection keys.
HARP_CHORD_TONE_KEYS = ["Z", "X", "C", "V", "B", "N", "M"]

BOW_KEYS = {
    "U": (0,),
    "I": (0, 1),
    "O": (1,),
    "P": (1, 2),
    "LBRACKET": (2,),
    "RBRACKET": (2, 3),
    "BACKSLASH": (3,),
}

GUITAR_STRING_KEYS = {
    "U": 0,
    "I": 1,
    "O": 2,
    "P": 3,
    "LBRACKET": 4,
    "RBRACKET": 5,
}

# Wind still uses discrete keyboard positions for pitch, but the whole main
# letter surface is available instead of an arbitrary ten-note strip. Breath
# and articulation remain separate, so it still behaves like a wind instrument
# rather than a piano patch.
WIND_PITCH_SEQUENCE = [
    "Z", "X", "C", "V", "B", "N", "M", "COMMA", "PERIOD", "SLASH",
    "A", "S", "D", "F", "G", "H", "J", "K", "L", "SEMICOLON", "APOSTROPHE",
    "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P",
]
WIND_PITCH_KEYS = {key: i for i, key in enumerate(WIND_PITCH_SEQUENCE)}

DRUM_HIT_KEYS = {
    "Z": (36, "Kick"),
    "X": (38, "Snare"),
    "C": (42, "Closed HH"),
    "V": (46, "Open HH"),
    "B": (51, "Ride"),
    "N": (49, "Crash"),
    "M": (45, "Low Tom"),
    "COMMA": (47, "Mid Tom"),
    "PERIOD": (50, "High Tom"),
    "A": (37, "Rim"),
    "S": (39, "Clap"),
    "D": (56, "Cowbell"),
    "F": (54, "Tambourine"),
}

DRUM_ROLL_KEYS = {
    "Q": (38, "Snare Roll"),
    "W": (42, "Hat Roll"),
    "E": (47, "Tom Roll"),
    "R": (51, "Ride Roll"),
}


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
    Low-latency polyphonic SoundFont / generated-fallback synthesizer.
    SoundFont sustain uses per-note MIDI channels so looped patches can fade
    smoothly without changing naturally-decaying patches or newly-played notes.
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
        self.sustain_timeout = 4.0

        # Per-note SoundFont voice state. Each active note gets its own MIDI
        # channel (or two channels for layered instruments), which lets us fade
        # only the notes that actually need artificial fading.
        self.sf_voices: Dict[int, Dict] = {}
        self.free_sf_channels = set(SOUNDFONT_VOICE_CHANNELS)

        self.instrument_mode = "gm"
        self.lock = threading.RLock()

        self.playback_lock = threading.RLock()
        self.playback_volume = 0.75
        self.playback_slots: List[Dict] = [
            {"path": None, "audio": None, "position": 0, "playing": False}
            for _ in range(10)
        ]

        self.record_lock = threading.RLock()
        self.recording = False
        self.record_mode = "regular"
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
        if self.using_soundfont:
            return GM_INSTRUMENTS + list(LAYERED_INSTRUMENTS.keys()) + list(DRUM_KITS.keys())
        return INSTRUMENTS

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
            self.instrument_mode = "gm"
            self.instrument = GM_INSTRUMENTS[0]
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
        if not self.using_soundfont:
            if name in INSTRUMENTS:
                self.instrument = name
            return

        with self.lock:
            self.panic()
            self.instrument = name

            if name in GM_PROGRAM_BY_NAME:
                self.instrument_mode = "gm"
                self.gm_program = GM_PROGRAM_BY_NAME[name]
                if self.fs is not None and self.sfid is not None:
                    self.fs.program_select(0, self.sfid, 0, self.gm_program)
                    try:
                        self.fs.cc(0, 7, 112)
                    except Exception:
                        pass
                return

            if name in LAYERED_INSTRUMENTS:
                self.instrument_mode = "layer"
                p0, p1 = LAYERED_INSTRUMENTS[name]
                if self.fs is not None and self.sfid is not None:
                    self.fs.program_select(0, self.sfid, 0, p0)
                    self.fs.program_select(1, self.sfid, 0, p1)
                    try:
                        self.fs.cc(0, 7, 92)
                        self.fs.cc(1, 7, 82)
                        self.fs.cc(0, 91, 30)
                        self.fs.cc(1, 91, 42)
                    except Exception:
                        pass
                return

            if name in DRUM_KITS:
                self.instrument_mode = "drums"
                program = DRUM_KITS[name]
                if self.fs is not None and self.sfid is not None:
                    # General MIDI percussion kits conventionally live in bank 128.
                    self.fs.program_select(9, self.sfid, 128, program)
                    try:
                        self.fs.cc(9, 7, 112)
                    except Exception:
                        pass
                return

            # Unknown preset: fall back to Acoustic Grand Piano.
            self.instrument_mode = "gm"
            self.instrument = GM_INSTRUMENTS[0]
            self.gm_program = 0
            if self.fs is not None and self.sfid is not None:
                self.fs.program_select(0, self.sfid, 0, 0)

    def _program_needs_smart_fade(self, program: int) -> bool:
        program = int(program)
        if program in FORCE_SMART_FADE_GM_PROGRAMS:
            return True
        return program not in NATURAL_DECAY_GM_PROGRAMS

    def _voice_parts_for_current_instrument(self) -> List[Tuple[int, int, bool]]:
        """
        Return [(program, channel_volume, smart_fade), ...] for one played note.
        Layered instruments return two independent parts.
        """
        if self.instrument_mode == "layer" and self.instrument in LAYERED_INSTRUMENTS:
            p0, p1 = LAYERED_INSTRUMENTS[self.instrument]
            return [
                (p0, 92, self._program_needs_smart_fade(p0)),
                (p1, 82, self._program_needs_smart_fade(p1)),
            ]

        if self.instrument_mode == "drums":
            return []

        program = int(self.gm_program)
        return [(program, 112, self._program_needs_smart_fade(program))]

    def _terminate_sf_voice(self, note: int):
        """Silently terminate one dedicated SoundFont voice and recycle channels."""
        state = self.sf_voices.pop(int(note), None)
        if not state or self.fs is None:
            return

        for part in state.get("parts", []):
            channel = int(part["channel"])
            try:
                # Dedicated channel: all-sounds-off cannot affect another note.
                self.fs.cc(channel, 120, 0)
                self.fs.cc(channel, 11, 127)
                self.fs.cc(channel, 64, 0)
            except Exception:
                pass
            self.free_sf_channels.add(channel)

    def _steal_oldest_sf_voice(self):
        if not self.sf_voices:
            return
        oldest_note = min(
            self.sf_voices,
            key=lambda n: self.sf_voices[n].get("created_at", 0.0),
        )
        self._terminate_sf_voice(oldest_note)

    def _allocate_sf_channels(self, count: int) -> Tuple[int, ...]:
        while len(self.free_sf_channels) < count and self.sf_voices:
            self._steal_oldest_sf_voice()

        if len(self.free_sf_channels) < count:
            # Extremely unlikely with the 63-channel pool, but fail gracefully.
            return ()

        chosen = tuple(sorted(self.free_sf_channels)[:count])
        for channel in chosen:
            self.free_sf_channels.discard(channel)
        return chosen

    def _configure_sf_channel(self, channel: int, program: int, volume: int):
        if self.fs is None or self.sfid is None:
            return
        self.fs.program_select(channel, self.sfid, 0, int(program))
        try:
            self.fs.cc(channel, 7, int(volume))
            self.fs.cc(channel, 11, 127)  # expression
            self.fs.cc(channel, 64, 0)    # never use CC64 sustain internally
            self.fs.cc(channel, 91, 30)   # reverb
            self.fs.cc(channel, 93, 18)   # chorus
        except Exception:
            pass

    def set_note_pitch_bend(self, note: int, cents: float):
        """Apply pitch bend to the currently sounding instance of one MIDI note."""
        cents = max(-190.0, min(190.0, float(cents)))
        with self.lock:
            if self.using_soundfont and self.fs is not None:
                state = self.sf_voices.get(int(note))
                if not state:
                    return
                # FluidSynth's pitch wheel range is conventionally +/- 2 semitones.
                wheel = int(round(8192 + (cents / 200.0) * 8191))
                wheel = max(0, min(16383, wheel))
                for part in state.get("parts", []):
                    if part.get("finished"):
                        continue
                    try:
                        self.fs.pitch_bend(int(part["channel"]), wheel)
                    except Exception:
                        pass
                return

            state = self.notes.get(int(note))
            if state is not None:
                state["bend_cents"] = cents

    def set_note_expression(self, note: int, value: int):
        """Set MIDI expression for an active note without changing master volume."""
        value = max(0, min(127, int(value)))
        with self.lock:
            if self.using_soundfont and self.fs is not None:
                state = self.sf_voices.get(int(note))
                if not state:
                    return
                for part in state.get("parts", []):
                    if part.get("finished"):
                        continue
                    try:
                        self.fs.cc(int(part["channel"]), 11, value)
                        part["last_expression"] = value
                    except Exception:
                        pass
                return

            state = self.notes.get(int(note))
            if state is not None:
                state["expression"] = value

    def reset_note_performance_controls(self, note: int):
        self.set_note_pitch_bend(note, 0.0)
        self.set_note_expression(note, 127)

    def note_on(self, note: int, velocity: float = 1.0):
        if not 0 <= note <= 127:
            return

        if self.using_soundfont and self.fs is not None:
            with self.lock:
                note = int(note)

                # Re-triggering the same pitch should cancel its old sustain tail.
                if note in self.sf_voices:
                    self._terminate_sf_voice(note)

                midi_velocity = max(1, min(127, int(velocity * 112)))

                if self.instrument_mode == "drums":
                    # Percussion remains on the conventional GM drum channel.
                    self.fs.noteon(9, note, midi_velocity)
                    self.sf_voices[note] = {
                        "created_at": time.monotonic(),
                        "held": True,
                        "drums": True,
                        "parts": [{"channel": 9, "smart_fade": False}],
                    }
                    return

                specs = self._voice_parts_for_current_instrument()
                channels = self._allocate_sf_channels(len(specs))
                if len(channels) != len(specs):
                    return

                parts = []
                for channel, (program, volume, smart_fade) in zip(channels, specs):
                    self._configure_sf_channel(channel, program, volume)
                    self.fs.noteon(channel, note, midi_velocity)
                    parts.append({
                        "channel": channel,
                        "program": int(program),
                        "smart_fade": bool(smart_fade),
                        "fade_start": None,
                        "fade_duration": None,
                        "fade_start_expression": 127,
                        "last_expression": 127,
                        "deadline": None,
                        "finished": False,
                    })

                self.sf_voices[note] = {
                    "created_at": time.monotonic(),
                    "held": True,
                    "drums": False,
                    "parts": parts,
                }
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
                "sustain_deadline": None,
                "instrument": self.instrument,
                "bend_cents": 0.0,
                "expression": 127,
            }

    def note_off_with_fade(self, note: int, seconds: float = 0.18):
        """
        Release one note through a short explicit amplitude fade even when the
        global sustain pedal is off. This is useful for bow lifts, breath stops,
        and similar physical gestures that should not hard-chop a looping sample.
        Global sustain still takes precedence when it is active.
        """
        note = int(note)
        seconds = max(0.03, min(3.0, float(seconds)))

        if self.sustain:
            self.note_off(note)
            return

        if self.using_soundfont and self.fs is not None:
            with self.lock:
                state = self.sf_voices.get(note)
                if not state:
                    return

                state["held"] = False
                if state.get("drums"):
                    try:
                        self.fs.noteoff(9, note)
                    except Exception:
                        pass
                    self.sf_voices.pop(note, None)
                    return

                now = time.monotonic()
                for part in state.get("parts", []):
                    if part.get("finished"):
                        continue
                    part["smart_fade"] = True
                    part["fade_start"] = now
                    part["fade_duration"] = seconds
                    part["fade_start_expression"] = int(part.get("last_expression", 127))
                    part["deadline"] = now + seconds
            return

        # Generated fallback already has a release envelope.
        with self.lock:
            state = self.notes.get(note)
            if state is not None:
                state["held"] = False
                state["released"] = True
                state["release_age"] = 0.0
                state["sustain_deadline"] = None

    def note_off(self, note: int):
        if self.using_soundfont and self.fs is not None:
            with self.lock:
                note = int(note)
                state = self.sf_voices.get(note)
                if not state:
                    return

                state["held"] = False

                if state.get("drums"):
                    try:
                        self.fs.noteoff(9, note)
                    except Exception:
                        pass
                    self.sf_voices.pop(note, None)
                    return

                if self.sustain:
                    now = time.monotonic()
                    for part in state["parts"]:
                        part["deadline"] = now + self.sustain_timeout

                        if part["smart_fade"]:
                            # True fade: looped/sustained patches begin getting
                            # quieter immediately after physical key release.
                            part["fade_start"] = now
                            part["fade_duration"] = self.sustain_timeout
                            part["fade_start_expression"] = 127
                            part["last_expression"] = 127
                        else:
                            # Piano/mallet/plucked patches keep their SoundFont's
                            # own natural decay. Timer is only a safety endpoint.
                            part["fade_start"] = None
                    return

                # Sustain is off: let each SoundFont patch use its normal release.
                for part in state["parts"]:
                    try:
                        self.fs.noteoff(int(part["channel"]), note)
                    except Exception:
                        pass
                    self.free_sf_channels.add(int(part["channel"]))
                self.sf_voices.pop(note, None)
            return

        with self.lock:
            state = self.notes.get(note)
            if not state:
                return
            state["held"] = False
            if self.sustain:
                state["sustain_deadline"] = time.monotonic() + self.sustain_timeout
            else:
                state["released"] = True
                state["release_age"] = 0.0
                state["sustain_deadline"] = None

    def set_sustain_timeout(self, seconds: float):
        seconds = max(0.25, min(15.0, float(seconds)))
        with self.lock:
            self.sustain_timeout = seconds
            now = time.monotonic()

            if self.using_soundfont and self.fs is not None:
                for state in self.sf_voices.values():
                    if state.get("held") or state.get("drums"):
                        continue
                    for part in state.get("parts", []):
                        deadline = part.get("deadline")
                        if deadline is not None:
                            remaining = max(0.01, min(deadline - now, seconds))
                            part["deadline"] = now + remaining

                            if part.get("smart_fade") and part.get("fade_start") is not None:
                                current_expr = int(part.get("last_expression", 127))
                                part["fade_start"] = now
                                part["fade_duration"] = remaining
                                part["fade_start_expression"] = current_expr
                return

            # Generated fallback.
            for state in self.notes.values():
                deadline = state.get("sustain_deadline")
                if deadline is not None:
                    state["sustain_deadline"] = min(deadline, now + seconds)

    def _process_sustain_timeouts(self):
        now = time.monotonic()

        if self.using_soundfont and self.fs is not None:
            with self.lock:
                finished_notes = []

                for note, state in list(self.sf_voices.items()):
                    if state.get("held") or state.get("drums"):
                        continue

                    all_finished = True

                    for part in state.get("parts", []):
                        if part.get("finished"):
                            continue

                        deadline = part.get("deadline")
                        if deadline is None:
                            all_finished = False
                            continue

                        channel = int(part["channel"])

                        if part.get("smart_fade") and part.get("fade_start") is not None:
                            fade_start = float(part["fade_start"])
                            duration = max(0.01, float(part.get("fade_duration") or self.sustain_timeout))
                            start_expr = int(part.get("fade_start_expression", 127))
                            progress = max(0.0, min(1.0, (now - fade_start) / duration))

                            # Slightly convex amplitude curve: audible and smooth
                            # through the tail without feeling like a sudden cliff.
                            # Near-linear amplitude fade. This makes looped patches
                            # such as Distortion Guitar audibly get quieter throughout
                            # the full sustain period instead of feeling flat until late.
                            expression = int(round(start_expr * (1.0 - progress)))
                            expression = max(0, min(127, expression))

                            if expression != int(part.get("last_expression", 127)):
                                try:
                                    self.fs.cc(channel, 11, expression)
                                except Exception:
                                    pass
                                part["last_expression"] = expression

                        if now < deadline:
                            all_finished = False
                            continue

                        # At the endpoint smart-faded voices are already at/near
                        # silence, so stopping the loop cannot create an audible
                        # hard cutoff. Natural-decay voices simply receive Note Off.
                        try:
                            if part.get("smart_fade"):
                                self.fs.cc(channel, 11, 0)
                                self.fs.cc(channel, 120, 0)
                                self.fs.cc(channel, 11, 127)
                            else:
                                self.fs.noteoff(channel, int(note))
                        except Exception:
                            pass

                        self.free_sf_channels.add(channel)
                        part["deadline"] = None
                        part["finished"] = True

                    if all_finished:
                        finished_notes.append(note)

                for note in finished_notes:
                    self.sf_voices.pop(note, None)
            return

        with self.lock:
            for state in self.notes.values():
                deadline = state.get("sustain_deadline")
                if (
                    deadline is not None
                    and deadline <= now
                    and not state["held"]
                    and not state["released"]
                ):
                    state["released"] = True
                    state["release_age"] = 0.0
                    state["sustain_deadline"] = None

    def set_sustain(self, enabled: bool):
        with self.lock:
            self.sustain = bool(enabled)

            if self.using_soundfont and self.fs is not None:
                # CC64 stays off. Sustain is implemented in our own per-note state
                # so looped SoundFont patches cannot ring forever.
                if not self.sustain:
                    now = time.monotonic()

                    for note, state in list(self.sf_voices.items()):
                        if state.get("held") or state.get("drums"):
                            continue

                        # Natural-decay instruments use their native release now.
                        # Smart-fade instruments get a very short smooth ramp so
                        # turning sustain off never causes a click/hard chop.
                        has_smart_fade = any(
                            bool(part.get("smart_fade"))
                            for part in state.get("parts", [])
                        )

                        if not has_smart_fade:
                            for part in state.get("parts", []):
                                channel = int(part["channel"])
                                try:
                                    self.fs.noteoff(channel, int(note))
                                except Exception:
                                    pass
                                self.free_sf_channels.add(channel)
                            self.sf_voices.pop(note, None)
                            continue

                        quick_fade = min(0.30, max(0.08, self.sustain_timeout))
                        for part in state.get("parts", []):
                            if part.get("smart_fade"):
                                part["fade_start"] = now
                                part["fade_duration"] = quick_fade
                                part["fade_start_expression"] = int(
                                    part.get("last_expression", 127)
                                )
                                part["deadline"] = now + quick_fade
                            else:
                                channel = int(part["channel"])
                                try:
                                    self.fs.noteoff(channel, int(note))
                                except Exception:
                                    pass
                                self.free_sf_channels.add(channel)
                                part["deadline"] = None
                                part["finished"] = True
                return

            if not self.sustain:
                for state in self.notes.values():
                    if not state["held"] and not state["released"]:
                        state["released"] = True
                        state["release_age"] = 0.0
                        state["sustain_deadline"] = None

    def panic(self):
        with self.lock:
            self.notes.clear()

            if self.fs is not None:
                # Dedicated melodic voice channels + GM percussion channel.
                for channel in set(SOUNDFONT_VOICE_CHANNELS) | {9}:
                    try:
                        self.fs.cc(channel, 64, 0)
                        self.fs.cc(channel, 123, 0)
                        self.fs.cc(channel, 120, 0)
                        self.fs.cc(channel, 11, 127)
                    except Exception:
                        pass

            self.sf_voices.clear()
            self.free_sf_channels = set(SOUNDFONT_VOICE_CHANNELS)

    @staticmethod
    def _read_wav_file(path: Path) -> Tuple[np.ndarray, int]:
        """
        Load a PCM WAV file into float32 stereo without adding another dependency.
        Supports 8/16/24/32-bit PCM WAV.
        """
        path = Path(path)
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            frame_count = wf.getnframes()
            raw = wf.readframes(frame_count)

        if sample_width == 1:
            data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
            data = (data - 128.0) / 128.0
        elif sample_width == 2:
            data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif sample_width == 3:
            b = np.frombuffer(raw, dtype=np.uint8)
            usable = (len(b) // 3) * 3
            b = b[:usable].reshape(-1, 3)
            vals = (
                b[:, 0].astype(np.int32)
                | (b[:, 1].astype(np.int32) << 8)
                | (b[:, 2].astype(np.int32) << 16)
            )
            negative = (vals & 0x800000) != 0
            vals[negative] -= 1 << 24
            data = vals.astype(np.float32) / 8388608.0
        elif sample_width == 4:
            data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        else:
            raise ValueError(f"Unsupported WAV sample width: {sample_width * 8}-bit")

        if channels <= 0:
            raise ValueError("WAV has no audio channels.")

        frames = len(data) // channels
        if frames <= 0:
            raise ValueError("WAV contains no audio frames.")

        data = data[:frames * channels].reshape(frames, channels)
        if channels == 1:
            stereo = np.repeat(data, 2, axis=1)
        else:
            stereo = data[:, :2]

        return np.ascontiguousarray(stereo, dtype=np.float32), int(sample_rate)

    def _resample_stereo(self, audio: np.ndarray, source_rate: int) -> np.ndarray:
        if source_rate == self.sample_rate:
            return np.ascontiguousarray(audio, dtype=np.float32)

        old_len = len(audio)
        if old_len <= 1:
            return np.ascontiguousarray(audio, dtype=np.float32)

        new_len = max(1, int(round(old_len * self.sample_rate / float(source_rate))))
        x_old = np.arange(old_len, dtype=np.float64)
        x_new = np.linspace(0.0, old_len - 1.0, new_len, dtype=np.float64)

        left = np.interp(x_new, x_old, audio[:, 0]).astype(np.float32)
        right = np.interp(x_new, x_old, audio[:, 1]).astype(np.float32)
        return np.column_stack((left, right)).astype(np.float32)

    def load_playback_slot(self, index: int, path: Path) -> float:
        if not 0 <= index < 10:
            raise IndexError(index)

        audio, rate = self._read_wav_file(Path(path))
        audio = self._resample_stereo(audio, rate)

        with self.playback_lock:
            self.playback_slots[index] = {
                "path": Path(path),
                "audio": audio,
                "position": 0,
                "playing": False,
            }

        return len(audio) / float(self.sample_rate)

    def set_playback_volume(self, value: float):
        self.playback_volume = max(0.0, min(1.5, float(value)))

    def start_playback(self, index: int, restart: bool = True):
        with self.playback_lock:
            slot = self.playback_slots[index]
            if slot["audio"] is None:
                return
            if restart or slot["position"] >= len(slot["audio"]):
                slot["position"] = 0
            slot["playing"] = True

    def stop_playback(self, index: int, reset: bool = True):
        with self.playback_lock:
            slot = self.playback_slots[index]
            slot["playing"] = False
            if reset:
                slot["position"] = 0

    def stop_all_playback(self):
        with self.playback_lock:
            for slot in self.playback_slots:
                slot["playing"] = False
                slot["position"] = 0

    def playback_is_playing(self, index: int) -> bool:
        with self.playback_lock:
            return bool(self.playback_slots[index]["playing"])

    def playback_path(self, index: int) -> Optional[Path]:
        with self.playback_lock:
            path = self.playback_slots[index]["path"]
            return Path(path) if path is not None else None

    def _render_playback(self, frames: int) -> np.ndarray:
        mixed = np.zeros((frames, 2), dtype=np.float32)
        active_count = 0

        with self.playback_lock:
            for slot in self.playback_slots:
                audio = slot["audio"]
                if not slot["playing"] or audio is None:
                    continue

                position = int(slot["position"])
                remaining = len(audio) - position
                if remaining <= 0:
                    slot["playing"] = False
                    slot["position"] = 0
                    continue

                count = min(frames, remaining)
                mixed[:count] += audio[position:position + count]
                slot["position"] = position + count
                active_count += 1

                if slot["position"] >= len(audio):
                    slot["playing"] = False
                    slot["position"] = 0

        if active_count > 1:
            mixed /= math.sqrt(active_count)

        mixed *= self.playback_volume
        return np.clip(mixed, -1.0, 1.0)

    def start_recording(self, mode: str = "regular", playback_indices: Optional[List[int]] = None):
        if mode not in {"regular", "mix", "live_only"}:
            mode = "regular"

        with self.record_lock:
            self.recorded_chunks = []
            self.recording = True
            self.record_mode = mode
            self.record_started_at = time.monotonic()

        if mode in {"mix", "live_only"}:
            for index in playback_indices or []:
                self.start_playback(index, restart=True)

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
        freq = state["freq"] * (2.0 ** (float(state.get("bend_cents", 0.0)) / 1200.0))
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
        env *= float(state.get("expression", 127)) / 127.0
        return wave_data, env, release

    def _finalize_audio_frame(
        self,
        outdata: np.ndarray,
        live_stereo: np.ndarray,
        playback_stereo: np.ndarray,
    ):
        # Audible monitor mix always contains both the live instrument and any
        # manually/automatically playing backing tracks.
        audible = live_stereo + playback_stereo
        audible = np.tanh(audible * 0.92).astype(np.float32)
        outdata[:] = audible

        with self.record_lock:
            if not self.recording:
                return

            if self.record_mode == "mix":
                recorded = audible
            else:
                # "regular" and "live_only" deliberately omit playback from the
                # recording. The distinction is whether playback auto-starts.
                recorded = live_stereo

            self.recorded_chunks.append(
                np.clip(recorded, -1.0, 1.0).astype(np.float32).copy()
            )

    def _callback(self, outdata, frames, time_info, status):
        self._process_sustain_timeouts()
        playback_stereo = self._render_playback(frames)

        if self.using_soundfont and self.fs is not None:
            try:
                with self.lock:
                    raw = np.asarray(self.fs.get_samples(frames))
                # pyFluidSynth returns interleaved signed-16-bit stereo samples.
                live_stereo = raw.reshape(-1, 2).astype(np.float32) / 32768.0
                live_stereo *= self.volume
                live_stereo = np.clip(live_stereo, -1.0, 1.0)
            except Exception:
                # Keep playback running even if FluidSynth has a transient error.
                live_stereo = np.zeros((frames, 2), dtype=np.float32)

            self._finalize_audio_frame(outdata, live_stereo, playback_stereo)
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
        live_stereo = np.column_stack((mixed, mixed)).astype(np.float32)
        self._finalize_audio_frame(outdata, live_stereo, playback_stereo)


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
        self.resize(1550, 980)

        self.synth = SynthEngine()
        self.synth.auto_load_soundfont(resource_dir())
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
        self.recordings_dir = writable_app_dir() / "recordings"
        self.record_started_ui: Optional[float] = None
        self.record_autoplay_slots: List[int] = []
        self.playback_file_labels: List[QLabel] = []
        self.playback_play_buttons: List[QPushButton] = []
        self.playback_record_checks: List[QCheckBox] = []

        # Instrument-native performance state.
        self.performance_mode_override = "auto"

        # Persistent instrument fingering state. These are not chord presets:
        # every string can have its own independently selected finger/fret.
        self.performance_fret_bank = 0

        # Independent stored voicings. Slot selection is separate for bowed and
        # fretted families so switching instrument families does not overwrite
        # the other hand layout.
        # TAB toggles string instruments between stored CHORD voicings and
        # one live FREE fingering/fret state.
        self.performance_string_mode = "chord"

        self.performance_fretted_slot = 0
        self.performance_fretted_selected_string = 0
        self.performance_fretted_slots: List[List[Optional[int]]] = [
            list(v) for v in DEFAULT_GUITAR_CHORD_VOICINGS
        ]
        self.performance_fretted_free_frets: List[Optional[int]] = [0, 0, 0, 0, 0, 0]

        self.performance_bowed_slot = 0
        self.performance_bowed_selected_string = 0
        self.performance_bowed_slots: List[List[int]] = [
            [0, 0, 0, 0] for _ in VOICING_SLOT_KEYS
        ]
        self.performance_bowed_slot_custom: List[bool] = [
            False for _ in VOICING_SLOT_KEYS
        ]
        self.performance_bowed_free_fingers: List[int] = [0, 0, 0, 0]

        self.performance_direct_chord_active: Dict[str, List[Tuple[int, int]]] = {}
        self.performance_direct_chord_tokens: Dict[str, int] = {}
        self.performance_direct_chord_token_counter = 0
        self.performance_free_direct_active: Dict[str, List[Tuple[int, int, int]]] = {}

        self.performance_harp_degree = 0
        self.performance_harp_slot = 0
        self.performance_harp_page = 0

        self.performance_bow_active: Dict[str, Dict[int, int]] = {}
        self.performance_guitar_active: Dict[str, Tuple[int, int]] = {}
        self.performance_wind_pitch_stack: List[str] = []
        self.performance_wind_note: Optional[Tuple[int, int]] = None
        self.performance_breath_held = False
        self.performance_vibrato_held = False
        self.performance_tremolo_held = False
        self.performance_fall_held = False
        self.performance_articulation_started = 0.0
        self.performance_note_started: Dict[int, float] = {}
        self.performance_note_serial: Dict[int, int] = {}
        self.performance_serial_counter = 0
        self.performance_rolls: Dict[str, float] = {}
        self.performance_roll_interval = 0.105
        self.performance_drum_accent = False
        self.performance_breath_level = 0.88

        self.performance_timer = QTimer(self)
        self.performance_timer.setInterval(18)
        self.performance_timer.timeout.connect(self.update_performance_engine)
        self.performance_timer.start()

        self._build_ui()
        QApplication.instance().installEventFilter(self)
        self.update_performance_mode_ui()
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
            "The keyboard adapts to how the selected instrument is actually performed: "
            "keys for piano, a free per-string fretboard for guitar, independent fingering + bow for violin, "
            "breath + fingering for winds, and stick/roll gestures for drums. "
            "Auto performance mode selects the appropriate surface."
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

        controls.addWidget(QLabel("Performance:"))
        self.performance_combo = QComboBox()
        for label, mode_id in PERFORMANCE_MODES:
            self.performance_combo.addItem(label, mode_id)
        self.performance_combo.setCurrentIndex(0)
        self.performance_combo.currentIndexChanged.connect(self.performance_mode_changed)
        controls.addWidget(self.performance_combo)

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

        controls2.addWidget(QLabel("Sustain fade time"))
        self.sustain_fade_slider = QSlider(Qt.Horizontal)
        self.sustain_fade_slider.setRange(5, 150)  # 0.5s to 15.0s
        self.sustain_fade_slider.setValue(40)      # default 4.0s
        self.sustain_fade_slider.setMaximumWidth(150)
        self.sustain_fade_slider.valueChanged.connect(self.sustain_fade_changed)
        controls2.addWidget(self.sustain_fade_slider)

        self.sustain_fade_label = QLabel("4.0s")
        self.sustain_fade_label.setMinimumWidth(42)
        controls2.addWidget(self.sustain_fade_label)

        controls2.addWidget(QLabel("String tuning:"))
        self.tuning_combo = QComboBox()
        self.tuning_combo.addItems(GUITAR_TUNINGS.keys())
        self.tuning_combo.currentIndexChanged.connect(self.performance_tuning_changed)
        self.tuning_combo.setMaximumWidth(180)
        controls2.addWidget(self.tuning_combo)

        outer.addLayout(controls2)

        self.performance_hint_label = QLabel("")
        self.performance_hint_label.setWordWrap(True)
        self.performance_hint_label.setStyleSheet(
            "QLabel { padding: 5px 8px; border: 1px solid #555; border-radius: 5px; }"
        )
        outer.addWidget(self.performance_hint_label)

        record_row = QHBoxLayout()
        self.record_button = QPushButton("● Start Recording")
        self.record_button.clicked.connect(self.toggle_recording)
        record_row.addWidget(self.record_button)

        self.record_time_label = QLabel("REC 00:00")
        record_row.addWidget(self.record_time_label)

        record_row.addWidget(QLabel("Record mode:"))
        self.record_mode_combo = QComboBox()
        for label, mode_id in RECORD_MODES:
            self.record_mode_combo.addItem(label, mode_id)
        self.record_mode_combo.setCurrentIndex(0)
        record_row.addWidget(self.record_mode_combo, 2)

        self.open_recordings_button = QPushButton("Open Recordings Folder")
        self.open_recordings_button.clicked.connect(self.open_recordings_folder)
        record_row.addWidget(self.open_recordings_button)

        self.engine_label = QLabel(self.synth.engine_description())
        record_row.addWidget(self.engine_label)
        self.load_soundfont_button = QPushButton("Load SoundFont (.sf2/.sf3)")
        self.load_soundfont_button.clicked.connect(self.load_soundfont_ui)
        record_row.addWidget(self.load_soundfont_button)
        outer.addLayout(record_row)

        playback_header = QHBoxLayout()
        playback_title = QLabel("Playback WAV slots — check 'On REC' for tracks that should start with recording")
        playback_title.setFont(QFont("Arial", 10, QFont.Bold))
        playback_header.addWidget(playback_title)
        playback_header.addStretch(1)

        playback_header.addWidget(QLabel("Playback volume"))
        self.playback_volume_slider = QSlider(Qt.Horizontal)
        self.playback_volume_slider.setRange(0, 125)
        self.playback_volume_slider.setValue(75)
        self.playback_volume_slider.setMaximumWidth(180)
        self.playback_volume_slider.valueChanged.connect(
            lambda v: self.synth.set_playback_volume(v / 100.0)
        )
        playback_header.addWidget(self.playback_volume_slider)

        self.stop_playback_button = QPushButton("Stop All Playback")
        self.stop_playback_button.clicked.connect(self.stop_all_playback_ui)
        playback_header.addWidget(self.stop_playback_button)
        outer.addLayout(playback_header)

        playback_grid = QGridLayout()
        playback_grid.setHorizontalSpacing(8)
        playback_grid.setVerticalSpacing(6)

        for i in range(10):
            cell = QFrame()
            cell.setFrameShape(QFrame.StyledPanel)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(6, 5, 6, 5)
            cell_layout.setSpacing(4)

            top = QHBoxLayout()
            slot_label = QLabel(f"Slot {i + 1}")
            slot_label.setFont(QFont("Arial", 9, QFont.Bold))
            top.addWidget(slot_label)

            rec_check = QCheckBox("On REC")
            rec_check.setChecked(False)
            rec_check.setEnabled(False)
            top.addWidget(rec_check)
            top.addStretch(1)
            cell_layout.addLayout(top)

            file_label = QLabel("No WAV loaded")
            file_label.setMinimumWidth(150)
            file_label.setMaximumWidth(230)
            file_label.setToolTip("No WAV loaded")
            cell_layout.addWidget(file_label)

            buttons = QHBoxLayout()
            load_button = QPushButton("Load WAV")
            load_button.clicked.connect(
                lambda checked=False, idx=i: self.load_playback_slot_ui(idx)
            )
            buttons.addWidget(load_button)

            play_button = QPushButton("▶ Play")
            play_button.setEnabled(False)
            play_button.clicked.connect(
                lambda checked=False, idx=i: self.toggle_playback_slot(idx)
            )
            buttons.addWidget(play_button)
            cell_layout.addLayout(buttons)

            self.playback_file_labels.append(file_label)
            self.playback_play_buttons.append(play_button)
            self.playback_record_checks.append(rec_check)

            playback_grid.addWidget(cell, i // 5, i % 5)

        outer.addLayout(playback_grid)

        self.record_timer = QTimer(self)
        self.record_timer.setInterval(250)
        self.record_timer.timeout.connect(self.update_record_timer)

        self.playback_ui_timer = QTimer(self)
        self.playback_ui_timer.setInterval(200)
        self.playback_ui_timer.timeout.connect(self.update_playback_ui)
        self.playback_ui_timer.start()

        # Prevent piano keystrokes from changing whichever GUI control was last clicked.
        # Mouse interaction still works normally. While Capture is ON, the eventFilter
        # below also consumes Qt key events before controls can act on them.
        for control in (
            self.profile_combo, self.scale_combo, self.root_combo, self.instrument_combo,
            self.performance_combo, self.tuning_combo,
            self.capture_box, panic, self.volume_slider, octave_down, octave_up,
            octave_reset, self.sustain_toggle_button, self.sustain_fade_slider,
            self.record_button, self.record_mode_combo, self.playback_volume_slider,
            self.stop_playback_button, self.open_recordings_button, self.load_soundfont_button
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
                str(writable_app_dir()),
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
        self.update_performance_mode_ui()
        self.refresh_button_labels()
        self.engine_label.setText(self.synth.engine_description())
        self.status.setText(
            f"Loaded {Path(path).name}. {len(self.synth.instrument_names())} instrument presets are available."
        )

    # ------------------------------------------------------------------
    # Instrument-native performance layer
    # ------------------------------------------------------------------

    def _auto_performance_mode(self, instrument_name: Optional[str] = None) -> str:
        name = (instrument_name or self.instrument_combo.currentText() or "").lower()

        if name.startswith("drums:"):
            return "drums"

        if any(token in name for token in (
            "violin", "viola", "cello", "contrabass", "fiddle", "tremolo strings"
        )):
            return "bowed"

        if any(token in name for token in (
            "pizzicato strings", "orchestral harp", "kalimba", "koto"
        )):
            return "plucked"

        physical_bass = (
            "acoustic bass" in name
            or "electric bass" in name
            or "fretless bass" in name
            or "slap bass" in name
        )
        if physical_bass or any(token in name for token in (
            "guitar", "banjo", "sitar", "shamisen"
        )):
            return "fretted"

        if any(token in name for token in (
            "trumpet", "trombone", "tuba", "french horn", "brass",
            "sax", "oboe", "english horn", "bassoon", "clarinet",
            "piccolo", "flute", "recorder", "pan flute", "blown bottle",
            "shakuhachi", "whistle", "ocarina", "harmonica", "bag pipe",
            "shanai"
        )):
            return "wind"

        # Pianos, organs, synths, mallets, accordion, pads, choirs, etc. retain
        # the direct note surface because discrete key presses already fit them.
        return "keys"

    def effective_performance_mode(self) -> str:
        selected = self.performance_combo.currentData() if hasattr(self, "performance_combo") else "auto"
        if selected in (None, "auto"):
            return self._auto_performance_mode()
        return str(selected)

    def performance_mode_changed(self):
        self.performance_mode_override = self.performance_combo.currentData() or "auto"
        self.panic()
        self.update_performance_mode_ui()
        self.refresh_button_labels()

    def performance_tuning_changed(self):
        mode = self.effective_performance_mode()
        if mode == "fretted":
            self._clear_performance_notes()
            self.status.setText(f"String tuning: {self.tuning_combo.currentText()}")
            self.refresh_button_labels()

    def update_performance_mode_ui(self):
        mode = self.effective_performance_mode()
        self.sustain_toggle_button.setEnabled(True)
        self.tuning_combo.setEnabled(mode == "fretted")

        if mode == "bowed":
            _, string_names = self._bowed_tuning()
            fingers = self._current_bowed_fingers()
            finger_text = ", ".join(
                f"{name}:{fingers[i]}" for i, name in enumerate(string_names[:4])
            )
            if self.performance_string_mode == "chord":
                self.performance_hint_label.setText(
                    f"BOWED CHORD MODE — each Caps→Enter key PLAYS its chord immediately. "
                    f"Active slot {self.performance_bowed_slot + 1}: "
                    f"{self._transposed_common_chord_label(self.performance_bowed_slot)}. "
                    "13 editable starter chords: C, G, D, A, E, Am, Em, Dm, F, C7, G7, A7, E7. "
                    "Customize the active chord with Z/X/C/V selecting a string and `/1–0/-/= "
                    "setting its finger position; PgUp/PgDn spans 0–36. Press the chord key itself "
                    "to perform the rolled bow chord. TAB = FREE mode. "
                    f"Current fingers: {finger_text}. Sustain, Scale, Root and Octave still apply."
                )
            else:
                bank_lo = self.performance_fret_bank + 1
                bank_hi = min(36, self.performance_fret_bank + 12)
                row_names = list(reversed(string_names))
                row_desc = ", ".join(f"S{i + 1}={name}" for i, name in enumerate(row_names))
                self.performance_hint_label.setText(
                    f"BOWED FREE MODE — simultaneous multi-string grid. {row_desc}. "
                    f"Every string row currently exposes positions {bank_lo}–{bank_hi} at once. "
                    "PgUp/PgDn moves the whole board 1–12 → 13–24 → 25–36. "
                    "Hold CAPS + any grid key for that row's open string (position 0). "
                    "Backspace = hold sustain; Toggle Sustain also works. "
                    "Insert = vibrato, Home = tremolo, End = staccato. TAB = CHORD mode. "
                    "Scale, Root and Octave still apply."
                )

        elif mode == "fretted":
            tuning = self._fretted_tuning()
            frets = self._current_fretted_frets()
            shown = []
            for i in range(len(tuning)):
                fret = frets[i] if i < len(frets) else 0
                shown.append("X" if fret is None else str(fret))
            if self.performance_string_mode == "chord":
                self.performance_hint_label.setText(
                    f"FRETTED CHORD MODE — each Caps→Enter key PLAYS its chord immediately. "
                    f"Active slot {self.performance_fretted_slot + 1}: "
                    f"{self._transposed_common_chord_label(self.performance_fretted_slot)}. "
                    "13 editable starter shapes: C, G, D, A, E, Am, Em, Dm, F, C7, G7, A7, E7. "
                    "Customize the active chord with Z/X/C/V/B/N selecting S6→S1 and `/1–0/-/= "
                    "setting its fret; PgUp/PgDn spans 0–36; Delete mutes the string; Home opens "
                    "the active shape. Press the chord key itself to hear the edited chord. "
                    "TAB = FREE mode. "
                    f"Current frets: {' / '.join(shown)}. Sustain, Scale, Root and Octave apply."
                )
            else:
                bank_lo = self.performance_fret_bank + 1
                bank_hi = min(36, self.performance_fret_bank + 12)
                string_count = len(tuning)
                self.performance_hint_label.setText(
                    f"FRETTED FREE MODE — full {string_count}×12 physical fretboard page. "
                    f"Top keyboard row = S1, next = S2, continuing to S{string_count}; "
                    f"all strings simultaneously expose frets {bank_lo}–{bank_hi}. "
                    "PgUp/PgDn moves the whole board 1–12 → 13–24 → 25–36. "
                    "Hold CAPS + any grid key for that row's open string (fret 0). "
                    "No string-selection step and no second strum action. "
                    "Backspace = hold sustain; Toggle Sustain also works. "
                    "Insert = vibrato, Home = bend up, End = bend down. TAB = CHORD mode. "
                    "Scale, Root and Octave still apply."
                )

        elif mode == "wind":
            self.performance_hint_label.setText(
                "WIND / BRASS — main letter surface controls pitch/fingering. Hold SPACE as breath; "
                "changing pitch while breath stays held creates a connected phrase. [ vibrato, ] tongue, "
                "\\ fall; ↑/↓ breath intensity. Right Ctrl = hold sustain; Toggle Sustain also works. "
                "Scale, Root and Octave remain global."
            )
        elif mode == "drums":
            self.performance_hint_label.setText(
                "DRUM KIT — Z kick, X snare, C/V closed/open hi-hat, B ride, N crash, M/,/. toms. "
                "Q/W/E/R are hold-to-roll controls; T flam, Y drag; ↑/↓ roll speed; Shift accent. "
                "Closed hi-hat chokes open hi-hat. Scale/Root/Octave do not transpose GM drum numbers."
            )
        elif mode == "plucked":
            if self.performance_string_mode == "chord":
                chord_name = self._transposed_common_chord_label(self.performance_harp_slot)
                self.performance_hint_label.setText(
                    f"HARP CHORD MODE — Caps→Enter are 13 direct-play common chord slots. "
                    f"Active slot {self.performance_harp_slot + 1}: {chord_name}. "
                    "Z/X/C/V/B/N/M are the 1st→7th individual notes of that chord voicing, "
                    "so they pluck chord tones rather than trigger different chords. "
                    "↑ = arpeggio up, ↓ = arpeggio down. TAB = FREE mode. "
                    "Space and Toggle Sustain work; Scale, Root and Octave remain global."
                )
            else:
                page_start = self.performance_harp_page * 12 + 1
                page_end = page_start + len(HARP_FREE_KEYS) - 1
                self.performance_hint_label.setText(
                    f"HARP FREE MODE — every playable key is an individual harp string/note. "
                    f"This page exposes string positions {page_start}–{page_end} across the keyboard. "
                    "PgUp/PgDn shifts the whole harp by 12 strings. TAB = CHORD mode. "
                    "Space and Toggle Sustain work; Scale determines the string tuning, while "
                    "Root and Octave shift the whole harp."
                )
        else:
            self.performance_hint_label.setText(
                "KEYS / PIANO GRID — direct whole-keyboard note mapping. Space = hold sustain; "
                "Scale, Root and Octave work normally."
            )

    def _performance_transform_note(self, raw_note: int) -> int:
        """
        Global musical transform used by physical-instrument modes.

        Root acts as transposition/key centre, Octave shifts the entire
        instrument, and a non-Chromatic Scale quantizes the physical result to
        the selected scale. Chromatic retains every physical semitone.
        """
        root_pc = self.root_combo.currentIndex()
        note = (
            int(raw_note)
            + root_pc
            + self.octave_shift * 12
            + self.temp_octave_shift
        )

        scale_name = self.scale_combo.currentText()
        scale = list(SCALES.get(scale_name, list(range(12))))
        if scale_name != "Chromatic" and scale:
            allowed = {(root_pc + interval) % 12 for interval in scale}
            # Prefer the nearest scale tone; ties favour upward movement.
            for distance in range(0, 7):
                candidates = [0] if distance == 0 else [distance, -distance]
                for offset in candidates:
                    candidate = note + offset
                    if candidate % 12 in allowed:
                        note = candidate
                        return max(0, min(127, note))

        return max(0, min(127, note))

    def _current_fretted_frets(self) -> List[Optional[int]]:
        return self.performance_fretted_slots[self.performance_fretted_slot]

    def _default_bowed_fingers_for_slot(self, slot: int) -> List[int]:
        tuning, _ = self._bowed_tuning()
        pcs = COMMON_CHORD_PITCH_CLASSES[int(slot) % len(COMMON_CHORD_PITCH_CLASSES)]
        fingers: List[int] = []
        for open_note in tuning[:4]:
            chosen = 0
            for position in range(13):
                if (int(open_note) + position) % 12 in pcs:
                    chosen = position
                    break
            fingers.append(chosen)
        while len(fingers) < 4:
            fingers.append(0)
        return fingers[:4]

    def _current_bowed_fingers(self) -> List[int]:
        if (
            self.performance_string_mode == "chord"
            and not self.performance_bowed_slot_custom[self.performance_bowed_slot]
        ):
            return self._default_bowed_fingers_for_slot(self.performance_bowed_slot)
        return self.performance_bowed_slots[self.performance_bowed_slot]

    def _materialize_bowed_slot_for_edit(self):
        slot = self.performance_bowed_slot
        if not self.performance_bowed_slot_custom[slot]:
            self.performance_bowed_slots[slot] = self._default_bowed_fingers_for_slot(slot)
            self.performance_bowed_slot_custom[slot] = True

    def _stop_all_free_direct_notes(self):
        for entries in list(self.performance_free_direct_active.values()):
            for note, serial, _string_index in entries:
                self._perf_stop_note(note, serial, release_fade=0.16)
        self.performance_free_direct_active.clear()

    def _stop_direct_chord(self, key_id: str, release_fade: float = 0.16):
        self.performance_direct_chord_tokens.pop(key_id, None)
        entries = self.performance_direct_chord_active.pop(key_id, [])
        for note, serial in entries:
            self._perf_stop_note(note, serial, release_fade=release_fade)

    def _stop_all_direct_chords(self):
        mode = self.effective_performance_mode()
        fade = 0.22 if mode == "bowed" else 0.12
        for key_id in list(self.performance_direct_chord_active):
            self._stop_direct_chord(key_id, release_fade=fade)
        self.performance_direct_chord_tokens.clear()

    def _toggle_string_performance_mode(self):
        mode = self.effective_performance_mode()
        if mode not in {"fretted", "bowed", "plucked"}:
            return
        self._stop_all_direct_chords()
        self._stop_all_free_direct_notes()
        self.performance_string_mode = (
            "free" if self.performance_string_mode == "chord" else "chord"
        )
        if self.performance_string_mode == "free":
            self.performance_fret_bank = 0
            if mode == "plucked":
                self.performance_harp_page = 0
        self.update_performance_mode_ui()
        self.refresh_button_labels()
        self.status.setText(
            f"Performance mode: {self.performance_string_mode.upper()} — "
            "Tab toggles CHORD/FREE."
        )

    def _transposed_common_chord_label(self, index: int) -> str:
        base = COMMON_CHORD_NAMES[int(index) % len(COMMON_CHORD_NAMES)]
        root_shift = self.root_combo.currentIndex()
        if len(base) >= 2 and base[1] == "#":
            root_name, suffix = base[:2], base[2:]
        else:
            root_name, suffix = base[:1], base[1:]
        try:
            pc = NOTE_NAMES.index(root_name)
        except ValueError:
            return base
        return NOTE_NAMES[(pc + root_shift) % 12] + suffix

    def _select_fretted_slot(self, index: int):
        index = max(0, min(len(VOICING_SLOT_KEYS) - 1, int(index)))
        if index == self.performance_fretted_slot:
            return
        self.performance_fretted_slot = index
        self._revoice_active_guitar_strings()
        self.update_performance_mode_ui()
        self.refresh_button_labels()

    def _select_bowed_slot(self, index: int):
        index = max(0, min(len(VOICING_SLOT_KEYS) - 1, int(index)))
        if index == self.performance_bowed_slot:
            return
        self.performance_bowed_slot = index
        self._revoice_active_bows()
        self.update_performance_mode_ui()
        self.refresh_button_labels()

    def _diatonic_scale_intervals(self) -> List[int]:
        scale = list(SCALES[self.scale_combo.currentText()])
        if len(scale) < 7 or self.scale_combo.currentText() == "Chromatic":
            if "Minor" in self.scale_combo.currentText():
                return [0, 2, 3, 5, 7, 8, 10]
            return [0, 2, 4, 5, 7, 9, 11]
        return scale[:7]

    def _harp_chord_pitch_classes(self) -> Tuple[int, int, int]:
        degree = int(self.performance_harp_degree)
        scale = self._diatonic_scale_intervals()
        root_pc = self.root_combo.currentIndex()
        tones = []
        for step in (degree, degree + 2, degree + 4):
            octave, idx = divmod(step, 7)
            tones.append((root_pc + scale[idx] + octave * 12) % 12)
        return tuple(tones)

    @staticmethod
    def _nearest_pitch_class_at_or_above(base_note: int, pitch_classes, search: int = 12) -> int:
        for offset in range(max(1, int(search)) + 1):
            candidate = int(base_note) + offset
            if candidate % 12 in pitch_classes:
                return candidate
        return int(base_note)

    def _harp_chord_notes(self) -> List[int]:
        pcs = self._harp_chord_pitch_classes()
        base = 48 + self.octave_shift * 12 + self.temp_octave_shift
        root = self._nearest_pitch_class_at_or_above(base, {pcs[0]}, 12)
        third = self._nearest_pitch_class_at_or_above(root + 2, {pcs[1]}, 12)
        fifth = self._nearest_pitch_class_at_or_above(third + 2, {pcs[2]}, 12)
        return [root, third, fifth]

    def _harp_arpeggiate(self, upward: bool):
        triad = self._harp_chord_notes()
        notes = triad + [n + 12 for n in triad if n + 12 <= 127]
        if not upward:
            notes.reverse()
        for i, note in enumerate(notes):
            self._perf_timed_note(note, 0.92, 1.25, i * 70)

    def _harp_slot_pitch_classes(self, slot: int):
        root_shift = self.root_combo.currentIndex()
        base = COMMON_CHORD_PITCH_CLASSES[int(slot) % len(COMMON_CHORD_PITCH_CLASSES)]
        return {(pc + root_shift) % 12 for pc in base}

    def _harp_slot_notes(self, slot: int, count: int = 7) -> List[int]:
        """
        Build an ascending harp voicing from the active chord's pitch classes.
        Triads repeat over octaves; seventh chords include the seventh before
        repeating. Used both for direct chord playback and Z-M chord-tone keys.
        """
        pcs = self._harp_slot_pitch_classes(slot)
        base = 48 + self.octave_shift * 12 + self.temp_octave_shift
        notes: List[int] = []
        cursor = max(0, base)
        while cursor <= 127 and len(notes) < int(count):
            if cursor % 12 in pcs:
                notes.append(cursor)
            cursor += 1
        return notes

    def _play_harp_chord_slot(self, slot: int):
        self.performance_harp_slot = max(0, min(len(VOICING_SLOT_KEYS) - 1, int(slot)))
        notes = self._harp_slot_notes(self.performance_harp_slot, 7)

        # A real harp chord is slightly spread rather than a piano-perfect
        # simultaneous block.
        for i, note in enumerate(notes):
            self._perf_timed_note(note, max(0.68, 0.98 - i * 0.035), 2.0, i * 22)

        self.update_performance_mode_ui()
        self.refresh_button_labels()

    def _harp_chord_tone_note(self, tone_index: int) -> Optional[int]:
        notes = self._harp_slot_notes(self.performance_harp_slot, 7)
        if not notes:
            return None
        index = max(0, min(len(notes) - 1, int(tone_index)))
        return notes[index]

    def _harp_free_note(self, key_id: str) -> Optional[int]:
        index = HARP_FREE_KEY_INDEX.get(key_id)
        if index is None:
            return None

        # Harp strings follow the selected scale. Chromatic gives a chromatic
        # harp; other scales constrain every string to that scale.
        scale = list(SCALES[self.scale_combo.currentText()])
        if not scale:
            scale = list(range(12))
        root_pc = self.root_combo.currentIndex()

        # One page shift = 12 scale steps/strings.
        degree = int(index) + self.performance_harp_page * 12
        base = 36 + self.octave_shift * 12 + self.temp_octave_shift
        return max(0, min(127, scale_note(degree, root_pc, base, scale)))

    def _harp_free_play(self, key_id: str):
        note = self._harp_free_note(key_id)
        if note is None:
            return
        serial = self._perf_start_note(note, 0.96)
        self.performance_free_direct_active.setdefault(key_id, []).append((note, serial, -1))

    def _harp_free_stop(self, key_id: str):
        entries = self.performance_free_direct_active.pop(key_id, [])
        for note, serial, _ in entries:
            self._perf_stop_note(note, serial, release_fade=0.16)

    def _harp_arpeggiate_slot(self, upward: bool):
        notes = self._harp_slot_notes(self.performance_harp_slot, 10)
        if not upward:
            notes = list(reversed(notes))
        for i, note in enumerate(notes):
            self._perf_timed_note(note, 0.91, 1.8, i * 58)

    def _bowed_tuning(self) -> Tuple[List[int], List[str]]:
        name = self.instrument_combo.currentText()
        for instrument_name, value in BOWED_TUNINGS.items():
            if instrument_name.lower() in name.lower():
                return list(value[0]), list(value[1])
        return list(BOWED_TUNINGS["Violin"][0]), list(BOWED_TUNINGS["Violin"][1])

    def _bowed_voicing(self) -> List[int]:
        tuning, _ = self._bowed_tuning()
        fingers = self._current_bowed_fingers()
        result = []
        for i, open_note in enumerate(tuning):
            finger = fingers[i] if i < len(fingers) else 0
            result.append(self._performance_transform_note(open_note + int(finger)))
        return result

    def _fretted_tuning(self) -> List[int]:
        name = self.instrument_combo.currentText().lower()
        if "bass" in name and "brass" not in name:
            return [28, 33, 38, 43]  # E1 A1 D2 G2
        if "banjo" in name:
            return [43, 50, 55, 59, 62]
        if "shamisen" in name:
            return [45, 52, 57]
        if "koto" in name:
            return [50, 55, 57, 62, 64, 69]
        return list(GUITAR_TUNINGS[self.tuning_combo.currentText()])

    def _fretted_voicing(self) -> List[Optional[int]]:
        tuning = self._fretted_tuning()
        frets = self._current_fretted_frets()
        result: List[Optional[int]] = []
        for i, open_note in enumerate(tuning):
            fret = frets[i] if i < len(frets) else 0
            if fret is None:
                result.append(None)
            else:
                result.append(self._performance_transform_note(open_note + int(fret)))
        return result

    def _selected_fretted_indices(self) -> List[int]:
        tuning_len = len(self._fretted_tuning())
        held = [
            i for i, key in enumerate(FRETTED_EDIT_KEYS[:tuning_len])
            if key in self.held_ids
        ]
        if held:
            return held
        return [min(self.performance_fretted_selected_string, max(0, tuning_len - 1))]

    def _selected_bowed_indices(self) -> List[int]:
        held = [
            i for i, key in enumerate(BOWED_EDIT_KEYS)
            if key in self.held_ids
        ]
        if held:
            return held
        return [min(self.performance_bowed_selected_string, 3)]

    def _set_fretted_position(self, fret: Optional[int]):
        frets = self._current_fretted_frets()
        for index in self._selected_fretted_indices():
            if index < len(frets):
                frets[index] = fret
        self._revoice_active_guitar_strings()
        self.update_performance_mode_ui()
        self.refresh_button_labels()

    def _set_bowed_position(self, position: int):
        position = max(0, min(36, int(position)))
        self._materialize_bowed_slot_for_edit()
        fingers = self._current_bowed_fingers()
        for index in self._selected_bowed_indices():
            if index < len(fingers):
                fingers[index] = position
        self._revoice_active_bows()
        self.update_performance_mode_ui()
        self.refresh_button_labels()

    def _revoice_active_guitar_strings(self):
        if not self.performance_guitar_active:
            return
        active_keys = list(self.performance_guitar_active)
        for key_id in active_keys:
            state = self.performance_guitar_active.pop(key_id, None)
            if state:
                self._perf_stop_note(state[0], state[1], release_fade=0.06)
        for key_id in active_keys:
            if key_id in self.held_ids:
                self._start_guitar_string(key_id)

    def _wind_base_note(self) -> int:
        name = self.instrument_combo.currentText().lower()
        if "piccolo" in name:
            return 72
        if "flute" in name or "recorder" in name or "whistle" in name:
            return 60
        if "soprano sax" in name:
            return 60
        if "alto sax" in name or "clarinet" in name or "trumpet" in name:
            return 55
        if "tenor sax" in name or "oboe" in name or "english horn" in name or "french horn" in name:
            return 48
        if "baritone sax" in name or "trombone" in name or "bassoon" in name:
            return 41
        if "tuba" in name:
            return 34
        return 50

    def _wind_note_for_index(self, index: int) -> int:
        scale = list(SCALES[self.scale_combo.currentText()])
        if not scale:
            scale = list(range(12))
        root_pc = self.root_combo.currentIndex()
        base = self._wind_base_note() + self.octave_shift * 12 + self.temp_octave_shift
        return max(0, min(127, scale_note(int(index), root_pc, base, scale)))

    def _perf_start_note(self, note: int, velocity: float = 1.0) -> int:
        note = int(max(0, min(127, note)))
        self.performance_serial_counter += 1
        serial = self.performance_serial_counter
        self.performance_note_serial[note] = serial
        self.performance_note_started[note] = time.monotonic()
        self.synth.note_on(note, max(0.05, min(1.25, float(velocity))))
        return serial

    def _perf_stop_note(self, note: int, serial: Optional[int] = None,
                        release_fade: Optional[float] = None):
        note = int(note)
        if serial is not None and self.performance_note_serial.get(note) != serial:
            return
        self.synth.reset_note_performance_controls(note)
        if release_fade is not None and not self.synth.sustain:
            self.synth.note_off_with_fade(note, release_fade)
        else:
            # When global sustain is active this enters the existing smart
            # sustain/fade engine instead of bypassing it.
            self.synth.note_off(note)
        if serial is None or self.performance_note_serial.get(note) == serial:
            self.performance_note_serial.pop(note, None)
            self.performance_note_started.pop(note, None)

    def _perf_timed_note(self, note: int, velocity: float = 1.0, duration: float = 0.7,
                         delay_ms: int = 0):
        def start():
            serial = self._perf_start_note(note, velocity)
            QTimer.singleShot(
                max(20, int(duration * 1000)),
                lambda n=note, s=serial: self._perf_stop_note(n, s),
            )
        if delay_ms > 0:
            QTimer.singleShot(int(delay_ms), start)
        else:
            start()

    def _clear_performance_notes(self):
        for note in list(self.performance_note_serial):
            self.synth.reset_note_performance_controls(note)
            self.synth.note_off(note)
        self.performance_note_serial.clear()
        self.performance_note_started.clear()
        self.performance_bow_active.clear()
        self.performance_guitar_active.clear()
        self.performance_direct_chord_active.clear()
        self.performance_direct_chord_tokens.clear()
        self.performance_free_direct_active.clear()
        self.performance_wind_note = None
        self.performance_breath_held = False
        self.performance_vibrato_held = False
        self.performance_tremolo_held = False
        self.performance_fall_held = False
        self.performance_rolls.clear()

    def _revoice_active_bows(self):
        if not self.performance_bow_active:
            return

        active_keys = list(self.performance_bow_active.keys())
        # Stop current voices first; reconstruct the held bow geometry against
        # the new left-hand chord/fingering state.
        for note in list(self.performance_note_serial):
            if any(note in note_map for note_map in self.performance_bow_active.values()):
                self._perf_stop_note(note)
        self.performance_bow_active.clear()

        for bow_key in active_keys:
            self._start_bow_selector(bow_key, revoice=True)

    def _start_bow_selector(self, key_id: str, revoice: bool = False):
        if key_id not in BOW_KEYS:
            return
        voicing = self._bowed_voicing()
        indices = BOW_KEYS[key_id]

        # Physical bowed strings can sustain one string or an adjacent pair.
        notes = [voicing[i] for i in indices if i < len(voicing)]
        if not notes:
            return

        if "E" in self.held_ids and not revoice:
            # Staccato bow articulation: short, explicit stroke.
            for note in notes:
                self._perf_timed_note(note, velocity=1.05, duration=0.16)
            return

        note_map: Dict[int, int] = {}
        for note in notes:
            if note in self.performance_note_serial:
                # Shared pitch from overlapping selectors: don't retrigger it.
                note_map[note] = self.performance_note_serial[note]
            else:
                note_map[note] = self._perf_start_note(note, 0.94 if revoice else 1.0)
        self.performance_bow_active[key_id] = note_map

    def _stop_bow_selector(self, key_id: str):
        note_map = self.performance_bow_active.pop(key_id, None)
        if not note_map:
            return

        # Only release a pitch if no other held bow selector still references it.
        remaining_notes = {
            note
            for other in self.performance_bow_active.values()
            for note in other
        }
        for note, serial in note_map.items():
            if note not in remaining_notes:
                self._perf_stop_note(note, serial, release_fade=0.22)

    def _bowed_sweep(self):
        voicing = self._bowed_voicing()
        # A real three/four-string violin chord is normally rolled because the
        # curved bridge prevents one sustained bow plane across all strings.
        for index, note in enumerate(voicing):
            self._perf_timed_note(
                note,
                velocity=max(0.72, 1.05 - index * 0.06),
                duration=0.8,
                delay_ms=index * 38,
            )

    def _strum_fretted(self, upward: bool = False, muted: bool = False,
                       harmonic: bool = False):
        voicing = self._fretted_voicing()
        order = [i for i, note in enumerate(voicing) if note is not None]
        if upward:
            order.reverse()

        name = self.instrument_combo.currentText().lower()
        if muted or "muted" in name:
            duration = 0.16
        elif "distortion" in name or "overdriven" in name or "harmonic" in name:
            duration = 1.6
        else:
            duration = 1.15

        for stroke_pos, string_index in enumerate(order):
            base_note = voicing[string_index]
            if base_note is None:
                continue
            note = min(127, int(base_note) + (12 if harmonic else 0))
            # Micro-timing + a small deterministic velocity gradient keeps a
            # six-string strum from sounding like one piano chord event.
            velocity = 1.02 - stroke_pos * 0.035
            if upward:
                velocity *= 0.92
            if muted:
                velocity *= 0.82
            self._perf_timed_note(
                note,
                velocity=velocity,
                duration=duration,
                delay_ms=stroke_pos * 14,
            )

    def _start_guitar_string(self, key_id: str):
        string_index = GUITAR_STRING_KEYS[key_id]
        voicing = self._fretted_voicing()
        if string_index >= len(voicing):
            return
        note = voicing[string_index]
        if note is None:
            return
        serial = self._perf_start_note(int(note), 0.98)
        self.performance_guitar_active[key_id] = (note, serial)

    def _stop_guitar_string(self, key_id: str):
        state = self.performance_guitar_active.pop(key_id, None)
        if state:
            self._perf_stop_note(state[0], state[1], release_fade=0.12)

    def _start_direct_chord_note(self, key_id: str, token: int,
                                 note: int, velocity: float):
        if self.performance_direct_chord_tokens.get(key_id) != token:
            return
        if key_id not in self.held_ids:
            return
        serial = self._perf_start_note(int(note), velocity)
        self.performance_direct_chord_active.setdefault(key_id, []).append(
            (int(note), serial)
        )

    def _play_fretted_chord_key(self, key_id: str, slot: int):
        self._stop_all_direct_chords()
        self.performance_fretted_slot = int(slot)
        voicing = self._fretted_voicing()
        notes = list(dict.fromkeys(int(n) for n in voicing if n is not None))
        self.performance_direct_chord_token_counter += 1
        token = self.performance_direct_chord_token_counter
        self.performance_direct_chord_tokens[key_id] = token
        self.performance_direct_chord_active[key_id] = []
        for i, note in enumerate(notes):
            velocity = max(0.68, 1.04 - i * 0.035)
            QTimer.singleShot(
                i * 14,
                lambda k=key_id, t=token, n=note, v=velocity:
                    self._start_direct_chord_note(k, t, n, v)
            )
        self.update_performance_mode_ui()
        self.refresh_button_labels()

    def _play_bowed_chord_key(self, key_id: str, slot: int):
        self._stop_all_direct_chords()
        self.performance_bowed_slot = int(slot)
        voicing = self._bowed_voicing()
        notes = list(dict.fromkeys(int(n) for n in voicing))
        self.performance_direct_chord_token_counter += 1
        token = self.performance_direct_chord_token_counter
        self.performance_direct_chord_tokens[key_id] = token
        self.performance_direct_chord_active[key_id] = []
        for i, note in enumerate(notes):
            velocity = max(0.72, 1.02 - i * 0.05)
            QTimer.singleShot(
                i * 34,
                lambda k=key_id, t=token, n=note, v=velocity:
                    self._start_direct_chord_note(k, t, n, v)
            )
        self.update_performance_mode_ui()
        self.refresh_button_labels()

    def _free_grid_key_info(self, key_id: str, bowed: bool):
        """Return (tuning_index, displayed_string_number, fret) for a FREE-grid key."""
        info = FREE_STRING_FRET_KEY_INFO.get(key_id)
        if info is None:
            return None
        row_index, position = info
        if bowed:
            tuning, _names = self._bowed_tuning()
        else:
            tuning = self._fretted_tuning()
        string_count = len(tuning)
        if row_index >= string_count:
            return None
        # Tunings are stored low->high; grid rows are S1(high)->S6(low).
        tuning_index = string_count - 1 - row_index
        displayed_string_number = row_index + 1
        # Caps is the open-string modifier so fret/position 0 remains available.
        if "CAPS" in self.held_ids:
            fret = 0
        else:
            fret = max(1, min(36, self.performance_fret_bank + int(position)))
        return tuning_index, displayed_string_number, fret

    def _start_free_fret_note(self, key_id: str, bowed: bool):
        info = self._free_grid_key_info(key_id, bowed)
        if info is None:
            return
        tuning_index, displayed_string_number, fret = info
        if bowed:
            tuning, names = self._bowed_tuning()
            raw_note = int(tuning[tuning_index]) + fret
            string_name = names[tuning_index] if tuning_index < len(names) else f"S{displayed_string_number}"
        else:
            tuning = self._fretted_tuning()
            raw_note = int(tuning[tuning_index]) + fret
            string_name = f"S{displayed_string_number}"
        note = self._performance_transform_note(raw_note)
        serial = self._perf_start_note(note, 0.98)
        self.performance_free_direct_active.setdefault(key_id, []).append((note, serial, tuning_index))
        if bowed and "END" in self.held_ids:
            QTimer.singleShot(125, lambda k=key_id: self._stop_free_fret_note(k, bowed=True))
        family = "Bowed" if bowed else "Fretted"
        extra = f" ({string_name})" if bowed else ""
        self.status.setText(f"{family} FREE: S{displayed_string_number}:{fret}{extra} → {midi_name(note)}")

    def _stop_free_fret_note(self, key_id: str, bowed: bool):
        entries = self.performance_free_direct_active.pop(key_id, [])
        fade = 0.22 if bowed else 0.11
        for note, serial, _string_index in entries:
            self._perf_stop_note(note, serial, release_fade=fade)

    def _free_grid_label(self, key_id: str, bowed: bool) -> Optional[str]:
        raw = FREE_STRING_FRET_KEY_INFO.get(key_id)
        if raw is None:
            return None
        row_index, position = raw
        if bowed:
            tuning, names = self._bowed_tuning()
        else:
            tuning = self._fretted_tuning()
            names = []
        if row_index >= len(tuning):
            return None
        fret = max(1, min(36, self.performance_fret_bank + int(position)))
        string_number = row_index + 1
        if bowed:
            tuning_index = len(tuning) - 1 - row_index
            string_name = names[tuning_index] if tuning_index < len(names) else ""
            return f"S{string_number}({string_name}):{fret}"
        return f"S{string_number}:{fret}"

    def _current_wind_pitch_key(self) -> Optional[str]:
        return self.performance_wind_pitch_stack[-1] if self.performance_wind_pitch_stack else None

    def _transition_wind_note(self, tongued: bool = False):
        pitch_key = self._current_wind_pitch_key()
        index = WIND_PITCH_KEYS.get(pitch_key, 0)
        note = self._wind_note_for_index(index)

        if self.performance_wind_note is not None:
            old_note, old_serial = self.performance_wind_note
            if old_note == note and not tongued:
                return
            self._perf_stop_note(old_note, old_serial)
            self.performance_wind_note = None

        if self.performance_breath_held:
            velocity = min(1.2, max(0.35, self.performance_breath_level))
            if tongued:
                velocity = min(1.25, velocity + 0.18)
            serial = self._perf_start_note(note, velocity)
            self.performance_wind_note = (note, serial)
            self.synth.set_note_expression(note, int(55 + self.performance_breath_level * 72))

    def _trigger_drum(self, note: int, velocity: float = 1.0, duration: float = 0.12):
        if int(note) == 42:  # closed hi-hat chokes open hi-hat
            self._perf_stop_note(46)
        self._perf_timed_note(int(note), velocity=velocity, duration=duration)

    def _drum_velocity(self, base: float = 0.95) -> float:
        return min(1.25, base + (0.22 if self.performance_drum_accent else 0.0))

    def performance_key_down(self, key_id: str) -> bool:
        mode = self.effective_performance_mode()
        if mode == "keys":
            return False

        if mode in {"bowed", "fretted", "plucked"} and key_id == "TAB":
            self._toggle_string_performance_mode()
            return True

        if (
            mode != "drums"
            and key_id in {"LSHIFT", "RSHIFT"}
            and not (mode in {"bowed", "fretted", "plucked"} and self.performance_string_mode == "free")
        ):
            self.temp_octave_shift += -12 if key_id == "LSHIFT" else 12
            if mode == "bowed":
                self._revoice_active_bows()
            elif mode == "fretted":
                self._revoice_active_guitar_strings()
            elif mode == "wind" and self.performance_breath_held:
                self._transition_wind_note(tongued=False)
            self.refresh_button_labels()
            return True

        if mode == "bowed":
            if self.performance_string_mode == "chord":
                if key_id in VOICING_SLOT_BY_KEY:
                    slot = VOICING_SLOT_BY_KEY[key_id]
                    self._play_bowed_chord_key(key_id, slot)
                    return True
                if key_id in BOWED_EDIT_KEYS:
                    self.performance_bowed_selected_string = BOWED_EDIT_KEYS.index(key_id)
                    self.update_performance_mode_ui(); self.refresh_button_labels()
                    return True
                if key_id in FINGER_POSITION_KEYS:
                    self._set_bowed_position(
                        self.performance_fret_bank + FINGER_POSITION_KEYS[key_id]
                    )
                    return True
                if key_id == "PAGEUP":
                    self.performance_fret_bank = min(24, self.performance_fret_bank + 12)
                    self.update_performance_mode_ui(); self.refresh_button_labels(); return True
                if key_id == "PAGEDOWN":
                    self.performance_fret_bank = max(0, self.performance_fret_bank - 12)
                    self.update_performance_mode_ui(); self.refresh_button_labels(); return True
                if key_id == "HOME":
                    self._materialize_bowed_slot_for_edit()
                    fingers = self._current_bowed_fingers()
                    fingers[:] = [0, 0, 0, 0]
                    self.update_performance_mode_ui(); self.refresh_button_labels(); return True
            else:
                if key_id in FREE_STRING_FRET_KEY_INFO and self._free_grid_key_info(key_id, bowed=True) is not None:
                    self._start_free_fret_note(key_id, bowed=True); return True
                if key_id == "CAPS":
                    return True
                if key_id == "PAGEUP":
                    self.performance_fret_bank = min(24, self.performance_fret_bank + 12)
                    self.update_performance_mode_ui(); self.refresh_button_labels(); return True
                if key_id == "PAGEDOWN":
                    self.performance_fret_bank = max(0, self.performance_fret_bank - 12)
                    self.update_performance_mode_ui(); self.refresh_button_labels(); return True
                if key_id == "BACKSPACE":
                    self.sustain_held = True; self._apply_sustain_state(); return True
                if key_id == "INSERT":
                    self.performance_vibrato_held = True; return True
                if key_id == "HOME":
                    self.performance_tremolo_held = True; return True
                if key_id == "END":
                    return True
                return key_id in self.key_buttons

            if key_id == "Q":
                self.performance_vibrato_held = True; return True
            if key_id == "W":
                self.performance_tremolo_held = True; return True
            if key_id == "E":
                return True
            if key_id == "SPACE":
                self.sustain_held = True; self._apply_sustain_state(); return True
            return key_id in self.key_buttons

        if mode == "fretted":
            if self.performance_string_mode == "chord":
                if key_id in VOICING_SLOT_BY_KEY:
                    slot = VOICING_SLOT_BY_KEY[key_id]
                    self._play_fretted_chord_key(key_id, slot)
                    return True
                if key_id in FRETTED_EDIT_KEYS:
                    index = FRETTED_EDIT_KEYS.index(key_id)
                    if index < len(self._fretted_tuning()):
                        self.performance_fretted_selected_string = index
                        self.update_performance_mode_ui(); self.refresh_button_labels()
                    return True
                if key_id in FINGER_POSITION_KEYS:
                    self._set_fretted_position(
                        self.performance_fret_bank + FINGER_POSITION_KEYS[key_id]
                    )
                    return True
                if key_id == "PAGEUP":
                    self.performance_fret_bank = min(24, self.performance_fret_bank + 12)
                    self.update_performance_mode_ui(); self.refresh_button_labels(); return True
                if key_id == "PAGEDOWN":
                    self.performance_fret_bank = max(0, self.performance_fret_bank - 12)
                    self.update_performance_mode_ui(); self.refresh_button_labels(); return True
                if key_id == "DELETE":
                    self._set_fretted_position(None); return True
                if key_id == "HOME":
                    frets = self._current_fretted_frets()
                    for i in range(len(frets)):
                        frets[i] = 0
                    self.update_performance_mode_ui(); self.refresh_button_labels(); return True
            else:
                if key_id in FREE_STRING_FRET_KEY_INFO and self._free_grid_key_info(key_id, bowed=False) is not None:
                    self._start_free_fret_note(key_id, bowed=False); return True
                if key_id == "CAPS":
                    return True
                if key_id == "PAGEUP":
                    self.performance_fret_bank = min(24, self.performance_fret_bank + 12)
                    self.update_performance_mode_ui(); self.refresh_button_labels(); return True
                if key_id == "PAGEDOWN":
                    self.performance_fret_bank = max(0, self.performance_fret_bank - 12)
                    self.update_performance_mode_ui(); self.refresh_button_labels(); return True
                if key_id == "BACKSPACE":
                    self.sustain_held = True; self._apply_sustain_state(); return True
                if key_id == "INSERT":
                    self.performance_vibrato_held = True; return True
                if key_id in {"HOME", "END"}:
                    bend = 135.0 if key_id == "HOME" else -95.0
                    for note in list(self.performance_note_serial):
                        self.synth.set_note_pitch_bend(note, bend)
                    return True
                return key_id in self.key_buttons

            if key_id == "Q":
                self.performance_vibrato_held = True; return True
            if key_id in {"W", "E"}:
                bend = 135.0 if key_id == "W" else -95.0
                for note in list(self.performance_note_serial):
                    self.synth.set_note_pitch_bend(note, bend)
                return True
            if key_id == "SPACE":
                self.sustain_held = True; self._apply_sustain_state(); return True
            return key_id in self.key_buttons

        if mode == "wind":
            if key_id in WIND_PITCH_KEYS:
                if key_id in self.performance_wind_pitch_stack:
                    self.performance_wind_pitch_stack.remove(key_id)
                self.performance_wind_pitch_stack.append(key_id)
                if self.performance_breath_held:
                    self._transition_wind_note(tongued=False)
                return True
            if key_id == "SPACE":
                self.performance_breath_held = True; self._transition_wind_note(tongued=True); return True
            if key_id == "LBRACKET":
                self.performance_vibrato_held = True; return True
            if key_id == "RBRACKET":
                if self.performance_breath_held: self._transition_wind_note(tongued=True)
                return True
            if key_id == "BACKSLASH":
                self.performance_fall_held = True; self.performance_articulation_started = time.monotonic(); return True
            if key_id == "RCTRL":
                self.sustain_held = True; self._apply_sustain_state(); return True
            if key_id == "UP":
                self.performance_breath_level = min(1.0, self.performance_breath_level + 0.08)
                if self.performance_wind_note:
                    self.synth.set_note_expression(self.performance_wind_note[0], int(55 + self.performance_breath_level * 72))
                return True
            if key_id == "DOWN":
                self.performance_breath_level = max(0.35, self.performance_breath_level - 0.08)
                if self.performance_wind_note:
                    self.synth.set_note_expression(self.performance_wind_note[0], int(55 + self.performance_breath_level * 72))
                return True
            return key_id in self.key_buttons

        if mode == "drums":
            if key_id in DRUM_HIT_KEYS:
                note, _ = DRUM_HIT_KEYS[key_id]
                duration = 1.2 if note in {46, 49, 51} else 0.12
                self._trigger_drum(note, self._drum_velocity(), duration); return True
            if key_id in DRUM_ROLL_KEYS:
                self.performance_rolls[key_id] = 0.0; return True
            if key_id == "T":
                velocity = self._drum_velocity(0.88)
                self._trigger_drum(38, velocity, 0.10)
                QTimer.singleShot(34, lambda: self._trigger_drum(38, min(1.25, velocity + 0.12), 0.10)); return True
            if key_id == "Y":
                velocity = self._drum_velocity(0.72)
                for delay, gain in ((0, 0.0), (24, 0.07), (48, 0.18)):
                    QTimer.singleShot(delay, lambda v=min(1.25, velocity + gain): self._trigger_drum(38, v, 0.10))
                return True
            if key_id == "UP":
                self.performance_roll_interval = max(0.040, self.performance_roll_interval - 0.012); return True
            if key_id == "DOWN":
                self.performance_roll_interval = min(0.240, self.performance_roll_interval + 0.012); return True
            if key_id in {"LSHIFT", "RSHIFT"}:
                self.performance_drum_accent = True; return True
            return key_id in self.key_buttons

        if mode == "plucked":
            if self.performance_string_mode == "chord":
                if key_id in VOICING_SLOT_BY_KEY:
                    self._play_harp_chord_slot(VOICING_SLOT_BY_KEY[key_id])
                    return True
                if key_id in HARP_CHORD_TONE_KEYS:
                    tone_index = HARP_CHORD_TONE_KEYS.index(key_id)
                    note = self._harp_chord_tone_note(tone_index)
                    if note is not None:
                        self._perf_timed_note(note, 0.98, 1.8)
                    return True
                if key_id == "UP":
                    self._harp_arpeggiate_slot(True)
                    return True
                if key_id == "DOWN":
                    self._harp_arpeggiate_slot(False)
                    return True
                if key_id == "SPACE":
                    self.sustain_held = True
                    self._apply_sustain_state()
                    return True
                return key_id in self.key_buttons

            # FREE mode: the entire key surface is individual harp strings.
            if key_id in HARP_FREE_KEY_INDEX:
                self._harp_free_play(key_id)
                return True
            if key_id == "PAGEUP":
                self.performance_harp_page = min(6, self.performance_harp_page + 1)
                self.update_performance_mode_ui()
                self.refresh_button_labels()
                return True
            if key_id == "PAGEDOWN":
                self.performance_harp_page = max(0, self.performance_harp_page - 1)
                self.update_performance_mode_ui()
                self.refresh_button_labels()
                return True
            if key_id == "SPACE":
                self.sustain_held = True
                self._apply_sustain_state()
                return True
            return key_id in self.key_buttons

        return False

    def performance_key_up(self, key_id: str) -> bool:
        mode = self.effective_performance_mode()
        if mode == "keys":
            return False

        if mode in {"bowed", "fretted", "plucked"} and key_id == "TAB":
            return True

        if (
            mode != "drums"
            and key_id in {"LSHIFT", "RSHIFT"}
            and not (mode in {"bowed", "fretted", "plucked"} and self.performance_string_mode == "free")
        ):
            self.temp_octave_shift += 12 if key_id == "LSHIFT" else -12
            if mode == "bowed": self._revoice_active_bows()
            elif mode == "fretted": self._revoice_active_guitar_strings()
            elif mode == "wind" and self.performance_breath_held: self._transition_wind_note(tongued=False)
            self.refresh_button_labels(); return True

        if mode == "bowed":
            if self.performance_string_mode == "chord" and key_id in VOICING_SLOT_BY_KEY:
                self._stop_direct_chord(key_id, release_fade=0.22)
                return True
            if self.performance_string_mode == "free":
                if key_id in FREE_STRING_FRET_KEY_INFO:
                    if key_id in self.performance_free_direct_active:
                        self._stop_free_fret_note(key_id, bowed=True)
                    return True
                if key_id == "CAPS": return True
                if key_id == "BACKSPACE":
                    self.sustain_held = False; self._apply_sustain_state(); return True
                if key_id == "INSERT":
                    self.performance_vibrato_held = False
                    for note in list(self.performance_note_serial): self.synth.set_note_pitch_bend(note, 0.0)
                    return True
                if key_id == "HOME":
                    self.performance_tremolo_held = False
                    for note in list(self.performance_note_serial): self.synth.set_note_expression(note, 127)
                    return True
                if key_id in {"END", "PAGEUP", "PAGEDOWN"}: return True
                return key_id in self.key_buttons
            if key_id == "Q":
                self.performance_vibrato_held = False
                for note in list(self.performance_note_serial): self.synth.set_note_pitch_bend(note, 0.0)
                return True
            if key_id == "W":
                self.performance_tremolo_held = False
                for note in list(self.performance_note_serial): self.synth.set_note_expression(note, 127)
                return True
            if key_id == "SPACE":
                self.sustain_held = False; self._apply_sustain_state(); return True
            if key_id in BOWED_EDIT_KEYS or key_id in FINGER_POSITION_KEYS or key_id in {"E", "PAGEUP", "PAGEDOWN", "HOME"}:
                return True
            return key_id in self.key_buttons

        if mode == "fretted":
            if self.performance_string_mode == "chord" and key_id in VOICING_SLOT_BY_KEY:
                self._stop_direct_chord(key_id, release_fade=0.12)
                return True
            if self.performance_string_mode == "free":
                if key_id in FREE_STRING_FRET_KEY_INFO:
                    if key_id in self.performance_free_direct_active:
                        self._stop_free_fret_note(key_id, bowed=False)
                    return True
                if key_id == "CAPS": return True
                if key_id == "BACKSPACE":
                    self.sustain_held = False; self._apply_sustain_state(); return True
                if key_id == "INSERT":
                    self.performance_vibrato_held = False
                    for note in list(self.performance_note_serial): self.synth.set_note_pitch_bend(note, 0.0)
                    return True
                if key_id in {"HOME", "END"}:
                    for note in list(self.performance_note_serial): self.synth.set_note_pitch_bend(note, 0.0)
                    return True
                if key_id in {"PAGEUP", "PAGEDOWN"}: return True
                return key_id in self.key_buttons
            if key_id == "Q":
                self.performance_vibrato_held = False
                for note in list(self.performance_note_serial): self.synth.set_note_pitch_bend(note, 0.0)
                return True
            if key_id in {"W", "E"}:
                for note in list(self.performance_note_serial): self.synth.set_note_pitch_bend(note, 0.0)
                return True
            if key_id == "SPACE":
                self.sustain_held = False; self._apply_sustain_state(); return True
            if key_id in FRETTED_EDIT_KEYS or key_id in FINGER_POSITION_KEYS or key_id in {"PAGEUP", "PAGEDOWN", "DELETE", "HOME"}:
                return True
            return key_id in self.key_buttons

        if mode == "wind":
            if key_id in WIND_PITCH_KEYS:
                if key_id in self.performance_wind_pitch_stack: self.performance_wind_pitch_stack.remove(key_id)
                if self.performance_breath_held: self._transition_wind_note(tongued=False)
                return True
            if key_id == "SPACE":
                self.performance_breath_held = False
                if self.performance_wind_note:
                    note, serial = self.performance_wind_note
                    self._perf_stop_note(note, serial, release_fade=0.12)
                    self.performance_wind_note = None
                return True
            if key_id == "LBRACKET":
                self.performance_vibrato_held = False
                if self.performance_wind_note: self.synth.set_note_pitch_bend(self.performance_wind_note[0], 0.0)
                return True
            if key_id == "BACKSLASH":
                self.performance_fall_held = False
                if self.performance_wind_note: self.synth.set_note_pitch_bend(self.performance_wind_note[0], 0.0)
                return True
            if key_id == "RCTRL":
                self.sustain_held = False; self._apply_sustain_state(); return True
            if key_id in {"RBRACKET", "UP", "DOWN"}: return True
            return key_id in self.key_buttons

        if mode == "drums":
            if key_id in DRUM_ROLL_KEYS:
                self.performance_rolls.pop(key_id, None); return True
            if key_id in {"LSHIFT", "RSHIFT"}:
                self.performance_drum_accent = bool({"LSHIFT", "RSHIFT"} & self.held_ids); return True
            if key_id in DRUM_HIT_KEYS or key_id in {"T", "Y", "UP", "DOWN"}: return True
            return key_id in self.key_buttons

        if mode == "plucked":
            if self.performance_string_mode == "free" and key_id in HARP_FREE_KEY_INDEX:
                self._harp_free_stop(key_id)
                return True
            if key_id == "SPACE":
                self.sustain_held = False
                self._apply_sustain_state()
                return True
            if self.performance_string_mode == "chord":
                if key_id in VOICING_SLOT_BY_KEY or key_id in HARP_CHORD_TONE_KEYS or key_id in {"UP", "DOWN"}:
                    return True
            if key_id in {"PAGEUP", "PAGEDOWN"}:
                return True
            return key_id in self.key_buttons

        return False

    def update_performance_engine(self):
        mode = self.effective_performance_mode()
        now = time.monotonic()

        if mode in {"bowed", "fretted", "wind"}:
            active_notes = list(self.performance_note_serial)

            if self.performance_vibrato_held:
                rate = 6.1 if mode == "bowed" else (5.5 if mode == "wind" else 5.8)
                depth = 27.0 if mode == "bowed" else (19.0 if mode == "wind" else 34.0)
                for note in active_notes:
                    age = now - self.performance_note_started.get(note, now)
                    # A real player typically lets the attack speak before full vibrato.
                    ramp = max(0.0, min(1.0, (age - 0.18) / 0.28))
                    cents = depth * ramp * math.sin(2.0 * math.pi * rate * now)
                    self.synth.set_note_pitch_bend(note, cents)

            if mode == "bowed" and self.performance_tremolo_held:
                value = int(92 + 35 * (0.5 + 0.5 * math.sin(2.0 * math.pi * 9.0 * now)))
                for note in active_notes:
                    self.synth.set_note_expression(note, value)

            if mode == "wind" and self.performance_fall_held and self.performance_wind_note:
                elapsed = now - self.performance_articulation_started
                cents = -min(190.0, elapsed * 260.0)
                self.synth.set_note_pitch_bend(self.performance_wind_note[0], cents)

        if mode == "drums" and self.performance_rolls:
            for key_id in list(self.performance_rolls):
                next_time = self.performance_rolls.get(key_id, 0.0)
                if now < next_time:
                    continue
                note, _ = DRUM_ROLL_KEYS[key_id]
                # Small alternating dynamic pattern avoids a completely static roll.
                phase = int(now / max(0.001, self.performance_roll_interval)) % 4
                base = (0.78, 0.90, 0.82, 0.97)[phase]
                self._trigger_drum(note, self._drum_velocity(base), 0.08)
                self.performance_rolls[key_id] = now + self.performance_roll_interval

    def performance_label_for_key(self, key_id: str) -> Optional[str]:
        mode = self.effective_performance_mode()

        if mode == "bowed":
            _, names = self._bowed_tuning()
            if key_id == "TAB":
                return f"Mode: {self.performance_string_mode.upper()}"

            if self.performance_string_mode == "chord":
                fingers = self._current_bowed_fingers()
                if key_id in VOICING_SLOT_BY_KEY:
                    slot = VOICING_SLOT_BY_KEY[key_id]
                    marker = "●" if slot == self.performance_bowed_slot else ""
                    return f"{marker}{slot + 1}:{self._transposed_common_chord_label(slot)}"
                if key_id in BOWED_EDIT_KEYS:
                    i = BOWED_EDIT_KEYS.index(key_id)
                    if i < len(names):
                        marker = "●" if i == self.performance_bowed_selected_string else ""
                        return f"{marker}{names[i]}:{fingers[i]}"
                if key_id in FINGER_POSITION_KEYS:
                    return f"Set {self.performance_fret_bank + FINGER_POSITION_KEYS[key_id]}"
            else:
                grid_label = self._free_grid_label(key_id, bowed=True)
                if grid_label: return grid_label
                return {
                    "CAPS": "Open string modifier", "BACKSPACE": "Hold Sustain",
                    "INSERT": "Vibrato", "HOME": "Tremolo", "END": "Staccato",
                    "PAGEUP": "Grid +12", "PAGEDOWN": "Grid -12",
                }.get(key_id)

            return {
                "Q": "Vibrato", "W": "Tremolo", "E": "Staccato",
                "SPACE": "Hold Sustain", "PAGEUP": "Fret +12",
                "PAGEDOWN": "Fret -12", "HOME": "Open active chord",
            }.get(key_id)

        if mode == "fretted":
            tuning_len = len(self._fretted_tuning())
            if key_id == "TAB":
                return f"Mode: {self.performance_string_mode.upper()}"

            if self.performance_string_mode == "chord":
                frets = self._current_fretted_frets()
                if key_id in VOICING_SLOT_BY_KEY:
                    slot = VOICING_SLOT_BY_KEY[key_id]
                    marker = "●" if slot == self.performance_fretted_slot else ""
                    return f"{marker}{slot + 1}:{self._transposed_common_chord_label(slot)}"
                if key_id in FRETTED_EDIT_KEYS:
                    i = FRETTED_EDIT_KEYS.index(key_id)
                    if i < tuning_len:
                        fret = frets[i]
                        marker = "●" if i == self.performance_fretted_selected_string else ""
                        shown = "X" if fret is None else str(fret)
                        return f"{marker}S{tuning_len-i}:{shown}"
                if key_id in FINGER_POSITION_KEYS:
                    return f"Set {self.performance_fret_bank + FINGER_POSITION_KEYS[key_id]}"
            else:
                grid_label = self._free_grid_label(key_id, bowed=False)
                if grid_label: return grid_label
                return {
                    "CAPS": "Open string modifier", "BACKSPACE": "Hold Sustain",
                    "INSERT": "Vibrato", "HOME": "Bend +", "END": "Bend -",
                    "PAGEUP": "Grid +12", "PAGEDOWN": "Grid -12",
                }.get(key_id)

            return {
                "Q": "Vibrato", "W": "Bend +", "E": "Bend -",
                "SPACE": "Hold Sustain", "PAGEUP": "Fret +12",
                "PAGEDOWN": "Fret -12", "DELETE": "Mute string",
                "HOME": "Open active chord",
            }.get(key_id)

        if mode == "wind":
            if key_id in WIND_PITCH_KEYS: return f"Pitch {WIND_PITCH_KEYS[key_id] + 1}"
            return {"SPACE": "BREATH", "LBRACKET": "Vibrato", "RBRACKET": "Tongue",
                    "BACKSLASH": "Fall", "UP": "Breath +", "DOWN": "Breath -",
                    "RCTRL": "Hold Sustain"}.get(key_id)

        if mode == "drums":
            if key_id in DRUM_HIT_KEYS: return DRUM_HIT_KEYS[key_id][1]
            if key_id in DRUM_ROLL_KEYS: return DRUM_ROLL_KEYS[key_id][1]
            return {"T": "Flam", "Y": "Drag", "UP": "Roll faster", "DOWN": "Roll slower",
                    "LSHIFT": "Accent", "RSHIFT": "Accent"}.get(key_id)

        if mode == "plucked":
            if key_id == "TAB":
                return f"Mode: {self.performance_string_mode.upper()}"

            if self.performance_string_mode == "chord":
                if key_id in VOICING_SLOT_BY_KEY:
                    slot = VOICING_SLOT_BY_KEY[key_id]
                    marker = "●" if slot == self.performance_harp_slot else ""
                    return f"{marker}{slot + 1}:{self._transposed_common_chord_label(slot)}"
                if key_id in HARP_CHORD_TONE_KEYS:
                    tone_index = HARP_CHORD_TONE_KEYS.index(key_id)
                    note = self._harp_chord_tone_note(tone_index)
                    return midi_name(note) if note is not None else "Chord tone"
                return {
                    "UP": "Arp ↑",
                    "DOWN": "Arp ↓",
                    "SPACE": "Hold Sustain",
                }.get(key_id)

            if key_id in HARP_FREE_KEY_INDEX:
                note = self._harp_free_note(key_id)
                return midi_name(note) if note is not None else None
            return {
                "PAGEUP": "Harp +12 strings",
                "PAGEDOWN": "Harp -12 strings",
                "SPACE": "Hold Sustain",
            }.get(key_id)

        return None

    def instrument_changed(self, name: str):
        self._clear_performance_notes()
        self.synth.set_instrument(name)
        self.update_performance_mode_ui()
        self.refresh_button_labels()
        mode = self.effective_performance_mode()
        self.status.setText(
            f"Instrument: {name} | performance surface: {mode.replace('_', ' ').title()}"
        )

    def toggle_recording(self):
        if not self.synth.recording:
            mode = self.record_mode_combo.currentData() or "regular"
            selected_slots = [
                i for i, checkbox in enumerate(self.playback_record_checks)
                if checkbox.isChecked() and self.synth.playback_path(i) is not None
            ]

            self.synth.start_recording(mode=mode, playback_indices=selected_slots)
            self.record_autoplay_slots = selected_slots if mode in {"mix", "live_only"} else []
            self.record_started_ui = time.monotonic()
            self.record_mode_combo.setEnabled(False)
            for checkbox in self.playback_record_checks:
                checkbox.setEnabled(False)
            self.record_button.setText("■ Stop & Save Recording")
            self.record_time_label.setText("REC 00:00")
            self.record_timer.start()

            if mode == "mix":
                self.status.setText(
                    f"Recording LIVE + PLAYBACK mix. Auto-started {len(selected_slots)} checked WAV slot(s)."
                )
            elif mode == "live_only":
                self.status.setText(
                    f"Recording LIVE instrument only while monitoring playback. "
                    f"Auto-started {len(selected_slots)} checked WAV slot(s)."
                )
            else:
                self.status.setText(
                    "Regular recording — live instrument only; playback slots are not auto-started."
                )
            return

        self.record_timer.stop()
        path = self.synth.stop_recording(self.recordings_dir)
        for index in self.record_autoplay_slots:
            self.synth.stop_playback(index, reset=True)
        self.record_autoplay_slots = []
        self.record_started_ui = None
        self.record_mode_combo.setEnabled(True)
        for i, checkbox in enumerate(self.playback_record_checks):
            checkbox.setEnabled(self.synth.playback_path(i) is not None)
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

    def sustain_fade_changed(self, value: int):
        seconds = value / 10.0
        self.synth.set_sustain_timeout(seconds)
        self.sustain_fade_label.setText(f"{seconds:.1f}s")
        self.status.setText(
            f"Sustain fade: looped instruments fade smoothly over {seconds:.1f}s; "
            "naturally-decaying instruments keep their native decay."
        )

    def load_playback_slot_ui(self, index: int):
        previous_capture = self.capture_enabled
        self.capture_enabled = False
        self.file_dialog_open = True
        try:
            path, _ = QFileDialog.getOpenFileName(
                self,
                f"Load WAV into Playback Slot {index + 1}",
                str(writable_app_dir()),
                "WAV audio (*.wav);;All files (*.*)",
            )
        finally:
            self.file_dialog_open = False
            self.capture_enabled = previous_capture

        if not path:
            return

        try:
            duration = self.synth.load_playback_slot(index, Path(path))
        except Exception as exc:
            self.status.setText(f"Could not load WAV in slot {index + 1}: {exc}")
            return

        name = Path(path).name
        shown = name if len(name) <= 28 else name[:25] + "..."
        label = self.playback_file_labels[index]
        label.setText(shown)
        label.setToolTip(str(Path(path)))
        self.playback_play_buttons[index].setEnabled(True)
        self.playback_record_checks[index].setEnabled(True)

        minutes = int(duration // 60)
        seconds = duration - minutes * 60
        self.status.setText(
            f"Playback slot {index + 1}: loaded {name} "
            f"({minutes}:{seconds:04.1f})."
        )

    def toggle_playback_slot(self, index: int):
        if self.synth.playback_path(index) is None:
            return

        if self.synth.playback_is_playing(index):
            self.synth.stop_playback(index, reset=True)
            self.playback_play_buttons[index].setText("▶ Play")
            self.status.setText(f"Playback slot {index + 1} stopped.")
        else:
            self.synth.start_playback(index, restart=True)
            self.playback_play_buttons[index].setText("■ Stop")
            self.status.setText(f"Playback slot {index + 1} playing.")

    def stop_all_playback_ui(self):
        self.synth.stop_all_playback()
        self.update_playback_ui()
        self.status.setText("All playback slots stopped.")

    def update_playback_ui(self):
        for i, button in enumerate(self.playback_play_buttons):
            if self.synth.playback_path(i) is None:
                button.setText("▶ Play")
                button.setEnabled(False)
                continue
            button.setEnabled(True)
            button.setText("■ Stop" if self.synth.playback_is_playing(i) else "▶ Play")

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
        if hasattr(self, "performance_note_serial"):
            self._clear_performance_notes()
            self.performance_wind_pitch_stack.clear()
            self.performance_drum_accent = False

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

        # Scale/Root/Octave are global controls. If a native instrument is
        # currently sounding, re-evaluate its physical fingering through the
        # new musical transform instead of leaving the old pitch in place.
        if hasattr(self, "performance_combo"):
            try:
                mode = self.effective_performance_mode()
                if mode == "bowed" and self.performance_bow_active:
                    self._revoice_active_bows()
                elif mode == "fretted" and self.performance_guitar_active:
                    self._revoice_active_guitar_strings()
                elif mode == "wind" and self.performance_breath_held:
                    self._transition_wind_note(tongued=False)
                if mode != "keys":
                    self.update_performance_mode_ui()
            except Exception:
                pass

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

        mode = self.effective_performance_mode()

        for key_id, button in self.key_buttons.items():
            base_label = label_by_id.get(key_id, key_id)
            performance_label = self.performance_label_for_key(key_id)

            if performance_label:
                button.setText(f"{base_label}\n{performance_label}")
            elif mode == "keys" and key_id == "SPACE":
                button.setText(f"{base_label}\nHold Sustain")
            elif mode == "keys" and key_id == "LSHIFT":
                button.setText(f"{base_label}\nOct -")
            elif mode == "keys" and key_id == "RSHIFT":
                button.setText(f"{base_label}\nOct +")
            elif key_id == "ESC":
                button.setText(f"{base_label}\nPanic")
            elif mode == "keys" and key_id in self.current_mapping:
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

        if self.performance_key_down(key_id):
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

        if self.performance_key_up(key_id):
            return

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
