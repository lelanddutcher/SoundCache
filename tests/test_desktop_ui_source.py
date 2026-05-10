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


def test_desktop_open_folder_button_is_wired():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "self.open_folder" in source
    assert "open_folder.clicked.connect" in source
    assert "QDesktopServices.openUrl" in source


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
    assert "self.audio_player.setSource" in source
    assert "self.audio_player.play()" in source
    assert "self.progress_slider" in source
    assert "QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))" not in source


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
    assert "def _player_error_occurred" in source
    assert "Playback error" in source


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


def test_desktop_table_columns_include_dates_popularity_and_local_audio():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert '"added"' in source
    assert '"packaged"' in source
    assert '"popularity"' in source
    assert '"local audio"' in source
    assert "self.table = QTableWidget(0, 10)" in source
    assert "self._format_usage_count(record.usage_count)" in source
    assert "item.setData(SORT_ROLE, record.usage_count or -1)" in source
    assert "class SortableTableWidgetItem" in source
    assert "setSectionsMovable(True)" in source
    assert "ResizeMode.Interactive" in source
    assert "ResizeMode.Stretch" not in source
    assert "resizeSection" in source


def test_desktop_debounces_search_and_preview_metadata_is_selectable():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "self._search_timer.setSingleShot(True)" in source
    assert "textChanged.connect(self.schedule_refresh_table)" in source
    assert "self._search_timer.start()" in source
    assert "TextInteractionFlag.TextSelectableByMouse" in source
    assert "setTextInteractionFlags" in source


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

    assert '("Library", "library")' in source
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


def test_desktop_library_has_duration_filters_visible_counts_and_real_row_play_buttons():
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
    assert "setCellWidget(row_idx, 0, play_cell)" in source
    assert "play_cell.clicked.connect" in source


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
    assert "sort_column = self.table.horizontalHeader().sortIndicatorSection()" in source
    assert "self._restore_library_selection(selected_music_id, scroll_value)" in source
    assert "def _selected_music_id" in source
    assert "def _restore_library_selection" in source
    assert "self.table.sortItems(sort_column, sort_order)" in source
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
    assert "seekRequested.connect(self.audio_player.setPosition)" in source


def test_desktop_retro_chrome_uses_itunes_limewire_early_web_motifs():
    source = DESKTOP_SOURCE.read_text(encoding="utf-8")

    assert "DESIGN_LANGUAGE" in source
    assert "iTunes 5 smooth-metal header" in source
    assert "LimeWire 2001 portal" in source
    assert "self._build_jukebox_chrome" in source
    assert "chromeHeader" in source
    assert "transportDeck" in source
    assert "capsuleDisplay" in source
    assert "sourceGroup" in source
    assert "limeStatusDot" in source
    assert "libraryTabs" in source
    assert "#7fcf2a" in source
    assert "#1d5f91" in source
    assert "qlineargradient" in source
    assert "border-top: 1px solid #ffffff" in source
    assert "border-bottom: 1px solid #8a949d" in source
    assert "Verdana" in source
    assert "Source List" in source
    assert "Now Playing" in source
