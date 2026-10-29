#!/usr/bin/env python3
"""Cut individual handwritten characters out of photographed template sheets.

Pipeline per page:
  1. find the four black corner squares and warp the photo flat (rectify)
  2. convert to "darkness" (0 = paper, 255 = black) with lighting flattened, then
     measure where every printed box REALLY is (paper curls, so layout.json drifts)
  3. subtract the printed baseline guide, find the pencil ink, crop each glyph to RGBA
  4. write a manifest and per-page contact sheets for eyeballing

Usage:
  python3 tools/extract_glyphs.py --out data/glyphs \
      data/scans/raw/setB-000.jpg:1 data/scans/raw/setA-000.jpg:2 ...
Each input is  <image>:<template page number>.
"""
import argparse
import json
import os

import cv2
import numpy as np

SCALE = 4  # warped pixels per PDF point
PAGE_W_PT, PAGE_H_PT = 612, 792
FID_CENTERS_PT = {  # top-down coordinates of the fiducial centers
    "tl": (30, 30), "tr": (PAGE_W_PT - 30, 30),
    "bl": (30, PAGE_H_PT - 30), "br": (PAGE_W_PT - 30, PAGE_H_PT - 30),
}

# thresholds are in "darkness" units: pencil ~40-70, printed grid/guide ~18-28, paper ~0
LINE_DARK = 10        # a pixel this dark counts toward a printed line
WEAK_DARK = 10        # pencil, including faint strokes (must connect to a strong pixel)
STRONG_DARK = 22
EMPTY_P999 = 18       # a cell whose darkest 0.1% is fainter than this has no ink
INSET = 8             # shrink cells by this many px so box lines stay out of the crop
PENCIL_RGB = (58, 58, 64)
PAD = 3
GUIDE_HALF = 4        # px either side of the guide line that get subtracted
MAX_PART_GAP = 80     # a separate blob this far from the main stroke is treated as a stray speck
MIN_PART_AREA = 12    # ...as is a blob smaller than this (i/j dots are bigger)


# ---------------------------------------------------------------- rectify
def find_fiducials(gray):
    """Return {corner: (x, y)} centers of the four black squares, or raise."""
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    found = {}
    for thr in (90, 120, 150):
        _, bw = cv2.threshold(blur, thr, 255, cv2.THRESH_BINARY_INV)
        n, _, stats, cents = cv2.connectedComponentsWithStats(bw, connectivity=8)
        cands = {k: [] for k in FID_CENTERS_PT}
        for i in range(1, n):
            x, y, bw_, bh_, area = stats[i]
            side = (bw_ + bh_) / 2
            if not (0.012 * w < side < 0.06 * w):
                continue
            if not (0.7 < bw_ / bh_ < 1.43):
                continue
            fill = area / float(bw_ * bh_)
            if fill < 0.72:
                continue
            cx, cy = cents[i]
            corner = ("t" if cy < h / 2 else "b") + ("l" if cx < w / 2 else "r")
            if not ((cx < 0.28 * w or cx > 0.72 * w) and (cy < 0.28 * h or cy > 0.72 * h)):
                continue
            cands[corner].append((fill, (float(cx), float(cy))))
        found = {k: max(v)[1] for k, v in cands.items() if v}
        if len(found) == 4:
            return found
    missing = [k for k in FID_CENTERS_PT if k not in found]
    raise RuntimeError(f"could not find corner squares: {missing}")


