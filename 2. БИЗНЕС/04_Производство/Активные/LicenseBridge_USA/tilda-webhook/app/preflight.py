"""Preflight: сверяет конфигурацию с реальным аккаунтом Kommo перед запуском
mutation-worker. Неверный ID / недоступный пользователь / пустой токен →
mutation-worker не стартует (в shadow — только предупреждения)."""
from __future__ import annotations

import logging

from app.config import settings
from app.kommo.client import KommoClient

log = logging.getLogger("preflight")


class PreflightError(RuntimeError):
    pass


# Не поломки конфигурации, а известные пропуски: код их обрабатывает и работает
# дальше. Раньше они роняли worker наравне с неверными id — и лид-машину нельзя
# было включить, пока не куплен SMS-бот (worker крутился в рестарте 03.09.2026).
DEGRADATIONS = ("SMS_BOT_ID",)


def run_preflight(client: KommoClient | None = None) -> list[str]:
    """Возвращает список проблем (пустой = всё ок).

    Предупреждения в список не попадают: они пишутся в лог и не мешают старту."""
    problems: list[str] = []
    if not settings.kommo_token:
        return ["KOMMO_TOKEN пуст"]
    close = False
    if client is None:
        client = KommoClient()
        close = True
    try:
        acc = client.account()
        if not acc:
            problems.append("не удалось получить /account")
        elif int(acc.get("id", 0)) != settings.kommo_account_id:
            problems.append(
                f"account_id {acc.get('id')} != ожидаемого {settings.kommo_account_id}"
            )

        pipelines = {int(p["id"]): p for p in client.pipelines()}
        if settings.pipeline_id not in pipelines:
            problems.append(f"pipeline_id {settings.pipeline_id} не найден")
        else:
            statuses = {int(st["id"]) for st in
                        pipelines[settings.pipeline_id].get("_embedded", {}).get("statuses", [])}
            if settings.status_new not in statuses:
                problems.append(
                    f"status_new {settings.status_new} нет в воронке {settings.pipeline_id}"
                )
            if settings.enable_leadflow and settings.status_in_work not in statuses:
                problems.append(
                    f"status_in_work {settings.status_in_work} нет в воронке {settings.pipeline_id}"
                )
        if settings.assembly_pipeline_id not in pipelines:
            problems.append(f"assembly_pipeline_id {settings.assembly_pipeline_id} не найден")
        elif settings.enable_handoff:
            asm_statuses = {int(st["id"]) for st in
                            pipelines[settings.assembly_pipeline_id].get("_embedded", {}).get("statuses", [])}
            if settings.assembly_status_start not in asm_statuses:
                problems.append(
                    f"assembly_status_start {settings.assembly_status_start} нет в воронке "
                    f"{settings.assembly_pipeline_id}"
                )

        users = {int(u["id"]): u for u in client.users()}
        if settings.default_sales_owner_id not in users:
            problems.append(
                f"DEFAULT_SALES_OWNER_ID {settings.default_sales_owner_id} (Илона) не найден среди users"
            )
        if settings.enable_handoff and settings.handoff_owner_id not in users:
            problems.append(
                f"HANDOFF_OWNER_ID {settings.handoff_owner_id} (Полина) не найден среди users"
            )

        cf_ids = {int(f["id"]) for f in client.custom_fields("contacts")}
        if settings.field_phone not in cf_ids:
            problems.append(f"field_phone {settings.field_phone} не найден у контактов")

        if settings.enable_leadflow:
            lead_fields = {int(f["id"]) for f in client.custom_fields("leads")}
            for name, fid in (("wa_variant", settings.field_wa_variant),
                              ("Окно звонка", settings.field_call_window)):
                if fid not in lead_fields:
                    problems.append(f"поле «{name}» {fid} не найдено у сделок")
            if not settings.ami_secret:
                problems.append("AMI_SECRET пуст — AI-звонок не инициируется")
            if not settings.sms_bot_id:
                problems.append("SMS_BOT_ID не задан — ветка SMS заменится задачей")
    finally:
        if close:
            client.close()

    warnings = [p for p in problems if p.startswith(DEGRADATIONS)]
    problems = [p for p in problems if p not in warnings]
    for w in warnings:
        log.warning("preflight: %s", w)
    for p in problems:
        log.error("preflight: %s", p)
    if not problems:
        log.info("preflight OK: account=%s pipeline=%s owner=%s",
                 settings.kommo_account_id, settings.pipeline_id, settings.default_sales_owner_id)
    return problems


def assert_ready_for_mutations() -> None:
    """Бросает PreflightError, если конфигурация не готова к мутациям (вне shadow)."""
    problems = run_preflight()
    if problems:
        raise PreflightError("preflight failed: " + "; ".join(problems))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    issues = run_preflight()
    if issues:
        raise SystemExit(1)
    print("preflight OK")
