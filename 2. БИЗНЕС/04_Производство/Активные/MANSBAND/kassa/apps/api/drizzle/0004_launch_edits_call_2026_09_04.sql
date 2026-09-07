-- Правки кассы по созвону 04.09.2026 (бета-запуск):
-- П1 САР по количеству костюмов в чеке и метка «телефон не найден».

-- Номер САР внутри заявки: костюмов несколько — САР столько же.
ALTER TABLE "sary" ADD COLUMN IF NOT EXISTS "seq" integer DEFAULT 1 NOT NULL;--> statement-breakpoint
-- До этой правки САР была одна на заявку, поэтому у всех исторических записей seq = 1.
ALTER TABLE "sary" ADD COLUMN IF NOT EXISTS "phone_found" boolean DEFAULT true NOT NULL;--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "sary_deal_seq_idx" ON "sary" ("ref_deal_number","seq");--> statement-breakpoint

-- П7. Ведомость доступов: магазин сотрудника.
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "store" text;--> statement-breakpoint

-- П5. Мотивация: премии в процентах от выручки за период вместо рублей плюс штрафы.
ALTER TABLE "payroll_settings" ADD COLUMN IF NOT EXISTS "penalty_conversion_tiers" jsonb DEFAULT '[]'::jsonb NOT NULL;--> statement-breakpoint
ALTER TABLE "payroll_settings" ADD COLUMN IF NOT EXISTS "penalty_upt_tiers" jsonb DEFAULT '[]'::jsonb NOT NULL;--> statement-breakpoint
-- Пороги переезжают с ключа bonus на bonusPct. Действующие суммы премий нулевые
-- (владелец их ещё не задавал), поэтому просто переименовываем ключ.
UPDATE "payroll_settings"
SET "conversion_tiers" = (
      SELECT coalesce(
        jsonb_agg(jsonb_build_object('from', t->'from', 'to', t->'to', 'bonusPct', coalesce(t->'bonusPct', t->'bonus', '0'::jsonb))),
        '[]'::jsonb
      )
      FROM jsonb_array_elements("conversion_tiers") AS t
    ),
    "upt_tiers" = (
      SELECT coalesce(
        jsonb_agg(jsonb_build_object('from', t->'from', 'to', t->'to', 'bonusPct', coalesce(t->'bonusPct', t->'bonus', '0'::jsonb))),
        '[]'::jsonb
      )
      FROM jsonb_array_elements("upt_tiers") AS t
    );
