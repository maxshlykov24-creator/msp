"""
Keris Club — снятие актуального состояния amoCRM.
Читает воронки + этапы + кастомные поля контактов и сделок,
печатает в формате, удобном для сверки с ДОСТУПЫ.md.
"""

import json
import urllib.request
import urllib.error

TOKEN = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImp0aSI6Ijk0NzNjN2UzYzczNzdjYzYwOTZlNzM0MzhlNGUwOTgzYTVmNjNjM2E2MGQ1MWI1MmRlNTM0YmJjMzRkYjU4YWQ1ZGY0NWEzNzVlZTJhYjNmIn0"
    ".eyJhdWQiOiJhYjU3ZjJhYy05M2Y3LTRhYzEtOTI0Mi0yY2VmMDZmZjhjZmEiLCJqdGkiOiI5NDczYzdlM2M3Mzc3Y2M2MDk2ZTczNDM4ZTRlMDk4M2E1ZjYzYzNhNjBkNTFiNTJkZTUzNGJiYzM0ZGI1OGFkNWRmNDVhMzc1ZWUyYWIzZiIsImlhdCI6MTc4MjI0NjIzNCwibmJmIjoxNzgyMjQ2MjM0LCJleHAiOjE4NjQ1MTIwMDAsInN1YiI6Ijk0OTA1MzAiLCJncmFudF90eXBlIjoiIiwiYWNjb3VudF9pZCI6MzMxMTcxNTAsImJhc2VfZG9tYWluIjoiYW1vY3JtLnJ1IiwidmVyc2lvbiI6Miwic2NvcGVzIjpbInB1c2hfbm90aWZpY2F0aW9ucyIsImZpbGVzIiwiY3JtIiwiZmlsZXNfZGVsZXRlIiwibm90aWZpY2F0aW9ucyJdLCJoYXNoX3V1aWQiOiJkNzhjNjlkOC1jMjA0LTQ1MmEtOWMxYS03MTRlNzQwMmM3NDciLCJhcGlfZG9tYWluIjoiYXBpLWIuYW1vY3JtLnJ1In0"
    ".KOFMQ9Qy7elwGPClf8PILvWx3YoIACUQROxE2u0ptABiZnL70Nqvmasa5mHIRnFxkemec2I4AWsBjBpAG-wWBkDWOFVYedLfFV-hXR27MOgadeLaTFC0XoSVSFcOyXyL7sNdxBQlJxuj4egGEEHcy7ruKiMp60Oij_5QTnnfUxalSSYr_f4GOzKYc2Q85D-k9mokPZjZ6F70oHrgipaKTq-fOvAYxfCiHfZK74_K9L2wHq_abom-C8_Hh4wZXvzIcR0dHF5TTLO8Z1Q7cdnrndsrbyvBM2sZPGUB-PQxXXQiZQo7W67ih7OJdBqtlE1PDNiqqkMiZiToKdmE4wgwEQ"
)
BASE = "https://kerisclub.amocrm.ru"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def get(path):
    r = urllib.request.Request(f"{BASE}{path}", headers=HEADERS, method="GET")
    try:
        with urllib.request.urlopen(r) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": raw.decode(errors="replace")}


print("=" * 60)
print("ВОРОНКИ И ЭТАПЫ")
print("=" * 60)
s, b = get("/api/v4/leads/pipelines")
if 200 <= s < 300:
    for p in b.get("_embedded", {}).get("pipelines", []):
        print(f"\n### {p['name']}  (ID {p['id']})  is_main={p.get('is_main')}")
        statuses = p.get("_embedded", {}).get("statuses", [])
        statuses.sort(key=lambda x: x.get("sort", 0))
        for st in statuses:
            print(f"  - {st['name']:30} ID {st['id']:>10}  sort={st.get('sort'):>4}  type={st.get('type')}  color={st.get('color')}")
else:
    print(f"ERROR {s}: {b}")


def dump_fields(entity, title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)
    page = 1
    while True:
        s, b = get(f"/api/v4/{entity}/custom_fields?page={page}&limit=50")
        if not (200 <= s < 300):
            print(f"ERROR {s}: {b}")
            return
        fields = b.get("_embedded", {}).get("custom_fields", [])
        if not fields:
            break
        for f in fields:
            line = f"  - {f['name']:35} ID {f['id']:>10}  type={f.get('type')}"
            print(line)
            enums = f.get("enums")
            if enums:
                vals = ", ".join(e.get("value", "") for e in enums)
                print(f"      enums: {vals}")
        if len(fields) < 50:
            break
        page += 1


dump_fields("contacts", "ПОЛЯ КОНТАКТОВ")
dump_fields("leads", "ПОЛЯ СДЕЛОК")
print("\nDONE")
