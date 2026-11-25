-- Чистка кассы перед сменой 2026-11-25.
-- Меняет только статусы задач, очереди, Сары, сертификатов и одну отметку «в пути».
-- Заявки, суммы, этапы, amo и МойСклад не трогает.
-- Повторный запуск после успеха останавливается: исходные количества уже не сходятся, транзакция откатится.

begin;

create or replace function pg_temp.is_test_deal(data jsonb) returns boolean
language sql immutable as $$
  select coalesce(data->>'isTest' = 'true', false)
    or coalesce(data->>'clientName', '') ~* '(^|[[:space:]·_—-])(тест|test)([[:space:]·_—-]|$)';
$$;

create temp table expected_open (
  number integer primary key,
  stage text not null,
  paid text not null,
  total text not null
);
insert into expected_open (number, stage, paid, total) values
  (57642770, 'Сертификат продан', '13500', '13500'),
  (57642775, 'Документы готовы', '10000', '10000'),
  (57642776, 'Документы готовы', '10000', '10000'),
  (57642777, 'Товар отложен', '0', '10000'),
  (57642778, 'Ждет товар', '45000', '40800'),
  (57642779, 'Документы переданы', '0', '10000'),
  (57642782, 'Товар отложен', '10000', '10000'),
  (57642783, 'Документы готовы', '10000', '10000'),
  (57642786, 'Товар отложен', '0', '17100'),
  (57642801, 'Сертификат оплачен', '15000', '15000'),
  (57642833, 'Товар в магазине', '0', '3825'),
  (57642834, 'Товар в магазине', '0', '4500');

create temp table deals_before as
select number, stage, amo_lead_id, ms_order_id, ms_demand_id, md5(data::text) as data_hash
from deals;

do $$
declare
  open_mismatch integer;
  pending_tasks integer;
  done_without_ms integer;
  salary_n integer;
  salary_sum bigint;
  change_sum bigint;
  tips_sum bigint;
  refund_sum bigint;
  invoice_work integer;
  invoice_test integer;
  sary_pending integer;
  sary_work integer;
  cert_loose integer;
  cert_work integer;
  in_transit integer;
