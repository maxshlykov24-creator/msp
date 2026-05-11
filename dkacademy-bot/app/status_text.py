STATUS_LABEL_RU = {
    "0": "В пути",
    "1": "В пункте выдачи",
    "2": "Вручен получателю",
    "3": "Возврат отправителю",
}


def format_liveinform_crm_line(result_dict: dict) -> str:
    code = str(result_dict.get("status") or "").strip()
    label = STATUS_LABEL_RU.get(code, f"Статус {code}")

    tl = result_dict.get("track")
    detail: str = ""
    if isinstance(tl, list) and tl:
        first = tl[0]
        if isinstance(first, dict):
            tx = (first.get("text") or "").strip()
            chk = (first.get("checkdate") or first.get("date") or "").strip()
            if chk and tx:
                detail = f"{chk} — {tx}"
            elif tx:
                detail = tx
            elif chk:
                detail = chk

    if detail:
        return f"{label} ({detail})"
    return label


def telegram_line_from_track(
    template: str, *, tracking: str, status_text: str, delivery: str = ""
) -> str:
    class _M(dict):
        def __missing__(self, _k: str) -> str:
            return ""

    return template.format_map(
        _M(tracking=tracking, status_text=status_text, delivery=delivery or "")
    )
