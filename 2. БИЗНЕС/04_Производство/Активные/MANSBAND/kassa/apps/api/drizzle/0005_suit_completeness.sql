-- Комплектность костюмов (план 07.09): костюм собирается вычислением из
-- характеристик модификаций МойСклад, комплекты (bundle) не заводим.

-- Характеристики модификации. Вариация одинакова у пиджака, брюк и жилета
-- одной модели — по ней и собирается костюм. Размер в МС приходит под именем
-- «Рзамер» (опечатка в справочнике клиента), синк читает оба варианта.
ALTER TABLE "products" ADD COLUMN IF NOT EXISTS "variation" text;--> statement-breakpoint
ALTER TABLE "products" ADD COLUMN IF NOT EXISTS "size" text;--> statement-breakpoint
ALTER TABLE "products" ADD COLUMN IF NOT EXISTS "height" text;--> statement-breakpoint
ALTER TABLE "products" ADD COLUMN IF NOT EXISTS "color" text;--> statement-breakpoint
ALTER TABLE "products" ADD COLUMN IF NOT EXISTS "pattern" text;--> statement-breakpoint
ALTER TABLE "products" ADD COLUMN IF NOT EXISTS "fit" text;--> statement-breakpoint
ALTER TABLE "products" ADD COLUMN IF NOT EXISTS "suit_part" text;--> statement-breakpoint
ALTER TABLE "products" ADD COLUMN IF NOT EXISTS "suit_line" text;--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "products_variation_idx" ON "products" ("variation");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "products_suit_part_idx" ON "products" ("suit_part");--> statement-breakpoint

-- Возраст остатка: дата первого оприходования позиции. Считается синком, чтобы
-- отчёт не тянул документы МойСклада на каждый запрос.
CREATE TABLE IF NOT EXISTS "product_enters" (
  "product_ms_id" text PRIMARY KEY NOT NULL,
  "first_enter_at" timestamp with time zone NOT NULL,
  "last_enter_at" timestamp with time zone NOT NULL,
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL
);--> statement-breakpoint

-- Журнал разбитых костюмов (пункт 4 Миши): таблица заполняется сама из чека,
-- ночной снимок ловит разбиение, прошедшее не через кассу.
CREATE TABLE IF NOT EXISTS "suit_breaks" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "at" timestamp with time zone DEFAULT now() NOT NULL,
  "store" text NOT NULL,
  "consultant" text NOT NULL,
  "ref_deal_number" text,
  "variation" text NOT NULL,
  "title" text NOT NULL,
  "sold_part" text NOT NULL,
  "size" text,
  "left_parts" jsonb DEFAULT '[]'::jsonb NOT NULL,
  "source" text DEFAULT 'sale' NOT NULL,
  -- Ключ идемпотентности: одна продажа не должна дать две записи при ретрае.
  "dedup_key" text
);--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "suit_breaks_dedup_idx" ON "suit_breaks" ("dedup_key");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "suit_breaks_at_idx" ON "suit_breaks" ("at");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "suit_breaks_consultant_idx" ON "suit_breaks" ("consultant");
