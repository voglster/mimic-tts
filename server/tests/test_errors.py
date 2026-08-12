from mimic_server.errors import AmbiguousVoice, MimicError, QuotaExceeded, VoiceNotFound


def test_error_carries_status_and_code():
    err = VoiceNotFound("no voice 'x'")
    assert err.status == 404
    assert err.code == "voice_not_found"
    assert str(err) == "no voice 'x'"


def test_error_payload_merges_extra():
    err = QuotaExceeded("over", extra={"used": 10, "limit": 5})
    assert err.status == 429
    assert err.payload() == {
        "error": "quota_exceeded",
        "detail": "over",
        "used": 10,
        "limit": 5,
    }


def test_ambiguous_is_409_and_a_mimic_error():
    err = AmbiguousVoice("pick one", extra={"candidates": ["a/x", "b/x"]})
    assert err.status == 409
    assert isinstance(err, MimicError)
