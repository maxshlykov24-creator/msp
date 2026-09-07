"""Маршрутизация исходящих запросов.

proxied_client — через exit-node (если задан HTTPS_PROXY): зарубежные API (AssemblyAI, Telegram).
                 Если прокси не задан — работает как обычный клиент (прямое соединение).
direct_client  — всегда НАПРЯМУЮ, мимо прокси: российские эндпоинты (Bitrix, AmoCRM) и
                 скачивание записей телефонии (часто гео-ограничены РФ).
"""
import httpx

from config import settings


def proxied_client(timeout: float = 120.0, **kw) -> httpx.Client:
    proxy = settings.https_proxy or None
    return httpx.Client(proxy=proxy, timeout=timeout, **kw)


def direct_client(timeout: float = 60.0, **kw) -> httpx.Client:
    # trust_env=False: не подхватывать HTTPS_PROXY из окружения.
    # Записи звонков (UIS/Мегафон и др.) часто доступны только с российского IP —
    # тянем их сами, с того же сервера, без зарубежного прокси.
    return httpx.Client(timeout=timeout, trust_env=False, **kw)
