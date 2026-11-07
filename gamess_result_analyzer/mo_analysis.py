"""MO Analyzer dialog: orbital list, cube generation/visualization and export."""

from ._qt_callbacks import connect_queued

import csv
import os
import tempfile
import numpy as np
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QAbstractItemView,
    QMessageBox,
    QFileDialog,
    QProgressDialog,
    QHeaderView,
    QGroupBox,
    QSpinBox,
    QDoubleSpinBox,
    QFormLayout,
    QTreeWidgetItemIterator,
    QApplication,
    QColorDialog,
    QInputDialog,
    QComboBox,
    QCheckBox,
    QMenu,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from .utils import get_default_export_path, notify
from .settings import load_section, save_section
import logging

try:
    from .mo_engine import (
        CANCELLED,
        BasisSetEngine,
        CalcWorker,
        UnsupportedBasisError,
        read_generation_settings,
    )
    from .vis import CubeVisualizer
except ImportError:
    try:
        from mo_engine import (
            CANCELLED,
            BasisSetEngine,
            CalcWorker,
            UnsupportedBasisError,
            read_generation_settings,
        )
        from vis import CubeVisualizer
    except ImportError:
        CANCELLED = "Cancelled"
        BasisSetEngine = None
        CalcWorker = None
        CubeVisualizer = None
        read_generation_settings = None

        class UnsupportedBasisError(ValueError):
            """Stand-in so the except clause stays valid without the engine."""


try:
    from .energy_diag import EnergyDiagramDialog
except ImportError:
    EnergyDiagramDialog = None

try:
    from .mo_compare import MOCompareDialog
except ImportError:
    MOCompareDialog = None


# Retain threads until QThread.finished, which follows the result signal.
_RUNNING_WORKERS = set()


