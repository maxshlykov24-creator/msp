#!/usr/bin/env python3
"""Генерация шаблона печатной формы договора для МойСклад (CRM → Договоры)."""

from pathlib import Path

import xlwt

OUT = Path(__file__).parent / "Договор_CRM_ИП_Афанасьева.xls"

# Формулы jXLS для раздела CRM — Договор (support.moysklad.ru)
F = {
    "num": "${o.name}",
    "date": '${formatter.format("%1$td.%1$tm.%1$tY", formatter.getExcelDate(o.moment))}',
    "sum": "${o.sum.sum / 100}",
    "sum_words": "${formatter.printAmount(o.sum.sum)}",
    "own_title": "${formatter.printIfElse(empty(o.ownCompany.requisite.legalTitle), o.ownCompany.name, o.ownCompany.requisite.legalTitle)}",
    "agent_title": "${formatter.printIfElse(empty(o.agent.requisite.legalTitle), o.agent.name, o.agent.requisite.legalTitle)}",
    "own_inn": "${o.ownCompany.requisite.INN}",
    "own_ogrnip": "${o.ownCompany.requisite.ogrnip}",
    "own_legal_addr": "${o.ownCompany.requisite.legalAddress}",
    "own_actual_addr": "${o.ownCompany.requisite.actualAddress}",
    "own_phone": "${o.ownCompany.contact.phones}",
    "own_email": "${o.ownCompany.contact.email}",
    "own_bank": "${o.ownCompany.requisite.account.bankName}",
    "own_bik": "${o.ownCompany.requisite.account.BIC}",
    "own_rs": "${o.ownCompany.requisite.account.accountNumber}",
    "own_ks": "${o.ownCompany.requisite.account.correspondentAccount}",
    "own_director": "${o.ownCompany.requisite.agent.director}",
    "agent_inn": "${o.agent.requisite.INN}",
    "agent_kpp": "${o.agent.requisite.KPP}",
    "agent_ogrn": "${o.agent.requisite.ogrn}",
    "agent_ogrnip": "${o.agent.requisite.ogrnip}",
    "agent_legal_addr": "${o.agent.requisite.legalAddress}",
    "agent_actual_addr": "${o.agent.requisite.actualAddress}",
    "agent_phone": "${o.agent.contact.phones}",
    "agent_email": "${o.agent.contact.email}",
    "agent_bank": "${o.agent.requisite.account.bankName}",
    "agent_bik": "${o.agent.requisite.account.BIC}",
    "agent_rs": "${o.agent.requisite.account.accountNumber}",
    "agent_ks": "${o.agent.requisite.account.correspondentAccount}",
    "agent_director": "${o.agent.requisite.agent.director}",
    "stamp": "${formatter.image(formatter.loadAgent(o.ownCompany.requisite.agent.id).stamp, true, false)}",
    "sign": "${formatter.image(formatter.loadAgent(o.ownCompany.requisite.agent.id).directorSign, true, false)}",
}


def style_book():
    book = xlwt.Workbook(encoding="utf-8")
    base = xlwt.easyxf(
        "font: name Times New Roman, height 220; align: wrap on, vert centre;"
    )
    bold = xlwt.easyxf(
        "font: name Times New Roman, height 220, bold on; align: wrap on, vert centre, horiz centre;"
    )
    title = xlwt.easyxf(
        "font: name Times New Roman, height 280, bold on; align: wrap on, vert centre, horiz centre;"
    )
    small = xlwt.easyxf(
        "font: name Times New Roman, height 200; align: wrap on, vert top;"
    )
    return book, base, bold, title, small


def write_lines(ws, row, col, lines, style, merge_cols=7):
    for line in lines:
        if isinstance(line, tuple):
            text, st = line
        else:
            text, st = line, style
        ws.write_merge(row, row, col, col + merge_cols, text, st)
        row += 1
    return row


