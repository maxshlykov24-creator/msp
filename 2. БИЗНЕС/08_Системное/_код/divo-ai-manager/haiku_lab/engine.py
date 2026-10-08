from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

INTENTS = ('availability price vat vin year motor power transmission drive color mileage mileage_verified paint measurements damage damage_severity owners owner_reason taxi service long options battery origin warranty tax legal report photo video seller address hours visit discount credit tradein contact name call human dispute refusal greeting thanks stop is_bot other_cars unknown').split()
PHONE = re.compile(r'(?<!\w)(?:\+?7|8)[\s(\-]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)')
UNKNOWN_LABELS = {'mileage_verified':'проверку пробега', 'measurements':'замеры кузова', 'damage_severity':'степень повреждений и ремонт', 'owner_reason':'причины смены владельцев', 'service':'обслуживание и техническое состояние', 'long':'длину колёсной базы', 'options':'точную комплектацию', 'battery':'проверку и остаточный ресурс батареи', 'origin':'происхождение автомобиля', 'warranty':'гарантию', 'tax':'утильсбор', 'legal':'документы и ограничения', 'unknown':'этот вопрос'}

@dataclass
class Answer:
    text: str = ''
    action: str = 'reply'
    intents: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    cost: float = 0
    raw: str = ''
    reason: str = ''


def redact(text):
    text = PHONE.sub('[телефон]', str(text))
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[email]', text)
    return re.sub(r'@[A-Za-z0-9_]+', '[Telegram]', text)


def history_contact(history):
    return any(m.get('role') == 'user' and (PHONE.search(m.get('content','')) or '[телефон]' in m.get('content','') or '[Telegram]' in m.get('content','')) for m in history)


def validate_plan(raw):
    plan = json.loads(raw)
    if not isinstance(plan, dict) or set(plan) != {'intents'}: raise ValueError('invalid plan fields')
    choices = plan['intents']
    if not isinstance(choices,list) or not 1 <= len(choices) <= 6 or any(x not in INTENTS for x in choices): raise ValueError('invalid intents')
    return list(dict.fromkeys(choices))


def deterministic_intents(text):
    low=text.lower().strip()
    if re.search(r'вы (?:же )?(?:писали|написали|обещали|договорились)|со мной договорились|как было обговорено|клиент прислал фото|скриншот|люди то подключаются|живого (?:человека|менеджера)|дайте (?:человека|менеджера)',low): return ['dispute']
    if re.fullmatch(r'(?:ок|окей|спасибо(?: большое)?|ясно|понятно|удачи(?: вам)?|👍)[\s.!]*',low): return ['thanks']
    if re.fullmatch(r'(?:привет|здравствуйте|доброе утро|добрый (?:день|вечер))[\s.!]*',low): return ['greeting']
    if PHONE.search(text) and not re.search(r'\?|авто|машин|состоя|грм|цен|пробег|вин|отчет|отчёт',low): return ['contact']
    return None


def money(value):
    number = re.sub(r'[^0-9]', '', str(value or ''))
    return f'{int(number):,}'.replace(',',' ') + ' ₽' if number else ''


