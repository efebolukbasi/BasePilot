import difflib
import math
import re
from dataclasses import dataclass
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from typing import Any, Dict, Optional, Tuple, List, Sequence
from app.config import ASPECT_BASELINE, ASPECT_16_10, ASPECT_16_9, Config, resolve_aspect_key
from app.utils.common import ensure_dir, get_resource_path, get_template_path
from app.utils.logger import setup_logger

try:
    import pytesseract
    logger = setup_logger('VisionService')
    
    @dataclass(frozen = True)
    class OcrWordBox:
        '''A single word bounding box in **screen** coordinates (matches `screen_img`).'''
        # [recovered from bytecode: decompiler collapsed these fields into the docstring]
        left: int
        top: int
        width: int
        height: int
        text: str
        confidence: float

        @property
        def center(self):
            return (self.left + self.width // 2, self.top + self.height // 2)


    _NUMBERS_HUD_ROI_AT_BASELINE: Dict[str, Tuple[int, int]] = {
        ASPECT_16_9: (550, 350),
        ASPECT_16_10: (600, 400) }
    _MULTIUPGRADE_COST_REDNESS_ABOVE_AT_BASELINE: Dict[str, Tuple[int, int, int]] = {
        ASPECT_16_10: (49, 80, 24),
        ASPECT_16_9: (43, 80, 18) }
    _NUMBERS_REF_LINE_HEIGHT_PX = 30
    # Blank between two characters, in median digit widths, that means they belong to
    # different numbers rather than to the same one.
    _NUMBER_RUN_GAP_WIDTHS = 2.0
    HUD_TOP_RIGHT_NUMBERS_ROI_UPSCALE = 3
    
    @dataclass(frozen = True)
    class GroupedNumber:
        '''Merged numeric string on one text line (see :meth:`VisionService.extract_grouped_numbers_in_region`).'''
        # [recovered from bytecode: decompiler collapsed these fields into the docstring]
        text: str
        left: int
        top: int
        width: int
        height: int
        confidence: float

        @property
        def center(self):
            return (self.left + self.width // 2, self.top + self.height // 2)


    
    @dataclass(frozen = True)
    class UpgradeCostIconRedness:
        '''Gold or elixir slot from ``multiupgrade.png`` + redness in the cost box above it.'''
        # [recovered + verified against original bytecode: field names from disassembly]
        template: str
        found: bool
        match_confidence: float
        redness: float
        cost_roi_xywh: Optional[Tuple[int, int, int, int]]
        center: Optional[Tuple[int, int]] = None

    
    @dataclass(frozen = True)
    class UpgradeCostRednessPair:
        '''See :meth:`VisionService.upgrade_cost_redness_by_resource_icons`.'''
        # [recovered from bytecode: decompiler collapsed these fields into the docstring]
        gold: UpgradeCostIconRedness
        elixir: UpgradeCostIconRedness

    BOTTOM_HALF_BOT_TEMPLATES = frozenset({
        'bstar.png',
        'attack.png',
        'bbstar.png',
        'addwall.png',
        'attack2.png',
        'findnow.png',
        'settings.png',
        'endbattle.png',
        'surrender.png',
        'babydragon.png',
        'farmbattle.png',
        'removewall.png',
        'addwallfake.png',
        'upgrademore.png',
        'rankedbattle.png',
        'battlemachine.png',
        'flyingmachine.png',
        'removewallfake.png',
        'rankedattackconfirm.png'})
    TOP_HALF_BOT_TEMPLATES = frozenset({
        'builder.png',
        'gbuilder.png',
        'mbuilder.png',
        'fullecart.png',
        'fullecart2.png',
        'fullecart3.png'})
    _WHITE_TEXT_BRIGHTNESS_FLOOR = 190
    _CC_INK_AREA_AT_BASELINE: Dict[str, Tuple[int, int]] = {
        ASPECT_16_9: (80, 500),
        ASPECT_16_10: (100, 600) }
    _TOP_CENTER_MENU_SQUARE_SIDE_AT_BASELINE: Dict[str, int] = {
        ASPECT_16_9: 1000,
        ASPECT_16_10: 1000 }
    _WALL_MENU_LETTER_CC_AT_BASELINE: Dict[str, Tuple[int, int]] = {
        ASPECT_16_9: (30, 350),
        ASPECT_16_10: (40, 400) }
    
    class VisionService:
        '''Handles image recognition and processing.'''
        _tesseract_missing_logged: bool = False
        
        @staticmethod
        def _log_tesseract_missing():
            '''Log the Tesseract-not-found error once (subsequent OCR calls stay silent).'''
            if VisionService._tesseract_missing_logged:
                return None
            VisionService._tesseract_missing_logged = True
            logger.error('Tesseract executable not found; OCR is disabled and all word/number reads will return empty. Install Tesseract and ensure it is on PATH or bundled (e.g. Windows installer from UB Mannheim). This is logged once per run.')

        
        @staticmethod
        def _template_scale_xy(screen_img):
            '''Scale from authored ref (:attr:`Config.ref_width` / ``ref_height``) to screenshot size.'''
            (h, w) = screen_img.shape[:2]
            cfg = Config()
            return (w / cfg.ref_width, h / cfg.ref_height)

        
        @staticmethod
        def _resize_template_for_screen(template, screen_img):
            '''Resize reference PNG templates to match the current capture.'''
            (sx, sy) = VisionService._template_scale_xy(screen_img)
            if abs(sx - 1) < 0.001 and abs(sy - 1) < 0.001:
                return template
            (t_h, t_w) = template.shape[:2]
            new_w = max(1, int(round(t_w * sx)))
            new_h = max(1, int(round(t_h * sy)))
            interp = cv2.INTER_AREA if min(sx, sy) < 1 else cv2.INTER_CUBIC
            return cv2.resize(template, (new_w, new_h), interpolation = interp)

        
        @staticmethod
        def bottom_half_region(screen_img):
            '''ROI (x, y, w, h) covering the bottom half of the screenshot.'''
            (h, w) = screen_img.shape[:2]
            y0 = h // 2
            return (0, y0, w, h - y0)

        
        @staticmethod
        def top_half_region(screen_img):
            '''ROI (x, y, w, h) covering the top half of the screenshot.'''
            (h, w) = screen_img.shape[:2]
            y1 = h // 2
            return (0, 0, w, y1)

        
        @staticmethod
        def right_half_region(screen_img):
            '''ROI (x, y, w, h) covering the right half of the screenshot.'''
            (h, w) = screen_img.shape[:2]
            x0 = w // 2
            return (x0, 0, w - x0, h)

        
        @staticmethod
        def top_right_quadrant_region(screen_img):
            '''ROI (x, y, w, h) covering the top-right quarter of the screenshot.'''
            (h, w) = screen_img.shape[:2]
            x0 = w // 2
            y1 = h // 2
            return (x0, 0, w - x0, y1)

        
        @staticmethod
        def find_template(screen_img, template_name, threshold = 0.8, region = None, *, scale_template = True):
            '''
Finds a single occurrence of a template in the screen image.
Returns (center_x, center_y) or (None, None).
'''
            
            try:
                template_path = str(get_template_path(template_name))
                template = cv2.imread(template_path)
                if template is None:
                    logger.error(f'''Template not found: {template_path}''')
                    return (None, None)
                if scale_template:
                    template = VisionService._resize_template_for_screen(template, screen_img)
                if region:
                    (x, y, w, h) = region
                    (h_screen, w_screen) = screen_img.shape[:2]
                    if x + w > w_screen or y + h > h_screen:
                        logger.warning(f'''Region {region} out of bounds for image size {w_screen}x{h_screen}''')
                        return (None, None)
                    search_img = screen_img[y:y + h, x:x + w]
                    (offset_x, offset_y) = (x, y)  # [fix: x/y offsets were swapped -> every region search returned wrong coords]
                else:
                    search_img = screen_img
                    (offset_x, offset_y) = (0, 0)
                (t_h, t_w) = template.shape[:2]
                (s_h, s_w) = search_img.shape[:2]
                if t_w > s_w or t_h > s_h:
                    return (None, None)
                result = cv2.matchTemplate(search_img, template, cv2.TM_CCOEFF_NORMED)
                (_, max_val, _, max_loc) = cv2.minMaxLoc(result)
                if max_val < threshold:
                    return (None, None)
                center_x = offset_x + max_loc[0] + t_w // 2
                center_y = offset_y + max_loc[1] + t_h // 2
                return (center_x, center_y)
            except Exception:
                e = None
                logger.error(f'''Error in find_template: {e}''')
                e = None
                del e
                return (None, None)
                e = None
                del e


        
        @staticmethod
        def find_template_with_confidence(screen_img, template_name, threshold = 0, region = None):
            '''
Finds a template and returns (center_x, center_y, confidence).
Confidence is 0.0-1.0 from cv2.matchTemplate TM_CCOEFF_NORMED.
Returns (None, None, confidence) if not found above threshold.
'''
            
            try:
                template_path = str(get_template_path(template_name))
                template = cv2.imread(template_path)
                if template is None:
                    logger.error(f'''Template not found: {template_path}''')
                    return (None, None, 0)
                template = VisionService._resize_template_for_screen(template, screen_img)
                if region:
                    (x, y, w, h) = region
                    (h_screen, w_screen) = screen_img.shape[:2]
                    if x + w > w_screen or y + h > h_screen:
                        return (None, None, 0)
                    search_img = screen_img[y:y + h, x:x + w]
                    (offset_x, offset_y) = (x, y)  # [fix: x/y offsets were swapped -> every region search returned wrong coords]
                else:
                    search_img = screen_img
                    (offset_x, offset_y) = (0, 0)
                (t_h, t_w) = template.shape[:2]
                (s_h, s_w) = search_img.shape[:2]
                if t_w > s_w or t_h > s_h:
                    return (None, None, 0)
                result = cv2.matchTemplate(search_img, template, cv2.TM_CCOEFF_NORMED)
                (_, max_val, _, max_loc) = cv2.minMaxLoc(result)
                if max_val < threshold:
                    return (None, None, float(max_val))
                center_x = offset_x + max_loc[0] + t_w // 2
                center_y = offset_y + max_loc[1] + t_h // 2
                return (center_x, center_y, float(max_val))
            except Exception:
                e = None
                logger.error(f'''Error in find_template_with_confidence: {e}''')
                e = None
                del e
                return (None, None, 0)
                e = None
                del e


        
        @staticmethod
        def find_all_template_centers(screen_img, template_name, threshold = 0.8, region = None, *, max_matches = 12, suppress_pad_frac = 0.5):
            '''All non-overlapping template hits as ``(center_x, center_y)`` centers.'''
            matches = VisionService._find_template_matches(screen_img, template_name, threshold, region, max_matches = max_matches, suppress_pad_frac = suppress_pad_frac)
            return [ (int(left) + int(tw) // 2, int(top) + int(th) // 2) for left, top, tw, th, _score in matches ]
            _score = None
            th = None
            tw = None
            top = None
            left = None

        
        @staticmethod
        def _template_best_match(screen_img, template_name, region = None):
            '''Best ``TM_CCOEFF_NORMED`` score and match center ``(x, y)``, or ``(0.0, None, None)``.'''
            
            try:
                template_path = str(get_template_path(template_name))
                template = cv2.imread(template_path)
                if template is None:
                    logger.error(f'''Template not found: {template_path}''')
                    return (0, None, None)
                template = VisionService._resize_template_for_screen(template, screen_img)
                if region:
                    (x, y, w, h) = region
                    (h_screen, w_screen) = screen_img.shape[:2]
                    if x + w > w_screen or y + h > h_screen:
                        return (0, None, None)
                    search_img = screen_img[y:y + h, x:x + w]
                    (offset_x, offset_y) = (x, y)  # [fix: x/y offsets were swapped -> every region search returned wrong coords]
                else:
                    search_img = screen_img
                    (offset_x, offset_y) = (0, 0)
                (t_h, t_w) = template.shape[:2]
                (s_h, s_w) = search_img.shape[:2]
                if t_w > s_w or t_h > s_h:
                    return (0, None, None)
                result = cv2.matchTemplate(search_img, template, cv2.TM_CCOEFF_NORMED)
                (_, max_val, _, max_loc) = cv2.minMaxLoc(result)
                center_x = offset_x + max_loc[0] + t_w // 2
                center_y = offset_y + max_loc[1] + t_h // 2
                return (float(max_val), center_x, center_y)
            except Exception:
                e = None
                logger.error(f'''Error in _template_best_match ({template_name}): {e}''')
                e = None
                del e
                return (0, None, None)
                e = None
                del e


        
        @staticmethod
        def find_active_over_disabled_template(screen_img, active_template, disabled_template, region = None, threshold = 0.8):
            '''
Match active vs disabled (grayed/red) UI variants of the same control.

Each PNG is matched once via ``minMaxLoc`` (single best peak in ``region``). Among
active and disabled scores that meet ``threshold``, only the **highest** score is
considered; the active center is returned when that winner is the active template,
otherwise ``(None, None)`` (disabled/grayed control).

Falls back to ``find_template`` when the disabled PNG is missing (e.g. 16:10 pack).
'''
            disabled_path = get_template_path(disabled_template)
            if not disabled_path.exists():
                return VisionService.find_template(screen_img, active_template, threshold = threshold, region = region)
            (active_score, ax, ay) = VisionService._template_best_match(screen_img, active_template, region = region)
            (disabled_score, dx, dy) = VisionService._template_best_match(screen_img, disabled_template, region = region)
            best_score = -1
            best_is_active = False
            best_x = None
            best_y = None
            for score, x, y, is_active in ((active_score, ax, ay, True), (disabled_score, dx, dy, False)):
                if not score >= threshold:
                    continue
                if x is None:
                    continue
                if y is None:
                    continue
                if not score > best_score:
                    continue
                best_score = score
                best_is_active = is_active
                (best_x, best_y) = (x, y)  # [recovered: pycdc reversed STORE_FAST_STORE_FAST pairs]
            if not best_is_active or best_x is None or best_y is None:  # [recovered: decompiler dropped the `not` — a disabled control counted as active]
                return (None, None)
            return (best_x, best_y)

        
        @staticmethod
        def lime_fraction(bgr, *, hue_lo = 35, hue_hi = 90, sat_floor = 80, val_floor = 80):
            '''
Fraction of pixels in lime/chartreuse hue (OpenCV H 0–179) with saturation
and value at least ``sat_floor`` / ``val_floor``.
'''
            if bgr is None or bgr.size == 0 or bgr.ndim != 3:
                return 0
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            sf = max(0, min(255, int(sat_floor)))
            vf = max(0, min(255, int(val_floor)))
            mask = cv2.inRange(hsv, (int(hue_lo), sf, vf), (int(hue_hi), 255, 255))
            total = int(bgr.shape[0]) * int(bgr.shape[1])
            if total > 0:
                return float(cv2.countNonZero(mask)) / float(total)
            return 0

        
        @staticmethod
        def find_active_addwall(screen_img, region = None, *, template_threshold = 0.75, lime_threshold = 0.3, max_matches = 32):
            '''
Locate an active (lime green) add-wall control via ``addwall.png`` template
matching plus per-hit lime color verification.

Returns the **rightmost** center among hits that pass both ``template_threshold``
and ``lime_threshold``, or ``(None, None)`` when none qualify.
'''
            matches = VisionService._find_template_matches(screen_img, 'addwall.png', template_threshold, region, max_matches = max_matches)
            if not matches:
                return (None, None)
            (frame_h, frame_w) = screen_img.shape[:2]
            passing = []
            for left, top, tw, th, _score in matches:
                x0 = max(0, min(int(left), frame_w))
                y0 = max(0, min(int(top), frame_h))
                x1 = max(0, min(int(left) + int(tw), frame_w))
                y1 = max(0, min(int(top) + int(th), frame_h))
                crop = screen_img[y0:y1, x0:x1]
                if VisionService.lime_fraction(crop) < lime_threshold:
                    continue
                passing.append((int(left) + int(tw) // 2, int(top) + int(th) // 2))
            if not passing:
                return (None, None)
            return max(passing, key = (lambda pt: pt[0]))

        
        @staticmethod
        def yellow_fraction(bgr, *, hue_lo = 18, hue_hi = 40, sat_floor = 80, val_floor = 80):
            '''
Fraction of pixels in yellow hue (OpenCV H 0–179) with saturation and value
at least ``sat_floor`` / ``val_floor``.
'''
            if bgr is None or bgr.size == 0 or bgr.ndim != 3:
                return 0
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            sf = max(0, min(255, int(sat_floor)))
            vf = max(0, min(255, int(val_floor)))
            mask = cv2.inRange(hsv, (int(hue_lo), sf, vf), (int(hue_hi), 255, 255))
            total = int(bgr.shape[0]) * int(bgr.shape[1])
            if total > 0:
                return float(cv2.countNonZero(mask)) / float(total)
            return 0

        
        @staticmethod
        def pink_fraction(bgr, *, hue_lo = 130, hue_hi = 175, sat_floor = 80, val_floor = 80):
            '''
Fraction of pixels in pink/magenta hue (OpenCV H 0–179) with saturation and value
at least ``sat_floor`` / ``val_floor``.
'''
            if bgr is None or bgr.size == 0 or bgr.ndim != 3:
                return 0
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            sf = max(0, min(255, int(sat_floor)))
            vf = max(0, min(255, int(val_floor)))
            mask = cv2.inRange(hsv, (int(hue_lo), sf, vf), (int(hue_hi), 255, 255))
            total = int(bgr.shape[0]) * int(bgr.shape[1])
            if total > 0:
                return float(cv2.countNonZero(mask)) / float(total)
            return 0

        
        @staticmethod
        def find_active_hgoldfull(screen_img, region = None, *, template_threshold = 0.85, yellow_threshold = 0.5, max_matches = 8):
            '''
Locate a full gold storage hero-bar icon via ``hgoldfull.png`` template matching
plus per-hit yellow color verification.
'''
            matches = VisionService._find_template_matches(screen_img, 'hgoldfull.png', template_threshold, region, max_matches = max_matches)
            if not matches:
                return (None, None)
            (frame_h, frame_w) = screen_img.shape[:2]
            for left, top, tw, th, _score in sorted(matches, key = (lambda m: m[4]), reverse = True):
                x0 = max(0, min(int(left), frame_w))
                y0 = max(0, min(int(top), frame_h))
                x1 = max(0, min(int(left) + int(tw), frame_w))
                y1 = max(0, min(int(top) + int(th), frame_h))
                crop = screen_img[y0:y1, x0:x1]
                if VisionService.yellow_fraction(crop) < yellow_threshold:
                    continue
                sorted(matches, key = (lambda m: m[4]), reverse = True)
                return (int(left) + int(tw) // 2, int(top) + int(th) // 2)
            return (None, None)

        
        @staticmethod
        def find_active_helixirfull(screen_img, region = None, *, template_threshold = 0.85, pink_threshold = 0.5, max_matches = 8):
            '''
Locate a full elixir storage hero-bar icon via ``helixirfull.png`` template matching
plus per-hit pink color verification.
'''
            matches = VisionService._find_template_matches(screen_img, 'helixirfull.png', template_threshold, region, max_matches = max_matches)
            if not matches:
                return (None, None)
            (frame_h, frame_w) = screen_img.shape[:2]
            for left, top, tw, th, _score in sorted(matches, key = (lambda m: m[4]), reverse = True):
                x0 = max(0, min(int(left), frame_w))
                y0 = max(0, min(int(top), frame_h))
                x1 = max(0, min(int(left) + int(tw), frame_w))
                y1 = max(0, min(int(top) + int(th), frame_h))
                crop = screen_img[y0:y1, x0:x1]
                if VisionService.pink_fraction(crop) < pink_threshold:
                    continue
                sorted(matches, key = (lambda m: m[4]), reverse = True)
                return (int(left) + int(tw) // 2, int(top) + int(th) // 2)
            return (None, None)

        
        @staticmethod
        def find_active_removewall(screen_img, region = None, *, template_threshold = 0.75, yellow_threshold = 0.3, max_matches = 32):
            '''
Locate an active (yellow) remove-wall control via ``removewall.png`` template
matching plus per-hit yellow color verification.

Returns the **leftmost** center among hits that pass both ``template_threshold``
and ``yellow_threshold``, or ``(None, None)`` when none qualify.
'''
            matches = VisionService._find_template_matches(screen_img, 'removewall.png', template_threshold, region, max_matches = max_matches)
            if not matches:
                return (None, None)
            (frame_h, frame_w) = screen_img.shape[:2]
            passing = []
            for left, top, tw, th, _score in matches:
                x0 = max(0, min(int(left), frame_w))
                y0 = max(0, min(int(top), frame_h))
                x1 = max(0, min(int(left) + int(tw), frame_w))
                y1 = max(0, min(int(top) + int(th), frame_h))
                crop = screen_img[y0:y1, x0:x1]
                if VisionService.yellow_fraction(crop) < yellow_threshold:
                    continue
                passing.append((int(left) + int(tw) // 2, int(top) + int(th) // 2))
            if not passing:
                return (None, None)
            return min(passing, key = (lambda pt: pt[0]))

        
        @staticmethod
        def scaled_multiupgrade_cost_redness_above(screen_w, screen_h):
            '''
Vertical center-to-center offset (template center → redness ROI center), ROI width,
ROI height — scaled from :data:`_MULTIUPGRADE_COST_REDNESS_ABOVE_AT_BASELINE`
(43 / 80 / 25 @ 2560×1440).
'''
            sw = max(1, int(screen_w))
            sh = max(1, int(screen_h))
            key = resolve_aspect_key(sw, sh)
            if key is None or key not in ASPECT_BASELINE:
                key = ASPECT_16_10
            (ref_w, ref_h) = ASPECT_BASELINE[key]
            (base_offset, base_rw, base_rh) = _MULTIUPGRADE_COST_REDNESS_ABOVE_AT_BASELINE.get(key, _MULTIUPGRADE_COST_REDNESS_ABOVE_AT_BASELINE[ASPECT_16_10])
            center_offset = max(1, int(round(base_offset * sh / float(ref_h))))
            rw = max(1, int(round(base_rw * sw / float(ref_w))))
            rh = max(1, int(round(base_rh * sh / float(ref_h))))
            return (center_offset, rw, rh)

        
        @staticmethod
        def red_hue_fraction(bgr, *, sat_floor = 40, val_floor = 40):
            '''
Fraction of pixels whose hue falls in red/orange-red ranges (OpenCV H 0–179),
with saturation and value at least ``sat_floor`` / ``val_floor``.
'''
            if bgr is None or bgr.size == 0 or bgr.ndim != 3:
                return 0
            hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
            sf = max(0, min(255, int(sat_floor)))
            vf = max(0, min(255, int(val_floor)))
            m1 = cv2.inRange(hsv, (0, sf, vf), (10, 255, 255))
            m2 = cv2.inRange(hsv, (170, sf, vf), (180, 255, 255))
            mask = cv2.bitwise_or(m1, m2)
            total = int(bgr.shape[0]) * int(bgr.shape[1])
            if total > 0:
                return float(cv2.countNonZero(mask)) / float(total)
            return 0

        
        @staticmethod
        def _clip_rect_xywh(x, y, w, h, frame_w, frame_h):
            (x2, y2) = (x + w, y + h)  # [recovered: pycdc reversed STORE_FAST_STORE_FAST pairs]
            x0 = max(0, min(int(x), int(frame_w)))
            y0 = max(0, min(int(y), int(frame_h)))
            x1 = max(0, min(int(x2), int(frame_w)))
            y1 = max(0, min(int(y2), int(frame_h)))
            (rw, rh) = (x1 - x0, y1 - y0)  # [recovered: pycdc reversed STORE_FAST_STORE_FAST pairs]
            if rw <= 0 or rh <= 0:
                return None
            return (x0, y0, rw, rh)

        
        @staticmethod
        def _match_template_top_left(screen_img, template_name, threshold, region = None):
            '''
Best ``cv2.matchTemplate`` match: ``(left, top, t_w, t_h, score)``.
``left`` … ``t_h`` are ``None`` when below ``threshold`` or on error (``score`` still set when matching ran).
'''
            
            try:
                template_path = str(get_template_path(template_name))
                template = cv2.imread(template_path)
                if template is None:
                    logger.error(f'''Template not found: {template_path}''')
                    return (None, None, None, None, 0)
                template = VisionService._resize_template_for_screen(template, screen_img)
                if region:
                    (x, y, w, h) = region
                    (h_screen, w_screen) = screen_img.shape[:2]
                    if x + w > w_screen or y + h > h_screen:
                        return (None, None, None, None, 0)
                    search_img = screen_img[y:y + h, x:x + w]
                    (offset_x, offset_y) = (x, y)  # [fix: x/y offsets were swapped -> every region search returned wrong coords]
                else:
                    search_img = screen_img
                    (offset_x, offset_y) = (0, 0)
                (t_h, t_w) = template.shape[:2]
                (s_h, s_w) = search_img.shape[:2]
                if t_w > s_w or t_h > s_h:
                    return (None, None, None, None, 0)
                result = cv2.matchTemplate(search_img, template, cv2.TM_CCOEFF_NORMED)
                (_, max_val, _, max_loc) = cv2.minMaxLoc(result)
                score = float(max_val)
                if score < threshold:
                    return (None, None, None, None, score)
                left = offset_x + max_loc[0]
                top = offset_y + max_loc[1]
                return (left, top, t_w, t_h, score)
            except Exception:
                e = None
                logger.error(f'''Error in _match_template_top_left: {e}''')
                e = None
                del e
                return (None, None, None, None, 0)
                e = None
                del e


        
        @staticmethod
        def _find_template_matches(screen_img, template_name, threshold, region = None, *, max_matches = 2, suppress_pad_frac = 0.5):
            '''Up to ``max_matches`` non-overlapping ``matchTemplate`` hits: ``(left, top, t_w, t_h, score)``.'''
            
            try:
                template_path = str(get_template_path(template_name))
                template = cv2.imread(template_path)
                if template is None:
                    logger.error(f'''Template not found: {template_path}''')
                    return []
                template = VisionService._resize_template_for_screen(template, screen_img)
                if region:
                    (x, y, w, h) = region
                    (h_screen, w_screen) = screen_img.shape[:2]
                    if x + w > w_screen or y + h > h_screen:
                        return []
                    search_img = screen_img[y:y + h, x:x + w]
                    offset_y = y
                    offset_x = x
                else:
                    search_img = screen_img
                    (offset_x, offset_y) = (0, 0)
                (t_h, t_w) = template.shape[:2]
                (s_h, s_w) = search_img.shape[:2]
                if t_w > s_w or t_h > s_h:
                    return []
                result = cv2.matchTemplate(search_img, template, cv2.TM_CCOEFF_NORMED)
                work = result.copy()
                matches = []
                for _ in range(max(1, int(max_matches))):
                    (_, max_val, _, max_loc) = cv2.minMaxLoc(work)
                    score = float(max_val)
                    if score < threshold:
                        range(max(1, int(max_matches)))
                        return matches
                    left = offset_x + int(max_loc[0])
                    top = offset_y + int(max_loc[1])
                    matches.append((left, top, t_w, t_h, score))
                    pad_x = max(0, int(round(t_w * suppress_pad_frac)))
                    pad_y = max(0, int(round(t_h * suppress_pad_frac)))
                    y0 = max(0, max_loc[1] - pad_y)
                    y1 = min(work.shape[0], max_loc[1] + t_h + pad_y)
                    x0 = max(0, max_loc[0] - pad_x)
                    x1 = min(work.shape[1], max_loc[0] + t_w + pad_x)
                    work[y0:y1, x0:x1] = 0
                return matches
            except Exception:
                e = None
                logger.error(f'''Error in _find_template_matches ({template_name}): {e}''')
                e = None
                del e
                return None
                e = None
                del e


        
        @staticmethod
        def _upgrade_cost_redness_for_template_rect(screen_img, left, top, tw, th, conf, *, center_offset, roi_w, roi_h, frame_w, frame_h):
            '''Measure :meth:`red_hue_fraction` in an ``roi_w×roi_h`` box whose center sits ``center_offset`` px above the template center.'''
            tpl_cx = int(left) + int(tw) // 2
            tpl_cy = int(top) + int(th) // 2
            roi_cx = tpl_cx
            roi_cy = tpl_cy - int(center_offset)
            rx = roi_cx - int(roi_w) // 2
            ry = roi_cy - int(roi_h) // 2
            clipped = VisionService._clip_rect_xywh(rx, ry, roi_w, roi_h, frame_w, frame_h)
            click_cx = tpl_cx
            click_cy = tpl_cy
            if clipped is None:
                return UpgradeCostIconRedness('multiupgrade.png', True, conf, 0, None, (click_cx, click_cy))
            (x0, y0, cw, ch) = clipped
            crop = screen_img[y0:y0 + ch, x0:x0 + cw]
            red = VisionService.red_hue_fraction(crop)
            return UpgradeCostIconRedness('multiupgrade.png', True, conf, red, clipped, (click_cx, click_cy))

        
        @staticmethod
        def upgrade_cost_redness_by_resource_icons(screen_img, *, match_threshold = 0.8, region = None):
            '''
Locate ``multiupgrade.png`` (up to two matches), then measure :meth:`red_hue_fraction`
in an **80×25-at-baseline** box whose center is **43 px above** each template center
(scaled to capture size).

One match covers both gold and elixir slots (left / right halves). Two matches are
treated as separate gold (leftmost) and elixir (rightmost) buttons.

Updates :class:`~app.config.Config` from ``screen_img`` so template paths match aspect.
'''
            miss = UpgradeCostIconRedness('multiupgrade.png', False, 0, 0, None, None)
            if screen_img is None or screen_img.size == 0:
                return UpgradeCostRednessPair(miss, miss)
            cfg = Config()
            cfg.set_target_size_from_frame(screen_img)
            (frame_h, frame_w) = screen_img.shape[:2]
            (center_offset, roi_w, roi_h) = VisionService.scaled_multiupgrade_cost_redness_above(frame_w, frame_h)
            matches = VisionService._find_template_matches(screen_img, 'multiupgrade.png', match_threshold, region, max_matches = 2)
            if not matches:
                return UpgradeCostRednessPair(miss, miss)
            
            def for_rect(left, top, tw, th, conf):
                return VisionService._upgrade_cost_redness_for_template_rect(screen_img, left, top, tw, th, conf, center_offset = center_offset, roi_w = roi_w, roi_h = roi_h, frame_w = frame_w, frame_h = frame_h)

            if len(matches) == 1:
                (left, top, tw, th, conf) = matches[0]
                half = max(1, tw // 2)
                gold = for_rect(left, top, half, th, conf)
                elixir = for_rect(left + half, top, tw - half, th, conf)
            else:
                matches = sorted(matches, key = (lambda m: m[0]))
                gold = for_rect(*matches[0])
                elixir = for_rect(*matches[1])
            return UpgradeCostRednessPair(gold, elixir)

        
        @staticmethod
        def preprocess_bw_ui_text(bgr, *, white_text = False, brightness_floor = None):
            '''
Normalize high-contrast UI text to **black glyphs on white** (single channel, 0 / 255).

``white_text=False`` (default): dark glyphs on a light background (Otsu).
``white_text=True``: light glyphs on dark; only pixels with grayscale >=
``brightness_floor`` (default :data:`_WHITE_TEXT_BRIGHTNESS_FLOOR`) count as ink
(strict, no blur), then mapped to black-on-white.
'''
            if bgr is None or bgr.size == 0:
                return bgr
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            if white_text:
                floor = _WHITE_TEXT_BRIGHTNESS_FLOOR if brightness_floor is None else max(0, min(255, int(brightness_floor)))
                bright = np.where(gray >= floor, np.uint8(255), np.uint8(0))
                binary = cv2.bitwise_not(bright)
                return binary
            (_, binary) = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            return binary

        
        @staticmethod
        def filter_binary_ink_by_component_area(binary, *, min_area = 150, max_area = 800, ink_value = 0, background_value = 255):
            '''
On a single-channel binary image (e.g. from :meth:`preprocess_bw_ui_text`), treat pixels
equal to ``ink_value`` as foreground, run 8-connected labeling, and set any component with
area outside ``[min_area, max_area]`` to ``background_value``.
'''
            if binary is None or binary.size == 0 or binary.ndim != 2:
                return binary
            (lo, hi) = (int(min_area), int(max_area))  # [recovered: pycdc reversed STORE_FAST_STORE_FAST pairs]
            if lo > hi:
                (lo, hi) = (hi, lo)
            ink_mask = np.uint8(np.where(binary == ink_value, 255, 0))
            (num_labels, labels, stats, _) = cv2.connectedComponentsWithStats(ink_mask, connectivity = 8)
            out = np.asarray(binary, dtype = np.uint8, order = 'C').copy()
            bg = np.uint8(background_value)
            for i in range(1, int(num_labels)):
                area = int(stats[i, cv2.CC_STAT_AREA])
                if lo <= area <= hi:  # [recovered: mangled condition blanked exactly the in-range glyphs]
                    continue
                out[labels == i] = bg
            return out

        
        @staticmethod
        def scaled_cc_ink_bounds(screen_w, screen_h, *, aspect_key = None):
            '''
``(min_area, max_area)`` for :meth:`filter_binary_ink_by_component_area`, scaled from the
aspect baseline: **16:10** → 100 / 600 @ 2560×1600; **16:9** → 80 / 500 @ 2560×1440.

Scales by ``(screen_w / ref_w) * (screen_h / ref_h)`` (blob pixel area vs capture size).
'''
            key = aspect_key or resolve_aspect_key(int(screen_w), int(screen_h))
            if key is None or key not in ASPECT_BASELINE:
                key = ASPECT_16_10
            (base_min, base_max) = _CC_INK_AREA_AT_BASELINE.get(key, _CC_INK_AREA_AT_BASELINE[ASPECT_16_10])
            (ref_w, ref_h) = ASPECT_BASELINE[key]
            sw = max(1, int(screen_w))
            sh = max(1, int(screen_h))
            scale = (sw / float(ref_w)) * (sh / float(ref_h))
            return (max(1, int(round(base_min * scale))), max(1, int(round(base_max * scale))))

        
        @staticmethod
        def scaled_top_center_menu_side(screen_w, screen_h, *, aspect_key = None):
            '''
Square side length (px) for :meth:`top_middle_square_roi` / builder-menu OCR, scaled from
the aspect baseline by **width** (``1000`` @ 2560 for both 16:10 and 16:9).
'''
            key = aspect_key or resolve_aspect_key(int(screen_w), int(screen_h))
            if key is None or key not in ASPECT_BASELINE:
                key = ASPECT_16_10
            base_side = int(_TOP_CENTER_MENU_SQUARE_SIDE_AT_BASELINE.get(key, 1000))
            (ref_w, _ref_h) = ASPECT_BASELINE[key]
            sw = max(1, int(screen_w))
            return max(1, int(round(base_side * sw / float(ref_w))))

        
        @staticmethod
        def scaled_wall_menu_cc_ink_bounds(screen_w, screen_h, *, aspect_key = None):
            '''
``(min_area, max_area)`` for wall / builder-menu letter OCR — **16:10** → 40 / 400 @ 2560×1600;
**16:9** → 35 / 350 @ 2560×1440. Scales by ``(screen_w / ref_w) * (screen_h / ref_h)``.
'''
            key = aspect_key or resolve_aspect_key(int(screen_w), int(screen_h))
            if key is None or key not in ASPECT_BASELINE:
                key = ASPECT_16_10
            (base_min, base_max) = _WALL_MENU_LETTER_CC_AT_BASELINE.get(key, _WALL_MENU_LETTER_CC_AT_BASELINE[ASPECT_16_10])
            (ref_w, ref_h) = ASPECT_BASELINE[key]
            sw = max(1, int(screen_w))
            sh = max(1, int(screen_h))
            scale = (sw / float(ref_w)) * (sh / float(ref_h))
            return (max(1, int(round(base_min * scale))), max(1, int(round(base_max * scale))))

        
        @staticmethod
        def _ocr_query_matches(raw, query_norm, *, case_sensitive, match_alnum_only, fuzzy_min_ratio):
            if not query_norm:
                return False
            
            def norm_text(s):
                s = s.strip()
                if case_sensitive:
                    return s
                return s.lower()

            r = norm_text(raw)
            if query_norm in r:
                return True
            if match_alnum_only:
                ar = re.sub('[^a-z0-9]', '', r.lower())
                aq = re.sub('[^a-z0-9]', '', query_norm.lower())
                if aq and aq in ar:
                    return True
                if aq and ar and ar in aq and len(ar) >= max(4, int(0.55 * len(aq))):
                    return True
            if not fuzzy_min_ratio is None:
                if match_alnum_only:
                    pass
                elif case_sensitive:
                    pass
                
                cr = r.lower()
                cq = re.sub('[^a-z0-9]', '', query_norm.lower()) if match_alnum_only else query_norm
                if len(cq) < 3:
                    return False
                (lo, hi) = sorted((len(cr), len(cq)))
                if hi and lo / hi < 0.5:
                    return False
                ratio = difflib.SequenceMatcher(None, cq, cr).ratio()
                if ratio >= fuzzy_min_ratio:
                    return True
            return False

        
        @staticmethod
        def _ocr_username_match_tier(raw, query_norm, *, case_sensitive, match_alnum_only, fuzzy_min_ratio):
            '''
Rank how well ``raw`` matches the username (higher is better).
Used to pick a click target: prefer full name in OCR, then longest token, then confidence.
'''
            if not query_norm:
                return 0
            
            def norm_text(s):
                s = s.strip()
                if case_sensitive:
                    return s
                return s.lower()

            r = norm_text(raw)
            ar = re.sub('[^a-z0-9]', '', r.lower()) if match_alnum_only else ''
            aq = re.sub('[^a-z0-9]', '', query_norm.lower()) if match_alnum_only else ''
            if match_alnum_only and aq and aq in ar:
                return 3
            if query_norm in r:
                return 2
            if match_alnum_only and aq and ar and ar in aq and len(ar) >= max(4, int(0.55 * len(aq))):
                return 1
            return 0

        
        @staticmethod
        def save_hud_ocr_debug_outputs(pil_mono, mono_gray, tess_data, *, tesseract_config, tsv_path, boxes_png_path):
            '''
Write Tesseract ``image_to_tsv`` and a word-level bounding-box overlay (mono / OCR resolution).
'''
            if tsv_path is None or boxes_png_path is None or pytesseract is None:
                return None
            if not tsv_path is None:
                
                ensure_dir(tsv_path.parent)
                tsv_raw = pytesseract.image_to_tsv(pil_mono, config = tesseract_config)
                tsv_path.write_text(tsv_raw, encoding = 'utf-8')
                while not boxes_png_path is None:
                    
                    try:
                        ensure_dir(boxes_png_path.parent)
                        if mono_gray.ndim == 2:
                            vis = cv2.cvtColor(mono_gray, cv2.COLOR_GRAY2BGR)
                        else:
                            vis = mono_gray.copy()
                        texts = tess_data.get('text', [])
                        n = len(texts)
                        levels = tess_data.get('level')
                        if levels or len(levels) < n:
                            levels = [
                                5] * n
                        for i in range(n):
                            lv = int(levels[i])
                            left = int(tess_data['left'][i])
                            top = int(tess_data['top'][i])
                            ww = int(tess_data['width'][i])
                            hh = int(tess_data['height'][i])
                            if ww <= 0 or hh <= 0:
                                continue
                            conf = int(tess_data['conf'][i])
                            if conf >= 60:
                                color = (0, 220, 100)
                            elif conf >= 30:
                                color = (0, 200, 255)
                            else:
                                color = (60, 60, 255)
                            cv2.rectangle(vis, (left, top), (left + ww - 1, top + hh - 1), color, 1)
                            raw = (texts[i] or '').strip()
                            if not raw:
                                continue
                            label = raw[:24] if len(raw) <= 24 else raw[:21] + '...'
                            cv2.putText(vis, label, (left, max(0, top - 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
                        cv2.imwrite(str(boxes_png_path), vis)
                        return None
                        
                        try:
                            return None
                        except Exception:
                            exc = None
                            logger.warning('Could not write OCR debug TSV %s: %s', tsv_path, exc)
                            exc = None
                            del exc

                        exc = None
                        
                        try:
                            
                            try:
                                del exc
                            except (TypeError, ValueError):
                                lv = 5

                        except (TypeError, ValueError):
                            conf = -1

                    except Exception:
                        exc = None
                        logger.warning('Could not write OCR debug boxes PNG %s: %s', boxes_png_path, exc)
                        exc = None
                        del exc
                        return None
                        exc = None
                        del exc



        
        @staticmethod
        def _preprocess_ocr_region(screen_img, region, *, preprocess, white_text, brightness_floor, cc_filter_blobs, cc_min_area, cc_max_area, roi_upscale, save_preprocess_png):
            '''
Shared OCR preprocessing: ROI crop → optional BGR upscale → grayscale via
:meth:`preprocess_bw_ui_text` (when ``preprocess``) → optional
:meth:`filter_binary_ink_by_component_area` (defaults from :meth:`scaled_cc_ink_bounds`)
→ optional ``save_preprocess_png`` of the binary.

Returns ``(pil_mono, mono_np, coord_scale, offset_x, offset_y)`` for the OCR call,
or ``None`` if the input is empty / region is out of bounds.
``coord_scale`` is the BGR upscale factor (1.0 when no upscale); callers map OCR
pixel coords back to full-frame using ``inv = 1.0 / coord_scale`` and the offsets.
'''
            if screen_img is None or screen_img.size == 0:
                return None
            (full_h, full_w) = screen_img.shape[:2]
            coord_scale = 1
            (offset_x, offset_y) = (0, 0)
            work = screen_img
            if not region is None:
                (rx, ry, rw, rh) = region
                (h_s, w_s) = screen_img.shape[:2]
                if rx < 0 or ry < 0 or rx + rw > w_s or ry + rh > h_s:
                    logger.warning(f'''OCR region {region} out of bounds for image {w_s}x{h_s}''')
                    return None
                work = screen_img[ry:ry + rh, rx:rx + rw]
                (offset_x, offset_y) = (rx, ry)  # [fix: x/y offsets were swapped]
                us = float(roi_upscale)
                if us > 1:
                    nh = max(1, int(round(work.shape[0] * us)))
                    nw = max(1, int(round(work.shape[1] * us)))
                    work = cv2.resize(work, (nw, nh), interpolation = cv2.INTER_LINEAR)
                    coord_scale = us
            if preprocess:
                mono = VisionService.preprocess_bw_ui_text(work, white_text = white_text, brightness_floor = brightness_floor)
                if cc_filter_blobs:
                    c_hi = cc_max_area
                    c_lo = cc_min_area
                    if c_lo is None or c_hi is None:
                        (a_lo, a_hi) = VisionService.scaled_cc_ink_bounds(full_w, full_h)
                        if c_lo is None:
                            c_lo = a_lo
                        if c_hi is None:
                            c_hi = a_hi
                    cc_am = coord_scale * coord_scale
                    if cc_am > 1.0001:
                        c_lo = int(round(float(c_lo) * cc_am))
                        c_hi = int(round(float(c_hi) * cc_am))
                    mono = VisionService.filter_binary_ink_by_component_area(mono, min_area = int(c_lo), max_area = int(c_hi))
            else:  # [recovered: this else belongs to `if preprocess`; and the return below was mis-nested inside the save block -> function returned None in normal use, killing ALL OCR]
                mono = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
            if not save_preprocess_png is None:

                try:
                    ensure_dir(save_preprocess_png.parent)
                    cv2.imwrite(str(save_preprocess_png), mono)
                except Exception:
                    logger.warning('Could not save OCR preprocess debug PNG')
            pil = Image.fromarray(mono)
            return (pil, mono, coord_scale, offset_x, offset_y)


        @staticmethod
        def _tesseract_single_glyph_confidence_psm10(mono_gray, left, top, right, bottom, *, expected_char = '', pad_px = 2):
            '''
Run a cheap **single-character** Tesseract pass on a mono crop (``--psm 10``) and return
the strongest reported word-level confidence (0–100) among recognized tokens, or
:data:`math.nan` if none.

**Note:** Uses **no** character whitelist — a strict whitelist often yields empty output on
very narrow glyphs (common for ``image_to_boxes``), which would falsely read as NaN.

``image_to_boxes`` does not emit confidences; this auxiliary pass is optional for HUD debug.
'''
            if pytesseract is None or mono_gray.size == 0:
                return math.nan
            (h, w) = mono_gray.shape[:2]
            pad = max(int(pad_px), 1)
            (t0, t1) = (max(0, top - pad), min(h, bottom + pad))  # [recovered: pycdc reversed STORE_FAST_STORE_FAST pairs]
            (l0, l1) = (max(0, left - pad), min(w, right + pad))
            if t1 <= t0 or l1 <= l0:
                return math.nan
            crop = mono_gray[t0:t1, l0:l1]
            (ch, cw) = crop.shape[:2]
            if ch < 4 or cw < 2:
                return math.nan
            if cw < 24 or ch < 20:
                scale = max(24 / float(cw), 20 / float(ch), 1.8)
                nw = max(8, int(round(cw * scale)))
                nh = max(8, int(round(ch * scale)))
                crop = cv2.resize(crop, (nw, nh), interpolation = cv2.INTER_CUBIC)
            pil = Image.fromarray(crop)
            cfg = '--psm 10'
            
            try:
                data = pytesseract.image_to_data(pil, output_type = pytesseract.Output.DICT, config = cfg)
                n = len(data.get('text', []))
                best_c = math.nan
                best_pri = -1
                exp = expected_char.strip() or ''
                best_any = math.nan
                for i in range(n):
                    lv = int(data['level'][i])
                    if lv != 5:
                        continue
                    raw = (data['text'][i] or '').strip()
                    if not raw:
                        continue
                    c = int(data['conf'][i])
                    if c < 0:
                        continue
                    fc = float(c)
                    if math.isnan(best_any) or fc > best_any:
                        best_any = fc
                    if raw == exp:
                        pass
                    elif exp and raw == exp[-1:]:
                        pass
                    
                    pri = 0
                    if not pri > best_pri:
                        if not pri == best_pri:
                            continue
                        if not math.isnan(best_c) and fc > float(best_c):
                            continue
                    best_pri = pri
                    best_c = fc
                2
                if not math.isnan(best_c):
                    return best_c
                return best_any
            except (pytesseract.TesseractNotFoundError, OSError):
                
                try:
                    
                    try:
                        return None
                    except (TypeError, ValueError, KeyError):
                        math.nan

                except (TypeError, ValueError):
                    math.nan



        
        @staticmethod
        def save_chars_ocr_debug_outputs(mono_gray, raw_box_text, parsed_chars, *, box_path, boxes_png_path, glyph_confidences = None):
            '''
Write Tesseract ``image_to_boxes`` raw text (``.box`` format: ``char x1 y1 x2 y2 page``,
bottom-origin Y) and a per-character bounding-box overlay (mono / OCR resolution,
top-origin Y; ``parsed_chars`` is the converted ``(char, left, top, right, bottom)``
tuple list).

``glyph_confidences``, when set, must align with ``parsed_chars`` (e.g. PSM-10 estimates);
non-finite values are shown as ``?`` on the overlay.
'''
            if box_path is None or boxes_png_path is None or pytesseract is None:
                return None
            if not box_path is None:
                
                ensure_dir(box_path.parent)
                box_path.write_text(raw_box_text, encoding = 'utf-8')
                while not boxes_png_path is None:
                    
                    try:
                        ensure_dir(boxes_png_path.parent)
                        if mono_gray.ndim == 2:
                            vis = cv2.cvtColor(mono_gray, cv2.COLOR_GRAY2BGR)
                        else:
                            vis = mono_gray.copy()
                        color = (0, 220, 100)
                        for i, row in enumerate(parsed_chars):
                            (ch, left, top, right, bottom) = row
                            cv2.rectangle(vis, (left, top), (right - 1, bottom - 1), color, 1)
                            if not ch:
                                continue
                            cv2.putText(vis, str(ch), (left, max(0, top - 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)  # [recovered from bytecode: label var was 'ch']
                        cv2.imwrite(str(boxes_png_path), vis)
                        return None
                        
                        try:
                            return None
                        except Exception:
                            exc = None
                            logger.warning('Could not write char-OCR debug box file %s: %s', box_path, exc)
                            exc = None
                            del exc

                        exc = None
                        del exc
                    except Exception:
                        exc = None
                        logger.warning('Could not write char-OCR debug boxes PNG %s: %s', boxes_png_path, exc)
                        exc = None
                        del exc
                        return None
                        exc = None
                        del exc



        
        @staticmethod
        def find_chars_ocr(screen_img, region = None, *, preprocess = True, white_text = False, tesseract_config = '--psm 6', cc_filter_blobs = False, cc_min_area = None, cc_max_area = None, brightness_floor = None, save_preprocess_png = None, roi_upscale = 1, ocr_debug_box_path = None, ocr_debug_boxes_png_path = None, psm10_glyph_confidence = False):
            """
Character-level OCR via :func:`pytesseract.image_to_boxes`.

Same preprocessing as :meth:`find_words_ocr` (shared :meth:`_preprocess_ocr_region`).
Each returned :class:`OcrWordBox` holds a single glyph in **full-frame** coords.
``image_to_boxes`` does not attach confidences — they stay :data:`math.nan` unless
``psm10_glyph_confidence`` is True, in which case each glyph is re-OCR'd with ``--psm 10``
on its mono crop and the reported word confidence (0–100) is stored (or :data:`math.nan`
when unavailable).

``ocr_debug_box_path`` writes the raw Tesseract ``.box`` text (bottom-origin Y).
``ocr_debug_boxes_png_path`` draws per-character boxes on the mono image (OCR pixel space);
with ``psm10_glyph_confidence``, labels include the estimated confidence.
"""
            if pytesseract is None:
                logger.error('pytesseract is not installed; pip install pytesseract and install the Tesseract OCR binary')
                return []
            prep = VisionService._preprocess_ocr_region(screen_img, region, preprocess = preprocess, white_text = white_text, brightness_floor = brightness_floor, cc_filter_blobs = cc_filter_blobs, cc_min_area = cc_min_area, cc_max_area = cc_max_area, roi_upscale = roi_upscale, save_preprocess_png = save_preprocess_png)
            if prep is None:
                return []
            (pil, mono, coord_scale, offset_x, offset_y) = prep
            
            try:
                raw = pytesseract.image_to_boxes(pil, config = tesseract_config)
                mono_h = int(mono.shape[0])
                inv = 1 / coord_scale if coord_scale > 0 else 1
                chars = []
                parsed_chars = []
                for line in (raw or '').splitlines():
                    parts = line.strip().split(' ')
                    if len(parts) < 5:
                        continue
                    ch = parts[0]
                    x1 = int(parts[1])
                    y1 = int(parts[2])
                    x2 = int(parts[3])
                    y2 = int(parts[4])
                    top = mono_h - y2
                    bottom = mono_h - y1
                    left = x1
                    right = x2
                    if right - left <= 0 or bottom - top <= 0:
                        continue
                    parsed_chars.append((ch, left, top, right, bottom))
                    full_left = int(round(float(left) * inv)) + offset_x
                    full_top = int(round(float(top) * inv)) + offset_y
                    full_w_box = max(1, int(round(float(right - left) * inv)))
                    full_h_box = max(1, int(round(float(bottom - top) * inv)))
                    chars.append(OcrWordBox(left = full_left, top = full_top, width = full_w_box, height = full_h_box, text = ch, confidence = math.nan))
                glyph_confs_for_vis = None
                if psm10_glyph_confidence and parsed_chars:
                    glyph_confs_for_vis = []
                    rebuilt = []
                    for ch, l, t, r, b in zip(parsed_chars, chars):
                        ob = None
                        gc = VisionService._tesseract_single_glyph_confidence_psm10(mono, l, t, r, b, expected_char = ch)
                        glyph_confs_for_vis.append(gc)
                        rebuilt.append(OcrWordBox(ob.left, ob.top, ob.width, ob.height, ob.text, gc))
                    chars = rebuilt
                VisionService.save_chars_ocr_debug_outputs(mono, raw or '', parsed_chars, box_path = ocr_debug_box_path, boxes_png_path = ocr_debug_boxes_png_path, glyph_confidences = glyph_confs_for_vis)
                return chars
            except pytesseract.TesseractNotFoundError:
                VisionService._log_tesseract_missing()
                
                try:
                    return None
                except ValueError:
                    []



        
        @staticmethod
        def find_words_ocr(screen_img, region = None, *, query = None, min_confidence = 30, case_sensitive = False, preprocess = True, white_text = False, tesseract_config = '--psm 11', match_alnum_only = False, fuzzy_min_ratio = None, cc_filter_blobs = False, cc_min_area = None, cc_max_area = None, brightness_floor = None, save_preprocess_png = None, roi_upscale = 1, ocr_debug_tsv_path = None, ocr_debug_boxes_png_path = None):
            '''
Locate words via OCR. With default preprocessing: dark glyphs on a light field.

Pass ``white_text=True`` for light glyphs on dark (see :meth:`preprocess_bw_ui_text`).
Optional ``brightness_floor`` overrides the default strict threshold when ``white_text``
and ``preprocess`` are True.
If ``cc_filter_blobs`` is True (with ``preprocess``), run
:meth:`filter_binary_ink_by_component_area` on the binarized ROI before Tesseract.
When ``cc_min_area`` / ``cc_max_area`` are omitted, they follow :meth:`scaled_cc_ink_bounds`
for the full-frame size.

Requires the ``tesseract`` binary on PATH (Windows: install from
https://github.com/UB-Mannheim/tesseract/wiki ) and ``pip install pytesseract``.

Args:
    screen_img: BGR screenshot (e.g. from ``WindowService.screenshot()``).
    region: Optional ROI ``(x, y, w, h)`` in screen coordinates.
    query: If set, only return words whose text contains this substring (after normalization).
    min_confidence: Reserved for API compatibility; **not used** — words are not dropped
        based on Tesseract confidence (scores ``0``–``100`` and ``-1`` are all kept when other
        filters pass).
    case_sensitive: Match behavior when ``query`` is set.
    preprocess: If True, apply binarization (Otsu for dark-on-light; strict brightness floor for ``white_text``).
    white_text: If True with ``preprocess``, extract near-white ink (see :data:`_WHITE_TEXT_BRIGHTNESS_FLOOR`) on dark UI.
    tesseract_config: Extra Tesseract CLI flags (default sparse text for scattered labels).
    match_alnum_only: If True with ``query``, also match after stripping non-alphanumeric characters
        and allow substring match either way on the compact strings.
    fuzzy_min_ratio: If set (e.g. ``0.8``), also accept tokens whose string similarity to the
        query reaches this ratio (SequenceMatcher).
    cc_min_area / cc_max_area: Blob size limits; default ``None`` uses :meth:`scaled_cc_ink_bounds`.
    brightness_floor: When set with ``white_text`` and ``preprocess``, passed to
        :meth:`preprocess_bw_ui_text`.
    roi_upscale: When ``region`` is set and this is ``> 1``, stretch the cropped BGR patch before
        binarize; OCR boxes scale back to full-frame coords. CC area thresholds scale by ``roi_upscale**2``.
    ocr_debug_tsv_path: When set, write :func:`pytesseract.image_to_tsv` for the binary passed to Tesseract.
    ocr_debug_boxes_png_path: When set, draw word-level boxes on the mono image (OCR pixel space).

Returns:
    List of ``OcrWordBox`` in full-screen coordinates (including ROI offset).
'''
            if pytesseract is None:
                logger.error('pytesseract is not installed; pip install pytesseract and install the Tesseract OCR binary')
                return []
            prep = VisionService._preprocess_ocr_region(screen_img, region, preprocess = preprocess, white_text = white_text, brightness_floor = brightness_floor, cc_filter_blobs = cc_filter_blobs, cc_min_area = cc_min_area, cc_max_area = cc_max_area, roi_upscale = roi_upscale, save_preprocess_png = save_preprocess_png)
            if prep is None:
                return []
            (pil, mono, coord_scale, offset_x, offset_y) = prep
            
            try:
                data = pytesseract.image_to_data(pil, output_type = pytesseract.Output.DICT, config = tesseract_config)
                VisionService.save_hud_ocr_debug_outputs(pil, mono, data, tesseract_config = tesseract_config, tsv_path = ocr_debug_tsv_path, boxes_png_path = ocr_debug_boxes_png_path)
                _ = min_confidence
                n = len(data.get('text', []))
                qnorm = None
                if not query is None:
                    qnorm = query.strip() if case_sensitive else query.strip().lower()
                words = []
                for i in range(n):
                    raw = (data['text'][i] or '').strip()
                    if not raw:
                        continue
                    conf = int(data['conf'][i])
                    if not qnorm is None and not VisionService._ocr_query_matches(raw, qnorm, case_sensitive = case_sensitive, match_alnum_only = match_alnum_only, fuzzy_min_ratio = fuzzy_min_ratio):  # [recovered: dropped `not` -> query kept non-matching words, so name search always failed]
                        continue
                    inv = 1 / coord_scale
                    left = int(round(float(data['left'][i]) * inv)) + offset_x
                    top = int(round(float(data['top'][i]) * inv)) + offset_y
                    w = max(1, int(round(float(data['width'][i]) * inv)))
                    h = max(1, int(round(float(data['height'][i]) * inv)))
                    words.append(OcrWordBox(left = left, top = top, width = w, height = h, text = raw, confidence = float(conf) if conf >= 0 else math.nan))
                return words
            except pytesseract.TesseractNotFoundError:
                VisionService._log_tesseract_missing()
                
                try:
                    return None
                except (ValueError, TypeError):
                    []
                    conf = -1



        _TESSERACT_NUMBERS_HUD = '--psm 6 -c tessedit_char_whitelist=0123456789l'
        _TESSERACT_LETTERS_SPARSE = '--psm 11 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
        _TESSERACT_WALL_LABEL_SPARSE = '--psm 11 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
        
        @staticmethod
        def numbers_hud_roi_top_right(screen_w, screen_h):
            '''
Top-right HUD rectangle scaled from the aspect baseline: **600×400** at 16:10 (2560×1600),
**550×350** at 16:9 (2560×1440).

Returns ``(x, y, w, h)`` in screen pixels, anchored to the top-right corner.
'''
            sw = max(1, int(screen_w))
            sh = max(1, int(screen_h))
            key = resolve_aspect_key(sw, sh)
            if key is None or key not in ASPECT_BASELINE:
                key = ASPECT_16_10
            (ref_w, ref_h) = ASPECT_BASELINE[key]
            (base_rw, base_rh) = _NUMBERS_HUD_ROI_AT_BASELINE.get(key, _NUMBERS_HUD_ROI_AT_BASELINE[ASPECT_16_10])
            rw = max(1, int(round(base_rw * sw / float(ref_w))))
            rh = max(1, int(round(base_rh * sh / float(ref_h))))
            x = max(0, sw - rw)
            y = 0
            return (x, y, rw, rh)

        
        @staticmethod
        def top_middle_square_roi(screen_w, screen_h, side = None, *, aspect_key = None):
            '''
A square anchored to the **top center**: ``y = 0``, horizontal centering,
width/height at most ``side`` (clamped to the frame).

``side`` omitted → :meth:`scaled_top_center_menu_side` (~1000 px @ 2560 wide for 16:10 / 16:9).
'''
            sw = max(1, int(screen_w))
            sh = max(1, int(screen_h))
            s = int(side) if not side is None else VisionService.scaled_top_center_menu_side(sw, sh, aspect_key = aspect_key)
            rw = max(1, min(int(s), sw))
            rh = max(1, min(int(s), sh))
            x = max(0, (sw - rw) // 2)
            y = 0
            return (x, y, rw, rh)

        
        @staticmethod
        def ocr_letters_top_center(screen_img, *, side = None, min_confidence = 0, white_text = True, brightness_floor = None, cc_filter_blobs = True, cc_min_area = None, cc_max_area = None, tesseract_config = None, save_preprocess_png = None):
            '''
**Builder-menu style OCR:** crop a :meth:`top_middle_square_roi`, run the same preprocess +
optional CC path as HUD numbers (CC defaults: :meth:`scaled_wall_menu_cc_ink_bounds`),
then Tesseract with :data:`_TESSERACT_LETTERS_SPARSE`
(PSM 11 + A–Z whitelist). Returns letter-only word boxes in **full-frame** coordinates.

``side`` omitted → resolution-scaled menu square (~1000 @ 2560).
'''
            if screen_img is None or screen_img.size == 0:
                return []
            (h_s, w_s) = screen_img.shape[:2]
            aspect_k = resolve_aspect_key(w_s, h_s)
            roi = VisionService.top_middle_square_roi(w_s, h_s, side = side, aspect_key = aspect_k)
            cfg = VisionService._TESSERACT_LETTERS_SPARSE.strip() if tesseract_config is None else str(tesseract_config).strip()
            c_hi = cc_max_area
            c_lo = cc_min_area
            if cc_filter_blobs:
                (wm_lo, wm_hi) = VisionService.scaled_wall_menu_cc_ink_bounds(w_s, h_s, aspect_key = aspect_k)
                if c_lo is None:
                    c_lo = wm_lo
                if c_hi is None:
                    c_hi = wm_hi
            words = VisionService.find_words_ocr(screen_img, region = roi, min_confidence = max(0, int(min_confidence)), preprocess = True, white_text = white_text, brightness_floor = brightness_floor, tesseract_config = cfg, cc_filter_blobs = cc_filter_blobs, cc_min_area = c_lo, cc_max_area = c_hi, save_preprocess_png = save_preprocess_png)
            letter_only = re.compile('^[A-Za-z]+$')
            out = []
            for b in words or []:
                t = b.text.strip()
                if not letter_only.fullmatch(t):
                    continue
                out.append(b)
            return out

        
        @staticmethod
        def find_wall_labels_top_center_ocr(screen_img, *, side = None, min_confidence = 0, white_text = True, brightness_floor = None, cc_filter_blobs = True, cc_min_area = None, cc_max_area = None, tesseract_config = None, save_debug_preprocess = False):
            '''
Same pipeline as :meth:`ocr_letters_top_center`, with Tesseract limited to **walWAL** unless
``tesseract_config`` overrides. Returns the **center** ``(x, y)`` of the OCR word whose text
contains **wall** (case-insensitive), **lowest on the screen** (largest ``top + height``).
``None`` if no such word. Coordinates are the full word box from Tesseract.

When ``save_debug_preprocess`` is ``True``, writes the binarized ROI to
``glyph_debug/wall_find_preprocess.png`` under the app resource root.
'''
            cfg = VisionService._TESSERACT_WALL_LABEL_SPARSE.strip() if tesseract_config is None else str(tesseract_config).strip()
            debug_png = get_resource_path('glyph_debug/wall_find_preprocess.png') if save_debug_preprocess else None
            words = VisionService.ocr_letters_top_center(screen_img, side = side, min_confidence = min_confidence, white_text = white_text, brightness_floor = brightness_floor, cc_filter_blobs = cc_filter_blobs, cc_min_area = cc_min_area, cc_max_area = cc_max_area, tesseract_config = cfg, save_preprocess_png = debug_png)
            matches = [ b for b in words if 'wall' in b.text.lower() ]
            if not matches:
                return None
            best = max(matches, key = (lambda b: b.top + b.height))
            return best.center
            b = None

        
        @staticmethod
        def _ocr_word_y_center(box):
            return box.top + box.height * 0.5

        
        @staticmethod
        def cluster_ocr_boxes_by_y(boxes, y_tolerance):
            '''
Group OCR boxes that share a text line: union on pairs whose vertical centers
differ by at most ``y_tolerance`` px.
'''
            if not boxes:
                return []
            n = len(boxes)
            cy = [ VisionService._ocr_word_y_center(boxes[i]) for i in range(n) ]
            parent = list(range(n))
            
            def find(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            
            def union(i, j):
                (pi, pj) = (find(i), find(j))  # [recovered: pycdc reversed STORE_FAST_STORE_FAST pairs]
                if pi != pj:
                    parent[pi] = pj
                    return None

            tol = float(y_tolerance)
            for i in range(n):
                for j in range(i + 1, n):
                    if not abs(cy[i] - cy[j]) <= tol:
                        continue
                    union(i, j)
            range(n)
            groups = { }
            for i in range(n):
                r = find(i)
                groups.setdefault(r, []).append(boxes[i])
            out = list(groups.values())
            for cl in out:
                cl.sort(key = (lambda b: b.left))
            out.sort(key = (lambda cl: VisionService._ocr_word_y_center(cl[0])))
            return out
            i = None

        
        @staticmethod
        def split_cluster_on_gaps(cluster, gap_px):
            '''Split one line of character boxes wherever a horizontal gap exceeds
            ``gap_px``. Returns a list of runs, left to right.

            A line of OCR'd characters is not necessarily one number. Anything bright and
            narrow sharing the line — the "+385,306" loot floater that drifts beside the
            HUD after a raid, a lamp post behind a bar, an icon edge — is read as a digit
            and, merged into the row, silently multiplies the reading by ten or more (live:
            elixir 16,256,996 read as 116,256,996 and acted on). Digits inside a number sit
            1-3 px apart at a 1299-wide capture and 6-8 px across a thousands separator;
            anything that is not part of the number is much further away than that.
            '''
            parts = sorted(cluster or [], key = (lambda b: b.left))
            if len(parts) < 2:
                return [ list(parts) ] if parts else []
            runs = [[parts[0]]]
            for box in parts[1:]:
                prev = runs[-1][-1]
                if box.left - (prev.left + prev.width) > float(gap_px):
                    runs.append([])
                runs[-1].append(box)
            return runs

        @staticmethod
        def merge_numeric_cluster(cluster, *, join_separator = ' '):
            '''
Sort left-to-right and union bounding boxes into one :class:`GroupedNumber`.

``join_separator`` defaults to a space (word-level OCR); pass ``""`` when each
:class:`OcrWordBox` already holds a single character (e.g. from
:meth:`find_chars_ocr`) so digits concatenate into one number.
'''
            if not cluster:
                return GroupedNumber('', 0, 0, 0, 0, float('nan'))
            parts = sorted(cluster, key = (lambda b: b.left))
            text = join_separator.join([ p.text.strip() for p in parts if p.text.strip() ])
            text = text.replace('l', '1')
            left = min([ p.left for p in parts ])
            top = min([ p.top for p in parts ])
            right = max([ p.left + p.width for p in parts ])
            bottom = max([ p.top + p.height for p in parts ])
            confs = [ float(p.confidence) for p in parts if not math.isnan(float(p.confidence)) ]  # [recovered: decompiler dropped the `not`, keeping only NaN confidences]
            conf = float(sum(confs) / len(confs)) if confs else float('nan')
            return GroupedNumber(text = text, left = left, top = top, width = max(1, right - left), height = max(1, bottom - top), confidence = conf)
            p = None

        
        @staticmethod
        def extract_grouped_numbers_in_region(screen_img, region, *, min_confidence = 0, white_text = True, tesseract_config = None, y_tolerance_px = None, cc_filter_blobs = True, cc_min_area = None, cc_max_area = None, preprocess = True, save_preprocess_png = None, roi_upscale = HUD_TOP_RIGHT_NUMBERS_ROI_UPSCALE, ocr_debug_box_path = None, ocr_debug_boxes_png_path = None, psm10_glyph_confidence = False, hud_debug_char_clusters_out = None, allow_hud_ocr_debug = False):
            '''
**Pipeline:** ROI crop → optional BGR ``roi_upscale`` before binarize → grayscale via :meth:`preprocess_bw_ui_text` (``white_text``) →
black/white mask inverted to ink on white → optional
:meth:`filter_binary_ink_by_component_area` (defaults from :meth:`scaled_cc_ink_bounds`) →
:meth:`find_chars_ocr` (Tesseract :func:`pytesseract.image_to_boxes`, digit-friendly HUD
config :data:`_TESSERACT_NUMBERS_HUD`) → digit chars → cluster by line (similar ``y``) →
merge each line into one number (no spaces).

``y_tolerance_px`` defaults to a fraction of ~30 px line height scaled by frame height
(1600p baseline).

``min_confidence`` is accepted for API compatibility but **not used**.

``allow_hud_ocr_debug``: when ``False`` (default for the GUI bot), ``save_preprocess_png``,
``ocr_debug_*`` disk paths, ``psm10_glyph_confidence``, and ``hud_debug_char_clusters_out``
are ignored so extraction never writes debug artifacts or runs extra passes.

When ``allow_hud_ocr_debug`` is ``True``:

``ocr_debug_box_path`` writes the raw Tesseract ``.box`` text;
``ocr_debug_boxes_png_path`` writes a per-character bbox overlay on the binary ROI.

``psm10_glyph_confidence``: optional second Tesseract pass per glyph (see :meth:`find_chars_ocr`).
``hud_debug_char_clusters_out``: when set to an empty mutable list, it is cleared and filled
with one inner list per Y-cluster (characters left-to-right) after clustering, for scripts / debug.
'''
            if not allow_hud_ocr_debug:
                save_preprocess_png = None
                ocr_debug_box_path = None
                ocr_debug_boxes_png_path = None
                psm10_glyph_confidence = False
                hud_debug_char_clusters_out = None
            if screen_img is None or screen_img.size == 0:
                return []
            (h_s, w_s) = screen_img.shape[:2]
            cfg = f'''{VisionService._TESSERACT_NUMBERS_HUD}'''.strip()
            if not tesseract_config is None:
                cfg = tesseract_config
            _ = min_confidence
            chars = VisionService.find_chars_ocr(screen_img, region = region, preprocess = preprocess, white_text = white_text, tesseract_config = cfg, cc_filter_blobs = cc_filter_blobs, cc_min_area = cc_min_area, cc_max_area = cc_max_area, save_preprocess_png = save_preprocess_png, roi_upscale = roi_upscale, ocr_debug_box_path = ocr_debug_box_path, ocr_debug_boxes_png_path = ocr_debug_boxes_png_path, psm10_glyph_confidence = psm10_glyph_confidence)
            # find_chars_ocr returns None when Tesseract itself is unavailable, and every
            # caller here is written for "no numbers found" rather than an exception (live:
            # the wall gate died with TypeError in a process that had not put Tesseract on
            # the path).
            digit_chars = [ c for c in chars or () if c.text.isdigit() or c.text == 'l' ]  # [recovered: decompiler dropped the isdigit() disjunct; 'l' is kept because merge_numeric_cluster maps it to '1']
            if not digit_chars:
                return []
            if y_tolerance_px is None:
                med_h = float(np.median([ c.height for c in digit_chars ]))
                y_tol = max(8, med_h * 0.45, float(h_s) * (_NUMBERS_REF_LINE_HEIGHT_PX / 1600) * 0.35)
            else:
                y_tol = float(y_tolerance_px)
            clusters = VisionService.cluster_ocr_boxes_by_y(digit_chars, y_tol)
            # One line can hold more than one number. Split each line where the spacing
            # stops looking like the inside of a number: digits sit 1-3 px apart at a
            # 1299-wide capture and 6-8 px across a thousands separator, so a couple of
            # digit widths of blank is something else entirely.
            med_w = float(np.median([ c.width for c in digit_chars ])) or 1.0
            gap_px = max(3.0, med_w * _NUMBER_RUN_GAP_WIDTHS)
            runs = []
            for cl in clusters:
                runs.extend(VisionService.split_cluster_on_gaps(cl, gap_px))
            clusters = runs
            if not hud_debug_char_clusters_out is None:
                hud_debug_char_clusters_out.clear()
                for cl in clusters:
                    hud_debug_char_clusters_out.append(sorted(cl, key = (lambda b: b.left)))
            return [ VisionService.merge_numeric_cluster(cl, join_separator = '') for cl in clusters ]
            c = None
            c = None
            cl = None

        
        @staticmethod
        def extract_top_right_hud_numbers(screen_img, *, min_confidence = 0, white_text = True, tesseract_config = None, y_tolerance_px = None, cc_filter_blobs = True, cc_min_area = None, cc_max_area = None, preprocess = True, save_preprocess_png = None, roi_upscale = HUD_TOP_RIGHT_NUMBERS_ROI_UPSCALE, ocr_debug_box_path = None, ocr_debug_boxes_png_path = None, psm10_glyph_confidence = False, hud_debug_char_clusters_out = None, allow_hud_ocr_debug = False):
            '''
Full **top-right HUD** number pipeline on ``screen_img`` (see
:meth:`extract_grouped_numbers_in_region`). ROI from :meth:`numbers_hud_roi_top_right`.

Updates :class:`~app.config.Config` capture size / aspect from ``screen_img`` when possible.
Default ``roi_upscale`` is :data:`HUD_TOP_RIGHT_NUMBERS_ROI_UPSCALE` (BGR enlargement before binarize).

``allow_hud_ocr_debug`` defaults to ``False`` so the GUI bot never enables preprocess saves,
``.box`` / overlay writes, PSM-10 glyph confidence, or cluster dump unless explicitly opted in.
'''
            if screen_img is None or screen_img.size == 0:
                return []
            cfg = Config()
            cfg.set_target_size_from_frame(screen_img)
            (h_s, w_s) = screen_img.shape[:2]
            roi = VisionService.numbers_hud_roi_top_right(w_s, h_s)
            return VisionService.extract_grouped_numbers_in_region(screen_img, roi, min_confidence = min_confidence, white_text = white_text, tesseract_config = tesseract_config, y_tolerance_px = y_tolerance_px, cc_filter_blobs = cc_filter_blobs, cc_min_area = cc_min_area, cc_max_area = cc_max_area, preprocess = preprocess, save_preprocess_png = save_preprocess_png, roi_upscale = roi_upscale, ocr_debug_box_path = ocr_debug_box_path, ocr_debug_boxes_png_path = ocr_debug_boxes_png_path, psm10_glyph_confidence = psm10_glyph_confidence, hud_debug_char_clusters_out = hud_debug_char_clusters_out, allow_hud_ocr_debug = allow_hud_ocr_debug)

        
        @staticmethod
        def parse_loot_amount_from_grouped_text(text):
            '''
Parse a HUD resource quantity from OCR text (digits only; commas and spaces ignored).

Used with :meth:`extract_top_right_hud_numbers` clusters: HUD shows gold / elixir / dark
elixir left‑to‑right in the OCR ROI — take clusters sorted by horizontal center for the triplet.
'''
            digits = re.sub('\\D', '', (text or '').strip())
            if not digits:
                return None
            
            try:
                return int(digits)
            except ValueError:
                return None


        
        @staticmethod
        def parse_hud_resources_triplet(groups):
            '''
From :meth:`extract_top_right_hud_numbers` clusters, derive ``(gold, elixir, dark_elixir)``:
the home HUD stacks the three resource bars vertically (gold, elixir, dark top to bottom),
so the three **topmost** rows are taken in vertical order — one number per row, the
rightmost when a row decodes more than one. Returns ``None`` if fewer than three rows
are available.
'''
            # The HUD numbers are right-aligned, so a horizontal sort orders them by digit
            # count (7-digit elixir would land before 6-digit gold) — vertical order is the
            # only stable assignment.
            scored = []
            for g in groups:
                v = VisionService.parse_loot_amount_from_grouped_text(g.text)
                if v is None:
                    continue
                cy = float(g.top) + float(g.height) * 0.5
                scored.append((cy, float(g.left) + float(g.width), v, float(g.height)))
            scored.sort(key = (lambda t: t[0]))
            if not scored:
                return None
            # One row, one number. Each bar's number is right-aligned against its resource
            # icon, so when a row yields more than one number the rightmost is the resource
            # and whatever else was on the line — a loot floater fading after a raid, a
            # piece of scenery behind the bar — is to the left of it and is not.
            row_tol = max(4.0, float(np.median([ t[3] for t in scored ])) * 0.6)
            rows = []
            for entry in scored:
                if rows and abs(entry[0] - rows[-1][0]) <= row_tol:
                    if entry[1] > rows[-1][1]:
                        rows[-1] = entry
                    continue
                rows.append(entry)
            if len(rows) < 3:
                return None
            return (rows[0][2], rows[1][2], rows[2][2])

        
        @staticmethod
        def _ocr_confidence_key(w):
            if math.isnan(w.confidence):
                return -1
            return float(w.confidence)

        
        @staticmethod
        def find_word_on_screen(screen_img, word_or_phrase, region = None, **kwargs):
            '''
Returns the center ``(x, y)`` of the best OCR match for ``word_or_phrase``, or
``(None, None)`` if not found.

Among all tokens that pass filters, picks by: (1) **full alnum name inside OCR token**,
(2) plain substring, (3) long name fragment, (4) fuzzy; then **longest** OCR token,
then Tesseract confidence — so a short side label does not beat the full username.
Extra kwargs are forwarded (e.g. ``min_confidence``, ``preprocess``).
'''
            matches = VisionService.find_words_ocr(screen_img, **{
                'region': region,
                'query': word_or_phrase })
            if not matches:
                return (None, None)
            case_sensitive = bool(kwargs.get('case_sensitive', False))
            match_alnum_only = bool(kwargs.get('match_alnum_only', False))
            fuzzy_min_ratio = kwargs.get('fuzzy_min_ratio')
            qnorm = word_or_phrase.strip() if case_sensitive else word_or_phrase.strip().lower()
            
            def pick_key(w):
                tier = VisionService._ocr_username_match_tier(w.text, qnorm, case_sensitive = case_sensitive, match_alnum_only = match_alnum_only, fuzzy_min_ratio = fuzzy_min_ratio)
                if match_alnum_only:
                    alen = len(re.sub('[^a-z0-9]', '', w.text.lower()))
                else:
                    alen = len(w.text.strip())
                return (tier, alen, VisionService._ocr_confidence_key(w))

            best = max(matches, key = pick_key)
            return best.center


except ImportError:
    pytesseract = None