def rectify(img_bgr):
    """Warp a page photo flat using its corner squares. Returns (warped_bgr, fiducials)."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    fids = find_fiducials(gray)
    src = np.float32([fids[k] for k in ("tl", "tr", "bl", "br")])
    dst = np.float32([[FID_CENTERS_PT[k][0] * SCALE, FID_CENTERS_PT[k][1] * SCALE]
                      for k in ("tl", "tr", "bl", "br")])
    H = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(
        img_bgr, H, (PAGE_W_PT * SCALE, PAGE_H_PT * SCALE),
        flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255),
    )
    return out, fids


def darkness_map(gray):
    """Flatten uneven lighting and return float darkness: 0 = paper, 255 = black."""
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8))
    bg = cv2.GaussianBlur(bg, (0, 0), 25)
    flat = gray.astype(np.float32) / np.maximum(bg, 1) * 255
    flat = cv2.GaussianBlur(np.clip(flat, 0, 255), (0, 0), 1.0)
    return 255.0 - flat


# ------------------------------------------------------------ grid finding
def grid_of(page_layout):
    """Nominal grid in warped pixels: row/col boundaries + (row, col, char, copy) per cell."""
    cells = page_layout["cells"]
    xs = sorted({round(c["x"], 3) for c in cells})
    ys = sorted({round(c["y"], 3) for c in cells}, reverse=True)  # top row first
    cell_w, cell_h = cells[0]["w"], cells[0]["h"]
    col_edges = [x * SCALE for x in xs] + [(xs[-1] + cell_w) * SCALE]
    row_edges = [(PAGE_H_PT - (y + cell_h)) * SCALE for y in ys] + [(PAGE_H_PT - ys[-1]) * SCALE]
    index = []
    for c in cells:
        index.append((ys.index(round(c["y"], 3)), xs.index(round(c["x"], 3)), c["char"], c["copy"]))
    return row_edges, col_edges, index


def _peak(counts, lo, hi, span, min_frac):
    """Index of the strongest line-like peak of `counts` within [lo, hi), or None."""
    lo, hi = max(int(lo), 0), min(int(hi), len(counts))
    if hi - lo < 5:
        return None
    sm = np.convolve(counts, np.ones(3), "same") / 3.0
    seg = sm[lo:hi]
    i = int(np.argmax(seg))
    if seg[i] < min_frac * span:
        return None
    return lo + i


def _track(counts_fn, nominal, span_fn, first_win=45, next_win=22, min_frac=0.3):
    """Follow a family of parallel grid lines: line i is predicted from line i-1.
    Returns (positions, fallback_flags)."""
    pos, fell = [], []
    for i, nom in enumerate(nominal):
        if i == 0:
            center, win = nom, first_win
        else:
            center, win = pos[-1] + (nom - nominal[i - 1]), next_win
        counts, span = counts_fn(i)
        p = _peak(counts, center - win, center + win, span, min_frac)
        pos.append(center if p is None else float(p))
        fell.append(p is None)
    return pos, fell


def refine_grid(dark, row_edges, col_edges):
    """Measure the true boundaries. Returns hy[r][c], vx[r][k] and fallback counts."""
    lines = dark >= LINE_DARK
    n_rows, n_cols = len(row_edges) - 1, len(col_edges) - 1

    hy = np.zeros((n_rows + 1, n_cols))
    hfell = 0
    for c in range(n_cols):
        xa, xb = int(col_edges[c]) + 25, int(col_edges[c + 1]) - 25
        counts = lines[:, xa:xb].sum(axis=1).astype(float)
        pos, fell = _track(lambda i: (counts, xb - xa), row_edges, None)
        hy[:, c] = pos
        hfell += sum(fell)

    vx = np.zeros((n_rows, n_cols + 1))
    vfell = 0
    for r in range(n_rows):
        ya = int(min(hy[r]) ) + 25
        yb = int(max(hy[r + 1])) - 25
        counts = lines[ya:yb, :].sum(axis=0).astype(float)
        pos, fell = _track(lambda i: (counts, yb - ya), col_edges, None)
        vx[r, :] = pos
        vfell += sum(fell)
    return hy, vx, hfell + vfell


def find_guide(dark, left, right, top, bottom):
    """Fit the pale printed baseline guide: returns (slope, intercept, darkness, found)."""
    h = bottom - top
    expect = bottom - 0.30 * h
    xs = np.linspace(left + INSET, right - INSET, 4).astype(int)
    pts = []
    for a, b in zip(xs[:-1], xs[1:]):
        band = dark[:, a:b] >= 8
        counts = band.sum(axis=1).astype(float)
        y = _peak(counts, expect - 25, expect + 25, b - a, 0.5)
        if y is not None:
            pts.append(((a + b) / 2.0, float(y)))
    found = len(pts) >= 2
    if found:
        px, py = zip(*pts)
        m, c = np.polyfit(px, py, 1)
    else:
        m, c = 0.0, expect
    # how dark is the guide itself? sample along the fitted line
    x0, x1 = int(left) + INSET, int(right) - INSET
    samples = []
    for x in range(x0, x1):
        y = int(round(m * x + c))
        samples.append(dark[y - 1:y + 2, x].max())
    gdark = float(np.median(samples)) if samples else 0.0
    return m, c, min(gdark, 35.0), found


# -------------------------------------------------------------- extraction
def extract_cell(dark, left, right, top, bottom):
    """Find the pencil ink in one cell. Returns a dict (with float `darkness` crop) or None."""
    l, r = int(round(left)) + INSET, int(round(right)) - INSET
    t, b = int(round(top)) + INSET, int(round(bottom)) - INSET
    crop = dark[t:b, l:r].copy()
    ch, cw = crop.shape

    # subtract the printed guide's own darkness along its line; pencil darker than it survives
    m, c, gdark, guide_found = find_guide(dark, left, right, top, bottom)
    gy = m * (np.arange(cw) + l) + c - t
    for x in range(cw):
        y0, y1 = max(int(round(gy[x])) - GUIDE_HALF, 0), max(int(round(gy[x])) + GUIDE_HALF + 1, 0)
        crop[y0:y1, x] = np.maximum(crop[y0:y1, x] - gdark, 0)

    if np.percentile(crop, 99.9) < EMPTY_P999:
        return None

    # hysteresis: weak ink counts only if its blob also contains strong ink
    weak = (crop >= WEAK_DARK).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(weak, connectivity=8)
    keep = np.zeros(n, bool)
    keep[np.unique(lab[crop >= STRONG_DARK])] = True
    keep[0] = False
    keep &= stats[:, cv2.CC_STAT_AREA] >= 6

    # printed-line remnants: very long, very thin blobs
    for i in np.flatnonzero(keep):
        w_, h_ = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if (w_ > 0.7 * cw and h_ < 10) or (h_ > 0.7 * ch and w_ < 10):
            keep[i] = False
    if not keep.any():
        return None

    # drop specks far from the main stroke mass (but keep i/j dots, colons, etc.)
    areas = np.where(keep, stats[:, cv2.CC_STAT_AREA], 0)
    main = int(np.argmax(areas))
    mx, my, mw, mh = (stats[main, k] for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                                               cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
    for i in np.flatnonzero(keep):
        if i == main:
            continue
        x, y, w_, h_ = (stats[i, k] for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                                              cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        gap = max(max(mx - (x + w_), x - (mx + mw), 0), max(my - (y + h_), y - (my + mh), 0))
        if gap > MAX_PART_GAP or stats[i, cv2.CC_STAT_AREA] < MIN_PART_AREA:
            keep[i] = False
            continue
        # leftover dashes of the printed guide: flat-ish blobs lying wholly inside its band
        cy0, cy1 = y, y + h_
        band_lo = gy[min(max(x, 0), cw - 1)] - GUIDE_HALF - 2
        band_hi = gy[min(max(x + w_ - 1, 0), cw - 1)] + GUIDE_HALF + 2
        if w_ >= 8 and h_ <= 2 * GUIDE_HALF + 3 and cy0 >= min(band_lo, band_hi) and cy1 <= max(band_lo, band_hi) + 1:
            keep[i] = False

    ink = keep[lab]
    ys, xs_ = np.nonzero(ink)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs_.min(), xs_.max() + 1

    grown = cv2.dilate(ink.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    gy0, gx0 = max(y0 - PAD, 0), max(x0 - PAD, 0)
    gy1, gx1 = min(y1 + PAD, ch), min(x1 + PAD, cw)
    gl = np.where(grown, crop, 0.0)[gy0:gy1, gx0:gx1].astype(np.float32)

    guide_page_y = m * (left + right) / 2 + c
    flags = []
    margin = 3
    if y0 <= margin or x0 <= margin or y1 >= ch - margin or x1 >= cw - margin:
        flags.append("touches_border")
    if not guide_found:
        flags.append("guide_guessed")
    return {
        "darkness": gl,
        "ink_vals": crop[ink].astype(np.float32),
        "bbox_page": [int(l + gx0), int(t + gy0), int(l + gx1), int(t + gy1)],
        "baseline_y": float(guide_page_y - (t + gy0)),
        "ink_px": int(ink.sum()),
        "flags": flags,
        "cell_page": [float(left), float(top), float(right), float(bottom)],
    }


def glyph_path(group, ch, copy):
    name = ch if ch.isalnum() else f"u{ord(ch):04x}"
    return os.path.join(group, f"{name}_{copy:02d}.png")


def process_page(dark, page_layout):
    row_edges, col_edges, index = grid_of(page_layout)
    hy, vx, fallbacks = refine_grid(dark, row_edges, col_edges)
    results = []
    for (r, k, ch, copy) in index:
        res = extract_cell(dark, vx[r][k], vx[r][k + 1], hy[r][k], hy[r + 1][k])
        results.append(((ch, copy), res))
    return results, fallbacks


def write_page(results, page_layout, out_dir):
    """Normalize pencil darkness per page, write PNGs, return manifest entries."""
    allv = [res["ink_vals"] for _, res in results if res is not None]
    ref = float(np.percentile(np.concatenate(allv), 98)) if allv else 60.0
    entries = []
    for (ch, copy), res in results:
        e = {"char": ch, "copy": copy, "group": page_layout["group"], "page": page_layout["page"]}
        if res is None:
            e.update({"file": None, "flags": ["empty"]})
            entries.append(e)
            continue
        alpha = np.clip(res.pop("darkness") / ref, 0, 1)
        vals = res.pop("ink_vals")
        rgba = np.zeros((*alpha.shape, 4), np.uint8)
        rgba[..., 0], rgba[..., 1], rgba[..., 2] = PENCIL_RGB[2], PENCIL_RGB[1], PENCIL_RGB[0]  # BGR
        rgba[..., 3] = (alpha * 255).astype(np.uint8)
        rel = glyph_path(page_layout["group"], ch, copy)
        full = os.path.join(out_dir, "glyphs", rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        cv2.imwrite(full, rgba)
        if np.percentile(vals, 90) < 0.5 * ref:
            res["flags"].append("faint")
        e.update({"file": rel.replace(os.sep, "/"), "darkness_p90": float(np.percentile(vals, 90) / ref), **res})
        entries.append(e)
    return entries, ref


# ----------------------------------------------------------- contact sheet
def contact_sheet(page_layout, entries, out_dir, scale=0.5):
    row_edges, col_edges, index = grid_of(page_layout)
    n_rows, n_cols = len(row_edges) - 1, max(k for _, k, _, _ in index) + 1
    cw, chh = int(232 * scale), int(184 * scale)
    label_w = 40
    sheet = np.full((n_rows * chh, label_w + n_cols * cw, 3), 255, np.uint8)
    by_key = {(e["char"], e["copy"]): e for e in entries}
    for (r, k, ch, copy) in index:
        x0, y0 = label_w + k * cw, r * chh
        cv2.rectangle(sheet, (x0, y0), (x0 + cw - 1, y0 + chh - 1), (225, 225, 225), 1)
        if k == 0:
            cv2.putText(sheet, ch, (6, y0 + chh // 2 + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
        e = by_key[(ch, copy)]
        if not e.get("file"):
            cv2.line(sheet, (x0, y0), (x0 + cw, y0 + chh), (0, 0, 255), 1)
            continue
        g = cv2.imread(os.path.join(out_dir, "glyphs", e["file"]), cv2.IMREAD_UNCHANGED)
        g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        cl, ct, _, _ = e["cell_page"]
        bx0, by0, _, _ = e["bbox_page"]
        px = x0 + int((bx0 - cl) * scale)
        py = y0 + int((by0 - ct) * scale)
        by = y0 + int((by0 + e["baseline_y"] - ct) * scale)
        cv2.line(sheet, (x0 + 2, by), (x0 + cw - 3, by), (255, 215, 170), 1)
        gh, gw = g.shape[:2]
        sx0, sy0 = max(0, x0 - px), max(0, y0 - py)
        px, py = max(px, x0), max(py, y0)
        gh2, gw2 = min(gh - sy0, y0 + chh - py), min(gw - sx0, x0 + cw - px)
        if gh2 > 0 and gw2 > 0:
            a = g[sy0:sy0 + gh2, sx0:sx0 + gw2, 3:4].astype(np.float32) / 255
            reg = sheet[py:py + gh2, px:px + gw2].astype(np.float32)
            sheet[py:py + gh2, px:px + gw2] = (reg * (1 - a) + g[sy0:sy0 + gh2, sx0:sx0 + gw2, :3] * a).astype(np.uint8)
        if e["flags"]:
            cv2.rectangle(sheet, (x0 + 1, y0 + 1), (x0 + cw - 2, y0 + chh - 2), (0, 0, 255), 2)
    path = os.path.join(out_dir, "contact", f"page{page_layout['page']}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, sheet)
    return path


def parse_inputs(items):
    pairs = []
    for it in items:
        path, _, page = it.rpartition(":")
        pairs.append((path, int(page)))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="<image>:<template page number>")
    ap.add_argument("--out", default="data/glyphs")
    ap.add_argument("--layout", default=os.path.join(os.path.dirname(__file__), "..", "template", "layout.json"))
    args = ap.parse_args()

    layout = json.load(open(args.layout))
    pages = {p["page"]: p for p in layout["pages"]}
    os.makedirs(os.path.join(args.out, "pages"), exist_ok=True)

    manifest, page_refs = [], {}
    for path, page_no in parse_inputs(args.inputs):
        img = cv2.imread(path)
        if img is None:
            raise SystemExit(f"cannot read {path}")
        warped, _ = rectify(img)
        cv2.imwrite(os.path.join(args.out, "pages", f"page{page_no}.png"), warped)
        page = pages[page_no]
        if page["type"] != "grid":
            print(f"page {page_no}: rectified ({page['type']} page, no glyph extraction)")
            continue
        dark = darkness_map(cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY))
        results, fallbacks = process_page(dark, page)
        entries, ref = write_page(results, page, args.out)
        manifest += entries
        page_refs[page_no] = ref
        sheet = contact_sheet(page, entries, args.out)
        flagged = sum(1 for e in entries if e["flags"])
        empty = sum(1 for e in entries if "empty" in e["flags"])
        print(f"page {page_no}: {len(entries)} cells, {empty} empty, {flagged} flagged, "
              f"{fallbacks} grid-line fallbacks, pencil ref {ref:.0f} -> {sheet}")

    with open(os.path.join(args.out, "manifest.json"), "w") as f:
        json.dump({"scale_px_per_pt": SCALE, "page_dark_ref": page_refs, "glyphs": manifest}, f, indent=1)
    print(f"manifest: {len(manifest)} entries")


if __name__ == "__main__":
    main()
