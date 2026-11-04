"""Offline export: renders every frame at full resolution and a locked 60 fps,
synthesizes the audio sample-accurately, and muxes both into an MP4 with ffmpeg.
"""
import copy
import os
import shutil
import subprocess
import tempfile
import wave

import numpy as np
import pygame

from .config import ASPECTS
from .music import SR, SongPlayer, chord_buffer
from .render import Renderer
from .sim import Sim

FPS = 60


def ffmpeg_exe():
    p = shutil.which('ffmpeg')
    if p:
        return p
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def decode_audio(path):
    """Any audio file -> float32 stereo array at SR."""
    out = subprocess.run([ffmpeg_exe(), '-v', 'error', '-i', path, '-f', 'f32le', '-ac', '2',
                          '-ar', str(SR), '-'], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32).reshape(-1, 2)


class Exporter:
    def __init__(self, cfg, out_path):
        self.cfg = copy.deepcopy(cfg)
        self.out = out_path
        self.fps = FPS = int(self.cfg['fps'])
        wW, wH = ASPECTS[self.cfg['aspect']]  # simulation world size (1080p units)
        k = int(self.cfg['export_res']) / min(wW, wH)
        self.W, self.H = int(round(wW * k / 2)) * 2, int(round(wH * k / 2)) * 2  # even for yuv420p
        self.player = SongPlayer(self.cfg)
        self.player.rebuild()
        self.notes = []
        self.flags = []
        self.frame = 0
        self.finish_frame = None
        self.max_frames = max(1, int(float(self.cfg['max_duration']) * FPS))
        self.sim = Sim(self.cfg, (wW, wH), on_hit=self._hit)
        self.renderer = Renderer()
        self.surf = pygame.Surface((self.W, self.H))

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        self.tmp = tempfile.mkdtemp(prefix='ballsim_')
        self.tmp_video = os.path.join(self.tmp, 'video.mp4')
        self.log = open(os.path.join(self.tmp, 'ffmpeg.log'), 'wb')
        self.ff = ffmpeg_exe()
        self.proc = subprocess.Popen(
            [self.ff, '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{self.W}x{self.H}',
             '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-preset', 'faster',
             '-crf', str(int(self.cfg['crf'])), '-pix_fmt', 'yuv420p', self.tmp_video],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.log)

    def _hit(self):
        t = self.frame / self.fps
        notes = self.player.hit(t)
        if notes:
            self.notes.append((t, notes))

    @property
    def progress(self):
        return min(1.0, self.frame / self.max_frames)

    def step(self):
        FPS = self.fps
        """Render and encode one frame. Returns False when the video is complete."""
        self.renderer.draw_aa(self.sim, self.surf, int(self.cfg['antialias']))
        self.proc.stdin.write(pygame.image.tobytes(self.surf, 'RGB'))
        t = self.frame / FPS
        self.sim.step(1 / FPS)
        self.flags.append(t < self.player.audio_until)
        self.frame += 1
        if self.sim.finished and self.finish_frame is None:
            self.finish_frame = self.frame
        if self.frame >= self.max_frames:
            return False
        if (self.cfg['stop_on_finish'] and self.finish_frame is not None
                and (self.frame - self.finish_frame) / FPS >= float(self.cfg['outro'])):
            return False
        return True

    def _build_audio(self):
        c = self.cfg
        FPS = self.fps
        block = SR // FPS
        n = self.frame * block
        out = np.zeros((n, 2), np.float32)
        vol = float(c['volume'])
        length = round(float(c['note_length']), 3)
        for t, notes in self.notes:
            buf = chord_buffer(notes, c['instrument'], length) * vol
            i = int(t * SR)
            j = min(n, i + len(buf))
            if j > i:
                out[i:j] += buf[:j - i, None]

        if c['song_source'] == 'audio' and c['audio_path'] and os.path.exists(c['audio_path']):
            song = decode_audio(c['audio_path']) * vol
            if len(song):
                pos, prev = 0, 0.0
                for k, on in enumerate(self.flags):
                    target = 1.0 if on else 0.0
                    if on or prev > 0:
                        idx = (np.arange(pos, pos + block)) % len(song)
                        ramp = np.linspace(prev, target, block, dtype=np.float32)[:, None]
                        out[k * block:(k + 1) * block] += song[idx] * ramp
                        pos = (pos + block) % len(song)
                    prev = target

        peak = float(np.max(np.abs(out))) if n else 0.0
        if peak > 0.98:
            out *= 0.98 / peak
        return (out * 32767).astype(np.int16)

    def finalize(self):
        self.proc.stdin.close()
        self.proc.wait()
        self.log.close()
        if self.proc.returncode != 0:
            with open(self.log.name, 'rb') as f:
                tail = f.read()[-800:].decode(errors='replace')
            shutil.rmtree(self.tmp, ignore_errors=True)
            raise RuntimeError(f'ffmpeg failed:\n{tail}')

        wav_path = os.path.join(self.tmp, 'audio.wav')
        with wave.open(wav_path, 'wb') as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(self._build_audio().tobytes())

        subprocess.run([self.ff, '-y', '-v', 'error', '-i', self.tmp_video, '-i', wav_path,
                        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-shortest',
                        '-movflags', '+faststart', self.out], check=True)
        shutil.rmtree(self.tmp, ignore_errors=True)
        return self.out

    def cancel(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.kill()
        self.proc.wait()
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
