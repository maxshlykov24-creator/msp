import { useEffect, useState } from "react";
import { StoreProvider } from "./store";
import { Layout, type Route } from "./components/Layout";
import type { DealKind } from "./data/types";
import { DealsBoard } from "./views/DealsBoard";
import { NewDeal } from "./views/NewDeal";
import { Certificates } from "./views/Certificates";
import { EdwinQueue } from "./views/EdwinQueue";
import { MishaQueue } from "./views/MishaQueue";
import { SaryScreen } from "./views/SaryScreen";
import { Dashboard } from "./views/Dashboard";
import { ShiftClose } from "./views/ShiftClose";
import { ProductCheck } from "./views/ProductCheck";
import { TasksMovements } from "./views/TasksMovements";
import { HistoryScreen, RolesScreen } from "./views/AuditRoles";
import { SettingsScreen } from "./views/SettingsScreen";

function Screens({
  route,
  setRoute,
  kind,
  sourceDealNumber,
  boardGroup,
  boardStatus,
  boardDealNumber,
  origin,
  navSeq,
  goBoard,
  goNew,
}: {
  route: Route;
  setRoute: (r: Route) => void;
  kind?: DealKind | null;
  sourceDealNumber?: number | null;
  boardGroup?: string | null;
  boardStatus?: string | null;
  boardDealNumber?: number | null;
  origin?: string | null;
  navSeq: number;
  goBoard: (group: string, status: string) => void;
  goNew: (kind?: DealKind, sourceNumber?: number) => void;
}) {
  switch (route) {
    case "board":
      return (
        <DealsBoard
          onNew={() => goNew()}
          onReturnExchange={(k, n) => goNew(k, n)}
          initialGroup={boardGroup}
          initialStatus={boardStatus}
          onBoardChange={goBoard}
          initialDealNumber={boardDealNumber}
          origin={origin}
        />
      );
    case "new":
      // key по счётчику навигации: повторный клик «Новая заявка» из открытой
      // формы возвращает к выбору вида, а не оставляет заполненный черновик.
      return (
        <NewDeal
          key={`new-${navSeq}`}
          onClose={() => setRoute("board")}
          initialKind={kind}
          sourceDealNumber={sourceDealNumber}
        />
      );
    case "certificates":
      return <Certificates />;
    case "sary":
      return <SaryScreen />;
    // Заявку открываем с пометкой, откуда пришли: по закрытию карточки
    // возвращаемся в ту же очередь, а не на «Все заявки».
    case "queue":
      return <EdwinQueue />;
    case "misha":
      return <MishaQueue />;
    case "shift":
      return <ShiftClose />;
    case "dashboard":
      return <Dashboard />;
    case "products":
      return <ProductCheck />;
    case "suits":
      return <ProductCheck initialSection="1. Костюмы" />;
    case "tasks":
      return <TasksMovements />;
    case "history":
    case "audit":
      return <HistoryScreen />;
    case "roles":
      return <RolesScreen />;
    case "settings":
      return <SettingsScreen />;
  }
}

const ROUTES: Route[] = [
  "board",
  "new",
  "certificates",
  "sary",
  "queue",
  "misha",
  "shift",
  "dashboard",
  "products",
  "suits",
  "tasks",
  "history",
  "roles",
  "settings",
  "audit",
];

interface HashState {
  route: Route;
  kind: DealKind | null;
  sourceDealNumber: number | null;
  boardGroup: string | null;
  boardStatus: string | null;
  boardDealNumber: number | null;
  /** Откуда открыли заявку — туда и вернём по закрытию карточки. */
  origin: string | null;
}

const EMPTY: Omit<HashState, "route"> = {
  kind: null,
  sourceDealNumber: null,
  boardGroup: null,
  boardStatus: null,
  boardDealNumber: null,
  origin: null,
};

function parseHash(): HashState {
  const raw = window.location.hash.replace("#", "");
  const parts = raw.split("/");
  const base = parts[0];
  const route = (ROUTES.includes(base as Route) ? base : "board") as Route;
  if (route === "board") {
    return {
      ...EMPTY,
      route,
      boardGroup: parts[1] || null,
      boardStatus: parts[2] || null,
      boardDealNumber: parts[3] && Number.isFinite(Number(parts[3])) ? Number(parts[3]) : null,
      origin: parts[4] || null,
    };
  }
  if (route === "new") {
    const kind = (parts[1] as DealKind) || null;
    const n = parts[2] ? Number(parts[2]) : NaN;
    return {
      ...EMPTY,
      route,
      kind,
      sourceDealNumber: Number.isFinite(n) ? n : null,
    };
  }
  return { ...EMPTY, route };
}

export default function App() {
  const [state, setState] = useState<HashState>(parseHash);
  const [navSeq, setNavSeq] = useState(0);

  useEffect(() => {
    const onHash = () => setState(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const setRoute = (r: Route) => {
    setNavSeq((n) => n + 1);
    setState({ ...EMPTY, route: r });
    window.location.hash = r;
  };
  const goBoard = (group: string, status: string) => {
    setState({ ...EMPTY, route: "board", boardGroup: group, boardStatus: status });
    window.location.hash = `board/${group}/${status}`;
  };
  const goNew = (kind?: DealKind, sourceNumber?: number) => {
    setNavSeq((n) => n + 1);
    const hash = kind
      ? sourceNumber != null
        ? `new/${kind}/${sourceNumber}`
        : `new/${kind}`
      : "new";
    setState({
      ...EMPTY,
      route: "new",
      kind: kind ?? null,
      sourceDealNumber: sourceNumber ?? null,
    });
    window.location.hash = hash;
  };

  return (
    <StoreProvider>
      <Layout
        route={state.route}
        setRoute={setRoute}
        onBoard={goBoard}
        onHome={() => goBoard("all", "all")}
      >
        <Screens
          route={state.route}
          setRoute={setRoute}
          kind={state.kind}
          sourceDealNumber={state.sourceDealNumber}
          boardGroup={state.boardGroup}
          boardStatus={state.boardStatus}
          boardDealNumber={state.boardDealNumber}
          origin={state.origin}
          navSeq={navSeq}
          goBoard={goBoard}
          goNew={goNew}
        />
      </Layout>
    </StoreProvider>
  );
}
