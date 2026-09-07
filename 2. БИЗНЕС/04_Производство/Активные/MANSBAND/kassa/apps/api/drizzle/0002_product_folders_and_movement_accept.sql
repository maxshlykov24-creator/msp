
-- Правки кассы по ответам Миши 31.07.2026: иерархия групп товаров из МойСклад (п.28)
-- и приёмка перемещения принимающей стороной (п.30 в трактовке «принять у консультанта»).

CREATE TABLE IF NOT EXISTS "product_folders" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"ms_id" text NOT NULL,
	"name" text NOT NULL,
	"path" text NOT NULL,
	"parent_ms_id" text,
	"level" integer DEFAULT 1 NOT NULL,
	"archived" boolean DEFAULT false NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "product_folders_ms_id_unique" UNIQUE("ms_id")
);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "product_folders_path_idx" ON "product_folders" USING btree ("path");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "product_folders_parent_idx" ON "product_folders" USING btree ("parent_ms_id");
