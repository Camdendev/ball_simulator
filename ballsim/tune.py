"""Auto-fit: adjust settings so a run ends close to a target length.

Runs fast headless simulations (no drawing, no particles). Because these
simulations are chaotic, it doesn't assume "more X = longer". Instead it:
  1. sweeps the mode's main setting across its range and keeps the best,
  2. refines around that value,
  3. optionally tries different random seeds for fine-tuning.
It is a generator-driven job so the editor can run it a few ms per frame.
"""
import copy
import math
import time

from .config import ASPECTS
from .sim import Sim

FPS = 60

# mode -> (config key, label, min, max, is_int, geometric spacing)
MODE_PARAM = {
    'escape': ('gap_deg', 'gap size', 5, 150, False, False),
    'rings': ('gap_deg', 'gap size', 5, 150, False, False),
    'polygon': ('time_scale', 'speed', 0.3, 2.0, False, True),
    'segments': ('seg_hp', 'hits per segment', 1, 12, True, False),
    'grow': ('growth', 'growth per bounce', 0.2, 30, False, True),
    'shrink': ('shrink', 'shrink per bounce', 0.5, 60, False, True),
    'multiply': ('multiply_chance', 'multiply chance', 0.05, 1.0, False, True),
    'lines': ('eliminate_after', 'no-deaths period', 0, 'target', False, False),
}
SPEED_PARAM = ('time_scale', 'speed', 0.3, 2.0, False, True)


def tunable(cfg):
    return cfg['mode'] in MODE_PARAM


def _values(lo, hi, n, is_int, geo):
    if geo and lo > 0:
        vals = [lo * (hi / lo) ** (i / (n - 1)) for i in range(n)]
    else:
        vals = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    if is_int:
        vals = sorted({int(round(v)) for v in vals})
    else:
        vals = [round(v, 3) for v in vals]
    return vals


class Tuner:
    def __init__(self, cfg, target):
        self.base = copy.deepcopy(cfg)
        # visuals don't affect physics; turn them off for speed
        self.base.update(particles=False, trail=0)
        self.target = float(target)
        self.size = ASPECTS[cfg['aspect']]
        choice = cfg['tune_param']
        if choice == 'seed':
            self.param = None
        elif choice == 'time_scale':
            self.param = SPEED_PARAM
        else:
            self.param = MODE_PARAM.get(cfg['mode'], SPEED_PARAM)
        self.try_seeds = bool(cfg['tune_seed']) or self.param is None
        self.limit = int(self.target * 2.5 * FPS) + int(float(cfg['start_delay']) * FPS)
        self.best = None      # (error, finish_time, overrides)
        self.runs = 0
        self.status = 'Starting…'
        self.done = False
        self.cancelled = False
        self._gen = self._job()

    # ---------------------------------------------------------------- driver
    def step(self, budget=0.03):
        """Advance the search for about `budget` seconds. Returns True when done."""
        end = time.perf_counter() + budget
        while not self.done and time.perf_counter() < end:
            try:
                next(self._gen)
            except StopIteration:
                self.done = True
        return self.done

    def cancel(self):
        self.cancelled = True
        self.done = True

    # ------------------------------------------------------------------ job
    def _simulate(self, overrides):
        cfg = dict(self.base, **overrides)
        sim = Sim(cfg, self.size)
        frames = 0
        while not sim.finished and frames < self.limit:
            sim.step(1 / FPS)
            frames += 1
            if frames % 120 == 0:
                yield
        self.runs += 1
        return frames / FPS if sim.finished else math.inf

    def _consider(self, overrides, t):
        err = abs(t - self.target)
        if self.best is None or err < self.best[0]:
            self.best = (err, t, dict(overrides))

    def _good_enough(self):
        return self.best and self.best[0] <= max(0.25, self.target * 0.02)

    def _fmt_best(self):
        if not self.best or math.isinf(self.best[1]):
            return 'no ending found yet'
        return f'best so far ends at {self.best[1]:.1f}s'

    def _job(self):
        seed0 = int(self.base['seed'])
        start = {}
        if self.param:
            key, label, lo, hi, is_int, geo = self.param
            if hi == 'target':
                hi = self.target
            # 1. coarse sweep
            vals = _values(lo, hi, 10, is_int, geo)
            results = []
            for i, v in enumerate(vals):
                self.status = f'Trying {label} {v} ({i + 1}/{len(vals)}) · {self._fmt_best()}'
                t = yield from self._simulate({key: v})
                results.append((v, t))
                self._consider({key: v}, t)
                if self._good_enough():
                    break
            # 2. refine between the neighbours of the best value
            if not self._good_enough() and self.best and not math.isinf(self.best[1]):
                bv = self.best[2][key]
                idx = vals.index(bv) if bv in vals else 0
                a = vals[max(0, idx - 1)]
                b = vals[min(len(vals) - 1, idx + 1)]
                fine = [v for v in _values(min(a, b), max(a, b), 8, is_int, False) if v not in vals]
                for i, v in enumerate(fine):
                    self.status = f'Refining {label} {v} ({i + 1}/{len(fine)}) · {self._fmt_best()}'
                    t = yield from self._simulate({key: v})
                    self._consider({key: v}, t)
                    if self._good_enough():
                        break
            start = dict(self.best[2]) if self.best else {}
        else:
            t = yield from self._simulate({})
            self._consider({}, t)

        # 3. seed search for the last bit of precision
        if self.try_seeds and not self._good_enough():
            for k in range(30):
                seed = (seed0 + 7919 * (k + 1)) % 1000
                ov = dict(start, seed=seed)
                self.status = f'Trying seed {seed} ({k + 1}/30) · {self._fmt_best()}'
                t = yield from self._simulate(ov)
                self._consider(ov, t)
                if self._good_enough():
                    break

    # --------------------------------------------------------------- result
    def summary(self):
        if self.cancelled:
            return 'Auto-fit cancelled'
        if not self.best or math.isinf(self.best[1]):
            return f'Auto-fit: no run ended within {self.target * 2.5:.0f}s — try a different setting to adjust'
        parts = []
        for k, v in self.best[2].items():
            label = SPEED_PARAM[1] if k == 'time_scale' else 'seed' if k == 'seed' else \
                next((p[1] for p in MODE_PARAM.values() if p[0] == k), k)
            parts.append(f'{label} {v}')
        return (f'Fitted ({", ".join(parts) or "no change"}): ends at {self.best[1]:.1f}s, '
                f'target {self.target:.0f}s · {self.runs} test runs')
