"""Матчинг разобранной позиции по справочнику (токены + RapidFuzz).

Идея: у каждого продукта есть набор алиасов (нормализованные строки). Сравниваем
сказанное с алиасами двумя способами — пересечение множеств токенов (порядок слов
не важен) и нечёткое сравнение RapidFuzz. Берём лучший продукт; если он ниже
порога или почти равен второму — считаем неоднозначным ("❓").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

from .normalize import tokenize_name


@dataclass
class MatchCandidate:
    product_id: int
    name: str
    score: float
    bag_size_kg: Optional[float]
    price_bag: Optional[float]
    price_kg: Optional[float]


@dataclass
class MatchResult:
    best: Optional[MatchCandidate]
    candidates: list[MatchCandidate]
    ambiguous: bool


class Matcher:
    def __init__(self, alias_rows: list, threshold: int = 72):
        """alias_rows: элементы с полями product_id, alias_norm, canonical_name,
        bag_size_kg, price_bag, price_kg (sqlite3.Row или dict)."""
        self.threshold = threshold
        self._entries = []
        for r in alias_rows:
            alias_norm = r["alias_norm"]
            tokens = tokenize_name(alias_norm)  # тот же вид, что и у сказанного
            self._entries.append(
                {
                    "product_id": r["product_id"],
                    "name": r["canonical_name"],
                    "alias_norm": " ".join(sorted(tokens)) or alias_norm,
                    "tokens": tokens,
                    "bag_size_kg": r["bag_size_kg"],
                    "price_bag": r["price_bag"],
                    "price_kg": r["price_kg"],
                }
            )

    def _score(self, spoken_tokens: set[str], spoken_text: str, entry: dict) -> float:
        etoks = entry["tokens"]
        if not etoks or not spoken_tokens:
            return 0.0
        # точное совпадение набора токенов — определённо эта позиция
        if spoken_tokens == etoks:
            return 100.0
        inter = spoken_tokens & etoks
        token_cov = len(inter) / len(etoks)          # доля покрытых токенов алиаса
        spoken_cov = len(inter) / len(spoken_tokens)  # доля покрытого сказанного
        token_score = 100.0 * (0.6 * token_cov + 0.4 * spoken_cov)
        # нечёткое сравнение (устойчиво к опечаткам STT), но со штрафом и за долю
        # алиаса (token_cov), и за долю сказанного (spoken_cov) — иначе короткий
        # «голый» алиас вида «пк-2» (общий для нескольких брендов) получает 100
        # через token_set_ratio при ЛЮБОМ лишнем слове в сказанном (например,
        # бренд), и брендовое уточнение перестаёт на что-либо влиять.
        fuzzy = fuzz.token_set_ratio(spoken_text, entry["alias_norm"])
        fuzzy_adj = fuzzy * (0.5 * token_cov + 0.5 * spoken_cov)
        return max(token_score, fuzzy_adj)

    def match(self, spoken_text: str) -> MatchResult:
        spoken_tokens = tokenize_name(spoken_text)
        best_by_product: dict[int, MatchCandidate] = {}
        for entry in self._entries:
            score = self._score(spoken_tokens, spoken_text, entry)
            pid = entry["product_id"]
            prev = best_by_product.get(pid)
            if prev is None or score > prev.score:
                best_by_product[pid] = MatchCandidate(
                    product_id=pid,
                    name=entry["name"],
                    score=score,
                    bag_size_kg=entry["bag_size_kg"],
                    price_bag=entry["price_bag"],
                    price_kg=entry["price_kg"],
                )
        candidates = sorted(best_by_product.values(), key=lambda c: c.score, reverse=True)
        if not candidates:
            return MatchResult(best=None, candidates=[], ambiguous=True)

        best = candidates[0]
        top = candidates[:3]
        if best.score < self.threshold:
            return MatchResult(best=None, candidates=top, ambiguous=True)
        # неоднозначно, если второй почти так же близок
        if len(candidates) > 1 and (best.score - candidates[1].score) < 6:
            return MatchResult(best=best, candidates=top, ambiguous=True)
        return MatchResult(best=best, candidates=top, ambiguous=False)
