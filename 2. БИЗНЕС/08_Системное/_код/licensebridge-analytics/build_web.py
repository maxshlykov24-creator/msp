#!/usr/bin/env python3
"""web/index.html из согласованного прототипа.

Единственный источник вёрстки — прототип в vault. Здесь он превращается в
страницу live-сервиса: данные приезжают из /api/data вместо зашитого data.js,
добавляются выход и баннер несвежих данных. Правки макета делаются в прототипе
и переносятся повторным запуском скрипта, иначе две версии разъедутся.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
DEFAULT_SRC = (
    DIR.parents[2]
    / "04_Производство"
    / "Активные"
    / "LicenseBridge_USA"
    / "дашборд"
    / "LicenseBridge_дашборд_прототип.src.html"
)
OUT = DIR / "web" / "index.html"

STALE_CSS = """
  .lb-banner{max-width:1300px;margin:14px auto 0;padding:14px 18px;border-radius:14px;
    font-size:13.5px;line-height:1.45;display:none;border:1px solid transparent}
  .lb-banner.show{display:block}
  .lb-banner.warn{background:rgba(196,91,85,.10);border-color:rgba(196,91,85,.35);color:#a8332c}
  [data-theme="dark"] .lb-banner.warn{background:rgba(217,138,134,.12);color:#f0b4b0}
  .lb-banner b{font-weight:650}
  .lb-logout{border:1px solid var(--line);background:var(--glass);color:var(--mute);
    font-family:var(--font);font-size:12px;font-weight:600;padding:8px 12px;border-radius:999px;
    cursor:pointer;text-decoration:none}
  .lb-logout:hover{color:var(--ink)}
  .lb-boot{max-width:1300px;margin:60px auto;padding:0 26px;color:var(--mute);font-size:14px}
"""

BANNER_HTML = """
<div class="lb-banner warn" id="lbStale"></div>
<div class="lb-boot" id="lbBoot">Загружаем данные из Kommo…</div>
"""

LOGOUT_HTML = """      <a class="lb-logout" href="/logout">Выйти</a>
"""

BOOTSTRAP = """
<script>
/* Данные приходят с сервера: срез Kommo пересобирается по расписанию.
   Пока он не загружен — дашборд не рисуем, чтобы на экране не было пустых нулей. */
(function(){
  function banner(html){
    var el = document.getElementById('lbStale');
    el.innerHTML = html;
    el.classList.add('show');
  }
  fetch('/api/data', {credentials:'same-origin'})
    .then(function(r){
      if(r.status === 401){ location.href = '/login'; return null; }
      if(!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function(payload){
      if(!payload) return;
      window.LB_DATA = payload.data;
      document.getElementById('lbBoot').style.display = 'none';
      if(payload.stale){
        banner('<b>Данные не обновлялись ' + payload.age_hours + ' ч.</b> ' +
               'Последний успешный сбор из Kommo: ' + (payload.last_success_label || '—') +
               '. Цифры ниже — на эту дату, не на сегодня.' +
               (payload.last_error ? ' Причина: ' + payload.last_error : ''));
      }
      startDashboard();
    })
    .catch(function(e){
      document.getElementById('lbBoot').textContent =
        'Не удалось загрузить данные: ' + e.message + '. Обновите страницу или сообщите в MS Product.';
    });
})();
</script>
"""


def build(src_path: Path) -> Path:
    html = src_path.read_text(encoding="utf-8")

    html, n = re.subn(r'\s*<script\s+src=["\']data\.js["\']\s*>\s*</script>', "", html, count=1)
    if n != 1:
        raise SystemExit("не нашёл подключение data.js в прототипе")

    html = html.replace('src="assets/', 'src="/assets/')

    html, n = re.subn(r"(\n</style>)", STALE_CSS + r"\1", html, count=1)
    if n != 1:
        raise SystemExit("не нашёл конец <style>")

    html, n = re.subn(r'(<div class="wrap">)', BANNER_HTML.strip() + r"\n\n\1", html, count=1)
    if n != 1:
        raise SystemExit("не нашёл .wrap")

    html, n = re.subn(
        r'(      <button class="theme-toggle" id="themeToggle")', LOGOUT_HTML + r"\1", html, count=1
    )
    if n != 1:
        raise SystemExit("не нашёл переключатель темы")

    # Основной скрипт прототипа выполняется только после загрузки данных.
    marker = "<script>\nconst D = window.LB_DATA;"
    if marker not in html:
        raise SystemExit("не нашёл начало основного скрипта")
    html = html.replace(marker, "<script>\nfunction startDashboard(){\nconst D = window.LB_DATA;", 1)
    tail = "\n</script>\n</body>"
    if tail not in html:
        raise SystemExit("не нашёл конец основного скрипта")
    html = html.replace(tail, "\n}\n</script>\n" + BOOTSTRAP + "</body>", 1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    return OUT


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.exists():
        raise SystemExit(f"нет прототипа: {src}")
    out = build(src)
    text = out.read_text(encoding="utf-8")
    checks = [
        ("нет data.js", "data.js" not in text),
        ("данные через /api/data", "/api/data" in text),
        ("обёртка startDashboard", "function startDashboard(){" in text),
        ("баннер несвежести", 'id="lbStale"' in text),
        ("выход", "/logout" in text),
        ("логотипы из /assets", 'src="/assets/logo-licensebridge' in text),
    ]
    ok = True
    for name, passed in checks:
        print(f"  [{'OK' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    print(f"OUT: {out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
