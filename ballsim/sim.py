"""Physics for all modes. World units are output-video pixels at 1080p
(e.g. 1080x1920); exports at other resolutions just scale the drawing.

Deterministic for a given seed: the sim always advances in fixed frames, so the
live preview and the exported video show the same run.

Performance notes: config values are read once per frame into `self.p`
(plain attributes), hot loops use locals, balls well inside the arena skip
the wall math entirely, ball-ball collisions use a spatial hash.
"""
import math
import random
from collections import deque

from .colors import hex_rgb, hsl

TAU = math.tau
TEAM_EXTRA = ['#ffd23f', '#7cff6b', '#c77dff', '#ff8c42', '#00ffd5', '#ff66c4', '#a0a0ff', '#ffffff']
DIRS12 = [(math.cos(k * TAU / 12), math.sin(k * TAU / 12)) for k in range(12)]

# Modes whose arena is a (possibly broken) circle.
CIRCLE_MODES = {'escape', 'rings', 'grow', 'lines', 'shrink', 'multiply', 'segments'}
# Modes where a ball leaving the arena triggers `escape_action`.
EXIT_MODES = {'escape', 'segments', 'polygon'}


def norm_angle(a):
    return (a + math.pi) % TAU - math.pi


class Ball:
    __slots__ = ('id', 'team', 'x', 'y', 'vx', 'vy', 'r', 'hue', 'trail',
                 'passing', 'escaped', 'frozen', 'bounces', 'lines', 'dead')

    def __init__(self, id, team, x, y, vx, vy, r, hue):
        self.id, self.team = id, team
        self.x, self.y, self.vx, self.vy, self.r, self.hue = x, y, vx, vy, r, hue
        self.trail = deque(maxlen=0)
        self.passing = self.escaped = self.frozen = self.dead = False
        self.bounces = 0
        self.lines = 0


class Ring:
    __slots__ = ('r', 'rot', 'idx', 'alive', 'flash', 'extra', 'segs', 'seg_max', 'last_seg', 'hp', 'hp_max')

    def __init__(self, r, rot, idx):
        self.r, self.rot, self.idx = r, rot, idx
        self.alive = True
        self.flash = 0.0
        self.extra = 0.0      # extra spin gained from bounces (rad/s)
        self.segs = None      # breakable wall: hit points per segment
        self.seg_max = 1
        self.last_seg = -1
        self.hp = 0           # hits until this wall breaks (0 = unbreakable)
        self.hp_max = 0


class Line:
    __slots__ = ('ax', 'ay', 'owner', 'hue')

    def __init__(self, ax, ay, owner, hue):
        self.ax, self.ay, self.owner, self.hue = ax, ay, owner, hue


class Particle:
    __slots__ = ('x', 'y', 'vx', 'vy', 'life', 'max', 'color', 'size')

    def __init__(self, x, y, vx, vy, mx, color, size):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.life, self.max, self.color, self.size = 0.0, mx, color, size


class Grid:
    __slots__ = ('x0', 'y0', 'S', 'N', 'cell', 'cells', 'counts')


class Params:
    """Per-frame snapshot of config values used in hot loops."""


