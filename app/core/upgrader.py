'''Auto-maxer orchestration: scan the builder popup, decide an upgrade, and start it.

:class:`UpgradeAdvisor` drives the bot's own services (window/input/vision/config) the
same way the wall flow does: open the builder popup from the home screen, wheel-scroll
through the whole list while parsing each position (:mod:`app.core.upgrade_menu`),
merge the rows, close the popup, and run the policy (:mod:`app.core.upgrade_policy`).

Execution (``maxer``/``rusher`` modes): re-open the popup, locate the picked row by
fuzzy label + exact cost (two consecutive frames must agree on its y before the click —
the list keeps easing after a wheel nudge and stale coordinates click whatever slid
underneath), then hunt the "Upgrade" button by OCR. The word-hunt is deliberately
layout-agnostic: clicking a row may select the building (bottom-bar Upgrade button),
open a hero/building screen, or jump straight to the confirm dialog — all of them
surface an "Upgrade" word in the bottom band, and clicking it walks the chain (at most
two hops) to the green confirm. Every uncertain frame is dumped to
``%LOCALAPPDATA%\\BasePilot\\debug\\upgexec_*.jpg`` and every abort escapes via
exit/empty-tap — never a blind confirm (gem guard: a red cost zone under the word
vetoes the click).

Success is verified by the builder chip: free builders must drop below the pre-click
reading. ``dry`` mode still only logs its decision.
'''
import difflib
import time
from dataclasses import dataclass, replace
from typing import List, Optional, Tuple

from app.config import ASPECT_16_10, ASPECT_16_9
from app.core.upgrade_menu import (
    SECTION_IN_PROGRESS,
    UpgradeRow,
    merge_rows,
    parse_builder_chip,
    parse_builder_menu,
)
from app.core.upgrade_policy import MODE_MAXER, MODE_RUSHER, UpgradePolicy, choose_upgrade
from app.core.village_state import read_hud_triplet_stable
from app.services.vision import VisionService
from app.utils.logger import setup_logger

logger = setup_logger('Upgrader')

MODE_OFF = 'off'
MODE_DRY = 'dry'
AUTO_UPGRADE_MODES = (MODE_OFF, MODE_DRY, MODE_MAXER, MODE_RUSHER)
LIVE_UPGRADE_MODES = (MODE_MAXER, MODE_RUSHER)

_BUILDER_TEMPLATES = ('builder.png', 'gbuilder.png')
# Wheel target inside the popup body — keep in sync with bot.py _WALL_MENU_SCROLL_BASELINE.
_SCROLL_BASELINE = {
    ASPECT_16_9: (1305, 605),
    ASPECT_16_10: (1305, 672) }
_MAX_SCROLL_STEPS = 12
_WHEEL_CLICKS = 2
_STALE_STEPS_AT_LIST_END = 2  # consecutive scrolls adding no new rows = bottom reached
# The GAME remembers the popup's scroll position across close/reopen (live repro: a
# scan after a mid-list close parsed 16 headerless rows — every section '', policy
# discarded all of them and the top 'Suggested' rows were never seen). Enough up-clicks
# to return from the very bottom of the longest list.
_RESET_TO_TOP_WHEEL_CLICKS = _MAX_SCROLL_STEPS * _WHEEL_CLICKS + 4

_EXEC_ROW_STABLE_TOL = 30  # ref px: max y drift between consecutive frames before a row click is trusted
_EXEC_CONFIRM_MISSES = 4  # in-place re-parses allowed before a sighted row is scrolled past (parses flicker)
_EXEC_LABEL_FUZZY_MIN = 0.85  # exec-pass row label vs the scan-pass pick (compacted, difflib)
_EXEC_UPGRADE_WORD_POLLS = 8  # post-click polls for an "Upgrade" word (camera pans >1s before UI renders)
_EXEC_MAX_UPGRADE_CLICKS = 2  # bottom-bar Upgrade → confirm-dialog Upgrade; never a third blind click
_EXEC_RED_VETO_FRACTION = 0.05  # red cost under/next to the Upgrade word = unaffordable → abort, no gem risk
_EXEC_COOLDOWN_SECONDS = 1800  # a row that failed to execute is not retried for this long
_EXEC_VERIFY_POLLS = 5  # builder-chip re-reads to confirm free count dropped
# The Upgrade word lives in the bottom band: bottom-bar button labels (~y 0.88+) and
# the confirm dialog's green button (~y 0.75-0.88). The fence must sit ABOVE the
# builder popup's bottom edge (~y 0.63 — its "Suggested/Other upgrades:" section
# headers fuzzy-match "upgrade"!) and the confirm dialog's title, so neither is ever
# clickable by the hunt.
_EXEC_UPGRADE_BAND_Y_FRAC = 0.66


