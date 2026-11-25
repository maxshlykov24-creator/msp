import { useEffect, useState } from "react";
import { api, USE_MOCK } from "../api/client";
import { CONSULTANTS } from "../data/mock";

export interface StaffPerson {
  name: string;
  role: string;
  store?: string | null;
}

function mockStaff(): StaffPerson[] {
  return CONSULTANTS.map((person) => ({
    name: person.name,
    role: person.role === "callmanager" ? "crm" : person.role,
  }));
}

/** Живые консультанты и колл-менеджеры. В мок-режиме остаётся старый справочник. */
export function useStaffDirectory(): StaffPerson[] {
  const [people, setPeople] = useState<StaffPerson[]>(() => (USE_MOCK ? mockStaff() : []));

  useEffect(() => {
    if (USE_MOCK) return;
    let cancelled = false;
    api
      .get<StaffPerson[]>("/users/directory")
      .then((rows) => {
        if (!cancelled) setPeople(rows);
      })
      .catch(() => {
        if (!cancelled) setPeople([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return people;
}
