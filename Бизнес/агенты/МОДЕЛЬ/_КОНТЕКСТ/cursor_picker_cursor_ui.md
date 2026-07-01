# Селектор моделей Cursor (иконка «мозги»)

Снимок UI **2026-07-01**: ярус интеллекта в выпадающем списке Cursor и связка с `models_cache.json`.

**Шкала в UI:** Fast → Medium → High / Extra High (не все модели промаркированы текстом).

| В Cursor | `cursor_brain_tier` | `cursor_id` (slug) | Цена API USD/1M in \| out | Примечание |
|----------|---------------------|---------------------|---------------------------|------------|
| Composer 2 | `fast` | `composer-2` | подписка Cursor | Быстрый native agent |
| Sonnet 5 | `high` | `anthropic/claude-sonnet-5` | 2 \| 10 (promo до 31.08) → 3 \| 15 | **Дефолт daily driver**; UI ctx ~300k |
| Sonnet 4.6 | `medium` | `anthropic/claude-sonnet-4.6` | 3 \| 15 | Fallback |
| Opus 4.8 | `extra_high` | `anthropic/claude-opus-4.8` | 5 \| 25 | Потолок качества |
| Opus 4.7 | `extra_high` | `anthropic/claude-opus-4.7` | 5 \| 25 | Legacy в списке |
| GPT-5.5 | `medium` | `openai/gpt-5.5` | 5 \| 30 | Альтернатива Claude |
| Codex 5.3 | `medium` | `openai/gpt-5.3-codex` | 1.75 \| 14 | Кодинг OpenAI |
| Gemini 3.1 Pro | `medium` | `google/gemini-3.1-pro-preview` | 2 \| 12 | Vision / 1M |
| GPT-5.2 | `medium` | `openai/gpt-5.2` | 1.75 \| 14 | |
| Gemini 3 Flash | `medium`* | `google/gemini-3-flash-preview` | 0.5 \| 3 | |
| GPT-5.4 Mini | `medium`* | `openai/gpt-5.4-mini` | 0.75 \| 4.5 | |
| GPT-5 Mini | `medium`* | `openai/gpt-5-mini` | 0.25 \| 2 | |

\*В UI без отдельной текстовой метки — в кэше условно **Medium**.

**Effort (Sonnet 5 / Opus):** `low` → `medium` → `high` → `xhigh` → `max`. Дефолт API — `high`. Выше effort = больше токенов, выше качество на agentic.

Приоритет цен для slug в `openrouter_cache.json`: брать оттуда (`fetched_at`). Для **Composer 2** — `0/0`, учёт по подписке.

**Обновлено:** 2026-07-01
