import { useState } from "react";
import { StoreProvider } from "./store";
import { Layout, type Route } from "./components/Layout";
import type { DealKind } from "./data/types";
import { DealsBoard } from "./views/DealsBoard";
import { NewDeal } from "./views/NewDeal";
import { Certificates } from "./views/Certificates";
import { EdwinQueue } from "./views/EdwinQueue";
import { SaryScreen } from "./views/SaryScreen";
import { Dashboard } from "./views/Dashboard";
import { ShiftClose } from "./views/ShiftClose";

function Screens({
  route,
  setRoute,
  kind,
  boardGroup,
  boardStatus,
  goBoard,
}: {
  route: Route;
  setRoute: (r: Route) => void;
  kind?: DealKind | null;
  boardGroup?: string | null;
  boardStatus?: string | null;
  goBoard: (group: string, status: string) => void;
}) {
  switch (route) {
    case "board":
      return (
        <DealsBoard
          onNew={() => setRoute("new")}
          initialGroup={boardGroup}
          initialStatus={boardStatus}
          onBoardChange={goBoard}
        />
      );
    case "new":
      return <NewDeal onClose={() => setRoute("board")} initialKind={kind} />;
    case "certificates":
      return <Certificates />;
    case "sary":
      return <SaryScreen />;
    case "queue":
      return <EdwinQueue />;
    case "shift":
      return <ShiftClose />;
    case "dashboard":
      return <Dashboard />;
  }
}

const ROUTES: Route[] = ["board", "new", "certificates", "sary", "queue", "shift", "dashboard"];

interface HashState {
  route: Route;
  kind: DealKind | null;
  boardGroup: string | null;
  boardStatus: string | null;
}

function parseHash(): HashState {
  const raw = window.location.hash.replace("#", "");
  const parts = raw.split("/");
  const base = parts[0];
  const route = (ROUTES.includes(base as Route) ? base : "board") as Route;
  if (route === "board") {
    return { route, kind: null, boardGroup: parts[1] || null, boardStatus: parts[2] || null };
  }
  return { route, kind: (parts[1] as DealKind) || null, boardGroup: null, boardStatus: null };
}

export default function App() {
  const [state, setState] = useState<HashState>(parseHash);
  const setRoute = (r: Route) => {
    setState({ route: r, kind: null, boardGroup: null, boardStatus: null });
    window.location.hash = r;
  };
  const goBoard = (group: string, status: string) => {
    setState({ route: "board", kind: null, boardGroup: group, boardStatus: status });
    window.location.hash = `board/${group}/${status}`;
  };
  return (
    <StoreProvider>
      <Layout route={state.route} setRoute={setRoute} onBoard={goBoard} boardGroup={state.boardGroup}>
        <Screens
          route={state.route}
          setRoute={setRoute}
          kind={state.kind}
          boardGroup={state.boardGroup}
          boardStatus={state.boardStatus}
          goBoard={goBoard}
        />
      </Layout>
    </StoreProvider>
  );
}
