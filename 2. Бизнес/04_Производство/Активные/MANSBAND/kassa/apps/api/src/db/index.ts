import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import { getEnv } from "../env.js";
import * as schema from "./schema.js";

const env = getEnv();

// Один пул соединений на процесс.
export const sql = postgres(env.DATABASE_URL, {
  max: 10,
  idle_timeout: 30,
});

export const db = drizzle(sql, { schema });

export { schema };
