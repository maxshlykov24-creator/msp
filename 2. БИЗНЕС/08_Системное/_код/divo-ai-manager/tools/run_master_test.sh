#!/usr/bin/env bash
# run_master_test.sh — мастер-тестирование моделей под задачу
#
# Прогоняет 3–5 моделей через OpenRouter с одинаковым system prompt и набором
# тестовых диалогов. Возвращает таблицу: модель / качество / задержка / стоимость.
# Подробно — ВЫБОР_МОДЕЛИ.md § 4.
#
# Использование:
#   export OPENROUTER_TEST_TOKEN=sk-or-v1-...
#   ./tools/run_master_test.sh
#
# Результаты — в results/<timestamp>/

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT/.env" ]; then
  set -a; . "$ROOT/.env"; set +a
fi

if [ -z "${OPENROUTER_TEST_TOKEN:-${OPENROUTER_API_KEY:-}}" ]; then
  echo "Need OPENROUTER_TEST_TOKEN or OPENROUTER_API_KEY in .env"
  exit 1
fi

TOKEN="${OPENROUTER_TEST_TOKEN:-$OPENROUTER_API_KEY}"

# Модели для теста — отредактируй под свой кейс.
MODELS=(
  "anthropic/claude-sonnet-4.6"
  "anthropic/claude-haiku-latest"
  "google/gemini-3.1-flash-lite"
  "deepseek/deepseek-v4-flash"
  "x-ai/grok-4.1-fast"
)

# Тестовые диалоги. Каждый — массив user-сообщений + ожидание.
# Расширь под свою задачу. Минимум 5–10 разных кейсов для надёжной оценки.
TEST_CASES=$(cat <<'EOF'
[
  {
    "name": "cold_lead_short_answers",
    "messages": ["Привет", "Да", "Не знаю"],
    "expected": "ready=false (мало информации, аккуратно завершить или продолжить вопрос)"
  },
  {
    "name": "hot_lead_full_info",
    "messages": [
      "Хочу автоматизировать первичную квалификацию лидов",
      "Компания 200 человек, недвижимость, СПб",
      "Бюджет до 300к, готовы на колл на этой неделе",
      "Меня зовут Иван, тел +79991234567"
    ],
    "expected": "ready=true, summary с компанией/контактом/бюджетом"
  },
  {
    "name": "off_topic",
    "messages": ["Сколько весит ваш CEO?"],
    "expected": "вежливо вернуть в воронку, не отшивать"
  },
  {
    "name": "stop_list_question",
    "messages": ["А можно купить без чека и НДС?"],
    "expected": "уточню у менеджера / без выдумывания политик"
  },
  {
    "name": "manual_request",
    "messages": ["Соедините меня с человеком сразу"],
    "expected": "ready=true с reason=manual_request, без уговоров"
  }
]
EOF
)

SYSTEM_PROMPT=$(cat "$ROOT/workspace/AGENTS.md" "$ROOT/workspace/IDENTITY.md" "$ROOT/workspace/BOOTSTRAP.md" "$ROOT/workspace/MODE_LEAD_QUAL.md" 2>/dev/null || true)

if [ -z "$SYSTEM_PROMPT" ]; then
  echo "Workspace files не найдены в $ROOT/workspace/. Запусти Cursor-сборку через ПРОМПТ_СБОРКИ.md"
  exit 1
fi

TS=$(date -u +"%Y-%m-%d_%H%M%S")
OUT_DIR="$ROOT/results/$TS"
mkdir -p "$OUT_DIR"

echo "Master test starting at $TS"
echo "Models: ${#MODELS[@]} · Cases: $(echo "$TEST_CASES" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
echo "Output: $OUT_DIR"
echo

SUMMARY="$OUT_DIR/_summary.md"
echo "# Master test results · $TS" > "$SUMMARY"
echo "" >> "$SUMMARY"
echo "| Model | Cases passed | Avg latency | Avg in/out tokens | \$ per 1000 dialogs (est.) |" >> "$SUMMARY"
echo "|---|---|---|---|---|" >> "$SUMMARY"

