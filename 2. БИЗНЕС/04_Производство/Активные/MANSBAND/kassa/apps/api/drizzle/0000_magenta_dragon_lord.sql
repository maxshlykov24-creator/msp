CREATE TABLE IF NOT EXISTS "account_balances" (
	"account" text PRIMARY KEY NOT NULL,
	"balance" integer DEFAULT 0 NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_by" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "amo_meta" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"kind" text NOT NULL,
	"amo_id" bigint NOT NULL,
	"parent_id" bigint,
	"name" text NOT NULL,
	"payload" jsonb,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "audit_log" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"actor_id" uuid,
	"actor_name" text NOT NULL,
	"actor_role" text NOT NULL,
	"action" text NOT NULL,
	"entity_type" text NOT NULL,
	"entity_id" text NOT NULL,
	"before" jsonb,
	"after" jsonb,
	"metadata" jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "certificates" (
	"number" text PRIMARY KEY NOT NULL,
	"nominal" integer NOT NULL,
	"balance" integer NOT NULL,
	"status" text DEFAULT 'active' NOT NULL,
	"type" text DEFAULT 'plastic' NOT NULL,
	"guest_name" text,
	"buyer_deal_number" integer,
	"issued_at" text NOT NULL,
	"valid_until" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "deal_item_state" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"deal_number" integer NOT NULL,
	"item_id" text NOT NULL,
	"state" text DEFAULT 'in_store' NOT NULL,
	"location" text,
	"metadata" jsonb,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "deals" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"number" integer NOT NULL,
	"amo_lead_id" bigint,
	"ms_order_id" text,
	"ms_demand_id" text,
	"data" jsonb NOT NULL,
	"stage" text DEFAULT 'Новая заявка' NOT NULL,
	"payment_status" text,
	"idempotency_key" text,
	"sync_status" text DEFAULT 'pending' NOT NULL,
	"sync_error" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "expenses" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"category" text NOT NULL,
	"account" text NOT NULL,
	"amount" integer NOT NULL,
	"description" text,
	"spent_at" timestamp with time zone DEFAULT now() NOT NULL,
	"created_by" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "idempotency_keys" (
	"key" text PRIMARY KEY NOT NULL,
	"scope" text NOT NULL,
	"result" jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "ledger" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"deal_number" integer NOT NULL,
	"method_id" text NOT NULL,
	"kind" text NOT NULL,
	"amount" integer NOT NULL,
	"ms_payment_id" text,
	"idempotency_key" text NOT NULL,
	"created_by" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "ms_refs" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"kind" text NOT NULL,
	"key" text NOT NULL,
	"ms_id" text NOT NULL,
	"meta_href" text NOT NULL,
	"name" text NOT NULL,
	"payload" jsonb,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "products" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"ms_id" text NOT NULL,
	"ms_meta_href" text NOT NULL,
	"ms_type" text DEFAULT 'product' NOT NULL,
	"name" text NOT NULL,
	"article" text,
	"code" text,
	"barcode" text,
	"category" text,
	"price" integer DEFAULT 0 NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "products_ms_id_unique" UNIQUE("ms_id")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "queue" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"kind" text NOT NULL,
	"deal_number" integer NOT NULL,
	"client" text NOT NULL,
	"amount" integer NOT NULL,
	"destination" text NOT NULL,
	"status" text DEFAULT 'pending' NOT NULL,
	"metadata" jsonb,
	"issued_at" timestamp with time zone,
	"issued_by" text,
	"issue_method" text,
	"issue_account" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "sary" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"client" text NOT NULL,
	"phone" text NOT NULL,
	"amount" integer NOT NULL,
	"reason" text NOT NULL,
	"ref_deal_number" integer,
	"status" text DEFAULT 'pending' NOT NULL,
	"screenshot_attached" boolean DEFAULT false NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "shifts" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"store" text NOT NULL,
	"opened_by" text NOT NULL,
	"opened_at" timestamp with time zone DEFAULT now() NOT NULL,
	"closed_at" timestamp with time zone,
	"expected_cash" integer,
	"counted_cash" integer,
	"reconciled" boolean DEFAULT false NOT NULL,
	"payload" jsonb
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "stock" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"product_ms_id" text NOT NULL,
	"warehouse_ms_id" text NOT NULL,
	"warehouse_name" text NOT NULL,
	"quantity" numeric DEFAULT '0' NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "tasks" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"kind" text NOT NULL,
	"deal_number" integer,
	"deal_item_id" text,
	"store" text,
	"assignee_role" text NOT NULL,
	"status" text DEFAULT 'pending' NOT NULL,
	"title" text NOT NULL,
	"idempotency_key" text,
	"metadata" jsonb,
	"created_by" text NOT NULL,
	"completed_by" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"completed_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "users" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"login" text NOT NULL,
	"name" text NOT NULL,
	"password_hash" text NOT NULL,
	"role" text DEFAULT 'seller' NOT NULL,
	"must_change_password" boolean DEFAULT true NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "users_login_unique" UNIQUE("login")
);
--> statement-breakpoint
ALTER TABLE "deals" ADD COLUMN IF NOT EXISTS "idempotency_key" text;--> statement-breakpoint
ALTER TABLE "deals" ADD COLUMN IF NOT EXISTS "sync_status" text DEFAULT 'pending' NOT NULL;--> statement-breakpoint
ALTER TABLE "deals" ADD COLUMN IF NOT EXISTS "sync_error" text;--> statement-breakpoint
ALTER TABLE "queue" ADD COLUMN IF NOT EXISTS "metadata" jsonb;--> statement-breakpoint
ALTER TABLE "queue" ADD COLUMN IF NOT EXISTS "issued_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "queue" ADD COLUMN IF NOT EXISTS "issued_by" text;--> statement-breakpoint
ALTER TABLE "queue" ADD COLUMN IF NOT EXISTS "issue_method" text;--> statement-breakpoint
ALTER TABLE "queue" ADD COLUMN IF NOT EXISTS "issue_account" text;--> statement-breakpoint
ALTER TABLE "tasks" ADD COLUMN IF NOT EXISTS "idempotency_key" text;--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "amo_meta_kind_idx" ON "amo_meta" USING btree ("kind");--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "amo_meta_kind_amo_idx" ON "amo_meta" USING btree ("kind","amo_id");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "audit_entity_idx" ON "audit_log" USING btree ("entity_type","entity_id");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "audit_created_at_idx" ON "audit_log" USING btree ("created_at");--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "deal_item_state_deal_item_idx" ON "deal_item_state" USING btree ("deal_number","item_id");--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "deals_number_idx" ON "deals" USING btree ("number");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "deals_amo_idx" ON "deals" USING btree ("amo_lead_id");--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "deals_idempotency_idx" ON "deals" USING btree ("idempotency_key");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "expenses_spent_at_idx" ON "expenses" USING btree ("spent_at");--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "ledger_idem_idx" ON "ledger" USING btree ("idempotency_key","method_id","amount");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "ledger_deal_idx" ON "ledger" USING btree ("deal_number");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "products_name_idx" ON "products" USING btree ("name");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "products_article_idx" ON "products" USING btree ("article");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "products_barcode_idx" ON "products" USING btree ("barcode");--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "stock_product_wh_idx" ON "stock" USING btree ("product_ms_id","warehouse_ms_id");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "tasks_status_role_idx" ON "tasks" USING btree ("status","assignee_role");--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "tasks_deal_idx" ON "tasks" USING btree ("deal_number");--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "tasks_idempotency_idx" ON "tasks" USING btree ("idempotency_key");--> statement-breakpoint
CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only';
END;
$$;--> statement-breakpoint
DROP TRIGGER IF EXISTS "audit_log_append_only" ON "audit_log";--> statement-breakpoint
CREATE TRIGGER "audit_log_append_only"
BEFORE UPDATE OR DELETE ON "audit_log"
FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();