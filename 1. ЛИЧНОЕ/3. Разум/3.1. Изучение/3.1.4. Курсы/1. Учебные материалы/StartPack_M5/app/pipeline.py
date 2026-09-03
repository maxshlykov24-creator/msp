"""Оркестрация: вебхук → запись → транскрипт → (опц.) оценка → Telegram → (опц.) обратно в CRM.

Запускается в фоне (BackgroundTask): ASR долгий, в самом вебхуке его делать нельзя.
"""
import logging
from pathlib import Path

import amocrm
import asr
import bitrix
import crm
import format as fmt
import telegram
from config import settings

log = logging.getLogger("pipeline")


def _read_context() -> str:
    p = Path(settings.company_context_path)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _writeback(source: str, meta: dict, text: str) -> None:
    if not settings.crm_writeback or not meta.get("deal"):
        return
    try:
        if source == "amo":
            amocrm.add_note(int(meta["deal"]), text)
        else:
            bitrix.add_timeline_comment(int(meta["deal"]), text)
    except Exception as e:                       # запись в CRM не критична — не роняем пайплайн
        log.warning("[%s] writeback не удался: %s", source, e)


def process(source: str, payload: dict) -> None:
    try:
        audio_url, meta = crm.extract(source, payload)
        if not audio_url:
            log.warning("[%s] ссылка на запись не найдена (возможно, ещё не готова)", source)
            return

        text, diarized, conf = asr.transcribe(audio_url)
        message = fmt.render_transcript(text, diarized, meta)

        if settings.llm_enabled:
            try:
                import llm
                ev = llm.evaluate(text, _read_context())
                message += "\n" + fmt.render_eval(ev)
            except Exception as e:               # оценка опциональна — транскрипт всё равно отдаём
                log.warning("[%s] оценка не удалась: %s", source, e)

        telegram.send(message)
        _writeback(source, meta, message)
        log.info("[%s] звонок обработан (conf=%s)", source, conf)
    except Exception:
        log.exception("[%s] ошибка обработки звонка", source)
