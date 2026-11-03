import { createRequire } from 'node:module';

Object.assign(process.env,{DATABASE_URL:'postgres://unused',JWT_SECRET:'test-only-secret-at-least-16',AMOCRM_API_BASE:'https://example.test/api/v4',AMOCRM_LONG_LIVED_TOKEN:'test-only-token-not-a-secret',AMOCRM_PIPELINE_SALES:'9601214',AMOCRM_PIPELINE_COMPLAINTS:'9601246',MOYSKLAD_API_BASE:'https://example.test',MOYSKLAD_API_TOKEN:'test-only-token-not-a-secret',MESSAGE_TEMPLATES_URL:'https://internal.example.test',MESSAGE_TEMPLATES_KEY:'test-only-service-key-length-24'});
const require=createRequire(new URL('../package.json',import.meta.url));const Fastify=require('fastify');
const {default:auth}=await import(new URL('../src/plugins/auth.ts',import.meta.url));
const {default:routes}=await import(new URL('../src/routes/messageTemplates.routes.ts',import.meta.url));
let forwarded;
globalThis.fetch=async(url,opts)=>{forwarded={url,opts};return new Response(JSON.stringify({ok:true}),{status:200,headers:{'Content-Type':'application/json'}})};
const app=Fastify();await app.register(auth);await app.register(routes);await app.ready();
const assert=(x,label)=>{if(!x)throw new Error(label)};
assert((await app.inject({method:'GET',url:'/message-templates'})).statusCode===401,'anonymous read');
let checks=1;
for(const role of ['rop','crm','consultant','finance','seller','logist']){
 const token=app.jwt.sign({sub:'test',name:'Тест',role});const headers={authorization:'Bearer '+token};
 assert((await app.inject({method:'GET',url:'/message-templates',headers})).statusCode===200,role+' read');checks++;
 for(const suffix of ['/draft','/check/test','/publish/test','/test/test','/gift/test','/gift/retry-pdf/1']){
  assert((await app.inject({method:'POST',url:'/message-templates'+suffix,headers,payload:{}})).statusCode===403,role+' mutation');checks++;
 }
}
const token=app.jwt.sign({sub:'admin-id',name:'Админ',role:'admin'});
assert((await app.inject({method:'POST',url:'/message-templates/draft',headers:{authorization:'Bearer '+token},payload:{actor:'forged'}})).statusCode===200,'admin mutation');
assert(JSON.parse(forwarded.opts.body).actor==='admin-id: Админ','trusted actor');
assert(forwarded.opts.headers['X-Reminder-Key']===process.env.MESSAGE_TEMPLATES_KEY,'server-only key');
checks+=3;console.log(JSON.stringify({checks,result:'passed'}));await app.close();

// Регрессии аудита кассы: финансовые роли, завершённые заявки, секреты в логах и WS.
const {completedDealEditError,safeRequestLog}=await import(new URL('../src/plugins/auth.ts',import.meta.url));
const {registerRealtime}=await import(new URL('../src/ws/hub.ts',import.meta.url));
const {parseAmoWebhookBody}=await import(new URL('../src/services/amoWebhook.ts',import.meta.url));
const security=Fastify();await security.register(auth);await security.register(require('@fastify/websocket'));
security.get('/finance',{preHandler:[security.requireFinanceQueues]},async()=>({ok:true}));
registerRealtime(security);await security.ready();
for(const role of ['consultant','logist','crm','seller','finance','rop','admin']) {
 const headers={authorization:'Bearer '+security.jwt.sign({sub:'fixture',name:'Максим Max',role})};
 const allowed=['finance','rop','admin'].includes(role);
 assert((await security.inject({method:'GET',url:'/finance',headers})).statusCode===(allowed?200:403),role+' finance access must not depend on name');checks++;
 for(const stage of ['Успех','Провал','Не слив']) {
  const err=completedDealEditError(stage,role,'Исправление теста');
  assert(['rop','admin'].includes(role)?err===null:err?.status===403,role+' closed deal permissions');checks++;
 }
}
assert(completedDealEditError('Успех','admin',' ')?.status===400,'closed correction requires reason');checks++;
assert(completedDealEditError('Новая заявка','consultant')===null,'open deal stays editable');checks++;
assert(!JSON.stringify(safeRequestLog({method:'GET',url:'/ws?token=secret-token&secret=hook-key'})).includes('secret'),'logs redact query');checks++;
const clean=parseAmoWebhookBody('__proto__[polluted]=yes&constructor[prototype][polluted]=yes&leads[status][0][id]=123');
assert({}.polluted===undefined&&clean.leads.status[0].id==='123','webhook form cannot pollute prototypes');checks++;
for(const protocol of [undefined,'kassa, bearer.invalid']) {
 const ws=await security.injectWS('/ws',protocol?{headers:{'sec-websocket-protocol':protocol}}:{});
 const closed=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('unauthorized WS remained open')),2000);ws.on('close',(code)=>{clearTimeout(timer);resolve(code)});});
 assert(await closed===4001,'anonymous/invalid websocket rejected');checks++;
}
const wsToken=security.jwt.sign({sub:'fixture',name:'Тест',role:'consultant'});
const good=await security.injectWS('/ws',{headers:{'sec-websocket-protocol':'kassa, bearer.'+wsToken}});
assert(good.readyState===1,'authorized websocket accepted');checks++;good.close();
await security.close();
console.log(JSON.stringify({checks,result:'passed',scope:'access and security regressions'}));

