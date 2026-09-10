import {
  MANSBAND_PAYOUT_LABEL,
  MANSBAND_PAYOUT_METHOD,
  type PaymentMethod,
} from "@kassa/shared";
import { PAYMENT_METHODS, PAYMENT_METHOD_GROUPS } from "../data/mock";
import { Select, type SelectGroup } from "./Select";

/**
 * Единый селект способов оплаты/выдачи: группы «Наличные / Безналичные /
 * РС / Сертификат». Используется и в форме заявки, и у Эдвина.
 */
export function PaymentMethodSelect({
  value,
  onChange,
  className = "",
  withCertificate = true,
  withMansband = false,
  placeholder,
  extraMethods,
  id,
  size = "md",
}: {
  value: string;
  onChange: (methodId: string) => void;
  className?: string;
  /** Сертификат нельзя выбрать как способ выдачи сдачи/чаевых. */
  withCertificate?: boolean;
  /** Строка «Перевести Mansband» — только в выплатах (сдача, чаевые, возврат). */
  withMansband?: boolean;
  /** Пустой пункт сверху — консультант обязан выбрать способ. */
  placeholder?: string;
  /** Доп. способы только в текущем контексте (например расход Эдвина). */
  extraMethods?: PaymentMethod[];
  id?: string;
  size?: "md" | "sm";
}) {
  const groups: SelectGroup[] = [];
  if (withMansband) {
    groups.push({
      label: "Через Эдвина",
      options: [{ value: MANSBAND_PAYOUT_METHOD, label: MANSBAND_PAYOUT_LABEL }],
    });
  }
  for (const group of PAYMENT_METHOD_GROUPS) {
    if (!withCertificate && group.kind === "certificate") continue;
    const extras = (extraMethods ?? [])
      .filter((method) => method.kind === group.kind)
      .map((method) => ({ value: method.id, label: method.label }));
    const main = PAYMENT_METHODS.filter((method) => method.kind === group.kind).map((method) => ({
      value: method.id,
      label: method.label,
    }));
    const options = [...extras, ...main];
    if (options.length === 0) continue;
    groups.push({ label: group.label, options });
  }

  return (
    <Select
      id={id}
      value={value}
      onChange={onChange}
      groups={groups}
      placeholder={placeholder ?? "Выбрать способ"}
      className={className}
      size={size}
      searchable
    />
  );
}

/** Первый способ выдачи денег (без сертификата) — дефолт в очереди Эдвина. */
export { DEFAULT_PAYOUT_METHOD_ID } from "@kassa/shared";
