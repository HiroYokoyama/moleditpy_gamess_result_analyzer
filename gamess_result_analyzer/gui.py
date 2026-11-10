"""Main dialog: file loading, the 3D structure view, atom picking and analysis launchers."""

import os
from datetime import datetime
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QWidget,
    QGridLayout,
    QMessageBox,
    QMenuBar,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QApplication,
)
from PySide6.QtGui import QAction, QIcon, QDesktopServices
from PySide6.QtCore import QSize, Qt, QObject, QEvent, QTimer, QUrl
from .loading import load_gamess_parser
from .parser import IMAGINARY_FREQ_THRESHOLD
from .utils import (
    normalize_atom_symbol,
    determine_bonds_without_dummies,
    list_gamess_output_files,
    clear_atom_color_overrides,
    sync_main_window_file,
    notify,
)


def accept_drop_as_copy(event) -> bool:
    """Accept a file drag as a copy. False, and refused, if the source cannot copy.

    Never acceptProposedAction(): Shift during a drag from Explorer proposes a
    move, and accepting one tells Explorer the file now lives here, so it removes
    the original. A dropped file is only ever read.
    """
    if not event.possibleActions() & Qt.DropAction.CopyAction:
        event.ignore()
        return False
    event.setDropAction(Qt.DropAction.CopyAction)
    event.accept()
    return True


# Tells the GAMESS window apart from the other analyzers at a glance.
THEME_WINDOW = "#f3faf9"
THEME_PANEL = "#e3f4f2"
THEME_MENU = "#d3ecea"
THEME_ACCENT = "#0066cc"


class _ClickFilter(QObject):
    """Qt event filter: detects non-drag left clicks on the 3D plotter widget."""

    def __init__(self, callback, press_callback=None, parent=None):
        super().__init__(parent)
        self._callback = callback
        self._press_callback = press_callback
        self._press_pos = None

    def eventFilter(self, obj, event):
        """Fire the press/click callbacks for a non-drag left click on the widget."""
        t = event.type()
        if t == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._press_pos = event.position().toPoint()
                if self._press_callback:
                    self._press_callback(self._press_pos.x(), self._press_pos.y(), obj)
        elif t == QEvent.Type.MouseButtonRelease:
            if (
                event.button() == Qt.MouseButton.LeftButton
                and self._press_pos is not None
            ):
                rel = event.position().toPoint()
                dx = rel.x() - self._press_pos.x()
                dy = rel.y() - self._press_pos.y()
                self._press_pos = None
                if dx * dx + dy * dy <= 25:  # <=5 px -> click
                    self._callback(rel.x(), rel.y(), obj)
        return False  # never consume -- camera interaction must keep working


