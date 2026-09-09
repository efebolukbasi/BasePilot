'''Upgrade policy for the auto-maxer: which upgrade to start, given the parsed
builder menu, OCR'd resources, and free builders.

Maxer and rusher are one engine with different rules — not two bots:
- maxer: never touches the Town Hall row; spends on everything else, cheapest first.
- rusher: takes the Town Hall the moment it is affordable; otherwise cheapest first.

Shared invariants: keep ``reserve_builders`` free (walls are the overflow sink for
the reserved builder — the existing wall flow handles those), and keep a gold buffer
so the Find a Match entry fee is never spent away.
'''
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

from app.core.upgrade_menu import (
    RESOURCE_DARK,
    RESOURCE_ELIXIR,
    RESOURCE_GOLD,
    SECTION_IN_PROGRESS,
    SECTION_OTHER,
    SECTION_SUGGESTED,
    UpgradeRow,
    is_town_hall_row,
    is_wall_row,
)
from app.utils.logger import setup_logger

logger = setup_logger('UpgradePolicy')

MODE_MAXER = 'maxer'
MODE_RUSHER = 'rusher'

ORDER_PRICIEST = 'priciest'
ORDER_CHEAPEST = 'cheapest'
UPGRADE_ORDERS = (ORDER_PRICIEST, ORDER_CHEAPEST)


@dataclass(frozen = True)
class UpgradePolicy:
    mode: str = MODE_MAXER
    reserve_builders: int = 1
    gold_attack_buffer: int = 50000  # Find a Match entry fee is ~1300; keep a margin
    # 'priciest' (default): a bot-farmed account is builder-limited, not loot-limited —
    # each builder should soak the biggest job available (also drains full storages
    # hardest, which is what unblocks farming). 'cheapest' spreads builders across
    # many small jobs instead.
    order: str = ORDER_PRICIEST


def _available_for(resource, resources, policy):
    (gold, elixir, dark) = resources
    if resource == RESOURCE_GOLD:
        return max(0, gold - policy.gold_attack_buffer)
    if resource == RESOURCE_ELIXIR:
        return elixir
    if resource == RESOURCE_DARK:
        return dark
    return 0


def can_pay(row, resources, policy):
    '''True when the row has a parsed cost/resource and the OCR'd resources cover it
    (after the gold attack buffer).'''
    if row.cost is None or row.resource is None:
        return False
    return row.cost <= _available_for(row.resource, resources, policy)


def choose_upgrade(rows, resources, free_builders, policy):
    '''Pick the upgrade to start now, or ``None``.

    ``rows``: merged :class:`UpgradeRow` list from the builder menu scan.
    ``resources``: OCR'd ``(gold, elixir, dark)`` HUD triplet.
    ``free_builders``: from the builder chip; ``None`` means unknown → do nothing.
    '''
    if free_builders is None or free_builders <= policy.reserve_builders:
        return None
    # An unread section header must not disqualify a row. The popup only draws
    # 'Suggested upgrades:' / 'Other upgrades:' once, so every view scrolled past them
    # has no header to read, and a missed header read blanks the section for the whole
    # descent — live repro: 51 rows scanned, every one section '', "nothing to start"
    # with two builders free and a 5,000-gold Bomb sitting there affordable. What
    # actually disqualifies a row is being IN PROGRESS, and those never carry a parsed
    # cost (they show remaining time), so the cost/affordability gates below already
    # exclude them; the section check is kept only to reject them explicitly.
    candidates = [
        r for r in rows
        if r.section != SECTION_IN_PROGRESS
        and r.affordable
        and not is_wall_row(r)  # walls belong to the wall-batch flow (overflow sink)
        and can_pay(r, resources, policy)
    ]
    if not candidates:
        return None
    th_rows = [r for r in candidates if is_town_hall_row(r)]
    if policy.mode == MODE_RUSHER and th_rows:
        pick = min(th_rows, key = (lambda r: r.cost))
        logger.info('Policy(rusher): Town Hall affordable — picking it (%s, cost %s)', pick.label, pick.cost)
        return pick
    if policy.mode != MODE_RUSHER:
        candidates = [r for r in candidates if not is_town_hall_row(r)]
    if not candidates:
        return None
    # Dark elixir rows get first claim regardless of raw cost: DE is the scarcest
    # resource and hero upgrades (its main sink) are the long pole to maxing — a
    # 175k-DE hero row must never lose to a 3M-gold trap on number size alone.
    de_rows = [r for r in candidates if r.resource == RESOURCE_DARK]
    pool = de_rows or candidates
    chooser = min if policy.order == ORDER_CHEAPEST else max
    pick = chooser(pool, key = (lambda r: r.cost))
    logger.info('Policy(%s/%s): picking %s (cost %s %s)', policy.mode, policy.order, pick.label, pick.cost, pick.resource)
    return pick
