"""Real-time audio for the preview window (pygame.mixer)."""
import numpy as np
import pygame

from .music import chord_buffer, pan_gains


class LiveAudio:
    def __init__(self):
        self.ok = pygame.mixer.get_init() is not None
        if self.ok:
            pygame.mixer.set_num_channels(48)
        self._sounds = {}
        self.music_path = None
        self.state = 'stopped'

    def play(self, notes, inst, length, volume, pan=0.0):
        if not self.ok:
            return
        key = (notes, inst, round(length, 3))
        snd = self._sounds.get(key)
        if snd is None:
            buf = chord_buffer(notes, inst, round(length, 3))
            mono = (np.clip(buf, -1, 1) * 32767).astype(np.int16)
            snd = pygame.sndarray.make_sound(np.ascontiguousarray(np.column_stack([mono, mono])))
            if len(self._sounds) > 400:
                self._sounds.clear()
            self._sounds[key] = snd
        snd.set_volume(min(1.0, volume))
        ch = snd.play()
        if ch is not None and pan:
            ch.set_volume(*pan_gains(pan))

    # Audio-file mode: the track plays only while balls keep bouncing.
    def load_music(self, path):
        self.music_path = None
        self.state = 'stopped'
        if not (self.ok and path):
            return
        try:
            pygame.mixer.music.load(path)
            self.music_path = path
        except pygame.error as e:
            print(f'Could not load audio for preview: {e}')

    def update_music(self, t, until, volume, active):
        if not self.music_path:
            return
        pygame.mixer.music.set_volume(volume)
        want = active and t < until
        if want and self.state != 'playing':
            if self.state == 'paused':
                pygame.mixer.music.unpause()
            else:
                pygame.mixer.music.play(loops=-1)
            self.state = 'playing'
        elif not want and self.state == 'playing':
            pygame.mixer.music.pause()
            self.state = 'paused'

    def reset_music(self):
        if self.music_path:
            pygame.mixer.music.stop()
            self.state = 'stopped'
