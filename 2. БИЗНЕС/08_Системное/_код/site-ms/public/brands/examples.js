(function(){
'use strict';
const entries={
 'mans-orders':{client:'MANSBAND',name:'Заявки и касса',heading:'Заказ, товар и оплата в одном месте',description:'Рабочее место консультанта связывает заявки amoCRM с товарами и документами МоегоСклада. Продажи, отложки и аренда собраны в одном интерфейсе.',instruction:'Выберите Отложка, отфильтруйте магазин и откройте заявку. Внутри можно посмотреть состав и учесть оплату.'},
 '2my-analytics':{client:'2MY',name:'Аналитика продаж',heading:'Разобраться в продажах без сборки отчётов вручную',description:'Панель на данных МоегоСклада и amoCRM. Продажи, каналы, товары и клиентская база доступны через общие фильтры.',instruction:'Выберите категорию и канал сбыта: суммы и график пересчитаются. Переключитесь на Товары или Клиенты и откройте строку.'},
 'marta-loyalty':{client:'МАРТАче',name:'Программа лояльности',heading:'Бонусная программа внутри МоегоСклада',description:'Бонусные поля заказа связаны с балансом покупателя. Сервис учитывает списания, ожидающие активации начисления и возвраты.',instruction:'Используйте доступные бонусы и нажмите Применить. Затем выберите Доставлен или Возврат и посмотрите баланс и историю операций.'},
 'mans-messages':{client:'MANSBAND',name:'Автоматические сообщения',heading:'Правила общения управляются из кассы',description:'Редактор автоматических сценариев, ручные шаблоны и порядок каналов. Условия, текст и проверка сообщения собраны в одном рабочем разделе.',instruction:'Откройте сценарий, измените текст и нажмите Проверить. Во вкладке Порядок каналов можно поменять очерёдность мессенджеров.'},
 '2my-production':{client:'2MY',name:'Производство',heading:'Нормы, материалы и экономика изделия',description:'Интерфейс производства: каталог норм, расход материалов, этапы работы и аналитика себестоимости. Можно разобрать изделие до размера и состава нормы.',instruction:'Найдите модель, откройте норму, выберите размер и измените расход. Во вкладке Аналитика раскройте остатки по размерам.'}
};
const frame=document.querySelector('#demo-frame'),viewport=document.querySelector('#demo-viewport'),canvas=document.querySelector('#demo-canvas'),tabs=[...document.querySelectorAll('[data-example]')],scaleSelect=document.querySelector('#demo-scale'),enlarge=document.querySelector('#demo-enlarge');
let selected='mans-orders',zoom='fit',ready=false;
const BASE_WIDTH=1280,BASE_HEIGHT=820;
function resize(){
 const fit=Math.min(1,viewport.clientWidth/BASE_WIDTH);
 const scale=zoom==='fit'?fit:Math.max(fit,Number(zoom));
 canvas.style.width=BASE_WIDTH*scale+'px';canvas.style.height=BASE_HEIGHT*scale+'px';
 frame.style.transform='scale('+scale+')';
 viewport.style.height=(zoom==='fit'?BASE_HEIGHT*scale:Math.min(BASE_HEIGHT*scale,innerWidth<760?540:820))+'px';
 viewport.classList.toggle('is-zoomed',zoom!=='fit');
 enlarge.setAttribute('aria-pressed',String(zoom!=='fit'));
 enlarge.innerHTML=zoom==='fit'?'Крупнее <span aria-hidden="true">↗</span>':'Общий вид <span aria-hidden="true">↙</span>';
}
function select(id,send=true,focus=false){
 const entry=entries[id];if(!entry)return;
 selected=id;
 tabs.forEach((tab,i)=>{const on=tab.dataset.example===id;tab.classList.toggle('active',on);tab.setAttribute('aria-selected',String(on));tab.tabIndex=on?0:-1;if(on){document.querySelector('#example-panel').setAttribute('aria-labelledby',tab.id);if(focus){tab.focus();tab.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});}}});
 document.querySelector('#demo-window-title').textContent=entry.client+' · '+entry.name;
 document.querySelector('#example-client').textContent=entry.client+' · '+entry.name;
 document.querySelector('#example-heading').textContent=entry.heading;
 document.querySelector('#example-description').textContent=entry.description;
 document.querySelector('#example-instruction').textContent=entry.instruction;
 frame.title=entry.client+' - интерактивный пример: '+entry.name;
 if(send&&ready)frame.contentWindow.postMessage({type:'msp-demo-init',id},'*');
 viewport.scrollTop=0;viewport.scrollLeft=0;resize();
}
tabs.forEach((tab,index)=>{
 tab.addEventListener('click',()=>select(tab.dataset.example));
 tab.addEventListener('keydown',event=>{let i;if(event.key==='ArrowRight')i=(index+1)%tabs.length;else if(event.key==='ArrowLeft')i=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')i=0;else if(event.key==='End')i=tabs.length-1;else return;event.preventDefault();select(tabs[i].dataset.example,true,true);});
});
scaleSelect.addEventListener('change',()=>{zoom=scaleSelect.value;resize();});
enlarge.addEventListener('click',()=>{zoom=zoom==='fit'?'0.75':'fit';scaleSelect.value=zoom;viewport.scrollLeft=0;resize();});
document.querySelector('#demo-reset').addEventListener('click',()=>{if(ready)frame.contentWindow.postMessage({type:'msp-demo-init',id:selected},'*');viewport.scrollTop=0;viewport.scrollLeft=0;});
addEventListener('message',event=>{
 if(event.source!==frame.contentWindow||event.origin!=='null')return;
 if(event.data?.type==='msp-demo-ready'){ready=true;frame.contentWindow.postMessage({type:'msp-demo-init',id:selected},'*');}
 if(event.data?.type==='msp-demo-selected'&&entries[event.data.id])select(event.data.id,false);
});
new ResizeObserver(resize).observe(viewport);select(selected,false);
})();
