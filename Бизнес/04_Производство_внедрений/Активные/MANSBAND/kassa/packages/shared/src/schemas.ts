import { z } from "zod";

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

export const cartItemSchema = z.object({
  productId: z.string(),
  name: z.string(),
  price: z.number(),
  qty: z.number().int().positive(),
  discountPct: z.number().min(0).max(100).optional(),
  discountRub: z.number().min(0).optional(),
  isGift: z.boolean().optional(),
  noPrice: z.boolean().optional(),
});

export const paymentSchema = z.object({
  id: z.string(),
  methodId: z.string(),
  amount: z.number(),
  certificateNumber: z.string().optional(),
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
});
export type CreateSaleInput = z.infer<typeof createSaleSchema>;

export const updateStageSchema = z.object({
  stage: z.string().min(1),
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
  q: z.string().min(1).max(128),
  store: z.string().optional(),
  limit: z.coerce.number().int().min(1).max(100).default(30),
});

export const redeemCertificateSchema = z.object({
  number: z.string().min(1),
  amount: z.number().positive(),
});

export const queueItemSchema = z.object({
  kind: z.enum(["change", "refund"]),
  dealNumber: z.number(),
  client: z.string(),
  amount: z.number(),
  destination: z.string(),
});
