// Isolated UI fixtures only. Screenshots include simulated layout responses;
// accounting operations are exercised separately by browser_cutting.cjs.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const requested=process.env.KROY_VISUAL_STATES?.split(',');
const output='.runtime/cutting-visual';fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true});
 const errors=[],checks=[];
 try{
  const p=await browser.newPage({viewport:{width:1440,height:1000},locale:'ru-RU',reducedMotion:'reduce'});
  p.on('pageerror',e=>errors.push(e.message));p.on('dialog',d=>d.accept());
  async function login(port){await p.goto(`http://127.0.0.1:${port}/yuji/`);await p.getByLabel('Личный ключ доступа').fill('cut-local-test');await p.getByRole('button',{name:'Войти',exact:true}).click();await p.getByRole('heading',{name:'Товары',exact:true}).waitFor();await p.getByRole('navigation').getByRole('button',{name:'Крой',exact:true}).click();await p.getByRole('heading',{name:'Задания на крой',exact:true}).waitFor()}
  async function choose(label,name){await p.getByRole('button',{name:label,exact:true}).click();await p.getByRole('option',{name,exact:true}).click()}
  async function screenshots(name){if(requested&&!requested.includes(name))return;for(const theme of ['light','dark']){
   await p.setViewportSize({width:1440,height:1000});await p.getByRole('button',{name:theme==='light'?'Светлая тема':'Тёмная тема',exact:true}).click();
   for(const width of [320,390,768,1440]){
    await p.setViewportSize({width,height:1000});await p.evaluate(()=>window.scrollTo(0,0));
    assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${name}: overflow ${theme}/${width}`);
    assert(await p.locator('.cutting input:not([type=checkbox]):not(.select-validation)').evaluateAll(inputs=>inputs.every(i=>{const r=i.getBoundingClientRect();return r.width===0||r.width>=65})),`${name}: compressed input ${width}`);
    await p.screenshot({path:`${output}/${name}-${theme}-${width}.png`,fullPage:true,animations:'disabled'});checks.push(`${name}/${theme}/${width}`);
   }
  }await p.setViewportSize({width:1440,height:1000});console.log('Checked '+name)}
  // Real catalog snapshot with synthetic, read-only documents in the preview DB.
  await login(18766);await choose('Статус задания','Все статусы');await p.getByRole('button',{name:'Фильтры',exact:true}).click();await screenshots('journal');
  for(const status of ['Черновик','Выдано','Частично выполнено','Завершено','Отменено']){
   await p.locator('.cut-register tbody tr').filter({has:p.getByText(status,{exact:true})}).first().click();
   const hero=await p.locator('.cut-hero').boundingBox(),plan=await p.locator('.cut-panel').first().boundingBox();
   assert(plan.y-hero.y-hero.height>=23,'Document sections need breathing room');
   assert(await p.locator('.cut-document-meta>div').evaluateAll(fields=>fields.every(f=>{const label=f.querySelector('dt').getBoundingClientRect(),value=f.querySelector('dd').getBoundingClientRect();return value.y-label.bottom>=9})), 'Metadata label gap');
   await screenshots('document-'+status);
   if(status==='Выдано'){
    await p.getByRole('button',{name:'Внести факт кроя',exact:true}).click();await screenshots('actual');
    await p.getByLabel('Проверил фактический выпуск и расход. Изделия поступят в заморозку.').check();await p.getByRole('button',{name:'Подтвердить',exact:true}).click();await p.getByRole('alert').filter({hasText:'Укажи основание'}).waitFor();await screenshots('actual-error');
    await p.getByRole('button',{name:'Закрыть операцию',exact:true}).click();
    await p.getByRole('button',{name:'Действия',exact:true}).click();await p.getByRole('menuitem',{name:'Изменить план',exact:true}).click();await screenshots('revise');await p.getByRole('button',{name:'Закрыть операцию',exact:true}).click();
    await p.getByRole('button',{name:'Действия',exact:true}).click();await p.getByRole('menuitem',{name:'Отменить остаток',exact:true}).click();await screenshots('cancel');await p.getByRole('button',{name:'Закрыть операцию',exact:true}).click();
    await p.emulateMedia({media:'print'});await p.screenshot({path:`${output}/print.png`,fullPage:true,animations:'disabled'});await p.emulateMedia({media:'screen'});
   }
   await p.getByRole('button',{name:'← Задания',exact:true}).click();
  }
  await p.getByRole('button',{name:'+ Задание',exact:true}).click();await p.locator('.cut-picker summary').first().click();
  assert(await p.locator('.cut-picker summary').evaluateAll(rows=>rows.every(el=>el.getBoundingClientRect().height>=78)),'Accordion rows must not shrink');
  await screenshots('catalog-picker');
  // A separate DB has known roll balances and two small garment fixtures.
  await login(18765);
  await p.route('**/api/cutting/sales-refresh',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({status:'done',id:'local-fixture'})}));
  await p.getByRole('button',{name:'+ Задание',exact:true}).click();await screenshots('empty-editor');
  await p.locator('.cut-picker summary').first().click();await p.getByRole('button',{name:'Выбрать все варианты',exact:true}).click();await p.getByRole('button',{name:'Рассчитать потребность',exact:true}).click();
  await p.getByLabel('Комментарий к заданию',{exact:true}).fill('Пополнение базовой коллекции: лонгсливы для осенней поставки с очень длинным названием');
  await p.getByLabel('К крою Лонг скимс S',{exact:true}).fill('38');await p.getByLabel('К крою Лонг скимс M',{exact:true}).fill('37');await p.getByLabel('Основание ручного расчёта или поправки').fill('Изолированный визуальный тест. Количества заданы вручную.');
  await p.locator('.cut-demand-table .cut-disclosure').first().evaluate(el=>el.open=true);await screenshots('demand');
  await p.getByRole('button',{name:'Далее →'}).click();await p.locator('.cut-auto-layout summary').click();
  await choose('Норма Жени',/^Лонг скимс/);await choose('Ткань · Бордо','Ткань бордо');await p.getByLabel('Допустимые соотношения:',{exact:false}).fill('1:1');
  await p.route('**/api/planning/grouped-layout',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({message:'Тестовый предварительный план: один рулон.',result:{Бордо:{rolls:1,patterns:[0],output:{S:38,M:37}}},pattern_outputs:[{S:38,M:37}]})}));
  await p.getByRole('button',{name:'Подобрать',exact:true}).click();await p.getByRole('button',{name:'Добавить в задание',exact:true}).waitFor();await screenshots('layout-preview');
  await p.getByLabel('Допустимые соотношения:',{exact:false}).fill('2:1');assert.equal(await p.getByRole('button',{name:'Добавить в задание',exact:true}).count(),0,'Stale preview must be cleared');
  await p.getByLabel('Допустимые соотношения:',{exact:false}).fill('1:1');await p.getByRole('button',{name:'Подобрать',exact:true}).click();await p.getByRole('button',{name:'Добавить в задание',exact:true}).click();await screenshots('layouts');
  await p.getByRole('button',{name:'Далее →'}).click();await choose('Исполнитель','Тестовый закройщик');await p.getByLabel('Срок кроя',{exact:true}).fill('2026-11-06');await p.getByLabel('Ожидается к продаже',{exact:true}).fill('2026-11-09');await screenshots('review');
  assert(await p.getByRole('button',{name:'Выдать задание',exact:true}).isDisabled(),'Issue requires explicit confirmation');
  await p.getByRole('button',{name:'Назад',exact:true}).click();assert.equal(await p.getByLabel('Бордо · S',{exact:true}).inputValue(),'38','Back must retain layout quantities');
  await p.route('**/api/catalog',async route=>{const r=await route.fetch(),data=await r.json();data.items=data.items.map(i=>i.kind==='material'?{...i,unit:'meter'}:i);await route.fulfill({response:r,json:data})});await p.getByRole('button',{name:'Обновить данные',exact:true}).click();await p.locator('.cut-auto-layout').evaluate(el=>el.open=true);await p.getByText('Для автоподбора нужна ткань с учётом в целых рулонах.',{exact:false}).waitFor();assert.equal(await p.getByRole('button',{name:'Подобрать',exact:true}).count(),0);await screenshots('no-roll-materials');await p.getByRole('button',{name:'← Задания',exact:true}).click();await p.getByRole('navigation').getByRole('button',{name:'Заказы',exact:true}).click();await p.getByRole('button',{name:'+ Зарегистрировать',exact:true}).waitFor();
  assert.deepEqual(errors,[]);console.log(JSON.stringify({screens:checks.length,states:checks.filter(v=>v.endsWith('/light/1440')),widths:[320,390,768,1440],themes:['light','dark'],navigation:true,errors}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
