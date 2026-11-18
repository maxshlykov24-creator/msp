(()=>{
'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const themeButton=$('.theme-toggle');
function syncTheme(){const dark=document.documentElement.dataset.theme==='dark';themeButton.setAttribute('aria-pressed',String(dark));themeButton.setAttribute('aria-label',dark?'Включить светлую тему':'Включить тёмную тему');$('meta[name="theme-color"]').content=dark?'#21171c':'#f5f2ed';}
themeButton.addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;try{localStorage.setItem('ms-product.redesign.theme',theme);}catch{}syncTheme();});syncTheme();
const choices = $$('.lab-choice');
choices.forEach(choice => choice.addEventListener('click', () => {
  choices.forEach(item => {
    const selected = item === choice;
    item.setAttribute('aria-expanded', String(selected));
    document.getElementById(item.getAttribute('aria-controls')).hidden = !selected;
  });
}));
const motionButton = $('.motion-toggle');
function setPaused(paused) {
  document.documentElement.toggleAttribute('data-paused', paused);
  motionButton.setAttribute('aria-pressed', String(paused));
  motionButton.setAttribute('aria-label', paused ? 'Возобновить движение фона' : 'Приостановить движение фона');
  motionButton.textContent = paused ? '▷' : 'Ⅱ';
  document.dispatchEvent(new Event('silk-pause'));
}
motionButton.addEventListener('click', () => setPaused(!document.documentElement.hasAttribute('data-paused')));
const opening = $('.opening-silk');
function sizeOpening() {
  opening.style.setProperty('--opening-height', Math.ceil($('.hero').getBoundingClientRect().bottom + window.scrollY) + 'px');
}
sizeOpening();
new ResizeObserver(sizeOpening).observe($('.hero'));
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
  }, {threshold:0.08});
  $$('.section-intro,.project-picker,.project-stage,.lab-choice,.economy-grid>div,.portrait-frame,.about-copy,.delivery article,.contact-layout>div').forEach(el => sections.observe(el));
  $$('.lab-choice,.delivery article').forEach((el,i) => el.style.setProperty('--entry-delay',(i % 3 * 0.06)+'s'));
}
const slides=$$('.project-slide'),pickers=$$('[data-project]'),names=['Продажи и коммуникации','Аналитика и экономика','Каталог и крой','Программа лояльности'];
let selected=0;
function selectProject(index){const direction=index<selected?-1:1;selected=(index+slides.length)%slides.length;
slides[selected].style.setProperty('--slide-from', direction*28+'px');slides.forEach((slide,i)=>{slide.hidden=i!==selected;});pickers.forEach((button,i)=>button.setAttribute('aria-pressed',String(i===selected)));$('#project-announcement').textContent=`0${selected+1} · 04 · ${names[selected]}`;}
pickers.forEach(button=>button.addEventListener('click',()=>selectProject(Number(button.dataset.project))));
$('#project-prev').addEventListener('click',()=>selectProject(selected-1));$('#project-next').addEventListener('click',()=>selectProject(selected+1));
$('.project-stage').addEventListener('keydown',event=>{if(event.target!==event.currentTarget)return;if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();selectProject(selected+(event.key==='ArrowRight'?1:-1));}});
window.addEventListener('pageshow', () => {
  const from = history.state?.cuttingReturn;
  if (!from) return;
  choices[choices.length - 1].click();
  $('#preview-cutting button').focus();
  const state = {...history.state};
  delete state.cuttingReturn;
  history.replaceState(state, '');
});
let touch=null;
$('.project-stage').addEventListener('touchstart',event=>{if(event.target.closest('button,a'))return;const t=event.touches[0];touch={x:t.clientX,y:t.clientY};},{passive:true});
$('.project-stage').addEventListener('touchend',event=>{if(!touch)return;const t=event.changedTouches[0],x=t.clientX-touch.x,y=t.clientY-touch.y;touch=null;if(Math.abs(x)>70&&Math.abs(x)>Math.abs(y)*1.5)selectProject(selected+(x<0?1:-1));},{passive:true});
const demos={orders:{name:'Заявки и касса',path:'mansband/index.html?v=anonymous-1#board/deferred/all',instruction:'Список уже отфильтрован по отложкам. Откройте строку заявки. Все изменения остаются в демонстрации.'},messages:{name:'Автоматические сообщения',path:'mansband/index.html?v=anonymous-1#message-templates',instruction:'Откройте сценарий и проверьте его текст. Реальные сообщения из демо не отправляются.'},analytics:{name:'Аналитика продаж',path:'analytics/index.html',instruction:'Выберите категорию и канал сбыта. Переключитесь на Товары или Клиенты и откройте строку.'},production:{name:'Нормы и себестоимость',path:'production/index.html',instruction:'Найдите модель, откройте норму, выберите размер. Данные здесь демонстрационные.'},cutting:{name:'Каталог и крой',path:'cutting/index.html',embeddedPath:'./cutting.html',instruction:'Откройте задание и посмотрите план партии. В отдельной вкладке выберите раздел «Крой».'}};
const dialog=$('#demo-dialog'),frameTemplate=$('#demo-frame'),status=$('#demo-status');
frameTemplate.remove();
let frame=null;let currentDemo=null,opener=null,controller=null,timeout=null,loadId=0;
function resetLoad(){loadId++;controller?.abort();clearTimeout(timeout);frame?.remove();frame=null;}
async function loadDemo(){resetLoad();const id=loadId;const url=currentDemo.embeddedPath || '/brands/previews/'+currentDemo.path;status.hidden=false;status.textContent='Загружаем интерфейс…';controller=new AbortController();timeout=setTimeout(()=>{controller?.abort();if(id===loadId){status.hidden=false;status.textContent='Демо загружается дольше обычного. Попробуйте ещё раз или откройте его в отдельной вкладке.';}},15000);
try{const response=await fetch(url.split('#')[0],{method:'HEAD',signal:controller.signal,credentials:'omit'});if(!response.ok||!response.headers.get('content-type')?.includes('text/html'))throw new Error('unavailable');if(id!==loadId)return;frame=frameTemplate.cloneNode(false);frame.title=currentDemo.name+' · демонстрационные данные';frame.onload=()=>{if(id!==loadId)return;clearTimeout(timeout);frame.hidden=false;status.textContent='Демо загружено. Используются обезличенные демонстрационные данные.';};frame.onerror=()=>{if(id!==loadId)return;clearTimeout(timeout);status.textContent='Не удалось загрузить демо. Повторите попытку или откройте отдельную вкладку.';};frame.hidden=false;frame.src=url;dialog.append(frame);}catch(error){if(id!==loadId)return;clearTimeout(timeout);status.textContent=error.name==='AbortError'?'Загрузка заняла слишком много времени. Повторите попытку или откройте отдельную вкладку.':'Не удалось загрузить демо. Проверьте соединение и повторите попытку.';}}
$$('[data-demo]').forEach(button=>button.addEventListener('click',()=>{currentDemo=demos[button.dataset.demo];opener=button;
if(button.dataset.demo==='cutting' && matchMedia('(max-width:720px)').matches){
  history.replaceState({...history.state,cuttingReturn:button.closest('.project-slide')?'project':'lab'},'');
  location.assign('./cutting.html');
  return;
}
$('#demo-title').textContent=currentDemo.name;$('#demo-instruction').textContent=currentDemo.instruction;$('#demo-external').href='/brands/previews/'+currentDemo.path;history.pushState({...history.state, redesignDemo:true},'');dialog.showModal();document.body.classList.add('modal-open');loadDemo();}));
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
