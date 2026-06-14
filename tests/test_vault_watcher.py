import threading

from sound_vault.vault.watcher import VaultWatcher, _VaultEventHandler


class _Event:
    def __init__(self, src_path, is_directory=False):
        self.src_path = src_path
        self.is_directory = is_directory


def test_relevance_filter():
    rel = _VaultEventHandler.is_relevant
    assert rel("/v/sounds/1 - x/metadata.json") is True
    assert rel("/v/catalog/sounds.jsonl") is True
    assert rel("/v/catalog/.sounds.jsonl.tmp") is False
    assert rel("/v/catalog/sounds.jsonl.tmp") is False
    assert rel("/v/index.sqlite3-wal") is False
    assert rel("/v/index.sqlite3-shm") is False
    assert rel("/v/catalog/.sounds.jsonl.lock") is False
    assert rel("/v/x/__pycache__/y.pyc") is False


def test_handler_schedules_only_on_relevant_events():
    calls = []
    handler = _VaultEventHandler(lambda: calls.append(1))
    handler.on_any_event(_Event("/v/sounds/1 - x/metadata.json"))
    assert calls == [1]
    handler.on_any_event(_Event("/v/catalog/.sounds.jsonl.tmp"))
    assert calls == [1]


class _FakeObserver:
    def __init__(self):
        self.scheduled = []
        self.started = False
        self.stopped = False

    def schedule(self, handler, path, recursive=False):
        self.scheduled.append((path, recursive))

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        pass


def test_watcher_debounces_then_fires(tmp_path):
    fired = threading.Event()
    observer = _FakeObserver()
    watcher = VaultWatcher(
        tmp_path,
        on_change=lambda: fired.set(),
        debounce_seconds=0.01,
        observer_factory=lambda: observer,
    )
    watcher.start()
    assert observer.started
    assert any(path.endswith("sounds") for path, _ in observer.scheduled)
    assert any(path.endswith("catalog") for path, _ in observer.scheduled)
    watcher._schedule()
    assert fired.wait(2.0)
    watcher.stop()
    assert observer.stopped


def test_watcher_coalesces_rapid_events(tmp_path):
    calls = []
    done = threading.Event()

    def on_change():
        calls.append(1)
        done.set()

    watcher = VaultWatcher(
        tmp_path, on_change=on_change, debounce_seconds=0.05, observer_factory=_FakeObserver
    )
    watcher.start()
    for _ in range(10):
        watcher._schedule()
    assert done.wait(2.0)
    watcher.stop()
    assert calls == [1]  # 10 rapid events coalesced into one rebuild
