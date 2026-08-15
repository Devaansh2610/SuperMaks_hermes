#!/usr/bin/env python3
"""Bundle ui/ into a single self-contained preview.html.

Only for sharing a reviewable copy of the interface — the real app always
serves ui/index.html + app.js + styles.css as separate files. The published
page has no <body> of its own to own the theme classes, so everything that
lives on <body> in the real app is re-pointed at one wrapper div here.
"""
import pathlib

ui = pathlib.Path("ui")
html = ui.joinpath("index.html").read_text(encoding="utf-8")
css = ui.joinpath("styles.css").read_text(encoding="utf-8")
js = ui.joinpath("app.js").read_text(encoding="utf-8")

body = html.split('<body class="state-boot">', 1)[1].rsplit("</body>", 1)[0]
# we inline our own copy of the script below; leaving this in runs app.js twice
body = body.replace('<script src="app.js"></script>', "")

# Keyed by #smRoot (an id) rather than a class: any code doing a wholesale
# className assignment would silently strip a class-based hook.
css = (css.replace("body{", "#smRoot{")
          .replace("body.state-", "#smRoot.state-")
          .replace("body.open-", "#smRoot.open-")
          .replace("body:not(.state-boot)", "#smRoot:not(.state-boot)"))

# app.js funnels every body-class/computed-style read through this one line
js = js.replace("const ROOT_EL = document.body;",
                "const ROOT_EL = document.getElementById('smRoot');")

out = (
    '<meta charset="utf-8">\n'
    "<title>SuperMaks Mission Control</title>\n"
    "<style>\n"
    "body{background:#04060b;margin:0}\n"
    "#smRoot{position:fixed;inset:0;overflow:hidden}\n"
    + css +
    "\n</style>\n"
    '<div class="state-boot" id="smRoot">\n' + body + "\n</div>\n"
    "<script>\n" + js + "\n</script>\n"
)
pathlib.Path("preview.html").write_text(out, encoding="utf-8")
print(f"preview.html rebuilt — {len(out)} bytes, {out.count('<script')} script tag(s)")