def render(intents, selected, session, company, text):
    """Only trusted fields/templates become customer text. Model text is never sent."""
    out=Answer(intents=intents)
    history=session.get('messages',[])
    asked_before=bool(session.get('contact_asked'))
    contact=bool(session.get('has_contact')) or history_contact(history) or bool(PHONE.search(text)) or bool(re.search(r'@[A-Za-z0-9_]+',text))
    session['has_contact']=contact
    if any(x in intents for x in ('human','dispute')):
        out.action='handoff';out.reason='спор или просьба подключить человека';return out
    if session.get('paused'):
        out.action='handoff';out.reason='диалог уже передан человеку';return out
    if all(x in ('thanks','stop') for x in intents):out.action='silence';return out
    card=(selected or {}).get('card') or {}
    listing=(selected or {}).get('listing') or {}
    lines=[];unknown=[]
    def fact(key, value, prefix):
        if value and str(value).strip().lower() not in ('нет данных','неизвестно','—','-'):
            value=re.sub(r'\s+', ' ', str(value)).strip()
            if value.count('(')>value.count(')'):value+=')'*(value.count('(')-value.count(')'))
            lines.append(prefix+value);out.facts.append(key);return True
        return False
    def missing(label):
        if label not in unknown:unknown.append(label)
    for intent in intents:
        if intent in UNKNOWN_LABELS:missing(UNKNOWN_LABELS[intent]);continue
        if intent=='greeting':
            lines.append('Здравствуйте!')
            if len(intents)==1:lines.append('Что хотите уточнить по автомобилю?')
            continue
        if intent=='name':lines.append('Приятно познакомиться. Что хотите уточнить по автомобилю?');continue
        if intent=='is_bot':lines.append('Да, я виртуальный помощник салона. Помогу с известными данными по машине');continue
        if intent=='address':fact('company.address',company.get('address'),'Мы находимся: ');continue
        if intent=='hours':fact('company.hours',company.get('hours'),'Работаем ');continue
        if intent=='visit':
            fact('company.hours',company.get('hours'),'Салон открыт ')
            lines.append('Возможность осмотра и тест-драйва именно этой машины нужно согласовать с сотрудником');missing('осмотр и тест-драйв');continue
        if intent=='seller':fact('company.seller',company.get('seller'),'');continue
        if intent=='discount':lines.append('Торг и окончательные условия обсуждаются после осмотра. Сумму скидки заранее подтвердить не могу');continue
        if intent=='credit':fact('company.credit',company.get('credit'),'');continue
        if intent=='tradein':fact('company.tradein',company.get('tradein'),'');missing('оценку автомобиля в обмен');continue
        if intent in ('photo','video'):
            missing('фото' if intent=='photo' else 'видео');continue
        if intent in ('call','contact'):
            if contact:out.action='handoff';out.reason='контакт получен';lines.append('Контакт получил')
            elif session.get('contact_asked'):out.action='handoff';out.reason='повторное уточнение связи'
            else:lines.append('Напишите, пожалуйста, номер телефона для связи');session['contact_asked']=True
            continue
        if intent=='refusal':out.action='handoff';out.reason='клиент хочет продолжить с человеком в чате';continue
        if intent in ('thanks','stop'):continue
        if intent=='other_cars':missing('подбор других автомобилей');continue
        if not selected:
            lines.append('По какому автомобилю вопрос? В тестовом боте выбери машину через /cars или сценарий через /cases');continue
        if intent in ('vin','year','motor','power','transmission','drive','color'):
            labels={'vin':'VIN: ','year':'Год: ','motor':'Двигатель: ','power':'Мощность: ','transmission':'Коробка: ','drive':'Привод: ','color':'Цвет: '}
            if not fact('card.'+intent,card.get(intent),labels[intent]):missing(labels[intent].rstrip(': '))
        elif intent=='price':
            if not fact('listing.price',listing.get('price') or card.get('price_cash'),'Стоимость за наличный расчёт: '):missing('цену')
        elif intent=='vat':
            if not fact('card.price_vat',card.get('price_vat'),'На организацию по расчётному счёту с НДС: '):missing('цену с НДС')
        elif intent=='availability':
            if card.get('price_cash'):lines.append('В тестовом снимке стока автомобиль есть в продаже. Наличие на момент приезда нужно подтвердить');out.facts.append('card.price_cash')
            else:missing('наличие автомобиля')
        elif intent=='mileage':
            if not fact('card.mileage',card.get('mileage'),'Пробег по карточке: '):missing('пробег')
        elif intent=='owners':
            if not fact('card.owners',card.get('owners'),'Владельцев по ПТС: '):missing('число владельцев')
        elif intent=='paint':
            if not fact('card.paint',card.get('paint'),'Окрасы по карточке: '):missing('окрасы кузова')
        elif intent=='damage':
            if not fact('card.damage',card.get('damage'),'По данным отчёта: '):missing('историю ДТП')
        elif intent=='taxi':
            if not fact('card.taxi',card.get('taxi'),'По данным отчёта: '):missing('историю работы в такси')
        elif intent=='report':
            link=card.get('report') or ''
            if re.match(r'^https://(?:www\.)?autoteka\.ru/',link):fact('card.report',link,'')
            else:missing('отчёт по автомобилю')
    if unknown:
        out.unknown=unknown
        lines.append('Нужно уточнить '+', '.join(unknown)+'.')
        if contact or asked_before or 'refusal' in intents or 'legal' in intents:
            out.action='handoff';out.reason='нужны данные сотрудника'
        else:
            lines.append('Напишите, пожалуйста, номер телефона для связи');session['contact_asked']=True
    if not lines and out.action=='reply':out.action='handoff';out.reason='нет безопасного ответа'
    out.text='\n\n'.join(dict.fromkeys(lines))
    return out


async def respond(text, session, selected, company, planner):
    if session.get('paused'):return Answer(action='handoff',reason='диалог уже передан человеку')
    intents=deterministic_intents(text);raw='';cost=0
    if intents is None:
        try:
            raw,cost=await planner(session.get('messages',[]),redact(text))
            intents=validate_plan(raw)
        except Exception as exc:
            result=Answer(action='error',reason='Не удалось получить корректный план Haiku: '+type(exc).__name__)
            return result
    low=text.lower()
    media_request=bool(re.search(r'(?:пришл|скин|отправ|можно|можете|покаж|сдела|прошу|дай|хочу).*?(?:фото|видео)|(?:фото|видео).*?(?:можно|можете|возможно)|^\s*(?:фото|видео)(?:графии|обзор)?(?: авто)?[?!. ]*$',low))
    if not media_request:intents=[x for x in intents if x not in ('photo','video')] or ['unknown']
    result=render(intents,selected,session,company,text);result.raw=raw;result.cost=cost
    session.setdefault('messages',[]).append({'role':'user','content':redact(text)})
    if result.text:session['messages'].append({'role':'assistant','content':result.text})
    if result.action=='handoff':session['paused']=True
    session['messages']=session['messages'][-30:]
    return result
