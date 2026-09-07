import {
  MANSBAND_PAYOUT_LABEL,
  MANSBAND_PAYOUT_METHOD,
  type PaymentMethod,
} from "@kassa/shared";
import { PAYMENT_METHODS, PAYMENT_METHOD_GROUPS } from "../data/mock";

/**
 * Единый селект способов оплаты/выдачи: группы «Наличные / Безналичные /
 * РС / Сертификат». Используется и в форме заявки, и у Эдвина.
 */
export function PaymentMethodSelect({
  value,
  onChange,
  className = "input",
  withCertificate = true,
  withMansband = false,
  placeholder,
  extraMethods,
  id,
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
}) {
  const groups = PAYMENT_METHOD_GROUPS.filter(
    (g) => withCertificate || g.kind !== "certificate"
  );
  return (
    <select id={id} className={className} value={value} onChange={(e) => onChange(e.target.value)}>
      {placeholder != null && <option value="">{placeholder}</option>}
      {withMansband && (
        <optgroup label="Через Эдвина">
          <option value={MANSBAND_PAYOUT_METHOD}>{MANSBAND_PAYOUT_LABEL}</option>
        </optgroup>
      )}
      {groups.map((group) => (
        <optgroup key={group.kind} label={group.label}>
          {(extraMethods ?? [])
            .filter((m) => m.kind === group.kind)
            .map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          {PAYMENT_METHODS.filter((m) => m.kind === group.kind).map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

/** Первый способ выдачи денег (без сертификата) — дефолт в очереди Эдвина. */
export { DEFAULT_PAYOUT_METHOD_ID } from "@kassa/shared";
