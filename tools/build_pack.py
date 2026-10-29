#!/usr/bin/env python3
"""Turn extracted glyphs into a web-ready pack: trimmed PNGs + pack.json.

pack.json holds, per character, a list of usable copies with the pen-baseline position
inside each image, plus spacing statistics learned from the written sentences page.
Everything is measured in the glyphs' native pixels (4 px per PDF point).

Usage: python3 tools/build_pack.py --glyphs data/glyphs --out app/pack
"""
import argparse
import json
import os
import shutil
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_glyphs as eg  # noqa: E402

XH_CHARS = "acemnorsuvwxz"   # lowercase letters with no ascender or descender
DESC_BODY = "gpqy"           # descender letters whose ink top sits at x-height
PAD = 2
MIN_COPIES = 3


def ink_box(alpha):
    ys, xs = np.nonzero(alpha > 15)
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def clipped(e, alpha):
    """True if ink runs into the edge of the crop (i.e. the pencil crossed the box line)."""
    l, t, r, b = e["cell_page"]
    il, it, ir, ib = round(l) + eg.INSET, round(t) + eg.INSET, round(r) - eg.INSET, round(b) - eg.INSET
    bx0, by0, bx1, by1 = e["bbox_page"]
    strong = 0.35 * 255
    return any([
        abs(bx0 - il) <= 1 and alpha[:, 0].max() >= strong,
        abs(bx1 - ir) <= 1 and alpha[:, -1].max() >= strong,
        abs(by0 - it) <= 1 and alpha[0, :].max() >= strong,
        abs(by1 - ib) <= 1 and alpha[-1, :].max() >= strong,
    ])


def robust_outliers(vals, z=3.2):
    vals = np.asarray(vals, float)
    med = np.median(vals)
    scale = 1.4826 * np.median(np.abs(vals - med)) + 0.08 * med
    return np.abs(vals - med) / scale > z


def load_glyphs(glyph_dir):
    m = json.load(open(os.path.join(glyph_dir, "manifest.json")))["glyphs"]
    out = []
    for e in m:
        if not e.get("file"):
            continue
        img = cv2.imread(os.path.join(glyph_dir, "glyphs", e["file"]), cv2.IMREAD_UNCHANGED)
        alpha = img[..., 3]
        x0, y0, x1, y1 = ink_box(alpha)
        e = dict(e)
        e.update({"img": img, "alpha": alpha, "ink": (x0, y0, x1, y1),
                  "ink_w": x1 - x0, "ink_h": y1 - y0, "clipped": bool(clipped(e, alpha))})
        out.append(e)
    return out


def select_usable(glyphs):
    by_char = {}
    for g in glyphs:
        by_char.setdefault(g["char"], []).append(g)
    excluded = []
    for ch, gs in by_char.items():
        out_h = robust_outliers([g["ink_h"] for g in gs]) if len(gs) >= 5 else np.zeros(len(gs), bool)
        out_w = robust_outliers([g["ink_w"] for g in gs]) if len(gs) >= 5 else np.zeros(len(gs), bool)
        for g, oh, ow in zip(gs, out_h, out_w):
            reasons = []
            if g["clipped"]:
                reasons.append("clipped")
            if oh or ow:
                reasons.append("size_outlier")
            if g.get("darkness_p90", 1) < 0.3:
                reasons.append("too_faint")
            g["bad"] = reasons
        good = [g for g in gs if not g["bad"]]
        if len(good) < MIN_COPIES:  # keep the least-bad ones rather than starve the character
            rescue = sorted((g for g in gs if g["bad"]), key=lambda g: (len(g["bad"]), -g["ink_px"]))
            for g in rescue[:MIN_COPIES - len(good)]:
                g["bad"] = []
                g["rescued"] = True
        for g in gs:
            if g["bad"]:
                excluded.append({"char": ch, "copy": g["copy"], "reasons": g["bad"]})
    return by_char, excluded


def measure_metrics(by_char):
    """x-height, i-height, and where the user's baseline sits relative to the printed guide."""
    xh_vals = [g["ink_h"] for ch in XH_CHARS for g in by_char.get(ch, []) if not g["bad"]]
    xh = float(np.median(xh_vals))
    i_h = float(np.median([g["ink_h"] for g in by_char.get("i", []) if not g["bad"]]))
    base_chars = [c for c in "abcdefhiklmnorstuvwxz" if c in by_char]
    offs = [g["baseline_y"] - g["ink"][3] for c in base_chars for g in by_char[c] if not g["bad"]]
    b0 = float(np.median(offs))  # > 0: user's letters sit above the guide line
    return xh, i_h, b0


def baseline_in_image(ch, g, xh, i_h, b0):
    """y (image px, from top) where the pen baseline falls for this glyph."""
    x0, y0, x1, y1 = g["ink"]
    if ch in DESC_BODY:
        return y0 + xh
    if ch == "j":
        return y0 + i_h
    if ch.isalnum():
        return float(y1)
    return g["baseline_y"] - b0  # punctuation: keep where the user put it relative to the guide


