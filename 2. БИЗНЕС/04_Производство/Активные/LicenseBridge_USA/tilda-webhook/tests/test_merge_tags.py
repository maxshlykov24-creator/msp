from app.merge_tags import allocate_slot, ensure_pool, free_slot, pool_stats, tag_name


def test_tag_name():
    assert tag_name("contact", 1) == "mc_01"
    assert tag_name("deal", 12) == "md_12"


def test_allocate_and_free(session):
    ensure_pool(session)
    slot = allocate_slot(session, "contact", "contact:1-2")
    assert slot.status == "used"
    assert slot.tag.startswith("mc_")
    # повтор той же пары → тот же слот (идемпотентность)
    slot2 = allocate_slot(session, "contact", "contact:1-2")
    assert slot2.tag == slot.tag
    stats = pool_stats(session)
    assert stats["contact"]["used"] == 1
    free_slot(session, "contact", slot.tag)
    assert pool_stats(session)["contact"]["used"] == 0
