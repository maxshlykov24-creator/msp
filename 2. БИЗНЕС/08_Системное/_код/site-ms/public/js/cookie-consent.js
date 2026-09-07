/**
 * Cookie / Яндекс.Метрика consent for ms-p.ru
 * Аналитика не грузится, пока нет согласия.
 * Номер счётчика: window.MSP_METRIKA_ID = "XXXXXX" (до этого скрипта).
 * Пока ID пустой — баннер всё равно показывает выбор (готовность к Метрике);
 * скрипт Метрики подключится, когда ID задан и есть согласие.
 */
(function () {
  var KEY = "msp_cookie_consent";
  var metrikaId = String(window.MSP_METRIKA_ID || "").trim();

  function getConsent() {
    try {
      return localStorage.getItem(KEY);
    } catch (e) {
      return null;
    }
  }

  function setConsent(value) {
    try {
      localStorage.setItem(KEY, value);
    } catch (e) {}
  }

  function loadMetrika(id) {
    if (!id || window.ym) return;
    window.ym =
      window.ym ||
      function () {
        (window.ym.a = window.ym.a || []).push(arguments);
      };
    window.ym.l = Date.now();
    var s = document.createElement("script");
    s.async = true;
    s.src = "https://mc.yandex.ru/metrika/tag.js";
    document.head.appendChild(s);
    window.ym(Number(id), "init", {
      clickmap: true,
      trackLinks: true,
      accurateTrackBounce: true,
      webvisor: false,
    });
  }

  function applyConsent(value) {
    if (value === "all" && metrikaId) loadMetrika(metrikaId);
  }

  function hide(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function showBanner() {
    if (document.getElementById("msp-cookie")) return;

    var css = document.createElement("style");
    css.textContent =
      "#msp-cookie{position:fixed;left:16px;right:16px;bottom:16px;z-index:9999;max-width:640px;margin:0 auto;padding:18px 20px;border-radius:18px;background:rgba(12,10,18,.92);border:1px solid rgba(242,238,252,.14);backdrop-filter:blur(18px) saturate(160%);-webkit-backdrop-filter:blur(18px) saturate(160%);box-shadow:0 16px 48px rgba(0,0,0,.45);color:#f2eefc;font-family:'Inter Tight',-apple-system,sans-serif}" +
      "#msp-cookie p{margin:0 0 14px;font-size:14px;line-height:1.5;color:rgba(242,238,252,.78)}" +
      "#msp-cookie a{color:#c4b0ff;text-decoration:none}" +
      "#msp-cookie a:hover{color:#fff}" +
      "#msp-cookie .msp-cookie-actions{display:flex;flex-wrap:wrap;gap:10px}" +
      "#msp-cookie button{appearance:none;border:0;cursor:pointer;border-radius:12px;padding:11px 16px;font:600 13px/1 'Inter Tight',-apple-system,sans-serif}" +
      "#msp-cookie .msp-ok{background:linear-gradient(110deg,#ff3db8,#9d6bff 48%,#3fc0ff);color:#0a0810}" +
      "#msp-cookie .msp-no{background:rgba(255,255,255,.06);color:#f2eefc;border:1px solid rgba(242,238,252,.14)}" +
      "@media(max-width:560px){#msp-cookie{left:10px;right:10px;bottom:10px;padding:16px}" +
      "#msp-cookie .msp-cookie-actions{flex-direction:column}" +
      "#msp-cookie button{width:100%}}";
    document.head.appendChild(css);

    var box = document.createElement("div");
    box.id = "msp-cookie";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", "Согласие на cookie");
    box.innerHTML =
      "<p>Мы используем необходимые cookie для сохранения вашего выбора. Аналитика (Яндекс.Метрика) включается только с вашего согласия — это помогает улучшать сайт. " +
      '<a href="/privacy.html">Политика конфиденциальности</a></p>' +
      '<div class="msp-cookie-actions">' +
      '<button type="button" class="msp-ok" data-c="all">Принять аналитику</button>' +
      '<button type="button" class="msp-no" data-c="necessary">Только необходимые</button>' +
      "</div>";
    document.body.appendChild(box);

    box.addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-c]");
      if (!btn) return;
      var value = btn.getAttribute("data-c");
      setConsent(value);
      applyConsent(value);
      hide(box);
    });
  }

  var existing = getConsent();
  if (existing === "all" || existing === "necessary") {
    applyConsent(existing);
    return;
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", showBanner);
  } else {
    showBanner();
  }
})();
