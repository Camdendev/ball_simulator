"""Songs and synthesis.

A song is a list of events; each event is a tuple of MIDI note numbers (a chord,
empty = rest). Every bounce plays the next event. In "audio" mode a real audio
file plays only while the balls keep bouncing instead.
"""
import functools
import math
import os
import re

import numpy as np

SR = 44100
TAU = 2 * math.pi

NOTE_IDX = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
NOTE_RE = re.compile(r'^([A-Ga-g])(#|b|s)?(-?\d)$')


def note_to_midi(tok):
    if tok.isdigit():
        return int(tok)
    m = NOTE_RE.match(tok)
    if not m:
        return None
    acc = {'#': 1, 's': 1, 'b': -1}.get(m.group(2), 0)
    return NOTE_IDX[m.group(1).upper()] + acc + (int(m.group(3)) + 1) * 12


def parse_note_text(text):
    """'C4 E4 G4+C5 _ F#3' -> [(60,), (64,), (67, 72), (), (54,)]"""
    events = []
    for tok in re.split(r'[\s,|]+', str(text or '')):
        if not tok:
            continue
        if tok in ('_', 'r', 'R'):
            events.append(())
            continue
        notes = [note_to_midi(t) for t in tok.split('+')]
        notes = tuple(n for n in notes if n is not None and 0 <= n < 128)
        if notes:
            events.append(notes)
    return events


def _rep(s, n):
    return ' '.join([s] * n)


# Public-domain melodies.
PRESETS = {
    'ode': ('Ode to Joy',
            'E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 E4 D4 D4 '
            'E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 D4 C4 C4 '
            'D4 D4 E4 C4 D4 E4 F4 E4 C4 D4 E4 F4 E4 D4 C4 D4 G3 '
            'E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 D4 C4 C4'),
    'elise': ('Für Elise',
              'E5 D#5 E5 D#5 E5 B4 D5 C5 A2+A4 E3 A3 C4 E4 A4 E2+B4 E3 G#3 E4 G#4 B4 A2+C5 E3 A3 E4 '
              'E5 D#5 E5 D#5 E5 B4 D5 C5 A2+A4 E3 A3 C4 E4 A4 E2+B4 E3 G#3 E4 C5 B4 A2+A4 E3 A3'),
    'prelude': ('Prelude in C (Bach)', ' '.join([
        _rep('C4 E4 G4 C5 E5 G4 C5 E5', 2), _rep('C4 D4 A4 D5 F5 A4 D5 F5', 2),
        _rep('B3 D4 G4 D5 F5 G4 D5 F5', 2), _rep('C4 E4 G4 C5 E5 G4 C5 E5', 2),
        _rep('C4 E4 A4 E5 A5 A4 E5 A5', 2), _rep('C4 D4 F#4 A4 D5 F#4 A4 D5', 2),
        _rep('B3 D4 G4 D5 G5 G4 D5 G5', 2), _rep('B3 C4 E4 G4 C5 E4 G4 C5', 2)])),
    'canon': ('Canon in D',
              'D3+F#5 A2+E5 B2+D5 F#2+C#5 G2+B4 D2+A4 G2+B4 A2+C#5 '
              'D3+D5 A2+C#5 B2+B4 F#2+A4 G2+G4 D2+F#4 G2+G4 A2+E4 '
              'D3+D4 F#4 A4 G4 F#4 D4 F#4 E4 D4 B3 D4 A4 G4 B4 A4 G4 '
              'F#4 D4 E4 C#5 D5 F#5 A5 A4 B4 G4 A4 F#4 D4 D5 D5 C#5'),
    'mountain': ('Hall of the Mountain King',
                 'B3 C#4 D4 E4 F#4 D4 F#4 F4 C#4 F4 E4 C4 E4 '
                 'B3 C#4 D4 E4 F#4 D4 F#4 B4 A4 F#4 D4 F#4 A4 '
                 'F#4 G#4 A#4 B4 C#5 A#4 C#5 D5 A#4 D5 C#5 A#4 C#5 '
                 'F#4 G#4 A#4 B4 C#5 A#4 C#5 D5 A#4 D5 C#5 _ _'),
    'twinkle': ('Twinkle Twinkle',
                'C4 C4 G4 G4 A4 A4 G4 F4 F4 E4 E4 D4 D4 C4 '
                'G4 G4 F4 F4 E4 E4 D4 G4 G4 F4 F4 E4 E4 D4 '
                'C4 C4 G4 G4 A4 A4 G4 F4 F4 E4 E4 D4 D4 C4'),
    'pentatonic': ('Pentatonic run',
                   'C4 D4 E4 G4 A4 C5 D5 E5 G5 A5 C6 A5 G5 E5 D5 C5 A4 G4 E4 D4'),
}

INSTRUMENTS = ['piano', 'pluck', 'bell', 'marimba', 'sine', 'triangle', 'square', 'sawtooth']


# ------------------------------------------------------------------- MIDI
@functools.lru_cache(maxsize=8)
def _load_midi(path, mtime):
    import mido
    mf = mido.MidiFile(path)
    tracks = []
    for tr in mf.tracks:
        tick, name, notes = 0, tr.name or '', []
        for msg in tr:
            tick += msg.time
            if msg.type == 'note_on' and msg.velocity > 0 and msg.channel != 9:
                notes.append((tick, msg.note))
        tracks.append((name.strip(), notes))
    return mf.ticks_per_beat, tracks


def load_midi(path):
    return _load_midi(path, os.path.getmtime(path))


