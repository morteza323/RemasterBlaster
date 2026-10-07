"""
Preview Workspace (spec §36-38): Original / Reconstructed / Difference,
in Side-by-Side, Top/Bottom, Split View (draggable divider), Overlay,
or Difference layouts. Difference is explicitly a diagnostic
visualization, never a quality score (spec §38) -- this panel doesn't
attach any score to it, just the image.

UNTESTED IN THIS ENVIRONMENT (see ui/canvas.py's note).
"""

from __future__ import annotations

from typing import Optional

from PIL import Image
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from image.difference import DifferenceMode, compute_difference

_DIFF_MODE_BY_LABEL = {
    "Absolute": DifferenceMode.ABSOLUTE,
    "Structural": DifferenceMode.STRUCTURAL,
    "Edge": DifferenceMode.EDGE,
}


def pil_to_qpixmap(image: Image.Image) -> QPixmap:
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    data = image.tobytes("raw", image.mode)
    fmt = QImage.Format.Format_RGBA8888 if image.mode == "RGBA" else QImage.Format.Format_RGB888
    qimage = QImage(data, image.width, image.height, fmt)
    return QPixmap.fromImage(qimage.copy())  # detach from the temporary `data` buffer


class _SplitViewWidget(QWidget):
    """spec §37: a draggable divider between Original | Reconstructed."""

    split_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._left: Optional[QPixmap] = None
        self._right: Optional[QPixmap] = None
        self._ratio = 0.5
        self.setMinimumHeight(200)

    def set_images(self, left: QPixmap, right: QPixmap) -> None:
        self._left = left
        self._right = right
        self.update()

    def set_split_ratio(self, ratio: float) -> None:
        self._ratio = max(0.0, min(1.0, ratio))
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        if self._left is None or self._right is None:
            return
        painter = QPainter(self)
        rect = self.rect()
        scaled_left = self._left.scaled(rect.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        scaled_right = self._right.scaled(rect.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        offset_x = (rect.width() - scaled_left.width()) // 2
        offset_y = (rect.height() - scaled_left.height()) // 2
        divider_x = int(rect.width() * self._ratio)

        painter.setClipRect(0, 0, divider_x, rect.height())
        painter.drawPixmap(offset_x, offset_y, scaled_left)

        painter.setClipRect(divider_x, 0, rect.width() - divider_x, rect.height())
        painter.drawPixmap(offset_x, offset_y, scaled_right)

        painter.setClipping(False)
        pen = QPen(QColor(255, 255, 255))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(divider_x, 0, divider_x, rect.height())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._update_from_x(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._update_from_x(event.position().x())

    def _update_from_x(self, x: float) -> None:
        if self.width() > 0:
            self.set_split_ratio(x / self.width())
            self.split_changed.emit(self._ratio)


class PreviewPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._original_image: Optional[Image.Image] = None
        self._reconstructed_image: Optional[Image.Image] = None

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("View:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Side-by-Side", "Top/Bottom", "Split View", "Overlay", "Difference"])
        self.mode_combo.currentTextChanged.connect(self._update_view)
        controls.addWidget(self.mode_combo)

        self.diff_mode_combo = QComboBox()
        self.diff_mode_combo.addItems(list(_DIFF_MODE_BY_LABEL))
        self.diff_mode_combo.currentTextChanged.connect(self._update_view)
        controls.addWidget(self.diff_mode_combo)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self._side_by_side, self.original_label, self.reconstructed_label = self._make_two_label_page(QHBoxLayout)
        self.stack.addWidget(self._side_by_side)

        self._top_bottom, self.original_label_tb, self.reconstructed_label_tb = self._make_two_label_page(QVBoxLayout)
        self.stack.addWidget(self._top_bottom)

        self.split_widget = _SplitViewWidget()
        self.stack.addWidget(self.split_widget)

        self._overlay_widget = QWidget()
        overlay_layout = QVBoxLayout(self._overlay_widget)
        self.overlay_label = QLabel()
        self.overlay_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.overlay_opacity_slider.setRange(0, 100)
        self.overlay_opacity_slider.setValue(50)
        self.overlay_opacity_slider.valueChanged.connect(self._update_view)
        overlay_layout.addWidget(self.overlay_label)
        overlay_layout.addWidget(self.overlay_opacity_slider)
        self.stack.addWidget(self._overlay_widget)

        self.difference_label = QLabel()
        self.difference_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.difference_label)

    @staticmethod
    def _make_two_label_page(layout_cls):
        page = QWidget()
        page_layout = layout_cls(page)
        label_a, label_b = QLabel(), QLabel()
        for label in (label_a, label_b):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            page_layout.addWidget(label)
        return page, label_a, label_b

    def set_images(self, original: Optional[Image.Image], reconstructed: Optional[Image.Image]) -> None:
        self._original_image = original
        self._reconstructed_image = reconstructed
        self._update_view()

    def _update_view(self, *_args) -> None:
        if self._original_image is None or self._reconstructed_image is None:
            return
        original_pixmap = pil_to_qpixmap(self._original_image)
        reconstructed_pixmap = pil_to_qpixmap(self._reconstructed_image)
        mode = self.mode_combo.currentText()

        if mode == "Side-by-Side":
            self.stack.setCurrentWidget(self._side_by_side)
            self.original_label.setPixmap(self._scaled(original_pixmap, 320))
            self.reconstructed_label.setPixmap(self._scaled(reconstructed_pixmap, 320))
        elif mode == "Top/Bottom":
            self.stack.setCurrentWidget(self._top_bottom)
            self.original_label_tb.setPixmap(self._scaled(original_pixmap, 400))
            self.reconstructed_label_tb.setPixmap(self._scaled(reconstructed_pixmap, 400))
        elif mode == "Split View":
            self.stack.setCurrentWidget(self.split_widget)
            self.split_widget.set_images(original_pixmap, reconstructed_pixmap)
        elif mode == "Overlay":
            self.stack.setCurrentWidget(self._overlay_widget)
            self.overlay_label.setPixmap(self._compose_overlay(
                original_pixmap, reconstructed_pixmap, self.overlay_opacity_slider.value() / 100.0
            ))
        elif mode == "Difference":
            self.stack.setCurrentWidget(self.difference_label)
            diff_mode = _DIFF_MODE_BY_LABEL[self.diff_mode_combo.currentText()]
            diff_image = compute_difference(self._original_image, self._reconstructed_image, diff_mode)
            self.difference_label.setPixmap(self._scaled(pil_to_qpixmap(diff_image), 400))

    @staticmethod
    def _scaled(pixmap: QPixmap, max_dim: int) -> QPixmap:
        return pixmap.scaled(max_dim, max_dim, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    @staticmethod
    def _compose_overlay(base: QPixmap, overlay: QPixmap, opacity: float) -> QPixmap:
        result = QPixmap(base.size())
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.drawPixmap(0, 0, base)
        painter.setOpacity(opacity)
        painter.drawPixmap(0, 0, overlay.scaled(base.size()))
        painter.end()
        return result
