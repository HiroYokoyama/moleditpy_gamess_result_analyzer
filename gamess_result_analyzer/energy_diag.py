"""Interactive orbital energy level diagram with zoom, pan and cube-file lookup."""

import glob
import os
import math
import logging
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QMenu,
    QApplication,
    QToolTip,
)
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QAction

#: CODATA 2018 Hartree energy in eV.
HARTREE_TO_EV = 27.211386245988


def calculate_arrow_shifts(items, val_to_y, threshold=15, distance=20):
    """Compute horizontal offsets so overlapping levels' electron arrows fan out."""
    shifts = {}
    if not items:
        return shifts

    # Group consecutive overlapping items into clusters
    clusters = []
    current_cluster = [0]
    for idx in range(1, len(items)):
        y_prev = val_to_y(items[idx - 1][1])
        y_curr = val_to_y(items[idx][1])
        if abs(y_curr - y_prev) < threshold:
            current_cluster.append(idx)
        else:
            clusters.append(current_cluster)
            current_cluster = [idx]
    clusters.append(current_cluster)

    # Calculate shifts for each cluster
    for cluster in clusters:
        n = len(cluster)
        if n == 1:
            i_orig = items[cluster[0]][0]
            shifts[i_orig] = 0
        else:
            start_shift = (distance / 2) * (n - 1)
            for i, idx in enumerate(cluster):
                i_orig = items[idx][0]
                shift_val = start_shift - i * distance
                shifts[i_orig] = int(shift_val)

    return shifts


