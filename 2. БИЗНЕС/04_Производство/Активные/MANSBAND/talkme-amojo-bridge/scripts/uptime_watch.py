"""Independent standard-library monitor; deploy on a different VPS."""
import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

URL = "https://ai-msp.ru/ct/health"


def read_health():
    try:
        with urllib.request.urlopen(URL, timeout=15) as r:
            data = json.load(r)
            return r.status == 200 and data.get("status") == "ok", data.get("quality")
    except Exception:
        return False, None


def healthy():
    return read_health()[0]


def step_quality(state, quality, notify, now):
    state = dict(state)
    if quality is None:
        return state  # Transport failure cannot prove quality recovered.
    warning = quality.get("status") != "ok"
    reasons = {k: v for k, v in quality.get("reasons", {}).items() if v}
    signature = json.dumps(reasons, sort_keys=True)
    if warning:
        if not state.get("notified") or now - state.get("sent_at", 0) >= 1800 or (
                signature != state.get("signature") and now - state.get("sent_at", 0) >= 60):
            labels = {"crm_pending_or_error": "Звонки ожидают обработки CRM или имеют ошибку",
                      "unattributed_recent": "Звонки без источника за сутки",
                      "crm_jobs": "Задания CRM требуют внимания", "fallback_recent": "Визиты с резервным номером за 5 минут",
                      "metrika_errors": "Ошибки или неясные отправки Метрики", "metrika_stalled": "Задержанные загрузки Метрики",
                      "missing_identifier_recent": "Звонки без идентификатора Метрики за сутки",
                      "notification_failures": "Не доставлены внутренние предупреждения"}
            detail = "\n".join(f"{labels.get(k, k)}: {v}" for k, v in reasons.items())
            if notify("MANSBAND: проблемы качества коллтрекинга.\n" + detail + "\nДоступность проверяется отдельно."):
                state.update(notified=True, sent_at=now, signature=signature)
    elif state.get("notified"):
        if notify("MANSBAND: текущих сигналов качества коллтрекинга нет. Исторические пропуски не считаются восстановленными."):
            state = {}
    state["status"] = quality.get("status")
    return state


def send(config, message):
    body = json.dumps({"chat_id": config["chat_id"], "text": message}).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot" + config["token"] + "/sendMessage",
        data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200 and json.load(r).get("ok") is True
    except Exception:
        return False


def step(state, ok, notify, now, prefix=""):
    state = dict(state)
    if ok:
        state["failures"] = 0
        if state.get("notified"):
            if notify(prefix + "MANSBAND: доступность сервера восстановлена. " + URL):
                state = {"failures": 0}
    else:
        state["failures"] = state.get("failures", 0) + 1
        if state["failures"] >= 2 and (not state.get("notified") or now - state.get("sent_at", 0) >= 1800):
            if notify(prefix + "MANSBAND: две проверки подряд не прошли. Сервер или фоновая сверка недоступны. " + URL):
                state.update(notified=True, sent_at=now)
    state["checked_at"] = now
    state["healthy"] = ok
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/mansband-uptime.json")
    parser.add_argument("--state", default="/var/lib/mansband-uptime/state.json")
    parser.add_argument("--drill", action="store_true")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    notify = lambda msg: send(config, msg)
    if args.drill:
        assert healthy(), "real health check failed"
        # Separate simulated state, explicit test labels; production state untouched.
        state = {}
        now = time.time()
        for ok, offset in [(False, 0), (False, 60), (True, 120)]:
            state = step(state, ok, notify, now + offset, "ТЕСТ внешнего мониторинга. ")
            if offset == 60:
                assert state.get("notified"), "test alert delivery failed"
        assert not state.get("notified"), "test recovery delivery failed"
        print("real health OK; test alert and recovery accepted by Telegram")
        return
    path = Path(args.state)
    state = json.loads(path.read_text()) if path.exists() else {}
    ok, quality = read_health()
    previous_quality = state.get("quality", {})
    state = step(state, ok, notify, time.time())
    state["quality"] = step_quality(previous_quality, quality, notify, time.time())
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state))
    temp.chmod(0o600)
    os.replace(temp, path)
    print(json.dumps({"healthy": state["healthy"], "failures": state["failures"],
                      "alert_delivered": bool(state.get("notified"))}))


if __name__ == "__main__":
    main()
