#!/usr/bin/env bash
# check_models.sh — свежие IDs и цены моделей с OpenRouter API.
#
# Использование:
#   ./tools/check_models.sh                  # все модели (~200 строк)
#   ./tools/check_models.sh anthropic        # все Claude
#   ./tools/check_models.sh google/gemini    # все Gemini
#   ./tools/check_models.sh deepseek         # все DeepSeek
#
# Делать ежемесячно перед мастер-тестом, чтобы не ставить устаревшие модели.

PROVIDER_FILTER="${1:-}"

curl -s https://openrouter.ai/api/v1/models | python3 -c "
import json, sys

data = json.load(sys.stdin)
filter = sys.argv[1].lower() if len(sys.argv) > 1 else ''

models = []
for m in data['data']:
    mid = m['id'].lower()
    if filter and filter not in mid:
        continue
    p = m.get('pricing', {})
    prompt = float(p.get('prompt', 0)) * 1_000_000
    completion = float(p.get('completion', 0)) * 1_000_000
    ctx = m.get('context_length', 0)
    models.append({
        'id': m['id'],
        'in': prompt,
        'out': completion,
        'ctx': ctx,
    })

# Сортируем по цене input
models.sort(key=lambda x: x['in'])

print(f'{\"Model ID\":50s}  {\"in/M\":>10s}  {\"out/M\":>10s}  {\"context\":>10s}')
print('-' * 90)
for m in models:
    ctx_str = f'{m[\"ctx\"]:,}' if m['ctx'] else '?'
    print(f'{m[\"id\"]:50s}  \${m[\"in\"]:>8.2f}  \${m[\"out\"]:>8.2f}  {ctx_str:>10s}')

print()
print(f'Total: {len(models)} models')
" "$PROVIDER_FILTER"
