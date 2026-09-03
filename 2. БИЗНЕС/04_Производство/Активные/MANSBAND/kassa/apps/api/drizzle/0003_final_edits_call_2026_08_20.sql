-- Финальные правки кассы по созвону 20.08.2026:
-- П1 САР (статус in_check + sent_at, настройки порога), П3 единый СДЭК,
-- П5 настройки мотивации, П9 источник записи истории (авто/ручное).

-- П1. САР: дата отправки и третий статус «учтено в чеке».
ALTER TABLE "sary" ADD COLUMN IF NOT EXISTS "sent_at" timestamp with time zone;--> statement-breakpoint
-- Ранее «учтено в чеке» писалось как sent с reason «Сарафан учтён…» — разводим статусы.
UPDATE "sary" SET "status" = 'in_check' WHERE "status" = 'sent' AND "reason" LIKE 'Сарафан учтён%';--> statement-breakpoint
-- Для реально отправленных дата отправки неизвестна — берём дату создания как приближение.
UPDATE "sary" SET "sent_at" = "created_at" WHERE "status" = 'sent' AND "sent_at" IS NULL;--> statement-breakpoint

-- П1. Настройки кассы (первый ключ — saryMinCheck, порог чека для САР).
CREATE TABLE IF NOT EXISTS "app_settings" (
	"key" text PRIMARY KEY NOT NULL,
	"value" jsonb NOT NULL,
	"updated_by" text NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);--> statement-breakpoint

-- П5. Настройки мотивации: append-only история, действующая запись — последняя.
CREATE TABLE IF NOT EXISTS "payroll_settings" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"revenue_pct" numeric DEFAULT '5' NOT NULL,
	"daily_floor" integer DEFAULT 500000 NOT NULL,
	"conversion_tiers" jsonb NOT NULL,
	"upt_tiers" jsonb NOT NULL,
	"created_by" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);--> statement-breakpoint

-- П9. Происхождение записи истории: действие человека или автоматика.
ALTER TABLE "audit_log" ADD COLUMN IF NOT EXISTS "source" text DEFAULT 'manual' NOT NULL;--> statement-breakpoint
UPDATE "audit_log" SET "source" = 'auto' WHERE "actor_name" = 'Касса (авто)';--> statement-breakpoint

-- П3. Единый статус «СДЭК» вместо «СДЭК у клиента» / «СДЭК у нас».
UPDATE "deal_item_state" SET "location" = 'СДЭК' WHERE "location" LIKE 'СДЭК %' OR "location" LIKE 'СДЭК у%';
