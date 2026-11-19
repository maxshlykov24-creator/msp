import React,{useEffect,useState} from 'react';
import {DialogFrame} from './UI';
import {ProductPicker} from './ProductPicker';
import {ItemPhoto} from './ItemPhoto';
type Row=Record<string,any>;
type Line={item:Row;version:number;known:boolean;before:number;ice:number;reserved:number;total:string;frozen:string;releases:Record<string,string>};
export function StockEdits({items,location,api,cmd,close}:{items:Row[];location:string;api:(p:string)=>Promise<Row[]>;cmd:(c:string,p:any,id:string,reason:string)=>Promise<void>;close:()=>void}){
 const [lines,setLines]=useState<Line[]>([]),[batches,setBatches]=useState<Row[]>([]),[review,setReview]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[reason,setReason]=useState(''),[op,setOp]=useState('');
 useEffect(()=>{api('entities/freeze').then(setBatches).catch(e=>setError(e.message))},[]);
 function add(ids:string[]){
  setLines(current=>{
   const chosen=new Set(ids);
   for(const line of current)chosen.delete(line.item.id);
   const added=items.filter(item=>chosen.has(item.id)).map(item=>{
    const balance=item.balances?.find((b:Row)=>b.location===location);
    const ice=Number(balance?.frozen||0),reserved=Number(balance?.reserved||0);
    const before=Number(balance?.free||0)+reserved+ice;
    return {item,version:balance?.version||0,known:!!balance?.known,before,ice,reserved,total:balance?.known?String(before):'',frozen:String(ice),releases:{}}
   });
   return [...current,...added]
  });
  setError('')
 }
 function patch(id:string,data:Partial<Line>){setLines(lines.map(l=>l.item.id===id?{...l,...data}:l))}
 const lots=(l:Line)=>batches.filter(b=>b.item_id===l.item.id&&b.location===location&&Number(b.remaining)>0);
 function release(l:Line){return l.ice<=Number(l.frozen)?{}:lots(l).length===1?{[lots(l)[0].id]:String(l.ice-Number(l.frozen))}:Object.fromEntries(Object.entries(l.releases).filter(([,v])=>Number(v)>0))}
 const changed=lines.filter(l=>!l.known||Number(l.total)!==l.before||Number(l.frozen)!==l.ice);
 function preview(){setError('');for(const l of lines){if(!/^\d+$/.test(l.total)||!/^\d+$/.test(l.frozen)||Number(l.total)<Number(l.frozen)+l.reserved){setError('Проверь остаток, резерв и заморозку: '+l.item.name);return}if(l.ice>Number(l.frozen)&&Object.values(release(l)).reduce((sum,v)=>sum+Number(v),0)!==l.ice-Number(l.frozen)){setError('Выбери партии для разморозки');return}}setOp(crypto.randomUUID());setReview(true)}
 async function commit(){if(busy)return;setBusy(true);try{await cmd('stock.batch',{location,rows:changed.map(l=>({item_id:l.item.id,version:l.version,initial:!l.known,total:l.total,frozen:l.frozen,releases:release(l)}))},op,reason.trim());close()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <DialogFrame title={review?'Проверить изменения':'Редактировать остатки'} busy={busy} close={close}><div className="stock-edit-stack">
 {!review&&<ProductPicker label="Выбрать товары и размеры" items={items.filter(i=>i.kind==='garment'&&!lines.some(l=>l.item.id===i.id))} onAdd={add}/>}
 {(review?changed:lines).map(l=><section key={l.item.id} className="stock-edit-line"><div className="count-product"><ItemPhoto item={l.item} className="count-photo"/><span><strong>{l.item.model}</strong><small>{l.item.color} · {l.item.size}</small></span></div>{review?<div>Остаток {l.known?l.before:'не внесён'} → <b>{l.total}</b><br/>Заморожено {l.known?l.ice:'не внесено'} → <b>{l.frozen}</b></div>:<><div className="cut-form-grid"><label>Остаток<input aria-label={'Остаток '+l.item.name} type="number" min="0" value={l.total} onChange={e=>patch(l.item.id,{total:e.target.value})}/></label><label>Заморожено<input aria-label={'Заморожено '+l.item.name} type="number" min="0" value={l.frozen} onChange={e=>patch(l.item.id,{frozen:e.target.value})}/></label></div>{Number(l.frozen)<l.ice&&lots(l).length>1&&lots(l).map(b=><label key={b.id}>Снять из партии {b.id.slice(0,8)} · доступно {Number(b.remaining)}<input type="number" min="0" max={b.remaining} value={l.releases[b.id]||''} onChange={e=>patch(l.item.id,{releases:{...l.releases,[b.id]:e.target.value}})}/></label>)}<button className="text-button" onClick={()=>setLines(lines.filter(x=>x!==l))}>Убрать строку</button></>}</section>)}
 {!review&&<label>Основание<input value={reason} onChange={e=>setReason(e.target.value)} maxLength={500}/></label>}{error&&<div className="error" role="alert">{error}</div>}
 <div className="actions">{review?<><button className="secondary" disabled={busy} onClick={()=>setReview(false)}>Назад</button><button disabled={busy} onClick={commit}>Подтвердить изменения</button></>:<button disabled={!changed.length||!reason.trim()} onClick={preview}>Проверить изменения</button>}</div></div></DialogFrame>
}
