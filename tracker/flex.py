"""Скачивание отчёта через IBKR Flex Web Service (v3).

Схема: SendRequest(token, queryId) -> ReferenceCode -> GetStatement(token, ReferenceCode).
Отчёт формируется на стороне IBKR несколько секунд, поэтому GetStatement опрашивается с паузой.
Токен никогда не выводится в лог.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import requests

BASE_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
HEADERS = {"User-Agent": "portfolio-tracker/1.0 (python-requests)"}

# Коды, при которых отчёт ещё не готов или сервис временно занят: ждём и повторяем
RETRY_CODES = {"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1019", "1021"}


class FlexError(RuntimeError):
    pass


def _parse_status(content: bytes) -> tuple[str, str, str]:
    root = ET.fromstring(content)
    status = (root.findtext("Status") or "").strip()
    code = (root.findtext("ErrorCode") or "").strip()
    msg = (root.findtext("ErrorMessage") or "").strip()
    return status, code, msg


def fetch_statement(token: str, query_id: str, *, max_wait_s: int = 300, poll_s: int = 10) -> bytes:
    if not token or not query_id:
        raise FlexError("Не заданы IBKR_FLEX_TOKEN и/или IBKR_FLEX_QUERY_ID")

    # 1) Запрос на формирование отчёта (с повторами, если сервис занят)
    deadline = time.time() + max_wait_s
    while True:
        r = requests.get(f"{BASE_URL}/SendRequest", params={"t": token, "q": query_id, "v": "3"},
                         headers=HEADERS, timeout=60)
        r.raise_for_status()
        status, code, msg = _parse_status(r.content)
        if status == "Success":
            root = ET.fromstring(r.content)
            ref = (root.findtext("ReferenceCode") or "").strip()
            url = (root.findtext("Url") or f"{BASE_URL}/GetStatement").strip()
            break
        if code in RETRY_CODES and time.time() < deadline:
            time.sleep(poll_s)
            continue
        raise FlexError(f"SendRequest: код {code}: {msg}")

    # 2) Получение готового отчёта
    while True:
        r = requests.get(url, params={"t": token, "q": ref, "v": "3"}, headers=HEADERS, timeout=120)
        r.raise_for_status()
        head = r.content[:200].lstrip()
        if head.startswith(b"<FlexQueryResponse") or b"<FlexQueryResponse" in head:
            return r.content
        status, code, msg = _parse_status(r.content)
        if code in RETRY_CODES and time.time() < deadline:
            time.sleep(poll_s)
            continue
        raise FlexError(f"GetStatement: код {code}: {msg}")
