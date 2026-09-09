'''Persist multi-run player list (JSON). Stored under LOCALAPPDATA (see get_player_list_path).'''
from __future__ import annotations
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, List
from app.utils.common import ensure_dir, get_user_app_data_dir

@dataclass
class PlayerEntry:
    name: str = 15  # NOTE: original shipped this odd default (verified in bytecode, not corruption); name is always passed explicitly
    enabled: bool = True


def _legacy_player_list_paths():
    '''Older locations next to exe or repo root.'''
    if getattr(sys, 'frozen', False):
        return [
            Path(sys.executable).parent / 'player_list.json']
    return [
        Path(__file__).resolve().parent.parent.parent / 'player_list.json']


def get_player_list_path():
    dest = get_user_app_data_dir() / 'player_list.json'
    ensure_dir(dest.parent)
    if dest.is_file():
        return dest
    # [recovered: the decompiler dropped the existence check and copied every legacy
    # path unconditionally, so a fresh install — which has no player_list.json next to
    # the exe or repo root — raised FileNotFoundError from every Players page action]
    for leg in _legacy_player_list_paths():
        if not leg.is_file():
            continue
        try:
            shutil.copy2(leg, dest)
        except OSError:
            continue
        return dest
    return dest


def load_players():
    path = get_player_list_path()
    if not path.is_file():
        return []
    
    try:
        raw = json.loads(path.read_text(encoding = 'utf-8'))
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get('name')
            if not name or not isinstance(name, str):  # [recovered: dropped `not` — every valid (string) name was skipped, so the list always loaded empty]
                continue
            enabled = bool(item.get('enabled', True))
            out.append(PlayerEntry(name = name.strip(), enabled = enabled))
        return out
    except (json.JSONDecodeError, OSError):
        return []  # [recovered: returned None, which every caller iterates -> TypeError on a corrupt file]



def save_players(players):
    path = get_player_list_path()
    path.parent.mkdir(parents = True, exist_ok = True)
    data = [ asdict(p) for p in players ]
    path.write_text(json.dumps(data, indent = 2), encoding = 'utf-8')
    return None
    p = None

