'''Clan assist: donate to clanmates' requests and request reinforcements.

The whole flow is template-driven — the same screen-capture + match + click loop the
attack and upgrade flows use, and **no OCR**, so it works whether or not Tesseract is
installed. Seven pieces of the game's own art drive it, captured once from your client
(*Settings -> Clan assist -> Capture templates*, which crops them at the reference
resolution for you):

===================== ============================================================
``clanmenu.png``      the orange speech-bubble button that opens the clan menu
``clannotify.png``    the green "!" badge — the game's own "there are more requests
                      than this screen shows"; followed when present
``donate.png``        the green **Donate** button on a clanmate's request
``donatetroop*.png``  the troops you hand over, as they appear in the donate panel —
                      one crop per troop (``donatetroop.png``, ``donatetroop2.png``…),
                      all of them tried in turn
``send.png``          the green **Send** button that confirms a troop request
``requesttroops.png`` the castle-with-a-plus button that asks for reinforcements
``clanexit.png``      the "<<" button that closes the clan menu
===================== ============================================================

The order they are used in is the order a player uses them: open the menu, donate
(Donate -> tap the troop -> tap outside the panel) to every request on screen, follow
the "!" badge for the ones the screen is not showing, ask for reinforcements
(Request -> Send), close the menu. There is no scrolling: the menu is done when it
shows neither a Donate button nor the badge, and that empty screen is the proof
nothing was left behind.

Donating has no confirm step — each tap on the troop hands one over — so the donate
panel is left the way a player leaves it, with a tap outside it. That tap sometimes
takes the whole clan menu down with the panel, so the menu is re-opened (verified)
before the visit continues.

**When to stop giving is the game's call, not a counter.** A troop that can give no
more is drawn in greyscale, so a greyed icon is never tapped and tapping continues
until the icon greys — then the next captured troop takes over. When everything on
screen is grey the troop row is swiped left, because the rest of the army sits off the
edge of it; only when a swiped row turns up nothing donatable is the request done.

``clanexit.png`` doubles as the state signal: it exists only while the clan menu is
open, so it is what proves the menu opened and what proves it closed again — no
guessing from pixel density, no assuming a click landed.

Safety rails, same spirit as the upgrader:

- **Send** is the only click that could ever cost something (a request during castle
  cooldown offers a gem boost), so it is vetoed when the art around it shows a red
  cost or a gem icon.
- Every click target is re-found on a fresh frame; stale coordinates are never reused
  after a panel moves.
- Panels are escaped via their own close button, never a blind confirm.
- Unverified states dump a frame to ``%LOCALAPPDATA%\\BasePilot\\debug\\clan_*.jpg``.
- ``dry_run`` walks in, logs every click it *would* make, and clicks nothing but the
  menu open/close — the intended first run after capturing templates.
- After ``_SESSION_FAILURE_LIMIT`` passes that could not verify what they were looking
  at, the assistant disables itself for the rest of the session.
'''
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from app.services.vision import VisionService
from app.utils.common import get_template_path, get_user_app_data_dir
from app.utils.logger import setup_logger

logger = setup_logger('ClanAssist')

TEMPLATE_CLAN_MENU = 'clanmenu.png'
TEMPLATE_CLAN_BADGE = 'clannotify.png'
TEMPLATE_DONATE = 'donate.png'
TEMPLATE_DONATE_TROOP = 'donatetroop.png'
TEMPLATE_SEND = 'send.png'
TEMPLATE_REQUEST = 'requesttroops.png'
TEMPLATE_CLAN_EXIT = 'clanexit.png'
# What the capture dialog lists, and what the docs describe. (name, label, required)
CLAN_TEMPLATE_SPECS: Tuple[Tuple[str, str, bool], ...] = (
    (TEMPLATE_CLAN_MENU, 'Clan menu button (orange speech bubble)', True),
    (TEMPLATE_CLAN_EXIT, 'Close clan menu button ("<<")', True),
    (TEMPLATE_DONATE, 'Donate button (green)', False),
    (TEMPLATE_DONATE_TROOP, 'First troop to donate, as shown in the donate panel', False),
    (TEMPLATE_SEND, 'Send button (green) — confirms a troop request', False),
    (TEMPLATE_REQUEST, 'Request troops button (castle with +)', False),
    (TEMPLATE_CLAN_BADGE, 'Notification badge (green "!") — optional', False))

