import { z } from "zod";
import { COMPANY_STAGES_WITH_INVOICE, EDWIN_EXPENSE_CATEGORIES } from "./constants.js";

// Zod-схемы для валидации запросов на бэке и форм на фронте.
// Источник истины для рантайм-валидации; статические типы — в types.ts.

export const loginSchema = z.object({
  login: z.string().min(2).max(64),
  password: z.string().min(1).max(256),
});
export type LoginInput = z.infer<typeof loginSchema>;

export const changePasswordSchema = z.object({
  currentPassword: z.string().min(1),
  newPassword: z.string().min(8, "Минимум 8 символов").max(256),
});
export type ChangePasswordInput = z.infer<typeof changePasswordSchema>;

export const userRoleSchema = z.enum(["consultant", "logist", "finance", "crm", "rop", "admin", "seller"]);
export const updateUserRoleSchema = z.object({ role: userRoleSchema });
/** Магазин сотрудника в ведомости доступов; пустая строка — снять привязку. */
export const updateUserStoreSchema = z.object({ store: z.string().trim().max(120) });

export const cartItemStatusSchema = z.enum(["waiting", "in_store", "booked", "reserved"]);

export const cartItemSchema = z.object({
  productId: z.string(),
  name: z.string(),
  price: z.number(),
  qty: z.number().int().positive(),
  discountPct: z.number().min(0).max(100).optional(),
  discountRub: z.number().min(0).optional(),
  isGift: z.boolean().optional(),
  noPrice: z.boolean().optional(),
  isReturn: z.boolean().optional(),
  barcode: z.string().optional(),
  itemStatus: cartItemStatusSchema.optional(),
});

export const paymentSchema = z.object({
  id: z.string(),
  methodId: z.string(),
  amount: z.number(),
  certificateNumber: z.string().optional(),
  paidAt: z.string().optional(), // YYYY-MM-DD
});

export const dealStoreSchema = z.enum(["На Бауманской", "На Пятницкой", "Онлайн-магазин"]);

// Создание/проведение продажи из кассы.
export const createSaleSchema = z.object({
  // idempotencyKey: уникальный ключ заявки на клиенте, защищает от двойного проведения.
  idempotencyKey: z.string().min(8).max(128),
  kind: z.string(),
  funnel: z.enum(["offline", "online", "defects", "return"]),
  store: dealStoreSchema,
  consultant: z.string(),
  referredBy: z.string().optional(),
  callManager: z.string().optional(),
  clientName: z.string(),
  clientPhone: z.string().optional(),
  email: z.string().email().optional().or(z.literal("")),
  channel: z.string().optional(),
  purpose: z.string().optional(),
  comment: z.string().optional(),
  /** Сарафан: телефон друга, кто направил. */
  saryPhone: z.string().optional(),
  saryClient: z.string().optional(),
  /** Бонус сарафана, уже вычтенный из чека (обычно 1000). */
  saryBonus: z.number().nonnegative().optional(),
  items: z.array(cartItemSchema),
  payments: z.array(paymentSchema),
  // флаг: проводить ли отгрузку+оплату в МойСклад (true только при полной оплате/Успехе)
  fulfill: z.boolean().default(false),
  // фотофиксации (base64) для прикрепления к документу МойСклад
  photos: z
    .array(
      z.object({
        filename: z.string(),
        contentBase64: z.string(),
        // к какому документу: shipment (отгрузка), order (заказ), return (возврат)
        target: z.enum(["shipment", "order", "return"]).default("shipment"),
      })
    )
    .optional(),
  // доп. поля под конкретные виды заявок
  guestName: z.string().optional(),
  certificateNumber: z.string().optional(),
  rentalFrom: z.string().optional(),
  rentalTo: z.string().optional(),
  reservedUntil: z.string().optional(),
  linkedDealNumber: z.number().optional(),
  companyName: z.string().optional(),
  invoiceNo: z.string().optional(),
  invoiceDate: z.string().optional(),
  invoiceStatus: z.enum(["draft", "awaiting_payment", "paid"]).optional(),
  documentsStatus: z.enum(["pending", "ready", "handed"]).optional(),
  atelierStatus: z.enum(["pending", "paid"]).optional(),
  atelierAmount: z.number().nonnegative().optional(),
  deliveryAmount: z.number().nonnegative().optional(),
  closingDocumentsRequired: z.boolean().optional(),
  issued: z.boolean().optional(),
  meetingDate: z.string().optional(),
  rentalStatus: z.enum(["reserved", "paid", "issued", "returned", "closed"]).optional(),
  rentalIssuedAt: z.string().optional(),
  rentalReturnedAt: z.string().optional(),
  rentalDeposit: z.number().nonnegative().optional(),
});
export type CreateSaleInput = z.infer<typeof createSaleSchema>;

