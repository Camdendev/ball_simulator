import colorsys
import functools


@functools.lru_cache(maxsize=256)
def hex_rgb(h):
    h = str(h).lstrip('#')
    if len(h) == 3:
        h = ''.join(ch * 2 for ch in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (255, 255, 255)


def hsl(h, s=0.9, l=0.6):
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, l, s)
    return (int(r * 255), int(g * 255), int(b * 255))


def mix(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


# Colors the in-app color pickers cycle through (edit config.json for any hex).
PALETTE = ['#ffffff', '#ff3b5c', '#ff8c42', '#ffd23f', '#7cff6b', '#00ffd5',
           '#2ec5ff', '#4d7cff', '#c77dff', '#ff66c4', '#f5f0e6', '#9aa0a6',
           '#0a0a12', '#000000', '#14213d', '#1b1b2f', '#2a0a1a', '#0a2a1a']
