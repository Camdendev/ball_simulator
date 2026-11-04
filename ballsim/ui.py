"""Control panel drawn with pygame, rendered at native (Retina) resolution.

Schema rows are dicts with a 'kind':
  section, slider, choice (dropdown), toggle, color (dropdown), text, info, buttons

All sizes are in logical points and multiplied by `k` (device pixel ratio).
Mouse positions passed in must already be in pixel coordinates.
"""
import os
import subprocess

import pygame

from .colors import PALETTE, hex_rgb

BG = (22, 22, 30)
FG = (240, 240, 246)
LABEL = (205, 205, 218)
DIM = (150, 150, 170)
ACCENT = (120, 150, 255)
FIELD = (40, 40, 56)
FIELD_HOVER = (52, 52, 72)
POPUP = (34, 34, 48)
BORDER = (70, 70, 95)

SF = '/System/Library/Fonts/SFNS.ttf'


def ui_font(px, bold=False):
    px = max(6, int(round(px)))
    try:
        f = pygame.font.Font(SF if os.path.exists(SF) else None, px)
        if bold:
            f.bold = True
    except (OSError, pygame.error):
        f = pygame.font.SysFont('helveticaneue,arial', px, bold=bold)
    return f


def _label(d, cfg):
    lab = d.get('label', '')
    return lab(cfg) if callable(lab) else lab


def _fmt(v, step):
    if step >= 1:
        return str(int(round(v)))
    dec = max(0, len(f'{step}'.rstrip('0').split('.')[-1]))
    return f'{v:.{dec}f}'


