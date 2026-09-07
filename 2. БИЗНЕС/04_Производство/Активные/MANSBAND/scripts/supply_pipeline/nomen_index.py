"""Индекс номенклатуры из листа Номенкларутра."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from expand import _clean


@dataclass
class NomenHit:
    uuid: str
    code: str
    name: str
    size: str
    rost: str
    variation: str
    color: str
    uzor: str
    kroy: str


def _header_map(header: List[Any]) -> Dict[str, int]:
    return {_clean(h): i for i, h in enumerate(header)}


def build_index(nomen_rows: List[List[Any]]) -> Dict[Tuple[str, str, str, str], List[NomenHit]]:
    if not nomen_rows:
        return {}
    h = _header_map(nomen_rows[0])
    need = {
        "uuid": h.get("UUID"),
        "type": h.get("Тип"),
        "code": h.get("Код"),
        "name": h.get("Наименование"),
        "size": h.get("Характеристика:Рзамер", h.get("Характеристика:Размер")),
        "color": h.get("Характеристика:Цвет"),
        "rost": h.get("Характеристика:Ростовка"),
        "uzor": h.get("Характеристика:Узорность"),
        "var": h.get("Характеристика:Вариация"),
        "kroy": h.get("Характеристика:Крой"),
    }
    missing = [k for k, v in need.items() if v is None and k in ("uuid", "name", "size", "rost", "var")]
    if missing:
        raise RuntimeError(f"Номенкларутра: нет колонок {missing}; есть={list(h)}")

    idx: Dict[Tuple[str, str, str, str], List[NomenHit]] = {}
    for r in nomen_rows[1:]:
        def g(key: str) -> str:
            i = need[key]
            if i is None or i >= len(r):
                return ""
            return _clean(r[i])

        if need["type"] is not None and g("type") and g("type") != "Модификация":
            # товары-родители без характеристик пропускаем, если тип заполнен
            if g("type") != "Модификация":
                continue
        var = g("var")
        size = g("size")
        rost = g("rost")
        name = g("name")
        uid = g("uuid")
        if not (var and size and name and uid):
            continue
        hit = NomenHit(
            uuid=uid,
            code=g("code"),
            name=name,
            size=size,
            rost=rost,
            variation=var,
            color=g("color"),
            uzor=g("uzor"),
            kroy=g("kroy"),
        )
        key = (name, var, size, rost)
        idx.setdefault(key, []).append(hit)
    return idx


def lookup(
    index: Dict[Tuple[str, str, str, str], List[NomenHit]],
    vid: str,
    variation: str,
    size: str,
    rost: str,
) -> List[NomenHit]:
    return list(index.get((vid, variation, size, rost), []))
