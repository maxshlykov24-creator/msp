(function(){
'use strict';
const entries={
 'cutting-stock':{client:'Решение 4',name:'Каталог и крой',heading:'От остатков по размерам до задания на крой',description:'Собственное приложение для бренда с производством. Каталог объединяет модели, цвета, размеры и остатки; задания на крой показывают план, раскладки и фактический выпуск.',instruction:'Найдите модель, раскройте размеры и откройте карточку. Перейдите в Крой и откройте задание: внутри план партии, раскладка и остаток к выполнению.'},
 'mans-orders':{client:'Решение 1',name:'Заявки и касса',heading:'Заказ, товар и оплата в одном месте',description:'Рабочее место консультанта связывает заявки amoCRM с товарами и документами МоегоСклада. Продажи, отложки и аренда собраны в одном интерфейсе.',instruction:'Выберите Отложка, отфильтруйте магазин и откройте заявку. Внутри можно посмотреть состав и учесть оплату.'},
 '2my-analytics':{client:'Решение 2',name:'Аналитика продаж',heading:'Разобраться в продажах без сборки отчётов вручную',description:'Панель на данных МоегоСклада и amoCRM. Продажи, каналы, товары и клиентская база доступны через общие фильтры.',instruction:'Выберите категорию и канал сбыта: суммы и график пересчитаются. Переключитесь на Товары или Клиенты и откройте строку.'},
 'mans-messages':{client:'Решение 1',name:'Автоматические сообщения',heading:'Правила общения управляются из кассы',description:'Редактор автоматических сценариев, ручные шаблоны и порядок каналов. Условия, текст и проверка сообщения собраны в одном рабочем разделе.',instruction:'Откройте сценарий, измените текст и нажмите Проверить. Во вкладке Порядок каналов можно поменять очерёдность мессенджеров.'},
 '2my-production':{client:'Решение 2',name:'Производство',heading:'Нормы, материалы и экономика изделия',description:'Интерфейс производства: каталог норм, расход материалов, этапы работы и аналитика себестоимости. Можно разобрать изделие до размера и состава нормы.',instruction:'Найдите модель, откройте норму, выберите размер и измените расход. Во вкладке Аналитика раскройте остатки по размерам.'}
};
const frame=document.querySelector('#demo-frame'),viewport=document.querySelector('#demo-viewport'),canvas=document.querySelector('#demo-canvas'),windowEl=document.querySelector('.demo-window'),panel=document.querySelector('#example-panel'),tabs=[...document.querySelectorAll('[data-example]')],deviceButtons=[...document.querySelectorAll('[data-device]')];
const sources={'cutting-stock':'cutting/index.html','mans-orders':'mansband/index.html#board/all/all','mans-messages':'mansband/index.html#message-templates','2my-analytics':'analytics/index.html','2my-production':'production/index.html'};
let selected='mans-orders',device='desktop';
const BASE_WIDTH=1440,BASE_HEIGHT=900;
let layout=null,motionFrame=0;
const reducedMotion=matchMedia('(prefers-reduced-motion:reduce)');
function targetLayout(){
 const maxHeight=Math.min(460,innerHeight*.5),phone=device==='phone';
 const tracks=getComputedStyle(panel).gridTemplateColumns.split(' ').map(parseFloat).filter(Number.isFinite);
 const available=Math.max(1,Math.min(panel.clientWidth,tracks[tracks.length-1]||panel.clientWidth));
 const width=phone?390:BASE_WIDTH,height=phone?780:BASE_HEIGHT;
 const scale=phone?Math.min((available-30)/width,maxHeight/780):Math.min((available-2)/width,maxHeight/BASE_HEIGHT);
 return {width,height,screen:width*scale,outer:phone?width*scale+30:available,padding:phone?14:0,round:phone?1:0};
}
function paintLayout(value){
 layout=value;
 const scale=value.screen/value.width;
 windowEl.style.width=value.outer+'px';
 windowEl.style.borderRadius=(7+19*value.round)+'px';
 frame.style.width=value.width+'px';frame.style.height=value.height+'px';
 frame.style.transform='scale('+scale+')';
 canvas.style.width=value.screen+'px';canvas.style.height=value.height*scale+'px';
 canvas.style.borderRadius=18*value.round+'px';
 viewport.style.paddingTop=value.padding+'px';viewport.style.paddingBottom=value.padding+'px';
 viewport.style.height=(value.height*scale+value.padding*2)+'px';
 windowEl.style.setProperty('--device-round',value.round);
}
function resize(animate=false){
 cancelAnimationFrame(motionFrame);
 const target=targetLayout();
 deviceButtons.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.device===device)));
 viewport.classList.toggle('is-phone',device==='phone');
 windowEl.classList.toggle('is-phone',device==='phone');
 if(!animate||!layout||reducedMotion.matches){paintLayout(target);windowEl.classList.remove('is-morphing');return;}
 const from={...layout},started=performance.now();
 windowEl.classList.add('is-morphing');
 function tick(now){
   const progress=Math.min(1,(now-started)/680);
   const ease=progress<.5?4*progress*progress*progress:1-Math.pow(-2*progress+2,3)/2;
   const value={};Object.keys(target).forEach(key=>value[key]=from[key]+(target[key]-from[key])*ease);
   paintLayout(value);
   if(progress<1)motionFrame=requestAnimationFrame(tick);
   else{windowEl.classList.remove('is-morphing');frame.width=target.width;frame.height=target.height;}
 }
 motionFrame=requestAnimationFrame(tick);
}

