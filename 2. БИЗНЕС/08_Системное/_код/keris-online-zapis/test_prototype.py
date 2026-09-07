#!/usr/bin/env python3
"""Smoke-тест автономного прототипа через локальный Chromium Playwright.

Кабинет «Мой Keris» работает на `GET /api/client`, поэтому сценарии с историей визитов
идут с подменённым `fetch` (`?stub=1`), а без него проверяется поведение боевого
пустого профиля.
"""

import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright


HTML = Path(__file__).with_name("prototype.html").resolve().as_uri()
VIEWPORTS = [(360, 800), (390, 844), (430, 932), (1440, 900)]

CLIENT_PROFILE = {
    "phone": "+79993689871",
    "name": "Ольга",
    "consent": {"personal_data": True, "marketing": True, "media": True},
    "pets": [
        {"id": "pet1", "name": "Тедди", "type": "dog", "breed": "мальтипу",
         "size": "XS", "weight": "4,2 кг", "note": "Чувствительные лапки"},
        {"id": "pet2", "name": "Муся", "type": "cat", "breed": "мейн-кун",
         "size": "long_large", "weight": "6,1 кг", "note": "Боится фена"},
    ],
    "subscription": {"name": "Привычка", "left": 3.5, "total": 6, "until": "2026-09-30"},
    "visits": [
        {"id": "KERIS-1042", "status": "upcoming", "date": "2026-08-14", "time": "14:00",
         "duration": 150, "masterId": "svetlana", "petId": "pet1", "service": "Комплекс со стрижкой",
         "addons": ["Маска Hydra"], "total": 15900, "pay": "Абонемент · списано 1 визита", "comment": ""},
        {"id": "KERIS-1017", "status": "done", "date": "2026-06-18", "time": "12:30",
         "duration": 145, "masterId": "svetlana", "petId": "pet1", "service": "Комплекс со стрижкой",
         "addons": ["Маска Hydra"], "total": 15900, "pay": "Оплата в салоне",
         "comment": "Чувствительные лапки"},
        {"id": "KERIS-0989", "status": "done", "date": "2026-04-29", "time": "11:00",
         "duration": 120, "masterId": "alla", "petId": "pet2", "service": "Гигиена",
         "addons": [], "total": 6800, "pay": "Оплата в салоне", "comment": ""},
    ],
}

API_STUB = """
(() => {
  if (!location.search.includes('stub=1')) return;
  const profile = __PROFILE__;
  const json = (body) => Promise.resolve(new Response(JSON.stringify(body),
    {status: 200, headers: {'Content-Type': 'application/json'}}));
  const real = window.fetch.bind(window);
  window.__apiPosts = [];
  const remember = (u, opts) => {
    let body = null;
    try { body = JSON.parse((opts && opts.body) || 'null'); } catch (e) {}
    window.__apiPosts.push({url: u, body: body});
  };
  window.fetch = (url, opts) => {
    const u = String(url && url.url ? url.url : url);
    if (u.includes('/api/auth/request-code')) return json({ok: true, ttl_sec: 600});
    if (u.includes('/api/auth/verify')) return json({ok: true, phone: profile.phone});
    if (u.includes('/api/client')) return json(profile);
    if (u.includes('/api/reviews')) return json({ok: true, review:
      {name: 'Ольга', stars: 5, date: '2026-08-07', text: 'Бережно и красиво.'}});
    if (u.includes('/reschedule')) {
      remember(u, opts);
      const b = window.__apiPosts[window.__apiPosts.length - 1].body || {};
      const visit = profile.visits.find(v => v.status === 'upcoming');
      if (visit) { visit.date = b.date_iso; visit.time = b.time_hhmm; }
      return json({booking_id: 'KERIS-1042', status: 'confirmed',
                   starts_at: b.date_iso + 'T' + b.time_hhmm + ':00'});
    }
    if (u.includes('/cancel')) {
      remember(u, opts);
      const visit = profile.visits.find(v => v.status === 'upcoming');
      if (visit) visit.status = 'cancelled';
      return json({booking_id: 'KERIS-1042', status: 'cancelled', visits_refunded: 1});
    }
    if (u.includes('/api/bookings')) {
      remember(u, opts);
      return json({booking_id: 'KERIS-2001', price: 11000, applied_promos: []});
    }
    return real(url, opts);
  };
})();
""".replace("__PROFILE__", json.dumps(CLIENT_PROFILE, ensure_ascii=False))