# Deploy-bar fallbacks for the donated troop, used only when donatetroop.png has not
# been captured. They are the attack flows' art on a different background, so they
# match less well than a crop from the donate panel itself.
DONATE_TROOP_AUTO = 'auto'
DONATE_TROOP_TEMPLATES: dict[str, tuple[str, ...]] = {
    DONATE_TROOP_AUTO: ('valkyrie.png', 'sneaky.png', 'superminion.png', 'edrag.png'),
    'valkyries': ('valkyrie.png',),
    'sneaky goblins': ('sneaky.png',),
    'super minions': ('superminion.png',),
    'edrags': ('edrag.png',) }
DONATE_TROOP_OPTIONS = tuple(DONATE_TROOP_TEMPLATES)

_BUTTON_THRESHOLD = 0.8  # flat UI art matches high; keep the bar where the rest of the bot has it
_TROOP_THRESHOLD = 0.75  # troop portraits are busier art
_BADGE_THRESHOLD = 0.8
_MENU_OPEN_POLLS = 8  # ~4s for the panel to slide out
_PANEL_POLLS = 6  # ~3s for a sub-panel (donate / request) to render
_MAX_REQUESTS_PER_PASS = 20  # hard stop for one visit, however long the request list is
_MAX_BADGE_HOPS = 6  # times the "!" badge may be followed before leaving the rest for later
# A troop the game has greyed out is spent — the request is full, or your camps are.
# The icon is drawn in greyscale, so "is it still coloured?" is the whole test, and it
# is what ends a donation instead of a tap count.
_GREY_SATURATION_FLOOR = 60  # HSV saturation at/above which a pixel counts as coloured
_GREY_COLOURED_MAX_FRACTION = 0.15  # fewer coloured pixels than this = greyed out
_MAX_TROOP_TAPS_PER_REQUEST = 30  # backstop in case an icon never greys (never reached normally)
# The donate panel's troop row runs off the side of the screen. When everything on it is
# grey, the rest of the army may still be one swipe away.
_MAX_TROOP_ROW_SWIPES = 4
_TROOP_ROW_SWIPE_FRAC = 0.18  # swipe distance, as a fraction of frame width, each way
_RED_VETO_FRACTION = 0.05  # red (a cost) around Send = never click
_GEM_TEMPLATE = 'bgem.png'
_VETO_PAD_FRAC = 0.035  # patch sampled around a button, as a fraction of frame height
_SESSION_FAILURE_LIMIT = 5


@dataclass(frozen = True)
class ClanOptions:
    '''What the user asked for, resolved from the Run page + profile settings.'''
    donate: bool = False
    request: bool = False
    dry_run: bool = True
    chat_point: Optional[Tuple[float, float]] = None  # (fx, fy) fractions — fallback way in
    chat_point_aspect: Optional[str] = None  # aspect key the point was picked on
    donate_troop: str = DONATE_TROOP_AUTO
    donate_count: int = 0  # 0 = keep giving until the troop greys out
    donate_interval_s: int = 0  # 0 = donate on every home visit (after each raid)
    request_interval_s: int = 1800
    min_elixir: int = 500000  # skip the donate errand below this much elixir; 0 = no check

    @property
    def enabled(self):
        return bool(self.donate or self.request)


@dataclass(frozen = True)
class ClanPassResult:
    '''One clan-menu visit, for the bot loop and the UI status line.'''
    opened: bool
    donated: int  # troops handed over (0 in dry run)
    requested: bool
    note: str