function select(id,send=true,focus=false){
 const entry=entries[id];if(!entry)return;
 selected=id;
 viewport.classList.toggle('is-kassa',id.startsWith('mans'));
 viewport.classList.toggle('is-loyalty',id==='marta-loyalty');
 tabs.forEach((tab,i)=>{const on=tab.dataset.example===id;tab.classList.toggle('active',on);tab.setAttribute('aria-selected',String(on));tab.tabIndex=on?0:-1;if(on){document.querySelector('#example-panel').setAttribute('aria-labelledby',tab.id);if(focus){tab.focus();tab.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});}}});
 document.querySelector('#demo-window-title').textContent=entry.client+' · '+entry.name;
 document.querySelector('#example-client').textContent=entry.client+' · '+entry.name;
 document.querySelector('#example-heading').textContent=entry.heading;
 document.querySelector('#example-description').textContent=entry.description;
 document.querySelector('#example-instruction').textContent=entry.instruction;
 frame.title=entry.client+' - интерактивный пример: '+entry.name;
 if(send)frame.src='/brands/previews/'+sources[id].replace('index.html','index.html?v=anonymous-1');
 viewport.scrollTop=0;viewport.scrollLeft=0;resize();
}
tabs.forEach((tab,index)=>{
 tab.addEventListener('click',()=>select(tab.dataset.example));
 tab.addEventListener('keydown',event=>{let i;if(event.key==='ArrowRight')i=(index+1)%tabs.length;else if(event.key==='ArrowLeft')i=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')i=0;else if(event.key==='End')i=tabs.length-1;else return;event.preventDefault();select(tabs[i].dataset.example,true,true);});
});
document.querySelectorAll('[data-example-link]').forEach(link=>link.addEventListener('click',()=>select(link.dataset.exampleLink)));
deviceButtons.forEach(button=>button.addEventListener('click',()=>{if(device===button.dataset.device)return;device=button.dataset.device;resize(true);}));
let measuredWidth=0;new ResizeObserver(()=>{if(Math.abs(panel.clientWidth-measuredWidth)>.5){measuredWidth=panel.clientWidth;resize();}}).observe(panel);
window.addEventListener('resize',()=>resize());
reducedMotion.addEventListener('change',()=>resize());select(selected,false);
})();
