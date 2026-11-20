(()=>{
'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const themeButton=$('.theme-toggle');
function syncTheme(){const dark=document.documentElement.dataset.theme==='dark';themeButton.setAttribute('aria-pressed',String(dark));themeButton.setAttribute('aria-label',dark?'Включить светлую тему':'Включить тёмную тему');$('meta[name="theme-color"]').content=dark?'#21171c':'#f5f2ed';}
themeButton.addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;try{localStorage.setItem('ms-product.redesign.theme',theme);}catch{}syncTheme();});syncTheme();
const menu = $('.mobile-menu');
function closeMenu() { menu.open = false; }
menu.addEventListener('click', event => {
  const link = event.target.closest('a');
  if (!link) return;
  closeMenu();
  const target = document.querySelector(link.hash);
  target.setAttribute('tabindex', '-1');
  target.focus({preventScroll:true});
});
document.addEventListener('click', event => {
  if (menu.open && !menu.contains(event.target)) closeMenu();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && menu.open) {
    closeMenu();
    menu.querySelector('summary').focus();
  }
});
matchMedia('(max-width:760px)').addEventListener('change', closeMenu);

const smallLab = matchMedia('(max-width:760px)');
const choices = $$('.lab-choice');
const labSelect = $('#software-select');
choices.forEach(choice => {
  const title = choice.querySelector('strong').textContent;
  labSelect.add(new Option(title, choice.getAttribute('aria-controls')));
});
function selectSoftware(choice) {
  choices.forEach(item => {
    const active = item === choice;
    item.setAttribute('aria-expanded', String(active));
    document.getElementById(item.getAttribute('aria-controls')).hidden = !active;
  });
  labSelect.value = choice.getAttribute('aria-controls');
}
choices.forEach(choice => choice.addEventListener('click', () => selectSoftware(choice)));
labSelect.addEventListener('change', () => selectSoftware(choices[labSelect.selectedIndex]));
labSelect.parentElement.hidden = false;

const root = document.documentElement;
const motionButton = $('.motion-toggle');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
function motionEnabled() { return !reducedMotion.matches && !root.hasAttribute('data-paused'); }
function setPaused(paused) {
  root.toggleAttribute('data-paused', paused);
  motionButton.setAttribute('aria-pressed', String(paused));
  const label = paused ? 'Возобновить анимацию' : 'Приостановить анимацию';
  motionButton.setAttribute('aria-label', label);
  motionButton.title = label;
  document.dispatchEvent(new Event('silk-pause'));
}
setPaused(root.hasAttribute('data-paused'));
motionButton.addEventListener('click', () => {
  const paused = !root.hasAttribute('data-paused');
  setPaused(paused);
  try { localStorage.setItem('ms-product.redesign.paused', String(paused)); } catch {}
});

