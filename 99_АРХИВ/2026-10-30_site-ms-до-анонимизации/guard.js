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
