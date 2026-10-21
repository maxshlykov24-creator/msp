"""Independent standard-library monitor; deploy on a different VPS."""
import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

URL = "https://ai-msp.ru/ct/health"


def healthy():
    try:
        with urllib.request.urlopen(URL, timeout=15) as r:
            return r.status == 200 and json.load(r).get("status") == "ok"
    except Exception:
        return False


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
    state = step(state, healthy(), notify, time.time())
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state))
    temp.chmod(0o600)
    os.replace(temp, path)
    print(json.dumps({"healthy": state["healthy"], "failures": state["failures"],
                      "alert_delivered": bool(state.get("notified"))}))


if __name__ == "__main__":
    main()
