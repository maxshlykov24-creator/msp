// Типы данных кассы. Форма приближена к amoCRM (сделка) + МойСклад (товар),
// чтобы мок-слой можно было заменить на реальный API без переделки UI.

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
  // Слив, который привёл к заявке (сворачиваемый блок в Продаже)
  meetingDate?: string;
  // Чаевые (передаются через очередь Эдвина)
  tips?: number;
  // Скидка на весь чек (₽), применённая поверх позиционных
  checkDiscount?: number;
  // Продажа компании (юрлицо)
  companyName?: string;
  invoiceNo?: string;
  invoicePaid?: boolean;
  issued?: boolean;
  managerName?: string;
  managerPhone?: string;
  deferredUntil?: string;
  // Обещание
  actualUntil?: string;
  // Сертификат
  receivedAt?: string;
  validUntil?: string;
  photoAttached?: boolean;
  // Доставка (СДЭК)
  deliveryCity?: string;
  recipientName?: string;
  recipientPhone?: string;
  // Возврат / обмен — привязка к исходной заявке
  linkedDealNumber?: number;
  returnStatus?: "issued" | "pending";
  returnDestination?: string;
  // Сдача / Эдвин
  changeStatus?: "issued" | "pending";
  changeDestination?: string;
  // Дата оплаты (ручная)
  paymentDate?: string;
  // История действий по заявке (таймлайн)
  history?: HistoryEntry[];
  total: number;
  paid: number;
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

// Сары — реферальные выплаты старым клиентам за совет (новый клиент по рекомендации)
export interface SaryPayout {
  id: string;
  client: string; // кому платим (старый клиент-рекомендатель)
  phone: string;
  amount: number;
  reason: string; // за кого / по какой заявке
  refDealNumber?: number;
  status: "sent" | "pending";
  screenshotAttached: boolean;
  createdAt: string;
}
