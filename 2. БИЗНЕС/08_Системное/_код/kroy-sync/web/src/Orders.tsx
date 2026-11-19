import React,{useEffect,useMemo,useRef,useState} from 'react';
import {Icon,DialogFrame} from './UI';
import {ItemPhoto} from './ItemPhoto';
import {compareVariants,words} from './catalogData';
import {ReadOrders,useOrderList} from './useOrderList';
import './Orders.css';
type Row=Record<string,any>;
const states:Record<string,string>={ordered:'Заказ зарегистрирован',new:'Новый',assembling:'На сборке',ready:'Ожидает отгрузки',shipping:'Отгружен',pickup:'На ПВЗ',delivered:'Доставлен',cancelled:'Отменён',returned:'Возврат',unknown:'Уточняется'};
const stockStates:Record<string,string>={covered_by_count:'Учтено пересчётом',unprocessed:'Ожидает сверки',needs_mapping:'Нужно сопоставить',shortage:'Не хватает остатка',needs_review:'Нужна проверка',history:'История',marketplace:'Склад площадки',reserved:'В резерве',shipped:'Списано по отгрузке',cancelled:'Резерв снят',return_pending:'Ожидается приёмка',returned:'Возврат принят',returned_partial:'Возврат принят частично',waiting_stock:'Нужен начальный остаток'};
const dateFormat=new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
const date=(s?:string)=>s?dateFormat.format(new Date(s)):'—';
const number=(q:any)=>q==null?'—':Number(q).toLocaleString('ru-RU');
const orderTabs:[string,string][]=[['','Все'],['new','Новые'],['assembling','На сборке'],['ready','Ожидают отгрузки'],['shipping','Отгружены'],['pickup','На ПВЗ'],['delivered','Доставлены'],['cancelled','Отменены'],['returned','Возвраты'],['ordered','Заказаны'],['unknown','Уточняются']];
const phases:Record<string,string>={'wb-catalog':'Товары и штрихкоды Wildberries','wb-new':'Новые Wildberries','wb-fbs':'Сборочные задания Wildberries','wb-statistics':'Заказы Wildberries','wb-sales':'Выкупы и возвраты Wildberries','ozon-open':'Открытые Ozon','ozon-fbs':'Ozon FBS','ozon-fbo':'Ozon FBO','ozon-returns':'Возвраты Ozon','ozon-history-fbs':'История Ozon FBS','ozon-history-fbo':'История Ozon FBO'};
const schemeName=(value:string)=>value==='fbo'?'FBO':value==='fbs'?'FBS':'Схема не указана';
function orderLabel(row:Row){const ext=String(row.external_id||''),cabinet=String(row.order_number||'');if(row.platform==='wb'&&cabinet&&!/^\d{4,16}$/.test(ext))return cabinet;return ext}
function orderStamp(value?:string){if(!value)return '';const parts=dateFormat.formatToParts(new Date(value));const pick=(type:string)=>parts.find(part=>part.type===type)?.value||'';return `от ${pick('day')}.${pick('month')}.${pick('year')} ${pick('hour')}:${pick('minute')}`}
function orderAge(value?:string){if(!value)return '';const time=Date.parse(value);if(!Number.isFinite(time)||time>Date.now())return '';const minutes=Math.floor((Date.now()-time)/60000);if(minutes<1)return 'только что';const hours=Math.floor(minutes/60),days=Math.floor(hours/24);if(days>=2)return `${days} дн назад`;return (hours?`${hours} ч `:'')+`${minutes%60} мин назад`}
function orderLate(row:Row){const stage=row.display_status||row.status;if(!['new','assembling','ready'].includes(stage))return false;const due=Date.parse(row.shipment_due||'');if(Number.isFinite(due))return Date.now()>=due;const created=Date.parse(row.created_at||'');return Number.isFinite(created)&&Date.now()-created>=86400000}
function OrderMark({row,copy}:{row:Row;copy?:boolean}){const label=orderLabel(row);return <div className="order-mark"><div className="order-mark-id"><strong title={row.external_id!==label?row.external_id:label} onClick={copy?(e:React.MouseEvent)=>{e.stopPropagation();navigator.clipboard?.writeText(label)}:undefined}>{label}</strong><span className="order-badge">{row.platform==='wb'?'WB':'Ozon'}</span>{(row.scheme==='fbs'||row.scheme==='fbo')&&<span className="order-badge">{schemeName(row.scheme)}</span>}</div>{row.created_at&&<small>{orderStamp(row.created_at)}</small>}<small className={'order-age'+(orderLate(row)?' is-late':'')}>{orderAge(row.created_at)}</small></div>}
export function Orders({api,read,items,canWrite,canMap,action,onCount}:{read:ReadOrders;api:(p:string,b?:any)=>Promise<any>;items:Row[];canWrite:boolean;canMap:boolean;action:(kind:string,row:Row)=>void;onCount?:()=>void}){
 const [page,setPage]=useState(1),[platform,setPlatform]=useState(''),[scheme,setScheme]=useState(''),[state,setState]=useState(''),[q,setQ]=useState(''),[start,setStart]=useState(''),[end,setEnd]=useState('');
 const [sync,setSync]=useState<Row>({}),[syncOpen,setSyncOpen]=useState(false),[error,setError]=useState(''),[opened,setOpened]=useState<Row|null>(null);
 const [cards,setCards]=useState(()=>window.matchMedia('(max-width: 1100px)').matches);
 const query=q.trim(), valid=!start||!end||start<=end;
 const scope=new URLSearchParams({platform,scheme,start,end,q:query}).toString();
 const list=useOrderList(read,scope,state,page,valid,query);
 const rows=list.data?.rows||[],total=list.data?.total||0,ready=Boolean(list.data),counts=list.counts;
 const latest=useRef({read,api}),syncRequest=useRef<AbortController|null>(null),latestCycle=useRef(''),manual=useRef(false),detailRequest=useRef<AbortController|null>(null);
 latest.current={read,api};
 useEffect(()=>{
   const media=window.matchMedia('(max-width: 1100px)'),change=()=>setCards(media.matches);
   media.addEventListener('change',change);
   return ()=>media.removeEventListener('change',change);
 },[]);
 useEffect(()=>{window.scrollTo({top:0,behavior:'instant'})},[page]);
 useEffect(()=>{
   if(list.data && page>1) {
     const last=Math.max(1,Math.ceil(list.data.total/list.data.per_page));
     if(page>last)setPage(last);
   }
 },[page,list.data]);
 const reset=()=>{setPlatform('');setScheme('');setState('');setQ('');setStart('');setEnd('');setPage(1)};
 useEffect(()=>{
   let alive=true;
   async function check(background:boolean) {
     if(!alive||syncRequest.current)return;
     const controller=new AbortController();syncRequest.current=controller;
     let changed=false;
     try {
       const status=await latest.current.read('orders/sync',controller.signal);
       if(!alive)return;
       setSync(status);
       const finished=status.cycle?.finished_at;
       if(finished){
         changed=Boolean(latestCycle.current&&finished!==latestCycle.current);
         latestCycle.current=finished;
         window.dispatchEvent(new CustomEvent('yuji:orders-updated',{detail:finished}));
       }
     } catch { /* Saved orders remain usable during a failed status check. */ }
     finally {if(syncRequest.current===controller)syncRequest.current=null}
     if(alive && changed)await list.invalidate();
     else if(alive && background)await list.load();
   }
   const checkVisible=()=>{if(document.visibilityState==='visible')void check(true)};
   const refresh=async()=>{
     if(manual.current)return;
     manual.current=true;setError('');
     window.dispatchEvent(new CustomEvent('yuji:orders-busy',{detail:true}));
     try {
       if(canWrite)await latest.current.api('orders/refresh',{});
       if(alive)await Promise.all([list.invalidate(),check(false)]);
     } catch(e){if(alive)setError(e instanceof Error?e.message:String(e))}
     finally {manual.current=false;if(alive)window.dispatchEvent(new CustomEvent('yuji:orders-busy',{detail:false}))}
   };
   void check(false);
   const timer=window.setInterval(checkVisible,30000);
   document.addEventListener('visibilitychange',checkVisible);
   window.addEventListener('yuji:sync-orders',refresh);
   return ()=>{alive=false;syncRequest.current?.abort();syncRequest.current=null;detailRequest.current?.abort();window.clearInterval(timer);document.removeEventListener('visibilitychange',checkVisible);window.removeEventListener('yuji:sync-orders',refresh);window.dispatchEvent(new CustomEvent('yuji:orders-busy',{detail:false}))};
 },[canWrite,list.invalidate,list.load]);
 async function open(row:Row){
   detailRequest.current?.abort();
   const controller=new AbortController();detailRequest.current=controller;
   try{const order=await read('orders/'+row.id,controller.signal);if(!controller.signal.aborted)setOpened(order)}
   catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:String(e))}
 }
 useEffect(()=>{
   if(!opened)return;
   const controller=new AbortController();
   latest.current.read('orders/'+opened.id,controller.signal).then(d=>{if(!controller.signal.aborted)setOpened(current=>current?.id===d.id?d:current)}).catch(()=>{});
   return ()=>controller.abort();
 },[list.data]);
 const index=useMemo(()=>new Map(items.map(i=>[i.id,i])),[items]);
 const byId=(id:string)=>index.get(id),itemName=(id:string)=>byId(id)?.name||id;

 const bad=Object.entries(sync).filter(([k,v])=>phases[k]&&v.state==='error');
 const running=sync.cycle?.state==='running';
 const clock=(value?:string)=>value?new Date(value).toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'}):'';
 const listLabel=ready?words(total,'заказ','заказа','заказов'):'';
 const pending=sync.request?.requested_at && sync.request.requested_at>(sync.request.handled_at||'');
 const syncNote=running?'Сверка с площадками идёт в фоне':pending?'Запрошена сверка с площадками':sync.cycle?.state==='partial'||sync.cycle?.state==='error'?'Сверка завершилась с замечаниями':sync.cycle?.finished_at?`Площадки сверены в ${clock(sync.cycle.finished_at)}`:'';
 const chosenCount=[scheme,state,start,end].filter(Boolean).length;
 const accountLabel=(value:string)=>value==='waiting_stock'?'':stockStates[value]||'Ожидает сверки';
 function changed(set:(v:any)=>void,v:any){set(v);setPage(1)}
 const product=(l:Row)=>{const i=byId(l.item_id);return <span className="order-product"><ItemPhoto item={i||{}} className="order-photo"/><span><strong>{i?.model||l.offer_id||l.sku||l.barcode||'Товар не сопоставлен'}</strong><small>{i?.color}{i?.size&&<span className="size">{i.size}</span>}</small><small>{l.barcode||l.sku||l.offer_id}</small></span></span>};
 return <div className="orders-page">{list.error&&<div className="error" role="alert">{list.error} <button className="text-button" onClick={()=>void list.load(true)}>Повторить</button></div>}{error&&<div className="error" role="alert">{error}</div>} {bad.length>0&&<div className="order-sync-warning"><Icon name="attention" size={17}/><span>Не удалось прочитать: {bad.map(([k])=>phases[k]).join(', ')}. Уже полученные заказы сохранены.</span><button onClick={()=>setSyncOpen(true)}>Подробнее</button></div>}{rows.some(r=>r.accounting_state==='waiting_stock')&&<div className="notice">Нужен начальный остаток. Пока его нет, заказы не списывают склад. {onCount&&<button className="secondary" onClick={onCount}>Пересчёт</button>}</div>}<><div className="orders-platforms" role="group" aria-label="Маркетплейс">{[['','Все площадки'],['wb','Wildberries'],['ozon','Ozon']].map(([value,label])=><button aria-pressed={platform===value} className={platform===value?'active':''} key={value} onClick={()=>changed(setPlatform,value)}>{label}</button>)}</div><div className="orders-scheme-row"><div className="orders-schemes" role="group" aria-label="Схема заказа">{[['','Все схемы'],['fbs','FBS'],['fbo','FBO']].map(([v,label])=><button type="button" key={v||'all-schemes'} aria-pressed={scheme===v} className={scheme===v?'active':''} onClick={()=>changed(setScheme,v)}>{label}</button>)}</div><div className="orders-dates"><label>Создан с<input aria-label="Заказы созданы с" type="date" max={end||undefined} value={start} onChange={e=>changed(setStart,e.target.value)}/></label><label>По дату<input aria-label="Заказы созданы по" type="date" min={start||undefined} value={end} onChange={e=>changed(setEnd,e.target.value)}/></label></div></div><div className="orders-status-tabs" role="group" aria-label="Этап заказа">{orderTabs.map(([value,label])=><button type="button" key={value||'all'} aria-pressed={state===value} className={state===value?'active':''} onClick={()=>changed(setState,value)}><span>{label}</span><span className="order-count">{counts?(counts[value||'all']||0).toLocaleString('ru-RU'):'…'}</span></button>)}</div><div className="orders-search-row"><label className="orders-search"><Icon name="search" size={18}/><input aria-label="Найти заказ" placeholder="Номер заказа, штрихкод или артикул" value={q} onChange={e=>changed(setQ,e.target.value)}/>{q&&<button aria-label="Очистить поиск заказов" onClick={()=>changed(setQ,'')}><Icon name="close" size={15}/></button>}</label></div>
 <div className="orders-list-meta"><span>{listLabel}{syncNote&&<small>{syncNote}</small>}</span>{(chosenCount>0||q||platform)&&<button className="text-button" onClick={reset}>Сбросить фильтры</button>}</div>
 {!valid?<p className="notice" role="alert">Конечная дата должна быть не раньше начальной.</p>:!ready&&!list.error?<div className="orders-loading" role="status" aria-live="polite"><span className="orders-spinner" aria-hidden="true"/><p>Загружаем заказы</p></div>:rows.length?<>{!cards?<div className="orders-table-wrap"><table className="orders-table"><thead><tr><th>Отправление</th><th>Товары</th><th className="order-qty">Кол-во</th><th>Статус</th><th>Учёт у нас</th><th>Передача до</th><th/></tr></thead><tbody>{rows.map(r=><tr key={r.id} tabIndex={0} onClick={()=>open(r)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open(r)}}}><td><OrderMark row={r} copy/></td><td><div className="order-mini-products">{r.lines.slice(0,2).map((l:Row,n:number)=><div key={n}>{product(l)}</div>)}{r.lines.length>2&&<small>Ещё {r.lines.length-2} позиций</small>}</div></td><td className="order-qty">{r.lines.slice(0,2).map((l:Row,n:number)=><span key={n}>{number(l.quantity)}</span>)}</td><td><span className={'order-status '+(r.display_status||r.status)}>{states[r.display_status||r.status]}</span>{(r.has_returns||r.returns?.length>0)&&<small className="order-warning">Есть возврат</small>}</td><td><span className={['shortage','needs_mapping','needs_review'].includes(r.accounting_state)?'order-warning':'muted'}>{accountLabel(r.accounting_state)}</span></td><td>{date(r.shipment_due)}</td><td><Icon name="chevron" size={16}/></td></tr>)}</tbody></table></div>:<div className="orders-cards">{rows.map(r=><button className="order-card" key={r.id} onClick={()=>open(r)}><span className="order-card-top"><OrderMark row={r}/><span className={'order-status '+(r.display_status||r.status)}>{states[r.display_status||r.status]}</span></span>{r.lines.slice(0,2).map((l:Row,n:number)=><span className="order-card-product" key={n}>{product(l)}<span className="order-line-qty"><small>Кол-во</small><strong>{number(l.quantity)}</strong></span></span>)}<span className="order-card-bottom">{accountLabel(r.accounting_state)}{r.has_returns&&<small className="order-warning">Есть возврат</small>}<Icon name="chevron" size={16}/></span></button>)}</div>}<div className="orders-pagination"><button className="secondary" disabled={page===1} onClick={()=>setPage(page-1)}>← Назад</button><span>{page} из {Math.max(1,Math.ceil(total/50))}</span><button className="secondary" disabled={page*50>=total} onClick={()=>setPage(page+1)}>Далее →</button></div></>:ready?<div className="empty"><Icon name="orders" size={28}/><p>{total===0&&(chosenCount||query||platform)?'По этим условиям заказы не найдены':running?'Заказы ещё подгружаются на сервер':'Заказов пока нет'}</p></div>:null}</>

 {opened&&<DialogFrame title={'Заказ '+orderLabel(opened)} close={()=>setOpened(null)} className="order-detail"><div className="order-detail-heading"><span>{opened.platform==='wb'?'Wildberries':'Ozon'} · {schemeName(opened.scheme)}</span><span className={'order-status '+(opened.display_status||opened.status)}>{states[opened.display_status||opened.status]}</span></div>{(opened.order_number&&opened.order_number!==orderLabel(opened)||opened.external_id!==orderLabel(opened)||opened.parent)&&<p className="order-parent">{opened.order_number&&opened.order_number!==orderLabel(opened)&&<>Заказ площадки: {opened.order_number}</>}{opened.external_id!==orderLabel(opened)&&<small>Номер позиции: {opened.external_id}</small>}{opened.parent&&<small>Разделён из отправления {opened.parent}</small>}</p>}<div className="order-details-grid"><div><small>Создан</small><strong>{date(opened.created_at)}</strong></div><div><small>Передача до</small><strong>{date(opened.shipment_due)}</strong></div><div><small>Фактическая передача</small><strong>{date(opened.handed_at)}</strong></div><div><small>Сверен</small><strong>{date(opened.observed_at)}</strong></div></div><div className="order-detail-lines">{opened.lines.map((l:Row,n:number)=>({...l,source_line:n})).sort((a:Row,b:Row)=>compareVariants(byId(a.item_id)||{},byId(b.item_id)||{})).map((l:Row,n:number)=><div key={n}>{product(l)}<span className="order-line-qty"><small>Кол-во</small><strong>{number(l.quantity)}</strong></span>{l.mapping!=='matched'&&<div className="order-mapping-warning"><small className="order-warning">Требуется сопоставление</small>{canMap&&<button className="text-button" onClick={()=>{action('map',{...opened,map_line:l.source_line});setOpened(null)}}>Сопоставить</button>}</div>}</div>)}</div>{(opened.raw_status||opened.supplier_status)&&<p className="order-raw-status">Статус площадки: {[opened.supplier_status,opened.raw_status].filter(Boolean).join(' / ')}</p>}<div className="order-detail-stock"><small>Учёт у нас</small><strong>{stockStates[opened.accounting_state]||'Ожидает сверки'}</strong>{opened.status_warning&&<p className="order-warning">{opened.status_warning}</p>}{opened.accounting_note&&<p>{opened.accounting_note}</p>}{opened.ledger?.shortages?.length>0&&<p>{opened.ledger.shortages.map((l:Row)=>itemName(l.item_id)+': не хватает '+number(l.quantity)).join('; ')}</p>}{canWrite&&opened.ledger&&<div className="actions">{opened.ledger.status==='shortage'&&<button onClick={()=>{action('reserve',opened.ledger);setOpened(null)}}>Пополнить резерв</button>}{['shipped','return_pending','returned_partial'].includes(opened.ledger.status)&&<button onClick={()=>{action('receive',opened.ledger);setOpened(null)}}>Принять физический возврат</button>}</div>}</div>{opened.returns.length>0&&<div className="order-return-list"><h3>Возвраты площадки</h3>{opened.returns.map((r:Row)=><div key={r.id}><span>{itemName(r.item_id)||r.sku}</span><strong>{number(r.quantity)}</strong><small>{r.status_label} · {date(r.changed_at||r.return_at)}</small></div>)}</div>}<details className="order-history"><summary>История статусов</summary>{opened.history.slice().reverse().map((h:Row,n:number)=><div key={n}><span>{states[opened.platform==='ozon'&&h.raw_status==='awaiting_packaging'?'new':h.status]||h.status}</span><small>{date(h.at)}</small></div>)}</details></DialogFrame>}
 {syncOpen&&<DialogFrame title="Синхронизация заказов" close={()=>setSyncOpen(false)} className="order-sync-detail">{Object.entries(phases).map(([key,label])=>{const d=sync[key];return <section key={key}><div><strong>{label}</strong><span className={'order-sync-state '+(d?.state||'')}>{!d?'Ожидает загрузки':d.state==='running'?'Загружается':d.state==='ready'?'Сверено':d.state==='waiting'?'Пауза площадки':'Нужна проверка'}</span></div>{d?.success_at&&<small>Успешно: {date(d.success_at)}</small>}{d?.history_from&&<small>История с {date(d.history_from)}</small>}{key.includes('history')&&d?.cursor&&<small>{d.complete?'История загружена':'Загружено до '+date(d.cursor)} · с {date(d.start)}</small>}{d?.error&&<p>{d.error}</p>}{d?.retry_at&&<small>Повтор после {date(d.retry_at)}</small>}</section>})}</DialogFrame>}
 </div>
}
