import os
import sys
from pathlib import Path

def get_resource_path(relative_path):
    '''Get absolute path to resource, works for dev and for PyInstaller.'''
    
    try:
        base_path = Path(sys._MEIPASS)
        return base_path / relative_path
    except Exception:
        base_path = Path(__file__).resolve().parent.parent.parent
        return base_path / relative_path



APP_NAME = 'BasePilot'
_LEGACY_DATA_DIR_NAME = 'ClashAutoLoot'  # pre-rebrand data dir; migrated on first use
_migration_checked = False


def _legacy_data_dir():
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA')
        if local:
            return Path(local) / _LEGACY_DATA_DIR_NAME
    return Path.home() / '.local' / 'share' / _LEGACY_DATA_DIR_NAME


def _maybe_migrate_legacy_data(new_dir):
    '''One-time settings migration from the pre-rebrand data dir: copy the small
    user-state files (settings, player list, window pin, display restore) — never
    logs or debug frames. Idempotent: skipped once the new dir holds settings.'''
    global _migration_checked
    if _migration_checked:
        return
    _migration_checked = True
    old_dir = _legacy_data_dir()
    if not old_dir.is_dir() or (new_dir / 'settings.json').exists():
        return
    import shutil
    ensure_dir(new_dir)
    for item in old_dir.glob('*.json'):
        try:
            shutil.copy2(item, new_dir / item.name)
        except OSError:
            pass
    for item in old_dir.glob('*.devmode'):
        try:
            shutil.copy2(item, new_dir / item.name)
        except OSError:
            pass


PROFILE_ENV = 'BASEPILOT_PROFILE'


def get_base_app_data_dir():
    '''The main profile's data directory (Windows: LOCALAPPDATA\\BasePilot).'''
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA')
        if local:
            path = Path(local) / APP_NAME
        else:
            path = Path.home() / '.local' / 'share' / APP_NAME
    else:
        path = Path.home() / '.local' / 'share' / APP_NAME
    _maybe_migrate_legacy_data(path)
    return path


def get_profile_name():
    '''The profile this process runs under, or ``''`` for the main one.

    Set by ``--profile NAME`` (or the ``BASEPILOT_PROFILE`` environment variable).
    Everything a run writes or pins — settings, the game-window selection, the player
    list, captured templates, the log, debug frames — hangs off the data directory, so a
    profile is what lets two BasePilot windows drive two clients at once without
    overwriting each other's state.
    '''
    return _sanitise_profile(os.environ.get(PROFILE_ENV, ''))


def _sanitise_profile(name):
    '''A profile name reduced to something safe to put in a path (``''`` if nothing is left).'''
    keep = [ c for c in str(name or '').strip() if c.isalnum() or c in ' -_' ]
    return ''.join(keep).strip()[:40]


def get_user_app_data_dir():
    '''Per-user writable data for this process — the profile's directory when one is set.'''
    base = get_base_app_data_dir()
    profile = get_profile_name()
    if not profile:
        return base
    path = base / 'profiles' / profile
    _seed_profile(base, path)
    return path


_seeded_profiles = set()


def _seed_profile(base_dir, profile_dir):
    '''Start a brand-new profile from the main one's settings — but never its window pin.

    A second session is only useful pointed at a second client, so copying window.json
    would hand it the first client and leave the two fighting over one window. Everything
    else (bot settings, the player list) is a sensible starting point. Runs once per
    process, and only while the profile has no settings of its own.
    '''
    key = str(profile_dir)
    if key in _seeded_profiles:
        return
    _seeded_profiles.add(key)
    if (profile_dir / 'settings.json').exists() or not base_dir.is_dir():
        return
    import shutil
    ensure_dir(profile_dir)
    for name in ('settings.json', 'player_list.json'):
        src = base_dir / name
        if src.is_file():
            try:
                shutil.copy2(src, profile_dir / name)
            except OSError:
                pass


def user_template_dirs():
    '''Where a run looks for captured templates, nearest first.

    A profile sees its own captures and then the main profile's, so templates captured
    once do not have to be captured again for every account.
    '''
    dirs = []
    for d in (get_user_app_data_dir(), get_base_app_data_dir()):
        path = d / 'templates'
        if path not in dirs:
            dirs.append(path)
    return dirs


def get_log_path():
    '''Path to the rotating ``basepilot.log`` in the per-user data dir.'''
    ensure_dir(get_user_app_data_dir())
    return get_user_app_data_dir() / 'basepilot.log'


# Back-compat alias (old name used across the decompiled tree).
get_autoloot_log_path = get_log_path


def get_template_path(template_name):
    '''
Return ``templates/<16_10|16_9>/…`` for the active aspect (see :class:`app.config.Config`).
``template_name`` should be a filename like ``attack.png`` (not a subpath with ``..``).

A file of the same name under ``<user data dir>/templates/<aspect>/`` wins over the
bundled one. The released exe unpacks its templates read-only, so that drop-in
folder is the only way to add art (e.g. ``donatetroop.png`` for clan assist) or
replace a template that does not match your client, without rebuilding.
'''
    from app.config import Config
    sub = Config().aspect_key
    for root in user_template_dirs():
        override = root / sub / template_name
        if override.is_file():
            return override
    return get_resource_path(f'''templates/{sub}/{template_name}''')


def ensure_dir(path):
    '''Ensure a directory exists.'''
    path.mkdir(parents = True, exist_ok = True)

