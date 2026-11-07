"""
tests/gui_harness.py
Isolated headless-Qt loader for instantiation-style coverage tests.

Every other test module in this suite installs its own partial PySide6 stub into
the shared ``sys.modules`` and relies on those exact objects at run time. So we
must NOT mutate the shared stubs in a way that outlives our own imports.

``load_isolated(modname)`` therefore:

  1. temporarily installs a complete set of real, subclassable Qt base classes
     (plus a matplotlib Qt-agg backend and a functional QColor) onto the shared
     stub modules, remembering whatever was there before;
  2. loads ``gamess_result_analyzer/<modname>.py`` under a private throwaway
     package whose ``__path__`` points at the real source dir, so the module's
     ``from .sibling import x`` relative imports auto-resolve to fresh copies of
     the siblings (also bound to our complete classes) without executing the
     heavy package ``__init__``;
  3. restores the shared stub modules to exactly what they were.

The loaded module keeps the classes it captured at import time, so it works at
run time while the shared stubs the other tests depend on are left untouched.
"""

import os
import re
import sys
import types
import importlib
import importlib.util
from unittest.mock import MagicMock

_PKG_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "gamess_result_analyzer")
)


class _PermissiveMeta(type):
    def __getattr__(cls, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return MagicMock()


_NOOP_METHODS = (
    "closeEvent",
    "showEvent",
    "resizeEvent",
    "keyPressEvent",
    "paintEvent",
    "moveEvent",
    "accept",
    "reject",
    "done",
    "close",
    "setWindowTitle",
    "resize",
    "setLayout",
    "setAcceptDrops",
)


def _noop(self, *a, **k):
    return None


# Attribute names the widget stubs must report as genuinely absent.
#
# Real Qt widgets raise AttributeError for a name that was never assigned, and
# the source relies on that: several modules lazily initialise state with
# ``getattr(self, "x", None) is None`` guards. The permissive ``__getattr__``
# below would hand those guards a MagicMock and the initialisation would be
# skipped, so names registered here raise AttributeError instead.
ABSENT_ATTRS = {
    "scaling_factor",
    "context",  # utils.notify() needs this absent when unset, not a MagicMock
    # utils.notify()'s owner-chain hops: a dialog that doesn't set one of
    # these must see it as absent, not get a phantom MagicMock that hijacks
    # the walk before it reaches a real self.parent().
    "parent_dlg",
    "freq_dialog",
    "parent_dialog",
}


def _make_widget(name):
    ns = {m: _noop for m in _NOOP_METHODS}

    class DialogCode:
        Accepted = 1
        Rejected = 0

    ns["DialogCode"] = DialogCode

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, attr):
        if attr.startswith("_") or attr in ABSENT_ATTRS:
            raise AttributeError(attr)
        return MagicMock()

    ns["__init__"] = __init__
    ns["__getattr__"] = __getattr__
    return _PermissiveMeta(name, (object,), ns)


# ---------------------------------------------------------------------------
# Stateful input widgets.
#
# The plain stubs above answer every call with a fresh MagicMock, so a dialog
# that reads back what it wrote (``spin.setValue(3.0)`` then ``spin.value()``)
# gets a MagicMock and any arithmetic on it explodes. These stand-ins keep real
# state for the accessors the source actually round-trips, while still
# tolerating arbitrary cosmetic calls (setFixedWidth, setSuffix, ...).
#
# Signals record their connections but do NOT fire on programmatic setters:
# widgets are wired up mid-``__init__``, so auto-emitting would run slots
# against half-built dialogs. Tests invoke the slot directly, or call
# ``signal.emit(...)`` when they want the wiring exercised.
# ---------------------------------------------------------------------------


class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def disconnect(self, slot=None):
        if slot is None:
            self._slots.clear()
        elif slot in self._slots:
            self._slots.remove(slot)

    def emit(self, *a):
        for slot in list(self._slots):
            slot(*a)


