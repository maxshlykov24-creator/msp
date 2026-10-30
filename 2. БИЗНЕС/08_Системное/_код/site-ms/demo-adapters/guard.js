(function(){
  for(const key of ['localStorage','sessionStorage']){
    const values=new Map([['kassa.rail','1']]);
    Object.defineProperty(window,key,{value:{getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k),clear:()=>values.clear()},configurable:true});
  }
  function external(){
    let el=document.getElementById('preview-notice');
    if(!el){el=document.createElement('div');el.id='preview-notice';el.style.cssText='position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#171717;color:white;padding:18px 28px;border-radius:14px;z-index:999999;font:16px sans-serif;box-shadow:0 6px 30px #0004';document.body.append(el);}
    el.textContent='Внешний документ скрыт в демо. Все изменения остаются в этом окне.';
    setTimeout(()=>el.remove(),4000);
  }
  window.open=()=>{external();return null};
  document.addEventListener('click',e=>{const a=e.target.closest('a');if(a&&/^(https?:|mailto:|tel:)/.test(a.getAttribute('href')||'')){e.preventDefault();e.stopImmediatePropagation();external()}},true);
})();

(function anonymizePreview(){
  const label=location.pathname.includes('/mansband/')?'Бренд 1':'Бренд 2';
  const names=/MANSBAND|2MY(?:MOODS)?|МАРТАче/gi;
  const badge='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="32" viewBox="0 0 120 32"><rect x="1" y="1" width="118" height="30" rx="6" fill="#888" fill-opacity=".12" stroke="#888" stroke-opacity=".35"/><text x="60" y="21" text-anchor="middle" fill="#888" font-family="Arial,sans-serif" font-size="15" font-weight="600">'+label+'</text></svg>');
  function scrub(){
    document.querySelectorAll('img').forEach(img=>{
      if(img.dataset.anonymousBrand)return;
      if(/mansband|2my|logo/i.test((img.alt||'')+' '+(img.getAttribute('src')||'').slice(0,120)+' '+img.className)){
        img.dataset.anonymousBrand='1';img.src=badge;img.removeAttribute('srcset');img.alt=label;
        img.style.cssText+=';width:120px!important;min-width:96px!important;height:32px!important;object-fit:contain!important;filter:none!important';
      }
    });
    const walk=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    let node;while(node=walk.nextNode()){
      if(node.parentElement.closest('script,style'))continue;
      const text=node.nodeValue.replace(names,label);if(text!==node.nodeValue)node.nodeValue=text;
    }
    document.title=document.title.replace(names,label);
    document.querySelectorAll('[title],[aria-label]').forEach(el=>{for(const attr of ['title','aria-label']){const val=el.getAttribute(attr);if(val){const next=val.replace(names,label);if(next!==val)el.setAttribute(attr,next)}}});
  }
  function start(){scrub();new MutationObserver(scrub).observe(document.body,{childList:true,subtree:true,characterData:true});}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
