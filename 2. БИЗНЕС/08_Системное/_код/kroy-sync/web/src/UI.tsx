import React,{useEffect,useId,useLayoutEffect,useRef,useState} from 'react';
import {createPortal,flushSync} from 'react-dom';

const paths:Record<string,React.ReactNode>={
 plus:<path d="M12 5v14M5 12h14"/>,edit:<><path d="m4 16 11-11 4 4L8 20l-5 1zM13 7l4 4"/></>,
 search:<><circle cx="11" cy="11" r="7"/><path d="m16 16 4 4"/></>,
 chevron:<path d="m9 5 7 7-7 7"/>,down:<path d="m6 9 6 6 6-6"/>,
 calendar:<><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 10h18m-13 5h2m4 0h2"/></>,
 catalog:<><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
 tasks:<><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="m8 8 13 13M8 16 21 3M14 14l-3-3"/></>,
 freeze:<><path d="M12 2v20M3.3 7l17.4 10M3.3 17 20.7 7M8 4l4 4 4-4M8 20l4-4 4 4"/></>,
 camera:<><path d="M4 7h3l2-3h6l2 3h3v12H4z"/><circle cx="12" cy="13" r="3"/></>,
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
function revealTheme(next:string,current:string,setTheme:(value:string)=>void,origin:HTMLElement){
 if(next===current)return;
 const apply=()=>{document.documentElement.dataset.theme=next;setTheme(next)};
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 const start=(document as Document&{startViewTransition?:(update:()=>void)=>{ready:Promise<void>}}).startViewTransition?.bind(document);
 if(reduced||!start){apply();return}
 const box=origin.getBoundingClientRect(),x=box.left+box.width/2,y=box.top+box.height/2,radius=Math.hypot(Math.max(x,innerWidth-x),Math.max(y,innerHeight-y));
 try{
  const transition=start(()=>{flushSync(apply)});
  transition.ready.then(()=>{document.documentElement.animate({clipPath:[`circle(0px at ${x}px ${y}px)`,`circle(${radius}px at ${x}px ${y}px)`]},{duration:680,easing:'cubic-bezier(.22,1,.36,1)',pseudoElement:'::view-transition-new(root)'})}).catch(()=>{});
 }catch{apply()}
}
export function ThemeToggle({theme,setTheme}:{theme:string;setTheme:(value:string)=>void}){return <div className="theme-pill" role="group" aria-label="Тема">{['light','dark'].map(t=><button key={t} aria-label={t==='light'?'Светлая тема':'Тёмная тема'} aria-pressed={theme===t} className={theme===t?'is-on':''} onClick={e=>revealTheme(t,theme,setTheme,e.currentTarget)}><Icon name={t==='light'?'sun':'moon'} size={17}/></button>)}</div>}

type PopupSize = { width: number; height: number; stretch?: boolean; align?: 'start' | 'end' };

export function useAnchoredPopup(
  open: boolean,
  trigger: React.RefObject<HTMLElement | null>,
  popup: React.RefObject<HTMLElement | null>,
  { width, height, stretch, align = 'start' }: PopupSize,
  onDismiss: () => void,
) {
  const [position, setPosition] = useState<React.CSSProperties>({ position: 'fixed', top: 0, left: 0 });
  useLayoutEffect(() => {
    if (!open) return;
    const viewport = window.visualViewport;
    function place(event?: Event) {
      if (event?.target instanceof Node && popup.current?.contains(event.target)) return;
      const anchor = trigger.current?.getBoundingClientRect();
      if (!anchor) return;
      const margin = 12, gap = 6;
      const left = (viewport?.offsetLeft ?? 0) + margin;
      const right = left + (viewport?.width ?? innerWidth) - 2 * margin;
      const top = (viewport?.offsetTop ?? 0) + margin;
      const bottom = top + (viewport?.height ?? innerHeight) - 2 * margin;
      const popupWidth = Math.min(stretch ? Math.max(width, anchor.width) : width, right - left);
      const desiredLeft = align === 'end' ? anchor.right - popupWidth : anchor.left;
      const belowTop = Math.max(top, Math.min(bottom, anchor.bottom + gap));
      const aboveBottom = Math.max(top, Math.min(bottom, anchor.top - gap));
      const below = bottom - belowTop, above = aboveBottom - top;
      const upwards = below < height && above > below;
      const room = upwards ? above : below;
      const horizontal = { position: 'fixed' as const, left: Math.max(left, Math.min(desiredLeft, right - popupWidth)), width: popupWidth };
      if (room < Math.min(height, 210)) {
        const popupHeight = Math.min(height, bottom - top);
        setPosition({ ...horizontal, top: Math.min(belowTop, bottom - popupHeight), maxHeight: popupHeight });
      } else {
        const side = upwards ? { bottom: innerHeight - aboveBottom } : { top: belowTop };
        setPosition({ ...horizontal, ...side, maxHeight: Math.min(height, room) });
      }
    }
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    viewport?.addEventListener('resize', place);
    viewport?.addEventListener('scroll', place);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      viewport?.removeEventListener('resize', place);
      viewport?.removeEventListener('scroll', place);
    };
  }, [open, trigger, popup, width, height, stretch, align]);
  useEffect(() => {
    if (!open) return;
    function outside(event: PointerEvent) {
      if (!(event.target instanceof Node)) return;
      if (!trigger.current?.contains(event.target) && !popup.current?.contains(event.target)) onDismiss();
    }
    document.addEventListener('pointerdown', outside, true);
    return () => document.removeEventListener('pointerdown', outside, true);
  }, [open, trigger, popup, onDismiss]);
  return position;
}