class _StatefulBase(metaclass=_PermissiveMeta):
    """Base for the stateful widgets: unknown attributes stay permissive.

    Properties every widget round-trips (tooltip, stylesheet, enabled and
    visible state) live here rather than on individual stubs — the source reads
    them back to decide what it rendered, so a MagicMock that forgets loses the
    assertion. Values are stashed in __dict__ so subclasses need no __init__
    cooperation.
    """

    def __getattr__(self, attr):
        if attr.startswith("_") or attr in ABSENT_ATTRS:
            raise AttributeError(attr)
        return MagicMock()

    def setToolTip(self, text):
        self.__dict__["_tooltip"] = "" if text is None else str(text)

    def toolTip(self):
        return self.__dict__.get("_tooltip", "")

    def setStyleSheet(self, sheet):
        self.__dict__["_stylesheet"] = "" if sheet is None else str(sheet)

    def styleSheet(self):
        return self.__dict__.get("_stylesheet", "")

    def setEnabled(self, value):
        self.__dict__["_enabled"] = bool(value)

    def isEnabled(self):
        return self.__dict__.get("_enabled", True)

    def setVisible(self, value):
        self.__dict__["_visible"] = bool(value)

    def isVisible(self):
        return self.__dict__.get("_visible", True)

    def show(self):
        self.setVisible(True)

    def hide(self):
        self.setVisible(False)


class _SpinBox(_StatefulBase):
    def __init__(self, *a, **k):
        self._value = 0
        self._min, self._max = -1e18, 1e18
        self.valueChanged = _Signal()
        self.editingFinished = _Signal()

    def setRange(self, lo, hi):
        self._min, self._max = lo, hi
        self._value = min(max(self._value, lo), hi)

    def setMinimum(self, lo):
        self._min = lo

    def setMaximum(self, hi):
        self._max = hi

    def minimum(self):
        return self._min

    def maximum(self):
        return self._max

    def setValue(self, v):
        self._value = min(max(v, self._min), self._max)

    def value(self):
        return self._value


class _ComboBox(_StatefulBase):
    _NO_DATA = object()

    def __init__(self, *a, **k):
        self._items = []
        self._data = []
        self._idx = -1
        self._editable = False
        self.currentIndexChanged = _Signal()
        self.currentTextChanged = _Signal()
        self.activated = _Signal()

    def addItem(self, text, data=_NO_DATA):
        self._items.append(text)
        self._data.append(None if data is self._NO_DATA else data)
        if self._idx < 0:
            self._idx = 0

    def addItems(self, texts):
        for t in texts:
            self.addItem(t)

    def itemData(self, i):
        return self._data[i] if 0 <= i < len(self._data) else None

    def currentData(self):
        return self.itemData(self._idx)

    def setEditable(self, v):
        self._editable = bool(v)

    def isEditable(self):
        return self._editable

    def clear(self):
        self._items = []
        self._data = []
        self._idx = -1

    def count(self):
        return len(self._items)

    def itemText(self, i):
        return self._items[i] if 0 <= i < len(self._items) else ""

    def setCurrentIndex(self, i):
        self._idx = i

    def currentIndex(self):
        return self._idx

    def setCurrentText(self, text):
        if text in self._items:
            self._idx = self._items.index(text)
            self._free_text = None
        elif self._editable:
            # An editable combo accepts text that is not in its item list.
            self._free_text = text

    def currentText(self):
        free = getattr(self, "_free_text", None)
        return free if free is not None else self.itemText(self._idx)

    def findText(self, text):
        return self._items.index(text) if text in self._items else -1


class _CheckBox(_StatefulBase):
    def __init__(self, *a, **k):
        self._checked = False
        self._text = a[0] if a and isinstance(a[0], str) else ""
        self.toggled = _Signal()
        self.stateChanged = _Signal()
        self.clicked = _Signal()

    def setChecked(self, v):
        self._checked = bool(v)

    def isChecked(self):
        return self._checked

    def setText(self, t):
        self._text = t

    def text(self):
        return self._text

    def checkState(self):
        return 2 if self._checked else 0


class _FontMetrics:
    """Just enough QFontMetrics for elision: one character per pixel."""

    def elidedText(self, text, mode, width, *a):
        text = "" if text is None else str(text)
        try:
            width = int(width)
        except (TypeError, ValueError):
            return text
        if width <= 0 or len(text) <= width:
            return text
        keep = max(1, width - 1)
        head = keep // 2
        tail = keep - head
        return text[:head] + "…" + (text[-tail:] if tail else "")

    def horizontalAdvance(self, text):
        return len(str(text))

    def __getattr__(self, name):
        return MagicMock()