class Sim:
    def __init__(self, cfg, size, on_hit=None):
        self.cfg = cfg
        self.W, self.H = size
        self.on_hit = on_hit or (lambda: None)
        self.p = Params()
        self.reset()

    # ------------------------------------------------------------------ setup
    def _params(self):
        c, p = self.cfg, self.p
        p.mode = c['mode']
        g = float(c['gravity'])
        ga = math.radians(float(c['gravity_angle']))
        p.gx, p.gy = g * math.cos(ga), -g * math.sin(ga)
        p.drag = float(c['air_drag'])
        p.e = float(c['restitution'])
        p.boost = float(c['bounce_boost'])
        p.max_speed = float(c['max_speed'])
        p.spin = math.radians(float(c['ring_spin']))
        p.spin_boost = math.radians(float(c['spin_boost']))
        p.gap = math.radians(float(c['gap_deg'])) if p.mode in ('escape', 'rings') else 0.0
        p.collide = bool(c['ball_collisions'])
        p.cut = p.mode == 'lines' and bool(c['cut_lines'])
        p.alt = bool(c['alternate_spin'])
        p.sides = max(3, int(c['poly_sides']))
        p.poly_open = bool(c['poly_open'])
        p.particles = float(c['particle_amount']) if c['particles'] else 0.0
        p.hue_step = float(c['hue_step'])
        p.rainbow = c['ball_color_mode'] == 'rainbow'
        p.min_bounce = float(c['min_bounce_speed'])
        p.min_speed = float(c['min_speed'])
        p.spin_step = float(c['wall_spin_step']) / 100
        p.sat = float(c['color_saturation'])
        p.lit = float(c['color_lightness'])
        p.rainbow_speed = float(c['rainbow_speed'])
        p.p_speed = float(c['particle_speed'])
        p.p_life = float(c['particle_life'])
        p.p_size = float(c['particle_size'])
        p.p_grav = float(c['particle_gravity'])
        p.jitter = math.radians(float(c['battle_jitter']))
        p.cut_action = c['line_cut_action']
        p.lines_per_bounce = int(c['lines_per_bounce'])

    def reset(self):
        c = self.cfg
        self._params()
        self.rng = random.Random(int(c['seed']))
        self.fx = random.Random(1)  # visual-only randomness (particles)
        self.t = 0.0
        self.hold = float(c['start_delay'])
        self.hits = 0
        self.escapes = 0
        self.finished = False
        self.finish_t = 0.0
        self.banner = ''
        self.banner_color = None
        self.last_x = self.W / 2      # where/how hard the latest hit was (for panning / velocity)
        self.last_impact = 600.0
        self.cx, self.cy = self.W / 2, self.H / 2 + float(c['arena_offset'])
        self.R = float(c['arena_radius']) - float(c['ring_thickness'])
        self.balls, self.particles, self.lines, self.rings = [], [], [], []
        self.grid = None
        self.next_id = 0
        self.roster = []

        mode = c['mode']
        if mode == 'battle':
            self._init_battle()
            return
        gap0 = -math.radians(float(c['gap_start']))  # 90 = top
        # Nested walls: index 0 is the innermost. Balls are always inside the
        # innermost wall that's still standing.
        n = max(1, int(c['wall_count']))
        inner = max(float(c['ball_radius']) * 2.5, self.R - (n - 1) * float(c['ring_spacing']))
        sp = (self.R - inner) / (n - 1) if n > 1 else 0.0
        hp = max(0, int(c['wall_hp']))
        for i in range(n):
            ring = Ring(inner + i * sp, gap0 + i * math.radians(float(c['wall_twist'])), i)
            ring.hp = ring.hp_max = hp
            if mode == 'segments':
                ring.seg_max = max(1, int(c['seg_hp']))
                ring.segs = [ring.seg_max] * max(3, int(c['seg_count']))
            self.rings.append(ring)

        region = self.rings[0].r
        if mode == 'polygon':
            region *= math.cos(math.pi / self.p.sides)  # inscribed circle
        count = max(1, int(c['ball_count']))
        for i in range(count):
            self.balls.append(self._spawn(i, count, region))
        self.roster = list(self.balls)
        if mode == 'lines':
            self._starting_lines()

    def _starting_lines(self):
        """Pre-place each ball's lines on the wall according to the layout setting."""
        c, rng = self.cfg, self.rng
        layout = c['start_lines_layout']
        count = len(self.balls)
        arc = math.radians(float(c['start_lines_arc']))
        base = None  # team A's random angles, reused for 'mirror'
        for b in self.balls:
            n = int(c['start_lines_b']) if (b.team == 1 and c['start_lines_split']) else int(c['start_lines'])
            if n <= 0:
                continue
            if layout == 'even':
                off = b.team / max(1, count) * TAU / n
                angles = [off + k * TAU / n for k in range(n)]
            elif layout == 'side':
                center = math.atan2(b.y - self.cy, b.x - self.cx) if (b.x, b.y) != (self.cx, self.cy) \
                    else b.team / max(1, count) * TAU
                angles = [center + (k / (n - 1) - 0.5) * arc if n > 1 else center for k in range(n)]
            elif layout == 'mirror':
                if base is None or len(base) < n:
                    base = (base or []) + [rng.random() * TAU for _ in range(n - len(base or []))]
                if count == 2:
                    angles = [(math.pi - a) if b.team == 1 else a for a in base[:n]]
                else:
                    angles = [a + b.team / count * TAU for a in base[:n]]
            else:  # random
                angles = [rng.random() * TAU for _ in range(n)]
            for a in angles:
                R0 = self.rings[0].r
                self.lines.append(Line(self.cx + math.cos(a) * R0, self.cy + math.sin(a) * R0, b, b.hue))
                b.lines += 1

    def _start_point(self, region, r):
        c, rng = self.cfg, self.rng
        lim = max(0.0, region - r)
        mode = c['start_pos']
        if mode == 'center':
            return self.cx, self.cy
        if mode == 'custom':
            dx, dy = float(c['start_x_px']), -float(c['start_y_px'])
        elif mode == 'random':
            a = rng.random() * TAU
            d = math.sqrt(rng.random()) * lim * 0.9
            dx, dy = math.cos(a) * d, math.sin(a) * d
        else:  # jitter: near the center
            dx, dy = (rng.random() - 0.5) * region * 0.2, (rng.random() - 0.5) * region * 0.2
        d = math.hypot(dx, dy)
        if d > lim and d > 0:
            dx, dy = dx * lim / d, dy * lim / d
        return self.cx + dx, self.cy + dy

    def _launch_angle(self, spawned=False):
        if self.cfg['launch_dir'] == 'fixed' and not spawned:
            return -math.radians(float(self.cfg['launch_angle']))  # 0 = right, 90 = up
        return self.rng.random() * TAU

    def _spawn(self, team, count, region, spawned=False):
        c, rng = self.cfg, self.rng
        r = min(float(c['ball_radius']), region * 0.45)
        if spawned:
            x = self.cx + (rng.random() - 0.5) * region * 0.2
            y = self.cy + (rng.random() - 0.5) * region * 0.2
        else:
            x, y = self._start_point(region, r)
            if count > 1:
                a = team / count * TAU + rng.random() * 0.3
                d = float(c['spread_px'])
                x, y = x + math.cos(a) * d, y + math.sin(a) * d
                dx, dy = x - self.cx, y - self.cy
                dd, lim = math.hypot(dx, dy), region - r
                if dd > lim:
                    x, y = self.cx + dx * lim / dd, self.cy + dy * lim / dd
        a = self._launch_angle(spawned)
        s = float(c['speed'])
        if spawned and float(c['spawn_speed']) > 0:
            s = float(c['spawn_speed'])
        b = Ball(self.next_id, team, x, y, math.cos(a) * s, math.sin(a) * s, r, rng.random() * 360)
        self.next_id += 1
        return b

    def _init_battle(self):
        c = self.cfg
        g = Grid()
        g.S = 2 * float(c['arena_radius'])
        g.N = N = max(4, int(c['grid_size']))
        g.x0, g.y0 = self.cx - g.S / 2, self.cy - g.S / 2
        g.cell = g.S / N
        g.cells = [0 if i < N // 2 else 1 for _ in range(N) for i in range(N)]
        g.counts = [g.cells.count(0), g.cells.count(1)]
        self.grid = g
        per = max(1, min(6, int(c['ball_count'])))
        r = min(float(c['ball_radius']), g.cell * 1.5)
        s = float(c['speed'])
        for team in (0, 1):
            for k in range(per):
                x = g.x0 + g.S * (0.25 if team == 0 else 0.75)
                y = g.y0 + g.S * (k + 1) / (per + 1)
                if c['launch_dir'] == 'fixed':
                    a = -math.radians(float(c['launch_angle']))
                    if team == 1:
                        a = math.pi - a  # mirror for the other side
                else:
                    a = math.pi / 4 + self.rng.randrange(4) * math.pi / 2 + (self.rng.random() - 0.5) * 0.5  # avoid axis-aligned
                self.balls.append(Ball(self.next_id, team, x, y, math.cos(a) * s, math.sin(a) * s, r, 0))
                self.next_id += 1
        self.roster = list(self.balls)

    # ----------------------------------------------------------------- colors
    def team_color(self, i):
        c = self.cfg
        if i == 0:
            return hex_rgb(c['team_a'])
        if i == 1:
            return hex_rgb(c['team_b'])
        return hex_rgb(TEAM_EXTRA[(i - 2) % len(TEAM_EXTRA)])

    def team_name(self, i):
        c = self.cfg
        if i == 0 and c['team_a_name']:
            return c['team_a_name']
        if i == 1 and c['team_b_name']:
            return c['team_b_name']
        return f'Ball {i + 1}'

    def ball_color(self, b):
        c = self.cfg
        if c['mode'] == 'battle':
            return self.team_color(1 - b.team)  # drawn in the enemy color, pong-wars style
        if c['mode'] == 'lines' and len(self.roster) > 1:
            return self.team_color(b.team)
        if c['ball_color_mode'] == 'solid':
            return hex_rgb(c['ball_color'])
        return self.hue_color(b.hue)

    def hue_color(self, h):
        return hsl(h, self.p.sat, self.p.lit)

    def ring_color(self, ring, seg=None):
        c = self.cfg
        if c['ring_color_mode'] == 'solid':
            return hex_rgb(c['ring_color'])
        sp = self.p.rainbow_speed
        if len(self.rings) > 1:
            return self.hue_color(ring.idx / len(self.rings) * 300 + self.t * sp)
        if seg is not None and ring.segs:
            return self.hue_color(seg / len(ring.segs) * 360 + self.t * sp * 0.5)
        return self.hue_color(self.t * sp)

    # ------------------------------------------------------------------- step
    def step(self, dt):
        c = self.cfg
        self._params()
        dt *= float(c['time_scale'])
        if self.hold > 0:
            self.hold -= dt
        else:
            n = max(1, int(c['substeps']))
            h = dt / n
            if self.p.mode == 'battle':
                sub = self._battle_sub
            elif self.p.mode == 'polygon':
                sub = self._poly_sub
            else:
                sub = self._circle_sub
            for _ in range(n):
                sub(h)
        self.t += dt

        tl = max(0, int(c['trail']))
        for b in self.balls:
            if b.trail.maxlen != tl:
                b.trail = deque(b.trail, maxlen=tl)
            if tl:
                b.trail.append((b.x, b.y))
        decay = dt * 4
        for r in self.rings:
            if r.flash > 0:
                r.flash = max(0.0, r.flash - decay)

        if self.particles:
            keep = []
            gdt = self.p.p_grav * dt
            for pt in self.particles:
                pt.life += dt
                if pt.life < pt.max:
                    pt.vy += gdt
                    pt.x += pt.vx * dt
                    pt.y += pt.vy * dt
                    keep.append(pt)
            self.particles = keep

        if any(b.escaped for b in self.balls):
            W, H = self.W, self.H
            self.balls = [b for b in self.balls if not (b.escaped and (
                b.y - b.r > H + 50 or b.y + b.r < -50 or b.x + b.r < -50 or b.x - b.r > W + 50))]

    def _integrate(self, h):
        p = self.p
        gxh, gyh = p.gx * h, p.gy * h
        damp = 1.0 - p.drag * h if p.drag > 0 else 1.0
        for b in self.balls:
            if b.frozen:
                continue
            vx = (b.vx + gxh) * damp
            vy = (b.vy + gyh) * damp
            b.vx, b.vy = vx, vy
            b.x += vx * h
            b.y += vy * h

    def _spin_rings(self, h):
        p = self.p
        multi = len(self.rings) > 1
        for r in self.rings:
            if not r.alive:
                continue
            d = -1 if (multi and p.alt and r.idx % 2) else 1
            k = 1 + p.spin_step * r.idx if multi else 1.0
            r.rot += (p.spin * k + r.extra) * d * h

    def _circle_sub(self, h):
        p = self.p
        self._spin_rings(h)
        self._integrate(h)
        if p.collide:
            self._collide_balls()
        ring = next((r for r in self.rings if r.alive), None)
        if ring:
            cx, cy, R = self.cx, self.cy, ring.r
            for b in self.balls[:]:
                if b.escaped or b.frozen:
                    continue
                dx, dy = b.x - cx, b.y - cy
                inner = R - b.r
                if inner > 0 and dx * dx + dy * dy <= inner * inner:
                    b.passing = False  # well inside: no wall contact possible
                    continue
                if self._collide_ring(b, ring, dx, dy):
                    break
        if p.cut:
            self._cut_lines()

    def _collide_ring(self, b, ring, dx, dy):
        """Ball touching or crossing the circle. Returns True if the ring broke."""
        p = self.p
        d = math.sqrt(dx * dx + dy * dy) or 1e-6
        R = ring.r
        in_gap = False
        if b.passing:
            in_gap = True
        elif p.gap > 0:
            in_gap = abs(norm_angle(math.atan2(dy, dx) - ring.rot)) < p.gap / 2
        elif ring.segs:
            n = len(ring.segs)
            i = int(((math.atan2(dy, dx) - ring.rot) % TAU) / (TAU / n)) % n
            ring.last_seg = i
            in_gap = ring.segs[i] <= 0
        if in_gap:
            b.passing = True
            if d - b.r > R:
                b.passing = False
                return self._escape(b, ring)
            return False

        nx, ny = dx / d, dy / d
        vn = b.vx * nx + b.vy * ny
        if vn > 0:
            self._reflect(b, nx, ny, vn)
            if vn > p.min_bounce:
                self._bounce(b, ring, self.cx + nx * R, self.cy + ny * R, vn)
        R = ring.r  # may have changed (shrink / grow)
        b.x = self.cx + nx * (R - b.r)
        b.y = self.cy + ny * (R - b.r)
        return not ring.alive

    def _reflect(self, b, nx, ny, vn):
        p = self.p
        k = 1 + p.e
        b.vx -= k * vn * nx
        b.vy -= k * vn * ny
        if p.boost != 1.0 or p.max_speed:
            sp = math.sqrt(b.vx * b.vx + b.vy * b.vy)
            if sp > 0:
                ns = min(max(sp * p.boost, p.min_speed), p.max_speed)
                if ns != sp:
                    f = ns / sp
                    b.vx *= f
                    b.vy *= f

    # ----------------------------------------------------------- polygon
    def _poly_sub(self, h):
        p = self.p
        self._spin_rings(h)
        self._integrate(h)
        if p.collide:
            self._collide_balls()
        ring = next((r for r in self.rings if r.alive), None)
        if ring is None:
            return
        n = p.sides
        a = ring.r * math.cos(math.pi / n)   # apothem
        half = ring.r * math.sin(math.pi / n)  # half edge length
        normals = [(math.cos(ring.rot + i * TAU / n), math.sin(ring.rot + i * TAU / n)) for i in range(n)]
        cx, cy = self.cx, self.cy
        open0 = p.poly_open
        for b in self.balls[:]:
            if b.escaped or b.frozen:
                continue
            dx, dy = b.x - cx, b.y - cy
            r = b.r
            inner = a - r
            if inner > 0 and dx * dx + dy * dy <= inner * inner:
                b.passing = False
                continue
            if open0:
                n0x, n0y = normals[0]
                d0 = dx * n0x + dy * n0y
                if b.passing or (d0 + r > a and abs(-dx * n0y + dy * n0x) < half):
                    if d0 + r <= a:
                        b.passing = False
                    else:
                        b.passing = True
                        if d0 - r > a:
                            b.passing = False
                            if self._escape(b, ring):
                                return  # wall broke; next substep uses the next wall
                        continue
            for i, (nx, ny) in enumerate(normals):
                if open0 and i == 0:
                    continue
                d = dx * nx + dy * ny
                pen = d + r - a
                if pen > 0:
                    b.x -= nx * pen
                    b.y -= ny * pen
                    dx -= nx * pen
                    dy -= ny * pen
                    vn = b.vx * nx + b.vy * ny
                    if vn > 0:
                        self._reflect(b, nx, ny, vn)
                        if vn > p.min_bounce:
                            self._bounce(b, ring, b.x + nx * r, b.y + ny * r, vn)
                            if not ring.alive:
                                return

    # ---------------------------------------------------------- events
    def _hit(self, x, impact=600.0):
        self.last_x = x
        self.last_impact = impact
        self.on_hit()

    def _bounce(self, b, ring, px, py, impact=600.0):
        c, p = self.cfg, self.p
        self.hits += 1
        b.bounces += 1
        ring.flash = 1.0
        if p.spin_boost:
            ring.extra += p.spin_boost if p.spin >= 0 else -p.spin_boost
        if p.rainbow:
            b.hue = (b.hue + p.hue_step) % 360
        mode = p.mode
        more_walls = sum(1 for r in self.rings if r.alive) > 1
        if mode == 'grow':
            b.r = min(ring.r - 0.5, b.r + float(c['growth']))
            if b.r >= ring.r - 1.0:
                if more_walls:
                    self._shatter(ring)  # keep growing inside the next wall
                else:
                    b.frozen = True
                    b.x, b.y, b.vx, b.vy = self.cx, self.cy, 0.0, 0.0
                    self._finish(c['end_text'] or 'FULL!')
        elif mode == 'shrink':
            biggest = max(x.r for x in self.balls if not x.escaped)
            ring.r = max(biggest + 1.0, ring.r - float(c['shrink']))
            if ring.r <= biggest * float(c['shrink_stop']) + 1.0:
                if more_walls:
                    self._shatter(ring)  # next wall starts closing in
                else:
                    for x in self.balls:
                        x.frozen = True
                    self._finish(c['end_text'] or 'TRAPPED!')
        elif mode == 'multiply':
            alive = sum(1 for x in self.balls if not x.escaped)
            cap = int(c['max_balls'])
            if alive < cap and self.rng.random() < float(c['multiply_chance']):
                a = self.rng.random() * TAU
                sp = math.sqrt(b.vx * b.vx + b.vy * b.vy)
                nb = Ball(self.next_id, 0, b.x, b.y, math.cos(a) * sp, math.sin(a) * sp, b.r, self.rng.random() * 360)
                self.next_id += 1
                self.balls.append(nb)
                if alive + 1 >= cap:
                    self._finish(c['end_text'] or f'{cap} BALLS!')
        elif mode == 'segments' and ring.segs and ring.last_seg >= 0:
            i = ring.last_seg
            if ring.segs[i] > 0:
                ring.segs[i] -= 1
                if ring.segs[i] == 0:
                    n = len(ring.segs)
                    col = self.ring_color(ring, i)
                    for k in range(8):
                        a = ring.rot + (i + (k + 0.5) / 8) * TAU / n
                        self._burst(self.cx + math.cos(a) * ring.r, self.cy + math.sin(a) * ring.r, col, 2, 260)
        elif mode == 'lines' and p.lines_per_bounce > 0:
            for _ in range(p.lines_per_bounce):
                self.lines.append(Line(px, py, b, b.hue))
                b.lines += 1
            cap = max(1, int(c['max_lines']))
            while b.lines > cap:
                for i, l in enumerate(self.lines):
                    if l.owner is b:
                        del self.lines[i]
                        break
                b.lines -= 1
            self._check_line_win(b)
        if ring.alive and ring.hp_max > 0:
            ring.hp -= 1
            if ring.hp <= 0:
                self._shatter(ring)
                if not any(r.alive for r in self.rings):
                    self._finish(c['end_text'] or 'BROKE FREE!')
        self._burst(px, py, self.ball_color(b), 6, 250)
        self._hit(px, impact)

    def _shatter(self, ring):
        """Destroy a wall with a burst of particles."""
        ring.alive = False
        col = self.ring_color(ring)
        for k in range(48):
            a = k / 48 * TAU
            self._burst(self.cx + math.cos(a) * ring.r, self.cy + math.sin(a) * ring.r, col, 1, 220)
        for x in self.balls:
            x.passing = False
        if not any(r.alive for r in self.rings):
            for x in self.balls:
                x.escaped = True
                x.frozen = False

    def _escape(self, b, ring):
        """Ball got out through an opening. Inner walls (and every wall in Ring
        escape) shatter; getting out of the last wall triggers escape_action."""
        c = self.cfg
        self.escapes += 1
        if self.p.mode == 'rings' or sum(1 for r in self.rings if r.alive) > 1:
            self._shatter(ring)
            if not any(r.alive for r in self.rings):
                self._finish(c['end_text'] or 'ESCAPED!')
            self._hit(b.x, 900.0)
            return True

        b.escaped = True
        self._burst(b.x, b.y, self.ball_color(b), 14, 350)
        alive = sum(1 for x in self.balls if not x.escaped)
        act = c['escape_action']
        cap = int(c['max_balls'])
        region = ring.r * (math.cos(math.pi / self.p.sides) if self.p.mode == 'polygon' else 1.0)  # last wall
        n_new = int(c['spawn_per_escape']) if act == 'spawn' else 1 if act == 'respawn' else 0
        for _ in range(n_new):
            if alive >= cap:
                break
            self.balls.append(self._spawn(0, 1, region, spawned=True))
            alive += 1
        if alive == 0:
            self._finish(c['end_text'] or 'ESCAPED!')
        elif act == 'spawn' and alive >= cap:
            self._finish(c['end_text'] or f'{cap} BALLS!')
        self._hit(b.x, 900.0)
        return False

    def _finish(self, text):
        if not self.finished:
            self.finished = True
            self.finish_t = self.t
            self.banner = text

    # ------------------------------------------------------- ball-ball
    def _collide_balls(self):
        bs = [b for b in self.balls if not b.escaped and not b.frozen]
        n = len(bs)
        if n < 2:
            return
        resolve = self._resolve
        if n <= 24:
            for i in range(n):
                a = bs[i]
                for j in range(i + 1, n):
                    resolve(a, bs[j])
            return
        # spatial hash: only check balls in neighbouring cells
        cell = 2 * max(b.r for b in bs) or 1.0
        grid = {}
        for b in bs:
            grid.setdefault((int(b.x // cell), int(b.y // cell)), []).append(b)
        for (gx, gy), items in grid.items():
            for ox, oy in ((0, 0), (1, 0), (0, 1), (1, 1), (-1, 1)):
                other = items if (ox, oy) == (0, 0) else grid.get((gx + ox, gy + oy))
                if not other:
                    continue
                if other is items:
                    m = len(items)
                    for i in range(m):
                        a = items[i]
                        for j in range(i + 1, m):
                            resolve(a, items[j])
                else:
                    for a in items:
                        for b in other:
                            resolve(a, b)

    @staticmethod
    def _resolve(a, b):
        dx, dy = b.x - a.x, b.y - a.y
        rs = a.r + b.r
        d2 = dx * dx + dy * dy
        if d2 >= rs * rs or d2 == 0:
            return
        d = math.sqrt(d2)
        nx, ny = dx / d, dy / d
        overlap = rs - d
        ma, mb = a.r * a.r, b.r * b.r
        tot = ma + mb
        a.x -= nx * overlap * mb / tot
        a.y -= ny * overlap * mb / tot
        b.x += nx * overlap * ma / tot
        b.y += ny * overlap * ma / tot
        rv = (b.vx - a.vx) * nx + (b.vy - a.vy) * ny
        if rv < 0:
            j = -2 * rv / (1 / ma + 1 / mb)
            a.vx -= j * nx / ma
            a.vy -= j * ny / ma
            b.vx += j * nx / mb
            b.vy += j * ny / mb

    # -------------------------------------------------------- line battle
    def _cut_lines(self):
        """Balls crossing enemy lines. What happens depends on line_cut_action:
        destroy (line gone), steal (cutter takes it), swap (cutter takes it and
        hands one of its own lines back)."""
        if not self.lines:
            return
        c = self.cfg
        action = self.p.cut_action
        balls = [b for b in self.balls if not b.dead]
        keep = []
        victims, gainers, swaps = set(), set(), []
        for l in self.lines:
            o = l.owner
            ax, ay, bx, by = l.ax, l.ay, o.x, o.y
            minx, maxx = (ax, bx) if ax < bx else (bx, ax)
            miny, maxy = (ay, by) if ay < by else (by, ay)
            ex, ey = bx - ax, by - ay
            l2 = ex * ex + ey * ey
            cut = None
            for b in balls:
                if b is o:
                    continue
                px, py, r = b.x, b.y, b.r
                if px + r < minx or px - r > maxx or py + r < miny or py - r > maxy:
                    continue
                # Only the "string" part can be cut: stop short of the owner ball,
                # otherwise touching the owner would cut every line at once.
                tmax = 1.0 - (o.r + r) / math.sqrt(l2) if l2 else 0.0
                if tmax <= 0:
                    continue
                t = ((px - ax) * ex + (py - ay) * ey) / l2
                t = 0.0 if t < 0 else tmax if t > tmax else t
                qx, qy = ax + t * ex, ay + t * ey
                if (px - qx) ** 2 + (py - qy) ** 2 < r * r:
                    cut = (qx, qy, b)
                    break
            if not cut:
                keep.append(l)
                continue
            thief = cut[2]
            o.lines -= 1
            victims.add(o)
            self._burst(cut[0], cut[1], self.ball_color(o), 8, 250)
            self.hits += 1
            self._hit(cut[0], math.hypot(thief.vx, thief.vy))
            if action in ('steal', 'swap'):
                l.owner = thief
                l.hue = thief.hue
                thief.lines += 1
                gainers.add(thief)
                keep.append(l)
                if action == 'swap':
                    swaps.append((thief, o, l))
        if not victims:
            return

        for thief, o, stolen in swaps:  # hand one of the thief's other lines back
            for l in keep:
                if l.owner is thief and l is not stolen:
                    l.owner = o
                    l.hue = o.hue
                    thief.lines -= 1
                    o.lines += 1
                    break

        cap = max(1, int(c['max_lines']))
        for g in gainers:  # respect the per-ball line limit (drop the oldest)
            extra = g.lines - cap
            if extra > 0:
                drop = set()
                for l in keep:
                    if extra <= 0:
                        break
                    if l.owner is g:
                        drop.add(id(l))
                        extra -= 1
                keep = [l for l in keep if id(l) not in drop]
                g.lines = min(g.lines, cap)

        if c['eliminate_lines'] and self.t >= float(c['eliminate_after']):
            for o in victims:
                if o.lines <= 0 and not o.dead:
                    o.dead = True
                    self._burst(o.x, o.y, self.ball_color(o), 60, 600)
        self.lines = [l for l in keep if not l.owner.dead]
        for g in gainers:
            if not g.dead:
                self._check_line_win(g)
        if any(b.dead for b in self.balls):
            self.balls = [b for b in self.balls if not b.dead]
            if len(self.roster) > 1 and len(self.balls) == 1:
                w = self.balls[0]
                self.banner_color = self.ball_color(w)
                self._finish(c['end_text'] or f'{self.team_name(w.team).upper()} WINS!')
            elif not self.balls:
                self._finish(c['end_text'] or 'DRAW!')

    def _check_line_win(self, b):
        """First ball to hold `win_lines` lines wins (0 = off)."""
        need = int(self.cfg['win_lines'])
        if need > 0 and len(self.roster) > 1 and b.lines >= need and not self.finished:
            self.banner_color = self.ball_color(b)
            self._finish(self.cfg['end_text'] or f'{self.team_name(b.team).upper()} WINS!')
            for x in self.balls:
                x.frozen = x is not b  # losers stop; the winner keeps bouncing

    # ------------------------------------------------------ territory battle
    def _battle_sub(self, h):
        g = self.grid
        N, cell, x0, y0, cells, counts = g.N, g.cell, g.x0, g.y0, g.cells, g.counts
        x1, y1 = x0 + g.S, y0 + g.S
        for b in self.balls:
            b.x += b.vx * h
            b.y += b.vy * h
            bx = by = False
            team, r = b.team, b.r
            for ca, sa in DIRS12:
                i = int((b.x + ca * r - x0) // cell)
                j = int((b.y + sa * r - y0) // cell)
                if 0 <= i < N and 0 <= j < N:
                    idx = j * N + i
                    if cells[idx] != team:
                        counts[cells[idx]] -= 1
                        cells[idx] = team
                        counts[team] += 1
                        if abs(ca) > abs(sa):
                            bx = bx or ca * b.vx > 0
                        else:
                            by = by or sa * b.vy > 0
                        self._burst(x0 + (i + 0.5) * cell, y0 + (j + 0.5) * cell, self.team_color(team), 3, 150)
                        self.hits += 1
                        self._hit(b.x, math.hypot(b.vx, b.vy))
            if bx:
                b.vx = -b.vx
            if by:
                b.vy = -b.vy
            if bx or by:
                a = (self.rng.random() - 0.5) * 2 * self.p.jitter
                ca, sa = math.cos(a), math.sin(a)
                b.vx, b.vy = b.vx * ca - b.vy * sa, b.vx * sa + b.vy * ca
            if b.x - r < x0:
                b.x, b.vx = x0 + r, abs(b.vx)
            if b.x + r > x1:
                b.x, b.vx = x1 - r, -abs(b.vx)
            if b.y - r < y0:
                b.y, b.vy = y0 + r, abs(b.vy)
            if b.y + r > y1:
                b.y, b.vy = y1 - r, -abs(b.vy)

    def _burst(self, x, y, color, n, speed):
        amt = self.p.particles
        if amt <= 0 or len(self.particles) > 2000:
            return
        p = self.p
        n = int(n * amt + self.fx.random())
        rnd = self.fx.random
        ps = self.particles
        speed *= p.p_speed
        life, size = p.p_life, p.p_size
        for _ in range(n):
            a = rnd() * TAU
            sp = speed * (0.3 + rnd())
            ps.append(Particle(x, y, math.cos(a) * sp, math.sin(a) * sp, (0.4 + rnd() * 0.4) * life,
                               color, (3 + rnd() * 4) * size))

    # ------------------------------------------------------------------ stats
    def stats(self):
        """List of (text, rgb) segments for the on-screen stats line."""
        segs = self._mode_stats()
        if len(self.rings) > 1 and self.cfg['mode'] != 'rings':
            left = sum(1 for r in self.rings if r.alive)
            segs = [(f'Walls: {left}/{len(self.rings)}    ', hex_rgb(self.cfg['stats_color']))] + segs
        return segs

    def _mode_stats(self):
        c = self.cfg
        white = hex_rgb(c['stats_color'])
        mode = c['mode']
        alive = sum(1 for b in self.balls if not b.escaped)
        if mode == 'escape' or (mode == 'polygon' and c['poly_open']):
            return [(f'Balls: {alive}    Escapes: {self.escapes}', white)]
        if mode == 'polygon':
            return [(f'Bounces: {self.hits}', white)]
        if mode == 'rings':
            left = sum(1 for r in self.rings if r.alive)
            return [(f'Rings left: {left}/{len(self.rings)}    Bounces: {self.hits}', white)]
        if mode == 'grow':
            pct = 0
            if self.balls and self.rings:
                pct = min(100, int(self.balls[0].r / self.rings[0].r * 100))
            return [(f'Size: {pct}%    Bounces: {self.hits}', white)]
        if mode == 'shrink':
            pct = int(self.rings[0].r / self.R * 100) if self.rings else 0
            return [(f'Circle: {pct}%    Bounces: {self.hits}', white)]
        if mode == 'multiply':
            return [(f'Balls: {alive}    Bounces: {self.hits}', white)]
        if mode == 'segments':
            segs = self.rings[0].segs or []
            left = sum(1 for s in segs if s > 0)
            return [(f'Wall: {left}/{len(segs)}    Bounces: {self.hits}', white)]
        if mode == 'lines':
            if len(self.roster) == 1:
                return [(f'Lines: {self.roster[0].lines}', white)]
            segs = []
            for i, b in enumerate(self.roster):
                col = (110, 110, 120) if b.dead else self.team_color(b.team)
                sep = '   ' if i < len(self.roster) - 1 else ''
                segs.append((f'{self.team_name(b.team)} {"OUT" if b.dead else b.lines}{sep}', col))
            return segs
        if mode == 'battle':
            a, b = self.grid.counts
            return [(f'{self.team_name(0)} {a}', self.team_color(0)), ('   vs   ', white),
                    (f'{self.team_name(1)} {b}', self.team_color(1))]
        return []

    def arena_bounds(self):
        """Top and bottom y of the arena (world units), for text placement."""
        if self.grid:
            return self.grid.y0, self.grid.y0 + self.grid.S
        th = float(self.cfg['ring_thickness'])
        return self.cy - self.R - th, self.cy + self.R + th