export function isLastPopupTab(event: React.KeyboardEvent, popup: HTMLElement) {
  if (event.key !== 'Tab') return false;
  const stops = Array.from(popup.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)'))
    .filter(element => element.tabIndex >= 0 && element.getClientRects().length > 0);
  return event.target === (event.shiftKey ? stops[0] : stops.at(-1));
}

export function scrollWithin(container: HTMLElement, option: HTMLElement) {
  const box = container.getBoundingClientRect(), row = option.getBoundingClientRect();
  const margin = 4;
  if (row.top < box.top + margin) container.scrollTop += row.top - box.top - margin;
  else if (row.bottom > box.bottom - margin) container.scrollTop += row.bottom - box.bottom + margin;
}

type SelectOption = { value: string; label: string; disabled?: boolean };
type SelectProps = {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  label: string;
  disabled?: boolean;
  required?: boolean;
  invalid?: boolean;
  searchable?: boolean;
};

const matchesOption = (option: SelectOption, text: string) =>
  option.label.toLocaleLowerCase('ru').includes(text.trim().toLocaleLowerCase('ru'));

export function Select({ value, onChange, options, label, disabled, required, invalid, searchable = true }: SelectProps) {
  const [open, setOpen] = useState(false), [query, setQuery] = useState(''), [active, setActive] = useState(-1);
  const trigger = useRef<HTMLButtonElement>(null), menu = useRef<HTMLDivElement>(null), input = useRef<HTMLInputElement>(null);
  const focusSearch = useRef(false), focusOption = useRef(false), id = useId();
  const filtered = options.filter(option => matchesOption(option, query));
  const position = useAnchoredPopup(open, trigger, menu, {
    width: 244, height: Math.min(352, (searchable ? 57 : 14) + 43 * Math.max(1, filtered.length)), stretch: true,
  }, () => setOpen(false));

  function show(keyboard = false) {
    const selected = options.findIndex(option => option.value === value && !option.disabled);
    setQuery('');
    setActive(selected >= 0 ? selected : options.findIndex(option => !option.disabled));
    focusSearch.current = searchable && (keyboard || matchMedia('(pointer:fine)').matches);
    focusOption.current = !focusSearch.current;
    setOpen(true);
  }
  function close(restoreFocus = false) {
    if (restoreFocus) trigger.current?.focus({ preventScroll: true });
    setOpen(false);
  }
  function pick(option: SelectOption) {
    if (disabled || trigger.current?.matches(':disabled') || option.disabled) return;
    onChange(option.value);
    close(true);
  }
  useLayoutEffect(() => {
    if (open && trigger.current?.matches(':disabled')) close();
  });
  useLayoutEffect(() => {
    if (open && focusSearch.current) input.current?.focus({ preventScroll: true });
  }, [open]);
  useLayoutEffect(() => {
    if (!open || !menu.current) return;
    const options = menu.current.querySelector<HTMLElement>('.select-options');
    const option = options?.querySelector<HTMLElement>('[data-active="true"]');
    if (focusOption.current) {
      (option ?? options)?.focus({ preventScroll: true });
      focusOption.current = false;
    }
    if (options && option) scrollWithin(options, option);
  }, [open, active, query, position.maxHeight]);

  function keydown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close(true);
    } else if (e.key === 'Tab') {
      if (!searchable || isLastPopupTab(e, e.currentTarget) || (e.target !== input.current && !e.shiftKey)) close(true);
    } else if (e.key === 'Enter' && e.target === input.current) {
      e.preventDefault();
      if (filtered[active]) pick(filtered[active]);
    } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
      e.preventDefault();
      const enabled = filtered.flatMap((option, index) => option.disabled ? [] : [index]);
      const next = e.key === 'Home' ? enabled[0] : e.key === 'End' ? enabled.at(-1)
        : e.key === 'ArrowDown' ? enabled.find(index => index > active) : enabled.filter(index => index < active).at(-1);
      if (next === undefined || next === active) return;
      focusOption.current = e.target !== input.current;
      setActive(next);
    }
  }

  return <div className="select">
    <button type="button" ref={trigger} disabled={disabled} className="select-trigger"
      aria-label={label} aria-describedby={id + '-value'} aria-required={required || undefined} aria-invalid={invalid || undefined}
      aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? id : undefined}
      onClick={e => open ? close() : show(e.detail === 0)}
      onKeyDown={e => { if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); show(true); } }}>
      <span id={id + '-value'}>{options.find(option => option.value === value)?.label || label}</span><Icon name="down" size={16}/>
    </button>
    {open && createPortal(<div className="select-menu picker-popup" ref={menu} style={position} onKeyDown={keydown}>
      {searchable && <div className="select-search"><Icon name="search" size={16}/>
        <input ref={input} aria-label={'Найти: ' + label} placeholder="Найти…" value={query}
          onChange={e => { const text = e.target.value; setQuery(text); setActive(options.filter(option => matchesOption(option, text)).findIndex(option => !option.disabled)); }}
          role="combobox" aria-expanded="true" aria-controls={id} aria-activedescendant={filtered[active] ? id + '-' + active : undefined}/>
      </div>}
      <div id={id} role="listbox" aria-label={label} className="select-options" tabIndex={-1}>
        {filtered.map((option, index) => <button id={id + '-' + index} key={option.value} type="button" role="option" tabIndex={-1}
          disabled={option.disabled} aria-selected={option.value === value} data-active={index === active} onClick={() => pick(option)}>
          <span>{option.label}</span>{option.value === value && <Icon name="check" size={16}/>}
        </button>)}
        {!filtered.length && <p className="select-empty">Ничего не найдено</p>}
      </div>
    </div>, document.body)}
  </div>;
}