export const updateStageSchema = z.object({
  stage: z.string().min(1),
  meetingDate: z.string().optional(),
  issued: z.boolean().optional(),
  reason: z.string().trim().max(1000).optional(),
}).superRefine((v, ctx) => {
  if (v.stage === "Встреча назначена" && !v.meetingDate) {
    ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["meetingDate"], message: "Нужна дата встречи" });
  }
});

const payoutRowSchema = z.object({
  methodId: z.string(),
  amount: z.number(),
});

/** Частичное обновление заявки из карточки (вид как при создании). */
export const updateDealSchema = z
  .object({
    consultant: z.string().optional(),
    referredBy: z.string().optional(),
    clientName: z.string().optional(),
    clientPhone: z.string().optional(),
    channel: z.string().optional(),
    purpose: z.string().optional(),
    items: z.array(cartItemSchema).optional(),
    payments: z.array(paymentSchema).optional(),
    comment: z.string().optional(),
    stage: z.string().optional(),
    total: z.number().optional(),
    paid: z.number().optional(),
    reason: z.string().trim().max(1000).optional(),
    companyName: z.string().optional(),
    managerName: z.string().optional(),
    managerPhone: z.string().optional(),
    atelierAmount: z.number().nonnegative().optional(),
    deliveryAmount: z.number().nonnegative().optional(),
    closingDocumentsRequired: z.boolean().optional(),
    issued: z.boolean().optional(),
    rentalFrom: z.string().optional(),
    rentalTo: z.string().optional(),
    issueDate: z.string().optional(),
    returnDate: z.string().optional(),
    /** Раскладка возврата клиенту (в т.ч. «Перевести Mansband» → Эдвин). */
    returnPayouts: z.array(payoutRowSchema).max(20).optional(),
    returnStatus: z.enum(["issued", "pending"]).optional(),
    returnDestination: z.string().max(500).optional(),
  })
  .refine((v) => Object.keys(v).some((k) => k !== "reason" && (v as Record<string, unknown>)[k] !== undefined), {
    message: "Нечего обновлять",
  });

/** Конвейер продажи компании: счёт и оплата от Эдвина, документы и ателье от Миши. */
export const companyStatusSchema = z
  .object({
    invoiceNo: z.string().min(1).max(64).optional(),
    invoiceDate: z.string().min(1).max(32).optional(),
    invoiceStatus: z.enum(["draft", "awaiting_payment", "paid"]).optional(),
    /** Дата внесения в кассу проставляется сервером, из формы не принимается. */
    documentsStatus: z.enum(["pending", "ready", "handed"]).optional(),
    atelierStatus: z.enum(["pending", "paid"]).optional(),
  })
  .refine((v) => Object.values(v).some((value) => value !== undefined), {
    message: "Нечего обновлять",
  });

/** Выдача клиенту: товар и документы консультант отмечает из карточки заявки. */
export const companyHandoverSchema = z
  .object({
    issued: z.boolean().optional(),
    documentsHanded: z.boolean().optional(),
  })
  .refine((v) => v.issued === true || v.documentsHanded === true, {
    message: "Отметьте выдачу товара или передачу документов",
  });

