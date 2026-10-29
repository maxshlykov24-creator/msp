import React,{useEffect,useId,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';

const paths:Record<string,React.ReactNode>={
 search:<><circle cx="11" cy="11" r="7"/><path d="m16 16 4 4"/></>,
 chevron:<path d="m9 5 7 7-7 7"/>,down:<path d="m6 9 6 6 6-6"/>,
 catalog:<><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
 tasks:<><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="m8 8 13 13M8 16 21 3M14 14l-3-3"/></>,
 freeze:<><path d="M12 2v20M3.3 7l17.4 10M3.3 17 20.7 7M8 4l4 4 4-4M8 20l4-4 4 4"/></>,
 count:<><path d="M8 3H4v4m12-4h4v4M4 17v4h4m12-4v4h-4M3 12h18M8 8v8m4-8v8m4-8v8"/></>,
 orders:<><path d="m3 7 9-4 9 4v10l-9 4-9-4ZM3 7l9 4 9-4M12 11v10"/></>,
 planning:<><path d="M4 3v17h17M8 14l4-5 4 3 5-7"/></>,
 finance:<><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M16 10h5v5h-5Z"/></>,
 journal:<><path d="M5 5h14M5 12h14M5 19h14"/></>,
 attention:<><circle cx="12" cy="12" r="9"/><path d="M12 8v5m0 3h.01"/></>,
 staff:<><circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5"/></>,
 mappings:<><path d="m10 13 4-4m-7 6-2 2a4 4 0 0 0 6 6l3-3M14 9l2-2a4 4 0 0 1 6 6l-3 3" transform="translate(-2 -3)"/></>,
 sun:<><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></>,
 moon:<path d="M21 13A9 9 0 0 1 11 3a9 9 0 1 0 10 10Z"/>,
 filter:<><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2"/><circle cx="15" cy="17" r="2"/></>,
 logout:<><path d="M9 4H4v16h5m5-4 4-4-4-4m-6 4h13"/></>,
 check:<path d="m5 12 4 4L19 6"/>,close:<path d="m6 6 12 12M6 18 18 6"/>,
 rail:<><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 3v18"/></>,
 refresh:<><path d="M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 13-3l2 2M4 17l2 2a8 8 0 0 0 13-3"/></>,
};
export function Icon({name,size=20}:{name:string;size?:number}){return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]||paths.catalog}</svg>}
export function useTheme(){const[theme,setTheme]=useState(()=>{try{return localStorage.getItem('yuji.theme')==='dark'?'dark':'light'}catch{return 'light'}});useLayoutEffect(()=>{document.documentElement.dataset.theme=theme;document.querySelector('meta[name="theme-color"]')?.setAttribute('content',getComputedStyle(document.documentElement).getPropertyValue('--bg').trim());try{localStorage.setItem('yuji.theme',theme)}catch{}},[theme]);return {theme,setTheme}}
export function ThemeToggle({theme,setTheme}:{theme:string;setTheme:(value:string)=>void}){return <div className="theme-pill" role="group" aria-label="Тема">{['light','dark'].map(t=><button key={t} aria-label={t==='light'?'Светлая тема':'Тёмная тема'} aria-pressed={theme===t} className={theme===t?'is-on':''} onClick={()=>setTheme(t)}><Icon name={t==='light'?'sun':'moon'} size={17}/></button>)}</div>}

