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
 for(const suffix of ['/draft','/check/test','/publish/test','/test/test']){
  assert((await app.inject({method:'POST',url:'/message-templates'+suffix,headers,payload:{}})).statusCode===403,role+' mutation');checks++;
 }
}
const token=app.jwt.sign({sub:'admin-id',name:'Админ',role:'admin'});
assert((await app.inject({method:'POST',url:'/message-templates/draft',headers:{authorization:'Bearer '+token},payload:{actor:'forged'}})).statusCode===200,'admin mutation');
assert(JSON.parse(forwarded.opts.body).actor==='admin-id: Админ','trusted actor');
assert(forwarded.opts.headers['X-Reminder-Key']===process.env.MESSAGE_TEMPLATES_KEY,'server-only key');
checks+=3;console.log(JSON.stringify({checks,result:'passed'}));await app.close();
