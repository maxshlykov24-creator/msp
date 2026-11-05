import React,{useEffect,useState} from 'react';
import {Icon} from './UI';
import {photoCandidates} from './catalogData';
export function ItemPhoto({item,className='cut-photo'}:{item:Record<string,any>;className?:string}){
 const candidates=photoCandidates(item),signature=candidates.join('|');
 const[index,setIndex]=useState(0);useEffect(()=>{setIndex(0)},[signature]);
 const src=candidates[index];
 return <span className={className}><Icon name="catalog" size={18}/>{src&&<img key={src} src={src} alt="" loading="lazy" decoding="async" onError={()=>setIndex(i=>i+1)}/>}</span>
}
