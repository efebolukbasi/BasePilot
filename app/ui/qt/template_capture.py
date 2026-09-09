'''Capture template crops from the live game window.

Template matching only works when the crop is authored at the *reference* resolution
the rest of the templates use (2560x1440 for 16:9, 2560x1600 for 16:10) — a crop taken
from a 1920x1080 window matches nothing once the bot scales it. Doing that by hand is
fiddly and easy to get wrong, so this dialog does it: screenshot the game, drag a box
around a button, pick a name, save. The crop is scaled to the reference size and written
to ``<user data dir>/templates/<aspect>/<name>``, which
:func:`app.utils.common.get_template_path` prefers over the bundled art — so it works
with the released exe too, without rebuilding.
'''
from __future__ import annotations
from typing import Optional

import cv2
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QRubberBand, QVBoxLayout

from app.config import ASPECT_BASELINE, resolve_aspect_key
from app.core.clan import CLAN_TEMPLATE_SPECS
from app.core.loot_filter import LOOT_TEMPLATE_SPECS
from app.services.window import WindowService
from app.ui.qt.theme import SPACING, TOKENS
from app.ui.qt.widgets import neutral_button, primary_button
from app.utils.common import ensure_dir, get_user_app_data_dir
from app.utils.logger import setup_logger

logger = setup_logger('TemplateCapture')

_MAX_PREVIEW_WIDTH = 1000
_MAX_PREVIEW_HEIGHT = 560
_MIN_CROP_PX = 8  # a stray click is not a selection
# Donating wants a crop per troop, so the dialog hands out the next free
# donatetroop / donatetroop2 / donatetroop3 … slot instead of asking for a filename.
_TROOP_SLOT = '__next_troop__'


class _SelectableImage(QLabel):
    '''Preview with a drag-to-select rubber band. Selection is in label pixels.'''

    def __init__(self, on_select, parent = None):
        super().__init__(parent)
        self._on_select = on_select
        self._origin = None
        self._band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.selection = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    def clear_selection(self):
        self.selection = None
        self._band.hide()

    def mousePressEvent(self, event):
        self._origin = event.position().toPoint()
        self._band.setGeometry(QRect(self._origin, QSize()))
        self._band.show()

    def mouseMoveEvent(self, event):
        if self._origin is None:
            return None
        self._band.setGeometry(QRect(self._origin, event.position().toPoint()).normalized())

    def mouseReleaseEvent(self, event):
        if self._origin is None:
            return None
        rect = QRect(self._origin, event.position().toPoint()).normalized()
        self._origin = None
        self.selection = rect
        self._on_select(rect)


