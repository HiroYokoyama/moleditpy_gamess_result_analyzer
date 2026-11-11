"""Geometry, job status, forces and optimization/scan steps from a GAMESS log."""

from __future__ import annotations

import re
import math

from .parser_properties import AU_TO_DEBYE

#: Atomic symbols indexed by atomic number; 0 is a ghost/dummy centre.
ELEMENTS = (
    "X H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni "
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe "
    "Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au "
    "Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf "
    "Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og"
).split()

BOHR_TO_ANGSTROM = 0.52917721067

_VERSION = re.compile(r"GAMESS VERSION\s*=\s*(.+?)\s*\*")
_FINAL = re.compile(r"^\s*FINAL\s+\S+\s+ENERGY IS\s+(-?\d+\.\d+)")
_SEARCH_POINT = re.compile(r"BEGINNING GEOMETRY SEARCH POINT NSERCH=\s*(\d+)")
_SEARCH_RESULT = re.compile(
    r"^\s*NSERCH:\s*(\d+)\s+E=\s*(-?\d+\.\d+)\s+GRAD\. MAX=\s*(\d+\.\d+)"
    r"\s+R\.M\.S\.=\s*(\d+\.\d+)"
)
_ATOM_XYZ = re.compile(
    r"^\s*(\S+)\s+(\d+\.\d*)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$"
)
_GRADIENT_ROW = re.compile(
    r"^\s*(\d+)\s+([A-Za-z]\S*)\s+(?:\d+\.\d\s+)?"
    r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$"
)
_FMO_ENERGY = re.compile(r"^\s*The best FMO energy is\s+(-?\d+\.\d+)")
_FMO_DIPOLE = re.compile(
    r"^\s*Dipole moment D\(xyz\),DA\(\d+\)=\s*(-?\d+\.\d+)\s+(-?\d+\.\d+)"
    r"\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)"
)
_FMO_GRADIENT_ROW = re.compile(
    r"^\s*(\d+)\s+\d+\s+(\d+\.\d)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$"
)
_IRC_POINT = re.compile(r"POINT\s+(\d+)\s+ON THE REACTION PATH")
_IRC_STOTAL = re.compile(r"AT PATH DISTANCE STOTAL\s*=\s*(-?\d+\.\d+)")
_IRC_ENERGY = re.compile(r"^\s*TOTAL ENERGY\s*=\s*(-?\d+\.\d+)\s+HARTREE")
_NAMED_VECTOR = re.compile(r"^\s*([A-Za-z]\S*)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$")
_MEX_STEP = re.compile(
    r"^\s*NSERCH:\s*(\d+)\s+E=\s*(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+GM=\s*(\d+\.\d+)\s+GR=\s*(\d+\.\d+)"
)
_NUMERICAL_HESSIAN = re.compile(r"^\s*METHOD=(SEMINUM|NUMERIC|FULLNUM)\b")
_OPTTOL = re.compile(r"OPTTOL\s*=\s*(\d*\.?\d+E[-+]\d+)")


def symbol_for(atomic_number):
    """Element symbol for an atomic number; ghosts and dummies are "X"."""
    if 0 < atomic_number < len(ELEMENTS):
        return ELEMENTS[atomic_number]
    return "X"


