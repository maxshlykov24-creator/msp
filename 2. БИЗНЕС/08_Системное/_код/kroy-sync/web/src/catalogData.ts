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
