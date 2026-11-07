"""Reusable spectrum plot widget: sticks/GAMESS broadening, zoom sync, CSV/PNG export."""

import csv
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import Signal, Qt
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
import logging


class MplCanvas(FigureCanvasQTAgg):
    """Matplotlib figure canvas with a single constrained-layout axes."""

    def __init__(self, parent=None, width=5, height=4, dpi=100):  # pylint: disable=unused-argument
        # parent is unused: layout.addWidget() reparents the canvas once it is added.
        # Use constrained_layout for robust handling of dual axes and labels
        fig = Figure(figsize=(width, height), dpi=dpi, constrained_layout=True)
        self.axes = fig.add_subplot(111)
        super().__init__(fig)


class SpectrumWidget(QWidget):
    """Sticks/GAMESS spectrum plot with click-to-select, zoom sync and export."""

    # pylint: disable=attribute-defined-outside-init,access-member-before-definition
    # Qt pattern: widget/state attributes are created lazily on first use, not in __init__.

    clicked = Signal(object)
    range_changed = Signal(
        float, float, float, float, bool
    )  # xmin, xmax, ymin, ymax, is_manual

    def __init__(
        self,
        data_list,
        x_key="energy",
        y_key="intensity",
        x_unit="nm",
        y_unit="Intensity",
        sigma=20.0,
        invert_x=False,
        invert_y=False,
    ):
        super().__init__()
        self.data_list = data_list
        self.x_key = x_key
        self.y_key = y_key
        self.y_key_sticks = (
            None  # Key for stick data (if different from GAMESS curve)
        )
        self.x_unit = x_unit
        self.y_unit = y_unit
        self.y_unit_sticks = "Intensity"  # Default for dual axis stick labels
        self.sigma = sigma
        self.invert_x = invert_x
        self.invert_y = invert_y
        self.normalization_mode = "height"  # 'height' or 'area'
        self.broaden_in_energy = False  # If True, broaden in cm-1

        self.x_range = None
        self.y_range = None

        if "freq" in x_unit.lower() or "cm" in x_unit.lower():
            self.invert_x = True
            self.x_range = (400, 4000)

        self.show_sticks = True
        self.show_gamess = True
        self.show_markers = True
        self.show_legend = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Increase default size for better visibility
        self.canvas = MplCanvas(self, width=8, height=5, dpi=100)
        layout.addWidget(self.canvas)

        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        layout.addWidget(self.toolbar)

        self.selected_item = None
        self._initial_plot_done = False
        self._is_plotting = False
        self.plot_spectrum()

        self.canvas.mpl_connect("button_press_event", self.on_click)
        # Connect to axis changes (zoom/pan)
        self.canvas.axes.callbacks.connect("xlim_changed", self._on_axes_changed)
        self.canvas.axes.callbacks.connect("ylim_changed", self._on_axes_changed)

    def _on_axes_changed(self, ax):  # pylint: disable=unused-argument
        # ax is unused: matplotlib's xlim_changed/ylim_changed callback signature.
        if self._is_plotting or not self._initial_plot_done:
            return

        xlim = self.canvas.axes.get_xlim()
        ylim = self.canvas.axes.get_ylim()

        xmin, xmax = min(xlim), max(xlim)
        ymin, ymax = min(ylim), max(ylim)

        # Update internal range so redraws (from selection etc) preserve this zoom
        # We only update if it's not Auto? Actually better to update always.
        self.x_range = (xmin, xmax)
        self.y_range = (ymin, ymax)

        self.range_changed.emit(xmin, xmax, ymin, ymax, True)

    def set_selected_item(self, item):
        """Highlight *item* on the plot and redraw."""
        self.selected_item = item
        self.plot_spectrum()

    def set_data(self, data_list):
        """Replace the plotted data points and redraw."""
        self.data_list = data_list
        self.plot_spectrum()

    def set_x_range(self, x_min, x_max):
        """Fix the X-axis to the given range and redraw."""
        self.x_range = (x_min, x_max)
        self.plot_spectrum()

    def set_auto_x_range(self):
        """Let the X-axis range be computed from the data again, and redraw."""
        self.x_range = None
        self.plot_spectrum()

    def set_y_range(self, y_min, y_max):
        """Fix the Y-axis to the given range and redraw."""
        self.y_range = (y_min, y_max)
        self.plot_spectrum()

    def set_auto_range(self):
        """Let the Y-axis range be computed from the data again, and redraw."""
        self.y_range = None
        self.plot_spectrum()

    def set_sigma(self, val):
        """Set the GAMESS broadening width and redraw."""
        self.sigma = val
        self.plot_spectrum()

    @staticmethod
    def _checked(state):
        """Accept either a Qt check state or a plain bool."""
        return bool(state) and state != Qt.CheckState.Unchecked.value

    def set_sticks(self, state):
        """Toggle the stick markers and redraw."""
        self.show_sticks = self._checked(state)
        self.plot_spectrum()

    def set_gamess(self, state):
        """Toggle the broadened GAMESS curve and redraw."""
        self.show_gamess = self._checked(state)
        self.plot_spectrum()

    def set_markers(self, state):
        """Toggle the peak markers and redraw."""
        self.show_markers = self._checked(state)
        self.plot_spectrum()

    def save_png(self, path):
        """Save the current plot as a PNG."""
        self.canvas.figure.savefig(path, dpi=300, bbox_inches="tight")

    def save_csv(self, path):
        """Write the broadened curve, sampled over its visible range, to CSV."""
        # Filter valid data
        points = []
        for item in self.data_list:
            x = item.get(self.x_key, 0.0)
            y = item.get(self.y_key, 0.0)
            if abs(y) > 1e-12:
                points.append((x, y))

        if not points:
            return False

        xs = [p[0] for p in points]
        is_energy_axis = "cm" in self.x_unit.lower() or "freq" in self.x_unit.lower()

        # Determine Range for CSV
        if self.x_range:
            min_x, max_x = min(self.x_range), max(self.x_range)
        else:
            if self.broaden_in_energy and not is_energy_axis:
                span = max(xs) - min(xs) if xs else 100
                margin = max(50.0, span * 0.1)
            else:
                margin = self.sigma * 5  # A bit wider for CSV than plot default
            min_x = min(xs) - margin
            max_x = max(xs) + margin

        # 5000 points resolution for CSV
        display_x = np.linspace(min_x, max_x, 5000)
        curve_y = np.zeros_like(display_x)

        # Broadening Logic (Must match plot_spectrum exactly)
        norm_factor = 1.0
        if self.normalization_mode == "area":
            norm_factor = 1.0 / (np.sqrt(np.pi) * self.sigma)

        if self.broaden_in_energy and not is_energy_axis:
            # UV-Vis case: Sigma in cm-1, Display in nm
            all_pts_cm = [(1e7 / x0, y0) for x0, y0 in points if x0 > 1e-6]
            if all_pts_cm:
                cms = [p[0] for p in all_pts_cm]
                cm_min = min(cms) - max(self.sigma * 10, 500)
                cm_max = max(cms) + max(self.sigma * 10, 500)
                grid_cm = np.linspace(cm_min, cm_max, 10000)
                curve_cm = np.zeros_like(grid_cm)
                for x0, y0 in all_pts_cm:
                    term = np.exp(-(((grid_cm - x0) / self.sigma) ** 2))
                    curve_cm += y0 * term * norm_factor

                valid_mask = grid_cm > 1.0
                curve_y = np.interp(
                    display_x,
                    1e7 / grid_cm[valid_mask][::-1],
                    curve_cm[valid_mask][::-1],
                )
        else:
            # Standard case
            for x0, y0 in points:
                term = np.exp(-(((display_x - x0) / self.sigma) ** 2))
                curve_y += y0 * term * norm_factor

        # Apply scaling
        scaling = getattr(self, "scaling_factor", 1.0)
        curve_y *= scaling

        # Clean up denormal-sized values (avoid E-555 etc) by MAGNITUDE.
        # Comparing the signed value against a positive threshold zeroed every
        # negative point, which silently flattened the negative Cotton effects
        # out of an exported CD spectrum (the on-screen plot was unaffected).
        max_val = np.max(np.abs(curve_y)) if len(curve_y) > 0 else 0
        threshold = max(1e-12, max_val * 1e-15)
        curve_y[np.abs(curve_y) < threshold] = 0.0

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                x_label = self.x_unit if self.x_unit else "X"
                y_label = self.y_unit if self.y_unit else "Intensity"
                writer.writerow([x_label, y_label])
                for i in range(len(display_x)):
                    writer.writerow([display_x[i], curve_y[i]])
            return True
        except (OSError, IndexError, ValueError) as e:
            logging.warning("Error saving CSV: %s", e)
            return False

    def save_sticks_csv(self, path):
        """Write the raw (unbroadened) stick data points to CSV."""
        # Filter valid data
        stick_key = self.y_key_sticks or self.y_key
        stick_unit = self.y_unit_sticks if self.y_key_sticks else self.y_unit
        points = []
        for item in self.data_list:
            x = item.get(self.x_key, 0.0)
            y = item.get(stick_key, 0.0)
            if abs(y) > 1e-12:
                points.append((x, y))

        if not points:
            return False

        # Sort by X
        points.sort(key=lambda p: p[0])

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                # Header
                x_head = self.x_unit if self.x_unit else "X"
                y_head = stick_unit if stick_unit else "Y"
                writer.writerow([x_head, y_head])
                for x, y in points:
                    writer.writerow([x, y])
            return True
        except (OSError, IndexError, ValueError):
            return False

    def set_scaling(self, factor):
        """Apply an intensity scaling factor to the broadened curve and redraw."""
        self.scaling_factor = factor
        self.plot_spectrum()

    def set_dual_axis(self, enable):
        """Toggle a secondary Y-axis for the stick data and redraw."""
        self.use_dual_axis = enable
        # Clear twin axis if disabling
        if not enable and getattr(self, "ax2", None) is not None:
            try:
                self.ax2.remove()
                del self.ax2
            except (AttributeError, ValueError, NotImplementedError) as e:
                logging.debug(
                    "Spectrum widget: could not remove the secondary (twin) axis: %s", e
                )
        self.plot_spectrum()

    def plot_spectrum(self):
        """Redraw the full plot: broadened curve, sticks, markers and axis ranges."""
        self._is_plotting = True
        try:
            self.canvas.axes.clear()
            if getattr(self, "ax2", None) is not None:
                try:
                    self.ax2.clear()
                except (AttributeError, ValueError, NotImplementedError) as e:
                    logging.debug(
                        "Spectrum widget: could not clear the secondary (twin) axis: %s",
                        e,
                    )

            # Re-connect straight after the clear that dropped them: clear()
            # installs a fresh CallbackRegistry, and the "No Data" path below
            # returns before the end of this method, which used to leave the
            # axes with no listener at all — the toolbar's zoom then stopped
            # emitting range_changed and the range boxes silently desynced
            # until the next successful plot. _on_axes_changed no-ops while
            # _is_plotting is set, so connecting this early costs nothing.
            self.canvas.axes.callbacks.connect("xlim_changed", self._on_axes_changed)
            self.canvas.axes.callbacks.connect("ylim_changed", self._on_axes_changed)

            if getattr(self, "scaling_factor", None) is None:
                self.scaling_factor = 1.0
            if getattr(self, "use_dual_axis", None) is None:
                self.use_dual_axis = False

            # Filter valid data for GAMESS Curve
            gamess_points = []
            for item in self.data_list:
                x = item.get(self.x_key, 0.0)
                y = item.get(self.y_key, 0.0)
                if abs(y) > 1e-12:
                    gamess_points.append((x, y))

            # Filter valid data for Sticks
            stick_points = []
            target_stick_key = self.y_key_sticks if self.y_key_sticks else self.y_key
            for item in self.data_list:
                x = item.get(self.x_key, 0.0)
                y = item.get(target_stick_key, 0.0)
                if abs(y) > 1e-12:
                    stick_points.append((x, y))

            if not gamess_points and not stick_points:
                self.canvas.axes.text(
                    0.5,
                    0.5,
                    "No Data",
                    ha="center",
                    va="center",
                    transform=self.canvas.axes.transAxes,
                )
                self.canvas.draw_idle()
                return

            # Use GAMESS points for X range if available, else Stick points
            pts_for_range = gamess_points if gamess_points else stick_points

            xs = [p[0] for p in pts_for_range]
            # ys not needed here for range

            # Determine X Range (for plotting calculation)
            is_energy_axis = (
                "cm" in self.x_unit.lower() or "freq" in self.x_unit.lower()
            )

            if self.x_range:
                new_xmin, new_xmax = min(self.x_range), max(self.x_range)
            else:
                # Calculate margin based on broadening type and axis unit
                if self.broaden_in_energy and not is_energy_axis:
                    # Case: Plotting in nm, but Sigma is in cm-1 (e.g. 3000)
                    # We cannot use sigma directly for nm margin.
                    # Use a heuristic margin: 50 nm or 10% of data span
                    span = max(xs) - min(xs) if xs else 100
                    margin = max(50.0, span * 0.1)
                else:
                    # Standard case: Sigma matches axis unit
                    margin = self.sigma * 3

                new_xmin = min(xs) - margin
                new_xmax = max(xs) + margin
                if new_xmax - new_xmin < 1.0:
                    new_xmin -= 10
                    new_xmax += 10

            # Determine Plotting Range for GAMESS (Full data range + margins)
            # This ensures the curve remains visible when zooming out or clicking HOME
            data_min_x = min(xs)
            data_max_x = max(xs)

            # Recalculate plot margin for consistency
            if self.broaden_in_energy and not is_energy_axis:
                plot_margin = max(100.0, (data_max_x - data_min_x) * 0.2)
            else:
                plot_margin = max(self.sigma * 10, 500)

            plot_min_x = data_min_x - plot_margin
            plot_max_x = data_max_x + plot_margin

            # Use wavenumber grid if broadening in energy
            is_energy = is_energy_axis

            # Decide interpolation grid
            curve_y = np.array([])
            display_x = np.array([])

            if gamess_points:
                if self.broaden_in_energy and not is_energy:
                    # Broaden in cm-1, but display is in nm
                    # Convert data points to cm-1
                    all_pts_cm = []
                    for x0, y0 in gamess_points:
                        if x0 > 1e-6:
                            all_pts_cm.append((1e7 / x0, y0))

                    if all_pts_cm:
                        # Create wavenumber grid
                        cms = [p[0] for p in all_pts_cm]
                        cm_min_x = min(cms) - max(self.sigma * 10, 500)
                        cm_max_x = max(cms) + max(self.sigma * 10, 500)
                        grid_cm = np.linspace(cm_min_x, cm_max_x, 10000)
                        curve_y_cm = np.zeros_like(grid_cm)

                        # Broaden in cm-1
                        norm_factor = 1.0
                        if self.normalization_mode == "area":
                            norm_factor = 1.0 / (np.sqrt(np.pi) * self.sigma)

                        for x0, y0 in all_pts_cm:
                            term = np.exp(-(((grid_cm - x0) / self.sigma) ** 2))
                            curve_y_cm += y0 * term * norm_factor

                        # Interpolate back to nm display grid
                        display_x = np.linspace(plot_min_x, plot_max_x, 10000)
                        # Filter grid_cm to avoid divide by zero if 0 in grid_cm?
                        # (handled by min_x range usually)
                        valid_grid = grid_cm[grid_cm > 1.0]
                        valid_curve = curve_y_cm[grid_cm > 1.0]
                        curve_y = np.interp(
                            display_x, 1e7 / valid_grid[::-1], valid_curve[::-1]
                        )
                else:
                    # Standard broadening in current axis space
                    display_x = np.linspace(plot_min_x, plot_max_x, 10000)
                    curve_y = np.zeros_like(display_x)

                    norm_factor = 1.0
                    if self.normalization_mode == "area":
                        norm_factor = 1.0 / (np.sqrt(np.pi) * self.sigma)

                    if self.show_gamess:
                        for x0, y0 in gamess_points:
                            term = np.exp(-(((display_x - x0) / self.sigma) ** 2))
                            curve_y += y0 * term * norm_factor

            # APPLY SCALING
            curve_y *= self.scaling_factor

            # Plot GAMESS on Primary Axis
            if self.show_gamess and len(display_x) > 0:
                self.canvas.axes.plot(
                    display_x, curve_y, "r-", linewidth=2, label="Spectrum"
                )

            # Determine Axes for Sticks
            ax_sticks = self.canvas.axes
            if self.use_dual_axis:
                if getattr(self, "ax2", None) is None:
                    self.ax2 = self.canvas.axes.twinx()
                ax_sticks = self.ax2
                ax_sticks.set_ylabel(self.y_unit_sticks)

                # Ensure Primary is Left, Secondary is Right (Standard)
                self.canvas.axes.yaxis.tick_left()
                self.canvas.axes.yaxis.set_label_position("left")
                self.ax2.yaxis.tick_right()
                self.ax2.yaxis.set_label_position("right")

            elif getattr(self, "ax2", None) is not None:
                # Reset Primary to Left if no dual axis
                self.canvas.axes.yaxis.tick_left()
                self.canvas.axes.yaxis.set_label_position("left")
                self.ax2.axis("off")

            # Plot Sticks
            if self.show_sticks and stick_points:
                st_xs = [p[0] for p in stick_points]
                st_ys = [p[1] for p in stick_points]
                ax_sticks.vlines(
                    st_xs,
                    0,
                    st_ys,
                    colors="black",
                    alpha=0.6,
                    linewidth=1.5,
                    label="Transitions",
                )

            # Plot Peak Markers at the top (Red inverted triangles)
            # Use data list to get ALL transitions including zero intensity ones
            if self.show_markers:
                all_xs = [item.get(self.x_key, 0.0) for item in self.data_list]
                if all_xs:
                    if self.selected_item:
                        sel_x = self.selected_item.get(self.x_key, None)
                        non_sel_xs = [
                            x for x in all_xs if sel_x is None or abs(x - sel_x) > 1e-7
                        ]
                    else:
                        sel_x = None
                        non_sel_xs = all_xs

                    # Common Y positions (axis transform sets them to top/bottom)
                    marker_y = 0.03 if self.invert_y else 0.97
                    marker_shape = "^" if self.invert_y else "v"

                    if non_sel_xs:
                        self.canvas.axes.scatter(
                            non_sel_xs,
                            [marker_y] * len(non_sel_xs),
                            transform=self.canvas.axes.get_xaxis_transform(),
                            marker=marker_shape,
                            color="red",
                            alpha=0.3,
                            s=50,
                            clip_on=True,
                            zorder=10,
                        )

                    if sel_x is not None:
                        self.canvas.axes.scatter(
                            [sel_x],
                            [marker_y],
                            transform=self.canvas.axes.get_xaxis_transform(),
                            marker=marker_shape,
                            color="blue",
                            alpha=0.8,
                            s=60,
                            clip_on=True,
                            zorder=11,
                        )

            # Highlight Selected Item
            if self.selected_item:
                sel_x = self.selected_item.get(self.x_key, None)

                # Use stick key for highlight height
                t_key = self.y_key_sticks if self.y_key_sticks else self.y_key
                sel_y = self.selected_item.get(t_key, 0.0)

                if sel_x is not None:
                    # Highlight vertical line (even for zero y)
                    ax_sticks.vlines(
                        [sel_x], 0, [sel_y], colors="blue", linewidth=2.5, zorder=5
                    )
                    self.canvas.axes.axvline(
                        sel_x, color="blue", linestyle="--", alpha=0.5, zorder=4
                    )

            # Determine Y scale for Primary Axis (Curve)
            if self.y_range:
                new_ymin, new_ymax = min(self.y_range), max(self.y_range)
            else:
                g_max = (
                    np.max(curve_y) if self.show_gamess and len(curve_y) > 0 else 1.0
                )
                g_min = (
                    np.min(curve_y) if self.show_gamess and len(curve_y) > 0 else 0.0
                )

                # Only consider sticks for primary scale if NOT dual axis
                if not self.use_dual_axis and self.show_sticks and stick_points:
                    st_ys = [p[1] for p in stick_points]
                    scale_max_sticks = max(st_ys) if st_ys else 1.0
                    g_max = max(g_max, scale_max_sticks)
                    g_min = min(g_min, min(st_ys))

                if g_min >= 0:
                    new_ymin, new_ymax = 0, g_max * 1.1
                else:
                    margin = (g_max - g_min) * 0.1
                    new_ymin, new_ymax = g_min - margin, g_max + margin

            # === FINAL AXIS AND CALLBACK CONFIGURATION ===
            # We do this at the very end to override any internal auto-scaling from plotting calls

            if self.invert_x:
                self.canvas.axes.set_xlim(new_xmax, new_xmin)
            else:
                self.canvas.axes.set_xlim(new_xmin, new_xmax)

            if self.invert_y:
                self.canvas.axes.set_ylim(new_ymax, new_ymin)
            else:
                self.canvas.axes.set_ylim(new_ymin, new_ymax)

            # Sync Dual Axis Limits to align Zeros
            if (
                self.use_dual_axis
                and getattr(self, "ax2", None) is not None
                and stick_points
            ):
                # Get stick data range
                st_ys = [p[1] for p in stick_points]
                if not st_ys:
                    s_min, s_max = 0.0, 1.0
                else:
                    s_min, s_max = min(st_ys), max(st_ys)

                # Primary Axis Range (Curve)
                p_min, p_max = new_ymin, new_ymax

                # --- Case 1: Absorption (0 at bottom) ---
                if p_min >= 0 and p_max > 0:
                    ax2_min = 0
                    # Primary scale ratio: max / (max - min) = 1 (since min=0)
                    # We want stick max to be at same relative position roughly
                    # Simple scaling: map 0->0 and max->max*constant
                    # Let's align the "top" of data.
                    # Curve data max is approx p_max / 1.1
                    # Stick data max is s_max
                    # So ax2_max should be s_max * 1.1
                    ax2_max = s_max * 1.1 if s_max > 1e-12 else 1.0
                    self.ax2.set_ylim(ax2_min, ax2_max)

                # --- Case 2: CD / General (Zero crossing) ---
                else:
                    # We need to align the y=0 lines of both axes.
                    # Primary axis zero fraction from bottom:
                    p_range = p_max - p_min
                    if p_range == 0:
                        p_range = 1.0
                    zero_frac = (0 - p_min) / p_range

                    # If zero is outside the view, just standard fit
                    if zero_frac <= 0.05 or zero_frac >= 0.95:
                        margin = (s_max - s_min) * 0.1
                        if margin == 0:
                            margin = 0.1
                        self.ax2.set_ylim(s_min - margin, s_max + margin)
                    else:
                        # We want 0 on ax2 to be at 'zero_frac' height.
                        # ax2_min = -H * zero_frac
                        # ax2_max = H * (1 - zero_frac)
                        # We need H such that [s_min, s_max] fits inside [ax2_min, ax2_max]

                        # Conditions:
                        # 1) ax2_max >= s_max  =>  H * (1-z) >= s_max  => H >= s_max / (1-z)
                        # 2) ax2_min <= s_min  => -H * z     <= s_min  => H * z >= -s_min => H >= -s_min / z

                        req_pos = max(0.0, s_max)
                        req_neg = max(0.0, -s_min)

                        h1 = req_pos / (1 - zero_frac)
                        h2 = req_neg / zero_frac

                        H = max(h1, h2) * 1.1  # 10% margin
                        if H == 0:
                            H = 1.0

                        self.ax2.set_ylim(-H * zero_frac, H * (1 - zero_frac))

            # Set axis properties
            self.canvas.axes.set_xlabel(self.x_unit)
            self.canvas.axes.set_ylabel(self.y_unit)
            self.canvas.axes.grid(True, alpha=0.3)

            self._initial_plot_done = True

            # Constrained layout handles padding automatically.

            self.canvas.draw_idle()

            # Emit range changed to sync UI
            xlim = self.canvas.axes.get_xlim()
            ylim = self.canvas.axes.get_ylim()
            self.range_changed.emit(min(xlim), max(xlim), min(ylim), max(ylim), False)
        finally:
            self._is_plotting = False

    def update(self):
        """Override to trigger plot update"""
        self.plot_spectrum()

    def on_click(self, event):
        """Select the nearest data point to a click, or clear on a double-click/miss."""
        valid_axes = [self.canvas.axes]
        if getattr(self, "ax2", None) is not None:
            valid_axes.append(self.ax2)

        if event.inaxes not in valid_axes:
            return

        # Only allow left-click
        if getattr(event, "button", None) != 1:
            return

        # Double-click to reset zoom and selection
        if event.dblclick:
            self.selected_item = None
            self.clicked.emit(None)
            if "freq" in self.x_unit.lower() or "cm" in self.x_unit.lower():
                self.set_x_range(400, 4000)
            else:
                self.set_auto_x_range()
            self.set_auto_range()
            return

        if not self.data_list:
            return

        click_x = event.xdata
        if click_x is None:
            return

        # Relative tolerance: 3% of current view range (increased for easier selection)
        xlim = self.canvas.axes.get_xlim()
        x_range = abs(xlim[1] - xlim[0])
        tolerance = x_range * 0.03

        # Find nearest point
        best_item = None
        min_dist = float("inf")

        for item in self.data_list:
            # No intensity cutoff: dark states must stay selectable.
            x = item.get(self.x_key, 0.0)

            dist = abs(x - click_x)
            if dist < min_dist:
                min_dist = dist
                best_item = item

        if best_item and min_dist <= tolerance:
            self.selected_item = best_item
            self.plot_spectrum()
            self.clicked.emit(best_item)
        else:
            # Clicked on empty space: clear selection
            self.selected_item = None
            self.plot_spectrum()
            self.clicked.emit(None)