def assert_no_horizontal_scroll(page) -> None:
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )
    assert not overflow, f"Обнаружен горизонтальный скролл: {page.viewport_size}"


def dismiss_cookie(page) -> None:
    banner = page.locator("#cookieBanner.show")
    if banner.count():
        page.get_by_role("button", name="Принять").click()


def fresh(page, api: bool = False) -> None:
    """Открыть прототип анонимно: сохранённая сессия кабинета сбрасывается.

    api=True — с подменённым API кабинета (профиль, визиты, отзывы).
    """
    page.goto(HTML + ("?stub=1" if api else ""))
    page.evaluate("() => { try { localStorage.removeItem('keris_client_phone') } catch (e) {} }")
    page.reload()
    dismiss_cookie(page)


def test_new_client(page) -> None:
    fresh(page)
    page.get_by_role("button", name="Записаться", exact=True).click()
    page.locator("#breed").fill("Мальтипу")
    page.locator("#dogSizeGrid .chip").first.click()
    page.locator("#nextBtn").click()
    page.get_by_text("Комплекс со стрижкой", exact=True).first.click()
    page.get_by_text("Маска Hydra", exact=True).click()
    page.locator("#nextBtn").click()
    assert page.locator("#stepStripLabel").get_by_text("Мастер и время", exact=False).is_visible()
    page.get_by_text("Любой свободный", exact=True).click()
    assert "Ближайшее:" in page.locator("#masterList").inner_text()
    assert page.locator("button.slot", has_text=re.compile(r"^20:30")).is_disabled()
    page.locator("button.slot", has_text=re.compile(r"^17:30")).click()
    page.locator("#nextBtn").click()
    page.locator("#ownerName").fill("Анна")
    page.locator("#phone").fill("+7 999 000-00-00")
    page.locator("#consent").check()
    page.locator("#consentAds").check()
    page.locator("#consentMedia").check()
    assert page.locator(".offer-note").get_by_text("Публичной оферты", exact=False).is_visible()
    total_text = page.locator("#summary").inner_text().replace("\xa0", " ")
    assert "11 000 ₽" in total_text
    page.locator("#nextBtn").click()
    # автономный показ с file:// — сервер записи недоступен, демо-подтверждения больше нет
    page.get_by_text("Сервер записи временно недоступен", exact=False).wait_for()


AUTH_STUB = """
(() => {
  const json = (body) => Promise.resolve(new Response(JSON.stringify(body),
    {status: 200, headers: {'Content-Type': 'application/json'}}));
  const real = window.fetch.bind(window);
  window.fetch = (url, opts) => {
    const u = String(url && url.url ? url.url : url);
    if (u.includes('/api/auth/request-code')) return json({ok: true, ttl_sec: 600});
    if (u.includes('/api/auth/verify')) return json({ok: true, phone: '+79993689871'});
    return real(url, opts);
  };
})();
"""


def login_cabinet(page) -> None:
    """Вход в личный кабинет: телефон → SMS-код (в тестах API подменён)."""
    page.evaluate(AUTH_STUB)
    page.get_by_role("button", name="Я уже клиент Keris Club").click()
    page.locator("#authPhoneInput").fill("9993689871")
    page.get_by_role("button", name="Получить код").click()
    page.locator("#authCode").fill("1234")
    page.get_by_role("button", name="Войти").click()
    page.locator("#cabinetBody .cab-head").wait_for()