/** Смена типа отложки/обещания без смены номера заявки (п.1.1 правок 10.08). */
export const convertKindSchema = z
  .object({
    kind: z.enum(["sale", "company", "rental"]),
    companyName: z.string().optional(),
    managerName: z.string().optional(),
    managerPhone: z.string().optional(),
    atelierAmount: z.number().nonnegative().optional(),
    deliveryAmount: z.number().nonnegative().optional(),
    closingDocumentsRequired: z.boolean().optional(),
    issued: z.boolean().optional(),
    rentalFrom: z.string().optional(),
    rentalTo: z.string().optional(),
    issueDate: z.string().optional(),
    returnDate: z.string().optional(),
    /** Фото паспорта при конвертации в аренду (как при создании). */
    photos: z
      .array(
        z.object({
          filename: z.string().min(1).max(255),
          contentBase64: z.string().min(1),
          target: z.enum(["shipment", "order", "return"]).optional(),
        })
      )
      .max(20)
      .optional(),
  })
  .superRefine((v, ctx) => {
    if (v.kind === "company") {
      if (!v.companyName?.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["companyName"],
          message: "Укажите наименование компании",
        });
      }
      if (!v.managerName?.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["managerName"],
          message: "Укажите имя руководителя",
        });
      }
      if (!v.managerPhone?.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["managerPhone"],
          message: "Укажите телефон руководителя",
        });
      }
    }
    if (v.kind === "rental") {
      if (!v.rentalFrom?.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["rentalFrom"],
          message: "Укажите дату начала аренды",
        });
      }
      if (!v.rentalTo?.trim()) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["rentalTo"],
          message: "Укажите дату конца аренды",
        });
      }
      if (!v.photos?.length) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["photos"],
          message: "Нужно фото паспорта",
        });
      }
    }
  });

export const addCommentSchema = z.object({
  text: z.string().min(1).max(2000),
});

export const dealsQuerySchema = z.object({
  stage: z.string().optional(),
  store: z.string().optional(),
  kind: z.string().optional(),
  funnel: z.string().optional(),
  limit: z.coerce.number().int().min(1).max(200).default(50),
  page: z.coerce.number().int().min(1).default(1),
});

export const dealsSearchSchema = z
  .object({
    phone: z.string().optional(),
    name: z.string().optional(),
    number: z.coerce.number().optional(),
  })
  .refine((v) => v.phone || v.name || v.number, {
    message: "Нужен хотя бы один параметр поиска: phone | name | number",
  });

export const catalogSearchSchema = z.object({
  q: z.string().max(128).default(""),
  category: z.string().max(256).optional(),
  store: z.string().optional(),
  /** Обзор каталога по группам без поискового запроса (лимит на группу). */
  browse: z
    .union([z.literal("1"), z.literal("true"), z.literal("0"), z.literal("false"), z.boolean()])
    .optional()
    .transform((v) => v === true || v === "1" || v === "true"),
  limit: z.coerce.number().int().min(1).max(200).default(30),
}).refine((value) => value.q.trim() || value.category?.trim() || value.browse, {
  message: "Укажите поисковый запрос, группу или browse=1",
});

export const redeemCertificateSchema = z.object({
  number: z.string().min(1),
  amount: z.number().positive(),
});

export const queueItemSchema = z.object({
  kind: z.enum(["change", "tips", "refund", "invoice", "invoice_check", "documents", "documents_hand", "atelier", "salary"]),
  dealNumber: z.number().int().positive(),
  client: z.string(),
  amount: z.number().nonnegative(),
  destination: z.string(),
  status: z.enum(["pending", "issued"]).optional(),
  metadata: z.record(z.unknown()).optional(),
});

