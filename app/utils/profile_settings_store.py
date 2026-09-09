'''Profile preferences (JSON) under LOCALAPPDATA\\BasePilot, next to ``player_list.json``.'''
from __future__ import annotations
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, List, Optional
from app.utils.common import ensure_dir, get_user_app_data_dir
EARTHQUAKE_METHOD_CURVE = 'Curve Placement'
EARTHQUAKE_METHOD_RANDOM = 'Random Placement'
EARTHQUAKE_METHOD_OPTIONS = (EARTHQUAKE_METHOD_CURVE, EARTHQUAKE_METHOD_RANDOM)
SETTINGS_FILENAME = 'settings.json'
WALL_UPGRADE_THRESHOLD_M_DEFAULT = 3
WALL_UPGRADE_THRESHOLD_M_MAX = 20
RESERVE_BUILDERS_DEFAULT = 1
RESERVE_BUILDERS_MAX = 5
UPGRADE_ORDER_PRICIEST = 'priciest'
UPGRADE_ORDER_CHEAPEST = 'cheapest'
UPGRADE_ORDER_OPTIONS = (UPGRADE_ORDER_PRICIEST, UPGRADE_ORDER_CHEAPEST)
# Clan assist (see app/core/clan.py). The chat button carries no text and no
# template, so it is calibrated once per install and stored as a fraction of the
# capture — that survives window resizes, and the aspect key it was picked on is
# kept alongside so a 16:9 <-> 16:10 switch can be called out instead of clicking
# a stale spot.
CLAN_DONATE_TROOP_AUTO = 'auto'
CLAN_DONATE_TROOP_OPTIONS = (CLAN_DONATE_TROOP_AUTO, 'valkyries', 'sneaky goblins', 'super minions', 'edrags')
CLAN_DONATE_COUNT_DEFAULT = 0  # 0 = donate until the game greys the troop out
CLAN_DONATE_COUNT_MAX = 10
CLAN_MIN_ELIXIR_K_DEFAULT = 500  # donating costs elixir to replace; keep a floor
CLAN_MIN_ELIXIR_K_MAX = 20000
CLAN_DONATE_INTERVAL_M_DEFAULT = 0  # 0 = donate after every raid
CLAN_DONATE_INTERVAL_M_MIN = 0
CLAN_DONATE_INTERVAL_M_MAX = 60
CLAN_REQUEST_INTERVAL_M_DEFAULT = 30
CLAN_REQUEST_INTERVAL_M_MIN = 5
CLAN_REQUEST_INTERVAL_M_MAX = 240
# Loot filter (see app/core/loot_filter.py): the minimum a base must hold to be worth
# the army and the minutes. Thousands, so the UI stays readable; 0 disables the check.
ATTACK_MIN_GOLD_K_DEFAULT = 500
ATTACK_MIN_ELIXIR_K_DEFAULT = 500
ATTACK_MIN_LOOT_K_MAX = 5000
ATTACK_MAX_SKIPS_DEFAULT = 10
ATTACK_MAX_SKIPS_MAX = 50

@dataclass
class ProfileSettings:
    earthquake_method: 'str' = EARTHQUAKE_METHOD_CURVE
    wall_upgrade_threshold_m: 'int' = WALL_UPGRADE_THRESHOLD_M_DEFAULT
    reserve_builders: 'int' = RESERVE_BUILDERS_DEFAULT
    upgrade_order: 'str' = UPGRADE_ORDER_PRICIEST
    clan_dry_run: 'bool' = True  # first run on a fresh calibration reads and clicks nothing
    clan_chat_point: 'Optional[List[float]]' = None  # [fx, fy] fractions of the capture
    clan_chat_point_aspect: 'Optional[str]' = None
    clan_donate_troop: 'str' = CLAN_DONATE_TROOP_AUTO
    clan_donate_count: 'int' = CLAN_DONATE_COUNT_DEFAULT  # 0 = until the troop greys out
    clan_min_elixir_k: 'int' = CLAN_MIN_ELIXIR_K_DEFAULT  # 0 = donate whatever the HUD says
    clan_donate_interval_m: 'int' = CLAN_DONATE_INTERVAL_M_DEFAULT
    clan_request_interval_m: 'int' = CLAN_REQUEST_INTERVAL_M_DEFAULT
    attack_min_gold_k: 'int' = ATTACK_MIN_GOLD_K_DEFAULT
    attack_min_elixir_k: 'int' = ATTACK_MIN_ELIXIR_K_DEFAULT
    attack_max_skips: 'int' = ATTACK_MAX_SKIPS_DEFAULT  # Next presses per attack cycle; 0 = attack anything


