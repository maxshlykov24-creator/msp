import React,{useEffect,useId,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {Icon,isLastPopupTab,useAnchoredPopup} from './UI';
import {ItemPhoto} from './ItemPhoto';
import {compareSizes,compareVariants,productCategory,words} from './catalogData';
import './Products.css';

type Item=Record<string,any>;

export function ProductPicker({items,value,onChange,label,disabled=false}:{items:Item[];value:string;onChange:(id:string)=>void;label:string;disabled?:boolean}){
 const[open,setOpen]=useState(false),[query,setQuery]=useState(''),[category,setCategory]=useState('Все'),[closed,setClosed]=useState<Set<string>>(new Set()),[expanded,setExpanded]=useState<Set<string>>(new Set());
 const trigger=useRef<HTMLButtonElement>(null),menu=useRef<HTMLDivElement>(null),input=useRef<HTMLInputElement>(null),id=useId();
 const selected=items.find(i=>i.id===value);
 const q=query.trim().toLocaleLowerCase('ru');
 const shown=items.filter(i=>!q||[i.model,i.name,i.color,i.size,...(i.barcodes||[])].join(' ').toLocaleLowerCase('ru').includes(q));
 const categories=[...new Set(items.map(productCategory))].sort((a,b)=>a.localeCompare(b,'ru'));
 const groups=(category==='Все'?categories:[category]).filter(c=>shown.some(i=>productCategory(i)===c));
 const gridSizes=[...new Set(items.map(i=>i.size||''))].sort(compareSizes);
 function close(){setOpen(false);setQuery('')}
 const position=useAnchoredPopup(open,trigger,menu,{width:680,height:540},close);
 function toggle(set:Set<string>,update:(next:Set<string>)=>void,key:string){const copy=new Set(set);copy.has(key)?copy.delete(key):copy.add(key);update(copy)}
 function pick(item:Item){if(trigger.current?.matches(':disabled'))return;onChange(item.id);close();trigger.current?.focus({preventScroll:true})}
 useLayoutEffect(()=>{if(open&&trigger.current?.matches(':disabled'))close()});
 useLayoutEffect(()=>{
  if(open)(matchMedia('(pointer:fine)').matches ? input.current : menu.current)?.focus({preventScroll:true});
 },[open]);
 const caption=selected?[selected.color,selected.size,selected.model||selected.name].filter(Boolean).join(' · '):'Выбери позицию';
 return <div className="product-picker"><button type="button" ref={trigger} className="product-picker-trigger" disabled={disabled} aria-label={label} aria-haspopup="dialog" aria-expanded={open} aria-controls={open?id:undefined} onClick={()=>open?close():setOpen(true)}>{selected&&<ItemPhoto item={selected} className="picker-photo"/>}<span>{caption}</span><Icon name="down" size={16}/></button><input disabled={disabled} className="select-validation" tabIndex={-1} aria-hidden="true" required value={value} onChange={()=>{}} onInvalid={e=>{e.preventDefault();trigger.current?.focus()}}/>{open&&createPortal(<div id={id} ref={menu} role="dialog" aria-label={label} className="product-picker-menu picker-popup" tabIndex={-1} style={{...position,height:position.maxHeight}} onKeyDown={e=>{
  if(e.key==='Escape'){e.preventDefault();e.stopPropagation()}
  else if(!isLastPopupTab(e,e.currentTarget))return;
  close();trigger.current?.focus({preventScroll:true});
 }}><div className="picker-search"><Icon name="search" size={16}/><input ref={input} aria-label="Найти позицию" placeholder="Модель, цвет, размер или штрихкод" value={query} onChange={e=>setQuery(e.target.value)}/></div><div className="picker-tabs" role="group" aria-label="Разделы">{['Все',...categories].map(c=><button type="button" key={c} aria-pressed={category===c} className={category===c?'active':''} onClick={()=>setCategory(c)}>{c==='Все'?'Все разделы':c}</button>)}</div><div className="picker-list">{groups.map(c=>{const rows=shown.filter(i=>productCategory(i)===c),models=[...new Set(rows.map(i=>i.model||i.name))].sort((a,b)=>a.localeCompare(b,'ru'));return <section key={c}><button type="button" className="picker-heading" aria-expanded={!closed.has(c)} onClick={()=>toggle(closed,setClosed,c)}><Icon name={closed.has(c)?'chevron':'down'} size={14}/><span>{c}</span><small>{words(models.length,'модель','модели','моделей')}</small></button>{!closed.has(c)&&models.map(model=>{const variants=rows.filter(i=>(i.model||i.name)===model).sort(compareVariants),photo=variants.find(i=>i.photo||i.photo_preview)||variants[0],key=c+'::'+model,isOpen=expanded.has(key)||!!q,colors=[...new Set(variants.map(i=>i.color))].filter(Boolean).join(' · ');return <div key={key}><button type="button" className={'picker-model'+(isOpen?' is-open':'')} aria-expanded={isOpen} onClick={()=>toggle(expanded,setExpanded,key)}><ItemPhoto item={photo} className="picker-photo"/><span><b>{model}</b><em>{colors?colors+' · ':''}{words(variants.length,'позиция','позиции','позиций')}</em></span><Icon name={isOpen?'down':'chevron'} size={15}/></button>{isOpen&&<div className="picker-matrix-wrap"><table className="picker-matrix" style={{minWidth:112+gridSizes.length*76}}><colgroup><col className="picker-color"/>{gridSizes.map(size=><col key={size||'—'}/>)}</colgroup><thead><tr><th>Цвет</th>{gridSizes.map(size=><th key={size||'—'}>{size||'—'}</th>)}</tr></thead><tbody>{[...new Set(variants.map(i=>i.color||''))].map(color=><tr key={color||'—'}><th>{color||'—'}</th>{gridSizes.map(size=>{const item=variants.find(i=>(i.color||'')===color&&(i.size||'')===size);if(!item)return <td key={size} className="picker-gap"/>;const on=item.id===value;return <td key={size}><button type="button" className={'picker-cell'+(on?' is-selected':'')} aria-label={(color||model)+' '+(size||'без размера')} aria-pressed={on} onClick={()=>pick(item)}>{on&&<Icon name="check" size={14}/>}</button></td>})}</tr>)}</tbody></table></div>}</div>})}</section>})}{!groups.length&&<p className="picker-empty">Ничего не найдено</p>}</div></div>,document.body)}</div>
}
