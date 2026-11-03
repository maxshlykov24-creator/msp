import assert from 'node:assert/strict';
const url=process.env.CERT_TEST_DATABASE_URL;
if(!url || !url.includes('certificate_registry_test'))throw new Error('Use the isolated certificate_registry_test database only');
Object.assign(process.env,{DATABASE_URL:url,JWT_SECRET:'test-only-secret-at-least-16',AMOCRM_API_BASE:'https://example.test/api/v4',AMOCRM_LONG_LIVED_TOKEN:'test-only-token-not-a-secret',AMOCRM_PIPELINE_SALES:'9601214',AMOCRM_PIPELINE_COMPLAINTS:'9601246',MOYSKLAD_API_BASE:'https://example.test',MOYSKLAD_API_TOKEN:'test-only-token-not-a-secret'});
const {db,sql:pg}=await import('../dist/db/index.js');const {sql}=await import('drizzle-orm');
const r=await import('../dist/services/certificateRegistry.js');const c=await import('../dist/services/certificates.js');
const {moscowTodayYmd}=await import('@kassa/shared');
try{
const {migrate}=await import("drizzle-orm/postgres-js/migrator");
await migrate(db,{migrationsFolder:"./drizzle"});
await db.execute(sql`CREATE TABLE IF NOT EXISTS certificates (number text PRIMARY KEY,nominal integer NOT NULL,balance integer NOT NULL,status text NOT NULL,type text NOT NULL,guest_name text,buyer_deal_number integer,issued_at text NOT NULL,valid_until text NOT NULL)`);
await r.initializeCertificateRegistry();await r.initializeCertificateRegistry();
const reserved=await r.reservedNumbers();assert(reserved.some(x=>x.number==='00873'));assert.equal(r.canonicalNumber(' mb48379261 '),'MB-4837-9261');assert.equal(r.canonicalNumber('00881'),'00881');
const base={number:'',nominal:100,balance:100,status:'active',type:'digital',issuedAt:moscowTodayYmd(),validFrom:moscowTodayYmd(),validUntil:'2099-01-01'};
const heldSource='test:'+crypto.randomUUID();const heldNumber=await r.reserveNumber(heldSource);assert(!(await c.list()).some(x=>x.number===heldNumber));const completed=await r.ensureCertificate(heldSource,base);assert.equal(completed.number,heldNumber);
const source='test:'+crypto.randomUUID();const issued=await Promise.all(Array.from({length:8},()=>r.ensureCertificate(source,base)));
assert.equal(new Set(issued.map(x=>x.number)).size,1);assert.match(issued[0].number,/^MB-\d{4}-\d{4}$/);
await assert.rejects(()=>r.ensureCertificate('test:'+crypto.randomUUID(),base,'00873'));
const spent=await Promise.all(Array.from({length:8},()=>c.redeem(issued[0].number,30)));assert.equal(spent.filter(Boolean).length,3);
const same=await r.ensureCertificate(source,base);assert.equal(same.balance,10);assert.equal(same.number,issued[0].number);
const future=await r.ensureCertificate('test:'+crypto.randomUUID(),{...base,validFrom:'2098-01-01'});assert.equal(await c.redeem(future.number,1),null);
await assert.rejects(()=>c.redeemMany([{number:future.number,amountRub:1}]));
const a=await r.ensureCertificate('test:'+crypto.randomUUID(),base);const b=await r.ensureCertificate('test:'+crypto.randomUUID(),base);
await assert.rejects(()=>c.redeemMany([{number:a.number,amountRub:10},{number:b.number,amountRub:101}]));
const after=(await c.list()).find(x=>x.number===a.number);assert.equal(after.balance,100);
const voided=await r.ensureCertificate('test:'+crypto.randomUUID(),base);assert(await c.cancel(voided.number));assert.equal(await c.redeem(voided.number,1),null);assert.equal((await r.ensureCertificate('test:'+crypto.randomUUID(),base)).status,'active');await assert.rejects(()=>r.ensureCertificate('test:'+crypto.randomUUID(),base,voided.number));

const q=await import('../dist/services/queue.js');
const fixture=900000000+Math.floor(Math.random()*10000000);
const payload={kind:'refund',dealNumber:fixture,client:'ТЕСТ',amount:100,destination:'isolated-test'};
const created=await Promise.all(Array.from({length:8},()=>q.addQueue(payload)));
assert.equal(new Set(created.map(x=>x.id)).size,1,'concurrent queue creation deduplicated');
const id=created[0].id;
assert.equal(await q.closeQueue(id,'ТЕСТ'),null,'money cannot be closed as documents');
const payouts=await Promise.allSettled(Array.from({length:8},()=>q.issueQueue(id,{by:'ТЕСТ',methodId:'cash',amountRub:30,expectedIssuedAmount:0})));
assert.equal(payouts.filter(x=>x.status==='fulfilled').length,1,'only one payout with expected balance');
let current=(await q.listQueue()).find(x=>x.id===id);
assert.equal(current.issuedAmount,30);assert.equal(current.payouts.length,1);
await assert.rejects(()=>q.reopenQueue(id,'ТЕСТ'),'paid history cannot be erased');
await q.issueQueue(id,{by:'ТЕСТ',methodId:'cash',amountRub:70,expectedIssuedAmount:30});
assert.equal((await q.addQueue(payload)).id,id,'issued obligation is still deduplicated');
current=(await q.listQueue()).find(x=>x.id===id);assert.equal(current.issuedAmount,100);assert.equal(current.payouts.length,2);
const cert=await r.ensureCertificate('test:'+crypto.randomUUID(),base);
const paymentDeal={id:'test:'+crypto.randomUUID(),number:fixture,payments:[{id:'p1',methodId:'certificate',certificateNumber:cert.number,amount:30}]};
await Promise.all(Array.from({length:8},()=>c.redeemDealPayments(paymentDeal)));
assert.equal((await c.list()).find(x=>x.number===cert.number).balance,70,'replayed certificate payment spent once');
await assert.rejects(()=>c.redeemDealPayments({...paymentDeal,payments:[{...paymentDeal.payments[0],amount:40}]}));
assert.equal((await c.list()).find(x=>x.number===cert.number).balance,70);
const idem=await import('../dist/lib/idempotency.js');let invoked=0;const operation='test:'+crypto.randomUUID();
const external=()=>{invoked++;return Promise.resolve({id:'external-id'})};
await idem.withIdempotency(operation,'test',external);await idem.withIdempotency(operation,'test',external);assert.equal(invoked,1);
const uncertain='test:'+crypto.randomUUID();await assert.rejects(()=>idem.withIdempotency(uncertain,'test',async()=>{throw Error('connection lost after commit')}));
await assert.rejects(()=>idem.withIdempotency(uncertain,'test',external));assert.equal(invoked,1,'unknown result not replayed');
const salary=await q.addQueue({...payload,kind:'salary',dealNumber:fixture+1});
await Promise.all(Array.from({length:6},()=>q.issueSalary(salary.id,{by:'ТЕСТ:'+fixture,methodId:'cash',expectedIssuedAmount:0})));
const expenseCount=await pg`select count(*)::int as count from expenses where category='Зарплата' and created_by=${'ТЕСТ:'+fixture}`;
assert.equal(expenseCount[0].count,1,'concurrent salary only records one expense');

const {deals:dealTable}=await import('../dist/db/schema.js');
const date='2098-12-25T10:00:00.000Z';
const sample={id:'stats:'+crypto.randomUUID(),number:fixture+10,createdAt:date,completedAt:date,kind:'sale',funnel:'offline',store:'На Бауманской',consultant:'Консультант '+fixture,clientName:'Покупатель',clientPhone:'',stage:'Успех',total:9000,paid:9000,payments:[],items:[{productId:'a',name:'A',qty:1,price:10000}]};
const fixtures=[sample,{...sample,id:'stats:'+crypto.randomUUID(),number:fixture+11,kind:'refund',funnel:'return',linkedDealNumber:sample.number,consultant:'Другой',total:-9000,paid:0,items:[{...sample.items[0],isReturn:true,settledUnitPrice:9000}]},{...sample,id:'stats:'+crypto.randomUUID(),number:fixture+12,clientName:'ТЕСТ кассы',total:500000},{...sample,id:'stats:'+crypto.randomUUID(),number:fixture+13,kind:'defect',funnel:'defects',total:0}];
for (const data of fixtures) await db.insert(dealTable).values({number:data.number,data});
const {summary}=await import('../dist/services/stats.js');
const report=await summary({from:'2098-12-25',to:'2098-12-25'},{name:sample.consultant,role:'consultant'});
assert.equal(report.totals.clients,1);assert.equal(report.totals.success,1);assert.equal(report.totals.revenue,0);assert.equal(report.totals.upt,1);

const payroll=await import('../dist/services/payroll.js');
const actor={id:crypto.randomUUID(),name:'Руководитель проверки',role:'admin'};
await payroll.confirmWorkday({consultant:sample.consultant,date:'2098-12-26',store:sample.store,active:true,reason:'Подтверждён день без продаж'},actor);
const wages=await payroll.payrollReport('2098-12-26','2098-12-26');
assert.equal(wages.rows.find(row=>row.consultant===sample.consultant).basePay,wages.settings.dailyFloor,'day without sales receives floor');
assert.equal(wages.warnings.length,0);
const unconfirmed=await payroll.payrollReport('2098-12-25','2098-12-25');assert(unconfirmed.warnings.length>0);
await assert.rejects(()=>payroll.enqueueSalaryForPeriod('2098-12-25','2098-12-25','Проверка'),'unconfirmed payroll not queued');
const cash=await import('../dist/services/stats.js');
const store='isolated-cash:'+fixture;
const cashDeal={...sample,id:crypto.randomUUID(),number:fixture+20,store};
await db.insert(dealTable).values({number:cashDeal.number,data:cashDeal});
const {ledger:ledgerTable}=await import('../dist/db/schema.js');
await db.insert(ledgerTable).values({dealNumber:cashDeal.number,methodId:'cash',kind:'cash',amount:5000,idempotencyKey:'test:'+fixture,createdBy:'Проверка'});
const snapshot=await cash.cashShift(store);
assert.equal(snapshot.movement,50);assert.equal(snapshot.openingCash,null);
const closeInput={store,cutoff:snapshot.cutoff,previousId:null,openingCash:100,countedCash:140,adjustment:0,reason:'Начальный остаток подтверждён'};
await assert.rejects(()=>cash.closeCashShift(closeInput,actor),'mismatch cannot close shift');
const closed=await cash.closeCashShift({...closeInput,countedCash:150},actor);assert.equal(closed.countedCash,150);
await assert.rejects(()=>cash.closeCashShift({...closeInput,countedCash:150},actor),'stale close cannot create another shift');
const cashAfter=await cash.cashShift(store);assert.equal(cashAfter.openingCash,150);assert.equal(cashAfter.movement,0);
await assert.rejects(()=>q.addQueue({...payload,amount:110}),'paid obligation cannot be recreated with another amount');

// Полный процесс на настоящей изолированной БД и подменённых CRM/МС.
const {msRefs,amoMeta,products}=await import('../dist/db/schema.js');
const {STORE_TO_WAREHOUSE,AMO_LEAD_FIELDS}=await import('@kassa/shared');
const meta=(type,id)=>({href:`https://example.test/entity/${type}/${id}`,type,mediaType:'application/json'});
for(const [kind,key,type] of [['organization','default','organization'],['counterparty','retail','counterparty'],['store',STORE_TO_WAREHOUSE[sample.store],'store']]){
 const id=crypto.randomUUID();const row={id,name:key,meta:meta(type,id)};
 await db.insert(msRefs).values({kind,key,msId:id,metaHref:row.meta.href,name:key,payload:row});
}
await db.insert(amoMeta).values({kind:'field',amoId:991122,name:AMO_LEAD_FIELDS.kassaLink,payload:{type:'text'}}).onConflictDoNothing();
const productId=crypto.randomUUID();await db.insert(products).values({msId:productId,msMetaHref:meta('product',productId).href,msType:'product',name:'Проверочная позиция',price:1000000});
const createdDocs=[];const externalDocs=new Map();const crmLeads=new Map();let rejectSecondCash=true;let failedCash=0;let newLeadCalls=0;
const json=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'content-type':'application/json'}});
globalThis.fetch=async(url,options={})=>{
 const u=new URL(String(url));const path=u.pathname;const method=options.method??'GET';const body=options.body?JSON.parse(options.body):{};
 if(path.startsWith('/api/v4/')){
  if(path.endsWith('/contacts')&&method==='GET')return json({_embedded:{contacts:[{id:fixture+40,name:'Проверка'}]}});
  if(path.endsWith('/contacts')&&method==='POST')return json({_embedded:{contacts:[{id:fixture+40}]}});
  if(/\/leads\/\d+$/.test(path)){const id=Number(path.split('/').at(-1));const old=crmLeads.get(id)??{id};if(method==='PATCH')crmLeads.set(id,{...old,...body});return json(crmLeads.get(id)??old);}
  if(path.endsWith('/leads')&&method==='POST'){newLeadCalls++;return json({_embedded:{leads:[{id:fixture+41+newLeadCalls}]}});}
  throw Error('Unexpected CRM test request '+method+' '+path);
 }
 if(path==='/entity/counterparty'&&method==='GET'){const id='00000000-0000-4000-8000-000000000055';return json({rows:[{id,name:'Проверка',phone:'+79990000000',meta:meta('counterparty',id)}]});}
 const match=path.match(/^\/entity\/(customerorder|demand|cashin|salesreturn|paymentin)(?:\/([0-9a-f-]+))?$/);
 if(match){const [,type,id]=match;
  if(method==='GET'&&id)return json(externalDocs.get(id));
  if(method==='PUT'&&id){const updated={...externalDocs.get(id),...body};externalDocs.set(id,updated);return json(updated);}
  if(method==='POST'){
   if(type==='cashin'&&body.sum===600000&&rejectSecondCash){failedCash++;rejectSecondCash=false;return json({errors:[{error:'isolated rejection'}]},400);}
   const newId=crypto.randomUUID();const doc={...body,id:newId,meta:meta(type,newId)};createdDocs.push({type,...doc});externalDocs.set(newId,doc);return json(doc);
  }
 }
 throw Error('Unexpected MS test request '+method+' '+path);
};
const saleService=await import('../dist/services/sale.js');const dealService=await import('../dist/services/deals.js');
let saleDeal={...sample,id:crypto.randomUUID(),number:fixture+30,amoLeadId:fixture+42,clientPhone:'+79990000000',channel:'Сайт',purpose:'Работа',stage:'Новая заявка',paid:3000,payments:[{id:'deposit',methodId:'cash',amount:3000}],items:[{productId,name:'Проверочная позиция',price:10000,qty:1,barcode:'test'}]};
await dealService.persist(saleDeal);
saleDeal=await saleService.processSale(saleDeal,{fulfill:false,who:'Проверка'});
assert.equal(createdDocs.filter(d=>d.type==='cashin').length,1);assert.equal(createdDocs.filter(d=>d.type==='demand').length,0);
saleDeal={...saleDeal,stage:'Успех',paid:9000,payments:[...saleDeal.payments,{id:'balance',methodId:'cash',amount:6000}]};await dealService.persist(saleDeal);
await assert.rejects(()=>saleService.processSale(saleDeal,{fulfill:true,who:'Проверка'}));assert.equal(failedCash,1);
saleDeal=await dealService.getByNumber(saleDeal.number);assert(saleDeal.msOrderId&&saleDeal.msDemandId,'checkpoints survive payment rejection');
saleDeal=await saleService.processSale(saleDeal,{fulfill:true,who:'Проверка'});
await saleService.processSale(saleDeal,{fulfill:true,who:'Проверка'});
assert.equal(createdDocs.filter(d=>d.type==='customerorder').length,1);assert.equal(createdDocs.filter(d=>d.type==='demand').length,1);assert.equal(createdDocs.filter(d=>d.type==='cashin').length,2);
const order=createdDocs.find(d=>d.type==='customerorder');assert.equal(order.positions.reduce((sum,p)=>sum+p.quantity*p.price,0),900000);
const depositDoc=createdDocs.find(d=>d.type==='cashin'&&d.sum===300000);assert.equal(externalDocs.get(depositDoc.id).operations[0].meta.href,order.meta.href,'deposit linked without new payment');
assert.equal(newLeadCalls,0,'existing CRM binding retained');
const refundDeal={...saleDeal,id:crypto.randomUUID(),number:fixture+31,kind:'refund',funnel:'return',amoLeadId:fixture+43,msOrderId:undefined,msDemandId:undefined,paid:0,payments:[],total:-9000,linkedDealNumber:saleDeal.number,items:[{...saleDeal.items[0],price:-9000,settledUnitPrice:9000,isReturn:true}]};
await dealService.persist(refundDeal);const returned=await saleService.processSale(refundDeal,{fulfill:true,who:'Проверка'});await saleService.processSale(returned,{fulfill:true,who:'Проверка'});
assert.equal(createdDocs.filter(d=>d.type==='salesreturn').length,1);assert.equal(newLeadCalls,0,'return retry does not create a new CRM lead');
console.log(JSON.stringify({saleProcess:['deposit before fulfillment','net discounted positions','payment rejection visible','checkpoint resume','documents and payments not duplicated','deposit linked','return CRM binding retained'],result:'passed'}));
console.log(JSON.stringify({workdaysAndShifts:['zero-sales day','unconfirmed payroll blocked','cash movement from ledger','mismatch blocked','closure persists','repeat blocked','balance carried forward'],result:'passed'}));
console.log(JSON.stringify({statistics:['refund attributed to original consultant','refund and defect not new clients','test deal excluded','net revenue'],result:'passed'}));
console.log(JSON.stringify({finance:['queue concurrency','partial payout retries','no paid reopen','closed queue dedup','certificate payment replay','external unknown outcome','salary concurrency'],result:'passed'}));
console.log(JSON.stringify({checks:['legacy reservations and zeros','canonical format','concurrent issuance','reserved conflict','atomic spending','repeat issuance keeps balance','future date blocks both spending paths','batch rollback'],result:'passed'}));
}finally{await pg.end();}