export function Select({value,onChange,options,label}:{value:string;onChange:(value:string)=>void;options:{value:string;label:string}[];label:string}){
 const[open,setOpen]=useState(false),[query,setQuery]=useState(''),[active,setActive]=useState(0),[box,setBox]=useState({top:0,left:0,width:240,height:360});
 const trigger=useRef<HTMLButtonElement>(null),menu=useRef<HTMLDivElement>(null),input=useRef<HTMLInputElement>(null),id=useId();
 const filtered=options.filter(o=>o.label.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')));
 function close(){setOpen(false);setQuery('')}
 function pick(v:string){onChange(v);close();trigger.current?.focus()}
 useLayoutEffect(()=>{if(!open)return;function place(){const r=trigger.current!.getBoundingClientRect(),width=Math.min(Math.max(r.width,260),innerWidth-24),below=innerHeight-r.bottom-10,up=below<260&&r.top>below,height=Math.max(80,Math.min(360,up?r.top-20:below));setBox({left:Math.max(12,Math.min(r.left,innerWidth-width-12)),top:up?r.top-height-8:r.bottom+8,width,height})}place();window.addEventListener('resize',place);window.addEventListener('scroll',place,true);return()=>{window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true)}},[open]);
 useEffect(()=>{if(!open)return;setActive(Math.max(0,filtered.findIndex(o=>o.value===value)));input.current?.focus();function outside(e:PointerEvent){if(!trigger.current?.contains(e.target as Node)&&!menu.current?.contains(e.target as Node))close()}document.addEventListener('pointerdown',outside,true);return()=>document.removeEventListener('pointerdown',outside,true)},[open]);
 useEffect(()=>{if(open)menu.current?.querySelector('[data-active="true"]')?.scrollIntoView({block:'nearest'})},[active,open]);
 function keydown(e:React.KeyboardEvent){if(e.key==='Escape'){e.preventDefault();close();trigger.current?.focus()}if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();setActive(n=>e.key==='Home'?0:e.key==='End'?filtered.length-1:Math.max(0,Math.min(filtered.length-1,n+(e.key==='ArrowDown'?1:-1))))}if(e.key==='Enter'){e.preventDefault();if(filtered[active])pick(filtered[active].value)}if(e.key==='Tab')close()}
 return <div className="select"><button type="button" ref={trigger} className="select-trigger" aria-label={label} aria-haspopup="listbox" aria-expanded={open} aria-controls={id} onClick={()=>open?close():setOpen(true)} onKeyDown={e=>{if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();setOpen(true)}}}><span>{options.find(o=>o.value===value)?.label||label}</span><Icon name="down" size={16}/></button>{open&&createPortal(<div className="select-menu" ref={menu} style={{position:'fixed',top:box.top,left:box.left,width:box.width,maxHeight:box.height}} onKeyDown={keydown}><div className="select-search"><Icon name="search" size={16}/><input ref={input} aria-label={'Найти: '+label} placeholder="Найти…" value={query} onChange={e=>{setQuery(e.target.value);setActive(0)}} role="combobox" aria-expanded="true" aria-controls={id} aria-activedescendant={filtered[active]?id+'-'+active:undefined}/></div><div id={id} role="listbox" aria-label={label} className="select-options">{filtered.map((o,n)=><button id={id+'-'+n} key={o.value} type="button" role="option" aria-selected={o.value===value} data-active={n===active} onMouseEnter={()=>setActive(n)} onClick={()=>pick(o.value)}><span>{o.label}</span>{o.value===value&&<Icon name="check" size={16}/>}</button>)}{!filtered.length&&<p className="select-empty">Ничего не найдено</p>}</div></div>,document.body)}</div>
}

export function ProfileMenu({name,role,links,nav,logout,theme,setTheme}:{name:string;role:string;theme:string;setTheme:(v:string)=>void;links:{id:string;label:string}[];nav:(id:string)=>void;logout:()=>void}){
 const[open,setOpen]=useState(false),ref=useRef<HTMLDivElement>(null),btn=useRef<HTMLButtonElement>(null),[pos,setPos]=useState({left:12,bottom:80});
 useEffect(()=>{if(!open)return;const place=()=>{const r=btn.current!.getBoundingClientRect();setPos({left:Math.max(12,Math.min(r.left,innerWidth-284)),bottom:innerHeight-r.top+10})};place();const outside=(e:PointerEvent)=>{if(!ref.current?.contains(e.target as Node)&&!btn.current?.contains(e.target as Node))setOpen(false)};const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){setOpen(false);btn.current?.focus()}};document.addEventListener('pointerdown',outside,true);document.addEventListener('keydown',key);window.addEventListener('resize',place);return()=>{document.removeEventListener('pointerdown',outside,true);document.removeEventListener('keydown',key);window.removeEventListener('resize',place)}},[open]);
 return <><button ref={btn} className="profile-trigger" aria-label={'Профиль: '+name} aria-expanded={open} onClick={()=>setOpen(!open)}><span className="avatar">{name.slice(0,1)}</span><span className="profile-name"><b>{name}</b><small>{role}</small></span><Icon name="down" size={16}/></button>{open&&createPortal(<div ref={ref} className="profile-menu" style={{left:pos.left,bottom:pos.bottom}} role="dialog" aria-label="Меню профиля"><div className="profile-caption"><b>{name}</b><small>{role} · YUJI.CLO</small><ThemeToggle theme={theme} setTheme={setTheme}/></div>{links.map(l=><button key={l.id} onClick={()=>{nav(l.id);setOpen(false)}}><Icon name={l.id} size={18}/>{l.label}</button>)}<button className="logout" onClick={()=>{setOpen(false);logout()}}><Icon name="logout" size={18}/>Выйти</button></div>,document.body)}</>
}
