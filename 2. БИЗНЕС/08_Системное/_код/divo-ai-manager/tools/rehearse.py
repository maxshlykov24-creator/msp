#!/usr/bin/env python3
"""Репетиции без Telegram: гоняем сценарии через тот же промпт и ту же модель.

Запуск: tools/rehearse.py --live-source [--compare] [--only текст]
Без --live-source запускает старые синтетические сценарии по номерам.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import human, llm, main as bot_main, nudge, prompt  # noqa: E402

SCENARIOS: list[tuple[str, list[str]]] = [
    (
        "Машина есть в стоке",
        [
            "Здравствуйте, Джили Кулрей 2023 белый еще актуален?",
            "Максим. А пробег какой и цена окончательная?",
            "Хорошо, а когда можно приехать посмотреть?",
        ],
    ),
    (
        "Машины нет в стоке",
        [
            "Добрый день! Интересует BMW X5 2021 года, есть у вас?",
            "А что-то похожее по бюджету до 3 миллионов есть?",
        ],
    ),
    (
        "Торг и скидка",
        [
            "Мерседес GLE купе за 12.7 — это много. Скидку какую дадите?",
            "Ну хотя бы примерно скажите, сколько скинете, чтобы я ехал не зря",
        ],
    ),
    (
        "Просит человека",
        [
            "Слушайте, вы бот? Дайте живого менеджера",
        ],
    ),
    (
        "Trade-in и кредит",
        [
            "Хочу Хавал Джолион в кредит, свою Гранту в трейд-ин. Что по ставке?",
            "Гранта 2019, пробег 90 тысяч, один хозяин. Сколько дадите?",
        ],
    ),
    # Ниже — реальные провалы из диалога 5 сентября. Смотрим, что бот больше
    # не выдумывает ТТХ, историю, оценку и проценты, а берёт номер.
    (
        "Чего нет в карточке: запас хода и обкатка",
        [
            "По Нату хочу узнать",
            "Николай. Запас хода какой?",
            "Где ее обкатывали? Пробег по Китаю?",
        ],
    ),
    (
        "Лицензия такси и автотека",
        [
            "Нат смотрю. Лицензия такси есть?",
            "Почему лицензия по автотеке бьется?",
        ],
    ),
    (
        "Кредит на NAT — пометка «только наличка»",
        [
            "Нат могу в кредит купить?",
        ],
    ),
    (
        "Оценка своей машины в чате",
        [
            "Мазду на обмен за сколько возьмете? 2018 год, 2.5 л, 60 тысяч пробег",
            "Напишите здесь цену. О123вм134 номер",
            "Когда ответ ждать?",
        ],
    ),
    (
        "Оплата картой: комиссия эквайринга",
        [
            "Нат хочу картой оплатить по итогу. Можно же так сделать?",
            "Какая комиссия при оплате картой?",
        ],
    ),
    (
        "Адрес один раз за диалог",
        [
            "Где вы находитесь и до скольки работаете?",
            "Хорошо. А Хавал Джолион у вас есть?",
            "Пробег какой у него?",
            "Ладно, подумаю",
        ],
    ),
    (
        "Машина со склада без цены",
        [
            "У вас Range Rover 2017 есть в продаже?",
            "Так а объявление ваше висит. Он что продан?",
        ],
    ),
    # Ниже — созвон с руководителем 8 сентября. Каждый сценарий это ошибка,
    # которую он показал на живом тесте.
    (
        "Ссылка с Авито на Cullinan: машина есть, статус в базе врал",
        [
            "Здравствуйте, у вас Rolls-Royce Cullinan продается?",
            "Вот ваше объявление на авито, ссылка. Почему говорите что нет?",
            "Панамера мне не нужна, мне нужен Cullinan",
        ],
    ),
    (
        "Такси: по отчету разрешение есть",
        [
            "Фольксваген Бора смотрю. Она в такси работала?",
            "А лицензия такси на ней есть?",
        ],
    ),
    (
        "Каршеринг: сглаживаем, но не отрицаем",
        [
            "Здравствуйте, подскажите, Джили Кулрей была в каршеринге или такси?",
            "То есть все таки каршеринг?",
        ],
    ),
    (
        "Оплата картой и переводом",
        [
            "Хавал Джолион хочу взять. Картой оплатить можно?",
            "А переводом на счет тогда?",
        ],
    ),
    (
        "Цена на юрлицо с НДС",
        [
            "Джили Монжаро хочу купить на организацию, с НДС. Сколько выйдет?",
            "А в лизинг можно?",
        ],
    ),
    (
        "Лизинг на машину без подтвержденного НДС",
        [
            "Порше Панамера в лизинг на компанию можно оформить?",
        ],
    ),
    (
        "Trade-in: собираем ссылку и номер, не оценку",
        [
            "Хочу свою машину в обмен сдать. Тойота Камри 2019",
            "Оцените примерно сколько дадите, я потом приеду",
        ],
    ),
    (
        "Торг на 30 тысяч",
        [
            "Мерседес GLS за 10.5. Скиньте 30 тысяч и я забираю",
            "Давайте в чате решим, номер не дам",
        ],
    ),
    (
        "Заведомо нереальный торг",
        [
            "Мерседес GLE купе за 13.5 у вас. Отдадите за 9?",
        ],
    ),
    (
        "Транспортный налог",
        [
            "Какой транспортный налог на Мерседес G-класс будет в год?",
        ],
    ),
    (
        "Окрасы против автотеки",
        [
            "По Хавал Джолион в автотеке половина машины крашеная. Это правда?",
        ],
    ),
    # Эталон переписки от менеджера, 9 сентября. Полный диалог одним
    # сценарием: NAT, кредит, такси, обмен, карта. Ответы сверяем с
    # _ЭТАЛОН/чаты/2026-09-09_эталон_менеджера.md
    (
        "Эталон менеджера: NAT от начала до карты",
        [
            "добрый день!",
            "да. Интересует нат",
            "Николай",
            "пока нет. В кредит продаете?",
            "у меня нет всей суммы, хочу в кредит",
            "они в такси катались?",
            "на обмен мою машину заберете?",
            "пока дистанционно",
            "могу ли оплатить картой?",
        ],
    ),
    # Созвон 8 сентября, вторая волна: «передам менеджеру» ломает роль -
    # клиент пишет продавцу и слышит, что вопрос уходит кому-то другому.
    (
        "Кому уходит контакт: менеджера в чате нет",
        [
            "Джили Монжаро хочу на юрлицо с НДС, посчитайте",
            "Хорошо, мой номер 89261234567",
            "А вы кто тогда? Мне менеджер перезвонит или вы?",
        ],
    ),
    (
        "Клиент не дает номер и требует чат",
        [
            "Кулрей 2023 интересует. Кредит одобрят на нее?",
            "Номер не дам, пишите здесь все условия",
            "Я же сказал, только в чате. Какая ставка будет?",
        ],
    ),
    # Ниже - конверсия. Цель заказчика не номер, а приезд в шоурум: смотрим,
    # что реплика не заканчивается тупиком и зовет посмотреть машину.
    (
        "Простые вопросы: зовем смотреть, номер не трогаем",
        [
            "Здравствуйте, Джили Кулрей 2023 еще в продаже?",
            "А цвет какой и пробег?",
            "Понятно, спасибо",
        ],
    ),
    (
        "«Подумаю» не тупик",
        [
            "Хавал Джолион смотрю. Цена 1 850 000 актуальна?",
            "Ладно, подумаю пока",
        ],
    ),
    (
        "Номер дан: следующий шаг это день визита",
        [
            "Мерседес GLE купе интересует, хочу в кредит",
            "Мой номер 89261234567",
            "Хорошо, а посмотреть когда можно?",
        ],
    ),
    (
        "Машины нет: не бросаем клиента",
        [
            "Тойота Камри 2020 есть у вас?",
            "А что есть похожее?",
        ],
    ),
    (
        "Клиент сам собрался приехать",
        [
            "Порше Макан ваш смотрю. Хочу подъехать посмотреть",
            "Сегодня вечером после работы получится",
        ],
    ),
]


# Изоляция обязательна: ни одна репетиция не пишет клиенту или в CRM.
import argparse
import copy
import json
import logging
import re
import subprocess
import tempfile
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch
from bot import avito_match, avito_loop, crm, store
from bot.config import settings

LIVE_IDS = (
    'av:u2i-19BncfafOIWyAUsTdK6wyw',
    'ar:999830c0629c7ff155fd1c84b24b7955',
    'ar:afaf605021d1266c5c45412ef39d37f1',
    'av:u2i-8QGoaK3Fa2JIu9Wy~TItbg',
)


def ssh_read(program: str, payload: dict | None = None) -> dict:
    """Только явные read-only программы и тестовые запросы LLM, без деплоя."""
    if payload is not None:
        import base64
        program = 'import json,base64\npayload=json.loads(base64.b64decode(%r))\n' % (
            base64.b64encode(json.dumps(payload).encode()).decode(),
        ) + program
    command = 'cd /root/divo-ai-manager && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -'
    result = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-i',
         str(Path.home() / '.ssh/divo_deploy'), 'root@104.171.136.226', command],
        input=program, text=True, capture_output=True, timeout=150,
    )
    if result.returncode:
        raise RuntimeError('Read-only SSH/test failed (exit %s)' % result.returncode)
    return json.loads(result.stdout)


def live_sources() -> tuple[dict, str]:
    data = ssh_read('''
import json
from bot.config import settings
ids = %r
docs = {cid: json.loads((settings.state_dir / (cid + '.json')).read_text()) for cid in ids}
print(json.dumps({'docs': docs, 'stock': (settings.kb / 'сток' / 'СТОК.md').read_text()}))
''' % (LIVE_IDS,))
    # Сохраняем только поля, нужные для воспроизведения, без CRM/контактов.
    docs = {}
    for cid, source in data['docs'].items():
        doc = {'messages': [], 'nudge': {}}
        for channel in ('avito', 'autoru'):
            if channel in source:
                doc[channel] = {k: source[channel].get(k, '') for k in ('title', 'price', 'url')}
        for m in source.get('messages', []):
            text = re.sub(r'(?<!\w)(?:\+?7|8)?[\s(\-]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)', '79000000000', m.get('content', ''))
            text = re.sub(r'@[A-Za-z0-9_]+', '@test_contact', text)
            text = text.replace('Аннет', 'Клиент')
            doc['messages'].append({'role': m['role'], 'content': text})
        docs[cid] = doc
    return docs, data['stock']


class Sandbox:
    def __enter__(self):
        self.stack = ExitStack()
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(prefix='divo-rehearse-'))
        for attr, suffix in [('state_dir', 'state'), ('paused_dir', 'paused')]:
            self.stack.enter_context(patch.object(settings, attr, Path(self.temp) / suffix))
        self.stack.enter_context(patch.object(bot_main, 'pending', {}))
        self.stack.enter_context(patch.object(bot_main, 'llm_fails', {}))
        self.stack.enter_context(patch.object(bot_main, 'type_and_wait', AsyncMock()))
        self.stack.enter_context(patch.object(crm, 'capture', AsyncMock()))
        self.stack.enter_context(patch.object(crm, 'notify_media', AsyncMock()))
        self.stack.enter_context(patch.object(crm, 'client_wrote_again', AsyncMock()))
        self.stack.enter_context(patch.object(crm, 'dismiss_llm_alert', Mock()))
        self.stack.enter_context(patch.object(crm, '_queue_edit', Mock()))
        self.stack.enter_context(patch('httpx.AsyncClient.request', side_effect=AssertionError('NETWORK DISABLED')))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


async def replay(doc: dict, text: str, raw: str | None = None) -> dict:
    cid = 'av:rehearsal'
    with Sandbox():
        store.save_doc(cid, copy.deepcopy(doc))
        channel = Mock()
        sent = []
        channel.send = AsyncMock(side_effect=lambda c, t: sent.append(t))
        channel.typing = AsyncMock()
        channel.notify_owner = AsyncMock()
        with ExitStack() as stack:
            if raw is not None:
                stack.enter_context(patch.object(llm, 'reply', AsyncMock(return_value=raw)))
            await bot_main._answer_locked(channel, cid, [text])
        return {'sent': sent, 'paused': store.is_paused(cid),
                'capture': crm.capture.await_count, 'history': store.load_history(cid)}


def before(doc: dict, needle: str) -> tuple[dict, str]:
    doc = copy.deepcopy(doc)
    for i, m in enumerate(doc['messages']):
        if m['role'] == 'user' and needle.lower() in m['content'].lower():
            doc['messages'] = doc['messages'][:i]
            return doc, m['content']
    raise AssertionError('Live case missing: ' + needle)


async def regression(docs: dict, stock: str) -> None:
    checks = 0
    def check(ok, label):
        nonlocal checks
        assert ok, label
        checks += 1
        print('PASS', label, flush=True)

    with patch.object(avito_match, '_stock_text', lambda text='': text or stock):
        for needle, raw in [
            ('Завтра могу на тест драйв', 'Да, конечно, завтра можно приехать, с 10:00 до 20:00 ждём'),
            ('Течение часа', 'Хорошо, ждём вас, на месте обсудим цену'),
        ]:
            doc, text = before(docs[LIVE_IDS[0]], needle)
            result = await replay(doc, text, raw)
            check(bool(result['sent']) and 'Цена в объявлении' not in ' '.join(result['sent']), 'visit: ' + needle)
            check(result['history'][-1]['content'] == result['sent'][-1], 'sent text stored')
        doc, text = before(docs[LIVE_IDS[3]], 'Люди то подключаются')
        result = await replay(doc, text, '[[ЧЕЛОВЕК]]')
        check(result['paused'] and result['capture'] == 1 and not result['sent'], 'handoff without phone')
        for raw in ('[[МЕДИА:отчёт]]', '[[ДЕЙСТВИЕ:неизвестно]]', 'Цена 9 999 000 рублей.'):
            result = await replay(doc, 'Есть новости?', raw)
            check(result['paused'] and not result['sent'], 'unsupported action / filtered answer: ' + raw)
        for cid in LIVE_IDS[1:3]:
            listing = avito_match.attached_listing(docs[cid])
            check(avito_match.match_card(listing['title'], listing['price']) is None, 'Monjaro must not attach: ' + cid[:8])
            check('70 007' not in avito_match.focus_from_doc(docs[cid]), 'foreign mileage excluded')
        doc, text = before(docs[LIVE_IDS[1]], 'Можно отправить отчет')
        result = await replay(doc, text, 'Пришлю отчёт')
        check(result['paused'] and result['capture'] == 1 and not result['sent'], 'missing report handed off')
        for needle in ('Причина продали', 'Продажа салонная'):
            doc, text = before(docs[LIVE_IDS[2]], needle)
            result = await replay(doc, text)
            check(result['sent'] == [prompt.seller_answer().rstrip('.')], 'KB seller answer, no LLM: ' + needle)
        with Sandbox():
            cid = 'av:staff'
            store.save_doc(cid, {'messages': [], 'crm': {'out_ids': ['bot-1']}})
            own = {'id': 'bot-1', 'direction': 'out', 'type': 'text', 'created': 1, 'content': {'text': 'Приезжайте'}}
            foreign = {'id': 'staff-1', 'direction': 'out', 'type': 'text', 'created': 2, 'content': {'text': 'Автомобиль продан'}}
            check(not crm.on_foreign_out(cid, own), 'bot echo ignored')
            check(crm.on_foreign_out(cid, foreign), 'staff message accepted without alert')
            check(store.is_paused(cid) and store.load_history(cid)[-1]['content'] == 'Автомобиль продан', 'staff history and pause')
            check(not crm.on_foreign_out(cid, foreign) and len(store.load_history(cid)) == 1, 'staff message idempotent')
            channel = Mock(send=AsyncMock(), typing=AsyncMock())
            await bot_main._answer_locked(channel, cid, ['Не приехать?'])
            check(channel.send.await_count == 0, 'no invitation after staff takeover')
        for reason in ('handoff', 'stuck'):
            doc = {}
            crm._start_alert(doc, {'reason': reason, 'phone': ''}, True)
            check(doc['crm']['alert']['active'], 'CRM alert without phone: ' + reason)
        for final_direction in ('out', 'in'):
            with Sandbox():
                cid = 'poll-test'
                state = {'allow': [cid], 'cursor': {cid: 1}}
                store.save_doc('av:' + cid, {'messages': []})
                events = [
                    {'id': 'i1', 'created': 2, 'direction': 'in', 'type': 'text', 'content': {'text': 'Еду'}},
                    {'id': 'o1', 'created': 3, 'direction': 'out', 'type': 'text', 'content': {'text': 'Автомобиль продан'}},
                ]
                if final_direction == 'in':
                    events.append({'id': 'i2', 'created': 4, 'direction': 'in', 'type': 'text', 'content': {'text': 'Не приехать?'}})
                api = Mock(chats=AsyncMock(return_value=[{'id': cid, 'last_message': events[-1]}]),
                           messages=AsyncMock(return_value=list(reversed(events))))
                schedule = Mock()
                with patch.object(avito_loop, 'load_state', return_value=state), \
                     patch.object(avito_loop, 'save_state', Mock()), \
                     patch.object(avito_loop, 'remember_listing', AsyncMock()):
                    await avito_loop.poll_once(api, Mock(), schedule, {})
                expected = [e['content']['text'] for e in events]
                check([m['content'] for m in store.load_history('av:' + cid)] == expected,
                      'poll keeps chronology, latest=' + final_direction)
                check(store.hard_paused('av:' + cid) and not schedule.called,
                      'poll stops bot, latest=' + final_direction)
        # Положительный контроль: точная карточка должна остаться доступной.
        card = next((c for c in avito_match._cards(stock) if c.get('km') and c.get('year')), None)
        assert card
        title = '%s, %s км' % (card['title'], card['km'])
        check(avito_match.match_card(title, stock=card['raw']) is not None, 'exact match kept')
        check(avito_match.match_card(title, stock=card['raw'] + '\n' + card['raw']) is None, 'ambiguous match refused')
        report_card = next((c for c in avito_match._cards(stock)
                            if c.get('km') and c.get('year') and (c.get('Автотека') or '').startswith('http')), None)
        assert report_card, 'Live stock has no report link for positive control'
        doc = {'messages': [], 'avito': {
            'title': '%s, %s км' % (report_card['title'], report_card['km']),
            'price': str(report_card.get('price') or ''),
        }}
        with patch.object(avito_match, '_stock_text', return_value=report_card['raw']):
            result = await replay(doc, 'Пришлите отчёт автотеки')
        check(report_card['Автотека'] in ' '.join(result['sent']) and not result['paused'], 'existing report sent without LLM')
        with Sandbox():
            cid = 'av:race'
            store.save_doc(cid, {'messages': []})
            msg = {'id': 'staff-race', 'created': 1, 'direction': 'out', 'type': 'text', 'content': {'text': 'Автомобиль продан'}}
            channel = Mock(send=AsyncMock(), typing=AsyncMock())
            async def takeover(*args, **kwargs):
                crm.on_foreign_out(cid, msg)
            with patch.object(llm, 'reply', AsyncMock(return_value='Приезжайте')), \
                 patch.object(bot_main, 'type_and_wait', takeover):
                await bot_main._answer_locked(channel, cid, ['Можно приехать?'])
            check(not channel.send.called and store.load_history(cid)[-1]['content'] == 'Автомобиль продан', 'staff takeover during typing cancels send')
    print('REGRESSION', checks, 'passed', flush=True)


async def remote_model(model: str, system: str, history: list[dict]) -> dict:
    # Тот же провайдер и параметры, без llm.reply fallback: сравниваем заданную модель.
    program = '''
import asyncio,json,time,httpx
from bot.config import settings
from bot import llm
async def run():
 body=llm._openrouter_body(payload['model'], [llm._system_message(payload['model'],payload['system'])]+payload['history'])
 body['max_tokens']=1200
 started=time.monotonic()
 async with httpx.AsyncClient(timeout=90,proxy=settings.llm_proxy or 'socks5://127.0.0.1:1080') as client:
  r=await client.post(llm.URL,headers={'Authorization':'Bearer '+settings.openrouter_key},json=body)
 if r.status_code!=200:
  print(json.dumps({'error':'HTTP '+str(r.status_code)}));return
 d=r.json();c=(d.get('choices') or [{}])[0]
 print(json.dumps({'text':(c.get('message') or {}).get('content') or '', 'finish':c.get('finish_reason'),'usage':d.get('usage') or {},'seconds':round(time.monotonic()-started,2)}))
asyncio.run(run())
'''
    return await asyncio.to_thread(ssh_read, program, {'model': model, 'system': system, 'history': history})


async def compare(docs: dict, stock: str, models: list[str], only: str = '') -> None:
    cases = [(0, 'Завтра могу на тест драйв'), (0, 'Течение часа'),
             (1, 'Здравствуйте'), (2, 'Причина продали'), (2, 'Продажа салонная'),
             (3, 'Люди то подключаются'), (0, 'Вы же написали вот')]
    if only:
        cases = [case for case in cases if only.lower() in case[1].lower()]
        if not cases:
            raise ValueError('No matching case')
    total = 0.0
    with patch.object(avito_match, '_stock_text', lambda text='': text or stock):
        for idx, needle in cases:
            doc, text = before(docs[LIVE_IDS[idx]], needle)
            history = doc['messages'] + [{'role': 'user', 'content': text}]
            with patch.object(store, 'load_doc', return_value=doc):
                system = bot_main._build_system(history, 'rehearsal')
            if total >= 3.0:
                raise RuntimeError('Test budget stopping threshold reached $3')
            answers = await asyncio.gather(*(remote_model(model, system, history) for model in models))
            for model, answer in zip(models, answers):
                if total >= 3.0:
                    raise RuntimeError('Test budget reached $3')
                if 'error' in answer:
                    print(json.dumps({'case': needle, 'model': model, **answer}), flush=True)
                    continue
                total += float(answer['usage'].get('cost') or 0)
                if answer.get('finish') != 'stop':
                    print(json.dumps({'case': needle, 'model': model, 'error': 'incomplete answer', 'usage': answer['usage']}), flush=True)
                    continue
                result = await replay(doc, text, answer['text'])
                print(json.dumps({'case': needle, 'model': model, 'raw': answer['text'],
                                  'sent': result['sent'], 'paused': result['paused'],
                                  'usage': answer['usage'], 'seconds': answer['seconds'],
                                  'finish': answer['finish']}, ensure_ascii=False), flush=True)
    print('TOTAL_USD', round(total, 6), flush=True)


async def run_one(title: str, turns: list[str]) -> None:
    print('СЦЕНАРИЙ:', title)
    doc = {'messages': []}
    for text in turns:
        # Генерация снаружи песочницы; действия и отправка внутри отключены.
        history = doc['messages'] + [{'role': 'user', 'content': text}]
        raw = await bot_main._generate(history)
        result = await replay(doc, text, raw)
        print(json.dumps({'user': text, 'sent': result['sent'], 'paused': result['paused']}, ensure_ascii=False))
        doc['messages'] = result['history']
        if result['paused']:
            break


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', nargs='*', type=int)
    parser.add_argument('--live-source', action='store_true', help='Read sanitized live cases over SSH')
    parser.add_argument('--compare', action='store_true', help='Paid isolated model test, $3 stopping threshold')
    parser.add_argument('--models', nargs='+', default=['anthropic/claude-sonnet-5', 'anthropic/claude-haiku-4.5'])
    parser.add_argument('--only', default='', help='Substring of a live comparison case')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if args.live_source:
        docs, stock = live_sources()
        await regression(docs, stock)
        if args.compare:
            await compare(docs, stock, args.models, args.only)
        return
    if args.compare:
        parser.error('--compare requires --live-source')
    for i, (title, turns) in enumerate(SCENARIOS, 1):
        if not args.cases or i in args.cases:
            await run_one(title, turns)


if __name__ == '__main__':
    asyncio.run(main())
