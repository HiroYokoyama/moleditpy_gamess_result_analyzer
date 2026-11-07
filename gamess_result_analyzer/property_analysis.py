"""Properties panel: scalar / global quantities from an GAMESS result.

Collects values that do not warrant a dedicated analysis panel (energy,
charge/multiplicity, dispersion correction, <S**2> spin contamination, ...).
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
)

from .bond_analysis import _make_table


class PropertiesDialog(QDialog):
    """Dialog listing scalar/global result properties (energy, charge, spin, ...)."""

    def __init__(self, parent, data):
        super().__init__(parent)
        self.setWindowTitle("Properties")
        self.resize(440, 400)

        rows = []
        energy = data.get("scf_energy")
        if energy is not None:
            rows.append(["Final Single Point Energy (Eh)", f"{energy:.8f}"])
        rows.append(["Charge", str(data.get("charge", 0))])
        rows.append(["Multiplicity", str(data.get("mult", 1))])
        if data.get("version"):
            rows.append(["GAMESS Version", str(data["version"])])
        rows.append(["Converged", "Yes" if data.get("converged") else "No"])

        disp = data.get("dispersion")
        if disp is not None:
            rows.append(["Dispersion correction (Eh)", f"{disp:.8f}"])

        s2 = data.get("spin_s2")
        if s2 and s2.get("actual") is not None:
            rows.append(["⟨S²⟩", f"{s2['actual']:.4f}"])
            if s2.get("ideal") is not None:
                rows.append(["Ideal S(S+1)", f"{s2['ideal']:.4f}"])
            if s2.get("contamination") is not None:
                rows.append(["Spin contamination", f"{s2['contamination']:+.4f}"])

        layout = QVBoxLayout(self)
        layout.addWidget(_make_table(["Property", "Value"], rows))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)
