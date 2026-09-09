'''Run page — attack, schedule, modes, live status, controls.'''
from __future__ import annotations
from typing import Callable, List, Optional
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget
from app.config import check_game_window_aspect_for_start
from app.ui.qt._constants import ATTACK_STRATEGIES, BUILDER_BASE_ATTACK_STRATEGIES, BUILDER_BASE_ATTACK_STRATEGIES_UNDER_DEV, BUILDER_BASE_PRIORITISE_LABELS
from app.ui.qt.bot_controller import BotController
from app.ui.qt.dialogs import RankedAttackConfirmDialog, show_bb_prioritise_help, show_error, show_under_development
from app.ui.qt.theme import SPACING, TOKENS
from app.ui.qt.widgets import Card, HelpButton, SectionTitle, StepperButton, ToggleSwitch, chip_button, danger_button, neutral_button, primary_button, segment_button
from app.utils.player_list_store import PlayerEntry, load_players

class RunPage(QWidget):
    _STRATEGY_LABELS = [
        'Valkyries',
        'Sneaky Goblins',
        'Super Minions',
        'Edrags']
    _VILLAGE_LABELS = [
        'Home Village',
        'Builder Base']
    _AUTO_UPGRADE_LABELS = [
        ('Off', 'off'),
        ('Dry run', 'dry'),
        ('Maxer', 'maxer'),
        ('Rusher', 'rusher')]

    def __init__(self, controller, navigate_to, parent = None):
        super().__init__(parent)
        self._controller = controller
        self._navigate_to = navigate_to
        self._settings = QSettings('BasePilot', 'UI')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING['lg'], SPACING['lg'], SPACING['lg'], SPACING['lg'])
        outer.setSpacing(SPACING['md'])
        outer.addWidget(self._build_village_selector())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(SPACING['md'])
        self._attack_card = self._build_attack_card()
        layout.addWidget(self._attack_card)
        self._bb_attack_card = self._build_bb_attack_card()
        layout.addWidget(self._bb_attack_card)
        layout.addWidget(self._build_schedule_card())
        self._modes_card = self._build_modes_card()
        layout.addWidget(self._modes_card)
        self._bb_modes_card = self._build_bb_modes_card()
        layout.addWidget(self._bb_modes_card)
        self._status_card = self._build_status_card()
        layout.addWidget(self._status_card)
        layout.addWidget(self._build_controls_card())
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._apply_village_ui()
        self._restore_choices()
        self._controller.botStarted.connect(self._on_bot_started)
        self._controller.botFinished.connect(self._on_bot_finished_ui)
        self._controller.runningChanged.connect(self._on_running_changed)
        self._controller.stateChanged.connect(self._on_state_changed)
        self._controller.lootChanged.connect(self._on_loot_changed)
        self._controller.statusChanged.connect(self._on_status_line)

    
    def _build_village_selector(self):
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        self._village_group = QButtonGroup(self)
        self._village_group.setExclusive(True)
        for i, label in enumerate(self._VILLAGE_LABELS):
            btn = segment_button(label, parent = wrapper)
            if label == 'Home Village':
                btn.setChecked(True)
            self._village_group.addButton(btn, i)
            row.addWidget(btn)
        row.addStretch()
        self._village_group.idClicked.connect(self._on_village_changed)
        return wrapper

    
    def _is_builder_base(self):
        btn = self._village_group.checkedButton()
        return btn is not None and btn.text() == 'Builder Base'

    
    def _on_village_changed(self, _button_id):
        self._apply_village_ui()

    
    def _apply_village_ui(self):
        builder_base = self._is_builder_base()
        self._attack_card.setVisible(not builder_base)
        self._modes_card.setVisible(not builder_base)
        self._bb_attack_card.setVisible(builder_base)
        self._bb_modes_card.setVisible(builder_base)
        # Until-maxed (and the auto-upgrader behind it) is Home Village only.
        self._until_maxed.setVisible(not builder_base)
        if builder_base and self._until_maxed.isChecked():
            self._until_maxed.setChecked(False)

    
    def _build_attack_card(self):
        card = Card()
        card.card_layout.addWidget(SectionTitle('Attack Strategy'))
        row = QHBoxLayout()
        self._strategy_group = QButtonGroup(self)
        self._strategy_group.setExclusive(True)
        for i, label in enumerate(self._STRATEGY_LABELS):
            btn = segment_button(label, parent = card)
            if label == 'Valkyries':
                btn.setChecked(True)
            self._strategy_group.addButton(btn, i)
            row.addWidget(btn)
        card.card_layout.addLayout(row)
        return card

    
    def _build_bb_attack_card(self):
        card = Card()
        card.card_layout.addWidget(SectionTitle('Attack Strategy'))
        row = QHBoxLayout()
        self._bb_strategy_group = QButtonGroup(self)
        self._bb_strategy_group.setExclusive(True)
        for i, label in enumerate(BUILDER_BASE_ATTACK_STRATEGIES):
            btn = segment_button(label, parent = card)
            if label == 'Baby Dragon':
                btn.setChecked(True)
            self._bb_strategy_group.addButton(btn, i)
            row.addWidget(btn)
        for label in BUILDER_BASE_ATTACK_STRATEGIES_UNDER_DEV:
            btn = segment_button(label, parent = card, under_development = True)
            btn.clicked.connect((lambda _checked = False: show_under_development(self.window())))
            row.addWidget(btn)
        row.addStretch()
        card.card_layout.addLayout(row)
        return card

    
    def _build_schedule_card(self):
        card = Card()
        card.card_layout.addWidget(SectionTitle('Schedule'))
        self._star_bonus = ToggleSwitch('Star Bonus', parent = card)
        self._star_bonus.toggled.connect(self._on_star_bonus_toggle)
        card.card_layout.addWidget(self._star_bonus)
        self._until_maxed = ToggleSwitch('Run until maxed (no time limit)', parent = card)
        self._until_maxed.toggled.connect(self._on_until_maxed_toggle)
        card.card_layout.addWidget(self._until_maxed)
        until_hint = QLabel('Farms, upgrades, and idles in a loop until you press Stop. When storages are full and nothing can be started, the bot waits and rechecks every 5 minutes instead of attacking for nothing.')
        until_hint.setWordWrap(True)
        until_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(until_hint)
        dur_row = QHBoxLayout()
        self._duration_label = SectionTitle('Duration')
        dur_row.addWidget(self._duration_label)
        self._minutes_spin = QSpinBox()
        self._minutes_spin.setObjectName('DurationSpin')
        self._minutes_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._minutes_spin.setRange(1, 999)
        self._minutes_spin.setValue(15)
        self._minutes_spin.setFixedSize(56, 28)
        self._minutes_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        step_col = QVBoxLayout()
        step_col.setSpacing(2)
        step_col.setContentsMargins(0, 0, 0, 0)
        self._btn_spin_up = StepperButton(up = True, parent = card)
        self._btn_spin_up.clicked.connect((lambda : self._step_minutes(1)))
        self._btn_spin_down = StepperButton(up = False, parent = card)
        self._btn_spin_down.clicked.connect((lambda : self._step_minutes(-1)))
        step_col.addWidget(self._btn_spin_up)
        step_col.addWidget(self._btn_spin_down)
        self._minutes_unit = QLabel('minutes')
        self._minutes_unit.setObjectName('DurationUnit')
        self._minutes_unit.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        spin_group = QHBoxLayout()
        spin_group.setSpacing(4)
        spin_group.addWidget(self._minutes_spin)
        spin_group.addLayout(step_col)
        spin_group.addWidget(self._minutes_unit)
        spin_group.addStretch()
        dur_row.addLayout(spin_group)
        dur_row.addStretch()
        card.card_layout.addLayout(dur_row)
        preset_row = QHBoxLayout()
        self._btn_5m = chip_button('5m', parent = card)
        self._btn_5m.clicked.connect((lambda : self._set_minutes(5)))
        preset_row.addWidget(self._btn_5m)
        self._btn_10m = chip_button('10m', parent = card)
        self._btn_10m.clicked.connect((lambda : self._set_minutes(10)))
        preset_row.addWidget(self._btn_10m)
        self._btn_20m = chip_button('20m', parent = card)
        self._btn_20m.clicked.connect((lambda : self._set_minutes(20)))
        preset_row.addWidget(self._btn_20m)
        preset_row.addStretch()
        card.card_layout.addLayout(preset_row)
        self._on_star_bonus_toggle(self._star_bonus.isChecked())
        return card

    
    def _build_modes_card(self):
        card = Card()
        card.card_layout.addWidget(SectionTitle('Modes'))
        upg_row = QHBoxLayout()
        upg_row.addWidget(SectionTitle('Auto upgrade'))
        upg_row.addSpacing(SPACING['xs'])
        self._auto_upgrade_group = QButtonGroup(self)
        self._auto_upgrade_group.setExclusive(True)
        for i, (label, _key) in enumerate(self._AUTO_UPGRADE_LABELS):
            btn = segment_button(label, parent = card)
            if label == 'Off':
                btn.setChecked(True)
            self._auto_upgrade_group.addButton(btn, i)
            upg_row.addWidget(btn)
        upg_row.addStretch()
        card.card_layout.addLayout(upg_row)
        upg_hint = QLabel('Maxer starts the cheapest affordable upgrade with your loot (never the Town Hall); Rusher grabs the Town Hall the moment it is affordable. Dry run only logs what it would start. Works at any Town Hall level. Reserve builders for walls in Settings.')
        upg_hint.setWordWrap(True)
        upg_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(upg_hint)
        self._ranked = ToggleSwitch('Ranked attack fill', parent = card, danger = True)
        card.card_layout.addWidget(self._ranked)
        self._upgrade_walls = ToggleSwitch('Upgrade walls', parent = card)
        card.card_layout.addWidget(self._upgrade_walls)
        self._auto_donate = ToggleSwitch('Auto donate', parent = card)
        card.card_layout.addWidget(self._auto_donate)
        self._auto_request = ToggleSwitch('Auto request troops', parent = card)
        card.card_layout.addWidget(self._auto_request)
        clan_hint = QLabel('Between raids the bot opens the clan chat, donates to open requests, and asks for reinforcements. Pick the chat button once in Settings → Clan assist — dry run is on there by default, so the first run only logs what it would click.')
        clan_hint.setWordWrap(True)
        clan_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(clan_hint)
        mr_row = QHBoxLayout()
        self._multi_run = ToggleSwitch('Multi-run', parent = card)
        mr_row.addWidget(self._multi_run)
        mr_row.addStretch()
        edit_players = neutral_button('Edit player list', parent = card)
        edit_players.clicked.connect((lambda : self._navigate_to('players')))
        mr_row.addWidget(edit_players)
        card.card_layout.addLayout(mr_row)
        return card

    
    def _build_bb_modes_card(self):
        card = Card()
        card.card_layout.addWidget(SectionTitle('Modes'))
        prior_row = QHBoxLayout()
        prior_row.addWidget(SectionTitle('Prioritise'))
        prior_help = HelpButton((lambda : show_bb_prioritise_help(self.window())), parent = card)
        prior_row.addWidget(prior_help, alignment = Qt.AlignmentFlag.AlignVCenter)
        prior_row.addSpacing(SPACING['xs'])
        self._bb_prioritise_group = QButtonGroup(self)
        self._bb_prioritise_group.setExclusive(True)
        for i, label in enumerate(BUILDER_BASE_PRIORITISE_LABELS):
            btn = segment_button(label, parent = card)
            if label == 'Both':
                btn.setChecked(True)
            self._bb_prioritise_group.addButton(btn, i)
            prior_row.addWidget(btn)
        prior_row.addStretch()
        card.card_layout.addLayout(prior_row)
        self._bb_upgrade_walls = ToggleSwitch('Upgrade walls', parent = card, under_development = True)
        card.card_layout.addWidget(self._bb_upgrade_walls)
        mr_row = QHBoxLayout()
        self._bb_multi_run = ToggleSwitch('Multi-run', parent = card, under_development = True)
        mr_row.addWidget(self._bb_multi_run)
        mr_row.addStretch()
        edit_players = neutral_button('Edit player list', parent = card)
        edit_players.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        edit_players.clicked.connect((lambda : show_under_development(self.window())))
        mr_row.addWidget(edit_players)
        card.card_layout.addLayout(mr_row)
        return card

    
    def _build_status_card(self):
        card = Card()
        card.setProperty('variant', 'status')
        card.style().unpolish(card)
        card.style().polish(card)
        card.card_layout.addWidget(SectionTitle('Live status'))
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACING['lg'])
        grid.setVerticalSpacing(4)

        def stat(row, col, title):
            t = QLabel(title.upper())
            t.setObjectName('StatTitle')
            font = t.font()
            font.setLetterSpacing(font.SpacingType.PercentageSpacing, 110)
            t.setFont(font)
            v = QLabel('—')
            v.setObjectName('StatValue')
            # Word-wrap keeps a long readout (the storages triplet) from inflating the
            # card's minimum width past the viewport (clipped every card's right edge).
            v.setWordWrap(True)
            grid.addWidget(t, row * 2, col)
            grid.addWidget(v, row * 2 + 1, col)
            grid.setColumnStretch(col, 1)
            return v

        self._stat_state = stat(0, 0, 'State')
        self._stat_builders = stat(0, 1, 'Builders free')
        self._stat_lab = stat(0, 2, 'Lab free')
        self._stat_storages = stat(0, 3, 'Storages')
        self._stat_loot = stat(1, 0, 'Loot this session')
        self._stat_loot_rate = stat(1, 1, 'Loot / hour')
        self._stat_skipped = stat(1, 2, 'Bases skipped')
        self._stat_note = QLabel('Start the bot to see live village state here. Builders / lab / storages update while Auto upgrade is on.')
        self._stat_note.setWordWrap(True)
        self._stat_note.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addLayout(grid)
        card.card_layout.addWidget(self._stat_note)
        return card

    @staticmethod
    def _fmt_amount(value):
        value = int(value)
        if value >= 1000000:
            return f'''{value / 1000000:.1f}M'''
        if value >= 10000:
            return f'''{value / 1000:.0f}k'''
        return str(value)

    _STATE_COLORS = {
        'farming': '#4ADE80',  # engaged green
        'idling': '#F59E0B',   # holding amber
    }

    def _on_state_changed(self, payload):
        state = payload.get('state')
        if state:
            self._stat_state.setText(str(state).upper())
            color = self._STATE_COLORS.get(str(state).lower(), TOKENS['text'])
            self._stat_state.setStyleSheet(f'''color: {color};''')
        builders = payload.get('builders')
        if builders:
            self._stat_builders.setText(f'''{builders[0]} / {builders[1]}''')
        lab = payload.get('lab')
        if lab:
            self._stat_lab.setText(f'''{lab[0]} / {lab[1]}''')
        storages = payload.get('storages')
        if storages:
            self._stat_storages.setText(str(storages))
        skipped = payload.get('skipped')
        if skipped is not None:
            self._stat_skipped.setText(str(skipped))
        note = payload.get('note')
        if note:
            self._stat_note.setText(f'''Upgrades: {note}''')

    def _on_loot_changed(self, gold, elixir, dark, elapsed):
        self._stat_loot.setText(f'''{self._fmt_amount(gold)} / {self._fmt_amount(elixir)} / {self._fmt_amount(dark)}''')
        if elapsed < 600:
            # A per-hour figure extrapolated from a few minutes is noise — hold it back.
            self._stat_loot_rate.setText('— (needs 10m)')
            return None
        hours = elapsed / 3600
        self._stat_loot_rate.setText(f'''{self._fmt_amount(gold / hours)} / {self._fmt_amount(elixir / hours)} / {self._fmt_amount(dark / hours)}''')

    def _on_status_line(self, msg, _warning):
        if msg.lower().startswith(('idling', 'resuming')):
            idling = msg.lower().startswith('idling')
            self._stat_state.setText('IDLING' if idling else 'FARMING')
            self._stat_state.setStyleSheet(f'''color: {self._STATE_COLORS['idling' if idling else 'farming']};''')
            self._stat_note.setText(msg)

    def _confirm_bb_elixir_prioritise(self):
        reply = QMessageBox.warning(self.window(), 'Elixir priority', 'Prioritising Elixir will deplete your trophies.\n\nDo you want to continue?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return reply == QMessageBox.StandardButton.Yes

    
    def _build_controls_card(self):
        card = Card()
        btn_row = QHBoxLayout()
        self._btn_start = primary_button('Start', parent = card)
        self._btn_start.setMinimumHeight(42)
        self._btn_start.clicked.connect(self.start_bot)
        btn_row.addWidget(self._btn_start)
        self._btn_stop = danger_button('Stop', parent = card)
        self._btn_stop.setMinimumHeight(42)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._controller.stop)
        btn_row.addWidget(self._btn_stop)
        card.card_layout.addLayout(btn_row)
        return card

    
    def _clear_minutes_selection(self):
        editor = self._minutes_spin.lineEdit()
        if not editor is None:
            editor.deselect()
            editor.setCursorPosition(len(editor.text()))
            return None

    
    def _duration_locked(self):
        return self._star_bonus.isChecked() or self._until_maxed.isChecked()

    def _step_minutes(self, delta):
        if self._duration_locked():
            return None
        if delta > 0:
            self._minutes_spin.stepUp()
        else:
            self._minutes_spin.stepDown()
        self._clear_minutes_selection()
        QTimer.singleShot(0, self._clear_minutes_selection)


    def _set_minutes(self, value):
        if self._duration_locked():
            return None
        self._minutes_spin.setValue(value)
        self._clear_minutes_selection()


    def _update_duration_enabled(self):
        enabled = not self._duration_locked()
        duration_widgets = (self._duration_label, self._minutes_spin, self._btn_spin_up, self._btn_spin_down, self._minutes_unit, self._btn_5m, self._btn_10m, self._btn_20m)
        for widget in duration_widgets:
            widget.setEnabled(enabled)
        if enabled:
            self._minutes_unit.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
            return None
        self._minutes_unit.setStyleSheet('color: #4a5568;')

    def _on_star_bonus_toggle(self, checked):
        if checked and getattr(self, '_until_maxed', None) is not None and self._until_maxed.isChecked():
            self._until_maxed.setChecked(False)
        self._update_duration_enabled()

    def _on_until_maxed_toggle(self, checked):
        if checked and self._star_bonus.isChecked():
            self._star_bonus.setChecked(False)
        self._update_duration_enabled()

    
    def _get_method(self):
        if self._is_builder_base():
            btn = self._bb_strategy_group.checkedButton()
            if btn is None:
                return BUILDER_BASE_ATTACK_STRATEGIES['Baby Dragon']
            return BUILDER_BASE_ATTACK_STRATEGIES.get(btn.text(), 5)
        btn = self._strategy_group.checkedButton()
        if btn is None:
            return ATTACK_STRATEGIES['Valkyries']
        return ATTACK_STRATEGIES.get(btn.text(), 1)

    
    def _get_bb_prioritise(self):
        btn = self._bb_prioritise_group.checkedButton()
        if btn is None:
            return 'both'
        return btn.text().lower()


    def _get_auto_upgrade_mode(self):
        checked_id = self._auto_upgrade_group.checkedId()
        if 0 <= checked_id < len(self._AUTO_UPGRADE_LABELS):
            return self._AUTO_UPGRADE_LABELS[checked_id][1]
        return 'off'

    def _set_auto_upgrade_mode(self, mode):
        for i, (_label, key) in enumerate(self._AUTO_UPGRADE_LABELS):
            if key == mode:
                btn = self._auto_upgrade_group.button(i)
                if btn is not None:
                    btn.setChecked(True)
                return None

    def _restore_choices(self):
        '''Bring back the last run's choices (mode, walls, duration) across app starts.'''
        self._set_auto_upgrade_mode(str(self._settings.value('run/autoUpgrade', 'off')))
        self._upgrade_walls.setChecked(self._settings.value('run/upgradeWalls', False, type = bool))
        self._auto_donate.setChecked(self._settings.value('run/autoDonate', False, type = bool))
        self._auto_request.setChecked(self._settings.value('run/autoRequest', False, type = bool))
        self._until_maxed.setChecked(self._settings.value('run/untilMaxed', False, type = bool))
        minutes = self._settings.value('run/minutes', 15, type = int)
        if 1 <= minutes <= 999:
            self._minutes_spin.setValue(minutes)

    def _save_choices(self):
        self._settings.setValue('run/autoUpgrade', self._get_auto_upgrade_mode())
        self._settings.setValue('run/upgradeWalls', self._upgrade_walls.isChecked())
        self._settings.setValue('run/autoDonate', self._auto_donate.isChecked())
        self._settings.setValue('run/autoRequest', self._auto_request.isChecked())
        self._settings.setValue('run/untilMaxed', self._until_maxed.isChecked())
        self._settings.setValue('run/minutes', self._minutes_spin.value())

    
    def _get_minutes(self):
        return self._minutes_spin.value()

    
    def _multi_run_players_for_start(self):
        if self._is_builder_base() or not self._multi_run.isChecked():  # [recovered: decompiler inverted both halves — multi-run never engaged on Home Village]
            return None
        players = load_players()
        if not any([ p.enabled and p.name.strip() for p in players ]):
            return []
        return players

    
    def start_bot(self):
        if self._controller.is_running():
            return None
        if not check_game_window_aspect_for_start(parent = self.window(), on_configure = (lambda : self._navigate_to('settings'))):
            return None
        builder_base = self._is_builder_base()
        method = self._get_method()
        multi_arg = self._multi_run_players_for_start()
        if not builder_base and self._multi_run.isChecked() and not multi_arg:  # [recovered: decompiler dropped the `not` — the error fired when players DID exist]
            show_error(self.window(), 'Multi-run', 'Enable at least one player with Run and a non-empty name.\nOpen Player list… to edit.')
            return None
        star_bonus = self._star_bonus.isChecked()
        # "Run until maxed" = unlimited duration; single Home Village runs only.
        until_maxed = (not builder_base and not star_bonus and multi_arg is None
                       and self._until_maxed.isChecked())
        if star_bonus:
            mins = 5
        elif until_maxed:
            mins = 0
        else:
            mins = self._get_minutes()
            if mins <= 0 or mins > 999:
                show_error(self.window(), 'Error', 'Invalid time duration. Enter 1-999 minutes.')
                return None
        ranked_fill = False if builder_base else self._ranked.isChecked()
        if ranked_fill:
            confirm_mins = self._get_minutes()
            if not RankedAttackConfirmDialog.ask(self.window(), confirm_mins):
                return None
        if builder_base and self._get_bb_prioritise() == 'elixir' and not self._confirm_bb_elixir_prioritise():  # [recovered: decompiler inverted both conditions]
            return None
        self._save_choices()
        # Clan assist rides the Home Village flow (the chat button is calibrated on it,
        # and the Builder Base has no clan chat).
        self._controller.start(method = method, minutes = mins, star_bonus = star_bonus, ranked_fill = ranked_fill, upgrade_walls = False if builder_base else self._upgrade_walls.isChecked(), multi_run_players = multi_arg, builder_base = builder_base, loot_prioritise = self._get_bb_prioritise() if builder_base else 'both', auto_upgrade = 'off' if builder_base else self._get_auto_upgrade_mode(), auto_donate = False if builder_base else self._auto_donate.isChecked(), auto_request = False if builder_base else self._auto_request.isChecked())

    
    def is_star_bonus_enabled(self):
        return self._star_bonus.isChecked()

    
    def request_start_from_taskbar(self):
        if not self._controller.is_running():
            self.start_bot()
            return None


    def apply_autostart(self, minutes, upgrade_walls, auto_upgrade = 'off', auto_donate = False, auto_request = False):
        '''CLI ``--autostart``: Home Village run with the given duration (``0`` =
        run until maxed), wall toggle, auto-upgrade mode (off|dry|maxer|rusher), and
        the clan-assist toggles.'''
        if self._controller.is_running():
            return None
        self._star_bonus.setChecked(False)
        self._until_maxed.setChecked(minutes <= 0)
        if minutes > 0:
            self._minutes_spin.setValue(minutes)
        self._upgrade_walls.setChecked(bool(upgrade_walls))
        self._auto_donate.setChecked(bool(auto_donate))
        self._auto_request.setChecked(bool(auto_request))
        self._set_auto_upgrade_mode(auto_upgrade)
        self.start_bot()

    
    def request_stop_from_taskbar(self):
        if self._controller.is_running():
            self._controller.stop()
            return None

    
    def _on_bot_started(self):
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)

    
    def _on_bot_finished_ui(self, _error):
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)

    
    def _on_running_changed(self, running):
        self._btn_start.setEnabled(not running)
        self._btn_stop.setEnabled(running)


