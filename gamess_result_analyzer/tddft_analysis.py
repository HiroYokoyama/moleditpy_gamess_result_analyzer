"""TD-DFT dialog: absorption/CD spectrum plot, gauge selection and transition details."""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QDoubleSpinBox,
    QCheckBox,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QGroupBox,
    QComboBox,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QWidget,
    QSplitter,
)
from PySide6.QtCore import Qt
import os

try:
    from .spectrum_widget import SpectrumWidget
except ImportError:
    try:
        from spectrum_widget import SpectrumWidget
    except ImportError:
        SpectrumWidget = None
from .utils import get_default_export_path, notify
from .settings import load_section, save_section
import datetime
import logging

#: SpectrumWidget's lineshape is exp(-((x-x0)/sigma)^2), whose full width at
#: half maximum is 2*sqrt(ln 2)*sigma. The familiar 2.355 (=2*sqrt(2 ln 2))
#: belongs to the exp(-(x-x0)^2/(2 sigma^2)) form instead, and using it here
#: made every band a factor of sqrt(2) narrower than the requested FWHM.
FWHM_TO_SIGMA = 1.6651092223153954


class TDDFTDialog(QDialog):
    """Dialog plotting TD-DFT/TDA absorption or CD spectra with transition details."""

    def __init__(self, parent, excitations):
        super().__init__(parent)
        self.setWindowTitle("TDDFT Spectrum")
        self.resize(1100, 720)
        self.excitations = excitations
        self.settings_file = os.path.join(os.path.dirname(__file__), "settings.json")
        self.prev_sigma_unit_idx = 0  # 0: cm-1, 1: eV
        self._current_item = None  # Currently selected excitation

        # ── Top-level layout: horizontal splitter (spectrum | info panel) ──
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(self._splitter)

        # ── LEFT SIDE: spectrum + controls ──
        left_widget = QWidget()
        left_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        main_layout = QVBoxLayout(left_widget)
        main_layout.setContentsMargins(0, 0, 4, 0)

        # 0. Spectrum Display
        if SpectrumWidget:
            self.spectrum = SpectrumWidget(
                self.excitations,
                x_key="energy_nm",
                y_key="osc",
                x_unit="Wavelength (nm)",
                sigma=3000.0,
            )
            self.spectrum.show_legend = False
            main_layout.addWidget(self.spectrum)
            self.spectrum.clicked.connect(self.on_spectrum_click)
        else:
            main_layout.addWidget(QLabel("SpectrumWidget not available."))
            self.spectrum = None

        # 1. Spectrum Settings Section
        settings_group = QGroupBox("Spectrum Settings")
        settings_vbox = QVBoxLayout(settings_group)

        # Row 2: Sigma & Sticks
        row2_layout = QHBoxLayout()
        row2_layout.addWidget(QLabel("Broadening (FWHM):"))
        self.spin_sigma = QDoubleSpinBox()
        self.spin_sigma.setRange(0.1, 10000.0)
        self.spin_sigma.setValue(3000.0)
        self.spin_sigma.setSingleStep(10.0)
        self.spin_sigma.valueChanged.connect(self.update_spectrum_sigma)
        row2_layout.addWidget(self.spin_sigma)

        self.combo_sigma_unit = QComboBox()
        self.combo_sigma_unit.addItems(["cm⁻¹", "eV"])
        self.combo_sigma_unit.setCurrentIndex(0)
        self.combo_sigma_unit.currentIndexChanged.connect(self.on_sigma_unit_changed)
        row2_layout.addWidget(self.combo_sigma_unit)

        row2_layout.addSpacing(20)
        self.chk_sticks = QCheckBox("Show Transitions (Sticks)")
        self.chk_sticks.setChecked(True)
        if self.spectrum:
            self.chk_sticks.stateChanged.connect(self.spectrum.set_sticks)
        row2_layout.addWidget(self.chk_sticks)

        self.chk_markers = QCheckBox("Markers")
        self.chk_markers.setChecked(True)
        if self.spectrum:
            self.chk_markers.stateChanged.connect(self.spectrum.set_markers)
        row2_layout.addWidget(self.chk_markers)

        row2_layout.addStretch()

        # Reset Button
        self.btn_reset = QPushButton("Reset Defaults")
        self.btn_reset.clicked.connect(self.reset_defaults)
        row2_layout.addWidget(self.btn_reset)

        settings_vbox.addLayout(row2_layout)

        # Row 3: Physical Broadening
        row3_layout = QHBoxLayout()
        self.chk_physical = QCheckBox("Physical (Area-preserving) Broadening")
        self.chk_physical.setChecked(True)
        self.chk_physical.toggled.connect(self.switch_spectrum_type)
        row3_layout.addWidget(self.chk_physical)
        row3_layout.addStretch()
        settings_vbox.addLayout(row3_layout)

        main_layout.addWidget(settings_group)

        # 2. Axis Controls Section
        axis_group = QGroupBox("Axis Scale")
        axis_layout = QVBoxLayout(axis_group)

        # X-Range
        x_row = QHBoxLayout()
        self.chk_auto_x = QCheckBox("Auto Wavelength Range")
        self.chk_auto_x.setChecked(True)
        self.chk_auto_x.stateChanged.connect(self.toggle_auto_x)
        x_row.addWidget(self.chk_auto_x)

        x_row.addWidget(QLabel("Range (nm):"))
        self.spin_x_min = QDoubleSpinBox()
        self.spin_x_min.setRange(0, 50000)
        self.spin_x_min.setValue(100)
        self.spin_x_min.setEnabled(False)
        self.spin_x_min.setFixedWidth(80)
        self.spin_x_min.valueChanged.connect(self.update_x_range)
        x_row.addWidget(self.spin_x_min)

        x_row.addWidget(QLabel("-"))
        self.spin_x_max = QDoubleSpinBox()
        self.spin_x_max.setRange(0, 50000)
        self.spin_x_max.setValue(800)
        self.spin_x_max.setEnabled(False)
        self.spin_x_max.setFixedWidth(80)
        self.spin_x_max.valueChanged.connect(self.update_x_range)
        x_row.addWidget(self.spin_x_max)
        x_row.addStretch()
        axis_layout.addLayout(x_row)

        # Y-Range
        y_row = QHBoxLayout()
        self.chk_auto_y = QCheckBox("Auto Intensity Range  ")
        self.chk_auto_y.setChecked(True)
        self.chk_auto_y.stateChanged.connect(self.toggle_auto_y)
        y_row.addWidget(self.chk_auto_y)

        y_row.addWidget(QLabel("Range:"))
        self.spin_y_min = QDoubleSpinBox()
        self.spin_y_min.setRange(-10000, 10000)
        self.spin_y_min.setValue(0)
        self.spin_y_min.setEnabled(False)
        self.spin_y_min.setFixedWidth(80)
        self.spin_y_min.valueChanged.connect(self.update_range)
        y_row.addWidget(self.spin_y_min)

        y_row.addWidget(QLabel("-"))
        self.spin_y_max = QDoubleSpinBox()
        self.spin_y_max.setRange(-10000, 10000)
        self.spin_y_max.setValue(1.0)
        self.spin_y_max.setEnabled(False)
        self.spin_y_max.setFixedWidth(80)
        self.spin_y_max.valueChanged.connect(self.update_range)
        y_row.addWidget(self.spin_y_max)
        y_row.addStretch()
        axis_layout.addLayout(y_row)

        main_layout.addWidget(axis_group)

        # 3. Actions Section
        action_layout = QHBoxLayout()

        self.btn_png = QPushButton("Export Image (PNG)")
        self.btn_png.clicked.connect(self.save_png)
        action_layout.addWidget(self.btn_png)

        self.btn_csv = QPushButton("Export Data (CSV)")
        self.btn_csv.clicked.connect(self.save_csv)
        action_layout.addWidget(self.btn_csv)

        self.btn_sticks = QPushButton("Export Sticks (CSV)")
        self.btn_sticks.clicked.connect(self.save_sticks)
        action_layout.addWidget(self.btn_sticks)

        self.btn_report = QPushButton("Export Full Data (.txt)")
        self.btn_report.clicked.connect(self.save_gamess_report)
        action_layout.addWidget(self.btn_report)

        action_layout.addStretch()

        self.btn_close = QPushButton("Close")
        self.btn_close.setFixedWidth(100)
        self.btn_close.clicked.connect(self.close)
        action_layout.addWidget(self.btn_close)

        main_layout.addLayout(action_layout)

        # ── RIGHT SIDE: transition info panel ──
        right_widget = self._build_info_panel()

        self._splitter.addWidget(left_widget)
        self._splitter.addWidget(right_widget)
        # Left gets ~70%, right ~30%
        self._splitter.setSizes([770, 330])
        self._splitter.setCollapsible(0, False)
        self._splitter.setCollapsible(1, False)

        self.load_settings()
        self.switch_spectrum_type()

    # ── Info-panel construction ─────────────────────────────────────────────

    def _build_info_panel(self):
        """Build the right-side panel that shows selected transition details."""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setMinimumWidth(260)
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(6)

        # ── Header ──
        hdr = QLabel("Transition Details")
        hdr.setStyleSheet(
            "font-size: 11pt; font-weight: bold; color: #0066cc;"
            " border-bottom: 2px solid #0066cc; padding-bottom: 4px;"
        )
        vbox.addWidget(hdr)

        # ── Placeholder when nothing selected ──
        self._info_placeholder = QLabel(
            "Click on a stick or spectrum\npeak to see details here."
        )
        self._info_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._info_placeholder.setStyleSheet(
            "color: #999; font-size: 9.5pt; font-style: italic;"
        )
        self._info_placeholder.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        vbox.addWidget(self._info_placeholder)

        # ── State header label (hidden until selection) ──
        self._lbl_state = QLabel()
        self._lbl_state.setStyleSheet(
            "font-size: 10.5pt; font-weight: bold; color: #003380;"
        )
        self._lbl_state.setWordWrap(True)
        self._lbl_state.hide()
        vbox.addWidget(self._lbl_state)

        # ── Separator ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.hide()
        self._sep1 = sep
        vbox.addWidget(sep)

        # ── Energy / strength grid ──
        self._energy_widget = QWidget()
        self._energy_layout = QVBoxLayout(self._energy_widget)
        self._energy_layout.setContentsMargins(0, 0, 0, 0)
        self._energy_layout.setSpacing(2)
        self._energy_widget.hide()
        vbox.addWidget(self._energy_widget)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        sep2.hide()
        self._sep2 = sep2
        vbox.addWidget(sep2)

        # ── Orbital contributions (scrollable) ──
        contrib_hdr = QLabel("Orbital Contributions")
        contrib_hdr.setStyleSheet("font-weight: bold; font-size: 9.5pt; color: #444;")
        contrib_hdr.hide()
        self._contrib_hdr = contrib_hdr
        vbox.addWidget(contrib_hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.hide()
        self._scroll = scroll

        self._contrib_label = QLabel()
        self._contrib_label.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self._contrib_label.setWordWrap(False)
        self._contrib_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._contrib_label.setStyleSheet(
            "font-family: monospace; font-size: 9pt; color: #222;"
            " background: #f9f9f9; padding: 4px;"
        )
        scroll.setWidget(self._contrib_label)
        vbox.addWidget(scroll, 1)  # stretch=1 so it fills remaining space

        return panel

    def _clear_info_panel(self):
        """Reset the right panel to placeholder state."""
        self._info_placeholder.show()
        self._lbl_state.hide()
        self._sep1.hide()
        self._energy_widget.hide()
        self._sep2.hide()
        self._contrib_hdr.hide()
        self._scroll.hide()

    def _populate_info_panel(self, item):
        """Fill the right panel with data from the selected excitation *item*."""
        if item is None:
            self._clear_info_panel()
            return

        state = item.get("state", "?")
        energy_ev = item.get("energy_ev", 0.0) or 0.0
        energy_nm = item.get("energy_nm", 0.0) or 0.0
        energy_cm = item.get("energy_cm", 0.0) or 0.0
        if energy_cm <= 0 and energy_nm > 0:
            energy_cm = 1e7 / energy_nm
        if energy_ev <= 0 and energy_nm > 0:
            # hc/e in eV*nm; the export path already did this, so the detail
            # panel used to be the only place showing "0.0000 eV".
            energy_ev = 1239.84193 / energy_nm


        # ── State header ──
        self._info_placeholder.hide()
        self._lbl_state.setText(f"Excited State {state}")
        self._lbl_state.show()
        self._sep1.show()

        # ── Energy rows ──
        # Clear old rows
        while self._energy_layout.count():
            item_w = self._energy_layout.takeAt(0)
            if item_w.widget():
                item_w.widget().deleteLater()

        def _row(label_text, value_text, highlight=False):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #555; font-size: 9pt;")
            val = QLabel(value_text)
            style = "font-size: 9pt; font-weight: bold;"
            if highlight:
                style += " color: #0055bb;"
            val.setStyleSheet(style)
            val.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(lbl)
            row.addStretch()
            row.addWidget(val)
            container = QWidget()
            container.setLayout(row)
            return container

        self._energy_layout.addWidget(_row("Energy (eV):", f"{energy_ev:.4f} eV", True))
        self._energy_layout.addWidget(_row("Wavelength:", f"{energy_nm:.2f} nm", True))
        if energy_cm > 0:
            self._energy_layout.addWidget(_row("Wavenumber:", f"{energy_cm:.1f} cm⁻¹"))

        # Oscillator / Rotatory strengths
        osc_len = item.get("osc_len", item.get("osc", None))

        def _fmt(v):
            return f"{v:.6f}" if v is not None else "N/A"

        self._energy_layout.addWidget(
            _row("Oscillator Strength  f:", _fmt(osc_len), True)
        )

        self._energy_widget.show()
        self._sep2.show()

        # ── Orbital contributions ──
        transitions = item.get("transitions", [])
        if transitions and len(transitions) > 0:
            self._contrib_hdr.show()
            self._contrib_label.setText("\n".join(str(t) for t in transitions))
            self._scroll.show()
        else:
            self._contrib_hdr.hide()
            self._contrib_label.setText("")
            self._scroll.hide()

    # ── Existing methods (unchanged logic, dialog removed) ──────────────────

    def reject(self):
        """Route Esc through close() so closeEvent cleanup always runs."""
        # Esc must run closeEvent cleanup (QDialog.reject only hides)
        self.close()

    def closeEvent(self, event):
        """Save settings before the dialog closes."""
        self.save_settings()
        # accept() not super().closeEvent(): QDialog.closeEvent calls reject(),
        # which is routed back through close() and would recurse.
        event.accept()

    def load_settings(self):
        """Restore the saved broadening, sigma unit and toggle preferences."""
        if os.path.exists(self.settings_file):
            settings = load_section(self.settings_file, "tddft_settings")
            try:
                self.spin_sigma.blockSignals(True)
                self.combo_sigma_unit.blockSignals(True)

                if "sigma" in settings:
                    self.spin_sigma.setValue(float(settings["sigma"]))

                if "sigma_unit_idx" in settings:
                    idx = int(settings["sigma_unit_idx"])
                    self.combo_sigma_unit.setCurrentIndex(idx)
                    self.prev_sigma_unit_idx = idx
                    self.spin_sigma.setSingleStep(0.1 if idx == 1 else 10.0)

                if "show_sticks" in settings:
                    self.chk_sticks.setChecked(bool(settings["show_sticks"]))

                if "physical" in settings:
                    self.chk_physical.setChecked(bool(settings["physical"]))

                self.spin_sigma.blockSignals(False)
                self.combo_sigma_unit.blockSignals(False)

            except (TypeError, ValueError) as e:
                self.spin_sigma.blockSignals(False)
                self.combo_sigma_unit.blockSignals(False)
                logging.warning("[tddft_analysis.py] Error loading settings: %s", e)

    def update_spectrum_sigma(self):
        """Convert the FWHM spin box value to sigma and apply it to the spectrum."""
        if not self.spectrum:
            return
        val = self.spin_sigma.value()
        unit_idx = self.combo_sigma_unit.currentIndex()

        fwhm_cm = val
        if unit_idx == 1:  # eV
            fwhm_cm = val * 8065.544

        self.spectrum.sigma = fwhm_cm / FWHM_TO_SIGMA
        self.spectrum.broaden_in_energy = True
        self.spectrum.update()

    def on_sigma_unit_changed(self):
        """Convert the broadening value between cm-1 and eV when the unit combo changes."""
        curr_idx = self.combo_sigma_unit.currentIndex()
        if curr_idx == self.prev_sigma_unit_idx:
            return

        val = self.spin_sigma.value()
        if self.prev_sigma_unit_idx == 0 and curr_idx == 1:
            new_val = val / 8065.544
        elif self.prev_sigma_unit_idx == 1 and curr_idx == 0:
            new_val = val * 8065.544
        else:
            new_val = val

        self.spin_sigma.blockSignals(True)
        self.spin_sigma.setValue(new_val)
        self.spin_sigma.setSingleStep(0.1 if curr_idx == 1 else 10.0)
        self.spin_sigma.blockSignals(False)

        self.prev_sigma_unit_idx = curr_idx
        self.update_spectrum_sigma()
        self.switch_spectrum_type()

    def save_settings(self):
        """Persist the broadening, sigma unit and toggle preferences."""
        tddft_settings = {
            "sigma": self.spin_sigma.value(),
            "sigma_unit_idx": self.combo_sigma_unit.currentIndex(),
            "show_sticks": self.chk_sticks.isChecked(),
            "physical": self.chk_physical.isChecked(),
        }

        try:
            os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        except OSError as e:
            logging.warning("[tddft_analysis.py:save_settings] Error: %s", e)
            return

        if save_section(self.settings_file, "tddft_settings", tddft_settings):
            notify(self, "TDDFT settings saved.", 3000)

    def toggle_auto_y(self):
        """Enable/disable the manual Y-range spin boxes and apply the current mode."""
        is_auto = self.chk_auto_y.isChecked()
        self.spin_y_min.setEnabled(not is_auto)
        self.spin_y_max.setEnabled(not is_auto)
        if is_auto and self.spectrum:
            self.spectrum.set_auto_range()
        else:
            self.update_range()

    def update_range(self):
        """Apply the manual Y-range spin box values to the spectrum."""
        if self.chk_auto_y.isChecked() or not self.spectrum:
            return
        ymin = self.spin_y_min.value()
        ymax = self.spin_y_max.value()
        self.spectrum.set_y_range(ymin, ymax)

    def toggle_auto_x(self):
        """Enable/disable the manual X-range spin boxes and apply the current mode."""
        is_auto = self.chk_auto_x.isChecked()
        self.spin_x_min.setEnabled(not is_auto)
        self.spin_x_max.setEnabled(not is_auto)
        if is_auto and self.spectrum:
            self.spectrum.set_auto_x_range()
        else:
            self.update_x_range()

    def update_x_range(self):
        """Apply the manual X-range spin box values to the spectrum."""
        if self.chk_auto_x.isChecked() or not self.spectrum:
            return
        xmin = self.spin_x_min.value()
        xmax = self.spin_x_max.value()
        self.spectrum.set_x_range(xmin, xmax)

    def save_png(self):
        """Prompt for a path and export the spectrum plot as a PNG."""
        if not self.spectrum:
            return
        default_path = get_default_export_path(
            self.parent().file_path, suffix="_tddft", extension=".png"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Graph", default_path, "Images (*.png)"
        )
        if path:
            try:
                self.spectrum.save_png(path)
            except (OSError, ValueError, RuntimeError) as exc:
                logging.warning("TD-DFT spectrum: image export failed: %s", exc)
                QMessageBox.critical(
                    self, "Export Error", f"Could not save image:\n{exc}"
                )

    def save_csv(self):
        """Prompt for a path and export the broadened curve data to CSV."""
        if not self.spectrum:
            return
        default_path = get_default_export_path(
            self.parent().file_path, suffix="_tddft", extension=".csv"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Data", default_path, "CSV Files (*.csv)"
        )
        if path:
            success = self.spectrum.save_csv(path)
            if success:
                notify(self, f"Data saved to {path}", 5000)
            else:
                QMessageBox.warning(self, "Error", "Failed to save CSV.")

    def save_sticks(self):
        """Prompt for a path and export the raw stick transition data to CSV."""
        if not self.spectrum:
            return
        default_path = get_default_export_path(
            self.parent().file_path, suffix="_tddft_sticks", extension=".csv"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Sticks", default_path, "CSV Files (*.csv)"
        )
        if path:
            success = self.spectrum.save_sticks_csv(path)
            if success:
                notify(self, f"Stick data saved to {path}", 5000)
            else:
                QMessageBox.warning(self, "Error", "Failed to export stick data.")

    def save_gamess_report(self):
        """Prompt for a path and write a full plain-text excitation report."""
        if not self.excitations:
            QMessageBox.warning(self, "No Data", "No excitation data to export.")
            return

        default_path = get_default_export_path(
            self.parent().file_path, suffix="_tddft_report", extension=".txt"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report", default_path, "Text Files (*.txt)"
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                # --- Header ---
                f.write("=" * 80 + "\n")
                f.write("             GAMESS TD-DFT / TDA EXCITATION SPECTRUM ANALYSIS\n")
                f.write("=" * 80 + "\n")
                f.write(
                    f"Generated on: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                f.write("-" * 80 + "\n")
                f.write("UNITS & DEFINITIONS:\n")
                f.write("  Energy      : eV, nm, cm^-1\n")
                f.write("  f (Osc)     : Oscillator Strength (Dimensionless)\n")
                f.write("  R (Rot)     : Rotatory Strength   (10^-40 esu^2 cm^2)\n")
                f.write("  Gauge       : L = Length (Dipole), V = Velocity\n")
                f.write("-" * 80 + "\n\n")

                for item in self.excitations:
                    state = item.get("state", "?")

                    if str(state) == "0":
                        continue

                    energy_ev = item.get("energy_ev", 0.0)
                    energy_nm = item.get("energy_nm", 0.0)
                    energy_cm = item.get("energy_cm", 0.0)

                    if energy_cm == 0.0 and energy_nm > 0:
                        energy_cm = 10000000.0 / energy_nm
                    if energy_ev == 0.0 and energy_nm > 0:
                        energy_ev = 1239.84193 / energy_nm

                    f_len = item.get("osc_len", item.get("osc", None))

                    def fmt_val(v):
                        return f"{v:>9.6f}" if v is not None else "      N/A"

                    f.write(
                        f"STATE {state:>3}  --------------------------------------------------------\n"
                    )
                    f.write(
                        f"  Energy : {energy_ev:>8.4f} eV  |  {energy_nm:>8.2f} nm  |  {energy_cm:>8.1f} cm^-1\n"
                    )

                    f.write(f"  f (Osc): {fmt_val(f_len)}\n")

                    f.write("\n  Major Transitions:\n")

                    transitions = item.get("transitions", [])
                    if isinstance(transitions, list):
                        if not transitions:
                            f.write("    (No transition details available)\n")
                        for trans in transitions:
                            f.write(f"    {trans}\n")
                    else:
                        f.write(f"    {transitions}\n")

                    f.write("\n")

            if not notify(self, f"Report saved to {path}", 5000):
                QMessageBox.information(self, "Exported", f"Report saved to:\n{path}")

        # Qt slot: a slot must never crash the app (CONTRIBUTING.md 4B)
        except Exception as e:  # pylint: disable=broad-exception-caught
            QMessageBox.critical(self, "Error", f"Failed to save report:\n{e}")

    def reset_defaults(self):
        """Restore the default broadening, sigma unit and toggle settings."""
        self.spin_sigma.blockSignals(True)
        self.combo_sigma_unit.blockSignals(True)
        self.chk_sticks.blockSignals(True)
        self.chk_physical.blockSignals(True)

        self.spin_sigma.setValue(3000.0)
        self.spin_sigma.setRange(0.1, 10000.0)
        self.spin_sigma.setSingleStep(10.0)
        self.combo_sigma_unit.setCurrentIndex(0)
        self.prev_sigma_unit_idx = 0
        self.chk_sticks.setChecked(True)
        self.chk_physical.setChecked(True)

        self.spin_sigma.blockSignals(False)
        self.combo_sigma_unit.blockSignals(False)
        self.chk_sticks.blockSignals(False)
        self.chk_physical.blockSignals(False)

        self.switch_spectrum_type()

    def switch_spectrum_type(self):
        """
        Calculates processed_y (Intensity or Area) based on Physical/Gauge settings.
        Correctly handles CD energy dependence (R * Energy).
        """
        if not self.excitations:
            return
        if not self.spectrum:
            return

        is_physical = self.chk_physical.isChecked()
        sample = self.excitations[0] if self.excitations else {}
        target_key = "osc_len" if "osc_len" in sample else "osc"
        y_unit_sticks = "f (osc)"
        y_unit_main = "ε (M⁻¹ cm⁻¹)" if is_physical else "Intensity (Arb.)"

        ABS_FACTOR = 2.315e8

        processed_data = []

        for item in self.excitations:
            new_item = item.copy()
            val = item.get(target_key, 0.0)
            if val is None:
                val = 0.0

            if is_physical:
                new_item["processed_y"] = val * ABS_FACTOR
            else:
                new_item["processed_y"] = val

            processed_data.append(new_item)

        self.spectrum.data_list = processed_data
        self.spectrum.y_key = "processed_y"
        self.spectrum.y_key_sticks = target_key

        self.spectrum.y_unit = y_unit_main
        self.spectrum.y_unit_sticks = y_unit_sticks

        self.spectrum.normalization_mode = "area" if is_physical else "height"
        self.spectrum.broaden_in_energy = True

        self.spectrum.set_dual_axis(is_physical)
        self.update_spectrum_sigma()
        self.spectrum.update()

        # Refresh the panel if a transition is already selected (gauge/type may have changed)
        if self._current_item is not None:
            self._populate_info_panel(self._current_item)

    def on_spectrum_click(self, item):
        """Handle click on spectrum peak — show details in the right panel."""
        self._current_item = item
        self._populate_info_panel(item)
