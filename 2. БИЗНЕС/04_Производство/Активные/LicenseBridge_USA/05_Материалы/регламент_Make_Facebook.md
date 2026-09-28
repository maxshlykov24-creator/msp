# Регламент: новая форма Facebook → Kommo (Make)

**Полная инструкция:** [`инструкция/Make_Facebook_Kommo.html`](../инструкция/Make_Facebook_Kommo.html)

## Чеклист на одну форму

1. Make → Scenarios → **Create a new scenario**
2. Три модуля: **Watch Leads** → **Create a Contact** → **Create a Lead**
3. Facebook: Connection **FB Feeder A**, Page **License Bridge USA**, Form = нужная форма, **Limit = 1**
4. Contact: Full name + Phone + **Email** из Facebook (Email: Key `WORK`, значение `Field data: Email`); Created/Updated/Responsible = **Pavel**. Без email рассылка по новой заявке не уходит.
5. Если контакт с этим телефоном уже есть, почту из заявки всё равно дописать (Update a Contact), когда в карточке её нет. Одного примечания «повтор» мало.
6. Lead: Name **FB NY** / **FB CA**; Pipeline + **Новая заявка**; Contacts = Contact ID; utm_source **fb**, utm_campaign **ny** / **ca**. Калифорнийская форма не может остаться на сценарии с именем FB NY и utm `la`.
7. Имя сценария: `FB Feeder A (L B USA название формы)`
8. **Run once** → проверка в Kommo: в контакте есть телефон и email из формы → ON + **Every 15 minutes**

## Критично

- Limit = 1 (иначе ошибки Kommo)
- Contacts в Lead — Map ON, Contact ID из Create a Contact
- Email в Create a Contact — Map ON, Key WORK. Проверка теста: в контакте есть и телефон, и email из формы. Пустой email = сценарий не готов.
- Новая форма без своего сценария в CRM не приходит. Клон и ON до запуска рекламы, не после.
- Аккаунт B не используется — только FB Feeder A
