import { readFile } from "node:fs/promises";
import { sql } from "./db/index.js";
import { createUser } from "./services/auth.js";
import type { UserRole } from "@kassa/shared";

// Заводка учёток команды пачкой (созвон 04.09, п.6). Состав берётся из файла,
// чтобы имена сотрудников не жили в коде:
//   pnpm --filter @kassa/api seed:team deploy/team.csv
//
// Формат строки: логин;Имя;роль;магазин
// Роль: consultant | crm | logist | finance | rop | admin
// Магазин необязателен. Строки с # игнорируются.
//
// На выходе — ведомость доступов в консоль. Пароли печатаются один раз,
// в базе лежит только хеш.

const ROLES: UserRole[] = ["consultant", "crm", "logist", "finance", "rop", "admin"];

interface Row {
  login: string;
  name: string;
  role: UserRole;
  store?: string;
}

function parse(text: string): Row[] {
  const rows: Row[] = [];
  text.split(/\r?\n/).forEach((line, i) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) return;
    const [login, name, role, store] = trimmed.split(";").map((c) => c.trim());
    if (!login || !name || !role) throw new Error(`Строка ${i + 1}: нужно «логин;Имя;роль[;магазин]»`);
    if (!ROLES.includes(role as UserRole))
      throw new Error(`Строка ${i + 1}: роль «${role}» неизвестна, допустимо: ${ROLES.join(", ")}`);
    rows.push({ login, name, role: role as UserRole, store: store || undefined });
  });
  return rows;
}

async function main() {
  const path = process.argv[2];
  if (!path) throw new Error("Укажи путь к файлу состава: seed:team <файл.csv>");
  const rows = parse(await readFile(path, "utf8"));

  const sheet: string[] = ["Имя;Логин;Роль;Магазин;Временный пароль"];
  for (const row of rows) {
    const res = await createUser(row);
    if (!res.user || !res.tempPassword) {
      // eslint-disable-next-line no-console
      console.log(`${row.login}: пропущен — ${res.error}`);
      continue;
    }
    sheet.push([row.name, row.login, row.role, row.store ?? "", res.tempPassword].join(";"));
  }

  // eslint-disable-next-line no-console
  console.log(`\nВедомость доступов (${sheet.length - 1} шт.):\n${sheet.join("\n")}`);
  await sql.end();
}

main().catch(async (err) => {
  // eslint-disable-next-line no-console
  console.error("Ошибка заводки команды:", err instanceof Error ? err.message : err);
  await sql.end().catch(() => {});
  process.exit(1);
});
