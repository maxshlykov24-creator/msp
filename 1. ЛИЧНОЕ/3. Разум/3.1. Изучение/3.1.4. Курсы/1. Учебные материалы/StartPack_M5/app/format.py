"""Сборка человекочитаемого сообщения для Telegram: шапка + транскрипт по спикерам + (опц.) оценка."""
from html import escape


def _hms(ms) -> str:
    try:
        s = int((ms or 0) / 1000)
    except (TypeError, ValueError):
        return "0:00"
    return f"{s // 60}:{s % 60:02d}"


def _dur(seconds) -> str:
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        return "—"
    return f"{s // 60} мин {s % 60} сек" if s else "—"


def render_transcript(text: str, diarized: list, meta: dict) -> str:
    """meta: {source, phone, duration, when, deal}. diarized: список реплик по спикерам."""
    head = ["📞 <b>Звонок расшифрован</b>"]
    if meta.get("source"):
        head.append(f"Источник: {escape(str(meta['source']))}")
    if meta.get("phone"):
        head.append(f"Клиент: {escape(str(meta['phone']))}")
    if meta.get("duration"):
        head.append(f"Длительность: {_dur(meta['duration'])}")
    if meta.get("deal"):
        head.append(f"Сделка: {escape(str(meta['deal']))}")

    body = []
    if diarized:
        for u in diarized:
            spk = u.get("speaker") or "?"
            body.append(f"<b>Спикер {escape(str(spk))}</b> [{_hms(u.get('t0'))}]: {escape(u.get('text') or '')}")
    else:
        body.append(escape(text or "(пусто)"))

    return "\n".join(head) + "\n\n" + "\n".join(body)


def render_eval(ev: dict) -> str:
    """Оценка от LLM — показываем компактно. Структура гибкая (зависит от пресета),
    поэтому достаём только знакомые ключи, остальное — как есть."""
    if not isinstance(ev, dict):
        return ""
    lines = ["", "🧮 <b>Оценка звонка</b>"]

    score = ev.get("оценка_1_10") or ev.get("итоговая_оценка") or ev.get("total_score")
    if score is not None:
        mx = ev.get("максимум") or ev.get("max")
        lines.append(f"Балл: <b>{escape(str(score))}{'/' + str(mx) if mx else ''}</b>")
    if ev.get("статус"):
        lines.append(f"Статус: {escape(str(ev['статус']))}")
    if ev.get("следующий_шаг"):
        lines.append(f"Следующий шаг: {escape(str(ev['следующий_шаг']))}")

    flags = ev.get("красные_флаги") or []
    if flags:
        lines.append("🚩 Красные флаги:")
        lines += [f"• {escape(str(f))}" for f in flags[:5]]

    fb = ev.get("обратная_связь_менеджеру") or ev.get("комментарий_аудитора")
    if fb:
        lines.append(f"💬 {escape(str(fb))}")
    return "\n".join(lines)
