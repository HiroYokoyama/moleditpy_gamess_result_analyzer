"""GAMESSParser: assembles the parsing mixins and drives parse_all's step order."""

from .parser_structure import _StructureParsingMixin
from .parser_electronic import _ElectronicParsingMixin
from .parser_properties import _PropertyParsingMixin, AU_TO_DEBYE
from .parser_spectra import _SpectraParsingMixin, IMAGINARY_FREQ_THRESHOLD

__all__ = [
    "ParseCancelled",
    "GAMESSParser",
    "AU_TO_DEBYE",
    "IMAGINARY_FREQ_THRESHOLD",
]


class ParseCancelled(Exception):
    """Raised by a progress callback to abort ``parse_all`` between steps."""


class GAMESSParser(
    _StructureParsingMixin,
    _ElectronicParsingMixin,
    _PropertyParsingMixin,
    _SpectraParsingMixin,
):
    """Parser for GAMESS output files (.log, .out)"""

    def __init__(self):
        self.filename = ""
        self.raw_content = ""
        self.lines = []
        self._reset_data()

    def _reset_data(self):
        """Discard every result derived from the previous input."""
        self.data = {
            "scf_traces": [],  # List of SCF energy values per iteration
            "converged": False,
            "scf_energy": None,
            "atoms": [],
            "coords": [],
            "charge": 0,
            "mult": 1,
            "frequencies": [],  # List of dicts: {freq, ir, raman, vector}
            "excitation_energies": [],  # TDDFT
            "dipole": None,
            "dipoles": None,
            "nmr_shielding": [],
            "charges": {},  # Type -> List
            "version": None,
            "scan_steps": [],
            "all_steps": [],
            "termination_status": "Running",
        }

    #: (method name, label) in execution order. Gradients must be parsed
    #: before the trajectory so they can be linked to it.
    PARSE_STEPS = (
        ("parse_basic", "Reading job information"),
        ("parse_gradients", "Reading forces"),
        ("parse_trajectory", "Reading optimization steps"),
        ("parse_frequencies", "Reading vibrational frequencies"),
        ("parse_thermal", "Reading thermochemistry"),
        ("parse_mo_coeffs", "Reading MO coefficients"),
        ("parse_orbital_energies", "Reading orbital energies"),
        ("parse_charges", "Reading atomic charges"),
        ("parse_mayer_bond_orders", "Reading bond orders"),
        ("parse_dipole", "Reading dipole moment"),
        ("parse_spin_contamination", "Reading spin contamination"),
        ("parse_dispersion", "Reading dispersion correction"),
        ("parse_energy_components", "Reading energy components"),
        ("parse_tddft", "Reading excited states"),
        ("parse_nmr", "Reading NMR data"),
        ("parse_basis_set", "Reading basis set"),
        ("parse_scf_trace", "Reading SCF convergence"),
        ("parse_scan_results_table", "Reading scan results"),
    )

    def load_from_memory(self, content, filename="", progress=None):
        """Store the GAMESS output text and run parse_all on it."""
        self._reset_data()
        self.filename = filename
        self.raw_content = content
        self.lines = content.splitlines()
        self.parse_all(progress=progress)

    def parse_all(self, progress=None):
        """Run every parse step.

        *progress* is called as ``progress(done, total, label)`` before each
        step and once when finished; it may raise ``ParseCancelled`` to abort.
        """
        total = len(self.PARSE_STEPS)
        for done, (method, label) in enumerate(self.PARSE_STEPS):
            if progress is not None:
                progress(done, total, label)
            getattr(self, method)()
        if progress is not None:
            progress(total, total, "Finished")
