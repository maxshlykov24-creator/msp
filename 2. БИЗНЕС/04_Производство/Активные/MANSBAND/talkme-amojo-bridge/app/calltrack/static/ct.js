(function () {
  var LIVE = __CT_LIVE__;
  var API = "__CT_API__";
  var ORIGINAL = "__CT_ORIGINAL__";
  if (navigator.webdriver) return;

  function readCookie(name) {
    var parts = document.cookie ? document.cookie.split("; ") : [];
    for (var i = 0; i < parts.length; i++) {
      var eq = parts[i].indexOf("=");
      if (eq === -1) continue;
      if (parts[i].slice(0, eq) === name) {
        return decodeURIComponent(parts[i].slice(eq + 1));
      }
    }
    return "";
  }

  function setCookie(name, value) {
    document.cookie = name + "=" + encodeURIComponent(value) + "; path=/; domain=.mansband.ru; SameSite=Lax";
  }

  var params = new URLSearchParams(location.search);
  if (params.get("ct") === "1") setCookie("ct_test", "1");
  if (params.get("ct") === "0") {
    document.cookie = "ct_test=; path=/; domain=.mansband.ru; max-age=0";
  }
  var test = readCookie("ct_test") === "1";
  if (!LIVE && !test) return;

  var visitId = parseInt(readCookie("ct_visit"), 10);
  if (!visitId) visitId = null;
  var shown = ORIGINAL;
  var current = null;
  var watching = false;

  function last10(value) {
    var digits = String(value || "").replace(/\D/g, "");
    return digits.length >= 10 ? digits.slice(-10) : "";
  }

  function pageUtm() {
    var q = new URLSearchParams(location.search);
    var keys = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "yclid", "ysclid"];
    var out = {};
    for (var i = 0; i < keys.length; i++) {
      var val = q.get(keys[i]);
      if (val) out[keys[i] === "ysclid" ? "yclid" : keys[i]] = val;
    }
    return out;
  }

  function groupPattern(source) {
    var groups = [source.slice(0, 3), source.slice(3, 6), source.slice(6, 8), source.slice(8, 10)];
    var sep = "[\\s\\-\\(\\)\\.]*";
    return new RegExp("(?:\\+7|8)?" + sep + groups.join(sep), "g");
  }

  function forbidden(node) {
    var el = node.parentElement;
    if (!el) return false;
    if (el.closest("script, style, input, textarea")) return true;
    var link = el.closest("a");
    if (!link) return false;
    var href = link.getAttribute("href") || "";
    return /wa\.me|whatsapp|t\.me/i.test(href);
  }

  function replaceTexts(source, display) {
    if (!source || source.length !== 10 || !document.body) return;
    var re = groupPattern(source);
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) {
      if (!node.nodeValue || forbidden(node)) continue;
      re.lastIndex = 0;
      if (!re.test(node.nodeValue)) continue;
      re.lastIndex = 0;
      var next = node.nodeValue.replace(re, function (match) {
        return match === display ? match : display;
      });
      if (next !== node.nodeValue) node.nodeValue = next;
    }
  }

  function replaceHrefs(source, e164) {
    var links = document.querySelectorAll('a[href^="tel:"]');
    for (var i = 0; i < links.length; i++) {
      var href = links[i].getAttribute("href") || "";
      if (/wa\.me|whatsapp|t\.me/i.test(href)) continue;
      if (last10(href) !== source) continue;
      links[i].setAttribute("href", "tel:" + e164);
    }
  }

  function replacePhones(e164, display) {
    var target = last10(e164);
    var sources = [];
    if (ORIGINAL && ORIGINAL !== target) sources.push(ORIGINAL);
    if (shown && shown !== target && sources.indexOf(shown) === -1) sources.push(shown);
    for (var i = 0; i < sources.length; i++) {
      replaceHrefs(sources[i], e164);
      replaceTexts(sources[i], display);
    }
    shown = target;
  }

  function watch() {
    if (watching || !document.body || !current) return;
    watching = true;
    var timer = null;
    var obs = new MutationObserver(function () {
      if (timer) return;
      timer = setTimeout(function () {
        timer = null;
        if (current && current.number && current.display) {
          replacePhones(current.number, current.display);
        }
      }, 100);
    });
    obs.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  function apply(data) {
    if (!data || data.mode === "off") return;
    if (data.visit_id) {
      visitId = data.visit_id;
      setCookie("ct_visit", String(data.visit_id));
      setCookie("roistat_visit", String(data.visit_id));
      window.ctVisit = { visit_id: data.visit_id, utm: pageUtm() };
    }
    if (data.number && data.display) {
      current = data;
      replacePhones(data.number, data.display);
      watch();
    }
  }

  function post(path, body) {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, 2000);
    return fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: ctrl.signal,
      credentials: "omit"
    }).then(function (r) {
      clearTimeout(timer);
      if (!r.ok) throw new Error("ct");
      return r.json();
    }, function (err) {
      clearTimeout(timer);
      throw err;
    });
  }

  function requestNumber() {
    var body = {
      visit_id: visitId,
      url: location.href,
      referrer: document.referrer || "",
      ym_uid: readCookie("_ym_uid") || null,
      test: !LIVE && test
    };
    post("/ct/session", body).then(apply).catch(function () {});
  }

  if (location.hostname === "zamer.mansband.ru" && window.fetch) {
    var origFetch = window.fetch;
    window.fetch = function (input, init) {
      try {
        init = init ? Object.assign({}, init) : {};
        if ((init.method || "GET").toUpperCase() === "POST" && typeof init.body === "string" && window.ctVisit) {
          var data = JSON.parse(init.body);
          if (data && typeof data === "object" && !Array.isArray(data)) {
            if (window.ctVisit.visit_id && !data.roistatVisitId) {
              data.roistatVisitId = String(window.ctVisit.visit_id);
            }
            var utm = window.ctVisit.utm || {};
            Object.keys(utm).forEach(function (key) {
              if (utm[key] && !data[key]) data[key] = utm[key];
            });
            init.body = JSON.stringify(data);
          }
        }
      } catch (e) {}
      return origFetch.call(this, input, init);
    };
  }

  var events = ["scroll", "pointerdown", "touchstart", "keydown", "mousemove"];
  function start() {
    events.forEach(function (ev) { window.removeEventListener(ev, start); });
    requestNumber();
  }
  if (location.hostname === "zamer.mansband.ru") {
    requestNumber();
  } else {
    events.forEach(function (ev) {
      window.addEventListener(ev, start, { passive: true, once: true });
    });
  }

  setInterval(function () {
    if (!visitId || document.visibilityState !== "visible") return;
    post("/ct/ping", { visit_id: visitId }).then(apply).catch(function () {});
  }, 60000);
})();