def spacing_from_sentences(glyph_dir, layout, xh):
    """Learn letter gaps and word gaps (in x-heights) from the handwritten sentence lines."""
    path = os.path.join(glyph_dir, "pages", "page8.png")
    page = [p for p in layout["pages"] if p["type"] == "free"][0]
    dark = eg.darkness_map(cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2GRAY))
    x0, x1 = 54 * eg.SCALE, (eg.PAGE_W_PT - 54) * eg.SCALE
    gaps = []
    for line in page["lines"]:
        nominal = (eg.PAGE_H_PT - line["baseline_y"]) * eg.SCALE
        band = (dark[:, x0:x1] >= 8).sum(axis=1).astype(float)
        lo, hi = int(nominal) - 30, int(nominal) + 30
        gy = eg._peak(band, lo, hi, x1 - x0, 0.5)
        gy = nominal if gy is None else gy
        strip = dark[int(gy) - 100:int(gy) - 6, x0:x1] >= 26
        n, lab, stats, _ = cv2.connectedComponentsWithStats(strip.astype(np.uint8), connectivity=8)
        keep = np.zeros(n, bool)
        keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= 10
        cols = keep[lab].any(axis=0)
        xs = np.flatnonzero(cols)
        if len(xs) < 2:
            continue
        runs_start = [xs[0]] + [xs[i] for i in range(1, len(xs)) if xs[i] != xs[i - 1] + 1]
        runs_end = [xs[i - 1] + 1 for i in range(1, len(xs)) if xs[i] != xs[i - 1] + 1] + [xs[-1] + 1]
        for a, b in zip(runs_end[:-1], runs_start[1:]):
            gaps.append(b - a)
    gaps = np.array(gaps, float) / xh
    # split letter gaps from word gaps with a 1-D 2-means
    lo_c, hi_c = np.percentile(gaps, 25), np.percentile(gaps, 90)
    for _ in range(30):
        assign = np.abs(gaps - lo_c) < np.abs(gaps - hi_c)
        lo_c, hi_c = gaps[assign].mean(), gaps[~assign].mean()
    cut = (lo_c + hi_c) / 2
    letter, word = gaps[gaps < cut], gaps[gaps >= cut]
    return {
        "cut": float(cut),
        "letter": [round(float(v), 3) for v in np.sort(letter)],
        "word": [round(float(v), 3) for v in np.sort(word)],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glyphs", default="data/glyphs")
    ap.add_argument("--out", default="app/pack")
    ap.add_argument("--layout", default=os.path.join(os.path.dirname(__file__), "..", "template", "layout.json"))
    args = ap.parse_args()

    glyphs = load_glyphs(args.glyphs)
    by_char, excluded = select_usable(glyphs)
    xh, i_h, b0 = measure_metrics(by_char)

    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    os.makedirs(os.path.join(args.out, "g"))

    pack_glyphs = {}
    for ch, gs in sorted(by_char.items()):
        for g in gs:
            if g["bad"]:
                continue
            x0, y0, x1, y1 = g["ink"]
            cx0, cy0 = max(x0 - PAD, 0), max(y0 - PAD, 0)
            cx1, cy1 = min(x1 + PAD, g["img"].shape[1]), min(y1 + PAD, g["img"].shape[0])
            crop = g["img"][cy0:cy1, cx0:cx1]
            base = baseline_in_image(ch, g, xh, i_h, b0) - cy0
            name = f"{g['group']}_{ch if ch.isalnum() else 'u%04x' % ord(ch)}_{g['copy']:02d}.png"
            cv2.imwrite(os.path.join(args.out, "g", name), crop, [cv2.IMWRITE_PNG_COMPRESSION, 9])
            pack_glyphs.setdefault(ch, []).append({
                "f": f"g/{name}", "w": int(crop.shape[1]), "h": int(crop.shape[0]),
                "b": round(float(base), 1), "pad": int(x0 - cx0),
                "d": round(float(g.get("darkness_p90", 1.0)), 2),
            })

    layout = json.load(open(args.layout))
    spacing = spacing_from_sentences(args.glyphs, layout, xh)
    pack = {
        "scale_px_per_pt": eg.SCALE, "xh": round(xh, 1), "i_h": round(i_h, 1),
        "guide_offset": round(b0, 1), "spacing": spacing, "glyphs": pack_glyphs,
    }
    json.dump(pack, open(os.path.join(args.out, "pack.json"), "w"), separators=(",", ":"))
    json.dump(excluded, open(os.path.join(args.out, "excluded.json"), "w"), indent=1)

    total = sum(len(v) for v in pack_glyphs.values())
    size = sum(os.path.getsize(os.path.join(args.out, "g", f)) for f in os.listdir(os.path.join(args.out, "g")))
    print(f"{len(pack_glyphs)} characters, {total} glyphs kept, {len(excluded)} excluded, {size/1024:.0f} KB of PNGs")
    print(f"x-height {xh:.0f}px ({xh/eg.SCALE:.1f}pt), i-height {i_h:.0f}px, guide offset {b0:.1f}px")
    print(f"spacing (x-heights): {len(spacing['letter'])} letter gaps median {np.median(spacing['letter']):.2f}, "
          f"{len(spacing['word'])} word gaps median {np.median(spacing['word']):.2f}, cut {spacing['cut']:.2f}")
    thin = {c: len(v) for c, v in pack_glyphs.items() if len(v) < 4}
    print("characters with <4 usable copies:", thin or "none")


if __name__ == "__main__":
    main()