// Document fields keep the option values and required constraints on their form.
export function Choice({ children, value, defaultValue = '', onChange, label, disabled, required, name, id }: {
  children: React.ReactNode; value?: string; defaultValue?: string; onChange: (event: { target: { value: string } }) => void;
  label: string; disabled?: boolean; required?: boolean; name?: string; id?: string; 'aria-label'?: string;
}) {
  const [local, setLocal] = useState(defaultValue), [invalid, setInvalid] = useState(false);
  const options = React.Children.toArray(children).filter(React.isValidElement).map(child => {
    const { value, children, disabled } = (child as React.ReactElement<{ value?: string; children: React.ReactNode; disabled?: boolean }>).props;
    const text = React.Children.toArray(children).join('');
    return { value: String(value ?? text), label: text, disabled };
  });
  const selected = value ?? local;
  return <div className="choice">
    <Select label={label} value={selected} options={options} disabled={disabled} required={required} invalid={invalid && !selected}
      searchable={options.length > 7} onChange={next => { setInvalid(false); setLocal(next); onChange({ target: { value: next } }); }}/>
    <select hidden id={id} name={name} className="select-validation" tabIndex={-1} aria-hidden="true"
      disabled={disabled} required={required} value={selected} onChange={() => {}}
      onInvalid={e => { e.preventDefault(); setInvalid(true); e.currentTarget.parentElement?.querySelector<HTMLButtonElement>('.select-trigger')?.focus(); }}>
      {children}
    </select>
    {invalid && !selected && <span className="picker-error" role="alert">Выбери значение</span>}
  </div>;
}

