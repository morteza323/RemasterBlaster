"""
UVCanvasWidget (spec §12-14): the professional UV canvas -- pan, zoom,
fit-to-screen, 100% view, pixel-perfect (nearest-neighbor) rendering,
guides as draggable first-class objects with numeric-precision
positions, live pixel coordinate readout, and a selected-part region
highlight.

NOT covered here (deferred, see ui/panels/mask_editor.py for masking):
polygon selection and freeform mask painting -- spec §12 lists these
alongside guides, but the phase plan (§98) splits masking into its own
Phase 12, which is a separate widget composited on top of a part's
crop, not the full-texture canvas.

UNTESTED IN THIS ENVIRONMENT: PyQt6 is not installed here (no network
access to add it -- see project README). This file is syntax-checked
only; run it in a real PyQt6 environment to verify.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from PyQt6.QtWidgets import QGraphicsLineItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView

from core.models.guide import Guide, GuideOrientation
from core.models.part import Bounds

_GUIDE_COLOR = QColor(0, 200, 255)
_HIGHLIGHT_PEN_COLOR = QColor(255, 200, 0)
_HIGHLIGHT_FILL_COLOR = QColor(255, 200, 0, 40)
_GUIDE_HIT_TOLERANCE_PX = 6  # screen pixels, independent of zoom


class UVCanvasWidget(QGraphicsView):
    coordinates_changed = pyqtSignal(int, int)  # image-space x, y under cursor
    guide_moved = pyqtSignal(str, int)          # guide id, new image-space position
    guide_selected = pyqtSignal(str)            # guide id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setMouseTracking(True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._guide_items: Dict[str, QGraphicsLineItem] = {}
        self._guides: List[Guide] = []
        self._highlight_item: Optional[QGraphicsRectItem] = None
        self._image_width = 0
        self._image_height = 0
        self._dragging_guide_id: Optional[str] = None

    # -- loading -----------------------------------------------------------

    def load_image(self, path: str) -> None:
        pixmap = QPixmap(path)
        self._image_width = pixmap.width()
        self._image_height = pixmap.height()
        self._scene.clear()
        self._guide_items.clear()
        self._highlight_item = None
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(0, 0, self._image_width, self._image_height)
        self.set_guides(self._guides)
        self.fit_to_screen()

    # -- view controls (spec §12) --------------------------------------------

    def fit_to_screen(self) -> None:
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def view_100_percent(self) -> None:
        self.resetTransform()

    def zoom_in(self, factor: float = 1.25) -> None:
        self.scale(factor, factor)

    def zoom_out(self, factor: float = 1.25) -> None:
        self.scale(1 / factor, 1 / factor)

    def set_pixel_perfect(self, enabled: bool) -> None:
        """Nearest-neighbor (pixel-perfect) vs. smooth preview (spec §12)."""
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not enabled)

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        else:
            super().keyReleaseEvent(event)

    # -- guides (spec §13-14) -------------------------------------------------

    def set_guides(self, guides: List[Guide]) -> None:
        self._guides = guides
        for item in self._guide_items.values():
            self._scene.removeItem(item)
        self._guide_items.clear()
        if self._image_width == 0:
            return
        for guide in guides:
            self._add_guide_item(guide)

    def _add_guide_item(self, guide: Guide) -> None:
        pen = QPen(_GUIDE_COLOR)
        pen.setWidth(0)  # cosmetic pen: always 1 device pixel, any zoom level
        if guide.orientation == GuideOrientation.HORIZONTAL:
            line = QLineF(0, guide.position, self._image_width, guide.position)
        else:
            line = QLineF(guide.position, 0, guide.position, self._image_height)
        item = self._scene.addLine(line, pen)
        item.setZValue(10)
        item.setVisible(guide.visible)
        self._guide_items[guide.id] = item

    def set_guide_position(self, guide_id: str, position: int) -> None:
        """Numeric-precision guide editing (spec §14) -- call this from
        the side panel's spinbox instead of dragging."""
        guide = next((g for g in self._guides if g.id == guide_id), None)
        item = self._guide_items.get(guide_id)
        if guide is None or item is None:
            return
        guide.position = position
        if guide.orientation == GuideOrientation.HORIZONTAL:
            item.setLine(0, position, self._image_width, position)
        else:
            item.setLine(position, 0, position, self._image_height)

    def _guide_at(self, scene_pos: QPointF) -> Optional[Guide]:
        scale = max(self.transform().m11(), 0.0001)
        tolerance = _GUIDE_HIT_TOLERANCE_PX / scale
        for guide in self._guides:
            if guide.locked or not guide.visible:
                continue
            if guide.orientation == GuideOrientation.HORIZONTAL and abs(scene_pos.y() - guide.position) <= tolerance:
                return guide
            if guide.orientation == GuideOrientation.VERTICAL and abs(scene_pos.x() - guide.position) <= tolerance:
                return guide
        return None

    # -- selected-part highlight ---------------------------------------------

    def highlight_region(self, bounds: Optional[Bounds]) -> None:
        if self._highlight_item is not None:
            self._scene.removeItem(self._highlight_item)
            self._highlight_item = None
        if bounds is None:
            return
        pen = QPen(_HIGHLIGHT_PEN_COLOR)
        pen.setWidth(0)
        self._highlight_item = self._scene.addRect(
            QRectF(bounds.x, bounds.y, bounds.width, bounds.height), pen, QBrush(_HIGHLIGHT_FILL_COLOR)
        )
        self._highlight_item.setZValue(5)

    # -- mouse interaction ----------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        scene_pos = self.mapToScene(event.pos())
        guide = self._guide_at(scene_pos)
        if guide is not None and event.button() == Qt.MouseButton.LeftButton:
            self._dragging_guide_id = guide.id
            self.guide_selected.emit(guide.id)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        scene_pos = self.mapToScene(event.pos())
        if self._image_width and self._image_height:
            x = max(0, min(self._image_width - 1, int(scene_pos.x())))
            y = max(0, min(self._image_height - 1, int(scene_pos.y())))
            self.coordinates_changed.emit(x, y)

        if self._dragging_guide_id is not None:
            guide = next((g for g in self._guides if g.id == self._dragging_guide_id), None)
            item = self._guide_items.get(self._dragging_guide_id)
            if guide is not None and item is not None:
                if guide.orientation == GuideOrientation.HORIZONTAL:
                    new_pos = max(0, min(self._image_height, int(scene_pos.y())))
                    item.setLine(0, new_pos, self._image_width, new_pos)
                else:
                    new_pos = max(0, min(self._image_width, int(scene_pos.x())))
                    item.setLine(new_pos, 0, new_pos, self._image_height)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging_guide_id is not None:
            guide = next((g for g in self._guides if g.id == self._dragging_guide_id), None)
            if guide is not None:
                scene_pos = self.mapToScene(event.pos())
                if guide.orientation == GuideOrientation.HORIZONTAL:
                    new_pos = max(0, min(self._image_height, int(scene_pos.y())))
                else:
                    new_pos = max(0, min(self._image_width, int(scene_pos.x())))
                guide.position = new_pos
                self.guide_moved.emit(guide.id, new_pos)
            self._dragging_guide_id = None
            return
        super().mouseReleaseEvent(event)
