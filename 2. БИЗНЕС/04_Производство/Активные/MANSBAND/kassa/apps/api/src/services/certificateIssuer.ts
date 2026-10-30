import * as amo from "../clients/amo.js";
import {getByAmoLeadId} from "./deals.js";
import {ensureCertificate,reserveNumber} from "./certificateRegistry.js";
import {moscowTodayYmd} from "@kassa/shared";
import {db} from "../db/index.js";
import {sql} from "drizzle-orm";

export function anniversary(start:string):string {
  const [year,month,day]=start.split("-").map(Number);const y=year!+1;const last=new Date(Date.UTC(y,month!,0)).getUTCDate();
  return `${y}-${String(month).padStart(2,"0")}-${String(Math.min(day!,last)).padStart(2,"0")}`;
}
export async function issueFromAmo(id:number) {
 const lead=await amo.getLead(id);if(!lead||lead.pipeline_id!==9601214||![76675038,142].includes(lead.status_id))throw new Error("Не подтверждена оплата сертификата");
 const field=(id:number)=>lead.custom_fields_values?.find(f=>f.field_id===id)?.values?.map(v=>v.value)??[];
 if(field(3828225).length!==1||![true,1,"true","1"].includes(field(3828225)[0] as boolean))throw new Error("Это не электронный сертификат");
 const local=await getByAmoLeadId(id);
 if(local && local.paid<local.total)throw new Error("Нет полной оплаты в кассе");
 const original=String(field(3135463)[0]??"").trim();
 const reserved=await reserveNumber(`amo:32417154:${id}`,original,local?.number);
 if(original!==reserved)await amo.updateLead(id,{customFields:[{field_id:3135463,values:[{value:reserved}]}]});
 const raw=Number(field(3829442)[0]);if(!Number.isFinite(raw)||raw<=0)throw new Error("Нет даты начала сертификата");
 const start=new Date(raw*1000+3*3600000).toISOString().slice(0,10);const until=anniversary(start);
 const certLine=local?.items.find(x=>x.productId==="cert"||/^Сертификат\s*№/i.test(x.name));
 const nominal=certLine?.price??lead.price;if(!Number.isFinite(nominal)||nominal<=0)throw new Error("Не определён номинал");
 if(local && local.paid<local.total)throw new Error("Нет полной оплаты в кассе");
 const prior=reserved;
 const certificate=await ensureCertificate(`amo:32417154:${id}`,{number:prior,nominal,balance:nominal,status:"active",type:"digital",guestName:String(field(3185675)[0]??""),buyerDealNumber:local?.number,issuedAt:moscowTodayYmd(),validFrom:start,validUntil:until},prior);
 if(certificate.validFrom!==start || certificate.validUntil!==until || certificate.nominal!==nominal)throw new Error("Данные CRM отличаются от выпущенного сертификата; нужна сверка");
 const stamp=Math.floor(Date.parse(`${until}T00:00:00+03:00`)/1000);
 if(prior!==certificate.number||Number(field(3169911)[0])!==stamp) await amo.updateLead(id,{customFields:[{field_id:3135463,values:[{value:certificate.number}]},{field_id:3169911,values:[{value:stamp}]}]});
 const fresh=await amo.getLead(id);const number=fresh?.custom_fields_values?.find(f=>f.field_id===3135463)?.values?.[0]?.value;
 if(number!==certificate.number)throw new Error("Запись номера в CRM не подтверждена; повтор сохранит тот же номер");
 return certificate;
}
export async function pendingSources(){return Array.from(await db.execute(sql`SELECT n.source_key,n.created_at FROM certificate_numbers n LEFT JOIN certificates c ON c.number=n.number WHERE n.source_key LIKE 'amo:32417154:%' AND (c.type='digital' OR c.number IS NULL) ORDER BY n.created_at DESC LIMIT 1000`));}