const steps = $$('.route-switch button');
const route = $('.route-switch');
const caption = $('#route-description');
const object = $('.garment-object');
const scene = $('.garment-scene');
const stageCopy = [
  caption.textContent,
  'Менеджер видит учтённый платёж в карточке заказа.',
  'Склад получает состав заказа и данные для отправки.'
];
let captionMotion;
function stopObjectMotion() {
  object.removeAttribute('style');
  captionMotion?.cancel();
}
document.addEventListener('silk-pause', stopObjectMotion);
reducedMotion.addEventListener('change', stopObjectMotion);
steps.forEach((button, index) => button.addEventListener('click', () => {
  if (button.getAttribute('aria-pressed') === 'true') return;
  steps.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  route.style.setProperty('--step', index);
  caption.textContent = stageCopy[index];
  scene.dataset.step = index;
  captionMotion?.cancel();
  if (motionEnabled() && caption.animate) {
    captionMotion = caption.animate([{opacity: .35, transform: 'translateY(5px)'}, {opacity: 1, transform: 'none'}],
      {duration: 300, easing: 'ease-out'});
  }
}));
const finePointer = matchMedia('(hover:hover) and (pointer:fine)');
scene.addEventListener('pointermove', event => {
  if (!finePointer.matches || !motionEnabled()) return;
  const rect = scene.getBoundingClientRect();
  const x = (event.clientX - rect.left) / rect.width - .5;
  const y = (event.clientY - rect.top) / rect.height - .5;
  object.style.setProperty('--object-x', (x * 12) + 'px');
  object.style.setProperty('--object-y', (y * 9) + 'px');
  object.style.setProperty('--object-turn', (x * 3) + 'deg');
});
scene.addEventListener('pointerleave', () => object.removeAttribute('style'));
const opening = $('.opening-silk');
function sizeOpening() {
  opening.style.setProperty('--opening-height', Math.ceil($('.hero').getBoundingClientRect().bottom + window.scrollY) + 'px');
}
sizeOpening();
if ('ResizeObserver' in window) {
  const sizing = new ResizeObserver(sizeOpening);
  sizing.observe($('.hero'));
  sizing.observe($('.header'));
} else {
  window.addEventListener('resize', sizeOpening);
}
if ('IntersectionObserver' in window) {
  const backgrounds = new IntersectionObserver(entries => {
    for (const entry of entries) entry.target.classList.toggle('silk-visible', entry.isIntersecting);
  });
  $$('.opening-silk,.about-backdrop').forEach(el => backgrounds.observe(el));
  const sections = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add('enter-spread');
      sections.unobserve(entry.target);
    }
  });
  $$('.section-intro,.project-stage,.economy-grid>div,.portrait-frame,.about-copy,.delivery article,.contact-layout>div').forEach(el => {
    if (el.getBoundingClientRect().top >= innerHeight) sections.observe(el);
  });
  $$('.delivery article').forEach((el,i) => el.style.setProperty('--entry-delay',(i * .065)+'s'));
}
const slides = $$('.project-slide');
const pickers = $$('[data-project]');
let selected=0;
function selectProject(index) {
  const next = (index + slides.length) % slides.length;
  if (next === selected) return;
  slides[next].style.setProperty('--slide-from', (index < selected ? -18 : 18) + 'px');
  selected = next;
  slides.forEach((slide,i) => { slide.hidden = i !== selected; });
  pickers.forEach((button,i) => button.setAttribute('aria-pressed', String(i === selected)));
  $('#project-announcement').textContent = (selected + 1) + ' из ' + slides.length + ' · ' + pickers[selected].textContent;
}
pickers.forEach(button => button.addEventListener('click', () => selectProject(Number(button.dataset.project))));
function advanceCase(delta) {
  selectProject(selected + delta);
  if (smallLab.matches && $('.project-picker').getBoundingClientRect().bottom < 0) {
    pickers[selected].focus({preventScroll:true});
    $('.project-picker').scrollIntoView({block:'start'});
  }
}
$('#project-prev').addEventListener('click', () => advanceCase(-1));
$('#project-next').addEventListener('click', () => advanceCase(1));
$('.project-stage').addEventListener('keydown',event=>{if(event.target!==event.currentTarget)return;if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();selectProject(selected+(event.key==='ArrowRight'?1:-1));}});
function revealLinkedProgram(hash) {
  const choice = choices.find(item => '#' + item.getAttribute('aria-controls') === hash);
  if (!choice) return;
  selectSoftware(choice);
  const panel = document.getElementById(choice.getAttribute('aria-controls'));
  panel.querySelector('.preview-action').focus({preventScroll: true});
  panel.scrollIntoView({block: 'start'});
}

window.addEventListener('hashchange', () => revealLinkedProgram(location.hash));
$$('.case-program').forEach(link => link.addEventListener('click', event => {
  if (link.hash === location.hash && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
    revealLinkedProgram(link.hash);
  }
}));
window.addEventListener('pageshow', event => {
  if (history.state?.cuttingReturn) {
    const state = {...history.state};
    delete state.cuttingReturn;
    history.replaceState(state, '');
    revealLinkedProgram('#preview-cutting');
  } else if (!event.persisted) {
    const expanded = choices.find(choice => choice.getAttribute('aria-expanded') === 'true');
    selectSoftware(expanded);
    revealLinkedProgram(location.hash);
  }
});
let touch=null;
$('.project-stage').addEventListener('touchstart',event=>{if(event.touches.length!==1||event.target.closest('button,a,summary')){touch=null;return;};const t=event.touches[0];touch={x:t.clientX,y:t.clientY};},{passive:true});
$('.project-stage').addEventListener('touchend',event=>{if(!touch)return;const t=event.changedTouches[0],x=t.clientX-touch.x,y=t.clientY-touch.y;touch=null;if(Math.abs(x)>70&&Math.abs(x)>Math.abs(y)*1.5)selectProject(selected+(x<0?1:-1));},{passive:true});
$('.project-stage').addEventListener('touchcancel',()=>{touch=null;});
const demos = {
  orders: {
    path: '/brands/previews/mansband/index.html?v=anonymous-1#board/deferred/all',
    instruction: 'Список уже отфильтрован по отложкам. Откройте заявку, чтобы увидеть товары и оплату.'
  },
  messages: {
    path: '/brands/previews/mansband/index.html?v=anonymous-1#message-templates',
    instruction: 'Выберите событие, чтобы увидеть текст сообщения покупателю. Отправка в демо отключена.'
  },
  analytics: {
    path: '/brands/previews/analytics/index.html',
    instruction: 'Выберите канал продаж и перейдите в раздел «Товары» или «Клиенты».'
  },
  production: {
    path: '/brands/previews/production/index.html',
    instruction: 'Откройте модель и выберите размер, чтобы проверить состав нормы.'
  },
  cutting: {
    path: './cutting.html',
    instruction: 'Откройте задание в разделе «Крой» и сверьте план с выпуском по размерам.'
  }
};
const dialog = $('#demo-dialog');
const frameTemplate = $('#demo-frame');
const status = $('#demo-status');
const retryButton = $('#demo-retry');
const demoScreen = $('.dialog-screen');
const demoHelp = $('.demo-help');
frameTemplate.remove();