class _Label(_StatefulBase):
    """QLabel stand-in. Also a base class — ElidedLabel subclasses it and calls
    ``super().setText()``, which the plain stub never defines."""

    def __init__(self, text="", *a, **k):
        self._text = text if isinstance(text, str) else ""
        self._width = 200

    def setText(self, t):
        self._text = "" if t is None else str(t)

    def text(self):
        return self._text

    def fontMetrics(self):
        return _FontMetrics()

    def width(self):
        return self._width

    def setFixedWidth(self, w):
        if isinstance(w, int):
            self._width = w


class _Button(_StatefulBase):
    """QPushButton stand-in with a real stylesheet.

    Some dialogs encode state in the stylesheet and read it back (MODialog
    stores the lobe colours there and parses them out in get_color_hex), so a
    MagicMock that forgets what it was told silently defeats that round-trip.
    """

    def __init__(self, *a, **k):
        self._text = a[0] if a and isinstance(a[0], str) else ""
        self._checkable = False
        self._checked = False
        self.clicked = _Signal()
        self.toggled = _Signal()

    def setText(self, t):
        self._text = "" if t is None else str(t)

    def text(self):
        return self._text

    # Toggle buttons drive show/hide state and are read back via isChecked().
    def setCheckable(self, v):
        self._checkable = bool(v)

    def isCheckable(self):
        return self._checkable

    def setChecked(self, v):
        self._checked = bool(v)

    def isChecked(self):
        return self._checked


class _Slider(_StatefulBase):
    """Stateful QSlider. Also serves as a base class — ResetSlider subclasses it."""

    def __init__(self, *a, **k):
        self._value = 0
        self._min, self._max = -100, 100
        self.valueChanged = _Signal()
        self.sliderReleased = _Signal()
        self.sliderMoved = _Signal()

    def setRange(self, lo, hi):
        self._min, self._max = lo, hi
        self._value = min(max(self._value, lo), hi)

    def setMinimum(self, lo):
        self._min = lo

    def setMaximum(self, hi):
        self._max = hi

    def minimum(self):
        return self._min

    def maximum(self):
        return self._max

    def setValue(self, v):
        self._value = min(max(v, self._min), self._max)

    def value(self):
        return self._value

    # Subclasses override these Qt event hooks; keep them harmless.
    def mouseDoubleClickEvent(self, event):
        return None


class _LineEdit(_StatefulBase):
    def __init__(self, *a, **k):
        self._text = a[0] if a and isinstance(a[0], str) else ""
        self.textChanged = _Signal()
        self.editingFinished = _Signal()
        self.returnPressed = _Signal()

    def setText(self, t):
        self._text = "" if t is None else str(t)

    def text(self):
        return self._text


class _ListItem(_StatefulBase):
    """QListWidgetItem stand-in storing its text and per-role data."""

    def __init__(self, text="", *a, **k):
        self._text = "" if text is None else str(text)
        self._data = {}

    def text(self):
        return self._text

    def setText(self, t):
        self._text = "" if t is None else str(t)

    def setData(self, role, value):
        self._data[role] = value

    def data(self, role):
        return self._data.get(role)


class _ListWidget(_StatefulBase):
    """QListWidget stand-in with a real item list and current row."""

    def __init__(self, *a, **k):
        self._items = []
        self._current = -1
        self.itemDoubleClicked = _Signal()
        self.itemSelectionChanged = _Signal()
        self.currentRowChanged = _Signal()

    def addItem(self, item):
        self._items.append(item if not isinstance(item, str) else _ListItem(item))

    def addItems(self, texts):
        for t in texts:
            self.addItem(t)

    def clear(self):
        self._items = []
        self._current = -1

    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def setCurrentRow(self, i):
        self._current = i

    def currentRow(self):
        return self._current

    def currentItem(self):
        return self.item(self._current)


class _TableItem(_StatefulBase):
    """QTableWidgetItem stand-in that stores its text."""

    def __init__(self, text="", *a, **k):
        self._text = "" if text is None else str(text)
        self._data = {}

    def text(self):
        return self._text

    def setText(self, t):
        self._text = "" if t is None else str(t)

    def setData(self, role, value):
        self._data[role] = value

    def data(self, role):
        return self._data.get(role)


