"""Vibrational modes, thermochemistry, TD-DFT and NMR from a GAMESS log."""

import re

#: Below this magnitude an imaginary frequency is taken as numerical noise.
IMAGINARY_FREQ_THRESHOLD = 10.0

KCAL_PER_HARTREE = 627.5094740631
EV_TO_CM = 8065.543937
EV_NM = 1239.841984
#: km/mol per Debye**2/(amu*Angstrom**2), the unit GAMESS prints IR intensity in
IR_TO_KM_MOL = 42.2561

_SKIPPED_MODES = re.compile(r"MODES\s+(\d+) TO\s+(\d+) ARE TAKEN AS ROTATIONS")
_MODE_ATOM = re.compile(r"^\s*\d+\s+[A-Za-z]\S*\s+X\s+(.+)$")
_MODE_AXIS = re.compile(r"^\s+([YZ])\s+(.+)$")
_THERMO_ROW = re.compile(r"^\s*(ELEC\.|TRANS\.|ROT\.|VIB\.|TOTAL)\s+(-?\d+\.\d+.*)$")
_TDDFT_ROW = re.compile(
    r"^\s*(\d+)\s+(\S+)\s+(-?\d+\.\d+)\s+(\d+\.\d+)"
    r"\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+\.\d+)\s*$"
)
_STATE_DETAIL = re.compile(r"^\s*STATE #\s*(\d+)\s+ENERGY\s*=")
_AMPLITUDE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$")
_NMR_ATOM = re.compile(r"^\s*(\d+)\s+([A-Za-z]\S*)\s+X\s+-?\d")
_NMR_ISOTROPIC = re.compile(r"^\s+(-?\d+\.\d+)\s*$")
_NMR_ANISOTROPY = re.compile(r"^\s*\(\s*(-?\d+\.\d+)\s*\)\s*$")


def _floats(text):
    """The numbers of a row, or None when one is cut off or overflowed."""
    try:
        return [float(v) for v in text.split()]
    except ValueError:
        return None


