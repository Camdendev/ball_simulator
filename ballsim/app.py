"""Interactive editor: control panel on the left, live preview on the right."""
import datetime
import os
import subprocess
import sys
import time

import pygame

from .audio_live import LiveAudio
from .config import ASPECTS, DEFAULTS, MODE_PRESETS, load_config, save_config
from .export import Exporter
from .music import INSTRUMENTS, PRESETS, SongPlayer, hit_gain_pan, midi_track_options
from .render import FONT_OPTIONS, Renderer
from .sim import Sim
from .tune import Tuner, tunable
from .ui import Panel, ui_font

PANEL_W = 420
REC_DIR = os.path.join(os.getcwd(), 'recordings')
MIDI_EXT = {'.mid', '.midi'}
AUDIO_EXT = {'.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.opus'}

MODES = [('escape', 'Escape (balls multiply)'), ('rings', 'Ring escape'),
         ('polygon', 'Spinning polygon'), ('segments', 'Breakable wall'),
         ('grow', 'Grow on bounce'), ('shrink', 'Shrinking circle'),
         ('multiply', 'Multiply on bounce'), ('lines', 'Line battle'), ('battle', 'Territory battle')]
CIRCLE = ('escape', 'rings', 'grow', 'lines', 'shrink', 'multiply', 'segments')
SPINNING = ('escape', 'rings', 'polygon', 'segments')
EXITS = ('escape', 'polygon', 'segments')
ASPECT_OPTS = [('9:16', '9:16 TikTok / Reels'), ('1:1', '1:1 Square'),
               ('4:5', '4:5 Portrait'), ('16:9', '16:9 YouTube')]
SOURCES = [('preset', 'Built-in melody'), ('text', 'Typed notes'),
           ('midi', 'MIDI file'), ('audio', 'Audio file')]


def _mode(*modes):
    return lambda c: c['mode'] in modes


def _not_battle(c):
    return c['mode'] != 'battle'


def _half_w(c):
    return ASPECTS[c['aspect']][0] // 2


def _half_h(c):
    return ASPECTS[c['aspect']][1] // 2


def _full_h(c):
    return ASPECTS[c['aspect']][1]


def _src(*s):
    return lambda c: c['song_source'] in s


def slider(key, label, mn, mx, step, **kw):
    return dict(kind='slider', key=key, label=label, min=mn, max=mx, step=step, **kw)


def choice(key, label, options, **kw):
    return dict(kind='choice', key=key, label=label, options=options, **kw)


def toggle(key, label, **kw):
    return dict(kind='toggle', key=key, label=label, **kw)


def color(key, label, **kw):
    return dict(kind='color', key=key, label=label, live=True, **kw)


def text(key, label, **kw):
    return dict(kind='text', key=key, label=label, **kw)


def section(label):
    return dict(kind='section', label=label)


