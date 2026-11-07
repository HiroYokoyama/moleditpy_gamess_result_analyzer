"""Charges, dipole, spin, dispersion, post-HF energies and bond orders from a GAMESS log."""

import re

#: Debye per atomic unit of dipole moment
AU_TO_DEBYE = 2.541746473

_NUMBER = r"(-?\d*\.\d+)"
_POPULATION = re.compile(
    rf"^\s*(\d+)\s+([A-Za-z]{{1,2}})\s+{_NUMBER}\s+{_NUMBER}\s+{_NUMBER}\s+{_NUMBER}\s*$"
)
_BOND = re.compile(r"(\d+)\s+(\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)")
_S2 = re.compile(rf"S-SQUARED\s*=\s*{_NUMBER}")
_DISPERSION = re.compile(rf"GRIMME'S DISPERSION ENERGY\s*=\s*{_NUMBER}")
_ESP_RADII = re.compile(r"^\s*PTSEL\s*=|PTSEL=(\S+)")
_ESP_NAMES = {"CHELPG": "CHELPG", "CONNOLLY": "Connolly ESP", "GEODESIC": "Geodesic ESP"}


class _PropertyParsingMixin:
    """Parsing of scalar and per-atom properties."""

    def _last_index(self, predicate):
        """Index of the last line satisfying *predicate*, or None."""
        for i in range(len(self.lines) - 1, -1, -1):
            if predicate(self.lines[i]):
                return i
        return None

    def parse_dipole(self):
        """The last dipole moment, printed in Debye."""
        self.data["dipoles"] = None
        self.data["dipole"] = None
        i = self._last_index(lambda l: "/D/  (DEBYE)" in l)
        if i is None or i + 1 >= len(self.lines):
            return
        try:
            x, y, z, total = (float(v) for v in self.lines[i + 1].split()[:4])
        except ValueError:
            return
        self.data["dipoles"] = {
            "vector_au": (x / AU_TO_DEBYE, y / AU_TO_DEBYE, z / AU_TO_DEBYE),
            "vector_debye": (x, y, z),
            "magnitude_au": total / AU_TO_DEBYE,
            "magnitude_debye": total,
            "vector": (x, y, z),
            "magnitude": total,
        }
        self.data["dipole"] = self.data["dipoles"]

    def parse_spin_contamination(self):
        """<S**2> of the final wavefunction against the ideal S(S+1)."""
        self.data["spin_s2"] = None
        actual = None
        for line in self.lines:
            match = _S2.search(line)
            if match:
                actual = float(match.group(1))
        if actual is None or self.data.get("mult", 1) == 1:
            return
        s = (self.data.get("mult", 1) - 1) / 2.0
        ideal = s * (s + 1)
        self.data["spin_s2"] = {
            "actual": actual,
            "ideal": ideal,
            "contamination": actual - ideal,
        }

    def parse_dispersion(self):
        """The empirical dispersion energy, when one was requested."""
        self.data["dispersion"] = None
        for line in self.lines:
            match = _DISPERSION.search(line)
            if match:
                self.data["dispersion"] = float(match.group(1))

    def parse_energy_components(self):
        """Post-HF energies, in the order the methods build on each other."""
        found = {}
        patterns = (
            ("MP2 Correlation", re.compile(rf"\bE\(2\)=\s*{_NUMBER}"), False),
            ("MP2 Total", re.compile(rf"\bE\(MP2\)=\s*{_NUMBER}"), False),
            ("SCS-MP2 Total", re.compile(rf"\bSCS-MP2=\s*{_NUMBER}"), False),
            ("MP2 Correlation", re.compile(rf"MBPT\(2\) ENERGY:.*CORR\.E=\s*{_NUMBER}"), False),
            ("MP2 Total", re.compile(rf"MBPT\(2\) ENERGY:\s*{_NUMBER}"), False),
            ("CCSD Total", re.compile(rf"CCSD\s+ENERGY:\s*{_NUMBER}"), False),
            ("CCSD(T) Total", re.compile(rf"CCSD\(T\) ENERGY:\s*{_NUMBER}"), False),
            ("T1 Diagnostic", re.compile(rf"T1 DIAGNOSTIC\s*=\s*{_NUMBER}"), True),
        )
        for line in self.lines:
            for label, pattern, dimensionless in patterns:
                match = pattern.search(line)
                if match:
                    found[label] = (float(match.group(1)), dimensionless)
        labels = list(dict.fromkeys(label for label, _p, _d in patterns))
        self.data["energy_components"] = [
            {"label": label, "value": found[label][0], "dimensionless": found[label][1]}
            for label in labels
            if label in found
        ]

    def parse_mayer_bond_orders(self):
        """The last bond order table, which is the Mayer analysis."""
        self.data["mayer_bond_orders"] = []
        start = self._last_index(lambda l: "BOND ORDER AND VALENCE ANALYSIS" in l)
        atoms = self.data.get("atoms", [])
        if start is None:
            return
        for line in self.lines[start + 2 :]:
            if "TOTAL" in line and "VALENCE" in line:
                break
            if "ATOM PAIR" in line or "BOND" in line:
                continue
            for match in _BOND.finditer(line):
                a, b = int(match.group(1)) - 1, int(match.group(2)) - 1
                self.data["mayer_bond_orders"].append(
                    {
                        "atom_idx1": a,
                        "atom_sym1": atoms[a] if a < len(atoms) else "X",
                        "atom_idx2": b,
                        "atom_sym2": atoms[b] if b < len(atoms) else "X",
                        "order": float(match.group(4)),
                    }
                )

    def _esp_scheme(self):
        """Display name of the ESP fit, from the $PDC settings echoed in the log."""
        for line in self.lines:
            for name, label in _ESP_NAMES.items():
                if f"PTSEL={name}" in line.replace(" ", ""):
                    return label
        return "ESP"

    def parse_charges(self):
        """Mulliken and Lowdin charges, and any fitted ESP charges."""
        self.data["charges"] = {}
        start = self._last_index(
            lambda l: "TOTAL MULLIKEN AND LOWDIN ATOMIC POPULATIONS" in l
        )
        if start is not None:
            mulliken, lowdin = [], []
            for line in self.lines[start + 2 :]:
                match = _POPULATION.match(line)
                if not match:
                    break
                idx, sym = int(match.group(1)) - 1, match.group(2)
                mulliken.append(
                    {"atom_idx": idx, "atom_sym": sym, "charge": float(match.group(4))}
                )
                lowdin.append(
                    {"atom_idx": idx, "atom_sym": sym, "charge": float(match.group(6))}
                )
            if mulliken:
                self.data["charges"]["Mulliken"] = mulliken
                self.data["charges"]["Lowdin"] = lowdin
        start = self._last_index(lambda l: l.strip() == "NET CHARGES:")
        if start is None:
            return
        rows = []
        for line in self.lines[start + 1 :]:
            parts = line.split()
            if parts[:1] == ["ATOM"] or (parts and set(parts[0]) == {"-"}):
                continue  # the header, and in an unsanitized log its rule
            if len(parts) != 3:
                break
            try:
                charge = float(parts[1])
            except ValueError:
                break
            rows.append(
                {"atom_idx": len(rows), "atom_sym": parts[0], "charge": charge}
            )
        if rows:
            self.data["charges"][self._esp_scheme()] = rows
