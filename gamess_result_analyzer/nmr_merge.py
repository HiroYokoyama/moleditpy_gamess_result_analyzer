"""NMR dialog mixin: merging equivalent peaks and persisting the merge choices."""

import hashlib
import os
import json
import logging
from PySide6.QtWidgets import QMessageBox
from .utils import save_json_atomic, notify


class _NMRMergeMixin:
    @staticmethod
    def _merged_peaks_path(file_path, data):
        """Path of the merged-peaks JSON for this result.

        Without a file path the old code used one shared fallback file, so
        merges saved for one molecule silently applied to (and were
        overwritten by) every other pathless result. Key the fallback by a
        fingerprint of the shielding data instead.
        """
        if file_path:
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            return os.path.join(
                os.path.dirname(file_path), f"{base_name}-nmr_peak_info.json"
            )
        payload = json.dumps(
            [
                [d.get("atom_idx"), d.get("atom_sym"), d.get("shielding")]
                for d in (data or [])
            ],
            sort_keys=True,
        )
        fingerprint = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
        return os.path.join(
            os.path.dirname(__file__), f"nmr_merged_peaks-{fingerprint}.json"
        )

    def merge_selected_peaks(self):
        """Merge selected peaks into a single entry with isotope validation"""
        if len(self.selected_peak_indices) < 2:
            QMessageBox.warning(
                self, "Invalid Selection", "Please select at least 2 peaks to merge."
            )
            return

        selected_indices = []
        atom_symbols = set()

        # 1. Collect atom indices and element symbols from the selected peaks
        if getattr(self, "peaks_metadata", None) is not None and self.peaks_metadata:
            for peak_idx in self.selected_peak_indices:
                if peak_idx < len(self.peaks_metadata):
                    _, _, _, atom_indices = self.peaks_metadata[peak_idx]
                    selected_indices.extend(atom_indices)

                    # Element symbols, for the same-nucleus check below
                    for idx in atom_indices:
                        item = next(
                            (d for d in self.data if d.get("atom_idx", None) == idx),
                            None,
                        )
                        if item:
                            # A missing symbol must not count as a distinct
                            # nucleus (and None would crash the ', '.join in
                            # the mixed-nuclei error message below).
                            sym = item.get("atom_sym", None)
                            if sym:
                                atom_symbols.add(sym)

        # 2. Physical validation: refuse to merge peaks of different elements
        if len(atom_symbols) > 1:
            QMessageBox.critical(
                self,
                "Physical Inconsistency",
                f"Cannot merge different nuclei: {', '.join(atom_symbols)}. "
                "NMR peaks can only be merged for the same isotope.",
            )
            return

        # 3. De-duplicate and sort
        selected_indices = sorted(list(set(selected_indices)))

        # Stale peak metadata can resolve the selection to zero atoms;
        # merging then would store an empty group (and a stale selection
        # must never reach the conflict-replace step below).
        if not selected_indices:
            QMessageBox.warning(
                self,
                "Invalid Selection",
                "Could not resolve the selected peaks to atoms. "
                "Please re-select the peaks and try again.",
            )
            return

        # 4. Check for conflicts with existing merge groups
        new_merged_peaks = []
        conflict_found = False
        for group in self.merged_peaks:
            if any(idx in group["indices"] for idx in selected_indices):
                conflict_found = True
                continue  # Drop the conflicting old group; the merged one is added below
            new_merged_peaks.append(group)

        if conflict_found:
            reply = QMessageBox.question(
                self,
                "Merge Conflict",
                "Some selected atoms are already part of another group. Replace existing merge?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return

        # 5. Add the new group (persisted by Save Merges / the close-time prompt)
        new_merged_peaks.append({"indices": selected_indices})
        self.merged_peaks = new_merged_peaks
        self._mark_merges_dirty()

        # 6. UI cleanup
        self.clear_peak_selection()
        notify(
            self,
            f"Merged {len(selected_indices)} atoms into one peak (not saved yet).",
            5000,
        )

        self.recalc()

    def unmerge_selected_peaks(self):
        """Separate previously merged peaks back into individuals"""
        if not self.selected_peak_indices:
            return

        metadata = getattr(self, "peaks_metadata", None) or []
        groups_to_remove = []
        for peak_idx in self.selected_peak_indices:
            if peak_idx < len(metadata):
                _, _, is_merged, atom_indices = metadata[peak_idx]
                if is_merged:
                    # Find which group in self.merged_peaks contains these indices
                    # Since groups are unique per atom, we can just match any index
                    for group in self.merged_peaks:
                        if all(idx in group["indices"] for idx in atom_indices) and len(
                            group["indices"]
                        ) == len(atom_indices):
                            if group not in groups_to_remove:
                                groups_to_remove.append(group)
                            break

        if not groups_to_remove:
            return

        for group in groups_to_remove:
            self.merged_peaks.remove(group)

        self._mark_merges_dirty()
        self.clear_peak_selection()
        self.recalc()

    def _mark_merges_dirty(self):
        """Flag in-memory merge changes as unsaved and enable the save button."""
        self._merged_dirty = True
        btn = getattr(self, "btn_save_merge", None)
        if btn:
            btn.setEnabled(True)

    def save_merges_clicked(self):
        """Persist merged peak groups to disk (explicit user action)."""
        if not self.save_merged_peaks():
            return
        self._merged_dirty = False
        btn = getattr(self, "btn_save_merge", None)
        if btn:
            btn.setEnabled(False)
        notify(self, "Merged peaks saved.", 3000)

    def save_merged_peaks(self):
        """Save merged peaks to JSON file and return whether it succeeded."""
        try:
            save_json_atomic(self.merged_peaks_file, self.merged_peaks)
            return True
        except (OSError, TypeError, ValueError) as e:
            logging.warning("Error saving merged peaks: %s", e)
            QMessageBox.warning(self, "Save Failed", f"Could not save merged peaks:\n{e}")
            return False

    def load_merged_peaks(self):
        """Load merged peaks from JSON file"""
        self.merged_peaks = []
        if not os.path.exists(self.merged_peaks_file):
            return
        try:
            with open(self.merged_peaks_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError) as e:
            logging.warning("Error loading merged peaks: %s", e)
            # Move the unreadable file aside instead of leaving it in the
            # save path: the next save would otherwise silently replace
            # every previously saved merge with the empty list.
            try:
                os.replace(self.merged_peaks_file, self.merged_peaks_file + ".corrupt")
            except OSError as move_exc:
                # Non-fatal, but worth saying: the unreadable file stays in the
                # save path and the next save will overwrite it.
                logging.warning(
                    "Could not move aside unreadable merged-peaks file %s: %s",
                    self.merged_peaks_file,
                    move_exc,
                )
            return
        if not isinstance(raw, list):
            logging.warning(
                "Ignoring merged peaks file with unexpected structure: %s",
                self.merged_peaks_file,
            )
            return
        for group in raw:
            indices = group.get("indices") if isinstance(group, dict) else None
            if (
                isinstance(indices, list)
                and indices
                and all(isinstance(i, int) and not isinstance(i, bool) for i in indices)
            ):
                self.merged_peaks.append(group)
