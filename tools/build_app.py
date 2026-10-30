#!/usr/bin/env python3
"""Bundle app/ + app/pack into ONE self-contained HTML file (works offline, no server).

Usage: python3 tools/build_app.py --out dist/handwriting.html
"""
import argparse
import base64
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default=os.path.join(ROOT, "app"))
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "handwriting.html"))
    ap.add_argument("--artifact", action="store_true",
                    help="emit a page fragment for claude.ai Artifacts (no doctype/head/body wrapper)")
    args = ap.parse_args()

    pack = json.load(open(os.path.join(args.app, "pack", "pack.json")))
    for glyphs in pack["glyphs"].values():
        for g in glyphs:
            raw = open(os.path.join(args.app, "pack", g.pop("f")), "rb").read()
            g["data"] = "data:image/png;base64," + base64.b64encode(raw).decode()
    pack_js = json.dumps(pack, separators=(",", ":")).replace("</", "<\\/")

    html = open(os.path.join(args.app, "index.html")).read()
    css = open(os.path.join(args.app, "style.css")).read()
    js = open(os.path.join(args.app, "app.js")).read().replace("</script", "<\\/script")
    scripts = f"<script>window.__PACK_INLINE__={pack_js};</script>\n<script>\n{js}\n</script>"
    if args.artifact:
        body = html[html.index("<body>") + len("<body>"):html.index("</body>")]
        body = body.replace('<script src="app.js"></script>', "").strip()
        html = ('<title>My Handwriting</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n'
                f"<style>\n{css}\n</style>\n{body}\n{scripts}\n")
    else:
        html = html.replace('<link rel="stylesheet" href="style.css">', f"<style>\n{css}\n</style>")
        html = html.replace('<script src="app.js"></script>', scripts)
        assert 'href="style.css"' not in html and 'src="app.js"' not in html
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    open(args.out, "w").write(html)
    print(f"wrote {args.out} ({os.path.getsize(args.out)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