@dataclass(frozen = True)
class ScanResult:
    rows: List[UpgradeRow]
    chip: Optional[Tuple[int, int]]  # (free, total) builders, or None
    hud: Optional[Tuple[int, int, int]]  # (gold, elixir, dark), or None


@dataclass(frozen = True)
class PassResult:
    '''One advisor pass, for the bot loop and the UI status panel.'''
    scan: Optional[ScanResult]
    pick: Optional[UpgradeRow]
    executed: bool  # an upgrade was started AND verified via the builder chip
    note: str  # one human-readable line describing the outcome
    # True when this pass was the session's first scan: corroboration had nothing to
    # compare against, so pick=None means "collecting", NOT "nothing startable" —
    # callers looping on passes must not give up on it (live repro: the spend burst
    # broke after the collect pass and never reached the pass that would have picked).
    collecting: bool = False


def _row_summary(rows):
    parts = []
    for r in rows:
        if r.cost is not None:
            tag = f'''{r.cost}{(r.resource or '?')[0]}'''
        elif r.affordable is False:
            tag = 'RED'
        else:
            tag = '?'
        parts.append(f'''{r.label}[{(r.section or '-')[:4]}:{tag}]''')
    return ' | '.join(parts)


def _compact(text):
    return ''.join(ch for ch in (text or '').lower() if ch.isalnum())


def _labels_match(a, b, min_ratio = _EXEC_LABEL_FUZZY_MIN):
    return difflib.SequenceMatcher(None, _compact(a), _compact(b)).ratio() >= min_ratio


def _dump_debug_frame(frame, prefix):
    '''Save a frame to the debug dir; returns the path or None. Never raises.'''
    try:
        import cv2 as _cv2
        from app.utils.common import get_user_app_data_dir
        dbg = get_user_app_data_dir() / 'debug'
        dbg.mkdir(parents = True, exist_ok = True)
        path = dbg / f'''{prefix}_{int(time.time())}.jpg'''
        _cv2.imwrite(str(path), frame, [_cv2.IMWRITE_JPEG_QUALITY, 88])
        logger.info('Upgrade exec: frame saved to %s', path)
        return path
    except Exception:
        logger.debug('Upgrade exec: debug frame dump failed', exc_info = True)
        return None