class _TableWidget(_StatefulBase):
    """QTableWidget stand-in with real row/column counts and cell storage.

    Also serves as a base class (bond_analysis subclasses it), and the counts
    must be real numbers: column-width weighting compares them against ints.
    """

    def __init__(self, rows=0, cols=0, *a, **k):
        self._rows = rows if isinstance(rows, int) else 0
        self._cols = cols if isinstance(cols, int) else 0
        self._cells = {}
        self._headers = []
        self._widths = {}
        self.itemSelectionChanged = _Signal()
        self.cellDoubleClicked = _Signal()
        self.itemChanged = _Signal()

    def setRowCount(self, n):
        self._rows = n
        self._cells = {k: v for k, v in self._cells.items() if k[0] < n}

    def rowCount(self):
        return self._rows

    def setColumnCount(self, n):
        self._cols = n

    def columnCount(self):
        return self._cols

    def setHorizontalHeaderLabels(self, labels):
        self._headers = list(labels)
        self._cols = max(self._cols, len(self._headers))

    def horizontalHeaderItem(self, c):
        return _TableItem(self._headers[c]) if 0 <= c < len(self._headers) else None

    def setItem(self, r, c, item):
        self._cells[(r, c)] = item
        self._rows = max(self._rows, r + 1)
        self._cols = max(self._cols, c + 1)

    def item(self, r, c):
        return self._cells.get((r, c))

    def setColumnWidth(self, c, w):
        self._widths[c] = w

    def columnWidth(self, c):
        return self._widths.get(c, 100)


class _TreeItem(_StatefulBase):
    """QTreeWidgetItem stand-in that stores its column text and children."""

    def __init__(self, texts=None, *a, **k):
        if isinstance(texts, (list, tuple)):
            self._texts = [("" if t is None else str(t)) for t in texts]
        else:
            self._texts = []
        self._children = []
        self._parent = None
        self._data = {}
        self._selected = False
        self.foregrounds = {}
        self.backgrounds = {}

    def text(self, col):
        return self._texts[col] if 0 <= col < len(self._texts) else ""

    def setText(self, col, value):
        while len(self._texts) <= col:
            self._texts.append("")
        self._texts[col] = "" if value is None else str(value)

    def setForeground(self, col, brush):
        self.foregrounds[col] = brush

    def setBackground(self, col, brush):
        self.backgrounds[col] = brush

    def setData(self, col, role, value):
        self._data[(col, role)] = value

    def data(self, col, role):
        return self._data.get((col, role))

    def addChild(self, child):
        child._parent = self
        self._children.append(child)

    def childCount(self):
        return len(self._children)

    def child(self, i):
        return self._children[i] if 0 <= i < len(self._children) else None

    def parent(self):
        return self._parent

    def setSelected(self, v):
        self._selected = bool(v)

    def isSelected(self):
        return self._selected


class _TreeWidget(_StatefulBase):
    """QTreeWidget stand-in backed by a real item list."""

    def __init__(self, *a, **k):
        self._top = []
        self._current = None
        self.itemSelectionChanged = _Signal()
        self.currentItemChanged = _Signal()
        self.itemDoubleClicked = _Signal()

    def clear(self):
        self._top = []
        self._current = None

    def addTopLevelItem(self, item):
        self._top.append(item)

    def addTopLevelItems(self, items):
        self._top.extend(items)

    def topLevelItemCount(self):
        return len(self._top)

    def topLevelItem(self, i):
        return self._top[i] if 0 <= i < len(self._top) else None

    def indexOfTopLevelItem(self, item):
        return self._top.index(item) if item in self._top else -1

    def invisibleRootItem(self):
        root = _TreeItem()
        root._children = self._top
        return root

    def setCurrentItem(self, item):
        self._current = item

    def currentItem(self):
        return self._current

    def selectedItems(self):
        return [i for i in self._flatten() if i.isSelected()]

    def _flatten(self):
        out = []

        def walk(items):
            for it in items:
                out.append(it)
                walk(it._children)

        walk(self._top)
        return out


class _TreeIterator:
    """QTreeWidgetItemIterator stand-in.

    The source walks trees with the standard Qt idiom::

        it = QTreeWidgetItemIterator(tree)
        while it.value():
            ...
            it += 1

    A MagicMock ``value()`` is always truthy, so that loop never terminates.
    Returning None past the last item keeps it finite, as real Qt does.
    """

    def __init__(self, tree, *a, **k):
        if isinstance(tree, _TreeWidget):
            self._items = tree._flatten()
        elif isinstance(tree, _TreeItem):
            self._items = [tree] + tree._children
        else:
            self._items = []
        self._pos = 0

    def value(self):
        return self._items[self._pos] if self._pos < len(self._items) else None

    def __iadd__(self, n):
        self._pos += n
        return self

    def __next__(self):
        item = self.value()
        self._pos += 1
        return item


