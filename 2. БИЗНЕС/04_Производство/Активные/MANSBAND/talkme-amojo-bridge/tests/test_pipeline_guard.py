from app.pipeline_guard import messaging_allowed
from app.message_templates import matches, Rule
from app.pipeline_guard import paid_certificate_scope, valid_certificate_rule_scope

def test_new_pipeline_cannot_send_even_when_rule_includes_it():
    rule = Rule(name="test", enabled=True, pipelines=[11482373], statuses=[142], trigger={"type":"stage"}, content="test")
    assert not matches({"id":1,"pipeline_id":11482373,"status_id":142}, rule)

def test_existing_allowed_but_tag_and_no_sliv_are_excluded():
    lead={"id":1,"pipeline_id":9601214}
    assert messaging_allowed(lead)
    assert not messaging_allowed({**lead,"_embedded":{"tags":[{"name":"ТЕСТ НЕ ОТПРАВЛЯТЬ"}]}})
    assert not messaging_allowed({**lead,"custom_fields_values":[{"field_id":3831526,"values":[{"value":"Не слив"}]}]})
    assert not messaging_allowed({"id":1})

def test_certificate_scope_still_requires_rollout_gate(monkeypatch):
    lead = {"id":1,"pipeline_id":11482377,"status_id":89900817}
    assert not paid_certificate_scope(lead)
    monkeypatch.setenv("MANSBAND_MESSAGE_PIPELINES", "9601214,11482377")
    assert paid_certificate_scope(lead)
    assert paid_certificate_scope({**lead,"status_id":142})
    assert not paid_certificate_scope({**lead,"status_id":89900793})
    assert not paid_certificate_scope({**lead,"status_id":143})
    assert not paid_certificate_scope({**lead,"isTest":True})

def test_certificate_configuration_requires_exact_pairs():
    assert valid_certificate_rule_scope([9601214,11482377], [76675038,89900817])
    assert not valid_certificate_rule_scope([11482377], [76675038])
    assert not valid_certificate_rule_scope([11482381], [89900893])
    assert not valid_certificate_rule_scope([11482377,11482377], [89900817])
    assert not valid_certificate_rule_scope([11482377], [89900817,142])


def test_migration_tag_blocks_without_enabling_new_pipeline():
    from app.pipeline_guard import MIGRATION_TAG, migration_blocked
    lead={"id":1,"pipeline_id":9601214,"_embedded":{"tags":[{"name":MIGRATION_TAG}]}}
    assert migration_blocked(lead)
    assert not messaging_allowed(lead)
    assert not paid_certificate_scope({**lead,"status_id":76675038})
    assert not migration_blocked({**lead,"_embedded":{"tags":[{"name":"MB_MIGRATION_DONE"}]}})