let frame = null;
let opener = null;
let controller = null;
let timeout = null;

function resetLoad() {
  controller?.abort();
  clearTimeout(timeout);
  frame?.remove();
  frame = null;
}

function showLoadError(message) {
  resetLoad();
  status.classList.remove('loaded');
  status.textContent = message;
  retryButton.textContent = 'Повторить загрузку';
  retryButton.disabled = false;
  demoHelp.open = true;
}

async function loadDemo() {
  resetLoad();
  const {path} = demos[opener.dataset.demo];
  controller = new AbortController();
  const {signal} = controller;
  status.classList.remove('loaded');
  status.textContent = 'Открываем демо…';
  retryButton.disabled = true;
  timeout = setTimeout(() => showLoadError('Демо не открылось за отведённое время. Можно повторить загрузку.'), 15000);
  try {
    // Check the GET route with a one-byte range before navigating the opaque sandbox.
    const response = await fetch(path.split('#')[0], {
      headers: {Range: 'bytes=0-0'}, signal, credentials: 'omit'
    });
    await response.body?.cancel();
    if (signal.aborted) return;
    if (!response.ok || !response.headers.get('content-type')?.includes('text/html')) {
      throw new Error('Demo page unavailable');
    }
    frame = frameTemplate.cloneNode(false);
    frame.title = $('#demo-title').textContent + ' · демонстрационные данные';
    frame.onload = () => {
      if (signal.aborted) return;
      clearTimeout(timeout);
      status.textContent = '';
      status.classList.add('loaded');
      retryButton.textContent = 'Начать демо заново';
      retryButton.disabled = false;
    };
    frame.onerror = () => {
      if (!signal.aborted) showLoadError('Не удалось открыть демо. Можно повторить загрузку или открыть его в новой вкладке.');
    };
    frame.src = path;
    demoScreen.append(frame);
  } catch {
    if (!signal.aborted) showLoadError('Не удалось открыть демо. Проверьте соединение и повторите загрузку.');
  }
}

$$('[data-demo]').forEach(button => button.addEventListener('click', () => {
  opener = button;
  const demo = demos[button.dataset.demo];
  if (button.dataset.demo === 'cutting' && matchMedia('(max-width:720px)').matches) {
    history.replaceState({...history.state, cuttingReturn: true}, '');
    location.assign(demo.path);
    return;
  }
  $('#demo-title').textContent = labSelect.selectedOptions[0].text;
  $('#demo-instruction').textContent = demo.instruction;
  $('#demo-external').href = demo.path;
  history.pushState({...history.state, redesignDemo: true}, '');
  demoHelp.open = !smallLab.matches;
  dialog.showModal();
  document.body.classList.add('modal-open');
  loadDemo();
}));
function dismissDemo() {
  resetLoad();dialog.close();
  if(history.state?.redesignDemo) history.back();
}
window.addEventListener('popstate', () => {if(dialog.open) dialog.close();});
dialog.addEventListener('cancel', event => {event.preventDefault();dismissDemo();});
$('#demo-close').addEventListener('click',dismissDemo);$('#demo-retry').addEventListener('click',loadDemo);
dialog.addEventListener('close',()=>{resetLoad();document.body.classList.remove('modal-open');opener?.focus();});
dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dismissDemo();});
})();