class _LayoutItem:
    def __init__(self, widget=None, layout=None):
        self._w, self._l = widget, layout

    def widget(self):
        return self._w

    def layout(self):
        return self._l


class _Layout(_StatefulBase):
    """Stateful layout stub.

    Dialogs rebuild dynamic panels with the standard Qt idiom::

        while layout.count():
            layout.takeAt(0)

    A MagicMock ``count()`` is always truthy, so that loop never terminates.
    Tracking the child list keeps it finite, exactly as real Qt does.
    """

    def __init__(self, *a, **k):
        self._items = []

    def addWidget(self, w, *a, **k):
        self._items.append(_LayoutItem(widget=w))

    def addLayout(self, lay, *a, **k):
        self._items.append(_LayoutItem(layout=lay))

    def insertWidget(self, i, w, *a, **k):
        self._items.insert(i, _LayoutItem(widget=w))

    # Stretches and spacers occupy a slot in Qt too, so they must be counted.
    def addStretch(self, *a, **k):
        self._items.append(_LayoutItem())

    def addSpacing(self, *a, **k):
        self._items.append(_LayoutItem())

    def addItem(self, *a, **k):
        self._items.append(_LayoutItem())

    def addRow(self, *a, **k):
        self._items.append(_LayoutItem(widget=a[-1] if a else None))

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def removeWidget(self, w):
        self._items = [it for it in self._items if it.widget() is not w]


def _stateful_widgets():
    return {
        "QVBoxLayout": _Layout,
        "QHBoxLayout": _Layout,
        "QGridLayout": _Layout,
        "QFormLayout": _Layout,
        "QStackedLayout": _Layout,
        "QSpinBox": _SpinBox,
        "QDoubleSpinBox": _SpinBox,
        "QComboBox": _ComboBox,
        "QFontComboBox": _ComboBox,
        "QCheckBox": _CheckBox,
        "QRadioButton": _CheckBox,
        "QLineEdit": _LineEdit,
        # Listed in _QTW_BASES too (ResetSlider subclasses it); the extras are
        # applied after the bases, so this stateful version wins.
        "QSlider": _Slider,
        # QLabel is in _QTW_BASES too (ElidedLabel subclasses it).
        "QLabel": _Label,
        "QPushButton": _Button,
        "QToolButton": _Button,
        # Listed in _QTW_BASES too (bond_analysis subclasses QTableWidget);
        # extras are applied after the bases, so the stateful version wins.
        "QTableWidget": _TableWidget,
        "QTableWidgetItem": _TableItem,
        "QListWidget": _ListWidget,
        "QListWidgetItem": _ListItem,
        "QTreeWidget": _TreeWidget,
        "QTreeWidgetItem": _TreeItem,
        "QTreeWidgetItemIterator": _TreeIterator,
    }


# Widget base classes subclassed anywhere in the source.
_QTW_BASES = ["QDialog", "QWidget", "QTableWidget", "QSlider", "QLabel", "QMainWindow"]
_QTC_BASES = ["QThread", "QObject"]
_BACKEND_BASES = ["FigureCanvasQTAgg", "NavigationToolbar2QT"]


def _ensure_module(name):
    mod = sys.modules.get(name)
    if mod is None:
        mod = MagicMock()
        mod.__name__ = name
        sys.modules[name] = mod
    return mod


_SENTINEL = object()


def _fresh_module(name, bases, base_factory, extra=None):
    """A MagicMock module (so `from mod import Anything` auto-resolves) carrying
    real, subclassable base classes plus any extras."""
    mod = MagicMock()
    mod.__name__ = name
    for b in bases:
        setattr(mod, b, base_factory(b))
    for k, v in (extra or {}).items():
        setattr(mod, k, v)
    return mod


_real_cache = {}


def _is_stub(obj):
    """True if *obj* is a MagicMock (instance or class), i.e. not the real thing."""
    if isinstance(obj, MagicMock):
        return True
    return isinstance(obj, type) and issubclass(obj, MagicMock)


