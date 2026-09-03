# Селектор моделей Cursor (иконка «мозги»)

Снимок UI / кэша **2026-07-20**: ярус интеллекта и связка с `models_cache.json`.

**Шкала в UI:** Fast → Medium → High / Extra High (не все модели промаркированы текстом).

| В Cursor | `cursor_brain_tier` | `cursor_id` (slug) | Цена API USD/1M in \| out | Примечание |
|----------|---------------------|---------------------|---------------------------|------------|
| Composer 2.5 | `fast` | `composer-2.5` | подписка Cursor | Быстрый native; CursorBench 56.1% |
| Composer 2 | `fast` | `composer-2` | подписка Cursor | Legacy fast |
| Sonnet 5 | `high` | `anthropic/claude-sonnet-5` | 2 \| 10 (promo до 31.08) → 3 \| 15 | **Daily driver** |
| GPT-5.6 Terra | `high` | `openai/gpt-5.6-terra` | 2.5 \| 15 | Escalate после Sonnet |
| GPT-5.6 Luna | `medium` | `openai/gpt-5.6-luna` | 1 \| 6 | Объём / первая проходка |
| GPT-5.6 Sol | `extra_high` | `openai/gpt-5.6-sol` | 5 \| 30 | Hardest agentic / terminal |
| Claude Fable 5 | `extra_high` | `anthropic/claude-fable-5` | 10 \| 50 | Потолок CursorBench 70.5% |
| Opus 4.8 | `extra_high` | `anthropic/claude-opus-4.8` | 5 \| 25 | Premium Anthropic без Fable |
| Sonnet 4.6 | `medium` | `anthropic/claude-sonnet-4.6` | 3 \| 15 | Fallback |
| GPT-5.5 | `medium` | `openai/gpt-5.5` | 5 \| 30 | Legacy OpenAI |
| Codex 5.3 | `medium` | `openai/gpt-5.3-codex` | 1.75 \| 14 | Кодинг OpenAI |
| Gemini 3.1 Pro | `medium` | `google/gemini-3.1-pro-preview` | 2 \| 12 | Vision / 1M |
| Gemini 3 Flash | `medium`* | `google/gemini-3-flash-preview` | 0.5 \| 3 | Быстрые тексты |
| GPT-5.4 Mini | `medium`* | `openai/gpt-5.4-mini` | 0.75 \| 4.5 | |

\*В UI без отдельной текстовой метки — в кэше условно **Medium**.

**Маршрут (практика MS Product / Cursor):**
1. **Sonnet 5** — по умолчанию  
2. **GPT-5.6 Terra** — застрял / длинный agent loop  
3. **GPT-5.6 Sol** — hardest agentic, terminal, cyber, design polish  
4. **Claude Fable 5** — абсолютный потолок качества (дорого)  
5. **Opus 4.8** — premium Anthropic, если Fable избыточен  

**Effort (Claude / GPT):** `low` → `medium` → `high` → `xhigh` → `max` (+ у GPT-5.6 Sol: `ultra`). Выше effort = больше токенов.

Приоритет цен для slug в `openrouter_cache.json`: брать оттуда, если `fetched_at` < ~7 дней. На 20.07.2026 fetch OpenRouter → **403** — цены из доков провайдеров / устаревший openrouter_cache. Composer — `0/0`, учёт по подписке.

**CursorBench v3.2 (BenchLM, 19.07.2026):** Fable 70.5 → Sol 67.2 → Terra 64.9 → Opus 4.8 62.3 → Sonnet 5 61.5 → Luna 61.1 → Composer 2.5 56.1.

**Обновлено:** 2026-07-20
