"""Разбор заявки квиза: ответы и комментарий попадают в примечание."""

from app.quiz_lead import build_note, parse_quiz_payload, pick_recent_quiz_lead


def test_flat_answers_stay_without_fake_comment():
    parsed = parse_quiz_payload(
        {
            "name": "Анастасия",
            "phone": "+79671387298",
            "answers": {
                "Мероприятие": "Свадьба",
                "Размер": "рост 190 см, вес 130 кг",
                "Модель": "Пока не определился",
                "Цвет": "Бордовый",
                "Способ связи": "Макс",
            },
            "page_url": "https://zamer.mansband.ru/?utm_source=yandex",
            "utm_source": "yandex",
        }
    )
    note = build_note(parsed=parsed)
    assert "Комментарий:" not in note
    assert "- Мероприятие: Свадьба" in note
    assert "- Способ связи: Макс" in note
    assert "utm_source=yandex" in note


def test_nested_comment_and_answer_comment():
    parsed = parse_quiz_payload(
        {
            "name": "Игорь",
            "phone": "89646860444",
            "answers": {
                "Цвет": {"label": "Синий", "comment": "не яркий"},
                "Комментарий": "нужен к субботе",
            },
            "data": {"comment": "этот не должен перекрыть ответ"},
        }
    )
    # верхний комментарий в data важнее ключа внутри ответов
    assert "этот не должен перекрыть ответ" in parsed["comment"]
    note = build_note(parsed=parsed)
    assert "Комментарий: этот не должен перекрыть ответ" in note
    assert "Цвет: Синий (не яркий)" in note
    assert "Комментарий: нужен к субботе" not in note.split("Ответы квиза:")[-1].split("Комментарий:")[0]


def test_comment_key_inside_answers_when_top_level_empty():
    parsed = parse_quiz_payload(
        {
            "name": "Кирилл",
            "phone": "9160709993",
            "answers": {
                "Модель": "Тройка",
                "Комментарий": "писать в телеграм",
            },
        }
    )
    note = build_note(parsed=parsed)
    assert "Комментарий: писать в телеграм" in note
    assert "- Модель: Тройка" in note
    assert "- Комментарий:" not in note


def test_pick_recent_quiz_lead_ignores_old_and_other_pipeline():
    now = 1_800_000_000
    leads = [
        {"id": 1, "name": "Квиз: Старый", "pipeline_id": 7, "created_at": now - 3600},
        {"id": 2, "name": "Звонок", "pipeline_id": 7, "created_at": now - 30},
        {"id": 3, "name": "Квиз: Владислав", "pipeline_id": 7, "created_at": now - 40},
        {"id": 4, "name": "Квиз: Чужая воронка", "pipeline_id": 8, "created_at": now - 10},
    ]
    found = pick_recent_quiz_lead(leads, pipeline_id=7, now=now)
    assert found is not None
    assert found["id"] == 3
