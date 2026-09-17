-- Сверка груминга на VPS. Запускать:
--   sudo -u postgres psql -d keris_grooming -f audit_bookings.sql
SELECT status, count(*) FROM bookings GROUP BY 1 ORDER BY 2 DESC;
SELECT count(*) FILTER (WHERE status = 'no_show') AS no_show_ever FROM bookings;
SELECT date_trunc('day', starts_at)::date AS day, count(*) 
FROM bookings
WHERE starts_at >= now() - interval '30 days' AND status NOT IN ('cancelled', 'no_show')
GROUP BY 1 ORDER BY 1;
SELECT master_id, count(*) AS visits, sum(price) AS revenue
FROM bookings
WHERE starts_at >= now() - interval '30 days'
  AND status NOT IN ('cancelled', 'no_show')
  AND starts_at <= now()
GROUP BY 1 ORDER BY visits DESC;
