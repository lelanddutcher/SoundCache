from pathlib import Path

DESKTOP_SOURCE = Path("src/sound_vault/ui/desktop.py")


def test_desktop_ui_does_not_show_fake_pairing_code():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "RIVER-7421" not in source
    assert "Relay not configured" in source


def test_desktop_table_has_operator_selection_and_sorting_affordances():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "setSortingEnabled(True)" in source
    assert "SelectionBehavior.SelectRows" in source
    assert "SelectionMode.SingleSelection" in source
    assert "SelectionMode.ExtendedSelection" in source


def test_desktop_open_folder_button_is_wired():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "self.open_folder" in source
    assert "open_folder.clicked.connect" in source
    assert "QDesktopServices.openUrl" in source


def test_desktop_inspector_opens_tiktok_sound_url():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "self.open_tiktok_sound = QPushButton(\"Open TikTok sound\")" in source
    assert "open_tiktok_sound.clicked.connect(self.open_selected_tiktok_sound)" in source
    assert "def open_selected_tiktok_sound" in source
    assert "def _sound_url_for_record" in source
    assert "record.canonical_url" in source
    assert "raw.get(\"mobile_music_url\")" in source
    assert "QDesktopServices.openUrl(QUrl(url))" in source
    assert "Open TikTok sound page" in source


def test_desktop_sorted_tables_store_row_identity_in_item_data():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "Qt.ItemDataRole.UserRole" in source
    assert "item.setData(Qt.ItemDataRole.UserRole, record.music_id)" in source
    assert "item.setData(Qt.ItemDataRole.UserRole, item.id)" in source
    assert "self.records_by_id" in source
    assert "self.inbox_rows_by_id" in source


def test_desktop_uses_integrated_qt_multimedia_not_os_open_for_audio():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "QMediaPlayer" in source
    assert "QAudioOutput" in source
    assert "from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer" in source
    assert "self.audio_player = None" in source
    assert "def _ensure_audio_player" in source
    assert "self.audio_player.setSource" in source
    assert "self.audio_player.play()" in source
    assert "self.progress_slider" in source
    assert "QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))" not in source


def test_desktop_uses_afplay_for_local_audio_on_macos_when_qt_multimedia_is_silent():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "def _should_use_external_audio" in source
    assert 'platform.system() == "Darwin"' in source
    assert 'shutil.which("afplay")' in source
    assert 'SOUND_VAULT_DISABLE_AFPLAY' in source
    assert "subprocess.Popen" in source
    assert "gui.external_audio_started" in source
    assert "self._stop_external_audio()" in source
    assert "self.external_audio_target == target" in source


def test_desktop_playing_different_selection_switches_source_instead_of_pausing_old_source():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "source = self._playback_source_for" in source
    assert "if self.audio_player.source() == source" in source
    assert "playbackState() == QMediaPlayer.PlaybackState.PlayingState" in source
    assert "self.audio_player.pause()" in source
    assert "self.audio_player.setSource(source)" in source
    assert "self.audio_player.play()" in source


def test_desktop_surfaces_media_player_errors_in_preview_panel():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "self.playback_status" in source
    assert "self.audio_player.errorOccurred.connect(self._player_error_occurred)" in source
    assert "gui.audio_player_exception" in source
    assert "def _player_error_occurred" in source
    assert "Playback error" in source


def test_desktop_has_safe_mode_switches_for_startup_triage():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "SOUND_VAULT_SAFE_MODE" in source
    assert "SOUND_VAULT_DISABLE_AUTO_INDEX" in source
    assert "SOUND_VAULT_DISABLE_AUDIO" in source
    assert "gui.auto_index_disabled" in source
    assert "gui.audio_player_disabled" in source
    assert "def _env_flag" in source


def test_desktop_has_real_views_and_rich_archive_detail_panel():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "QStackedWidget" in source
    assert "show_view" in source
    assert "self.inbox_view" in source
    assert "self.library_view" in source
    assert "self.evidence_list" in source
    assert "self.video_table" in source
    assert "Raw metadata" in source
    assert "QCollapsible" not in source  # no imaginary Qt widget, please


def test_desktop_inspector_shows_full_transcript_without_metadata_truncation():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert 'transcript_group = QGroupBox("Transcript")' in source
    assert "self.transcript_text = QTextEdit()" in source
    assert "self.transcript_text.setReadOnly(True)" in source
    assert "self.transcript_text.setPlainText(self._full_transcript_text(record))" in source
    assert "def _full_transcript_text" in source
    assert "def _format_transcript_status" in source
    assert "record.transcript_text[:180]" not in source


