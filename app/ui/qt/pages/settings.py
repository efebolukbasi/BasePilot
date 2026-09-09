'''Settings page — profile preferences and manual game-window selection.'''
from __future__ import annotations
from typing import List, Optional
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QSpinBox, QVBoxLayout, QWidget
from app.config import resolve_aspect_key
from app.services.display import DisplayService
from app.services.window import DescendantInfo, WindowCandidate, WindowService
from app.core.clan import donate_troop_template_names, missing_templates
from app.ui.qt.point_picker import PointPickerDialog
from app.ui.qt.template_capture import TemplateCaptureDialog
from app.ui.qt.theme import SPACING, TOKENS
from app.ui.qt.widgets import Card, PageTitle, SectionTitle, ToggleSwitch, neutral_button, primary_button
from app.utils.logger import setup_logger
from app.utils.profile_settings_store import ATTACK_MAX_SKIPS_MAX, ATTACK_MIN_LOOT_K_MAX, CLAN_MIN_ELIXIR_K_MAX, CLAN_DONATE_COUNT_MAX, CLAN_DONATE_INTERVAL_M_MAX, CLAN_DONATE_INTERVAL_M_MIN, CLAN_REQUEST_INTERVAL_M_MAX, CLAN_REQUEST_INTERVAL_M_MIN, EARTHQUAKE_METHOD_OPTIONS, RESERVE_BUILDERS_MAX, WALL_UPGRADE_THRESHOLD_M_MAX, ProfileSettings, load_profile_settings, save_profile_settings
from app.utils.window_settings_store import clear_window_selection, load_window_selection, save_window_selection
logger = setup_logger('SettingsPage')
# Display label -> the key stored in settings.json (see app.core.clan).
CLAN_TROOP_CHOICES = (
    ('Auto (first troop found)', 'auto'),
    ('Valkyries', 'valkyries'),
    ('Sneaky Goblins', 'sneaky goblins'),
    ('Super Minions', 'super minions'),
    ('Edrags', 'edrags'))

class WindowInfoDialog(QDialog):
    '''Read-only view of every child window/surface under a selected top-level window.'''
    
    def __init__(self, parent, candidate, descendants):
        super().__init__(parent)
        self.setWindowTitle('Window info')
        self.setModal(True)
        self.resize(640, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING['lg'], SPACING['lg'], SPACING['lg'], SPACING['lg'])
        layout.setSpacing(SPACING['sm'])
        header = QLabel(f'''Top-level: {candidate.title or '(no title)'}\nClass: {candidate.top_class}    hwnd={candidate.top_hwnd}''')
        header.setWordWrap(True)
        header.setStyleSheet(f'''color: {TOKENS['text']};''')
        layout.addWidget(header)
        count = len(descendants)
        surfaces = sum([ 1 for d in descendants if d.is_surface ])
        summary = QLabel(f'''{count} child window(s), {surfaces} game surface(s). Surfaces are marked [surface].''')
        summary.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        layout.addWidget(summary)
        listing = QListWidget()
        listing.setObjectName('WindowInfoList')
        mono = QFont('Consolas')
        mono.setStyleHint(QFont.StyleHint.Monospace)
        listing.setFont(mono)
        if descendants:
            for d in descendants:
                item = QListWidgetItem(d.display_label())
                if d.is_surface:
                    item.setForeground(QColor(TOKENS['primary']))
                listing.addItem(item)
        else:
            listing.addItem(QListWidgetItem('No child windows found under this window.'))
        layout.addWidget(listing, stretch = 1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = neutral_button('Close', parent = self)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)