const {default:webhooks}=await import(new URL('../src/routes/webhooks.routes.ts',import.meta.url));
const hooks=Fastify();let failSync=true;let calls=0;
await hooks.register(webhooks,{
 handleAmoWebhook:async()=>{calls++;if(failSync)throw new Error('provider offline');return {results:[]}},
 syncCatalog:async()=>{calls++;if(failSync)throw new Error('provider offline');return {products:1,stock:1}},
});
for(const route of ['amo','ms']) {
 assert((await hooks.inject({method:'POST',url:'/webhooks/'+route+'?secret=wrong',payload:{}})).statusCode===403,'webhook secret required');checks++;
 assert((await hooks.inject({method:'POST',url:'/webhooks/'+route+'?secret=change-me',payload:{}})).statusCode===503,'webhook failure must be retryable');checks++;
}
assert(calls===2,'invalid hook never invokes providers');checks++;
failSync=false;
for(const route of ['amo','ms']) {
 assert((await hooks.inject({method:'POST',url:'/webhooks/'+route+'?secret=change-me',payload:{}})).statusCode===200,'webhook retry succeeds');checks++;
}
await hooks.close();console.log(JSON.stringify({checks,result:'passed',scope:'including webhook failures and retries'}));

const {default:dealRoutes}=await import(new URL('../src/routes/deals.routes.ts',import.meta.url));
const closedApi=Fastify();await closedApi.register(auth);
await closedApi.register(dealRoutes,{resolveDeal:async()=>({id:'fixture-closed',number:9001,stage:'Успех',kind:'company',total:100,paid:100,items:[],payments:[],clientName:'Тест',clientPhone:'',store:'На Бауманской',consultant:'Тест'})});
const mutations=[['',{comment:'правка'}],['/stage',{stage:'Провал',reason:'тест'}],['/kind',{kind:'promise',reason:'тест'}],['/handover',{issued:true,reason:'тест'}],['/company',{invoiceNo:'TEST',reason:'тест'}]];
for(const role of ['consultant','logist','crm','seller','finance']) {
 const headers={authorization:'Bearer '+closedApi.jwt.sign({sub:'test',name:'Максим',role})};
 for(const [suffix,payload] of mutations) {
  const res=await closedApi.inject({method:'PATCH',url:'/deals/9001'+suffix,headers,payload});
  assert(res.statusCode===403,role+' cannot mutate completed via '+suffix+' ('+res.statusCode+')');checks++;
 }
}
for(const role of ['rop','admin']) {
 const headers={authorization:'Bearer '+closedApi.jwt.sign({sub:'test',name:'Тест',role})};
 const res=await closedApi.inject({method:'PATCH',url:'/deals/9001',headers,payload:{comment:'правка'}});
 assert(res.statusCode===400&&res.json().message.includes('причину'),role+' must give correction reason');checks++;
}
await closedApi.close();console.log(JSON.stringify({checks,result:'passed',scope:'including actual completed-deal HTTP routes'}));

const {pickOpenSalesLead}=await import(new URL('../src/clients/amo.ts',import.meta.url));
const leads=[{id:11,pipeline_id:9601214,status_id:100,updated_at:10},{id:12,pipeline_id:9601214,status_id:100,updated_at:20},{id:13,pipeline_id:9601214,status_id:142,updated_at:30}];
assert(pickOpenSalesLead(leads,9601214,{claimedLeadIds:new Set([12])})?.id===11,'claimed recent lead is not reused');checks++;
assert(pickOpenSalesLead(leads,9601214,{claimedLeadIds:new Set([11,12])})===null,'all claimed leads require a new lead');checks++;
const {handleAmoWebhook}=await import(new URL('../src/services/amoWebhook.ts',import.meta.url));
const seen=[];let hookFailed=false;
try {await handleAmoWebhook({leads:{status:[{id:11},{id:12}]}},async event=>{seen.push(event.id);if(event.id===11)throw Error('offline');return {tasks:true};});} catch {hookFailed=true;}
assert(hookFailed&&seen.join(',')==='11,12','one failed event must reject the batch after attempting other events');checks++;
const retry=await handleAmoWebhook({leads:{status:[{id:11}]}},async()=>({tasks:true}));
assert(retry.ok&&retry.processed===1,'failed event can be retried');checks++;
const batchHooks=Fastify();await batchHooks.register(webhooks,{handleAmoWebhook:body=>handleAmoWebhook(body,async()=>{throw Error('offline')})});
assert((await batchHooks.inject({method:'POST',url:'/webhooks/amo?secret=change-me',payload:{leads:{status:[{id:11}]}}})).statusCode===503,'real event handler failures propagate to HTTP 503');checks++;
await batchHooks.close();console.log(JSON.stringify({checks,result:'passed',scope:'including amo binding and partial webhook failures'}));
