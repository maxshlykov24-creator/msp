(function () {
  'use strict';
  var root = document.documentElement;
  var key = 'ms-product.theme';
  var theme = 'light';
  try {
    var savedTheme = localStorage.getItem(key);
    if (savedTheme === 'light' || savedTheme === 'dark') theme = savedTheme;
  } catch (_) {}
  root.dataset.theme = theme;

  var activeTransition = null;
  var fadeTimer = null;
  function setTheme(next) {
    root.dataset.theme = next;
    try { localStorage.setItem(key, next); } catch (_) {}
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = next === 'light' ? '#faf9f6' : '#130b10';
    document.querySelectorAll('.theme-pill button').forEach(function (button) {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === next));
    });
  }

  function apply(next, animate) {
    if (!animate || root.dataset.theme === next) { setTheme(next); return; }
    var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) { setTheme(next); return; }
    if (!document.startViewTransition) {
      clearTimeout(fadeTimer);
      root.classList.add('theme-fading');
      setTheme(next);
      fadeTimer = setTimeout(function () { root.classList.remove('theme-fading'); }, 600);
      return;
    }
    if (activeTransition) activeTransition.skipTransition();
    root.classList.add('theme-changing');
    var transition = document.startViewTransition(function () {
      setTheme(next);
      // Render the matching canvas palette synchronously before capture.
      document.dispatchEvent(new Event('msp-theme-snapshot'));
    });
    activeTransition = transition;
    transition.finished.catch(function () {}).finally(function () {
      if (activeTransition === transition) {
        activeTransition = null;
        root.classList.remove('theme-changing');
      }
    });
  }

  function mount() {
    var nav = document.querySelector('header.nav');
    if (!nav) return;
    var pill = document.createElement('div');
    pill.className = 'theme-pill';
    pill.setAttribute('role', 'group');
    pill.setAttribute('aria-label', 'Тема оформления');
    pill.innerHTML = '<button type="button" data-theme-choice="light" aria-label="Светлая тема"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42"/></svg></button>' +
      '<button type="button" data-theme-choice="dark" aria-label="Тёмная тема"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.9 13A9 9 0 0 1 11 3.1 9 9 0 1 0 20.9 13Z"/></svg></button>';
    pill.addEventListener('click', function (event) {
      var button = event.target.closest('button[data-theme-choice]');
      if (button) apply(button.dataset.themeChoice, true);
    });
    var anchor = nav.querySelector('.nav-cta, .nav-back');
    if (anchor) anchor.parentNode.insertBefore(pill, anchor);
    apply(root.dataset.theme);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
