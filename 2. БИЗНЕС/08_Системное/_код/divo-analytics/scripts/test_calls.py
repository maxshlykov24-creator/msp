"""Регрессии бизнес-правил и очереди. Запуск: python -m unittest scripts.test_calls -v."""
from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import call_worker, calls_api
from app.call_report import render_report
from app.call_rules import CRITERIA, validate_analysis
from app.config import settings
from app.database import Base
from app.models import CallDelivery, CallRecord, SyncState
from app.amo_client import AmoClient, AmoError
from app.nexara import AmbiguousSubmission, validate_recording_url


@compiles(JSONB, "sqlite")
def sqlite_json(element, compiler, **kw):
    return "JSON"


TEXT = "Здравствуйте, DIVO Motors, меня зовут менеджер. Планирую купить автомобиль завтра. Для обмена автомобиля нет. Предлагаю приехать завтра в 12 часов. Да, приеду завтра в 12 часов. До встречи."
TRANSCRIPT = {"text": TEXT, "duration": 60, "segments": [{"text": TEXT, "start": 0, "end": 60, "speaker": "Менеджер"}]}


def evidence(quote="Здравствуйте, DIVO Motors, меня зовут менеджер."):
    return {"quote": quote, "start": 0, "end": 60}


def analysis():
    return {"category": "primary_inbound", "category_reason": "Входящее обращение о покупке",
            "category_evidence": [], "summary": "Обсудили покупку и встречу",
            "recording_complete": True, "speech_clear": True, "roles_reliable": True,
            "criteria": [{"id": i, "status": "yes", "explanation": "Условия выполнены",
                          "evidence": [evidence()],
                          "conditions": [{"key": k, "status": "yes", "evidence": [evidence()]}
                                         for k in keys]} for i, (_, keys) in CRITERIA.items()],
            "outcome": {"meeting_agreed": True, "meeting_when": "завтра в 12 часов",
                        "next_contact": None, "trade_in": "interest",
                        "trade_in_evidence": [evidence()], "meeting_evidence": [evidence()]},
            "recommendation": "Подтвердить встречу"}


def fail_condition(a, i, key):
    c = a["criteria"][i-1]
    c["status"] = "no"
    for v in c["conditions"]:
        if v["key"] == key:
            v["status"], v["evidence"] = "no", []


class RulesTest(unittest.TestCase):
    def check(self, a, direction="in"):
        return validate_analysis(a, TRANSCRIPT, direction)

    def test_score_is_computed_and_equal_weight(self):
        a=analysis();fail_condition(a,2,"purchase_time")
        r=self.check(a)
        self.assertTrue(r["is_scored"]);self.assertEqual(r["score"],85.71)

    def test_trade_not_discussed_requires_two_no(self):
        a=analysis();a["outcome"].update(trade_in="not_discussed",trade_in_evidence=[])
        fail_condition(a,4,"exchange_or_sale_plans");fail_condition(a,5,"year")
        self.assertTrue(self.check(a)["is_scored"])
        a["criteria"][4]["status"]="na"
        self.assertFalse(self.check(a)["is_scored"])

    def test_no_car_allows_only_fifth_na(self):
        a=analysis();a["outcome"].update(trade_in="no_car",trade_in_evidence=[evidence("Для обмена автомобиля нет.")])
        a["criteria"][4].update(status="na",conditions=[])
        r=self.check(a);self.assertEqual(r["applicable_count"],6);self.assertEqual(r["score"],100)
        a["criteria"][3]["status"]="na";self.assertFalse(self.check(a)["is_scored"])

    def test_no_evidence_for_refusal_cannot_allow_na(self):
        a=analysis();a["outcome"].update(trade_in="refused",trade_in_evidence=[])
        a["criteria"][4].update(status="na",conditions=[])
        self.assertFalse(self.check(a)["is_scored"])

    def test_missing_time_fails_invitation(self):
        a=analysis();fail_condition(a,6,"specific_time")
        self.assertEqual(self.check(a)["score"],85.71)
        a["criteria"][5]["status"]="yes";self.assertFalse(self.check(a)["is_scored"])

    def test_meeting_refusal_does_not_cancel_invitation(self):
        a=analysis();a["outcome"].update(meeting_agreed=False,meeting_when=None,meeting_evidence=[])
        self.assertEqual(self.check(a)["analysis"]["criteria"][5]["status"],"yes")

    def test_information_from_customer_is_valid_evidence(self):
        a=analysis();a["criteria"][1]["conditions"][1]["evidence"]=[evidence("Планирую купить автомобиль завтра.")]
        self.assertTrue(self.check(a)["is_scored"])

    def test_repeat_and_outgoing_have_no_score(self):
        for category in ("repeat","outbound","service","irrelevant"):
            a=analysis();a.update(category=category,criteria=[],category_evidence=[evidence()])
            r=self.check(a);self.assertFalse(r["is_scored"]);self.assertEqual(r["errors"],[])
        a=analysis();self.assertFalse(self.check(a,"out")["is_scored"])

    def test_repeat_needs_evidence(self):
        a=analysis();a.update(category="repeat",criteria=[])
        self.assertIn("category:repeat_requires_evidence",self.check(a)["errors"])

    def test_cutoff_unclear_or_transferred_roles_exclude_average(self):
        for key in ("recording_complete","speech_clear","roles_reliable"):
            a=analysis();a[key]=False;self.assertFalse(self.check(a)["is_scored"])
        a=analysis();a["criteria"][0]["status"]="unknown"
        self.assertFalse(self.check(a)["is_scored"])

    def test_fabricated_quote_and_wrong_timestamp_rejected(self):
        a=analysis();a["criteria"][0]["evidence"]=[evidence("Несуществующая реплика")]
        self.assertFalse(self.check(a)["is_scored"])
        a=analysis();a["criteria"][0]["evidence"][0]["start"]=70
        self.assertFalse(self.check(a)["is_scored"])
        transcript=copy.deepcopy(TRANSCRIPT);transcript["segments"]=[{"text":TEXT,"start":0,"end":10},{"text":"Другая реплика","start":40,"end":60}]
        a=analysis();a["criteria"][0]["evidence"][0].update(start=40,end=50)
        self.assertFalse(validate_analysis(a,transcript,"in")["is_scored"])

    def test_duplicate_criterion_or_missing_component_rejected(self):
        a=analysis();a["criteria"][6]=copy.deepcopy(a["criteria"][0])
        self.assertFalse(self.check(a)["is_scored"])
        a=analysis();a["criteria"][0]["conditions"].pop();self.assertFalse(self.check(a)["is_scored"])

    def test_malformed_provider_output_is_not_published(self):
        r=validate_analysis({"criteria":"incorrect"},TRANSCRIPT,"in")
        self.assertFalse(r["is_scored"]);self.assertEqual(r["analysis"],{})
        self.assertTrue(r["errors"])