def build_schema():
    red = (190, 45, 60)
    return [
        dict(kind='buttons', buttons=[
            dict(label=lambda a: 'Pause' if a.playing else 'Play', action=lambda a: a.toggle_play(),
                 color=(60, 80, 160)),
            dict(label='Restart', action=lambda a: a.reset()),
            dict(label=lambda a: 'Cancel' if a.export else 'Record MP4', action=lambda a: a.start_export(),
                 color=red),
        ]),
        dict(kind='buttons', buttons=[
            dict(label='Open recordings', action=lambda a: a.open_recordings()),
            dict(label='Reset settings', action=lambda a: a.reset_settings()),
        ]),
        dict(kind='info', text=lambda c: APP.status),

        section('Scene'),
        choice('mode', 'Mode', MODES),
        choice('aspect', 'Video format', ASPECT_OPTS),
        slider('seed', 'Random seed', 0, 999, 1),
        slider('time_scale', 'Speed', 0.25, 2.0, 0.05, live=True),
        slider('start_delay', 'Start pause (s)', 0, 3, 0.1),

        section('Start'),
        dict(kind='info', text='Angles: 0 = right, 90 = up, 180 = left, 270 = down'),
        choice('start_pos', 'Start position', [('center', 'Center'), ('jitter', 'Near center (random)'),
                                               ('random', 'Anywhere (random)'), ('custom', 'Custom')],
               show=_not_battle),
        slider('start_x_px', 'Start X (px)', lambda c: -_half_w(c), _half_w, 1,
               show=lambda c: c['start_pos'] == 'custom' and c['mode'] != 'battle'),
        slider('start_y_px', 'Start Y (px, up+)', lambda c: -_half_h(c), _half_h, 1,
               show=lambda c: c['start_pos'] == 'custom' and c['mode'] != 'battle'),
        slider('spread_px', 'Ball spread (px)', 0, 600, 1,
               show=lambda c: c['ball_count'] > 1 and c['mode'] != 'battle'),
        choice('launch_dir', 'Launch direction', [('random', 'Random'), ('fixed', 'Fixed angle')]),
        slider('launch_angle', 'Launch angle', 0, 359, 1, show=lambda c: c['launch_dir'] == 'fixed'),
        slider('speed', 'Launch speed', 0, 2500, 10),
        slider('gap_start', 'Opening angle', 0, 359, 1, show=_mode(*SPINNING)),
        slider('start_lines', lambda c: f"Start lines ({c['team_a_name'] or 'A'})" if c['start_lines_split']
               else 'Start lines (each)', 0, 100, 1, show=_mode('lines')),
        toggle('start_lines_split', 'Different per team', show=lambda c: c['mode'] == 'lines' and c['ball_count'] > 1),
        slider('start_lines_b', lambda c: f"Start lines ({c['team_b_name'] or 'B'})", 0, 100, 1,
               show=lambda c: c['mode'] == 'lines' and c['ball_count'] > 1 and c['start_lines_split']),
        choice('start_lines_layout', 'Line placement', [('random', 'Random'), ('even', 'Evenly spaced'),
                                                        ('side', 'Clustered on own side'), ('mirror', 'Mirrored (fair)')],
               show=_mode('lines')),
        slider('start_lines_arc', 'Cluster width (deg)', 10, 360, 1,
               show=lambda c: c['mode'] == 'lines' and c['start_lines_layout'] == 'side'),

        section('Balls'),
        slider('ball_count', lambda c: 'Balls per team' if c['mode'] == 'battle' else 'Starting balls', 1, 12, 1),
        slider('ball_radius', 'Ball radius', 4, 120, 1),
        slider('gravity', 'Gravity', 0, 3000, 10, live=True, show=_not_battle),
        slider('gravity_angle', 'Gravity angle', 0, 359, 1, live=True, show=_not_battle),
        slider('air_drag', 'Air drag', 0, 2, 0.01, live=True, show=_not_battle),
        slider('restitution', 'Bounciness', 0.5, 1.1, 0.01, live=True, show=_not_battle),
        slider('bounce_boost', 'Speed-up per bounce', 1.0, 1.1, 0.002, live=True, show=_not_battle),
        slider('max_speed', 'Max speed', 200, 6000, 50, live=True, show=_not_battle),
        toggle('ball_collisions', 'Balls collide', live=True, show=_not_battle),
        slider('min_speed', 'Min speed', 0, 2000, 10, live=True, show=_not_battle),
        slider('min_bounce_speed', 'Min impact for note', 0, 600, 5, live=True, show=_not_battle),
        slider('spawn_speed', 'New ball speed', 0, 2500, 10, live=True,
               show=_mode('escape', 'polygon', 'segments')),
        dict(kind='info', text='New ball speed: 0 = same as launch speed', show=_mode('escape', 'polygon', 'segments')),
        slider('battle_jitter', 'Bounce jitter (deg)', 0, 20, 0.1, live=True, show=_mode('battle')),
        slider('substeps', 'Physics substeps', 1, 24, 1),
        dict(kind='info', text='More substeps = more accurate, slower'),
        slider('growth', 'Growth per bounce', 0.2, 20, 0.1, live=True, show=_mode('grow')),
        choice('escape_action', 'When a ball escapes', [('spawn', 'Spawn new balls'), ('respawn', 'Respawn one'),
                                                        ('finish', 'It escaped (end)')], live=True,
               show=lambda c: c['mode'] in EXITS and (c['mode'] != 'polygon' or c['poly_open'])),
        slider('spawn_per_escape', 'New balls per escape', 0, 5, 1, live=True,
               show=lambda c: c['mode'] in EXITS and c['escape_action'] == 'spawn'),
        slider('shrink', 'Shrink per bounce (px)', 0.5, 40, 0.5, live=True, show=_mode('shrink')),
        slider('shrink_stop', 'Stop at ×ball size', 1.0, 3.0, 0.05, live=True, show=_mode('shrink')),
        slider('multiply_chance', 'Chance to multiply', 0.05, 1.0, 0.05, live=True, show=_mode('multiply')),
        slider('max_balls', 'Max balls', 2, 500, 1, live=True,
               show=lambda c: c['mode'] in ('escape', 'polygon', 'segments', 'multiply')),
        slider('max_lines', 'Max lines per ball', 5, 500, 5, live=True, show=_mode('lines')),
        toggle('cut_lines', 'Balls cut enemy lines', live=True, show=_mode('lines')),
        choice('line_cut_action', 'When a line is cut', [('destroy', 'Destroy it'), ('steal', 'Cutter steals it'),
                                                          ('swap', 'Swap (trade one each)')], live=True,
               show=lambda c: c['mode'] == 'lines' and c['cut_lines']),
        slider('lines_per_bounce', 'Lines per bounce', 0, 5, 1, live=True, show=_mode('lines')),
        slider('win_lines', 'Win at lines (0=off)', 0, 300, 1, live=True,
               show=lambda c: c['mode'] == 'lines' and c['ball_count'] > 1),
        slider('line_width', 'Line width (px)', 0.5, 12, 0.5, live=True, show=_mode('lines')),
        slider('line_opacity', 'Line opacity', 0.1, 1.0, 0.05, live=True, show=_mode('lines')),
        toggle('eliminate_lines', 'Ball dies at 0 lines', live=True, show=_mode('lines')),
        slider('eliminate_after', 'No deaths before (s)', 0, 20, 0.5, live=True,
               show=lambda c: c['mode'] == 'lines' and c['eliminate_lines']),

        section('Arena'),
        slider('arena_radius', 'Arena radius (px)', 100, lambda c: max(_half_w(c), _half_h(c)), 1),
        slider('arena_offset', 'Arena up/down (px)', lambda c: -_half_h(c), _half_h, 1),
        slider('ring_thickness', 'Wall thickness', 1, 40, 1, show=_not_battle),
        slider('gap_deg', 'Gap size (deg)', 0, 180, 1, live=True, show=_mode('escape', 'rings')),
        slider('ring_spin', 'Spin (deg/s)', -360, 360, 5, live=True, show=_mode(*SPINNING)),
        slider('spin_boost', 'Spin-up / bounce', 0, 30, 0.5, live=True, show=_mode(*SPINNING)),
        slider('poly_sides', 'Polygon sides', 3, 12, 1, live=True, show=_mode('polygon')),
        toggle('poly_open', 'One side open', live=True, show=_mode('polygon')),
        slider('seg_count', 'Wall segments', 3, 72, 1, show=_mode('segments')),
        slider('seg_hp', 'Hits per segment', 1, 10, 1, show=_mode('segments')),
        slider('wall_count', 'Number of walls', 1, 40, 1, show=_not_battle),
        slider('ring_spacing', 'Wall spacing (px)', 8, 150, 1, show=lambda c: c['mode'] != 'battle' and c['wall_count'] > 1),
        toggle('alternate_spin', 'Alternate spin', live=True,
               show=lambda c: c['mode'] in SPINNING and c['wall_count'] > 1),
        slider('wall_spin_step', 'Spin step per wall (%)', -20, 20, 0.5, live=True,
               show=lambda c: c['mode'] in SPINNING and c['wall_count'] > 1),
        slider('wall_twist', 'Wall twist (deg)', -180, 180, 1,
               show=lambda c: c['mode'] in SPINNING and c['wall_count'] > 1),
        slider('wall_hp', 'Hits to break wall', 0, 100, 1, show=_not_battle),
        dict(kind='info', text='0 = walls never break from hits', show=_not_battle),
        slider('grid_size', 'Grid size', 6, 60, 1, show=_mode('battle')),

        section('Look'),
        choice('bg_style', 'Background', [('solid', 'Solid color'), ('gradient', 'Vertical gradient')], live=True),
        color('bg', 'Background color'),
        color('bg2', 'Gradient bottom color', show=lambda c: c['bg_style'] == 'gradient'),
        choice('ball_color_mode', 'Ball color', [('rainbow', 'Rainbow'), ('random', 'Random'), ('solid', 'Solid')],
               live=True, show=lambda c: c['mode'] != 'battle'),
        color('ball_color', 'Ball solid color', show=lambda c: c['ball_color_mode'] == 'solid' and c['mode'] != 'battle'),
        toggle('ball_outline', 'Ball outline', live=True),
        slider('outline_width', 'Outline width (px)', 0.5, 12, 0.5, live=True, show=lambda c: c['ball_outline']),
        color('outline_color', 'Outline color', show=lambda c: c['ball_outline']),
        slider('color_saturation', 'Color saturation', 0, 1, 0.01, live=True),
        slider('color_lightness', 'Color brightness', 0.2, 0.9, 0.01, live=True),
        choice('ring_color_mode', 'Ring color', [('rainbow', 'Rainbow'), ('solid', 'Solid')], live=True,
               show=_not_battle),
        color('ring_color', 'Ring solid color', show=lambda c: c['ring_color_mode'] == 'solid' and c['mode'] != 'battle'),
        color('team_a', 'Team A color', show=_mode('lines', 'battle')),
        color('team_b', 'Team B color', show=_mode('lines', 'battle')),
        slider('rainbow_speed', 'Wall color cycle speed', 0, 360, 1, live=True,
               show=lambda c: c['ring_color_mode'] == 'rainbow' and c['mode'] != 'battle'),
        slider('flash_strength', 'Wall hit flash', 0, 2, 0.05, live=True, show=_not_battle),
        slider('trail', 'Trail length', 0, 40, 1, live=True),
        slider('trail_opacity', 'Trail opacity', 0.05, 1, 0.05, live=True, show=lambda c: c['trail'] > 0),
        choice('preview_aa', 'Preview smoothing', [(1, 'Off (fastest)'), (2, '2x supersample'),
                                                   (3, '3x supersample')], live=True),
        toggle('glow', 'Glow', live=True),
        slider('glow_strength', 'Glow strength', 0.05, 1.5, 0.05, live=True, show=lambda c: c['glow']),
        slider('glow_size', 'Glow size (×radius)', 1.2, 5, 0.1, live=True, show=lambda c: c['glow']),
        slider('hue_step', 'Rainbow step', 0, 120, 1, live=True,
               show=lambda c: c['ball_color_mode'] == 'rainbow' and c['mode'] != 'battle'),
        toggle('particles', 'Particles', live=True),
        slider('particle_amount', 'Particle amount', 0.1, 4, 0.1, live=True, show=lambda c: c['particles']),
        slider('particle_speed', 'Particle speed', 0.1, 4, 0.1, live=True, show=lambda c: c['particles']),
        slider('particle_life', 'Particle lifetime', 0.1, 4, 0.1, live=True, show=lambda c: c['particles']),
        slider('particle_size', 'Particle size', 0.2, 5, 0.1, live=True, show=lambda c: c['particles']),
        slider('particle_gravity', 'Particle gravity', -1000, 3000, 10, live=True, show=lambda c: c['particles']),

        section('Sound'),
        choice('song_source', 'Song', SOURCES, song=True),
        choice('preset', 'Melody', [(k, v[0]) for k, v in PRESETS.items()], song=True, show=_src('preset')),
        text('song_text', 'Notes', song=True, show=_src('text')),
        dict(kind='info', text='e.g. C4 E4 G4+C5 _ F#4  (+ = chord, _ = rest)', show=_src('text')),
        choice('midi_track', 'MIDI track', lambda c: midi_track_options(c['midi_path']), song=True,
               show=_src('midi')),
        toggle('top_note_only', 'Melody only (top note)', song=True, show=_src('midi')),
        dict(kind='info', text=lambda c: APP.player.info, show=lambda c: True),
        slider('slice_ms', 'Play / bounce (ms)', 50, 1500, 10, live=True, show=_src('audio')),
        choice('instrument', 'Instrument', [(i, i.title()) for i in INSTRUMENTS], live=True,
               show=_src('preset', 'text', 'midi')),
        slider('note_length', 'Note length (s)', 0.05, 2.0, 0.05, live=True, show=_src('preset', 'text', 'midi')),
        slider('transpose', 'Transpose', -24, 24, 1, live=True, show=_src('preset', 'text', 'midi')),
        slider('min_note_gap_ms', 'Min note gap (ms)', 0, 300, 5, live=True,
               show=_src('preset', 'text', 'midi')),
        choice('note_order', 'Note order', [('forward', 'Forward'), ('reverse', 'Reverse'),
                                            ('pingpong', 'Ping-pong'), ('random', 'Random')], song=True,
               show=_src('preset', 'text', 'midi')),
        toggle('velocity_sensitive', 'Harder hits = louder', live=True, show=_src('preset', 'text', 'midi')),
        slider('stereo_pan', 'Stereo pan by position', 0, 1, 0.05, live=True, show=_src('preset', 'text', 'midi')),
        slider('volume', 'Volume', 0, 1, 0.05, live=True),

        section('Text & layout'),
        dict(kind='info', text='Tip: drag the title, stats, footer or arena in the preview'),
        text('title', 'Title', live=True),
        choice('font', 'Font', FONT_OPTIONS, live=True),
        toggle('text_bold', 'Bold', live=True),
        choice('text_style', 'Text effect', [('shadow', 'Drop shadow'), ('outline', 'Black outline'),
                                             ('none', 'None')], live=True),
        slider('title_px', 'Title size (px)', 12, 300, 1, live=True),
        color('title_color', 'Title color'),
        toggle('show_stats', 'Show stats', live=True),
        toggle('show_timer', 'Show timer', live=True),
        slider('stats_px', 'Stats size (px)', 10, 250, 1, live=True,
               show=lambda c: c['show_stats'] or c['show_timer']),
        color('stats_color', 'Stats color', show=lambda c: c['show_stats'] or c['show_timer']),
        text('team_a_name', 'Team A name', live=True, show=_mode('lines', 'battle')),
        text('team_b_name', 'Team B name', live=True, show=_mode('lines', 'battle')),
        text('footer', 'Footer / handle', live=True),
        slider('footer_px', 'Footer size (px)', 8, 200, 1, live=True, show=lambda c: bool(c['footer'])),
        color('footer_color', 'Footer color', show=lambda c: bool(c['footer'])),
        text('end_text', 'Finish banner', live=True),
        slider('banner_px', 'Banner size (px)', 20, 400, 1, live=True),
        color('banner_color', 'Banner color'),
        choice('layout', 'Text positions', [('auto', 'Automatic'), ('custom', 'Custom')], live=True),
        slider('title_y_px', 'Title Y (px)', 0, _full_h, 1, live=True, show=lambda c: c['layout'] == 'custom'),
        slider('stats_y_px', 'Stats Y (px)', 0, _full_h, 1, live=True, show=lambda c: c['layout'] == 'custom'),
        slider('footer_y_px', 'Footer Y (px)', 0, _full_h, 1, live=True,
               show=lambda c: c['layout'] == 'custom' and bool(c['footer'])),

        section('Auto-fit to time'),
        dict(kind='info', text='Adjusts settings so the run ends near the target length'),
        slider('target_time', 'Target length (s)', 5, 180, 1, live=True),
        choice('tune_param', 'What to adjust', [('auto', "Mode's main setting"), ('time_scale', 'Speed'),
                                                ('seed', 'Random seed only')], live=True),
        toggle('tune_seed', 'Also try other seeds', live=True, show=lambda c: c['tune_param'] != 'seed'),
        dict(kind='buttons', buttons=[
            dict(label=lambda a: 'Cancel fit' if a.tuner else 'Fit to time', action=lambda a: a.start_fit(),
                 color=(40, 120, 90)),
        ]),
        dict(kind='info', text=lambda c: APP.fit_status),

        section('Recording'),
        slider('max_duration', 'Max length (s)', 5, 300, 5, live=True),
        toggle('stop_on_finish', 'Stop when finished', live=True),
        slider('outro', 'Outro after finish (s)', 0, 10, 0.5, live=True, show=lambda c: c['stop_on_finish']),
        choice('antialias', 'Video anti-aliasing', [(1, 'Off'), (2, '2x supersample'), (3, '3x supersample'),
                                                    (4, '4x supersample (slow)')], live=True),
        choice('export_res', 'Resolution', [(720, '720p'), (1080, '1080p'), (1440, '1440p (2K)'),
                                            (2160, '2160p (4K)')], live=True),
        choice('fps', 'Frame rate', [(30, '30 fps'), (60, '60 fps')], live=True),
        slider('crf', 'Quality (CRF)', 12, 30, 1, live=True),
        dict(kind='info', text='CRF: lower = better quality, bigger file (18 is great)'),
        dict(kind='info', text='Drop .mid / audio / .txt notes / .json config on window'),
    ]


