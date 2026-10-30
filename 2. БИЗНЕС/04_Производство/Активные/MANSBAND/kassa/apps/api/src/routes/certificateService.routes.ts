import type {FastifyInstance} from "fastify";
import {timingSafeEqual} from "node:crypto";
import {getEnv} from "../env.js";
import {issueFromAmo,pendingSources} from "../services/certificateIssuer.js";
export default async function routes(app:FastifyInstance){
 const authorize=async(req:import("fastify").FastifyRequest,reply:import("fastify").FastifyReply)=>{
 const key=getEnv().CERTIFICATE_SERVICE_KEY;const supplied=String(req.headers["x-certificate-key"]??"");
 if(!key||Buffer.byteLength(key)!==Buffer.byteLength(supplied)||!timingSafeEqual(Buffer.from(key),Buffer.from(supplied)))return reply.code(403).send({message:"Доступ запрещён"});};
 app.get("/internal/certificates/pending",{preHandler:[authorize]},async(req,reply)=>{const since=Number((req.query as {since?:string}).since);if(!Number.isFinite(since)||since<=0||since>4e9)return reply.code(400).send({message:"Нужна дата начала очереди"});return {items:await pendingSources(since)};});
 app.post("/internal/certificates/issue",{preHandler:[authorize]},async(req,reply)=>{
 const id=(req.body as {amoLeadId?:unknown})?.amoLeadId;if(!Number.isSafeInteger(id)||Number(id)<=0)return reply.code(400).send({message:"Некорректная сделка"});
 try{return await issueFromAmo(Number(id));}catch(error){return reply.code(409).send({message:error instanceof Error?error.message:"Требуется сверка"});}
 });
}
