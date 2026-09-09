'''Main application window — sidebar shell.'''
from __future__ import annotations
import platform
import sys
from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QStackedWidget, QStatusBar, QVBoxLayout, QWidget
from app import __version__
from app.ui.qt.branding import apply_app_icon, logo_pixmap
from app.ui.qt.bot_controller import BotController
from app.ui.qt.dialogs import show_error
from app.ui.qt.pages.logs import LogsPage
from app.ui.qt.pages.players import PlayersPage
from app.ui.qt.pages.run import RunPage
from app.ui.qt.pages.settings import SettingsPage
from app.ui.qt.taskbar_thumb_qt import QtTaskbarThumb
from app.ui.qt.theme import SIDEBAR_WIDTH, TOKENS, WINDOW_DEFAULT, WINDOW_MIN
PAGE_KEYS = [
    'run',
    'settings',
    'players',
    'logs']
PAGE_LABELS = [
    'Run',
    'Settings',
    'Players',
    'Logs']

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        # The profile is in the title because two BasePilot windows on one desktop are
        # otherwise indistinguishable, and each one is driving a different account.
        from app.utils.common import get_profile_name
        profile = get_profile_name()
        self.setWindowTitle(f'''BasePilot — {profile}''' if profile else 'BasePilot')
        self.resize(*WINDOW_DEFAULT)
        self.setMinimumSize(*WINDOW_MIN)
        # Window geometry, the last open tab and other UI state are per profile too.
        self._settings = QSettings('BasePilot', f'''UI-{profile}''' if profile else 'UI')
        self._migrate_legacy_ui_settings()
        self._controller = BotController(bot_version = __version__)
        self._taskbar = None
        self._taskbar_setup_attempts = 0
        self._taskbar_setup_done = False
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar_panel = QWidget()
        sidebar_panel.setFixedWidth(SIDEBAR_WIDTH)
        sidebar_col = QVBoxLayout(sidebar_panel)
        sidebar_col.setContentsMargins(12, 16, 12, 8)
        sidebar_col.setSpacing(8)
        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(logo_pixmap(48))
        sidebar_col.addWidget(logo)
        brand = QLabel(f'''<span style="color:{TOKENS['primary']}">Base</span><span style="color:{TOKENS['text']}">Pilot</span>''')
        brand.setObjectName('Brand')
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_col.addWidget(brand)
        self._sidebar = QListWidget()
        self._sidebar.setObjectName('Sidebar')
        for label in PAGE_LABELS:
            QListWidgetItem(label, self._sidebar)
        self._sidebar.setCurrentRow(0)
        sidebar_col.addWidget(self._sidebar, stretch = 1)
        root.addWidget(sidebar_panel)
        self._stack = QStackedWidget()
        self._run_page = RunPage(self._controller, self.navigate_to)
        self._settings_page = SettingsPage()
        self._players_page = PlayersPage()
        self._logs_page = LogsPage()
        for page in (self._run_page, self._settings_page, self._players_page, self._logs_page):
            self._stack.addWidget(page)
        root.addWidget(self._stack, stretch = 1)
        self._build_status_bar()
        self._wire_signals()
        self._sidebar.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._restore_geometry()


    def _migrate_legacy_ui_settings(self):
        '''One-time QSettings migration from the pre-rebrand org name.'''
        if self._settings.contains('geometry'):
            return None
        legacy = QSettings('ClashAutoLoot', 'UI')
        for key in ('geometry', 'windowState', 'run/autoUpgrade', 'run/upgradeWalls', 'run/untilMaxed', 'run/minutes'):
            if legacy.contains(key):
                self._settings.setValue(key, legacy.value(key))

    def _build_status_bar(self):
        bar = QStatusBar()
        self.setStatusBar(bar)
        self._status_label = QLabel('Ready')
        self._status_label.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        bar.addWidget(self._status_label, stretch = 1)
        version_lbl = QLabel(f'''v{__version__}''')
        version_lbl.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        bar.addPermanentWidget(version_lbl)


    def _wire_signals(self):
        self._controller.statusChanged.connect(self._on_status)
        self._controller.botStarted.connect(self._on_bot_started_status)
        self._controller.botFinished.connect(self._on_bot_finished)
        self._controller.runningChanged.connect(self._on_running_changed)


    def navigate_to(self, page_key):
        if page_key in PAGE_KEYS:
            self._sidebar.setCurrentRow(PAGE_KEYS.index(page_key))
            return None


    def autostart_run(self, minutes, upgrade_walls, auto_upgrade = 'off', auto_donate = False, auto_request = False):
        '''CLI ``--autostart``: configure the Run page and press Start.'''
        self._run_page.apply_autostart(minutes, upgrade_walls, auto_upgrade, auto_donate, auto_request)


    def _restore_geometry(self):
        geo = self._settings.value('geometry')
        if not geo is None:
            self.restoreGeometry(geo)
        state = self._settings.value('windowState')
        if not state is None:
            self.restoreState(state)
            return None


    def showEvent(self, event):
        super().showEvent(event)
        if not self._taskbar_setup_done:
            QTimer.singleShot(500, self._setup_taskbar_thumb)
            return None


    def closeEvent(self, event):
        if self._controller.is_running():
            self._controller.stop()
        if not self._taskbar is None:
            self._taskbar.teardown()
        self._settings.setValue('geometry', self.saveGeometry())
        self._settings.setValue('windowState', self.saveState())
        event.accept()


    def _setup_taskbar_thumb(self):
        if self._taskbar_setup_done or platform.system() != 'Windows':
            return None

        try:
            self._taskbar = QtTaskbarThumb(self._controller, self._run_page, parent = self)
            if self._taskbar.setup(self):
                self._taskbar.set_running(self._controller.is_running())
                self._taskbar_setup_done = True
                return None
            self._taskbar = None
            return None
        except Exception:
            self._taskbar = None
            return None


    def _on_status(self, msg, warning):
        color = TOKENS['danger'] if warning else TOKENS['text_muted']
        self._status_label.setText(msg)
        self._status_label.setStyleSheet(f'''color: {color};''')


    def _on_bot_started_status(self):
        if self._run_page.is_star_bonus_enabled():
            self._status_label.setText('Star Bonus...')
        else:
            self._status_label.setText('Running...')
        self._status_label.setStyleSheet(f'''color: {TOKENS['text_muted']};''')


    def _on_bot_finished(self, error_msg):
        self._controller.on_bot_finished()
        if not self._taskbar is None:
            self._taskbar.set_running(False)
        if error_msg:
            preview = error_msg[:50] + '...' if len(error_msg) > 50 else error_msg
            self._status_label.setText(f'''Error: {preview}''')
            self._status_label.setStyleSheet(f'''color: {TOKENS['danger']};''')
            show_error(self, 'Error', str(error_msg))
            return None
        self._status_label.setText('Stopped')
        self._status_label.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        if sys.platform == 'win32':

            try:
                import winsound
                winsound.MessageBeep(winsound.MB_OK)
            except Exception:
                pass


    def _on_running_changed(self, running):
        if not self._taskbar is None:
            self._taskbar.set_running(running)
            return None
