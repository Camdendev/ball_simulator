"""Draws a Sim onto a pygame Surface at any scale (preview or full-res export)."""
import math

import numpy as np
import pygame
from pygame import gfxdraw

from .colors import hex_rgb, hsl, mix

WHITE = (255, 255, 255)
FONT_NAMES = 'helveticaneue,avenirnext,arialblack,arial,dejavusans'
_F = '/System/Library/Fonts/'
FONTS = {  # key: (label, file)
    'sf': ('SF Pro', _F + 'SFNS.ttf'),
    'sfrounded': ('SF Rounded', _F + 'SFNSRounded.ttf'),
    'helvetica': ('Helvetica Neue', _F + 'HelveticaNeue.ttc'),
    'avenir': ('Avenir Next', _F + 'Avenir Next.ttc'),
    'futura': ('Futura', _F + 'Supplemental/Futura.ttc'),
    'arialrounded': ('Arial Rounded', _F + 'Supplemental/Arial Rounded Bold.ttf'),
    'arialblack': ('Arial Black', _F + 'Supplemental/Arial Black.ttf'),
    'impact': ('Impact', _F + 'Supplemental/Impact.ttf'),
    'menlo': ('Menlo (mono)', _F + 'Menlo.ttc'),
}
FONT_OPTIONS = [(k, v[0]) for k, v in FONTS.items()]