class TemplateCaptureDialog(QDialog):
    '''Crop a button out of the live game and save it as a template.'''

    def __init__(self, parent = None):
        super().__init__(parent)
        self.setWindowTitle('Capture templates')
        self.setModal(True)
        self._frame = None
        self._aspect_key = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING['lg'], SPACING['lg'], SPACING['lg'], SPACING['lg'])
        layout.setSpacing(SPACING['sm'])
        hint = QLabel('Open the game on the screen that shows the button you want, press Refresh, then drag a box around the button — tight to its edges, no background. Pick what it is and press Save.')
        hint.setWordWrap(True)
        hint.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        layout.addWidget(hint)
        self._image = _SelectableImage(self._on_select, parent = self)
        layout.addWidget(self._image)
        pick_row = QHBoxLayout()
        self._name = QComboBox()
        self._name.setEditable(True)
        for (filename, label, _required) in CLAN_TEMPLATE_SPECS + LOOT_TEMPLATE_SPECS:
            self._name.addItem(f'''{label}  ({filename})''', filename)
        self._name.addItem('Another troop to donate  (next free slot)', _TROOP_SLOT)
        pick_row.addWidget(self._name, stretch = 1)
        btn_save = primary_button('Save', parent = self)
        btn_save.clicked.connect(self._on_save)
        pick_row.addWidget(btn_save)
        layout.addLayout(pick_row)
        self._status = QLabel('')
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        layout.addWidget(self._status)
        row = QHBoxLayout()
        btn_refresh = neutral_button('Refresh', parent = self)
        btn_refresh.clicked.connect(self._refresh)
        row.addWidget(btn_refresh)
        row.addStretch()
        btn_close = neutral_button('Close', parent = self)
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        layout.addLayout(row)
        self._refresh()

    # ------------------------------------------------------------------ capture

    def _refresh(self):
        self._image.clear_selection()
        try:
            frame = WindowService().screenshot()
        except Exception:
            logger.warning('Template capture: screenshot failed', exc_info = True)
            frame = None
        self._frame = frame
        if frame is None or not getattr(frame, 'size', 0):
            self._image.setPixmap(QPixmap())
            self._status.setText('Could not capture the game window. Open Clash of Clans (and check Settings → Game window), then press Refresh.')
            return None
        (h, w) = frame.shape[:2]
        self._aspect_key = resolve_aspect_key(w, h)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        # .copy() detaches the QImage from the numpy buffer Qt would otherwise keep.
        image = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image).scaled(_MAX_PREVIEW_WIDTH, _MAX_PREVIEW_HEIGHT,
                                                 Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation)
        self._image.setPixmap(pixmap)
        self._image.setFixedSize(pixmap.size())
        aspect = self._aspect_key.replace('_', ':') if self._aspect_key else 'unsupported aspect'
        self._status.setText(f'''Capture {w}x{h} ({aspect}). Drag a box around a button.''')

    def _on_select(self, rect):
        pixmap = self._image.pixmap()
        if self._frame is None or pixmap is None or pixmap.width() <= 0:
            return None
        scale = self._frame.shape[1] / pixmap.width()
        self._status.setText(f'''Selected {int(rect.width() * scale)}x{int(rect.height() * scale)} px of the capture. Pick what it is, then Save.''')

    # --------------------------------------------------------------------- save

    def _selected_crop(self):
        '''The selection as a BGR crop of the full-resolution frame, or None.'''
        rect = self._image.selection
        pixmap = self._image.pixmap()
        if self._frame is None or rect is None or pixmap is None or pixmap.width() <= 0:
            return None
        (fh, fw) = self._frame.shape[:2]
        scale = fw / pixmap.width()
        x0 = max(0, min(fw - 1, int(round(rect.left() * scale))))
        y0 = max(0, min(fh - 1, int(round(rect.top() * scale))))
        x1 = max(0, min(fw, int(round(rect.right() * scale))))
        y1 = max(0, min(fh, int(round(rect.bottom() * scale))))
        if x1 - x0 < _MIN_CROP_PX or y1 - y0 < _MIN_CROP_PX:
            return None
        return self._frame[y0:y1, x0:x1]

    def _next_troop_slot(self):
        '''``donatetroop.png``, else the first free ``donatetroopN.png`` — so capturing
        a second, third, fourth troop is just Save, Save, Save.'''
        folder = get_user_app_data_dir() / 'templates' / (self._aspect_key or '')
        if not (folder / 'donatetroop.png').is_file():
            return 'donatetroop.png'
        n = 2
        while (folder / f'''donatetroop{n}.png''').is_file():
            n += 1
        return f'''donatetroop{n}.png'''

    def _on_save(self):
        crop = self._selected_crop()
        if crop is None:
            self._status.setText('Drag a box around the button first (a click alone is too small).')
            return None
        if self._aspect_key not in ASPECT_BASELINE:
            self._status.setText('The game is not rendering at ~16:9 or ~16:10, so a template cropped from it would never match. Fix the window first (Settings → Game window).')
            return None
        name = self._name.currentData()
        if name == _TROOP_SLOT:
            name = self._next_troop_slot()
        if not name:
            name = self._name.currentText().strip()
            if not name.lower().endswith('.png'):
                name = f'''{name}.png'''
        # Author at the reference resolution: find_template scales templates by
        # capture/ref, so a crop taken at any other size matches nothing.
        (fh, fw) = self._frame.shape[:2]
        (ref_w, ref_h) = ASPECT_BASELINE[self._aspect_key]
        (ch, cw) = crop.shape[:2]
        out_w = max(1, int(round(cw * ref_w / fw)))
        out_h = max(1, int(round(ch * ref_h / fh)))
        interp = cv2.INTER_AREA if (out_w < cw or out_h < ch) else cv2.INTER_CUBIC
        scaled = cv2.resize(crop, (out_w, out_h), interpolation = interp)
        dest_dir = get_user_app_data_dir() / 'templates' / self._aspect_key
        ensure_dir(dest_dir)
        dest = dest_dir / name
        try:
            (ok, buf) = cv2.imencode('.png', scaled)
            if not ok:
                raise RuntimeError('PNG encode failed')
            dest.write_bytes(buf.tobytes())
        except Exception as exc:
            logger.warning('Template capture: could not save %s', dest, exc_info = True)
            self._status.setText(f'''Could not save {dest}: {exc}''')
            return None
        logger.info('Template capture: saved %s (%dx%d at %s reference)', dest, out_w, out_h, self._aspect_key)
        self._status.setText(f'''Saved {name} ({out_w}x{out_h}) to {dest_dir}. Capture the next one, or Close.''')
        self._image.clear_selection()

    @staticmethod
    def open(parent):
        TemplateCaptureDialog(parent).exec()
