import { useMemo, useState } from "react";
import { Button, Field } from "./ui";
import { AtelierAmountField } from "./AtelierAmountField";
import { PhotoField } from "../views/forms/common";
import { formatPhone } from "../lib/format";
import { attachmentsToUpload, type PhotoAttachment } from "../lib/photo";
import type { Deal } from "../data/types";

export type ConvertKind = "sale" | "company" | "rental";

export type ConvertKindPayload = {
  kind: ConvertKind;
  companyName?: string;
  managerName?: string;
  managerPhone?: string;
  atelierAmount?: number;
  deliveryAmount?: number;
  closingDocumentsRequired?: boolean;
  issued?: boolean;
  rentalFrom?: string;
  rentalTo?: string;
  issueDate?: string;
  returnDate?: string;
  photos?: Array<{ filename: string; contentBase64: string; target?: "shipment" | "order" | "return" }>;
};

const TARGETS: Array<{ kind: ConvertKind; label: string; hint: string }> = [
  {
    kind: "sale",
    label: "Продажу",
    hint: "Клиент, товары и оплаты уже в заявке. Этап — «Хочет прийти» или текущий товарный.",
  },
  {
    kind: "company",
    label: "Продажу компании",
    hint: "Нужны реквизиты компании. Эдвину уйдёт задача «Выставить счет».",
  },
  {
    kind: "rental",
    label: "Аренду",
    hint: "Сроки аренды и фото паспорта — как при создании аренды.",
  },
];

function addDays(dateStr: string, n: number): string {
  try {
    const d = new Date(dateStr);
    d.setDate(d.getDate() + n);
    return d.toISOString().slice(0, 10);
  } catch {
    return "";
  }
}