class Renderer:
    def __init__(self):
        self._fonts = {}
        self._glow = {}
        self._big = None
        self._bg_key = None
        self._bg_surf = None
        self.rects = {}  # element -> (x0, y0, x1, y1) as fractions of the frame

    def draw_aa(self, sim, surf, factor=2):
        """Supersampled draw: render at `factor`x size, then smooth-downscale.
        Gives clean anti-aliased edges on every shape, line and glyph."""
        factor = max(1, int(factor))
        if factor == 1:
            self.draw(sim, surf)
            return
        W, H = surf.get_size()
        size = (W * factor, H * factor)
        if self._big is None or self._big.get_size() != size:
            self._big = pygame.Surface(size)
        self.draw(sim, self._big)
        pygame.transform.smoothscale(self._big, (W, H), surf)

    def font(self, px, family='sf', bold=True):
        px = max(8, int(px))
        key = (px, family, bold)
        f = self._fonts.get(key)
        if f is None:
            path = FONTS.get(family, FONTS['sf'])[1]
            try:
                f = pygame.font.Font(path, px)
                f.bold = bold
            except (OSError, FileNotFoundError, pygame.error):
                f = pygame.font.SysFont(FONT_NAMES, px, bold=bold)
            if len(self._fonts) > 200:
                self._fonts.clear()
            self._fonts[key] = f
        return f

    # ---------------------------------------------------------------- main
    def _background(self, c, surf):
        bg = hex_rgb(c['bg'])
        if c['bg_style'] != 'gradient':
            surf.fill(bg)
            return
        key = (surf.get_size(), c['bg'], c['bg2'])
        if self._bg_key != key:
            W, H = surf.get_size()
            top, bot = np.array(bg, float), np.array(hex_rgb(c['bg2']), float)
            t = np.linspace(0, 1, H)[:, None]
            col = (top * (1 - t) + bot * t).astype(np.uint8)          # (H, 3)
            arr = np.broadcast_to(col[None, :, :], (W, H, 3)).copy()  # surfarray is (x, y)
            self._bg_surf = pygame.surfarray.make_surface(arr)
            self._bg_key = key
        surf.blit(self._bg_surf, (0, 0))

    def draw(self, sim, surf):
        W, H = surf.get_size()
        s = W / sim.W
        c = sim.cfg
        bg = hex_rgb(c['bg'])
        self._background(c, surf)
        mode = c['mode']
        fl = float(c['flash_strength'])
        X, Y = sim.cx * s, sim.cy * s
        th = float(c['ring_thickness'])

        if mode == 'battle':
            self._grid(sim, surf, s)
        elif mode == 'crusher':
            self._crusher(sim, surf, s, bg, th)
        elif mode == 'polygon':
            for ring in sorted((r for r in sim.rings if r.alive), key=lambda r: -r.r):
                col = self._wall_color(sim, ring, bg)
                self._polygon(surf, X, Y, ring.r * s, (th + ring.flash * 6 * fl) * s, ring.rot,
                              max(3, int(c['poly_sides'])), bool(c['poly_open']), col)
        else:
            gap = math.radians(float(c['gap_deg'])) if mode in ('escape', 'rings') else 0.0
            for ring in sorted((r for r in sim.rings if r.alive), key=lambda r: -r.r):
                tw = (th + ring.flash * 6 * fl) * s
                if ring.segs:
                    n = len(ring.segs)
                    seg = math.tau / n
                    pad = min(seg * 0.12, 3.0 / max(1.0, ring.r))
                    for i, hp in enumerate(ring.segs):
                        if hp <= 0:
                            continue
                        col = mix(bg, sim.ring_color(ring, i), 0.35 + 0.65 * hp / ring.seg_max)
                        if ring.flash > 0 and i == ring.last_seg:
                            col = mix(col, WHITE, ring.flash * 0.6 * float(sim.cfg['flash_strength']))
                        a0 = ring.rot + i * seg + pad
                        self._arc(surf, X, Y, ring.r * s, tw, a0, a0 + seg - 2 * pad, col)
                    continue
                col = self._wall_color(sim, ring, bg)
                if gap <= 0:
                    self._circle_ring(surf, X, Y, ring.r * s, tw, col)
                else:
                    self._arc(surf, X, Y, ring.r * s, tw, ring.rot + gap / 2, ring.rot - gap / 2 + math.tau, col)
            self._lines(sim, surf, s, bg)

        self._balls(sim, surf, s, bg)

        fc = gfxdraw.filled_circle
        for p in sim.particles:
            f = 1 - p.life / p.max
            r = max(1, int(p.size * s * (0.5 + 0.5 * f)))
            fc(surf, int(p.x * s), int(p.y * s), r, mix(bg, p.color, f))

        self._overlay(sim, surf, s, bg)

    # ---------------------------------------------------------------- parts
    @staticmethod
    def _wall_color(sim, ring, bg):
        col = sim.ring_color(ring)
        if ring.hp_max > 0:
            col = mix(bg, col, 0.35 + 0.65 * ring.hp / ring.hp_max)
        if ring.flash > 0:
            col = mix(col, WHITE, ring.flash * 0.6 * float(sim.cfg['flash_strength']))
        return col

    @staticmethod
    def _circle_ring(surf, X, Y, r_in, th, col):
        ro = r_in + th
        pygame.draw.circle(surf, col, (X, Y), ro, max(1, int(round(th))))
        gfxdraw.aacircle(surf, int(X), int(Y), int(ro), col)
        gfxdraw.aacircle(surf, int(X), int(Y), int(r_in), col)

    @staticmethod
    def _arc(surf, X, Y, r_in, th, a0, a1, col):
        ro = r_in + th
        steps = max(4, int((a1 - a0) * ro / 6))
        da = (a1 - a0) / steps
        outer, inner = [], []
        cos, sin = math.cos, math.sin
        for i in range(steps + 1):
            a = a0 + da * i
            ca, sa = cos(a), sin(a)
            outer.append((round(X + ca * ro), round(Y + sa * ro)))
            inner.append((round(X + ca * r_in), round(Y + sa * r_in)))
        pts = outer + inner[::-1]
        gfxdraw.filled_polygon(surf, pts, col)
        gfxdraw.aapolygon(surf, pts, col)

    @staticmethod
    def _polygon(surf, X, Y, R, th, rot, n, open0, col):
        """Thick regular polygon; edge 0's outward normal points along `rot`."""
        a_in = R * math.cos(math.pi / n)
        Ro = R * (a_in + th) / a_in
        verts_in, verts_out = [], []
        for i in range(n):
            a = rot + (i - 0.5) * math.tau / n
            verts_in.append((X + math.cos(a) * R, Y + math.sin(a) * R))
            verts_out.append((X + math.cos(a) * Ro, Y + math.sin(a) * Ro))
        for i in range(n):
            if open0 and i == 0:
                continue
            j = (i + 1) % n
            pts = [verts_in[i], verts_in[j], verts_out[j], verts_out[i]]
            pts = [(round(x), round(y)) for x, y in pts]
            gfxdraw.filled_polygon(surf, pts, col)
            gfxdraw.aapolygon(surf, pts, col)

    @staticmethod
    def _crusher(sim, surf, s, bg, th):
        cr = sim.crusher
        col = sim.crusher_color()
        if cr.flash > 0:
            col = mix(col, WHITE, cr.flash * 0.6 * float(sim.cfg['flash_strength']))
        body = mix(bg, col, 0.22)
        if cr.kind == 'hooks':
            X, Y = sim.cx * s, sim.cy * s
            Renderer._circle_ring(surf, X, Y, cr.Rw * s, th * s, col)
            w = max(2, round(2 * max(6.0, th * 1.2) * s))
            for pts, _ in cr.arms:
                sp = [(x * s, y * s) for x, y in pts]
                pygame.draw.lines(surf, col, False, sp, w)
                for x, y in sp:
                    gfxdraw.filled_circle(surf, int(x), int(y), w // 2, col)
                tx, ty = sp[-1]
                gfxdraw.filled_circle(surf, int(tx), int(ty), int(w * 0.9), col)  # hook tip
            hr = int(cr.hub * s)
            gfxdraw.filled_circle(surf, int(X), int(Y), hr, body)
            gfxdraw.aacircle(surf, int(X), int(Y), hr, col)
            pygame.draw.circle(surf, col, (int(X), int(Y)), hr, max(2, int(th * s * 0.6)))
            return

        co, si = math.cos(cr.rot), math.sin(cr.rot)
        cx, cy = sim.cx, sim.cy

        def rect(x0, y0, x1, y1, c):
            if x1 <= x0 or y1 <= y0:
                return
            if not cr.rot:
                r = pygame.Rect(round(x0 * s), round(y0 * s), round((x1 - x0) * s), round((y1 - y0) * s))
                if r.w > 0 and r.h > 0:
                    surf.fill(c, r)
                return
            pts = []
            for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):  # rotate about the arena centre
                dx, dy = px - cx, py - cy
                pts.append((round((cx + dx * co - dy * si) * s), round((cy + dx * si + dy * co) * s)))
            gfxdraw.filled_polygon(surf, pts, c)
            gfxdraw.aapolygon(surf, pts, c)

        # fixed frame
        rect(cr.L0 - th, cr.T0 - th, cr.R0 + th, cr.T0, col)
        rect(cr.L0 - th, cr.B0, cr.R0 + th, cr.B0 + th, col)
        rect(cr.L0 - th, cr.T0, cr.L0, cr.B0, col)
        rect(cr.R0, cr.T0, cr.R0 + th, cr.B0, col)
        # rods first (dimmer), then the platforms on top
        for x0, y0, x1, y1, _, _, sd, part in sorted(cr.blocks, key=lambda b: b[7] == 'plate'):
            rect(x0, y0, x1, y1, mix(bg, col, 0.6) if part == 'rod' else col)

    @staticmethod
    def _grid(sim, surf, s):
        g = sim.grid
        ca, cb = sim.team_color(0), sim.team_color(1)
        xs = [round((g.x0 + i * g.cell) * s) for i in range(g.N + 1)]
        ys = [round((g.y0 + j * g.cell) * s) for j in range(g.N + 1)]
        surf.fill(ca, pygame.Rect(xs[0], ys[0], xs[-1] - xs[0], ys[-1] - ys[0]))
        cells, N = g.cells, g.N
        fill, Rect = surf.fill, pygame.Rect
        for j in range(N):
            row = j * N
            y, hgt = ys[j], ys[j + 1] - ys[j]
            i = 0
            while i < N:  # draw runs of team-B cells as single rects
                if cells[row + i]:
                    k = i
                    while k < N and cells[row + k]:
                        k += 1
                    fill(cb, Rect(xs[i], y, xs[k] - xs[i], hgt))
                    i = k
                else:
                    i += 1

    @staticmethod
    def _lines(sim, surf, s, bg):
        if not sim.lines:
            return
        c = sim.cfg
        w = max(1, round(float(c['line_width']) * s))
        op = float(c['line_opacity'])
        multi = len(sim.roster) > 1
        for l in sim.lines:
            o = l.owner
            col = mix(bg, sim.ball_color(o) if multi else sim.hue_color(l.hue), op)
            a, b = (l.ax * s, l.ay * s), (o.x * s, o.y * s)
            if w <= 1:
                pygame.draw.aaline(surf, col, a, b)
            else:
                pygame.draw.line(surf, col, a, b, w)

    def _balls(self, sim, surf, s, bg):
        c = sim.cfg
        many = len(sim.balls)
        glow = c['glow'] and many <= 60
        t_op = float(c['trail_opacity'])
        ow = float(c['outline_width'])
        gs_k, gs_size = float(c['glow_strength']), float(c['glow_size'])
        for b in sim.balls:
            col = sim.ball_color(b)
            rr = b.r * s
            flat = b.squash > 0.05  # squashed balls skip the round trail / glow
            if b.trail and many <= 120 and not flat:
                base = sim.team_color(b.team) if sim.grid else bg
                n = len(b.trail)
                for k, (tx, ty) in enumerate(b.trail):
                    f = (k + 1) / (n + 1)
                    gfxdraw.filled_circle(surf, int(tx * s), int(ty * s),
                                          max(1, int(rr * (0.35 + 0.65 * f))), mix(base, col, f * t_op))
            X, Y = b.x * s, b.y * s
            if glow and not flat:
                gs = self._glow_surf(col, rr, gs_k, gs_size)
                surf.blit(gs, (X - gs.get_width() / 2, Y - gs.get_height() / 2),
                          special_flags=pygame.BLEND_RGB_ADD)
            ri = max(1, int(round(rr)))
            if b.squash > 0.01 and b.sq_ang is not None:
                # squeezed at an angle: draw the ellipse flat, then rotate it into place
                short = max(1.0, rr * (1 - b.squash))
                long_ = rr * (1 + b.squash * 0.8)
                es = pygame.Surface((int(2 * long_) + 4, int(2 * short) + 4), pygame.SRCALPHA)
                pygame.draw.ellipse(es, col, es.get_rect().inflate(-4, -4))
                if c['ball_outline']:
                    pygame.draw.ellipse(es, hex_rgb(c['outline_color']), es.get_rect().inflate(-4, -4), max(1, int(ow * s)))
                es = pygame.transform.rotate(es, -math.degrees(b.sq_ang) + 90)
                surf.blit(es, es.get_rect(center=(int(X), int(Y))))
                continue
            gfxdraw.filled_circle(surf, int(X), int(Y), ri, col)
            gfxdraw.aacircle(surf, int(X), int(Y), ri, col)
            if c['ball_outline']:
                pygame.draw.circle(surf, hex_rgb(c['outline_color']), (int(X), int(Y)), ri, max(1, int(ow * s)))

    def _glow_surf(self, col, rr, strength=0.5, size=2.4):
        key = (col[0] // 16, col[1] // 16, col[2] // 16, int(rr), strength, size)
        g = self._glow.get(key)
        if g is not None:
            return g
        if len(self._glow) > 400:
            self._glow.clear()
        R = int(rr * max(1.1, size)) + 2
        size = 2 * R
        yy, xx = np.mgrid[0:size, 0:size]
        d = np.hypot(xx - R + 0.5, yy - R + 0.5)
        inten = np.clip(1 - (d - rr) / max(1.0, R - rr), 0, 1) ** 2 * strength
        arr = (np.array(col, float)[None, None, :] * inten[..., None]).astype(np.uint8)
        g = pygame.surfarray.make_surface(arr.transpose(1, 0, 2))
        self._glow[key] = g
        return g

    # ------------------------------------------------------------------ text
    def _fnt(self, cfg, px):
        return self.font(px, cfg['font'], bool(cfg['text_bold']))

    def _blit_text(self, surf, font, text, color, rect, style, px):
        img = font.render(text, True, color)
        if style == 'outline':
            blk = font.render(text, True, (0, 0, 0))
            o = max(2, int(px) // 14)
            for dx in (-o, 0, o):
                for dy in (-o, 0, o):
                    if dx or dy:
                        surf.blit(blk, rect.move(dx, dy))
        elif style == 'shadow':
            sh = font.render(text, True, (0, 0, 0))
            sh.set_alpha(150)
            o = max(1, int(px) // 18)
            surf.blit(sh, rect.move(o, o))
        surf.blit(img, rect)

    def _text(self, surf, cfg, text, px, color, center, style=None):
        f = self._fnt(cfg, px)
        r = pygame.Rect((0, 0), f.size(text))
        r.center = (int(center[0]), int(center[1]))
        self._blit_text(surf, f, text, color, r, style or cfg['text_style'], px)
        return r

    def _wrap(self, cfg, text, px, maxw):
        f = self._fnt(cfg, px)
        lines, cur = [], ''
        for w in text.split():
            test = f'{cur} {w}'.strip()
            if f.size(test)[0] <= maxw or not cur:
                cur = test
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def _overlay(self, sim, surf, s, bg):
        c = sim.cfg
        W, H = surf.get_size()
        top, bottom = sim.arena_bounds()
        top *= s
        bottom *= s
        custom = c['layout'] == 'custom'
        rects = {'arena': (0.0, top / H, 1.0, bottom / H)}

        def frac(r):
            return (r.left / W, r.top / H, r.right / W, r.bottom / H)

        if c['title']:
            fs = float(c['title_px']) * s
            lines = self._wrap(c, c['title'], fs, W * 0.9)
            lh = fs * 1.15
            block = len(lines) * lh
            if custom:
                cy = float(c['title_y_px']) * s
            else:
                cy = top / 2 if top > block + fs * 0.6 else fs * 0.5 + block / 2
            y0 = cy - block / 2
            box = None
            for i, line in enumerate(lines):
                r = self._text(surf, c, line, fs, hex_rgb(c['title_color']), (W / 2, y0 + lh * (i + 0.5)))
                box = r if box is None else box.union(r)
            if box:
                rects['title'] = frac(box)

        region_end = H
        if c['footer']:
            fs = float(c['footer_px']) * s
            cy = float(c['footer_y_px']) * s if custom else H - fs * 1.3
            r = self._text(surf, c, c['footer'], fs, hex_rgb(c['footer_color']), (W / 2, cy), style='none')
            rects['footer'] = frac(r)
            region_end = r.top - fs * 0.4

        segs = sim.stats() if c['show_stats'] else []
        if c['show_timer']:
            t = max(0.0, sim.t)
            segs = segs + [((' ' * 4 if segs else '') + f'{int(t // 60)}:{int(t % 60):02d}', hex_rgb(c['stats_color']))]
        if segs:
            fs = float(c['stats_px']) * s
            if custom:
                y = float(c['stats_y_px']) * s
            else:
                y = (bottom + region_end) / 2 if region_end - bottom > fs * 1.6 else region_end - fs * 0.8
            f = self._fnt(c, fs)
            sizes = [f.size(t) for t, _ in segs]
            x = W / 2 - sum(w for w, _ in sizes) / 2
            box = None
            for (t, col), (w, h) in zip(segs, sizes):
                r = pygame.Rect(int(x), int(y - h / 2), w, h)
                self._blit_text(surf, f, t, col, r, c['text_style'], fs)
                box = r if box is None else box.union(r)
                x += w
            rects['stats'] = frac(box)

        if sim.finished and sim.banner:
            k = min(1.0, (sim.t - sim.finish_t) * 5)
            scale = 0.5 + 0.5 * (1 - (1 - k) ** 3)
            fs = float(c['banner_px']) * s * scale
            col = sim.banner_color or hex_rgb(c['banner_color'])
            lines = self._wrap(c, sim.banner, fs, W * 0.92)
            lh = fs * 1.1
            for i, line in enumerate(lines):
                self._text(surf, c, line, fs, col, (W / 2, H / 2 + (i - (len(lines) - 1) / 2) * lh), style='outline')

        self.rects = rects
