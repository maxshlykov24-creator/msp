"""Клиент МойСклад API (create variant / enter)."""
from __future__ import annotations

import gzip
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_TRANSIENT = (
    urllib.error.URLError,
    TimeoutError,
    OSError,
    socket.timeout,
    ssl.SSLError,
)

from config import MOYSKLAD_ENV, MS_API, ORG_NAME, STORE_NAME


def load_token(path: Path = MOYSKLAD_ENV) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Нет {path}. Создай файл с строкой MOYSKLAD_TOKEN=..."
        )
    token = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("MOYSKLAD_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not token:
        raise RuntimeError("MOYSKLAD_TOKEN пуст")
    return token


class MoySklad:
    def __init__(self, token: Optional[str] = None):
        self.token = token or load_token()
        self._char_meta: Optional[Dict[str, Dict[str, Any]]] = None
        self._cristal_seq = int(time.time()) % 10_000_000

    def _req(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Any = None,
        timeout: int = 60,
        retries: int = 5,
    ) -> Any:
        url = f"{MS_API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json",
        }
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        last_err: Optional[BaseException] = None
        for attempt in range(1, retries + 1):
            req = urllib.request.Request(
                url, data=data, headers=headers, method=method
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    if (
                        resp.headers.get("Content-Encoding") == "gzip"
                        or raw[:2] == b"\x1f\x8b"
                    ):
                        raw = gzip.decompress(raw)
                    text = raw.decode("utf-8")
                    return json.loads(text) if text else {}
            except urllib.error.HTTPError as e:
                # MS иногда отдаёт 308 Permanent Redirect — urllib не следует за ним на POST/PUT
                if e.code in (301, 302, 307, 308) and e.headers.get("Location"):
                    loc = e.headers["Location"]
                    req2 = urllib.request.Request(
                        loc, data=data, headers=headers, method=method
                    )
                    try:
                        with urllib.request.urlopen(req2, timeout=timeout) as resp:
                            raw = resp.read()
                            if (
                                resp.headers.get("Content-Encoding") == "gzip"
                                or raw[:2] == b"\x1f\x8b"
                            ):
                                raw = gzip.decompress(raw)
                            text = raw.decode("utf-8")
                            return json.loads(text) if text else {}
                    except _TRANSIENT as e2:
                        last_err = e2
                        if attempt < retries:
                            print(
                                f"  MS retry {attempt}/{retries} redirect: {e2}",
                                flush=True,
                            )
                            time.sleep(min(2 ** attempt, 30))
                            continue
                        raise
                # 429 / 5xx — повторить
                if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                    last_err = e
                    print(f"  MS retry {attempt}/{retries} HTTP {e.code}", flush=True)
                    time.sleep(min(2 ** attempt, 30))
                    continue
                err_raw = e.read()
                try:
                    if err_raw[:2] == b"\x1f\x8b":
                        err_raw = gzip.decompress(err_raw)
                    err = err_raw.decode("utf-8", errors="replace")
                except Exception:
                    err = repr(err_raw[:200])
                raise RuntimeError(
                    f"MS {method} {path} → {e.code}: {err[:800]}"
                ) from e
            except _TRANSIENT as e:
                last_err = e
                if attempt < retries:
                    print(f"  MS retry {attempt}/{retries}: {e}", flush=True)
                    time.sleep(min(2 ** attempt, 30))
                    continue
                raise
        raise RuntimeError(f"MS {method} {path} failed after retries: {last_err}")

    def get(
        self, path: str, timeout: int = 90, retries: int = 5, **params
    ) -> Any:
        return self._req(
            "GET", path, params=params or None, timeout=timeout, retries=retries
        )

    def post(
        self, path: str, body: Any, timeout: int = 60, retries: int = 5
    ) -> Any:
        return self._req("POST", path, body=body, timeout=timeout, retries=retries)

    def batch_update_variants(
        self,
        items: List[Dict[str, Any]],
        chunk_size: int = 50,
        verify_field: str = "code",
    ) -> Tuple[int, List[str]]:
        """
        Массово обновить модификации через POST /entity/variant (массив).
        items: [{id, code?, characteristics?}, ...]
        characteristics — dict имя→значение (через metadata).
        """
        ok = 0
        errors: List[str] = []
        for i in range(0, len(items), chunk_size):
            chunk = items[i : i + chunk_size]
            body = []
            for it in chunk:
                row: Dict[str, Any] = {
                    "meta": {
                        "href": f"{MS_API}/entity/variant/{it['id']}",
                        "metadataHref": f"{MS_API}/entity/variant/metadata",
                        "type": "variant",
                        "mediaType": "application/json",
                    }
                }
                if "code" in it and it["code"] is not None:
                    row["code"] = it["code"]
                if it.get("characteristics"):
                    row["characteristics"] = self._chars_payload(it["characteristics"])
                if "description" in it:
                    row["description"] = it["description"]
                body.append(row)
            expected = [it.get(verify_field) for it in chunk]
            try:
                resp = self.post("/entity/variant", body, timeout=180)
                if isinstance(resp, list):
                    for row, want, it in zip(resp, expected, chunk):
                        got = row.get(verify_field)
                        if want is None or got == want:
                            ok += 1
                        else:
                            errors.append(
                                f"{it['id']}: {verify_field} want={want!r} got={got!r}"
                            )
                else:
                    errors.append(f"chunk@{i}: unexpected resp type {type(resp)}")
            except Exception as e:
                recovered = 0
                for it, want in zip(chunk, expected):
                    try:
                        got = (self.get(f"/entity/variant/{it['id']}") or {}).get(
                            verify_field
                        )
                        if want is None or got == want:
                            recovered += 1
                        else:
                            errors.append(
                                f"{it['id']}: after error {verify_field}={got!r} want={want!r}"
                            )
                    except Exception as e2:
                        errors.append(f"{it['id']}: {e} / verify {e2}")
                ok += recovered
                if recovered != len(chunk):
                    errors.append(f"chunk@{i}: {e} (recovered {recovered}/{len(chunk)})")
            time.sleep(0.05)
        return ok, errors

    def batch_update_variant_codes(
        self,
        items: List[Tuple[str, str]],
        chunk_size: int = 50,
    ) -> Tuple[int, List[str]]:
        """Обёртка: [(uuid, code)] → batch_update_variants."""
        payload = [{"id": uid, "code": code} for uid, code in items]
        return self.batch_update_variants(
            payload, chunk_size=chunk_size, verify_field="code"
        )

    def find_by_name(self, entity: str, name: str) -> Dict[str, Any]:
        data = self.get(f"/entity/{entity}", filter=f"name={name}", limit=5)
        rows = data.get("rows", [])
        exact = [r for r in rows if r.get("name") == name]
        if len(exact) == 1:
            return exact[0]
        if not exact and len(rows) == 1 and rows[0].get("name") == name:
            return rows[0]
        raise RuntimeError(f"Не однозначно {entity} name={name!r}: найдено {len(exact) or len(rows)}")

    def _variant_count(self, product_id: str) -> int:
        data = self.get(
            "/entity/variant",
            filter=f"productid={product_id}",
            limit=1,
        )
        return int((data.get("meta") or {}).get("size") or 0)

    def resolve_parent(self, name: str, preferred_uuid: str = "") -> Dict[str, Any]:
        """
        В МС бывают дубли товаров с одним name.
        Берём неархивный с max(variants); preferred_uuid — только если у него есть модификации
        или это единственный кандидат.
        """
        data = self.get("/entity/product", filter=f"name={name}", limit=20)
        rows = [r for r in data.get("rows", []) if r.get("name") == name]
        if not rows and preferred_uuid:
            try:
                return self.get(f"/entity/product/{preferred_uuid}")
            except RuntimeError:
                pass
        if not rows:
            raise RuntimeError(f"Нет product name={name!r}")

        scored: List[Tuple[int, Dict[str, Any]]] = []
        for r in rows:
            if r.get("archived"):
                continue
            scored.append((self._variant_count(r["id"]), r))
        if not scored:
            scored = [(self._variant_count(r["id"]), r) for r in rows]
        scored.sort(key=lambda x: x[0], reverse=True)

        if preferred_uuid:
            for n, r in scored:
                if r["id"] == preferred_uuid and n > 0:
                    return r
            # preferred пустой (0 вариантов) — берём лидера по количеству
        best_n, best = scored[0]
        if best_n == 0 and len(scored) > 1:
            # все пустые — всё равно вернём preferred если есть в списке
            for n, r in scored:
                if preferred_uuid and r["id"] == preferred_uuid:
                    return r
        return best

    def iter_variants_of_product(self, parent_uuid: str, page: int = 1000):
        """Все модификации товара (для индекса антиключа)."""
        offset = 0
        while True:
            data = self.get(
                "/entity/variant",
                filter=f"productid={parent_uuid}",
                limit=page,
                offset=offset,
                expand="characteristics",
            )
            rows = data.get("rows", [])
            if not rows:
                break
            for v in rows:
                yield v
            offset += len(rows)
            if offset >= data.get("meta", {}).get("size", offset):
                break
            time.sleep(0.03)

    def build_parent_key_index(
        self, parent_uuid: str
    ) -> Dict[Tuple[str, str, str], List[Dict[str, Any]]]:
        """Индекс (variation, size, rost) → variants для одного родителя."""
        idx: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        for v in self.iter_variants_of_product(parent_uuid):
            chars = {
                c.get("name"): str(c.get("value", ""))
                for c in v.get("characteristics", [])
            }
            var = chars.get("Вариация") or ""
            size = chars.get("Рзамер") or chars.get("Размер") or ""
            rost = chars.get("Ростовка") or ""
            if not (var and size):
                continue
            idx.setdefault((var, size, rost), []).append(v)
        return idx

    def find_variant_by_key(
        self,
        parent_uuid: str,
        variation: str,
        size: str,
        rost: str,
        index: Optional[Dict[Tuple[str, str, str], List[Dict[str, Any]]]] = None,
    ) -> List[Dict[str, Any]]:
        """Антиключ: вид(parent) + вариация + размер + ростовка."""
        if index is not None:
            return list(index.get((variation, size, rost or ""), []))

        def _match(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            hits: List[Dict[str, Any]] = []
            for v in rows:
                chars = {
                    c.get("name"): str(c.get("value", ""))
                    for c in v.get("characteristics", [])
                }
                sz = chars.get("Рзамер") or chars.get("Размер") or ""
                var = chars.get("Вариация") or ""
                if (
                    (var == variation or str(v.get("code") or "") == variation)
                    and sz == size
                    and (chars.get("Ростовка") or "") == (rost or "")
                ):
                    hits.append(v)
            return hits

        # 1) быстрый путь: code == вариация (канон после sync)
        data = self.get(
            "/entity/variant",
            filter=f"productid={parent_uuid};code={variation}",
            limit=100,
            expand="characteristics",
            timeout=90,
        )
        hits = _match(data.get("rows") or [])
        if hits:
            return hits

        # 2) search по строке вариации в рамках товара
        data = self.get(
            "/entity/variant",
            filter=f"productid={parent_uuid}",
            search=variation,
            limit=100,
            expand="characteristics",
            timeout=90,
        )
        return _match(data.get("rows") or [])
        # полный скан родителя намеренно НЕ делаем — рвёт SSL на «Брюки» ~5k

    def next_cristal_code(self) -> str:
        """Уникальный temp-code без скана каталога (потом batch → вариация)."""
        self._cristal_seq += 1
        return f"Cristal{self._cristal_seq:08d}"

    def resolve_org_store(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        org = self.find_by_name("organization", ORG_NAME)
        store = self.find_by_name("store", STORE_NAME)
        return org, store

    def variant_characteristics_meta(self) -> Dict[str, Dict[str, Any]]:
        if self._char_meta is not None:
            return self._char_meta
        meta = self.get("/entity/variant/metadata")
        out: Dict[str, Dict[str, Any]] = {}
        for ch in meta.get("characteristics", []):
            name = ch.get("name") or ""
            out[name] = ch
            # алиас опечатки выгрузки
            if name in ("Размер", "Рзамер"):
                out["Размер"] = ch
                out["Рзамер"] = ch
        self._char_meta = out
        return out

    def iter_variants(self, limit: int = 1000):
        offset = 0
        while True:
            data = self.get(
                "/entity/variant",
                limit=limit,
                offset=offset,
                expand="product,characteristics",
            )
            rows = data.get("rows", [])
            if not rows:
                break
            for r in rows:
                yield r
            offset += len(rows)
            if offset >= data.get("meta", {}).get("size", offset):
                break
            time.sleep(0.05)

    def build_live_index(self) -> Dict[Tuple[str, str, str, str], List[Dict[str, Any]]]:
        idx: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
        for v in self.iter_variants():
            product = (v.get("product") or {}).get("name") or ""
            if not product and v.get("product") and "meta" in v["product"]:
                # expand мог не дать name
                product = ""
            chars = {c.get("name"): str(c.get("value", "")) for c in v.get("characteristics", [])}
            size = chars.get("Размер") or chars.get("Рзамер") or ""
            rost = chars.get("Ростовка") or ""
            var = chars.get("Вариация") or ""
            if not (product and size and var):
                # fallback: иногда name продукта только через отдельный get — пропустим тонко
                product = (v.get("name") or "").split("(")[0].strip() or product
            if not (product and size and var):
                continue
            key = (product, var, size, rost)
            idx.setdefault(key, []).append(v)
        return idx

    def max_cristal(self) -> int:
        """Макс. номер temp-кода CristalNNNN (без полного скана каталога)."""
        mx = 0
        offset = 0
        while True:
            # filter ~= быстрее полного обхода ~19k модификаций
            data = self.get(
                "/entity/variant",
                limit=100,
                offset=offset,
                filter="code~=Cristal",
                timeout=120,
            )
            rows = data.get("rows", [])
            if not rows:
                break
            for r in rows:
                code = str(r.get("code") or "")
                m = re.fullmatch(r"Cristal(\d+)", code, re.I)
                if m:
                    mx = max(mx, int(m.group(1)))
            offset += len(rows)
            size = data.get("meta", {}).get("size", offset)
            if offset >= size:
                break
            time.sleep(0.05)
        return mx

    def _chars_payload(self, characteristics: Dict[str, str]) -> List[Dict[str, Any]]:
        meta_chars = self.variant_characteristics_meta()
        chars_payload = []
        for name, value in characteristics.items():
            ch = meta_chars.get(name)
            if not ch:
                raise RuntimeError(f"Нет характеристики {name!r} в metadata variant")
            chars_payload.append(
                {
                    "id": ch["id"],
                    "name": ch["name"],
                    "value": value,
                    "meta": ch["meta"],
                }
            )
        return chars_payload

    def create_variant(
        self,
        parent_uuid: str,
        code: str,
        characteristics: Dict[str, str],
        description: str = "",
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "code": code,
            "product": {
                "meta": {
                    "href": f"{MS_API}/entity/product/{parent_uuid}",
                    "type": "product",
                    "mediaType": "application/json",
                }
            },
            "characteristics": self._chars_payload(characteristics),
        }
        if description:
            body["description"] = description
        return self.post("/entity/variant", body)

    def update_variant_attrs(
        self,
        variant_uuid: str,
        characteristics: Dict[str, str],
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Обновить цвет/узор (и др. переданные хар-ки) у существующей модификации."""
        body: Dict[str, Any] = {
            "characteristics": self._chars_payload(characteristics),
        }
        if description is not None:
            body["description"] = description
        return self._req("PUT", f"/entity/variant/{variant_uuid}", body=body)

    def find_enter_by_external_code(self, external_code: str) -> Optional[Dict[str, Any]]:
        data = self.get(
            "/entity/enter",
            filter=f"externalCode={external_code}",
            limit=5,
        )
        rows = data.get("rows", [])
        return rows[0] if rows else None

    def create_enter(
        self,
        org: Dict[str, Any],
        store: Dict[str, Any],
        positions: List[Dict[str, Any]],
        external_code: str,
        description: str,
        moment: str = "",
    ) -> Dict[str, Any]:
        existing = self.find_enter_by_external_code(external_code)
        if existing:
            return existing
        body: Dict[str, Any] = {
            "organization": {"meta": org["meta"]},
            "store": {"meta": store["meta"]},
            "externalCode": external_code,
            "description": description,
            "positions": positions,
        }
        if moment:
            body["moment"] = moment
        return self.post("/entity/enter", body)