/** Новая выплата «сара» тому, кто направил клиента. */
export const createSarySchema = z.object({
  client: z.string().trim().min(2, "Укажите, кому платим"),
  phone: z.string().trim().min(5, "Нужен телефон получателя"),
  amount: z.number().positive("Сумма больше нуля"),
  reason: z.string().trim().min(2, "Укажите основание"),
  refDealNumber: z.number().int().positive().optional(),
});

/** Массовая отметка «отправлено» по сарам: обязателен реальный скрин перевода на пачку. */
export const sarySentBatchSchema = z.object({
  ids: z.array(z.string().uuid()).min(1).max(200),
  /** С какого счёта переведена пачка — по нему пишется расход «Программа лояльности». */
  methodId: z.string().trim().min(1, "Выберите способ перевода").max(100),
  screenshot: z.object({
    filename: z.string().trim().min(1).max(200),
    contentBase64: z.string().min(200, "Приложите реальный скриншот перевода"),
  }),
});

export const dealKindSchema = z.enum([
  "sale", "company", "cert_plastic", "rental", "deferred", "promise", "no_sliv",
  "sliv", "cert_digital", "delivery", "defect", "drycleaning", "resew",
  "wrong_size", "wrong_label", "refund", "exchange",
]);

/** Отложка/обещание могут прийти от колл-менеджера без консультанта (созвон 20.08). */
export const KINDS_WITHOUT_CONSULTANT = new Set<string>(["deferred", "promise"]);

/** Полный контракт старого web → POST /deals. number принимается, но игнорируется сервером. */
export const createDealSchema = z
  .object({
    id: z.string().min(8).max(128).optional(),
    idempotencyKey: z.string().min(8).max(128).optional(),
    number: z.number().int().nonnegative().optional(),
    createdAt: z.string().datetime().optional(),
    funnel: z.enum(["offline", "online", "defects", "return"]),
    kind: dealKindSchema,
    // Пустой консультант допустим только для отложки/обещания от колл-менеджера —
    // проверка ниже в superRefine. Продажа без консультанта не проводится.
    consultant: z.string(),
    referredBy: z.string().optional(),
    callManager: z.string().optional(),
    clientName: z.string().min(1),
    clientPhone: z.string().default(""),
    email: z.string().email().optional().or(z.literal("")),
    store: dealStoreSchema,
    storeAddress: z.string().optional(),
    channel: z.string().optional(),
    purpose: z.string().optional(),
    saryPhone: z.string().optional(),
    saryClient: z.string().optional(),
    saryBonus: z.number().nonnegative().optional(),
    items: z.array(cartItemSchema).max(500),
    payments: z.array(paymentSchema).max(50),
    stage: z.string().min(1),
    total: z.number(),
    paid: z.number(),
    subtotal: z.number().nonnegative().optional(),
    discountTotal: z.number().nonnegative().optional(),
    checkDiscount: z.number().nonnegative().optional(),
    comment: z.string().max(5000).optional(),
    photos: z.array(z.object({
      filename: z.string().min(1).max(255),
      contentBase64: z.string().min(1),
      target: z.enum(["shipment", "order", "return"]).optional(),
    })).max(20).optional(),
  })
  .passthrough()
  .superRefine((v, ctx) => {
    if (!v.idempotencyKey && !v.id) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["idempotencyKey"], message: "Нужен idempotencyKey или id" });
    }
    if (!v.consultant.trim() && !KINDS_WITHOUT_CONSULTANT.has(v.kind)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["consultant"], message: "Укажите консультанта" });
    }
    if (v.stage === "Встреча назначена" && !v.meetingDate) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["meetingDate"], message: "Для этапа «Встреча назначена» нужна дата встречи" });
    }
    if ((v.kind === "refund" || v.kind === "exchange") && !v.linkedDealNumber) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["linkedDealNumber"], message: "Нужна исходная сделка" });
    }
    if (v.kind === "company" && v.stage === "Успех" && v.issued !== true) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["issued"], message: "Продажа компании завершается только после фактической выдачи" });
    }
    if (v.kind === "company" && v.stage !== "Провал") {
      // Счёт выставляет Эдвин из своей очереди, поэтому номер и дата требуются
      // номер/дата счёта — после того как Эдвин выставил (не на «Счёт запрошен»).
      if (COMPANY_STAGES_WITH_INVOICE.includes(v.stage)) {
        if (!v.invoiceNo) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["invoiceNo"], message: "Укажите номер счёта" });
        }
        if (!v.invoiceDate) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["invoiceDate"], message: "Укажите дату счёта" });
        }
      }
      if (["Оплачено", "Документы готовы", "Документы переданы", "Товар выдан", "Успех"].includes(v.stage) && v.paid < v.total) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["paid"], message: "Этап оплаты недоступен до полной оплаты" });
      }
      if (["Товар выдан", "Успех"].includes(v.stage) && v.issued !== true) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["issued"], message: "Отметьте фактическую выдачу товара" });
      }
      if (v.stage === "Успех" && v.documentsStatus && v.documentsStatus !== "ready" && v.documentsStatus !== "handed") {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["documentsStatus"], message: "Документы не готовы — заявку нельзя закрыть" });
      }
    }
    if (v.kind === "rental" && v.stage !== "Провал") {
      if (!v.rentalFrom || !v.rentalTo) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["rentalFrom"], message: "Укажите плановые даты аренды" });
      }
      if (["Аренда оплачена", "Комплект выдан", "Комплект возвращён", "Успех"].includes(v.stage) && v.paid < v.total) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["paid"], message: "Аренда ещё не оплачена полностью" });
      }
      if (["Комплект выдан", "Комплект возвращён", "Успех"].includes(v.stage) && !v.rentalIssuedAt) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["rentalIssuedAt"], message: "Укажите фактическую дату выдачи" });
      }
      if (["Комплект возвращён", "Успех"].includes(v.stage) && !v.rentalReturnedAt) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["rentalReturnedAt"], message: "Укажите фактическую дату возврата" });
      }
    }
    if ((v.kind === "cert_plastic" || v.kind === "cert_digital") && v.stage !== "Провал") {
      if (v.kind === "cert_plastic" && !/^\d{5,6}$/.test(String(v.certificateNumber ?? ""))) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["certificateNumber"], message: "Укажите номер пластикового сертификата: 5–6 цифр" });
      }
      if (v.paid < v.total) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["paid"], message: "Сертификат ещё не оплачен полностью" });
      }
    }
  });