def get_settings_path():
    dest = get_user_app_data_dir() / SETTINGS_FILENAME
    ensure_dir(dest.parent)
    return dest


def _normalize_earthquake_method(raw):
    if raw == EARTHQUAKE_METHOD_RANDOM or raw == EARTHQUAKE_METHOD_CURVE:
        return str(raw)
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s == 'random placement':
            return EARTHQUAKE_METHOD_RANDOM
        if s == 'curve placement':
            return EARTHQUAKE_METHOD_CURVE
    return EARTHQUAKE_METHOD_CURVE


def _normalize_wall_threshold_m(raw):
    '''Millions of gold/elixir that trigger wall upgrades; 0 = only when storages are full.'''
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return WALL_UPGRADE_THRESHOLD_M_DEFAULT
    return max(0, min(WALL_UPGRADE_THRESHOLD_M_MAX, value))


def _normalize_upgrade_order(raw):
    if isinstance(raw, str) and raw.strip().lower() in UPGRADE_ORDER_OPTIONS:
        return raw.strip().lower()
    return UPGRADE_ORDER_PRICIEST


def _normalize_reserve_builders(raw):
    '''Builders the auto-upgrader must leave free (the wall flow spends through them).
    0 = every free builder may be used; walls-maxed accounts want 0.'''
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return RESERVE_BUILDERS_DEFAULT
    return max(0, min(RESERVE_BUILDERS_MAX, value))


def _normalize_clan_chat_point(raw):
    '''``[fx, fy]`` fractions of the capture, or None when not calibrated. Anything
    outside 0..1 is a stale/foreign value and is dropped rather than clicked.'''
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        (fx, fy) = (float(raw[0]), float(raw[1]))
    except (TypeError, ValueError):
        return None
    if not 0 <= fx <= 1 or not 0 <= fy <= 1:
        return None
    return [fx, fy]


def _normalize_clan_chat_point_aspect(raw):
    from app.config import ASPECT_16_9, ASPECT_16_10
    if isinstance(raw, str) and raw.strip() in (ASPECT_16_9, ASPECT_16_10):
        return raw.strip()
    return None


def _normalize_clan_donate_troop(raw):
    if isinstance(raw, str) and raw.strip().lower() in CLAN_DONATE_TROOP_OPTIONS:
        return raw.strip().lower()
    return CLAN_DONATE_TROOP_AUTO


def _normalize_int(raw, default, low, high):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def load_profile_settings():
    path = get_settings_path()
    if not path.is_file():
        return ProfileSettings()

    try:
        raw = json.loads(path.read_text(encoding = 'utf-8'))
        if not isinstance(raw, dict):
            return ProfileSettings()
        return ProfileSettings(
            earthquake_method = _normalize_earthquake_method(raw.get('earthquake_method')),
            wall_upgrade_threshold_m = _normalize_wall_threshold_m(raw.get('wall_upgrade_threshold_m', WALL_UPGRADE_THRESHOLD_M_DEFAULT)),
            reserve_builders = _normalize_reserve_builders(raw.get('reserve_builders', RESERVE_BUILDERS_DEFAULT)),
            upgrade_order = _normalize_upgrade_order(raw.get('upgrade_order', UPGRADE_ORDER_PRICIEST)),
            clan_dry_run = bool(raw.get('clan_dry_run', True)),
            clan_chat_point = _normalize_clan_chat_point(raw.get('clan_chat_point')),
            clan_chat_point_aspect = _normalize_clan_chat_point_aspect(raw.get('clan_chat_point_aspect')),
            clan_donate_troop = _normalize_clan_donate_troop(raw.get('clan_donate_troop')),
            clan_donate_count = _normalize_int(raw.get('clan_donate_count'), CLAN_DONATE_COUNT_DEFAULT, 0, CLAN_DONATE_COUNT_MAX),
            clan_min_elixir_k = _normalize_int(raw.get('clan_min_elixir_k'), CLAN_MIN_ELIXIR_K_DEFAULT, 0, CLAN_MIN_ELIXIR_K_MAX),
            clan_donate_interval_m = _normalize_int(raw.get('clan_donate_interval_m'), CLAN_DONATE_INTERVAL_M_DEFAULT, CLAN_DONATE_INTERVAL_M_MIN, CLAN_DONATE_INTERVAL_M_MAX),
            clan_request_interval_m = _normalize_int(raw.get('clan_request_interval_m'), CLAN_REQUEST_INTERVAL_M_DEFAULT, CLAN_REQUEST_INTERVAL_M_MIN, CLAN_REQUEST_INTERVAL_M_MAX),
            attack_min_gold_k = _normalize_int(raw.get('attack_min_gold_k'), ATTACK_MIN_GOLD_K_DEFAULT, 0, ATTACK_MIN_LOOT_K_MAX),
            attack_min_elixir_k = _normalize_int(raw.get('attack_min_elixir_k'), ATTACK_MIN_ELIXIR_K_DEFAULT, 0, ATTACK_MIN_LOOT_K_MAX),
            attack_max_skips = _normalize_int(raw.get('attack_max_skips'), ATTACK_MAX_SKIPS_DEFAULT, 0, ATTACK_MAX_SKIPS_MAX))
    except (json.JSONDecodeError, OSError):
        return ProfileSettings()  # [recovered: decompiler turned this into `return None`, crashing every caller on a corrupt settings.json]



