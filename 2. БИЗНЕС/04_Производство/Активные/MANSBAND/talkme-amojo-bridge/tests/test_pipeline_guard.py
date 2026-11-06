from app.pipeline_guard import messaging_allowed
from app.message_templates import matches, Rule

def test_new_pipeline_cannot_send_even_when_rule_includes_it():
    rule = Rule(name="test", enabled=True, pipelines=[11482373], statuses=[142], trigger={"type":"stage"}, content="test")
    assert not matches({"id":1,"pipeline_id":11482373,"status_id":142}, rule)

def test_existing_allowed_but_tag_and_no_sliv_are_excluded():
    lead={"id":1,"pipeline_id":9601214}
    assert messaging_allowed(lead)
    assert not messaging_allowed({**lead,"_embedded":{"tags":[{"name":"ТЕСТ НЕ ОТПРАВЛЯТЬ"}]}})
    assert not messaging_allowed({**lead,"custom_fields_values":[{"field_id":3831526,"values":[{"value":"Не слив"}]}]})
    assert not messaging_allowed({"id":1})
