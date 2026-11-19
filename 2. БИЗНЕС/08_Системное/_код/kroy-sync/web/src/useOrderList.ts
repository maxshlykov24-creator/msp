import {useCallback, useEffect, useReducer, useRef, useState} from 'react';

type Page = {rows: Record<string, any>[], total: number, counts: Record<string,number>, page: number, per_page: number};
type Cached = {data: Page, scope: string, at: number};
export type ReadOrders = (path: string, signal?: AbortSignal) => Promise<any>;
const freshFor = 30_000;

export function useOrderList(read: ReadOrders, scope: string, status: string, page: number, valid: boolean, query: string) {
  const key = scope+'&'+new URLSearchParams({status,page:String(page),compact:'true'});
  const cache = useRef(new Map<string,Cached>());
  const active = useRef({key,scope,valid,read});
  active.current = {key,scope,valid,read};
  const flight = useRef<{key:string, controller:AbortController} | null>(null);
  const previousQuery = useRef(query);
  const [,render] = useReducer(n=>n+1,0);
  const [failure,setFailure] = useState({key:'',message:''});

  const load = useCallback(async (force=false) => {
    const {key,scope,valid,read} = active.current;
    if (!valid || flight.current?.key===key) return;
    const cached=cache.current.get(key);
    if (!force && cached && Date.now()-cached.at<freshFor) return;
    flight.current?.controller.abort();
    const controller = new AbortController();
    flight.current = {key,controller};
    setFailure(old=>old.key===key?{key:'',message:''}:old);
    try {
      const data:Page = await read('orders?'+key,controller.signal);
      if (controller.signal.aborted || active.current.key!==key) return;
      const pages=cache.current;
      pages.delete(key);
      pages.set(key,{data,scope,at:Date.now()});
      if (pages.size>32) pages.delete(pages.keys().next().value!);
      render();
    } catch(e) {
      if (!controller.signal.aborted && active.current.key===key)
        setFailure({key,message:e instanceof Error?e.message:String(e)});
    } finally {
      if (flight.current?.controller===controller) flight.current=null;
    }
  },[]);

  useEffect(()=>{
    const changedQuery = previousQuery.current!==query;
    previousQuery.current=query;
    if (changedQuery && query && !cache.current.has(key)) {
      const timer=window.setTimeout(()=>void load(),220);
      return ()=>{window.clearTimeout(timer);flight.current?.controller.abort();flight.current=null};
    }
    void load();
    return ()=>{flight.current?.controller.abort();flight.current=null};
  },[key,valid,query,load]);

  const invalidate = useCallback(()=>{
    for (const [key,cached] of cache.current) {
      if (key===active.current.key) cached.at=0;
      else cache.current.delete(key);
    }
    // A request started before the new snapshot cannot refresh the cache.
    flight.current?.controller.abort();
    flight.current=null;
    return load(true);
  },[load]);

  const cached=valid?cache.current.get(key):undefined;
  let counts:Page['counts']|undefined;
  if (valid) {
    for (const entry of cache.current.values()) if (entry.scope===scope) counts=entry.data.counts;
  }
  return {data:cached?.data, counts, error:valid&&failure.key===key?failure.message:'', load, invalidate};
}
