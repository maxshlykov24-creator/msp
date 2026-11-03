import { useCallback, useEffect, useState } from "react";
import { Wallet, CheckCircle2 } from "lucide-react";
import { Card, Button } from "../components/ui";
import { money } from "../lib/format";
import { api, USE_MOCK } from "../api/client";
import { useStore } from "../store";
import { useAuth } from "../auth/AuthContext";

type Snapshot={store:string;from:string;cutoff:string;previousId:string|null;openingCash:number|null;movement:number;operations:number};
export function ShiftClose(){
  const {activeStore}=useStore();const {user}=useAuth();
  const [snapshot,setSnapshot]=useState<Snapshot|null>(null);
  const [opening,setOpening]=useState("");const [counted,setCounted]=useState("");const [adjustment,setAdjustment]=useState("0");const [reason,setReason]=useState("");
  const [error,setError]=useState("");const [busy,setBusy]=useState(false);const [closed,setClosed]=useState(false);
  const manager=["admin","rop"].includes(user?.role??"");
  const refresh=useCallback(async()=>{setError("");setSnapshot(null);setClosed(false);if(USE_MOCK){setError("Сверка доступна только с подключённой рабочей базой");return;}try{const row=await api.getQuery<Snapshot>("/shifts/current",{store:activeStore});setSnapshot(row);setOpening(row.openingCash==null?"":String(row.openingCash));}catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить движения");}},[activeStore]);
  useEffect(()=>{void refresh();},[refresh]);
  const expected=(Number(opening)||0)+(snapshot?.movement??0)+(Number(adjustment)||0);
  const diff=counted.trim()&&Number.isFinite(Number(counted))?Math.round((Number(counted)-expected)*100)/100:null;
  async function close(){if(!snapshot||busy)return;setBusy(true);setError("");try{await api.post("/shifts/close",{store:activeStore,cutoff:snapshot.cutoff,previousId:snapshot.previousId,openingCash:Number(opening),countedCash:Number(counted),adjustment:Number(adjustment),reason});setClosed(true);}catch(e){setError(e instanceof Error?e.message:"Не удалось сохранить сверку");}finally{setBusy(false);}}
  return <div className="max-w-2xl mx-auto space-y-4"><h1 className="text-2xl font-extrabold text-white flex gap-2"><Wallet/>Закрытие смены</h1>
    <p className="text-mute">Сверка наличных: {activeStore}. Безналичные оплаты не входят в физический остаток кассы.</p>
    {error&&<p role="alert" className="text-red-400">{error}</p>}
    {snapshot&&<Card className="p-4 space-y-4">
      <p className="text-sm text-mute">Движения до {new Date(snapshot.cutoff).toLocaleString("ru-RU",{timeZone:"Europe/Moscow"})}. Более поздние операции попадут в следующую сверку.</p>
      <label className="block">Остаток после предыдущей сверки<input type="number" min="0" step="0.01" className="input" value={opening} disabled={snapshot.openingCash!==null||!manager||closed} onChange={e=>setOpening(e.target.value)}/></label>
      {snapshot.openingCash===null&&<p className="text-sm text-mute">Первый начальный остаток подтверждает руководитель.</p>}
      <div>Проведённые наличные оплаты и выплаты: <strong>{money(snapshot.movement)}</strong> · {snapshot.operations} операций</div>
      <label className="block">Прочие движения наличных: внесение со знаком плюс, расход или инкассация со знаком минус<input type="number" step="0.01" className="input" value={adjustment} disabled={closed} onChange={e=>setAdjustment(e.target.value)}/></label>
      <input className="input" aria-label="Основание движений" placeholder="Основание начального остатка или прочих движений" value={reason} disabled={closed} onChange={e=>setReason(e.target.value)}/>
      <div>Расчётный остаток: <strong>{money(expected)}</strong></div>
      <label className="block">Фактически наличных в кассе<input type="number" min="0" step="0.01" className="input" value={counted} disabled={closed} onChange={e=>setCounted(e.target.value)}/></label>
      {diff!==null&&<p>Расхождение: {money(diff)}</p>}
      {closed?<p className="text-white flex gap-2"><CheckCircle2/>Сверка сохранена. Смена закрыта.</p>:<div className="flex gap-3"><Button disabled={busy} onClick={()=>void refresh()}>Обновить</Button><Button disabled={busy||opening===""||diff!==0||Number(counted)<0||(snapshot.openingCash===null&&!manager)} onClick={()=>void close()}>Подтвердить и закрыть смену</Button></div>}
    </Card>}
  </div>;
}
