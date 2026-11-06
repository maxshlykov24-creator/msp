// Run in the API container. Additive only; no leads, messages or existing stages are changed.
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { getEnv } from '../dist/env.js';
import { allowedAmoPipeline, typedRoutingEnabled } from '../dist/services/amoRouting.js';
import { AD_SOURCES } from '@kassa/shared';
const env=getEnv(), apply=process.argv.includes('--apply');
const dir=process.env.PREPARATION_DIR || '/tmp/mansband-preparation';
await mkdir(dir,{recursive:true});
async function request(path,method='GET',body){
 await new Promise(r=>setTimeout(r,260));
 const res=await fetch(env.AMOCRM_API_BASE.replace(/\/$/,'')+path,{method,headers:{Authorization:`Bearer ${env.AMOCRM_LONG_LIVED_TOKEN}`,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 if(!res.ok)throw Error(`${method} ${path}: HTTP ${res.status}`);
 return res.status===204?{}:res.json();
}
async function allFields(){const out=[];for(let page=1;page<50;page++){const r=await request(`/leads/custom_fields?limit=250&page=${page}`);out.push(...(r._embedded?.custom_fields||[]));if(!r._links?.next)return out;}throw Error('Incomplete fields');}
const pipelines=(await request('/leads/pipelines'))._embedded.pipelines;
let fields=await allFields();
const before={pipelines,fields};
try {await readFile(`${dir}/typed-before.json`);}catch{await writeFile(`${dir}/typed-before.json`,JSON.stringify(before,null,2));}
const common=['Новая заявка','Взято в работу','Дано обещание','Хочет прийти'];
const hold=['Ждет товар','Товар в пути','Товар в магазине','Товар отложен'];
const specs={
 'Продажа офлайн':[...common,'Встреча назначена',...hold,'Сертификат оплачен'],
 'Продажа онлайн':['Новая заявка','Взято в работу','Дано обещание','Хочет заказать','Сертификат оплачен','Товар в магазине','Передан на сборку','Собран','Вызван курьер','Отправлен','Доставлен','Не выкуплен'],
 'Аренда':[...common,'Встреча назначена',...hold,'Аренда оплачена','В аренде'],
 'Продажа компании':[...common,...hold,'Счёт запрошен','Счет выставлен','Документы готовы','Документы переданы','Оплачено'],
};
if(!apply){console.log(JSON.stringify({mode:'read-only',existing:pipelines.map(p=>({id:p.id,name:p.name})),planned:Object.keys(specs)}));process.exit(0);}
if(typedRoutingEnabled()||allowedAmoPipeline(99999999))throw Error('Preparation requires disabled routing and active guard');
const manifest={pipelines:{},fields:{},createdAt:new Date().toISOString()};
for(const [name,stages] of Object.entries(specs)){
 const matches=pipelines.filter(p=>p.name===name);if(matches.length>1)throw Error('Ambiguous pipeline');
 let p=matches[0];
 if(!p){p=(await request('/leads/pipelines','POST',[{name,sort:100+Object.keys(manifest.pipelines).length,is_main:false,is_unsorted_on:false,_embedded:{statuses:stages.map((name,i)=>({name,sort:(i+1)*10,color:'#c1e0ff'}))}}]))._embedded.pipelines[0];}
 if(p.is_main)throw Error('New pipeline must not be main');
 for(const name of stages)if(!p._embedded.statuses.some(s=>s.name===name))throw Error(`Missing stage ${name}`);
 manifest.pipelines[name]=p.id;
 await writeFile(`${dir}/typed-manifest.json`,JSON.stringify(manifest,null,2));
}
const freshP=(await request('/leads/pipelines'))._embedded.pipelines;
const required=Object.values(manifest.pipelines).map(id=>({pipeline_id:id,status_id:freshP.find(p=>p.id===id)._embedded.statuses.find(s=>s.name==='Взято в работу').id}));
const lists={
 'Тип заявки':['Продажа офлайн','Продажа онлайн','Продажа компании','Сертификат пластик','Сертификат электронный','Аренда','Слив','Не слив','Дефект','Химчистка','Перешив','Перепутан размер','Некорректная бирка','Возврат','Обмен'],
 'Касание':['Целевое','Нецелевое'],
 'Причина ожидания':['Не оказалось размера','Ушел смотреть у конкурентов'],
};
for(const [name,values] of Object.entries(lists)){
 let f=fields.find(f=>f.name===name);
 if(!f){f=(await request('/leads/custom_fields','POST',[{name,type:'select',enums:values.map((value,i)=>({value,sort:i+1})),required_statuses:name==='Причина ожидания'?[]:required}]))._embedded.custom_fields[0];fields.push(f);}
 if(f.type!=='select'||values.some(v=>!f.enums?.some(e=>e.value===v)))throw Error(`Existing field conflicts: ${name}`);
 manifest.fields[name]=f.id;
}
const client=fields.find(f=>f.name==='Тип клиента');if(!client)throw Error('Missing client type');
const oldRequired=client.required_statuses||[];
const merged=[...oldRequired,...required.filter(r=>!oldRequired.some(x=>x.pipeline_id===r.pipeline_id&&x.status_id===r.status_id))];
if(merged.length!==oldRequired.length)await request(`/leads/custom_fields/${client.id}`,'PATCH',{required_statuses:merged});
for(const [name,values] of [['Источник',[...AD_SOURCES,'Без предв. касания']],['Причина провала',['Спам','Сдвоенная заявка']]]){
 const f=fields.find(f=>f.name===name);if(!f||f.type!=='select')throw Error(`Missing ${name}`);
 const added=[...new Set(values)].filter(v=>!f.enums.some(e=>e.value===v));
 if(added.length)await request(`/leads/custom_fields/${f.id}`,'PATCH',{enums:[...f.enums.map(({id,value,sort})=>({id,value,sort})),...added.map((value,i)=>({value,sort:1000+i}))]});
}
const after={pipelines:(await request('/leads/pipelines'))._embedded.pipelines,fields:await allFields()};
for(const old of pipelines){const current=after.pipelines.find(p=>p.id===old.id);if(JSON.stringify(current)!==JSON.stringify(old))throw Error(`Original pipeline changed: ${old.id}`);}
for(const old of fields.filter(f=>before.fields.some(x=>x.id===f.id))){const current=after.fields.find(f=>f.id===old.id);if(!current||current.name!==old.name||current.type!==old.type)throw Error('Existing field changed');for(const e of old.enums||[])if(!current.enums?.some(v=>v.id===e.id&&v.value===e.value))throw Error('Existing enum changed');}
await writeFile(`${dir}/typed-after.json`,JSON.stringify(after,null,2));
await writeFile(`${dir}/typed-manifest.json`,JSON.stringify(manifest,null,2));
console.log(JSON.stringify({verified:true,manifest,existingPipelinesUnchanged:true,existingEnumsPreserved:true}));