class _StructureParsingMixin:
    """Parsing of geometry, job status, forces and optimization trajectories."""

    def _element(self, index, label):
        """Element of atom *index*; tables print its $DATA name, which is free text."""
        atoms = self.data.get("atoms") or []
        return atoms[index] if 0 <= index < len(atoms) else label

    def _read_geometry(self, header_idx, scale=1.0):
        """(atoms, coords in Angstrom) of the table whose title is at header_idx."""
        _names, atoms, coords = self._read_named_geometry(header_idx, scale)
        return atoms, coords

    def _read_named_geometry(self, header_idx, scale=1.0):
        """($DATA names, atoms, coords in Angstrom) of the table titled at header_idx."""
        names, atoms, coords = [], [], []
        started = False
        for line in self.lines[header_idx + 1 :]:
            match = _ATOM_XYZ.match(line)
            if not match:
                if started:
                    break
                if line.strip() and not line.lstrip().startswith(("ATOM", "CHARGE", "---")):
                    break
                continue
            started = True
            xyz = [float(match.group(k)) * scale for k in (3, 4, 5)]
            if not all(math.isfinite(v) for v in xyz):
                break
            names.append(match.group(1))
            atoms.append(symbol_for(int(float(match.group(2)))))
            coords.append(xyz)
        return names, atoms, coords

    def parse_basic(self):
        """Version, charge/multiplicity, final energy and geometry, job status."""
        last_angs = last_bohr = None
        raman = any("RUNTYP=RAMAN" in line for line in self.lines)
        displaced = False
        self._atom_names = []
        for i, line in enumerate(self.lines):
            if "COORDINATES OF ALL ATOMS ARE (ANGS)" in line:
                last_angs = i
                continue
            if "COORDINATES (BOHR)" in line and last_bohr is None:
                last_bohr = i
                continue
            version = _VERSION.search(line)
            if version:
                self.data["version"] = "GAMESS " + version.group(1)
                continue
            match = re.match(r"^\s*CHARGE OF MOLECULE\s*=\s*(-?\d+)", line)
            if match:
                self.data["charge"] = int(match.group(1))
                continue
            match = re.match(r"^\s*SPIN MULTIPLICITY\s*=\s*(\d+)", line)
            if match:
                self.data["mult"] = int(match.group(1))
                continue
            match = _FINAL.match(line)
            if match:
                # A Raman job's later SCFs run under finite electric fields, and
                # a numerical hessian's at displaced geometries.
                if displaced == "next":
                    displaced = True
                elif displaced or (raman and self.data.get("scf_energy") is not None):
                    continue
                self.data["scf_energy"] = float(match.group(1))
                continue
            if _NUMERICAL_HESSIAN.match(line):
                displaced = "next"
                continue
            if "EQUILIBRIUM GEOMETRY LOCATED" in line:
                self.data["converged"] = True

        if last_angs is not None:
            names, atoms, coords = self._read_named_geometry(last_angs)
        elif last_bohr is not None:
            names, atoms, coords = self._read_named_geometry(last_bohr, BOHR_TO_ANGSTROM)
        else:
            names, atoms, coords = [], [], []
        if atoms:
            self.data["atoms"], self.data["coords"] = atoms, coords
            self._atom_names = names

        self.data["termination_status"] = "Running"
        for line in reversed(self.lines):
            if "EXECUTION OF GAMESS TERMINATED NORMALLY" in line:
                self.data["termination_status"] = "Terminated normally"
                break
            if "EXECUTION OF GAMESS TERMINATED -ABNORMALLY-" in line:
                self.data["termination_status"] = "ERROR"
                break

    def parse_gradients(self):
        """Every gradient table; GAMESS prints the gradient, in the shown frame."""
        self.data["gradients"] = []
        self.data["all_gradients"] = []
        for i, line in enumerate(self.lines):
            text = line.strip()
            if text.startswith("UNITS ARE HARTREE/BOHR") or text == "GRADIENT (HARTREE/BOHR)":
                block = self._read_gradient_block(i)
                if block:
                    self.data["all_gradients"].append({"line": i, "grads": block})
        if self.data["all_gradients"]:
            self.data["gradients"] = [
                dict(g, vector=list(g["vector"]))
                for g in self.data["all_gradients"][-1]["grads"]
            ]

    def _read_gradient_block(self, header_idx):
        """The rows of the gradient table titled at header_idx."""
        block = []
        for line in self.lines[header_idx + 1 :]:
            row = _GRADIENT_ROW.match(line)
            if not row:
                if block:
                    break
                if line.strip() and not re.match(r"^\s*(-+|ATOM\b.*)\s*$", line):
                    break
                continue
            block.append(
                {
                    "atom_idx": int(row.group(1)) - 1,
                    "atom_sym": self._element(int(row.group(1)) - 1, row.group(2)),
                    "vector": [float(row.group(k)) for k in (3, 4, 5)],
                }
            )
        return block

    def parse_trajectory(self):
        """One entry per geometry search point in data["scan_steps"]."""
        self.data["scan_steps"] = []
        tolerance = 1.0e-4
        for line in self.lines:
            match = _OPTTOL.search(line)
            if match:
                tolerance = float(match.group(1))
        grads = self.data["all_gradients"]
        point = None
        for i, line in enumerate(self.lines):
            match = _SEARCH_POINT.search(line)
            if match:
                point = i
                continue
            result = _SEARCH_RESULT.match(line)
            if result is None or point is None:
                continue
            atoms, coords = [], []
            for k in range(point, i):
                if "COORDINATES OF ALL ATOMS ARE (ANGS)" in self.lines[k]:
                    atoms, coords = self._read_geometry(k)
                    break
            if not atoms:
                continue
            step = len(self.data["scan_steps"]) + 1
            maximum, rms = float(result.group(3)), float(result.group(4))
            self.data["scan_steps"].append(
                {
                    "type": "opt_cycle",
                    "scan_step_id": step,
                    "step": step,
                    "energy": float(result.group(2)),
                    "atoms": atoms,
                    "coords": coords,
                    "convergence": {
                        "Maximum Gradient": {
                            "value": result.group(3),
                            "tolerance": f"{tolerance:.6f}",
                            "converged": "YES" if maximum < tolerance else "NO",
                        },
                        "RMS Gradient": {
                            "value": result.group(4),
                            "tolerance": f"{tolerance / 3.0:.6f}",
                            "converged": "YES" if rms < tolerance / 3.0 else "NO",
                        },
                    },
                    "gradients": next(
                        (g["grads"] for g in grads if point < g["line"] < i), []
                    ),
                }
            )
            point = None
        if self.data["scan_steps"]:
            return
        if any(_IRC_POINT.search(l) for l in self.lines):
            self._read_irc()
        elif any("START OF DRC CALCULATION" in l for l in self.lines):
            self._read_drc()
        elif any(_MEX_STEP.match(l) for l in self.lines):
            self._read_mex()

    def _path_step(self, point, energy, atoms, coords, grads, coordinate):
        """One entry of data["scan_steps"] along a path."""
        return {
            "type": "scan_step",
            "scan_step_id": point,
            "step": 1,
            "energy": energy,
            "atoms": atoms,
            "coords": coords,
            "convergence": {},
            "gradients": grads,
            "scan_coord": coordinate,
        }

    def _first_structure(self):
        """(atoms, coords in Angstrom) of the first geometry the job prints."""
        for i, line in enumerate(self.lines):
            if "COORDINATES OF ALL ATOMS ARE (ANGS)" in line:
                return self._read_geometry(i)
            if "COORDINATES (BOHR)" in line:
                return self._read_geometry(i, BOHR_TO_ANGSTROM)
        return [], []

    def _precise_energy(self, before, rounded):
        """The last FINAL energy above line *before* when it matches *rounded*."""
        for line in reversed(self.lines[:before]):
            match = _FINAL.match(line)
            if match:
                value = float(match.group(1))
                return value if abs(value - rounded) < 1.0e-5 else rounded
        return rounded

    def _read_irc(self):
        """The points of an IRC run, with the saddle point first when it starts there."""
        steps = self.data["scan_steps"]
        first_point = next(i for i, l in enumerate(self.lines) if _IRC_POINT.search(l))
        if any("JUMPING OFF SADDLE POINT" in l for l in self.lines[:first_point]):
            energy = next(
                (float(m.group(1)) for l in self.lines[:first_point] if (m := _FINAL.match(l))),
                None,
            )
            atoms, coords = self._first_structure()
            if energy is not None and atoms:
                steps.append(self._path_step(0, energy, atoms, coords, [], 0.0))
        for i, line in enumerate(self.lines):
            match = _IRC_POINT.search(line)
            if not match:
                continue
            stotal = energy = None
            atoms, coords, grads = [], [], []
            for k in range(i + 1, min(i + 60 + 3 * len(self.data["atoms"]), len(self.lines))):
                text = self.lines[k]
                if _IRC_POINT.search(text):
                    break
                found = _IRC_STOTAL.search(text)
                if found:
                    stotal = float(found.group(1))
                found = _IRC_ENERGY.match(text)
                if found:
                    energy = self._precise_energy(i, float(found.group(1)))
                if "COORDINATES OF ALL ATOMS ARE (ANGS)" in text and not atoms:
                    atoms, coords = self._read_geometry(k)
                if "CURRENT CARTESIAN GRADIENT" in text:
                    grads = self._named_vectors(k + 1)
            if stotal is not None and energy is not None and atoms:
                steps.append(
                    self._path_step(int(match.group(1)), energy, atoms, coords, grads, stotal)
                )

    def _named_vectors(self, start):
        """Gradient rows "NAME gx gy gz" from line *start*, in atom order."""
        block = []
        for line in self.lines[start : start + 4 + len(self.data["atoms"])]:
            row = _NAMED_VECTOR.match(line)
            if not row:
                if block:
                    break
                continue
            index = len(block)
            block.append(
                {
                    "atom_idx": index,
                    "atom_sym": self._element(index, row.group(1)),
                    "vector": [float(row.group(k)) for k in (2, 3, 4)],
                }
            )
        return block

    def _read_drc(self):
        """One point per printed time of a dynamic reaction coordinate run."""
        steps = self.data["scan_steps"]
        headers = [
            i
            for i, line in enumerate(self.lines)
            if line.strip().startswith("TIME ") and "POTENTIAL" in line
        ]
        for n, i in enumerate(headers):
            end = headers[n + 1] if n + 1 < len(headers) else len(self.lines)
            try:
                parts = self.lines[i + 2].split()
                time, potential = float(parts[0]), float(parts[-2])
            except (IndexError, ValueError):
                continue
            atoms, coords = [], []
            for k in range(i + 3, min(i + 40 + len(self.data["atoms"]) * 4, len(self.lines))):
                if "CARTESIAN COORDINATES (BOHR)" not in self.lines[k]:
                    continue
                for row in self.lines[k + 1 :]:
                    values = row.split()
                    if not atoms and (not values or set(row.strip()) == {"-"}):
                        continue
                    if len(values) != 7:
                        break
                    try:
                        numbers = [float(v) for v in values]
                    except ValueError:
                        break
                    atoms.append(symbol_for(int(numbers[0])))
                    coords.append([v * BOHR_TO_ANGSTROM for v in numbers[1:4]])
                break
            if atoms:
                energy = self._precise_energy(i, potential)
                grads = next(
                    (g["grads"] for g in self.data["all_gradients"] if i < g["line"] < end),
                    [],
                )
                steps.append(
                    self._path_step(len(steps) + 1, energy, atoms, coords, grads, time)
                )

    def _read_mex(self):
        """Search steps of a minimum-energy crossing point, and the crossing found.

        Each step prints only the symmetry-unique atoms, so a step is kept
        only when that is every atom. Its energy is the first state's.
        """
        steps = []
        n_atoms = len(self.data["atoms"])
        atoms, coords = self._first_structure()
        partial = False
        for i, line in enumerate(self.lines):
            if "COORDINATES OF SYMMETRY UNIQUE ATOMS (ANGS)" in line:
                unique = self._read_geometry(i)
                if len(unique[0]) == n_atoms:
                    atoms, coords = unique
                else:
                    partial = True
                continue
            if "CONVERGED GEOMETRY IN ANGS." in line:
                final = self._read_geometry(i)
                if final[0]:
                    self.data["atoms"], self.data["coords"] = final
                    self.data["converged"] = True
                continue
            match = _MEX_STEP.match(line)
            if not match or not atoms:
                continue
            gap = abs(float(match.group(2)) - float(match.group(3)))
            step = self._path_step(
                int(match.group(1)) + 1, float(match.group(2)), atoms, coords, [], None
            )
            step.update(
                type="opt_cycle",
                energy_2=float(match.group(3)),
                convergence={
                    "Energy Difference": {"value": f"{gap:.8f}", "tolerance": "", "converged": ""},
                    "Maximum Gradient": {"value": match.group(4), "tolerance": "", "converged": ""},
                    "RMS Gradient": {"value": match.group(5), "tolerance": "", "converged": ""},
                },
            )
            del step["scan_coord"]
            steps.append(step)
        if not partial:
            self.data["scan_steps"].extend(steps)

    def _fmo_structure(self):
        """(atoms, coords in Angstrom) of the $FMOXYZ input echoed in the log."""
        units = BOHR_TO_ANGSTROM if any(
            re.search(r"UNITS\s*=\s*BOHR", l) for l in self.lines
        ) else 1.0
        atoms, coords, reading = [], [], False
        for line in self.lines:
            if not line.lstrip().startswith("INPUT CARD>"):
                continue
            card = line.split(">", 1)[1].split()
            if not card:
                continue
            if card[0].lower() == "$fmoxyz":
                reading = True
                continue
            if not reading:
                continue
            if card[0].lower() == "$end" or len(card) < 5:
                break
            try:
                xyz = [float(v) * units for v in card[2:5]]
            except ValueError:
                break
            kind = card[1]
            if kind.replace(".", "", 1).isdigit():
                atoms.append(symbol_for(int(float(kind))))
            else:
                atoms.append(kind.capitalize() if kind.capitalize() in ELEMENTS else "X")
            coords.append(xyz)
        return atoms, coords

    def parse_fmo(self):
        """The whole system of a fragment molecular orbital (FMO) job.

        Each fragment and pair prints its own SCF, orbitals and charges; only
        the FMO properties at the end describe the molecule. The structure is
        printed only as the echoed $FMOXYZ input.
        """
        energy = None
        for line in self.lines:
            match = _FMO_ENERGY.match(line)
            if match:
                energy = float(match.group(1))
        if energy is None:
            return
        self.data["scf_energy"] = energy
        atoms, coords = self._fmo_structure()
        if atoms:
            self.data["atoms"], self.data["coords"] = atoms, coords
        for key, empty in (
            ("mo_coeffs", {}),
            ("orbital_energies", []),
            ("mos", []),
            ("charges", {}),
            ("mayer_bond_orders", []),
            ("energy_components", []),
        ):
            self.data[key] = empty
        self.data["dipole"] = self.data["dipoles"] = None
        self.data["gradients"] = []
        for i, line in enumerate(self.lines):
            dipole = _FMO_DIPOLE.match(line)
            if dipole:
                x, y, z, total = (float(dipole.group(k)) for k in (1, 2, 3, 4))
                self.data["dipole"] = self.data["dipoles"] = {
                    "vector_au": (x / AU_TO_DEBYE, y / AU_TO_DEBYE, z / AU_TO_DEBYE),
                    "vector_debye": (x, y, z),
                    "magnitude_au": total / AU_TO_DEBYE,
                    "magnitude_debye": total,
                    "vector": (x, y, z),
                    "magnitude": total,
                }
            if "Energy gradient (hartree/bohr)" in line:
                block = []
                for row in self.lines[i + 1 : i + 4 + len(self.data["atoms"])]:
                    match = _FMO_GRADIENT_ROW.match(row)
                    if not match:
                        if block:
                            break
                        continue
                    index = int(match.group(1)) - 1
                    block.append(
                        {
                            "atom_idx": index,
                            "atom_sym": self._element(index, "X"),
                            "vector": [float(match.group(k)) for k in (3, 4, 5)],
                        }
                    )
                if block:
                    self.data["gradients"] = block

    def parse_scan_results_table(self):
        """GAMESS has no scan keyword, so there is no results table to attach."""