export function ActionMenu({label='Действия',children}:{label?:string;children:React.ReactNode}){
 const [open, setOpen] = useState(false), button = useRef<HTMLButtonElement>(null), menu = useRef<HTMLDivElement>(null), id = useId();
 const position = useAnchoredPopup(open, button, menu, { width: 272, height: 240, align: 'end' }, () => setOpen(false));
 useLayoutEffect(() => { if (open) menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus({ preventScroll: true }); }, [open]);
 function close() { button.current?.focus({ preventScroll: true }); setOpen(false); }
 return <>
   <button ref={button} type="button" className="secondary action-menu-trigger" aria-expanded={open} aria-haspopup="menu"
     aria-controls={open ? id : undefined} onClick={() => setOpen(!open)}>{label}<Icon name="down" size={15}/></button>
   {open && createPortal(<div id={id} role="menu" aria-label={label} ref={menu} className="action-menu picker-popup" style={position}
     onClick={e => { if ((e.target as Element).closest('button')) close(); }}
     onKeyDown={e => {
       if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return; }
       if (e.key === 'Tab') { close(); return; }
       if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
       const buttons = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
       if (!buttons.length) return;
       const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
       e.preventDefault();
       const next = e.key === 'Home' ? 0 : e.key === 'End' ? buttons.length - 1
         : (index + (e.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
       buttons[next].focus();
     }}>
     {React.Children.map(children, child => React.isValidElement(child)
       ? React.cloneElement(child as React.ReactElement<{ role?: string }>, { role: 'menuitem' }) : child)}
   </div>, document.body)}
 </>;
}

export function ProfileMenu({name,role,links,nav,logout,theme,setTheme}:{name:string;role:string;theme:string;setTheme:(v:string)=>void;links:{id:string;label:string}[];nav:(id:string)=>void;logout:()=>void}){
 const [open, setOpen] = useState(false), menu = useRef<HTMLDivElement>(null), button = useRef<HTMLButtonElement>(null), id = useId();
 const position = useAnchoredPopup(open, button, menu, { width: 272, height: 410 }, () => setOpen(false));
 useLayoutEffect(() => { if (open) menu.current?.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true }); }, [open]);
 return <>
   <button ref={button} type="button" className="profile-trigger" aria-label={'Профиль: ' + name} aria-expanded={open} aria-haspopup="dialog"
     aria-controls={open ? id : undefined} onClick={() => setOpen(!open)}>
     <span className="avatar">{name.slice(0, 1)}</span><span className="profile-name"><b>{name}</b><small>{role}</small></span><Icon name="down" size={16}/>
   </button>
   {open && createPortal(<div id={id} ref={menu} className="profile-menu picker-popup" style={position} role="dialog" aria-label="Меню профиля"
     onKeyDown={e => {
       if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); }
       else if (!isLastPopupTab(e, e.currentTarget)) return;
       button.current?.focus({ preventScroll: true });
       setOpen(false);
     }}>
     <div className="profile-caption"><b>{name}</b><small>{role} · YUJI.CLO</small><ThemeToggle theme={theme} setTheme={setTheme}/></div>
     {links.map(link => <button key={link.id} type="button" onClick={() => { nav(link.id); setOpen(false); }}><Icon name={link.id} size={18}/>{link.label}</button>)}
     <button type="button" className="logout" onClick={() => { setOpen(false); logout(); }}><Icon name="logout" size={18}/>Выйти</button>
   </div>, document.body)}
 </>;
}

