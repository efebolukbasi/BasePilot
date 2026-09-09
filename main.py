'''Entry point for the BasePilot GUI.'''
from __future__ import annotations
import os
import sys
from multiprocessing import freeze_support

# Make the project root (which contains the `app` package) importable when run
# from source. When frozen by PyInstaller, the package is already on the path.
_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

# When frozen, use the Tesseract bundled alongside the exe. It lives in a
# subfolder so its ~34 DLLs don't collide with PyInstaller's own libs at the
# bundle root. Respect an existing TESSERACT_CMD if the user set one.
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS') and not os.environ.get('TESSERACT_CMD'):
    _bundled_tess = os.path.join(sys._MEIPASS, 'tesseract', 'tesseract.exe')
    if os.path.isfile(_bundled_tess):
        os.environ['TESSERACT_CMD'] = _bundled_tess

# `--profile NAME` before anything reads the data directory: it decides where settings,
# the game-window pin, captured templates, the log and debug frames live, so two copies
# of BasePilot can drive two clients at once without overwriting each other. Read from
# argv here rather than in the GUI parser because the very first import below already
# resolves the log path.
if '--profile' in sys.argv:
    try:
        os.environ['BASEPILOT_PROFILE'] = sys.argv[sys.argv.index('--profile') + 1]
    except IndexError:
        pass

from app.ui.qt.app import run_gui
from app.utils.logger import setup_logger
from app.utils.tesseract_env import configure_tesseract
logger = setup_logger('Main')


def main():
    try:
        configure_tesseract()
        try:
            from app.services.display import DisplayService
            DisplayService().restore_if_pending()
        except Exception:
            logger.warning('Display restore-if-pending check failed', exc_info = True)
        logger.info('Starting Application...')
        run_gui()
    except Exception as e:
        logger.critical(f'''Unhandled exception: {e}''', exc_info = True)
        sys.exit(1)


if __name__ == '__main__':
    freeze_support()
    main()
