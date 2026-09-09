'''Home-village state snapshot for the auto-maxer's idle gate and the UI status panel.

Reads everything the home screen shows about "is there anything productive to do":
free builders (top-HUD builder chip), laboratory research slots (flask chip left of
it), and full gold/elixir storages (the hero-bar full-storage icons). All fields are
Optional — a chip that is absent or unreadable yields ``None``, never a guess, so the
caller can be conservative. TH-agnostic by construction: villages without a lab (or
with fewer builders) simply read as ``None`` / smaller totals.

The Pet House has NO home-screen indicator (verified on live TH15 frames: a busy pet
upgrade appears neither as a HUD chip nor as a builder-popup in-progress row), so pet
state is not tracked here — pets remain a manual job and never block idling.
'''
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from app.core.upgrade_menu import parse_builder_chip, parse_lab_chip
from app.services.vision import VisionService
from app.utils.logger import setup_logger

logger = setup_logger('VillageState')
# No village holds this much. A bigger number is the HUD reader gaining a digit: live,
# elixir read as 49,796,363 against a real 9,796,363 in the second after a purchase, and
# as 411,251,572 and 116,737,862 in the run log. The corruption is systematic while the
# resource bar animates, so two consecutive reads can agree on it and pass the stability
# check above. Set above the largest storage set in the game today (a maxed Town Hall 17
# holds a little under 25M of either) and well below a spurious extra digit. Rejecting a
# real read by mistake costs one cycle: callers treat it as an unreadable HUD, which
# falls back to the storage icons rather than doing anything rash.
_IMPLAUSIBLE_HUD_MAIN = 30000000


@dataclass(frozen = True)
class VillageState:
    builders: Optional[Tuple[int, int]]  # (free, total) from the builder chip
    lab: Optional[Tuple[int, int]]  # (free, total) research slots from the lab chip
    gold_full: bool  # hero-bar full-storage icon visible
    elixir_full: bool
    captured_at: float  # time.monotonic() of the frame

    @property
    def free_builders(self):
        return self.builders[0] if self.builders else None

    @property
    def storages_full(self):
        '''Both main storages at cap — farming would earn (almost) nothing.
        Dark elixir has no full-storage icon; DE overflow alone never blocks idling.'''
        return self.gold_full and self.elixir_full

    @property
    def lab_idle(self):
        '''True when the lab chip is readable and shows a free research slot.'''
        return bool(self.lab) and self.lab[0] > 0

    def summary(self):
        # The full-storage templates are flat bar-fill swatches — they fire from
        # ~90% up, not at exactly 100% — so phrase it honestly as "~full".
        b = f'{self.builders[0]}/{self.builders[1]}' if self.builders else '?'
        l = f'{self.lab[0]}/{self.lab[1]}' if self.lab else '?'
        s = '~full' if self.storages_full else ('gold ~full' if self.gold_full else ('elixir ~full' if self.elixir_full else 'filling'))
        return f'builders {b} free, lab {l} free, storages {s}'


def read_village_state(frame):
    '''Parse a home-screen frame → :class:`VillageState`. Chip reads are OCR — callers
    that gate behavior on them should tolerate ``None`` (busy frames misread; the
    idle gate re-reads on its next wake anyway).'''
    if frame is None or frame.size == 0:
        return None
    (gx, _gy) = VisionService.find_active_hgoldfull(frame)
    (ex, _ey) = VisionService.find_active_helixirfull(frame)
    state = VillageState(
        builders = parse_builder_chip(frame),
        lab = parse_lab_chip(frame),
        gold_full = gx is not None,
        elixir_full = ex is not None,
        captured_at = time.monotonic())
    logger.debug('village state: %s', state.summary())
    return state


def read_hud_triplet_stable(capture, wait, attempts = 4):
    '''Top-right HUD (gold, elixir, dark) with consecutive-frame agreement. Single
    reads get corrupted by animations/popups crossing the HUD (live repro: elixir
    read as "115M" — 9 digits against a ~21M cap), but corruption is stochastic:
    two identical consecutive parses are trustworthy. Returns the triplet or
    ``None`` when no two consecutive reads agree.'''
    prev = None
    for i in range(max(2, attempts)):
        if i and wait(0.35):
            return None
        frame = capture()
        if frame is None or getattr(frame, 'size', 0) == 0:
            continue
        triplet = VisionService.parse_hud_resources_triplet(VisionService.extract_top_right_hud_numbers(frame))
        if triplet is None:
            prev = None
            continue
        if triplet == prev:
            (gold, elixir, _dark) = triplet
            if gold > _IMPLAUSIBLE_HUD_MAIN or elixir > _IMPLAUSIBLE_HUD_MAIN:
                logger.warning('HUD read gold=%s elixir=%s is impossibly large — treating it as unreadable', gold, elixir)
                prev = None
                continue
            return triplet
        prev = triplet
    return None


def read_village_state_stable(capture, wait, attempts = 3):
    '''Multi-frame :func:`read_village_state`: the translucent HUD chips flicker against
    the animated village, so single-frame chip reads drop out (live repro). Merges up
    to ``attempts`` frames — first readable value per field wins; full-storage icons
    OR across frames. ``capture()`` → frame or None; ``wait(seconds)`` → True to abort
    (stop-event semantics).'''
    merged = None
    for i in range(max(1, attempts)):
        if i and wait(0.4):
            return merged
        state = read_village_state(capture())
        if state is None:
            continue
        if merged is None:
            merged = state
        else:
            merged = VillageState(
                builders = merged.builders or state.builders,
                lab = merged.lab or state.lab,
                gold_full = merged.gold_full or state.gold_full,
                elixir_full = merged.elixir_full or state.elixir_full,
                captured_at = state.captured_at)
        if merged.builders and merged.lab:
            break
    if merged is not None:
        logger.info('village state: %s', merged.summary())
    return merged
