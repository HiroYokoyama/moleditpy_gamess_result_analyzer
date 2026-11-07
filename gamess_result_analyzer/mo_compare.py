"""Side-by-side comparison of up to four molecular orbitals.

Opened from the MO dialog. Each slot owns its own colours, isovalue, opacity
and style, and renders into its own namespaced pair of actors so all four
isosurfaces coexist in the host's 3D view.
"""

import logging
import os

from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from PySide6.QtCore import Qt

try:
    from .vis import CubeVisualizer
except ImportError:
    try:
        from vis import CubeVisualizer
    except ImportError:
        CubeVisualizer = None

try:
    from .settings import load_section, save_section
except ImportError:
    from settings import load_section, save_section


SLOT_COUNT = 4

# Slot 1 is overwritten with the MO dialog's own colours at build time, so the
# comparison always opens showing the orbital the user was already looking at
# in the colours they chose there.
DEFAULT_COLORS = [
    ("#ff0000", "#0000ff"),
    ("#ff8c00", "#008b8b"),
    ("#9400d3", "#7fbf00"),
    ("#ff1493", "#00bfff"),
]

STYLES = ["Surface", "Wireframe", "Points"]

# What each slot opens on, in this order. Also what an unassigned slot shows,
# so a slot that is merely switched off still names a useful orbital rather
# than whichever one happens to sit at the top of the table.
DEFAULT_TAGS = ["HOMO", "LUMO", "HOMO-1", "LUMO+1"]


def contrast_text(hex_c):
    """'black' or 'white', whichever stays readable on *hex_c*."""
    h = str(hex_c).lstrip("#")
    if len(h) != 6:
        return "black"
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return "black"
    return "black" if (r * 299 + g * 587 + b * 114) / 1000 > 128 else "white"


class MOSlot:
    """One orbital row: its widgets and the values they hold."""

    def __init__(self, index, orbitals, colors, parent_dlg):
        self.index = index
        self.prefix = f"mo_cmp{index}"
        self.vis = None

        self.box = QGroupBox(f"Orbital {index + 1}")
        outer = QVBoxLayout(self.box)

        row1 = QHBoxLayout()
        self.check_on = QCheckBox("Show")
        row1.addWidget(self.check_on)

        self.combo_mo = QComboBox()
        for label, key, display_id, _tag in orbitals:
            self.combo_mo.addItem(label, (key, display_id))
        row1.addWidget(self.combo_mo)
        outer.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_p = QPushButton("Pos (+)")
        self.btn_n = QPushButton("Neg (-)")
        self.btn_p.clicked.connect(lambda: parent_dlg.pick_slot_color(self, "p"))
        self.btn_n.clicked.connect(lambda: parent_dlg.pick_slot_color(self, "n"))
        row2.addWidget(self.btn_p)
        row2.addWidget(self.btn_n)
        outer.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Iso:"))
        # pylint: disable=duplicate-code  # isovalue spinbox setup mirrors mo_analysis; unrelated dialogs
        self.spin_iso = QDoubleSpinBox()
        self.spin_iso.setRange(0.001, 1.0)
        self.spin_iso.setSingleStep(0.005)
        self.spin_iso.setDecimals(3)
        self.spin_iso.setValue(0.02)
        # pylint: enable=duplicate-code
        row3.addWidget(self.spin_iso)

        row3.addWidget(QLabel("Opacity:"))
        self.spin_opacity = QDoubleSpinBox()
        self.spin_opacity.setRange(0.0, 1.0)
        self.spin_opacity.setSingleStep(0.1)
        self.spin_opacity.setValue(0.5)
        row3.addWidget(self.spin_opacity)
        outer.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Style:"))
        self.combo_style = QComboBox()
        self.combo_style.addItems(STYLES)
        row4.addWidget(self.combo_style)
        self.check_smooth = QCheckBox("Smooth")
        self.check_smooth.setChecked(True)
        row4.addWidget(self.check_smooth)
        outer.addLayout(row4)

        self.set_color("p", colors[0])
        self.set_color("n", colors[1])

    def set_color(self, which, hex_c):
        """Paint the +/- lobe button with hex_c, choosing readable text contrast."""
        btn = self.btn_p if which == "p" else self.btn_n
        btn.setStyleSheet(
            f"background-color: {hex_c}; color: {contrast_text(hex_c)}; "
            "font-weight: bold;"
        )

    def color(self, which):
        """Read the +/- lobe's current hex color from its button's stylesheet."""
        btn = self.btn_p if which == "p" else self.btn_n
        style = btn.styleSheet()
        if "background-color:" in style:
            return style.split("background-color:")[1].split(";")[0].strip()
        return "#ff0000" if which == "p" else "#0000ff"

    def is_on(self):
        """Whether this slot's Show checkbox is checked."""
        return self.check_on.isChecked()

    def selection(self):
        """(access key, display id) of the chosen orbital, or (None, None)."""
        data = self.combo_mo.currentData()
        if isinstance(data, (tuple, list)) and len(data) == 2:
            return data[0], data[1]
        return None, None

    def to_settings(self):
        """Appearance only. Which orbital a slot holds, and whether it is on,
        follow the loaded file's selection and are not persisted."""
        return {
            "color_p": self.color("p"),
            "color_n": self.color("n"),
            "iso": self.spin_iso.value(),
            "opacity": self.spin_opacity.value(),
            "style": self.combo_style.currentText(),
            "smooth_shading": bool(self.check_smooth.isChecked()),
        }

    def from_settings(self, data):
        """Restore appearance from *data*; unknown or malformed keys are left
        at whatever the slot was seeded with."""
        if not isinstance(data, dict):
            return
        for which, key in (("p", "color_p"), ("n", "color_n")):
            value = data.get(key)
            if isinstance(value, str) and value:
                self.set_color(which, value)
        for widget, key in ((self.spin_iso, "iso"), (self.spin_opacity, "opacity")):
            value = data.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                widget.setValue(float(value))
        style = data.get("style")
        if isinstance(style, str):
            idx = self.combo_style.findText(style)
            if idx >= 0:
                self.combo_style.setCurrentIndex(idx)
        if "smooth_shading" in data:
            self.check_smooth.setChecked(bool(data.get("smooth_shading")))


