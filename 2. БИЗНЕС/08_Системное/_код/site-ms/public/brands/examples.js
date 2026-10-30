(function(){
'use strict';
const entries={
 'mans-orders':{client:'MANSBAND',name:'Заявки и касса',heading:'Заказ, товар и оплата в одном месте',description:'Рабочее место консультанта связывает заявки amoCRM с товарами и документами МоегоСклада. Продажи, отложки и аренда собраны в одном интерфейсе.',instruction:'Выберите Отложка, отфильтруйте магазин и откройте заявку. Внутри можно посмотреть состав и учесть оплату.'},
 '2my-analytics':{client:'2MY',name:'Аналитика продаж',heading:'Разобраться в продажах без сборки отчётов вручную',description:'Панель на данных МоегоСклада и amoCRM. Продажи, каналы, товары и клиентская база доступны через общие фильтры.',instruction:'Выберите категорию и канал сбыта: суммы и график пересчитаются. Переключитесь на Товары или Клиенты и откройте строку.'},
 'mans-messages':{client:'MANSBAND',name:'Автоматические сообщения',heading:'Правила общения управляются из кассы',description:'Редактор автоматических сценариев, ручные шаблоны и порядок каналов. Условия, текст и проверка сообщения собраны в одном рабочем разделе.',instruction:'Откройте сценарий, измените текст и нажмите Проверить. Во вкладке Порядок каналов можно поменять очерёдность мессенджеров.'},
 '2my-production':{client:'2MY',name:'Производство',heading:'Нормы, материалы и экономика изделия',description:'Интерфейс производства: каталог норм, расход материалов, этапы работы и аналитика себестоимости. Можно разобрать изделие до размера и состава нормы.',instruction:'Найдите модель, откройте норму, выберите размер и измените расход. Во вкладке Аналитика раскройте остатки по размерам.'}
};
const frame=document.querySelector('#demo-frame'),viewport=document.querySelector('#demo-viewport'),canvas=document.querySelector('#demo-canvas'),windowEl=document.querySelector('.demo-window'),panel=document.querySelector('#example-panel'),tabs=[...document.querySelectorAll('[data-example]')],deviceButtons=[...document.querySelectorAll('[data-device]')];
const sources={'mans-orders':'mansband/index.html#board/all/all','mans-messages':'mansband/index.html#message-templates','2my-analytics':'analytics/index.html','2my-production':'production/index.html'};
let selected='mans-orders',device='desktop';
const BASE_WIDTH=1440,BASE_HEIGHT=900;
function resize(){
 const maxHeight=Math.min(460,innerHeight*.5);
 const phone=device==='phone';
 const width=phone?390:BASE_WIDTH;
 const scale=phone?Math.min((panel.clientWidth-30)/width,maxHeight/780):Math.min(viewport.clientWidth/width,maxHeight/BASE_HEIGHT);
 const height=phone?780:BASE_HEIGHT;
 frame.width=width;frame.height=height;
 frame.style.width=width+'px';frame.style.height=height+'px';
 canvas.style.width=width*scale+'px';canvas.style.height=height*scale+'px';
 frame.style.transform='scale('+scale+')';
 viewport.style.height=(height*scale+(phone?28:0))+'px';
 viewport.classList.toggle('is-phone',phone);
 windowEl.style.setProperty('--phone-width',width*scale+30+'px');
 windowEl.classList.toggle('is-phone',phone);
 deviceButtons.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.device===device)));
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
 if(send)frame.src='./previews/'+sources[id].replace('index.html','index.html?v=mobile-shell-3');
 viewport.scrollTop=0;viewport.scrollLeft=0;resize();
}
tabs.forEach((tab,index)=>{
 tab.addEventListener('click',()=>select(tab.dataset.example));
 tab.addEventListener('keydown',event=>{let i;if(event.key==='ArrowRight')i=(index+1)%tabs.length;else if(event.key==='ArrowLeft')i=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')i=0;else if(event.key==='End')i=tabs.length-1;else return;event.preventDefault();select(tabs[i].dataset.example,true,true);});
});
document.querySelectorAll('[data-example-link]').forEach(link=>link.addEventListener('click',()=>select(link.dataset.exampleLink)));
deviceButtons.forEach(button=>button.addEventListener('click',()=>{device=button.dataset.device;viewport.scrollTop=0;viewport.scrollLeft=0;resize();}));
document.querySelector('#demo-reset').addEventListener('click',()=>{frame.src='./previews/'+sources[selected].split('#')[0]+'?reset='+Date.now()+(sources[selected].includes('#')?'#'+sources[selected].split('#')[1]:'');viewport.scrollTop=0;viewport.scrollLeft=0;});
new ResizeObserver(resize).observe(viewport);select(selected,false);
})();