for model in "${MODELS[@]}"; do
  echo "→ Testing $model"

  TOTAL_LATENCY=0
  TOTAL_IN=0
  TOTAL_OUT=0
  TOTAL_CASES=0
  PASSED_CASES=0
  CASES_LOG="$OUT_DIR/${model//\//-}.jsonl"

  while IFS= read -r case_json; do
    CASE_NAME=$(echo "$case_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
    MESSAGES=$(echo "$case_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps([{"role":"user","content":m} for m in d["messages"]]))')
    EXPECTED=$(echo "$case_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["expected"])')

    REQ_BODY=$(python3 -c "
import json
print(json.dumps({
  'model': '$model',
  'messages': [{'role':'system','content':'''$SYSTEM_PROMPT'''}] + $MESSAGES,
  'temperature': 0.4,
  'max_tokens': 600,
}))
")

    START=$(date +%s%3N)
    RESPONSE=$(curl -s -X POST https://openrouter.ai/api/v1/chat/completions \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d "$REQ_BODY")
    END=$(date +%s%3N)
    LATENCY_MS=$((END - START))

    IN_TOKENS=$(echo "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("usage",{}).get("prompt_tokens",0))' 2>/dev/null || echo 0)
    OUT_TOKENS=$(echo "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("usage",{}).get("completion_tokens",0))' 2>/dev/null || echo 0)
    CONTENT=$(echo "$RESPONSE" | python3 -c 'import json,sys; d=json.load(sys.stdin); c=d.get("choices",[{}])[0].get("message",{}).get("content",""); print(c[:500])' 2>/dev/null || echo "ERROR")

    TOTAL_LATENCY=$((TOTAL_LATENCY + LATENCY_MS))
    TOTAL_IN=$((TOTAL_IN + IN_TOKENS))
    TOTAL_OUT=$((TOTAL_OUT + OUT_TOKENS))
    TOTAL_CASES=$((TOTAL_CASES + 1))

    # Просто логируем — оценку «прошёл / не прошёл» делает человек глазами по полю expected.
    echo "  case: $CASE_NAME · ${LATENCY_MS}ms · tokens ${IN_TOKENS}/${OUT_TOKENS}"
    python3 -c "
import json
print(json.dumps({
  'case': '$CASE_NAME',
  'latency_ms': $LATENCY_MS,
  'tokens_in': $IN_TOKENS,
  'tokens_out': $OUT_TOKENS,
  'expected': '''$EXPECTED''',
  'response': '''$CONTENT''',
}, ensure_ascii=False))
" >> "$CASES_LOG"

  done < <(echo "$TEST_CASES" | python3 -c 'import json,sys; [print(json.dumps(c)) for c in json.load(sys.stdin)]')

  AVG_LATENCY=$((TOTAL_LATENCY / TOTAL_CASES))
  AVG_IN=$((TOTAL_IN / TOTAL_CASES))
  AVG_OUT=$((TOTAL_OUT / TOTAL_CASES))

  # Получаем цены модели для оценки стоимости
  PRICING=$(curl -s "https://openrouter.ai/api/v1/models" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for m in data['data']:
    if m['id'] == '$model':
        p = m.get('pricing', {})
        prompt_cost = float(p.get('prompt', 0)) * 1_000_000
        completion_cost = float(p.get('completion', 0)) * 1_000_000
        # Стоимость за 1000 диалогов (avg in × 1000 prompt + avg out × 1000 completion)
        cost_1k = ($AVG_IN * 1000 / 1_000_000) * prompt_cost + ($AVG_OUT * 1000 / 1_000_000) * completion_cost
        print(f'{cost_1k:.2f}')
        break
else:
    print('?')
")

  echo "| \`$model\` | (review by hand) | ${AVG_LATENCY}ms | ${AVG_IN}/${AVG_OUT} | \$$PRICING |" >> "$SUMMARY"
  echo
done

echo "Done. See: $SUMMARY"
echo
echo "Now open each $OUT_DIR/<model>.jsonl, посмотри ответы глазами и заполни"
echo "колонку 'Cases passed' в $SUMMARY вручную (сколько кейсов реально прошло)."
echo "Это и есть мастер-тест из ВЫБОР_МОДЕЛИ.md § 4."
