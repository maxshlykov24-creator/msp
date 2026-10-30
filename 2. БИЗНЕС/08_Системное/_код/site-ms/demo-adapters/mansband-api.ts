import { PRODUCTS } from '../data/mock';
import { classifyHall } from '@kassa/shared';
export const USE_MOCK=true;
export class ApiError extends Error {constructor(public status:number,message:string){super(message)}}
export const getToken=()=>null;
export const setToken=(_:unknown)=>{};
export const warmupApi=async()=>{};
export function apiPath(path:string,params:Record<string,unknown>){const q=new URLSearchParams();Object.entries(params).forEach(([k,v])=>{if(v!=null&&v!=='')q.set(k,String(v))});return path+'?'+q}
const rule=(name:string,content:string,type='stage')=>({name,enabled:true,pipelines:[9601214],statuses:[100],match:'all',conditions:[],trigger:{type,field:type==='date'?101:null,offset:-1,unit:'days',time:'10:00',delay_minutes:0,repeat:'each_entry'},recipient:'contact',phone_field:null,content,variants:{},store_field:3158921,window_start:10,window_end:20,late_policy:'skip',legacy_next_day:false});
let docs:any[]=[
 ...[['meeting','Напоминание о встрече','Напоминаем о вашей встрече в шоуруме. Будем рады видеть вас!','date'],['ready','Товар готов к примерке','Ваш товар уже в шоуруме. Можно записаться на примерку.','stage'],['rental','Возврат аренды','Напоминаем о дате возврата костюма.','date'],['delivery','Заказ отправлен','Ваш заказ передан в доставку.','stage']].map(([id,n,c,t])=>({id,kind:'rule',version:1,draft:rule(n,c,t),active:rule(n,c,t),checked:true})),
 {id:'routing',kind:'routing',version:1,draft:{order:['telegram','whatsapp','max'],previous_first:true,social_fallback:true},active:{order:['telegram','whatsapp','max'],previous_first:true,social_fallback:true},checked:true},
 {id:'manual-demo',kind:'manual',version:1,draft:{name:'Приглашение в шоурум',content:'Здравствуйте! Подберём костюм и удобное время для примерки.'},active:{name:'Приглашение в шоурум',content:'Здравствуйте! Подберём костюм и удобное время для примерки.'},checked:true}
];
async function request<T>(path:string,body?:any):Promise<T>{
 const u=new URL(path,'https://demo.invalid'),p=u.pathname;
 let result:any;
 if(p==='/message-templates')result={rules:docs,executor:'live'};
 else if(p.endsWith('/catalog'))result={fields:[{id:101,name:'Дата встречи',type:'date_time'},{id:3158921,name:'Магазин',type:'select',enums:[{id:1,value:'На Бауманской'},{id:2,value:'На Пятницкой'}]}],pipelines:[{id:9601214,name:'Продажи',_embedded:{statuses:[{id:100,name:'Встреча назначена'},{id:101,name:'Товар в магазине'},{id:102,name:'Доставлен'}]}}]};
 else if(p.endsWith('/manual'))result={items:[]};
 else if(p.endsWith('/journal'))result={items:[]};
 else if(p.endsWith('/draft')){let d=docs.find(x=>x.id===body.id);if(!d){d={id:body.id,kind:body.kind,version:0,active:null};docs.push(d)}d.draft=body.payload;d.version++;d.checked=false;result=d;}
 else if(p.includes('/check/')){const d=docs.find(x=>x.id===p.split('/').pop());if(d)d.checked=true;result={total:2,sample_limit:10,samples:[{lead_id:1001,text:d?.draft.content||'Сообщение по выбранному правилу',channels:['telegram']}],text:d?.draft.content||'',overlaps:[]};}
 else if(p.includes('/publish/')){const d=docs.find(x=>x.id===p.split('/').pop());d.active=structuredClone(d.draft);result=d;}
 else if(p.includes('/test/'))result={job_id:'demo',state:'preview'};
 else if(p.includes('/history/'))result={items:[]};
 else if(p==='/catalog/references')result={warehouses:[{id:'demo-a',name:'На Бауманской'},{id:'demo-b',name:'На Пятницкой'}]};
 else if(p==='/catalog/browse'){
 const q=u.searchParams.get('q')?.toLowerCase()||'',section=u.searchParams.get('section'),sub=u.searchParams.get('subCategory');
 const catalogue=PRODUCTS.map((x,i)=>({...x,name:x.name+' ('+(i%2?'Чёрный':'Белый')+', '+(48+i%3*2)+')',warehouseStocks:[{name:'На Бауманской',stock:x.stock,reserve:0},{name:'На Пятницкой',stock:3,reserve:0}]}));
 let items=catalogue.filter(x=>{const hall=classifyHall(x);return x.name.toLowerCase().includes(q)&&(!section||hall?.section===section)&&(!sub||hall?.group===sub)&&(!u.searchParams.get('priceMin')||x.price>=Number(u.searchParams.get('priceMin')))&&(!u.searchParams.get('priceMax')||x.price<=Number(u.searchParams.get('priceMax')))});
 const suits=PRODUCTS.slice(0,4).filter(x=>x.name.toLowerCase().includes(q)).map((x,i)=>({modelId:'demo-suit-'+i,title:x.name,variation:'DEMO-'+(i+1),color:i%2?'Графит':'Синий',pattern:null,fit:'Slim',height:'176',line:'regular',composition:['jacket','trousers'],mixed:false,whole:12,tolerant:0,orphans:0,onHalfSetWarehouse:0,priceRub:x.price,sizes:['48','50','52'].map(size=>({size,parts:{jacket:4,trousers:4,vest:0},whole:4,tolerant:0,orphans:[],warehouses:[{name:'На Бауманской',whole:2},{name:'На Пятницкой',whole:2}]}))}));
 const sectionCounts:Record<string,number>={},groupCounts:Record<string,number>={};
 catalogue.forEach(x=>{const h=classifyHall(x);if(h){sectionCounts[h.section]=(sectionCounts[h.section]||0)+1;groupCounts[h.group]=(groupCounts[h.group]||0)+1}});
 result={items,itemsTotal:items.length,suits:!section||section==='suits'?suits:[],groupCounts,sectionCounts,page:1,pageSize:60};
 }else if(p.includes('/settings'))result={};
 else if(p.includes('/users'))result={items:[]};
 else result={items:[],rows:[],ok:true};
 return structuredClone(result) as T;
}
export async function apiBlob(){return new Blob(['Демонстрационный документ'],{type:'text/plain;charset=utf-8'})}
export const api={get:<T>(p:string)=>request<T>(p),getQuery:<T>(p:string,q:Record<string,unknown>)=>request<T>(apiPath(p,q)),post:<T>(p:string,b?:unknown)=>request<T>(p,b),put:<T>(p:string,b?:unknown)=>request<T>(p,b),patch:<T>(p:string,b?:unknown)=>request<T>(p,b),del:<T>(p:string)=>request<T>(p)};