begin
  select count(*) into open_mismatch
  from expected_open e
  full join (
    select d.number, d.stage, d.data->>'paid' as paid, d.data->>'total' as total
    from deals d
    where not pg_temp.is_test_deal(d.data)
      and d.stage not in ('Успех', 'Провал', 'Не слив')
  ) live on live.number = e.number
    and live.stage = e.stage
    and live.paid = e.paid
    and live.total = e.total
  where live.number is null or e.number is null;
  if open_mismatch <> 0 then
    raise exception 'открытые рабочие не совпали со срезом: %', open_mismatch;
  end if;

  select count(*) into pending_tasks from tasks where status = 'pending';
  select count(*) into done_without_ms
  from tasks t
  join deals d on d.number = t.deal_number
  where t.kind = 'movement' and t.status = 'done' and not pg_temp.is_test_deal(d.data)
    and not exists (
      select 1 from tasks a
      where a.kind = 'movement_accept' and a.status = 'done'
        and coalesce(a.metadata->>'msMovementId', '') <> ''
        and a.idempotency_key = t.id::text || ':accept'
    );
  select count(*), coalesce(sum(amount), 0) into salary_n, salary_sum
  from queue where status = 'pending' and kind = 'salary';
  select coalesce(sum(amount), 0) into change_sum from queue where status = 'pending' and kind = 'change';
  select coalesce(sum(amount), 0) into tips_sum from queue where status = 'pending' and kind = 'tips';
  select coalesce(sum(amount), 0) into refund_sum from queue where status = 'pending' and kind = 'refund';
  select count(*) into invoice_work
  from queue q join deals d on d.number = q.deal_number
  where q.status = 'pending' and q.kind = 'invoice' and not pg_temp.is_test_deal(d.data);
  select count(*) into invoice_test
  from queue q join deals d on d.number = q.deal_number
  where q.status = 'pending' and q.kind = 'invoice' and pg_temp.is_test_deal(d.data);
  select count(*) into sary_pending
  from sary s join deals d on d.number = s.ref_deal_number
  where s.status = 'pending' and pg_temp.is_test_deal(d.data);
  select count(*) into sary_work
  from sary s join deals d on d.number = s.ref_deal_number
  where s.status = 'in_check' and not pg_temp.is_test_deal(d.data);
  select count(*) into cert_loose
  from certificates c
  where c.status = 'active'
    and (
      c.buyer_deal_number is null
      or exists (select 1 from deals d where d.number = c.buyer_deal_number and pg_temp.is_test_deal(d.data))
    );
  select count(*) into cert_work
  from certificates c
  where c.status = 'active'
    and c.buyer_deal_number is not null
    and exists (select 1 from deals d where d.number = c.buyer_deal_number and not pg_temp.is_test_deal(d.data));
  select count(*) into in_transit
  from deal_item_state s
  join deals d on d.number = s.deal_number
  where s.state = 'in_transit' and d.stage = 'Товар отложен' and not pg_temp.is_test_deal(d.data)
    and coalesce(s.metadata->>'msMovementId', '') = '';

  if pending_tasks <> 32
    or done_without_ms <> 1
    or salary_n <> 4 or salary_sum <> 7616200
    or change_sum <> 520000 or tips_sum <> 100000 or refund_sum <> 1260000
    or invoice_work <> 3 or invoice_test <> 1
    or sary_pending <> 1 or sary_work <> 3
    or cert_loose <> 5 or cert_work <> 2
    or in_transit <> 1
  then
    raise exception
      'срез перед чисткой изменился: tasks % move % salary %/% change % tips % refund % invoice %/% sary %/% cert %/% transit %',
      pending_tasks, done_without_ms, salary_n, salary_sum, change_sum, tips_sum, refund_sum,
      invoice_work, invoice_test, sary_pending, sary_work, cert_loose, cert_work, in_transit;
  end if;
end $$;

create temp table cleaned_tasks (id uuid, prev_status text);

with pending as (
  update tasks
  set status = 'cancelled',
      completed_by = coalesce(completed_by, 'Касса (чистка)'),
      completed_at = coalesce(completed_at, now()),
      idempotency_key = case
        when idempotency_key is null then null
        when idempotency_key like '%:cleanup-2026-11-25' then idempotency_key
        else idempotency_key || ':cleanup-2026-11-25'
      end
  where status = 'pending'
  returning id
)
insert into cleaned_tasks (id, prev_status)
select id, 'pending' from pending;

with shipped as (
  update tasks t
  set status = 'cancelled',
      completed_by = coalesce(t.completed_by, 'Касса (чистка)'),
      completed_at = coalesce(t.completed_at, now()),
      idempotency_key = case
        when t.idempotency_key is null then null
        when t.idempotency_key like '%:cleanup-2026-11-25' then t.idempotency_key
        else t.idempotency_key || ':cleanup-2026-11-25'
      end
  from deals d
  where t.deal_number = d.number
    and t.kind = 'movement'
    and t.status = 'done'
    and not pg_temp.is_test_deal(d.data)
    and not exists (
      select 1 from tasks a
      where a.kind = 'movement_accept' and a.status = 'done'
        and coalesce(a.metadata->>'msMovementId', '') <> ''
        and a.idempotency_key = t.id::text || ':accept'
    )
  returning t.id
)
insert into cleaned_tasks (id, prev_status)
select id, 'done' from shipped;

insert into audit_log (actor_name, actor_role, action, entity_type, entity_id, source, before, after, metadata)
select 'Касса (чистка)', 'admin', 'task.cancelled', 'task', id::text, 'auto',
       jsonb_build_object('status', prev_status),
       jsonb_build_object('status', 'cancelled'),
       jsonb_build_object('reason', 'перед сменой 2026-11-25')
