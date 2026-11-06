// Only an isolated disposable database is accepted. No external API is contacted.
import assert from 'node:assert/strict';
const database = process.env.AMO_EDIT_TEST_DATABASE_URL;
if (!database || new URL(database).hostname !== 'mansband-pipeline-test-20261106') {
  throw new Error('Use the isolated mansband-pipeline-test-20261106 database');
}
Object.assign(process.env, {
  DATABASE_URL: database, JWT_SECRET: 'synthetic-test-secret-only',
  AMOCRM_API_BASE: 'https://amo.invalid/api/v4', AMOCRM_LONG_LIVED_TOKEN: 'synthetic-test-token-only',
  AMOCRM_PIPELINE_SALES: '1', AMOCRM_PIPELINE_COMPLAINTS: '2',
  AMOCRM_TYPED_PIPELINES_ENABLED: 'false', AMOCRM_ASYNC_EDITS: 'true',
  MOYSKLAD_API_BASE: 'https://ms.invalid', MOYSKLAD_API_TOKEN: 'synthetic-test-token-only',
});
const { sql } = await import('../dist/db/index.js');
const { closeOperationLocks } = await import('../dist/lib/idempotency.js');
const { prepareAmoEdit, flushAmoEdit } = await import('../dist/services/amoEditQueue.js');
const { getByNumber } = await import('../dist/services/deals.js');
let calls = 0;
let fail = false;
let duringSend;
globalThis.fetch = async (url, init) => {
  assert.equal(String(url), 'https://amo.invalid/api/v4/leads/11');
  assert.equal(init.method, 'PATCH');
  calls++;
  if (duringSend) await duringSend();
  return new Response(JSON.stringify(fail ? {error:'synthetic rejection'} : {_embedded:{leads:[{id:11}]}}), {status:fail ? 400 : 200});
};
try {
  await sql`create table if not exists deals (
    id uuid default gen_random_uuid(), number integer primary key, amo_lead_id bigint,
    ms_order_id text, ms_demand_id text, data jsonb not null, stage text,
    payment_status text, idempotency_key text, sync_status text, sync_error text,
    created_at timestamptz default now(), updated_at timestamptz default now())`;
  await sql`create table if not exists amo_meta (
    id uuid default gen_random_uuid(), kind text, amo_id bigint, parent_id bigint,
    name text, payload jsonb, updated_at timestamptz default now())`;
  await sql`insert into amo_meta(kind,amo_id,name,payload)
    select 'field',101,'Комментарий к заказу','{"type":"text"}'::jsonb
    where not exists(select 1 from amo_meta where amo_id=101)`;
  const base = {number:1,amoLeadId:11,kind:'sale',stage:'Новая заявка',items:[],payments:[],total:0,paid:0,comment:'before'};
  const edited = {...base,comment:'first'};
  await prepareAmoEdit(edited, base);
  assert.equal(edited.amoEditOutbox.customFields.length, 1);
  const latest = {...edited,comment:'latest'};
  await prepareAmoEdit(latest, edited);
  assert.notEqual(latest.amoEditOutbox.id, edited.amoEditOutbox.id);
  assert.equal(latest.amoEditOutbox.customFields[0].values[0].value, 'latest');
  await sql`insert into deals(number,amo_lead_id,data) values(1,11,${JSON.stringify(latest)}::jsonb)
    on conflict(number) do update set data=excluded.data`;
  fail = true;
  await flushAmoEdit(1);
  let saved = await getByNumber(1);
  assert.equal(saved.amoEditOutbox.attempts, 1);
  assert.equal(saved.syncStatus,'failed');
  assert.equal(saved.comment,'latest');
  await flushAmoEdit(1);
  assert.equal(calls,1,'retry delay must prevent immediate resend');
  await sql`update deals set data=jsonb_set(data,'{amoEditOutbox,retryAt}','0') where number=1`;
  fail = false;
  await flushAmoEdit(1);
  saved = await getByNumber(1);
  assert.equal(saved.amoEditOutbox,undefined);
  assert.equal(saved.syncStatus,'synced');
  await flushAmoEdit(1);
  assert.equal(calls,2,'acknowledged job must not resend');
  const newer = {...latest.amoEditOutbox,id:'newer-version'};
  await sql`update deals set data=${JSON.stringify(latest)}::jsonb where number=1`;
  duringSend = async () => {
    await sql`update deals set data=jsonb_set(data,'{amoEditOutbox}',${JSON.stringify(newer)}::jsonb) where number=1`;
  };
  await flushAmoEdit(1);
  assert.equal((await getByNumber(1)).amoEditOutbox.id,'newer-version','stale ACK must preserve newer job');
  duringSend = undefined;
  await flushAmoEdit(1);
  assert.equal((await getByNumber(1)).amoEditOutbox,undefined);
  console.log('PASS: merge, durable failure, delayed retry, ACK, duplicate suppression, stale ACK protection');
} finally {
  await closeOperationLocks();
  await sql.end();
}