def save_profile_settings(settings):
    path = get_settings_path()
    path.parent.mkdir(parents = True, exist_ok = True)
    normalized = ProfileSettings(
        earthquake_method = _normalize_earthquake_method(settings.earthquake_method),
        wall_upgrade_threshold_m = _normalize_wall_threshold_m(getattr(settings, 'wall_upgrade_threshold_m', WALL_UPGRADE_THRESHOLD_M_DEFAULT)),
        reserve_builders = _normalize_reserve_builders(getattr(settings, 'reserve_builders', RESERVE_BUILDERS_DEFAULT)),
        upgrade_order = _normalize_upgrade_order(getattr(settings, 'upgrade_order', UPGRADE_ORDER_PRICIEST)),
        clan_dry_run = bool(getattr(settings, 'clan_dry_run', True)),
        clan_chat_point = _normalize_clan_chat_point(getattr(settings, 'clan_chat_point', None)),
        clan_chat_point_aspect = _normalize_clan_chat_point_aspect(getattr(settings, 'clan_chat_point_aspect', None)),
        clan_donate_troop = _normalize_clan_donate_troop(getattr(settings, 'clan_donate_troop', CLAN_DONATE_TROOP_AUTO)),
        clan_donate_count = _normalize_int(getattr(settings, 'clan_donate_count', CLAN_DONATE_COUNT_DEFAULT), CLAN_DONATE_COUNT_DEFAULT, 0, CLAN_DONATE_COUNT_MAX),
        clan_min_elixir_k = _normalize_int(getattr(settings, 'clan_min_elixir_k', CLAN_MIN_ELIXIR_K_DEFAULT), CLAN_MIN_ELIXIR_K_DEFAULT, 0, CLAN_MIN_ELIXIR_K_MAX),
        clan_donate_interval_m = _normalize_int(getattr(settings, 'clan_donate_interval_m', CLAN_DONATE_INTERVAL_M_DEFAULT), CLAN_DONATE_INTERVAL_M_DEFAULT, CLAN_DONATE_INTERVAL_M_MIN, CLAN_DONATE_INTERVAL_M_MAX),
        clan_request_interval_m = _normalize_int(getattr(settings, 'clan_request_interval_m', CLAN_REQUEST_INTERVAL_M_DEFAULT), CLAN_REQUEST_INTERVAL_M_DEFAULT, CLAN_REQUEST_INTERVAL_M_MIN, CLAN_REQUEST_INTERVAL_M_MAX),
        attack_min_gold_k = _normalize_int(getattr(settings, 'attack_min_gold_k', ATTACK_MIN_GOLD_K_DEFAULT), ATTACK_MIN_GOLD_K_DEFAULT, 0, ATTACK_MIN_LOOT_K_MAX),
        attack_min_elixir_k = _normalize_int(getattr(settings, 'attack_min_elixir_k', ATTACK_MIN_ELIXIR_K_DEFAULT), ATTACK_MIN_ELIXIR_K_DEFAULT, 0, ATTACK_MIN_LOOT_K_MAX),
        attack_max_skips = _normalize_int(getattr(settings, 'attack_max_skips', ATTACK_MAX_SKIPS_DEFAULT), ATTACK_MAX_SKIPS_DEFAULT, 0, ATTACK_MAX_SKIPS_MAX))
    payload = asdict(normalized)
    path.write_text(json.dumps(payload, indent = 2), encoding = 'utf-8')