def test_desktop_table_columns_include_dates_popularity_and_local_audio():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert '"added"' in source
    assert '"packaged"' in source
    assert '"popularity"' in source
    assert '"local audio"' in source
    assert "self.table = SoundTableWidget(0, 11)" in source
    assert '"★"' in source
    assert "self._format_usage_count(record.usage_count)" in source
    assert "item = self._readonly_item(value)" in source
    assert "item.setData(Qt.ItemDataRole.DisplayRole, int(record.usage_count or 0))" in source
    assert "self.table.horizontalHeader().sectionClicked.connect(self.handle_library_header_clicked)" in source
    assert "self.library_sort_column = POPULARITY_COL" in source
    assert "def _sort_library_rows" in source
    assert "record.usage_count if record.usage_count is not None else -1" in source
    assert "self.table.setSortingEnabled(False)" in source
    assert "class SortableTableWidgetItem" not in source
    assert "def __lt__" not in source
    assert "SORT_ROLE" not in source
    assert "setSectionsMovable(True)" in source
    assert "ResizeMode.Interactive" in source
    assert "ResizeMode.Stretch" not in source
    assert "resizeSection" in source


def test_desktop_has_favorites_bins_drag_drop_and_readable_menus():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "FavoriteButtonDelegate" in source
    assert "LibraryDropButton" in source
    assert "application/x-sound-vault-music-id" in source
    assert "create_sorting_bin" in source
    assert "Add to…" in source
    assert "New sorting bin…" in source
    assert "smart:no_transcript" in source
    assert "QComboBox QAbstractItemView" in source
    assert "QMenu::item:selected" in source
    assert "selection-color: #ffffff" in source
    assert "mark_selected_library_as_duplicate" in source
    assert "Mark as Duplicate" in source
    assert "setCurrentCell(item.row(), item.column())" in source
    assert "if item.row() not in selected_rows" in source
    assert "create_manual_duplicate_group" in Path("src/sound_vault/ui/view_model.py").read_text(encoding="utf-8")


def test_desktop_debounces_search_and_preview_metadata_is_selectable():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "self._search_timer.setSingleShot(True)" in source
    assert "textChanged.connect(self.schedule_refresh_table)" in source
    assert "self._search_timer.start()" in source
    assert "search_had_focus = self.search_box.hasFocus()" in source
    assert "self.search_box.setFocus(Qt.FocusReason.OtherFocusReason)" in source
    assert "self.search_box.setCursorPosition" in source
    assert "TextInteractionFlag.TextSelectableByMouse" in source
    assert "setTextInteractionFlags" in source


def test_desktop_normalizes_vault_picker_and_resets_filters_on_new_vault():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "resolve_vault_root(Path(selected))" in source
    assert "self.reset_library_filters()" in source
    assert "def reset_library_filters" in source


def test_desktop_selection_updates_duration_from_metadata_before_playback_loads():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "self._duration_ms_for_record(record)" in source
    assert "self.progress_slider.setRange(0, duration_ms)" in source
    assert "self._format_ms(duration_ms)" in source


def test_desktop_surfaces_archive_context_and_video_thumbnails():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "usage count:" in source
    assert "source confidence:" in source
    assert "vault version:" in source
    assert "canonical url:" in source
    assert "source music:" in source
    assert "music page:" in source
    assert "captured:" in source
    assert "QIcon" in source
    assert "screenshot_path" in source
    assert "setIcon" in source
    assert "download_bytes" in source
    assert "page_title" in source
    assert "hashtags:" in source
    assert "tag_line = \" \".join" in source


def test_desktop_persists_library_and_inbox_table_layouts():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "restore_table_layout" in source
    assert "save_table_layout" in source
    assert "saveState()" in source
    assert "restoreState(" in source
    assert "closeEvent" in source


def test_sidebar_nav_has_no_placeholder_only_routes():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "No review workflow is wired yet" not in source
    assert "Collections are planned" not in source
    assert "Worker activity appears in the status cards for now" not in source
    assert "_placeholder_view" not in source
    assert '("Collections", "collections")' not in source


def test_visible_nav_labels_map_to_real_views():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert 'self.library_toggle = QPushButton("▾ Library")' in source
    assert '("Ingest inbox", "inbox")' in source
    assert '("Review queues", "review")' in source
    assert '("Worker status", "worker")' in source
    assert "self.review_view = self._build_review_view()" in source
    assert "self.worker_view = self._build_worker_view()" in source
    assert "refresh_review_queues" in source
    assert "refresh_worker_status" in source


def test_desktop_surfaces_catalog_row_vs_unique_index_counts():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")
    view_model_source = Path("src/sound_vault/ui/view_model.py").read_text(encoding="utf-8")
    combined_source = source + view_model_source

    assert "self.catalog_label" in source
    assert "catalog_stats_text" in source
    assert "catalog_rows" in combined_source
    assert "unique_catalog_ids" in combined_source
    assert "duplicate_catalog_rows" in combined_source
    assert "packaged_sound_folders" in combined_source
    assert "Catalog" in combined_source