def _looks_real(mod, required):
    """True if *mod* exposes every name in *required* as a non-stub object."""
    if mod is None or isinstance(mod, MagicMock):
        return False
    for attr in required:
        if not hasattr(mod, attr) or _is_stub(getattr(mod, attr)):
            return False
    return True


def _real_tree(root, wanted):
    """Genuine ``root`` package and submodules, bypassing any test stub.

    Several test modules install permanent fakes for numpy/matplotlib into the
    shared ``sys.modules`` — a numpy whose only attribute is ``array``, a
    ``matplotlib.figure`` whose ``Figure`` is a MagicMock. Modules loaded here
    do real numerical work and real plotting, so they need the true packages.

    *wanted* maps module name -> attributes that must be present and not stubs.
    If anything installed fails that check, the whole ``root`` subtree is lifted
    out of ``sys.modules``, the real packages are imported from disk, and the
    stubs are put back for whichever test module owns them.
    """
    if all(_looks_real(_real_cache.get(n), a) for n, a in wanted.items()):
        return {n: _real_cache[n] for n in wanted}

    if all(_looks_real(sys.modules.get(n), a) for n, a in wanted.items()):
        for n in wanted:
            _real_cache[n] = sys.modules[n]
        return {n: _real_cache[n] for n in wanted}

    stub_backup = {
        k: v for k, v in sys.modules.items() if k == root or k.startswith(root + ".")
    }
    for k in stub_backup:
        del sys.modules[k]
    try:
        importlib.invalidate_caches()
        for name in wanted:
            try:
                _real_cache[name] = importlib.import_module(name)
            except Exception:
                _real_cache[name] = stub_backup.get(name) or MagicMock()
    finally:
        sys.modules.update(stub_backup)
    return {n: _real_cache[n] for n in wanted}


def _pyvista_module():
    """Real pyvista if usable, else a MagicMock that answers any mesh factory.

    Other test modules leave a partial pyvista stub (PolyData/Sphere only) in
    sys.modules. A plain ``import pyvista`` would just hand that stub back, so
    geometry the source actually builds — pv.Arrow for the dipole vector —
    would be missing. Fall through to a MagicMock in that case rather than
    returning a module that is real but incomplete.
    """
    needed = ("Arrow", "Box", "Sphere", "Line", "PolyData")
    pv = sys.modules.get("pyvista")
    if _looks_real(pv, needed):
        return pv

    # A stub is installed. Do NOT import the real pyvista here: mesh factories
    # such as pv.Arrow() resolve pyvista's own module globals through
    # sys.modules at call time, and by then the harness has restored the stub
    # for its owner — the real library would fail on its internals. A MagicMock
    # answers any factory and is all the source needs, since the meshes are
    # handed straight to a mocked plotter.
    return MagicMock()


class qt_available:
    """Context manager making ``PySide6.QtWidgets`` importable *at call time*.

    ``load_isolated`` restores sys.modules once a module is imported, but some
    methods import Qt lazily inside the function body (e.g.
    ``from PySide6.QtWidgets import QInputDialog`` in save_custom_preset). Wrap
    such calls in this to install a stub package for the duration, optionally
    overriding individual names::

        with gui_harness.qt_available(QInputDialog=fake):
            dialog.save_custom_preset()
    """

    _KEYS = ("PySide6", "PySide6.QtWidgets", "PySide6.QtCore", "PySide6.QtGui")

    def __init__(self, **overrides):
        self._overrides = overrides

    def __enter__(self):
        self._saved = {k: sys.modules.get(k, _SENTINEL) for k in self._KEYS}

        pkg = types.ModuleType("PySide6")
        pkg.__path__ = []
        widgets = _fresh_module(
            "PySide6.QtWidgets", _QTW_BASES, _make_widget, extra=_stateful_widgets()
        )
        core = _fresh_module("PySide6.QtCore", _QTC_BASES, _make_widget)
        gui = _fresh_module(
            "PySide6.QtGui", [], _make_widget, extra={"QColor": QColorStub}
        )
        for name, value in self._overrides.items():
            setattr(widgets, name, value)
        pkg.QtWidgets, pkg.QtCore, pkg.QtGui = widgets, core, gui

        sys.modules.update(
            {
                "PySide6": pkg,
                "PySide6.QtWidgets": widgets,
                "PySide6.QtCore": core,
                "PySide6.QtGui": gui,
            }
        )
        return widgets

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is _SENTINEL:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        return False


