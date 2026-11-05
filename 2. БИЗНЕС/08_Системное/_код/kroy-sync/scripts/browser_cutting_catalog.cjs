// Local real-catalog snapshot only; no accounting writes.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs');
(async()=>{const b=await chromium.launch({headless:true});try{
 const p=await b.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),errors=[];p.on('pageerror',e=>errors.push(e.message));await p.route('**/api/commands',r=>{errors.push('Unexpected accounting command');return r.abort()});
 // Simulate a browser without access to the marketplace image host.
 await p.route('https://**.wbbasket.ru/**',r=>r.abort());
 await p.goto(process.env.KROY_UI_URL||'http://127.0.0.1:18766/yuji/');await p.getByLabel('Личный ключ доступа').fill(process.env.KROY_UI_KEY_FILE?fs.readFileSync(process.env.KROY_UI_KEY_FILE,'utf8').trim():'cut-local-test');await p.getByRole('button',{name:'Войти',exact:true}).click();await p.locator('.model-trigger').first().click();
 const catalogSizes=await p.locator('.model-details').first().locator('.size').allTextContents();assert(catalogSizes.length>0);for(let i=0;i<catalogSizes.length;i+=2)assert.deepEqual(catalogSizes.slice(i,i+2),['S','M']);
 await p.getByRole('navigation').getByRole('button',{name:'Крой',exact:true}).click();await p.getByRole('button',{name:'+ Задание',exact:true}).click();
 assert.equal(await p.getByRole('heading',{name:'Основное',exact:true}).count(),0);assert.equal(await p.getByLabel('Название задания',{exact:true}).count(),0);assert.equal(await p.getByRole('button',{name:'Склад',exact:true}).count(),0);
 const comment=p.getByLabel('Комментарий к заданию',{exact:true});await comment.fill('Проверить оттенок перед раскроем.');
 const cats=p.getByRole('group',{name:'Категории товаров для кроя',exact:true});await cats.getByRole('button',{name:'Костюмы',exact:true}).click();
 assert(await p.locator('.cut-picker summary strong').evaluateAll(els=>els.every(e=>e.textContent.startsWith('Костюм'))));await p.locator('.cut-picker summary').first().click();
 const cutSizes=await p.locator('.cut-picker details').first().locator('.cut-size').allTextContents();assert(cutSizes.length>0);for(let i=0;i<cutSizes.length;i+=2)assert.deepEqual(cutSizes.slice(i,i+2),['S','M']);
 await p.getByRole('button',{name:'Выбрать все варианты',exact:true}).click();await cats.getByRole('button',{name:'Лонгсливы',exact:true}).click();await p.getByText('Выбрано '+cutSizes.length,{exact:true}).waitFor();
 await cats.getByRole('button',{name:'Все разделы',exact:true}).click();assert.equal(await p.locator('.cut-picker input:checked').count(),cutSizes.length);assert.equal(await comment.inputValue(),'Проверить оттенок перед раскроем.');
 const img=p.locator('.cut-picker summary img').first();await img.evaluate(i=>i.decode());assert(await img.evaluate(i=>i.naturalWidth>0&&new URL(i.src).pathname.includes('/api/photos/')));
 for(const theme of ['light','dark']){await p.setViewportSize({width:1440,height:1000});await p.getByRole('button',{name:theme==='light'?'Светлая тема':'Тёмная тема',exact:true}).click();for(const width of [390,768,1440]){await p.setViewportSize({width,height:1000});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Overflow '+width);await p.screenshot({path:`.runtime/cut-catalog-${theme}-${width}.png`,fullPage:true,animations:'disabled'})}}
 assert.deepEqual(errors,[]);console.log(JSON.stringify({catalog_size_order:true,cut_size_order:true,categories:true,selection_retained:true,comment_retained:true,cached_photo_with_marketplace_blocked:true,themes:2,widths:[390,768,1440],errors}));
}finally{await b.close()}})().catch(e=>{console.error(e);process.exitCode=1});