class _SpectraParsingMixin:
    """Parsing of spectra and thermochemistry."""

    def parse_frequencies(self):
        """Normal modes of the last analysis, without translations and rotations."""
        self.data["frequencies"] = []
        start = None
        skipped = range(0)
        for i, line in enumerate(self.lines):
            match = _SKIPPED_MODES.search(line)
            if match:
                # Imaginary modes come first, so the range need not start at 1.
                skipped = range(int(match.group(1)) - 1, int(match.group(2)))
            if "FREQUENCIES IN CM**-1" in line:
                start = i
        if start is None:
            return
        modes = []
        current = []
        vector_row = None
        for line in self.lines[start:]:
            stripped = line.strip()
            if stripped.startswith("FREQUENCY:"):
                current = self._new_modes(stripped)
                modes.extend(current)
                continue
            if "SAYVETZ" in line:
                current = []
                continue
            if "REFERENCE ON SAYVETZ" in line or "THERMOCHEMISTRY" in line:
                break
            column = next(
                (
                    (key, factor)
                    for title, key, factor in (
                        ("IR INTENSITY:", "ir", IR_TO_KM_MOL),
                        ("RAMAN ACTIVITY:", "raman", 1.0),
                        ("REDUCED MASS:", "reduced_mass", 1.0),
                    )
                    if stripped.startswith(title)
                ),
                None,
            )
            if column:
                for mode, value in zip(current, _floats(stripped.split(":", 1)[1]) or []):
                    mode[column[0]] = value * column[1]
                continue
            if not current:
                continue
            atom = _MODE_ATOM.match(line)
            if atom:
                values = _floats(atom.group(1))
                complete = values is not None and len(values) == len(current)
                vector_row = [[v] for v in values] if complete else None
                continue
            axis = _MODE_AXIS.match(line)
            if axis and vector_row is not None:
                values = _floats(axis.group(2))
                if values is None or len(values) != len(vector_row):
                    vector_row = None  # the job is still writing this row
                    continue
                for cell, value in zip(vector_row, values):
                    cell.append(value)
                if axis.group(1) == "Z":
                    for mode, cell in zip(current, vector_row):
                        mode["vector"].append(tuple(cell))
                    vector_row = None
        self.data["frequencies"] = [
            mode for k, mode in enumerate(modes) if k not in skipped
        ]

    @staticmethod
    def _new_modes(stripped):
        """Mode dicts for a FREQUENCY row; "I" after a value marks it imaginary."""
        modes = []
        for text in stripped.split(":", 1)[1].split():
            if text == "I":
                modes[-1]["freq"] = -abs(modes[-1]["freq"])
                continue
            try:
                frequency = float(text)
            except ValueError:
                break  # cut off: the modes read so far keep their columns
            modes.append({"freq": frequency, "ir": 0.0, "raman": 0.0, "vector": []})
        return modes

    def _final_energy(self):
        """The last SCF energy, which the thermal contributions are added to."""
        return self.data.get("scf_energy")

    def parse_thermal(self):
        """Thermochemistry in Hartree, from the kcal/mol table of the last analysis.

        GAMESS's E, H and G columns include the zero-point energy, in VIB.
        """
        self.data["thermal"] = {}
        start = None
        for i, line in enumerate(self.lines):
            if "THERMOCHEMISTRY AT T=" in line:
                start = i
        energy = self._final_energy()
        if start is None or energy is None:
            return
        data = self.data["thermal"]
        match = re.search(r"T=\s*(\d+\.\d+)", self.lines[start])
        temperature = float(match.group(1)) if match else 298.15
        data["temperature"] = temperature
        zpe = None
        rows = {}
        in_kcal = False
        for i, line in enumerate(self.lines[start:], start):
            if "ZERO POINT ENERGY" in line and zpe is None and i + 1 < len(self.lines):
                first = _floats(" ".join(self.lines[i + 1].split()[:1]))
                zpe = first[0] if first else None
            if "KCAL/MOL" in line and "CAL/MOL-K" in line:
                in_kcal = True
                continue
            if in_kcal:
                row = _THERMO_ROW.match(line)
                if row:
                    values = _floats(row.group(2))
                    if values is not None and len(values) >= 6:
                        rows[row.group(1)] = values
                elif rows:
                    break
        if zpe is None or "TOTAL" not in rows:
            data.clear()
            return

        def hartree(key, column):
            """One table value converted to Hartree."""
            return rows[key][column] / KCAL_PER_HARTREE if key in rows else None

        def ts(key):
            """T*S in Hartree for one row of the table."""
            if key not in rows:
                return None
            return temperature * rows[key][5] / 1000.0 / KCAL_PER_HARTREE

        total_e, total_h, total_g = (hartree("TOTAL", c) for c in range(3))
        data["zpe"] = zpe
        data["electronic_energy"] = energy
        data["thermal_energy"] = energy + total_e
        data["enthalpy"] = energy + total_h
        data["gibbs"] = energy + total_g
        data["enthalpy_corr"] = total_h
        data["gibbs_corr"] = total_g
        data["entropy"] = ts("TOTAL")
        data["s_el"] = ts("ELEC.")
        data["s_vib"] = ts("VIB.")
        data["s_rot"] = ts("ROT.")
        data["s_trans"] = ts("TRANS.")
        vib = hartree("VIB.", 0)
        data["corr_vib"] = vib - zpe if vib is not None else None
        data["corr_rot"] = hartree("ROT.", 0)
        data["corr_trans"] = hartree("TRANS.", 0)
        data["corr_zpe"] = zpe
        data["corr_total"] = total_e
        data["corr_thermal_total"] = total_e - zpe
        data["imaginary_freq_count"] = sum(
            1
            for f in self.data.get("frequencies", [])
            if f.get("freq", 0) < -IMAGINARY_FREQ_THRESHOLD
        )

    def parse_tddft(self):
        """Excited states from the TD-DFT summary, with their orbital amplitudes."""
        self.data["tddft"] = []
        start = None
        for i, line in enumerate(self.lines):
            if "SUMMARY OF TDDFT RESULTS" in line:
                start = i
        if start is None:
            self._read_transition_moments()
            return
        states = {}
        for line in self.lines[start:]:
            match = _TDDFT_ROW.match(line)
            if match:
                ev = float(match.group(4))
                index = int(match.group(1))
                osc = float(match.group(8))
                states[index] = {
                    "state": index,
                    "label": match.group(2),
                    "energy_ev": ev,
                    "energy_nm": EV_NM / ev if ev else 0.0,
                    "energy_cm": ev * EV_TO_CM,
                    "osc": osc,
                    "osc_len": osc,
                    "transitions": [],
                }
            elif states and not line.strip():
                break
        current = None
        for line in self.lines[:start]:
            detail = _STATE_DETAIL.match(line)
            if detail:
                current = states.get(int(detail.group(1)))
                continue
            amplitude = _AMPLITUDE.match(line)
            if amplitude and current is not None:
                current["transitions"].append(
                    f"{amplitude.group(1)} -> {amplitude.group(2)} "
                    f"(coeff: {amplitude.group(3)})"
                )
        self.data["tddft"] = [states[k] for k in sorted(states)]

    def _read_transition_moments(self):
        """Transitions from the lowest CI state of a RUNTYP=TRANSITN job (length form)."""
        states, pair, energies, ev = {}, None, None, None
        reading = False
        for line in self.lines:
            text = line.strip()
            if text == "---- LENGTH FORM ----":
                reading = True
                continue
            if text.startswith("---- ") and text.endswith(" ----"):
                reading = False
            if not reading:
                continue
            match = re.match(r"^CI STATE NUMBER=\s*(\d+)\s+(\d+)", text)
            if match:
                pair, energies, ev = (int(match.group(1)), int(match.group(2))), None, None
                continue
            match = re.match(r"^STATE ENERGIES\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)", text)
            if match:
                energies = (float(match.group(1)), float(match.group(2)))
                continue
            match = re.search(r"=\s*(-?\d+\.\d+) \[EV\]", text)
            if text.startswith("TRANSITION ENERGY") and match:
                ev = float(match.group(1))
                continue
            match = re.match(r"^OSCILLATOR STRENGTH =\s*(-?\d+\.\d+)", text)
            if not (match and pair and ev and ev > 0):
                continue
            lower, upper = pair
            if lower != 1 or upper == 1:
                continue
            if energies and self.data.get("scf_energy") is None:
                self.data["scf_energy"] = energies[0]
            osc = float(match.group(1))
            if energies:
                ev = (energies[1] - energies[0]) * 27.211386245988
            states[upper] = {
                "state": upper - 1,
                "label": f"CI state {upper}",
                "energy_ev": ev,
                "energy_nm": EV_NM / ev,
                "energy_cm": ev * EV_TO_CM,
                "osc": osc,
                "osc_len": osc,
                "transitions": [],
            }
        self.data["tddft"] = [states[k] for k in sorted(states)]

    def parse_nmr(self):
        """GIAO isotropic shieldings; GAMESS does not compute J couplings."""
        self.data["nmr_shielding"] = []
        start = None
        for i, line in enumerate(self.lines):
            if "GIAO CHEMICAL SHIELDING TENSOR" in line:
                start = i
        if start is None:
            return
        current = None
        for line in self.lines[start:]:
            if "DONE WITH NMR" in line:
                break
            atom = _NMR_ATOM.match(line)
            if atom:
                current = {
                    "atom_idx": int(atom.group(1)) - 1,
                    "atom_sym": self._element(int(atom.group(1)) - 1, atom.group(2)),
                    "shielding": 0.0,
                    "anisotropy": 0.0,
                }
                self.data["nmr_shielding"].append(current)
                continue
            if current is None:
                continue
            isotropic = _NMR_ISOTROPIC.match(line)
            if isotropic:
                current["shielding"] = float(isotropic.group(1))
                continue
            anisotropy = _NMR_ANISOTROPY.match(line)
            if anisotropy:
                current["anisotropy"] = float(anisotropy.group(1))