def _plugin_version():
    """PLUGIN_VERSION read straight from the package source.

    Parsed rather than imported so we don't execute the real __init__, which
    would drag in the whole plugin (and a live Qt).
    """
    init_py = os.path.join(_PKG_DIR, "__init__.py")
    try:
        with open(init_py, encoding="utf-8") as fh:
            m = re.search(r"""PLUGIN_VERSION\s*=\s*['"](.+?)['"]""", fh.read())
        return m.group(1) if m else "0.0.0"
    except OSError:
        return "0.0.0"


_load_counter = [0]

# sys.modules keys we swap for the duration of an isolated load.
_SWAP_KEYS = [
    "PySide6.QtWidgets",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "matplotlib.backends.backend_qtagg",
    "pyvista",
    # Other test modules leave permanent numpy/matplotlib fakes behind; the
    # modules loaded here need the genuine articles to do real numerics.
    "numpy",
    "matplotlib",
    "matplotlib.figure",
    "matplotlib.ticker",
    "matplotlib.backends",
]


def load_isolated(modname):
    _ensure_module("PySide6")

    swapped = {
        "PySide6.QtWidgets": _fresh_module(
            "PySide6.QtWidgets", _QTW_BASES, _make_widget, extra=_stateful_widgets()
        ),
        "PySide6.QtCore": _fresh_module("PySide6.QtCore", _QTC_BASES, _make_widget),
        "PySide6.QtGui": _fresh_module(
            "PySide6.QtGui", [], _make_widget, extra={"QColor": QColorStub}
        ),
        "pyvista": _pyvista_module(),
    }
    swapped.update(_real_tree("numpy", {"numpy": ("linspace", "exp", "zeros_like")}))
    swapped.update(
        _real_tree(
            "matplotlib",
            {
                "matplotlib": ("use",),
                "matplotlib.figure": ("Figure",),
                "matplotlib.ticker": ("MaxNLocator",),
                # Real backends package: figure.tight_layout() resolves a
                # renderer through it, which a bare stub cannot provide.
                "matplotlib.backends": ("__name__",),
            },
        )
    )
    # ... but the Qt-agg backend stays stubbed — importing the real one needs a
    # live Qt binding. Applied last so it wins over the real backends tree.
    swapped["matplotlib.backends.backend_qtagg"] = _fresh_module(
        "matplotlib.backends.backend_qtagg", _BACKEND_BASES, _make_widget
    )
    saved = {k: sys.modules.get(k, _SENTINEL) for k in _SWAP_KEYS}
    for k, v in swapped.items():
        sys.modules[k] = v

    _load_counter[0] += 1
    pkg_name = f"_ora_iso_{_load_counter[0]}"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [_PKG_DIR]
    # Some modules do `from . import PLUGIN_VERSION`. The throwaway package
    # deliberately never executes the real __init__ (it pulls in the whole
    # plugin), so mirror the package-level constants it exposes.
    pkg.PLUGIN_VERSION = _plugin_version()
    sys.modules[pkg_name] = pkg
    try:
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{modname}", os.path.join(_PKG_DIR, f"{modname}.py")
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = pkg_name
        sys.modules[f"{pkg_name}.{modname}"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k in _SWAP_KEYS:
            if saved[k] is _SENTINEL:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = saved[k]


# ---------------------------------------------------------------------------
# Functional QColor for color-interpolation math.
# ---------------------------------------------------------------------------

_NAMED = {
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "purple": (128, 0, 128),
    "cyan": (0, 255, 255),
    "yellow": (255, 255, 0),
    "gray": (128, 128, 128),
}


class QColorStub:
    def __init__(self, *a):
        r = g = b = 0
        if len(a) == 1 and isinstance(a[0], str):
            s = a[0].strip().lower()
            if s.startswith("#") and len(s) == 7:
                r, g, b = int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16)
            else:
                r, g, b = _NAMED.get(s, (0, 0, 0))
        elif len(a) >= 3:
            r, g, b = int(a[0]), int(a[1]), int(a[2])
        self._r, self._g, self._b = r, g, b

    def red(self):
        return self._r

    def green(self):
        return self._g

    def blue(self):
        return self._b

    def name(self):
        return f"#{self._r:02x}{self._g:02x}{self._b:02x}"

    def isValid(self):
        return True

    def getRgb(self):
        return (self._r, self._g, self._b, 255)
