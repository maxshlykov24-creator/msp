// One ordering for every product selector, output table and cutting ratio.
type Variant={model?:string;name?:string;color?:string;size?:string;[key:string]:any};
function key(value:string){
 const raw=value.trim().toUpperCase().replace(/\s+/g,'');
 if(!raw)return [4,0];
 if(['OS','ONESIZE','ONE-SIZE','УНИВЕРСАЛЬНЫЙ','БЕЗРАЗМЕРА'].includes(raw))return [3,0];
 const first=raw.split(/[\/–—-]/)[0];
 const expanded=first.replace(/^([2-9])XL$/,(_,n)=>'X'.repeat(Number(n))+'L').replace(/^([2-9])XS$/,(_,n)=>'X'.repeat(Number(n))+'S');
 const small=expanded.match(/^(X*)S$/);if(small)return [0,-small[1].length];
 if(expanded==='M')return [0,1];
 const large=expanded.match(/^(X*)L$/);if(large)return [0,2+large[1].length];
 if(/^\d+(?:[.,]\d+)?$/.test(first))return [1,Number(first.replace(',','.'))];
 return [2,0];
}
export function compareSizes(a:string='',b:string=''){const x=key(a),y=key(b);return x[0]-y[0]||x[1]-y[1]||a.localeCompare(b,'ru',{numeric:true})}
export function compareVariants(a:Variant,b:Variant){return String(a.model||a.name||'').localeCompare(String(b.model||b.name||''),'ru',{numeric:true})||String(a.color||'').localeCompare(String(b.color||''),'ru')||compareSizes(a.size,b.size)||String(a.name||'').localeCompare(String(b.name||''),'ru',{numeric:true})}
export function productCategory(i:Variant){
 if(i.kind==='material')return 'Материалы';
 if(i.category&&!['YUJI','YUJI.CLO'].includes(i.category))return i.category;
 const s=String(i.model||i.name).toLowerCase();
 for(const [prefix,label] of [['лонгслив','Лонгсливы'],['футболк','Футболки'],['костюм','Костюмы'],['штан','Брюки'],['брюк','Брюки'],['майк','Майки'],['топ','Топы'],['плать','Платья'],['рубаш','Рубашки']])if(s.startsWith(prefix))return label;
 return 'Другая одежда';
}
export function photoCandidates(i:Variant){return [...new Set([i.photo_preview,i.photo].filter(Boolean))] as string[]}

export function stockFigures(item:Variant,location:string){
 const balances=(item.balances||[]).filter((b:any)=>b.location===location);
 const amount=(key:string):number|null=>balances.length&&balances.every((b:any)=>b.known&&b[key]!=null&&Number.isFinite(Number(b[key])))?balances.reduce((sum:number,b:any)=>sum+Number(b[key]),0):null;
 // free already excludes reservations. Do not subtract reserved a second time.
 const available=amount('free'),reserved=amount('reserved'),frozen=amount('frozen');
 const total=available===null||reserved===null||frozen===null?null:available+reserved+frozen;
 return {available,total,reserved,frozen};
}
export function modelStock(items:Variant[],location:string){
 const values=items.map(i=>stockFigures(i,location));
 const sum=(key:'available'|'frozen'):number|null=>values.length&&new Set(items.map(i=>i.unit)).size===1&&values.every(v=>v[key]!==null)?values.reduce((n,v)=>n+v[key]!,0):null;
 return {available:sum('available'),frozen:sum('frozen')};
}
export function words(n:number,one:string,few:string,many:string){
 const abs=Math.abs(n)%100,last=abs%10;
 const form=abs>10&&abs<20?many:last===1?one:last>1&&last<5?few:many;
 return `${n.toLocaleString('ru-RU')} ${form}`;
}
