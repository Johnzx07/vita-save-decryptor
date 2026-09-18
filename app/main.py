"""Vita Save Decryptor — PySide6 GUI (Phase 3) + headless CLI mode.

The GUI calls the verified backend in ``app/core.py`` only — there is NO second
decryption implementation here.

Run modes
---------
GUI (default):      python -m app.main          /  dist\\VitaSaveDecryptor.exe
Headless/CLI:       VitaSaveDecryptor.exe --decrypt <backup> --key <64hex> [--out DIR]
Version:            VitaSaveDecryptor.exe --version

Source safety: the original CMA backup is never opened for writing; all work
happens under ``<out_dir>/work``. The full CMA key is NEVER written to logs —
only a masked form (first 4 + last 4 chars) appears in diagnostics.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QSize, QUrl
from PySide6.QtGui import QIcon, QPixmap, QFont, QTextCursor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QCheckBox, QFrame, QTextEdit, QProgressBar,
    QFileDialog, QMessageBox, QDialog, QSizePolicy,
)

from . import APP_NAME, APP_VERSION
from .core import (
    KEY_RE, PipelineError, STAGES, DecryptResult, detect_save, decrypt_backup,
)
from .resources import icon_path, logo_path, bundled_tools


# --------------------------------------------------------------------------- #
# Diagnostics (never logs the full key)
# --------------------------------------------------------------------------- #
def mask_key(key: str) -> str:
    key = (key or "").strip()
    if len(key) >= 8:
        return f"{key[:4]}…{key[-4:]}"
    return "***"


def diag_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "VitaSaveDecryptor"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Path(os.devnull)
    return d / "vitasave_decryptor.log"


def diag_log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        with open(diag_log_path(), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Best-effort game icon lookup (CMA backups usually contain no icon)
# --------------------------------------------------------------------------- #
_ICON_NAMES = {"icon0.png", "icon.png", "cover.png", "thumbnail.png"}


def find_game_icon(root: Path, max_depth: int = 3) -> Path | None:
    try:
        root = root.resolve()
    except OSError:
        return None
    if not root.is_dir():
        return None
    for depth in range(max_depth + 1):
        pattern = "*/*" * depth + "*"
        for p in root.glob(pattern):
            if p.is_file() and p.name.lower() in _ICON_NAMES:
                return p
    return None


# --------------------------------------------------------------------------- #
# Worker thread (keeps the UI responsive)
# --------------------------------------------------------------------------- #
class DecryptWorker(QThread):
    sig_stage = Signal(int, str)
    sig_log = Signal(str)
    sig_ok = Signal(object)      # DecryptResult
    sig_err = Signal(str)

    def __init__(self, backup: Path | str, key: str, out_dir: Path | str, parent=None):
        super().__init__(parent)
        self.backup = backup
        self.key = key
        self.out_dir = out_dir

    def run(self) -> None:  # noqa: D102 — QThread entry point
        try:
            res = decrypt_backup(
                self.backup, self.key, self.out_dir,
                progress_cb=lambda i, t: (self.sig_stage.emit(i, t),
                                          self.sig_log.emit(t)),
            )
            self.sig_ok.emit(res)
        except PipelineError as e:
            self.sig_err.emit(str(e))
        except Exception as e:  # unexpected — surface it, never crash silently
            self.sig_err.emit(f"Unexpected error:\n{e}")


# --------------------------------------------------------------------------- #
# About dialog
# --------------------------------------------------------------------------- #
class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About")
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(10)

        try:
            pm = QPixmap(str(logo_path()))
            if not pm.isNull():
                icon_lbl = QLabel()
                icon_lbl.setPixmap(pm.scaled(128, 128, Qt.KeepAspectRatio,
                                             Qt.SmoothTransformation))
                icon_lbl.setAlignment(Qt.AlignCenter)
                lay.addWidget(icon_lbl)
        except PipelineError:
            pass

        title = QLabel(f"<b>{APP_NAME}</b> &nbsp; v{APP_VERSION}")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        body = QLabel(
            "Decrypts PSVita CMA save backups (savedata.psvimg) into plain files\n"
            "for use with Vita3K.\n\n"
            "Pipeline: psvimg-extract → native PFS decryption (keys derived\n"
            "locally from the sealed key — no online service needed).\n"
            "Your original backup is never modified."
        )
        body.setAlignment(Qt.AlignCenter)
        lay.addWidget(body)

        links = QLabel(
            '<a href="https://github.com/Vita3K/vita3k">Vita3K</a> &nbsp;·&nbsp; '
            '<a href="http://cma.henkaku.xyz">CMA backup format</a>'
        )
        links.setAlignment(Qt.AlignCenter)
        links.setOpenExternalLinks(True)
        lay.addWidget(links)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignRight)
        self.resize(460, 380)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        try:
            self.setWindowIcon(QIcon(str(icon_path())))
        except PipelineError:
            pass
        self.setMinimumSize(920, 780)

        self.detected = None          # core.DetectedSave
        self.worker = None
        self.user_set_output = False
        self.last_result: DecryptResult | None = None

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)
        self.setCentralWidget(central)

        root.addWidget(self._build_header())
        root.addWidget(self._build_backup_card(), 0)
        root.addLayout(self._build_key_row(), 0)
        root.addLayout(self._build_output_row(), 0)
        self.decrypt_btn = QPushButton("DECRYPT SAVE DATA")
        self.decrypt_btn.setObjectName("PrimaryBtnBig")
        self.decrypt_btn.setMinimumHeight(52)
        self.decrypt_btn.clicked.connect(self._start_decrypt)
        root.addWidget(self.decrypt_btn, 0)
        self.progress_frame = self._build_progress_frame()
        root.addWidget(self.progress_frame, 1)

        self.setAcceptDrops(True)
        self._refresh_state()
        # Qt shows top-level windows at their layout sizeHint (628x752 here),
        # which is smaller than the minimum size — force a comfortable default.
        self.resize(920, 780)

    # ---------------- UI construction -------------------------------------- #
    def _card(self, title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(objectName="Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)
        if title:
            t = QLabel(title)
            t.setObjectName("StepTitle")
            lay.addWidget(t)
        return card, lay

    def _build_header(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 4)
        try:
            pm = QPixmap(str(logo_path()))
            if not pm.isNull():
                logo = QLabel()
                logo.setPixmap(pm.scaled(56, 56, Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))
                h.addWidget(logo)
        except PipelineError:
            pass
        title = QLabel(f"<span style='font-size:20px; font-weight:700;'>{APP_NAME}</span>"
                       f"&nbsp;&nbsp;<span style='color:#8fa3c8;'>v{APP_VERSION} — "
                       "CMA backup → decrypted save data</span>")
        h.addWidget(title, 1)
        self.about_btn = QPushButton("About")
        self.about_btn.setObjectName("GhostBtn")
        self.about_btn.clicked.connect(lambda: AboutDialog(self).exec())
        h.addWidget(self.about_btn)
        return w

    def _build_backup_card(self) -> QFrame:
        card, lay = self._card("1 · CMA BACKUP")
        row = QHBoxLayout()
        self.drop_lbl = QLabel(
            "Drag & drop your CMA backup folder (or savedata.psvimg) here"
        )
        self.drop_lbl.setObjectName("DropHint")
        self.drop_lbl.setWordWrap(True)
        row.addWidget(self.drop_lbl, 1)

        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self._browse_backup)
        row.addWidget(self.browse_btn)
        lay.addLayout(row)

        # detection summary (hidden until a save is found)
        self.det_frame = QFrame(objectName="DetCard")
        dlay = QGridLayout(self.det_frame)
        dlay.setContentsMargins(12, 10, 12, 10)
        dlay.setHorizontalSpacing(18)

        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(QSize(72, 72))
        self.icon_lbl.setAlignment(Qt.AlignCenter)
        self.icon_lbl.setObjectName("IconTile")
        dlay.addWidget(self.icon_lbl, 0, 0, 3, 1)

        self.lbl_title = QLabel("—")
        self.lbl_title.setObjectName("DetValue")
        self.lbl_tid = QLabel("—")
        self.lbl_tid.setObjectName("DetValueMono")
        self.lbl_psvimg = QLabel("—")
        self.lbl_psvimg.setObjectName("DetValueMono")
        self.lbl_psvimg.setTextInteractionFlags(Qt.TextSelectableByMouse)

        dlay.addWidget(QLabel("Game"), 0, 1)
        dlay.addWidget(self.lbl_title, 0, 2)
        dlay.addWidget(QLabel("Title ID"), 1, 1)
        dlay.addWidget(self.lbl_tid, 1, 2)
        dlay.addWidget(QLabel("PSVIMG"), 2, 1)
        dlay.addWidget(self.lbl_psvimg, 2, 2)
        lay.addWidget(self.det_frame)

        self.backup_path_lbl = QLabel("")
        self.backup_path_lbl.setObjectName("PathHint")
        self.backup_path_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.backup_path_lbl)
        return card

    def _build_key_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel("2 · CMA KEY")
        lbl.setObjectName("StepTitle")
        row.addWidget(lbl, 0)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText(
            "64 hexadecimal characters — shown by cma.henkaku.xyz when you created the backup"
        )
        self.key_edit.setClearButtonEnabled(True)
        row.addWidget(self.key_edit, 1)
        self.show_key = QCheckBox("Show")
        self.show_key.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password))
        row.addWidget(self.show_key)
        return row

    def _build_output_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel("OUTPUT")
        lbl.setObjectName("StepTitle")
        row.addWidget(lbl, 0)
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText(
            "Default: Decrypted Saves\\<TitleID> next to your backup (backup is never modified)")
        row.addWidget(self.out_edit, 1)
        self.out_browse = QPushButton("Browse…")
        self.out_browse.clicked.connect(self._browse_output)
        row.addWidget(self.out_browse)
        return row

    def _build_progress_frame(self) -> QFrame:
        f = QFrame(objectName="Card")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        self.status_lbl = QLabel("Ready.")
        self.status_lbl.setObjectName("StatusLbl")
        top.addWidget(self.status_lbl, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, len(STAGES))
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        top.addWidget(self.progress_bar, 2)
        lay.addLayout(top)

        # stage checklist
        self.stage_rows: list[tuple[QLabel, QLabel]] = []
        for text in STAGES:
            r = QHBoxLayout()
            mark = QLabel("○")
            mark.setObjectName("StageMark")
            mark.setFixedWidth(18)
            txt = QLabel(text.rstrip("."))
            txt.setObjectName("StageText")
            r.addWidget(mark, 0)
            r.addWidget(txt, 1)
            lay.addLayout(r)
            self.stage_rows.append((mark, txt))

        # success banner (hidden until done)
        self.success_frame = QFrame(objectName="SuccessCard")
        slay = QVBoxLayout(self.success_frame)
        slay.setContentsMargins(16, 14, 16, 14)
        ok_title = QLabel("DECRYPTION SUCCESSFUL")
        ok_title.setObjectName("SuccessTitle")
        self.ok_detail = QLabel("")
        self.ok_detail.setObjectName("SuccessDetail")
        self.ok_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        slay.addWidget(ok_title, 0, Qt.AlignCenter)
        slay.addWidget(self.ok_detail, 0, Qt.AlignCenter)
        btns = QHBoxLayout()
        self.open_btn = QPushButton("Open Decrypted Folder")
        self.open_btn.setObjectName("PrimaryBtn")
        self.open_btn.clicked.connect(self._open_output)
        btns.addStretch(1)
        btns.addWidget(self.open_btn)
        slay.addLayout(btns)
        lay.addWidget(self.success_frame)

        # log area
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("LogArea")
        f.setFont(QFont("Consolas", 9))
        lay.addWidget(self.log, 1)
        return f

    # ---------------- helpers ---------------------------------------------- #
    def log_line(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{stamp}] {msg}")
        cur = self.log.textCursor()
        cur.movePosition(QTextCursor.End)
        self.log.setTextCursor(cur)

    def _set_stage(self, idx: int, state: str) -> None:
        """state: pending | active | done | error"""
        marks = {"pending": ("○", "#5a6b8c"), "active": ("▶", "#37b1ff"),
                 "done": ("✓", "#2ecc71"), "error": ("✗", "#e74c3c")}
        mark, txt = self.stage_rows[idx]
        m, c = marks[state]
        mark.setText(m)
        mark.setStyleSheet(f"color:{c}; font-size:15px;")
        if state == "active":
            txt.setStyleSheet("color:#ffffff; font-weight:600;")
        elif state in ("done",):
            txt.setStyleSheet("color:#9fb4d8;")
        elif state == "error":
            txt.setStyleSheet("color:#e74c3c; font-weight:600;")
        else:
            txt.setStyleSheet("color:#5a6b8c;")

    def _reset_stages(self) -> None:
        for i in range(len(STAGES)):
            self._set_stage(i, "pending")
        self.progress_bar.setValue(0)
        self.success_frame.hide()

    def _refresh_state(self) -> None:
        ready = self.detected is not None and bool(KEY_RE.fullmatch(self.key_edit.text().strip()))
        self.decrypt_btn.setEnabled(bool(ready))

    # ---------------- backup selection ------------------------------------- #
    def _browse_backup(self) -> None:
        start = str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Select your CMA backup folder", start)
        if d:
            self._handle_path(Path(d))

    def _browse_output(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select output folder",
                                             str(Path.home()))
        if d:
            self.out_edit.setText(d)
            self.user_set_output = True

    def _default_out_dir(self) -> Path | None:
        if not self.detected:
            return None
        anchor = self.detected.backup_root
        if not anchor.is_dir():
            anchor = self.detected.psvimg.parent
        name = self.detected.title_id or self.detected.psvimg.stem
        return anchor.parent / "Decrypted Saves" / name

    def _handle_path(self, p: Path) -> None:
        try:
            det = detect_save(p)
        except PipelineError as e:
            self.log_line(f"✗ {e}")
            self.status_lbl.setText("Backup not recognized.")
            return
        self.detected = det
        icon = find_game_icon(det.backup_root if det.backup_root.is_dir() else det.psvimg.parent)
        if icon is not None:
            pm = QPixmap(str(icon))
            if not pm.isNull():
                self.icon_lbl.setPixmap(pm.scaled(64, 64, Qt.KeepAspectRatio,
                                                  Qt.SmoothTransformation))
            else:
                self._placeholder_icon()
        else:
            self._placeholder_icon()

        game = det.title_id or "Unknown"
        size_mb = det.psvimg.stat().st_size / (1024 * 1024) if det.psvimg.is_file() else 0.0
        self.lbl_title.setText(f"{game}  ({size_mb:.1f} MB)")
        self.lbl_tid.setText(det.title_id or "—")
        self.lbl_psvimg.setText(str(det.psvimg))
        self.backup_path_lbl.setText(f"Backup: {det.backup_root}")
        self.drop_lbl.setText("CMA backup loaded — save detected automatically.")
        if not self.user_set_output:
            d = self._default_out_dir()
            if d is not None:
                self.out_edit.setText(str(d))
        self.det_frame.show()
        self.log_line(f"✓ Save detected: {det.psvimg.name}"
                      + (f"  Title ID {det.title_id}" if det.title_id else ""))
        diag_log(f"detect ok psvimg={det.psvimg} title={det.title_id}")
        self._refresh_state()

    def _placeholder_icon(self) -> None:
        text = (self.detected.title_id[:4] if self.detected and self.detected.title_id else "PSV")
        self.icon_lbl.setText(text)
        self.icon_lbl.setStyleSheet(
            "#IconTile{background:#20304d;border-radius:10px;color:#7fb6ff;"
            "font-size:18px;font-weight:700;}")

    # ---------------- drag & drop ------------------------------------------ #
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if not p.exists():
                continue
            self._handle_path(p)
            break

    # ---------------- decrypt flow ----------------------------------------- #
    def _start_decrypt(self) -> None:
        key = self.key_edit.text().strip()
        if not KEY_RE.fullmatch(key):
            QMessageBox.warning(
                self, "Invalid CMA key",
                "The CMA key must be exactly 64 hexadecimal characters.\n\n"
                "It is the key shown by cma.henkaku.xyz when you created the backup.")
            return
        if not self.detected or not self.detected.psvimg.is_file():
            QMessageBox.warning(self, "No save selected",
                                "Select your CMA backup folder first (step 1).")
            return
        out_dir = Path(self.out_edit.text().strip() or "")
        if not str(out_dir):
            d = self._default_out_dir()
            if d is None:
                return
            out_dir = d

        # verify bundled tools up-front so the user gets an instant, clear error
        try:
            bundled_tools()
        except PipelineError as e:
            QMessageBox.critical(self, "Missing components", str(e))
            return

        self._reset_stages()
        self.success_frame.hide()
        for w in (self.decrypt_btn, self.browse_btn, self.out_browse,
                  self.key_edit, self.show_key):
            w.setEnabled(False)
        self.status_lbl.setText("Working…")
        self.log_line(f"Starting decryption  key={mask_key(key)}  out={out_dir}")
        diag_log(f"run start backup={self.detected.psvimg} key={mask_key(key)} out={out_dir}")

        self.worker = DecryptWorker(self.detected.psvimg, key, out_dir, self)
        self.worker.sig_stage.connect(self._on_stage)
        self.worker.sig_ok.connect(self._on_done)
        self.worker.sig_err.connect(self._on_error)
        self.worker.start()

    def _on_stage(self, idx: int, text: str) -> None:
        for i in range(idx):
            self._set_stage(i, "done")
        if idx < len(STAGES):
            self._set_stage(idx, "active")
            self.status_lbl.setText(text.rstrip("."))
            self.log_line(f"… {text}")
            self.progress_bar.setValue(idx)

    def _on_done(self, res: DecryptResult) -> None:
        for i in range(len(STAGES)):
            self._set_stage(i, "done")
        self.progress_bar.setValue(len(STAGES))
        self.last_result = res
        name = res.game_title or res.title_id or "save"
        self.ok_detail.setText(
            f"{name}   ·   Title ID {res.title_id or '—'}   ·   {res.file_count} files\n"
            f"{res.decrypted_dir}")
        self.success_frame.show()
        self.status_lbl.setText("DECRYPTION SUCCESSFUL")
        self.log_line(f"✓ DECRYPTION SUCCESSFUL — {res.file_count} files → {res.decrypted_dir}")
        diag_log(f"run ok title={res.title_id} files={res.file_count} out={res.decrypted_dir}")
        for w in (self.decrypt_btn, self.browse_btn, self.out_browse):
            w.setEnabled(True)

    def _on_error(self, msg: str) -> None:
        idx = min(self.progress_bar.value(), len(STAGES) - 1)
        self._set_stage(idx, "error")
        self.status_lbl.setText("Decryption failed.")
        for line in msg.splitlines():
            self.log_line(f"✗ {line}")
        diag_log(f"run FAILED: {msg[:400]}")
        QMessageBox.critical(self, "Decryption failed", msg)
        for w in (self.decrypt_btn, self.browse_btn, self.out_browse):
            w.setEnabled(True)

    def _open_output(self) -> None:
        if not self.last_result or not self.last_result.decrypted_dir.is_dir():
            return
        target = str(self.last_result.decrypted_dir)
        try:
            os.startfile(target)  # Windows
        except (AttributeError, OSError):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))


# --------------------------------------------------------------------------- #
# Styling — dark theme in the spirit of RPCS3 Game Updater
# --------------------------------------------------------------------------- #
QSS = """
QWidget { background-color: #0f1420; color: #e8ecf3; font-family: 'Segoe UI', sans-serif; }
QMainWindow, QDialog { background-color: #0f1420; }

QFrame#Card { background: #161d2c; border: 1px solid #232d44; border-radius: 10px; }
QFrame#DetCard { background: #1b2438; border: 1px solid #2a3a5e; border-radius: 8px; }

QLabel#StepTitle { color: #7fb6ff; font-weight: 700; font-size: 13px; letter-spacing: 1px; }
QLabel#DropHint { color: #9fb4d8; font-size: 15px; }
QLabel#DetValue { color: #ffffff; font-size: 16px; font-weight: 600; }
QLabel#DetValueMono { color: #cfe3ff; font-family: Consolas, monospace; font-size: 12px; }
QLabel#PathHint { color: #5a6b8c; font-size: 11px; }
QLabel#StatusLbl { color: #9fb4d8; font-weight: 600; }
QLabel#StageMark { font-size: 15px; }
QLabel#StageText { color: #5a6b8c; font-size: 13px; }

QPushButton { background: #22304e; border: 1px solid #33456e; border-radius: 8px;
              padding: 7px 16px; color: #e8ecf3; font-weight: 600; }
QPushButton:hover { background: #2a3c62; }
QPushButton:disabled { background: #1a2234; color: #5a6b8c; border-color: #232d44; }

QPushButton#PrimaryBtn, QPushButton#PrimaryBtnBig {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2ea8ff, stop:1 #1673d6);
    border: none; color: white; font-weight: 800; }
QPushButton#PrimaryBtn:hover, QPushButton#PrimaryBtnBig:hover {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #4cb5ff, stop:1 #1f83ea); }
QPushButton#PrimaryBtnBig { font-size: 17px; letter-spacing: 2px; border-radius: 10px; }

QFrame#SuccessCard { background: rgba(46, 204, 113, 0.12);
    border: 1px solid #2ecc71; border-radius: 10px; }
QLabel#SuccessTitle { color: #2ecc71; font-size: 20px; font-weight: 800; letter-spacing: 2px; }
QLabel#SuccessDetail { color: #d6f5e3; font-size: 13px; }

QLineEdit, QTextEdit { background: #0c1119; border: 1px solid #2a3a5e;
    border-radius: 8px; padding: 6px 10px; selection-background-color: #2ea8ff; }
QTextEdit#LogArea { font-family: Consolas, monospace; color: #b9c7de; }

QProgressBar { background: #0c1119; border: none; border-radius: 4px; height: 10px; }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
    stop:0 #2ea8ff, stop:1 #2ecc71); border-radius: 4px; }

QCheckBox { color: #9fb4d8; spacing: 6px; }
QCheckBox::indicator { width: 15px; height: 15px; border-radius: 3px;
    border: 1px solid #33456e; background: #0c1119; }
QCheckBox::indicator:checked { background: #2ea8ff; border-color: #2ea8ff; }

QMessageBox, QMessageBox QLabel { background-color: #161d2c; color: #e8ecf3; }
"""


# --------------------------------------------------------------------------- #
# Headless CLI mode (also used to regression-test the packaged EXE)
# --------------------------------------------------------------------------- #
def run_cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="VitaSaveDecryptor",
        description=f"{APP_NAME} v{APP_VERSION} — headless mode")
    ap.add_argument("--version", action="store_true", help="print version and exit")
    ap.add_argument("--decrypt", metavar="BACKUP",
                    help="CMA backup folder or savedata.psvimg to decrypt")
    ap.add_argument("--key", metavar="KEY64HEX", help="64-hex CMA decryption key")
    ap.add_argument("--out", metavar="DIR", help="output directory (default: "
                                                 "Decrypted Saves\\<TitleID> next to backup)")
    args = ap.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0
    if not args.decrypt or not args.key:
        ap.error("--decrypt <backup> and --key <64hex> are required")

    key = (args.key or "").strip()
    if not KEY_RE.fullmatch(key):
        print("ERROR: CMA key must be exactly 64 hexadecimal characters.", file=sys.stderr)
        return 2

    backup = Path(args.decrypt)
    try:
        det = detect_save(backup)
    except PipelineError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.out:
        out_dir = Path(args.out)
    else:
        anchor = det.backup_root if det.backup_root.is_dir() else det.psvimg.parent
        name = det.title_id or det.psvimg.stem
        out_dir = anchor.parent / "Decrypted Saves" / name

    print(f"[detect] psvimg={det.psvimg}  title_id={det.title_id}")
    diag_log(f"cli run start backup={det.psvimg} key={mask_key(key)} out={out_dir}")

    def cb(i, t):
        print(f"[stage {i + 1}/{len(STAGES)}] {t}", flush=True)

    try:
        res = decrypt_backup(det.psvimg, key, out_dir, progress_cb=cb)
    except PipelineError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        diag_log(f"cli run FAILED: {str(e)[:400]}")
        return 1

    print("DECRYPTION SUCCESSFUL")
    print(f"title_id   : {res.title_id}")
    print(f"game_title : {res.game_title}")
    print(f"files      : {res.file_count}")
    print(f"output     : {res.decrypted_dir}")
    diag_log(f"cli run ok title={res.title_id} files={res.file_count} out={res.decrypted_dir}")
    return 0


# --------------------------------------------------------------------------- #
def main() -> int:
    argv = sys.argv[1:]
    if any(a in ("--decrypt", "--version") for a in argv):
        return run_cli(argv)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("VitaSaveDecryptor")
    try:
        app.setWindowIcon(QIcon(str(icon_path())))
    except PipelineError:
        pass
    app.setStyleSheet(QSS)
    win = MainWindow()
    win.show()
    win.activateWindow()
    win.raise_()
    diag_log(f"gui start v{APP_VERSION} frozen={getattr(sys, 'frozen', False)}")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
