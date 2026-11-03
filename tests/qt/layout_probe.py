"""Measurements of the rendered QML tree, shared by tests and visual evidence."""

import json
import re
from dataclasses import dataclass

from PySide6.QtCore import QPointF
from PySide6.QtQml import QQmlExpression


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    def contains(self, other, epsilon=2):
        return (
            other.x >= self.x - epsilon
            and other.y >= self.y - epsilon
            and other.x + other.width <= self.x + self.width + epsilon
            and other.y + other.height <= self.y + self.height + epsilon
        )


@dataclass(frozen=True)
class Node:
    parent: int | None
    type: str
    name: str
    text: str
    rect: Rect
    control: bool
    textitem: bool
    scroll: bool
    props: tuple

    def prop(self, key, default=None):
        return dict(self.props).get(key, default)


@dataclass(frozen=True)
class Violation:
    kind: str
    item: str
    text: str
    rect: Rect
    ancestor: str = ""


# QML traversal avoids Python wrapping/deleting internal Qt Quick items. Geometry is
# mapped to window coordinates; each parent is an index in the frozen snapshot.
_TREE_JS = r"""
JSON.stringify((function() {
    var nodes = [];
    var footerItem = typeof footer === "undefined" ? null : footer;
    function visit(o, parent, inFooter) {
        inFooter = inFooter || o === footerItem;
        if (!o.visible || o.opacity < 0.01) return;
        var p = o.mapToItem(null, 0, 0), t = String(o).split('(')[0];
        var control = typeof o.down === 'boolean' ||
            (typeof o.cursorPosition === 'number' && !o.readOnly) ||
            /ComboBox|SpinBox|Slider/.test(t);
        var textitem = typeof o.text === 'string' && typeof o.wrapMode !== 'undefined' &&
            (typeof o.cursorPosition === 'undefined' || o.readOnly === true);
        var n = {parent: parent, type: t, name: o.objectName || '', text: o.text || '',
            x: p.x, y: p.y, width: o.width, height: o.height, control: control, textitem: textitem,
            scroll: typeof o.contentX === 'number' && typeof o.flickableDirection !== 'undefined',
            props: {inFooter: inFooter, clip: o.clip,
                interactive: o.interactive === undefined ? true : o.interactive,
                contentWidth: o.contentWidth || 0, contentHeight: o.contentHeight || 0,
                implicitWidth: o.implicitWidth || 0, implicitHeight: o.implicitHeight || 0,
                wrapMode: o.wrapMode || 0, elide: o.elide || 0, truncated: o.truncated || false,
                fontPx: o.font ?
                    (o.font.pixelSize > 0 ? o.font.pixelSize : o.font.pointSize * 96/72) : 0}};
        var i = nodes.length; nodes.push(n);
        for (var j = 0; j < o.children.length; j++) visit(o.children[j], i, inFooter);
    }
    var top = contentItem;
    REPLACE_TOP
    visit(top, null);
    return nodes;
})())
"""


def tree(engine, root, popup=None):
    """Snapshot visible visual items; a modal probe excludes obscured page items."""
    top = "top = top.parent;" if popup else "while (top.parent) top = top.parent;"
    script = _TREE_JS.replace("REPLACE_TOP", top)
    if popup is not None and hasattr(popup, "evaluate"):
        value = popup.evaluate(script)
    else:
        expr = QQmlExpression(engine.rootContext(), popup or root, script)
        value, _undefined = expr.evaluate()
        if expr.hasError():
            raise RuntimeError(expr.error().toString())
    return tuple(
        Node(
            raw["parent"],
            raw["type"],
            raw["name"],
            str(raw["text"]),
            Rect(raw["x"], raw["y"], raw["width"], raw["height"]),
            raw["control"],
            raw["textitem"],
            raw["scroll"],
            tuple(raw["props"].items()),
        )
        for raw in json.loads(value)
    )


def ancestors(nodes, node):
    while node.parent is not None:
        node = nodes[node.parent]
        yield node


