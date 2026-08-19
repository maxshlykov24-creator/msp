"""
Нормализация размеров в наименованиях: * / х → ×, склейки 1002009 → 100×200×9.
"""

import re

_DIM_SEP = re.compile(r"(\d)\s*[\*xX]\s*(\d)")
_CYR_SEP = re.compile(r"(\d)\s*х\s*(\d)", re.IGNORECASE)
_CHAIN_STAR = re.compile(r"(\d+)\*(\d+)")
_CHAIN_DIM = re.compile(r"(\d+)×(\d+)\*(\d+)")
_GLUED = re.compile(r"(?<![×\d])(\d{2,3})(200)(\d{1,2})(?![×\d])")


def normalize_dimensions_in_name(name: str) -> str:
    if not name:
        return name
    s = name
    for _ in range(8):
        s2 = _DIM_SEP.sub(r"\1×\2", s)
        s2 = _CYR_SEP.sub(r"\1×\2", s2)
        s2 = _CHAIN_DIM.sub(r"\1×\2×\3", s2)
        s2 = _CHAIN_STAR.sub(r"\1×\2", s2)
        if s2 == s:
            break
        s = s2
    s = _GLUED.sub(r"\1×\2×\3", s)
    return s
