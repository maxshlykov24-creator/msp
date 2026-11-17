"""New funnels stay inert until explicitly allowed. Rechecked before delivery."""
import os

# Verified against the live catalog on 17 November 2026. This map does not
# enable pipelines: allowed_pipeline remains the independent rollout gate.
CERTIFICATE_STAGES = {9601214: 76675038, 11482373: 89900793, 11482377: 89900817}
MIGRATION_TAG = "MB_MIGRATION_IN_PROGRESS"


def migration_blocked(lead):
    """Shared CRM-side interlock; no expiry or automatic removal on restart."""
    return any(str(tag.get("name", "")).strip() == MIGRATION_TAG
               for tag in (lead.get("_embedded") or {}).get("tags", []))


def paid_certificate_scope(lead):
    stage = CERTIFICATE_STAGES.get(lead.get("pipeline_id"))
    return bool(stage and lead.get("status_id") in (stage, 142)
                and not lead.get("is_deleted") and messaging_allowed(lead))


def valid_certificate_rule_scope(pipelines, statuses):
    return bool(pipelines and len(pipelines) == len(set(pipelines))
                and set(pipelines) <= set(CERTIFICATE_STAGES)
                and set(statuses) == {CERTIFICATE_STAGES[p] for p in pipelines})

def allowed_pipeline(pipeline_id):
    values = os.getenv("MANSBAND_MESSAGE_PIPELINES", "9601214,9601246,10730010")
    try:
        allowed = {int(value.strip()) for value in values.split(",") if value.strip()}
        return int(pipeline_id) in allowed
    except (TypeError, ValueError):
        return False

def messaging_allowed(lead):
    if not allowed_pipeline(lead.get("pipeline_id")) or lead.get("isTest") or migration_blocked(lead):
        return False
    tags = lead.get("_embedded", {}).get("tags", [])
    if any(str(tag.get("name", "")).strip().upper() == "ТЕСТ НЕ ОТПРАВЛЯТЬ" for tag in tags):
        return False
    # The agreed no-sale workflow must not inherit failure messages.
    for field in lead.get("custom_fields_values") or []:
        if field.get("field_id") == 3831526:
            if any(value.get("value") == "Не слив" for value in field.get("values") or []):
                return False
    return True