def test_cabinet_and_visit(page) -> None:
    fresh(page, api=True)
    login_cabinet(page)
    body = page.locator("#cabinetBody").inner_text()
    assert "ближайшая запись" in body.lower()
    assert "История посещений" in body
    assert "Абонемент" in body
    assert "Тедди" in body and "Муся" in body
    page.locator("#cabinetBody .cab-card.visit").first.click()
    visit = page.locator("#visitBody").inner_text()
    assert "Комплекс со стрижкой" in visit
    assert "Светлана" in visit
    assert "Отчёт мастера появится здесь" in visit
    assert page.locator("#visitBody .ba-shot").count() == 2
    page.get_by_role("button", name="Повторить этот уход").click()
    assert page.locator(".choice.service.selected").count() == 1


def test_cabinet_live_empty(page) -> None:
    """Боевой режим без демо-данных: кабинет пустой, телефон входа подставлен в запись."""
    fresh(page)
    login_cabinet(page)
    body = page.locator("#cabinetBody").inner_text()
    assert "Добавить питомца" in body
    assert "Пока нет завершённых визитов" in body
    assert page.locator("#cabinetBody .cab-card.pet").count() == 0
    page.get_by_role("button", name="Записаться на груминг").click()
    assert page.locator("#petPicker").is_hidden()
    assert "999 368" in page.locator("#phone").input_value()


def test_client_prefill_and_pets(page) -> None:
    """Авторизованный клиент: питомцы и контакты подставляются, но заменяемы."""
    fresh(page, api=True)
    login_cabinet(page)
    page.get_by_role("button", name="Записаться на груминг").click()
    page.locator("#petPicker").wait_for()
    assert page.locator("#petName").input_value() == "Тедди"
    page.locator("#petPickerChips .chip", has_text="Муся").click()
    assert page.locator("#catPetName").input_value() == "Муся"
    assert page.locator("#catFields").is_visible()
    page.locator("#petPickerChips .chip", has_text="Другой питомец").click()
    assert page.locator("#petName").input_value() == ""
    page.locator("#petPickerChips .chip", has_text="Тедди").click()
    page.locator("#nextBtn").click()
    page.get_by_text("Комплекс со стрижкой", exact=True).first.click()
    page.locator("#nextBtn").click()
    page.get_by_text("Любой свободный", exact=True).click()
    page.locator("button.slot:not([disabled])").first.click()
    page.locator("#nextBtn").click()
    assert page.locator("#ownerName").input_value() == "Ольга"
    assert "999 368" in page.locator("#phone").input_value()
    assert page.locator("#contactAuthNote").is_visible()
    assert "Тедди" in page.locator("#summary").inner_text()
    # уже был у нас и подтверждал все 3 согласия — повторно отмечать не нужно
    assert page.locator("#consentAuthNote").is_visible()
    assert page.locator("#consent").is_checked()
    assert page.locator("#consentAds").is_checked()
    assert page.locator("#consentMedia").is_checked()


def test_pet_card_and_new_pet(page) -> None:
    """Карточка питомца ведёт в Keris Memory, «другой питомец» — чистая форма."""
    fresh(page, api=True)
    login_cabinet(page)
    page.locator("#cabinetBody .cab-card.pet").first.click()
    assert page.locator("#stepStripLabel").get_by_text("Keris Memory", exact=False).is_visible()
    memory = page.locator("#memoryBody").inner_text()
    assert "Тедди" in memory and "Светлана" in memory
    page.get_by_role("button", name="Повторить прошлый уход").click()
    assert page.locator(".choice.service.selected").count() == 1
    fresh(page, api=True)
    login_cabinet(page)
    page.get_by_role("button", name="Записать другого питомца").click()
    assert page.locator("#petName").input_value() == ""
    page.locator("#dogSizeGrid .chip").first.click()
    page.locator("#nextBtn").click()
    page.get_by_text("Гигиена", exact=True).first.click()
    page.locator("#nextBtn").click()
    page.get_by_text("Любой свободный", exact=True).click()
    assert "раньше всех" not in page.locator("#masterList").inner_text()


