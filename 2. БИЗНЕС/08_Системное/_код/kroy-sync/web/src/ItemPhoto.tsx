import React,{useEffect,useState} from 'react';
import {createPortal} from 'react-dom';
import {Icon} from './UI';
import {photoCandidates} from './catalogData';
export function ItemPhoto({item,className='cut-photo'}:{item:Record<string,any>;className?:string}){
 const candidates=photoCandidates(item),signature=candidates.join('|');
 const[index,setIndex]=useState(0),[open,setOpen]=useState(false);
 useEffect(()=>{setIndex(0)},[signature]);
 const src=candidates[index],full=item.photo||src;
 useEffect(()=>{if(!open)return;const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();setOpen(false)}};document.addEventListener('keydown',key,true);return()=>document.removeEventListener('keydown',key,true)},[open]);
 return <>
  <span className={className+(src?' is-zoomable':'')} onClick={src?(e)=>{e.preventDefault();e.stopPropagation();setOpen(true)}:undefined}>{src&&<Icon name="catalog" size={18}/>}{src&&<img key={src} src={src} alt="" loading="lazy" decoding="async" onError={()=>setIndex(i=>i+1)}/>}{!src&&<Icon name="catalog" size={18}/>}</span>
  {open&&full&&createPortal(<div className="photo-zoom" role="dialog" aria-modal="true" aria-label="Фото товара" onMouseDown={e=>{e.preventDefault();e.stopPropagation();setOpen(false)}}><img src={full} alt="" onMouseDown={e=>e.stopPropagation()} onClick={e=>{e.stopPropagation();setOpen(false)}}/></div>,document.body)}
 </>
}