class MOCompareDialog(QDialog):
    """Dialog rendering up to four orbitals' isosurfaces side by side in 3D."""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent_dlg = parent
        self.mw = getattr(parent, "mw", None)
        self.setWindowTitle("Compare Molecular Orbitals")
        self.resize(760, 520)
        self.slots = []
        # Guards the live-redraw slots while the dialog sets widgets itself.
        self._ready = False
        self._is_closing = False
        self._suspend = 0
        self.opened_from_selection = False
        self.setup_ui()

    # -- construction ------------------------------------------------------

    def collect_orbitals(self):
        """(label, access key, display id, frontier tag) per MO table row."""
        out = []
        tree = getattr(self.parent_dlg, "tree", None)
        if tree is None:
            return out
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item is None:
                continue
            display_id = item.text(0)
            tag = item.text(1)
            label = f"{display_id}  {tag}".strip() if tag else display_id
            key = item.data(0, Qt.ItemDataRole.UserRole)
            if key is None:
                continue
            out.append((label, key, display_id, tag))
        return out

    def preselected_keys(self):
        """Orbitals highlighted in the MO table when this window opened."""
        keys = []
        tree = getattr(self.parent_dlg, "tree", None)
        if tree is None:
            return keys
        try:
            selected = tree.selectedItems() or []
        except (AttributeError, RuntimeError) as e:
            logging.warning(
                "MO compare: could not read the MO table's current selection: %s", e
            )
            return keys
        for item in selected:
            key = item.data(0, Qt.ItemDataRole.UserRole)
            if key is not None and key not in keys:
                keys.append(key)
        return keys[:SLOT_COUNT]

    def default_keys(self):
        """The frontier orbitals, in DEFAULT_TAGS order.

        Unrestricted output tags both spin channels, so a tag can repeat; the
        first row wins, which is the channel the table lists first.
        """
        by_tag = {}
        for _label, key, _display_id, tag in self.orbitals:
            if tag and tag not in by_tag:
                by_tag[tag] = key
        return [by_tag[t] for t in DEFAULT_TAGS if t in by_tag]

    def apply_selection(self, keys=None):
        """Fill the slots with *keys*, or with the table selection, or with
        the frontier orbitals — whichever is available, in that order."""
        if keys is None:
            preselected = self.preselected_keys()
            self.opened_from_selection = bool(preselected)
            keys = preselected or self.default_keys()
        else:
            self.opened_from_selection = True
        positions = {key: i for i, (_l, key, _d, _t) in enumerate(self.orbitals)}
        # A slot with nothing assigned still has to show something. Left alone
        # its combo sits on row 0, the highest-numbered virtual orbital, which
        # is never what someone wants to compare against.
        defaults = self.default_keys()

        # One redraw at the end, not one per widget touched.
        self._suspend += 1
        try:
            for i, slot in enumerate(self.slots):
                key = keys[i] if i < len(keys) else None
                shown = (
                    key
                    if key is not None
                    else (defaults[i] if i < len(defaults) else None)
                )
                pos = positions.get(shown) if shown is not None else None
                if pos is not None:
                    slot.combo_mo.setCurrentIndex(pos)
                slot.check_on.setChecked(key is not None and pos is not None)
        finally:
            self._suspend -= 1

        if self._ready:
            self.on_open()

    def missing_keys(self):
        """Orbitals that are switched on but have no cube on disk."""
        out = []
        for slot in self.slots:
            if not slot.is_on():
                continue
            key, display_id = slot.selection()
            if key is None:
                continue
            path = self._cube_path(display_id)
            if (not path or not os.path.exists(path)) and key not in out:
                out.append(key)
        return out

    def refresh_update_button(self):
        """The button is the only thing the user must press, so it has to say
        so: enabled exactly when a shown orbital still needs computing."""
        missing = self.missing_keys()
        self.btn_update.setEnabled(bool(missing))
        if missing:
            self.btn_update.setText(f"Generate {len(missing)} Missing Cube(s)")
            self.btn_update.setToolTip(
                "These orbitals have no cube yet. Press to compute them."
            )
            self.btn_update.setStyleSheet(
                "font-weight: bold; background-color: #ffe0b2;"
            )
        else:
            self.btn_update.setText("All Cubes Ready")
            self.btn_update.setToolTip(
                "Every shown orbital is drawn; other changes apply instantly."
            )
            self.btn_update.setStyleSheet("")

    def setup_ui(self):
        """Build the four orbital-slot boxes and the update/close controls."""
        layout = QVBoxLayout(self)

        info = QLabel(
            "Pick up to four orbitals. Colour, isovalue, opacity and style "
            "apply instantly; only a missing cube needs the button."
        )
        layout.addWidget(info)

        self.orbitals = self.collect_orbitals()
        grid = QGridLayout()
        for i in range(SLOT_COUNT):
            colors = list(DEFAULT_COLORS[i])
            if i == 0:
                colors = [self._parent_color("p"), self._parent_color("n")]
            slot = MOSlot(i, self.orbitals, colors, self)
            if i == 0:
                self._seed_first_slot(slot)
            grid.addWidget(slot.box, i // 2, i % 2)
            self.slots.append(slot)
        layout.addLayout(grid)

        # Saved appearance wins over the seeded defaults; with nothing saved,
        # slot 1 keeps the colours it just inherited from the MO dialog.
        self.load_settings()
        # Which orbitals go where depends on the loaded file, not on saved
        # settings, so this runs last.
        self.apply_selection()

        btns = QHBoxLayout()
        self.btn_update = QPushButton("Generate Missing Cubes")
        self.btn_update.clicked.connect(self.update_view)
        btns.addWidget(self.btn_update)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setToolTip(
            "Redraw the orbitals. Anything that rebuilds the 3D scene — "
            "loading a structure, changing the display style — removes them, "
            "and this puts them back."
        )
        self.btn_refresh.clicked.connect(self.refresh_view)
        btns.addWidget(self.btn_refresh)

        self.btn_sync_iso = QPushButton("Sync Iso from Orbital 1")
        self.btn_sync_iso.setToolTip(
            "Copy Orbital 1's isovalue to the other slots, so the lobes are "
            "drawn at the same contour level."
        )
        self.btn_sync_iso.clicked.connect(self.sync_isovalue)
        btns.addWidget(self.btn_sync_iso)

        btn_clear = QPushButton("Clear All")
        btn_clear.clicked.connect(self.clear_all)
        btns.addWidget(btn_clear)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        btns.addWidget(btn_close)
        layout.addLayout(btns)

        self.lbl_status = QLabel("")
        layout.addWidget(self.lbl_status)

        # Wired only now: everything above set widget values programmatically,
        # and a half-built dialog must not try to render.
        for slot in self.slots:
            slot.check_on.toggled.connect(self.on_live_change)
            slot.combo_mo.currentIndexChanged.connect(self.on_live_change)
            slot.spin_iso.valueChanged.connect(self.on_live_change)
            slot.spin_opacity.valueChanged.connect(self.on_live_change)
            slot.combo_style.currentTextChanged.connect(self.on_live_change)
            slot.check_smooth.toggled.connect(self.on_live_change)

        self._ready = True
        self.on_open()

    def on_open(self):
        """Draw what is already on disk.

        Cubes cost seconds to compute, so they are only generated up front
        when the user asked for these specific orbitals by selecting them in
        the table. Otherwise the frontier defaults just show whatever is
        cached and the button offers the rest.
        """
        if self.opened_from_selection and self.missing_keys():
            self.update_view()
        else:
            self.render_all()

    def refresh_view(self):
        """Redraw the orbitals on demand.

        Anything that rebuilds the host's scene drops the actors, and this
        dialog gets no say in when that happens, so there has to be a way to
        ask for them back without touching a setting. Runs even while redraws
        are suspended — this is an explicit request, not a side effect.
        """
        suspended, self._suspend = self._suspend, 0
        try:
            self.render_all()
        finally:
            self._suspend = suspended

    def on_live_change(self, *_args):
        """A cheap setting changed: redraw straight away.

        Colour, isovalue, opacity, style and visibility all re-contour a grid
        that is already in memory, so there is nothing to wait for. Switching
        to an orbital with no cube leaves that slot blank and lights up the
        button instead — render_all refreshes it.
        """
        if not self._ready:
            return
        self.render_all()

    # -- persistence -------------------------------------------------------

    def settings_path(self):
        """Shared with the MO dialog: one settings.json beside the package."""
        return os.path.join(os.path.dirname(__file__), "settings.json")

    def load_settings(self):
        """Restore each slot's saved appearance from settings.json."""
        path = self.settings_path()
        if not os.path.exists(path):
            return
        saved = load_section(path, "mo_compare")
        slots = saved.get("slots") if isinstance(saved, dict) else None
        if not isinstance(slots, list):
            return
        for slot, data in zip(self.slots, slots):
            slot.from_settings(data)

    def save_settings(self):
        """Persist each slot's appearance to settings.json."""
        path = self.settings_path()
        save_section(
            path, "mo_compare", {"slots": [slot.to_settings() for slot in self.slots]}
        )

    def _parent_color(self, which):
        try:
            return self.parent_dlg.get_color_hex(which)
        except (AttributeError, RuntimeError) as e:
            logging.warning(
                "MO compare: could not read the parent dialog's %s color: %s", which, e
            )
            return DEFAULT_COLORS[0][0 if which == "p" else 1]

    def _seed_first_slot(self, slot):
        """Slot 1 renders with the MO dialog's own settings."""
        parent = self.parent_dlg
        try:
            slot.spin_iso.setValue(parent.spin_iso.value())
            slot.spin_opacity.setValue(parent.spin_opacity.value())
            idx = slot.combo_style.findText(parent.combo_style.currentText())
            if idx >= 0:
                slot.combo_style.setCurrentIndex(idx)
            slot.check_smooth.setChecked(parent.check_smooth.isChecked())
        except (AttributeError, RuntimeError, TypeError) as e:
            logging.warning(
                "MO compare: could not copy the MO dialog's render settings into slot 1: %s",
                e,
            )

    # -- interaction -------------------------------------------------------

    def pick_slot_color(self, slot, which):
        """Open a color picker for a slot's +/- lobe and re-render on acceptance."""
        from PySide6.QtGui import QColor

        col = QColorDialog.getColor(QColor(slot.color(which)), self, "Select Color")
        if col.isValid():
            slot.set_color(which, col.name())
            self.render_all()

    def sync_isovalue(self):
        """Give every slot Orbital 1's isovalue.

        Orbitals only compare meaningfully at one contour level; the other
        per-slot settings stay independent so the lobes remain tellable apart.
        """
        if not self.slots:
            return
        value = self.slots[0].spin_iso.value()
        self._suspend += 1
        try:
            for slot in self.slots[1:]:
                slot.spin_iso.setValue(value)
        finally:
            self._suspend -= 1
        self.render_all()

    def update_view(self):
        """Generate whatever cube files are missing, then draw everything."""
        missing = self.missing_keys()
        if not missing:
            self.render_all()
            return

        self.lbl_status.setText(f"Generating {len(missing)} cube(s)...")
        # A second press while the queue runs would start an overlapping
        # batch; refresh_update_button turns it back on afterwards.
        self.btn_update.setEnabled(False)
        try:
            self.parent_dlg.generate_cubes(missing, on_done=self.render_all)
        except (AttributeError, RuntimeError) as e:
            logging.warning("Cube generation unavailable: %s", e)
            QMessageBox.warning(
                self, "Error", f"Could not generate the missing cubes:\n{e}"
            )
            self.lbl_status.setText("")
            self.refresh_update_button()

    def _cube_path(self, display_id):
        """Resolve a display id to its cube file path via the parent MO dialog."""
        try:
            return self.parent_dlg.get_cube_path(display_id)
        except (AttributeError, RuntimeError) as e:
            logging.warning(
                "MO compare: could not resolve the cube path for %s: %s", display_id, e
            )
            return None

    def render_all(self):
        """Draw every enabled slot's isosurfaces (skipping any missing cube)."""
        if getattr(self, "_is_closing", False) is True or self._suspend:
            return
        if not CubeVisualizer:
            QMessageBox.warning(
                self,
                "Visualizer Error",
                "CubeVisualizer module not loaded.\nCheck if 'pyvista' is installed.",
            )
            self.refresh_update_button()
            return
        mw = self.mw
        if not mw:
            self.refresh_update_button()
            return

        # The MO dialog's own single-orbital actors would sit on top of the
        # comparison and double-draw slot 1.
        self._remove_actors("mo_iso")

        shown, missing = 0, 0
        for slot in self.slots:
            if not slot.is_on():
                self._remove_actors(slot.prefix)
                continue
            _key, display_id = slot.selection()
            path = self._cube_path(display_id)
            if not path or not os.path.exists(path):
                self._remove_actors(slot.prefix)
                missing += 1
                continue

            vis = CubeVisualizer(mw)
            if not vis.load_file(path):
                self._remove_actors(slot.prefix)
                continue
            vis.show_iso(
                slot.spin_iso.value(),
                opacity=slot.spin_opacity.value(),
                color_p=slot.color("p"),
                color_n=slot.color("n"),
                style=slot.combo_style.currentText().lower(),
                smooth_shading=slot.check_smooth.isChecked(),
                name_prefix=slot.prefix,
            )
            slot.vis = vis
            shown += 1

        try:
            mw.plotter.render()
        except (AttributeError, RuntimeError) as e:
            logging.debug(
                "MO compare: could not render the plotter after showing orbitals: %s", e
            )

        msg = f"Showing {shown} orbital(s)."
        if missing:
            msg += f" {missing} cube(s) still missing."
        self.lbl_status.setText(msg)
        self.save_settings()
        # Last, so the button always reflects what is actually on screen.
        self.refresh_update_button()

    def clear_all(self):
        """Turn off every slot and remove all their 3D actors."""
        self._suspend += 1
        try:
            for slot in self.slots:
                slot.check_on.setChecked(False)
                self._remove_actors(slot.prefix)
        finally:
            self._suspend -= 1
        try:
            if self.mw:
                self.mw.plotter.render()
        except (AttributeError, RuntimeError) as e:
            logging.debug(
                "MO compare: could not render the plotter after clearing all slots: %s",
                e,
            )
        self.lbl_status.setText("")
        self.refresh_update_button()

    def _remove_actors(self, prefix):
        try:
            plotter = self.mw.plotter if self.mw else None
        except (AttributeError, RuntimeError) as e:
            logging.debug(
                "MO compare: could not access the 3D plotter to remove actors for %s: %s",
                prefix,
                e,
            )
            return
        if plotter is None:
            return
        for suffix in ("_p", "_n"):
            try:
                plotter.remove_actor(f"{prefix}{suffix}")
            except (AttributeError, RuntimeError, KeyError) as e:
                logging.debug(
                    "MO compare: could not remove actor %s%s: %s", prefix, suffix, e
                )

    # -- teardown ----------------------------------------------------------

    def reject(self):
        """Route Esc through close() so closeEvent cleanup always runs."""
        # Esc must run closeEvent cleanup (QDialog.reject only hides)
        self.close()

    def closeEvent(self, event):
        """Save settings and remove every slot's 3D actors before closing."""
        self._is_closing = True
        self.save_settings()
        for slot in self.slots:
            self._remove_actors(slot.prefix)
        try:
            if self.mw:
                self.mw.plotter.render()
        except (AttributeError, RuntimeError) as e:
            logging.debug("MO compare: could not render the plotter on close: %s", e)

        # Without this the MO dialog keeps a dead reference and reopening
        # raises the destroyed window instead of building a new one.
        try:
            self.parent_dlg.on_compare_closed()
        except (AttributeError, RuntimeError) as e:
            logging.warning(
                "MO compare: could not notify the parent MO dialog that this window closed: %s",
                e,
            )

        # accept() not super().closeEvent(): QDialog.closeEvent calls reject(),
        # which is routed back through close() and would recurse.
        event.accept()