class SettingsPage(QWidget):
    
    def __init__(self, parent = None):
        super().__init__(parent)
        self._candidates = []
        self._display = DisplayService()
        # Held here rather than in a widget: a picked point is data, not a control.
        self._clan_chat_point = None
        self._clan_chat_point_aspect = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING['lg'], SPACING['lg'], SPACING['lg'], SPACING['lg'])
        outer.setSpacing(SPACING['md'])
        outer.addWidget(PageTitle('Settings'))
        # The settings cards outgrew the fixed window (upgrade-order/reserve controls) —
        # scroll like the Run page instead of compressing cards into overlap.
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING['md'])
        layout.addWidget(self._build_earthquake_card())
        layout.addWidget(self._build_loot_filter_card())
        layout.addWidget(self._build_clan_card())
        layout.addWidget(self._build_window_card())
        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    
    def _build_earthquake_card(self):
        card = Card()
        card.card_layout.addWidget(SectionTitle('Earthquake placement'))
        self._earthquake = QComboBox()
        self._earthquake.addItems(list(EARTHQUAKE_METHOD_OPTIONS))
        card.card_layout.addWidget(self._earthquake)
        card.card_layout.addWidget(SectionTitle('Wall upgrade threshold'))
        wall_hint = QLabel('With "Upgrade walls" on, only spend time on a wall pass once gold or elixir reaches this amount — a pass opens the builder menu and reads it, which costs far longer than a raid, so it is not worth doing on loot that cannot buy a wall. 0 = only upgrade when storages are full.')
        wall_hint.setWordWrap(True)
        wall_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(wall_hint)
        wall_row = QHBoxLayout()
        self._wall_threshold = QSpinBox()
        self._wall_threshold.setRange(0, WALL_UPGRADE_THRESHOLD_M_MAX)
        self._wall_threshold.setSuffix('M')
        self._wall_threshold.setFixedWidth(88)
        wall_row.addWidget(self._wall_threshold)
        wall_unit = QLabel('gold or elixir')
        wall_unit.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        wall_row.addWidget(wall_unit)
        wall_row.addStretch()
        card.card_layout.addLayout(wall_row)
        card.card_layout.addWidget(SectionTitle('Upgrade order'))
        order_hint = QLabel('Priciest first soaks full storages into the biggest jobs (best when the bot farms loot faster than builders free up). Dark elixir upgrades (heroes) always get first claim either way.')
        order_hint.setWordWrap(True)
        order_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(order_hint)
        self._upgrade_order = QComboBox()
        self._upgrade_order.addItems(['Priciest first', 'Cheapest first'])
        card.card_layout.addWidget(self._upgrade_order)
        card.card_layout.addWidget(SectionTitle('Reserve builders'))
        reserve_hint = QLabel('With Auto upgrade on, keep this many builders free (the wall flow spends through them). Set 0 when your walls are maxed so every builder is used for upgrades.')
        reserve_hint.setWordWrap(True)
        reserve_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(reserve_hint)
        reserve_row = QHBoxLayout()
        self._reserve_builders = QSpinBox()
        self._reserve_builders.setRange(0, RESERVE_BUILDERS_MAX)
        self._reserve_builders.setFixedWidth(88)
        reserve_row.addWidget(self._reserve_builders)
        reserve_unit = QLabel('builders kept free')
        reserve_unit.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        reserve_row.addWidget(reserve_unit)
        reserve_row.addStretch()
        card.card_layout.addLayout(reserve_row)
        btn_row = QHBoxLayout()
        self._btn_save = primary_button('Save', parent = card)
        self._btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self._btn_save)
        self._btn_reset = neutral_button('Reset', parent = card)
        self._btn_reset.clicked.connect(self._reload_earthquake)
        btn_row.addWidget(self._btn_reset)
        btn_row.addStretch()
        card.card_layout.addLayout(btn_row)
        return card

    
    def _build_loot_filter_card(self):
        '''Minimum loot a base must hold before the bot spends an army on it.'''
        card = Card()
        card.card_layout.addWidget(SectionTitle('Minimum loot to attack'))
        hint = QLabel('On the battle-prep screen the bot reads how much loot the base holds and presses Next while it is under these amounts, so an army is never spent on a 40k base. Needs OCR (Tesseract) and a captured nextbase.png — without either it attacks every base and says so in the log. Set both to 0 to attack anything.')
        hint.setWordWrap(True)
        hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(hint)
        gold_row = QHBoxLayout()
        self._attack_min_gold = QSpinBox()
        self._attack_min_gold.setRange(0, ATTACK_MIN_LOOT_K_MAX)
        self._attack_min_gold.setSingleStep(50)
        self._attack_min_gold.setSuffix('k')
        self._attack_min_gold.setSpecialValueText('any')
        self._attack_min_gold.setFixedWidth(88)
        gold_row.addWidget(self._attack_min_gold)
        gold_unit = QLabel('gold')
        gold_unit.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        gold_row.addWidget(gold_unit)
        gold_row.addStretch()
        card.card_layout.addLayout(gold_row)
        elixir_row = QHBoxLayout()
        self._attack_min_elixir = QSpinBox()
        self._attack_min_elixir.setRange(0, ATTACK_MIN_LOOT_K_MAX)
        self._attack_min_elixir.setSingleStep(50)
        self._attack_min_elixir.setSuffix('k')
        self._attack_min_elixir.setSpecialValueText('any')
        self._attack_min_elixir.setFixedWidth(88)
        elixir_row.addWidget(self._attack_min_elixir)
        elixir_unit = QLabel('elixir')
        elixir_unit.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        elixir_row.addWidget(elixir_unit)
        elixir_row.addStretch()
        card.card_layout.addLayout(elixir_row)
        skips_row = QHBoxLayout()
        self._attack_max_skips = QSpinBox()
        self._attack_max_skips.setRange(0, ATTACK_MAX_SKIPS_MAX)
        self._attack_max_skips.setFixedWidth(88)
        skips_row.addWidget(self._attack_max_skips)
        skips_unit = QLabel('bases skipped per raid at most — every Next costs another search fee')
        skips_unit.setWordWrap(True)
        skips_unit.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        skips_row.addWidget(skips_unit)
        skips_row.addStretch()
        card.card_layout.addLayout(skips_row)
        btn_row = QHBoxLayout()
        btn_save = primary_button('Save', parent = card)
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)
        btn_reset = neutral_button('Reset', parent = card)
        btn_reset.clicked.connect(self._reload_earthquake)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()
        card.card_layout.addLayout(btn_row)
        return card


    def _build_clan_card(self):
        '''Clan assist preferences. The toggles that arm it live on the Run page, next
        to the other per-run modes; what lives here is the calibration and the knobs
        that rarely change.'''
        card = Card()
        card.card_layout.addWidget(SectionTitle('Clan assist'))
        hint = QLabel('Donates to your clanmates\' requests and asks for reinforcements between raids. Turn it on per run with "Auto donate" / "Auto request troops" on the Run page.')
        hint.setWordWrap(True)
        hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(hint)
        card.card_layout.addWidget(SectionTitle('Templates'))
        tpl_hint = QLabel('Clan assist clicks buttons it matches from the game art, and a full set of crops ships for 16:9 clients — so there is usually nothing to do here. Capture your own if you play at 16:10, if a bundled template does not match your client, or to add a troop you want to donate: open the game on the screen that shows the button, drag a box around it, save.')
        tpl_hint.setWordWrap(True)
        tpl_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(tpl_hint)
        tpl_row = QHBoxLayout()
        self._clan_templates_label = QLabel('')
        self._clan_templates_label.setWordWrap(True)
        self._clan_templates_label.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        tpl_row.addWidget(self._clan_templates_label, stretch = 1)
        btn_capture = primary_button('Capture templates', parent = card)
        btn_capture.clicked.connect(self._on_capture_templates)
        tpl_row.addWidget(btn_capture)
        card.card_layout.addLayout(tpl_row)
        card.card_layout.addWidget(SectionTitle('Chat button (fallback)'))
        chat_hint = QLabel('Only used when the clan menu button cannot be matched — a hand-picked spot to click instead. Open the game, click "Pick chat button", then click the clan menu button in the screenshot. Re-pick it if you switch the game between 16:9 and 16:10.')
        chat_hint.setWordWrap(True)
        chat_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(chat_hint)
        chat_row = QHBoxLayout()
        self._clan_chat_label = QLabel('Not set')
        self._clan_chat_label.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        chat_row.addWidget(self._clan_chat_label)
        chat_row.addStretch()
        btn_pick = neutral_button('Pick chat button', parent = card)
        btn_pick.clicked.connect(self._on_pick_chat_button)
        chat_row.addWidget(btn_pick)
        btn_clear = neutral_button('Clear', parent = card)
        btn_clear.clicked.connect(self._on_clear_chat_button)
        chat_row.addWidget(btn_clear)
        card.card_layout.addLayout(chat_row)
        self._clan_dry_run = ToggleSwitch('Dry run — read the chat and log what it would click', parent = card)
        card.card_layout.addWidget(self._clan_dry_run)
        dry_hint = QLabel('Leave this on for the first run after calibrating: the bot opens the chat, logs the Donate / Request buttons it recognises, and clicks nothing. Check the Logs page, then turn it off.')
        dry_hint.setWordWrap(True)
        dry_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(dry_hint)
        card.card_layout.addWidget(SectionTitle('Donate troop'))
        troop_hint = QLabel('Whatever you captured as donatetroop*.png — every one of them is tried in the donate panel, so capture a crop for each troop you are happy to give away. The list below is only a fallback for when none has been captured: it reuses the deploy-bar icons, which are the same troops drawn on a different background and match less reliably.')
        troop_hint.setWordWrap(True)
        troop_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(troop_hint)
        self._clan_troop = QComboBox()
        self._clan_troop.addItems([label for (label, _key) in CLAN_TROOP_CHOICES])
        self._clan_troop.currentIndexChanged.connect((lambda _idx: self._update_clan_templates_label()))
        card.card_layout.addWidget(self._clan_troop)
        count_row = QHBoxLayout()
        self._clan_donate_count = QSpinBox()
        self._clan_donate_count.setRange(0, CLAN_DONATE_COUNT_MAX)
        self._clan_donate_count.setSpecialValueText('until grey')
        self._clan_donate_count.setFixedWidth(88)
        count_row.addWidget(self._clan_donate_count)
        count_unit = QLabel('troops per request — "until grey" gives until the game greys the troop out (it is full)')
        count_unit.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        count_row.addWidget(count_unit)
        count_row.addStretch()
        card.card_layout.addLayout(count_row)
        card.card_layout.addWidget(SectionTitle('Elixir floor'))
        elixir_hint = QLabel('Only donate while the HUD shows at least this much elixir, so giving troops away never eats the loot you are farming for. Read before the clan menu opens; needs OCR (Tesseract) — without it the check is skipped and logged. 0 turns it off.')
        elixir_hint.setWordWrap(True)
        elixir_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(elixir_hint)
        elixir_row = QHBoxLayout()
        self._clan_min_elixir = QSpinBox()
        self._clan_min_elixir.setRange(0, CLAN_MIN_ELIXIR_K_MAX)
        self._clan_min_elixir.setSingleStep(50)
        self._clan_min_elixir.setSuffix('k')
        self._clan_min_elixir.setSpecialValueText('off')
        self._clan_min_elixir.setFixedWidth(88)
        elixir_row.addWidget(self._clan_min_elixir)
        elixir_unit = QLabel('elixir needed before donating')
        elixir_unit.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        elixir_row.addWidget(elixir_unit)
        elixir_row.addStretch()
        card.card_layout.addLayout(elixir_row)
        card.card_layout.addWidget(SectionTitle('How often'))
        interval_hint = QLabel('Checked between raids, from the home screen. Requests are asked for far less often than clanmates ask you — the castle only holds so much.')
        interval_hint.setWordWrap(True)
        interval_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(interval_hint)
        donate_row = QHBoxLayout()
        self._clan_donate_interval = QSpinBox()
        self._clan_donate_interval.setRange(CLAN_DONATE_INTERVAL_M_MIN, CLAN_DONATE_INTERVAL_M_MAX)
        self._clan_donate_interval.setSuffix('m')
        self._clan_donate_interval.setSpecialValueText('every raid')
        self._clan_donate_interval.setFixedWidth(88)
        donate_row.addWidget(self._clan_donate_interval)
        donate_unit = QLabel('between donation visits — "every raid" donates on every trip home')
        donate_unit.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        donate_row.addWidget(donate_unit)
        donate_row.addStretch()
        card.card_layout.addLayout(donate_row)
        request_row = QHBoxLayout()
        self._clan_request_interval = QSpinBox()
        self._clan_request_interval.setRange(CLAN_REQUEST_INTERVAL_M_MIN, CLAN_REQUEST_INTERVAL_M_MAX)
        self._clan_request_interval.setSuffix('m')
        self._clan_request_interval.setFixedWidth(88)
        request_row.addWidget(self._clan_request_interval)
        request_unit = QLabel('between reinforcement requests')
        request_unit.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        request_row.addWidget(request_unit)
        request_row.addStretch()
        card.card_layout.addLayout(request_row)
        btn_row = QHBoxLayout()
        btn_save = primary_button('Save', parent = card)
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)
        btn_reset = neutral_button('Reset', parent = card)
        btn_reset.clicked.connect(self._reload_earthquake)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()
        card.card_layout.addLayout(btn_row)
        return card


    def _on_capture_templates(self):
        TemplateCaptureDialog.open(self.window())
        self._update_clan_templates_label()


    def _update_clan_templates_label(self):
        '''Say plainly which crops are still missing — clan assist does nothing until
        the ones its errands need are on disk.'''
        troop = CLAN_TROOP_CHOICES[max(0, self._clan_troop.currentIndex())][1]
        missing = missing_templates(donate = True, request = True, donate_troop = troop,
                                    have_chat_point = bool(self._clan_chat_point))
        troops = donate_troop_template_names()
        troop_note = f'''{len(troops)} troop(s) captured''' if troops else 'no troop captured'
        if not missing:
            self._clan_templates_label.setText(f'''All templates captured — {troop_note}.''')
            return None
        self._clan_templates_label.setText(f'''Missing: {', '.join(missing)} ({troop_note})''')


    def _update_chat_point_label(self):
        point = self._clan_chat_point
        if not point:
            self._clan_chat_label.setText('Not set — clan assist stays idle until you pick it.')
            return None
        aspect = self._clan_chat_point_aspect
        pretty = {'16_9': '16:9', '16_10': '16:10'}.get(aspect or '', 'unknown aspect')
        self._clan_chat_label.setText(f'''Set: {point[0] * 100:.1f}% x, {point[1] * 100:.1f}% y ({pretty})''')


    def _on_pick_chat_button(self):
        picked = PointPickerDialog.pick(self.window(), title = 'Pick the clan chat button',
                                        hint = 'Click the clan chat button in the screenshot below — the one that opens the chat panel on the left of the game. Press Refresh if the game has moved since this was captured.')
        if picked is None:
            return None
        (fx, fy, aspect) = picked
        self._clan_chat_point = [float(fx), float(fy)]
        self._clan_chat_point_aspect = aspect
        self._update_chat_point_label()
        self._flash_status_bar('Chat button picked — press Save')


    def _on_clear_chat_button(self):
        self._clan_chat_point = None
        self._clan_chat_point_aspect = None
        self._update_chat_point_label()
        self._flash_status_bar('Chat button cleared — press Save')


    def _build_window_card(self):
        card = Card()
        card.card_layout.addWidget(SectionTitle('Game window'))
        hint = QLabel("If the bot can't find Clash of Clans, pick the Google Play Games window below and press Test. Windows with a game surface are listed first.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(hint)
        self._window_list = QListWidget()
        self._window_list.setObjectName('WindowList')
        self._window_list.setMinimumHeight(140)
        self._window_list.currentRowChanged.connect((lambda _: self._update_window_buttons()))
        card.card_layout.addWidget(self._window_list)
        self._window_status = QLabel('')
        self._window_status.setWordWrap(True)
        self._window_status.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(self._window_status)
        row = QHBoxLayout()
        self._btn_refresh = neutral_button('Refresh', parent = card)
        self._btn_refresh.clicked.connect(self._refresh_windows)
        row.addWidget(self._btn_refresh)
        self._btn_test = neutral_button('Test', parent = card)
        self._btn_test.clicked.connect(self._on_test_window)
        row.addWidget(self._btn_test)
        self._btn_info = neutral_button('Info', parent = card)
        self._btn_info.clicked.connect(self._on_window_info)
        row.addWidget(self._btn_info)
        self._btn_use = primary_button('Use this window', parent = card)
        self._btn_use.clicked.connect(self._on_use_window)
        row.addWidget(self._btn_use)
        row.addStretch()
        self._btn_auto = neutral_button('Auto-detect', parent = card)
        self._btn_auto.clicked.connect(self._on_auto_detect)
        row.addWidget(self._btn_auto)
        card.card_layout.addLayout(row)
        disp_hint = QLabel("Ultrawide / 21:9 monitor? Google Play Games locks the game to your display's aspect at launch, so it renders 21:9 (unsupported). Fix: click below to switch to 16:9, FULLY close and reopen Clash, then restore your display — the running game stays 16:9.")
        disp_hint.setWordWrap(True)
        disp_hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        card.card_layout.addWidget(disp_hint)
        disp_row = QHBoxLayout()
        self._btn_disp_169 = neutral_button('Switch display to 16:9', parent = card)
        self._btn_disp_169.clicked.connect(self._on_switch_display_169)
        disp_row.addWidget(self._btn_disp_169)
        self._btn_disp_restore = neutral_button('Restore my display', parent = card)
        self._btn_disp_restore.clicked.connect(self._on_restore_display)
        disp_row.addWidget(self._btn_disp_restore)
        disp_row.addStretch()
        card.card_layout.addLayout(disp_row)
        return card

    
    def showEvent(self, event):
        super().showEvent(event)
        self._reload_earthquake()
        self._refresh_windows()

    
    def _reload_earthquake(self):
        settings = load_profile_settings()
        idx = self._earthquake.findText(settings.earthquake_method)
        if idx >= 0:
            self._earthquake.setCurrentIndex(idx)
        self._wall_threshold.setValue(settings.wall_upgrade_threshold_m)
        self._reserve_builders.setValue(settings.reserve_builders)
        self._upgrade_order.setCurrentIndex(1 if settings.upgrade_order == 'cheapest' else 0)
        self._clan_chat_point = list(settings.clan_chat_point) if settings.clan_chat_point else None
        self._clan_chat_point_aspect = settings.clan_chat_point_aspect
        self._update_chat_point_label()
        self._clan_dry_run.setChecked(bool(settings.clan_dry_run))
        keys = [key for (_label, key) in CLAN_TROOP_CHOICES]
        self._clan_troop.setCurrentIndex(keys.index(settings.clan_donate_troop) if settings.clan_donate_troop in keys else 0)
        self._clan_donate_count.setValue(settings.clan_donate_count)
        self._clan_min_elixir.setValue(settings.clan_min_elixir_k)
        self._clan_donate_interval.setValue(settings.clan_donate_interval_m)
        self._clan_request_interval.setValue(settings.clan_request_interval_m)
        self._update_clan_templates_label()
        self._attack_min_gold.setValue(settings.attack_min_gold_k)
        self._attack_min_elixir.setValue(settings.attack_min_elixir_k)
        self._attack_max_skips.setValue(settings.attack_max_skips)


    def _on_save(self):
        # One profile file: every card's fields go in together, or the ones left out
        # would be written back as defaults.
        save_profile_settings(ProfileSettings(
            earthquake_method = self._earthquake.currentText(),
            wall_upgrade_threshold_m = self._wall_threshold.value(),
            reserve_builders = self._reserve_builders.value(),
            upgrade_order = 'cheapest' if self._upgrade_order.currentIndex() == 1 else 'priciest',
            clan_dry_run = self._clan_dry_run.isChecked(),
            clan_chat_point = self._clan_chat_point,
            clan_chat_point_aspect = self._clan_chat_point_aspect,
            clan_donate_troop = CLAN_TROOP_CHOICES[max(0, self._clan_troop.currentIndex())][1],
            clan_donate_count = self._clan_donate_count.value(),
            clan_min_elixir_k = self._clan_min_elixir.value(),
            clan_donate_interval_m = self._clan_donate_interval.value(),
            clan_request_interval_m = self._clan_request_interval.value(),
            attack_min_gold_k = self._attack_min_gold.value(),
            attack_min_elixir_k = self._attack_min_elixir.value(),
            attack_max_skips = self._attack_max_skips.value()))
        self._flash_status_bar('Saved')

    
    def _selected_candidate(self):
        row = self._window_list.currentRow()
        if 0 <= row and row < len(self._candidates):
            pass
        else:
            return None
        return self._candidates[row]

    
    def _update_window_buttons(self):
        cand = self._selected_candidate()
        has_sel = cand is not None
        self._btn_test.setEnabled(has_sel)
        self._btn_use.setEnabled(has_sel)
        self._btn_info.setEnabled(has_sel)

    
    def _refresh_windows(self):
        
        try:
            self._candidates = WindowService().enumerate_windows()
            saved = load_window_selection()
            self._window_list.clear()
            selected_row = -1
            for i, cand in enumerate(self._candidates):
                item = QListWidgetItem(cand.display_label())
                if not cand.is_game:
                    item.setForeground(self._muted_brush())
                self._window_list.addItem(item)
                if not saved.is_set():
                    continue
                if not cand.title.strip().lower() == saved.title.strip().lower():
                    continue
                if not saved.top_class and cand.top_class == saved.top_class:
                    continue
                selected_row = i
            if selected_row >= 0:
                self._window_list.setCurrentRow(selected_row)
            if not self._candidates:
                self._window_status.setText('No visible windows found. Open the game, then Refresh.')
            elif saved.is_set():
                self._window_status.setText(f'''Pinned window: {saved.title or '(saved)'}''')
            else:
                self._window_status.setText('Using auto-detect.')
            self._update_window_buttons()
            return None
        except Exception:
            exc = None
            logger.warning(f'''Could not enumerate windows: {exc}''')
            self._candidates = []
            exc = None
            del exc

        exc = None
        del exc

    
    def _muted_brush(self):
        return QColor(TOKENS['text_muted'])

    
    @staticmethod
    def _aspect_label(w, h):
        if not w or not h:
            return 'size unavailable'
        aspect = resolve_aspect_key(w, h)
        if aspect is None:
            return f'''{w}x{h} (not ~16:9/16:10)'''
        pretty = '16:9' if aspect == '16_9' else '16:10'
        return f'''{w}x{h} ({pretty})'''

    
    def _on_test_window(self):
        cand = self._selected_candidate()
        if cand is None:
            return None
        if not cand.is_game:
            self._window_status.setText('No Google Play Games surface (CROSVM) under this window — pick the game window.')
            return None
        ws = WindowService()
        surface_size = ws.window_pixel_size(cand.child_hwnd)
        if surface_size is None:
            self._window_status.setText('Could not read the window size. Is the game minimized?')
            return None
        sub_size = None
        
        try:
            for d in ws.enumerate_descendants(cand.top_hwnd):
                if not d.cls.lower() == 'subwin':
                    continue
                sub_size = (d.width, d.height)
                ws.enumerate_descendants(cand.top_hwnd)
            (sw, sh) = surface_size
            lines = [
                f'''Capture target {cand.child_class}: {self._aspect_label(sw, sh)}''']
            if not sub_size is None:
                lines.append(f'''Inner subWin: {self._aspect_label(*sub_size)}''')
            if resolve_aspect_key(sw, sh) is None:
                lines.append('Surface aspect unsupported — try resizing the game window.')
            else:
                lines.append('Surface OK to use.')
            self._window_status.setText('\n'.join(lines))
            return None
        except Exception:
            exc = None
            logger.warning(f'''Could not inspect subWin: {exc}''')
            exc = None
            del exc

        exc = None
        del exc

    
    def _on_switch_display_169(self):
        (ok, size, reason) = self._display.switch_to_16_9()
        if ok and reason == 'already_16_9':
            self._window_status.setText('Your display is already 16:9 — just (re)launch Clash in Google Play Games and it will render 16:9.')
        elif ok:
            self._window_status.setText(f'''Display set to {size[0]}x{size[1]} (16:9). Now FULLY close and reopen Clash in Google Play Games, then click "Restore my display".''')
        elif reason == 'no_16_9_mode':
            self._window_status.setText('Your display driver offers no 16:9 mode — use a 16:9 monitor for the game instead.')
        else:
            self._window_status.setText('Could not switch the display resolution.')
        self._flash_status_bar('Display set to 16:9' if ok else 'Display switch failed')


    def _on_restore_display(self):
        (ok, size, reason) = self._display.restore()
        if ok:
            self._window_status.setText(f'''Display restored to {size[0]}x{size[1]}. If you relaunched Clash while in 16:9 it stays 16:9 — press Test to confirm, then Start.''')
        elif reason == 'nothing_to_restore':
            self._window_status.setText('Nothing to restore — you have not switched your display (or it was already restored).')
        else:
            self._window_status.setText('Could not restore the display. Use Windows Settings → Display to set it back.')
        self._flash_status_bar('Display restored' if ok else 'Restore failed')


    def _on_window_info(self):
        cand = self._selected_candidate()
        if cand is None:
            return None

        try:
            descendants = WindowService().enumerate_descendants(cand.top_hwnd)
            WindowInfoDialog(self.window(), cand, descendants).exec()
            return None
        except Exception:
            exc = None
            logger.warning(f'''Could not enumerate descendants: {exc}''')
            descendants = []
            exc = None
            del exc

        exc = None
        del exc

    
    def _on_use_window(self):
        cand = self._selected_candidate()
        if cand is None:
            return None
        save_window_selection(cand.to_selection())
        self._window_status.setText(f'''Pinned: {cand.title or '(no title)'}. Press Test to verify.''')
        self._flash_status_bar('Window saved')

    
    def _on_auto_detect(self):
        clear_window_selection()
        self._window_status.setText('Cleared — using auto-detect.')
        self._flash_status_bar('Auto-detect')
        self._refresh_windows()

    
    def _flash_status_bar(self, msg):
        win = self.window()
        if isinstance(win, QMainWindow):
            if not win.statusBar() is None:
                win.statusBar().showMessage(msg, 1500)
                return None
            return None