export function ConvertKindForm({
  deal,
  busy,
  onCancel,
  onSubmit,
}: {
  deal: Deal;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (payload: ConvertKindPayload) => Promise<void>;
}) {
  const [kind, setKind] = useState<ConvertKind | null>(null);
  const [companyName, setCompanyName] = useState(deal.companyName || "");
  const [managerName, setManagerName] = useState(deal.managerName || "");
  const [managerPhone, setManagerPhone] = useState(deal.managerPhone || "");
  const [atelier, setAtelier] = useState(deal.atelierAmount ? String(deal.atelierAmount) : "");
  const [delivery, setDelivery] = useState(deal.deliveryAmount ? String(deal.deliveryAmount) : "");
  const [issued, setIssued] = useState(Boolean(deal.issued));
  const [rentalFrom, setRentalFrom] = useState(deal.rentalFrom || "");
  const [rentalTo, setRentalTo] = useState(deal.rentalTo || "");
  const [photos, setPhotos] = useState<PhotoAttachment[]>([]);
  const [error, setError] = useState<string | null>(null);

  const missing = useMemo(() => {
    if (!kind) return [] as string[];
    if (kind === "sale") {
      return [
        !deal.clientName?.trim() && "Имя клиента",
        !deal.clientPhone?.trim() && "Телефон клиента",
        deal.items.length === 0 && "Товары",
      ].filter(Boolean) as string[];
    }
    if (kind === "company") {
      return [
        !deal.clientName?.trim() && "Имя клиента",
        !deal.clientPhone?.trim() && "Телефон клиента",
        deal.items.length === 0 && "Товары",
        !companyName.trim() && "Наименование компании",
        !managerName.trim() && "Имя руководителя",
        !managerPhone.trim() && "Телефон руководителя",
      ].filter(Boolean) as string[];
    }
    return [
      !deal.clientName?.trim() && "Имя клиента",
      !deal.clientPhone?.trim() && "Телефон клиента",
      deal.items.length === 0 && "Комплект (товары в заявке)",
      !rentalFrom && "Дата начала аренды",
      !rentalTo && "Дата конца аренды",
      photos.length === 0 && "Фото паспорта",
    ].filter(Boolean) as string[];
  }, [kind, deal, companyName, managerName, managerPhone, rentalFrom, rentalTo, photos.length]);

  async function submit() {
    if (!kind) return;
    if (missing.length) {
      setError(`Заполните: ${missing.join(", ")}`);
      return;
    }
    setError(null);
    const payload: ConvertKindPayload = { kind };
    if (kind === "company") {
      payload.companyName = companyName.trim();
      payload.managerName = managerName.trim();
      payload.managerPhone = managerPhone.trim();
      payload.atelierAmount = Number(atelier) || undefined;
      payload.deliveryAmount = Number(delivery) || undefined;
      payload.issued = issued;
    }
    if (kind === "rental") {
      payload.rentalFrom = rentalFrom;
      payload.rentalTo = rentalTo;
      payload.photos = attachmentsToUpload(photos);
    }
    await onSubmit(payload);
  }

  if (!kind) {
    return (
      <div className="space-y-3">
        <p className="text-[13px] text-mute">
          Номер заявки #{deal.number} сохранится. Выберите тип — дальше заполним обязательные поля.
        </p>
        <div className="space-y-2">
          {TARGETS.map((t) => (
            <button
              key={t.kind}
              type="button"
              className="w-full text-left rounded-lg border border-ink-700 px-4 py-3 hover:border-gold/40"
              onClick={() => setKind(t.kind)}
            >
              <div className="text-white font-semibold text-[14px]">{t.label}</div>
              <div className="text-[12px] text-mute mt-0.5">{t.hint}</div>
            </button>
          ))}
        </div>
        <div className="flex justify-end">
          <Button variant="subtle" onClick={onCancel}>
            Отмена
          </Button>
        </div>
      </div>
    );
  }

  const title = TARGETS.find((t) => t.kind === kind)?.label ?? kind;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-white font-semibold">Провести как {title}</div>
          <div className="text-[12px] text-mute mt-0.5">Заявка №{deal.number}</div>
        </div>
        <button
          type="button"
          className="text-[13px] text-mute hover:text-white"
          onClick={() => setKind(null)}
          disabled={busy}
        >
          ← Другой тип
        </button>
      </div>

      {kind === "sale" && (
        <div className="rounded-lg border border-ink-700 px-3 py-3 text-[13px] text-mute space-y-1">
          <div>
            Клиент: <span className="text-white">{deal.clientName}</span> · {deal.clientPhone}
          </div>
          <div>
            Товаров: <span className="text-white">{deal.items.length}</span> · оплачено{" "}
            <span className="text-white">{deal.paid.toLocaleString("ru-RU")} ₽</span>
          </div>
          <div>После смены типа заявка ведёт себя как обычная продажа.</div>
        </div>
      )}

      {kind === "company" && (
        <div className="space-y-3">
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Телефон руководителя" required>
              <input
                className="input"
                inputMode="tel"
                value={managerPhone}
                placeholder="+7 (___) ___-__-__"
                onChange={(e) => setManagerPhone(formatPhone(e.target.value))}
              />
            </Field>
            <Field label="Имя руководителя" required>
              <input
                className="input"
                value={managerName}
                onChange={(e) => setManagerName(e.target.value.replace(/[0-9]/g, ""))}
                placeholder="ФИО руководителя"
              />
            </Field>
            <Field label="Наименование компании" required>
              <input
                className="input"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                placeholder="ООО «Вектор» · ИНН …"
              />
            </Field>
            <AtelierAmountField value={atelier} onChange={setAtelier} />
            <Field label="Доставка, ₽">
              <input
                className="input"
                inputMode="numeric"
                value={delivery}
                onChange={(e) => setDelivery(e.target.value.replace(/\D/g, ""))}
                placeholder="0"
              />
            </Field>
          </div>
          <label className="flex items-center gap-2 text-sm text-mute-soft">
            <input type="checkbox" checked={issued} onChange={(e) => setIssued(e.target.checked)} />
            Товар фактически выдан
          </label>
          <p className="text-[12px] text-mute">
            После подтверждения Эдвину сразу уйдёт задача «Выставить счет» по этой заявке.
          </p>
        </div>
      )}

      {kind === "rental" && (
        <div className="space-y-3">
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Начало аренды" required>
              <input
                className="input"
                type="date"
                value={rentalFrom}
                onChange={(e) => {
                  const v = e.target.value;
                  setRentalFrom(v);
                  if (v && !rentalTo) setRentalTo(addDays(v, 2));
                }}
              />
            </Field>
            <Field label="Конец аренды" required>
              <input
                className="input"
                type="date"
                value={rentalTo}
                onChange={(e) => setRentalTo(e.target.value)}
              />
            </Field>
          </div>
          <PhotoField photos={photos} onChange={setPhotos} required label="Фото паспорта" />
        </div>
      )}

      {missing.length > 0 && (
        <div className="text-[12px] text-amber-300/90">Ещё нужно: {missing.join(", ")}</div>
      )}
      {error && <div className="text-[13px] text-red-300">{error}</div>}

      <div className="flex justify-end gap-2">
        <Button variant="subtle" onClick={onCancel} disabled={busy}>
          Отмена
        </Button>
        <Button disabled={busy || missing.length > 0} onClick={() => void submit()}>
          {busy ? "Меняем…" : "Подтвердить"}
        </Button>
      </div>
    </div>
  );
}
