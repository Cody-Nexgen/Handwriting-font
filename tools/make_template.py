#!/usr/bin/env python3
"""Generate the printable handwriting sample sheets.

Outputs:
  template/handwriting-template.pdf  - print this, fill in every box with pencil
  template/layout.json               - cell geometry, used later by the extractor

Each character gets one ROW; the row's boxes are the repeat copies. Four black
corner squares on every page let the extractor straighten and crop a photo.
"""
import json
import os

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = letter  # 612 x 792 pt

# (group name, characters, copies per character)
GROUPS = [
    ("lowercase", list("abcdefghijklmnopqrstuvwxyz"), 8),
    ("uppercase", list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), 5),
    ("digits", list("0123456789"), 5),
    ("punctuation", list(".,?!'\"-:;()/&@#$%"), 4),
]

PUNCT_NAMES = {
    ".": "period", ",": "comma", "?": "question", "!": "exclaim", "'": "apostrophe",
    '"': "quote", "-": "dash", ":": "colon", ";": "semicolon", "(": "paren open",
    ")": "paren close", "/": "slash", "&": "ampersand", "@": "at", "#": "hash",
    "$": "dollar", "%": "percent",
}

FREE_PROMPTS = [
    "The quick brown fox jumps over the lazy dog.",
    "Pack my box with five dozen liquor jugs!",
    "How vexingly quick daft zebras jump?",
    "Sphinx of black quartz, judge my vow.",
    "Meet me at 7:45 on Friday (bring snacks & a charger).",
    "Dear Sam, thanks for everything - see you on 12/20!",
]

MARGIN = 54
FID = 16            # corner square size
HEADER_H = 46
FOOTER_H = 26
LABEL_W = 52
COLS = 8
ROW_H = 46
GRID_COLOR = (0.62, 0.66, 0.74)
GUIDE_COLOR = (0.80, 0.85, 0.95)  # pale blue baseline: easy to filter out of a scan

GRID_X0 = MARGIN
GRID_TOP = PAGE_H - MARGIN - HEADER_H
GRID_BOTTOM = MARGIN + FOOTER_H
CELL_W = (PAGE_W - 2 * MARGIN - LABEL_W) / COLS
ROWS_PER_PAGE = int((GRID_TOP - GRID_BOTTOM) // ROW_H)


def fiducials(c):
    c.setFillColorRGB(0, 0, 0)
    inset = 22
    for x in (inset, PAGE_W - inset - FID):
        for y in (inset, PAGE_H - inset - FID):
            c.rect(x, y, FID, FID, stroke=0, fill=1)


def header(c, title, page_no, total):
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(MARGIN, PAGE_H - MARGIN - 12, title)
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    c.drawString(
        MARGIN, PAGE_H - MARGIN - 26,
        "Pencil only. Write like you ACTUALLY write - messy is good. Sit on the faint line. "
        "Don't touch the box edges.",
    )
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN - 12, f"Page {page_no}/{total}")
    c.setFillColorRGB(0, 0, 0)


def footer(c, text):
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.drawCentredString(PAGE_W / 2, MARGIN - 4, text)
    c.setFillColorRGB(0, 0, 0)


def build_pages():
    """Flow each group's characters onto pages; returns a list of page dicts."""
    pages = []
    for group, chars, copies in GROUPS:
        for i in range(0, len(chars), ROWS_PER_PAGE):
            pages.append({"group": group, "copies": copies, "chars": chars[i:i + ROWS_PER_PAGE]})
    return pages


def draw_grid_page(c, page, page_no, total, layout_pages):
    group, copies, chars = page["group"], page["copies"], page["chars"]
    fiducials(c)
    header(c, f"Handwriting samples - {group} ({copies} copies each)", page_no, total)

    cells = []
    for r, ch in enumerate(chars):
        y_top = GRID_TOP - r * ROW_H
        y_bot = y_top - ROW_H

        # row label (the character itself, big, plus a name for punctuation)
        c.setFillColorRGB(0.15, 0.15, 0.15)
        c.setFont("Helvetica-Bold", 18)
        c.drawCentredString(GRID_X0 + LABEL_W / 2, y_bot + ROW_H / 2 - 2, ch)
        if ch in PUNCT_NAMES:
            c.setFont("Helvetica", 6)
            c.drawCentredString(GRID_X0 + LABEL_W / 2, y_bot + 6, PUNCT_NAMES[ch])

        for k in range(COLS):
            x0 = GRID_X0 + LABEL_W + k * CELL_W
            used = k < copies
            c.setStrokeColorRGB(*GRID_COLOR)
            c.setLineWidth(0.7 if used else 0.3)
            if used:
                c.rect(x0, y_bot, CELL_W, ROW_H, stroke=1, fill=0)
                # baseline guide ~30% up from the bottom of the cell
                c.setStrokeColorRGB(*GUIDE_COLOR)
                c.setLineWidth(0.6)
                c.line(x0 + 3, y_bot + ROW_H * 0.30, x0 + CELL_W - 3, y_bot + ROW_H * 0.30)
                cells.append({
                    "char": ch, "copy": k,
                    "x": x0, "y": y_bot, "w": CELL_W, "h": ROW_H,
                })
    footer(c, "handwriting-font sample sheet")
    layout_pages.append({"page": page_no, "type": "grid", "group": group, "cells": cells})


def draw_free_page(c, page_no, total, layout_pages):
    fiducials(c)
    header(c, "Normal writing - just write the lines below, twice", page_no, total)
    y = PAGE_H - MARGIN - HEADER_H - 14
    lines = []
    c.setFont("Helvetica", 8)
    for prompt in FREE_PROMPTS:
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.setFont("Helvetica", 8)
        c.drawString(MARGIN, y, prompt)
        y -= 6
        # two blank writing lines per prompt
        for _ in range(2):
            y -= 34
            c.setStrokeColorRGB(*GUIDE_COLOR)
            c.setLineWidth(0.8)
            c.line(MARGIN, y, PAGE_W - MARGIN, y)
            lines.append({"prompt": prompt, "baseline_y": y})
        y -= 12
    c.setFillColorRGB(0, 0, 0)
    footer(c, "handwriting-font sample sheet")
    layout_pages.append({"page": page_no, "type": "free", "lines": lines})


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "..", "template")
    os.makedirs(out_dir, exist_ok=True)
    pdf_path = os.path.join(out_dir, "handwriting-template.pdf")

    grid_pages = build_pages()
    total = len(grid_pages) + 1
    layout_pages = []

    c = canvas.Canvas(pdf_path, pagesize=letter)
    c.setTitle("Handwriting sample sheets")
    for i, page in enumerate(grid_pages, start=1):
        draw_grid_page(c, page, i, total, layout_pages)
        c.showPage()
    draw_free_page(c, total, total, layout_pages)
    c.showPage()
    c.save()

    layout = {
        "page_size_pt": [PAGE_W, PAGE_H],
        "fiducial": {"size": FID, "inset": 22},
        "cols": COLS,
        "pages": layout_pages,
    }
    with open(os.path.join(out_dir, "layout.json"), "w") as f:
        json.dump(layout, f, indent=2)

    n_cells = sum(len(p.get("cells", [])) for p in layout_pages)
    print(f"wrote {pdf_path}: {total} pages, {n_cells} character boxes")


if __name__ == "__main__":
    main()