APP = None


class App:
    def __init__(self, config_path):
        global APP
        APP = self
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.init()
        self.config_path = config_path
        self.cfg = load_config(config_path)
        self._open_window()
        self.audio = LiveAudio()
        self.player = SongPlayer(self.cfg)
        self.player.rebuild()
        self.status = 'Space = play/pause · R = restart'
        self.schema = build_schema()
        self.defs = {d['key']: d for d in self.schema if 'key' in d}
        self.panel = Panel(self, self.schema, PANEL_W, self.k)
        self.renderer = Renderer()
        self.sim = Sim(self.cfg, ASPECTS[self.cfg['aspect']], on_hit=self.on_hit)
        self.preview = None
        self.playing = True
        self.clock = 0.0
        self.export = None
        self.finalizing = False
        self.last_output = None
        self.preview_dest = None
        self.tuner = None
        self.fit_status = ''
        self.pdrag = None
        self._cursor = None
        if self.cfg['song_source'] == 'audio':
            self.audio.load_music(self.cfg['audio_path'])

    # -------------------------------------------------------------- window
    def _open_window(self):
        """Native-resolution window: on Retina screens the surface has 2x the
        pixels of the window's logical size, so UI text and shapes stay sharp."""
        self.window = None
        if hasattr(pygame, 'Window'):  # pygame-ce
            self.window = pygame.Window('Ball Simulator', (1400, 900), resizable=True, allow_high_dpi=True)
        else:
            pygame.display.set_mode((1400, 900), pygame.RESIZABLE)
            pygame.display.set_caption('Ball Simulator')
        self._refresh_surface()

    def _refresh_surface(self):
        if self.window:
            self.screen = self.window.get_surface()
            lw = self.window.size[0]
        else:
            self.screen = pygame.display.get_surface()
            lw = self.screen.get_width()
        self.k = max(1.0, self.screen.get_width() / max(1, lw))
        if hasattr(self, 'panel') and self.panel.k != self.k:
            self.panel.set_scale(self.k)
        self.small = ui_font(13 * self.k)
        self.big = ui_font(22 * self.k, bold=True)

    def _flip(self):
        if self.window:
            self.window.flip()
        else:
            pygame.display.flip()

    def _px(self, pos):
        return (int(pos[0] * self.k), int(pos[1] * self.k))

    # ------------------------------------------------------------- control
    def on_hit(self):
        notes = self.player.hit(self.clock)
        if notes:
            gain, pan = hit_gain_pan(self.cfg, self.sim)
            self.audio.play(notes, self.cfg['instrument'], float(self.cfg['note_length']),
                            float(self.cfg['volume']) * gain, pan)

    def reset(self):
        self.sim.reset()
        self.player.reset()
        self.audio.reset_music()
        self.clock = 0.0

    def toggle_play(self):
        self.playing = not self.playing

    def set(self, key, value, final=False):
        changed = self.cfg.get(key) != value
        self.cfg[key] = value
        if changed:
            self._apply(key)
        if final:
            save_config(self.cfg, self.config_path)

    def _apply(self, key):
        d = self.defs.get(key, {})
        c = self.cfg
        if key == 'mode':
            c.update(MODE_PRESETS[c['mode']])
            self.reset()
        elif key == 'aspect':
            self.sim = Sim(c, ASPECTS[c['aspect']], on_hit=self.on_hit)
            self.reset()
        elif d.get('song'):
            self.player.rebuild()
            if key == 'song_source':
                self.audio.load_music(c['audio_path'] if c['song_source'] == 'audio' else None)
            self.reset()
        elif not d.get('live'):
            self.reset()

    def reset_settings(self):
        self.cfg.clear()
        self.cfg.update(DEFAULTS)
        self.audio.load_music(None)
        self.player.rebuild()
        self.sim = Sim(self.cfg, ASPECTS[self.cfg['aspect']], on_hit=self.on_hit)
        self.reset()
        save_config(self.cfg, self.config_path)
        self.status = 'Settings reset to defaults'

    def drop_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        c = self.cfg
        if ext in MIDI_EXT:
            c['midi_path'], c['midi_track'], c['song_source'] = path, -1, 'midi'
            self._apply('song_source')
            self.status = f'Loaded MIDI {os.path.basename(path)}'
        elif ext in AUDIO_EXT:
            c['audio_path'], c['song_source'] = path, 'audio'
            self._apply('song_source')
            note = '' if self.audio.music_path else ' (no live preview for this format; export still works)'
            self.status = f'Loaded audio {os.path.basename(path)}{note}'
        elif ext == '.txt':
            with open(path, errors='replace') as f:
                c['song_text'] = f.read().replace('\n', ' ')
            c['song_source'] = 'text'
            self._apply('song_source')
            self.status = f'Loaded notes {os.path.basename(path)}'
        elif ext == '.json':
            new = load_config(path)
            c.clear()
            c.update(new)
            self.audio.load_music(c['audio_path'] if c['song_source'] == 'audio' else None)
            self.player.rebuild()
            self.sim = Sim(c, ASPECTS[c['aspect']], on_hit=self.on_hit)
            self.reset()
            self.status = f'Loaded config {os.path.basename(path)}'
        else:
            self.status = f'Unsupported file: {os.path.basename(path)}'
            return
        save_config(c, self.config_path)

    # ------------------------------------------------------------ auto-fit
    def start_fit(self):
        if self.tuner:
            self.tuner.cancel()
            self.tuner = None
            self.fit_status = 'Auto-fit cancelled'
            return
        if not tunable(self.cfg):
            self.fit_status = 'Territory battle never ends on its own, so it can\'t be fitted'
            return
        self.tuner = Tuner(self.cfg, self.cfg['target_time'])
        self.fit_status = 'Auto-fit starting…'

    def _fit_tick(self):
        t = self.tuner
        t.step(0.035)
        self.fit_status = t.status
        if not t.done:
            return
        self.tuner = None
        self.fit_status = t.summary()
        if t.cancelled or not t.best or t.best[1] == float('inf'):
            return
        self.cfg.update(t.best[2])
        self.cfg['stop_on_finish'] = True
        need = int(t.best[1] + float(self.cfg['outro'])) + 1
        if self.cfg['max_duration'] < need:
            self.cfg['max_duration'] = need
        self.reset()
        self.playing = True
        save_config(self.cfg, self.config_path)

    # -------------------------------------------------------------- export
    def start_export(self):
        if self.export:
            self.export.cancel()
            self.export = None
            self.finalizing = False
            self.status = 'Recording cancelled'
            return
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        path = os.path.join(REC_DIR, f'ballsim_{self.cfg["mode"]}_{stamp}.mp4')
        try:
            self.export = Exporter(self.cfg, path)
        except Exception as e:
            self.status = f'Could not start export: {e}'
            return
        self.finalizing = False
        self.playing = False
        self.status = 'Rendering… (Esc to cancel)'

    def _export_tick(self):
        ex = self.export
        if self.finalizing:
            try:
                out = ex.finalize()
                self.last_output = out
                self.status = f'Saved {os.path.relpath(out)}'
            except Exception as e:
                self.status = f'Export failed: {e}'
            self.export = None
            self.finalizing = False
            return
        t0 = time.perf_counter()
        try:
            while time.perf_counter() - t0 < 0.04:
                if not ex.step():
                    self.finalizing = True
                    break
        except (BrokenPipeError, OSError) as e:
            ex.cancel()
            self.export = None
            self.status = f'Export failed: {e}'

    def open_recordings(self):
        os.makedirs(REC_DIR, exist_ok=True)
        if sys.platform == 'darwin':
            args = ['open', '-R', self.last_output] if self.last_output else ['open', REC_DIR]
        elif sys.platform.startswith('win'):
            args = ['explorer', REC_DIR]
        else:
            args = ['xdg-open', REC_DIR]
        subprocess.Popen(args)

    # ------------------------------------------------- preview drag & drop
    DRAG_KEYS = {'title': 'title_y_px', 'stats': 'stats_y_px', 'footer': 'footer_y_px', 'arena': 'arena_offset'}

    def _preview_frac(self, pos):
        d = self.preview_dest
        return (pos[0] - d.x) / d.w, (pos[1] - d.y) / d.h

    def _preview_hit(self, pos):
        d = self.preview_dest
        if self.export or d is None or not d.collidepoint(pos):
            return None
        fx, fy = self._preview_frac(pos)
        rects = self.renderer.rects
        for name in ('title', 'stats', 'footer', 'arena'):
            r = rects.get(name)
            if r and r[0] - 0.01 <= fx <= r[2] + 0.01 and r[1] - 0.01 <= fy <= r[3] + 0.01:
                return name
        return None

    def _preview_mouse(self, e, pos):
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            hit = self._preview_hit(pos)
            if hit:
                if hit != 'arena' and self.cfg['layout'] != 'custom':
                    # switch to custom layout, starting from where things are now
                    for name in ('title', 'stats', 'footer'):
                        r = self.renderer.rects.get(name)
                        if r:
                            self.cfg[self.DRAG_KEYS[name]] = int(round((r[1] + r[3]) / 2 * self.sim.H))
                    self.cfg['layout'] = 'custom'
                key = self.DRAG_KEYS[hit]
                self.pdrag = (hit, self._preview_frac(pos)[1], float(self.cfg[key]))
                return True
        elif e.type == pygame.MOUSEMOTION and self.pdrag:
            name, fy0, v0 = self.pdrag
            H = self.sim.H
            lo, hi = (-H // 2, H // 2) if name == 'arena' else (0, H)
            v = int(round(max(lo, min(hi, v0 + (self._preview_frac(pos)[1] - fy0) * H))))
            self.set(self.DRAG_KEYS[name], v)
            return True
        elif e.type == pygame.MOUSEBUTTONUP and self.pdrag:
            self.pdrag = None
            save_config(self.cfg, self.config_path)
            return True
        return False

    def _set_cursor(self, resize):
        want = pygame.SYSTEM_CURSOR_SIZENS if resize else pygame.SYSTEM_CURSOR_ARROW
        if want != self._cursor:
            self._cursor = want
            try:
                pygame.mouse.set_cursor(want)
            except pygame.error:
                pass

    # ---------------------------------------------------------------- loop
    def run(self):
        clk = pygame.time.Clock()
        resize_events = {getattr(pygame, n) for n in ('VIDEORESIZE', 'WINDOWRESIZED', 'WINDOWSIZECHANGED',
                                                       'WINDOWDISPLAYCHANGED') if hasattr(pygame, n)}
        while True:
            self.panel.mouse = self._px(pygame.mouse.get_pos())
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    if self.export:
                        self.export.cancel()
                    save_config(self.cfg, self.config_path)
                    pygame.quit()
                    return
                if e.type in resize_events:
                    self._refresh_surface()
                    continue
                if e.type == pygame.DROPFILE:
                    self.drop_file(e.file)
                    continue
                pos = self._px(e.pos) if hasattr(e, 'pos') else None
                if self.panel.handle(e, pos):
                    continue
                if pos is not None and self._preview_mouse(e, pos):
                    continue
                if e.type == pygame.KEYDOWN and not self.panel.wants_keys():
                    if e.key == pygame.K_SPACE:
                        self.toggle_play()
                    elif e.key == pygame.K_r:
                        self.reset()
                    elif e.key == pygame.K_ESCAPE and self.export:
                        self.start_export()

            if self.export:
                self._export_tick()
            elif self.tuner:
                self._fit_tick()
            elif self.playing:
                self.sim.step(1 / 60)
                self.clock += 1 / 60
            self.audio.update_music(self.clock, self.player.audio_until, float(self.cfg['volume']),
                                    self.playing and not self.export and self.cfg['song_source'] == 'audio')

            # ---- draw (all in device pixels)
            k = self.k
            scr = self.screen
            sw, sh = scr.get_size()
            pw = self.panel.w
            m = int(20 * k)
            scr.fill((12, 12, 16))
            area = pygame.Rect(pw + m, m, sw - pw - 2 * m, sh - m - int(40 * k))
            W, H = (self.export.W, self.export.H) if self.export else (self.sim.W, self.sim.H)
            sc = max(0.05, min(area.w / W, area.h / H))
            size = (max(1, int(W * sc)), max(1, int(H * sc)))
            dest = pygame.Rect(0, 0, *size)
            dest.center = area.center

            if self.export:
                scr.blit(pygame.transform.smoothscale(self.export.surf, size), dest)
                bh = int(8 * k)
                bar = pygame.Rect(dest.x + m, dest.centery - bh // 2, dest.w - 2 * m, bh)
                pygame.draw.rect(scr, (40, 40, 50), bar, border_radius=bh // 2)
                pygame.draw.rect(scr, (220, 60, 80), (bar.x, bar.y, int(bar.w * self.export.progress), bh),
                                 border_radius=bh // 2)
                msg = 'Finalizing…' if self.finalizing else \
                    f'Rendering {self.export.progress * 100:.0f}%  ({self.export.frame / self.export.fps:.1f}s)'
                t = self.big.render(msg, True, (255, 255, 255))
                scr.blit(t, (dest.centerx - t.get_width() // 2, bar.y - t.get_height() - int(12 * k)))
            else:
                if self.preview is None or self.preview.get_size() != size:
                    self.preview = pygame.Surface(size)
                self.renderer.draw_aa(self.sim, self.preview, int(self.cfg['preview_aa']))
                scr.blit(self.preview, dest)
                self.preview_dest = dest
                if self.tuner:
                    shade = pygame.Surface(dest.size, pygame.SRCALPHA)
                    shade.fill((0, 0, 0, 150))
                    scr.blit(shade, dest)
                    t = self.big.render('Auto-fitting to target length…', True, (255, 255, 255))
                    scr.blit(t, (dest.centerx - t.get_width() // 2, dest.centery - t.get_height()))
                    t = self.small.render(self.tuner.status, True, (200, 200, 215))
                    scr.blit(t, (dest.centerx - t.get_width() // 2, dest.centery + int(8 * k)))
                hover = self.pdrag[0] if self.pdrag else (
                    None if self.panel.dropdown else self._preview_hit(self.panel.mouse))
                self._set_cursor(hover is not None)
                r = self.renderer.rects.get(hover) if hover else None
                if r:
                    pad = int(6 * k)
                    box = pygame.Rect(dest.x + r[0] * dest.w - pad, dest.y + r[1] * dest.h - pad,
                                      (r[2] - r[0]) * dest.w + 2 * pad, (r[3] - r[1]) * dest.h + 2 * pad)
                    pygame.draw.rect(scr, (120, 150, 255), box.clip(dest.inflate(-2, -2)), max(1, int(1.5 * k)),
                                     border_radius=int(6 * k))

            hint = (f'{W}x{H}  ·  {self.clock:5.1f}s  ·  bounces {self.sim.hits}  ·  '
                    f'{clk.get_fps():.0f} fps  ·  Space play/pause  ·  R restart')
            t = self.small.render(hint, True, (135, 135, 155))
            scr.blit(t, (area.centerx - t.get_width() // 2, sh - int(28 * k)))

            self.panel.draw(scr)
            self._flip()
            clk.tick(60)