from cleaned_tasks;

create temp table cleaned_queue (id uuid, kind text);

with money as (
  update queue
  set status = 'cancelled',
      metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object('cleanup', '2026-11-25', 'reason', 'перед сменой')
  where status = 'pending' and kind in ('salary', 'change', 'tips', 'refund')
  returning id, kind
)
insert into cleaned_queue (id, kind)
select id, kind from money;

with test_invoice as (
  update queue q
  set status = 'cancelled',
      metadata = coalesce(q.metadata, '{}'::jsonb) || jsonb_build_object('cleanup', '2026-11-25', 'reason', 'тестовый счёт перед сменой')
  from deals d
  where q.deal_number = d.number
    and q.status = 'pending'
    and q.kind = 'invoice'
    and pg_temp.is_test_deal(d.data)
  returning q.id, q.kind
)
insert into cleaned_queue (id, kind)
select id, kind from test_invoice;

insert into audit_log (actor_name, actor_role, action, entity_type, entity_id, source, before, after, metadata)
select 'Касса (чистка)', 'admin', 'queue.cancelled', 'queue', id::text, 'auto',
       jsonb_build_object('status', 'pending', 'kind', kind),
       jsonb_build_object('status', 'cancelled'),
       jsonb_build_object('reason', 'перед сменой 2026-11-25')
from cleaned_queue;

create temp table cleaned_sary as
with upd as (
  update sary s
  set status = 'cancelled'
  from deals d
  where s.ref_deal_number = d.number
    and s.status = 'pending'
    and pg_temp.is_test_deal(d.data)
  returning s.id
)
select id from upd;

insert into audit_log (actor_name, actor_role, action, entity_type, entity_id, source, before, after, metadata)
select 'Касса (чистка)', 'admin', 'sary.cancelled', 'sary', id::text, 'auto',
       jsonb_build_object('status', 'pending'),
       jsonb_build_object('status', 'cancelled'),
       jsonb_build_object('reason', 'перед сменой 2026-11-25')
from cleaned_sary;

create temp table cleaned_certs as
with upd as (
  update certificates c
  set status = 'cancelled'
  where c.status = 'active'
    and (
      c.buyer_deal_number is null
      or exists (
        select 1 from deals d
        where d.number = c.buyer_deal_number and pg_temp.is_test_deal(d.data)
      )
    )
  returning c.number
)
select number from upd;

insert into audit_log (actor_name, actor_role, action, entity_type, entity_id, source, before, after, metadata)
select 'Касса (чистка)', 'admin', 'certificate.blocked', 'certificate', number, 'auto',
       jsonb_build_object('status', 'active'),
       jsonb_build_object('status', 'cancelled'),
       jsonb_build_object('reason', 'перед сменой 2026-11-25')
from cleaned_certs;

update deal_item_state s
set state = 'in_store',
    location = coalesce(nullif(d.data->>'store', ''), 'в магазине'),
    updated_at = now()
from deals d
where s.deal_number = d.number
  and s.state = 'in_transit'
  and d.stage = 'Товар отложен'
  and not pg_temp.is_test_deal(d.data)
  and coalesce(s.metadata->>'msMovementId', '') = '';

do $$
declare
  open_mismatch integer;
  deal_changed integer;
  pending_tasks integer;
  done_without_ms integer;
  money_pending integer;
  invoice_work integer;
  invoice_test integer;
  sary_pending integer;
  sary_work integer;
  cert_loose integer;
  cert_work integer;
  in_transit integer;
  task_n integer;
  queue_n integer;
  sary_n integer;
  cert_n integer;
