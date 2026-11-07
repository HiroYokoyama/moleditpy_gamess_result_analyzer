"""Shared helpers: atomic JSON save, default export paths and host status-bar notify."""

import json
import logging
import os
import stat
import time

# Known dummy / pseudo-atom labels used in GAMESS and other QC output files.
_DUMMY_SYMBOLS: frozenset[str] = frozenset(
    {"*", "-", "X", "DA", "DU", "DUM", "DUMMY", "Q", "BQ", "LP"}
)


def get_default_export_path(base_path, suffix="_analyzed", extension=""):
    """
    Generates a default export path based on the input file path.
    Example: 'job.out' -> 'job_analyzed.csv'
    """
    if not base_path:
        return ""
    dirname = os.path.dirname(base_path)
    filename_base = os.path.splitext(os.path.basename(base_path))[0]
    new_filename = f"{filename_base}{suffix}{extension}"
    return os.path.join(dirname, new_filename)


def _replace_with_retry(tmp_path, path, attempts=5, delay=0.05):
    """``os.replace`` with Windows-specific recovery.

    On Windows the destination is opened by the replace itself, so a virus
    scanner, search indexer or backup agent holding a transient handle makes
    it fail with ``PermissionError`` (WinError 5) even though nothing is
    wrong. A read-only destination — common when the plugin was unpacked
    from a zip that carried the attribute — fails the same way but never
    recovers on its own, so clear it before the final try.
    """
    last = None
    for attempt in range(attempts):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError as exc:
            last = exc
            if attempt == attempts - 2 and os.path.exists(path):
                # Transient-lock retries are exhausted; treat it as a
                # read-only destination for the last attempt.
                try:
                    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    logging.debug("Could not clear read-only on %s", path)
            time.sleep(delay * (attempt + 1))
    raise last


def save_json_atomic(path, data, indent=2):
    """Write *data* as JSON via a temp file + ``os.replace``.

    A direct ``open(path, "w")`` truncates the file first, so a crash or
    I/O error mid-write destroys the previous contents (saved NMR merges,
    dialog settings). Writing to a sibling temp file and atomically
    replacing keeps the old file intact until the new one is complete.

    The temp file is removed if the write or the replace fails, so a failing
    save cannot leave a growing pile of ``*.json.tmp`` beside the real file.
    """
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
        _replace_with_retry(tmp_path, path)
    except (OSError, TypeError, ValueError):
        try:
            os.remove(tmp_path)
        except OSError:
            logging.debug("Could not remove temp file %s", tmp_path)
        raise


def normalize_atom_symbol(raw: str) -> str:
    """Return a valid RDKit atom symbol for *raw*, mapping dummy labels to '*'.

    Handles:
    - Labels with a colon suffix  e.g. ``X:1`` → ``*``
    - Known dummy labels          e.g. ``DA``, ``DU``, ``BQ`` → ``*``
    - Unknown / non-periodic      e.g. ``Xx`` → ``*``
    - Normal elements             e.g. ``Fe``, ``C`` → returned as-is (capitalised)
    """
    sym = raw.strip()
    # Strip GAMESS-style colon-suffixed labels like "X:1" or "C:2"
    if ":" in sym:
        sym = sym.split(":")[0]
    if sym.upper() in _DUMMY_SYMBOLS:
        return "*"
    sym = sym.capitalize()
    try:
        from rdkit import Chem

        if Chem.GetPeriodicTable().GetAtomicNumber(sym) <= 0:
            return "*"
    except (ImportError, RuntimeError, AttributeError, ValueError):
        return "*"
    return sym


