from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageStat
except ImportError:  # pragma: no cover - optional local dependency.
    Image = None
    ImageStat = None

try:
    import numpy as _np
except ImportError:  # pragma: no cover - optional local dependency.
    _np = None

try:
    import cv2 as _cv2
except ImportError:  # pragma: no cover - optional local dependency.
    _cv2 = None


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def screenshot_summary(path: Path) -> dict[str, Any]:
    if Image is None or ImageStat is None:
        return {"path": str(path), "available": False, "reason": "pillow_missing"}
    if not path.exists():
        return {"path": str(path), "available": False, "reason": "missing_screenshot"}

    with Image.open(path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        sample = rgb.resize((min(240, width), min(240, height)))
        stat = ImageStat.Stat(sample)
        mean_rgb = tuple(int(value) for value in stat.mean[:3])
        luminance = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in sample.getdata()]
        mean_luminance = round(sum(luminance) / len(luminance), 2) if luminance else 0
        dark_ratio = round(sum(1 for value in luminance if value < 48) / len(luminance), 4) if luminance else 0
        light_ratio = round(sum(1 for value in luminance if value > 220) / len(luminance), 4) if luminance else 0

        quantized = sample.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette() or []
        color_counts = quantized.getcolors(maxcolors=256) or []
        colors = []
        for count, index in sorted(color_counts, reverse=True)[:8]:
            offset = index * 3
            rgb_color = tuple(palette[offset : offset + 3])
            if len(rgb_color) == 3:
                colors.append({"hex": rgb_to_hex(rgb_color), "count": count})

    return {
        "path": str(path),
        "available": True,
        "width": width,
        "height": height,
        "file_bytes": path.stat().st_size,
        "mean_rgb": rgb_to_hex(mean_rgb),
        "mean_luminance": mean_luminance,
        "dark_pixel_ratio": dark_ratio,
        "light_pixel_ratio": light_ratio,
        "palette": colors,
    }


# ---------------------------------------------------------------------------
# D3 visual-fidelity primitives (deterministic: same inputs -> same output;
# no randomness / time / network). Used by check_visual_regression.
# Three metrics: (1) Pixelmatch per-pixel diff ratio, (2) SSIM structural
# similarity, (3) Design2Code-style block fidelity over component crops.
# ---------------------------------------------------------------------------

_MISSING = {"available": False}


def _load_rgb_array(path: Path):
    """Load an image as a uint8 HxWx3 numpy array, or None if unavailable."""
    if Image is None or _np is None:
        return None
    if not path.exists():
        return None
    with Image.open(path) as image:
        return _np.asarray(image.convert("RGB"), dtype=_np.uint8)


def _resize_to(arr, size: tuple[int, int]):
    """Deterministically resize an RGB array to (width, height)."""
    if arr is None or Image is None or _np is None:
        return None
    width, height = size
    pil = Image.fromarray(arr).resize((width, height), Image.BILINEAR)
    return _np.asarray(pil, dtype=_np.uint8)


def _common_canvas(ref, sub):
    """Resize both arrays to a shared reference resolution (the reference's)."""
    if ref is None or sub is None:
        return None, None
    h, w = ref.shape[0], ref.shape[1]
    sub_r = sub if (sub.shape[0] == h and sub.shape[1] == w) else _resize_to(sub, (w, h))
    return ref, sub_r


def _align_vertical(ref, sub, y_shift: int):
    """Crop both arrays to the shared content band given a recorded scroll delta.

    `y_shift` = reference_scroll_y - submission_scroll_y (from each capture's
    metrics.json `scroll.y`). It is the ground-truth vertical offset between the
    two viewports over the SAME document — reference row i shows the same content
    as submission row (i + y_shift). We keep only the overlapping band so the two
    arrays describe the same pixels before any metric runs.

    This makes the visual metrics invariant to *where the capture recipe happened
    to scroll* (a capture artifact, not a property of the model's output) WITHOUT
    searching for a best-fit offset: the shift is fixed by the recorded metadata,
    so a genuine regression cannot be aligned away. y_shift == 0 (the common case,
    and every historical single-viewport capture) returns the arrays unchanged →
    byte-identical to the pre-alignment behavior.
    """
    if ref is None or sub is None or not y_shift:
        return ref, sub
    height = ref.shape[0]
    dy = int(y_shift)
    if abs(dy) >= height:
        # No meaningful overlap; leave untouched so the metric fails loudly
        # rather than comparing an empty band.
        return ref, sub
    if dy > 0:
        return ref[: height - dy, :], sub[dy:, :]
    return ref[-dy:, :], sub[: height + dy, :]