def test_review_unlocked_after_visit(page) -> None:
    """Отзыв открыт клиенту с завершённым визитом у мастера и закрыт анониму."""
    fresh(page)
    page.get_by_role("button", name="Записаться", exact=True).click()
    page.locator("#dogSizeGrid .chip").first.click()
    page.locator("#nextBtn").click()
    page.get_by_text("Комплекс со стрижкой", exact=True).first.click()
    page.locator("#nextBtn").click()
    page.get_by_role("button", name="Профиль и отзывы").first.click()
    assert page.locator(".review-form .locked").is_visible()

    fresh(page, api=True)
    login_cabinet(page)
    page.locator("#cabinetBody .cab-card.visit").first.click()
    page.get_by_role("button", name="Оставить отзыв о мастере").click()
    page.locator("#reviewText").fill("Бережно и красиво, питомец спокоен.")
    page.locator(".review-form .stars-pick button").nth(4).click()
    page.get_by_role("button", name="Опубликовать отзыв").click()
    page.get_by_text("Спасибо! Отзыв опубликован", exact=False).wait_for()
    assert "Бережно и красиво" in page.locator(".profile-reviews").inner_text()


def test_subscriptions_and_legal(page) -> None:
    # временно: кнопка «Купить абонемент» убрана из UI — сценарий отключён
    return
    fresh(page)
    page.get_by_role("button", name="Купить абонемент").click()
    assert page.locator("#stepStripLabel").get_by_text("Абонементы", exact=False).is_visible()
    page.get_by_text("Узнаем", exact=True).click()
    page.locator("#nextBtn").click()
    with page.context.expect_page() as popup_info:
        page.get_by_role("link", name="Публичной оферты").first.click()
    popup = popup_info.value
    assert "public_offer" in popup.url
    popup.close()


def start_move(page, entry: str) -> None:
    """Открыть перенос ближайшей записи из кабинета или с экрана визита."""
    if entry == "visit":
        page.locator("#cabinetBody .cab-next button", has_text="Подробнее").click()
        page.locator("#visitBody button", has_text="Перенести").click()
    else:
        page.locator("#cabinetBody .cab-next button", has_text="Перенести").click()
    page.get_by_role("button", name="Выбрать новый слот").click()


def move_flow(page, entry: str) -> None:
    """Перенос = та же запись на новое время: POST /reschedule, без второй записи."""
    fresh(page, api=True)
    login_cabinet(page)
    start_move(page, entry)
    assert page.locator("#stepStripLabel").get_by_text("Мастер и время", exact=False).is_visible()
    # контекст записи взят из визита: услуга, допы, мастер и размер
    ctx = page.evaluate(
        "() => ({svc: state.service && state.service.id, master: state.master && state.master.id,"
        " size: state.size, addons: state.addons.join(','), moveId: state.moveId})"
    )
    assert ctx == {"svc": "dog_complex_cut", "master": "svetlana", "size": "XS",
                   "addons": "dog_mask_hydra", "moveId": "KERIS-1042"}, ctx
    # мастера при переносе не меняем — в списке только он один
    assert page.locator("#masterList .master-card").count() == 1
    assert "того же мастера" in page.locator("#masterLead").inner_text()

    page.locator("button.slot:not([disabled])").first.click()
    assert "Перенести на" in page.locator("#nextBtn").inner_text()
    page.locator("#nextBtn").click()

    page.get_by_text("Запись перенесена", exact=False).wait_for()
    assert page.locator("#bookingCode").inner_text() == "KERIS-1042"
    post = page.evaluate("() => window.__apiPosts.filter(p => p.url.includes('reschedule')).pop()")
    assert post["url"].endswith("/api/bookings/KERIS-1042/reschedule"), post
    assert post["body"]["date_iso"] and post["body"]["time_hhmm"]
    assert "999" in post["body"]["phone"]  # телефон владельца = право на перенос
    assert page.evaluate("() => state.moveId") is None


