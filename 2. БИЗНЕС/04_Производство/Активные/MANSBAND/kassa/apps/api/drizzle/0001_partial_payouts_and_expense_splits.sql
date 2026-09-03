
-- Правки кассы по созвону 29.07.2026: частичная выдача сдачи/чаевых (п.6, п.7)
-- и детализация расходов Эдвина с разбивкой по способам оплаты (п.11).

ALTER TABLE "queue" ADD COLUMN IF NOT EXISTS "issued_amount" integer DEFAULT 0 NOT NULL;--> statement-breakpoint
UPDATE "queue" SET "issued_amount" = "amount" WHERE "status" = 'issued' AND "issued_amount" = 0;--> statement-breakpoint

CREATE TABLE IF NOT EXISTS "queue_payouts" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"queue_id" uuid NOT NULL,
	"amount" integer NOT NULL,
	"method_id" text NOT NULL,
	"issued_by" text NOT NULL,
	"issued_at" timestamp with time zone DEFAULT now() NOT NULL,
	"note" text
);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "queue_payouts_queue_idx" ON "queue_payouts" USING btree ("queue_id");--> statement-breakpoint

ALTER TABLE "expenses" ADD COLUMN IF NOT EXISTS "splits" jsonb;
