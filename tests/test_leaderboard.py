from fastapi.testclient import TestClient

from sound_vault.relay import server
from sound_vault.relay.leaderboard import LeaderboardStore


def test_record_and_rank():
    store = LeaderboardStore(now=lambda: 1000.0)
    store.record_save(sound_id="a", title="Alpha", artist="X", platform="tiktok")
    store.record_save(sound_id="a", title="Alpha", artist="X", platform="tiktok")
    store.record_save(sound_id="b", title="Beta", artist="Y", platform="youtube")
    board = store.leaderboard()
    assert [(e.sound_id, e.saves) for e in board] == [("a", 2), ("b", 1)]
    assert board[0].title == "Alpha"
    assert board[1].platform == "youtube"


def test_window_filter():
    clock = {"t": 1_000_000.0}
    store = LeaderboardStore(now=lambda: clock["t"])
    store.record_save(sound_id="old", occurred_at=clock["t"] - (8 * 24 * 60 * 60))
    store.record_save(sound_id="new", occurred_at=clock["t"] - 60)
    week = store.leaderboard(window="7d")
    assert [e.sound_id for e in week] == ["new"]
    assert {e.sound_id for e in store.leaderboard(window="all")} == {"old", "new"}


def test_limit_and_empty_sound_id():
    store = LeaderboardStore(now=lambda: 1.0)
    store.record_save(sound_id="")  # ignored
    for i in range(5):
        store.record_save(sound_id=f"s{i}")
    assert store.leaderboard() and all(e.sound_id for e in store.leaderboard())
    assert len(store.leaderboard(limit=2)) == 2


def test_persistence_round_trip(tmp_path):
    db = tmp_path / "lb.sqlite3"
    first = LeaderboardStore(now=lambda: 5.0, db_path=db)
    first.record_save(sound_id="a", title="Alpha", artist="X", platform="tiktok")
    first.record_save(sound_id="a")
    restarted = LeaderboardStore(now=lambda: 6.0, db_path=db)
    board = restarted.leaderboard()
    assert board[0].sound_id == "a"
    assert board[0].saves == 2
    assert board[0].title == "Alpha"


def test_endpoints_record_and_read():
    server.leaderboard_store.reset()
    server.rate_limiter.reset()
    client = TestClient(server.app)
    assert client.post("/v1/events/save", json={"sound_id": "a", "title": "Alpha", "platform": "tiktok"}).status_code == 200
    client.post("/v1/events/save", json={"sound_id": "a"})
    client.post("/v1/events/save", json={"sound_id": "b", "title": "Beta"})
    response = client.get("/v1/leaderboard?limit=10")
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert entries[0]["sound_id"] == "a"
    assert entries[0]["saves"] == 2


def test_save_event_requires_sound_id():
    server.rate_limiter.reset()
    client = TestClient(server.app)
    assert client.post("/v1/events/save", json={"title": "no id"}).status_code == 422
    assert client.post("/v1/events/save", json={"sound_id": "   "}).status_code == 422
