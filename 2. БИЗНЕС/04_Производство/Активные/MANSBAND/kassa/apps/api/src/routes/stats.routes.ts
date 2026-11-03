import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { dealStoreSchema } from "@kassa/shared";
import { appendAudit } from "../services/audit.js";
import { summary, cashShift, closeCashShift } from "../services/stats.js";

const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const periodSchema = z.object({ from: day.optional(), to: day.optional() });

export default async function statsRoutes(app: FastifyInstance) {
  const canStore = (user: {role:string;store?:string}, store:string) => ["admin","rop"].includes(user.role) || user.store === store;
  app.get("/shifts/current",{preHandler:[app.requireRoles(["admin","rop","consultant","seller"])]},async(req,reply)=>{
    const parsed=z.object({store:dealStoreSchema}).safeParse(req.query);
    if(!parsed.success)return reply.code(400).send({message:"Выберите магазин"});
    if(!canStore(req.user,parsed.data.store))return reply.code(403).send({message:"Доступен только ваш магазин"});
    return cashShift(parsed.data.store);
  });
  app.post("/shifts/close",{preHandler:[app.requireRoles(["admin","rop","consultant","seller"])]},async(req,reply)=>{
    const amount=z.number().finite().nonnegative().max(100000000).refine(v=>Math.abs(v*100-Math.round(v*100))<0.0001);
    const parsed=z.object({store:dealStoreSchema,cutoff:z.string().datetime(),previousId:z.string().uuid().nullable(),openingCash:amount,countedCash:amount,adjustment:z.number().finite().min(-100000000).max(100000000),reason:z.string().max(500)}).safeParse(req.body);
    if(!parsed.success)return reply.code(400).send({message:"Проверьте суммы сверки"});
    if(!canStore(req.user,parsed.data.store))return reply.code(403).send({message:"Доступен только ваш магазин"});
    const age=Date.now()-new Date(parsed.data.cutoff).getTime();
    if(age<0||age>3600000)return reply.code(409).send({message:"Обновите данные сверки"});
    const result=await closeCashShift(parsed.data,req.user);
    await appendAudit({actor:{id:req.user.sub,name:req.user.name,role:req.user.role},action:"shift.closed",entityType:"shift",entityId:result.id,after:result,metadata:{reason:parsed.data.reason,store:parsed.data.store}});
    return result;
  });

  app.get("/stats/summary", { preHandler: [app.authenticate] }, async (req, reply) => {
    const parsed = periodSchema.safeParse(req.query);
    if (!parsed.success) return reply.code(400).send({ message: "Некорректный период" });
    // Роль решает, чьи цифры отдаём: консультанту — только его заявки.
    return summary(parsed.data, { name: req.user.name, role: req.user.role });
  });
}
