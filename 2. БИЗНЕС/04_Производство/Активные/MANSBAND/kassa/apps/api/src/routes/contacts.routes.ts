import type { FastifyInstance } from "fastify";
import * as amo from "../clients/amo.js";

// Подстановка имени клиента из amoCRM по телефону (используется при вводе
// телефона в формах кассы — «Телефон → имя»).

export default async function contactsRoutes(app: FastifyInstance) {
  app.get("/contacts/by-phone", { preHandler: [app.authenticate] }, async (req, reply) => {
    const { phone } = req.query as { phone?: string };
    if (!phone || phone.replace(/\D/g, "").length < 10) {
      return reply.code(400).send({ message: "Нужен телефон (минимум 10 цифр)" });
    }
    const contact = await amo.findContactByPhone(phone).catch(() => null);
    if (!contact) return reply.code(404).send({ message: "Контакт не найден" });
    return { id: contact.id, name: contact.name };
  });
}