class Panel:
    def __init__(self, app, schema, width=420, k=1.0):
        self.app = app
        self.schema = schema
        self.base_w = width
        self.scroll = 0
        self.drag = None
        self.focus = None
        self.edit = ''
        self.fresh = False
        self.dropdown = None  # (def, anchor rect, scroll)
        self.rows = []
        self.content_h = 0
        self.view_h = 0
        self.mouse = (-1, -1)
        self.set_scale(k)

    def set_scale(self, k):
        self.k = k
        self.w = int(self.base_w * k)
        self.pad = int(16 * k)
        self.ctrl_x = int(178 * k)
        self.row_h = int(36 * k)
        self.font = ui_font(15 * k)
        self.small = ui_font(13 * k)
        self.head = ui_font(12.5 * k, bold=True)
        self.btn = ui_font(15 * k, bold=True)

    def wants_keys(self):
        return self.focus is not None or self.dropdown is not None

    # ---------------------------------------------------------------- layout
    def layout(self, h):
        cfg = self.app.cfg
        k = self.k
        y = self.pad - self.scroll
        rows = []
        for d in self.schema:
            if 'show' in d and not d['show'](cfg):
                continue
            hh = {'section': int(44 * k), 'buttons': int(48 * k), 'info': int(28 * k)}.get(d['kind'], self.row_h)
            rows.append((d, pygame.Rect(0, y, self.w, hh)))
            y += hh
        self.rows = rows
        self.view_h = h
        self.content_h = y + self.scroll + self.pad
        self.scroll = max(0, min(self.scroll, self.content_h - h))

    def ctrl_rect(self, r):
        m = int(5 * self.k)
        return pygame.Rect(self.ctrl_x, r.y + m, self.w - self.ctrl_x - self.pad, r.h - 2 * m)

    # ------------------------------------------------------------------ draw
    def draw(self, surf):
        H = surf.get_height()
        self.layout(H)
        cfg = self.app.cfg
        k = self.k
        rad = int(6 * k)
        pygame.draw.rect(surf, BG, (0, 0, self.w, H))
        pygame.draw.line(surf, (40, 40, 54), (self.w - 1, 0), (self.w - 1, H), max(1, int(k)))
        for d, r in self.rows:
            if r.bottom < 0 or r.top > H:
                continue
            kind = d['kind']
            if kind == 'section':
                t = self.head.render(d['label'].upper(), True, ACCENT)
                surf.blit(t, (self.pad, r.bottom - t.get_height() - int(9 * k)))
                pygame.draw.line(surf, (48, 48, 66), (self.pad, r.bottom - int(3 * k)),
                                 (self.w - self.pad, r.bottom - int(3 * k)), max(1, int(k)))
                continue
            if kind == 'buttons':
                self._draw_buttons(surf, d, r)
                continue
            if kind == 'info':
                txt = d['text'](cfg) if callable(d['text']) else d['text']
                t = self.small.render(self._clip(self.small, txt, self.w - 2 * self.pad), True, DIM)
                surf.blit(t, (self.pad, r.centery - t.get_height() // 2))
                continue

            lab = self.font.render(self._clip(self.font, _label(d, cfg), self.ctrl_x - self.pad - int(8 * k)),
                                   True, LABEL)
            surf.blit(lab, (self.pad, r.centery - lab.get_height() // 2))
            cr = self.ctrl_rect(r)
            v = cfg[d['key']]
            hover = cr.collidepoint(self.mouse) and not self.dropdown
            if kind == 'slider':
                mn, mx = self._bounds(d)
                frac = 0 if mx == mn else (min(mx, max(mn, v)) - mn) / (mx - mn)
                tw, vbox = self._slider_parts(cr)
                th = max(2, int(4 * k))
                track = pygame.Rect(cr.x, cr.centery - th // 2, tw, th)
                pygame.draw.rect(surf, FIELD, track, border_radius=th)
                pygame.draw.rect(surf, ACCENT, (track.x, track.y, int(track.w * frac), th), border_radius=th)
                kx = track.x + int(track.w * frac)
                pygame.draw.circle(surf, FG if not hover else (255, 255, 255), (kx, track.centery), int(8 * k))
                focused = self.focus is d
                vhover = vbox.collidepoint(self.mouse) and not self.dropdown
                pygame.draw.rect(surf, FIELD_HOVER if (vhover or focused) else FIELD, vbox, border_radius=int(5 * k))
                if focused:
                    pygame.draw.rect(surf, ACCENT, vbox, max(1, int(k)), border_radius=int(5 * k))
                    txt = self._clip(self.font, self.edit, vbox.w - int(14 * k), tail=True) + '|'
                    if self.fresh:  # whole value selected: typing replaces it
                        sel = pygame.Rect(0, 0, self.font.size(self.edit)[0] + int(4 * k), vbox.h - int(8 * k))
                        sel.midright = (vbox.right - int(10 * k), vbox.centery)
                        pygame.draw.rect(surf, (70, 90, 170), sel.clip(vbox), border_radius=int(3 * k))
                else:
                    txt = self._clip(self.font, _fmt(v, d['step']), vbox.w - int(10 * k))
                t = self.font.render(txt, True, FG)
                surf.blit(t, (vbox.right - t.get_width() - int(6 * k), vbox.centery - t.get_height() // 2))
            elif kind in ('choice', 'color'):
                pygame.draw.rect(surf, FIELD_HOVER if hover else FIELD, cr, border_radius=rad)
                x = cr.x + int(10 * k)
                if kind == 'color':
                    sw = pygame.Rect(x, cr.y + int(5 * k), int(22 * k), cr.h - int(10 * k))
                    pygame.draw.rect(surf, hex_rgb(v), sw, border_radius=int(4 * k))
                    pygame.draw.rect(surf, BORDER, sw, max(1, int(k)), border_radius=int(4 * k))
                    x = sw.right + int(8 * k)
                    name = str(v)
                else:
                    name = next((lab for val, lab in self._opts(d) if val == v), str(v))
                t = self.font.render(self._clip(self.font, name, cr.right - x - int(26 * k)), True, FG)
                surf.blit(t, (x, cr.centery - t.get_height() // 2))
                self._caret(surf, cr.right - int(14 * k), cr.centery)
            elif kind == 'toggle':
                s = int(22 * k)
                box = pygame.Rect(cr.right - s, cr.centery - s // 2, s, s)
                pygame.draw.rect(surf, ACCENT if v else (FIELD_HOVER if hover else FIELD), box, border_radius=int(5 * k))
                if v:
                    w = max(2, int(2.5 * k))
                    pygame.draw.lines(surf, FG, False, [(box.x + s * 0.24, box.centery), (box.x + s * 0.43, box.bottom - s * 0.27),
                                                        (box.right - s * 0.22, box.y + s * 0.27)], w)
            elif kind == 'text':
                focused = self.focus is d
                pygame.draw.rect(surf, FIELD_HOVER if (hover or focused) else FIELD, cr, border_radius=rad)
                if focused:
                    pygame.draw.rect(surf, ACCENT, cr, max(1, int(k)), border_radius=rad)
                txt = self.edit if focused else str(v)
                t = self.font.render(self._clip(self.font, txt, cr.w - int(20 * k), tail=focused) + ('|' if focused else ''),
                                     True, FG)
                surf.blit(t, (cr.x + int(8 * k), cr.centery - t.get_height() // 2))

        if self.content_h > H:
            bar_h = max(30 * k, H * H / self.content_h)
            y = (H - bar_h) * self.scroll / max(1, self.content_h - H)
            pygame.draw.rect(surf, (70, 70, 92), (self.w - int(6 * k), y, int(3 * k), bar_h), border_radius=int(2 * k))

        if self.dropdown:
            self._draw_dropdown(surf)

    def _caret(self, surf, x, y):
        s = 4 * self.k
        pygame.draw.polygon(surf, DIM, [(x - s, y - s / 2), (x + s, y - s / 2), (x, y + s / 2)])

    def _draw_buttons(self, surf, d, r):
        k = self.k
        btns = d['buttons']
        gap = int(8 * k)
        bw = (self.w - 2 * self.pad - gap * (len(btns) - 1)) / len(btns)
        for i, b in enumerate(btns):
            rect = pygame.Rect(self.pad + i * (bw + gap), r.y + int(6 * k), bw, r.h - int(12 * k))
            label = b['label'](self.app) if callable(b['label']) else b['label']
            col = b.get('color', (58, 58, 80))
            col = col(self.app) if callable(col) else col
            if rect.collidepoint(self.mouse) and not self.dropdown:
                col = tuple(min(255, c + 25) for c in col)
            pygame.draw.rect(surf, col, rect, border_radius=int(8 * k))
            t = self.btn.render(self._clip(self.btn, label, rect.w - int(8 * k)), True, FG)
            surf.blit(t, (rect.centerx - t.get_width() // 2, rect.centery - t.get_height() // 2))

    # -------------------------------------------------------------- dropdown
    def _dd_items(self, d):
        if d['kind'] == 'color':
            cur = str(self.app.cfg[d['key']]).lower()
            pal = PALETTE if cur in PALETTE else [cur] + PALETTE
            return [(c, c) for c in pal]
        return self._opts(d)

    def _dd_geometry(self):
        d, anchor, _ = self.dropdown
        items = self._dd_items(d)
        ih = int(32 * self.k)
        H = self.view_h
        n = len(items)
        below = H - anchor.bottom - self.pad
        above = anchor.top - self.pad
        room = max(below, above)
        vis = max(1, min(n, int(room // ih)))
        h = vis * ih + int(8 * self.k)
        x, w = anchor.x, anchor.w
        y = anchor.bottom + int(4 * self.k) if below >= h or below >= above else anchor.top - h - int(4 * self.k)
        return items, pygame.Rect(x, y, w, h), ih, vis

    def _draw_dropdown(self, surf):
        d, anchor, scroll = self.dropdown
        k = self.k
        items, box, ih, vis = self._dd_geometry()
        shadow = box.move(0, int(4 * k))
        pygame.draw.rect(surf, (8, 8, 12), shadow, border_radius=int(8 * k))
        pygame.draw.rect(surf, POPUP, box, border_radius=int(8 * k))
        pygame.draw.rect(surf, BORDER, box, max(1, int(k)), border_radius=int(8 * k))
        cur = self.app.cfg[d['key']]
        old_clip = surf.get_clip()
        surf.set_clip(box.inflate(-2, -2))
        for i in range(scroll, min(len(items), scroll + vis)):
            val, lab = items[i]
            ir = pygame.Rect(box.x + int(4 * k), box.y + int(4 * k) + (i - scroll) * ih, box.w - int(8 * k), ih)
            if ir.collidepoint(self.mouse):
                pygame.draw.rect(surf, (60, 64, 100), ir, border_radius=int(5 * k))
            elif val == cur:
                pygame.draw.rect(surf, (46, 48, 70), ir, border_radius=int(5 * k))
            x = ir.x + int(10 * k)
            if d['kind'] == 'color':
                sw = pygame.Rect(x, ir.y + int(6 * k), int(22 * k), ir.h - int(12 * k))
                pygame.draw.rect(surf, hex_rgb(val), sw, border_radius=int(4 * k))
                pygame.draw.rect(surf, BORDER, sw, max(1, int(k)), border_radius=int(4 * k))
                x = sw.right + int(8 * k)
            t = self.font.render(self._clip(self.font, str(lab), ir.right - x - int(24 * k)), True,
                                 ACCENT if val == cur else FG)
            surf.blit(t, (x, ir.centery - t.get_height() // 2))
            if val == cur:
                c = (ir.right - int(14 * k), ir.centery)
                s = 4 * k
                pygame.draw.lines(surf, ACCENT, False, [(c[0] - s, c[1]), (c[0] - s / 3, c[1] + s * 0.7),
                                                        (c[0] + s, c[1] - s * 0.8)], max(2, int(2 * k)))
        surf.set_clip(old_clip)
        if len(items) > vis:
            bar_h = box.h * vis / len(items)
            y = box.y + (box.h - bar_h) * scroll / max(1, len(items) - vis)
            pygame.draw.rect(surf, (90, 90, 115), (box.right - int(5 * k), y, int(3 * k), bar_h))

    def _dd_click(self, pos):
        d, anchor, scroll = self.dropdown
        items, box, ih, vis = self._dd_geometry()
        if box.collidepoint(pos):
            i = scroll + int((pos[1] - box.y - 4 * self.k) // ih)
            if 0 <= i < len(items):
                self.dropdown = None
                self.app.set(d['key'], items[i][0], final=True)
            return
        self.dropdown = None

    def _clip(self, font, txt, maxw, tail=False):
        txt = str(txt)
        if font.size(txt)[0] <= maxw:
            return txt
        while txt and font.size('…' + txt if tail else txt + '…')[0] > maxw:
            txt = txt[1:] if tail else txt[:-1]
        return '…' + txt if tail else txt + '…'

    def _bounds(self, d):
        cfg = self.app.cfg
        mn, mx = d['min'], d['max']
        return (mn(cfg) if callable(mn) else mn), (mx(cfg) if callable(mx) else mx)

    def _opts(self, d):
        o = d['options']
        return o(self.app.cfg) if callable(o) else o

    # ---------------------------------------------------------------- events
    def handle(self, e, pos=None):
        """Returns True if the event was consumed. `pos` = mouse pos in pixels."""
        if pos is not None:
            self.mouse = pos

        if e.type == pygame.MOUSEWHEEL:
            if self.dropdown:
                items, box, ih, vis = self._dd_geometry()
                d, a, sc = self.dropdown
                sc = max(0, min(len(items) - vis, sc - e.y))
                self.dropdown = (d, a, sc)
                return True
            if self.mouse[0] < self.w:
                self.scroll = max(0, min(max(0, self.content_h - self.view_h), self.scroll - e.y * 40 * self.k))
                return True
            return False

        if e.type == pygame.MOUSEBUTTONDOWN and e.button in (1, 3):
            if self.dropdown:
                self._dd_click(pos)
                return True
            if pos[0] >= self.w:
                self._commit()
                return False
            hit = next(((d, r) for d, r in self.rows if r.collidepoint(pos)), None)
            if self.focus and (not hit or hit[0] is not self.focus):
                self._commit()
            if hit:
                self._click(hit[0], hit[1], pos, e.button)
            return True

        if e.type == pygame.MOUSEMOTION and self.drag:
            d, r = self.drag
            self._slide(d, r, pos[0], final=False)
            return True

        if e.type == pygame.MOUSEBUTTONUP and self.drag:
            d, r = self.drag
            self.drag = None
            self._slide(d, r, pos[0], final=True)
            return True

        if e.type == pygame.KEYDOWN and self.dropdown:
            if e.key == pygame.K_ESCAPE:
                self.dropdown = None
            return True

        if self.focus:
            numeric = self.focus['kind'] == 'slider'
            if e.type == pygame.TEXTINPUT:
                txt = e.text
                if numeric:
                    txt = ''.join(ch for ch in txt if ch in '0123456789.-,eE')
                if self.fresh:
                    self.edit = ''
                    self.fresh = False
                self.edit += txt
                return True
            if e.type == pygame.KEYDOWN:
                mods = pygame.key.get_mods()
                if e.key == pygame.K_BACKSPACE:
                    if self.fresh or mods & (pygame.KMOD_META | pygame.KMOD_CTRL):
                        self.edit = ''
                    else:
                        self.edit = self.edit[:-1]
                    self.fresh = False
                elif numeric and e.key in (pygame.K_UP, pygame.K_DOWN):
                    self._nudge(self.focus, 1 if e.key == pygame.K_UP else -1)
                elif e.key == pygame.K_TAB:
                    # jump to the next (or previous) number box
                    sliders = [d for d, _ in self.rows if d['kind'] == 'slider']
                    cur = self.focus
                    self._commit()
                    if cur in sliders and sliders:
                        step = -1 if mods & pygame.KMOD_SHIFT else 1
                        self._focus_number(sliders[(sliders.index(cur) + step) % len(sliders)])
                elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self._commit()
                elif e.key == pygame.K_ESCAPE:
                    self.focus = None
                    pygame.key.stop_text_input()
                elif e.key == pygame.K_v and mods & (pygame.KMOD_META | pygame.KMOD_CTRL):
                    if self.fresh:
                        self.edit = ''
                        self.fresh = False
                    self.edit += self._paste()
                return True
        return False

    def _click(self, d, r, pos, button):
        cfg = self.app.cfg
        kind = d['kind']
        if kind == 'buttons':
            btns = d['buttons']
            gap = 8 * self.k
            bw = (self.w - 2 * self.pad - gap * (len(btns) - 1)) / len(btns)
            i = int((pos[0] - self.pad) // (bw + gap))
            if 0 <= i < len(btns):
                btns[i]['action'](self.app)
            return
        if 'key' not in d:
            return
        key = d['key']
        cr = self.ctrl_rect(r)
        if kind == 'slider' and button == 1:
            if self._slider_parts(cr)[1].collidepoint(pos):
                self._focus_number(d)
            else:
                if self.focus is d:  # dragging wins over a half-typed number
                    self.focus = None
                    pygame.key.stop_text_input()
                self.drag = (d, r)
                self._slide(d, r, pos[0], final=False)
        elif kind in ('choice', 'color') and cr.collidepoint(pos):
            items = self._dd_items(d)
            vals = [v for v, _ in items]
            cur = vals.index(cfg[key]) if cfg[key] in vals else 0
            self.dropdown = (d, cr, 0)
            _, _, _, vis = self._dd_geometry()
            self.dropdown = (d, cr, max(0, min(len(items) - vis, cur - vis // 2)))
        elif kind == 'toggle':
            self.app.set(key, not cfg[key], final=True)
        elif kind == 'text':
            self.focus = d
            self.edit = str(cfg[key])
            self.fresh = False
            pygame.key.start_text_input()

    def _slider_parts(self, cr):
        """(track width, value-box rect) for a slider's control area."""
        vw = int(68 * self.k)
        vbox = pygame.Rect(cr.right - vw, cr.y, vw, cr.h)
        return cr.w - vw - int(12 * self.k), vbox

    def _focus_number(self, d):
        self._commit()
        self.focus = d
        self.edit = _fmt(self.app.cfg[d['key']], d['step'])
        self.fresh = True
        pygame.key.start_text_input()

    def _number(self, d, text):
        """Parse typed text into a value within the slider's range, or None."""
        try:
            v = float(text.strip().replace(',', '.'))
        except ValueError:
            return None
        if v != v:  # NaN
            return None
        mn, mx = self._bounds(d)
        v = max(mn, min(mx, v))
        step = d['step']
        if step >= 1 and float(step).is_integer() and float(mn).is_integer():
            return int(round(v))
        return round(v, 6)

    def _nudge(self, d, direction):
        v = self._number(d, self.edit)
        if v is None:
            v = self.app.cfg[d['key']]
        v = self._number(d, str(v + direction * d['step']))
        if v is not None:
            self.edit = _fmt(v, d['step'])
            self.fresh = False
            self.app.set(d['key'], v)

    def _slide(self, d, r, x, final):
        cr = self.ctrl_rect(r)
        tw = self._slider_parts(cr)[0]
        frac = max(0.0, min(1.0, (x - cr.x) / tw))
        (mn, mx), step = self._bounds(d), d['step']
        v = mn + frac * (mx - mn)
        v = round(round(v / step) * step, 6)
        if step >= 1 and float(step).is_integer() and float(mn).is_integer():
            v = int(v)
        self.app.set(d['key'], v, final=final)

    def _commit(self):
        if self.focus:
            d = self.focus
            self.focus = None
            pygame.key.stop_text_input()
            if d['kind'] == 'slider':
                v = self._number(d, self.edit)
                if v is not None:
                    self.app.set(d['key'], v, final=True)
            else:
                self.app.set(d['key'], self.edit, final=True)

    @staticmethod
    def _paste():
        try:
            return subprocess.run(['pbpaste'], capture_output=True, text=True, timeout=2).stdout.replace('\n', ' ')
        except (OSError, subprocess.SubprocessError):
            return ''
