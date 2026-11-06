"""New funnels stay inert until explicitly allowed. Rechecked before delivery."""
import os

def allowed_pipeline(pipeline_id):
    values = os.getenv("MANSBAND_MESSAGE_PIPELINES", "9601214,9601246,10730010")
    try:
        allowed = {int(value.strip()) for value in values.split(",") if value.strip()}
        return int(pipeline_id) in allowed
    except (TypeError, ValueError):
        return False

def messaging_allowed(lead):
    if not allowed_pipeline(lead.get("pipeline_id")) or lead.get("isTest"):
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