def donate_troop_template_names():
    '''Every ``donatetroop*.png`` on disk, user drop-ins first, sorted by name.

    One troop is rarely enough — a clan asks for whatever it asks for — so the donate
    panel is matched against **all** of them and the first one present is handed over.
    Add a troop by capturing another crop; no code or settings change needed.
    '''
    from app.config import Config
    from app.utils.common import get_resource_path, user_template_dirs
    names: List[str] = []
    aspect = Config().aspect_key
    for folder in [ d / aspect for d in user_template_dirs() ] + [
                   get_resource_path(f'''templates/{aspect}''')]:
        try:
            found = sorted(p.name for p in folder.glob('donatetroop*.png') if p.is_file())
        except OSError:
            found = []
        names.extend(n for n in found if n not in names)
    return names


def missing_templates(*, donate = True, request = True, donate_troop = DONATE_TROOP_AUTO, have_chat_point = False):
    '''Templates the requested errands need but which are not on disk. The names are
    reported to the user; nothing is clicked while any of them is missing.

    ``have_chat_point``: a calibrated click point stands in for the clan menu button,
    so that one template stops being required. Nothing stands in for the close button —
    it is how the flow knows the menu is open.'''
    required = (TEMPLATE_CLAN_EXIT,) if have_chat_point else (TEMPLATE_CLAN_MENU, TEMPLATE_CLAN_EXIT)
    missing = [name for name in required if not get_template_path(name).exists()]
    if donate:
        # No Send here: troops are handed over on the taps themselves, and the panel is
        # closed with a tap outside it.
        if not get_template_path(TEMPLATE_DONATE).exists():
            missing.append(TEMPLATE_DONATE)
        fallback = DONATE_TROOP_TEMPLATES.get(
            (donate_troop or DONATE_TROOP_AUTO).strip().lower(), DONATE_TROOP_TEMPLATES[DONATE_TROOP_AUTO])
        if not donate_troop_template_names() and not any(get_template_path(n).exists() for n in fallback):
            missing.append(TEMPLATE_DONATE_TROOP)
    if request:
        missing += [name for name in (TEMPLATE_REQUEST, TEMPLATE_SEND)
                    if not get_template_path(name).exists()]
    seen = []
    for name in missing:
        if name not in seen:
            seen.append(name)
    return seen


def _dump_debug_frame(frame, prefix):
    '''Save a frame to the debug dir; returns the path or None. Never raises.'''
    if frame is None:
        return None
    try:
        import cv2 as _cv2
        dbg = get_user_app_data_dir() / 'debug'
        dbg.mkdir(parents = True, exist_ok = True)
        path = dbg / f'''{prefix}_{int(time.time())}.jpg'''
        _cv2.imwrite(str(path), frame, [_cv2.IMWRITE_JPEG_QUALITY, 88])
        logger.info('Clan assist: frame saved to %s', path)
        return path
    except Exception:
        logger.debug('Clan assist: debug frame dump failed', exc_info = True)
        return None