export type CreateDealInput = z.infer<typeof createDealSchema>;

/**
 * Выдача из очереди. `amount` не задан — закрываем остаток целиком; задан —
 * частичная выдача, позиция остаётся pending до полной суммы (п.6, п.7).
 */
export const issueQueueSchema = z.object({
  methodId: z.string().min(1).max(100),
  amount: z.number().positive().optional(),
  note: z.string().max(500).optional(),
  metadata: z.record(z.unknown()).optional(),
});

export const expenseSplitSchema = z.object({
  methodId: z.string().min(1).max(100),
  amount: z.number().positive(),
});

export const expenseSchema = z
  .object({
    category: z.enum(EDWIN_EXPENSE_CATEGORIES),
    account: z.string().min(1).max(100),
    amount: z.number().positive(),
    splits: z.array(expenseSplitSchema).max(10).optional(),
    description: z.string().max(1000).optional(),
    spentAt: z.string().datetime().optional(),
  })
  .superRefine((v, ctx) => {
    if (!v.splits?.length) return;
    const sum = v.splits.reduce((s, part) => s + part.amount, 0);
    // Копейки округляются на клиенте — допускаем расхождение в 1 копейку.
    if (Math.abs(sum - v.amount) > 0.01) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["splits"],
        message: "Сумма по способам оплаты должна совпадать с суммой расхода",
      });
    }
  });

export const balanceSchema = z.object({
  account: z.string().min(1).max(100),
  balance: z.number(),
});

