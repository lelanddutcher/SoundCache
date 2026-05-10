from __future__ import annotations

from pathlib import Path
import json
import urllib.error
import urllib.request

from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QSlider,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sound_vault.settings import AppSettings, default_index_path
from sound_vault.ui.view_model import LibraryViewModel

SORT_ROLE = Qt.ItemDataRole.UserRole + 1

DESIGN_LANGUAGE = """
Sound Vault visual direction: iTunes 5 smooth-metal header + Aqua capsule display + LimeWire 2001 portal/source-list density.
Use early-web green/blue utility accents, beveled tabs, crisp white content panes, and compact Verdana/SF system typography.
""".strip()


class SortableTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other) -> bool:  # noqa: D105 - Qt sort hook
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE) if isinstance(other, QTableWidgetItem) else None
        if left is not None and right is not None:
            return left < right
        return super().__lt__(other)


class SeekSlider(QSlider):
    seekRequested = Signal(int)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.maximum() > self.minimum():
            ratio = max(0.0, min(1.0, event.position().x() / max(1, self.width())))
            value = round(self.minimum() + ratio * (self.maximum() - self.minimum()))
            self.setValue(value)
            self.seekRequested.emit(value)
        super().mousePressEvent(event)


class SettingsDialog(QDialog):
    def __init__(self, *, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.relay_url = QLineEdit(settings.relay_base_url())
        self.relay_url.setPlaceholderText("https://your-relay.example")
        self.pair_code = QLineEdit(settings.relay_pair_code())
        self.pair_code.setPlaceholderText("VAULT-1234")
        self.device_id = QLineEdit(settings.relay_device_id())
        self.device_secret = QLineEdit(settings.relay_device_secret())
        self.device_secret.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Relay URL", self.relay_url)
        form.addRow("Pair code", self.pair_code)
        form.addRow("Device ID", self.device_id)
        form.addRow("Device secret", self.device_secret)
        layout.addLayout(form)

        self.result_label = QLabel("Create pairing code on the relay, then add it to the iOS Shortcut.")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        actions = QHBoxLayout()
        create_pairing = QPushButton("Create pairing code")
        create_pairing.clicked.connect(self.create_pairing_code)
        save = QPushButton("Save relay settings")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.save_settings)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        actions.addWidget(create_pairing)
        actions.addStretch(1)
        actions.addWidget(save)
        actions.addWidget(close)
        layout.addLayout(actions)

    def save_settings(self) -> None:
        self.settings.set_relay_config(
            base_url=self.relay_url.text(),
            pair_code=self.pair_code.text(),
            device_id=self.device_id.text(),
            device_secret=self.device_secret.text(),
        )
        self.result_label.setText("Relay settings saved. Device secret is stored locally and hidden here.")

    def create_pairing_code(self) -> None:
        base_url = self.relay_url.text().strip().rstrip("/")
        if not base_url:
            self.result_label.setText("Enter a relay URL first.")
            return
        try:
            request = urllib.request.Request(
                f"{base_url}/v1/pairing/create",
                data=json.dumps({"device_name": "Sound Vault Desktop"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310 - user relay URL
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            self.result_label.setText(f"Pairing failed: {exc}")
            return
        self.pair_code.setText(str(payload.get("pair_code") or ""))
        self.device_id.setText(str(payload.get("device_id") or ""))
        self.device_secret.setText(str(payload.get("device_secret") or ""))
        self.save_settings()
        self.result_label.setText(
            "Create pairing code succeeded. Put this pair code in the iOS Shortcut: "
            f"{self.pair_code.text()}"
        )


class SoundVaultWindow(QMainWindow):
    def __init__(self, *, vault_root: Path | None = None, settings: AppSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or AppSettings()
        self.setWindowTitle("Sound Vault — local sound archive")
        self.resize(1420, 860)
        self.vault_root = vault_root or self.settings.vault_root()
        self.vm = LibraryViewModel(vault_root=self.vault_root, index_path=default_index_path())
        self.current_rows = []
        self.records_by_id = {}
        self.current_inbox_rows = []
        self.inbox_rows_by_id = {}
        self.current_dedupe_groups = []
        self.dedupe_groups_by_id = {}
        self.current_preview_record = None
        self._index_future = None
        self._index_timer = QTimer(self)
        self._index_timer.setInterval(150)
        self._index_timer.timeout.connect(self._finish_async_index)
        self._search_timer = QTimer(self)
        self._search_timer.setInterval(220)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.refresh_table)
        self.audio_output = QAudioOutput(self)
        self.audio_player = QMediaPlayer(self)
        self.audio_player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.85)
        self.audio_player.positionChanged.connect(self._player_position_changed)
        self.audio_player.durationChanged.connect(self._player_duration_changed)
        self.audio_player.playbackStateChanged.connect(self._player_state_changed)
        self.audio_player.errorOccurred.connect(self._player_error_occurred)
        self._build_ui()
        self.setup_keyboard_shortcuts()
        self.restore_library_search_state()
        self.update_pairing_status()
        self.rebuild_index()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(238)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 22, 16, 22)
        side.setSpacing(10)
        brand = QLabel("▸ Sound Vault")
        brand.setObjectName("brand")
        side.addWidget(brand)
        source_label = QLabel("Source List")
        source_label.setObjectName("sourceGroup")
        side.addWidget(source_label)
        self.nav_buttons = {}
        for label, view_name in [
            ("Library", "library"),
            ("Ingest inbox", "inbox"),
            ("Review queues", "review"),
            ("Duplicate review", "dedupe"),
            ("Worker status", "worker"),
            ("Settings", "settings"),
        ]:
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            btn.clicked.connect(lambda _checked=False, name=view_name: self.show_view(name))
            self.nav_buttons[view_name] = btn
            side.addWidget(btn)
        side.addStretch(1)
        self.pairing_label = QLabel("Shortcut relay\nRelay not configured\nOpen Settings to pair")
        self.pairing_label.setObjectName("pairingCard")
        self.pairing_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(self.pairing_label)
        shell.addWidget(sidebar)

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(24, 22, 18, 22)
        main_layout.setSpacing(12)
        top = QHBoxLayout()
        title_box = QVBoxLayout()
        self.title_label = QLabel("Library")
        self.title_label.setObjectName("title")
        self.vault_label = QLabel(str(self.vault_root))
        self.vault_label.setObjectName("muted")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.vault_label)
        top.addLayout(title_box)
        top.addStretch(1)
        choose = QPushButton("Choose vault")
        choose.clicked.connect(self.choose_vault)
        rebuild = QPushButton("Rebuild index")
        rebuild.setObjectName("primaryButton")
        rebuild.clicked.connect(self.rebuild_index)
        top.addWidget(choose)
        top.addWidget(rebuild)
        main_layout.addLayout(top)
        main_layout.addWidget(self._build_jukebox_chrome())

        self.stack = QStackedWidget()
        self.library_view = self._build_library_view()
        self.inbox_view = self._build_inbox_view()
        self.review_view = self._build_review_view()
        self.dedupe_view = self._build_dedupe_view()
        self.worker_view = self._build_worker_view()
        self.stack.addWidget(self.library_view)
        self.stack.addWidget(self.inbox_view)
        self.stack.addWidget(self.review_view)
        self.stack.addWidget(self.dedupe_view)
        self.stack.addWidget(self.worker_view)
        main_layout.addWidget(self.stack, 1)
        shell.addWidget(main, 1)

        preview = self._build_preview_panel()
        shell.addWidget(preview)
        self.setStyleSheet(STYLESHEET)
        self.show_view("library")

    def _build_jukebox_chrome(self) -> QWidget:
        chrome = QFrame()
        chrome.setObjectName("chromeHeader")
        layout = QHBoxLayout(chrome)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(14)

        transport = QFrame()
        transport.setObjectName("transportDeck")
        transport_layout = QHBoxLayout(transport)
        transport_layout.setContentsMargins(9, 8, 9, 8)
        transport_layout.setSpacing(7)
        for label in ["◀◀", "▶", "▮▮", "▶▶"]:
            button = QToolButton()
            button.setText(label)
            button.setObjectName("transportButton")
            transport_layout.addWidget(button)
        layout.addWidget(transport)

        display = QFrame()
        display.setObjectName("capsuleDisplay")
        display_layout = QVBoxLayout(display)
        display_layout.setContentsMargins(18, 8, 18, 8)
        display_layout.setSpacing(2)
        now_playing = QLabel("Now Playing")
        now_playing.setObjectName("displayEyebrow")
        display_title = QLabel("select a sound to inspect waveform, provenance, transcript, and evidence")
        display_title.setObjectName("displayTitle")
        display_subtitle = QLabel("local-first jukebox archive • early web source intelligence")
        display_subtitle.setObjectName("displaySubtitle")
        display_layout.addWidget(now_playing)
        display_layout.addWidget(display_title)
        display_layout.addWidget(display_subtitle)
        layout.addWidget(display, 1)

        tabs = QFrame()
        tabs.setObjectName("libraryTabs")
        tabs_layout = QHBoxLayout(tabs)
        tabs_layout.setContentsMargins(7, 7, 7, 7)
        tabs_layout.setSpacing(6)
        for label in ["LIBRARY", "EVIDENCE", "TRANSCRIPTS", "POPULARITY"]:
            tab = QLabel(label)
            tab.setObjectName("portalTab")
            tab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tabs_layout.addWidget(tab)
        layout.addWidget(tabs)

        status = QFrame()
        status.setObjectName("limeStatusPanel")
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(10, 7, 10, 7)
        status_layout.setSpacing(7)
        dot = QLabel("●")
        dot.setObjectName("limeStatusDot")
        status_text = QLabel("INDEX ONLINE")
        status_text.setObjectName("statusReadout")
        status_layout.addWidget(dot)
        status_layout.addWidget(status_text)
        layout.addWidget(status)
        return chrome

    def _build_library_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        search_bar = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search title, artist, tag, music ID, spoken phrase…")
        self.search_box.textChanged.connect(self.schedule_refresh_table)
        self.duration_filter = QComboBox()
        self.duration_filter.addItem("All durations", "all")
        self.duration_filter.addItem("Under 30s", "under_30")
        self.duration_filter.addItem("30s and up", "30_plus")
        self.duration_filter.currentIndexChanged.connect(self.schedule_refresh_table)
        self.media_filter = QComboBox()
        self.media_filter.addItem("All media", "all")
        self.media_filter.addItem("Has audio", "has_audio")
        self.media_filter.addItem("Missing audio", "missing_audio")
        self.media_filter.addItem("Has artwork", "has_artwork")
        self.media_filter.addItem("Missing artwork", "missing_artwork")
        self.media_filter.addItem("Has transcript", "has_transcript")
        self.media_filter.addItem("Missing transcript", "missing_transcript")
        self.media_filter.addItem("Has associated videos", "has_videos")
        self.media_filter.addItem("Missing associated videos", "missing_videos")
        self.media_filter.addItem("Has evidence", "has_evidence")
        self.media_filter.addItem("Missing evidence", "missing_evidence")
        self.media_filter.currentIndexChanged.connect(self.schedule_refresh_table)
        self.status_filter = QComboBox()
        self.status_filter.addItem("All statuses", "all")
        self.status_filter.addItem("Approved", "approved")
        self.status_filter.addItem("Needs review", "needs_review")
        self.status_filter.addItem("Unreviewed", "unreviewed")
        self.status_filter.currentIndexChanged.connect(self.schedule_refresh_table)
        self.usage_filter = QComboBox()
        self.usage_filter.addItem("All usage", "all")
        self.usage_filter.addItem("Unknown usage", "unknown_usage")
        self.usage_filter.addItem("Under 1K uses", "under_1k")
        self.usage_filter.addItem("1K+ uses", "over_1k")
        self.usage_filter.addItem("100K+ uses", "over_100k")
        self.usage_filter.addItem("1M+ uses", "over_1m")
        self.usage_filter.currentIndexChanged.connect(self.schedule_refresh_table)
        self.column_menu = QMenu("Columns", self)
        columns_button = QToolButton()
        columns_button.setText("Columns")
        columns_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        columns_button.setMenu(self.column_menu)
        self.result_count_label = QLabel("0 displayed / 0 indexed")
        self.result_count_label.setObjectName("muted")
        search_bar.addWidget(self.search_box, 1)
        search_bar.addWidget(self.duration_filter)
        search_bar.addWidget(self.media_filter)
        search_bar.addWidget(self.status_filter)
        search_bar.addWidget(self.usage_filter)
        search_bar.addWidget(columns_button)
        search_bar.addWidget(self.result_count_label)
        layout.addLayout(search_bar)
        stats_grid = QGridLayout()
        self.stats_label = QLabel("index not built")
        self.stats_label.setObjectName("statCard")
        self.inbox_label = QLabel("Shortcut inbox\n0 pending")
        self.inbox_label.setObjectName("statCard")
        self.worker_label = QLabel("Worker\nidle")
        self.worker_label.setObjectName("statCard")
        self.catalog_label = QLabel("Catalog\nrows vs unique IDs pending")
        self.catalog_label.setObjectName("statCard")
        stats_grid.addWidget(self.stats_label, 0, 0)
        stats_grid.addWidget(self.inbox_label, 0, 1)
        stats_grid.addWidget(self.worker_label, 0, 2)
        stats_grid.addWidget(self.catalog_label, 0, 3)
        layout.addLayout(stats_grid)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "play",
                "sound",
                "artist/source",
                "status",
                "added",
                "packaged",
                "popularity",
                "videos",
                "local audio",
                "trend/context",
            ]
        )
        self._prepare_table(self.table, stretch_column=1)
        self.configure_column_menu()
        self.restore_hidden_columns()
        self.restore_table_layout("library", self.table)
        self.table.itemSelectionChanged.connect(self.update_preview_from_selection)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.open_library_context_menu)
        layout.addWidget(self.table, 1)
        return view

    def _build_inbox_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        inbox_title = QLabel("Shortcut inbox")
        inbox_title.setObjectName("sectionTitle")
        refresh_inbox = QPushButton("Refresh inbox")
        refresh_inbox.clicked.connect(self.refresh_inbox)
        poll_relay = QPushButton("Poll relay")
        poll_relay.clicked.connect(self.poll_relay_inbox)
        mark_imported = QPushButton("Mark selected imported")
        mark_imported.clicked.connect(self.mark_selected_inbox_imported)
        header.addWidget(inbox_title)
        header.addStretch(1)
        header.addWidget(poll_relay)
        header.addWidget(refresh_inbox)
        header.addWidget(mark_imported)
        layout.addLayout(header)
        self.inbox_table = QTableWidget(0, 4)
        self.inbox_table.setHorizontalHeaderLabels(["received", "source", "url", "status"])
        self._prepare_table(self.inbox_table, stretch_column=2)
        self.restore_table_layout("inbox", self.inbox_table)
        layout.addWidget(self.inbox_table, 1)
        return view

    def _build_review_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        review_title = QLabel("Review queues")
        review_title.setObjectName("sectionTitle")
        refresh_review = QPushButton("Refresh review queues")
        refresh_review.clicked.connect(self.refresh_review_queues)
        header.addWidget(review_title)
        header.addStretch(1)
        header.addWidget(refresh_review)
        layout.addLayout(header)
        self.review_table = QTableWidget(0, 3)
        self.review_table.setHorizontalHeaderLabels(["queue", "count", "next action"])
        self.review_table.itemDoubleClicked.connect(self.apply_review_queue_filter)
        self._prepare_table(self.review_table, stretch_column=2)
        layout.addWidget(self.review_table, 1)
        return view

    def _build_dedupe_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        dedupe_title = QLabel("Duplicate review")
        dedupe_title.setObjectName("sectionTitle")
        refresh_dedupe = QPushButton("Refresh duplicate candidates")
        refresh_dedupe.clicked.connect(self.refresh_dedupe_review)
        mark_duplicates = QPushButton("Mark duplicates")
        mark_duplicates.clicked.connect(lambda: self.record_selected_duplicate_decision("duplicates"))
        mark_not_duplicates = QPushButton("Mark not duplicates")
        mark_not_duplicates.clicked.connect(lambda: self.record_selected_duplicate_decision("not_duplicates"))
        header.addWidget(dedupe_title)
        header.addStretch(1)
        header.addWidget(refresh_dedupe)
        header.addWidget(mark_duplicates)
        header.addWidget(mark_not_duplicates)
        layout.addLayout(header)
        tables = QHBoxLayout()
        self.dedupe_groups_table = QTableWidget(0, 4)
        self.dedupe_groups_table.setHorizontalHeaderLabels(["group", "score", "candidates", "reason"])
        self.dedupe_groups_table.itemSelectionChanged.connect(self.refresh_dedupe_candidates)
        self._prepare_table(self.dedupe_groups_table, stretch_column=3)
        self.dedupe_candidates_table = QTableWidget(0, 5)
        self.dedupe_candidates_table.setHorizontalHeaderLabels(["play", "music id", "title", "artist", "audio/folder"])
        self._prepare_table(self.dedupe_candidates_table, stretch_column=2)
        tables.addWidget(self.dedupe_groups_table, 1)
        tables.addWidget(self.dedupe_candidates_table, 2)
        layout.addLayout(tables, 1)
        return view

    def _build_worker_view(self) -> QWidget:
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        worker_title = QLabel("Worker status")
        worker_title.setObjectName("sectionTitle")
        refresh_worker = QPushButton("Refresh worker status")
        refresh_worker.clicked.connect(self.refresh_worker_status)
        header.addWidget(worker_title)
        header.addStretch(1)
        header.addWidget(refresh_worker)
        layout.addLayout(header)
        self.worker_status_table = QTableWidget(0, 2)
        self.worker_status_table.setHorizontalHeaderLabels(["metric", "value"])
        self._prepare_table(self.worker_status_table, stretch_column=1)
        layout.addWidget(self.worker_status_table, 1)
        return view

    def _build_preview_panel(self) -> QFrame:
        preview = QFrame()
        preview.setObjectName("preview")
        preview.setFixedWidth(430)
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(20, 22, 20, 22)
        preview_layout.setSpacing(10)
        self.artwork_label = QLabel("no artwork")
        self.artwork_label.setObjectName("artwork")
        self.artwork_label.setFixedHeight(170)
        self.artwork_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_title = QLabel("Select a sound")
        self.preview_title.setObjectName("previewTitle")
        self.preview_title.setWordWrap(True)
        self._make_label_selectable(self.preview_title)
        self.preview_meta = QLabel("Audio, tags, evidence and local paths will show here.")
        self.preview_meta.setObjectName("muted")
        self.preview_meta.setWordWrap(True)
        self._make_label_selectable(self.preview_meta)
        self.preview_tags = QLabel("")
        self.preview_tags.setWordWrap(True)
        self._make_label_selectable(self.preview_tags)
        self.play_button = QPushButton("Play")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.play_selected_sound)
        self.progress_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setRange(0, 0)
        self.progress_slider.sliderMoved.connect(self.audio_player.setPosition)
        self.progress_slider.seekRequested.connect(self.audio_player.setPosition)
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("muted")
        self._make_label_selectable(self.time_label)
        self.playback_status = QLabel("Playback idle")
        self.playback_status.setObjectName("muted")
        self.playback_status.setWordWrap(True)
        self._make_label_selectable(self.playback_status)
        self.copy_metadata = QPushButton("Copy metadata")
        self.copy_metadata.setEnabled(False)
        self.copy_metadata.clicked.connect(self.copy_selected_metadata)
        self.open_folder = QPushButton("Open folder")
        self.open_folder.setEnabled(False)
        self.open_folder.clicked.connect(self.open_selected_folder)
        evidence_group = QGroupBox("Evidence")
        evidence_layout = QVBoxLayout(evidence_group)
        self.evidence_list = QLabel("No local screenshots yet.")
        self.evidence_list.setWordWrap(True)
        self._make_label_selectable(self.evidence_list)
        evidence_layout.addWidget(self.evidence_list)
        video_group = QGroupBox("Associated videos")
        video_layout = QVBoxLayout(video_group)
        self.video_table = QTableWidget(0, 4)
        self.video_table.setHorizontalHeaderLabels(["rank", "creator", "clip", "notes"])
        self._prepare_table(self.video_table, stretch_column=3)
        self.video_table.setMaximumHeight(170)
        video_layout.addWidget(self.video_table)
        self.raw_toggle = QToolButton()
        self.raw_toggle.setText("Raw metadata")
        self.raw_toggle.setCheckable(True)
        self.raw_toggle.setChecked(False)
        self.raw_toggle.clicked.connect(lambda checked: self.preview_json.setVisible(bool(checked)))
        self.preview_json = QTextEdit()
        self.preview_json.setReadOnly(True)
        self.preview_json.setVisible(False)
        self.preview_json.setPlaceholderText("raw metadata preview")
        preview_layout.addWidget(self.artwork_label)
        preview_layout.addWidget(self.preview_title)
        preview_layout.addWidget(self.preview_meta)
        preview_layout.addWidget(self.preview_tags)
        preview_layout.addWidget(self.play_button)
        preview_layout.addWidget(self.progress_slider)
        preview_layout.addWidget(self.time_label)
        preview_layout.addWidget(self.playback_status)
        preview_layout.addWidget(self.copy_metadata)
        preview_layout.addWidget(self.open_folder)
        preview_layout.addWidget(evidence_group)
        preview_layout.addWidget(video_group)
        preview_layout.addWidget(self.raw_toggle)
        preview_layout.addWidget(self.preview_json, 1)
        return preview

    def setup_keyboard_shortcuts(self) -> None:
        find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        find_shortcut.activated.connect(self.focus_search)
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self)
        copy_shortcut.activated.connect(self.copy_selected_metadata)
        play_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        play_shortcut.activated.connect(self.play_selected_sound)
        escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape_shortcut.activated.connect(self.clear_search)

    def focus_search(self) -> None:
        self.search_box.setFocus()
        self.search_box.selectAll()
        self.statusBar().showMessage("Search focused", 1500)

    def clear_search(self) -> None:
        if self.search_box.text():
            self.search_box.clear()
            self.statusBar().showMessage("Search cleared", 1500)

    def _prepare_table(self, table: QTableWidget, *, stretch_column: int) -> None:
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSortingEnabled(True)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        header.setSectionsMovable(True)
        header.setStretchLastSection(False)
        for idx in range(table.columnCount()):
            header.setSectionResizeMode(idx, QHeaderView.ResizeMode.Interactive)
        table.resizeColumnsToContents()
        header.resizeSection(stretch_column, max(header.sectionSize(stretch_column), 300))

    @staticmethod
    def _make_label_selectable(label: QLabel) -> None:
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )

    def restore_table_layout(self, name: str, table: QTableWidget) -> None:
        state = self.settings.table_layout(name)
        if state:
            table.horizontalHeader().restoreState(QByteArray(state))

    def save_table_layout(self, name: str, table: QTableWidget) -> None:
        self.settings.set_table_layout(name, bytes(table.horizontalHeader().saveState()))

    def configure_column_menu(self) -> None:
        self.column_menu.clear()
        for column in range(self.table.columnCount()):
            label = self.table.horizontalHeaderItem(column).text()
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(column))
            action.triggered.connect(lambda checked=False, col=column: self.toggle_column_visibility(col, bool(checked)))
            self.column_menu.addAction(action)

    def toggle_column_visibility(self, column: int, visible: bool) -> None:
        if column == 1 and not visible:
            self.statusBar().showMessage("Sound column stays visible", 2500)
            return
        self.table.horizontalHeader().setSectionHidden(column, not visible)
        self.settings.set_hidden_table_columns("library", self.hidden_library_columns())
        self.configure_column_menu()

    def hidden_library_columns(self) -> list[int]:
        return [column for column in range(self.table.columnCount()) if self.table.isColumnHidden(column)]

    def restore_hidden_columns(self) -> None:
        for column in self.settings.hidden_table_columns("library"):
            if 0 <= column < self.table.columnCount() and column != 1:
                self.table.horizontalHeader().setSectionHidden(column, True)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.save_table_layout("library", self.table)
        self.save_table_layout("inbox", self.inbox_table)
        self.settings.set_hidden_table_columns("library", self.hidden_library_columns())
        self.save_library_search_state()
        super().closeEvent(event)

    def _set_combo_by_data(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def restore_library_search_state(self) -> None:
        state = self.settings.library_search_state()
        self.search_box.setText(str(state.get("query") or ""))
        self._set_combo_by_data(self.duration_filter, str(state.get("duration_filter") or "all"))
        self._set_combo_by_data(self.media_filter, str(state.get("media_filter") or "all"))
        self._set_combo_by_data(self.status_filter, str(state.get("status_filter") or "all"))
        self._set_combo_by_data(self.usage_filter, str(state.get("usage_filter") or "all"))

    def save_library_search_state(self) -> None:
        self.settings.set_library_search_state(
            {
                "query": self.search_box.text(),
                "duration_filter": str(self.duration_filter.currentData() or "all"),
                "media_filter": str(self.media_filter.currentData() or "all"),
                "status_filter": str(self.status_filter.currentData() or "all"),
                "usage_filter": str(self.usage_filter.currentData() or "all"),
                "selected_music_id": self._selected_music_id() or "",
            }
        )

    def show_view(self, name: str) -> None:
        if name == "settings":
            self.open_settings_dialog()
            return
        mapping = {
            "library": (self.library_view, "Library"),
            "inbox": (self.inbox_view, "Shortcut inbox"),
            "review": (self.review_view, "Review queues"),
            "dedupe": (self.dedupe_view, "Duplicate review"),
            "worker": (self.worker_view, "Worker status"),
        }
        widget, title = mapping.get(name, mapping["library"])
        self.stack.setCurrentWidget(widget)
        self.title_label.setText(title)
        for key, button in self.nav_buttons.items():
            button.setProperty("active", key == name)
            button.style().unpolish(button)
            button.style().polish(button)
        if name == "inbox":
            self.refresh_inbox()
        elif name == "review":
            self.refresh_review_queues()
        elif name == "dedupe":
            self.refresh_dedupe_review()
        elif name == "worker":
            self.refresh_worker_status()

    def choose_vault(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose Sound Vault", str(self.vault_root))
        if not selected:
            return
        self.vault_root = Path(selected)
        self.settings.set_vault_root(self.vault_root)
        self.vault_label.setText(str(self.vault_root))
        self.vm = LibraryViewModel(vault_root=self.vault_root, index_path=default_index_path())
        self.rebuild_index()

    def rebuild_index(self) -> None:
        if self._index_future is not None and not self._index_future.done():
            return
        self.worker_label.setText("Worker\nindexing…")
        self._index_future = self.vm.rebuild_index_async()
        self._index_timer.start()

    def _finish_async_index(self) -> None:
        if self._index_future is None or not self._index_future.done():
            return
        self._index_timer.stop()
        try:
            count = self._index_future.result()
            self.stats_label.setText(self.vm.stats_text())
            self.catalog_label.setText(self.vm.catalog_stats_text())
            self.worker_label.setText(f"Worker\nidle • indexed {count:,}")
            self.refresh_table()
            self.refresh_inbox()
            self.refresh_review_queues()
            self.refresh_worker_status()
        except Exception as exc:
            self.worker_label.setText(f"Worker\nindex error: {exc}")
            self.stats_label.setText("index failed")
        finally:
            self._index_future = None

    def schedule_refresh_table(self) -> None:
        self._search_timer.start()

    def refresh_table(self) -> None:
        selected_music_id = self._selected_music_id()
        scroll_value = self.table.verticalScrollBar().value()
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()
        duration_filter = self.duration_filter.currentData() if hasattr(self, "duration_filter") else "all"
        media_filter = self.media_filter.currentData() if hasattr(self, "media_filter") else "all"
        status_filter = self.status_filter.currentData() if hasattr(self, "status_filter") else "all"
        usage_filter = self.usage_filter.currentData() if hasattr(self, "usage_filter") else "all"
        self.current_rows = self.vm.search(
            self.search_box.text(),
            duration_filter=str(duration_filter or "all"),
            media_filter=str(media_filter or "all"),
            status_filter=str(status_filter or "all"),
            usage_filter=str(usage_filter or "all"),
        )
        self.records_by_id = {record.music_id: record for record in self.current_rows}
        total = self.vm.db.stats().total_sounds
        self.result_count_label.setText(f"{len(self.current_rows):,} displayed / {total:,} indexed")
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.current_rows))
        for row_idx, record in enumerate(self.current_rows):
            play_cell = QPushButton("▶")
            play_cell.setToolTip(f"Play {record.title or record.music_id}")
            play_cell.setEnabled(self.vm.play_target_for(record) is not None)
            play_cell.clicked.connect(lambda _checked=False, music_id=record.music_id: self.play_record_by_id(music_id))
            self.table.setCellWidget(row_idx, 0, play_cell)
            has_audio = "m4a" if self.vm.play_target_for(record) is not None else "—"
            flags = ", ".join(record.tags[:3]) or record.status
            values = [
                "",
                record.title or record.music_id,
                record.artist,
                record.status,
                record.added_at,
                record.packaged_at,
                self._format_usage_count(record.usage_count),
                str(record.associated_video_count),
                has_audio,
                flags,
            ]
            for col_idx, value in enumerate(values):
                item = SortableTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, record.music_id)
                if col_idx == 6:
                    item.setData(SORT_ROLE, record.usage_count or -1)
                if col_idx in (0, 6, 7, 8):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_idx, col_idx, item)
        self.table.setSortingEnabled(True)
        if sort_column >= 0:
            self.table.sortItems(sort_column, sort_order)
        if self.current_rows:
            self._restore_library_selection(selected_music_id, scroll_value)
        else:
            self.clear_preview("No matching sounds")

    def _selected_music_id(self) -> str | None:
        selected = self.table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        id_item = self.table.item(row, 1) or selected[0]
        value = id_item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def _restore_library_selection(self, selected_music_id: str | None, scroll_value: int) -> None:
        target_row = 0
        if selected_music_id:
            for row_idx in range(self.table.rowCount()):
                item = self.table.item(row_idx, 1)
                if item and item.data(Qt.ItemDataRole.UserRole) == selected_music_id:
                    target_row = row_idx
                    break
        self.table.selectRow(target_row)
        self.table.verticalScrollBar().setValue(scroll_value)
        self.update_preview_from_selection()

    def clear_preview(self, title: str = "Select a sound") -> None:
        self.current_preview_record = None
        self.preview_title.setText(title)
        self.preview_meta.setText("Audio, tags, evidence and local paths will show here.")
        self.preview_tags.setText("")
        self.preview_json.clear()
        self.artwork_label.setPixmap(QPixmap())
        self.artwork_label.setText("no artwork")
        self.evidence_list.setText("No local screenshots yet.")
        self.video_table.setRowCount(0)
        self.open_folder.setEnabled(False)
        self.copy_metadata.setEnabled(False)
        self.play_button.setEnabled(False)
        self.progress_slider.setRange(0, 0)
        self.time_label.setText("0:00 / 0:00")
        self.playback_status.setText("Playback idle")

    def update_preview_from_selection(self) -> None:
        selected = self.table.selectedItems()
        if not selected:
            self.clear_preview()
            return
        row = selected[0].row()
        id_item = self.table.item(row, 1) or selected[0]
        music_id = id_item.data(Qt.ItemDataRole.UserRole)
        row_record = self.records_by_id.get(str(music_id))
        if row_record is None:
            self.clear_preview()
            return
        try:
            record = self.vm.preview_for(row_record.music_id)
        except KeyError:
            record = row_record
        self.current_preview_record = record
        self.preview_title.setText(record.title or record.music_id)
        self.preview_meta.setText(self._formatted_metadata(record))
        self.preview_tags.setText(" ".join(f"#{tag}" for tag in record.tags) or "no tags yet")
        self.preview_json.setPlainText(json.dumps(record.raw or {"music_id": record.music_id, "status": record.status}, indent=2, ensure_ascii=False))
        self._populate_artwork(record)
        self._populate_evidence(record)
        self._populate_videos(record)
        self.open_folder.setEnabled(record.folder_path is not None and record.folder_path.exists())
        self.copy_metadata.setEnabled(True)
        target = self.vm.play_target_for(record)
        self.play_button.setEnabled(target is not None)
        duration_ms = self._duration_ms_for_record(record)
        self.progress_slider.setRange(0, duration_ms)
        self.progress_slider.setValue(0)
        self.time_label.setText(f"0:00 / {self._format_ms(duration_ms)}")
        self.playback_status.setText("Ready to play" if target is not None else "No playable audio source")

    def _formatted_metadata(self, record) -> str:
        parts = [
            f"artist/source: {record.artist or 'pending'}",
            f"music id: {record.music_id}",
            f"status: {record.status}",
            f"added: {record.added_at or 'unknown'}",
            f"packaged: {record.packaged_at or 'not packaged'}",
            f"videos: {record.associated_video_count}",
            f"local audio: {record.local_audio_path.name if record.local_audio_path else 'missing'}",
            f"artwork: {record.artwork_path.name if record.artwork_path else 'missing true sound art'}",
            f"usage count: {record.usage_count:,}" if record.usage_count is not None else "usage count: unknown",
            f"source provider: {record.source_provider or 'unknown'}",
            f"source confidence: {record.source_confidence or 'unknown'}",
            f"vault version: {record.vault_version or 'unknown'}",
            f"canonical url: {record.canonical_url or 'missing'}",
            f"source music: {record.source_music_url or 'missing'}",
            f"duration: {self._format_seconds(record.duration_seconds) if record.duration_seconds is not None else 'unknown'}",
            f"transcript: {record.transcript_text[:180] if record.transcript_text else 'missing'}",
            f"music page: {record.music_page_title or 'unknown'}",
            f"captured: {record.video_manifest_captured_at or 'unknown'}",
        ]
        return "\n".join(parts)

    def _populate_artwork(self, record) -> None:
        image = record.artwork_path or (record.evidence_images[0] if record.evidence_images else None)
        if image is None:
            self.artwork_label.setPixmap(QPixmap())
            self.artwork_label.setText("no artwork")
            return
        pixmap = QPixmap(str(image))
        if pixmap.isNull():
            self.artwork_label.setText(image.name)
            return
        self.artwork_label.setText("")
        self.artwork_label.setPixmap(pixmap.scaled(390, 170, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _populate_evidence(self, record) -> None:
        lines = [f"{idx}. {path.name}" for idx, path in enumerate(record.evidence_images[:8], start=1)]
        if record.local_audio_path:
            lines.insert(0, f"audio: {record.local_audio_path.name}")
        self.evidence_list.setText("\n".join(lines) if lines else "No local screenshots yet.")

    def _populate_videos(self, record) -> None:
        self.video_table.setSortingEnabled(False)
        self.video_table.setRowCount(len(record.associated_videos))
        for row_idx, video in enumerate(record.associated_videos):
            notes = video.description
            if video.page_title:
                notes = f"{video.page_title}\n{notes}" if notes else video.page_title
            if video.captured_at:
                notes = f"{notes}\ncaptured: {video.captured_at}" if notes else f"captured: {video.captured_at}"
            if video.download_bytes is not None:
                mb = video.download_bytes / (1024 * 1024)
                notes = f"{notes}\nlocal video: {mb:.1f} MB" if notes else f"local video: {mb:.1f} MB"
            values = [
                str(video.rank),
                video.author_handle,
                video.video_path.name if video.video_path else video.video_url,
                notes,
            ]
            for col_idx, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col_idx == 2 and video.screenshot_path is not None:
                    item.setIcon(QIcon(str(video.screenshot_path)))
                self.video_table.setItem(row_idx, col_idx, item)
        self.video_table.setSortingEnabled(True)

    def open_selected_folder(self) -> None:
        if self.current_preview_record is None:
            return
        folder = self.current_preview_record.folder_path
        if folder is not None and folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def copy_selected_metadata(self) -> None:
        if self.current_preview_record is None:
            self.statusBar().showMessage("No selected sound to copy", 2500)
            return
        QApplication.clipboard().setText(self.vm.copyable_metadata(self.current_preview_record))
        self.statusBar().showMessage(f"Copied metadata for {self.current_preview_record.music_id}", 3000)

    def copy_selected_audio_path(self) -> None:
        if self.current_preview_record is None or self.current_preview_record.local_audio_path is None:
            self.statusBar().showMessage("No local audio path to copy", 2500)
            return
        QApplication.clipboard().setText(str(self.current_preview_record.local_audio_path))
        self.statusBar().showMessage("Copied local audio path", 2500)

    def copy_selected_canonical_url(self) -> None:
        if self.current_preview_record is None or not self.current_preview_record.canonical_url:
            self.statusBar().showMessage("No canonical URL to copy", 2500)
            return
        QApplication.clipboard().setText(self.current_preview_record.canonical_url)
        self.statusBar().showMessage("Copied canonical URL", 2500)

    def open_library_context_menu(self, point) -> None:
        item = self.table.itemAt(point)
        if item is not None:
            self.table.selectRow(item.row())
            self.update_preview_from_selection()
        menu = QMenu(self)
        play_action = QAction("Play / pause", self)
        play_action.triggered.connect(self.play_selected_sound)
        metadata_action = QAction("Copy metadata", self)
        metadata_action.triggered.connect(self.copy_selected_metadata)
        audio_path_action = QAction("Copy local audio path", self)
        audio_path_action.triggered.connect(self.copy_selected_audio_path)
        canonical_action = QAction("Copy canonical URL", self)
        canonical_action.triggered.connect(self.copy_selected_canonical_url)
        folder_action = QAction("Open sound folder", self)
        folder_action.triggered.connect(self.open_selected_folder)
        for action in (play_action, metadata_action, audio_path_action, canonical_action, folder_action):
            menu.addAction(action)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def play_record_by_id(self, music_id: str) -> None:
        record = self.records_by_id.get(str(music_id))
        if record is None:
            return
        for row_idx in range(self.table.rowCount()):
            item = self.table.item(row_idx, 1)
            if item and item.data(Qt.ItemDataRole.UserRole) == music_id:
                self.table.selectRow(row_idx)
                break
        self.current_preview_record = self.vm.preview_for(record.music_id)
        self.play_selected_sound()

    def play_selected_sound(self) -> None:
        if self.current_preview_record is None:
            return
        target = self.vm.play_target_for(self.current_preview_record)
        if target is None:
            self.playback_status.setText("No playable audio source")
            QMessageBox.information(self, "No playable audio", "This record has no local audio or preview URL yet.")
            return
        source = self._playback_source_for(target)
        if self.audio_player.source() == source and self.audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.audio_player.pause()
            self.playback_status.setText("Playback paused")
            return
        if self.audio_player.source() != source:
            self.audio_player.setSource(source)
        self.audio_player.play()
        self.playback_status.setText(f"Playing {self._playback_label_for(target)}")

    @staticmethod
    def _playback_source_for(target: Path | str) -> QUrl:
        return QUrl.fromLocalFile(str(target)) if isinstance(target, Path) else QUrl(target)

    @staticmethod
    def _playback_label_for(target: Path | str) -> str:
        return target.name if isinstance(target, Path) else "remote preview"

    @staticmethod
    def _duration_ms_for_record(record) -> int:
        if record.duration_seconds is None:
            return 0
        return max(0, int(record.duration_seconds * 1000))

    @staticmethod
    def _format_seconds(value: float) -> str:
        seconds = max(0, int(round(value)))
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes}:{seconds:02d}"

    def _player_position_changed(self, position: int) -> None:
        if not self.progress_slider.isSliderDown():
            self.progress_slider.setValue(position)
        self.time_label.setText(f"{self._format_ms(position)} / {self._format_ms(self.audio_player.duration())}")

    def _player_duration_changed(self, duration: int) -> None:
        self.progress_slider.setRange(0, max(0, duration))
        self.time_label.setText(f"{self._format_ms(self.audio_player.position())} / {self._format_ms(duration)}")

    def _player_state_changed(self, state) -> None:
        self.play_button.setText("Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play")

    def _player_error_occurred(self, _error, error_string: str = "") -> None:
        detail = error_string or self.audio_player.errorString() or "unknown media error"
        self.playback_status.setText(f"Playback error: {detail}")
        self.play_button.setText("Play")

    @staticmethod
    def _format_usage_count(value: int | None) -> str:
        if value is None:
            return "unknown"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M videos"
        if value >= 1_000:
            return f"{value / 1_000:.1f}K videos"
        return f"{value:,} videos"

    @staticmethod
    def _format_ms(value: int) -> str:
        seconds = max(0, int(value // 1000))
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes}:{seconds:02d}"

    def update_pairing_status(self) -> None:
        self.pairing_label.setText("Shortcut relay\n" + self.settings.relay_status_text())

    def open_settings_dialog(self) -> None:
        dialog = SettingsDialog(settings=self.settings, parent=self)
        dialog.exec()
        self.update_pairing_status()

    def refresh_inbox(self) -> None:
        self.current_inbox_rows = self.vm.pending_inbox()
        self.inbox_rows_by_id = {item.id: item for item in self.current_inbox_rows}
        self.inbox_label.setText(self.vm.inbox_text())
        self.inbox_table.setSortingEnabled(False)
        self.inbox_table.setRowCount(len(self.current_inbox_rows))
        for row_idx, item in enumerate(self.current_inbox_rows):
            received = getattr(item, "created_at", "") or getattr(item, "received_at", "") or ""
            for col_idx, value in enumerate([received, item.source, item.url, item.status]):
                table_item = QTableWidgetItem(str(value))
                table_item.setData(Qt.ItemDataRole.UserRole, item.id)
                self.inbox_table.setItem(row_idx, col_idx, table_item)
        self.inbox_table.setSortingEnabled(True)
        self.inbox_table.sortItems(0, Qt.SortOrder.DescendingOrder)

    def poll_relay_inbox(self) -> None:
        base_url = self.settings.relay_base_url()
        pair_code = self.settings.relay_pair_code()
        device_id = self.settings.relay_device_id()
        device_secret = self.settings.relay_device_secret()
        if not all((base_url, pair_code, device_id, device_secret)):
            QMessageBox.information(self, "Relay not configured", "Open Settings and create a relay pairing first.")
            return
        try:
            items = self.vm.poll_relay_inbox(
                base_url=base_url,
                pair_code=pair_code,
                device_id=device_id,
                device_secret=device_secret,
            )
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Relay poll failed", str(exc))
            return
        self.refresh_inbox()
        QMessageBox.information(self, "Relay poll complete", f"Imported {len(items)} relay link(s).")

    def refresh_review_queues(self) -> None:
        rows = self.vm.review_queue_rows()
        self.review_table.setSortingEnabled(False)
        self.review_table.setRowCount(len(rows))
        for row_idx, (queue, count, action, status_filter, media_filter) in enumerate(rows):
            for col_idx, value in enumerate([queue, f"{count:,}", action]):
                table_item = QTableWidgetItem(str(value))
                table_item.setData(Qt.ItemDataRole.UserRole, {"status_filter": status_filter, "media_filter": media_filter})
                if col_idx == 1:
                    table_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.review_table.setItem(row_idx, col_idx, table_item)
        self.review_table.setSortingEnabled(True)

    def apply_review_queue_filter(self, item: QTableWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return
        status_index = self.status_filter.findData(str(payload.get("status_filter") or "all"))
        if status_index >= 0:
            self.status_filter.setCurrentIndex(status_index)
        media_index = self.media_filter.findData(str(payload.get("media_filter") or "all"))
        if media_index >= 0:
            self.media_filter.setCurrentIndex(media_index)
        self.show_view("library")
        self.refresh_table()
        self.statusBar().showMessage("Applied review queue filter", 3000)

    def refresh_dedupe_review(self) -> None:
        self.current_dedupe_groups = self.vm.duplicate_review_groups()
        self.dedupe_groups_by_id = {group.group_id: group for group in self.current_dedupe_groups}
        self.dedupe_groups_table.setSortingEnabled(False)
        self.dedupe_groups_table.setRowCount(len(self.current_dedupe_groups))
        for row_idx, group in enumerate(self.current_dedupe_groups):
            values = [group.group_id, f"{group.score:.2f}" if group.score else "—", str(len(group.candidates)), group.reason]
            for col_idx, value in enumerate(values):
                table_item = QTableWidgetItem(str(value))
                table_item.setData(Qt.ItemDataRole.UserRole, group.group_id)
                if col_idx in (1, 2):
                    table_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.dedupe_groups_table.setItem(row_idx, col_idx, table_item)
        self.dedupe_groups_table.setSortingEnabled(True)
        if self.current_dedupe_groups and not self.dedupe_groups_table.selectedItems():
            self.dedupe_groups_table.selectRow(0)
        self.refresh_dedupe_candidates()

    def _selected_duplicate_group_id(self) -> str | None:
        selected = self.dedupe_groups_table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        group_item = self.dedupe_groups_table.item(row, 0) or selected[0]
        value = group_item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def refresh_dedupe_candidates(self) -> None:
        group_id = self._selected_duplicate_group_id()
        group = self.dedupe_groups_by_id.get(str(group_id)) if group_id else None
        candidates = group.candidates if group is not None else ()
        self.dedupe_candidates_table.setSortingEnabled(False)
        self.dedupe_candidates_table.setRowCount(len(candidates))
        for row_idx, candidate in enumerate(candidates):
            music_id = str(candidate.get("music_id") or "")
            play_cell = QPushButton("▶")
            play_cell.clicked.connect(lambda _checked=False, row=row_idx: self.play_dedupe_candidate(row))
            self.dedupe_candidates_table.setCellWidget(row_idx, 0, play_cell)
            audio_or_folder = candidate.get("local_audio_path") or candidate.get("folder") or ""
            values = ["", music_id, candidate.get("title") or "", candidate.get("artist") or "", audio_or_folder]
            for col_idx, value in enumerate(values):
                table_item = QTableWidgetItem(str(value))
                table_item.setData(Qt.ItemDataRole.UserRole, {"group_id": group_id, "music_id": music_id, "candidate": candidate})
                self.dedupe_candidates_table.setItem(row_idx, col_idx, table_item)
        self.dedupe_candidates_table.setSortingEnabled(True)

    def play_dedupe_candidate(self, row: int | None = None) -> None:
        if row is None:
            selected = self.dedupe_candidates_table.selectedItems()
            if not selected:
                return
            row = selected[0].row()
        item = self.dedupe_candidates_table.item(row, 1)
        payload = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(payload, dict):
            return
        candidate = payload.get("candidate") or {}
        audio = candidate.get("local_audio_path")
        if audio and Path(str(audio)).exists():
            self.audio_player.setSource(QUrl.fromLocalFile(str(audio)))
            self.audio_player.play()
            self.playback_status.setText(f"Playing duplicate candidate {candidate.get('music_id')}")

    def record_selected_duplicate_decision(self, decision: str) -> None:
        group_id = self._selected_duplicate_group_id()
        group = self.dedupe_groups_by_id.get(str(group_id)) if group_id else None
        if group is None:
            self.statusBar().showMessage("No duplicate group selected", 2500)
            return
        candidate_ids = [str(candidate.get("music_id") or "") for candidate in group.candidates if candidate.get("music_id")]
        selected = self.dedupe_candidates_table.selectedItems()
        keep_music_id = ""
        if selected:
            item = self.dedupe_candidates_table.item(selected[0].row(), 1) or selected[0]
            payload = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(payload, dict):
                keep_music_id = str(payload.get("music_id") or "")
        duplicate_music_ids = [music_id for music_id in candidate_ids if music_id != keep_music_id]
        self.vm.record_duplicate_decision(
            group_id=group.group_id,
            decision=decision,
            keep_music_id=keep_music_id,
            duplicate_music_ids=duplicate_music_ids,
        )
        self.statusBar().showMessage(f"Recorded duplicate review: {decision}", 3000)

    def refresh_worker_status(self) -> None:
        rows = self.vm.archive_health_rows()
        self.worker_status_table.setSortingEnabled(False)
        self.worker_status_table.setRowCount(len(rows))
        for row_idx, (metric, value) in enumerate(rows):
            self.worker_status_table.setItem(row_idx, 0, QTableWidgetItem(metric))
            self.worker_status_table.setItem(row_idx, 1, QTableWidgetItem(value))
        self.worker_status_table.setSortingEnabled(True)

    def mark_selected_inbox_imported(self) -> None:
        selected = self.inbox_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        id_item = self.inbox_table.item(row, 2) or selected[0]
        item_id = id_item.data(Qt.ItemDataRole.UserRole)
        inbox_item = self.inbox_rows_by_id.get(str(item_id))
        if inbox_item is None:
            return
        self.vm.mark_inbox_imported(inbox_item.id)
        self.refresh_inbox()


STYLESHEET = """
QMainWindow, QWidget {
    background: #d5dadd;
    color: #18242d;
    font-family: Verdana, "Lucida Grande", "Segoe UI", Arial;
    font-size: 12px;
}
#sidebar {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #bdc7cf, stop:0.42 #edf2f5, stop:1 #d8e0e5);
    border-right: 1px solid #6f7d86;
}
#brand {
    font-size: 22px;
    font-weight: 900;
    color: #174967;
    padding: 6px 4px 12px 4px;
    border-bottom: 1px solid #9eabb2;
}
#sourceGroup {
    color: #ffffff;
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2b6f9d, stop:1 #1d5f91);
    border-top: 1px solid #ffffff;
    border-bottom: 1px solid #8a949d;
    padding: 5px 8px;
    font-size: 10px;
    font-weight: 900;
    letter-spacing: 1px;
    text-transform: uppercase;
}
#title { font-size: 28px; font-weight: 900; color: #173c55; }
#sectionTitle { font-size: 17px; font-weight: 900; color: #173c55; }
#previewTitle { font-size: 21px; font-weight: 900; color: #173c55; }
#muted { color: #53636c; }
#chromeHeader {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f8fbfd, stop:0.47 #ccd6de, stop:0.5 #aebbc5, stop:1 #8796a0);
    border-top: 1px solid #ffffff;
    border-bottom: 1px solid #8a949d;
    border-left: 1px solid #b9c3ca;
    border-right: 1px solid #74828b;
    border-radius: 12px;
}
#transportDeck {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #eff5f8, stop:1 #96a5af);
    border: 1px solid #687883;
    border-top: 1px solid #ffffff;
    border-radius: 18px;
}
#transportButton {
    min-width: 34px;
    min-height: 28px;
    border-radius: 14px;
    text-align: center;
    padding: 4px;
}
#capsuleDisplay {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #1d2b23, stop:0.55 #0c140f, stop:1 #24362c);
    border: 2px inset #7c8d7b;
    border-radius: 14px;
}
#displayEyebrow {
    color: #7fcf2a;
    font-size: 10px;
    font-weight: 900;
    letter-spacing: 1px;
    text-transform: uppercase;
}
#displayTitle { color: #eef8df; font-size: 13px; font-weight: 900; }
#displaySubtitle { color: #a6d978; font-family: Menlo, Monaco, Consolas; font-size: 10px; }
#libraryTabs {
    background: #d9e5ec;
    border: 1px solid #678193;
    border-radius: 9px;
}
#portalTab {
    color: #ffffff;
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #357aaa, stop:1 #1d5f91);
    border-top: 1px solid #cbe4f5;
    border-left: 1px solid #85a9c0;
    border-right: 1px solid #154b73;
    border-bottom: 1px solid #0e3552;
    border-radius: 6px;
    padding: 5px 9px;
    font-size: 10px;
    font-weight: 900;
}
#limeStatusPanel {
    background: #f8fbf2;
    border: 1px solid #9ba7a0;
    border-radius: 8px;
}
#limeStatusDot { color: #7fcf2a; font-size: 18px; }
#statusReadout { color: #1d5f21; font-family: Menlo, Monaco, Consolas; font-size: 10px; font-weight: 900; }
#preview {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f6fafc, stop:1 #c4d1d9);
    border-left: 1px solid #7c8b94;
}
#artwork {
    background: #18242d;
    color: #edf5f8;
    border: 2px inset #8796a0;
    border-radius: 8px;
}
QPushButton, QToolButton {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:0.48 #dfe8ee, stop:1 #a9b7c1);
    color: #172936;
    border: 1px solid #657785;
    border-top: 1px solid #ffffff;
    border-radius: 8px;
    padding: 7px 10px;
    text-align: left;
    font-weight: 700;
}
QPushButton:hover, QToolButton:hover { background: #f8fff0; border-color: #7fcf2a; }
#primaryButton {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #baf277, stop:1 #55a915);
    color: #0b2608;
    text-align: center;
}
#navButton[active="true"] { background: #7fcf2a; color: #10220c; }
#pairingCard, #statCard, QGroupBox {
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid #91a0a8;
    border-top: 1px solid #ffffff;
    border-radius: 9px;
    padding: 12px;
    color: #18242d;
}
QGroupBox { margin-top: 9px; font-weight: 900; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #1d5f91; }
QLineEdit, QComboBox {
    background: #ffffff;
    border: 2px inset #9ba7a0;
    border-radius: 7px;
    padding: 8px;
    color: #18242d;
}
QTableWidget {
    background: #ffffff;
    alternate-background-color: #eef5e9;
    border: 2px inset #8796a0;
    gridline-color: #c6d1d6;
    selection-background-color: #1d5f91;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f8fbfd, stop:1 #b7c4cc);
    color: #173c55;
    padding: 7px;
    border: 0;
    border-right: 1px solid #8a949d;
    border-bottom: 1px solid #8a949d;
    font-weight: 900;
}
QTextEdit {
    background: #101b22;
    border: 2px inset #7b8a94;
    border-radius: 8px;
    padding: 9px;
    color: #ecf8df;
    font-family: Menlo, Monaco, Consolas;
}
QSlider::groove:horizontal { height: 8px; background: #8fa0a9; border-radius: 4px; }
QSlider::handle:horizontal { background: #fefefe; border: 1px solid #61717b; width: 16px; margin: -5px 0; border-radius: 8px; }
"""


def run_desktop(vault_root: Path | None = None) -> int:
    app = QApplication.instance() or QApplication([])
    window = SoundVaultWindow(vault_root=vault_root)
    window.show()
    return app.exec()
