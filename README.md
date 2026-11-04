# Ball Simulator

Make satisfying bouncing-ball videos (TikTok / Reels / Shorts style) where every bounce plays the next note of a song.

Built with Python, pygame-ce, and numpy. Videos render offline at a locked 60 fps in full 1080×1920, with audio synthesized sample-accurately, so sound and bounces are always in sync.

## Setup

Uses **pygame-ce** (the community edition) because it supports sharp high-DPI/Retina windows. Don't install regular `pygame` in the same environment: they conflict.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

You don't need to install ffmpeg yourself: it comes with `imageio-ffmpeg`. If one is on your PATH, that one is used instead.

## Modes

| Mode | What happens |
|---|---|
| **Escape** | Ball bounces inside a spinning ring with a gap. Every escape spawns N new balls. |
| **Ring escape** | Concentric spinning rings. Each one shatters when the ball gets through its gap. |
| **Spinning polygon** | A rotating triangle-to-12-gon, optionally with one open side to escape through. |
| **Breakable wall** | The ring is made of segments. Each hit cracks one, and broken ones let the ball out. |
| **Grow** | The ball grows a little on every bounce until it fills the circle. |
| **Shrinking circle** | The circle closes in on every bounce until the ball is trapped. |
| **Multiply** | Every bounce has a chance to spawn a copy of the ball. |
| **Line battle** | Every bounce leaves a line from the wall to the ball. Balls cut enemy lines, a ball whose last line is cut is eliminated, and the last ball standing wins. |
| **Territory battle** | Two balls fight over a grid, flipping the enemy's tiles to their color. |

What you can control includes:
- **Start:** position, launch angle or random launch, spread, where the opening starts, starting lines
- **Physics:** gravity strength and direction, air drag, bounciness, speed-up per bounce, max speed, ball collisions, wall spin and spin-up per bounce, what happens on escape
- **Look:** solid or gradient background, ball and ring color modes, rainbow step, trails, glow, particle amount
- **Text & layout:** font, bold, shadow or outline, sizes and colors for the title, stats, footer and banner, a timer, team names. Drag text or the arena in the preview to reposition it.
- **Export:** 720p to 4K, 30 or 60 fps, quality, and supersampled anti-aliasing

## Songs

- **Built-in melodies:** public-domain pieces such as Ode to Joy, Für Elise, and Bach's Prelude.
- **Typed notes:** for example `C4 E4 G4+C5 _ F#4`. Use `+` for a chord and `_` for a rest.
- **MIDI file:** drag a `.mid` onto the window. Each bounce plays the next chord, and you can pick a track or "melody only".
- **Audio file:** drag an `.mp3`/`.wav`/etc. The real song plays only while the balls keep bouncing.

You can also drag a `.txt` file of notes, or a `.json` config, onto the window.

## Nested walls

**Number of walls** (Arena section) works in every mode except Territory battle.

- **Escape, polygon, breakable wall:** inner walls shatter when a ball gets out, and the outermost wall then behaves normally.
- **Ring escape:** every wall shatters.
- **Grow and shrink:** each full or trapped wall breaks, and the next level starts.
- **Hits to break wall:** walls can also break after N hits, in any mode.

## Auto-fit to time

Set a **Target length** and click **Fit to time**. The app runs quick simulations in the background, adjusts the mode's main setting, then tries other seeds until the run ends near your target. Most modes land within a few tenths of a second. It also makes sure the recording's max length covers the result.

What it adjusts in each mode:

| Mode | Setting |
|---|---|
| Escape, Ring escape | gap size |
| Grow | growth per bounce |
| Shrink | shrink per bounce |
| Multiply | multiply chance |
| Breakable wall | hits per segment |
| Line battle | no-deaths period |
| Polygon | speed |

You can choose to adjust only the speed or only the seed instead. Territory battle never ends on its own, so it can't be fitted.

## Recording

Click **Record MP4**. The current settings are re-run from the start and rendered to `recordings/`. Recording stops at the max length, or a couple of seconds after the run finishes (all rings broken, ball full size, and so on). The same seed always gives the same run, so what you previewed is what you get.

Headless / batch rendering:

```bash
.venv/bin/python main.py --config config.json --render out.mp4
```

## Controls

- `Space` play/pause · `R` restart · `Esc` cancel a recording
- Every option is a dropdown: click it, then pick an item (scroll for long lists, Esc to close)
- Colors are dropdowns with swatches (or set any hex in `config.json`)
- The window renders at native Retina resolution. Preview smoothing and video anti-aliasing (supersampling) are in the Look and Recording sections.
- Settings auto-save to `config.json`