begin
  select count(*) into open_mismatch
  from expected_open e
  full join (
    select d.number, d.stage, d.data->>'paid' as paid, d.data->>'total' as total
    from deals d
    where not pg_temp.is_test_deal(d.data)
      and d.stage not in ('Успех', 'Провал', 'Не слив')
  ) live on live.number = e.number
    and live.stage = e.stage
    and live.paid = e.paid
    and live.total = e.total
  where live.number is null or e.number is null;

  select count(*) into deal_changed
  from deals_before b
  join deals d on d.number = b.number
  where d.stage is distinct from b.stage
     or d.amo_lead_id is distinct from b.amo_lead_id
     or d.ms_order_id is distinct from b.ms_order_id
     or d.ms_demand_id is distinct from b.ms_demand_id
     or md5(d.data::text) is distinct from b.data_hash;

  select count(*) into pending_tasks from tasks where status = 'pending';
  select count(*) into done_without_ms
  from tasks t
  join deals d on d.number = t.deal_number
  where t.kind = 'movement' and t.status = 'done' and not pg_temp.is_test_deal(d.data)
    and not exists (
      select 1 from tasks a
      where a.kind = 'movement_accept' and a.status = 'done'
        and coalesce(a.metadata->>'msMovementId', '') <> ''
        and a.idempotency_key = t.id::text || ':accept'
    );
  select count(*) into money_pending
  from queue where status = 'pending' and kind in ('salary', 'change', 'tips', 'refund');
  select count(*) into invoice_work
  from queue q join deals d on d.number = q.deal_number
  where q.status = 'pending' and q.kind = 'invoice' and not pg_temp.is_test_deal(d.data);
  select count(*) into invoice_test
  from queue q join deals d on d.number = q.deal_number
  where q.status = 'pending' and q.kind = 'invoice' and pg_temp.is_test_deal(d.data);
  select count(*) into sary_pending
  from sary s join deals d on d.number = s.ref_deal_number
  where s.status = 'pending' and pg_temp.is_test_deal(d.data);
  select count(*) into sary_work
  from sary s join deals d on d.number = s.ref_deal_number
  where s.status = 'in_check' and not pg_temp.is_test_deal(d.data);
  select count(*) into cert_loose
  from certificates c
  where c.status = 'active'
    and (
      c.buyer_deal_number is null
      or exists (select 1 from deals d where d.number = c.buyer_deal_number and pg_temp.is_test_deal(d.data))
    );
  select count(*) into cert_work
  from certificates c
  where c.status = 'active'
    and c.buyer_deal_number is not null
    and exists (select 1 from deals d where d.number = c.buyer_deal_number and not pg_temp.is_test_deal(d.data));
  select count(*) into in_transit
  from deal_item_state s
  join deals d on d.number = s.deal_number
  where s.state = 'in_transit' and d.stage = 'Товар отложен' and not pg_temp.is_test_deal(d.data);
  select count(*) into task_n from cleaned_tasks;
  select count(*) into queue_n from cleaned_queue;
  select count(*) into sary_n from cleaned_sary;
  select count(*) into cert_n from cleaned_certs;

  if open_mismatch <> 0 or deal_changed <> 0
    or pending_tasks <> 0 or done_without_ms <> 0 or money_pending <> 0
    or invoice_work <> 3 or invoice_test <> 0
    or sary_pending <> 0 or sary_work <> 3
    or cert_loose <> 0 or cert_work <> 2
    or in_transit <> 0
    or task_n <> 33 or queue_n <> 9 or sary_n <> 1 or cert_n <> 5
  then
    raise exception
      'проверка после чистки не сошлась: deals %/% tasks %/% money % invoice %/% sary %/% cert %/% transit % rows %/%/%/%',
      open_mismatch, deal_changed, pending_tasks, done_without_ms, money_pending,
      invoice_work, invoice_test, sary_pending, sary_work, cert_loose, cert_work, in_transit,
      task_n, queue_n, sary_n, cert_n;
  end if;
end $$;

select number, stage, data->>'paid' as paid, data->>'total' as total
from deals
where number in (select number from expected_open)
order by number;

commit;