class MODialog(QDialog):
    """Dialog listing MOs, generating their cube files and visualizing them in 3D."""

    # pylint: disable=attribute-defined-outside-init
    # Qt/PyVista pattern: label/actor/state attrs are created lazily, not in __init__.

    def __init__(self, parent, mo_data, result_dir=None):  # pylint: disable=unused-argument
        # result_dir is unused: get_cube_path() re-derives the cube directory from
        # parent_dlg.parser.filename instead.
        super().__init__(parent)
        self.mw = None
        if hasattr(parent, "mw"):
            self.mw = parent.mw
        elif hasattr(parent, "context"):
            self.mw = parent.context.get_main_window()

        self.mo_data = mo_data
        self.setWindowTitle("MO Analysis & Visualization")
        self.resize(550, 750)
        self.mos = mo_data
        self.parent_dlg = parent
        self._is_closing = False
        self.last_cube_path = None
        self.generation_queue = []  # Init queue
        self.generation_force = False  # Overwrite cached cubes for this batch
        self.generation_silent = False  # Batch generates files without redrawing
        self.generation_done_cb = None
        self.energy_dlg = None  # Track Energy Diagram
        self.compare_dlg = None  # Track Compare MOs
        self.setup_ui()

    def get_cube_path(self, display_id):
        """Return the on-disk cube file path for an orbital, if the parser has one."""
        if not hasattr(self.parent_dlg, "parser") or not self.parent_dlg.parser:
            return None

        parser = self.parent_dlg.parser
        if hasattr(parser, "filename") and parser.filename:
            fpath = parser.filename
            base_dir = os.path.dirname(fpath)
            filename_base = os.path.splitext(os.path.basename(fpath))[0]
            out_dir = os.path.join(base_dir, f"{filename_base}_cubes")

            # Just return the path, don't create dir here (lazy creation requested)
            # Sanitize display ID (which might contain "1 (a)")
            # Example: "1 (a)" -> "1_a"
            safe_id = (
                str(display_id)
                .replace(" ", "_")
                .replace("(", "")
                .replace(")", "")
                .replace(":", "")
            )
            # Ensure no double underscores
            while "__" in safe_id:
                safe_id = safe_id.replace("__", "_")

            # User requested prefix: benzene-ene_MO_... (Using filename base)
            return os.path.join(out_dir, f"{filename_base}_MO_{safe_id}.cube")
        return None

    def setup_ui(self):
        """Build the orbital tree, visualization controls and action buttons."""
        # Use simpler Vertical Layout to fill the window
        layout = QVBoxLayout(self)

        # 1. Visualization Settings (Grouping "Grid" vs "Calc")
        vis_grp = QGroupBox("Visualization Controls")
        vis_layout = QVBoxLayout(vis_grp)

        # --- Presets ---
        h_preset = QHBoxLayout()
        h_preset.addWidget(QLabel("Preset:"))
        self.combo_presets = QComboBox()
        self.combo_presets.addItem("Default")
        h_preset.addWidget(self.combo_presets)

        btn_save_preset = QPushButton("Save")
        btn_save_preset.setFixedWidth(50)
        btn_save_preset.clicked.connect(self.save_preset)
        h_preset.addWidget(btn_save_preset)

        btn_del_preset = QPushButton("Del")
        btn_del_preset.setFixedWidth(40)
        btn_del_preset.clicked.connect(self.delete_preset)
        h_preset.addWidget(btn_del_preset)
        vis_layout.addLayout(h_preset)

        # --- Style & Colors ---
        h_style = QHBoxLayout()
        h_style.addWidget(QLabel("Style:"))
        self.combo_style = QComboBox()
        self.combo_style.addItems(["Surface", "Wireframe", "Points"])
        h_style.addWidget(self.combo_style)

        self.check_smooth = QCheckBox("Smooth Shading")
        self.check_smooth.setChecked(True)
        h_style.addWidget(self.check_smooth)
        vis_layout.addLayout(h_style)

        h_colors = QHBoxLayout()
        self.btn_color_p = QPushButton("Pos (+)")
        self.btn_color_p.setStyleSheet(
            "background-color: red; color: white; font-weight: bold;"
        )
        self.btn_color_p.clicked.connect(lambda: self.pick_color("p"))
        h_colors.addWidget(self.btn_color_p)

        self.btn_color_n = QPushButton("Neg (-)")
        self.btn_color_n.setStyleSheet(
            "background-color: blue; color: white; font-weight: bold;"
        )
        self.btn_color_n.clicked.connect(lambda: self.pick_color("n"))
        h_colors.addWidget(self.btn_color_n)
        vis_layout.addLayout(h_colors)

        # Isovalue / Opacity
        h_iso = QHBoxLayout()
        h_iso.addWidget(QLabel("Isovalue:"))
        self.spin_iso = QDoubleSpinBox()
        self.spin_iso.setRange(0.001, 1.0)
        self.spin_iso.setSingleStep(0.005)
        self.spin_iso.setDecimals(3)
        self.spin_iso.setValue(0.02)
        h_iso.addWidget(self.spin_iso)

        h_iso.addWidget(QLabel("Opacity:"))
        self.spin_opacity = QDoubleSpinBox()
        self.spin_opacity.setRange(0.0, 1.0)
        self.spin_opacity.setSingleStep(0.1)
        self.spin_opacity.setValue(0.5)
        h_iso.addWidget(self.spin_opacity)
        vis_layout.addLayout(h_iso)

        # Connect changes to update view immediately if exists
        self.combo_style.currentTextChanged.connect(self.update_vis_only)
        self.check_smooth.toggled.connect(self.update_vis_only)
        self.spin_iso.valueChanged.connect(self.update_vis_only)
        self.spin_opacity.valueChanged.connect(self.update_vis_only)

        # Load Settings must be called after UI init
        self.load_settings()
        self.combo_presets.currentTextChanged.connect(self.apply_preset)

        # Calculation Settings
        calc_grp = QGroupBox("Calculation Parameters")
        calc_layout = QFormLayout(calc_grp)

        self.spin_pts = QSpinBox()
        self.spin_pts.setRange(10, 200)
        self.spin_pts.setValue(40)
        self.spin_pts.setSuffix(" pts")
        calc_layout.addRow("Grid Resolution (x,y,z):", self.spin_pts)

        self.spin_margin = QDoubleSpinBox()
        self.spin_margin.setRange(1.0, 15.0)
        self.spin_margin.setValue(4.0)
        # CalcWorker converts this value from Angstrom; the old " Bohr" label
        # understated the real margin by 1.89x.
        self.spin_margin.setSuffix(" Å")
        calc_layout.addRow("Calc Boundary Margin:", self.spin_margin)

        vis_layout.addWidget(calc_grp)

        # Warning Layout (Label + Copy Button)
        warn_layout = QHBoxLayout()

        self.lbl_warning = QLabel("")
        self.lbl_warning.setStyleSheet(
            "color: #e65100; font-weight: bold; font-size: 9pt;"
        )
        self.lbl_warning.setWordWrap(True)
        self.lbl_warning.setVisible(False)
        warn_layout.addWidget(self.lbl_warning)

        self.btn_copy_input = QPushButton("Copy Input")
        self.btn_copy_input.setFixedWidth(80)
        self.btn_copy_input.setVisible(False)
        self.btn_copy_input.clicked.connect(self.copy_gamess_input)
        warn_layout.addWidget(self.btn_copy_input)

        vis_layout.addLayout(warn_layout)

        layout.addWidget(vis_grp)

        # 2. MO List
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["MO", "Label", "Occ", "E (eV)", "E (Eh)"])
        # Columns: Let them fill
        self.tree.setColumnWidth(0, 120)
        self.tree.setColumnWidth(1, 100)  # Label
        self.tree.setColumnWidth(2, 80)
        self.tree.setColumnWidth(3, 80)
        # Last column stretches
        header = self.tree.header()
        if header:
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self.on_double_click)
        # Connect currentItemChanged for both keyboard navigation and single click
        self.tree.currentItemChanged.connect(self.on_item_changed)
        # Keep selection changed only for button state specific logic if needed,
        # but currentItemChanged covers the "primary" selection change.
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_tree_context_menu)
        layout.addWidget(self.tree)

        # 3. Action Buttons
        btn_layout = QHBoxLayout()
        self.btn_vis = QPushButton("Visualize Selected")
        self.btn_vis.setStyleSheet("font-weight: bold; background-color: #d0f0c0;")
        # Wrapped: clicked(bool) would otherwise bind `checked` to `force`.
        self.btn_vis.clicked.connect(lambda: self.visualize_selected_mos())
        self.btn_vis.setEnabled(False)  # Default disabled until selection
        btn_layout.addWidget(self.btn_vis)

        # Connect selection
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)

        # MO Diagram Button
        btn_diag = QPushButton("Show MO Diagram")
        btn_diag.clicked.connect(self.show_mo_diagram)
        btn_layout.addWidget(btn_diag)

        self.btn_compare = QPushButton("Compare MOs")
        self.btn_compare.setToolTip(
            "Show up to four orbitals at once, each with its own colours."
        )
        self.btn_compare.clicked.connect(self.show_compare_dialog)
        btn_layout.addWidget(self.btn_compare)

        btn_csv = QPushButton("Export CSV")
        btn_csv.clicked.connect(self.export_csv)
        btn_layout.addWidget(btn_csv)

        btn_close = QPushButton("Close")
        # close(), not accept(): since Qt 6.3 done() swallows the QCloseEvent,
        # so accept() skipped closeEvent and left the isosurfaces in the view.
        btn_close.clicked.connect(self.close)
        btn_layout.addWidget(btn_close)

        # Add Stretch to push buttons to right or keep centered?
        # Standard dialogs often have buttons on right.
        # But centering is fine.

        # IMPORTANT: Add the button layout to the main layout!
        layout.addLayout(btn_layout)
        # If we want interaction with 3D view, we need Non-Modal (show()).

        # For now, we keep it Modal, but 'Visualize' updates the background window?
        # If modal, user can't rotate 3D view easily without closing dialog.
        # Populate
        self.normalize_and_populate()

    def normalize_and_populate(self):
        """Sort the parsed orbitals by spin/index and rebuild the orbital tree."""
        self.tree.clear()  # Verify clear first
        self.mo_list = []

        # Sort keys logic:
        # Keys are likely strings "0_alpha", "1_alpha" etc. or integers (if old/other format)
        # We want to sort by Spin then Index, or just by Energy if available?
        # Standard: Sort by Spin (Alpha first), then Index.

        keys = []
        if isinstance(self.mos, dict):
            raw_keys = list(self.mos.keys())
            if raw_keys and isinstance(raw_keys[0], str) and "_" in raw_keys[0]:
                # Sort by spin, then index -> NOW Sort by Index, then Spin (interleaved)
                def sort_key(k):
                    if "_" in str(k):
                        idx_s, spin_s = str(k).split("_")

                        if spin_s == "alpha":
                            pass
                        else:
                            pass  # beta comes first in sorted list -> appears later in reverse iteration -> appears LOWER in UI?

                        # So we need CUSTOM sort where Beta < Alpha.
                        s_priority = 0 if spin_s == "beta" else 1

                        return (int(idx_s), s_priority)
                    return (0, 0)

                keys = sorted(raw_keys, key=sort_key)
            else:
                # Fallback numeric sort
                keys = sorted(
                    raw_keys, key=lambda x: int(x) if str(x).isdigit() else str(x)
                )

            for k in keys:
                val = self.mos[k]
                if "id" not in val:
                    val["id"] = k
                # Store the Access Key used in self.mos dictionary
                val["_access_key"] = k
                self.mo_list.append(val)
        elif isinstance(self.mos, list):
            self.mo_list = self.mos
            # Add index as access key
            for i, mo in enumerate(self.mo_list):
                mo["_access_key"] = i

        # Separate HOMO/LUMO logic for each spin
        spin_mos = {"restricted": [], "alpha": [], "beta": []}
        for mo in self.mo_list:
            s = mo.get("spin", "restricted")
            if s not in spin_mos:
                s = "restricted"  # fallback using restricted logic for unknown strings if any
            spin_mos[s].append(mo)

        # HOMO = the highest-indexed occupied orbital, not the occupied
        # *count* - 1: fractional-occupation output (FOD, natural orbitals)
        # leaves gaps, and counting labelled a mid-manifold orbital HOMO.
        spin_homo_idx = {}
        for s, mos in spin_mos.items():
            occupied = []
            for i, mo in enumerate(mos):
                if mo.get("occ", mo.get("occupation", 0.0)) <= 0.1:
                    continue
                try:
                    # "id" may arrive as the string dict key; the display path
                    # below parses it the same way.
                    occupied.append(int(mo.get("id", mo.get("index", i))))
                except (TypeError, ValueError):
                    occupied.append(i)
            spin_homo_idx[s] = max(occupied) if occupied else -1

        # Display order: Reversed (High Energy Top)
        # We need to sort global list carefully if mixing spins?
        # Or just show them in the order we sorted above (Alpha then Beta)
        # Our self.mo_list is already sorted by Spin, Index.
        # But we iterate backwards! so Beta (high idx -> low idx) -> Alpha (high idx -> low idx)

        count = len(self.mo_list)
        for i in range(count - 1, -1, -1):
            mo = self.mo_list[i]

            # Internal unique key
            access_key = mo.get("_access_key", None)

            # Display ID
            mo_idx_val = mo.get("id", mo.get("index", i))
            spin = mo.get("spin", "restricted")

            # Determine if HOMO/LUMO for this specific spin channel
            # We need the index OF THIS MO within its spin group
            # Since self.mo_list is sorted, we can find it?
            # Or just use the ID? Usually ID corresponds to index.
            # Let's use the ID if available, assuming 0-based index.

            local_idx = -1
            try:
                local_idx = int(mo_idx_val)
            except (TypeError, ValueError) as e:
                logging.warning(
                    "MO: could not parse MO index %r as an integer: %s", mo_idx_val, e
                )

            if spin in spin_homo_idx:
                h = spin_homo_idx[spin]

            display_id = str(mo_idx_val)

            label_id = display_id
            if spin == "alpha":
                label_id += " (a)"
            elif spin == "beta":
                label_id += " (b)"

            # Label Separation
            homo_lumo_label = ""
            if spin in spin_homo_idx:
                diff = local_idx - h

                if diff == 0:
                    homo_lumo_label = "HOMO"
                elif diff == 1:
                    homo_lumo_label = "LUMO"
                elif diff > 1 and diff < 100:
                    homo_lumo_label = f"LUMO+{diff - 1}"
                elif diff < 0 and diff > -100:
                    homo_lumo_label = f"HOMO{diff}"

            occ = mo.get("occ", mo.get("occupation", 0.0))
            e_eh = mo.get("energy_eh", mo.get("energy", None))
            e_ev = mo.get("energy_ev", None)

            if e_eh is None and e_ev is not None:
                e_eh = e_ev / 27.2114
            elif e_ev is None and e_eh is not None:
                e_ev = e_eh * 27.2114
            if e_eh is None:
                e_eh = 0.0
            if e_ev is None:
                e_ev = 0.0

            # Check if file exists to highlight
            bg_color = None
            try:
                # Use label_id as the display_id to match logic in visualize_current_mo
                path = self.get_cube_path(label_id)
                if path and os.path.exists(path):
                    bg_color = QColor(240, 255, 240)  # Light Green
            # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logging.warning(
                    "MO: could not check for an existing cube file for %s: %s",
                    label_id,
                    e,
                )

            item = QTreeWidgetItem(
                [label_id, homo_lumo_label, f"{occ:.2f}", f"{e_ev:.3f}", f"{e_eh:.5f}"]
            )
            if bg_color:
                for c in range(5):
                    item.setBackground(c, QBrush(bg_color))

            # Store the unique lookup key
            item.setData(0, Qt.ItemDataRole.UserRole, access_key)

            # Text Color based on Occ
            if occ > 1.9:
                item.setForeground(0, QColor("blue"))
            elif occ > 0.1:
                item.setForeground(0, QColor("green"))
            else:
                item.setForeground(0, QColor("gray"))

            self.tree.addTopLevelItem(item)

        # Scroll to HOMO of first available spin branch
        # Since we just fill them, scrolling to center might be ambiguous.
        # Let's scroll to the first 'Occupied' item found from top of tree (High energy)
        # That would be the HOMO of the last spin block added (e.g. Beta HOMO if present, or Alpha HOMO).
        # Actually tree has High Energy at top. So First Occupied Item is HOMO.

        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            # Check column 1 for Label "HOMO"
            if "HOMO" in item.text(1):
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(
                    item, QAbstractItemView.ScrollHint.PositionAtCenter
                )
                break
            iterator += 1

    def on_double_click(self, item, col):  # pylint: disable=unused-argument
        # item/col are unused: Qt's itemDoubleClicked signature; uses the tree's current selection.
        """Visualize the double-clicked orbital, generating its cube if needed."""
        # Double click always tries to visualize (generate if needed)
        self.visualize_selected_mos()

    def on_item_changed(self, current, previous):  # pylint: disable=unused-argument
        # previous is unused: Qt's currentItemChanged signature, only the new item is needed.
        """Auto-load the already-cached cube for the newly selected orbital, if any."""
        # Single click or keyboard change
        if not current:
            return

        # 1. Update Buttons (reuse logic)
        # on_selection_changed handles button state but relies on "selectedItems"
        # which might not be updated yet? usually it is.
        # But let's check for Auto-Load

        try:
            # Logic: If generated file exists, load it.
            display_id = current.text(0)
            # Need to clean display_id to match file system (e.g. "17 (a)" -> "17_a")
            # Use self.get_cube_path which handles this
            path = self.get_cube_path(display_id)
            if path and os.path.exists(path):
                self.show_cube(path)
        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning(
                "MO: could not auto-load the cached cube for %s: %s", display_id, e
            )

    def on_selection_changed(self):
        """Enable Visualize only when the selected orbital has coefficients."""
        items = self.tree.selectedItems()
        has_coeffs = False
        if items:
            try:
                # Use UserRole data for robust lookup
                key = items[0].data(0, Qt.ItemDataRole.UserRole)

                # Check coeffs in parser data
                if hasattr(self.parent_dlg, "parser") and self.parent_dlg.parser:
                    if (
                        hasattr(self.parent_dlg.parser, "data")
                        and self.parent_dlg.parser.data
                    ):
                        if "mo_coeffs" in self.parent_dlg.parser.data:
                            # Use key directly
                            if key in self.parent_dlg.parser.data["mo_coeffs"]:
                                has_coeffs = True
            except (KeyError, IndexError) as e:
                logging.warning("MO: could not check mo_coeffs for key %r: %s", key, e)

        self.btn_vis.setEnabled(has_coeffs)
        if items and not has_coeffs:
            self.btn_vis.setToolTip(
                f"No coefficients available for MO {items[0].text(0)}"
            )
            self.lbl_warning.setText(
                "<b>Coefficients Missing.</b> Required Input:"
                "<pre style='margin-top:0; margin-bottom:0;'>%output<br>  Print[P_Basis] 2<br>  Print[P_Mos] 1<br>end</pre>"
            )
            self.lbl_warning.setVisible(True)
            self.btn_copy_input.setVisible(True)
        else:
            self.btn_vis.setToolTip("")
            self.lbl_warning.setVisible(False)
            self.btn_copy_input.setVisible(False)

    def copy_gamess_input(self):
        """Copy the control keyword needed for MO coefficients to the clipboard."""
        text = " $CONTRL NPRINT=7 $END"
        QApplication.clipboard().setText(text)
        notify(self, "Control keyword copied to clipboard.", 5000)

    def get_engine(self):
        """Build a BasisSetEngine from the parser's basis set, or None on failure."""
        if not BasisSetEngine:
            QMessageBox.critical(self, "Error", "BasisSetEngine not available")
            return None
        try:
            # Safety checks
            if not hasattr(self.parent_dlg, "parser") or not self.parent_dlg.parser:
                return None
            if (
                not hasattr(self.parent_dlg.parser, "data")
                or not self.parent_dlg.parser.data
            ):
                return None

            shells = self.parent_dlg.parser.data.get("basis_set_shells", [])

            # Convert lists to numpy arrays for the engine AND map keys
            # IMPORTANT: Engine expects Bohr for centers (matching internal grid)
            # Parser provides Angstroms.
            BOHR_TO_ANG = 0.529177249

            clean_shells = []
            for s in shells:
                center_ang = np.array(s.get("origin", s.get("center", [0, 0, 0])))
                # Convert Angstrom -> Bohr
                center_bohr = center_ang / BOHR_TO_ANG

                d = {
                    "type": s.get("l", s.get("type", 0)),
                    "center": center_bohr,
                    "exps": np.array(s["exps"]),
                    "coeffs": np.array(s["coeffs"]),
                    "cartesian": bool(s.get("cartesian", False)),
                }
                clean_shells.append(d)

            return BasisSetEngine(clean_shells)
        except UnsupportedBasisError as e:
            # Written for the user; show it as-is rather than wrapped in
            # "Engine Init Failed".
            QMessageBox.warning(self, "Unsupported basis set", str(e))
            return None
        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Error", f"Engine Init Failed: {e}")
            return None

    def describe_cached_cube(self, item):
        """One line naming the settings the cached cube was built with.

        Cubes written before the settings were stamped, or by another
        program, report what is genuinely missing rather than the current
        spin-box values, which may have nothing to do with the file.
        """
        path = self.get_cube_path(item.text(0))
        if not path or not os.path.exists(path):
            return "Not generated yet"
        if read_generation_settings is None:
            return "Cube on disk"

        info = read_generation_settings(path)
        grid = info.get("grid")
        margin = info.get("margin")
        grid_s = f"{grid} pts" if grid is not None else "unknown grid"
        margin_s = f"{margin:.2f} Å" if margin is not None else "unknown margin"
        version = info.get("version")
        version_s = f" — v{version}" if version else ""
        return f"Cached: {grid_s}, margin {margin_s}{version_s}"

    def show_tree_context_menu(self, pos):
        """Right-click menu on the orbital table."""
        selected = self.tree.selectedItems()
        if not selected:
            return

        cached = [
            item
            for item in selected
            if (path := self.get_cube_path(item.text(0))) and os.path.exists(path)
        ]

        menu = QMenu(self.tree)
        info = menu.addAction(self.describe_cached_cube(selected[0]))
        info.setEnabled(False)
        menu.addSeparator()

        act_vis = menu.addAction(f"Visualize Selected ({len(selected)})")
        act_regen = menu.addAction(f"Regenerate Cube ({len(selected)})")
        act_regen.setToolTip(
            "Recompute the cube even if a file already exists, replacing it."
        )
        # Regenerating is only meaningful when something is actually cached;
        # with no file on disk a plain Visualize already computes it.
        act_regen.setEnabled(bool(cached))

        act_compare = menu.addAction(f"Compare Selected ({len(selected)})")
        act_compare.setToolTip(
            "Open the comparison window with these orbitals filled in."
        )
        act_compare.setEnabled(len(selected) <= 4)

        viewport = self.tree.viewport()
        chosen = menu.exec(
            viewport.mapToGlobal(pos) if viewport else self.tree.mapToGlobal(pos)
        )
        if chosen is act_vis:
            self.visualize_selected_mos()
        elif chosen is act_regen:
            self.regenerate_selected_mos()
        elif chosen is act_compare:
            self.show_compare_dialog()

    def regenerate_selected_mos(self):
        """Recompute cubes for the selected orbitals, overwriting existing files."""
        self.visualize_selected_mos(force=True)

    def generate_cubes(self, keys, on_done=None, force=False):
        """Write cubes for *keys* without disturbing the main 3D view.

        The compare dialog draws the orbitals itself, so this reuses the
        sequential worker queue but suppresses show_cube for the batch.
        """
        if self._generation_busy():
            notify(
                self,
                "Cube generation already in progress; try again when it finishes.",
                5000,
            )
            if on_done:
                on_done()
            return
        self.generation_queue = [k for k in keys if k is not None]
        if not self.generation_queue:
            if on_done:
                on_done()
            return
        self.generation_force = bool(force)
        self.generation_silent = True
        self.generation_done_cb = on_done
        self.process_generation_queue()

    def _generation_busy(self) -> bool:
        """Whether a CalcWorker is still computing a cube.

        Starting a second batch meanwhile replaced self.worker, dropping the
        last reference to a running QThread (a hard crash when it is
        collected), and interleaved two queues.
        """
        worker = getattr(self, "worker", None)
        try:
            # `is True`: a real QThread returns a bool; anything else is not
            # a running worker.
            return worker is not None and worker.isRunning() is True
        except RuntimeError:
            return False

    def cancel_generation(self) -> None:
        """Stop the running cube batch: drop the queue and cancel the worker."""
        self.generation_queue = []
        worker = getattr(self, "worker", None)
        if worker is not None and hasattr(worker, "cancel"):
            worker.cancel()

    def _display_cube(self, path):
        """show_cube unless the running batch asked for files only."""
        if getattr(self, "generation_silent", False):
            return
        self.show_cube(path)

    def visualize_selected_mos(self, force=False):
        """Queue the selected orbitals for cube generation/visualization."""
        # Batch generation for selected items
        selected = self.tree.selectedItems()
        if not selected:
            return
        if self._generation_busy():
            notify(
                self,
                "Cube generation already in progress; try again when it finishes.",
                5000,
            )
            return

        # Build Queue
        self.generation_queue = []
        for item in selected:
            key = item.data(0, Qt.ItemDataRole.UserRole)
            if key is not None:  # Ensure key is valid
                self.generation_queue.append(key)

        if not self.generation_queue:
            return

        # Applies to the whole batch; generation is strictly sequential, so a
        # single flag cannot straddle two different requests.
        self.generation_force = bool(force)

        # Start Batch
        self.process_generation_queue()

    def process_generation_queue(self):
        """Generate the cube for the next queued orbital, or finish the batch."""
        if getattr(self, "_is_closing", False) is True:
            return
        if getattr(self, "_processing_queue", False) is True:
            return
        self._processing_queue = True
        try:
            while getattr(self, "generation_queue", None):
                key = self.generation_queue.pop(0)
                if self._generate_single_mo(key):
                    return
        finally:
            self._processing_queue = False
        if not getattr(self, "generation_queue", None):
            # Done
            if (
                getattr(self, "progress_dialog", None) is not None
                and self.progress_dialog
            ):
                self.progress_dialog.close()
                self.progress_dialog = None
            # Batch over: a later plain Visualize must use the cache again.
            self.generation_force = False
            self.generation_silent = False
            cb, self.generation_done_cb = self.generation_done_cb, None
            if cb:
                cb()
            return

    def _generate_single_mo(self, key):
        # ... (Logic from old visualize_current_mo) ...
        # Re-fetch data using key
        # Key might be index (int) or dict key (str)
        # Find the correct MO in self.mo_list
        mo_data = None
        for mo in self.mo_list:
            if mo.get("_access_key", None) == key:
                mo_data = mo
                break

        if not mo_data:
            logging.warning("MO not found for key: %s", key)
            self.process_generation_queue()  # Skip invalid key
            return

        # Get Display ID
        # Need to get the display_id from the original item for consistent caching/display
        # Find the item in the tree by key
        display_id = None
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            if item.data(0, Qt.ItemDataRole.UserRole) == key:
                display_id = item.text(0)
                break
            it += 1

        if display_id is None:
            # Fallback if item not found (shouldn't happen if key is from selected item)
            display_id = str(mo_data.get("id", key))
            spin = mo_data.get("spin", "")
            if spin == "alpha":
                display_id += " (a)"
            elif spin == "beta":
                display_id += " (b)"

        # Safety checks for parser data
        if not hasattr(self.parent_dlg, "parser") or not self.parent_dlg.parser:
            QMessageBox.warning(self, "Error", "Parser not available")
            self.process_generation_queue()
            return
        if (
            not hasattr(self.parent_dlg.parser, "data")
            or not self.parent_dlg.parser.data
        ):
            QMessageBox.warning(self, "Error", "No parser data available")
            self.process_generation_queue()
            return

        coeffs_map = self.parent_dlg.parser.data.get("mo_coeffs", {})
        mo_data_coeffs = coeffs_map.get(key, None)  # This is the actual coeffs data
        if not mo_data_coeffs:
            QMessageBox.warning(self, "Error", f"No coefficients for MO {display_id}")
            self.process_generation_queue()
            return

        engine = self.get_engine()
        if not engine:
            self.process_generation_queue()  # Skip
            return

        if engine.n_basis == 0:
            QMessageBox.warning(
                self,
                "Error",
                "Basis Set Engine initialized with 0 basis functions. Check parser.",
            )
            self.process_generation_queue()
            return

        # Prepare Data
        raw_coeffs = [x["coeff"] for x in mo_data_coeffs["coeffs"]]

        # GAMESS prints one coefficient per basis function, so a mismatch means
        # the basis set we rebuilt is not the one the coefficients belong to
        # -- typically a shell the parser did not recognise. min()-truncating
        # the vector used to hide that: after a dropped shell every remaining
        # coefficient lines up with the wrong basis function and the orbital
        # is quietly wrong.
        if len(raw_coeffs) != engine.n_basis:
            QMessageBox.warning(
                self,
                "Basis set mismatch",
                f"This job has {len(raw_coeffs)} MO coefficients per orbital "
                f"but the reconstructed basis set has {engine.n_basis} "
                "functions.\n\nVisualization is blocked rather than showing "
                "an orbital that would be silently incorrect.",
            )
            self.process_generation_queue()
            return

        dense_vec = np.array(raw_coeffs, dtype=float)

        if np.sum(np.abs(dense_vec)) < 1e-9:
            QMessageBox.warning(
                self,
                "Data Error",
                f"MO coefficients for {display_id} are all zero/empty! Cube will be empty.",
            )
            self.process_generation_queue()
            return

        out_path = self.get_cube_path(display_id)
        if not out_path:
            out_path = os.path.join(tempfile.gettempdir(), f"gamess_mo_{display_id}.cube")
        else:
            # Lazy directory creation: create it now if it doesn't exist
            out_dir = os.path.dirname(out_path)
            if not os.path.exists(out_dir):
                try:
                    os.makedirs(out_dir)
                except OSError as e:
                    logging.warning(
                        "MO: could not create cube output directory %s: %s", out_dir, e
                    )

        # A silent batch must not steal what the main view is showing: an
        # update_vis_only() afterwards would redraw somebody else's orbital.
        if not getattr(self, "generation_silent", False):
            self.last_cube_path = out_path

        if os.path.exists(out_path) and not getattr(self, "generation_force", False):
            self._display_cube(out_path)
            # Highlight
            it = QTreeWidgetItemIterator(self.tree)
            bg = QBrush(QColor(240, 255, 240))
            while it.value():
                item = it.value()
                if item.data(0, Qt.ItemDataRole.UserRole) == key:
                    for c in range(5):
                        item.setBackground(c, bg)
                    break
                it += 1

            # Next!
            self.process_generation_queue()
            return

        # Start Worker
        # Manage Progress Dialog (Shared)
        if getattr(self, "progress_dialog", None) is None:
            self.progress_dialog = QProgressDialog(
                "Generating Cubes...", "Cancel", 0, 100, self
            )
            self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            self.progress_dialog.setAutoClose(False)  # Keep open for batch
            self.progress_dialog.canceled.connect(self.cancel_generation)
            self.progress_dialog.show()

        self.progress_dialog.setLabelText(f"Generating {display_id}...")
        self.progress_dialog.setValue(0)

        self.worker = CalcWorker(
            engine,
            key,
            self.spin_pts.value(),
            self.spin_margin.value(),
            self.parent_dlg.parser.data["atoms"],
            self.parent_dlg.parser.data["coords"],
            dense_vec,
            out_path,
        )
        worker = self.worker
        _RUNNING_WORKERS.add(worker)
        connect_queued(
            worker.finished,
            lambda: _RUNNING_WORKERS.discard(worker),
            cleanup=(worker.finished,),
        )

        self.worker.progress_sig.connect(self.progress_dialog.setValue)

        def on_finished(success, res):
            if getattr(self, "_is_closing", False) is True:
                return
            if success:
                self._display_cube(res)
                # Highlight
                it = QTreeWidgetItemIterator(self.tree)
                bg = QBrush(QColor(240, 255, 240))
                while it.value():
                    item = it.value()
                    if item.data(0, Qt.ItemDataRole.UserRole) == key:
                        for c in range(5):
                            item.setBackground(c, bg)
                        break
                    it += 1

                if (
                    getattr(self, "diag_dlg", None) is not None
                    and self.diag_dlg
                    and self.diag_dlg.isVisible()
                ):
                    self.diag_dlg.status_label.setText(
                        f"Generated: {os.path.basename(res)}"
                    )
            elif res == CANCELLED:
                logging.info("MO: cube generation cancelled by the user")
            else:
                logging.warning("MO: cube generation failed: %s", res)
                QMessageBox.warning(
                    self, "Generation Failed", f"Failed to generate cube:\n{res}"
                )
                if (
                    getattr(self, "diag_dlg", None) is not None
                    and self.diag_dlg
                    and self.diag_dlg.isVisible()
                ):
                    self.diag_dlg.status_label.setText("Generation Failed")

            # Trigger Next
            self.process_generation_queue()

        connect_queued(
            self.worker.finished_sig,
            on_finished,
            self,
            (bool, str),
            cleanup=(self.worker.finished,),
        )
        self.worker.start()
        return True

    def pick_color(self, which):
        """Open a color picker for the +/- lobe and apply it to the swatch button."""
        current_col = QColor("red") if which == "p" else QColor("blue")
        # Try to parse from button style
        try:
            style = (
                self.btn_color_p.styleSheet()
                if which == "p"
                else self.btn_color_n.styleSheet()
            )
            if "background-color:" in style:
                c_str = style.split("background-color:")[1].split(";")[0].strip()
                current_col = QColor(c_str)
        except (RuntimeError, AttributeError, IndexError) as e:
            logging.warning(
                "MO: could not read the current %s-lobe color from the button style: %s",
                which,
                e,
            )

        col = QColorDialog.getColor(current_col, self, "Select Color")
        if col.isValid():
            hex_c = col.name()
            # Determine contrasting text color
            # Simple brightness check
            brightness = (col.red() * 299 + col.green() * 587 + col.blue() * 114) / 1000
            text_c = "black" if brightness > 128 else "white"

            style_sheet = (
                f"background-color: {hex_c}; color: {text_c}; font-weight: bold;"
            )
            if which == "p":
                self.btn_color_p.setStyleSheet(style_sheet)
            else:
                self.btn_color_n.setStyleSheet(style_sheet)
            self.update_vis_only()

    def load_settings(self):
        """Restore saved visualization presets and select the last-used one."""
        self.settings_file = os.path.join(os.path.dirname(__file__), "settings.json")
        self.presets = {
            "Default": {
                "iso": 0.02,
                "opacity": 0.5,
                "style": "Surface",
                "color_p": "#ff0000",
                "color_n": "#0000ff",
                "smooth_shading": True,
            }
        }

        if os.path.exists(self.settings_file):
            mo_settings = load_section(self.settings_file, "mo_settings")
            try:
                # Load presets
                saved_presets = mo_settings.get("presets", {})
                for name, data in saved_presets.items():
                    self.presets[name] = data

                # Last used
                last_preset = mo_settings.get("last_preset", "Default")

                # Populate combo
                self.combo_presets.blockSignals(True)
                self.combo_presets.clear()
                self.combo_presets.addItems(list(self.presets.keys()))

                if last_preset in self.presets:
                    self.combo_presets.setCurrentText(last_preset)
                    self.apply_preset(last_preset)
                else:
                    self.combo_presets.setCurrentText("Default")
                    self.apply_preset("Default")

                self.combo_presets.blockSignals(False)
            # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logging.warning("Error loading settings: %s", e)

    def save_settings(self):
        """Persist the visualization presets and the currently selected one."""
        # Save current presets and selection
        mo_settings = {
            "presets": {k: v for k, v in self.presets.items() if k != "Default"},
            "last_preset": self.combo_presets.currentText(),
            "smooth_shading": self.check_smooth.isChecked(),
        }
        save_section(self.settings_file, "mo_settings", mo_settings)

    def save_preset(self):
        """Prompt for a name and save the current visualization settings as a preset."""
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset Name:")
        if not ok or not name:
            return

        # Get current state
        data = {
            "iso": self.spin_iso.value(),
            "opacity": self.spin_opacity.value(),
            "style": self.combo_style.currentText(),
            "color_p": self.get_color_hex("p"),
            "color_n": self.get_color_hex("n"),
            "smooth_shading": self.check_smooth.isChecked(),
        }

        self.presets[name] = data

        # Update combo
        self.combo_presets.blockSignals(True)
        self.combo_presets.clear()
        self.combo_presets.addItems(list(self.presets.keys()))
        self.combo_presets.setCurrentText(name)  # switch to new
        self.combo_presets.blockSignals(False)

        self.save_settings()

    def delete_preset(self):
        """Delete the currently selected preset (Default cannot be deleted)."""
        curr = self.combo_presets.currentText()
        if curr == "Default":
            QMessageBox.warning(self, "Error", "Cannot delete Default preset.")
            return

        if curr in self.presets:
            del self.presets[curr]

        self.combo_presets.blockSignals(True)
        self.combo_presets.clear()
        self.combo_presets.addItems(list(self.presets.keys()))
        self.combo_presets.setCurrentText("Default")
        self.apply_preset("Default")
        self.combo_presets.blockSignals(False)

        self.save_settings()

    def apply_preset(self, name):
        """Apply a named preset's values to the visualization controls."""
        if name not in self.presets:
            return
        data = self.presets[name]

        # Block signals on all vis controls so intermediate changes do NOT each
        # trigger a separate show_cube() call (which would alter other MOs).
        for w in (
            self.spin_iso,
            self.spin_opacity,
            self.combo_style,
            self.check_smooth,
        ):
            w.blockSignals(True)
        try:
            self.spin_iso.setValue(data.get("iso", 0.02))
            self.spin_opacity.setValue(data.get("opacity", 0.5))

            style = data.get("style", "Surface")
            idx = self.combo_style.findText(style)
            if idx >= 0:
                self.combo_style.setCurrentIndex(idx)

            cp = data.get("color_p", "#ff0000")
            cn = data.get("color_n", "#0000ff")
            self.set_btn_color(self.btn_color_p, cp)
            self.set_btn_color(self.btn_color_n, cn)

            self.check_smooth.setChecked(data.get("smooth_shading", True))
        finally:
            for w in (
                self.spin_iso,
                self.spin_opacity,
                self.combo_style,
                self.check_smooth,
            ):
                w.blockSignals(False)

        # One single redraw after all values are set
        self.update_vis_only()
        self.save_settings()  # Save last used

    def get_color_hex(self, which):
        """Read the +/- lobe's current hex color from its swatch button's stylesheet."""
        # Extract from stylesheet
        btn = self.btn_color_p if which == "p" else self.btn_color_n
        style = btn.styleSheet()
        if "background-color:" in style:
            return style.split("background-color:")[1].split(";")[0].strip()
        return "#ff0000" if which == "p" else "#0000ff"

    def set_btn_color(self, btn, hex_c):
        """Paint a swatch button with hex_c, choosing readable text contrast."""
        col = QColor(hex_c)
        brightness = (col.red() * 299 + col.green() * 587 + col.blue() * 114) / 1000
        text_c = "black" if brightness > 128 else "white"
        btn.setStyleSheet(
            f"background-color: {hex_c}; color: {text_c}; font-weight: bold;"
        )

    def update_vis_only(self):
        """Redraw the last-shown cube with the current visualization settings."""
        if self.last_cube_path and os.path.exists(self.last_cube_path):
            self.show_cube(self.last_cube_path)

    def show_cube(self, path):
        """Load a cube file and render its +/- isosurfaces in the 3D view."""
        if not CubeVisualizer:
            logging.warning("Warning: CubeVisualizer module not loaded.")
            QMessageBox.warning(
                self,
                "Visualizer Error",
                "CubeVisualizer module not loaded.\nCheck if 'pyvista' is installed.",
            )
            return

        # Access Main Window
        mw = self.mw
        if not mw:
            return

        cp = self.get_color_hex("p")
        cn = self.get_color_hex("n")
        style = self.combo_style.currentText().lower()  # surface, wireframe, points

        vis = CubeVisualizer(mw)
        if vis.load_file(path):
            vis.show_iso(
                self.spin_iso.value(),
                opacity=self.spin_opacity.value(),
                color_p=cp,
                color_n=cn,
                style=style,
                smooth_shading=self.check_smooth.isChecked(),
            )
            mw.plotter.render()
            self.last_cube_path = path

    def reject(self):
        """Route Esc through close() so closeEvent cleanup always runs."""
        # Esc must run closeEvent cleanup (QDialog.reject only hides)
        self.close()

    def closeEvent(self, event):
        """Clean up 3D actors when closing"""
        self._is_closing = True
        self.cancel_generation()
        self.generation_done_cb = None
        if getattr(self, "progress_dialog", None) is not None:
            self.progress_dialog.close()
            self.progress_dialog = None
        # Clean up tracked sub-dialogs
        if getattr(self, "energy_dlg", None) is not None and self.energy_dlg:
            try:
                self.energy_dlg.close()
            except (RuntimeError, AttributeError) as e:
                logging.warning(
                    "MO: could not close the energy-components sub-dialog: %s", e
                )
            self.energy_dlg = None

        if getattr(self, "compare_dlg", None) is not None and self.compare_dlg:
            try:
                self.compare_dlg.close()
            except (RuntimeError, AttributeError) as e:
                logging.warning(
                    "MO: could not close the MO comparison sub-dialog: %s", e
                )
            self.compare_dlg = None

        if hasattr(self.parent_dlg, "mw"):
            plotter = self.parent_dlg.mw.plotter
            plotter.remove_actor("mo_iso_p")
            plotter.remove_actor("mo_iso_n")
            plotter.render()
        elif hasattr(self.parent_dlg, "context"):
            plotter = self.parent_dlg.context.get_main_window().plotter
            plotter.remove_actor("mo_iso_p")
            plotter.remove_actor("mo_iso_n")
            plotter.render()

        # accept() not super().closeEvent(): QDialog.closeEvent calls reject(),
        # which is routed back through close() and would recurse.
        event.accept()

    def export_csv(self):
        """Prompt for a path and write the visible orbital tree to CSV."""
        default_path = get_default_export_path(
            self.parent_dlg.file_path, suffix="_mo_list", extension=".csv"
        )
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export MO Data", default_path, "CSV Files (*.csv)"
        )
        if not filename:
            return

        try:
            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["MO", "Label", "Occupation", "Energy (eV)", "Energy (Eh)"]
                )

                # Iterate tree items to respect current sort/display?
                # Using the data list is safer and complete.
                # However, the tree has formatted text. Let's use the list self.mo_list
                # But self.mo_list is raw. Let's replicate the display logic or use tree items.

                # Using tree items ensures we export what is seen (including calculated Energy if missing)
                it = QTreeWidgetItemIterator(self.tree)
                while it.value():
                    item = it.value()
                    # Must cover every tree column (MO, Label, Occ, eV, Eh):
                    # stopping at text(3) shifted each value one header to the
                    # left and dropped the Hartree energy entirely.
                    row = [item.text(c) for c in range(5)]
                    writer.writerow(row)
                    it += 1
            notify(self, f"Data exported to {filename}", 5000)
        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Error", f"Failed to export CSV: {e}")

    def show_mo_diagram(self):
        """Open the orbital energy level diagram for the current orbitals."""
        if not EnergyDiagramDialog:
            return

        # Prepare Data for Diagram
        # 1. Separate by spin
        mos_alpha = []
        mos_beta = []
        mos_restr = []

        for mo in self.mo_list:
            s = mo.get("spin", "restricted")
            e_eh = mo.get("energy_eh", mo.get("energy", None))
            e_ev = mo.get("energy_ev", None)
            # Ensure e_eh is set (EnergyDiagram usually expects Hartree if unit selection is implemented there, defaults to "Ha")
            if e_eh is None and e_ev is not None:
                e_eh = e_ev / 27.2114
            if e_eh is None:
                e_eh = 0.0

            occ = mo.get("occ", mo.get("occupation", 0.0))

            item = {"e": e_eh, "occ": occ}

            if s == "alpha":
                mos_alpha.append(item)
            elif s == "beta":
                mos_beta.append(item)
            else:
                mos_restr.append(item)

        # 2. Determine Type
        is_uhf = len(mos_beta) > 0

        diag_data = {}
        if is_uhf:
            diag_data["type"] = "UHF"
            # Lists must be sorted by energy usually? They are sorted in list by spin/index.
            # Assuming 'mo_list' was sorted by index effectively
            # Let's ensure sort by index or energy
            # Since self.mo_list keys were keys...
            # Let's rely on standard order.

            es_a = [m["e"] for m in mos_alpha]
            noccs_a = [m["occ"] for m in mos_alpha]
            es_b = [m["e"] for m in mos_beta]
            noccs_b = [m["occ"] for m in mos_beta]

            diag_data["energies"] = [es_a, es_b]
            diag_data["occupations"] = [noccs_a, noccs_b]
        else:
            diag_data["type"] = "RHF"
            es = [m["e"] for m in mos_restr]
            noccs = [m["occ"] for m in mos_restr]
            if not es and mos_alpha:  # Fallback if labeled alpha but no beta
                es = [m["e"] for m in mos_alpha]
                noccs = [m["occ"] for m in mos_alpha]

            diag_data["energies"] = es
            diag_data["occupations"] = noccs

        # Determine Result Dir
        res_dir = None
        if hasattr(self.parent_dlg, "parser") and self.parent_dlg.parser:
            if (
                hasattr(self.parent_dlg.parser, "filename")
                and self.parent_dlg.parser.filename
            ):
                res_dir = os.path.dirname(self.parent_dlg.parser.filename)
                # Check for dedicated cube dir?
                base = os.path.splitext(
                    os.path.basename(self.parent_dlg.parser.filename)
                )[0]
                cube_dir = os.path.join(res_dir, f"{base}_cubes")
                if os.path.exists(cube_dir):
                    res_dir = cube_dir

        # Make modeless
        if getattr(self, "energy_dlg", None) is not None and self.energy_dlg:
            self.energy_dlg.close()

        self.energy_dlg = EnergyDiagramDialog(
            diag_data, parent=self, result_dir=res_dir
        )
        self.energy_dlg.show()

    def show_compare_dialog(self):
        """Open the modeless multi-orbital comparison window."""
        if not MOCompareDialog:
            QMessageBox.warning(
                self,
                "Unavailable",
                "The MO comparison dialog could not be loaded.",
            )
            return

        if getattr(self, "compare_dlg", None) is not None and self.compare_dlg:
            try:
                # Reopening from a new table selection must refill the slots,
                # not just surface a window still showing the old orbitals.
                self.compare_dlg.apply_selection()
                self.compare_dlg.raise_()
                self.compare_dlg.activateWindow()
                return
            except RuntimeError as e:
                # Underlying C++ object already gone; fall through and rebuild.
                logging.warning(
                    "MO: could not reuse the existing MO comparison dialog, rebuilding it: %s",
                    e,
                )

        self.compare_dlg = MOCompareDialog(self)
        self.compare_dlg.show()

    def on_compare_closed(self):
        """Clear the tracked reference once the MO comparison dialog closes."""
        self.compare_dlg = None

    def load_file_by_path(self, path):
        """Called from Diagram to load valid existing file"""
        if os.path.exists(path):
            self.show_cube(path)

            # Highlight in tree if possible
            # Need to reverse map path to key? Or user can just view it.
            # Best effort to highlight
            # We don't have the key here easily unless we parse filename.
            # But the visualization is what matters.

    def generate_specific_orbital(self, index, label, spin_suffix=""):  # pylint: disable=unused-argument
        # label is unused: index + spin_suffix fully identify the orbital to generate.
        """Called from Diagram to generate cube"""
        # Diagram index is index within spin channel.
        # We need to find the MO with that index and spin.

        found_mo = None
        target_spin = "beta" if spin_suffix == "_B" else "alpha"  # or restricted
        # Diagram treats 'restricted' as alpha usually (col 0).

        # Refined Logic:
        # iterate self.mo_list, count indices for that spin
        # 0-based index means nth orbital of that spin.

        # Check type
        is_uhf = False
        for mo in self.mo_list:
            if mo.get("spin", None) == "beta":
                is_uhf = True
                break

        if not is_uhf:
            target_spin = "restricted"

        # If we asked for Alpha but it's restricted, map alpha->restricted
        if target_spin == "alpha" and not is_uhf:
            target_spin = "restricted"

        curr_idx = 0
        mo_key = None

        # The diagram's index is positional within one spin channel, while
        # mo_list holds whatever order normalize_and_populate appended. Sort
        # explicitly by (spin, id) rather than trusting that order: picking the
        # wrong orbital here still renders a perfectly plausible-looking cube,
        # so the mistake is invisible in the picture.
        sorted_mos = sorted(
            self.mo_list, key=lambda x: (x.get("spin", ""), int(x.get("id", -1)))
        )

        for mo in sorted_mos:
            s = mo.get("spin", "restricted")
            if s != target_spin:
                continue

            if curr_idx == index:
                mo_key = mo.get("_access_key", None)
                found_mo = mo
                break
            curr_idx += 1

        if found_mo:
            # Trigger Visualization
            # We need to select it in the tree?
            # Or just call worker directly.
            # Visualizing relies on selection in tree for `visualize_current_mo`.
            # Let's Select it in tree.

            # Find item with data key
            it = QTreeWidgetItemIterator(self.tree)
            while it.value():
                item = it.value()
                if item.data(0, Qt.ItemDataRole.UserRole) == mo_key:
                    self.tree.setCurrentItem(item)
                    self.visualize_selected_mos()
                    break
                it += 1
        else:
            QMessageBox.warning(
                self, "Error", f"Could not find MO for Index {index} ({target_spin})"
            )
