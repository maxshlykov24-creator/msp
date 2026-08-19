#!/bin/bash
# Van Pak night pipeline — run from scripts/
set -euo pipefail
cd "$(dirname "$0")"
PY=python3
LOG=night_pipeline.log
exec > >(tee -a "$LOG") 2>&1

export OLD_TOKEN="${OLD_TOKEN:-e95af8f2c93487a215f754ea1e5469e281613d61}"

echo "=== $(date -Iseconds) START night pipeline ==="
echo "OLD token ...${OLD_TOKEN: -8}"

# Skip redundant monthly pre-runs — one full audit at end (rate-limit safe)
$PY audit_doc_pairs.py --from-date 2026-01-01 --to-date 2026-06-30

echo "--- audit_doc_agent ---"
$PY audit_doc_agent.py

echo "--- sync agent may-jun dry/live ---"
$PY sync_doc_agent_from_old.py --dry --from-date 2026-05-01 --to-date 2026-06-30
$PY sync_doc_agent_from_old.py --from-date 2026-05-01 --to-date 2026-06-30
$PY sync_order_fields_from_old.py --dry --from-date 2026-05-01 --to-date 2026-06-30
$PY sync_order_fields_from_old.py --from-date 2026-05-01 --to-date 2026-06-30

$PY audit_doc_agent.py --from-date 2026-05-01 --to-date 2026-06-30

echo "--- sync agent full 2026 ---"
$PY sync_doc_agent_from_old.py --dry
$PY sync_doc_agent_from_old.py
$PY sync_order_fields_from_old.py --dry
$PY sync_order_fields_from_old.py

$PY audit_doc_agent.py --final

echo "--- payedSum/shippedSum tails ---"
$PY sync_demand_links.py --dry --from-date "2026-01-01 00:00:00" --to-date "2026-06-30 23:59:59"
$PY sync_demand_links.py --from-date "2026-01-01 00:00:00" --to-date "2026-06-30 23:59:59"
$PY fix_payment_links.py --dry || true
$PY fix_payment_links.py || true

echo "--- catchup 2026-06-04 ---"
$PY catchup_today.py --dry --date 2026-06-04
$PY catchup_today.py --dry --date 2026-06-04 --link-payments
$PY catchup_today.py --date 2026-06-04
$PY catchup_today.py --date 2026-06-04 --link-payments

echo "--- responsible + shipmentAddress (период catchup) ---"
$PY sync_responsible.py --from-date 2026-06-01 --to-date 2026-06-30
$PY sync_shipment_address_from_old.py --from-date 2026-06-01

echo "--- stock ---"
$PY audit_stock.py
$PY reconcile_stock.py --dry
$PY reconcile_stock.py
$PY audit_stock.py --final

echo "=== $(date -Iseconds) DONE ==="
