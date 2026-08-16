"""Synthetic bank-card generator.

The spec says two reference images of a fictive card are "provided" with the
project, but they were not present in the repository. Per rule 52-53 we MUST
build our own test environment, so this module generates a realistic-looking
fictive card with a known, *relative* layout. The layout is expressed entirely
in normalized coordinates (0..1) relative to the card — never absolute pixels
(rule 4) — so CardTemplate/regions stay resolution-independent.

The card mimics the described visual structure:
  - horizontal rectangle, beige/gold design
  - "WORLD ELITE" text zone
  - 16-digit number split into 4 groups (region_01..region_04)
  - cardholder name (region_name)
  - expiration (region_exp)
  - CVV code (region_cvv)
  - contactless + conformity symbols
  - red rectangular masks over unrevealed regions
  - a Snapcode/QR-like code UNDER the card (excluded from analysis, rule 21)

Regions are the puzzle pieces. Each region has a *full* value and a *revealed*
mask: initially most are masked, and stories progressively reveal them.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# ----------------------------------------------------------------------
# Canonical relative layout. All coords are (x, y, w, h) in 0..1 of the card.
# Card aspect ratio ~ 1.586 (ISO/IEC 7810 ID-1). We render at CARD_W x CARD_H.
# ----------------------------------------------------------------------

CARD_ASPECT = 1.586  # width / height

# Region definitions. ``digits`` => numeric; ``text`` => alnum.
# Vertical layout per spec §3 BIS:
#   1. CARDHOLDER_NAME (top)
#   2. CARD_NUMBER      (middle)
#   3. EXPIRATION + CVC (bottom, side by side)
REGIONS: Dict[str, Dict] = {
    "region_name": {"kind": "text", "rect": (0.06, 0.30, 0.55, 0.08), "label": "CARDHOLDER_NAME"},
    "region_01": {"kind": "digits", "n": 4, "rect": (0.06, 0.46, 0.20, 0.10), "label": "CARD_NUMBER"},
    "region_02": {"kind": "digits", "n": 4, "rect": (0.27, 0.46, 0.20, 0.10), "label": "CARD_NUMBER"},
    "region_03": {"kind": "digits", "n": 4, "rect": (0.48, 0.46, 0.20, 0.10), "label": "CARD_NUMBER"},
    "region_04": {"kind": "digits", "n": 4, "rect": (0.69, 0.46, 0.20, 0.10), "label": "CARD_NUMBER"},
    "region_exp": {"kind": "text", "rect": (0.06, 0.70, 0.16, 0.09), "label": "EXPIRATION_DATE"},
    "region_cvv": {"kind": "digits", "n": 3, "rect": (0.26, 0.70, 0.12, 0.09), "label": "CVC"},
}

# Region label text drawn above each region on the synthetic card.
REGION_LABELS: Dict[str, str] = {
    "region_name": "NOM DU TITULAIRE",
    "region_01": "NUMERO DE CARTE",
    "region_exp": "EXPIRATION",
    "region_cvv": "CVC",
}

# Non-puzzle zones (for template structure / exclusion).
ZONE_WORLD_ELITE = (0.06, 0.08, 0.40, 0.08)
ZONE_CONTACTLESS = (0.82, 0.08, 0.12, 0.10)
ZONE_CONFORMITY = (0.60, 0.20, 0.30, 0.05)
# Snapcode lives BELOW the card (in story frame coords, not card coords).
SNAPCODE_RELATIVE_BELOW = (0.30, 1.02, 0.40, 0.18)  # relative to card box in frame


@dataclass
class CardGroundTruth:
    """The full known state of a generated card (for test assertions only)."""

    values: Dict[str, str] = field(default_factory=dict)
    revealed: Dict[str, bool] = field(default_factory=dict)
    card_box: Tuple[int, int, int, int] = (0, 0, 0, 0)  # x,y,w,h in frame px
    card_corners: List[Tuple[int, int]] = field(default_factory=list)
    frame_size: Tuple[int, int] = (0, 0)  # (w,h)

    def expected_display(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for k, v in self.values.items():
            if self.revealed.get(k):
                out[k] = v
            else:
                spec = REGIONS.get(k, {})
                if spec.get("kind") == "digits":
                    out[k] = "?" * spec.get("n", 4)
                else:
                    out[k] = "????"
        return out


def _random_digits(n: int, rng: random.Random) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def _random_name(rng: random.Random) -> str:
    first = rng.choice(["BENOIT", "LUCAS", "MARIE", "PAUL", "ANNA", "LEO"])
    last = rng.choice(["CHEVALIER", "DUBOIS", "MARTIN", "ROY", "BLANC", "FORD"])
    return f"{first} {last}"


def _draw_text(img: np.ndarray, text: str, rect_rel: Tuple[float, float, float, float],
               card_w: int, card_h: int, color=(40, 30, 20), scale: float = 1.0,
               bold: bool = False) -> None:
    x = int(rect_rel[0] * card_w)
    y = int(rect_rel[1] * card_h)
    h = int(rect_rel[3] * card_h)
    font = cv2.FONT_HERSHEY_DUPLEX
    # fit font size to box height
    fscale = (h / 28.0) * scale
    thickness = 2 if bold else 1
    # letter spacing for digit groups
    if text and text[0].isdigit() and " " not in text:
        # monospace-ish spacing
        char_w = int(h * 0.62 * scale)
        for i, ch in enumerate(text):
            cv2.putText(img, ch, (x + i * char_w, y + h - int(h * 0.18)),
                        font, fscale, color, thickness, cv2.LINE_AA)
    else:
        cv2.putText(img, text, (x, y + h - int(h * 0.18)), font, fscale, color, thickness, cv2.LINE_AA)


def _draw_mask(img: np.ndarray, rect_rel: Tuple[float, float, float, float],
               card_w: int, card_h: int, color=(0, 0, 220)) -> None:
    """Draw a red rectangle mask over a region (rule 20: detect red masks)."""
    x = int(rect_rel[0] * card_w)
    y = int(rect_rel[1] * card_h)
    w = int(rect_rel[2] * card_w)
    h = int(rect_rel[3] * card_h)
    cv2.rectangle(img, (x, y), (x + w, y + h), color, -1)
    cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 120), 2)


def render_card(
    card_w: int = 1024,
    card_h: Optional[int] = None,
    values: Optional[Dict[str, str]] = None,
    revealed: Optional[Dict[str, bool]] = None,
    rng: Optional[random.Random] = None,
) -> Tuple[np.ndarray, Dict[str, str], Dict[str, bool]]:
    """Render the card alone (no background) at card_w x card_h.

    Returns (image, values, revealed). Masked regions are covered with red
    rectangles. ``values`` is the FULL ground truth; ``revealed`` marks which
    are visible.
    """
    if card_h is None:
        card_h = int(round(card_w / CARD_ASPECT))
    rng = rng or random.Random(0)
    if values is None:
        values = {
            "region_01": _random_digits(4, rng),
            "region_02": _random_digits(4, rng),
            "region_03": _random_digits(4, rng),
            "region_04": _random_digits(4, rng),
            "region_name": _random_name(rng),
            "region_exp": f"{rng.randint(1,12):02d}/{rng.randint(26,32)}",
            "region_cvv": _random_digits(3, rng),
        }
    if revealed is None:
        revealed = {k: False for k in values}

    # beige/gold gradient background
    img = np.zeros((card_h, card_w, 3), dtype=np.uint8)
    for i in range(card_h):
        t = i / card_h
        r = int(235 - 25 * t)
        g = int(218 - 30 * t)
        b = int(170 - 50 * t)
        img[i, :] = (b, g, r)  # BGR
    # subtle gold border
    cv2.rectangle(img, (0, 0), (card_w - 1, card_h - 1), (90, 140, 200), 3)
    cv2.rectangle(img, (6, 6), (card_w - 7, card_h - 7), (120, 170, 210), 1)

    # WORLD ELITE zone
    _draw_text(img, "WORLD ELITE", ZONE_WORLD_ELITE, card_w, card_h, color=(90, 70, 30), scale=0.9, bold=True)
    # contactless symbol (rotated arcs)
    cx = int((ZONE_CONTACTLESS[0] + ZONE_CONTACTLESS[2] / 2) * card_w)
    cy = int((ZONE_CONTACTLESS[1] + ZONE_CONTACTLESS[3] / 2) * card_h)
    for r in (int(card_h * 0.02), int(card_h * 0.035), int(card_h * 0.05)):
        cv2.ellipse(img, (cx, cy), (r, r), 0, 200, 340, (80, 60, 30), 2)
    # conformity symbols
    _draw_text(img, "-><  S  >=", ZONE_CONFORMITY, card_w, card_h, color=(80, 60, 30), scale=0.5)

    # regions: draw a small label above each region, then the value or mask
    for key, spec in REGIONS.items():
        label = REGION_LABELS.get(key)
        if label:
            lx, ly, lw, lh = spec["rect"]
            label_rect = (lx, max(0.0, ly - 0.05), lw, 0.04)
            _draw_text(img, label, label_rect, card_w, card_h, color=(100, 85, 50), scale=0.45)
        val = values.get(key, "")
        is_rev = revealed.get(key, False)
        if is_rev and val:
            _draw_text(img, val, spec["rect"], card_w, card_h, color=(30, 25, 15), scale=1.0, bold=True)
        else:
            _draw_mask(img, spec["rect"], card_w, card_h)
    return img, values, revealed


def place_card_in_frame(
    card_img: np.ndarray,
    frame_w: int = 1080,
    frame_h: int = 1920,
    card_scale: float = 0.72,
    offset: Tuple[float, float] = (0.5, 0.45),
    angle_deg: float = 0.0,
    perspective: float = 0.0,
    bg: str = "indoor",
    add_snapcode: bool = True,
    rng: Optional[random.Random] = None,
) -> Tuple[np.ndarray, CardGroundTruth]:
    """Composite the card into a larger story-like frame with a background.

    Returns (frame, ground_truth) where ground_truth.card_box / card_corners
    describe where the card ended up (for detector validation).
    """
    rng = rng or random.Random(0)
    bg_img = _make_background(frame_w, frame_h, bg, rng)

    # scale card
    ch, cw = card_img.shape[:2]
    new_w = int(cw * card_scale)
    new_h = int(ch * card_scale)
    card = cv2.resize(card_img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # rotate
    if abs(angle_deg) > 0.01:
        M = cv2.getRotationMatrix2D((new_w / 2, new_h / 2), angle_deg, 1.0)
        cos = abs(M[0, 0]); sin = abs(M[0, 1])
        bw = int(new_h * sin + new_w * cos)
        bh = int(new_h * cos + new_w * sin)
        M[0, 2] += bw / 2 - new_w / 2
        M[1, 2] += bh / 2 - new_h / 2
        card = cv2.warpAffine(card, M, (bw, bh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        new_w, new_h = bw, bh

    # perspective skew
    corners_local: List[Tuple[float, float]] = [(0, 0), (new_w, 0), (new_w, new_h), (0, new_h)]
    if abs(perspective) > 0.01:
        px = perspective * new_w
        py = perspective * new_h
        src = np.float32(corners_local)
        dst = np.float32([
            (0 + px, 0 + py),
            (new_w - px, 0 - py * 0.3),
            (new_w + px * 0.3, new_h - py),
            (0 - px * 0.3, new_h + py * 0.3),
        ])
        H = cv2.getPerspectiveTransform(src, dst)
        card = cv2.warpPerspective(card, H, (new_w, new_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        corners_local = [(float(dst[i][0]), float(dst[i][1])) for i in range(4)]
        # recompute bounding box and crop card to match
        xs = [p[0] for p in corners_local]; ys = [p[1] for p in corners_local]
        min_x, max_x = int(min(xs)), int(max(xs))
        min_y, max_y = int(min(ys)), int(max(ys))
        cw, ch = max_x - min_x, max_y - min_y
        if 0 <= min_x and 0 <= min_y and min_x + cw <= card.shape[1] and min_y + ch <= card.shape[0] and cw > 0 and ch > 0:
            card = card[min_y:min_y + ch, min_x:min_x + cw]
            corners_local = [(float(p[0] - min_x), float(p[1] - min_y)) for p in corners_local]
        new_w, new_h = card.shape[1], card.shape[0]

    # placement
    px = int(offset[0] * frame_w - new_w / 2)
    py = int(offset[1] * frame_h - new_h / 2)
    px = max(0, min(px, frame_w - new_w))
    py = max(0, min(py, frame_h - new_h))

    # alpha-less paste (card is opaque)
    roi = bg_img[py:py + new_h, px:px + new_w]
    if roi.shape[0] == new_h and roi.shape[1] == new_w:
        bg_img[py:py + new_h, px:px + new_w] = card

    box = (px, py, new_w, new_h)
    corners = [(px + corners_local[i][0], py + corners_local[i][1]) for i in range(4)]

    # snapcode below card (rule 21: must be excludable)
    if add_snapcode:
        sx = int(px + SNAPCODE_RELATIVE_BELOW[0] * new_w)
        sy = int(py + SNAPCODE_RELATIVE_BELOW[1] * new_h)
        sw = int(SNAPCODE_RELATIVE_BELOW[2] * new_w)
        sh = int(SNAPCODE_RELATIVE_BELOW[3] * new_h)
        if sy + sh < frame_h and sx + sw < frame_w and sw > 10 and sh > 10:
            _draw_snapcode(bg_img, sx, sy, sw, sh, rng)

    gt = CardGroundTruth(card_box=box, card_corners=[(int(x), int(y)) for x, y in corners], frame_size=(frame_w, frame_h))
    return bg_img, gt


def _draw_snapcode(img: np.ndarray, x: int, y: int, w: int, h: int, rng: random.Random) -> None:
    """Draw a QR/snapcode-like square of random dots so it can trigger false OCR."""
    cv2.rectangle(img, (x, y), (x + w, y + h), (255, 255, 255), -1)
    cv2.rectangle(img, (x, y), (x + w, y + h), (40, 40, 40), 2)
    grid = 8
    cw = w // grid
    ch = h // grid
    for gy in range(grid):
        for gx in range(grid):
            if rng.random() < 0.5:
                cv2.rectangle(img, (x + gx * cw, y + gy * ch),
                              (x + (gx + 1) * cw, y + (gy + 1) * ch), (30, 30, 30), -1)
    # finder squares
    for fx, fy in [(x, y), (x + w - 3 * cw, y), (x, y + h - 3 * ch)]:
        cv2.rectangle(img, (fx, fy), (fx + 3 * cw, fy + 3 * ch), (0, 0, 0), -1)
        cv2.rectangle(img, (fx + cw, fy + ch), (fx + 2 * cw, fy + 2 * ch), (255, 255, 255), -1)


def _make_background(w: int, h: int, kind: str, rng: random.Random) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    if kind == "indoor":
        # warm wall with a plant-like blob
        for i in range(h):
            t = i / h
            img[i, :] = (int(60 + 20 * t), int(80 + 20 * t), int(110 + 30 * t))
        # plant blob bottom-right
        cv2.circle(img, (int(w * 0.82), int(h * 0.85)), int(w * 0.18), (40, 90, 50), -1)
        cv2.circle(img, (int(w * 0.7), int(h * 0.78)), int(w * 0.10), (30, 80, 40), -1)
    elif kind == "dark":
        img[:] = (20, 22, 28)
    else:
        img[:] = (200, 200, 210)
    # snapchat-ish top bar
    cv2.rectangle(img, (0, 0), (w, 90), (30, 30, 30), -1)
    cv2.rectangle(img, (0, h - 120), (w, h), (20, 20, 20), -1)
    return img


# ----------------------------------------------------------------------
# Augmentations (rule 53): produce robust variants from a placed frame.
# ----------------------------------------------------------------------

def augment_frame(img: np.ndarray, rng: random.Random,
                  zoom: float = 1.0, brightness: float = 1.0, contrast: float = 1.0,
                  blur: float = 0.0, noise: float = 0.0, jpeg: int = 0) -> np.ndarray:
    h, w = img.shape[:2]
    out = img.astype(np.float32)
    if zoom != 1.0:
        nw = int(w * zoom); nh = int(h * zoom)
        out = cv2.resize(out, (nw, nh))
        if nw > w:
            x0 = (nw - w) // 2; y0 = (nh - h) // 2
            out = out[y0:y0 + h, x0:x0 + w]
        else:
            canvas = np.zeros((h, w, 3), np.float32)
            x0 = (w - nw) // 2; y0 = (h - nh) // 2
            canvas[y0:y0 + nh, x0:x0 + nw] = out
            out = canvas
    if brightness != 1.0:
        out = out * brightness
    if contrast != 1.0:
        mean = out.mean()
        out = (out - mean) * contrast + mean
    if blur > 0:
        k = int(blur) | 1  # odd
        out = cv2.GaussianBlur(out, (k, k), 0)
    out = np.clip(out, 0, 255).astype(np.uint8)
    if noise > 0:
        n = np.random.RandomState(rng.randint(0, 2**31)).normal(0, noise, out.shape)
        out = np.clip(out.astype(np.float32) + n, 0, 255).astype(np.uint8)
    if jpeg > 0:
        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, max(10, 100 - jpeg)])
        if ok:
            out = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return out


def generate_reference_pair(out_dir: Path, seed: int = 42) -> List[Path]:
    """Generate the two reference images the spec describes (rules 3, 4).

    Story 1: region_01 revealed, rest masked.
    Story 2: region_01 + region_04 revealed, rest masked (slightly different).
    """
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    scenarios = [
        {"region_01": True},
        {"region_01": True, "region_04": True},
    ]
    for i, rev in enumerate(scenarios, start=1):
        card, values, revealed = render_card(rng=rng)
        # same values across both references (rule 4: learn relative structure,
        # don't assume identical)
        revealed = {k: rev.get(k, False) for k in values}
        card, values, revealed = render_card(values=values, revealed=revealed, rng=rng)
        frame, gt = place_card_in_frame(card, offset=(0.5, 0.40), angle_deg=-3.0, rng=rng)
        path = out_dir / f"reference_{i}.png"
        cv2.imwrite(str(path), frame)
        paths.append(path)
    return paths


def make_card_from_values(values: Dict[str, str], revealed: Dict[str, bool],
                          card_w: int = 1024) -> np.ndarray:
    img, _, _ = render_card(card_w=card_w, values=values, revealed=revealed)
    return img