def _apply_masks(mask, regions, width: int, height: int):
    """Zero out (ignore) rectangular mask regions in a boolean keep-mask."""
    for region in regions or []:
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        rw = int(region.get("width", region.get("w", 0)))
        rh = int(region.get("height", region.get("h", 0)))
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(width, x + rw), min(height, y + rh)
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = False
    return mask


def pixel_diff_ratio(
    reference_path: Path,
    submission_path: Path,
    tolerance: int = 30,
    mask_regions: list[dict] | None = None,
    y_shift: int = 0,
) -> dict[str, Any]:
    """Fraction of compared pixels whose max per-channel abs diff > tolerance.

    Mask regions are excluded from the comparison. `y_shift` (recorded scroll
    delta ref_y-sub_y) aligns the two captures to the shared content band before
    comparing; 0 leaves the images untouched. Deterministic.
    """
    if _np is None:
        return {"available": False, "reason": "numpy_missing"}
    ref = _load_rgb_array(Path(reference_path))
    sub = _load_rgb_array(Path(submission_path))
    if ref is None:
        return {"available": False, "reason": "missing_reference_screenshot"}
    if sub is None:
        return {"available": False, "reason": "missing_submission_screenshot"}
    ref, sub = _common_canvas(ref, sub)
    ref, sub = _align_vertical(ref, sub, y_shift)
    height, width = ref.shape[0], ref.shape[1]
    diff = _np.abs(ref.astype(_np.int16) - sub.astype(_np.int16)).max(axis=2)
    changed = diff > int(tolerance)
    keep = _np.ones((height, width), dtype=bool)
    keep = _apply_masks(keep, mask_regions, width, height)
    considered = int(keep.sum())
    if considered == 0:
        return {"available": True, "diff_ratio": 0.0, "considered_pixels": 0}
    changed_kept = int((changed & keep).sum())
    return {
        "available": True,
        "diff_ratio": round(changed_kept / considered, 6),
        "changed_pixels": changed_kept,
        "considered_pixels": considered,
        "tolerance": int(tolerance),
    }


def ssim_score(reference_path: Path, submission_path: Path, y_shift: int = 0) -> dict[str, Any]:
    """Mean SSIM over a sliding window (Wang et al. 2004), numpy-only.

    Grayscale, 7x7 uniform window, C1/C2 per the standard. `y_shift` (recorded
    scroll delta ref_y-sub_y) aligns the captures to the shared band first; 0
    leaves them untouched. Deterministic.
    """
    if _np is None:
        return {"available": False, "reason": "numpy_missing"}
    ref = _load_rgb_array(Path(reference_path))
    sub = _load_rgb_array(Path(submission_path))
    if ref is None:
        return {"available": False, "reason": "missing_reference_screenshot"}
    if sub is None:
        return {"available": False, "reason": "missing_submission_screenshot"}
    ref, sub = _common_canvas(ref, sub)
    ref, sub = _align_vertical(ref, sub, y_shift)
    # luminance (Rec.709) as float
    coeffs = _np.array([0.2126, 0.7152, 0.0722], dtype=_np.float64)
    a = ref.astype(_np.float64) @ coeffs
    b = sub.astype(_np.float64) @ coeffs

    def _box(mat, k: int = 7):
        # separable uniform blur via cumulative sums (deterministic, no cv2 dep)
        pad = k // 2
        padded = _np.pad(mat, pad, mode="reflect")
        csum = _np.cumsum(_np.cumsum(padded, axis=0), axis=1)
        csum = _np.pad(csum, ((1, 0), (1, 0)), mode="constant")
        h, w = mat.shape
        y0 = _np.arange(h)
        x0 = _np.arange(w)
        Y1, X1 = _np.meshgrid(y0 + k, x0 + k, indexing="ij")
        Y0, X0 = _np.meshgrid(y0, x0, indexing="ij")
        total = csum[Y1, X1] - csum[Y0, X1] - csum[Y1, X0] + csum[Y0, X0]
        return total / (k * k)

    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    mu_a = _box(a)
    mu_b = _box(b)
    mu_a2, mu_b2, mu_ab = mu_a * mu_a, mu_b * mu_b, mu_a * mu_b
    sig_a = _box(a * a) - mu_a2
    sig_b = _box(b * b) - mu_b2
    sig_ab = _box(a * b) - mu_ab
    ssim_map = ((2 * mu_ab + c1) * (2 * sig_ab + c2)) / (
        (mu_a2 + mu_b2 + c1) * (sig_a + sig_b + c2)
    )
    return {"available": True, "ssim": round(float(ssim_map.mean()), 6)}


