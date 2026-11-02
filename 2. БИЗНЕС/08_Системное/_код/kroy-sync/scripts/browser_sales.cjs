// Real sales readback + refresh queue. Never creates warehouse documents.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');const fs=require('fs'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const p=await browser.newPage({viewport:{width:1440,height:1100},locale:'ru-RU'}),errors=[];p.on('pageerror',e=>errors.push(e.message));
 const base=process.env.KROY_UI_URL||'https://ms-p.ru/yuji/',key=fs.readFileSync(process.env.KROY_UI_KEY_FILE||'.runtime/stage-owner.key','utf8').trim(),headers={Authorization:'Bearer '+key};
 await p.goto(base);await p.getByLabel('Личный ключ доступа').fill(key);await p.getByRole('button',{name:'Войти',exact:true}).click();await p.getByRole('heading',{name:'Поиск товара',exact:true}).waitFor();
 const catalog=await(await p.request.get(base+'api/catalog',{headers})).json();const items=catalog.items.filter(i=>i.kind==='garment'),location=catalog.items.flatMap(i=>i.balances).find(b=>!b.location.includes(':')).location;
 const response=await p.request.post(base+'api/cutting/demand',{headers,data:{location,item_ids:items.map(i=>i.id)}});assert(response.ok());const overview=await response.json();
 const sample=overview.rows.find(r=>r.platforms.wb.known&&r.platforms.ozon.known&&r.platforms.ozon.sales>0&&r.platforms.wb.sales>0);assert(sample,'No item with both marketplace sales');const item=items.find(i=>i.id===sample.item_id);
 const absent=overview.rows.find(r=>r.platforms.ozon.listed===false&&r.platforms.wb.known);assert(absent&&absent.platforms.ozon.sales===0&&absent.platforms.ozon.returns===0);
 await p.getByRole('navigation').getByRole('button',{name:'Крой',exact:true}).click();await p.getByRole('button',{name:'+ Задание',exact:true}).click();
 const model=p.locator('.cut-picker details').filter({has:p.locator('summary strong').getByText(item.model,{exact:true})});
 await model.locator('summary').click();await model.getByLabel((item.color||item.name)+' · '+(item.size||'Без размера'),{exact:true}).check();
 const calculated=p.waitForResponse(r=>r.url().endsWith('/api/cutting/demand')&&r.request().method()==='POST',{timeout:180000});
 await p.getByRole('button',{name:'Рассчитать потребность',exact:true}).click();const result=await(await calculated).json();await p.getByRole('button',{name:'Применить рекомендации',exact:true}).waitFor({timeout:180000});
 const row=result.rows[0],wb=row.platforms.wb,ozon=row.platforms.ozon;assert(wb.known&&ozon.known);assert.equal(row.net,wb.sales-wb.returns+ozon.sales-ozon.returns);
 await p.locator('.cut-editor tbody tr td').nth(1).locator('summary').click();const text=await p.locator('.cut-editor tbody').innerText();assert(text.includes('Ozon: '+ozon.sales+' − '+ozon.returns+' = '+ozon.net));
 await p.screenshot({path:'.runtime/sales-reconciled-desktop.png'});await p.setViewportSize({width:390,height:950});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await p.screenshot({path:'.runtime/sales-reconciled-mobile.png'});
 const after=await(await p.request.get(base+'api/catalog',{headers})).json();assert.deepEqual(after.items.map(i=>({id:i.id,balances:i.balances})),catalog.items.map(i=>({id:i.id,balances:i.balances})));
 const proof={period:result.parameters.start+'..'+result.parameters.end,item:item.name,wb:{sales:wb.sales,returns:wb.returns,net:wb.net},ozon:{sales:ozon.sales,returns:ozon.returns,net:ozon.net},total:row.net,known_wb:overview.rows.filter(r=>r.platforms.wb.known).length,known_ozon:overview.rows.filter(r=>r.platforms.ozon.known).length,not_listed_ozon:overview.rows.filter(r=>r.platforms.ozon.listed===false).length,refresh_completed:true,warehouse_changed:false,js_errors:errors};
 assert.deepEqual(errors,[]);fs.writeFileSync('.runtime/sales-live-proof.json',JSON.stringify(proof,null,2));console.log(JSON.stringify(proof));
}finally{await browser.close()}})().catch(e=>{console.error(e.message);process.exitCode=1});