class PaginationTest(unittest.TestCase):
    def test_repeated_page_is_not_silently_accepted(self):
        client=AmoClient()
        try:
            page={"_embedded":{"events":[{"id":"same"}]},"_links":{"next":{"href":"page=2"}}}
            with patch.object(client,"_get",return_value=page):
                with self.assertRaises(AmoError):list(client.paginate("/events","events"))
        finally:client.close()


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory=sessionmaker(bind=self.engine,expire_on_commit=False)
        self.db=self.factory()
        self.patch=patch.object(call_worker,"SessionLocal",self.factory);self.patch.start()
        self.settings={k:getattr(settings,k) for k in ("calls_verified_authors","calls_manager_map","calls_amo_enabled","calls_telegram_enabled","calls_process_enabled","nexara_api_key","calls_recording_hosts")}
        settings.calls_verified_authors="";settings.calls_manager_map=""

    def tearDown(self):
        for k,v in self.settings.items():setattr(settings,k,v)
        self.patch.stop();self.db.close();self.engine.dispose()

    def note(self,id=1,uniq="call-1",link=""):
        return {"id":id,"created_at":1000,"created_by":13334858,"note_type":"call_in",
                "params":{"uniq":uniq,"duration":60,"link":link,"source":"mango"}}

    def call(self):
        r=call_worker.upsert_note(self.db,self.note(),"lead",100,[100]);self.db.commit();return r

    def delivery(self,r,channel="amo",state="pending"):
        d=CallDelivery(call_id=r.id,channel=channel,target="100",state=state)
        self.db.add(d);self.db.commit();return d

    def test_duplicate_events_and_uniq_added_later(self):
        a=call_worker.upsert_note(self.db,self.note(uniq=""),"lead",100,[100]);self.db.commit()
        b=call_worker.upsert_note(self.db,self.note(),"lead",100,[100]);self.db.commit()
        c=call_worker.upsert_note(self.db,self.note(),"lead",100,[100]);self.db.commit()
        self.assertEqual((a.id,b.id,c.id),(a.id,a.id,a.id))
        self.assertEqual(len(list(self.db.scalars(select(CallRecord)))),1)

    def test_collect_both_directions_and_cursor_only_after_complete_read(self):
        self.db.add(SyncState(key="calls_start_at",value="900"));self.db.commit()
        seen=[]
        def paginate(path,key,params=None,**kwargs):
            if key=="events":
                seen.append(params)
                return iter([{"entity_type":"lead","entity_id":100}])
            return iter([self.note(),{**self.note(id=2,uniq="out"),"note_type":"call_out"}])
        amo=SimpleNamespace(paginate=paginate)
        with patch.object(call_worker.time,"time",return_value=1100):
            call_worker.collect_calls(amo)
        self.assertEqual(seen[0]["filter[type][0]"],"incoming_call")
        self.assertEqual(seen[0]["filter[type][1]"],"outgoing_call")
        self.db.expire_all()
        self.assertEqual({r.direction for r in self.db.scalars(select(CallRecord))},{"in","out"})
        self.assertEqual(self.db.get(SyncState,"calls_cursor").value,"1100")
        def broken(*args,**kwargs):
            yield {"entity_type":"lead","entity_id":100}
            raise AmoError("incomplete_collection")
        with patch.object(call_worker.time,"time",return_value=1200):
            with self.assertRaises(AmoError):call_worker.collect_calls(SimpleNamespace(paginate=broken))
        self.db.expire_all()
        self.assertEqual(self.db.get(SyncState,"calls_cursor").value,"1100")

    def test_late_recording_revives_call(self):
        r=self.call();self.assertEqual(r.state,"waiting_recording")
        r=call_worker.upsert_note(self.db,self.note(link="https://example.invalid/a.mp3"),"lead",100,[100])
        self.assertEqual(r.state,"ready")

    def test_author_not_assumed_to_be_speaker(self):
        r=self.call();self.assertFalse(r.manager_verified)
        settings.calls_verified_authors="13334858"
        r=call_worker.upsert_note(self.db,self.note(),"lead",100,[100])
        self.assertTrue(r.manager_verified)
        r=call_worker.upsert_note(self.db,{**self.note(id=2),"created_by":13180098},"lead",100,[100])
        settings.calls_verified_authors="13334858,13180098"
        self.assertIsNone(call_worker.resolve_manager(r.owner_candidates))

    def test_multiple_leads_block_note(self):
        r=self.call();call_worker.upsert_note(self.db,self.note(id=2),"lead",200,[200]);self.db.commit()
        d=self.delivery(r)
        amo=SimpleNamespace(post_once=lambda *a: self.fail("must not write"))
        call_worker.deliver_amo(self.db,d,r,amo);self.assertEqual(d.state,"blocked")

    def test_existing_report_is_read_back_without_post(self):
        r=self.call();d=self.delivery(r)
        amo=SimpleNamespace(post_once=lambda *a:self.fail("duplicate POST"))
        with patch.object(call_worker,"find_note",return_value="note-123"):
            call_worker.deliver_amo(self.db,d,r,amo)
        self.assertEqual((d.state,d.remote_id),("sent","note-123"))

    def test_amo_timeout_is_not_reposted(self):
        r=self.call();d=self.delivery(r);posts=[]
        def post(*args):
            posts.append(args);raise httpx.ReadTimeout("safe test")
        amo=SimpleNamespace(post_once=post)
        with patch.object(call_worker,"find_note",return_value=None):
            call_worker.deliver_amo(self.db,d,r,amo)
            self.assertEqual(d.state,"ambiguous")
            call_worker.deliver_amo(self.db,d,r,amo)
        self.assertEqual(len(posts),1)

    def test_telegram_restart_ambiguous_does_not_send(self):
        r=self.call();d=self.delivery(r,"telegram","sending")
        with patch("httpx.Client",side_effect=AssertionError("must not send")):
            call_worker.deliver_telegram(self.db,d,r)
        self.assertEqual(d.state,"ambiguous")

    def test_telegram_connection_failure_can_retry_before_request_sent(self):
        r=self.call();d=self.delivery(r,"telegram")
        with patch("httpx.Client") as cls:
            cls.return_value.__enter__.return_value.post.side_effect=httpx.ConnectError("connection not established")
            call_worker.deliver_telegram(self.db,d,r)
        self.assertEqual(d.state,"pending")
        self.assertEqual(d.last_error,"telegram_connection_failed")
        self.assertIsNotNone(d.next_attempt_at)

    def test_telegram_read_timeout_requires_manual_check(self):
        r=self.call();d=self.delivery(r,"telegram")
        with patch("httpx.Client") as cls:
            cls.return_value.__enter__.return_value.post.side_effect=httpx.ReadTimeout("request may have been received")
            call_worker.deliver_telegram(self.db,d,r)
        self.assertEqual(d.state,"ambiguous")

    def test_calibration_never_delivers(self):
        r=self.call();r.is_calibration=True;r.state="complete";self.db.commit()
        settings.calls_amo_enabled=True;settings.calls_telegram_enabled=True
        call_worker.deliver_calls(SimpleNamespace())
        self.assertEqual(list(self.db.scalars(select(CallDelivery))),[])

    def test_restart_during_nexara_submit_requires_review(self):
        r=self.call();r.state="submitting";self.db.commit()
        with patch.object(call_worker,"NexaraClient"):
            call_worker.process_calls(SimpleNamespace())
        self.db.refresh(r);self.assertEqual(r.state,"submit_ambiguous")

    def test_submit_timeout_does_not_resubmit(self):
        r=self.call();r.state="ready";r.recording_url="https://example.invalid/a.mp3";self.db.commit()
        settings.calls_process_enabled=True;settings.nexara_api_key="test";settings.calls_recording_hosts="example.invalid"
        import io
        with patch.object(call_worker,"download_audio",return_value=(io.BytesIO(b"audio"),"audio/mpeg")),patch.object(call_worker,"NexaraClient") as cls:
            cls.return_value.submit.side_effect=AmbiguousSubmission("nexara_submit_transport_ambiguous")
            call_worker.process_calls(SimpleNamespace());call_worker.process_calls(SimpleNamespace())
            self.assertEqual(cls.return_value.submit.call_count,1)
        self.db.refresh(r);self.assertEqual(r.state,"submit_ambiguous")

    def test_summary_excludes_calibration_and_unknown_from_named_manager(self):
        r=self.call();r.state="complete";r.is_scored=True;r.score=50;r.analysis=analysis();self.db.commit()
        c=call_worker.upsert_note(self.db,self.note(id=2,uniq="calibration"),"lead",100,[100],calibration=True)
        c.state="complete";c.is_scored=True;c.score=100;c.analysis=analysis();self.db.commit()
        with patch.object(calls_api,"SessionLocal",self.factory):
            s=calls_api.summary();self.assertEqual(s["scored"],1);self.assertEqual(s["average_score"],50)
            self.assertIsNone(s["managers"][0]["manager_id"])
            self.assertEqual(calls_api.summary(manager="13334858")["total"],0)

    def test_sales_collector_preserves_all_configured_managers(self):
        from app.collector import build_daily
        from app.models import LeadSnapshot
        for i,uid in enumerate(settings.manager_key_map_dict,1):
            self.db.add(LeadSnapshot(lead_id=i,pipeline_id=1,
                status_id=settings.status_won,responsible_user_id=uid,created_at=1000,closed_at=1100))
        self.db.commit()
        daily=build_daily(self.db)
        self.assertEqual(sum(b["traffic"] for b in daily.values()),len(settings.manager_key_map_dict))
        for key in settings.manager_key_map_dict.values():
            self.assertEqual(sum(b["mgr"][key]["traffic"] for b in daily.values()),1)
            self.assertEqual(sum(b["mgr"][key]["won"] for b in daily.values()),1)

    def test_call_endpoints_require_login(self):
        app=FastAPI();app.include_router(calls_api.router);client=TestClient(app)
        for path in ("/api/calls","/api/calls/summary","/api/calls/1","/api/calls/1/recording"):
            self.assertEqual(client.get(path).status_code,401)

    def test_msk_midnight_bounds(self):
        from datetime import date
        r=self.call();r.occurred_at=int(datetime(2026,10,7,21,0,tzinfo=timezone.utc).timestamp());self.db.commit()
        with patch.object(calls_api,"SessionLocal",self.factory):
            self.assertEqual(calls_api.summary(start=date(2026,10,7),end=date(2026,10,7))["total"],0)
            self.assertEqual(calls_api.summary(start=date(2026,10,8),end=date(2026,10,8))["total"],1)