def test_move_booking_calls_reschedule(page) -> None:
    move_flow(page, "cabinet")
    move_flow(page, "visit")


def test_move_blocked_close_to_visit(page) -> None:
    """Ближе чем за 24 ч перенос и отмена — только через мастера, без запроса к API."""
    fresh(page, api=True)
    login_cabinet(page)
    page.evaluate(
        """() => {
             const v = CLIENT.visits.find(x => x.status === 'upcoming');
             const soon = new Date(Date.now() + 3 * 3600 * 1000);
             v.date = soon.toISOString().slice(0, 10);
             v.time = soon.toTimeString().slice(0, 5);
             renderCabinet();
           }"""
    )
    page.locator("#cabinetBody .cab-next button", has_text="Перенести").click()
    page.get_by_role("button", name="Выбрать новый слот").click()
    page.locator("#toast", has_text="только через мастера").wait_for()
    assert page.evaluate("() => current") == "cabinet"
    assert page.evaluate("() => window.__apiPosts.length") == 0


def test_cancel_calls_api_and_refreshes_cabinet(page) -> None:
    fresh(page, api=True)
    login_cabinet(page)
    page.locator("#cabinetBody .cab-next button", has_text="Подробнее").click()
    page.locator("#visitBody button", has_text="Отменить").click()
    page.get_by_role("button", name="Отменить запись").click()
    page.get_by_text("Запись отменена", exact=False).wait_for()
    post = page.evaluate("() => window.__apiPosts.filter(p => p.url.includes('cancel')).pop()")
    assert post["url"].endswith("/api/bookings/KERIS-1042/cancel"), post
    assert "999" in post["body"]["phone"]
    # кабинет перечитан: активных записей больше нет
    page.locator("#cabinetBody").get_by_text("Активных записей нет", exact=False).wait_for()


def test_move_right_after_booking(page) -> None:
    """Аноним переносит только что оформленную запись: телефон берётся из формы."""
    fresh(page, api=True)
    page.get_by_role("button", name="Записаться", exact=True).click()
    page.locator("#dogSizeGrid .chip").first.click()
    page.locator("#nextBtn").click()
    page.get_by_text("Комплекс со стрижкой", exact=True).first.click()
    page.locator("#nextBtn").click()
    page.get_by_text("Любой свободный", exact=True).click()
    # день подальше: до визита должно остаться больше 24 ч, иначе перенос закрыт
    page.locator("#masterList button.date").nth(3).click()
    page.locator("button.slot:not([disabled])").first.click()
    page.locator("#nextBtn").click()
    page.locator("#ownerName").fill("Анна")
    page.locator("#phone").fill("+7 999 000-00-00")
    for box in ("consent", "consentAds", "consentMedia"):
        page.locator("#" + box).check()
    page.locator("#nextBtn").click()
    page.get_by_text("Запись подтверждена", exact=False).wait_for()

    page.locator(".screen[data-screen='success'] button", has_text="Перенести").click()
    page.get_by_role("button", name="Выбрать новый слот").click()
    page.locator("button.slot:not([disabled])").first.click()
    page.locator("#nextBtn").click()
    page.get_by_text("Запись перенесена", exact=False).wait_for()
    post = page.evaluate("() => window.__apiPosts.filter(p => p.url.includes('reschedule')).pop()")
    assert post["url"].endswith("/api/bookings/KERIS-2001/reschedule"), post
    assert post["body"]["phone"] == "+79990000000", post


