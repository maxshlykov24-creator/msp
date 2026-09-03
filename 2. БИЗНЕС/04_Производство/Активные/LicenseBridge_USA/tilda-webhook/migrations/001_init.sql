-- Референс схемы hub LicenseBridge. Таблицы создаются автоматически из ORM
-- (app/models.py) через init_db(). Этот файл — документация/ручной bootstrap.

CREATE TABLE IF NOT EXISTS inbox (
    id           BIGSERIAL PRIMARY KEY,
    event_key    VARCHAR(255) UNIQUE NOT NULL,
    source       VARCHAR(32)  NOT NULL,
    event_type   VARCHAR(64)  NOT NULL,
    payload      JSONB        NOT NULL DEFAULT '{}',
    phone        VARCHAR(32),
    status       VARCHAR(16)  NOT NULL DEFAULT 'pending',
    attempts     INTEGER      NOT NULL DEFAULT 0,
    last_error   TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_inbox_status ON inbox(status);
CREATE INDEX IF NOT EXISTS ix_inbox_phone  ON inbox(phone);

CREATE TABLE IF NOT EXISTS decisions (
    id         BIGSERIAL PRIMARY KEY,
    inbox_id   BIGINT,
    phone      VARCHAR(32),
    action     VARCHAR(64) NOT NULL,
    shadow     BOOLEAN     NOT NULL DEFAULT true,
    detail     JSONB       NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_decisions_action ON decisions(action);

CREATE TABLE IF NOT EXISTS entity_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    entity_type VARCHAR(16) NOT NULL,
    entity_id   BIGINT      NOT NULL,
    reason      VARCHAR(64) NOT NULL,
    snapshot    JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS merge_slots (
    id         SERIAL PRIMARY KEY,
    kind       VARCHAR(8)  NOT NULL,
    slot_no    INTEGER     NOT NULL,
    tag        VARCHAR(16) NOT NULL,
    status     VARCHAR(8)  NOT NULL DEFAULT 'free',
    pair_ref   VARCHAR(255),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_slot UNIQUE (kind, slot_no)
);
CREATE INDEX IF NOT EXISTS ix_merge_slots_status ON merge_slots(status);

CREATE TABLE IF NOT EXISTS rollout_state (
    id               INTEGER PRIMARY KEY,
    stage            INTEGER     NOT NULL DEFAULT 0,
    paused           BOOLEAN     NOT NULL DEFAULT false,
    stage_entered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    note             TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dup_manual (
    id          BIGSERIAL PRIMARY KEY,
    kind        VARCHAR(8)  NOT NULL,
    tag         VARCHAR(16) NOT NULL,
    entity_ids  JSONB       NOT NULL,
    reason      VARCHAR(64) NOT NULL,
    status      VARCHAR(12) NOT NULL DEFAULT 'open',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_dup_manual_status ON dup_manual(status);

CREATE TABLE IF NOT EXISTS handoffs (
    id                BIGSERIAL PRIMARY KEY,
    source_lead_id    BIGINT      NOT NULL UNIQUE,
    assembly_lead_id  BIGINT,
    contact_id        BIGINT      NOT NULL,
    shadow            BOOLEAN     NOT NULL DEFAULT true,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_handoffs_source_lead_id ON handoffs(source_lead_id);
CREATE INDEX IF NOT EXISTS ix_handoffs_contact_id ON handoffs(contact_id);

CREATE TABLE IF NOT EXISTS calls (
    id          BIGSERIAL PRIMARY KEY,
    uniqueid    VARCHAR(64) NOT NULL UNIQUE,
    phone       VARCHAR(32),
    direction   VARCHAR(8)  NOT NULL DEFAULT 'in',
    did         VARCHAR(8),
    ext         VARCHAR(8),
    duration    INTEGER     NOT NULL DEFAULT 0,
    disposition VARCHAR(24),
    recording   VARCHAR(160),
    contact_id  BIGINT,
    lead_id     BIGINT,
    note_done   BOOLEAN     NOT NULL DEFAULT false,
    task_done   BOOLEAN     NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_calls_phone ON calls(phone);
