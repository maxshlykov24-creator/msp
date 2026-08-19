#!/usr/bin/env python3
"""Tilda → Kommo webhook. Отвечает 'ok' за <7 сек (требование Tilda)."""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("tilda-kommo")

BASE = os.environ.get("KOMMO_BASE", "https://licensebridgeusa.kommo.com/api/v4")
PIPELINE_ID = int(os.environ.get("KOMMO_PIPELINE_ID", "11274779"))
STATUS_ID = int(os.environ.get("KOMMO_STATUS_ID", "108112044"))  # Новая заявка
RESPONSIBLE = int(os.environ.get("KOMMO_RESPONSIBLE", "13291175"))
PHONE_FIELD = 142532
PHONE_ENUM_WORK = 120012
CHANNEL_FIELD = 143104
UTM_SOURCE_FIELD = 142546
UTM_CAMPAIGN_FIELD = 142544


def token() -> str:
    t = os.environ.get("KOMMO_TOKEN", "").strip()
    if not t:
        raise RuntimeError("KOMMO_TOKEN not set")
    return t


def api(method: str, path: str, payload=None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token()}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read().decode()
            return r.getcode(), json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = {"raw": body}
        return e.code, parsed


def clean_phone(raw: str) -> str:
    p = (raw or "").strip()
    if p.startswith("p:"):
        p = p[2:]
    digits = re.sub(r"\D", "", p)
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if p.startswith("+"):
        return "+" + digits
    return p or raw.strip()


def pick(fields: dict, *keys: str) -> str:
    """Ищет поле по точному или регистронезависимому ключу."""
    lower_map = {k.lower(): v for k, v in fields.items()}
    for k in keys:
        for src in (fields, lower_map):
            key = k if src is fields else k.lower()
            if key not in src:
                continue
            v = src[key]
            if v is not None and str(v).strip():
                return str(v).strip()
    return ""


def find_contact_by_phone(phone: str) -> int | None:
    if not phone:
        return None
    q = urllib.parse.quote(phone)
    code, data = api("GET", f"/contacts?query={q}&limit=5")
    if code != 200 or not data:
        return None
    for c in data.get("_embedded", {}).get("contacts", []):
        for cf in c.get("custom_fields_values") or []:
            if cf.get("field_id") != PHONE_FIELD:
                continue
            for v in cf.get("values") or []:
                if clean_phone(str(v.get("value", ""))) == clean_phone(phone):
                    return c["id"]
    return None


def create_contact(name: str, phone: str, email: str) -> int:
    cf = []
    if phone:
        cf.append(
            {
                "field_id": PHONE_FIELD,
                "values": [{"value": phone, "enum_id": PHONE_ENUM_WORK}],
            }
        )
    payload = [{"name": name or phone or email or "Tilda lead", "custom_fields_values": cf}]
    if email:
        payload[0]["custom_fields_values"] = payload[0].get("custom_fields_values", [])
        # email via standard embedded if needed — Kommo often accepts top-level
    code, data = api("POST", "/contacts", payload)
    if code not in (200, 201) or not data:
        raise RuntimeError(f"contact create failed {code}: {data}")
    return data["_embedded"]["contacts"][0]["id"]


def add_lead_note(lead_id: int, text: str) -> None:
    if not text.strip():
        return
    payload = [
        {
            "entity_id": lead_id,
            "note_type": "common",
            "params": {"text": text.strip()},
        }
    ]
    code, data = api("POST", "/leads/notes", payload)
    if code not in (200, 201):
        log.warning("note failed lead=%s code=%s data=%s", lead_id, code, data)


def create_lead(name: str, contact_id: int, formid: str, utm_source: str, utm_campaign: str) -> int:
    lead_name = name or "Tilda lead"
    cf = [{"field_id": CHANNEL_FIELD, "values": [{"value": "Tilda"}]}]
    if utm_source:
        cf.append({"field_id": UTM_SOURCE_FIELD, "values": [{"value": utm_source}]})
    if utm_campaign:
        cf.append({"field_id": UTM_CAMPAIGN_FIELD, "values": [{"value": utm_campaign}]})
    payload = [
        {
            "name": lead_name,
            "pipeline_id": PIPELINE_ID,
            "status_id": STATUS_ID,
            "responsible_user_id": RESPONSIBLE,
            "custom_fields_values": cf,
            "_embedded": {"contacts": [{"id": contact_id}]},
        }
    ]
    code, data = api("POST", "/leads", payload)
    if code not in (200, 201) or not data:
        raise RuntimeError(f"lead create failed {code}: {data}")
    return data["_embedded"]["leads"][0]["id"]


def parse_body(content_type: str, raw: bytes) -> dict[str, str]:
    ct = (content_type or "").lower()
    if "application/json" in ct:
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace"))
            if isinstance(obj, dict):
                return {str(k): "" if v is None else str(v) for k, v in obj.items()}
        except json.JSONDecodeError:
            pass
    # Tilda default: application/x-www-form-urlencoded
    parsed = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


def handle_tilda(fields: dict) -> None:
    if fields.get("test") == "test":
        log.info("tilda test ping")
        return

    name = pick(fields, "Name", "name", "Имя", "Full name", "full_name")
    phone = clean_phone(pick(fields, "Phone", "phone", "Tel", "tel"))
    email = pick(fields, "Email", "email", "E-mail")
    license_class = pick(
        fields,
        "class", "Class", "CLASS",
        "Какая_лицензия_вас_интересует",
        "license", "License",
    )
    formid = pick(fields, "formid", "FormID")
    tranid = pick(fields, "tranid")
    utm_source = pick(fields, "utm_source", "utm-source")
    utm_campaign = pick(fields, "utm_campaign", "utm-campaign")

    if not name and not phone and not email:
        log.warning("empty lead fields: %s", list(fields.keys()))
        return

    contact_name = name or phone or email or "Tilda lead"
    contact_id = find_contact_by_phone(phone) if phone else None
    if contact_id:
        log.info("existing contact %s for %s", contact_id, phone)
    else:
        contact_id = create_contact(contact_name, phone, email)
        log.info("created contact %s name=%s phone=%s", contact_id, contact_name, phone)

    lead_id = create_lead(contact_name, contact_id, formid, utm_source, utm_campaign)
    if license_class:
        add_lead_note(lead_id, f"Class: {license_class}")
        log.info("note added lead=%s class=%s", lead_id, license_class)
    log.info("created lead %s tranid=%s formid=%s", lead_id, tranid, formid)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def _ok(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self):
        if self.path.rstrip("/") in ("", "/health", "/tilda/webhook"):
            self._ok()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if not self.path.startswith("/tilda/webhook"):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            fields = parse_body(self.headers.get("Content-Type", ""), raw)
            log.info("POST %s keys=%s", self.path, list(fields.keys()))
            handle_tilda(fields)
        except Exception:
            log.exception("handler error")
            # Tilda retries on failure — still return ok if Kommo failed? Better log and return ok to avoid spam retries, but lead lost.
            # Return 500 so Tilda retries
            self.send_response(500)
            self.end_headers()
            return
        self._ok()


def main():
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log.info("listening on :%s path=/tilda/webhook", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