export const taskSchema = z.object({
  kind: z.enum(["movement", "movement_accept", "reserve", "reserve_call", "unreserve", "assemble_cdek", "call_courier", "take_to_cdek", "pickup_from_cdek", "sary_send", "barcode", "manual_check"]),
  dealNumber: z.number().int().positive().optional(),
  dealItemId: z.string().optional(),
  store: z.string().optional(),
  assigneeRole: z.enum(["consultant", "logist", "crm"]),
  title: z.string().min(1).max(500),
  idempotencyKey: z.string().min(8).max(128).optional(),
  metadata: z.record(z.unknown()).optional(),
});

export const movementTaskSchema = taskSchema.extend({
  kind: z.literal("movement"),
  /** Клиент шлёт короткий отпечаток (shortIdempotencyKey); 256 — запас на старые клиенты. */
  idempotencyKey: z.string().min(8).max(256),
  fromStoreMsId: z.string().min(1),
  toStoreMsId: z.string().min(1),
  positions: z
    .array(
      z.object({
        productId: z.string().min(1),
        quantity: z.number().positive(),
        /** Название для карточки задачи (не обязательно для МС). */
        name: z.string().min(1).optional(),
      })
    )
    .min(1),
});

export const certificateImportRowSchema = z.object({
  number: z.string().regex(/^\d{6}$/, "Номер должен содержать 6 цифр"),
  nominal: z.coerce.number().positive(),
  balance: z.coerce.number().nonnegative(),
  type: z.enum(["plastic", "digital"]).default("plastic"),
  guestName: z.string().optional(),
  issuedAt: z.string().min(10),
  validUntil: z.string().min(10),
});

export const certificateImportSchema = z.object({
  csv: z.string().min(1),
  dryRun: z.boolean().default(true),
});

// ── Настройки кассы и мотивации (созвон 20.08) ───────────────────────

export const appSettingsSchema = z.object({
  saryMinCheck: z.number().int().min(0).max(10_000_000),
  sarySuitGroups: z.array(z.string().trim().min(1).max(200)).max(50),
});
export type AppSettingsInput = z.infer<typeof appSettingsSchema>;

const payrollTierSchema = z
  .object({
    from: z.number().min(0),
    to: z.number().min(0).nullable(),
    // Премия и штраф считаются в процентах от выручки за период (созвон 04.09).
    bonusPct: z.number().min(0).max(100),
  })
  .refine((t) => t.to == null || t.to > t.from, {
    message: "Верхняя граница должна быть больше нижней",
  });

export const payrollSettingsSchema = z.object({
  revenuePct: z.number().min(0).max(100),
  dailyFloor: z.number().min(0).max(1_000_000),
  conversionTiers: z.array(payrollTierSchema).max(20),
  uptTiers: z.array(payrollTierSchema).max(20),
  penaltyConversionTiers: z.array(payrollTierSchema).max(20).default([]),
  penaltyUptTiers: z.array(payrollTierSchema).max(20).default([]),
});
export type PayrollSettingsInput = z.infer<typeof payrollSettingsSchema>;

export const payrollQuerySchema = z.object({
  from: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Формат даты: YYYY-MM-DD"),
  to: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Формат даты: YYYY-MM-DD"),
});

/**
 * Создание пользователя из интерфейса (только admin). Пароль можно не задавать:
 * тогда касса генерирует временный и показывает его один раз в ведомости доступов
 * (созвон 04.09).
 */
export const createUserSchema = z.object({
  login: z.string().trim().min(2).max(64).regex(/^[a-zA-Z0-9._-]+$/, "Логин: латиница, цифры, точка, дефис"),
  name: z.string().trim().min(2).max(120),
  password: z.string().min(8, "Минимум 8 символов").max(256).optional(),
  role: userRoleSchema,
  store: z.string().trim().max(120).optional(),
});
export type CreateUserInput = z.infer<typeof createUserSchema>;
