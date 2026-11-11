"""MO coefficients, orbital energies, basis set and SCF traces from a GAMESS log."""

import re
import logging

HARTREE_TO_EV = 27.211386245988

_SHELL_L = {"S": 0, "P": 1, "D": 2, "F": 3, "G": 4, "H": 5, "I": 6}
_MO_NUMBER = re.compile(r"-?\d*\.\d{6}")
#: "   26  C  1 XXX  -0.000000 ...": the atom number and label may touch at 3 digits.
_MO_ROW = re.compile(
    r"^\s*(\d+)\s+([A-Za-z]{1,2})\s*(\d+)\s*([SPXYZDFGHI]+)\s+(-?\d*\.\d{6}.*)$"
)
_OCCUPIED = re.compile(
    r"NUMBER OF OCCUPIED ORBITALS \((ALPHA|BETA)\s*\)(?: KEPT IS)?\s*=\s*(\d+)"
)
#: Orbital energies are F11.4: large ones touch, and larger ones print as asterisks.
_ENERGY_WIDTH = 11
_ENERGY_OVERFLOW = 1.0e6
_BASIS_ROW = re.compile(
    r"^\s*(\d+)\s+([SPLDFGHI])\s+(\d+)\s+(\d+\.\d+)\s+(-?\d+\.\d+)(?:\s+(-?\d+\.\d+))?"
)
_SCF_ROW = re.compile(r"^\s*(\d+)\s+\d+(?:\s+\d+)?\s+(-?\d+\.\d{6,})\s")
_ATOM_TITLE = re.compile(r"^ (\S+)\s*$")


def _orbital_energies(line, count):
    """The *count* energies of one row, read by column position."""
    text = line.rstrip()
    start = len(text) - _ENERGY_WIDTH * count
    if start < 0:
        return [float(part) for part in text.split()]
    energies = []
    for k in range(count):
        field = text[start + k * _ENERGY_WIDTH : start + (k + 1) * _ENERGY_WIDTH]
        energies.append(_ENERGY_OVERFLOW if "*" in field else float(field))
    return energies


def _is_coefficient_header(parts):
    """Whether a split line is the row of orbital numbers above a coefficient block."""
    return bool(parts) and all(p.isdigit() for p in parts)


