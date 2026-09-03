"""Снимает экраны прототипа в presentation/assets для презентации."""
import pathlib
import sys
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
PROTO = HERE.parent / "прототип" / "Триумф_система_прототип.html"
OUT = HERE / "assets"
OUT.mkdir(parents=True, exist_ok=True)

SHOTS = [
    ("shot-parse",     "openReq('247')",   2600),
    ("shot-requests",  "nav('requests')",  900),
    ("shot-money",     "nav('money')",     1200),
    ("shot-reports",   "nav('reports')",   1200),
    ("shot-memo",      "nav('reports'); memo()", 1400),
    ("shot-telegram",  "telegram()",       1200),
    ("shot-quotes",    "nav('quotes')",    1000),
    ("shot-price",     "openReq('247'); setTimeout(()=>price('электроды'),2200)", 3600),
    ("shot-suppliers", "nav('suppliers')", 1000),
    ("shot-konj",      "nav('konj')",      1200),
]

theme = sys.argv[1] if len(sys.argv) > 1 else "dark"

with sync_playwright() as p:
    b = p.chromium.launch()
    for name, script, wait in SHOTS:
        page = b.new_page(viewport={"width": 1560, "height": 900}, device_scale_factor=2)
        page.goto(PROTO.as_uri())
        page.wait_for_timeout(600)
        page.evaluate(f"setTheme('{theme}')")
        page.wait_for_timeout(300)
        page.evaluate(script)
        page.wait_for_timeout(wait)
        path = OUT / f"{name}.png"
        page.screenshot(path=str(path))
        print(name, path.stat().st_size // 1024, "KB")
        page.close()
    b.close()