class UpgradeAdvisor:
    '''Scans the builder popup, decides which upgrade to start, and (in live modes)
    starts it. Keep ONE instance per bot session — it remembers per-row execution
    cooldowns so a row that fails to execute is not hammered every pass.'''

    def __init__(self, window, input_service, vision, config, stop_event):
        self.window = window
        self.input = input_service
        self.vision = vision
        self.config = config
        self.stop_event = stop_event
        self._exec_cooldowns = {}  # compacted label -> monotonic time of last failed execution
        self._prev_scan_rows = None  # last scan's rows, for cross-scan cost corroboration
        self._last_pass_collecting = False

    def _frame(self):
        frame = self.window.screenshot()
        if frame is None or frame.size == 0:
            return None
        self.config.set_target_size_from_frame(frame)
        return frame

    def _scroll_point(self):
        ref = _SCROLL_BASELINE.get(self.config.aspect_key)
        if ref is None:
            ref = _SCROLL_BASELINE[ASPECT_16_10]
        (x, y) = self.config.scale_point([
            ref[0],
            ref[1]])
        return (int(x), int(y))

    def _close_popup(self):
        try:
            self.input.click(pause = 0.3, *self.config.get_point('empty'))
        except Exception:
            logger.debug('Upgrade scan: empty-point tap failed', exc_info = True)

    def _escape_ui(self, reason):
        '''Back out of whatever screen the executor is on: exit X if visible, else
        empty-ground taps. Never clicks okay.png — an unknown dialog's Okay can
        confirm something.'''
        logger.info('Upgrade exec: escaping UI (%s)', reason)
        for _ in range(2):
            frame = self._frame()
            if frame is None:
                return
            (ex, ey) = self.vision.find_template(frame, 'exit.png')
            if ex:
                self.input.click(ex, ey, pause = 0.3)
            else:
                self._close_popup()
            if self.stop_event.wait(0.3):
                return

    def _open_builder_popup(self):
        '''From the home screen, click the builder portrait. Returns True when clicked.'''
        frame = self._frame()
        if frame is None:
            return False
        home_roi = VisionService.bottom_half_region(frame)
        (ax, _ay) = self.vision.find_template(frame, 'attack.png', region = home_roi)
        if not ax:
            logger.info('Upgrade advisor: not on the home screen (Attack missing) — skipping')
            return False
        top_roi = VisionService.top_half_region(frame)
        for name in _BUILDER_TEMPLATES:
            (bx, by) = self.vision.find_template(frame, name, region = top_roi)
            if bx:
                self.input.click(bx, by, pause = 0.5)
                if self.stop_event.wait(0.5):
                    return False
                # The popup reopens at its LAST scroll position — wheel back to the
                # top so every parse starts at the section headers.
                self.input.scroll(*self._scroll_point(), _RESET_TO_TOP_WHEEL_CLICKS, upward = True)
                return not self.stop_event.wait(0.6)
        logger.info('Upgrade advisor: builder portrait not found — skipping')
        return False

    def scan(self):
        '''From the home screen: open the builder popup, parse every scroll position,
        close it. Returns :class:`ScanResult` or ``None`` when preconditions fail.'''
        frame = self._frame()
        if frame is None:
            return None
        home_roi = VisionService.bottom_half_region(frame)
        (ax, _ay) = self.vision.find_template(frame, 'attack.png', region = home_roi)
        if not ax:
            # Not home: chip/HUD reads would be junk (and dumped chipfail frames of
            # battle screens are noise) — bail before reading anything.
            logger.info('Upgrade scan: not on the home screen (Attack missing) — skipping')
            return None
        # Stable (consecutive-frame agreement) HUD read: a corrupted single read here
        # feeds can_pay garbage (live repro: gold "66M" → policy skipped real rows).
        hud = read_hud_triplet_stable(self._frame, self.stop_event.wait)
        chip = parse_builder_chip(frame)
        if chip is None:
            # Builder count gates live execution — dump the frame so misses are diagnosable.
            _dump_debug_frame(frame, 'chipfail')
        if not self._open_builder_popup():
            return None
        rows = []
        section = ''
        stale = 0
        for _step in range(_MAX_SCROLL_STEPS):
            if self.stop_event.is_set():
                break
            frame = self._frame()
            if frame is None:
                break
            parsed = parse_builder_menu(frame, initial_section = section)
            if parsed:
                section = parsed[-1].section
            before = len(rows)
            rows = merge_rows(rows, parsed)
            stale = stale + 1 if len(rows) == before else 0
            if stale >= _STALE_STEPS_AT_LIST_END:
                break
            self.input.scroll(*self._scroll_point(), _WHEEL_CLICKS)
            if self.stop_event.wait(0.45):
                break
        self._close_popup()
        return ScanResult(rows = rows, chip = chip, hud = hud)

    def _cooldown_active(self, label):
        key_time = None
        for key, when in self._exec_cooldowns.items():
            if _labels_match(label, key):
                key_time = when
                break
        return key_time is not None and time.monotonic() - key_time < _EXEC_COOLDOWN_SECONDS

    def _rows_without_cooldowns(self, rows):
        kept = []
        for row in rows:
            if row.section != SECTION_IN_PROGRESS and self._cooldown_active(row.label):
                logger.info('Upgrade advisor: %r is on execution cooldown — skipping this pass', row.label)
                continue
            kept.append(row)
        return kept

    def _corroborate_rows(self, rows):
        '''Cross-scan corroboration: a costed row is pickable only when the PREVIOUS
        scan also saw it at the exact same cost. One-off OCR bargains (dropped leading
        digits: 'XBow 350000' for a 9.5M row) never repeat a value, so they die here
        instead of wasting an execution attempt; and a row whose resource icon misread
        this scan adopts the previous scan's resolved resource (the dark-elixir read
        is intermittent — this is what keeps Barbarian King pickable).'''
        prev = self._prev_scan_rows
        self._prev_scan_rows = rows
        self._last_pass_collecting = prev is None
        if prev is None:
            logger.info('Upgrade advisor: first scan this session — collecting, no pick yet')
            return []
        out = []
        for row in rows:
            if row.cost is None:
                out.append(row)
                continue
            match = None
            for p in prev:
                if p.cost == row.cost and _labels_match(row.label, p.label, 0.7):
                    match = p
                    break
            if match is None:
                logger.debug('Upgrade advisor: %r (%s) not corroborated by the previous scan — holding', row.label, row.cost)
                continue
            if row.resource is None and match.resource is not None:
                row = replace(row, resource = match.resource)
            out.append(row)
        return out

    def run_pass(self, mode, reserve_builders = 1, order = None):
        '''One scan + decision (+ execution in live modes). Returns :class:`PassResult`.'''
        if mode not in AUTO_UPGRADE_MODES or mode == MODE_OFF:
            return PassResult(scan = None, pick = None, executed = False, note = 'auto-upgrade off')
        started = time.monotonic()
        scan = self.scan()
        if scan is None:
            return PassResult(scan = None, pick = None, executed = False, note = 'scan skipped (not on home screen)')
        elapsed = time.monotonic() - started
        free = scan.chip[0] if scan.chip else None
        logger.info('Upgrade scan (%.1fs): %d rows, builders=%s, HUD=%s', elapsed, len(scan.rows), scan.chip, scan.hud)
        if scan.rows:
            logger.info('Upgrade scan rows: %s', _row_summary(scan.rows))
        if scan.hud is None:
            logger.info('Upgrade advisor: HUD unreadable this pass — no decision')
            return PassResult(scan = scan, pick = None, executed = False, note = 'HUD unreadable — no decision')
        policy_kwargs = {'order': order} if order else {}
        policy = UpgradePolicy(mode = MODE_RUSHER if mode == MODE_RUSHER else MODE_MAXER,
                               reserve_builders = max(0, int(reserve_builders)), **policy_kwargs)
        rows = self._corroborate_rows(self._rows_without_cooldowns(scan.rows))
        pick = choose_upgrade(rows, scan.hud, free, policy)
        if pick is None:
            if mode == MODE_DRY:
                hypo = choose_upgrade(rows, scan.hud, policy.reserve_builders + 1, policy)
                if hypo is not None:
                    logger.info('Upgrade advisor: no free builder above reserve (builders=%s); hypothetical pick: %r (cost %s %s)', scan.chip, hypo.label, hypo.cost, hypo.resource)
                else:
                    logger.info('Upgrade advisor: nothing affordable to start (even hypothetically)')
            collecting = getattr(self, '_last_pass_collecting', False)
            if collecting:
                note = 'first scan — collecting for corroboration'
            else:
                note = f'''nothing to start (builders {scan.chip[0]}/{scan.chip[1]})''' if scan.chip else 'nothing to start'
            return PassResult(scan = scan, pick = None, executed = False, note = note, collecting = collecting)
        if mode == MODE_DRY:
            logger.info('Upgrade advisor (dry): WOULD START %r (cost %s %s)', pick.label, pick.cost, pick.resource)
            return PassResult(scan = scan, pick = pick, executed = False, note = f'''dry run: would start {pick.label}''')
        executed = self._execute(pick, scan)
        note = (f'''started {pick.label} ({pick.cost} {pick.resource})''' if executed
                else f'''could not start {pick.label} — see debug dumps''')
        return PassResult(scan = scan, pick = pick, executed = executed, note = note)

    # ---- execution ----

    def _row_matches_pick(self, row, pick):
        # Unknown section ('') is fine here: mid-list popup views have no headers, so
        # a single missed header read used to zero the section for the WHOLE descent
        # and reject every row (live repro: Eagle Artillery visible with its exact
        # cost, 'not re-found'). In-progress rows can never match anyway — they carry
        # remaining-time text, never a parsed white cost, and cost+affordable are
        # required below.
        if row.section == SECTION_IN_PROGRESS:
            return False
        if row.affordable is not True or row.cost is None:
            return False
        if pick.cost is not None and row.cost != pick.cost:
            return False
        if pick.resource and row.resource and pick.resource != row.resource:
            return False
        # OCR respells labels between scans ('HotCandle'→'HotCaridie' 0.74,
        # 'Bombx'→'BornbxB' 0.67 — both live). Exact cost+resource agreement among
        # ~50 rows is strong identity evidence on its own, so demand little of the
        # label then; even a same-cost sibling row would be an equally-cheapest,
        # equally-valid pick to start.
        exact_numbers = (pick.cost is not None and row.cost == pick.cost
                         and pick.resource is not None and row.resource == pick.resource)
        return _labels_match(row.label, pick.label, 0.6 if exact_numbers else _EXEC_LABEL_FUZZY_MIN)

    def _find_pick_row_once(self, pick, section, near_y = None):
        '''Parse the current popup frame; returns (point, section) — point None on miss.
        ``section`` carries the running header across scroll positions. With ``near_y``
        (a prior sighting's y), a row with the pick's exact cost at ~the same height
        also counts: at confirmation time identity is position + cost — the label can
        respell arbitrarily between frames (live repro: 'Bombx' confirmed as 'BornbxB').'''
        frame = self._frame()
        if frame is None:
            return (None, section)
        rows = parse_builder_menu(frame, initial_section = section)
        if rows:
            section = rows[-1].section
        for row in rows:
            if self._row_matches_pick(row, pick):
                return (row.center, section)
        if near_y is not None:
            tol = self.config.scale_scalar(_EXEC_ROW_STABLE_TOL)
            for row in rows:
                if (row.section != SECTION_IN_PROGRESS and row.cost is not None
                        and row.cost == pick.cost and abs(row.line_y - near_y) <= tol):
                    return (row.center, section)
        return (None, section)

    def _descend_for_pick(self, pick):
        '''Walk the popup from its current position looking for ``pick``. Returns the
        row's click point, or None.

        Two consecutive frames must agree on the row's y before it is trusted — the list
        keeps easing after a wheel nudge and a click on stale coordinates lands on
        whatever slid underneath. The re-parse allowance matters: a sighting that flickers
        out for a couple of frames is otherwise scrolled past, and the descent only
        travels downwards, so the row is then gone for the rest of the pass.
        '''
        candidate = None
        confirm_misses = 0
        section = ''
        scrolls = 0
        for _ in range(_MAX_SCROLL_STEPS * 3):
            if self.stop_event.is_set():
                return None
            (found, section) = self._find_pick_row_once(
                pick, section, near_y = candidate[1] if candidate else None)
            if found:
                if candidate and abs(found[1] - candidate[1]) <= self.config.scale_scalar(_EXEC_ROW_STABLE_TOL):
                    return found
                candidate = found
                confirm_misses = 0
                if self.stop_event.wait(0.25):
                    return None
                continue
            if candidate is not None and confirm_misses < _EXEC_CONFIRM_MISSES:
                # One flickered parse must not lose a live candidate: the list is
                # static here (no scroll since the sighting) — re-parse in place
                # instead of scrolling the row away.
                confirm_misses += 1
                if self.stop_event.wait(0.3):
                    return None
                continue
            if candidate is not None:
                # The parse lands about one frame in six on a small capture (measured
                # live: sighted at parse 6, then five straight misses at the same
                # position), so demanding a second agreeing sighting can never be met and
                # the row gets scrolled past forever. Nothing has scrolled since the
                # sighting, so those coordinates are still current — the agreement rule
                # guards against post-scroll easing, not against a list sitting still.
                # A wrong click here is still caught downstream by the Upgrade-word hunt,
                # the red-cost veto and the builder-chip verification.
                logger.info('Upgrade exec: row %r sighted at %s but re-parses keep missing — trusting the sighting (nothing scrolled since)', pick.label, candidate)
                return candidate
            candidate = None
            confirm_misses = 0
            scrolls += 1
            if scrolls >= _MAX_SCROLL_STEPS:
                return None
            self.input.scroll(*self._scroll_point(), _WHEEL_CLICKS)
            if self.stop_event.wait(0.45):
                return None
        return None

    def _execute(self, pick, scan):
        '''Click the picked row and walk the Upgrade chain. True only when the builder
        chip verifies the start (free count dropped below the scan-time reading).'''
        free_before = scan.chip[0] if scan.chip else None
        if free_before is None or free_before <= 0:
            return False
        if not self._open_builder_popup():
            return False
        row_pt = self._descend_for_pick(pick)
        if row_pt is None:
            # A flickery descent is not proof the row is gone. Parses drop out at random
            # positions (live: 2 of 8 positions parsed zero rows on a real client), and a
            # sighting lost to consecutive bad frames gets scrolled past for good, since
            # the descent only travels downwards. Walk it once more from the top before
            # writing the row off for half an hour.
            logger.info('Upgrade exec: row %r not found on the first descent — resetting to the top and retrying', pick.label)
            self.input.scroll(*self._scroll_point(), _RESET_TO_TOP_WHEEL_CLICKS, upward = True)
            if self.stop_event.wait(0.6):
                return False
            row_pt = self._descend_for_pick(pick)
        if not row_pt:
            # The two-scan re-find is the phantom-row filter: OCR junk (village pixels
            # bleeding through the translucent popup) parses as cheap fake rows, and
            # cheapest-first loves them — but junk never reproduces with the same
            # label+cost. Cool the label down so the NEXT pass falls through to a real
            # row instead of re-picking the same phantom forever (live repro:
            # 'ScatbershotxZ (cost 100000)' thrash-looped every idle wake).
            logger.info('Upgrade exec: row %r not re-found in the popup — cooling it down and closing', pick.label)
            self._exec_cooldowns[_compact(pick.label)] = time.monotonic()
            self._close_popup()
            return False
        # Easing guard: both agreement frames can catch the list mid-glide and still
        # agree (live repro: click meant for Eagle Artillery landed on the Elixir
        # Storage row above it). Let the ease finish, then click FRESH coordinates.
        if self.stop_event.wait(0.4):
            return False
        (fresh, _section) = self._find_pick_row_once(pick, '', near_y = row_pt[1])
        if fresh is not None:
            row_pt = fresh
        else:
            # A missed re-parse is not the row moving — at this capture size most parses
            # miss. The descent has not scrolled since it sighted the row, so its point
            # is still the best evidence available; giving up here is what benched every
            # candidate in turn until nothing was left to start.
            logger.info('Upgrade exec: re-parse missed right before the click — using the sighted point %s', row_pt)
        logger.info('Upgrade exec: clicking row %r at %s', pick.label, row_pt)
        self.input.click(pause = 0.6, *row_pt)
        started = self._walk_upgrade_chain(pick, free_before = free_before)
        if not started:
            self._exec_cooldowns[_compact(pick.label)] = time.monotonic()
            self._escape_ui('upgrade chain did not complete')
            return False
        # The chip must be read on the HOME screen — while a confirm dialog is still
        # up, its digits leak into the chip ROI (live repro: read "8/8"). Capture the
        # still-open screen FIRST (it is the diagnostic for a chain that quietly
        # under-clicked), then close everything, then verify.
        verify_frame = self._frame()
        self._close_popup()
        if self.stop_event.wait(0.8):
            return False
        if self._verify_builder_drop(free_before):
            logger.info('Upgrade exec: STARTED %r (cost %s %s) — builder chip dropped below %d free', pick.label, pick.cost, pick.resource, free_before)
            self._exec_cooldowns.pop(_compact(pick.label), None)
            return True
        logger.warning('Upgrade exec: clicked the Upgrade chain for %r but the builder chip did not drop — cooling this row down', pick.label)
        if verify_frame is not None:
            _dump_debug_frame(verify_frame, 'upgexec_verifyfail')
        self._exec_cooldowns[_compact(pick.label)] = time.monotonic()
        self._escape_ui('no builder-chip confirmation')
        return False

    @staticmethod
    def _word_is(text, target, min_ratio = 0.75):
        return difflib.SequenceMatcher(None, _compact(text), target).ratio() >= min_ratio

    def _find_upgrade_word(self, frame):
        '''Bottom-band OCR hunt for the button that commits the upgrade. Hero (and
        crafting) dialogs label it "Confirm"; building flows label it "Upgrade" — hunt
        Confirm first. A bare "Upgrade" with a "time" neighbor on the same line is the
        inert "Upgrade time" caption (live repro: clicked it twice on the Barbarian
        King dialog while the real button said Confirm). Returns (x, y) or None.'''
        (fh, fw) = frame.shape[:2]
        y0 = int(fh * _EXEC_UPGRADE_BAND_Y_FRAC)
        region = (0, y0, fw, fh - y0)
        words = []
        # Binarization ladder — different button skins need different floors, AND the
        # live raw frames run brighter than the JPEG debug dumps the floors were first
        # tuned on (live repro: floor-200 read the bottom-bar 'Upgrade' on the saved
        # dump but missed it on the raw frames of the very same screen). 228 isolates
        # raw whites, 200 catches JPEG-soft whites/green-button text, default reads
        # dark-bar labels, and the upscale pass rescues small glyphs. Words pool
        # across passes so the 'Upgrade time' veto sees every reading.
        for kwargs in ({'brightness_floor': 228}, {'brightness_floor': 200}, {}, {'roi_upscale': 2}):
            try:
                words += self.vision.find_words_ocr(
                    frame, region = region, white_text = True,
                    preprocess = True, min_confidence = 25, **kwargs)
            except Exception:
                logger.debug('Upgrade exec: word OCR failed (%s)', kwargs, exc_info = True)
        if not words:
            return None

        def has_time_neighbor(w):
            for other in words:
                if other is w:
                    continue
                same_line = abs((other.top + other.height / 2) - (w.top + w.height / 2)) <= max(w.height, other.height) * 0.7
                gap = other.left - (w.left + w.width)
                if same_line and 0 <= gap <= 2.5 * max(8, w.height) and self._word_is(other.text, 'time'):
                    return True
            return False

        confirms = [w for w in words if self._word_is(w.text, 'confirm')]
        upgrades = [w for w in words if self._word_is(w.text, 'upgrade') and not has_time_neighbor(w)]
        pool = confirms or upgrades
        if not pool:
            return None
        # Bottom-most match: bar labels and the confirm button both sit low; any stray
        # match higher up (dialog body text) loses.
        word = max(pool, key = (lambda w: w.top))
        (wx, wy) = (word.left + word.width // 2, word.top + word.height // 2)
        # Gem guard: an unaffordable confirm button renders its cost in red right under
        # the word. Sample the patch around/below it — any red vetoes the click.
        px0 = max(0, word.left - word.width)
        px1 = min(fw, word.left + 2 * word.width)
        py0 = max(0, word.top - word.height)
        py1 = min(fh, word.top + 3 * word.height)
        redness = VisionService.red_hue_fraction(frame[py0:py1, px0:px1])
        if redness >= _EXEC_RED_VETO_FRACTION:
            logger.warning('Upgrade exec: Upgrade word found but cost zone reads red (%.2f) — vetoing the click', redness)
            return None
        return (wx, wy)

    def _selection_matches_pick(self, frame, pick):
        '''After the row click: does the screen actually show the picked target? The
        selection bottom-bar titles it ("Eagle Artillery (Level 5)") and upgrade
        dialogs put it in the header ("Upgrade Barbarian King to Level 80?"). A row
        click on a still-easing list lands on the NEIGHBOR row (live repro: picked
        Eagle Artillery 13M, bought Elixir Storage 4M — user had to cancel at a 50%
        refund loss), so the name is now a hard gate on every commit click. Returns
        True / False / None (no name text found — treated as NOT matching).'''
        (fh, fw) = frame.shape[:2]
        words = []
        for (y0f, y1f) in ((0.60, 0.80), (0.02, 0.12)):
            region = (0, int(fh * y0f), fw, int(fh * (y1f - y0f)))
            for kwargs in ({'brightness_floor': 200}, {}):
                try:
                    words += self.vision.find_words_ocr(
                        frame, region = region, white_text = True,
                        preprocess = True, min_confidence = 20, **kwargs)
                except Exception:
                    logger.debug('Upgrade exec: name-gate OCR failed', exc_info = True)
        if not words:
            return None
        target = _compact(pick.label)
        # Slide a window of 1-3 consecutive words per text line over the target name
        # ("Eagle" + "Artillery" must merge before comparing).
        lines = VisionService.cluster_ocr_boxes_by_y(words, 12)
        best = 0.0
        for line in lines:
            line = sorted(line, key = (lambda w: w.left))
            for i in range(len(line)):
                joined = ''
                for j in range(i, min(i + 3, len(line))):
                    joined += _compact(line[j].text)
                    if len(joined) < 3:
                        continue
                    ratio = difflib.SequenceMatcher(None, joined, target).ratio()
                    best = max(best, ratio)
        logger.info('Upgrade exec: name gate for %r — best screen match %.2f', pick.label, best)
        return best >= 0.6

    def _find_green_confirm_blob(self, frame):
        '''Positional/color fallback for the commit button: dialogs place the green
        Confirm at a stable spot (x~0.70, y~0.83 on both hero and building screens),
        but its white-on-bright-green text reads on JPEG dumps yet intermittently NOT
        on live raw frames (live repro: Eagle confirm sat unclicked while offline the
        same frame OCR'd fine). Look for a large green blob in the dialog-button band;
        x is fenced to exclude the home Attack!/Shop buttons and the crafting screen's
        component buttons sit far above the y band. Returns (x, y) or None.'''
        import cv2 as _cv2
        import numpy as _np
        (fh, fw) = frame.shape[:2]
        (y0, y1) = (int(fh * 0.78), int(fh * 0.93))
        # x fenced to the Confirm's measured spot (x-frac 0.70 on both hero and
        # building dialogs): 0.78 keeps out the crafting screen's "Defense Overview"
        # green button at 0.80, as well as home-screen Attack!/Shop.
        (x0, x1) = (int(fw * 0.60), int(fw * 0.78))
        band = frame[y0:y1, x0:x1]
        if band.size == 0:
            return None
        hsv = _cv2.cvtColor(band, _cv2.COLOR_BGR2HSV)
        mask = _cv2.inRange(hsv, (40, 80, 120), (80, 255, 255))
        contours, _ = _cv2.findContours(mask, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
        best = None
        min_area = (fw * fh) * 0.002  # the Confirm button is big (~0.5% of the frame)
        for c in contours:
            area = _cv2.contourArea(c)
            if area < min_area:
                continue
            if best is None or area > best[0]:
                (bx, by, bw, bh) = _cv2.boundingRect(c)
                best = (area, x0 + bx + bw // 2, y0 + by + bh // 2)
        if best is None:
            return None
        (_area, cx, cy) = best
        patch = frame[max(0, cy - 30):cy + 30, max(0, cx - 90):cx + 90]
        if VisionService.red_hue_fraction(patch) >= _EXEC_RED_VETO_FRACTION:
            logger.warning('Upgrade exec: green-blob confirm candidate reads red — vetoing')
            return None
        return (cx, cy)

    def _walk_upgrade_chain(self, pick, free_before = None):
        '''After the row click: hunt and click "Upgrade" up to twice (bottom bar →
        confirm dialog). True when at least one Upgrade was clicked and the chain went
        quiet (no further Upgrade word — the confirm dialog closed itself).'''
        clicks = 0
        quiet_polls = 0
        dumped = False
        name_ok = False
        name_strikes = 0
        for _ in range(_EXEC_UPGRADE_WORD_POLLS + _EXEC_MAX_UPGRADE_CLICKS * 4):
            if self.stop_event.is_set():
                return False
            frame = self._frame()
            if frame is None:
                return False
            if not dumped:
                # First post-click frame is the unknown of this whole flow — always keep
                # one on disk until the executor has a live track record.
                _dump_debug_frame(frame, 'upgexec_row')
                dumped = True
            if clicks == 0 and not name_ok:
                # HARD GATE before the first commit click: the screen must name the
                # picked target. A row click that landed on a neighbor row selects
                # the wrong building — money must never move for it.
                verdict = self._selection_matches_pick(frame, pick)
                if verdict is not True:
                    name_strikes += 1
                    if name_strikes >= 3:
                        logger.warning('Upgrade exec: screen does not name %r — wrong selection, aborting without buying', pick.label)
                        _dump_debug_frame(frame, 'upgexec_wrongsel')
                        return False
                    if self.stop_event.wait(0.6):
                        return False
                    continue
                name_ok = True
            pt = self._find_upgrade_word(frame)
            if pt is None and clicks >= 1:
                # Mid-chain (bottom-bar Upgrade already clicked): the confirm dialog
                # is expected — fall back to the color/position blob when the word
                # OCR misses on live frames.
                pt = self._find_green_confirm_blob(frame)
                if pt is not None:
                    logger.info('Upgrade exec: confirm found via green-blob fallback at %s', pt)
            if pt is None:
                if clicks == 0:
                    if self.stop_event.wait(0.5):
                        return False
                    continue
                quiet_polls += 1
                # 3 quiet polls (~2s): the confirm dialog can take >1s to render after
                # the bottom-bar click — declaring "chain complete" too early skips it
                # and the builder-chip verify would then fail the whole pass.
                if quiet_polls >= 3:
                    return True  # clicked Upgrade and the UI went quiet — chain complete
                if self.stop_event.wait(0.5):
                    return False
                continue
            if clicks >= _EXEC_MAX_UPGRADE_CLICKS:
                # A visible "Upgrade" word is not proof the upgrade did not start: the
                # building's own bar keeps one on screen afterwards. The builder chip is
                # the ground truth this module already trusts, so ask it before writing
                # the attempt off — measured live: the chain "aborted" here on a Bomb
                # that had in fact started, and the next scan read 1/5 builders free
                # instead of 2/5, with the row then benched for half an hour for nothing.
                frame_now = self._frame()
                chip = parse_builder_chip(frame_now) if frame_now is not None else None
                if chip is not None and free_before is not None and chip[0] < free_before:
                    logger.info('Upgrade exec: Upgrade word still visible, but builders dropped %d -> %d — the upgrade started', free_before, chip[0])
                    return True
                logger.warning('Upgrade exec: Upgrade word still visible after %d clicks — aborting', clicks)
                _dump_debug_frame(frame, 'upgexec_fail')
                return False
            logger.info('Upgrade exec: clicking Upgrade/Confirm at %s (click %d)', pt, clicks + 1)
            self.input.click(pause = 0.7, *pt)
            clicks += 1
            quiet_polls = 0
            # Screen transitions take >1s — re-hunting too fast re-finds the same
            # button mid-transition and burns the click budget (live repro: two
            # clicks 1s apart on one dialog).
            if self.stop_event.wait(1.2):
                return False
        if clicks == 0:
            frame = self._frame()
            if frame is not None:
                _dump_debug_frame(frame, 'upgexec_fail')
            logger.info('Upgrade exec: no Upgrade button appeared after the row click')
            return False
        return True

    def _verify_builder_drop(self, free_before):
        '''Poll the builder chip until it reads below ``free_before``.'''
        for _ in range(_EXEC_VERIFY_POLLS):
            if self.stop_event.wait(0.6):
                return False
            frame = self._frame()
            if frame is None:
                continue
            chip = parse_builder_chip(frame)
            if chip is not None:
                if chip[0] < free_before:
                    return True
                logger.info('Upgrade exec verify: builder chip reads %s (was %d free)', chip, free_before)
        return False
