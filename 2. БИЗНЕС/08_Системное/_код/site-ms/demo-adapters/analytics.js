// Entirely synthetic snapshot. Original client rendering and calculations are retained.
const demoSnapshot=(()=>{
 const orders=[],now=new Date(),names=['Кардиган объёмный','Жакет укороченный','Брюки прямые','Юбка миди','Джемпер фактурный','Топ базовый'],categories=['Кардиганы','Жакеты','Брюки','Юбки','Джемперы','Топы'];
 for(let i=0;i<96;i++){
  const dt=new Date(now.getFullYear(),now.getMonth(),now.getDate()-(i%58));const date=[dt.getFullYear(),String(dt.getMonth()+1).padStart(2,'0'),String(dt.getDate()).padStart(2,'0')].join('-');
  const k=i%6,qty=i%7===0?2:1,rev=(4900+k*1800)*qty;
  orders.push({id:'demo-order-'+i,name:'ДЕМО-'+String(1000+i),client:'Покупатель '+String(i%28+1).padStart(2,'0'),phone:'',agent_id:'demo-agent-'+i%28,buy:true,paid_date:date,created:date,channel:['Сайт','Шоурум','Telegram'][i%3],source:['Яндекс','Прямой заход','Соцсети','Повторная покупка'][i%4],city:['Москва','Санкт-Петербург','Казань'][i%3],manager:'Менеджер '+(i%2+1),paid:rev,sum:rev,confirmed_at:date+'T10:00:00',sent_at:date+'T15:00:00',stock_checked:true,await_stock:false,lines:[{name:names[k],category:categories[k],size:['XS-S','M-L','XL'][i%3],color:['Молочный','Чёрный','Бежевый'][i%3],qty,rev,cost:rev*.36,delivery:false}]});
 }
 return {generated:now.toISOString(),orders,returns:[],exchanges:[],return_gaps:[],leads:orders.map((o,i)=>({id:i+1,created:o.created,status:i%2?100:142,won:i%2===0})),tasks:{open:7,sampled:12,scope:'all_pipeline_leads',stages:[{id:100,name:'В работе'},{id:142,name:'Успешно'}]},amo_status:{status:'ok'},shipment_discounts:{documents:[],gaps:[]},payment_daily:orders.map(o=>({date:o.paid_date,sum:o.paid})),roster:{status:'ok'},responses:{samples:[],pending:[],unknown_rows:[],until_revenue:[],queue_verified:true,clock:'10-21',unknown_author:0}};
})();
window.fetch=async(url,options)=>{
 if(String(url).includes('snapshot.local.json'))return new Response(JSON.stringify(demoSnapshot),{headers:{'Content-Type':'application/json'}});
 if(String(url).includes('/api/refresh'))return new Response(JSON.stringify({running:false,result:'success',joined:true,version:2,state:'idle',status:'ok',updated:demoSnapshot.generated}),{headers:{'Content-Type':'application/json'}});
 throw Error('Внешние запросы отключены в демо');
};