def main():
    book, base, bold, title, small = style_book()
    ws = book.add_sheet("Договор")

    ws.col(0).width = 256 * 4
    for c in range(1, 8):
        ws.col(c).width = 256 * 14

    r = 0
    c = 0
    mc = 7

    r = write_lines(
        ws,
        r,
        c,
        [
            ("ДОГОВОР № " + F["num"], title),
            (
                'г. ____________  «___» __________ 20___ г.     Дата в системе: '
                + F["date"],
                base,
            ),
            ("", base),
            (
                F["own_title"]
                + ', именуемый в дальнейшем «Исполнитель», с одной стороны, и '
                + F["agent_title"]
                + ', именуемый в дальнейшем «Заказчик», с другой стороны, совместно именуемые «Стороны», '
                "заключили настоящий договор о нижеследующем.",
                small,
            ),
            ("", base),
            ("1. ПРЕДМЕТ ДОГОВОРА", bold),
            (
                "1.1. Исполнитель обязуется оказать Заказчику услуги (выполнить работы, передать товар) "
                "в соответствии с условиями настоящего договора и приложений к нему, а Заказчик обязуется "
                "принять и оплатить результат в порядке и сроки, установленные договором.",
                small,
            ),
            (
                "1.2. Конкретный перечень, объём, сроки и стоимость определяются согласованием Сторон "
                "(заказ, спецификация, счёт) и являются неотъемлемой частью настоящего договора.",
                small,
            ),
            ("", base),
            ("2. СТОИМОСТЬ И ПОРЯДОК РАСЧЁТОВ", bold),
            (
                "2.1. Стоимость по настоящему договору (при наличии суммы в карточке): "
                + F["sum"]
                + " (" + F["sum_words"] + ") рублей, в том числе НДС — по применимой системе налогообложения Исполнителя.",
                small,
            ),
            (
                "2.2. Оплата производится безналичным переводом на расчётный счёт Исполнителя "
                "в сроки, согласованные Сторонами, но не позднее 5 (пяти) банковских дней с даты выставления счёта, "
                "если иное не согласовано в заказе.",
                small,
            ),
            ("", base),
            ("3. ПРАВА И ОБЯЗАННОСТИ СТОРОН", bold),
            (
                "3.1. Исполнитель обязуется оказать услуги надлежащего качества в согласованные сроки.",
                small,
            ),
            (
                "3.2. Заказчик обязуется своевременно предоставить информацию, необходимую для исполнения договора, "
                "и произвести оплату.",
                small,
            ),
            ("", base),
            ("4. ОТВЕТСТВЕННОСТЬ СТОРОН", bold),
            (
                "4.1. За неисполнение или ненадлежащее исполнение обязательств Стороны несут ответственность "
                "в соответствии с законодательством Российской Федерации.",
                small,
            ),
            ("", base),
            ("5. СРОК ДЕЙСТВИЯ И РАСТОРЖЕНИЕ", bold),
            (
                "5.1. Договор вступает в силу с даты подписания и действует до полного исполнения обязательств Сторонами, "
                "если иной срок не установлен дополнительным соглашением.",
                small,
            ),
            (
                "5.2. Договор может быть расторгнут по соглашению Сторон или в одностороннем порядке "
                "в случаях, предусмотренных законом и настоящим договором.",
                small,
            ),
            ("", base),
            ("6. ПРОЧИЕ УСЛОВИЯ", bold),
            (
                "6.1. Все изменения и дополнения оформляются в письменной форме и подписываются обеими Сторонами.",
                small,
            ),
            (
                "6.2. Споры разрешаются путём переговоров, при недостижении согласия — в суде по месту нахождения Исполнителя.",
                small,
            ),
            (
                "6.3. Договор составлен в двух экземплярах, имеющих одинаковую юридическую силу, по одному для каждой из Сторон.",
                small,
            ),
            ("", base),
            ("7. РЕКВИЗИТЫ И ПОДПИСИ СТОРОН", bold),
            ("", base),
        ],
        base,
        mc,
    )

    # Таблица реквизитов: две колонки
    ws.write_merge(r, r, 0, 3, "ИСПОЛНИТЕЛЬ", bold)
    ws.write_merge(r, r, 4, 7, "ЗАКАЗЧИК", bold)
    r += 1

    left = [
        ("Наименование:", F["own_title"]),
        ("ИНН:", F["own_inn"]),
        ("ОГРНИП:", F["own_ogrnip"]),
        ("Юр. адрес:", F["own_legal_addr"]),
        ("Факт. адрес:", F["own_actual_addr"]),
        ("Тел.:", F["own_phone"]),
        ("E-mail:", F["own_email"]),
        ("Банк:", F["own_bank"]),
        ("БИК:", F["own_bik"]),
        ("р/с:", F["own_rs"]),
        ("к/с:", F["own_ks"]),
    ]
    right = [
        ("Наименование:", F["agent_title"]),
        ("ИНН:", F["agent_inn"]),
        ("КПП:", F["agent_kpp"]),
        ("ОГРН:", F["agent_ogrn"]),
        ("ОГРНИП:", F["agent_ogrnip"]),
        ("Юр. адрес:", F["agent_legal_addr"]),
        ("Факт. адрес:", F["agent_actual_addr"]),
        ("Тел.:", F["agent_phone"]),
        ("E-mail:", F["agent_email"]),
        ("Банк:", F["agent_bank"]),
        ("БИК:", F["agent_bik"]),
        ("р/с:", F["agent_rs"]),
        ("к/с:", F["agent_ks"]),
    ]

    for (lbl_l, val_l), (lbl_r, val_r) in zip(left, right):
        ws.write_merge(r, r, 0, 1, lbl_l, base)
        ws.write_merge(r, r, 2, 3, val_l, small)
        ws.write_merge(r, r, 4, 5, lbl_r, base)
        ws.write_merge(r, r, 6, 7, val_r, small)
        r += 1

    r += 1
    ws.write_merge(r, r, 0, 3, "_________________ / " + F["own_director"] + " /", base)
    ws.write_merge(r, r, 4, 7, "_________________ / " + F["agent_director"] + " /", base)
    r += 1
    ws.write_merge(r, r, 0, 1, "м.п.", base)
    ws.write_merge(r, r, 2, 3, F["stamp"], base)
    ws.write_merge(r, r, 4, 5, "м.п.", base)
    r += 1
    ws.write_merge(r, r, 0, 3, "Подпись: " + F["sign"], base)

    book.save(str(OUT))
    print(f"Saved: {OUT}")


if __name__ == "__main__":
    main()
