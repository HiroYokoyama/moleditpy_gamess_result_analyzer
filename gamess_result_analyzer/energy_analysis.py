"""Energy Components panel: post-HF correlation energy decomposition.

Shows whatever correlated-method components the parser found (MP2 correlation
and total; CCSD(T) reference / correlation / (T) / totals; T1 diagnostic).
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
)

from .bond_analysis import _make_table


class EnergyComponentsDialog(QDialog):
    """Dialog listing post-HF correlation energy components (MP2, CCSD(T), T1)."""

    def __init__(self, parent, data):
        super().__init__(parent)
        self.setWindowTitle("Energy Components")
        self.resize(480, 360)

        rows = []
        for c in data.get("energy_components", []):
            if c.get("dimensionless"):
                rows.append([c["label"], f"{c['value']:.6f}"])
            else:
                rows.append([c["label"], f"{c['value']:.8f} Eh"])

        layout = QVBoxLayout(self)
        if rows:
            layout.addWidget(_make_table(["Component", "Value"], rows))
        else:
            layout.addWidget(
                QLabel("No post-HF energy components found (HF/DFT result).")
            )

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)
