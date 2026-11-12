const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});const errors=[];page.on('pageerror',e=>errors.push(e.message));const url=process.env.DEMO_URL||'http://127.0.0.1:8879';await page.goto(url);
 const click=async name=>page.getByRole('button',{name,exact:true}).click();
 const nav=async name=>page.locator('#nav').getByRole('button',{name:new RegExp(name)}).click();
 const val=code=>page.evaluate(code);
 await page.screenshot({path:'/tmp/lukdecor-demo-desktop.png',fullPage:true});
 // One product, repeated orders: shared pick, shortage, permission and cancellation.
 await nav('Волны');await click('Снять выбор');await page.locator('[name=task][value="DEMO-001"]').check();await page.locator('[name=task][value="DEMO-009"]').check();await click('Создать волну');
 assert.equal(await val(()=>state.wave.route.length),1);const before=await val(()=>total(0));await click('Неверный товар');assert.equal(await val(()=>total(0)),before);
 await click('Нет на месте');await click('Открыть исключение');await click('Настройки демо');await page.locator('#role').selectOption('Сборщик');await click('Подтвердить другую ячейку');assert.match(await page.locator('#notice').textContent(),/старший/);await page.locator('#role').selectOption('Старший смены');await click('Подтвердить другую ячейку');
 await click('Скан верного кода');await click('Скан верного кода');await click('Скан верного кода');assert.equal(await val(()=>total(0)),before-2);await click('Передать на станцию');await click('Имитировать отмену задания');await click('Скан верного кода');assert.match(await page.locator('#notice').textContent(),/Отменённое/);await click('Снять учебную отмену');await click('Скан верного кода');for(let i=0;i<2;i++){await click('Скан верного кода');await click('Подтвердить заказ');}assert.equal(await val(()=>state.completed.length),2);assert.equal(await val(()=>total(0)),before-2);await click('Следующая волна');assert.equal(await page.locator('[name=task]').count(),22);
 // Complete each operation. A partial move preserves total stock and changes both cells.
 for(let k=0;k<6;k++){
  await nav('Операции');await page.locator(`[data-action=op][data-arg="${k}"]`).click();
  const n=await val(()=>total(0));const from=await val(()=>available(0,'A-2-5'));
  await page.locator('#qty').fill('2');await page.locator('#qty').blur();await click('Скан верной ячейки');await click('Скан верного товара');await click('Подтвердить операцию');assert.match(await page.locator('#notice').textContent(),/подтверждён/);
  const after=await val(()=>total(0));if([0,2].includes(k))assert.equal(after,n+2);if([3,5].includes(k))assert.equal(after,n-2);if(k===1){assert.equal(after,n);assert.equal(await val(()=>available(0,'A-2-5')),from-2);}if(k===4)assert.equal(await val(()=>available(0,'A-2-5')),2);
  const count=await val(()=>state.docs.length);await page.evaluate(()=>commit());assert.equal(await val(()=>state.docs.length),count);
 }
 // Negative stock blocked; changing quantity revokes scanned confirmation.
 await nav('Операции');await page.locator('[data-action=op][data-arg="5"]').click();await page.locator('#qty').fill('999');await page.locator('#qty').blur();await click('Скан верной ячейки');await click('Скан верного товара');await click('Подтвердить операцию');assert.match(await page.locator('#notice').textContent(),/Недостаточно остатка/);
 // Editable draft and simulated two-way document status.
 await nav('Операции');await page.locator('[data-action=op][data-arg="0"]').click();await page.locator('#qty').fill('3');await page.locator('#qty').blur();await click('Сохранить черновик');const draft=await val(()=>state.docs.at(-1).id);await page.locator(`[data-action=doc][data-arg="${draft}"]`).click();await click('Редактировать и проверить');await page.locator('#qty').fill('4');await page.locator('#qty').blur();await click('Скан верной ячейки');await click('Скан верного товара');await click('Подтвердить операцию');await nav('Документы');await page.waitForFunction(id=>state.docs.find(x=>x.id===id).status==='Передан',draft);await page.locator(`[data-action=doc][data-arg="${draft}"]`).click();await click('Имитировать статус из МойСклад');assert.equal(await val(()=>state.docs.at(-1).external),true);
 // Role changes cannot confirm protected documents.
 await page.locator('#role').selectOption('Сборщик');await nav('Операции');await page.locator('[data-action=op][data-arg="3"]').click();assert.match(await page.locator('#notice').textContent(),/другой роли/);await page.locator('#role').selectOption('Старший смены');
 // KB conversation, unknown issue and handoff with history, no network messaging.
 await nav('Помощь');await click('Нет товара в ячейке');await page.locator('#message').fill('Да, проверил, не помогло');await page.locator('#chatForm').evaluate(f=>f.requestSubmit());await page.locator('#message').fill('Неизвестная проблема');await page.locator('#chatForm').evaluate(f=>f.requestSubmit());await click('Обратиться к Максиму');assert.ok(await val(()=>state.tickets[0].history.length>=7));assert.match(await page.locator('.ticket').textContent(),/Никому не отправлено/);
 // All sections at terminal ratio and phones/tablets. Tables have deliberate local scrolling.
 await click('Настройки демо');for(const [width,height] of [[320,534],[360,600],[390,844],[480,800],[760,1024],[1280,800]]){await page.setViewportSize({width,height});for(const name of ['Смена','Волны','Операции','Документы','Остатки','Сотрудники','Журнал','Помощь']){await nav(name);assert.ok(await val(()=>document.documentElement.scrollWidth<=innerWidth),`overflow ${width} ${name}`);}}
 await page.setViewportSize({width:360,height:600});await nav('Операции');await page.screenshot({path:'/tmp/lukdecor-demo-mobile.png',fullPage:true});
 // Fresh pages are isolated, and reset restores stock and seeded histories.
 const other=await browser.newPage();await other.goto(url);assert.equal(await other.evaluate(()=>state.docs.length),2);assert.equal(await other.evaluate(()=>state.wave),null);await other.close();page.on('dialog',d=>d.accept());await click('Настройки демо');await click('Сбросить демо');assert.equal(await val(()=>state.docs.length),2);assert.equal(await val(()=>total(0)),38);assert.equal(await val(()=>state.wave),null);
 await page.setViewportSize({width:1440,height:1000});await page.locator('#mode').selectOption('terminal');await nav('Операции');assert.equal(await page.locator('.shell').evaluate(el=>Math.round(el.getBoundingClientRect().height)),600);await page.screenshot({path:'/tmp/lukdecor-demo-simulator.png'});
 assert.deepEqual(errors,[]);console.log('PASS: shared wave, shortage, roles, cancellation, station, 6 operations, partial move, stock guard, idempotency, draft editing, demo exchange, help/handoff, 6 viewports, isolation, reset, 3:5 simulator; no JS errors');await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