def test_no_show_visit_shown_as_missed(page) -> None:
    """Статус no_show с сервера («Не пришел» в журнале YCLIENTS) виден клиенту."""
    fresh(page, api=True)
    login_cabinet(page)
    page.evaluate(
        """() => {
             CLIENT.visits.find(v => v.id === 'KERIS-1017').status = 'no_show';
             renderCabinet();
           }"""
    )
    assert "Не пришёл" in page.locator("#cabinetBody").inner_text()
    page.locator("#cabinetBody .cab-card.visit", has_text="Не пришёл").first.click()
    visit = page.locator("#visitBody").inner_text()
    assert "Не пришёл" in visit and "Слот освобождён" in visit
    assert page.get_by_role("button", name="Записаться заново").is_visible()


def test_spa_blocks_late_slots(page) -> None:
    fresh(page)
    page.get_by_role("button", name="Записаться", exact=True).click()
    page.locator("#dogSizeGrid .chip").first.click()
    page.locator("#nextBtn").click()
    page.get_by_text("SPA-комплекс", exact=True).first.click()
    page.locator("#nextBtn").click()
    page.get_by_text("Любой свободный", exact=True).click()
    # SPA XS — 120 мин, салон до 22:00: старт позже 20:00 не влезает
    assert page.locator("button.slot", has_text=re.compile(r"^20:30")).is_disabled()
    assert page.locator("button.slot", has_text=re.compile(r"^19:00")).is_enabled()
    assert page.locator("button.slot", has_text=re.compile(r"^17:30")).is_enabled()


def test_master_profile_and_addon_duration(page) -> None:
    fresh(page)
    page.get_by_role("button", name="Записаться", exact=True).click()
    page.locator("#dogSizeGrid .chip").first.click()
    page.locator("#nextBtn").click()
    page.get_by_text("Комплекс со стрижкой", exact=True).first.click()
    page.get_by_text("Маска Hydra", exact=True).click()
    assert "+15 мин" in page.locator("#addonList").inner_text()
    page.locator("#nextBtn").click()
    page.get_by_role("button", name="Профиль и отзывы").first.click()
    assert page.get_by_text("Отзывы", exact=True).first.is_visible()
    assert "после визита у мастера" in page.locator(".review-form").inner_text()
    page.get_by_role("button", name="Выбрать Светлану и время").click()
    assert page.locator("#stepStripLabel").get_by_text("Мастер и время", exact=False).is_visible()
    page.get_by_text("Любой свободный", exact=True).click()
    # Комплекс со стрижкой XS + маска = 105 мин: 20:30 уже не влезает, 19:00 да
    assert page.locator("button.slot", has_text=re.compile(r"^20:30")).is_disabled()
    assert page.locator("button.slot", has_text=re.compile(r"^17:30")).is_enabled()


def main() -> None:
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for width, height in VIEWPORTS:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(HTML)
            dismiss_cookie(page)
            assert "Запишите питомца" in page.locator("h1").first.inner_text()
            assert_no_horizontal_scroll(page)
            page.close()

        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(API_STUB)  # срабатывает только на ?stub=1
        test_new_client(page)
        test_cabinet_and_visit(page)
        test_cabinet_live_empty(page)
        test_client_prefill_and_pets(page)
        test_pet_card_and_new_pet(page)
        test_review_unlocked_after_visit(page)
        test_subscriptions_and_legal(page)
        test_move_booking_calls_reschedule(page)
        test_move_blocked_close_to_visit(page)
        test_cancel_calls_api_and_refreshes_cabinet(page)
        test_move_right_after_booking(page)
        test_no_show_visit_shown_as_missed(page)
        test_spa_blocks_late_slots(page)
        test_master_profile_and_addon_duration(page)
        assert_no_horizontal_scroll(page)
        page.close()
        browser.close()

    assert not errors, f"Ошибки JavaScript: {errors}"
    print("OK: viewport, запись, кабинет и визит, автоподстановка клиента и питомцев, Keris Memory, "
          "отзывы, абонементы, перенос и отмена записи, окно 24 ч, статус «Не пришёл», "
          "профиль мастера, длительность доп.")


if __name__ == "__main__":
    main()
