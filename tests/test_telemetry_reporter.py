from sound_vault.telemetry.reporter import SaveEventReporter


class _RecordingPost:
    def __init__(self, *, raises=False):
        self.calls = []
        self.raises = raises

    def __call__(self, url, payload, *, timeout=8.0):
        self.calls.append((url, payload))
        if self.raises:
            raise OSError("network down")
        return 200


def test_disabled_reporter_does_not_post():
    post = _RecordingPost()
    reporter = SaveEventReporter(base_url="https://relay.example", enabled=False, post=post)
    assert reporter.report_save(sound_id="a") is False
    assert post.calls == []


def test_enabled_reporter_posts_anonymized_payload():
    post = _RecordingPost()
    reporter = SaveEventReporter(base_url="https://relay.example/", enabled=True, post=post)
    assert reporter.report_save(sound_id="123", platform="tiktok", title="T", artist="A") is True
    url, payload = post.calls[0]
    assert url == "https://relay.example/v1/events/save"
    assert payload == {"sound_id": "123", "platform": "tiktok", "title": "T", "artist": "A"}
    # never leaks identifiers
    assert "device_secret" not in payload and "folder" not in payload


def test_failure_is_swallowed():
    reporter = SaveEventReporter(base_url="https://relay.example", enabled=True, post=_RecordingPost(raises=True))
    assert reporter.report_save(sound_id="a") is False


def test_no_base_url_or_no_sound_id():
    post = _RecordingPost()
    assert SaveEventReporter(base_url="", enabled=True, post=post).report_save(sound_id="a") is False
    assert SaveEventReporter(base_url="https://r", enabled=True, post=post).report_save(sound_id="") is False
    assert post.calls == []
