import { useEffect, useState } from "react";
import {
  SARY_MIN_CHECK_DEFAULT,
  SARY_SUIT_GROUPS_DEFAULT,
  STOCK_AGE_RED_DAYS_DEFAULT,
  STOCK_AGE_YELLOW_DAYS_DEFAULT,
  SUIT_PART_KEYWORDS_DEFAULT,
  SUIT_SIZE_TOLERANCE_DEFAULT,
} from "@kassa/shared";
import type { AppSettings } from "@kassa/shared";
import { api, USE_MOCK } from "../api/client";

/**
 * Настройки кассы с сервера (порог САР и т.п.). Кэш на сессию:
 * форма продажи не должна ходить в API на каждый рендер.
 */

const DEFAULTS: AppSettings = {
  saryMinCheck: SARY_MIN_CHECK_DEFAULT,
  sarySuitGroups: [...SARY_SUIT_GROUPS_DEFAULT],
  suitSizeTolerance: SUIT_SIZE_TOLERANCE_DEFAULT,
  suitPartKeywords: SUIT_PART_KEYWORDS_DEFAULT,
  stockAgeYellowDays: STOCK_AGE_YELLOW_DAYS_DEFAULT,
  stockAgeRedDays: STOCK_AGE_RED_DAYS_DEFAULT,
  suitPriceOverrides: {},
};

let cache: AppSettings | null = null;
let inflight: Promise<AppSettings> | null = null;

export async function fetchAppSettings(force = false): Promise<AppSettings> {
  if (USE_MOCK) return DEFAULTS;
  if (cache && !force) return cache;
  if (!inflight || force) {
    inflight = api
      .get<AppSettings>("/settings")
      .then((s) => {
        cache = { ...DEFAULTS, ...s };
        return cache;
      })
      .catch(() => cache ?? DEFAULTS)
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/** Сбросить кэш после сохранения настроек. */
export function invalidateAppSettings() {
  cache = null;
}

export function useAppSettings(): AppSettings {
  const [value, setValue] = useState<AppSettings>(cache ?? DEFAULTS);
  useEffect(() => {
    let alive = true;
    fetchAppSettings().then((s) => {
      if (alive) setValue(s);
    });
    return () => {
      alive = false;
    };
  }, []);
  return value;
}