def test_desktop_has_user_reachable_relay_poll_path():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert 'poll_relay = QPushButton("Poll relay")' in source
    assert "poll_relay.clicked.connect(self.poll_relay_inbox)" in source
    assert "def poll_relay_inbox" in source
    assert "self.vm.poll_relay_inbox" in source
    assert "self.refresh_inbox()" in source


def test_desktop_has_import_workers_dashboard_actions():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")
    view_model_source = Path("src/sound_vault/ui/view_model.py").read_text(encoding="utf-8")

    assert 'import_export = QPushButton("Import TikTok export")' in source
    assert "import_export.clicked.connect(self.import_tiktok_favorite_sounds_export)" in source
    assert "def import_tiktok_favorite_sounds_export" in source
    assert 'enrich_oembed = QPushButton("Run oEmbed enrichment")' in source
    assert "enrich_oembed.clicked.connect(self.run_oembed_enrichment)" in source
    assert 'package_import = QPushButton("Package imported metadata")' in source
    assert "package_import.clicked.connect(self.package_imported_metadata)" in source
    assert "self.vm.enrich_favorite_sounds_oembed_async(Path(selected))" in source
    assert "self.vm.package_imported_sounds_async(Path(selected))" in source
    assert "latest_import_artifact_rows" in view_model_source
    assert "package_summary_rows" in view_model_source


def test_desktop_library_has_duration_filters_visible_counts_and_real_row_play_controls():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "self.duration_filter" in source
    assert "Under 30s" in source
    assert "30s and up" in source
    assert "self.media_filter" in source
    assert "Has audio" in source
    assert "Has artwork" in source
    assert "Has transcript" in source
    assert "Has associated videos" in source
    assert "media_filter=str(media_filter or \"all\")" in source
    assert "self.result_count_label" in source
    assert "displayed /" in source
    assert "class PlayButtonDelegate" in source
    assert "self.table.itemDoubleClicked.connect(lambda _item: self.play_selected_sound())" in source
    assert "self.table.setItemDelegateForColumn(PLAY_COL, PlayButtonDelegate(self.play_record_by_id, self.table))" in source
    assert "item.setData(PLAYABLE_ROLE, has_playable_pointer)" in source
    assert "def _record_has_playable_pointer" in source
    assert "NoEditTriggers" in source
    assert "load_sidecars=False" in source
    assert 'sidecar_mode="summary"' in source


def test_desktop_has_status_evidence_filters_review_drilldown_and_copy_metadata():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")
    settings_source = Path("src/sound_vault/settings.py").read_text(encoding="utf-8")

    assert "self.status_filter" in source
    assert "All statuses" in source
    assert "Approved" in source
    assert "Needs review" in source
    assert "Unreviewed" in source
    assert "status_filter=str(status_filter or \"all\")" in source
    assert "Has evidence" in source
    assert "Missing evidence" in source
    assert "has_evidence" in source
    assert "missing_evidence" in source
    assert "review_table.itemDoubleClicked.connect" in source
    assert "apply_review_queue_filter" in source
    assert "self.status_filter.setCurrentIndex" in source
    assert "self.media_filter.setCurrentIndex" in source
    assert "self.show_view(\"library\")" in source
    assert "self.copy_metadata" in source
    assert "Copy metadata" in source
    assert "copy_metadata.clicked.connect(self.copy_selected_metadata)" in source
    assert "QApplication.clipboard().setText" in source
    assert "self.vm.copyable_metadata" in source
    assert "statusBar().showMessage" in source
    assert "restore_library_search_state" in source
    assert "save_library_search_state" in source
    assert "library_search_state" in settings_source
    assert "set_library_search_state" in settings_source


def test_desktop_preserves_selection_scroll_and_sort_when_refreshing_library():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "selected_music_id = self._selected_music_id()" in source
    assert "scroll_value = self.table.verticalScrollBar().value()" in source
    assert "sort_column = self.library_sort_column" in source
    assert "sort_order = self.library_sort_order" in source
    assert "self._restore_library_selection(selected_music_id, scroll_value)" in source
    assert "def _selected_music_id" in source
    assert "def _restore_library_selection" in source
    assert "self.table.horizontalHeader().setSortIndicator(self.library_sort_column, self.library_sort_order)" in source
    assert "if selected_music_id" in source


def test_desktop_has_keyboard_shortcuts_for_editor_workflow():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "QShortcut" in source
    assert "QKeySequence" in source
    assert "setup_keyboard_shortcuts" in source
    assert "QShortcut(QKeySequence.StandardKey.Find" in source
    assert "QShortcut(QKeySequence.StandardKey.Copy" in source
    assert "QShortcut(QKeySequence(Qt.Key.Key_Space)" in source
    assert "focus_search" in source
    assert "clear_search" in source
    assert "copy_selected_metadata" in source
    assert "play_selected_sound" in source


