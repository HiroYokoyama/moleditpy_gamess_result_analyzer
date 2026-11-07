"""Dialog for defining a custom NMR reference standard (per-nucleus delta/sigma)."""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QDoubleSpinBox,
    QGroupBox,
    QMessageBox,
    QComboBox,
    QScrollArea,
    QWidget,
)

from .wheel_guard import install_wheel_guard


class CustomReferenceDialog(QDialog):
    """Dialog for creating custom reference standards"""

    def __init__(self, parent, existing_nuclei=None):
        super().__init__(parent)
        self.setWindowTitle("Add Custom Reference Standard")
        self.resize(650, 550)  # Larger window for better visibility

        self.existing_nuclei = existing_nuclei or ["1H", "13C", "15N", "31P", "19F"]
        self.nucleus_widgets = []  # List of (nucleus, delta_spin, sigma_spin) tuples

        self.setup_ui()

    def setup_ui(self):
        """Build the reference-name field and per-nucleus delta/sigma rows."""
        main_layout = QVBoxLayout(self)

        # Combined reference configuration group
        config_group = QGroupBox("Reference Configuration")
        config_layout = QVBoxLayout(config_group)

        # Reference name at the top
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Reference Name:"))
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("e.g., Reference Compound Name")
        name_row.addWidget(self.edit_name)
        config_layout.addLayout(name_row)

        # Separator
        config_layout.addSpacing(10)
        config_layout.addWidget(QLabel("<b>Nucleus-Specific Values:</b>"))

        # Add explanation text
        explanation = QLabel(
            "<font color='gray'>"
            "• <b>δ_ref</b>: Reference peak position on the chemical shift scale (ppm)<br>"
            "• <b>σ_ref</b>: Isotropic shielding constant of the reference compound (ppm)"
            "</font>"
        )
        explanation.setWordWrap(True)
        config_layout.addWidget(explanation)
        config_layout.addSpacing(5)

        # Scroll area for multiple nuclei
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(200)

        scroll_widget = QWidget()
        self.nucleus_layout = QVBoxLayout(scroll_widget)
        scroll.setWidget(scroll_widget)
        install_wheel_guard(scroll_widget)

        config_layout.addWidget(scroll)

        # Add nucleus button
        btn_add_nucleus = QPushButton("+ Add Nucleus")
        btn_add_nucleus.clicked.connect(self.add_nucleus_row)
        config_layout.addWidget(btn_add_nucleus)

        main_layout.addWidget(config_group)

        # Add initial row
        self.add_nucleus_row()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_ok = QPushButton("Add Reference")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self.accept_reference)
        btn_layout.addWidget(btn_ok)

        main_layout.addLayout(btn_layout)

    def add_nucleus_row(self):
        """Add a new nucleus configuration row"""
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)

        # Nucleus selector
        combo_nucleus = QComboBox()
        combo_nucleus.setEditable(True)
        combo_nucleus.addItems(self.existing_nuclei)
        combo_nucleus.setFixedWidth(80)
        row_layout.addWidget(QLabel("Nucleus:"))
        row_layout.addWidget(combo_nucleus)

        # Delta ref
        row_layout.addWidget(QLabel("  δ_ref:"))
        spin_delta = QDoubleSpinBox()
        spin_delta.setRange(-500, 500)
        spin_delta.setValue(0.0)
        spin_delta.setDecimals(2)
        spin_delta.setSuffix(" ppm")
        spin_delta.setFixedWidth(120)
        spin_delta.setToolTip("Reference peak position on chemical shift scale")
        row_layout.addWidget(spin_delta)

        # Sigma ref
        row_layout.addWidget(QLabel("  σ_ref:"))
        spin_sigma = QDoubleSpinBox()
        spin_sigma.setRange(-10000, 20000)
        spin_sigma.setValue(0.0)
        spin_sigma.setDecimals(2)
        spin_sigma.setSuffix(" ppm")
        spin_sigma.setFixedWidth(120)
        spin_sigma.setToolTip("Isotropic shielding constant of reference compound")
        row_layout.addWidget(spin_sigma)

        # Remove button
        btn_remove = QPushButton("✕")
        btn_remove.setFixedWidth(30)
        btn_remove.setToolTip("Remove this nucleus")
        btn_remove.clicked.connect(lambda: self.remove_nucleus_row(row_widget))
        row_layout.addWidget(btn_remove)

        row_layout.addStretch()

        self.nucleus_layout.addWidget(row_widget)
        self.nucleus_widgets.append((combo_nucleus, spin_delta, spin_sigma, row_widget))

    def remove_nucleus_row(self, row_widget):
        """Remove a nucleus row"""
        if len(self.nucleus_widgets) <= 1:
            QMessageBox.warning(
                self, "Cannot Remove", "At least one nucleus must be specified."
            )
            return

        # Find and remove from list
        for i, (_, _, _, widget) in enumerate(self.nucleus_widgets):
            if widget == row_widget:
                self.nucleus_widgets.pop(i)
                break

        # Remove from layout
        row_widget.setParent(None)
        row_widget.deleteLater()

    def accept_reference(self):
        """Validate and accept the reference"""
        ref_name = self.edit_name.text().strip()
        if not ref_name:
            QMessageBox.warning(self, "Missing Name", "Please enter a reference name.")
            return
        if ref_name in ("Custom", "No Reference"):
            # Both are placeholders the NMR dialog manages; a user entry under
            # either name is dropped on save or cannot be deleted.
            QMessageBox.warning(
                self, "Reserved Name", f"'{ref_name}' is reserved; choose another name."
            )
            return

        # Check for duplicate nuclei. Compare normalized keys: "H" and "1H"
        # are the same nucleus, and the second silently overwrote the first.
        to_key = getattr(self.parent(), "get_nucleus_key", None)
        nuclei_used = []
        for combo, _, _, _ in self.nucleus_widgets:
            nucleus = combo.currentText().strip()
            if callable(to_key):
                nucleus = to_key(nucleus)
            if nucleus in nuclei_used:
                QMessageBox.warning(
                    self,
                    "Duplicate Nucleus",
                    f"Nucleus '{nucleus}' is specified multiple times.",
                )
                return
            nuclei_used.append(nucleus)

        self.accept()

    def get_reference_data(self):
        """Get the configured reference data"""
        ref_name = self.edit_name.text().strip()
        nucleus_data = {}

        for combo, spin_delta, spin_sigma, _ in self.nucleus_widgets:
            nucleus = combo.currentText()
            nucleus_data[nucleus] = {
                "delta_ref": spin_delta.value(),
                "sigma_ref": spin_sigma.value(),
            }

        return ref_name, nucleus_data