def _mean_lab(arr):
    """Mean CIELAB color of an RGB uint8 array (uses cv2 if present, else sRGB->Lab)."""
    if _cv2 is not None:
        lab = _cv2.cvtColor(arr, _cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(_np.float64)
        # cv2 L in [0,255]; scale L back to [0,100] for a standard ΔE.
        lab[:, 0] *= 100.0 / 255.0
        lab[:, 1] -= 128.0
        lab[:, 2] -= 128.0
        return lab.mean(axis=0)
    # numpy sRGB -> XYZ -> Lab fallback (D65)
    rgb = arr.reshape(-1, 3).astype(_np.float64) / 255.0
    m = rgb > 0.04045
    rgb = _np.where(m, ((rgb + 0.055) / 1.055) ** 2.4, rgb / 12.92)
    x = rgb @ _np.array([0.4124, 0.3576, 0.1805])
    y = rgb @ _np.array([0.2126, 0.7152, 0.0722])
    z = rgb @ _np.array([0.0193, 0.1192, 0.9505])
    xyz = _np.stack([x / 0.95047, y, z / 1.08883], axis=1)
    d = xyz > 0.008856
    f = _np.where(d, _np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[:, 1] - 16
    A = 500 * (f[:, 0] - f[:, 1])
    B = 200 * (f[:, 1] - f[:, 2])
    return _np.stack([L, A, B], axis=1).mean(axis=0)


def _windowed_ssim_mean(a, b, k: int = 7) -> float:
    """Mean windowed SSIM (Wang et al. 2004) over two grayscale float arrays.

    Same 7x7 uniform-window formulation as `ssim_score`; factored out so block
    fidelity uses the identical structural metric instead of a single-window
    global approximation, which is unstable on thin/low-variance crops (e.g. a
    filter bar) and produced false mismatches even when the crop was near
    pixel-identical.
    """
    if a.size == 0 or b.size == 0:
        return 1.0
    pad = k // 2

    def _box(mat):
        padded = _np.pad(mat, pad, mode="reflect")
        csum = _np.cumsum(_np.cumsum(padded, axis=0), axis=1)
        csum = _np.pad(csum, ((1, 0), (1, 0)), mode="constant")
        h, w = mat.shape
        y0 = _np.arange(h)
        x0 = _np.arange(w)
        Y1, X1 = _np.meshgrid(y0 + k, x0 + k, indexing="ij")
        Y0, X0 = _np.meshgrid(y0, x0, indexing="ij")
        total = csum[Y1, X1] - csum[Y0, X1] - csum[Y1, X0] + csum[Y0, X0]
        return total / (k * k)

    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    mu_a, mu_b = _box(a), _box(b)
    mu_a2, mu_b2, mu_ab = mu_a * mu_a, mu_b * mu_b, mu_a * mu_b
    sig_a = _box(a * a) - mu_a2
    sig_b = _box(b * b) - mu_b2
    sig_ab = _box(a * b) - mu_ab
    ssim_map = ((2 * mu_ab + c1) * (2 * sig_ab + c2)) / (
        (mu_a2 + mu_b2 + c1) * (sig_a + sig_b + c2)
    )
    return float(ssim_map.mean())


def block_fidelity_score(
    reference_path: Path,
    submission_path: Path,
    blocks: list[dict],
    y_shift: int = 0,
) -> dict[str, Any]:
    """Design2Code-style block fidelity over component crops.

    For each block rect, compare reference vs submission crop on:
    structural similarity (block_match via SSIM on the crop) and mean color
    ΔE (Euclidean in Lab). Reports block_match rate + worst color ΔE +
    worst position drift (blocks are anchored, so position drift is measured
    by where the best-matching region actually lands — here we measure the
    in-place crop, so position_l_inf reflects content shift within the rect).

    Block rects are in the REFERENCE viewport frame (they come from the
    reference capture's crops). `y_shift` (recorded scroll delta ref_y-sub_y)
    is added to the submission sampling so the same document region is compared
    even when the two captures scrolled to different offsets; 0 samples in place.
    Deterministic.
    """
    if _np is None:
        return {"available": False, "reason": "numpy_missing"}
    ref = _load_rgb_array(Path(reference_path))
    sub = _load_rgb_array(Path(submission_path))
    if ref is None:
        return {"available": False, "reason": "missing_reference_screenshot"}
    if sub is None:
        return {"available": False, "reason": "missing_submission_screenshot"}
    ref, sub = _common_canvas(ref, sub)
    height, width = ref.shape[0], ref.shape[1]
    if not blocks:
        return {"available": True, "block_match": 1.0, "max_color_delta_e": 0.0, "blocks": 0}
    dy = int(y_shift)
    matched = 0
    worst_delta_e = 0.0
    per_block = []
    for blk in blocks:
        x = max(0, int(blk.get("x", 0)))
        y = max(0, int(blk.get("y", 0)))
        rw = int(blk.get("width", blk.get("w", 0)))
        rh = int(blk.get("height", blk.get("h", 0)))
        x1, y1 = min(width, x + rw), min(height, y + rh)
        if x1 <= x or y1 <= y:
            continue
        # Submission is sampled at the same rect shifted by the recorded scroll
        # delta so it lands on the same document content as the reference block.
        # When the block partially extends past the shared overlap band, CLIP it
        # to the band (rather than dropping the whole block) so the visible part
        # is still compared — dropping large blocks would leave only tiny/flaky
        # ones and understate coverage.
        y_lo = max(y, -dy)               # lowest ref-row whose sub-row (y+dy) >= 0
        y_hi = min(y1, height - dy)      # highest ref-row whose sub-row < height
        if y_hi <= y_lo:
            # No overlap at all for this block's document region → cannot compare.
            continue
        rc = ref[y_lo:y_hi, x:x1]
        sc = sub[y_lo + dy:y_hi + dy, x:x1]
        # structural: windowed SSIM on the crop (same 7x7 formulation as
        # ssim_score) — stable on thin/low-variance crops where the older
        # single-window global stats produced spurious mismatches.
        coeffs = _np.array([0.2126, 0.7152, 0.0722])
        ga = rc.astype(_np.float64) @ coeffs
        gb = sc.astype(_np.float64) @ coeffs
        block_ssim = _windowed_ssim_mean(ga, gb)
        delta_e = float(_np.linalg.norm(_mean_lab(rc) - _mean_lab(sc)))
        worst_delta_e = max(worst_delta_e, delta_e)
        is_match = block_ssim >= 0.8 and delta_e <= 15.0
        matched += 1 if is_match else 0
        per_block.append(
            {
                "rect": [x, y, rw, rh],
                "block_ssim": round(float(block_ssim), 4),
                "color_delta_e": round(delta_e, 3),
                "match": bool(is_match),
                "kind": blk.get("kind"),
            }
        )
    total = len(per_block)
    return {
        "available": True,
        "block_match": round(matched / total, 4) if total else 1.0,
        "max_color_delta_e": round(worst_delta_e, 3),
        "blocks": total,
        "per_block": per_block,
    }