def _scrollable(nodes, node, axis, boundary, clip=None):
    for ancestor in ancestors(nodes, node):
        rect = ancestor.rect
        inside = (
            rect.x >= boundary.x - 2 and rect.x + rect.width <= boundary.x + boundary.width + 2
            if axis == "x"
            else rect.y >= boundary.y - 2
            and rect.y + rect.height <= boundary.y + boundary.height + 2
        )
        size = rect.width if axis == "x" else rect.height
        content = ancestor.prop("contentWidth" if axis == "x" else "contentHeight", 0)
        if ancestor.scroll and inside and ancestor.prop("interactive") and content > size + 2:
            return True
        if ancestor is clip:
            break
    return False


def _outside(nodes, node, boundary, clip=None):
    rect = node.rect
    x = rect.x < boundary.x - 2 or rect.x + rect.width > boundary.x + boundary.width + 2
    y = rect.y < boundary.y - 2 or rect.y + rect.height > boundary.y + boundary.height + 2
    return (x and not _scrollable(nodes, node, "x", boundary, clip)) or (
        y and not _scrollable(nodes, node, "y", boundary, clip)
    )


def owner_name(nodes, node):
    """Attribute internal labels/buttons to the nearest named application item."""
    return next((item.name for item in (node, *ancestors(nodes, node)) if item.name), node.type)


def _intentional_internal(nodes, node):
    # Kirigami's application header reserves an off-window navigation button.
    # Restrict the exception to its internal type and named header ancestor.
    return (
        not node.name
        and "ToolButton" in node.type
        and any(a.name == "applicationHeader" for a in ancestors(nodes, node))
    )


def _node_violations(nodes, node, viewport):
    if _intentional_internal(nodes, node):
        return ()

    def issue(kind, ancestor=""):
        return Violation(kind, owner_name(nodes, node), node.text[:150], node.rect, ancestor)

    if node.rect.width <= 0 or node.rect.height <= 0:
        return (issue("collapsed_control"),) if node.control else ()
    result = []
    if node.control and _outside(nodes, node, viewport):
        result.append(issue("control_outside_window"))
    if node.control or node.textitem:
        for ancestor in ancestors(nodes, node):
            if ancestor.prop("clip") and _outside(nodes, node, ancestor.rect, ancestor):
                result.append(issue("outside_clipping_parent", owner_name(nodes, ancestor)))
                break
    if node.textitem and node.text and node.prop("elide") != 1:
        if node.prop("truncated"):
            result.append(issue("text_elided"))
        elif (
            node.prop("wrapMode") == 0 and node.prop("implicitWidth", 0) > node.rect.width + 2
        ) or node.prop("implicitHeight", 0) > node.rect.height + 2:
            result.append(issue("text_clipped"))
    return tuple(result)


def popup_rect(popup):
    if hasattr(popup, "rect"):
        return Rect(*popup.rect())
    parent = popup.property("parent")
    origin = parent.mapToScene(QPointF(float(popup.property("x")), float(popup.property("y"))))
    return Rect(
        origin.x(), origin.y(), float(popup.property("width")), float(popup.property("height"))
    )


def detect(engine, root, popup=None, nodes=None):
    """Find unreachable controls/clipped text, respecting scrollable ancestors."""
    nodes = tree(engine, root, popup) if nodes is None else nodes
    viewport = Rect(0, 0, root.width(), root.height())
    issues = tuple(issue for node in nodes for issue in _node_violations(nodes, node, viewport))
    if popup is not None and not viewport.contains(popup_rect(popup)):
        issues += (Violation("dialog_exceeds_window", popup.objectName(), "", popup_rect(popup)),)
    return issues


def named(nodes, name):
    matches = tuple(node for node in nodes if node.name == name)
    assert len(matches) == 1, f"expected one visible {name!r}, got {len(matches)}"
    return matches[0]


def assert_reachable(nodes, root, names):
    """A named item must be visible and contained or reachable by scrolling."""
    viewport = Rect(0, 0, root.width(), root.height())
    for name in names:
        node = named(nodes, name)
        assert node.rect.width > 0 and node.rect.height > 0, name
        assert not _outside(nodes, node, viewport), f"{name} outside window: {node.rect}"
        for ancestor in ancestors(nodes, node):
            if ancestor.prop("clip"):
                assert not _outside(nodes, node, ancestor.rect, ancestor), f"{name} clipped"


def thumb_caption_violations(nodes):
    return tuple(
        node
        for node in nodes
        if node.textitem
        and node.prop("truncated")
        and any(re.fullmatch(r"slideThumb_\d+_\d+", a.name) for a in ancestors(nodes, node))
    )
