import assert from 'node:assert/strict';
import Fastify from 'fastify';
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
const integration=process.env.MANSBAND_AUTOMATION_INTEGRATION==='true';
let crmTask, crmLead;
const fakeAmo=createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(req.url.startsWith('/tasks/')?crmTask:crmLead));});
if(integration) {
  process.env.PGHOST='/tmp/mansband-f5-pg';process.env.PGUSER='postgres';
  await new Promise(resolve=>fakeAmo.listen(0,'127.0.0.1',resolve));
}

// No network or live credentials: unauthorized/invalid requests must never reach SQL.
Object.assign(process.env, {
  DATABASE_URL: integration ? 'postgres:///kassa_automation_test' : 'postgres://test:test@127.0.0.1:1/test',
  JWT_SECRET: 'test-only-jwt-secret-000000000000',
  AMOCRM_API_BASE: integration ? `http://127.0.0.1:${fakeAmo.address().port}` : 'https://example.invalid',
  AMOCRM_LONG_LIVED_TOKEN: 'test-only-token-0000000000000000',
  AMOCRM_PIPELINE_SALES: '9601214', AMOCRM_PIPELINE_COMPLAINTS: '9601246',
  MOYSKLAD_API_BASE: 'https://example.invalid',
  MOYSKLAD_API_TOKEN: 'test-only-token-0000000000000000',
  CRM_AUTOMATION_SERVICE_KEY: 'test-only-automation-key-00000000000000',
  CRM_AUTOMATION_TASKS_ENABLED: integration ? 'true' : 'false',
});
const { default: routes } = await import('../src/routes/crmAutomation.routes.ts');
const { sql } = await import('../src/db/index.ts');
const app = Fastify();
try {
  await app.register(routes);
  for (const headers of [{}, {'x-automation-key':'wrong'}, {'x-automation-key':'x'.repeat(38)}]) {
    const response = await app.inject({method:'GET', url:'/internal/crm-automation/leads/55', headers});
    assert.equal(response.statusCode,403);
  }
  for (const id of ['0','-1','1.5','abc','9007199254740992']) {
    const response = await app.inject({method:'GET',url:'/internal/crm-automation/leads/'+id,
      headers:{'x-automation-key':process.env.CRM_AUTOMATION_SERVICE_KEY}});
    assert.equal(response.statusCode,400);
  }
  const handoff='/internal/crm-automation/leads/55/courier-completed';
  assert.equal((await app.inject({method:'POST',url:handoff,payload:{task_id:1,occurrence:'entry:abc'}})).statusCode,403);
  const headers={'x-automation-key':process.env.CRM_AUTOMATION_SERVICE_KEY};
  for (const payload of [{},{task_id:0,occurrence:'entry:abc'},{task_id:1,occurrence:'anything'},{task_id:1,occurrence:'entry:a/b'}]) {
    assert.equal((await app.inject({method:'POST',url:handoff,headers,payload})).statusCode,400);
  }
  if(!integration) assert.equal((await app.inject({method:'POST',url:handoff,headers,payload:{task_id:1,occurrence:'entry:abc'}})).statusCode,409);
  if(!integration) console.log('14 automation API authorization/validation checks passed; SQL was not reached');
  else {
    await sql.unsafe(`create table if not exists deals (id uuid primary key default gen_random_uuid(),number integer unique not null,amo_lead_id bigint,ms_order_id text,ms_demand_id text,data jsonb not null,stage text not null,payment_status text,idempotency_key text,sync_status text default 'pending',sync_error text,created_at timestamptz default now(),updated_at timestamptz default now());
      create table if not exists tasks (id uuid primary key default gen_random_uuid(),kind text not null,deal_number integer,deal_item_id text,store text,assignee_role text not null,status text default 'pending',title text not null,idempotency_key text unique,metadata jsonb,created_by text not null,completed_by text,created_at timestamptz default now(),completed_at timestamptz);
      create table if not exists audit_log (id uuid primary key default gen_random_uuid(),actor_id uuid,actor_name text,actor_role text,action text,entity_type text,entity_id text,source text,"before" jsonb,"after" jsonb,metadata jsonb,created_at timestamptz default now());`);
    const lid=Math.floor(Date.now()/1000), number=lid, occurrence='entry:integration';
    const marker='[MB-AUTO:'+createHash('sha256').update(`courier:${lid}:${occurrence}`).digest('hex').slice(0,24)+']';
    crmLead={id:lid,pipeline_id:11482377,status_id:89900829};
    crmTask={id:777,entity_id:lid,entity_type:'leads',task_type_id:3946594,is_completed:true,text:'Вызвать курьера\n'+marker};
    const deal={id:'integration-'+lid,number,amoLeadId:lid,kind:'delivery',funnel:'online',stage:'Собран',items:[],isTest:false};
    await sql`insert into deals(number,amo_lead_id,data,stage) values(${number},${lid},${JSON.stringify(deal)}::jsonb,'Собран')`;
    const url=`/internal/crm-automation/leads/${lid}/courier-completed`;
    const send=()=>app.inject({method:'POST',url,headers,payload:{task_id:777,occurrence}});
    const snapshot=await app.inject({method:'GET',url:`/internal/crm-automation/leads/${lid}`,headers});
    assert.equal(snapshot.statusCode,200);assert.equal(snapshot.json().bindings.length,1);
    crmTask.is_completed=false;assert.equal((await send()).statusCode,409);
    crmTask.is_completed=true;crmTask.text='manual';assert.equal((await send()).statusCode,409);crmTask.text='Вызвать курьера\n'+marker;
    crmLead._embedded={tags:[{name:'MB_MIGRATION_IN_PROGRESS'}]};assert.equal((await send()).statusCode,409);delete crmLead._embedded;
    const responses=await Promise.all([send(),send(),send()]);
    assert.deepEqual(responses.map(r=>r.statusCode),[200,200,200]);
    const rows=await sql`select * from tasks where deal_number=${number}`;
    assert.equal(rows.length,1);assert.equal(rows[0].kind,'take_to_cdek');
    assert.equal((await sql`select stage from deals where number=${number}`)[0].stage,'Собран');
    await sql`update tasks set status='cancelled' where deal_number=${number}`;
    assert.equal((await send()).statusCode,200);
    assert.equal((await sql`select status from tasks where deal_number=${number}`)[0].status,'cancelled');
    crmLead.status_id=89900837;assert.equal((await send()).statusCode,409);
    console.log('Local PostgreSQL + HTTP integration passed: read snapshot, reject uncompleted/manual/migration, concurrent dedup, no stage move, no cancelled-task reopening');
  }
} finally {
  await app.close();
  await sql.end();
  if(integration) await new Promise(resolve=>fakeAmo.close(resolve));
}
