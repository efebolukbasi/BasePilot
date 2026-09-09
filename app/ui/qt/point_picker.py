'''Screenshot point picker — calibrate a click target that vision cannot find.

Almost everything BasePilot clicks is found on screen: a template, an OCR'd word,
a colour. The clan chat button is neither — it is a bare icon in a corner whose
position moves with the game's own layout. Rather than ship a guessed coordinate,
this dialog shows the live capture and lets the user point at the button once.

The pick is stored as a **fraction of the capture** (see
:mod:`app.utils.profile_settings_store`), so it survives window resizes; the
aspect key it was taken on is returned alongside so a later 16:9 <-> 16:10 switch
can be flagged instead of silently clicking a stale spot.
'''
from __future__ import annotations
from typing import Optional, Tuple

import cv2
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from app.config import resolve_aspect_key
from app.services.window import WindowService
from app.ui.qt.theme import SPACING, TOKENS
from app.ui.qt.widgets import neutral_button
from app.utils.logger import setup_logger

logger = setup_logger('PointPicker')

_MAX_PREVIEW_WIDTH = 960
_MAX_PREVIEW_HEIGHT = 560


def _frame_to_pixmap(frame):
    '''BGR numpy frame -> QPixmap scaled to fit the preview box.'''
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    (h, w) = rgb.shape[:2]
    # .copy() detaches the QImage from the numpy buffer, which Qt would otherwise
    # keep referencing after this function returns.
    image = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    pixmap = QPixmap.fromImage(image)
    return pixmap.scaled(_MAX_PREVIEW_WIDTH, _MAX_PREVIEW_HEIGHT,
                         Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


class _ClickableImage(QLabel):
    '''The preview. Reports clicks as (fx, fy) fractions of the image.'''

    def __init__(self, on_click, parent = None):
        super().__init__(parent)
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def mousePressEvent(self, event):
        pixmap = self.pixmap()
        if pixmap is None or pixmap.width() <= 0 or pixmap.height() <= 0:
            return None
        pos = event.position()
        fx = min(1.0, max(0.0, pos.x() / pixmap.width()))
        fy = min(1.0, max(0.0, pos.y() / pixmap.height()))
        self._on_click(fx, fy)


class PointPickerDialog(QDialog):
    '''Modal: click the game element, get back its position as fractions.'''

    def __init__(self, parent, title, hint):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.picked: Optional[Tuple[float, float, Optional[str]]] = None
        self._aspect_key = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING['lg'], SPACING['lg'], SPACING['lg'], SPACING['lg'])
        layout.setSpacing(SPACING['sm'])
        hint_label = QLabel(hint)
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        layout.addWidget(hint_label)
        self._image = _ClickableImage(self._on_image_click, parent = self)
        layout.addWidget(self._image)
        self._status = QLabel('')
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f'''color: {TOKENS['text_muted']};''')
        layout.addWidget(self._status)
        row = QHBoxLayout()
        refresh = neutral_button('Refresh', parent = self)
        refresh.clicked.connect(self._refresh)
        row.addWidget(refresh)
        row.addStretch()
        cancel = neutral_button('Cancel', parent = self)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        layout.addLayout(row)
        self._refresh()

    def _refresh(self):
        try:
            frame = WindowService().screenshot()
        except Exception:
            logger.warning('Point picker: screenshot failed', exc_info = True)
            frame = None
        if frame is None or not getattr(frame, 'size', 0):
            self._image.setPixmap(QPixmap())
            self._status.setText('Could not capture the game window. Open Clash of Clans (and check Settings → Game window), then press Refresh.')
            return None
        (h, w) = frame.shape[:2]
        self._aspect_key = resolve_aspect_key(w, h)
        self._image.setPixmap(_frame_to_pixmap(frame))
        aspect = self._aspect_key.replace('_', ':') if self._aspect_key else 'unsupported aspect'
        self._status.setText(f'''Capture {w}x{h} ({aspect}). Click the button in the image.''')

    def _on_image_click(self, fx, fy):
        self.picked = (fx, fy, self._aspect_key)
        self.accept()

    @staticmethod
    def pick(parent, *, title, hint):
        '''Show the picker; returns ``(fx, fy, aspect_key)`` or None if cancelled.'''
        dialog = PointPickerDialog(parent, title, hint)
        if dialog.exec() and dialog.picked is not None:
            return dialog.picked
        return None
