"""Ball Simulator: make satisfying bouncing-ball videos synced to music.

    python main.py                         # open the editor
    python main.py --config my.json        # editor with a specific config
    python main.py --render out.mp4        # render headlessly from config.json
"""
import argparse
import os
import sys

os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--config', default='config.json', help='settings file (auto-saved by the editor)')
    ap.add_argument('--render', metavar='OUT.mp4', help='render a video without opening the editor')
    args = ap.parse_args()

    if args.render:
        os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
        os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')
        import pygame
        pygame.init()
        from ballsim.config import load_config
        from ballsim.export import Exporter

        ex = Exporter(load_config(args.config), args.render)
        while ex.step():
            if ex.frame % 30 == 0:
                print(f'\rRendering {ex.frame / ex.fps:6.1f}s  ({ex.progress * 100:3.0f}% of max)', end='', flush=True)
        print('\nEncoding audio + muxing…')
        print(f'Saved {ex.finalize()}')
        return 0

    from ballsim.app import App
    App(args.config).run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
