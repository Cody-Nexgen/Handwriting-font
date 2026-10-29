# Handwriting-font

Type text and get it back in *your* handwriting: real pencil glyphs cut from your own
sample sheets, randomized per letter, drawn on notebook paper.

## Pipeline

1. `python3 tools/make_template.py` - writes `template/handwriting-template.pdf` (+ `layout.json`).
   Print at 100% scale, fill every box in pencil, photograph/scan each page flat.
2. `python3 tools/extract_glyphs.py --out data/glyphs <scan>:<page> ...` - straightens each page
   (via the 4 corner squares), tracks the printed grid, subtracts the baseline guide and cuts out
   every character as a transparent PNG. Check `data/glyphs/contact/*.png` by eye.
3. `python3 tools/build_pack.py` - drops clipped/outlier copies, works out each letter's baseline and
   learns letter/word spacing from the sentences page -> `app/pack/`.
4. `python3 tools/build_app.py` - bundles everything into one offline file, `dist/handwriting.html`.

`app/` can also be served directly (`python3 -m http.server -d app`) while developing.

`data/`, `app/pack/` and `dist/` hold personal handwriting and are gitignored.

Requires: `pip install -r requirements.txt`.