class RecordingTest(unittest.TestCase):
    def test_recording_host_allowlist_and_private_urls(self):
        for url in ("http://mango.example/a","https://other.example/a","https://user:pass@mango.example/a","https://mango.example:8080/a"):
            with self.assertRaises(Exception):validate_recording_url(url,{"mango.example"},resolve=False)
        validate_recording_url("https://mango.example/a",{"mango.example"},resolve=False)


class ReportTest(unittest.TestCase):
    def test_long_telegram_report_preserves_every_criterion(self):
        a=analysis();a["summary"]="Суть разговора. "*70
        for c in a["criteria"]:
            c["explanation"]="Объяснение. "*50
            c["evidence"]=[{"quote":"Короткое доказательство. "*20,"start":0},
                           {"quote":"Второе доказательство. "*20,"start":10}]
        row=SimpleNamespace(id=1,account_id=32590598,occurred_at=1000,duration_sec=60,
                            analysis=a,manager_verified=False,manager_id=None,is_calibration=False,
                            is_scored=True,yes_count=7,applicable_count=7,score=100,
                            category="primary_inbound",category_reason="Входящий",state="complete",
                            lead_ids=[100],rule_version="test",validation_errors=[])
        result=render_report(row,telegram=True)
        self.assertLessEqual(len(result),3900)
        for i,(name,_) in CRITERIA.items():self.assertIn(f"{i}. {name}:",result)
        self.assertIn('/?call=1#callQuality',result)


if __name__ == "__main__":
    unittest.main()