def determine_bonds_without_dummies(mol, charge: int = 0, bond_orders: bool = True):
    """Run RDKit bond determination on *mol* (an RWMol), skipping dummy atoms.

    Non-fatal: failures are logged. Pass bond_orders=False during animation
    playback to avoid per-frame latency.
    """
    try:
        from rdkit import Chem
        from rdkit.Geometry import Point3D  # pylint: disable=no-name-in-module
        from rdkit.Chem import rdDetermineBonds

        conf = mol.GetConformer()

        # Build index map: sub_idx -> orig_idx (only real atoms)
        real_indices = [
            i
            for i in range(mol.GetNumAtoms())
            if mol.GetAtomWithIdx(i).GetSymbol() != "*"
        ]

        if not real_indices:
            return  # nothing to do

        # Build sub-molecule with only real atoms
        sub = Chem.RWMol()  # pylint: disable=no-member
        sub_conf = Chem.Conformer(len(real_indices))  # pylint: disable=no-member
        for sub_i, orig_i in enumerate(real_indices):
            sym = mol.GetAtomWithIdx(orig_i).GetSymbol()
            sub.AddAtom(Chem.Atom(sym))  # pylint: disable=no-member
            pos = conf.GetAtomPosition(orig_i)
            sub_conf.SetAtomPosition(sub_i, Point3D(pos.x, pos.y, pos.z))
        sub.AddConformer(sub_conf)

        # Bond determination on the sub-molecule
        rdDetermineBonds.DetermineConnectivity(sub)
        if bond_orders:
            try:
                rdDetermineBonds.DetermineBondOrders(sub, charge=charge)
            except (RuntimeError, ValueError) as e:
                logging.debug(
                    "DetermineBondOrders failed, falling back to connectivity: %s", e
                )

        # Copy bonds back to the original molecule
        for bond in sub.GetBonds():
            orig_i = real_indices[bond.GetBeginAtomIdx()]
            orig_j = real_indices[bond.GetEndAtomIdx()]
            mol.AddBond(orig_i, orig_j, bond.GetBondType())

    # C++ library boundary: RDKit/VTK/pyvista exceptions do not map to Python types
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        logging.debug("determine_bonds_without_dummies: non-fatal — %s", exc)


def clear_atom_color_overrides(mw) -> None:
    """Clear any 3D atom-color overrides left by the Atomic Charges view.

    Loading a *different* result (new file, reload, or a fresh analyzer
    window) must not let colors keyed to the previous molecule's atom
    indices bleed onto a differently-indexed molecule. Called on every
    "new file" entry point (drag-drop / Select File / Reload / opening a
    fresh analyzer from the main GUI) as well as on document reset.
    """
    v3d = getattr(mw, "view_3d_manager", None) if mw is not None else None
    if v3d is not None and hasattr(v3d, "_plugin_color_overrides"):
        try:
            v3d._plugin_color_overrides.clear()
        except (AttributeError, RuntimeError) as exc:  # noqa: BLE001
            logging.warning("clear_atom_color_overrides: %s", exc)


def sync_main_window_file(mw, path: str, context=None) -> None:
    """Show *path* as the main window's current file.

    The host titles its window from ``current_file_path`` but only rebuilds
    the title on demand, so setting the path alone changes nothing on screen.
    ``update_window_title`` is what the host itself calls after running a
    plugin file opener; ``context.refresh_ui()`` is only the fallback, as it
    also recomputes the 2D formula label we have no business touching.
    """
    if mw is None:
        return
    im = getattr(mw, "init_manager", None)
    if im is None:
        return
    im.current_file_path = path

    sm = getattr(mw, "state_manager", None)
    refresh = getattr(sm, "update_window_title", None) if sm is not None else None
    if not callable(refresh):
        refresh = getattr(context, "refresh_ui", None) if context is not None else None
    if callable(refresh):
        try:
            refresh()
        except (AttributeError, RuntimeError) as exc:
            logging.warning("sync_main_window_file: %s", exc)


def list_gamess_output_files(directory: str) -> list[str]:
    """Return a sorted list of ``*.log`` and ``*.out`` filenames found in *directory*.

    Only the bare filenames (not full paths) are returned.  An empty list is
    returned if *directory* does not exist or cannot be listed.

    Parameters
    ----------
    directory:
        Path to the directory to scan.
    """
    try:
        return sorted(f for f in os.listdir(directory) if f.lower().endswith((".log", ".out")))
    except OSError as exc:
        logging.debug("list_gamess_output_files: cannot list '%s' — %s", directory, exc)
        return []


def _host_context(owner):
    """Resolve the host PluginContext via context/parent_dlg/freq_dialog/parent()."""
    if owner is None:
        return None
    seen = []
    candidate = owner
    for _ in range(4):
        if candidate is None or any(candidate is s for s in seen):
            break
        seen.append(candidate)
        context = getattr(candidate, "context", None)
        if context is not None and hasattr(context, "show_status_message"):
            return context
        for hop in ("parent_dlg", "freq_dialog", "parent_dialog"):
            nxt = getattr(candidate, hop, None)
            if nxt is not None:
                break
        else:
            parent = getattr(candidate, "parent", None)
            nxt = parent() if callable(parent) else parent
        candidate = nxt
    return None


def notify(owner, message, timeout=3000):
    """Show *message* in the host status bar, else log it. Never raises."""
    try:
        context = _host_context(owner)
    except (AttributeError, TypeError, RuntimeError) as exc:
        logging.debug("Could not resolve the status-message owner: %s", exc)
        context = None
    if context is not None:
        try:
            context.show_status_message(message, timeout)
            return True
        except (AttributeError, TypeError, RuntimeError) as exc:
            logging.warning("Status message rejected by host: %s", exc)
    logging.info("%s", message)
    return False
