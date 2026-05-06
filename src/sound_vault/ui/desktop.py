from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sound_vault.ui.view_model import LibraryViewModel


DEFAULT_VAULT = Path("/nas/TikTok Sound Vault")


class SoundVaultWindow(QMainWindow):
    def __init__(self, *, vault_root: Path = DEFAULT_VAULT) -> None:
        super().__init__()
        self.setWindowTitle("Sound Vault")
        self.resize(1280, 820)
        self.vault_root = vault_root
        self.vm = LibraryViewModel(
            vault_root=self.vault_root,
            index_path=Path.home() / ".sound-vault" / "index.sqlite3",
        )
        self.current_rows = []
        self._build_ui()
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
        side.setContentsMargins(22, 24, 18, 24)
        side.setSpacing(12)

        brand = QLabel("Sound Vault")
        brand.setObjectName("brand")
        side.addWidget(brand)
        for label in ["Library", "Ingest inbox", "Review queues", "Collections", "Worker status", "Settings"]:
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            side.addWidget(btn)
        side.addStretch(1)
        self.pairing_label = QLabel("Shortcut relay\nPAIR CODE\nRIVER-7421")
        self.pairing_label.setObjectName("pairingCard")
        self.pairing_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(self.pairing_label)
        shell.addWidget(sidebar)

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(26, 24, 20, 24)
        main_layout.setSpacing(14)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Library")
        title.setObjectName("title")
        self.vault_label = QLabel(str(self.vault_root))
        self.vault_label.setObjectName("muted")
        title_box.addWidget(title)
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

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search title, artist, tag, music ID…")
        self.search_box.textChanged.connect(self.refresh_table)
        main_layout.addWidget(self.search_box)

        stats_grid = QGridLayout()
        self.stats_label = QLabel("index not built")
        self.stats_label.setObjectName("statCard")
        self.inbox_label = QLabel("Shortcut inbox\n0 pending")
        self.inbox_label.setObjectName("statCard")
        self.worker_label = QLabel("Worker\nidle")
        self.worker_label.setObjectName("statCard")
        stats_grid.addWidget(self.stats_label, 0, 0)
        stats_grid.addWidget(self.inbox_label, 0, 1)
        stats_grid.addWidget(self.worker_label, 0, 2)
        main_layout.addLayout(stats_grid)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["play", "sound", "artist/source", "tags", "status", "videos"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self.update_preview_from_selection)
        main_layout.addWidget(self.table, 1)
        shell.addWidget(main, 1)

        preview = QFrame()
        preview.setObjectName("preview")
        preview.setFixedWidth(390)
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(22, 24, 22, 24)
        preview_layout.setSpacing(12)
        self.preview_title = QLabel("Select a sound")
        self.preview_title.setObjectName("previewTitle")
        self.preview_meta = QLabel("Audio, tags, evidence and local paths will show here.")
        self.preview_meta.setObjectName("muted")
        self.preview_meta.setWordWrap(True)
        self.preview_tags = QLabel("")
        self.preview_tags.setWordWrap(True)
        self.preview_json = QTextEdit()
        self.preview_json.setReadOnly(True)
        self.preview_json.setPlaceholderText("raw metadata preview")
        open_folder = QPushButton("Open folder")
        open_folder.setEnabled(False)
        preview_layout.addWidget(self.preview_title)
        preview_layout.addWidget(self.preview_meta)
        preview_layout.addWidget(self.preview_tags)
        preview_layout.addWidget(open_folder)
        preview_layout.addWidget(self.preview_json, 1)
        shell.addWidget(preview)

        self.setStyleSheet(STYLESHEET)

    def choose_vault(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose Sound Vault", str(self.vault_root))
        if not selected:
            return
        self.vault_root = Path(selected)
        self.vault_label.setText(str(self.vault_root))
        self.vm = LibraryViewModel(vault_root=self.vault_root, index_path=Path.home() / ".sound-vault" / "index.sqlite3")
        self.rebuild_index()

    def rebuild_index(self) -> None:
        self.vm.rebuild_index()
        self.stats_label.setText(self.vm.stats_text())
        self.refresh_table()

    def refresh_table(self) -> None:
        self.current_rows = self.vm.search(self.search_box.text())
        self.table.setRowCount(len(self.current_rows))
        for row_idx, record in enumerate(self.current_rows):
            values = ["▶", record.title or record.music_id, record.artist, ", ".join(record.tags), record.status, str(record.associated_video_count)]
            for col_idx, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col_idx == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row_idx, col_idx, item)
        if self.current_rows and not self.table.selectedItems():
            self.table.selectRow(0)

    def update_preview_from_selection(self) -> None:
        selected = self.table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        if row >= len(self.current_rows):
            return
        record = self.current_rows[row]
        self.preview_title.setText(record.title or record.music_id)
        self.preview_meta.setText(f"{record.artist or 'source pending'} • {record.music_id} • {record.associated_video_count} videos")
        self.preview_tags.setText(" ".join(f"#{tag}" for tag in record.tags) or "no tags yet")
        self.preview_json.setPlainText(str(record.raw or {"music_id": record.music_id, "status": record.status}))


STYLESHEET = """
QMainWindow, QWidget { background: #0e1117; color: #eef2f6; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI"; font-size: 14px; }
#sidebar { background: #111722; border-right: 1px solid #293241; }
#brand { font-size: 22px; font-weight: 800; padding-bottom: 16px; }
#title { font-size: 36px; font-weight: 800; }
#previewTitle { font-size: 25px; font-weight: 800; }
#muted { color: #8e9bad; }
#preview { background: #111722; border-left: 1px solid #293241; }
QPushButton { background: #202735; color: #eef2f6; border: 1px solid #293241; border-radius: 10px; padding: 10px 12px; text-align: left; }
QPushButton:hover { background: #293241; }
#primaryButton { background: #55d6c2; color: #08100f; font-weight: 800; text-align: center; }
#navButton { text-align: left; }
#pairingCard, #statCard { background: #151a22; border: 1px solid #293241; border-radius: 16px; padding: 16px; color: #eef2f6; }
QLineEdit { background: #151a22; border: 1px solid #293241; border-radius: 14px; padding: 12px; color: #eef2f6; }
QTableWidget { background: #111722; border: 1px solid #293241; border-radius: 14px; gridline-color: #293241; selection-background-color: #1f3d47; }
QHeaderView::section { background: #151a22; color: #8e9bad; padding: 9px; border: 0; border-bottom: 1px solid #293241; }
QTextEdit { background: #151a22; border: 1px solid #293241; border-radius: 12px; padding: 10px; color: #cbd5e1; }
"""


def run_desktop(vault_root: Path = DEFAULT_VAULT) -> int:
    app = QApplication.instance() or QApplication([])
    window = SoundVaultWindow(vault_root=vault_root)
    window.show()
    return app.exec()
