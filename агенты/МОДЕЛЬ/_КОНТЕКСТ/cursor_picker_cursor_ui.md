# Селектор моделей Cursor (иконка «мозги»)

Снимок UI **2026-05**: ярус интеллекта в выпадающем списке Cursor и связка с `models_cache.json`.

**Шкала в UI:** Fast → Medium → Extra High (не все модели промаркированы текстом).

| В Cursor | `cursor_brain_tier` | `cursor_id` (slug) | Цена API ориентир USD/1M in \| out |
|----------|---------------------|---------------------|-----------------------------------|
| Composer 2 | `fast` | `composer-2` | биллинг Cursor, не OpenRouter |
| GPT-5.5 | `medium` | `openai/gpt-5.5` | 5 \| 30 |
| Codex 5.3 | `medium` | `openai/gpt-5.3-codex` | 1.75 \| 14 |
| Sonnet 4.6 | `medium` | `anthropic/claude-sonnet-4.6` | 3 \| 15 |
| Opus 4.7 | `extra_high` | `anthropic/claude-opus-4.7` | 5 \| 25 |
| GPT-5.2 | `medium` | `openai/gpt-5.2` | 1.75 \| 14 |
| Gemini 3.1 Pro | `medium` | `google/gemini-3.1-pro-preview` | 2 \| 12 |
| GPT-5.4 Mini | `medium` | `openai/gpt-5.4-mini` | 0.75 \| 4.5 |
| Gemini 3 Flash | `medium`* | `google/gemini-3-flash-preview` | 0.5 \| 3 |
| GPT-5 Mini | `medium`* | `openai/gpt-5-mini` | 0.25 \| 2 |

\*В UI без отдельной текстовой метки — в кэше условно **Medium** как соседние позиции списка.

Приоритет цен для slug, присутствующих в `openrouter_cache.json`: брать оттуда (актуальность — `fetched_at`). Для **Composer 2** цены в кэше `0/0`: учёт по подписке Cursor.
