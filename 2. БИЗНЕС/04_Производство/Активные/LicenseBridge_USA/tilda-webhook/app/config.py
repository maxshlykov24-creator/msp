"""Конфигурация hub LicenseBridge (распределение + антидубли, v3 без NOVA).

Все ID Kommo — из AS-IS.md и .env.example существующего tilda-webhook. Секреты
только в server `.env`. ID не угадываются вслепую: перед запуском mutation-worker
запускается preflight (app/preflight.py), который сверяет их с реальным аккаунтом.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


def _pairs(raw: str) -> list[tuple[str, str]]:
    """«101:15537380,102:15293564» → [(«101», «15537380»), …]."""
    out: list[tuple[str, str]] = []
    for chunk in raw.split(","):
        if ":" not in chunk:
            continue
        k, v = chunk.split(":", 1)
        if k.strip() and v.strip():
            out.append((k.strip(), v.strip()))
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Kommo API ──
    kommo_base: str = "https://licensebridgeusa.kommo.com/api/v4"
    kommo_token: str = ""
    kommo_account_id: int = 34679511
    kommo_max_rps: float = 6.0

    # ── Воронки / этапы (AS-IS) ──
    pipeline_id: int = 11274779           # Pipeline — основная воронка продаж
    status_new: int = 108112044           # «Новая заявка» — старт обработки
    assembly_pipeline_id: int = 12079591  # «Сборка» — производство после оплаты
    status_won: int = 142
    status_lost: int = 143
    assembly_status_start: int = 93231787  # «Заполняют анкету» — старт Сборки

    # ── Handoff Pipeline → Сборка (won создаёт новую сделку в производстве) ──
    handoff_owner_id: int = 15293564      # Полина — фиксированный ответственный Сборки
    enable_handoff: bool = False          # вне авто-раскатки: включается вручную после теста
    handoff_backdate: bool = True         # created_at новой = created_at исходной (лента подтягивает чаты)
    handoff_copy_notes: bool = True       # копировать common/service_message + дайджест звонков/чата
    handoff_tag: str = "сборка_из_продажи"
    handoff_max_per_hour: int = 20        # защита от петли/массового перетаскивания

    # ── Ответственные ──
    # Александра (15648532, РОП) — все новые не-телефонные лиды и fallback.
    # До 19.08.2026 здесь стояла Илона (15537380). Её уволили, и пока дефолт был
    # её, каждый новый лид и каждая задача уходили человеку, которого нет: и сам
    # выбор, и fallback после отказа наследования вели в одного и того же
    # пользователя, поэтому контур не самовосстанавливался.
    default_sales_owner_id: int = 15648532
    # Полина (клиентский отдел) — исключить из автораспределения продаж.
    client_dept_owner_ids: str = "15293564"
    # Ушедшие из команды. Пользователь может остаться активным в Kommo (историю
    # не удаляют), и тогда наследование ответственного из закрытой сделки снова
    # приводит лид к человеку, которого нет: вернувшийся клиент попадает в
    # карточку уволенного и его никто не видит. Проверено прогоном 19.08.2026.
    # Илона `15537380` уволена 19.08.2026.
    departed_owner_ids: str = "15537380"

    # ── Кастомные поля (id сверены с боевым аккаунтом 2026-07-24) ──
    # контакты
    field_phone: int = 142532
    field_phone_enum_work: int = 120012
    # контакты, Wazzup: устойчивые id мессенджеров — ключ склейки, когда телефона нет
    field_wz_telegram_id: int = 1418422
    field_wz_telegram_username: int = 1418420
    field_wz_whatsapp_lid: int = 1418426
    field_wz_whatsapp_username: int = 1418424
    # сделки
    field_channel: int = 143104
    field_utm_source: int = 142546
    field_utm_campaign: int = 142544
    field_teletype_id: int = 143110
    field_ttad_id: int = 1408266
    field_ttad_name: int = 1408268
    field_teletype_url: int = 143106

    # ── БД ──
    database_url: str = (
        "postgresql+psycopg2://lbhub:lbhub@db:5432/lbhub"
    )

    # ── Секреты входящих каналов ──
    pleep_api_key: str = ""
    # path-secret для нативных вебхуков Kommo: /kommo/webhook/{secret}
    kommo_webhook_secret: str = ""
    # отдельный ключ для внутреннего resolver телефонии
    internal_api_key: str = ""

    # ── Режимы работы ──
    run_mode: str = "api"                 # api | worker

    # ── Feature flags (безопасное поэтапное включение, см. план §7) ──
    # SHADOW: считать решения и писать audit, но НЕ менять Kommo.
    shadow_mode: bool = True
    enable_assignment: bool = False       # автораспределение новых лидов
    enable_deal_dedup: bool = False       # устранение дубль-сделок
    enable_cross_funnel: bool = False     # свёртка Pipeline→Сборка при создании
    enable_contact_soft_merge: bool = False  # soft-merge контактов (включаем последним)
    enable_light_chat_merge: bool = False    # merge контактов с лёгким чатом (после без-чатов)

    # ── Авто-раскатка (app/rollout.py) ──
    # Сервер сам двигается по этапам shadow → assignment → deals → contacts,
    # если выдержано время и нет сбоев. Ручные ENABLE_* выше — принудительное
    # включение поверх этапа; полный стоп: auto_rollout=false + shadow_mode=true.
    auto_rollout: bool = True
    rollout_shadow_hours: int = 24        # сколько наблюдаем в shadow до первых мутаций
    rollout_stage_hours: int = 24         # выдержка каждого следующего этапа
    rollout_min_decisions: int = 20       # минимум решений в shadow до выхода из него
    rollout_health_window_hours: int = 24 # окно подсчёта решений/ошибок
    rollout_max_deadletter: int = 0       # >N необработанных ошибок → продвижение стоп
    rollout_max_pending: int = 100        # затор в очереди → продвижение стоп
    rollout_check_interval_sec: int = 300 # как часто worker проверяет условия перехода

    # Создавать контакт/сделку из Tilda/Pleep даже в shadow-режиме, чтобы не терять
    # входящие лиды (это базовая функция, заменяющая прежний webhook). Деструктивные
    # действия (удаление дублей, merge контактов) в shadow всё равно не выполняются.
    intake_create_in_shadow: bool = True

    # ── Телефония (Asterisk 159.65.97.156) ──
    # Диалплан спрашивает очередь добавочных у /internal/call/route и присылает
    # итог звонка в /internal/call/missed и /internal/call/finished.
    enable_telephony: bool = False
    # добавочный → пользователь Kommo: Полина (102) / Александра (РОП, 103) /
    # Павел (101, владелец, с 24.08.2026). По этой карте звонок подписывается тем,
    # кто его вёл (created_by примечания). Пользователя из карты нельзя удалять в
    # Kommo, не поправив её: id уволенной Елены (15618144) Kommo отвергал с
    # NotSupportedChoice.
    telephony_ext_users: str = "102:15293564,103:15648532,101:13291175"
    # круг продаж в покое: постоянная линия одна — РОП Александра. Павел садится
    # на 101 сменами, тогда `.env` держит `103,101` (RUNBOOK, «линия на одну смену»).
    telephony_sales_order: str = "103"
    # линия (SIM-порт) → её владелец: звонок на прямой номер начинается с него.
    # Линия 1 — бывший номер Илоны, клиенты по нему звонят до сих пор, поэтому вне
    # смены Павла ведём её на Александру. Линия 2 держится под «2 — обслуживание».
    telephony_did_ext: str = "1:103,3:103"
    # пропущенный от нового/ничьего номера → РОП Александра
    telephony_missed_owner_id: int = 15648532
    # «2 — обслуживание»: Полина (102) не ответила → задача на неё
    telephony_service_owner_id: int = 15293564
    telephony_task_minutes: int = 60      # срок задачи «перезвонить»
    # ── сторож дозвона (антиспам) ──
    # Аналитические движки (Hiya, TNS, First Orion) метят номер спамом за
    # поведение: короткие соединения и повторы на один и тот же номер. Профиль
    # 01-19.08.2026: 713 наборов, медиана разговора 7 с, 116 повторов быстрее
    # 15 минут — ровно то, что читается как обзвон.
    # off — не спрашивать, warn — считать и писать в лог, block — не пускать набор.
    # Лестница 27.08: 1 недозвон → 15 мин, 2 → 3 часа, 3 → Reactivation и 1/сутки.
    dial_guard_mode: str = "warn"
    dial_guard_min_gap_min: int = 15      # после любого набора, в том числе дозвона
    dial_guard_hard_gap_sec: int = 90     # мгновенный второй набор
    dial_guard_second_gap_min: int = 180  # после второго недозвона, 3 часа
    dial_guard_reactivation_hours: int = 24
    dial_guard_max_per_day: int = 3       # исходящих наборов на номер в сутки
    status_reactivation: int = 110600324  # этап Reactivation в Pipeline
    # Голосовая почта снимает трубку сама, и Asterisk считает это ответом: дозвон
    # 30-40 секунд, потом «разговор» 2-5 секунд. Такой звонок не разговор — он
    # закрывал лид в отчётах и не поднимал лестницу недозвона (жалоба 24.08.2026).
    call_short_talk_sec: int = 15
    telephony_source: str = "asterisk_lb"  # params.source в примечании-звонке
    telephony_channel: str = "Входящий звонок"  # канал сделки, созданной по звонку
    # записи разговоров: файлы лежат на Asterisk, наружу отдаёт хаб по подписи
    recordings_url: str = "http://159.65.97.156:8088/recordings"
    public_base_url: str = "https://72-56-123-137.sslip.io"
    rec_link_secret: str = ""             # пусто → берётся internal_api_key

    # ── Лид-машина: WhatsApp молчит → AI-звонок Pleep → SMS ──
    # Salesbot пишет клиенту первым и ждёт ответа; по таймауту зовёт хаб, а хаб
    # соединяет клиента с голосовым агентом Pleep и разбирает итог разговора.
    enable_leadflow: bool = False
    # этапы Pipeline, по которым лид-машина двигает сделку
    status_first_touch: int = 86527439     # «Первичный контакт»
    status_qualification: int = 96943275   # «Квалификация»
    status_in_work: int = 106419976        # «В работе»
    # поля сделки (созданы в боевом аккаунте 2026-08-10)
    field_wa_variant: int = 1420023        # вариант текста первого сообщения, 1..N
    field_call_window: int = 1420025       # окно звонка по местному времени клиента
    wa_variant_count: int = 4              # сколько вариантов текста у Salesbot

    # ── Первое сообщение в WhatsApp отправляет хаб, а не Salesbot ──
    # Бота выключили в UI Kommo, и заявки остались без ответа: снаружи это
    # выглядело как «Pleep не пишет первым» (26.08–03.09.2026). Отдельный флаг:
    # включается после прогона на тестовом номере, независимо от лид-машины.
    enable_wazzup_first_touch: bool = False
    wazzup_api_key: str = ""
    wazzup_channel_id: str = ""            # пусто → берётся активный канал WhatsApp
    # Тексты вариантов лежат в Salesbot, их переносит владелец: сочинять письмо
    # клиенту за него нельзя. Формат — `1|текст||2|текст`, `{name}` = имя клиента.
    # Нет текста для варианта — сообщение не уходит, в журнале `no_template`.
    wa_templates: str = ""
    # Прогон без живых лидов: пока номер задан, сообщения уходят только на него.
    wazzup_test_phone: str = ""
    # Сколько ждём ответа на первое сообщение, прежде чем звонить голосовым
    # агентом. Раньше молчание отслеживал Salesbot и сам звал хаб; теперь пишет
    # хаб, поэтому и молчание считает он — иначе цепочка обрывается на сообщении.
    leadflow_silence_min: int = 30
    leadflow_followup_max_hours: int = 48  # старше — уже не «ответ на заявку»
    # голосовой агент Pleep (SIP-транкинг ElevenLabs): не публичный номер для
    # звонка извне, а идентификатор в SIP URI на sip.rtc.elevenlabs.io — см.
    # asterisk/pjsip_pleep.conf [pleep_out]. Сейчас это DID линии 103.
    pleep_number: str = "+18188066735"
    # когда клиенту можно звонить по ЕГО местному времени. Уже клиента, чем TCPA
    # (8:00–21:00), потому что таймзону определяем по коду номера, а не по адресу.
    call_window_start_hour: int = 9
    call_window_end_hour: int = 20
    call_window_default_tz: str = "America/New_York"  # номер без узнаваемой зоны
    ai_call_max_attempts: int = 2
    ai_call_ring_sec: int = 45             # сколько ждём ответа клиента
    ai_call_sweep_interval_sec: int = 60   # как часто worker проверяет отложенные
    # SMS-бот RingCentral в Kommo: id из адресной строки редактора бота. 0 —
    # бот не задан, тогда вместо SMS ставится задача менеджеру.
    sms_bot_id: int = 0
    leadflow_task_minutes: int = 30
    tag_do_not_call: str = "не звонить"
    # AMI Asterisk — только для инициации AI-звонка. Слушает на 159.65.97.156:5038
    # и открыт правилом firewall исключительно для IP хаба.
    ami_host: str = "159.65.97.156"
    ami_port: int = 5038
    ami_user: str = "lbhub"
    ami_secret: str = ""
    ami_timeout: float = 10.0
    ami_outbound_context: str = "lb-ai-outbound"  # набираем клиента с нашего DID
    ami_connect_context: str = "lb-ai-connect"    # клиент ответил → соединяем с Pleep

    # ── Алерты и наблюдение ──
    # Отвал софтфона 102 наблюдатель на Asterisk записал 07.08, а узнали мы от
    # клиента 13.08: журнал сам себя не читает. Токен бота держим только здесь —
    # наблюдатель на Asterisk шлёт свои алерты через /internal/alert.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""            # пусто → алерты только в лог
    monitor_interval_sec: int = 300
    monitor_workday_start_hour: int = 9   # по settings.tz
    monitor_workday_end_hour: int = 18
    monitor_silence_hours: int = 3        # столько тишины на добавочном = алерт
    monitor_lead_burst_per_hour: int = 25 # выше — похоже на дубль-шторм Make.com
    monitor_spam_check_days: int = 30     # как часто напоминать сверить реестры
    # утренняя сводка: её отсутствие — единственный способ заметить, что умер сам
    # хаб. Молчание сломанного наблюдателя неотличимо от «всё хорошо».
    monitor_digest_hour: int = 9          # по settings.tz
    # Заявка приходит ночью на менеджера вне смены, и первым это видит клиент, а
    # не система (просьба Павла 28.08.2026). Алерт на каждый новый лид.
    alert_new_lead: bool = True
    # Клиент ответил в мессенджер, а человек молчит. Ответ бота ответом не
    # считается: в июле так осталось без ответа 63,6% чатов при медиане 8 часов.
    chat_silence_hours: int = 1
    # окно, за которое считаем переписку: чат-события отдаёт только общий поток
    # /events, и окно приходится держать в кеше (см. app/chat_events.py)
    chat_digest_days: int = 60
    # как часто подметать сделки продаж, осевшие на «не продавце» (Павел, Полина,
    # уволенные): событие создания ловит только новые, руками перенесённые — нет
    owner_sweep_interval_sec: int = 3600

    # ── Дедуп-пороги ──
    # «Общий номер» (семья/офис) определяется по именам карточек, число сделок в
    # решении больше не участвует (см. app/dedup_contacts.py::_is_shared_number).
    name_similarity_ratio: float = 0.8    # ниже — имена считаются разными людьми
    light_chat_max_messages: int = 5      # порог «лёгкого» чата (кол-во сообщений)

    # ── Теги ──
    # Kommo API v4 не умеет удалять сделки/контакты (DELETE → 405, политика платформы,
    # проверено на боевом аккаунте 2026-07-24). Пустую карточку-дубль КОНТАКТА
    # помечаем тегом, физически удаляет человек пакетно в UI по фильтру тега.
    tag_dup_to_delete: str = "дубль_удалить"
    # Дубль-СДЕЛКА не удаляется: уходит в «Провал» с причиной — история остаётся,
    # из рабочих этапов исчезает, статистика конверсии не портится.
    tag_dup_deal: str = "дубль"
    dup_lost_reason_id: int = 36943028  # «дубль (есть другая сделка у того же клиента)»
    archive_pipeline_id: int = 0   # 0 = не переносить; иначе воронка «Архив дублей»
    archive_status_id: int = 0     # первый этап архивной воронки
    tag_notmerge: str = "notmerge"
    tag_marker_contact: str = "need_merge_contact"
    tag_marker_deal: str = "need_merge_deal"
    merge_slots_contacts: int = 20        # стартовый пул слотов mc_NN
    merge_slots_deals: int = 20           # стартовый пул слотов md_NN

    # ── Worker / scanner ──
    worker_poll_interval_sec: float = 2.0
    worker_max_attempts: int = 5          # затем dead-letter
    scanner_interval_min: int = 180       # плановый детект дублей
    scanner_autoqueue: bool = True        # найденные дубли отправлять в inbox на обработку
    scanner_max_contacts: int = 20000     # ограничение объёма планового скана
    tag_reclaim_interval_hours: int = 168 # еженедельный авто-возврат тегов
    tz: str = "America/New_York"

    @property
    def client_dept_owner_id_set(self) -> set[int]:
        return {int(x) for x in self.client_dept_owner_ids.split(",") if x.strip()}

    @property
    def departed_owner_id_set(self) -> set[int]:
        return {int(x) for x in self.departed_owner_ids.split(",") if x.strip()}

    @property
    def wa_template_map(self) -> dict[str, str]:
        """Варианты первого сообщения: `1|текст||2|текст` → {"1": "текст", ...}.

        Разделитель `||`, потому что сам текст содержит и запятые, и переводы
        строк, и двоеточия."""
        out: dict[str, str] = {}
        for chunk in (self.wa_templates or "").split("||"):
            key, sep, text = chunk.partition("|")
            if sep and key.strip() and text.strip():
                out[key.strip()] = text.strip()
        return out

    @property
    def ext_to_user(self) -> dict[str, int]:
        return {k: int(v) for k, v in _pairs(self.telephony_ext_users)}

    @property
    def user_to_ext(self) -> dict[int, str]:
        return {int(v): k for k, v in _pairs(self.telephony_ext_users)}

    @property
    def did_to_ext(self) -> dict[str, str]:
        return dict(_pairs(self.telephony_did_ext))

    @property
    def sales_order(self) -> list[str]:
        return [x.strip() for x in self.telephony_sales_order.split(",") if x.strip()]


settings = Settings()

# Терминальные статусы (закрытые сделки) — общие для всех воронок Kommo.
CLOSED_STATUS_IDS: set[int] = {settings.status_won, settings.status_lost}