def test_desktop_has_popularity_filters_column_visibility_and_quick_actions():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")
    settings_source = Path("src/sound_vault/settings.py").read_text(encoding="utf-8")

    assert "self.usage_filter" in source
    assert "Unknown usage" in source
    assert "Under 1K uses" in source
    assert "100K+ uses" in source
    assert "1M+ uses" in source
    assert "usage_filter=str(usage_filter or \"all\")" in source
    assert "column_menu" in source
    assert "QAction" in source
    assert "toggle_column_visibility" in source
    assert "setSectionHidden" in source
    assert "hidden_table_columns" in settings_source
    assert "set_hidden_table_columns" in settings_source
    assert "setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)" in source
    assert "customContextMenuRequested.connect(self.open_library_context_menu)" in source
    assert "Open sound folder" in source
    assert "Copy local audio path" in source
    assert "Copy canonical URL" in source


def test_desktop_scrubber_clicks_seek_even_while_playing():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "class SeekSlider" in source
    assert "mousePressEvent" in source
    assert "self.setValue(value)" in source
    assert "self.seekRequested.emit(value)" in source
    assert "seekRequested.connect(self.seek_playback)" in source
    assert "def seek_playback" in source
    assert "self.audio_player.setPosition(position)" in source


def test_desktop_retro_chrome_uses_itunes_limewire_early_web_motifs():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "DESIGN_LANGUAGE" in source
    assert "tactile retro-futurist control-room dashboard" in source
    assert "brushed metal chrome" in source
    assert "dark graphite inset panels" in source
    assert "physical knobs/sliders/toggles" in source
    assert "mainDeck" in source
    assert "appShell" in source
    assert "dangerButton" in source
    assert "rowPlayButton" in source
    assert "3px inset" in source
    assert "self._build_jukebox_chrome" in source
    assert "chromeHeader" in source
    assert "transportDeck" in source
    assert "capsuleDisplay" in source
    assert "sourceGroup" in source
    assert "limeStatusDot" in source
    assert "libraryTabs" in source
    assert "#7fcf2a" in source
    assert "#1f6dad" in source
    assert "qlineargradient" in source
    assert "border-top: 1px solid #ffffff" in source
    assert "border-bottom: 1px solid #8a949d" in source
    assert "Verdana" in source
    assert "Source List" in source
    assert "Now Playing" in source

def test_desktop_chrome_controls_are_wired_to_real_behavior():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "transport_actions" in source
    assert "self.select_previous_sound" in source
    assert "self.play_selected_sound" in source
    assert "self.pause_playback" in source
    assert "self.select_next_sound" in source
    assert "self.continuous_play_button" in source
    assert "self.random_play_button" in source
    assert "self.toggle_continuous_play" in source
    assert "self.play_random_sound" in source
    assert "self._playable_library_rows" in source
    assert "self._play_next_continuous_sound" in source
    assert "mediaStatusChanged.connect(self._player_media_status_changed)" in source
    assert "QMediaPlayer.MediaStatus.EndOfMedia" in source
    assert "was_playing = self._is_audio_playing()" in source
    assert "tab.clicked.connect(handler)" in source
    assert "self._set_media_filter(\"has_evidence\")" in source
    assert "self._set_media_filter(\"has_transcript\")" in source
    assert "self._set_usage_filter(\"over_100k\")" in source
    assert "refresh_portal_tabs" in source
    assert "INDEX ONLINE" not in source
    assert "self._set_index_status(\"INDEXING\", state=\"running\")" in source
    assert "self._set_index_status(f\"INDEXED {count:,}\", state=\"online\")" in source
    assert "self._set_index_status(\"INDEX ERROR\", state=\"error\")" in source


def test_desktop_inspector_scrolls_and_restores_saved_selection():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "QScrollArea" in source
    assert "inspectorScroll" in source
    assert "scroll.setWidgetResizable(True)" in source
    assert "scroll_layout.addWidget(video_group)" in source
    assert "self._pending_selected_music_id" in source
    assert "state.get(\"selected_music_id\")" in source
    assert "selected_music_id = self._selected_music_id() or self._pending_selected_music_id" in source
    assert "self.now_playing_title.setText" in source


def test_desktop_inspector_opens_associated_video_assets_and_normalizes_artwork():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "self.artwork_label.setFixedSize(210, 210)" in source
    assert "pixmap.scaled(" in source
    assert "self.open_video = QPushButton(\"Open video\")" in source
    assert "self.open_video_url = QPushButton(\"Open URL\")" in source
    assert "itemDoubleClicked.connect(self.open_associated_video_from_item)" in source
    assert "def open_selected_associated_video" in source
    assert "QDesktopServices.openUrl(QUrl.fromLocalFile(str(video.video_path)))" in source
    assert "QDesktopServices.openUrl(QUrl(video.video_url))" in source