class ElidedLabel(QLabel):
    """Label that elides its text with an ellipsis to fit the current width."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = text
        super().setText(text)

    def setText(self, text):
        """Store the full text and redraw it elided to the current width."""
        self._full_text = text
        self._update_elided_text()

    def resizeEvent(self, event):
        """Re-elide the text when the label is resized."""
        super().resizeEvent(event)
        self._update_elided_text()

    def _update_elided_text(self):
        fm = self.fontMetrics()
        # Elide in the middle to show start and end of path
        elided = fm.elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, self.width()
        )
        super().setText(elided)


try:
    from rdkit import Chem
    from rdkit.Geometry import Point3D
except ImportError:
    Chem = None
    Point3D = None

# Imported Modules for Analysis
from .mo_analysis import MODialog  # noqa: E402
from .freq_analysis import FrequencyDialog  # noqa: E402
from .traj_analysis import TrajectoryResultDialog  # noqa: E402
from .force_analysis import ForceViewerDialog, ConvergenceGraphDialog  # noqa: E402
from .charge_analysis import ChargeDialog  # noqa: E402
from .dipole_analysis import DipoleDialog  # noqa: E402
from .nmr_analysis import NMRDialog  # noqa: E402
from .tddft_analysis import TDDFTDialog  # noqa: E402
from .thermal_analysis import ThermalTableDialog  # noqa: E402
from .scf_analysis import SCFTraceDialog  # noqa: E402

from . import PLUGIN_VERSION  # noqa: E402
import logging  # noqa: E402


class _DirectoryFilePicker(QDialog):
    """Simple dialog that lists *.out files in a directory for selection."""

    def __init__(self, parent, directory: str, filenames: list[str]):
        super().__init__(parent)
        self.directory = directory
        self.selected_path: str | None = None

        self.setWindowTitle(
            f"Select GAMESS Output — {os.path.basename(directory)} ({len(filenames)} files)"
        )
        self.resize(500, 380)

        layout = QVBoxLayout(self)

        lbl = QLabel(
            f"<b>{len(filenames)}</b> file(s) found in:<br><small>{directory}</small>"
        )
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        for name in filenames:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, os.path.join(directory, name))
            self._list.addItem(item)
        self._list.itemDoubleClicked.connect(self._accept_item)
        layout.addWidget(self._list)

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("Open")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._accept_selection)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addStretch()
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

        if filenames:
            self._list.setCurrentRow(0)

    def _accept_item(self, item: QListWidgetItem):
        self.selected_path = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def _accept_selection(self):
        item = self._list.currentItem()
        if item:
            self.selected_path = item.data(Qt.ItemDataRole.UserRole)
            self.accept()


def build_status_suffix(data: dict) -> tuple[str, int]:
    """Suffix for the status label: imaginary-mode count.

    Returns (suffix, imaginary_count). suffix is "" when there is no
    frequency data or no imaginary modes. Uses the parser's noise threshold,
    as the thermochemistry count does, so an unprojected -3 cm-1 rotation
    does not flag a minimum as a saddle point.
    """
    freqs = data.get("frequencies") or []
    if not freqs:
        return "", 0
    imag = [f for f in freqs if f.get("freq", 0) < -IMAGINARY_FREQ_THRESHOLD]
    if not imag:
        return "", 0
    n = len(imag)
    label = "imaginary mode" if n == 1 else "imaginary modes"
    return f" ({n} {label})", n


class GAMESSResultAnalyzerDialog(QDialog):
    """Main plugin window: loads GAMESS output, drives the 3D view and analysis dialogs."""

    # pylint: disable=attribute-defined-outside-init,access-member-before-definition
    # Qt pattern: widget attributes are set in init_ui(), called from __init__.

    def __init__(self, parent, parser, file_path, context=None):
        super().__init__(parent)
        self.mw = parent
        self.parser = parser
        self.file_path = file_path
        self.context = context

        self.setWindowTitle(f"GAMESS Result Analyzer (v{PLUGIN_VERSION})")
        self.resize(450, 600)
        self.setAcceptDrops(True)  # enable folder/file drag-and-drop
        self._positioned = False
        self.init_ui()

        # Install global picking logic
        self._click_filter = None
        self._enable_plotter_picking()

        # Only for a real file: an empty analyzer must not blank out the
        # name of whatever the main window already has open.
        if self.file_path:
            host = context.get_main_window() if context is not None else self.mw
            sync_main_window_file(host, self.file_path, context)

    def showEvent(self, event):
        """Shift the window right of the WM's default centered position on first show."""
        super().showEvent(event)
        # Shift right of the WM's default center-on-parent placement; only
        # on first show so a later existing.show() (re-raising an
        # already-open window) doesn't snap it back after the user has
        # dragged it elsewhere.
        if not self._positioned:
            self._positioned = True
            try:
                self.move(self.x() + 600, self.y())
            except (RuntimeError, AttributeError) as e:
                logging.warning(
                    "Could not shift the analyzer window position on first show: %s", e
                )

    def get_icon(self, name):
        """Helper to load icon from icon directory"""
        icon_path = os.path.join(os.path.dirname(__file__), "icon", name)
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        return QIcon()

    # ------------------------------------------------------------------
    # Drag-and-drop: folder → Select from Directory, .out → load direct
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        """Accept a dragged folder or .out file, reject anything else."""
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                local = url.toLocalFile()
                if os.path.isdir(local) or local.lower().endswith((".log", ".out")):
                    accept_drop_as_copy(event)
                    return
        event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        """Open a dropped folder via the file picker, or load a dropped .out directly."""
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if os.path.isdir(local):
                open_it = self._open_directory_path
            elif local.lower().endswith((".log", ".out")) and os.path.isfile(local):
                open_it = self.load_file
            else:
                continue
            # After the drop returns: opening can show a modal picker or message,
            # and the file manager stays frozen until this handler ends.
            if accept_drop_as_copy(event):
                QTimer.singleShot(0, self, lambda p=local, f=open_it: f(p))
            return
        event.ignore()

    def _open_directory_path(self, directory: str):
        """Run the Select-from-Directory picker for a specific *directory*."""
        found = list_gamess_output_files(directory)
        if not found:
            QMessageBox.information(
                self,
                "No Files Found",
                f"No GAMESS output files (*.log, *.out) found in:\n{directory}",
            )
            return
        if len(found) == 1:
            self.load_file(os.path.join(directory, found[0]))
            return
        picker = _DirectoryFilePicker(self, directory, found)
        if picker.exec() == QDialog.DialogCode.Accepted and picker.selected_path:
            self.load_file(picker.selected_path)

    def init_ui(self):
        """Build the menu bar, 3D viewer, file info panel and action buttons."""
        self.setStyleSheet(f"GAMESSResultAnalyzerDialog {{ background-color: {THEME_WINDOW}; }}")
        layout = QVBoxLayout(self)

        # Menu Bar (added as widget since QDialog doesn't have native menu bar)
        menu_bar = QMenuBar(self)
        menu_bar.setStyleSheet(
            f"QMenuBar {{ background-color: {THEME_MENU}; padding: 5px; font-size: 11pt; }}"
        )
        layout.addWidget(menu_bar)

        # File Menu
        file_menu = menu_bar.addMenu("&File")

        open_action = QAction("&Select File", self)
        open_action.setShortcut("Ctrl+O")
        open_action.setIcon(self.get_icon("menu_open.svg"))
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        open_dir_action = QAction("Select from &Directory", self)
        open_dir_action.setShortcut("Ctrl+D")
        open_dir_action.setIcon(self.get_icon("menu_open.svg"))
        open_dir_action.triggered.connect(self.open_directory)
        file_menu.addAction(open_dir_action)

        reload_action = QAction("&Reload File", self)
        reload_action.setShortcut("Ctrl+R")
        reload_action.setIcon(self.get_icon("menu_reload.svg"))
        reload_action.triggered.connect(self.reload_file)
        file_menu.addAction(reload_action)

        view_output_action = QAction("Open &Output File", self)
        view_output_action.setShortcut("Ctrl+Shift+O")
        view_output_action.setIcon(self.get_icon("menu_output.svg"))
        view_output_action.triggered.connect(self.open_output_file)
        file_menu.addAction(view_output_action)

        file_menu.addSeparator()

        close_action = QAction("&Close", self)
        close_action.setShortcut("Ctrl+W")
        close_action.setIcon(self.get_icon("menu_close.svg"))
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)

        # Analysis Menu (text-only; mirrors the quick-access buttons below)
        analysis_menu = menu_bar.addMenu("&Analysis")
        analysis_items = [
            ("SCF Trace", self.show_scf_trace),
            ("MO Analysis", self.show_mo_analyzer),
            ("Optimization", self.show_trajectory),
            ("Forces", self.show_forces),
            ("Atomic Charges", self.show_charges),
            ("Dipole Moment", self.show_dipole),
            ("Frequencies", self.show_freq),
            ("Thermochemistry", self.show_thermal),
            ("TD-DFT", self.show_tddft),
            ("NMR", self.show_nmr),
            (None, None),  # separator
            ("Properties", self.show_properties),
            ("Bond Analysis", self.show_bond_analysis),
            ("Energy Components", self.show_energy_components),
        ]
        for label, slot in analysis_items:
            if label is None:
                analysis_menu.addSeparator()
                continue
            act = QAction(label, self)
            act.triggered.connect(slot)
            analysis_menu.addAction(act)

        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About GAMESS Result Analyzer", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        # Current File Display with Open Button
        file_frame = QWidget()
        file_frame.setStyleSheet(f"""
            QWidget {{
                background-color: {THEME_PANEL};
                border: 2px solid {THEME_ACCENT};
                border-radius: 5px;
                padding: 10px;
            }}
        """)
        file_frame_layout = QHBoxLayout(file_frame)
        file_frame_layout.setContentsMargins(10, 10, 10, 10)

        # File info section
        file_info_layout = QVBoxLayout()
        lbl_current = QLabel("<b>Current File:</b>")
        lbl_current.setStyleSheet(
            "font-size: 10pt; background: transparent; border: none; padding: 0;"
        )
        file_info_layout.addWidget(lbl_current)

        self.lbl_file_path = QLabel(os.path.basename(self.file_path))
        self.lbl_file_path.setStyleSheet(
            f"color: {THEME_ACCENT}; font-size: 9pt; font-weight: bold; background: transparent; border: none; padding: 0;"
        )
        self.lbl_file_path.setToolTip(self.file_path)
        self.lbl_file_path.setWordWrap(True)
        file_info_layout.addWidget(self.lbl_file_path)

        self.lbl_file_dir = ElidedLabel(os.path.dirname(self.file_path))
        self.lbl_file_dir.setStyleSheet(
            f"color: {THEME_ACCENT}; font-size: 9pt; background: transparent; border: none; padding: 0;"
        )
        self.lbl_file_dir.setToolTip(self.file_path)
        self.lbl_file_dir.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        file_info_layout.addWidget(self.lbl_file_dir)

        # GAMESS Run Status Label
        status_str = (
            self.parser.data.get("termination_status", "Running")
            if self.parser
            else "Unknown"
        )
        self.lbl_status = QLabel(f"Status: {status_str}")
        self.lbl_status.setStyleSheet(
            "font-size: 9pt; font-weight: bold; background: transparent; border: none; padding: 0;"
        )
        if "Terminated normally" in status_str:
            self.lbl_status.setStyleSheet(
                "color: #28a745; font-size: 9pt; font-weight: bold; background: transparent; border: none; padding: 0;"
            )
        elif "Running" in status_str:
            self.lbl_status.setStyleSheet(
                "color: #fd7e14; font-size: 9pt; font-weight: bold; background: transparent; border: none; padding: 0;"
            )
        else:
            self.lbl_status.setStyleSheet(
                "color: #dc3545; font-size: 9pt; font-weight: bold; background: transparent; border: none; padding: 0;"
            )
        file_info_layout.addWidget(self.lbl_status)

        # Updated Time Label
        self.lbl_updated = QLabel("Updated: ---")
        self.lbl_updated.setStyleSheet(
            "color: #555; font-size: 9pt; background: transparent; border: none; padding: 0;"
        )
        file_info_layout.addWidget(self.lbl_updated)

        # GAMESS Version Label
        version_str = (
            self.parser.data.get("version", "Unknown") if self.parser else "Unknown"
        )
        self.lbl_version = QLabel(f"GAMESS Version: {version_str}")
        self.lbl_version.setStyleSheet(
            "color: #555; font-size: 9pt; background: transparent; border: none; padding: 0;"
        )
        file_info_layout.addWidget(self.lbl_version)

        # Initial call to update labels
        self.update_file_info_labels()

        file_frame_layout.addLayout(file_info_layout, 1)

        # Buttons Layout (Open + Reload)
        btns_top_layout = QVBoxLayout()

        # Large Open File Button
        btn_open_large = QPushButton("Select File")
        btn_open_large.setIcon(self.get_icon("menu_open.svg"))
        btn_open_large.setStyleSheet("""
            QPushButton {
                background-color: #0066cc;
                color: white;
                font-size: 10pt;
                font-weight: bold;
                padding: 8px 15px;
                border-radius: 5px;
                border: none;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #0052a3;
            }
            QPushButton:pressed {
                background-color: #003d7a;
            }
        """)
        btn_open_large.clicked.connect(self.open_file)
        btn_open_large.setToolTip(
            "Select GAMESS Output File\nShift+Click: Select from Directory"
        )
        btns_top_layout.addWidget(btn_open_large)

        # Reload Button
        btn_reload = QPushButton("Reload")
        btn_reload.setIcon(self.get_icon("menu_reload.svg"))
        btn_reload.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                font-size: 10pt;
                font-weight: bold;
                padding: 8px 15px;
                border-radius: 5px;
                border: none;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #218838;
            }
            QPushButton:pressed {
                background-color: #1e7e34;
            }
        """)
        btn_reload.clicked.connect(self.reload_file)
        btn_reload.setToolTip("Reload the currently opened GAMESS output file")
        btns_top_layout.addWidget(btn_reload)

        # Open Output Button
        self.btn_view_output = QPushButton("Open Output")
        self.btn_view_output.setIcon(self.get_icon("menu_output.svg"))
        self.btn_view_output.setStyleSheet("""
            QPushButton {
                background-color: #ffaa00;
                color: white;
                font-size: 10pt;
                font-weight: bold;
                padding: 8px 15px;
                border-radius: 5px;
                border: none;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #ff9900;
            }
            QPushButton:pressed {
                background-color: #e68a00;
            }
        """)
        self.btn_view_output.clicked.connect(self.open_output_file)
        self.btn_view_output.setToolTip(
            "Open the raw GAMESS output file in the text viewer"
        )
        btns_top_layout.addWidget(self.btn_view_output)

        file_frame_layout.addLayout(btns_top_layout)

        layout.addWidget(file_frame)

        # Grid for buttons
        grid = QGridLayout()
        grid.setSpacing(12)
        layout.addLayout(grid)

        # Button style for all analysis buttons
        button_style = """
            QPushButton {
                background-color: #ffffff;
                border: 2px solid #cccccc;
                border-radius: 8px;
                padding: 12px 15px;
                font-size: 10pt;
                font-weight: bold;
                text-align: left;
                min-height: 40px;
            }
            QPushButton:hover {
                background-color: #f0f8ff;
                border: 2px solid #0066cc;
            }
            QPushButton:pressed {
                background-color: #e0e8ff;
            }
            QPushButton:disabled {
                background-color: #f0f0f0;
                border: 2px solid #dddddd;
                color: #999999;
            }
        """

        icon_size = QSize(32, 32)

        # Row 0: Electronic Structure
        self.btn_scf = QPushButton("SCF Trace")
        self.btn_scf.setIcon(self.get_icon("icon_scf.svg"))
        self.btn_scf.setIconSize(icon_size)
        self.btn_scf.setStyleSheet(button_style)
        self.btn_scf.clicked.connect(self.show_scf_trace)
        grid.addWidget(self.btn_scf, 0, 0)

        self.btn_mo = QPushButton("MO Analysis")
        self.btn_mo.setIcon(self.get_icon("icon_mo.svg"))
        self.btn_mo.setIconSize(icon_size)
        self.btn_mo.setStyleSheet(button_style)
        self.btn_mo.clicked.connect(self.show_mo_analyzer)
        grid.addWidget(self.btn_mo, 0, 1)

        # Row 1: Geometry Trajectory
        self.btn_traj = QPushButton("Optimization")
        self.btn_traj.setIcon(self.get_icon("icon_traj.svg"))
        self.btn_traj.setIconSize(icon_size)
        self.btn_traj.setStyleSheet(button_style)
        self.btn_traj.clicked.connect(self.show_trajectory)
        grid.addWidget(self.btn_traj, 1, 0)

        self.btn_forces = QPushButton("Forces")
        self.btn_forces.setIcon(self.get_icon("icon_forces.svg"))
        self.btn_forces.setIconSize(icon_size)
        self.btn_forces.setStyleSheet(button_style)
        # Shift+click → open convergence graph directly; plain click → force viewer
        self.btn_forces.clicked.connect(self.show_forces)
        grid.addWidget(self.btn_forces, 1, 1)

        # Row 2: Atomic Properties
        self.btn_charge = QPushButton("Atomic Charges")
        self.btn_charge.setIcon(self.get_icon("icon_charge.svg"))
        self.btn_charge.setIconSize(icon_size)
        self.btn_charge.setStyleSheet(button_style)
        self.btn_charge.clicked.connect(self.show_charges)
        grid.addWidget(self.btn_charge, 2, 0)

        self.btn_dipole = QPushButton("Dipole Moment")
        self.btn_dipole.setIcon(self.get_icon("icon_dipole.svg"))
        self.btn_dipole.setIconSize(icon_size)
        self.btn_dipole.setStyleSheet(button_style)
        self.btn_dipole.clicked.connect(self.show_dipole)
        grid.addWidget(self.btn_dipole, 2, 1)

        # Row 3: Vibrations & Thermodynamics
        self.btn_freq = QPushButton("Frequencies")
        self.btn_freq.setIcon(self.get_icon("icon_freq.svg"))
        self.btn_freq.setIconSize(icon_size)
        self.btn_freq.setStyleSheet(button_style)
        self.btn_freq.clicked.connect(self.show_freq)
        grid.addWidget(self.btn_freq, 3, 0)

        self.btn_therm = QPushButton("Thermochemistry")
        self.btn_therm.setIcon(self.get_icon("icon_therm.svg"))
        self.btn_therm.setIconSize(icon_size)
        self.btn_therm.setStyleSheet(button_style)
        self.btn_therm.clicked.connect(self.show_thermal)
        grid.addWidget(self.btn_therm, 3, 1)

        # Row 4: Advanced Spectroscopy
        self.btn_tddft = QPushButton("TDDFT")
        self.btn_tddft.setIcon(self.get_icon("icon_tddft.svg"))
        self.btn_tddft.setIconSize(icon_size)
        self.btn_tddft.setStyleSheet(button_style)
        self.btn_tddft.clicked.connect(self.show_tddft)
        grid.addWidget(self.btn_tddft, 4, 0)

        self.btn_nmr = QPushButton("NMR")
        self.btn_nmr.setIcon(self.get_icon("icon_nmr.svg"))
        self.btn_nmr.setIconSize(icon_size)
        self.btn_nmr.setStyleSheet(button_style)
        self.btn_nmr.clicked.connect(self.show_nmr)
        grid.addWidget(self.btn_nmr, 4, 1)

        layout.addStretch()

        # Close button only (Open is now in menu)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        btn_layout.addWidget(btn_close)

        layout.addLayout(btn_layout)

        self.update_button_states()

    def _enable_plotter_picking(self):
        """Install Qt event filter on the 3D plotter widget for atom click detection."""
        try:
            if not hasattr(self, "mw"):
                return
            v3d = getattr(self.mw, "view_3d_manager", None)
            if not v3d:
                return
            plotter = getattr(v3d, "plotter", None)
            if not plotter:
                return
            self._click_filter = _ClickFilter(
                self._on_plotter_click,
                press_callback=self._on_plotter_press,
                parent=self,
            )
            plotter.installEventFilter(self._click_filter)
        except (RuntimeError, AttributeError, KeyError, ValueError) as e:
            logging.error("GUI _enable_plotter_picking failed: %s", e)

    def _disable_plotter_picking(self):
        """Remove the event filter from the 3D plotter widget."""
        try:
            if not hasattr(self, "mw"):
                return
            v3d = getattr(self.mw, "view_3d_manager", None)
            plotter = getattr(v3d, "plotter", None) if v3d else None
            if plotter and self._click_filter:
                plotter.removeEventFilter(self._click_filter)
        except (RuntimeError, AttributeError, KeyError, ValueError) as e:
            logging.debug(
                "Could not remove the atom-picking event filter from the 3D plotter: %s",
                e,
            )
        self._click_filter = None

    def _pick_atom_at(self, x, y, widget):
        """Run VTK pick at (x, y) and return the closest atom index, or None."""
        try:
            import vtk
            import numpy as np

            if not hasattr(self, "mw"):
                return None
            v3d = getattr(self.mw, "view_3d_manager", None)
            if not v3d:
                return None
            plotter = getattr(v3d, "plotter", None)
            if not plotter:
                return None
            atom_actor = getattr(v3d, "atom_actor", None)
            if atom_actor is None:
                return None
            atom_positions = getattr(v3d, "atom_positions_3d", None)
            if atom_positions is None or len(atom_positions) == 0:
                return None
            # Scale Qt logical px → VTK physical px for HiDPI/Retina (macOS
            # devicePixelRatio 2); without it the pick lands toward the bottom-
            # left and you must click up-and-right. No-op on Windows/Linux.
            ratio = widget.devicePixelRatioF()
            px = x * ratio
            vtk_y = (widget.height() - y) * ratio
            picker = vtk.vtkCellPicker()  # pylint: disable=no-member
            picker.SetTolerance(0.005)
            picker.Pick(px, vtk_y, 0, plotter.renderer)
            if picker.GetActor() is not atom_actor:
                return None
            pick_pos = picker.GetPickPosition()
            diffs = atom_positions - np.array(pick_pos)
            return int(np.argmin((diffs**2).sum(axis=1)))
        # C++ library boundary: RDKit/VTK/pyvista exceptions do not map to Python types
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.error("GUI _pick_atom_at error: %s", e)
            return None

    def _on_plotter_press(self, x, y, widget):
        self._pending_click_atom = None
        try:
            best_idx = self._pick_atom_at(x, y, widget)
            if best_idx is None:
                return
            self._pending_click_atom = best_idx
        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.error("GUI press handler error: %s", e)

    def _on_plotter_click(self, x, y, widget):  # pylint: disable=unused-argument
        # x/y/widget are unused: fixed _ClickFilter callback signature, position was
        # already captured by _on_plotter_press.
        try:
            best_idx = getattr(self, "_pending_click_atom", None)
            self._pending_click_atom = None
            if not hasattr(self, "mw"):
                return
            e3d = getattr(self.mw, "edit_3d_manager", None)
            if not e3d:
                return

            if best_idx is None:
                # Clicked empty space -> clear selection
                e3d.selected_atoms_3d.clear()
                if hasattr(e3d, "update_selection_visuals"):
                    e3d.update_selection_visuals()
                return

            modifiers = QApplication.keyboardModifiers()
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                if best_idx in e3d.selected_atoms_3d:
                    e3d.selected_atoms_3d.remove(best_idx)
                else:
                    e3d.selected_atoms_3d.add(best_idx)
            else:
                if (
                    best_idx in e3d.selected_atoms_3d
                    and len(e3d.selected_atoms_3d) == 1
                ):
                    e3d.selected_atoms_3d.clear()
                else:
                    e3d.selected_atoms_3d = {best_idx}

            if hasattr(e3d, "update_selection_visuals"):
                e3d.update_selection_visuals()
        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.error("GUI click handler error: %s", e)

    def close_all_sub_dialogs(self):
        """Close all tracked analysis dialogs to prevent orphaned windows."""
        dialog_attrs = [
            "mo_dlg",
            "freq_dlg",
            "traj_dlg",
            "forces_dlg",
            "conv_graph_dlg",
            "thermal_dlg",
            "tddft_dlg",
            "dipole_dlg",
            "charges_dlg",
            "nmr_dlg",
            "scf_dlg",
            "props_dlg",
            "bond_dlg",
            "energy_dlg",
        ]
        for attr in dialog_attrs:
            if getattr(self, attr, None) is not None:
                dlg = getattr(self, attr)
                if dlg is not None:
                    try:
                        if dlg.close() is False:
                            return False
                    except (RuntimeError, AttributeError) as e:
                        logging.warning(
                            "Could not close the %s dialog while resetting the document: %s",
                            attr,
                            e,
                        )
                setattr(self, attr, None)
        return True

    def reject(self):
        """Route Esc through close() so closeEvent cleanup always runs."""
        # Esc must run closeEvent cleanup (QDialog.reject only hides)
        self.close()

    def closeEvent(self, event):
        """Close every sub-dialog, drop picking, and deregister the window.

        accept() rather than super().closeEvent(), which would route back
        through reject() -> close() and recurse. Deregistering matters: a
        window left in the registry is re-shown with its event filter gone.
        """
        if not self.close_all_sub_dialogs():
            event.ignore()
            return
        self._disable_plotter_picking()
        if self.context is not None:
            try:
                self.context.register_window("analyzer", None)
            except (RuntimeError, AttributeError) as exc:
                logging.warning("Could not deregister analyzer window: %s", exc)
        event.accept()

    def show_about(self):
        """Show About dialog."""
        QMessageBox.about(
            self,
            "About GAMESS Result Analyzer",
            f"<b>GAMESS Result Analyzer v{PLUGIN_VERSION}</b><br><br>"
            "Analyzer for GAMESS quantum chemistry output files.<br><br>"
            "<b>Author:</b> Hiromichi Yokoyama<br>"
            "<b>License:</b> LGPL-3.0-or-later License<br>"
            '<b>GitHub:</b> <a href="https://github.com/HiroYokoyama/moleditpy_gamess_result_analyzer">https://github.com/HiroYokoyama/moleditpy_gamess_result_analyzer</a>',
        )

    def open_file(self):
        """Prompt for an .out file to load, or the directory picker on Shift+click."""
        # Shift+click → open directory picker instead
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.open_directory()
            return
        # Get last directory from current file
        start_dir = os.path.dirname(self.file_path) if self.file_path else ""

        path, _ = QFileDialog.getOpenFileName(
            self, "Open GAMESS Output", start_dir, "GAMESS Output (*.log *.out);;All Files (*)"
        )
        if not path:
            return

        self.load_file(path)

    def load_file(self, path):
        """Load a file and update UI"""
        # Close existing dialogs to prevent confusion
        if not self.close_all_sub_dialogs():
            return

        # New result loaded — any atom colors applied to the previous
        # molecule's indices must not bleed onto this (possibly
        # differently-indexed) one.
        clear_atom_color_overrides(self.mw)

        try:
            new_parser = load_gamess_parser(path, self)
            if new_parser is None:  # cancelled by the user
                notify(self, "Loading cancelled", 3000)
                return

            self.parser = new_parser
            self.file_path = path

            # --- Sync with Main Window Title ---
            sync_main_window_file(self.mw, path, self.context)

            # Update File Info Labels
            self.update_file_info_labels()

            # Auto-load 3D structure. This is a fresh file load, so frame the
            # molecule once (fit_camera=True); later analysis popups won't refit.
            self.load_structure_3d(fit_camera=True)
            self.update_button_states()

            notify(self, f"Successfully loaded: {os.path.basename(path)}", 5000)

        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Error", f"Failed to load file:\n{e}")

    def update_file_info_labels(self):
        """Update the file info labels (Path, Time, Versions)"""
        if getattr(self, "lbl_file_path", None) is None:
            return

        self.lbl_file_path.setText(os.path.basename(self.file_path))
        self.lbl_file_path.setToolTip(self.file_path)
        if getattr(self, "lbl_file_dir", None) is not None:
            self.lbl_file_dir.setText(os.path.dirname(self.file_path))
            self.lbl_file_dir.setToolTip(self.file_path)

        # Updated Time
        mtime_str = "---"
        if self.file_path and os.path.exists(self.file_path):
            try:
                dt = datetime.fromtimestamp(os.path.getmtime(self.file_path))
                mtime_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except OSError as e:
                logging.warning(
                    "Could not read the modification time of %s: %s", self.file_path, e
                )

        if getattr(self, "lbl_updated", None) is not None:
            self.lbl_updated.setText(f"Updated: {mtime_str}")

        # GAMESS Version
        v = self.parser.data.get("version", "Unknown") if self.parser else "Unknown"
        if getattr(self, "lbl_version", None) is not None:
            self.lbl_version.setText(f"GAMESS Version: {v}")

        # GAMESS Run Status
        if not self.file_path:
            status = "No file loaded"
        else:
            status = (
                self.parser.data.get("termination_status", "Running")
                if self.parser
                else "Unknown"
            )
        if getattr(self, "lbl_status", None) is not None:
            suffix, imag_count = (
                build_status_suffix(self.parser.data) if self.parser else ("", 0)
            )
            self.lbl_status.setText(f"Status: {status}{suffix}")
            if "Terminated normally" in status and imag_count > 0:
                self.lbl_status.setStyleSheet(
                    "color: #fd7e14; font-size: 9pt; font-weight: bold; background: transparent; border: none; padding: 0;"
                )
            elif "Terminated normally" in status:
                self.lbl_status.setStyleSheet(
                    "color: #28a745; font-size: 9pt; font-weight: bold; background: transparent; border: none; padding: 0;"
                )
            elif "Running" in status:
                self.lbl_status.setStyleSheet(
                    "color: #fd7e14; font-size: 9pt; font-weight: bold; background: transparent; border: none; padding: 0;"
                )
            elif "No file loaded" in status:
                self.lbl_status.setStyleSheet(
                    "color: #6c757d; font-size: 9pt; font-weight: bold; background: transparent; border: none; padding: 0;"
                )
            else:
                self.lbl_status.setStyleSheet(
                    "color: #dc3545; font-size: 9pt; font-weight: bold; background: transparent; border: none; padding: 0;"
                )

    def reload_file(self):
        """Re-parse and refresh the view from the currently loaded file's path."""
        if self.file_path and os.path.exists(self.file_path):
            self.load_file(self.file_path)
        else:
            QMessageBox.warning(
                self, "Error", "No file currently loaded or file not found."
            )

    def open_output_file(self):
        """Open the loaded .out file in the OS's default text viewer."""
        if self.file_path and os.path.exists(self.file_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.file_path))
        else:
            QMessageBox.warning(
                self, "Error", "No output file path available or file does not exist."
            )

    def open_directory(self):
        """Scan a directory for GAMESS .out files and let the user pick one."""
        start_dir = os.path.dirname(self.file_path) if self.file_path else ""
        chosen_dir = QFileDialog.getExistingDirectory(
            self, "Select from Directory", start_dir
        )
        if not chosen_dir:
            return
        self._open_directory_path(chosen_dir)

    def update_button_states(self):
        """Enable/disable each analysis button based on what the parser found."""
        data = self.parser.data

        # Enable MO button if MO coefficients OR orbital energies exist
        # Strict check: must be non-empty dict or list
        mo_coeffs = data.get("mo_coeffs", {})
        orb_energies = data.get("orbital_energies", [])

        has_mo = bool(mo_coeffs) or (bool(orb_energies) and len(orb_energies) > 0)
        self.btn_mo.setEnabled(has_mo)
        tooltip = ""
        if not has_mo:
            tooltip = "No MO data found"
        elif orb_energies and not mo_coeffs:
            tooltip = "Orbital energies available (no coefficients for visualization)"
        self.btn_mo.setToolTip(tooltip)

        freqs = data.get("frequencies", [])
        has_freq = bool(freqs and len(freqs) > 0)
        self.btn_freq.setEnabled(has_freq)
        self.btn_freq.setToolTip("" if has_freq else "No frequency data found")

        # Scan / Optimization
        scan_steps = data.get("scan_steps", [])
        has_scan = bool(scan_steps and len(scan_steps) > 0)
        self.btn_traj.setEnabled(has_scan)
        self.btn_traj.setToolTip("" if has_scan else "No trajectory/scan steps found")
        # Enable Forces button if gradients exist or scan exists
        grads = data.get("gradients", [])
        scan_steps = data.get("scan_steps", [])
        has_forces = bool(grads or scan_steps)

        self.btn_forces.setEnabled(has_forces)
        tooltip = ""
        if not has_forces:
            tooltip = "No gradients or optimization trajectory found"
        elif grads:
            tooltip = (
                "View Forces (Gradients)\nShift+Click: Open convergence graph directly"
            )
        elif scan_steps:
            tooltip = (
                "View trajectory steps\nShift+Click: Open convergence graph directly"
            )
        self.btn_forces.setToolTip(tooltip)

        self.btn_therm.setEnabled(bool(data.get("thermal")))

        has_tddft = bool(data.get("tddft"))
        self.btn_tddft.setEnabled(has_tddft)

        has_dipole = bool(data.get("dipoles"))
        self.btn_dipole.setEnabled(has_dipole)

        has_charges = bool(data.get("charges"))
        self.btn_charge.setEnabled(has_charges)

        has_nmr = bool(data.get("nmr_shielding"))
        self.btn_nmr.setEnabled(has_nmr)

        # SCF Trace
        has_scf = bool(data.get("scf_traces"))
        self.btn_scf.setEnabled(has_scf)
        self.btn_scf.setToolTip("" if has_scf else "No SCF iteration data found")

    def load_structure_3d(self, fit_camera=False):
        """Build an RDKit mol from the parsed geometry and draw it in the host's 3D view."""
        # Helper to ensure the 3D structure is (re)drawn.
        #
        # Analysis dialogs redraw the optimized/final molecule on popup so the
        # 3D view always matches the data being shown (accurate result), but
        # they must NOT refit the camera — the user's current zoom and
        # orientation are preserved. Only an explicit file load passes
        # fit_camera=True to frame a freshly loaded molecule.
        atoms = self.parser.data.get("atoms", [])
        coords = self.parser.data.get("coords", [])

        if not atoms or not coords:
            return

        if not Chem:
            return

        try:
            mol = Chem.RWMol()  # pylint: disable=no-member
            conf = Chem.Conformer(len(atoms))  # pylint: disable=no-member

            for i, sym in enumerate(atoms):
                # pylint: disable-next=no-member
                idx = mol.AddAtom(Chem.Atom(normalize_atom_symbol(sym)))
                x, y, z = coords[i]
                conf.SetAtomPosition(idx, Point3D(x, y, z))

            mol.AddConformer(conf)

            # Bond determination must run on the RWMol (mutable) before GetMol().
            # DetermineBonds on a read-only Mol silently fails.
            # Skip if any animation is currently playing (traj or freq) — the result
            # would be immediately overwritten and the cost is wasted.
            _traj_playing = getattr(self, "traj_dlg", None) is not None and getattr(
                self.traj_dlg, "is_playing", False
            )
            _freq_playing = getattr(self, "freq_dlg", None) is not None and getattr(
                self.freq_dlg, "is_playing", False
            )
            if not (_traj_playing or _freq_playing):
                charge = self.parser.data.get("charge", 0)
                # determine_bonds_without_dummies excludes dummy ('*') atoms
                # from the sub-molecule passed to RDKit, preventing crashes
                # when QM point charges or ghost atoms are present.
                determine_bonds_without_dummies(mol, charge=charge, bond_orders=True)

            final_mol = mol.GetMol()

            # Set as current molecule for export functionality
            if hasattr(self.mw, "current_mol"):
                self.mw.current_mol = final_mol

            self.context.draw_molecule_3d(final_mol)

            # Sync with main window features
            if hasattr(self.mw, "ui_manager"):
                # Treat GAMESS molecules as XYZ-derived (fixed geometry)
                self.mw.is_xyz_derived = True

                # Enter 3D mode which enables export/analysis buttons and hides 2D panel
                if hasattr(self.context, "enter_3d_viewer_mode"):
                    self.context.enter_3d_viewer_mode()
                elif hasattr(self.mw.ui_manager, "_enter_3d_viewer_ui_mode"):
                    self.mw.ui_manager._enter_3d_viewer_ui_mode()
                else:
                    self.context.set_3d_features_enabled(True)
                    if hasattr(self.mw.ui_manager, "minimize_2d_panel"):
                        self.mw.ui_manager.minimize_2d_panel()

            # Fit the camera only on an explicit file load. On analysis-dialog
            # popups (fit_camera=False) the molecule is redrawn above but the
            # camera is left untouched, preserving the user's zoom/orientation.
            if fit_camera:
                try:
                    self.context.reset_3d_camera()
                    if hasattr(self.mw, "view_3d_manager") and hasattr(
                        self.mw.view_3d_manager, "plotter"
                    ):
                        self.mw.view_3d_manager.plotter.render()
                except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                    logging.warning("3D camera/render update failed: %s", e)
            elif hasattr(self.mw, "view_3d_manager") and hasattr(
                self.mw.view_3d_manager, "plotter"
            ):
                # Still render so the redrawn structure is shown immediately.
                try:
                    self.mw.view_3d_manager.plotter.render()
                except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                    logging.warning("3D render update failed: %s", e)
        # C++ library boundary: RDKit/VTK/pyvista exceptions do not map to Python types
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.error(
                "[gui.py:load_structure_3d] Failed to load 3D structure: %s",
                e,
                exc_info=True,
            )

    def show_mo_analyzer(self):
        """Open the MO Analyzer dialog on the parsed orbital coefficients/energies."""
        self.load_structure_3d()
        mo_coeffs = self.parser.data.get("mo_coeffs", None)
        orb_energies = self.parser.data.get("orbital_energies", None)

        data_to_show = mo_coeffs if mo_coeffs else orb_energies

        if not data_to_show:
            QMessageBox.warning(
                self, "No Data", "No Molecular Orbital coefficients or energies found."
            )
            return

        if getattr(self, "mo_dlg", None) is not None and self.mo_dlg is not None:
            self.mo_dlg.close()

        self.mo_dlg = MODialog(self, data_to_show)
        self.mo_dlg.show()

    def show_freq(self):
        """Open the Frequency dialog on the parsed vibrational modes."""
        self.load_structure_3d()  # Reset to final structure before opening
        freqs = self.parser.data.get("frequencies", [])
        if not freqs:
            QMessageBox.warning(self, "No Data", "No frequency data found.")
            return
        atoms = self.parser.data.get("atoms", [])
        coords = self.parser.data.get("coords", [])

        # Ensure only one instance is open
        if getattr(self, "freq_dlg", None) is not None and self.freq_dlg is not None:
            self.freq_dlg.close()

        self.freq_dlg = FrequencyDialog(
            self.mw, freqs, atoms, coords, context=self.context
        )
        self.freq_dlg.show()

    def show_trajectory(self):
        """Open the trajectory dialog on the parsed optimization or scan steps."""
        data = self.parser.data.get("scan_steps", [])
        if not data:
            QMessageBox.warning(
                self, "No Info", "No trajectory steps (Optimization / Scan) found."
            )
            return
        if getattr(self, "traj_dlg", None) is not None and self.traj_dlg is not None:
            self.traj_dlg.close()
        charge = self.parser.data.get("charge", 0)
        base_dir = os.path.dirname(self.file_path) if self.file_path else None
        self.traj_dlg = TrajectoryResultDialog(
            self.mw,
            data,
            charge=charge,
            title="Optimization Analysis",
            base_dir=base_dir,
            output_path=self.file_path,
            context=self.context,
        )
        self.traj_dlg.show()

    def show_convergence_graph_direct(self):
        """Open the convergence graph directly (Shift+click shortcut), without the force viewer."""
        scan_steps = self.parser.data.get("scan_steps", [])
        if not scan_steps:
            QMessageBox.warning(
                self, "No Convergence Data", "No optimization trajectory steps found."
            )
            return
        # Find the current step index based on which step has data
        has_conv = [bool(s.get("convergence")) for s in scan_steps]
        current_idx = next(
            (len(scan_steps) - 1 - i for i, v in enumerate(reversed(has_conv)) if v),
            0,
        )
        if getattr(self, "conv_graph_dlg", None) is not None:
            try:
                self.conv_graph_dlg.close()
            except (RuntimeError, AttributeError):
                logging.debug(
                    "Closing previous convergence graph failed", exc_info=True
                )
        self.conv_graph_dlg = ConvergenceGraphDialog(self, scan_steps, current_idx)
        self.conv_graph_dlg.show()

    def show_forces(self):
        """Open the Force Viewer, or the convergence graph directly on Shift+click."""
        # Shift+click → open convergence graph directly instead
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.show_convergence_graph_direct()
            return

        grads = self.parser.data.get("gradients", [])
        has_scan = bool(self.parser.data.get("scan_steps"))

        if not grads and not has_scan:
            QMessageBox.warning(
                self, "No Info", "No cartesian gradients or optimization steps found."
            )
            return

        if (
            getattr(self, "forces_dlg", None) is not None
            and self.forces_dlg is not None
        ):
            self.forces_dlg.close()
        # Open Force Viewer with trajectory support
        self.forces_dlg = ForceViewerDialog(self, grads, parser=self.parser)
        self.forces_dlg.show()

    def show_thermal(self):
        """Open the Thermochemistry dialog on the parsed thermal data."""
        self.load_structure_3d()
        data = self.parser.data.get("thermal", {})
        if not data:
            QMessageBox.warning(self, "No Info", "No thermochemistry section found.")
            return
        if (
            getattr(self, "thermal_dlg", None) is not None
            and self.thermal_dlg is not None
        ):
            self.thermal_dlg.close()
        self.thermal_dlg = ThermalTableDialog(self, data)
        self.thermal_dlg.show()

    def show_tddft(self):
        """Open the TD-DFT dialog on the parsed excitation energies."""
        self.load_structure_3d()
        excitations = self.parser.data.get("tddft", [])
        if not excitations:
            QMessageBox.warning(
                self, "No Analysis", "No TDDFT/TDA excitation energies found."
            )
            return

        if getattr(self, "tddft_dlg", None) is not None and self.tddft_dlg is not None:
            self.tddft_dlg.close()

        self.tddft_dlg = TDDFTDialog(self, excitations)
        self.tddft_dlg.show()

    def show_dipole(self):
        """Open the Dipole Moment dialog on the parsed dipole data."""
        self.load_structure_3d()
        d = self.parser.data.get("dipoles", None)
        if not d:
            QMessageBox.warning(self, "No Info", "No dipole moment found.")
            return
        if (
            getattr(self, "dipole_dlg", None) is not None
            and self.dipole_dlg is not None
        ):
            self.dipole_dlg.close()
        self.dipole_dlg = DipoleDialog(self, d)
        self.dipole_dlg.show()

    def show_charges(self):
        """Open the Atomic Charges dialog on the parsed charge schemes."""
        self.load_structure_3d()
        charges = self.parser.data.get("charges", {})
        if not charges:
            QMessageBox.warning(self, "No Info", "No atomic charges found.")
            return
        if (
            getattr(self, "charges_dlg", None) is not None
            and self.charges_dlg is not None
        ):
            self.charges_dlg.close()
        self.charges_dlg = ChargeDialog(self, charges)
        self.charges_dlg.show()

    def show_nmr(self):
        """Open the NMR dialog on the parsed shielding and coupling data."""
        self.load_structure_3d()
        data = self.parser.data.get("nmr_shielding", [])
        if not data:
            QMessageBox.warning(
                self, "No Info", "No NMR chemical shielding data found."
            )
            return
        if getattr(self, "nmr_dlg", None) is not None and self.nmr_dlg is not None:
            if self.nmr_dlg.close() is False:
                return
        self.nmr_dlg = NMRDialog(
            self, data, file_path=self.file_path
        )
        self.nmr_dlg.show()


    def show_scf_trace(self):
        """Open the SCF Trace dialog on the parsed convergence iterations."""
        self.load_structure_3d()
        data = self.parser.data.get("scf_traces", [])
        if not data:
            QMessageBox.warning(self, "No Info", "No SCF energy trace data found.")
            return
        if getattr(self, "scf_dlg", None) is not None and self.scf_dlg is not None:
            self.scf_dlg.close()
        self.scf_dlg = SCFTraceDialog(
            self,
            data,
            dispersion=self.parser.data.get("dispersion"),
            spin_s2=self.parser.data.get("spin_s2"),
        )
        self.scf_dlg.show()

    def show_properties(self):
        """Open the general Properties dialog on the full parsed data dict."""
        from .property_analysis import PropertiesDialog

        if getattr(self, "props_dlg", None) is not None:
            try:
                self.props_dlg.close()
            except (RuntimeError, AttributeError) as e:
                logging.warning("Could not close the existing properties dialog: %s", e)
        self.props_dlg = PropertiesDialog(self, self.parser.data)
        self.props_dlg.show()

    def show_bond_analysis(self):
        """Open the Bond Analysis dialog on the Mayer bond orders."""
        from .bond_analysis import BondAnalysisDialog

        data = self.parser.data
        if not data.get("mayer_bond_orders"):
            QMessageBox.warning(self, "No Info", "No bond-analysis data found.")
            return
        if getattr(self, "bond_dlg", None) is not None:
            try:
                self.bond_dlg.close()
            except (RuntimeError, AttributeError) as e:
                logging.warning(
                    "Could not close the existing bond-analysis dialog: %s", e
                )
        self.bond_dlg = BondAnalysisDialog(self, data)
        self.bond_dlg.show()

    def show_energy_components(self):
        """Open the Energy Components dialog on the parsed post-HF energy terms."""
        from .energy_analysis import EnergyComponentsDialog

        if not self.parser.data.get("energy_components"):
            QMessageBox.information(
                self,
                "Energy Components",
                "No post-HF energy components found (HF/DFT result).",
            )
            return
        if getattr(self, "energy_dlg", None) is not None:
            try:
                self.energy_dlg.close()
            except (RuntimeError, AttributeError) as e:
                logging.warning(
                    "Could not close the existing energy-components dialog: %s", e
                )
        self.energy_dlg = EnergyComponentsDialog(self, self.parser.data)
        self.energy_dlg.show()
