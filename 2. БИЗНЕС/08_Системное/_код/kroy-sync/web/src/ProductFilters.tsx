import './Products.css';
import React,{useId,useState} from 'react';
import {Icon,Select} from './UI';
import {compareSizes,productCategory,stockFigures} from './catalogData';
type Item=Record<string,any>;
export function useProductFilters(items:Item[],location:string){
 const[query,setQuery]=useState(''),[category,setCategory]=useState('Все'),[color,setColor]=useState(''),[size,setSize]=useState(''),[stock,setStock]=useState('any'),[open,setOpen]=useState(false);
 const categories=[...new Set(items.map(productCategory))].sort((a,b)=>a==='Материалы'?1:b==='Материалы'?-1:a.localeCompare(b,'ru'));
 const context=items.filter(i=>category==='Все'||productCategory(i)===category);
 const colors=[...new Set(context.map(i=>i.color).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'ru'));
 const sizes=[...new Set(context.map(i=>i.size||''))].sort(compareSizes);
 function pickCategory(c:string){setCategory(c);setColor('');setSize('')}
 function reset(){setQuery('');setCategory('Все');setColor('');setSize('');setStock('any')}
 const shown=context.filter(i=>{
  const available=stockFigures(i,location).available;
  return (!color||i.color===color)&&(!size||(size==='__none__'?!i.size:i.size===size))&&(stock==='any'||stock==='unknown'&&available===null||stock==='positive'&&available!==null&&available>0||stock==='zero'&&available===0)&&[i.model,i.name,i.color,i.size,...(i.barcodes||[])].join(' ').toLocaleLowerCase('ru').includes(query.trim().toLocaleLowerCase('ru'));
 });
 return {query,setQuery,category,pickCategory,color,setColor,size,setSize,stock,setStock,open,setOpen,categories,colors,sizes,shown,reset};
}
export function ProductFilters({filters:f,create,categoryLabel='Категории товаров',searchLabel='Поиск товара'}:{filters:ReturnType<typeof useProductFilters>;create?:()=>void;categoryLabel?:string;searchLabel?:string}){
 const id=useId();const count=Number(!!f.color)+Number(!!f.size)+Number(f.stock!=='any');
 const stockLabel={positive:'Есть доступные',zero:'Нет доступных',unknown:'Остаток не подтверждён',any:'Любой остаток'}[f.stock];
 return <div className="product-filters"><div className="product-category-row"><div className="category-tabs" role="group" aria-label={categoryLabel}>{['Все',...f.categories].map(c=><button type="button" key={c} aria-pressed={f.category===c} className={f.category===c?'active':''} onClick={()=>f.pickCategory(c)}>{c==='Все'?'Все разделы':c}</button>)}</div>{create&&<button className="secondary product-create" onClick={create}>+ Позиция</button>}</div>
 <div className="catalog-search-row"><div className="search-field"><Icon name="search" size={18}/><input aria-label={searchLabel} placeholder="Модель, цвет, размер или штрихкод" value={f.query} onChange={e=>f.setQuery(e.target.value)}/>{f.query&&<button aria-label="Очистить поиск товара" onClick={()=>f.setQuery('')}><Icon name="close" size={15}/></button>}</div><button className={'secondary filter-toggle '+(f.open||count?'active':'')} aria-expanded={f.open} aria-controls={id} onClick={()=>f.setOpen(!f.open)}><Icon name="filter" size={17}/><span>Фильтры</span>{count>0&&<span className="filter-count">{count}</span>}<Icon name="down" size={14}/></button></div>
 {f.open&&<div id={id} className="filter-panel product-filter-panel"><label>Цвет<Select label="Цвет" value={f.color} onChange={f.setColor} options={[{value:'',label:'Все цвета'},...f.colors.map(c=>({value:c,label:c}))]}/></label><label>Размер<Select label="Размер" value={f.size} onChange={f.setSize} options={[{value:'',label:'Все размеры'},...f.sizes.map(s=>({value:s||'__none__',label:s||'Без размера'}))]}/></label><label>Наличие<Select label="Наличие" value={f.stock} onChange={f.setStock} options={[{value:'any',label:'Любой остаток'},{value:'positive',label:'Есть доступные'},{value:'zero',label:'Нет доступных'},{value:'unknown',label:'Остаток не подтверждён'}]}/></label></div>}
 {count>0&&<div className="filter-chips product-filter-chips">{f.color&&<button aria-label={'Убрать фильтр: '+f.color} onClick={()=>f.setColor('')}>{f.color}<Icon name="close" size={13}/></button>}{f.size&&<button aria-label={'Убрать фильтр: '+f.size} onClick={()=>f.setSize('')}>{f.size==='__none__'?'Без размера':f.size}<Icon name="close" size={13}/></button>}{f.stock!=='any'&&<button aria-label={'Убрать фильтр: '+stockLabel} onClick={()=>f.setStock('any')}>{stockLabel}<Icon name="close" size={13}/></button>}<button className="text-button" onClick={()=>{f.setColor('');f.setSize('');f.setStock('any')}}>Сбросить фильтры</button></div>}
 </div>
}
export function StockSummary({available,total,className=''}:{available:number|null;total:number|null;className?:string}){
 const fmt=(n:number|null)=>n===null?'—':n.toLocaleString('ru-RU');
 return <span className={'stock-summary '+className}><span title="Доступно для продажи: без резерва и заморозки"><small>Доступно</small><strong className={available!==null&&available>0?'stock-positive':''}>{fmt(available)}</strong></span><span title="Доступно + резерв + заморозка"><small>Остаток</small><strong>{fmt(total)}</strong></span></span>
}