def midi_track_options(path):
    opts = [(-1, 'All tracks')]
    if path and os.path.exists(path):
        try:
            _, tracks = load_midi(path)
            for i, (name, notes) in enumerate(tracks):
                if notes:
                    opts.append((i, f'{i + 1}: {name or "Track"} ({len(notes)})'))
        except Exception:
            pass
    return opts


def midi_events(path, track=-1, top_only=False, max_chord=6):
    tpb, tracks = load_midi(path)
    notes = []
    for i, (_, ns) in enumerate(tracks):
        if track == -1 or track == i:
            notes.extend(ns)
    notes.sort()
    tol = max(1, tpb // 24)
    groups, start = [], None
    for tick, n in notes:
        if start is None or tick - start > tol:
            groups.append(set())
            start = tick
        groups[-1].add(n)
    events = []
    for g in groups:
        arr = sorted(g)
        if top_only:
            arr = arr[-1:]
        events.append(tuple(arr[-max_chord:]))
    return events


# --------------------------------------------------------------- synthesis
@functools.lru_cache(maxsize=1024)
def synth_note(midi, inst, length):
    """Mono float32 buffer for one note, peak-normalized to 1."""
    f = 440.0 * 2 ** ((midi - 69) / 12)
    mult = {'bell': 3.0, 'piano': 2.2, 'pluck': 1.3, 'marimba': 0.8}.get(inst, 1.0)
    dur = max(0.04, length * mult)
    n = int((dur + 0.03) * SR)
    t = np.arange(n) / SR
    k = math.log(1000) / dur  # -60 dB at `dur`
    nyq = SR / 2

    if inst == 'piano':
        amps = [1, 0.55, 0.3, 0.18, 0.12, 0.07, 0.04, 0.02]
        y = sum(a * np.sin(TAU * f * h * t) * np.exp(-t * k * (1 + 0.35 * (h - 1)))
                for h, a in enumerate(amps, 1) if f * h < nyq)
    elif inst == 'pluck':
        y = sum((1 / h) * np.sin(TAU * f * h * t) * np.exp(-t * k * (1 + 0.8 * (h - 1)))
                for h in range(1, 13) if f * h < nyq)
    elif inst == 'bell':
        idx = 3 * np.exp(-t * k * 0.7)
        y = np.sin(TAU * f * t + idx * np.sin(TAU * f * 3.5 * t)) * np.exp(-t * k)
    elif inst == 'marimba':
        y = (np.sin(TAU * f * t) + 0.35 * np.sin(TAU * 4 * f * t) * np.exp(-t / 0.015)) * np.exp(-t * k)
    elif inst in ('square', 'sawtooth', 'triangle'):
        y = np.zeros(n)
        for h in range(1, 60):
            if f * h >= nyq:
                break
            if inst == 'sawtooth':
                y += np.sin(TAU * f * h * t) / h
            elif h % 2:
                if inst == 'square':
                    y += np.sin(TAU * f * h * t) / h
                else:
                    y += (-1) ** ((h - 1) // 2) * np.sin(TAU * f * h * t) / (h * h)
        y *= np.exp(-t * k)
        if inst != 'triangle':
            y *= 0.55  # harsh timbres sound louder
    else:
        y = np.sin(TAU * f * t) * np.exp(-t * k)

    y = np.asarray(y, dtype=np.float64)
    peak = np.max(np.abs(y)) or 1.0
    y /= peak
    if inst in ('square', 'sawtooth'):
        y *= 0.55
    a = int(0.004 * SR)
    y[:a] *= np.linspace(0, 1, a)
    r = int(0.01 * SR)
    y[-r:] *= np.linspace(1, 0, r)
    return y.astype(np.float32)


@functools.lru_cache(maxsize=512)
def chord_buffer(notes, inst, length):
    bufs = [synth_note(n, inst, length) for n in notes]
    out = np.zeros(max(len(b) for b in bufs), np.float32)
    for b in bufs:
        out[:len(b)] += b
    return out * (0.38 / math.sqrt(len(bufs)))


# ------------------------------------------------------------ song player
class SongPlayer:
    """Decides what plays on each hit. `t` is the video clock in seconds."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.events = []
        self.info = ''
        self.reset()

    def reset(self):
        self.idx = 0
        self.last = -1e9
        self.audio_until = -1.0

    def rebuild(self):
        c = self.cfg
        src = c['song_source']
        ev, info = [], ''
        try:
            if src == 'preset':
                ev = parse_note_text(PRESETS.get(c['preset'], PRESETS['ode'])[1])
            elif src == 'text':
                ev = parse_note_text(c['song_text'])
            elif src == 'midi':
                p = c['midi_path']
                if p and os.path.exists(p):
                    ev = midi_events(p, int(c['midi_track']), bool(c['top_note_only']))
                    info = f'{os.path.basename(p)}: {len(ev)} notes'
                else:
                    info = 'Drag a .mid file onto the window'
            elif src == 'audio':
                p = c['audio_path']
                info = os.path.basename(p) if p and os.path.exists(p) else 'Drag an audio file onto the window'
        except Exception as e:
            info = f'Error: {e}'
        self.events = ev
        self.info = info or f'{len(ev)} notes'
        self.reset()

    def hit(self, t):
        c = self.cfg
        if c['song_source'] == 'audio':
            self.audio_until = max(self.audio_until, t + float(c['slice_ms']) / 1000)
            return None
        if not self.events or t - self.last < float(c['min_note_gap_ms']) / 1000:
            return None
        self.last = t
        ev = self.events[self.idx % len(self.events)]
        self.idx += 1
        tr = int(c['transpose'])
        notes = tuple(n + tr for n in ev if 0 <= n + tr < 128)
        return notes or None
