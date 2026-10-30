(function(){
'use strict';
const steps=[
 ['Заказ приходит в работу','Источник, товар, размер и контакты сохраняются вместе. Менеджеру не нужно собирать заявку из переписки.','Заказ получен ↙'],
 ['Понятно, что делать дальше','У заказа есть ответственный и следующий шаг. Уточнения клиента остаются в истории, а не в отдельных чатах.','В обработке →'],
 ['Нужный размер закреплён','Проверяем доступность конкретного размера и цвета. Правила резерва учитывают, откуда пришёл заказ.','Товар в резерве ✓'],
 ['Оплата связана с заказом','Статус оплаты виден рядом с составом заказа. Сотрудник понимает, когда можно переходить к отправке.','Оплата получена ✓'],
 ['Склад готовит отправку','Состав, способ доставки и статус передаются дальше по цепочке. Менеджер видит, что происходит с заказом.','Готов к отгрузке ↗']
];
const buttons=[...document.querySelectorAll('.flow-step')];
const scene=document.querySelector('.flow-scene');
let current=0, manuallySelected=false;
function select(index){
 if(index===current && manuallySelected)return;
 current=index;
 buttons.forEach((button,i)=>{button.classList.toggle('active',i===index);button.setAttribute('aria-pressed',String(i===index));});
 document.querySelector('#step-title').textContent=steps[index][0];
 document.querySelector('#step-copy').textContent=steps[index][1];
 document.querySelector('#order-state').textContent=steps[index][2];
 document.querySelector('.flow-counter').textContent='0'+(index+1)+' / 05';
}
buttons.forEach((button,index)=>button.addEventListener('click',()=>{manuallySelected=false;select(index);manuallySelected=true;}));
const motion=matchMedia('(prefers-reduced-motion:reduce)');
let queued=false;
function update(){
 queued=false;if(manuallySelected||motion.matches)return;
 const rect=scene.getBoundingClientRect();
 const progress=Math.max(0,Math.min(.999,(innerHeight*.8-rect.top)/(rect.height+innerHeight*.35)));
 const next=Math.floor(progress*5);if(next!==current)select(next);
}
addEventListener('scroll',()=>{if(!queued){queued=true;requestAnimationFrame(update);}},{passive:true});
motion.addEventListener('change',update);
const dialog=document.querySelector('#screen-dialog');
let opener;
document.querySelectorAll('.zoom-button,.screen-image').forEach(button=>button.addEventListener('click',()=>{opener=button;dialog.showModal();}));
document.querySelector('.close-dialog').addEventListener('click',()=>dialog.close());
dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
dialog.addEventListener('close',()=>opener?.focus());
})();
