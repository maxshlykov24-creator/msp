# Регламент: новая форма Facebook → Kommo (Make)

**Полная инструкция:** [`инструкция/Make_Facebook_Kommo.html`](../инструкция/Make_Facebook_Kommo.html)

## Чеклист на одну форму

1. Make → Scenarios → **Create a new scenario**
2. Три модуля: **Watch Leads** → **Create a Contact** → **Create a Lead**
3. Facebook: Connection **FB Feeder A**, Page **License Bridge USA**, Form = нужная форма, **Limit = 1**
4. Contact: Full name + Phone из Facebook; Created/Updated/Responsible = **Pavel**
5. Lead: Name **FB NY** / **FB CA**; Pipeline + **Новая заявка**; Contacts = Contact ID; utm_source **fb**, utm_campaign **ny** / **ca**
6. Имя сценария: `FB Feeder A (L B USA название формы)`
7. **Run once** → проверка в Kommo → ON + **Every 15 minutes**

## Критично

- Limit = 1 (иначе ошибки Kommo)
- Contacts в Lead — Map ON, Contact ID из Create a Contact
- Аккаунт B не используется — только FB Feeder A
