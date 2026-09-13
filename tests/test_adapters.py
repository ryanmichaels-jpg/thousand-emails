from api.adapters import get_adapter

def test_fixture_outreach_enrollment_is_idempotent():
    o = get_adapter("outreach")
    pid = o.upsert_prospect({"Email": "Someone.New@example.com"})
    assert o.upsert_prospect({"Email": "someone.new@example.com"}) == pid
    s1 = o.add_to_sequence(pid, "seq_3"); s2 = o.add_to_sequence(pid, "seq_3")
    assert s1 == s2

def test_stub_sender_respects_suppression():
    s = get_adapter("sender"); s.suppress("blocked@example.com", "unsubscribed")
    try:
        s.send("d1", "blocked@example.com", "hi", "body"); assert False
    except PermissionError: pass
