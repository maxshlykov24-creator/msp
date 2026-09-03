"""Транскрибация + диаризация через AssemblyAI.

Схема: скачиваем запись напрямую (РФ-IP) → загружаем файл в AssemblyAI → опрашиваем
до готовности. Возвращаем (текст, реплики_по_спикерам, уверенность).
"""
import time

from config import settings
from net import direct_client, proxied_client

AAI = "https://api.assemblyai.com/v2"

# Слова, которые ASR коверкает (названия продуктов, бренды, термины вашей ниши).
# Подставьте свои — это заметно повышает точность. Пример:
# KEYTERMS = ["НазваниеПродукта", "рассрочка", "тариф Премиум", "ИвановИванИваныч"]
KEYTERMS: list[str] = []


def _download_recording(audio_url: str) -> bytes:
    """Скачать запись звонка с этого сервера НАПРЯМУЮ (важно для гео-ограниченных записей РФ)."""
    with direct_client(timeout=90) as dl:
        r = dl.get(audio_url, follow_redirects=True)
        r.raise_for_status()
        return r.content


def transcribe(audio_url: str) -> tuple[str, list, float | None]:
    if not settings.assemblyai_api_key:
        raise RuntimeError("ASSEMBLYAI_API_KEY не задан в .env")
    headers = {"authorization": settings.assemblyai_api_key}

    audio = _download_recording(audio_url)                       # 1) скачали запись (напрямую)
    with proxied_client(timeout=180) as cli:                     # 2-3) AssemblyAI — через прокси, если задан
        up = cli.post(f"{AAI}/upload",
                      headers={**headers, "content-type": "application/octet-stream"},
                      content=audio)
        up.raise_for_status()
        upload_url = up.json()["upload_url"]

        params = {
            "audio_url": upload_url,
            "speaker_labels": True,                # диаризация: разделяем говорящих (менеджер/клиент)
            "language_code": settings.asr_language,
            "punctuate": True,
            "format_text": True,
            "speech_models": ["universal-3-pro"],  # обязательный параметр текущего API AssemblyAI
        }
        if KEYTERMS:
            params["keyterms_prompt"] = KEYTERMS
        r = cli.post(f"{AAI}/transcript", headers=headers, json=params)
        r.raise_for_status()
        tid = r.json()["id"]

        for _ in range(120):                                     # поллинг до завершения (~6 мин макс)
            g = cli.get(f"{AAI}/transcript/{tid}", headers=headers)
            g.raise_for_status()
            data = g.json()
            if data["status"] == "completed":
                diarized = [
                    {"speaker": u.get("speaker"), "t0": u.get("start"),
                     "t1": u.get("end"), "text": u.get("text")}
                    for u in (data.get("utterances") or [])
                ]
                return data.get("text", ""), diarized, data.get("confidence")
            if data["status"] == "error":
                raise RuntimeError(f"AssemblyAI error: {data.get('error')}")
            time.sleep(3)
    raise TimeoutError("AssemblyAI: транскрипт не готов за отведённое время")
