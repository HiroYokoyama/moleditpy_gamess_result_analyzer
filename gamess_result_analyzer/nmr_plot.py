"""NMR dialog mixin: stick spectrum plotting, highlighting and 3D labels."""

import re
import logging
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
import pyvista as pv
import numpy as np

from matplotlib.ticker import MaxNLocator

# Import RDKit for VDW radii calculation
try:
    from rdkit import Chem

    _pt = Chem.GetPeriodicTable()  # pylint: disable=no-member
    # Base VDW radii (scaled by 0.3 as in moledit core)
    VDW_RADII = {_pt.GetElementSymbol(i): _pt.GetRvdw(i) * 0.3 for i in range(1, 119)}
except ImportError:
    VDW_RADII = {"H": 1.2 * 0.3, "C": 1.7 * 0.3, "N": 1.55 * 0.3, "O": 1.52 * 0.3}


class _NMRPlotMixin:
    def reset_zoom(self, event):
        """Reset plot zoom on double click"""
        if event.dblclick:
            self.toolbar.home()

    def plot_spectrum(self):
        """Draw the NMR stick spectrum"""
        self.figure.clear()
        ax = self.figure.add_subplot(111)

        # Peaks come from the shared builder
        old_metadata = getattr(self, "peaks_metadata", None) or []
        self.peaks_metadata = self._get_current_peaks()
        self._remap_selection_to_new_peaks(old_metadata)
        self.current_shifts = [p[0] for p in self.peaks_metadata]

        if not self.peaks_metadata:
            ax.text(
                0.5,
                0.5,
                "No data to display",
                ha="center",
                va="center",
                transform=ax.transAxes,
                fontsize=14,
            )
            self.canvas.draw_idle()
            return

        # Split the peak tuples into the two series matplotlib wants
        shifts = [p[0] for p in self.peaks_metadata]
        intensities = [p[1] for p in self.peaks_metadata]

        self.current_shifts = shifts

        # X-axis range and padding
        min_shift, max_shift = min(shifts), max(shifts)
        padding = (
            max(0.5, (max_shift - min_shift) * 0.15) if max_shift > min_shift else 5.0
        )

        _markerline, stemlines, baseline = ax.stem(
            shifts, intensities, linefmt="b-", markerfmt="None", basefmt="k-"
        )
        stemlines.set_linewidth(2.5)
        stemlines.set_alpha(0.8)
        baseline.set_alpha(0.3)

        if self.chk_auto_x.isChecked():
            ax.set_xlim(max_shift + padding, min_shift - padding)
        else:
            ax.set_xlim(self.spin_x_max.value(), self.spin_x_min.value())

        ax.set_ylim(0, max(intensities) * 1.2)
        ax.set_xlabel("Chemical Shift δ (ppm)", fontsize=10, fontweight="bold")
        ax.set_ylabel(
            f"{self.current_nucleus} Count"
            if self.current_nucleus != "All"
            else "Atom Count",
            fontsize=10,
            fontweight="bold",
        )
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.grid(True, alpha=0.2, linestyle="--")

        display_nuc = self.get_nucleus_key(self.current_nucleus)
        ax.set_title(
            f"{display_nuc} NMR Stick Spectrum", fontsize=12, fontweight="bold", pad=20
        )

        # Redraw the highlight for the current selection
        if self.selected_peak_indices:
            self.highlight_selected_peaks()

        self.figure.tight_layout()
        self.canvas.draw_idle()

    def highlight_selected_peaks(self):
        """Add red highlights and labels to selected peaks"""
        if not getattr(self, "current_shifts", None):
            # Clear highlights if no selection
            for artist in self.highlight_artists:
                try:
                    artist.remove()
                except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                    logging.debug(
                        "NMR: could not remove a peak-highlight artist: %s", e
                    )
            self.highlight_artists = []
            self.canvas.draw_idle()
            return

        ax = self.figure.axes[0] if self.figure.axes else None
        if not ax:
            return

        # Clear old highlights and labels
        for artist in self.highlight_artists:
            try:
                artist.remove()
            except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                logging.debug("NMR: could not remove a peak-highlight artist: %s", e)
        self.highlight_artists = []

        # Add red highlights and text labels for selected peaks
        for idx in self.selected_peak_indices:
            if idx < len(self.current_shifts):
                shift = self.current_shifts[idx]

                # Draw red line over selected peak (only if not in show_all_mode)
                if not self.show_all_mode:
                    line = ax.axvline(
                        shift,
                        ymin=0,
                        ymax=1,
                        color="red",
                        linewidth=3.5,
                        alpha=0.7,
                        zorder=10,
                    )
                    self.highlight_artists.append(line)

                # Add text label above the peak
                if idx < len(self.peaks_metadata):
                    # Get peak metadata
                    _, _, _is_merged, atom_indices = self.peaks_metadata[idx]

                    # Build label text from all atoms in this peak
                    label_parts = []
                    for atom_idx in atom_indices:
                        atom_item = next(
                            (
                                d
                                for d in self.data
                                if d.get("atom_idx", None) == atom_idx
                            ),
                            None,
                        )
                        if atom_item:
                            atom_sym = atom_item.get("atom_sym", "?")
                            label_parts.append(f"{atom_sym}{atom_idx}")

                    label_text = ",".join(label_parts) if label_parts else "?"

                    # Position label above the peak
                    label_color = "black" if self.show_all_mode else "red"
                    text = ax.text(
                        shift,
                        1.1,
                        label_text,
                        ha="center",
                        va="bottom",
                        fontsize=10,
                        fontweight="bold",
                        color=label_color,
                        zorder=11,
                    )
                    self.highlight_artists.append(text)

        self.canvas.draw_idle()

    def _get_current_peaks(self):
        """
        Build the peak list shared by the plot and the analysis views, honouring
        the current nucleus filter, merge groups and reference values.
        Returns: List of (delta, intensity, is_merged, atom_indices)
        """
        # Fast atom-index -> data lookup
        atom_map = {d["atom_idx"]: d for d in self.data}
        # Atoms currently on screen for this nucleus
        displayed_indices = {d["atom_idx"] for d in self.displayed_data}

        peaks = []
        processed_indices = set()

        # 1. Merged groups
        for group in self.merged_peaks:
            indices = group["indices"]
            # Include the group only if one of its atoms is on screen
            if any(idx in displayed_indices for idx in indices):
                total_sigma = 0.0
                valid_count = 0
                for idx in indices:
                    atom = atom_map.get(idx, None)
                    if atom:
                        total_sigma += atom.get("shielding", 0.0)
                        valid_count += 1

                if valid_count > 0:
                    avg_sigma = total_sigma / valid_count
                    avg_delta = self.delta_ref + (self.sigma_ref - avg_sigma)
                    # Intensity is the number of merged atoms (the integral)
                    peaks.append((avg_delta, float(len(indices)), True, indices))
                    processed_indices.update(indices)

        # 2. Individual, unmerged atoms
        for item in self.displayed_data:
            idx = item.get("atom_idx", None)
            if idx not in processed_indices:
                sigma = item.get("shielding", 0.0)
                delta = self.delta_ref + (self.sigma_ref - sigma)
                peaks.append((delta, 1.0, False, [idx]))

        # Descending chemical shift, the NMR convention
        return sorted(peaks, key=lambda x: x[0], reverse=True)

    def _check_external_selection(self):
        """Poll main window for 3D selection changes"""
        if not hasattr(self.parent_dlg, "mw"):
            return

        mw = self.parent_dlg.mw

        # 0. Check if Selection Mode is active (Optimization: Don't hijack selection if user is doing something else)
        # Assuming mw has a 'current_mode' or we check if 'SelectionTool' is active if that structure exists.
        # Fallback: If mw has 'selection_enabled' flag.
        # 0. Check if Selection Mode is active
        # We removed the mw.scene.mode check because it refers to the 2D editor mode.
        # 3D selection should be allowed unless we are in a specific conflicting mode like Measurement.

        if (
            getattr(mw.edit_3d_manager, "measurement_mode", False)
            if hasattr(mw, "edit_3d_manager")
            else False
        ):
            # Measurement mode uses a different selection list
            pass

        indices = set()

        e3d = getattr(mw, "edit_3d_manager", None)

        # Check standard 3D selection
        if e3d and e3d.selected_atoms_3d:
            indices.update(e3d.selected_atoms_3d)

        # Check measurement selection
        if e3d and e3d.selected_atoms_for_measurement:
            for item in e3d.selected_atoms_for_measurement:
                if isinstance(item, int):
                    indices.add(item)

        # 1. State Tracking for Stability
        # Compare current 3D selection with what we LAST knew about/set.
        # If they are identical, NO CHANGE has happened, so we do nothing.
        # This prevents the "Echo" loop where we clear the selection (setting it to empty)
        # and then this poller sees "Empty" vs "My Internal Selection" and clears the graph.

        current_mw_selection_frozen = frozenset(indices)

        # Initialize tracker if missing
        if getattr(self, "_last_synced_mw_selection", None) is None:
            self._last_synced_mw_selection = frozenset()

        # If the 3D selection HAS NOT CHANGED from what we last saw/set, STOP.
        if current_mw_selection_frozen == self._last_synced_mw_selection:
            return

        # Update our tracker to the new state
        self._last_synced_mw_selection = current_mw_selection_frozen

        # Calculate what the NMR selection SHOULD be based on the current 3D selection
        # Note: We use the "Any Member" rule for selecting, but by not expanding the 3D set,
        # unselection of the specific clicked atom correctly clears the indices.
        new_peak_selection = self._calculate_peak_selection_from_atoms(indices)

        # Only update if the peak selection itself has changed
        if new_peak_selection != self.selected_peak_indices:
            # If 3D selection is empty, force clear
            if not indices:
                self.clear_peak_selection()
            else:
                self.selected_peak_indices = new_peak_selection
                self.highlight_selected_peaks()
                # Update visual labels and spheres (Yellow),
                # but tell it NOT to sync back to the main window's selection set (Green).
                self.update_selected_labels(is_external_sync=True)

    def _calculate_peak_selection_from_atoms(self, target_atoms):
        """Helper to determine which peaks should be selected based on atom set"""
        if not getattr(self, "peaks_metadata", None):
            return set()

        new_selection = set()
        target_atoms = {int(i) for i in target_atoms}

        for peak_idx, metadata in enumerate(self.peaks_metadata):
            # metadata: (shift, intensity, is_merged, atom_indices)
            _, _, _, peak_atoms = metadata
            peak_atoms_set = {int(i) for i in peak_atoms}

            # Selection Rule: A peak is selected if ANY of its atoms are in the 3D selection set.
            # This is stable and prevents the "flicker" caused by switching between ANY and SUBSET.
            if not peak_atoms_set.isdisjoint(target_atoms):
                new_selection.add(peak_idx)
        return new_selection

    def _remap_selection_to_new_peaks(self, old_metadata):
        """Re-derive selected_peak_indices after peaks_metadata is rebuilt.

        The selection stores positions into peaks_metadata; a rebuild
        (nucleus filter, reference change, merge/unmerge) reorders that
        list, so the same numbers would silently point at different peaks
        — and a later Merge/Unmerge would then destroy the wrong groups.
        """
        if not self.selected_peak_indices:
            return
        selected_atoms = set()
        for idx in self.selected_peak_indices:
            if isinstance(idx, int) and 0 <= idx < len(old_metadata):
                selected_atoms.update(old_metadata[idx][3])
        self.selected_peak_indices = self._calculate_peak_selection_from_atoms(
            selected_atoms
        )

    def select_peaks_by_atom_indices(self, atom_indices):
        """Deprecated/Legacy: Now uses _check_external_selection logic directly"""
        # Kept for potential internal calls, but redirected to robust logic
        new_peaks = self._calculate_peak_selection_from_atoms(atom_indices)
        if new_peaks != self.selected_peak_indices:
            self.selected_peak_indices = new_peaks
            self.highlight_selected_peaks()
            self.update_selected_labels()

    def on_peak_click(self, event):
        """Handle clicking on a peak in the spectrum"""
        # Only allow left-click
        if (
            getattr(event, "button", None) != 1
            or event.inaxes is None
            or not getattr(self, "current_shifts", None)
        ):
            return

        click_x = event.xdata
        if click_x is None:
            return

        # Find nearest peak within tolerance (relative to current x-axis range)
        xlim = event.inaxes.get_xlim()
        x_range = abs(xlim[1] - xlim[0])
        tolerance = x_range * 0.01  # 1% of view width

        distances = [abs(shift - click_x) for shift in self.current_shifts]
        min_distance = min(distances)

        if min_distance > tolerance:
            return  # Click too far from any peak

        # Find the clicked peak index
        peak_idx = distances.index(min_distance)

        # Check for Shift or Ctrl key (using Qt modifiers)
        modifiers = QApplication.keyboardModifiers()
        is_multi = bool(
            modifiers
            & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier)
        )

        if peak_idx < len(self.current_shifts):
            if not is_multi:
                # Normal click
                if (
                    len(self.selected_peak_indices) == 1
                    and peak_idx in self.selected_peak_indices
                ):
                    # User clicked the ONLY selected peak -> Toggle OFF (Deselect)
                    self.selected_peak_indices = set()
                else:
                    # New peak, or switching from multi-selection -> Select ONLY this peak
                    self.selected_peak_indices = {peak_idx}
            else:
                # Shift or Ctrl + Click: Toggle selection
                if peak_idx in self.selected_peak_indices:
                    self.selected_peak_indices.remove(peak_idx)
                else:
                    self.selected_peak_indices.add(peak_idx)

            # Force a fresh draw on next poll if desired
            self._last_highlight_atoms = set()

            # Update highlights
            self.highlight_selected_peaks()

            # Update 3D labels for all selected peaks
            self.update_selected_labels()

    def clear_peak_selection(self):
        """Clear all selected peaks and their labels"""
        # Clear selected peaks
        self.selected_peak_indices.clear()

        # Clear highlights
        for artist in self.highlight_artists:
            try:
                artist.remove()
            except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                logging.debug(
                    "NMR: could not remove a peak-highlight artist while clearing selection: %s",
                    e,
                )
        self.highlight_artists = []

        # Clear 3D labels
        self.clear_atom_labels()

        # [Commented out to avoid doubled spheres]
        if hasattr(self.parent_dlg, "mw"):
            mw = self.parent_dlg.mw
            e3d = getattr(mw, "edit_3d_manager", None)
            if e3d:
                e3d.selected_atoms_3d.clear()
                try:
                    e3d.update_3d_selection_display()
                except (AttributeError, RuntimeError) as e:
                    logging.warning(
                        "NMR: could not update the 3D selection display after clearing peaks: %s",
                        e,
                    )

        # Redraw spectrum
        if getattr(self, "canvas", None) is not None:
            self.canvas.draw_idle()

    def update_selected_labels(self, is_external_sync=False):
        """Update 3D labels and spheres for all selected peaks"""
        # 1. Clear existing labels
        self.clear_atom_labels()

        # 2. Add labels for each selected peak
        all_peak_indices = set()

        for peak_idx in sorted(self.selected_peak_indices):
            if peak_idx < len(self.peaks_metadata):
                # Get metadata for this peak (shift, intensity, is_merged, atom_indices)
                peak_shift, _, is_merged, atom_indices = self.peaks_metadata[peak_idx]

                # Add label for each atom in this peak (handles both merged and individual)
                for atom_idx in atom_indices:
                    all_peak_indices.add(atom_idx)
                    # Find atom symbol from data
                    atom_item = next(
                        (d for d in self.data if d.get("atom_idx", None) == atom_idx),
                        None,
                    )
                    if atom_item:
                        atom_sym = atom_item.get("atom_sym", "?")
                        shift_text = None
                        if self._shift_labels_enabled():
                            # Per-atom original shift; for merged peaks show
                            # both the atom's own value and the merged
                            # (averaged) one.
                            own_delta = getattr(self, "delta_ref", 0.0) + (
                                getattr(self, "sigma_ref", 0.0)
                                - atom_item.get("shielding", 0.0)
                            )
                            if is_merged:
                                shift_text = f"δ {own_delta:.2f} → {peak_shift:.2f}"
                            else:
                                shift_text = f"δ {own_delta:.2f}"
                        self.add_atom_label(atom_idx, atom_sym, shift_text)

        # 3. Synchronize with Main Window
        if hasattr(self.parent_dlg, "mw"):
            mw = self.parent_dlg.mw

            # If this is a sync FROM 3D (user clicked in viewer), we should NOT clear the 3D selection!
            # We only clear it if the user clicked in the Graph (internal sync), to replace Green with Yellow.
            if not is_external_sync:
                # Ensure we don't have double spheres (Green + Yellow).
                # We clear the global selection so ONLY our internal Yellow spheres are visible.
                e3d = getattr(mw, "edit_3d_manager", None)
                if e3d:
                    e3d.selected_atoms_3d.clear()

                # CRITICAL: Update our sync tracker so the polling loop knows WE did this
                # and doesn't interpret the empty set as a user unselection.
                self._last_synced_mw_selection = frozenset()

                # Sync to MW if we are the originator (internal sync)
                e3d = getattr(mw, "edit_3d_manager", None)
                if e3d:
                    try:
                        e3d.update_3d_selection_display()
                    except (AttributeError, RuntimeError) as e:
                        logging.warning(
                            "NMR: could not sync the 3D selection display to the main window: %s",
                            e,
                        )

            # Draw yellow highlights for NMR selection
            self.draw_custom_nmr_highlights_3d(all_peak_indices)

            # Render once after all labels added
            v3d = getattr(self.parent_dlg.mw, "view_3d_manager", None)
            if v3d and hasattr(v3d, "plotter"):
                v3d.plotter.render()

    def _shift_labels_enabled(self):
        """Whether 3D labels should include chemical shift values.

        Uses an explicit isinstance-free truthiness contract: the checkbox may
        be absent on test fakes, in which case shifts stay hidden (the
        default).
        """
        chk = getattr(self, "chk_label_shifts", None)
        try:
            return bool(chk is not None and chk.isChecked())
        except (RuntimeError, AttributeError):
            return False

    def on_label_shifts_toggled(self):
        """Re-render the current selection's labels with/without shifts."""
        # is_external_sync=True: only refresh labels, never clear the 3D
        # selection the user may have made in the viewer.
        if self.selected_peak_indices:
            self.update_selected_labels(is_external_sync=True)

    def add_atom_label(self, atom_idx, atom_sym, shift_text=None):
        """Add a single atom label to 3D viewer.

        shift_text: optional second label line with the chemical shift
        (e.g. "δ 7.26" or, for merged peaks, "δ 7.10 → 7.26").
        """
        # Check if parent has plotter
        v3d = (
            getattr(self.parent_dlg.mw, "view_3d_manager", None)
            if hasattr(self.parent_dlg, "mw")
            else None
        )
        if not v3d or not hasattr(v3d, "plotter"):
            return

        # Get coordinates from 3D viewer to match current view
        if hasattr(v3d, "atom_positions_3d") and atom_idx < len(v3d.atom_positions_3d):
            pos = v3d.atom_positions_3d[atom_idx]
        else:
            # Fallback to parser data
            coords = self.parent_dlg.parser.data.get("coords", [])
            if not coords or atom_idx >= len(coords):
                return
            pos = coords[atom_idx]

        try:
            label_pos = [pos[0], pos[1], pos[2] + 0.4]  # Offset above atom

            label_text = f"{atom_sym}{atom_idx}"
            if shift_text:
                label_text += f"\n{shift_text}"

            label_name = f"nmr_label_{atom_idx}"
            actor = v3d.plotter.add_point_labels(
                [label_pos],
                [label_text],
                font_size=12,
                text_color="cyan",
                point_size=0,
                always_visible=True,
                bold=True,
                name=label_name,
            )
            self._atom_labels.append(actor)
            self._nmr_label_names.append(label_name)
        # C++ library boundary: RDKit/VTK/pyvista exceptions do not map to Python types
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning(
                "NMR: could not add the shift label for atom %d to the 3D view: %s",
                atom_idx,
                e,
            )

    def highlight_atom_in_3d(self, atom_idx, atom_sym):
        """Highlight selected atom with a label in 3D viewer (legacy - now uses update_selected_labels)"""
        # This is now handled by update_selected_labels

    def toggle_all_labels(self):
        """Toggle showing all atom labels on the spectrum graph"""
        show_all = self.chk_show_all_labels.isChecked()

        if show_all:
            # Enable show all mode (labels without red highlights)
            self.show_all_mode = True
            self.selected_peak_indices.clear()

            # Select all peaks to show labels
            # Use peaks_metadata length if available, otherwise displayed_data as fallback
            count = (
                len(self.peaks_metadata)
                if getattr(self, "peaks_metadata", None) is not None
                else len(self.displayed_data)
            )
            for i in range(count):
                self.selected_peak_indices.add(i)

            # Update graph with labels only (no red highlights)
            self.highlight_selected_peaks()

            # For "Show All", we also want to update the 3D view to reflect "All"
            # or at least clear the specific "red" selection we had.
            # If we want to show labels for ALL atoms in 3D:
            self.update_selected_labels()
        else:
            # Disable show all mode
            self.show_all_mode = False
            # Clear all selections (graph and 3D)
            self.clear_peak_selection()

    def clear_atom_labels(self):
        """Remove all atom labels and custom selection spheres from 3D viewer"""
        v3d = (
            getattr(self.parent_dlg.mw, "view_3d_manager", None)
            if hasattr(self.parent_dlg, "mw")
            else None
        )
        if not v3d or not hasattr(v3d, "plotter"):
            return

        plotter = v3d.plotter

        # 1. Clear custom NMR selection spheres by name (most reliable in PyVista)
        try:
            plotter.remove_actor("nmr_selection_highlights")
        except (RuntimeError, AttributeError, KeyError, ValueError) as e:
            logging.debug("NMR: could not remove the selection-highlight actor: %s", e)

        # 2. Clear labels by tracked name
        if getattr(self, "_nmr_label_names", None) is not None:
            for name in self._nmr_label_names:
                try:
                    plotter.remove_actor(name)
                except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                    logging.debug("NMR: could not remove label actor %s: %s", name, e)
            self._nmr_label_names = []

        # 3. Fallback: Clear labels by list reference
        for actor in self._atom_labels:
            try:
                plotter.remove_actor(actor)
            except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                logging.debug("NMR: could not remove a tracked atom-label actor: %s", e)
        self._atom_labels = []

        # 4. Clean up private spheres actor list
        if getattr(self, "_nmr_sphere_actors", None) is not None:
            for actor in self._nmr_sphere_actors:
                try:
                    plotter.remove_actor(actor)
                except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                    logging.debug(
                        "NMR: could not remove a selection-sphere actor: %s", e
                    )
            self._nmr_sphere_actors = []

        try:
            plotter.render()
        except (RuntimeError, AttributeError, KeyError, ValueError) as e:
            logging.debug(
                "NMR: could not render the plotter after clearing atom labels: %s", e
            )

    def draw_custom_nmr_highlights_3d(self, atom_indices):
        """Draw yellow highlight spheres for selected atoms in 3D viewer"""
        mw = self.parent_dlg.mw if hasattr(self.parent_dlg, "mw") else None
        v3d = getattr(mw, "view_3d_manager", None) if mw else None
        if not v3d or not hasattr(v3d, "plotter"):
            return

        plotter = v3d.plotter

        # ALWAYS clear existing custom highlights first to prevent stacking/phantom spheres
        try:
            plotter.remove_actor("nmr_selection_highlights")
        except (RuntimeError, AttributeError, KeyError, ValueError) as e:
            logging.debug(
                "NMR: could not remove the previous selection-highlight actor: %s", e
            )

        # Clear tracker list to prevent stale references
        self._nmr_sphere_actors = []

        # If no indices provided, just render the cleared state and return
        if not atom_indices or not hasattr(v3d, "atom_positions_3d"):
            try:
                plotter.render()
            except (RuntimeError, AttributeError, KeyError, ValueError) as e:
                logging.debug(
                    "NMR: could not render the plotter after clearing highlights: %s", e
                )
            return

        indices = list(atom_indices)
        valid_indices = [
            i for i in indices if i < len(mw.view_3d_manager.atom_positions_3d)
        ]

        if not valid_indices:
            return

        try:
            # Get positions
            selected_positions = mw.view_3d_manager.atom_positions_3d[valid_indices]

            # Highlight sphere size: 40% (1.4x) relative to VDW radii per user request
            radii = []

            for i in valid_indices:
                try:
                    # Try to match the exact radius used by the 3D viewer
                    base_r = float(mw.view_3d_manager.glyph_source["radii"][i])
                    if base_r < 0.1:
                        raise ValueError("Radius too small")
                except (
                    RuntimeError,
                    AttributeError,
                    KeyError,
                    IndexError,
                    TypeError,
                    ValueError,
                ):
                    # Fallback to calculated radius if not available
                    atom_item = next(
                        (d for d in self.data if i == d.get("atom_idx", None)), None
                    )
                    sym = atom_item.get("atom_sym", "C") if atom_item else "C"
                    # Strip isotopes like "13C" -> "C"
                    clean_sym = re.sub(r"[^A-Za-z]", "", sym)
                    base_r = VDW_RADII.get(clean_sym, 0.4)

                # Use 1.4x scaling factor (40% larger)
                r = base_r * 1.4
                radii.append(r)

            # Create glyphs for highlights
            highlight_source = pv.PolyData(selected_positions)
            highlight_source["radii"] = np.array(radii)

            highlight_glyphs = highlight_source.glyph(
                scale="radii",
                geom=pv.Sphere(radius=1.0, theta_resolution=16, phi_resolution=16),
                orient=False,
            )

            # Add to plotter and track actor
            actor = plotter.add_mesh(
                highlight_glyphs,
                color="yellow",
                opacity=0.3,
                name="nmr_selection_highlights",
            )
            self._nmr_sphere_actors.append(actor)
            plotter.render()

        # C++ library boundary: RDKit/VTK/pyvista exceptions do not map to Python types
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning(
                "NMR: could not draw selection-highlight spheres for atoms %s: %s",
                atom_indices,
                e,
            )