class ClanAssistant:
    '''Donates and requests reinforcements through the clan menu.

    Keep ONE instance per bot session: it counts the failures that disable the
    feature, so a bad template stops the flow after a handful of passes instead of
    clicking the same wrong pixel all night.
    '''

    def __init__(self, window, input_service, vision, config, stop_event, options, status_callback = None):
        self.window = window
        self.input = input_service
        self.vision = vision
        self.config = config
        self.stop_event = stop_event
        self.options = options
        self._status_callback = status_callback
        self.failures = 0
        self.disabled = False

    # ------------------------------------------------------------------ plumbing

    def _check_stop(self):
        if self.stop_event.is_set():
            raise InterruptedError('Bot stopped by user')

    def _wait(self, seconds):
        if self.stop_event.wait(seconds):
            raise InterruptedError('Bot stopped by user')

    def _frame(self):
        '''Fresh screenshot with the config's target size synced to it.'''
        self._check_stop()
        frame = self.window.screenshot()
        if frame is None or not getattr(frame, 'size', 0):
            return None
        self.config.set_target_size_from_frame(frame)
        return frame

    def _status(self, msg):
        cb = self._status_callback
        if cb:
            try:
                cb(msg)
            except Exception:
                logger.debug('Clan assist: status callback failed', exc_info = True)

    def _note_failure(self, why, frame = None):
        '''Count an unverified pass; disable the feature once they pile up.'''
        self.failures += 1
        logger.warning('Clan assist: %s (%d/%d)', why, self.failures, _SESSION_FAILURE_LIMIT)
        _dump_debug_frame(frame, 'clan')
        if self.failures >= _SESSION_FAILURE_LIMIT:
            self.disabled = True
            msg = 'Clan assist disabled for this session — the clan menu could not be read. Re-capture the templates in Settings.'
            logger.error(msg)
            self._status(msg)

    # ------------------------------------------------------------- template finds

    def _find(self, frame, name, threshold = _BUTTON_THRESHOLD, region = None):
        '''Center of ``name`` on this frame, or (None, None). A template file that was
        never captured is not an error here — the caller decides what to do about it.'''
        if not get_template_path(name).exists():
            return (None, None)
        return self.vision.find_template(frame, name, threshold = threshold, region = region)

    def _find_all_top_down(self, frame, name, threshold = _BUTTON_THRESHOLD):
        '''Every hit for ``name``, topmost first (the oldest request in the list).'''
        if not get_template_path(name).exists():
            return []
        try:
            centers = VisionService.find_all_template_centers(frame, name, threshold = threshold) or []
        except Exception:
            logger.debug('Clan assist: multi-match failed for %s', name, exc_info = True)
            return []
        return sorted(centers, key = lambda c: c[1])

    def _wait_for(self, name, polls = _PANEL_POLLS, threshold = _BUTTON_THRESHOLD):
        '''Poll for a template; returns ((x, y), frame) with the frame it was seen on,
        or ((None, None), last_frame) when it never showed.'''
        frame = None
        for _ in range(max(1, polls)):
            frame = self._frame()
            if frame is not None:
                (x, y) = self._find(frame, name, threshold = threshold)
                if x:
                    return ((x, y), frame)
            self._wait(0.5)
        return ((None, None), frame)

    # ---------------------------------------------------------------- gem guard

    def _price_veto(self, frame, x, y):
        '''True when the art around (x, y) shows a cost — a red price zone or a gem
        icon. Donating and requesting are free; a request during castle cooldown is
        the one place the game offers a gem-priced button, and this is what keeps a
        click off it.'''
        (h, w) = frame.shape[:2]
        pad = max(12, int(round(h * _VETO_PAD_FRAC)))
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(w, x + pad)
        y1 = min(h, y + pad)
        if x1 <= x0 or y1 <= y0:
            return True
        patch = frame[y0:y1, x0:x1]
        if VisionService.red_hue_fraction(patch) >= _RED_VETO_FRACTION:
            logger.warning('Clan assist: red cost around (%s, %s) — not clicking', x, y)
            return True
        if get_template_path(_GEM_TEMPLATE).exists():
            (gx, _gy) = self.vision.find_template(frame, _GEM_TEMPLATE, region = (x0, y0, x1 - x0, y1 - y0))
            if gx:
                logger.warning('Clan assist: gem icon beside (%s, %s) — not clicking', x, y)
                return True
        return False

    # ---------------------------------------------------------------- clan menu

    def _menu_is_open(self, frame):
        '''The close ("<<") button exists only while the clan menu is open.'''
        (x, _y) = self._find(frame, TEMPLATE_CLAN_EXIT)
        return bool(x)

    def _chat_click_point(self, frame):
        '''Fallback way in: the calibrated fraction from Settings, if there is one.'''
        point = self.options.chat_point
        if point is None:
            return None
        (h, w) = frame.shape[:2]
        (fx, fy) = point
        return (min(w - 1, max(0, int(round(float(fx) * w)))),
                min(h - 1, max(0, int(round(float(fy) * h)))))

    def open_menu(self):
        '''Open the clan menu and confirm it by its close button. Dry run opens it too —
        walking the menu is how a fresh set of templates gets checked.'''
        frame = self._frame()
        if frame is None:
            return False
        if self._menu_is_open(frame):
            return True
        (x, y) = self._find(frame, TEMPLATE_CLAN_MENU)
        if not x:
            point = self._chat_click_point(frame)
            if point is None:
                self._note_failure(f'''{TEMPLATE_CLAN_MENU} not found on the home screen''', frame)
                return False
            (x, y) = point
            logger.info('Clan assist: %s not matched — using the calibrated point %s', TEMPLATE_CLAN_MENU, point)
        logger.info('Clan assist: opening the clan menu at (%s, %s)', x, y)
        self.input.click(x, y, pause = 0.6)
        ((ex, _ey), frame) = self._wait_for(TEMPLATE_CLAN_EXIT, polls = _MENU_OPEN_POLLS)
        if not ex:
            self._note_failure('clan menu did not open', frame)
            return False
        return True

    def close_menu(self):
        '''Close the menu and confirm it. The farm loop taps Attack next, and an open
        panel would swallow that tap.'''
        for _ in range(3):
            frame = self._frame()
            if frame is None:
                return False
            (x, y) = self._find(frame, TEMPLATE_CLAN_EXIT)
            if not x:
                return True  # the close button is gone, so the menu is
            self.input.click(x, y, pause = 0.5)
        logger.warning('Clan assist: clan menu still open — leaving it to home-screen recovery')
        return False

    def _escape_subpanel(self):
        '''Back out of a donate/request panel: its own close button when it has one,
        otherwise a tap on empty ground — which is how a player dismisses a panel that
        has no X, and the donate panel has none. Never a blind confirm.'''
        frame = self._frame()
        if frame is None:
            return None
        for name in ('exit.png', 'okay.png'):
            (x, y) = self._find(frame, name)
            if x:
                self.input.click(x, y, pause = 0.3)
                return None
        try:
            (px, py) = self.config.get_point('empty')
        except KeyError:
            logger.debug('Clan assist: no empty point in data.json')
            return None
        logger.info('Clan assist: tapping outside the panel at (%s, %s)', px, py)
        self.input.click(px, py, pause = 0.5)

    # ---------------------------------------------------------------- donations

    def _troop_templates(self):
        '''Every troop crop the user has, then the deploy-bar art as a fallback.'''
        names = list(donate_troop_template_names())
        if names:
            return names
        key = (self.options.donate_troop or DONATE_TROOP_AUTO).strip().lower()
        fallback = DONATE_TROOP_TEMPLATES.get(key, DONATE_TROOP_TEMPLATES[DONATE_TROOP_AUTO])
        return [n for n in fallback if get_template_path(n).exists()]

    def _find_troop(self, frame, skip = ()):
        '''First matching troop icon in the donate panel, greyed out or not.'''
        for name in self._troop_templates():
            if name in skip:
                continue
            (x, y) = self.vision.find_template(frame, name, threshold = _TROOP_THRESHOLD)
            if x:
                return (name, x, y)
        return None

    def _template_size_on_screen(self, frame, name):
        '''How big ``name`` is drawn on this capture — the template's own size scaled the
        way :meth:`VisionService.find_template` scales it before matching.'''
        try:
            template = cv2.imread(str(get_template_path(name)))
        except Exception:
            template = None
        if template is None:
            return None
        (th, tw) = template.shape[:2]
        (h, w) = frame.shape[:2]
        return (max(1, int(round(tw * w / self.config.ref_width))),
                max(1, int(round(th * h / self.config.ref_height))))

    def _troop_is_greyed(self, frame, name, x, y):
        '''True when the troop icon is drawn in greyscale, i.e. it cannot be donated any
        more — the request is full, or the camps are. This is what says "stop tapping".

        Judged on the icon itself, by how much of it is still coloured. An icon we
        cannot measure is treated as donatable: the game's own greying is the stop
        signal, and a failed measurement must not silently end every donation.
        '''
        size = self._template_size_on_screen(frame, name)
        (fh, fw) = frame.shape[:2]
        if size is None:
            side = max(16, int(fh * 0.04))
            size = (side, side)
        (tw, th) = size
        x0 = max(0, int(x - tw // 2))
        y0 = max(0, int(y - th // 2))
        x1 = min(fw, x0 + tw)
        y1 = min(fh, y0 + th)
        patch = frame[y0:y1, x0:x1]
        if patch.size == 0:
            return False
        saturation = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[:, :, 1]
        coloured = float(np.mean(saturation >= _GREY_SATURATION_FLOOR))
        greyed = coloured < _GREY_COLOURED_MAX_FRACTION
        # The fraction is logged either way: it is the number to look at if a troop is
        # called spent too early (crop carries too much dark background) or too late.
        logger.debug('Clan assist: %s is %.0f%% coloured -> %s', name, coloured * 100,
                     'greyed out' if greyed else 'donatable')
        return greyed

    def _first_donatable_troop(self, frame):
        '''The first troop icon that is still coloured, or None when every troop we know
        is greyed out (or none is on screen).'''
        skip = set()
        while True:
            hit = self._find_troop(frame, skip = skip)
            if hit is None:
                return None
            (name, x, y) = hit
            if not self._troop_is_greyed(frame, name, x, y):
                return hit
            logger.info('Clan assist: %s is greyed out — nothing left to give from it', name)
            skip.add(name)

    def _donate_one_request(self):
        '''Give to one request, with the donate panel already open. Returns troops handed
        over.

        Taps a troop until the game greys it out — that is the game itself saying the
        request is full (or the camps are), so there is no tap count to guess at. When
        one troop greys, the next captured troop takes over; when they all have, the
        request is done. The icon is re-found on a fresh frame before every tap, so a
        panel that closes itself mid-batch ends it instead of tapping the village.

        The troop row runs off the side of the screen, so when everything visible has
        greyed the row is swiped left to bring the rest of the army into view before
        giving up on the request.

        ``donate_count`` (Settings) caps this when set; 0 means "until it greys".
        '''
        hit = None
        frame = None
        for _ in range(_PANEL_POLLS):
            frame = self._frame()
            if frame is not None:
                hit = self._find_troop(frame)  # anything at all, greyed or not
                if hit is not None:
                    break
            self._wait(0.5)
        if hit is None:
            self._note_failure('donate panel showed no known troop icon', frame)
            self._dismiss_donate_panel()  # the panel has no X — a tap outside is the way out
            return 0
        cap = max(0, int(self.options.donate_count or 0)) or _MAX_TROOP_TAPS_PER_REQUEST
        cap = min(cap, _MAX_TROOP_TAPS_PER_REQUEST)
        row_y = hit[2]  # the troop row's height on screen, for the swipe
        given = 0
        swipes = 0
        while given < cap:
            frame = self._frame()
            if frame is None:
                break
            hit = self._first_donatable_troop(frame)
            if hit is None:
                if swipes >= _MAX_TROOP_ROW_SWIPES:
                    logger.info('Clan assist: whole troop row is grey after %d troop(s)', given)
                    break
                swipes += 1
                self._swipe_troop_row(frame, row_y)
                continue
            (name, x, y) = hit
            row_y = y
            self.input.click(x, y, pause = 0.35)
            given += 1
            logger.info('Clan assist: donated 1x %s (%d so far for this request)', name, given)
        self._dismiss_donate_panel()
        return given

    def _swipe_troop_row(self, frame, row_y):
        '''Swipe the donate panel's troop row leftwards, revealing the troops that sit
        off the right edge.

        A touch-style drag along the row, the way the wall menu scrolls its list — the
        mouse wheel scrolls vertically and this row runs sideways. Swiping past the end
        is a harmless no-op, so an extra one costs nothing.
        '''
        (h, w) = frame.shape[:2]
        step = max(40, int(w * _TROOP_ROW_SWIPE_FRAC))
        y = int(min(max(row_y, 1), h - 2))
        x_from = int(min(w - 2, w // 2 + step))
        x_to = int(max(1, w // 2 - step))
        logger.info('Clan assist: every visible troop is grey — swiping the row left for more')
        self.input.move(x_from, y)
        self.input.mouse_down(x_from, y)
        try:
            self.input.human_move(x_from, y, x_to, y, duration = 0.4)
        finally:
            self.input.mouse_up(x_to, y)
        self._wait(0.4)  # let the row settle before the next match

    def _dismiss_donate_panel(self):
        '''Leave the donate panel and land back in the clan menu.

        The troops are handed over on the taps themselves — there is nothing to
        confirm — so the panel is closed the way a player closes it: a tap outside it.
        A client that *does* draw a confirm button is honoured first (it costs nothing
        to press Send where one exists, and skips a stray tap on the village).
        '''
        frame = self._frame()
        if frame is not None:
            (sx, sy) = self._find(frame, TEMPLATE_SEND)
            if sx and not self._price_veto(frame, sx, sy):
                logger.info('Clan assist: Send at (%s, %s) — confirming the donation', sx, sy)
                self.input.click(sx, sy, pause = 0.6)
                return None
        self._escape_subpanel()

    def _ensure_menu_open(self):
        '''The tap that closes the donate panel can close the whole clan menu with it.
        Re-open it (verified, like any other open) so the rest of the visit still
        happens instead of clicking into the village.'''
        frame = self._frame()
        if frame is None:
            return False
        if self._menu_is_open(frame):
            return True
        logger.info('Clan assist: clan menu closed with the panel — re-opening')
        return self.open_menu()

    def _work_left(self, frame):
        '''What the donate side still has to do on this frame: a Donate button to press,
        or the "!" badge that brings in the requests this screen is not showing.
        Returns ``('donate', x, y)``, ``('badge', x, y)``, or None when the menu is
        genuinely clear — which is the condition for leaving.'''
        centers = self._find_all_top_down(frame, TEMPLATE_DONATE)
        if centers:
            (x, y) = centers[0]  # topmost = the clanmate who has waited longest
            return ('donate', x, y)
        (bx, by) = self._find(frame, TEMPLATE_CLAN_BADGE, threshold = _BADGE_THRESHOLD)
        if bx:
            return ('badge', bx, by)
        return None

    def donate_pass(self):
        '''Give to every open request, then leave. Returns the number of troops handed
        over (always 0 in dry run).

        No scrolling: the clan menu shows a screenful of requests, and the "!" badge is
        the game's own way of bringing in the ones it is not showing. So the loop is
        simply "press Donate while there is one, follow the badge when there is not,
        stop when neither is on screen" — and that empty screen is also the proof that
        nothing was left behind.
        '''
        if self.options.dry_run:
            frame = self._frame()
            work = self._work_left(frame) if frame is not None else None
            if work is None:
                logger.info('Clan assist (dry run): no Donate button and no badge — nothing to give')
            elif work[0] == 'donate':
                logger.info('Clan assist (dry run): would donate at (%s, %s)', work[1], work[2])
            else:
                logger.info('Clan assist (dry run): no Donate button, would follow the "!" badge at (%s, %s)', work[1], work[2])
            return 0
        donated = 0
        requests_left = _MAX_REQUESTS_PER_PASS
        hops = 0
        while requests_left > 0:
            self._check_stop()
            frame = self._frame()
            if frame is None:
                break
            work = self._work_left(frame)
            if work is None:
                logger.info('Clan assist: no Donate button and no badge left — every request is served')
                break
            (kind, x, y) = work
            if kind == 'badge':
                if hops >= _MAX_BADGE_HOPS:
                    logger.info('Clan assist: badge still showing after %d hops — leaving the rest for the next visit', hops)
                    break
                hops += 1
                logger.info('Clan assist: nothing to donate on this screen — following the "!" badge (%d)', hops)
                self.input.click(x, y, pause = 0.6)
                if not self._ensure_menu_open():
                    break
                continue
            logger.info('Clan assist: tapping Donate at (%s, %s)', x, y)
            self.input.click(x, y, pause = 0.5)
            given = self._donate_one_request()
            donated += given
            requests_left -= 1
            # Re-open before deciding anything: the tap that closed the panel may have
            # closed the menu, and even a failed request must leave a verified screen
            # behind for the rest of the visit (and for close_menu) to work with.
            if given == 0 or not self._ensure_menu_open():
                break
        else:
            logger.info('Clan assist: hit the %d-request cap for one visit', _MAX_REQUESTS_PER_PASS)
        return donated

    # ----------------------------------------------------------------- requests

    def request_pass(self):
        '''Ask the clan for reinforcements: Request troops -> Send. Returns True when
        the request was sent.

        No Request button is the normal case, not a failure: the castle is full, or a
        request is already pending.
        '''
        self._check_stop()
        if not self._ensure_menu_open():
            return False
        frame = self._frame()
        if frame is None:
            return False
        (x, y) = self._find(frame, TEMPLATE_REQUEST)
        if not x:
            logger.info('Clan assist: no Request troops button — castle full or a request is already open')
            return False
        if self.options.dry_run:
            logger.info('Clan assist (dry run): would request reinforcements at (%s, %s)', x, y)
            return False
        if self._price_veto(frame, x, y):
            return False
        logger.info('Clan assist: tapping Request troops at (%s, %s)', x, y)
        self.input.click(x, y, pause = 0.6)
        ((sx, sy), frame) = self._wait_for(TEMPLATE_SEND)
        if not sx:
            self._note_failure('request panel never showed a Send button', frame)
            self._escape_subpanel()
            return False
        if self._price_veto(frame, sx, sy):
            self._escape_subpanel()
            return False
        logger.info('Clan assist: Send at (%s, %s) — requesting reinforcements', sx, sy)
        self.input.click(sx, sy, pause = 0.6)
        # Verified by the panel going away: while it is up, its Send button is still
        # there to be found.
        for _ in range(_PANEL_POLLS):
            frame = self._frame()
            if frame is None:
                break
            (still, _) = self._find(frame, TEMPLATE_SEND)
            if not still:
                logger.info('Clan assist: reinforcements requested')
                return True
            self._wait(0.5)
        self._note_failure('request panel stayed open after Send', frame)
        self._escape_subpanel()
        return False

    # --------------------------------------------------------------------- pass

    def run_pass(self, donate = True, request = True):
        '''One clan-menu visit: open, badge, donate, request, close.'''
        if self.disabled:
            return ClanPassResult(False, 0, False, 'disabled for this session')
        missing = missing_templates(donate = donate, request = request,
                                    donate_troop = self.options.donate_troop,
                                    have_chat_point = self.options.chat_point is not None)
        if missing:
            note = f'''missing template(s): {', '.join(missing)}'''
            logger.warning('Clan assist: %s — capture them in Settings → Clan assist', note)
            self._status(f'''Clan assist: {note} — capture them in Settings.''')
            return ClanPassResult(False, 0, False, note)
        if not self.open_menu():
            return ClanPassResult(False, 0, False, 'clan menu did not open')
        donated = 0
        requested = False
        try:
            if donate:
                donated = self.donate_pass()
            if request:
                requested = self.request_pass()
        finally:
            self.close_menu()
        if self.options.dry_run:
            note = 'dry run — clan menu walked, nothing clicked'
        else:
            parts = []
            if donate:
                parts.append(f'''donated {donated}''')
            if request:
                parts.append('requested' if requested else 'no request sent')
            note = ', '.join(parts) if parts else 'nothing to do'
        logger.info('Clan assist: %s', note)
        return ClanPassResult(True, donated, requested, note)
