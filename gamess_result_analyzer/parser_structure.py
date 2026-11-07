"""Geometry, job status, forces and optimization/scan steps from a GAMESS log."""

from __future__ import annotations

import re
import math

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
    r"^\s*([A-Za-z][A-Za-z0-9]*)\s+(\d+\.\d*)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$"
)
_GRADIENT_ROW = re.compile(
    r"^\s*(\d+)\s+([A-Za-z]{1,2})\s+(?:\d+\.\d\s+)?"
    r"(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$"
)
_OPTTOL = re.compile(r"OPTTOL\s*=\s*(\d*\.?\d+E[-+]\d+)")


def symbol_for(atomic_number):
    """Element symbol for an atomic number; ghosts and dummies are "X"."""
    if 0 < atomic_number < len(ELEMENTS):
        return ELEMENTS[atomic_number]
    return "X"


class _StructureParsingMixin:
    """Parsing of geometry, job status, forces and optimization trajectories."""

    def _read_geometry(self, header_idx, scale=1.0):
        """(atoms, coords in Angstrom) of the table whose title is at header_idx."""
        atoms, coords = [], []
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
            atoms.append(symbol_for(int(float(match.group(2)))))
            coords.append(xyz)
        return atoms, coords

    def parse_basic(self):
        """Version, charge/multiplicity, final energy and geometry, job status."""
        last_angs = last_bohr = None
        raman = any("RUNTYP=RAMAN" in line for line in self.lines)
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
                # A Raman job's later SCFs run under finite electric fields.
                if not (raman and self.data.get("scf_energy") is not None):
                    self.data["scf_energy"] = float(match.group(1))
                continue
            if "EQUILIBRIUM GEOMETRY LOCATED" in line:
                self.data["converged"] = True

        if last_angs is not None:
            atoms, coords = self._read_geometry(last_angs)
        elif last_bohr is not None:
            atoms, coords = self._read_geometry(last_bohr, BOHR_TO_ANGSTROM)
        else:
            atoms, coords = [], []
        if atoms:
            self.data["atoms"], self.data["coords"] = atoms, coords

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
                    "atom_sym": row.group(2),
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

    def parse_scan_results_table(self):
        """GAMESS has no scan keyword, so there is no results table to attach."""
