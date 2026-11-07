"""NMR dialog: shielding/coupling tables, reference calibration and the spectrum tab."""

import os
import re
import logging
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QDoubleSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
    QGroupBox,
    QMessageBox,
    QCheckBox,
    QButtonGroup,
    QAbstractItemView,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer
from .utils import notify
from .settings import load_section, save_section


from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT,
)
from matplotlib.figure import Figure
from .nmr_custom_ref_dialog import CustomReferenceDialog
from .nmr_merge import _NMRMergeMixin
from .nmr_plot import _NMRPlotMixin
from .nmr_export import _NMRExportMixin
from . import PLUGIN_VERSION

# Shared by __init__ and save_settings, which subtracts these to find the
# user's own entries. Per nucleus every entry must share one δ_ref + σ_ref:
# that sum is the absolute shielding at δ=0. Method-dependent, so quantitative
# work wants a reference computed at the sample's level of theory via "Custom".
DEFAULT_REFERENCE_STANDARDS = {
    "1H": {
        "No Reference": {"delta_ref": 0.0, "sigma_ref": 0.0},
        "TMS": {"delta_ref": 0.0, "sigma_ref": 31.80},
        "CDCl3": {"delta_ref": 7.26, "sigma_ref": 24.54},
        "DMSO-d6": {"delta_ref": 2.50, "sigma_ref": 29.30},
    },
    "13C": {
        "No Reference": {"delta_ref": 0.0, "sigma_ref": 0.0},
        "TMS": {"delta_ref": 0.0, "sigma_ref": 182.40},
        "CDCl3": {"delta_ref": 77.16, "sigma_ref": 105.24},
        "DMSO-d6": {"delta_ref": 39.52, "sigma_ref": 142.88},
    },
    "15N": {
        "No Reference": {"delta_ref": 0.0, "sigma_ref": 0.0},
        "CH3NO2": {"delta_ref": 0.0, "sigma_ref": -135.80},
        "NH3": {"delta_ref": -381.90, "sigma_ref": 246.10},
    },
    "31P": {
        "No Reference": {"delta_ref": 0.0, "sigma_ref": 0.0},
        "H3PO4 (85%)": {"delta_ref": 0.0, "sigma_ref": 328.40},
    },
    "19F": {
        "No Reference": {"delta_ref": 0.0, "sigma_ref": 0.0},
        "CFCl3": {"delta_ref": 0.0, "sigma_ref": 188.50},
    },
}


