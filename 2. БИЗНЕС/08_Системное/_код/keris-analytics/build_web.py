#!/usr/bin/env python3
"""web/index.html из согласованного прототипа пульса."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
DEFAULT_SRC = (
    DIR.parents[2]
    / "04_Производство"
    / "Активные"
    / "Keris_Club"
    / "03_Проекты"
    / "Дашборд"
    / "dashboard.html"
)
OUT = DIR / "web" / "index.html"
LOGO_SRC = DEFAULT_SRC.parent / "logo-keris.png"

BOOTSTRAP = """
  fetch('api/data', {credentials:'same-origin'})
    .then(function(r){
      if(r.status === 401){ location.href = 'login'; return null; }
      if(!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function(payload){
      if(!payload) return;
      var D = payload.data || {};
      if(payload.age_minutes != null) D.age_minutes = payload.age_minutes;
      document.getElementById('logoutWrap').style.display = 'block';
      if(payload.stale){
        var el = document.getElementById('staleBanner');
        el.innerHTML = '<b>Цифры не обновлялись ' + payload.age_hours + ' ч.</b> Последний сбор: ' +
          (payload.last_success_label || '—') + '.' +
          (payload.last_error ? ' Причина: ' + payload.last_error : '');
        el.classList.add('show');
      }
      bind(D);
      startDashboard(D);
    })
    .catch(function(e){
      document.getElementById('root').innerHTML =
        '<div class="empty"><div class="big">!</div><h3>Не удалось загрузить</h3><p>' +
        e.message + '</p></div>';
    });
"""


def build(src_path: Path) -> Path:
    html = src_path.read_text(encoding="utf-8")
    marker = "  /* PULSE_BOOT */\n  var D = window.KERIS_PULSE_MOCK;\n  bind(D);\n  startDashboard(D);"
    if marker not in html:
        raise SystemExit("не нашёл PULSE_BOOT в прототипе")
    html = html.replace(marker, BOOTSTRAP.strip(), 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    assets = DIR / "web" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    if LOGO_SRC.exists():
        shutil.copy2(LOGO_SRC, DIR / "web" / "logo-keris.png")
        shutil.copy2(LOGO_SRC, assets / "logo-keris.png")
    return OUT


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.exists():
        raise SystemExit(f"нет прототипа: {src}")
    out = build(src)
    text = out.read_text(encoding="utf-8")
    checks = [
        ("нет MOCK в загрузке", "var D = window.KERIS_PULSE_MOCK" not in text),
        ("данные через api/data", "fetch('api/data'" in text),
        ("баннер несвежести", "staleBanner" in text),
        ("выход", "logoutWrap" in text),
        ("стиль записи", "--bg:#fafaf2" in text),
        ("нет Google Fonts", "fonts.googleapis.com" not in text),
        ("нет LTV", "LTV" not in text),
    ]
    ok = True
    for name, passed in checks:
        print(f"  [{'OK' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    print(f"OUT: {out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