// Shared application dialog: keyboard focus, Escape, restoration and scroll lock.
export function DialogFrame({title,close,busy=false,children,className=''}:{title:string;close:()=>void;busy?:boolean;children:React.ReactNode;className?:string}){
 const ref=useRef<HTMLElement>(null),id=useId(),onClose=useRef(close);onClose.current=close;
 useEffect(() => {
   const previous = document.activeElement as HTMLElement, overflow = document.body.style.overflow;
   const dialog = ref.current;
   document.body.style.overflow = 'hidden';
   const field = matchMedia('(pointer:fine)').matches
     ? dialog?.querySelector<HTMLElement>('[autofocus],input:not([readonly]):not(:disabled):not(.select-validation)') : null;
   (field ?? dialog?.querySelector<HTMLElement>('button:not(:disabled)'))?.focus({ preventScroll: true });
   return () => {
     document.body.style.overflow = overflow;
     if (previous?.isConnected) previous.focus({ preventScroll: true });
   };
 }, []);
 useEffect(() => {
   function key(e: KeyboardEvent) {
     if (e.defaultPrevented || Array.from(document.querySelectorAll('.app-dialog')).at(-1) !== ref.current) return;
     if (e.key === 'Escape' && !document.querySelector('.picker-popup,.photo-zoom')) {
       e.preventDefault();
       if (!busy) onClose.current();
     }
     if (e.key === 'Tab') {
       const stops = Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),[tabindex="0"]') || [])
         .filter(element => element.tabIndex >= 0 && element.getClientRects().length > 0);
       const first = stops[0], last = stops.at(-1);
       if (!first || document.activeElement === (e.shiftKey ? first : last)) {
         e.preventDefault();
         (e.shiftKey ? last : first)?.focus();
       }
     }
   }
   document.addEventListener('keydown', key);
   return () => document.removeEventListener('keydown', key);
 }, [busy]);
 return createPortal(<div className="overlay app-dialog-overlay" onMouseDown={e=>{if(e.target===e.currentTarget&&!busy)close()}}><section ref={ref} className={'modal app-dialog '+className} role="dialog" aria-modal="true" aria-labelledby={id}><header><h2 id={id}>{title}</h2><button type="button" className="dialog-close" aria-label="Закрыть окно" disabled={busy} onClick={close}><Icon name="close"/></button></header>{children}</section></div>,document.body)
}
let confirmPending=false;
export function confirmAction(title:string,message:string,accept='Продолжить'):Promise<boolean>{if(confirmPending)return Promise.resolve(false);confirmPending=true;return new Promise(resolve=>{window.dispatchEvent(new CustomEvent('yuji:confirm',{detail:{title,message,accept,resolve:(v:boolean)=>{confirmPending=false;resolve(v)}}}))})}
export function ConfirmationHost(){const[request,setRequest]=useState<any>(null);useEffect(()=>{const listener=(e:Event)=>setRequest((e as CustomEvent).detail);window.addEventListener('yuji:confirm',listener);return()=>window.removeEventListener('yuji:confirm',listener)},[]);if(!request)return null;const done=(v:boolean)=>{setRequest(null);request.resolve(v)};return <DialogFrame title={request.title} close={()=>done(false)} className="confirm-dialog"><p>{request.message}</p><div className="actions"><button className="secondary" onClick={()=>done(false)}>Отмена</button><button onClick={()=>done(true)}>{request.accept}</button></div></DialogFrame>}
