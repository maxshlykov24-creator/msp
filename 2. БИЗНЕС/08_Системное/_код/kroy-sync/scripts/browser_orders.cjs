// Read-only acceptance against a populated snapshot or the deployed site.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.KROY_UI_URL || 'http://127.0.0.1:18768/yuji/';
const keyFile = process.env.KROY_UI_KEY_FILE;
const token = keyFile ? fs.readFileSync(keyFile, 'utf8').trim() : 'orders-preview';

(async () => {
 const browser = await chromium.launch();
 try {
  const p = await browser.newPage({viewport:{width:1440,height:950},locale:'ru-RU',reducedMotion:'reduce'});
  const errors = [], lists = [];
  p.on('pageerror', e => errors.push(e.message));
  p.on('dialog', d => { errors.push('Native dialog'); return d.dismiss() });
  await p.route('**/api/**', route => {
   const req=route.request(), u=new URL(req.url());
   if (u.origin!==new URL(base).origin || req.method()!=='GET') {
    errors.push('Unexpected API call '+req.method()+' '+u.pathname);
    return route.abort();
   }
   if (u.pathname.endsWith('/orders')) lists.push(u.search);
   return route.continue({headers:{...req.headers(),authorization:'Bearer '+token}});
  });
  const btn = name => p.getByRole('button',{name,exact:true});
  const rows = p.locator('.orders-table tbody tr');
  const counts = p.locator('.orders-status-tabs .order-count');
  const waitRows = match => p.waitForFunction(match => {
   const rows=[...document.querySelectorAll('.orders-table tbody tr')];
   return rows.length>0 && rows.every(r => match.every(text=>r.textContent.includes(text)));
  },match);
  await p.goto(base);
  await p.getByRole('navigation').getByRole('button',{name:'Заказы',exact:true}).click();
  await rows.first().waitFor();
  await btn('Ozon').click();
  await waitRows(['Ozon']);
  await btn('FBS').click();
  await waitRows(['Ozon','FBS']);
  const scopeCount = await counts.first().textContent();
  const stages=p.getByRole('group',{name:'Этап заказа',exact:true});
  await stages.getByRole('button',{name:/^Доставлены/}).click();
  await waitRows(['Ozon','FBS','Доставлен']);
  assert.equal(await counts.first().textContent(),scopeCount);
  const reads=lists.length;
  await stages.getByRole('button',{name:/^Все /}).click();
  await waitRows(['Ozon','FBS']);
  assert.equal(lists.length,reads,'Repeated filter should reuse its fresh page');
  const id=await rows.first().locator('.order-mark-id strong').innerText();
  await p.getByLabel('Найти заказ').fill(id);
  await waitRows([id]);
  await rows.first().locator('td.order-qty').click();
  const dialog=p.getByRole('dialog',{name:'Заказ '+id,exact:true});
  await dialog.waitFor();
  assert(await dialog.locator('.order-detail-lines .order-product').count());
  await p.keyboard.press('Escape');
  await btn('Сбросить фильтры').click();
  await rows.nth(1).waitFor();
  await btn('Далее →').click();
  await p.getByText(/^2 из /).waitFor();
  await btn('← Назад').click();
  await p.getByText(/^1 из /).waitFor();
  const capture=async (state,theme)=>{
   for(const width of [320,390,768,1440]){
    await p.setViewportSize({width,height:950});
    if(state==='list')await (width>1100?rows.first():p.locator('.order-card').first()).waitFor();
    assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),state+' overflow at '+width);
    if(process.env.KROY_UI_SHOTS==='1')await p.screenshot({path:'.runtime/orders-'+(keyFile?'live':'local')+'-'+state+'-'+theme+'-'+width+'.png'});
   }
  };
  for(const theme of ['light','dark']){
   await p.locator('.profile-trigger').click();
   await btn(theme==='light'?'Светлая тема':'Тёмная тема').click();
   await p.keyboard.press('Escape');
   await capture('list',theme);
   await rows.first().locator('td.order-qty').click();
   await p.getByRole('dialog').waitFor();
   await capture('document',theme);
   await p.keyboard.press('Escape');
   await btn('Подробности синхронизации заказов').click();
   await p.getByRole('dialog',{name:'Синхронизация заказов',exact:true}).waitFor();
   await capture('sync',theme);
   await p.keyboard.press('Escape');
  }
  // The date fields and status strip must also fit when the page has no orders.
  await p.getByLabel('Найти заказ').fill('not-found-order-000');
  await p.getByText('По этим условиям заказы не найдены',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);
  console.log('Order filters, cache, search, detail, pagination and both themes at 320/390/768/1440 px passed.');
 } finally { await browser.close() }
})().catch(e=>{console.error(e);process.exitCode=1});
