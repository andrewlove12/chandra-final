"""Desktop app for the notebook OCR: pick folders and options, run, watch progress.

The OCR itself runs in a separate process (process_pdfs.py --progress-json), the
same offline engine as the command line: Hugging Face offline mode and the
network block stay in force there. This window only reads its progress lines.
Nothing here uses the network.

Start it with `.venv\\Scripts\\pythonw.exe gui.py` (the installer's shortcut does this).
"""
import json
import os
import subprocess
import sys
import time

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "Chandra Notebook OCR"
CREATE_NO_WINDOW = 0x08000000  # Windows: don't flash a console window for the worker


def worker_python() -> str:
    """python.exe next to the interpreter running this GUI (pythonw.exe can't pipe output)."""
    exe = sys.executable
    folder, name = os.path.split(exe)
    if name.lower() == "pythonw.exe":
        candidate = os.path.join(folder, "python.exe")
        if os.path.exists(candidate):
            return candidate
    return exe


def kill_tree(pid: int):
    """Stop the worker and everything it started (the venv python.exe is a launcher with a child)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


class Worker(QObject):
    """Runs process_pdfs.py in a background thread and forwards its output line by line."""
    line = Signal(str)
    event = Signal(dict)
    finished = Signal(int, bool)  # exit code, cancelled

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.proc = None
        self.cancelled = False

    def run(self):
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        cmd = [worker_python(), "-u", os.path.join(APP_DIR, "process_pdfs.py"), *self.args, "--progress-json"]
        try:
            self.proc = subprocess.Popen(cmd, cwd=APP_DIR, env=env, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="replace", bufsize=1,
                                         creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as e:
            self.line.emit(f"Couldn't start the OCR engine: {e}")
            self.finished.emit(-1, False)
            return
        for raw in self.proc.stdout:
            text = raw.rstrip("\r\n")
            if text.startswith("@@"):
                try:
                    self.event.emit(json.loads(text[2:]))
                except ValueError:
                    pass
                continue
            # tqdm progress bars redraw with \r; only show their last state.
            text = text.split("\r")[-1]
            if text.strip():
                self.line.emit(text)
        code = self.proc.wait()
        self.finished.emit(code, self.cancelled)

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            kill_tree(self.proc.pid)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        icon = os.path.join(APP_DIR, "installer", "app.ico")
        if os.path.exists(icon):
            self.setWindowIcon(QIcon(icon))
        self.settings = QSettings("ChandraNotebookOCR", "gui")
        self.thread = None
        self.worker = None
        self._reset_run_state()
        self._build_ui()
        self._load_settings()
        self._check_install()
        self.resize(980, 760)

    # ----- layout -------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet("background:#fdecea; color:#8a1c12; padding:8px; border-radius:4px;")
        self.banner.hide()
        root.addWidget(self.banner)

        # Folders
        folders = QGroupBox("Folders")
        grid = QGridLayout(folders)
        self.input_edit, self.output_edit = QLineEdit(), QLineEdit()
        self.input_edit.setPlaceholderText("Folder with the scanned notebook PDFs (subfolders included)")
        self.output_edit.setPlaceholderText("Folder for the copies with the red transcription")
        for row, (label, edit, title) in enumerate([
            ("Notebooks (input):", self.input_edit, "Choose the folder with the notebook PDFs"),
            ("Results (output):", self.output_edit, "Choose where to save the annotated PDFs"),
        ]):
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(edit, row, 1)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _=False, e=edit, t=title: self._browse(e, t))
            grid.addWidget(browse, row, 2)
        root.addWidget(folders)

        # Options
        options = QGroupBox("Options")
        form = QFormLayout(options)
        self.device_combo = QComboBox()
        self.device_combo.addItem("Automatic (graphics card if available)", "auto")
        self.device_combo.addItem("Graphics card (NVIDIA or AMD)", "cuda")
        self.device_combo.addItem("Processor only (very slow)", "cpu")
        form.addRow("Run on:", self.device_combo)
        self.precision_combo = QComboBox()
        self.precision_combo.addItem("Full precision (recommended, most accurate)", "none")
        self.precision_combo.addItem("Compressed int8 (smaller; less accurate)", "int8")
        form.addRow("Model:", self.precision_combo)
        self.auto_rotate = QCheckBox("Turn sideways or upside-down pages upright before reading them")
        self.auto_rotate.setChecked(True)
        form.addRow("", self.auto_rotate)
        self.skip_existing = QCheckBox("Skip notebooks that already have a result file")
        form.addRow("", self.skip_existing)

        advanced = QHBoxLayout()
        self.reserve_spin = QDoubleSpinBox()
        self.reserve_spin.setRange(0.5, 12.0)
        self.reserve_spin.setSingleStep(0.5)
        self.reserve_spin.setSuffix(" GB")
        self.reserve_spin.setValue(2.5)
        self.reserve_spin.setToolTip("Graphics memory kept free for the page being read. "
                                     "Raise it if a page fails with 'out of memory'.")
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(100, 600)
        self.dpi_spin.setSingleStep(50)
        self.dpi_spin.setValue(300)
        self.dpi_spin.setToolTip("Resolution pages are rendered at. Rarely worth changing.")
        advanced.addWidget(QLabel("GPU memory reserve:"))
        advanced.addWidget(self.reserve_spin)
        advanced.addSpacing(20)
        advanced.addWidget(QLabel("Render DPI:"))
        advanced.addWidget(self.dpi_spin)
        advanced.addStretch(1)
        form.addRow("Advanced:", advanced)
        root.addWidget(options)

        # Buttons
        buttons = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.setDefault(True)
        self.start_btn.setMinimumWidth(120)
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        self.open_btn = QPushButton("Open results folder")
        self.open_btn.clicked.connect(self._open_output)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.cancel_btn)
        buttons.addStretch(1)
        self.logs_btn = QPushButton("Open logs folder")
        self.logs_btn.setToolTip("Log files to send if something isn't working. They contain progress, settings, "
                                 "folder and file names, and errors, but never the transcribed text.")
        self.logs_btn.clicked.connect(self._open_logs)
        buttons.addWidget(self.logs_btn)
        buttons.addWidget(self.open_btn)
        root.addLayout(buttons)

        # Progress
        progress = QGroupBox("Progress")
        pv = QVBoxLayout(progress)
        self.stage_label = QLabel("Ready.")
        self.stage_label.setWordWrap(True)
        bold = QFont(self.stage_label.font())
        bold.setBold(True)
        self.stage_label.setFont(bold)
        pv.addWidget(self.stage_label)
        pgrid = QGridLayout()
        self.overall_bar, self.pdf_bar = QProgressBar(), QProgressBar()
        self.overall_bar.setFormat("%p%")
        self.pdf_bar.setFormat("%v of %m pages")
        pgrid.addWidget(QLabel("All notebooks:"), 0, 0)
        pgrid.addWidget(self.overall_bar, 0, 1)
        pgrid.addWidget(QLabel("This notebook:"), 1, 0)
        pgrid.addWidget(self.pdf_bar, 1, 1)
        pv.addLayout(pgrid)
        self.detail_label = QLabel("")
        self.detail_label.setStyleSheet("color:#555;")
        pv.addWidget(self.detail_label)
        root.addWidget(progress)

        # Issues + log
        splitter = QSplitter(Qt.Vertical)
        issues_box = QGroupBox("Pages to check")
        iv = QVBoxLayout(issues_box)
        self.issues = QListWidget()
        self.issues.setToolTip("Pages that failed or whose reading may be incomplete. Double-check these.")
        iv.addWidget(self.issues)
        log_box = QGroupBox("Log")
        lv = QVBoxLayout(log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(20000)
        self.log.setFont(QFont("Consolas", 9))
        lv.addWidget(self.log)
        splitter.addWidget(issues_box)
        splitter.addWidget(log_box)
        splitter.setSizes([110, 300])
        root.addWidget(splitter, 1)

        privacy = QLabel("Runs fully offline: your notebooks never leave this computer.")
        privacy.setStyleSheet("color:#2e7d32;")
        root.addWidget(privacy)
        self.setCentralWidget(central)

    # ----- settings -------------------------------------------------------------
    def _load_settings(self):
        s = self.settings
        self.input_edit.setText(s.value("input_dir", ""))
        self.output_edit.setText(s.value("output_dir", ""))
        for combo, key in ((self.device_combo, "device"), (self.precision_combo, "quantize")):
            i = combo.findData(s.value(key, combo.itemData(0)))
            combo.setCurrentIndex(max(0, i))
        self.auto_rotate.setChecked(s.value("auto_rotate", True, type=bool))
        self.skip_existing.setChecked(s.value("skip_existing", False, type=bool))
        self.reserve_spin.setValue(s.value("gpu_reserve_gb", 2.5, type=float))
        self.dpi_spin.setValue(s.value("dpi", 300, type=int))

    def _save_settings(self):
        s = self.settings
        s.setValue("input_dir", self.input_edit.text())
        s.setValue("output_dir", self.output_edit.text())
        s.setValue("device", self.device_combo.currentData())
        s.setValue("quantize", self.precision_combo.currentData())
        s.setValue("auto_rotate", self.auto_rotate.isChecked())
        s.setValue("skip_existing", self.skip_existing.isChecked())
        s.setValue("gpu_reserve_gb", self.reserve_spin.value())
        s.setValue("dpi", self.dpi_spin.value())

    def _check_install(self):
        sys.path.insert(0, APP_DIR)
        try:
            from model_config import model_is_downloaded
            ok = model_is_downloaded()
        except Exception:
            ok = False
        if not ok:
            self.banner.setText("The OCR model isn't installed yet. Run the installer again (or setup.ps1) "
                                "while connected to the internet.")
            self.banner.show()

    # ----- actions -------------------------------------------------------------
    def _browse(self, edit, title):
        start = edit.text() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, title, start)
        if folder:
            edit.setText(os.path.normpath(folder))

    def _open_logs(self):
        import run_log
        folder = run_log.log_dir()
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            QMessageBox.information(self, APP_NAME, "No folder for log files could be created.")

    def _open_output(self):
        folder = self.output_edit.text().strip()
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            QMessageBox.information(self, APP_NAME, "The results folder doesn't exist yet.")

    def _validate(self):
        inp, out = self.input_edit.text().strip(), self.output_edit.text().strip()
        if not inp or not os.path.isdir(inp):
            return "Choose an existing notebooks (input) folder."
        if not out:
            return "Choose a results (output) folder."
        if os.path.normcase(os.path.abspath(inp)) == os.path.normcase(os.path.abspath(out)):
            return "The results folder must be different from the notebooks folder."
        return None

    def start(self):
        problem = self._validate()
        if problem:
            QMessageBox.warning(self, APP_NAME, problem)
            return
        self._save_settings()
        args = [self.input_edit.text().strip(), self.output_edit.text().strip(),
                "--device", self.device_combo.currentData(),
                "--quantize", self.precision_combo.currentData(),
                "--gpu-reserve-gb", f"{self.reserve_spin.value():g}",
                "--dpi", str(self.dpi_spin.value())]
        if not self.auto_rotate.isChecked():
            args.append("--no-auto-rotate")
        if self.skip_existing.isChecked():
            args.append("--skip-existing")

        self._reset_run_state()
        self.log.clear()
        self.issues.clear()
        self.overall_bar.setValue(0)
        self.pdf_bar.setRange(0, 1)
        self.pdf_bar.setValue(0)
        self.detail_label.setText("")
        self._set_stage("Starting…")
        self._set_running(True)

        self.thread = QThread(self)
        self.worker = Worker(args)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        # Queued: the worker emits from its own thread; widgets may only be touched on the GUI thread.
        self.worker.line.connect(self._append_log, Qt.QueuedConnection)
        self.worker.event.connect(self._on_event, Qt.QueuedConnection)
        self.worker.finished.connect(self._on_finished, Qt.QueuedConnection)
        self.worker.finished.connect(self.thread.quit)
        self.thread.start()

    def cancel(self):
        if not self.worker:
            return
        answer = QMessageBox.question(
            self, APP_NAME, "Stop now? Pages already read are kept, so running again picks up where it "
                            "left off. The notebook in progress won't get its result file until then.")
        if answer == QMessageBox.Yes:
            self._set_stage("Stopping…")
            self.worker.cancel()

    def closeEvent(self, event):
        if self.worker and self.thread and self.thread.isRunning():
            answer = QMessageBox.question(self, APP_NAME, "OCR is still running. Stop it and quit?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.thread.quit()
            self.thread.wait(5000)
        self._save_settings()
        event.accept()

    # ----- progress -------------------------------------------------------------
    def _reset_run_state(self):
        self.total_pdfs = 0
        self.pdf_index = 0
        self.pdf_name = ""
        self.pdf_pages = 0
        self.pages_done_in_pdf = 0
        self.page_seconds = []  # seconds of pages actually read (not cached)
        self.issue_count = 0
        self.pdfs_saved = 0
        self.run_start = time.time()

    def _set_running(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        for w in (self.input_edit, self.output_edit, self.device_combo, self.precision_combo,
                  self.auto_rotate, self.skip_existing, self.reserve_spin, self.dpi_spin):
            w.setEnabled(not running)

    def _set_stage(self, text):
        self.stage_label.setText(text)

    def _append_log(self, text):
        self.log.appendPlainText(text)
        self.log.moveCursor(QTextCursor.End)

    def _add_issue(self, text):
        self.issue_count += 1
        self.issues.addItem(QListWidgetItem(text))

    def _update_overall(self):
        if not self.total_pdfs:
            return
        within = self.pages_done_in_pdf / self.pdf_pages if self.pdf_pages else 0
        done = max(0, self.pdf_index - 1) + within
        self.overall_bar.setValue(int(100 * done / self.total_pdfs))

    def _eta_text(self):
        if not self.page_seconds or not self.pdf_pages:
            return ""
        avg = sum(self.page_seconds) / len(self.page_seconds)
        left = (self.pdf_pages - self.pages_done_in_pdf) * avg
        if left < 60:
            return f"About {int(left)} s left for this notebook (≈{avg:.0f} s per page)."
        return f"About {left / 60:.0f} min left for this notebook (≈{avg / 60:.1f} min per page)."

    def _on_event(self, ev):
        kind = ev.get("event")
        if kind == "found":
            self.total_pdfs = ev["pdfs"]
            self._set_stage(f"Found {self.total_pdfs} notebook PDF(s).")
        elif kind == "orientation":
            self._set_stage(f"Checking page orientation… ({ev['index']} of {ev['total']})")
            for page, deg in sorted(ev.get("turned", {}).items(), key=lambda kv: int(kv[0])):
                self._append_log(f"Will turn {ev['name']} page {page} by {deg}°")
        elif kind == "orientation_done":
            self.detail_label.setText(f"Orientation checked: {ev['turned']} of {ev['pages']} page(s) will be turned.")
        elif kind == "loading_model":
            self._set_stage("Loading the OCR model (about a minute)…")
            self.pdf_bar.setRange(0, 0)  # busy indicator
        elif kind == "model_loaded":
            self.pdf_bar.setRange(0, 1)
        elif kind == "pdf_start":
            self.pdf_index, self.pdf_name, self.pdf_pages = ev["index"], ev["name"], ev["pages"]
            self.pages_done_in_pdf = 0
            self.pdf_bar.setRange(0, max(1, self.pdf_pages))
            self.pdf_bar.setValue(0)
            self._update_overall()
        elif kind == "page_start":
            turned = f" (turned {ev['rotated']}°)" if ev.get("rotated") else ""
            self._set_stage(f"Notebook {ev['index']} of {self.total_pdfs}: {self.pdf_name}  —  "
                            f"reading page {ev['page']} of {ev['pages']}{turned}")
        elif kind == "page_done":
            self.pages_done_in_pdf = ev["page"]
            self.pdf_bar.setValue(self.pages_done_in_pdf)
            if not ev.get("cached"):
                self.page_seconds.append(ev.get("seconds", 0))
            if ev.get("warning"):
                self._add_issue(f"{self.pdf_name}, page {ev['page']}: the model got stuck repeating itself; "
                                "the reading may be incomplete.")
            how = "from earlier run" if ev.get("cached") else f"{ev.get('seconds', 0):.0f} s"
            self.detail_label.setText(f"Page {ev['page']}: {ev.get('words', 0)} words ({how}). {self._eta_text()}")
            self._update_overall()
        elif kind == "page_error":
            self.pages_done_in_pdf = ev["page"]
            self.pdf_bar.setValue(self.pages_done_in_pdf)
            self._add_issue(f"{self.pdf_name}, page {ev['page']}: {ev.get('message', 'failed')}")
            self._update_overall()
        elif kind == "pdf_done":
            if ev.get("saved"):
                self.pdfs_saved += 1
            else:
                self._add_issue(f"{ev.get('name', self.pdf_name)}: no result file was saved. "
                                f"{ev.get('message', '')}".strip())
        elif kind == "pdf_skipped":
            self.pdf_index = ev["index"]
            self.pages_done_in_pdf, self.pdf_pages = 1, 1
            self._update_overall()
        elif kind == "pdf_error":
            self._add_issue(f"{ev.get('name')}: {ev.get('message')}")
        elif kind == "error":
            self._add_issue(ev.get("message", "Error"))
        elif kind == "done":
            self.overall_bar.setValue(100)

    def _on_finished(self, code, cancelled):
        self._set_running(False)
        self.pdf_bar.setRange(0, max(1, self.pdf_pages))
        elapsed = time.time() - self.run_start
        mins = f"{elapsed / 60:.0f} min" if elapsed >= 90 else f"{elapsed:.0f} s"
        if cancelled:
            self._set_stage("Stopped. Run again to continue where it left off.")
        elif code == 0:
            extra = f" {self.issue_count} item(s) to check below." if self.issue_count else ""
            self._set_stage(f"Done in {mins}: {self.pdfs_saved} result file(s) saved.{extra}")
        else:
            self._set_stage("Stopped with an error. See the list and log below.")
            if not self.issue_count:
                self._add_issue(f"The OCR engine exited with code {code}. See the log for details.")
        self.worker = None


def main():
    sys.path.insert(0, APP_DIR)
    import run_log
    run_log.start("gui")  # app-level errors; each OCR run writes its own run-*.log
    if sys.platform == "win32":
        # Same ID as the installer's shortcuts: the taskbar then groups the window
        # under the app's own icon instead of pythonw.exe's.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ChandraNotebookOCR.App")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon = os.path.join(APP_DIR, "installer", "app.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
