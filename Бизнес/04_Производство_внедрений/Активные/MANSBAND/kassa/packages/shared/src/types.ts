// Доменные типы кассы. Форма приближена к amoCRM (сделка) + МойСклад (товар),
// чтобы один и тот же тип использовался и фронтом (apps/web), и бэком (apps/api).

export type FunnelType = "offline" | "online" | "defects" | "return";

export type DealKind =
  | "sale"
  | "company"
  | "cert_plastic"
  | "rental"
  | "deferred"
  | "promise"
  | "no_sliv"
  | "sliv"
  | "cert_digital"
  | "delivery"
  | "defect"
  | "drycleaning"
  | "resew"
  | "wrong_size"
  | "refund"
  | "exchange";

export type Store = "На Бауманской" | "На Пятницкой" | "Онлайн-магазин";

export type PaymentKind = "cash" | "card" | "account" | "certificate";

export interface PaymentMethod {
  id: string;
  label: string;
  kind: PaymentKind;
}

export interface Payment {
  id: string;
  methodId: string;
  amount: number;
  certificateNumber?: string;
}

export interface Product {
  id: string;
  name: string;
  sku: string;
  category: string;
  price: number;
  store: string;
  stock: number;
}

export interface CartItem {
  productId: string;
  name: string;
  price: number;
  qty: number;
  discountPct?: number;
  discountRub?: number;
  isGift?: boolean;
  noPrice?: boolean; // для аренды/дефектов
}

export interface Consultant {
  id: string;
  name: string;
  role: "consultant" | "callmanager" | "supply" | "rop" | "finance";
}

export type ChangeStatus = "issued" | "pending";

// Запись истории действий по заявке (таймлайн в карточке)
export interface HistoryEntry {
  at: string; // ISO-датавремя
  who: string; // консультант / система
  action: string; // что произошло
}

export interface Deal {
  id: string;
  number: number;
  createdAt: string;
  funnel: FunnelType;
  kind: DealKind;
  consultant: string;
  referredBy?: string;
  callManager?: string;
  clientName: string;
  clientPhone: string;
  email?: string;
  store: Store;
  storeAddress?: string;
  channel?: string;
  purpose?: string;
  items: CartItem[];
  payments: Payment[];
  stage: string;
  certificateNumber?: string;
  guestName?: string;
  rentalFrom?: string;
  rentalTo?: string;
  issueDate?: string;
  returnDate?: string;
  reservedUntil?: string;
  comment?: string;
  meetingDate?: string;
  tips?: number;
  tipsDestination?: string;
  checkDiscount?: number;
  companyName?: string;
  invoiceNo?: string;
  invoicePaid?: boolean;
  issued?: boolean;
  managerName?: string;
  managerPhone?: string;
  deferredUntil?: string;
  actualUntil?: string;
  receivedAt?: string;
  validUntil?: string;
  photoAttached?: boolean;
  deliveryCity?: string;
  recipientName?: string;
  recipientPhone?: string;
  linkedDealNumber?: number;
  returnStatus?: "issued" | "pending";
  returnDestination?: string;
  changeStatus?: "issued" | "pending";
  changeDestination?: string;
  paymentDate?: string;
  history?: HistoryEntry[];
  total: number;
  paid: number;
  // Связь с amoCRM / МойСклад (заполняется бэкендом)
  amoLeadId?: number;
  msOrderId?: string;
  msDemandId?: string; // отгрузка
  paymentStatus?: string;
}

export type CertStatus = "active" | "used" | "expired";

export interface Certificate {
  number: string;
  nominal: number;
  balance: number;
  status: CertStatus;
  type: "plastic" | "digital";
  guestName?: string;
  buyerDealNumber?: number;
  issuedAt: string;
  validUntil: string;
}

export type QueueKind = "change" | "refund";

export interface QueueItem {
  id: string;
  kind: QueueKind;
  dealNumber: number;
  client: string;
  amount: number;
  destination: string; // телефон/карта + банк
  status: ChangeStatus;
  createdAt: string;
}

export interface SaryPayout {
  id: string;
  client: string;
  phone: string;
  amount: number;
  reason: string;
  refDealNumber?: number;
  status: "sent" | "pending";
  screenshotAttached: boolean;
  createdAt: string;
}

// ── Аутентификация / пользователи ───────────────────────────────────

export type UserRole = "admin" | "seller";

export interface User {
  id: string;
  login: string;
  name: string;
  role: UserRole;
  mustChangePassword: boolean;
}

export interface AuthResponse {
  token: string;
  user: User;
}

// ── Реалтайм (WebSocket) ────────────────────────────────────────────

export type WsEventType =
  | "deal.created"
  | "deal.updated"
  | "deal.stage_changed"
  | "catalog.updated"
  | "certificate.updated"
  | "queue.updated"
  | "ping";

export interface WsEvent<T = unknown> {
  type: WsEventType;
  at: string;
  payload: T;
}