class EnergyDiagramDialog(QDialog):
    """Interactive orbital energy level diagram with zoom, pan and cube-file lookup."""

    # pylint: disable=attribute-defined-outside-init
    # Qt/PyVista pattern: label/actor/state attrs are created lazily, not in __init__.

    def __init__(self, mo_data, parent=None, result_dir=None):
        super().__init__(parent)
        self.result_dir = result_dir
        self.setWindowTitle("Orbital Energy Diagram")
        self.resize(500, 600)

        # Enable mouse tracking to receive hover events
        self.setMouseTracking(True)
        self.hit_zones = []

        # Add Save Button overlay
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 20, 20)  # margins
        layout.addStretch()
        btn_layout = QHBoxLayout()
        # Unit Selection
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["eV", "Hartree"])
        self.unit_combo.currentTextChanged.connect(self.update_unit)
        self.lbl_unit = QLabel("Unit:")
        btn_layout.addWidget(self.lbl_unit)
        btn_layout.addWidget(self.unit_combo)

        btn_layout.addStretch()  # Right align

        self.btn_save = QPushButton("Save PNG")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self.save_image)
        self.btn_save.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                border: 1px solid #999;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
        """)

        btn_layout.addWidget(self.btn_save)
        layout.addLayout(btn_layout)

        # Status Label (User Request: Bottom message)
        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(self.status_label)

        self.data = mo_data
        self.is_uhf = self.data["type"] == "UHF"

        # Extract energy levels
        self.energies = self.data["energies"]
        self.occupations = self.data["occupations"]

        # Safety: Flatten occupancy lists if they act weirdly (sometimes list of lists?)
        def safe_occ(occ_list):
            if not occ_list:
                return []
            # Check if first element is list
            if isinstance(occ_list[0], (list, tuple)):
                return [x[0] if len(x) > 0 else 0 for x in occ_list]
            return occ_list

        if self.is_uhf:
            # UHF
            if len(self.energies) == 2 and isinstance(self.energies[0], list):
                self.energies_a = self.energies[0]
                self.energies_b = self.energies[1]
                self.occ_a = safe_occ(self.occupations[0])
                self.occ_b = safe_occ(self.occupations[1])
            else:
                # Fallback
                self.energies_a = self.energies
                self.energies_b = []
                self.occ_a = safe_occ(self.occupations)
                self.occ_b = []
        else:
            # RHF
            self.energies_a = self.energies
            self.occ_a = safe_occ(self.occupations)
            self.energies_b = []
            self.occ_b = []

        all_e = self.energies_a + self.energies_b

        if not all_e:
            self.full_min = -1.0
            self.full_max = 1.0
            h_e, l_e = -0.5, 0.5
        else:
            self.full_min = min(all_e)
            self.full_max = max(all_e)

            # Find HOMO/LUMO for default center
            occupied = [e for i, e in enumerate(self.energies_a) if self.occ_a[i] > 0]
            virtual = [e for i, e in enumerate(self.energies_a) if self.occ_a[i] == 0]

            if self.is_uhf:
                occupied += [
                    e for i, e in enumerate(self.energies_b) if self.occ_b[i] > 0
                ]
                virtual += [
                    e for i, e in enumerate(self.energies_b) if self.occ_b[i] == 0
                ]

            h_e = max(occupied) if occupied else self.full_min
            l_e = min(virtual) if virtual else self.full_max

        # Store for double-click reset
        self.homo_energy = h_e
        self.lumo_energy = l_e
        self.current_min, self.current_max = self._default_view()

    def _default_view(self) -> tuple[float, float]:
        """(min, max) energy window: 3x the HOMO-LUMO gap centred on the gap, at least 0.2 Eh."""
        gap_center = (self.homo_energy + self.lumo_energy) / 2
        gap = abs(self.lumo_energy - self.homo_energy)
        if gap < 0.01:
            gap = 0.05  # Fallback for near-degeneracy
        target_span = max(gap * 3.0, 0.2)
        return gap_center - target_span / 2.0, gap_center + target_span / 2.0

    def wheelEvent(self, event):
        """Zoom the energy range on Ctrl+wheel, otherwise pan it."""
        # Zoom with Ctrl + Wheel
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            angle_delta = event.angleDelta().y()
            if angle_delta != 0:
                # Wheel UP (Positive) -> Zoom IN (Factor < 1.0)
                # Wheel DOWN (Negative) -> Zoom OUT (Factor > 1.0)

                steps = angle_delta / 120.0
                zoom_factor = pow(0.85, steps)  # 15% change per step

                span = self.current_max - self.current_min
                center = (self.current_min + self.current_max) / 2

                # Check for Mouse Position Zooming?
                # For now, Center Zoom is safer and easier to implement reliably.

                new_span = span * zoom_factor

                # Bounds check (Prevent infinite zoom in/out)
                if new_span < 0.1:
                    new_span = 0.1
                if new_span > 2000.0:
                    new_span = 2000.0

                self.current_min = center - new_span / 2
                self.current_max = center + new_span / 2
                self.update()
            return

        # Optimized Pan Logic for Trackpads

        # 1. Try high-res pixel delta (Trackpads usually send this)
        pixel_delta = event.pixelDelta().y()
        angle_delta = event.angleDelta().y()

        # Pixels to Energy Scale estimate
        h = self.height()
        margin_top = 40
        margin_bottom = 40
        draw_h = h - margin_top - margin_bottom

        range_e = self.current_max - self.current_min
        if abs(range_e) < 1e-9:
            range_e = 1.0

        # How much energy per pixel?
        scale_per_pixel = range_e / draw_h if draw_h > 0 else 0.01

        change = 0.0

        if not event.pixelDelta().isNull() and pixel_delta != 0:
            # Trackpad Case: Direct 1:1 mapping feels natural
            # Scroll UP content (positive pixel_delta) -> View moves UP -> Energy increases
            # Sensitivity Factor can be tuned. 1.0 means 1 pixel scroll = 1 pixel shift
            sensitivity = 1.0
            change = pixel_delta * scale_per_pixel * sensitivity
        elif angle_delta != 0:
            # Mouse Wheel Case: Fixed steps
            # 120 units = 1 notch usually.
            # Let's say 120 units = 10% shift

            fraction = angle_delta / 120.0
            change = (range_e * 0.1) * fraction

        # Apply Change
        # Scroll UP (Positive) usually means "Move content Down", so View moves UP.
        # Energy Y axis grows UP.
        # So Positive Delta = Increase Min/Max

        self.current_min += change
        self.current_max += change
        self.update()

    def mouseDoubleClickEvent(self, event):  # pylint: disable=unused-argument
        # Qt override signature requires the event argument.
        """Reset the view to 3x the HOMO-LUMO gap, centered on the gap."""
        # Same window as on open; the old inline copy lacked the minimum span,
        # so a degenerate gap collapsed the view to zero height.
        if getattr(self, "homo_energy", None) is not None and hasattr(
            self, "lumo_energy"
        ):
            self.current_min, self.current_max = self._default_view()
        else:
            # Fallback to full view if HOMO/LUMO not available
            self.current_min = self.full_min - 0.05 * (self.full_max - self.full_min)
            self.current_max = self.full_max + 0.05 * (self.full_max - self.full_min)
        self.update()

    def mousePressEvent(self, event):
        """Load or generate the orbital under the click, else start a drag-to-zoom."""
        if event.button() == Qt.MouseButton.LeftButton:
            # Hit Testing with "Closest to Mouse" logic
            pos = event.position()
            point = pos.toPoint()
            y_click = point.y()

            best_hit = None
            min_dist = 1000.0

            if getattr(self, "hit_zones", None) is not None:
                for rect, index, label, spin_suffix in self.hit_zones:
                    # Check X-bounds first (strict)
                    if point.x() >= rect.left() and point.x() <= rect.right():
                        # Check Y-vicinity (e.g. +/- 10 pixels to allow slack)
                        center_y = rect.center().y()
                        dist = abs(y_click - center_y)

                        if rect.contains(point):
                            if dist < min_dist:
                                min_dist = dist
                                best_hit = (index, label, spin_suffix)

            if best_hit:
                # best_hit is (index, label, spin_suffix)
                self.try_load_cube(best_hit[0], best_hit[1], best_hit[2])
                return

            self.dragging = True
            self.last_mouse_y = event.position().y()

    def try_load_cube(self, index, label, spin_suffix=""):
        """Load the matching cube file for an orbital, or offer to generate one."""
        if not self.result_dir:
            # QMessageBox.information(self, "Info", "No result directory linked.")
            return

        # Pattern matching
        # Files like: "15_HOMO.cube" or "16_LUMO.cube" or "15_MO_15.cube"

        # We need to construct likely filenames based on index (which is reliable)
        # Search pattern: "15_*.cube"

        # Try padded first (normalized sorting)
        # Check for 10a/10b convention if spin_suffix is present
        # spin_suffix is "_A" or "_B" from Diagram click

        patterns = []

        # index is 0-based logic.
        target_idx = int(index)

        if spin_suffix == "_A":
            # Matches "16 (a)" -> "16_a"
            patterns.append(f"*_MO_{target_idx}_a.cube")
            patterns.append(f"*_MO_{target_idx}_alpha.cube")  # Fallback
            # Legacy padded
            patterns.append(f"{target_idx:03d}a_*.cube")
        elif spin_suffix == "_B":
            patterns.append(f"*_MO_{target_idx}_b.cube")
            patterns.append(f"*_MO_{target_idx}_beta.cube")
            patterns.append(f"{target_idx:03d}b_*.cube")
        else:
            # RHF/RKS: "16" -> "16"
            patterns.append(f"*_MO_{target_idx}.cube")
            patterns.append(f"{target_idx:03d}_*.cube")  # Legacy
            patterns.append(f"{index:03d}_*.cube")  # Legacy 0-based

        # Check for dedicated _cubes subfolder
        search_dirs = [self.result_dir]

        # 1. Try to find the exact _cubes subfolder from parser filename
        own_cube_dir_name = None
        base = None
        parent_dlg = self.parent()
        if parent_dlg and hasattr(parent_dlg, "parent_dlg") and parent_dlg.parent_dlg:
            main_dlg = parent_dlg.parent_dlg
            if hasattr(main_dlg, "parser") and main_dlg.parser:
                if hasattr(main_dlg.parser, "filename") and main_dlg.parser.filename:
                    res_dir = os.path.dirname(main_dlg.parser.filename)
                    base = os.path.splitext(os.path.basename(main_dlg.parser.filename))[
                        0
                    ]
                    own_cube_dir_name = f"{base}_cubes"
                    cube_dir = os.path.join(res_dir, own_cube_dir_name)
                    if os.path.exists(cube_dir) and cube_dir not in search_dirs:
                        search_dirs.append(cube_dir)

        # 2. Also search '_cubes' subdirectories of self.result_dir. When the
        # loaded file is known, only its own: another job's "*_cubes" folder
        # in the same directory holds a same-numbered orbital of a different
        # molecule, which then loaded silently as if it were this one.
        if os.path.isdir(self.result_dir):
            try:
                for item in os.listdir(self.result_dir):
                    item_path = os.path.join(self.result_dir, item)
                    wanted = (
                        item == own_cube_dir_name
                        if own_cube_dir_name
                        else item.endswith("_cubes")
                    )
                    if os.path.isdir(item_path) and wanted:
                        if item_path not in search_dirs:
                            search_dirs.append(item_path)
            except OSError as e:
                logging.warning("Error listing result_dir in try_load_cube: %s", e)

        files = []
        for s_dir in search_dirs:
            search_patterns = patterns
            if base:
                exact_patterns = [
                    p.replace("*_MO_", f"{glob.escape(base)}_MO_")
                    for p in patterns
                    if p.startswith("*_MO_")
                ]
                # Legacy names have provenance only inside this job's cube folder.
                search_patterns = exact_patterns
                if os.path.basename(s_dir) == own_cube_dir_name:
                    search_patterns = exact_patterns + [
                        p for p in patterns if not p.startswith("*_MO_")
                    ]
            for p in search_patterns:
                full_p = os.path.join(s_dir, p)
                files = glob.glob(full_p)
                if files:
                    break
            if files:
                break

        if files:
            target = files[0]  # Take first match
            # Call Parent method
            if hasattr(self.parent(), "load_file_by_path"):
                self.parent().load_file_by_path(target)
                # User Request: Bottom message instead of title
                self.status_label.setText(f"Loaded: {os.path.basename(target)}")
                # Clear title notify
                self.setWindowTitle("Orbital Energy Diagram")
        else:
            # File not found
            self.status_label.setText(f"File not found: {label}")
            # User Request: Use orbital number for label
            # Use safe 0-based syntax for worker

            # User Request: Confirm dialog "same with the rest"
            reply = QMessageBox.question(
                self,
                "Confirm Analysis",
                f"Generate cube file for Orbital {label} (Index {index})?\nThis may take some time.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )

            if reply == QMessageBox.StandardButton.Yes:
                if hasattr(self.parent(), "generate_specific_orbital"):
                    self.status_label.setText(f"Generating {label}...")

                    # Force index-based request as per user requirement
                    # "make sure to use index num to generate or find"

                    # We pass the explicit index to guaranteed unambiguous generation
                    self.parent().generate_specific_orbital(index, label, spin_suffix)

    def mouseMoveEvent(self, event):
        """Show an orbital tooltip on hover, or continue a drag-to-zoom."""
        # Check if hovering over a clickable orbital level (when not dragging)
        if not (getattr(self, "dragging", None) is not None and self.dragging):
            pos = event.position()
            point = pos.toPoint()

            hovering_over_orbital = False
            if getattr(self, "hit_zones", None) is not None:
                hit_found = False
                for rect, index, label, spin_suffix in self.hit_zones:
                    if rect.contains(point):
                        hovering_over_orbital = True
                        hit_found = True

                        # 0-based, matching GAMESS's MO numbering.
                        tip_text = f"Index: {index}"
                        if label:
                            # A real newline: "\\n" showed a literal backslash-n.
                            tip_text += f"\n{label}"
                        if spin_suffix:
                            tip_text += f" ({spin_suffix.replace('_', '')})"

                        QToolTip.showText(
                            event.globalPosition().toPoint(), tip_text, self
                        )
                        break

                if not hit_found:
                    QToolTip.hideText()

            # Update cursor based on hover state
            if hovering_over_orbital:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

        # Handle drag-to-zoom functionality
        if getattr(self, "dragging", None) is not None and self.dragging:
            current_y = event.position().y()
            delta_y = current_y - self.last_mouse_y

            # Zoom Logic (User Request: Drag to Zoom)
            # Drag DOWN (Positive Delta) -> Zoom OUT (Factor > 1)
            # Drag UP (Negative Delta) -> Zoom IN (Factor < 1)

            # Sensitivity
            factor = 1.0 + (delta_y * 0.01)

            # Bounds check
            if factor < 0.1:
                factor = 0.1
            if factor > 10.0:
                factor = 10.0

            span = self.current_max - self.current_min
            center = (self.current_min + self.current_max) / 2
            new_span = span * factor

            self.current_min = center - new_span / 2
            self.current_max = center + new_span / 2

            self.last_mouse_y = current_y
            self.update()

    def mouseReleaseEvent(self, event):
        """End a drag-to-zoom gesture."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False

    def contextMenuEvent(self, event):
        """Show a right-click menu offering to save the diagram as PNG."""
        menu = QMenu(self)
        save_act = QAction("Save as PNG...", self)
        save_act.triggered.connect(self.save_image)
        menu.addAction(save_act)
        menu.exec(event.globalPos())

    def save_image(self):
        """Prompt for a path and save the diagram (minus controls) as a PNG."""
        fname, _ = QFileDialog.getSaveFileName(
            self, "Save Diagram", "orbital_diagram.png", "Images (*.png)"
        )
        if fname:
            # User Request: Prevent exporting message (and UI controls)
            widgets_to_restore = []

            # Hide top buttons and bottom label
            if getattr(self, "unit_combo", None) is not None:
                self.unit_combo.setVisible(False)
                widgets_to_restore.append(self.unit_combo)

            if getattr(self, "lbl_unit", None) is not None:
                self.lbl_unit.setVisible(False)
                widgets_to_restore.append(self.lbl_unit)

            if getattr(self, "btn_save", None) is not None:
                self.btn_save.setVisible(False)
                widgets_to_restore.append(self.btn_save)

            if getattr(self, "status_label", None) is not None:
                self.status_label.setVisible(False)
                widgets_to_restore.append(self.status_label)

            QApplication.processEvents()  # Ensure hidden

            pix = self.grab()
            pix.save(fname)

            # Restore
            for w in widgets_to_restore:
                w.setVisible(True)

    def update_unit(self, text):  # pylint: disable=unused-argument
        # text is unused: connected to the combo box's signal, unit is re-read from the widget.
        """Repaint the diagram after the energy unit combo box changes."""
        self.update()

    def paintEvent(self, event):  # pylint: disable=unused-argument
        # Qt override signature requires the event argument.
        """Draw the axis, energy levels, occupation arrows and orbital labels."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Reset Hit Zones
        self.hit_zones = []  # List of (QRect, index, label)
        w = self.width()
        h = self.height()

        # Unit Conversion
        unit = self.unit_combo.currentText()
        factor = 1.0
        unit_label_str = "Ha"
        if unit == "eV":
            factor = HARTREE_TO_EV
            unit_label_str = "eV"

        # Draw Background
        painter.fillRect(0, 0, w, h, QColor("white"))

        min_e = self.current_min
        max_e = self.current_max
        range_e = max_e - min_e
        if abs(range_e) < 1e-9:
            range_e = 1.0

        margin_top = 40
        margin_bottom = 80  # Increased for Save Button
        draw_h = h - margin_top - margin_bottom

        def val_to_y(val):
            rel = (val - min_e) / range_e
            return (h - margin_bottom) - (rel * draw_h)

        # --- Draw Axis ---
        pen_axis = QPen(QColor("black"), 2)
        painter.setPen(pen_axis)
        painter.drawLine(
            60, margin_top, 60, h - margin_bottom
        )  # Shifted right for labels

        # Draw Unit Label
        font_axis = QFont("Arial", 10)
        painter.setFont(font_axis)
        painter.drawText(5, margin_top - 10, unit_label_str)

        # Dynamic Ticks (Round Numbers in Display Units)
        # 1. Determine Range in Display Units
        min_disp = min_e * factor
        max_disp = max_e * factor
        range_disp = max_disp - min_disp
        if abs(range_disp) < 1e-9:
            range_disp = 1.0

        # 2. Calculate Nice Step in Display Units
        target_ticks = 10
        raw_step = range_disp / target_ticks

        try:
            magnitude = 10 ** math.floor(math.log10(raw_step))
            residual = raw_step / magnitude
            if residual > 5:
                step = 10 * magnitude
            elif residual > 2:
                step = 5 * magnitude
            elif residual > 1:
                step = 2 * magnitude
            else:
                step = magnitude
        except (TypeError, ValueError, ZeroDivisionError):
            # ZeroDivision: a subnormal raw_step underflows 10**floor(log10(x)) to 0.0.
            step = 1.0

        if step <= 0:
            step = 1.0

        # 3. Find First Tick in Display Units
        start_tick_disp = math.ceil(min_disp / step) * step

        # 4. Loop Ticks
        current_tick_disp = start_tick_disp
        # Use epsilon to handle float precision in loop
        while current_tick_disp <= max_disp + (step * 0.001):
            # Convert back to Internal Units for Y position
            val_internal = current_tick_disp / factor
            y = val_to_y(val_internal)

            if margin_top <= y <= (h - margin_bottom):
                painter.drawLine(55, int(y), 60, int(y))  # Tick mark

                # Label (Use display value directly)
                label = f"{current_tick_disp:.2f}"

                # Right align text to x=50, Vertically Center
                painter.drawText(
                    5,
                    int(y) - 10,
                    45,
                    20,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )

            current_tick_disp += step

        # Draw Levels
        font = QFont("Arial", 12)
        painter.setFont(font)

        cols = 1 if not self.is_uhf else 2
        # Tighter layout for smaller window
        # Left margin for axis must fit Axis Ticks (x=50) AND Energy Labels (Orbital specific)
        left_margin = 120
        right_margin = 10  # Only for window edge, but column width absorbs text space
        avail_w = w - left_margin - right_margin

        level_w = 50  # Fixed compact width (pixels)
        padding_left = 10  # Space from column start

        def find_somo_indices(energies_a, occ_a, energies_b, occ_b):  # pylint: disable=unused-argument
            # energies_a/energies_b are unused: SOMO is derived from occupation counts alone,
            # assuming orbitals are already energy-sorted from index 0.
            """Find orbitals where Alpha is occupied but Beta is not (SOMO)"""
            somo_indices = set()

            # Use threshold 0.1 to avoid numerical precision errors
            occ_threshold = 0.1
            n_alpha = sum(1 for o in occ_a if o > occ_threshold)
            n_beta = sum(1 for o in occ_b if o > occ_threshold)

            start_somo = n_beta
            end_somo = n_alpha
            for i in range(start_somo, end_somo):
                somo_indices.add(i)
            return somo_indices

        if self.is_uhf:
            somo_set = find_somo_indices(
                self.energies_a, self.occ_a, self.energies_b, self.occ_b
            )
        else:
            somo_set = set()

        def draw_levels(energies, occs, col_idx, title):
            total_w = self.width()

            # Centering Logic
            if not self.is_uhf:
                center_of_window = total_w / 2
                line_center_x = center_of_window

                # Reverse calculate col_start
                raw_col_start = line_center_x - (level_w / 2) - padding_left

                # CLAMP against left_margin to prevent clipping of energy labels
                col_start = max(raw_col_start, left_margin)
            else:
                # UKS Logic: Use calculated column widths respecting margins
                u_col_width = avail_w / cols
                col_start = left_margin + col_idx * u_col_width

            target_x1 = col_start + padding_left
            center_x = target_x1 + level_w / 2

            painter.setPen(QColor("black"))

            # Title with Electron Count
            n_elec = sum(occs)
            title_text = f"{title}\\n({n_elec:.0f}e)"

            fm = painter.fontMetrics()
            lines = title_text.split("\\n")
            y_title_base = 20

            for line in lines:
                t_w = fm.horizontalAdvance(line)
                title_x = center_x - t_w / 2
                painter.drawText(int(title_x), y_title_base, line)
                y_title_base += 15

            homo_idx = -1
            for i, o in enumerate(occs):
                if o > 0:
                    homo_idx = i

            # Lists
            occupied_items = []
            virtual_items = []

            for (
                i,
                e,
            ) in enumerate(energies):
                if min_e <= e <= max_e:
                    item = (i, e, occs[i])
                    if occs[i] > 0:
                        occupied_items.append(item)
                    else:
                        virtual_items.append(item)
            occupied_items.sort(key=lambda x: x[1], reverse=True)
            virtual_items.sort(key=lambda x: x[1], reverse=False)

            distance = 10 if self.is_uhf else 20
            shifts = calculate_arrow_shifts(occupied_items, val_to_y, distance=distance)

            # Colors and Labels
            is_alpha_col = title == "Alpha"
            is_beta_col = title == "Beta"

            # Color Definition
            if is_alpha_col:
                col_occ = QColor(180, 50, 50)
                col_vir = QColor(180, 50, 50)
            elif is_beta_col:
                col_occ = QColor(50, 50, 180)
                col_vir = QColor(50, 50, 180)
            else:
                col_occ = QColor("black")
                col_vir = QColor("black")

            def process_list(items, last_y_ref):
                new_last_y = last_y_ref
                for i_orig, e, occ_val in items:
                    y = val_to_y(e)

                    is_occ = occ_val > 0
                    color = col_occ if is_occ else col_vir
                    pen = QPen(color, 2)

                    is_somo = is_alpha_col and i_orig in somo_set
                    is_homo = i_orig == homo_idx
                    is_lumo = i_orig == homo_idx + 1

                    # Highlight important levels
                    if is_somo or is_homo or is_lumo:
                        pen.setWidth(3)

                    painter.setPen(pen)
                    x1 = center_x - level_w / 2
                    x2 = center_x + level_w / 2
                    painter.drawLine(int(x1), int(y), int(x2), int(y))

                    # Electron Icons (Arrows)
                    if is_occ:
                        painter.setPen(QColor("black"))
                        f_icon = QFont("Arial", 16, QFont.Weight.Bold)
                        painter.setFont(f_icon)

                        arrow_txt = ""
                        if not self.is_uhf:
                            if abs(occ_val - 1.0) < 0.1:
                                arrow_txt = "↑"  # Singly occupied (ROKS)
                            else:
                                arrow_txt = "↑↓"  # Doubly occupied
                        else:
                            if is_alpha_col:
                                arrow_txt = "↑"
                            elif is_beta_col:
                                arrow_txt = "↓"

                        shift_x = shifts.get(i_orig, 0)
                        rect_icon = QRect(
                            int(x1) + shift_x, int(y) - 14, int(level_w), 28
                        )
                        painter.drawText(
                            rect_icon, Qt.AlignmentFlag.AlignCenter, arrow_txt
                        )

                        painter.setFont(font)

                    # Label Logic
                    label_txt = ""

                    # 1. ROKS SOMO Special Case
                    is_roks_somo = (not self.is_uhf) and (abs(occ_val - 1.0) < 0.1)

                    if is_roks_somo:
                        label_txt = "SOMO"
                    elif i_orig <= homo_idx:
                        diff = homo_idx - i_orig
                        if diff == 0:
                            label_txt = "HOMO"
                        else:
                            label_txt = f"HOMO-{diff}"
                    else:
                        diff = i_orig - (homo_idx + 1)
                        label_txt = "LUMO" if diff == 0 else f"LUMO+{diff}"

                    if label_txt:
                        painter.setPen(QColor("black"))
                        painter.drawText(int(x2) + 4, int(y) + 4, label_txt)

                    # Energy Values
                    vis_e_str = f"{e * factor:.2f} {unit_label_str}"
                    painter.setPen(QColor("black"))
                    rect_e = QRect(int(x1) - 85, int(y) - 7, 80, 14)
                    painter.drawText(
                        rect_e,
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        vis_e_str,
                    )

                    # Store Hit Zone
                    r = QRect(int(x1), int(y) - 7, int(level_w + 80), 14)
                    gen_label = label_txt if label_txt else f"MO_{i_orig}"

                    spin_suffix = ""
                    if self.is_uhf:
                        spin_suffix = "_A" if is_alpha_col else "_B"

                    self.hit_zones.append((r, i_orig, gen_label, spin_suffix))

                return new_last_y

            process_list(occupied_items, -1000)
            process_list(virtual_items, 10000)

        if self.is_uhf:
            draw_levels(self.energies_a, self.occ_a, 0, "Alpha")
            draw_levels(self.energies_b, self.occ_b, 1, "Beta")
        else:
            draw_levels(self.energies_a, self.occ_a, 0, "Orbitals")