class _ElectronicParsingMixin:
    """Parsing of orbitals, the basis set and SCF convergence."""

    def _electron_counts(self):
        """(n_alpha, n_beta) from the last counts printed, or (None, None)."""
        counts = {}
        for line in self.lines:
            match = _OCCUPIED.search(line)
            if match:
                counts[match.group(1)] = int(match.group(2))
        if "ALPHA" not in counts:
            return None, None
        return counts["ALPHA"], counts.get("BETA", counts["ALPHA"])

    def _gvb_occupations(self):
        """{0-based orbital: electrons} of a GVB/ROHF-GVB job, or None.

        Core orbitals hold two; each open-shell set holds 2F per orbital from
        the F vector, whose first entry is the core's; pairs take their
        converged occupations from the last PAIR INFORMATION table.
        """
        start = None
        for i, line in enumerate(self.lines):
            if "ROHF-GVB INPUT PARAMETERS" in line:
                start = i
        if start is None:
            return None
        nco, f_vector, sets, pairs = None, {}, {}, {}
        reading_f = False
        for line in self.lines[start:]:
            text = line.strip()
            if "FINAL GVB ENERGY" in text or text == "EIGENVECTORS":
                break
            match = re.search(r"\bNCO\s*=\s*(\d+)", text)
            if match:
                nco = int(match.group(1))
            match = re.match(r"^(SET|PAIR)\s+(\d+) HAS ORBS\s+([\d\s]+)$", text)
            if match:
                target = sets if match.group(1) == "SET" else pairs
                target[int(match.group(2))] = [int(n) - 1 for n in match.group(3).split()]
            if text == "F VECTOR (OCCUPANCIES)":
                reading_f = True
                continue
            if reading_f:
                parts = text.split()
                if len(parts) == 2 and parts[0].isdigit():
                    f_vector[int(parts[0])] = float(parts[1])
                    continue
                reading_f = False
        if nco is None:
            return None
        occupations = {k: 2.0 for k in range(nco)}
        for k, orbitals in sets.items():
            for orbital in orbitals:
                occupations[orbital] = 2.0 * f_vector.get(k + 1, 0.0)
        offset = len(sets) + 1
        for k, orbitals in pairs.items():
            for j, orbital in enumerate(orbitals):
                occupations[orbital] = 2.0 * f_vector.get(offset + 2 * (k - 1) + j + 1, 0.0)
        for k, first, second in self._gvb_pair_occupations():
            if k in pairs and len(pairs[k]) == 2:
                occupations[pairs[k][0]], occupations[pairs[k][1]] = first, second
        return occupations

    def _gvb_pair_occupations(self):
        """[(pair, occupation 1, occupation 2)] of the last PAIR INFORMATION table."""
        start = None
        for i, line in enumerate(self.lines):
            if line.strip() == "PAIR INFORMATION":
                start = i
        if start is None:
            return []
        rows = []
        for line in self.lines[start + 4 :]:
            parts = line.split()
            if len(parts) < 7 or not all(p.isdigit() for p in parts[:3]):
                break
            try:
                rows.append((int(parts[0]), float(parts[5]), float(parts[6])))
            except ValueError:
                break
        return rows

    def _occupation(self, index, spin):
        """Electrons in 0-based orbital *index*."""
        gvb = getattr(self, "_gvb", None)
        if gvb is not None and spin == "restricted":
            return gvb.get(index, 0.0)
        n_alpha, n_beta = self._electron_counts()
        if n_alpha is None:
            return 0.0
        if spin == "alpha":
            return 1.0 if index < n_alpha else 0.0
        if spin == "beta":
            return 1.0 if index < n_beta else 0.0
        return float(index < n_alpha) + float(index < n_beta)

    def _last_mo_blocks(self):
        """{spin: title line index} of the final orbitals, one entry per spin set."""
        found = {}
        spin = "restricted"
        for i, line in enumerate(self.lines):
            text = line.strip()
            if text.startswith("----- ALPHA SET"):
                spin = "alpha"
            elif text.startswith("----- BETA SET"):
                spin = "beta"
            elif text == "EIGENVECTORS":
                if spin in ("restricted", "alpha"):
                    found = {}
                found[spin] = i
                # Only a set header says the next table is spin-resolved.
                spin = "restricted"
            elif text == "MOLECULAR ORBITALS":
                # An optimization repeats the orbitals here at the final geometry.
                found = {"restricted": i}
            elif text == "MCSCF NATURAL ORBITALS":
                found = {"natural": i}
            elif text == "MCSCF OPTIMIZED ORBITALS":
                found = {"natural": found["natural"]} if "natural" in found else {}
                found["optimized"] = i
            elif text == "**** ALPHA SET ****":
                found = {"alpha": i}
            elif text == "**** BETA SET ****":
                found["beta"] = i
        return found

    def parse_mo_coeffs(self) -> None:
        """Extract per-orbital coefficients, energy, occupancy and spin.

        Keys are "<0-based index>_<spin>". MO vectors are always over the
        Cartesian AOs, even with ISPHER=1, which only removes combinations from
        the variational space: there are then fewer MOs than coefficients each.
        """
        self.data["mo_coeffs"] = {}
        self.data["cartesian_d"] = False
        self.data["cartesian_f"] = False
        self._gvb = self._gvb_occupations()
        blocks = self._last_mo_blocks()
        if "natural" in blocks or "optimized" in blocks:
            self._read_mcscf_orbitals(blocks)
            return
        for spin, title in blocks.items():
            self._read_mo_block(title + 2, spin)

    def _read_mcscf_orbitals(self, blocks):
        """Natural orbitals with their occupations, then the virtual optimized ones.

        The natural-orbital table stops after the active orbitals; the
        transformation leaves the others as optimized. Its second row holds
        occupations, except for canonicalized core orbitals, whose energy it
        repeats from the optimized table. An uncanonicalized orbital prints
        an energy of zero there, so it is given none.
        """
        natural = {}
        if "natural" in blocks:
            self._read_mo_block(blocks["natural"] + 2, "restricted", occupations=True)
            natural = self.data["mo_coeffs"]
            self.data["mo_coeffs"] = {}
        if "optimized" in blocks:
            self._read_mo_block(blocks["optimized"] + 2, "restricted")
        optimized = self.data["mo_coeffs"]
        for key, mo in optimized.items():
            energy = mo["energy"] or None
            if key not in natural:
                natural[key] = dict(mo, occ=0.0, energy=energy)
            elif energy is not None and abs(natural[key]["occ"] - energy) < 1e-4:
                natural[key].update(occ=2.0, energy=energy)
        self.data["mo_coeffs"] = natural

    def _read_mo_block(self, start, spin, occupations=False):
        """Read the column blocks of one MO table, starting at line *start*.

        With *occupations*, the row under the orbital numbers holds
        occupations, as in natural-orbital tables, instead of energies.
        """
        columns = []
        expect_energy = False
        expect_symmetry = False
        for line in self.lines[start:]:
            parts = line.split()
            if not parts:
                continue
            if line.lstrip().startswith(("...", "---", "****")) or "END OF" in line:
                break
            if _is_coefficient_header(parts):
                columns = [int(p) - 1 for p in parts]
                expect_energy = True
                continue
            if expect_energy:
                expect_energy = False
                try:
                    energies = _orbital_energies(line, len(columns))
                except ValueError as exc:
                    logging.warning("MO table: bad energy row %r: %s", line, exc)
                    break
                for column, energy in zip(columns, energies):
                    self.data["mo_coeffs"][f"{column}_{spin}"] = {
                        "coeffs": [],
                        "spin": spin,
                        "energy": None if occupations else energy,
                        "occ": energy if occupations else self._occupation(column, spin),
                        "id": column,
                    }
                expect_symmetry = True
                continue
            if expect_symmetry:
                expect_symmetry = False
                if not _MO_ROW.match(line):
                    continue  # the row of symmetry labels, such as A1 or B2
            row = _MO_ROW.match(line)
            if row and columns:
                orb = row.group(4)
                self._note_cartesian(orb)
                numbers = _MO_NUMBER.findall(row.group(5))
                for column, text in zip(columns, numbers):
                    self.data["mo_coeffs"][f"{column}_{spin}"]["coeffs"].append(
                        {
                            "atom_idx": int(row.group(3)) - 1,
                            "sym": row.group(2),
                            "orb": orb,
                            "coeff": float(text),
                        }
                    )
                continue
            break

    def _note_cartesian(self, orb):
        """Record which Cartesian shells the MO table holds, from its labels."""
        if len(orb) == 2 and set(orb) <= set("XYZ"):
            self.data["cartesian_d"] = True
        elif len(orb) == 3 and set(orb) <= set("XYZ"):
            self.data["cartesian_f"] = True

    def parse_orbital_energies(self):
        """Orbital energies, from the final MO table."""
        self.data["orbital_energies"] = []
        self.data["mos"] = []
        entries = [
            (mo["spin"], mo["id"], mo["energy"], mo["occ"])
            for mo in self.data.get("mo_coeffs", {}).values()
        ]
        for spin, index, energy, occ in sorted(entries, key=lambda e: (e[0], e[1])):
            orb = {
                "index": index,
                "id": index,
                "occupation": occ,
                "occ": occ,
                "energy_eh": energy,
                "energy_ev": None if energy is None else energy * HARTREE_TO_EV,
                "energy": energy,
                "spin": spin,
                "type": "occupied" if occ > 0.1 else "virtual",
            }
            self.data["orbital_energies"].append(orb)
            self.data["mos"].append(orb)

    def _basis_by_title(self):
        """[(atom label, [shell])] of the ATOMIC BASIS SET table, in print order."""
        start = None
        for i, line in enumerate(self.lines):
            if line.strip() == "ATOMIC BASIS SET":
                start = i
        if start is None:
            return []
        titles = []
        by_number = {}
        for line in self.lines[start + 2 :]:
            if "TOTAL NUMBER OF" in line:
                break
            title = _ATOM_TITLE.match(line)
            if title and title.group(1) not in ("THE", "SHELL"):
                titles.append((title.group(1), []))
                continue
            row = _BASIS_ROW.match(line)
            if not row or not titles:
                continue
            number = int(row.group(1))
            shell = by_number.get(number)
            if shell is None:
                shell = {
                    "letter": row.group(2),
                    "exps": [],
                    "coeffs": [],
                    "coeffs_p": [],
                }
                by_number[number] = shell
                titles[-1][1].append(shell)
            shell["exps"].append(float(row.group(4)))
            shell["coeffs"].append(float(row.group(5)))
            if row.group(6) is not None:
                shell["coeffs_p"].append(float(row.group(6)))
        return titles

    def parse_basis_set(self):
        """Contracted shells, all Cartesian, in the order the MO table uses them.

        An L shell becomes an S and a P shell sharing exponents. Under symmetry
        only unique atoms are listed, so a symmetry copy takes the shells of the
        listed atom with its label; the MO dialog's count check catches a basis
        that differs between atoms of one element.
        """
        self.data["basis_set_shells"] = []
        titles = self._basis_by_title()
        atoms = self.data.get("atoms", [])
        coords = self.data.get("coords", [])
        if not titles or not coords:
            return
        if len(titles) == len(atoms):
            per_atom = [shells for _label, shells in titles]
        else:
            by_label = {}
            for label, shells in titles:
                by_label.setdefault(label.upper(), shells)
            # The table is titled with the $DATA name, which need not be the element.
            names = getattr(self, "_atom_names", [])
            if len(names) != len(atoms):
                names = atoms
            per_atom = [
                by_label.get(name.upper()) or by_label.get(atom.upper(), [])
                for name, atom in zip(names, atoms)
            ]
            if not all(per_atom):
                logging.warning("Basis set: an atom has no listed shells")
                return
        out = []
        for atom_idx, shells in enumerate(per_atom):
            for shell in shells:
                pieces = (
                    [("S", shell["coeffs"]), ("P", shell["coeffs_p"])]
                    if shell["letter"] == "L"
                    else [(shell["letter"], shell["coeffs"])]
                )
                for letter, coeffs in pieces:
                    out.append(
                        {
                            "atom_idx": atom_idx,
                            "origin": coords[atom_idx],
                            "l": _SHELL_L[letter],
                            "exps": shell["exps"],
                            "coeffs": coeffs,
                            "cartesian": _SHELL_L[letter] >= 2,
                        }
                    )
        self.data["basis_set_shells"] = out

    def parse_scf_trace(self):
        """Energy per SCF cycle, one trace for each SCF in the job."""
        self.data["scf_traces"] = []
        trace = None

        def finish():
            if trace:
                n = len(self.data["scf_traces"]) + 1
                self.data["scf_traces"].append(
                    {"step": f"SCF {n}", "iterations": list(trace)}
                )

        for line in self.lines:
            if re.match(r"^\s*ITER EX", line):
                finish()
                trace = []
                continue
            if trace is None:
                continue
            match = _SCF_ROW.match(line)
            if match:
                trace.append(
                    {"iter": int(match.group(1)), "energy": float(match.group(2))}
                )
            elif re.match(r"^\s*FINAL .*ENERGY IS", line):
                finish()
                trace = None
        finish()