class NMRDialog(QDialog, _NMRMergeMixin, _NMRPlotMixin, _NMRExportMixin):
    """Enhanced NMR Chemical Shielding Dialog with Spectrum"""

    # pylint: disable=attribute-defined-outside-init
    # Qt/PyVista pattern: label/actor/state attrs are created lazily, not in __init__.

    # Class-level constants for Nucleus Mapping and Physics
    ISOTOPE_MAP = {
        "H": "1H",
        "D": "2H",
        "T": "3H",
        "Li": "7Li",
        "Be": "9Be",
        "B": "11B",
        "C": "13C",
        "N": "15N",
        "O": "17O",
        "F": "19F",
        "Na": "23Na",
        "Mg": "25Mg",
        "Al": "27Al",
        "Si": "29Si",
        "P": "31P",
        "S": "33S",
        "Cl": "35Cl",
        "K": "39K",
        "Ca": "43Ca",
        "Sc": "45Sc",
        "Ti": "47Ti",
        "V": "51V",
        "Cr": "53Cr",
        "Mn": "55Mn",
        "Fe": "57Fe",
        "Co": "59Co",
        "Ni": "61Ni",
        "Cu": "63Cu",
        "Zn": "67Zn",
        "Ga": "71Ga",
        "Ge": "73Ge",
        "As": "75As",
        "Se": "77Se",
        "Br": "81Br",
        "Kr": "83Kr",
        "Rb": "87Rb",
        "Sr": "87Sr",
        "Y": "89Y",
        "Zr": "91Zr",
        "Nb": "93Nb",
        "Mo": "95Mo",
        "Tc": "99Tc",
        "Ru": "99Ru",
        "Rh": "103Rh",
        "Pd": "105Pd",
        "Ag": "109Ag",
        "Cd": "113Cd",
        "In": "115In",
        "Sn": "119Sn",
        "Sb": "121Sb",
        "Te": "125Te",
        "I": "127I",
        "Xe": "129Xe",
        "Cs": "133Cs",
        "Ba": "137Ba",
        "La": "139La",
        "W": "183W",
        "Os": "187Os",
        "Pt": "195Pt",
        "Au": "197Au",
        "Hg": "199Hg",
        "Tl": "205Tl",
        "Pb": "207Pb",
    }

    # Gyromagnetic ratios (10^6 rad s^-1 T^-1) approx -> Updated to IUPAC/CODATA standards
    GAMMA = {
        "H": 267.522,
        "1H": 267.522,
        "2H": 41.065,
        "3H": 285.35,
        "Li": 103.96,
        "7Li": 103.96,
        "9Be": 37.59,
        "B": 85.847,
        "11B": 85.847,
        "10B": 28.75,
        "C": 67.262,
        "13C": 67.262,
        "N": -27.116,
        "15N": -27.116,
        "14N": 19.331,
        "O": -36.26,
        "17O": -36.26,
        "F": 251.662,
        "19F": 251.662,
        "Na": 70.76,
        "23Na": 70.76,
        "25Mg": -16.38,
        "27Al": 69.76,
        "Si": -53.267,
        "29Si": -53.267,
        "P": 108.394,
        "31P": 108.394,
        "S": 20.53,
        "33S": 20.53,
        "Cl": 26.21,
        "35Cl": 26.21,
        "K": 12.48,
        "39K": 12.48,
        "Ca": -18.00,
        "43Ca": -18.00,
        "45Sc": 64.99,
        "47Ti": -15.08,
        "51V": 70.33,
        "Cr": -15.12,
        "53Cr": -15.12,
        "55Mn": 66.08,
        "57Fe": 8.66,
        "59Co": 63.17,
        "61Ni": -23.91,
        "63Cu": 70.90,
        "Zn": 16.74,
        "67Zn": 16.74,
        "71Ga": 81.74,
        "73Ge": -9.33,
        "75As": 45.80,
        "77Se": 51.203,
        "81Br": 72.24,
        "Rb": 87.53,
        "87Rb": 87.53,
        "87Sr": -11.57,
        "89Y": -13.11,
        "91Zr": -24.87,
        "93Nb": 65.47,
        "Mo": -17.46,
        "95Mo": -17.46,
        "99Ru": -12.29,
        "103Rh": -8.468,
        "105Pd": -12.24,
        "109Ag": -12.45,
        "Cd": -59.53,
        "113Cd": -59.53,
        "115In": 58.85,
        "119Sn": -99.95,
        "121Sb": 64.18,
        "125Te": -84.71,
        "127I": 53.71,
        "Xe": -73.99,
        "129Xe": -73.99,
        "133Cs": 35.09,
        "137Ba": 29.86,
        "139La": 37.90,
        "183W": 11.13,
        "187Os": 6.08,
        "Pt": 58.385,
        "195Pt": 58.385,
        "197Au": 4.71,
        "199Hg": 47.91,
        "205Tl": 155.19,
        "207Pb": 55.70,
    }

    def __init__(self, parent, data, file_path=None):
        super().__init__(parent)
        self.setWindowTitle(f"Calculated NMR Spectrum (v{PLUGIN_VERSION})")
        self.resize(600, 850)  # More compact width

        # Make dialog modeless (non-blocking)
        self.setWindowModality(Qt.WindowModality.NonModal)

        # Store parent dialog for 3D viewer access
        self.parent_dlg = parent

        self.data = data
        self.displayed_data = list(data)

        # Track atom labels in 3D viewer
        self._atom_labels = []

        # Unsaved merge changes (merges are saved explicitly, never auto-saved)
        self._merged_dirty = False

        # Track selected peaks for highlighting
        self.selected_peak_indices = set()
        self.highlight_artists = []
        self.show_all_mode = False  # Track if showing all labels without highlights

        self.last_ref_name = None

        # Reference standards (delta = reference position, sigma = isotropic
        # shielding). Chemical shift: δ_sample = δ_ref + (σ_ref - σ_sample)
        self.reference_standards = {
            nucleus: dict(refs, Custom={"delta_ref": 0.0, "sigma_ref": 0.0})
            for nucleus, refs in DEFAULT_REFERENCE_STANDARDS.items()
        }

        # Current reference values
        self.delta_ref = 0.0
        self.sigma_ref = 0.0

        # Spectrum settings
        self.linewidth = 1.0  # ppm for spectrum
        self.peak_intensity = 1.0

        # Settings file
        self.file_path = file_path
        self.merged_peaks_file = self._merged_peaks_path(file_path, data)

        self.settings_file = os.path.join(os.path.dirname(__file__), "settings.json")

        # Track manually merged peaks: [{"indices": [0, 1, 2], "avg_delta": 7.5, ...}]
        self.merged_peaks = []
        self.load_merged_peaks()

        self.load_settings()

        # Initialize current nucleus before UI setup
        self.current_nucleus = "All"

        self.setup_ui()

        # Timer for polling main window selection (sync 3D -> NMR)
        self.sel_timer = QTimer(self)
        self.sel_timer.timeout.connect(self._check_external_selection)
        self.sel_timer.start(200)  # Check every 200ms

        # Custom 3D highlight actors and names
        self._nmr_sphere_actors = []
        self._nmr_label_names = []  # Explicitly track label names for removal

    def get_nucleus_key(self, atom_sym):
        """Map atom symbol to nucleus key for reference standards"""
        # Clean the input symbol (remove whitespace, numbers if accidentally passed)
        clean_sym = "".join([c for c in atom_sym if c.isalpha()])
        return self.ISOTOPE_MAP.get(
            clean_sym.upper(), self.ISOTOPE_MAP.get(clean_sym, clean_sym)
        )

    def load_settings(self):
        """Load NMR settings from JSON"""
        if os.path.exists(self.settings_file):
            nmr_settings = load_section(self.settings_file, "nmr_settings")
            try:
                self.linewidth = nmr_settings.get("spectrum_linewidth", 1.0)
                self.peak_intensity = nmr_settings.get("peak_intensity", 1.0)

                self.last_ref_name = nmr_settings.get("last_reference", None)

                # Load custom references
                custom_refs = nmr_settings.get("custom_references", {})
                for nucleus, refs in custom_refs.items():
                    if nucleus not in self.reference_standards:
                        self.reference_standards[nucleus] = {}
                    for ref_name, ref_val in refs.items():
                        self.reference_standards[nucleus][ref_name] = ref_val
            except AttributeError as e:
                logging.warning("Error loading NMR settings: %s", e)

    def save_settings(self):
        """Save NMR settings to JSON"""
        # Extract custom references only (non-default)
        default_standards = DEFAULT_REFERENCE_STANDARDS

        custom_refs = {}
        for nucleus, refs in self.reference_standards.items():
            for ref_name, ref_val in refs.items():
                if ref_name == "Custom":
                    continue  # Skip custom placeholder
                default_dict = default_standards.get(nucleus, {})
                if ref_name not in default_dict:
                    # Completely custom reference
                    if nucleus not in custom_refs:
                        custom_refs[nucleus] = {}
                    custom_refs[nucleus][ref_name] = ref_val
                elif default_dict.get(ref_name, None) != ref_val:
                    # Modified default reference
                    if nucleus not in custom_refs:
                        custom_refs[nucleus] = {}
                    custom_refs[nucleus][ref_name] = ref_val

        current_nmr_settings = {
            "spectrum_linewidth": self.linewidth,
            "peak_intensity": self.peak_intensity,
            "last_reference": self.last_ref_name,
            "custom_references": custom_refs,
        }

        # Update only the 'nmr_settings' key of the whole settings file
        save_section(self.settings_file, "nmr_settings", current_nmr_settings)

    def setup_ui(self):
        """Build the dialog's layout: filters, tables, plot tab and action buttons."""
        main_layout = QVBoxLayout(self)

        # 1. Reference & Element Filter Row
        top_row = QHBoxLayout()

        # Nucleus filter with toggle buttons
        nucleus_box = QGroupBox("Nucleus Filter")
        nucleus_layout = QHBoxLayout(nucleus_box)

        # Get available nuclei
        nuclei = ["All"] + sorted(list(set([d["atom_sym"] for d in self.data])))

        # Create button group for exclusive selection
        self.nucleus_button_group = QButtonGroup()
        self.nucleus_buttons = {}

        for nucleus in nuclei:
            btn = QPushButton(nucleus)
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #f8f8f8;
                    border: 1px solid #ccc;
                    border-radius: 4px;
                    padding: 6px 15px;
                    font-size: 10pt;
                    font-weight: 500;
                }
                QPushButton:checked {
                    background-color: #0066cc;
                    color: white;
                    border-color: #004d99;
                    font-weight: bold;
                }
                QPushButton:hover {
                    border-color: #0066cc;
                    background-color: #eef6ff;
                }
                QPushButton:checked:hover {
                    background-color: #0059b3;
                }
            """)
            btn.toggled.connect(
                lambda checked, n=nucleus: self.on_nucleus_changed(n)
                if checked
                else None
            )
            self.nucleus_button_group.addButton(btn)
            self.nucleus_buttons[nucleus] = btn
            nucleus_layout.addWidget(btn)

        nucleus_layout.addStretch()
        top_row.addWidget(nucleus_box)

        # Reference selection and values (merged into single group)
        ref_box = QGroupBox("Reference Standard")
        ref_layout = QVBoxLayout(ref_box)

        # Reference selection row
        ref_sel_row = QHBoxLayout()
        ref_sel_row.addWidget(QLabel("Standard:"))
        self.combo_ref = QComboBox()
        self.combo_ref.setMinimumWidth(250)
        self.combo_ref.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.combo_ref.setToolTip(
            "Built-in σ_ref values are absolute shieldings and therefore depend "
            "on the functional and basis set.\nFor quantitative shifts, compute "
            "the reference at the same level of theory as this job and enter it "
            "under “Custom”."
        )
        self.combo_ref.currentIndexChanged.connect(self.on_ref_change)
        ref_sel_row.addWidget(self.combo_ref)

        btn_add_ref = QPushButton("+ Custom")
        btn_add_ref.setFixedWidth(80)
        btn_add_ref.setAutoDefault(False)
        btn_add_ref.clicked.connect(self.add_custom_reference)
        ref_sel_row.addWidget(btn_add_ref)

        btn_del_ref = QPushButton("Delete")
        btn_del_ref.setFixedWidth(60)
        btn_del_ref.setAutoDefault(False)
        btn_del_ref.setToolTip("Delete selected custom reference")
        btn_del_ref.clicked.connect(self.delete_custom_reference)
        ref_sel_row.addWidget(btn_del_ref)

        # Duplicate combo_ref removed

        ref_layout.addLayout(ref_sel_row)

        # Delta ref (reference peak position)
        delta_row = QHBoxLayout()
        delta_row.addWidget(QLabel("δ_ref (ppm):"))
        self.spin_delta_ref = QDoubleSpinBox()
        self.spin_delta_ref.setRange(-500, 500)
        self.spin_delta_ref.setValue(self.delta_ref)
        self.spin_delta_ref.setDecimals(2)
        self.spin_delta_ref.setToolTip(
            "Reference peak position on chemical shift scale"
        )
        self.spin_delta_ref.valueChanged.connect(self.on_ref_value_change)
        delta_row.addWidget(self.spin_delta_ref)
        ref_layout.addLayout(delta_row)

        # Sigma ref (isotropic shielding)
        sigma_row = QHBoxLayout()
        sigma_row.addWidget(QLabel("σ_ref (ppm):"))
        self.spin_sigma_ref = QDoubleSpinBox()
        self.spin_sigma_ref.setRange(-10000, 20000)
        self.spin_sigma_ref.setValue(self.sigma_ref)
        self.spin_sigma_ref.setDecimals(2)
        self.spin_sigma_ref.setToolTip(
            "Isotropic shielding value of reference compound"
        )
        self.spin_sigma_ref.valueChanged.connect(self.on_ref_value_change)
        sigma_row.addWidget(self.spin_sigma_ref)
        ref_layout.addLayout(sigma_row)

        top_row.addWidget(ref_box)

        main_layout.addLayout(top_row)

        # 2. Spectrum Plot
        spec_group = QGroupBox("NMR Stick Spectrum (δ = δ_ref + σ_ref - σ)")
        spec_layout = QVBoxLayout(spec_group)

        # Spectrum settings row
        spec_settings = QHBoxLayout()

        # Add checkbox to show all labels
        self.chk_show_all_labels = QCheckBox("Show all atom labels")
        self.chk_show_all_labels.stateChanged.connect(self.toggle_all_labels)
        spec_settings.addWidget(self.chk_show_all_labels)

        self.chk_label_shifts = QCheckBox("Show shift values")
        self.chk_label_shifts.setChecked(False)  # default: hide (labels stay compact)
        self.chk_label_shifts.setToolTip(
            "Add the chemical shift to 3D atom labels "
            "(merged atoms show original → merged value)"
        )
        self.chk_label_shifts.stateChanged.connect(self.on_label_shifts_toggled)
        spec_settings.addWidget(self.chk_label_shifts)

        # Selection/merge workflow gets its own row: together with the two
        # checkboxes and the export buttons a single line exceeds the
        # dialog's 600px default width.
        merge_row = QHBoxLayout()

        # Add button to clear selection
        btn_clear_selection = QPushButton("Clear Selection")
        btn_clear_selection.setFixedWidth(120)
        btn_clear_selection.setAutoDefault(False)
        btn_clear_selection.setToolTip("Clear all selected peaks and labels")
        btn_clear_selection.clicked.connect(self.clear_peak_selection)
        merge_row.addWidget(btn_clear_selection)

        btn_merge = QPushButton("Merge Selected")
        btn_merge.setFixedWidth(120)
        btn_merge.setAutoDefault(False)
        btn_merge.setToolTip("Merge selected peaks into one entry")
        btn_merge.clicked.connect(self.merge_selected_peaks)
        merge_row.addWidget(btn_merge)

        btn_unmerge = QPushButton("Unmerge")
        btn_unmerge.setFixedWidth(120)
        btn_unmerge.setAutoDefault(False)
        btn_unmerge.setToolTip("Separate merged peaks back to individuals")
        btn_unmerge.clicked.connect(self.unmerge_selected_peaks)
        merge_row.addWidget(btn_unmerge)

        self.btn_save_merge = QPushButton("Save Merges")
        self.btn_save_merge.setFixedWidth(120)
        self.btn_save_merge.setAutoDefault(False)
        self.btn_save_merge.setToolTip(
            "Persist merged peak groups to disk (merges are no longer auto-saved)"
        )
        self.btn_save_merge.setEnabled(False)  # enabled once there are unsaved changes
        self.btn_save_merge.clicked.connect(self.save_merges_clicked)
        merge_row.addWidget(self.btn_save_merge)
        merge_row.addStretch()

        # X-Axis Range row
        x_range_layout = QHBoxLayout()
        self.chk_auto_x = QCheckBox("Auto Range")
        self.chk_auto_x.setChecked(False)
        self.chk_auto_x.stateChanged.connect(lambda: self.plot_spectrum())
        x_range_layout.addWidget(self.chk_auto_x)

        x_range_layout.addWidget(QLabel(" X Range:"))
        self.spin_x_max = QDoubleSpinBox()
        self.spin_x_max.setRange(-2000, 2000)
        self.spin_x_max.setValue(12.0)
        self.spin_x_max.valueChanged.connect(lambda: self.plot_spectrum())
        x_range_layout.addWidget(self.spin_x_max)

        x_range_layout.addWidget(QLabel("to"))
        self.spin_x_min = QDoubleSpinBox()
        self.spin_x_min.setRange(-2000, 2000)
        self.spin_x_min.setValue(-1.0)
        self.spin_x_min.valueChanged.connect(lambda: self.plot_spectrum())
        x_range_layout.addWidget(self.spin_x_min)
        x_range_layout.addWidget(QLabel("ppm"))

        def update_x_spins_enabled():
            enabled = not self.chk_auto_x.isChecked()
            self.spin_x_max.setEnabled(enabled)
            self.spin_x_min.setEnabled(enabled)

        self.chk_auto_x.stateChanged.connect(update_x_spins_enabled)
        update_x_spins_enabled()

        x_range_layout.addStretch()
        spec_layout.addLayout(x_range_layout)

        spec_settings.addStretch()

        # Add spec_settings to layout if not already (it was implied in previous context to be added)
        # spec_layout.addLayout(spec_settings)

        btn_export = QPushButton("Export Image")
        btn_export.setAutoDefault(False)
        btn_export.clicked.connect(self.export_spectrum)
        spec_settings.addWidget(btn_export)

        btn_export_csv = QPushButton("Export CSV")
        btn_export_csv.setAutoDefault(False)
        btn_export_csv.clicked.connect(self.export_spectrum_csv)
        spec_settings.addWidget(btn_export_csv)

        spec_layout.addLayout(spec_settings)
        spec_layout.addLayout(merge_row)

        # Matplotlib canvas - adjusted for narrower dialog
        self.figure = Figure(figsize=(5.5, 4))  # Narrower to fit 600px width
        self.canvas = FigureCanvas(self.figure)
        self.canvas.mpl_connect("button_press_event", self.reset_zoom)
        self.canvas.mpl_connect("button_press_event", self.on_peak_click)

        # Add navigation toolbar for zoom/pan
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        spec_layout.addWidget(self.toolbar)
        spec_layout.addWidget(self.canvas)

        main_layout.addWidget(spec_group)

        # 3. Data Table
        table_group = QGroupBox("Chemical Shift Data")
        table_layout = QVBoxLayout(table_group)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            ["Idx", "Nucleus", "σ (ppm)", "δ (ppm)"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(100)  # Half size for compact layout
        table_layout.addWidget(self.table)

        table_btn_row = QHBoxLayout()
        btn_copy = QPushButton("Copy to Clipboard")
        btn_copy.clicked.connect(self.copy_table)
        table_btn_row.addWidget(btn_copy)

        btn_export_table_csv = QPushButton("Export CSV")
        btn_export_table_csv.clicked.connect(self.export_table_csv)
        table_btn_row.addWidget(btn_export_table_csv)
        table_btn_row.addStretch()
        table_layout.addLayout(table_btn_row)

        main_layout.addWidget(table_group)

        # 4. Bottom Buttons
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(self.close)
        bottom_row.addWidget(btn_close)
        main_layout.addLayout(bottom_row)

        # Finally select "All" by default to trigger population
        if "All" in self.nucleus_buttons:
            self.nucleus_buttons["All"].setChecked(True)

    def update_reference_combo(self):
        """Update reference combo box based on current nucleus"""
        if getattr(self, "combo_ref", None) is None:
            return

        current_nucleus = self.current_nucleus
        if current_nucleus == "All":
            # Special case for "All": Only allow "No Reference"
            # We want to visualize raw values (no shifting) across different nuclei
            self.combo_ref.blockSignals(True)
            self.combo_ref.clear()
            self.combo_ref.addItems(["No Reference"])
            self.combo_ref.setCurrentText("No Reference")
            self.combo_ref.blockSignals(False)

            # Set values to 0,0
            self.delta_ref = 0.0
            self.sigma_ref = 0.0

            # Update spinboxes
            self.spin_delta_ref.blockSignals(True)
            self.spin_sigma_ref.blockSignals(True)
            self.spin_delta_ref.setValue(0.0)
            self.spin_sigma_ref.setValue(0.0)
            self.spin_delta_ref.blockSignals(False)
            self.spin_sigma_ref.blockSignals(False)

            # Disable inputs
            self.spin_delta_ref.setEnabled(False)
            self.spin_sigma_ref.setEnabled(False)
            return

        # Map atom symbol to nucleus key (e.g., "H" -> "1H")
        current_nucleus = self.get_nucleus_key(current_nucleus)

        # Save current selection to preserve it if possible
        current_ref = (
            self.combo_ref.currentText() if self.combo_ref.count() > 0 else None
        )

        # Block signals to prevent triggering on_ref_change during population
        self.combo_ref.blockSignals(True)

        # Clear and repopulate
        self.combo_ref.clear()
        refs = self.reference_standards.get(current_nucleus, {})

        # Create list of items
        items = list(refs.keys())

        if "No Reference" not in items:
            items.insert(0, "No Reference")

        # Always ensure "Custom" is in the list
        if "Custom" not in items:
            items.append("Custom")

        self.combo_ref.addItems(items)

        target_ref = None

        # A previous selection that did not come from "All" mode
        if current_ref and current_ref in items and current_ref != "No Reference":
            target_ref = current_ref
        # Otherwise the remembered reference
        elif self.last_ref_name and self.last_ref_name in items:
            target_ref = self.last_ref_name
        # Otherwise the default, TMS
        elif "TMS" in items:
            target_ref = "TMS"
        # Otherwise whatever is first
        elif items:
            target_ref = items[0]

        if target_ref:
            self.combo_ref.setCurrentText(target_ref)
            ref_data = refs.get(target_ref, {"delta_ref": 0.0, "sigma_ref": 0.0})
        else:
            self.combo_ref.setCurrentText("Custom")
            ref_data = {"delta_ref": 0.0, "sigma_ref": 0.0}

        # Update internal values
        self.delta_ref = ref_data.get("delta_ref", 0.0)
        self.sigma_ref = ref_data.get("sigma_ref", 0.0)

        # Update spinboxes (these updates won't trigger recalc since we blocked combo signals)
        self.spin_delta_ref.blockSignals(True)
        self.spin_sigma_ref.blockSignals(True)
        self.spin_delta_ref.setValue(self.delta_ref)
        self.spin_sigma_ref.setValue(self.sigma_ref)
        # Only allow editing if "Custom" is selected
        is_custom = self.combo_ref.currentText() == "Custom"
        self.spin_delta_ref.setEnabled(is_custom)
        self.spin_sigma_ref.setEnabled(is_custom)
        self.spin_delta_ref.blockSignals(False)
        self.spin_sigma_ref.blockSignals(False)

        # Auto Range if "No Reference" is active
        if self.combo_ref.currentText() == "No Reference":
            if getattr(self, "chk_auto_x", None) is not None:
                self.chk_auto_x.setChecked(True)

        # Re-enable combo signals
        self.combo_ref.blockSignals(False)

    def on_ref_change(self):
        """Handle reference standard change"""
        current_nucleus = self.current_nucleus
        if current_nucleus != "All":
            self.last_ref_name = self.combo_ref.currentText()
        if current_nucleus == "All":
            # Force 0,0 for All view
            self.delta_ref = 0.0
            self.sigma_ref = 0.0
            self.recalc()
            return

        # Map atom symbol to nucleus key
        nucleus_key = self.get_nucleus_key(current_nucleus)

        ref_name = self.combo_ref.currentText()
        self.last_ref_name = ref_name

        # Auto Range if "No Reference" is selected
        if ref_name == "No Reference":
            if getattr(self, "chk_auto_x", None) is not None:
                self.chk_auto_x.setChecked(True)

        refs = self.reference_standards.get(nucleus_key, {})
        ref_data = refs.get(ref_name, {"delta_ref": 0.0, "sigma_ref": 0.0})

        self.delta_ref = ref_data["delta_ref"]
        self.sigma_ref = ref_data["sigma_ref"]

        # Block signals only during setValue to prevent triggering on_ref_value_change
        self.spin_delta_ref.blockSignals(True)
        self.spin_sigma_ref.blockSignals(True)
        self.spin_delta_ref.setValue(self.delta_ref)
        self.spin_sigma_ref.setValue(self.sigma_ref)
        self.spin_delta_ref.blockSignals(False)
        self.spin_sigma_ref.blockSignals(False)

        # Only allow editing if "Custom" is selected
        is_custom = ref_name == "Custom"
        self.spin_delta_ref.setEnabled(is_custom)
        self.spin_sigma_ref.setEnabled(is_custom)

        self.recalc()

    def on_ref_value_change(self):
        """Handle manual reference value changes"""
        self.delta_ref = self.spin_delta_ref.value()
        self.sigma_ref = self.spin_sigma_ref.value()

        # Update the reference standard dict
        current_nucleus = self.current_nucleus
        ref_name = self.combo_ref.currentText()
        if current_nucleus != "All":
            # Map atom symbol to nucleus key
            nucleus_key = self.get_nucleus_key(current_nucleus)
            if nucleus_key not in self.reference_standards:
                self.reference_standards[nucleus_key] = {}
            self.reference_standards[nucleus_key][ref_name] = {
                "delta_ref": self.delta_ref,
                "sigma_ref": self.sigma_ref,
            }
        self.recalc()

    def add_custom_reference(self):
        """Add a custom reference standard using custom dialog"""
        # Get available nuclei from data
        available_nuclei = sorted(list(set([d["atom_sym"] for d in self.data])))
        if not available_nuclei:
            available_nuclei = ["1H", "13C", "15N", "31P", "19F"]

        # Show custom dialog
        dialog = CustomReferenceDialog(self, available_nuclei)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        ref_name, nucleus_data = dialog.get_reference_data()

        # Add to standards for each nucleus
        for raw_nucleus, values in nucleus_data.items():
            # Ensure we use the standardized key (e.g. "H" -> "1H")
            nucleus = self.get_nucleus_key(raw_nucleus)

            if nucleus not in self.reference_standards:
                self.reference_standards[nucleus] = {}
            self.reference_standards[nucleus][ref_name] = values

        self.save_settings()

        # If we are in "All" mode, switch to the specific nucleus of the added reference
        # This prevents the reference from being hidden by the "All" restriction ("No Reference" only)
        if self.current_nucleus == "All" and nucleus_data:
            # Pick the first nucleus added (e.g. "1H" or "H")
            first_raw = list(nucleus_data.keys())[0]
            # Get the key used for buttons (e.g. "H" or "1H") - keys in nucleus_buttons usually match atom symbols
            # We should try to find the matching button.
            target_btn_key = None

            # Try standardized key first
            std_key = self.get_nucleus_key(first_raw)
            if std_key in self.nucleus_buttons:
                target_btn_key = std_key
            elif first_raw in self.nucleus_buttons:
                target_btn_key = first_raw

            if target_btn_key:
                self.nucleus_buttons[target_btn_key].setChecked(True)
                # Manually trigger the mode change handler since setChecked might not if via code (depends on signal)
                # But usually the group handles it. Let's explicitly call the update to be safe.
                self.current_nucleus = target_btn_key

        # Force UI update
        self.update_reference_combo()

        # Select the new reference
        # Now that we switched inputs (if we were in All), this should work
        self.combo_ref.setCurrentText(ref_name)

        notify(
            self,
            f"Added reference '{ref_name}' for {len(nucleus_data)} nucleus/nuclei.",
            5000,
        )

    def delete_custom_reference(self):
        """Delete currently selected custom reference"""
        if not getattr(self, "current_nucleus", None):
            return

        current_nucleus = (
            self.get_nucleus_key(self.current_nucleus)
            if self.current_nucleus != "All"
            else "1H"
        )
        current_ref = self.combo_ref.currentText()

        if not current_ref:
            return

        # Built-ins come from the one shared table; a second hand-kept copy
        # here could drift out of sync with it.
        is_default = current_ref in DEFAULT_REFERENCE_STANDARDS.get(
            current_nucleus, {}
        )

        # Prevent deletion of "Custom" placeholder AND "No Reference"
        if current_ref in ["Custom", "No Reference"]:
            is_default = True

        if is_default:
            QMessageBox.warning(
                self,
                "Cannot Delete",
                f"'{current_ref}' is a built-in standard and cannot be deleted.",
            )
            return

        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Are you sure you want to delete custom reference '{current_ref}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            # Remove from storage
            if current_nucleus in self.reference_standards:
                if current_ref in self.reference_standards[current_nucleus]:
                    del self.reference_standards[current_nucleus][current_ref]
                    self.save_settings()
                    self.update_reference_combo()
                    self.recalc()
                    notify(self, f"Reference '{current_ref}' removed.", 5000)
                else:
                    # Should not happen if UI is consistent
                    QMessageBox.warning(
                        self, "Error", "Reference not found in storage."
                    )

    def on_spectrum_settings_change(self):
        """Handle spectrum visualization parameter changes"""
        self.linewidth = self.spin_linewidth.value()
        self.peak_intensity = self.spin_intensity.value()
        self.save_settings()
        self.plot_spectrum()

    def on_nucleus_changed(self, nucleus):
        """Handle nucleus button toggle"""
        self.current_nucleus = nucleus
        self.clear_peak_selection()

        if getattr(self, "chk_auto_x", None) is not None:
            self.chk_auto_x.blockSignals(True)
            self.chk_auto_x.setChecked(False)
            self.chk_auto_x.blockSignals(False)

            # Auto Range is now off, so the spin boxes take over
            if getattr(self, "spin_x_max", None) is not None:
                self.spin_x_max.setEnabled(True)
            if getattr(self, "spin_x_min", None) is not None:
                self.spin_x_min.setEnabled(True)

        # Update default X range for this nucleus
        self.update_x_range_defaults(nucleus)

        # Coupling simulation stays off until the user ticks "Simulate Coupling":
        # nmrsim is too slow on large molecules and merged peaks to run unasked.
        self.apply_filter()

    def update_x_range_defaults(self, nucleus):
        """Set appropriate default X range based on nucleus type"""
        if getattr(self, "spin_x_max", None) is None or not hasattr(self, "spin_x_min"):
            return

        # Block signals to prevent intermediate plotting (fixes "slope graph" artifact)
        self.spin_x_max.blockSignals(True)
        self.spin_x_min.blockSignals(True)

        # Ranges based on typical chemical shifts (ppm)
        defaults = {
            "H": (12, -1),
            "1H": (12, -1),
            "C": (220, -10),
            "13C": (220, -10),
            "N": (500, -400),
            "15N": (500, -400),
            "F": (0, -200),
            "19F": (0, -200),
            "P": (250, -150),
            "31P": (250, -150),
            "O": (1000, -500),
            "17O": (1000, -500),
            "Si": (100, -200),
            "29Si": (100, -200),
            "Pt": (1000, -5000),
        }

        # Get nucleus key to handle isotopes
        key = self.get_nucleus_key(nucleus)
        # Try key, then generic element (e.g. "Pt" if "195Pt" not in defaults), then fallback

        # Keep both cases: stripping lowercase would reduce a two-letter symbol
        # to its first letter, so "195Pt" became "P" and platinum silently
        # inherited phosphorus's window.
        element_only = re.sub(r"[^A-Za-z]", "", key)

        range_vals = defaults.get(key, defaults.get(element_only, (500, -500)))

        self.spin_x_max.setValue(range_vals[0])
        self.spin_x_min.setValue(range_vals[1])

        self.spin_x_max.blockSignals(False)
        self.spin_x_min.blockSignals(False)

    def apply_filter(self):
        """Filter data by nucleus and update UI"""
        if getattr(self, "table", None) is None:
            return

        nucleus = self.current_nucleus
        if nucleus == "All":
            self.displayed_data = self.data
        else:
            self.displayed_data = [d for d in self.data if d["atom_sym"] == nucleus]

        # Update reference combo for this nucleus
        self.update_reference_combo()

        # Force recalculation with new reference values
        self.recalc()

    def recalc(self):
        """Recalculate chemical shifts and update table + spectrum"""
        # Get merged peak groups
        merged_indices = set()
        for group in self.merged_peaks:
            merged_indices.update(group["indices"])

        # Track which rows to display
        rows_to_display = []

        # Add merged peaks first
        for group in self.merged_peaks:
            # Calculate averages dynamically based on current reference
            total_sigma = 0.0
            total_delta = 0.0
            count = 0

            for atom_idx in group["indices"]:
                item = next(
                    (d for d in self.data if d.get("atom_idx", None) == atom_idx), None
                )
                if item:
                    sigma = item.get("shielding", 0.0)
                    delta = self.delta_ref + (self.sigma_ref - sigma)
                    total_sigma += sigma
                    total_delta += delta
                    count += 1

            if count > 0:
                avg_sigma = total_sigma / count
                avg_delta = total_delta / count

                # Check if any atoms in this group are in displayed_data
                group_items = [
                    item
                    for item in self.displayed_data
                    if item.get("atom_idx", -1) in group["indices"]
                ]
                if group_items:
                    rows_to_display.append(
                        (
                            "merged",
                            {
                                "indices": group["indices"],
                                "avg_sigma": avg_sigma,
                                "avg_delta": avg_delta,
                            },
                            group_items,
                        )
                    )

        # Add individual non-merged items
        for item in self.displayed_data:
            if item.get("atom_idx", -1) not in merged_indices:
                rows_to_display.append(("individual", item, None))

        # Update table
        self.table.setRowCount(len(rows_to_display))

        for r, row_data in enumerate(rows_to_display):
            row_type, data, group_items = row_data

            if row_type == "merged":
                # Display merged group
                indices_str = ", ".join([str(idx) for idx in data["indices"]])
                self.table.setItem(r, 0, QTableWidgetItem(f"[{indices_str}]"))
                self.table.setItem(
                    r,
                    1,
                    QTableWidgetItem(
                        f"{len(data['indices'])}{group_items[0].get('atom_sym', '')}"
                    ),
                )
                self.table.setItem(r, 2, QTableWidgetItem(f"{data['avg_sigma']:.2f}"))
                self.table.setItem(r, 3, QTableWidgetItem(f"{data['avg_delta']:.2f}"))
            else:
                # Display individual item
                self.table.setItem(
                    r, 0, QTableWidgetItem(str(data.get("atom_idx", "")))
                )
                self.table.setItem(r, 1, QTableWidgetItem(data.get("atom_sym", "")))

                sigma_sample = data.get("shielding", 0.0)
                self.table.setItem(r, 2, QTableWidgetItem(f"{sigma_sample:.2f}"))

                # Chemical shift: δ = δ_ref + (σ_ref - σ_sample)
                delta_sample = self.delta_ref + (self.sigma_ref - sigma_sample)
                self.table.setItem(r, 3, QTableWidgetItem(f"{delta_sample:.2f}"))


        self.plot_spectrum()

    def reset_selection(self):
        """Reset all NMR selection state — call this on document reset."""
        self.sel_timer.stop()
        try:
            self.clear_peak_selection()
        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning(
                "NMR: could not clear the peak selection on document reset: %s", e
            )
        self._last_synced_mw_selection = frozenset()
        self.sel_timer.start(200)

    def reject(self):
        """Esc hides a QDialog without closeEvent — route it through
        close() so the unsaved-merge prompt fires and sel_timer stops
        instead of polling a hidden dialog forever."""
        self.close()

    def closeEvent(self, event):
        """Clean up labels and stop polling timer when dialog closes"""
        # Stop the polling timer first so it cannot fire on a dead widget
        self.sel_timer.stop()

        # Merges are never auto-saved: ask the user to save or discard.
        if getattr(self, "_merged_dirty", False):
            reply = QMessageBox.question(
                self,
                "Unsaved Merged Peaks",
                "Merged peak changes have not been saved.\nSave them now?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard,
            )
            if reply == QMessageBox.StandardButton.Save:
                if not self.save_merged_peaks():
                    self.sel_timer.start(200)
                    event.ignore()
                    return
            self._merged_dirty = False

        self.save_settings()
        self.clear_atom_labels()
        # accept() not super().closeEvent(): QDialog.closeEvent calls reject(),
        # which is routed back through close() and would recurse.
        event.accept()

    def keyPressEvent(self, event):
        """Intercept Enter/Return keys to prevent dialog reset or closure"""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Focus on parent to clear focus from spinboxes but don't close
            self.setFocus()
            event.accept()
            return
        super().keyPressEvent(event)
