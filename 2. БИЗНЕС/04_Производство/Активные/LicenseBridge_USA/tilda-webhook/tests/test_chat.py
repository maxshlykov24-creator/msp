from app.chat import ChatInfo, read_contact_chat


def test_classify_none():
    assert ChatInfo(has_chat=False).classify() == "none"


def test_classify_light():
    ci = ChatInfo(has_chat=True, readable=True, message_count=2, messages=["hi", "hello"])
    assert ci.classify() == "light"


def test_classify_rich_on_attachment():
    ci = ChatInfo(has_chat=True, readable=True, has_attachments=True)
    assert ci.classify() == "rich"


def test_classify_rich_on_manager_reply():
    ci = ChatInfo(has_chat=True, has_manager_reply=True, message_count=1)
    assert ci.classify() == "rich"


def test_classify_rich_on_unreadable():
    ci = ChatInfo(has_chat=True, readable=False)
    assert ci.classify() == "rich"


def test_read_light_chat(fake):
    cid = fake.add_contact(name="A", phone="+15551234567")
    fake.notes[("contacts", cid)] = [
        {"note_type": "amomessage", "params": {"text": "Здравствуйте", "in": True}},
        {"note_type": "amomessage", "params": {"text": "Есть вопрос", "in": True}},
    ]
    info = read_contact_chat(fake, cid)
    assert info.classify() == "light"
    assert info.messages == ["Здравствуйте", "Есть вопрос"]


def test_read_rich_chat_with_reply(fake):
    cid = fake.add_contact(name="A", phone="+15551234567")
    fake.notes[("contacts", cid)] = [
        {"note_type": "amomessage", "params": {"text": "Здравствуйте", "in": True}},
        {"note_type": "amomessage", "params": {"text": "Ответ менеджера", "in": False}},
    ]
    assert read_contact_chat(fake, cid).classify() == "rich"
