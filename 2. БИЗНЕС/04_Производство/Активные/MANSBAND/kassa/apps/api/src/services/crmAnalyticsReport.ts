import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

export type CrmAnalyticsMode = "demo" | "review" | "live";

export function snapshotPath(): string {
  return path.join(homedir(), ".config", "mansband", "crm-analytics-snapshot.json");
}

export function loadCrmSnapshot(file = snapshotPath()): { meta?: { accepted?: boolean }; deals?: unknown[] } | null {
  try {
    const parsed = JSON.parse(readFileSync(file, "utf8")) as { meta?: { accepted?: boolean }; deals?: unknown[] };
    if (!parsed?.meta || !Array.isArray(parsed.deals)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function renderCrmAnalytics(
  snapshot: { meta?: { accepted?: boolean }; deals?: unknown[] } | null,
  files: { shell: string; calc: string; view: string },
): { mode: CrmAnalyticsMode; html: string } {
  if (!snapshot) return { mode: "demo", html: files.shell };
  const json = JSON.stringify(snapshot).replace(/</g, "\\u003c");
  const calc = files.calc.replace(/^export /gm, "");
  const html = files.shell.replace(/<script>[\s\S]*<\/script>/, `<script>\nwindow.SNAPSHOT=${json};\n${calc}\n${files.view}\n</script>`);
  return { mode: snapshot.meta?.accepted ? "live" : "review", html };
}

export function readCrmAnalytics(file = snapshotPath()): { mode: CrmAnalyticsMode; html: string } {
  const dir = new URL("../../reports/", import.meta.url);
  const files = {
    shell: readFileSync(new URL("crm-analytics.html", dir), "utf8"),
    calc: readFileSync(new URL("crm-analytics-calc.mjs", dir), "utf8"),
    view: readFileSync(new URL("crm-analytics-view.js", dir), "utf8"),
  };
  return renderCrmAnalytics(loadCrmSnapshot(file), files);
}
