"""
Quick Masking editor (spec §27-28): a lightweight, Photoshop-like mask
painter over a part's crop. Brush/eraser/rectangle/ellipse painting
happens directly on an internal grayscale QImage mask buffer (0 =
unmasked, 255 = fully masked); non-interactive operations (invert,
feather, expand, contract) delegate to image.mask's PIL-based
functions, converting the buffer to/from PIL only for those calls.
Pixel buffer <-> array conversions use numpy (vectorized) rather than
per-pixel Python loops, since a per-pixel loop over a full-resolution
mask would be far too slow for interactive repainting.

UNTESTED IN THIS ENVIRONMENT (see ui/canvas.py's note).
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Optional

import numpy as np
from PIL import Image
from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

import image.mask as mask_ops

_MASK_TINT = QColor(255, 60, 60)  # overlay color; alpha comes from the mask itself


class MaskTool(Enum):
    BRUSH = auto()
    ERASER = auto()
    RECTANGLE = auto()
    ELLIPSE = auto()


def qimage_mask_to_pil(qimage: QImage) -> Image.Image:
    gray = qimage.convertToFormat(QImage.Format.Format_Grayscale8)
    width, height = gray.width(), gray.height()
    stride = gray.bytesPerLine()
    ptr = gray.bits()
    ptr.setsize(stride * height)
    buf = np.frombuffer(bytes(ptr), dtype=np.uint8).reshape((height, stride))[:, :width]
    return Image.fromarray(buf.copy(), mode="L")


def pil_mask_to_qimage(pil_image: Image.Image) -> QImage:
    gray = pil_image.convert("L")
    data = gray.tobytes("raw", "L")
    qimage = QImage(data, gray.width, gray.height, gray.width, QImage.Format.Format_Grayscale8)
    return qimage.copy()  # detach from the temporary `data` buffer


def _mask_overlay_qimage(mask_image: QImage, color: QColor) -> QImage:
    """An ARGB image whose per-pixel alpha equals the mask's grayscale
    value -- a true alpha-mask overlay (not a flat-colored rectangle)."""
    gray = mask_image.convertToFormat(QImage.Format.Format_Grayscale8)
    width, height = gray.width(), gray.height()
    stride = gray.bytesPerLine()
    ptr = gray.bits()
    ptr.setsize(stride * height)
    alpha = np.frombuffer(bytes(ptr), dtype=np.uint8).reshape((height, stride))[:, :width]

    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 0] = color.red()
    rgba[..., 1] = color.green()
    rgba[..., 2] = color.blue()
    rgba[..., 3] = alpha
    rgba = np.ascontiguousarray(rgba)

    qimage = QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888)
    return qimage.copy()


class _MaskCanvas(QWidget):
    mask_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._base_pixmap: Optional[QPixmap] = None
        self._mask_image: Optional[QImage] = None
        self.tool = MaskTool.BRUSH
        self.brush_size = 24
        self._drawing = False
        self._last_pos: Optional[QPoint] = None
        self._drag_start: Optional[QPoint] = None
        self._preview_rect: Optional[QRect] = None
        self.setMinimumSize(200, 200)

    def load(self, base_image: Image.Image, existing_mask: Optional[Image.Image] = None) -> None:
        from ui.panels.preview_panel import pil_to_qpixmap  # local import: avoid a top-level circular import
        self._base_pixmap = pil_to_qpixmap(base_image)
        if existing_mask is not None:
            self._mask_image = pil_mask_to_qimage(existing_mask)
        else:
            self._mask_image = QImage(base_image.width, base_image.height, QImage.Format.Format_Grayscale8)
            self._mask_image.fill(0)
        self.setFixedSize(self._base_pixmap.size())
        self.update()

    def current_mask(self) -> Optional[Image.Image]:
        return qimage_mask_to_pil(self._mask_image) if self._mask_image is not None else None

    def clear(self) -> None:
        if self._mask_image is not None:
            self._mask_image.fill(0)
            self.update()
            self.mask_changed.emit()

    def fill(self) -> None:
        if self._mask_image is not None:
            self._mask_image.fill(255)
            self.update()
            self.mask_changed.emit()

    def apply_op(self, op_name: str, amount: int = 0) -> None:
        current = self.current_mask()
        if current is None:
            return
        ops = {
            "invert": lambda m: mask_ops.invert_mask(m),
            "feather": lambda m: mask_ops.feather_mask(m, amount),
            "expand": lambda m: mask_ops.expand_mask(m, amount),
            "contract": lambda m: mask_ops.contract_mask(m, amount),
        }
        if op_name not in ops:
            raise ValueError(f"Unknown mask operation: {op_name}")
        self._mask_image = pil_mask_to_qimage(ops[op_name](current))
        self.update()
        self.mask_changed.emit()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        if self._base_pixmap is not None:
            painter.drawPixmap(0, 0, self._base_pixmap)
        if self._mask_image is not None:
            painter.drawImage(0, 0, _mask_overlay_qimage(self._mask_image, _MASK_TINT))
        if self._preview_rect is not None:
            pen = QPen(QColor(255, 255, 255))
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(self._preview_rect)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._mask_image is None:
            return
        pos = event.position().toPoint()
        if self.tool in (MaskTool.BRUSH, MaskTool.ERASER):
            self._drawing = True
            self._last_pos = pos
            self._paint_stroke(pos, pos)
        elif self.tool in (MaskTool.RECTANGLE, MaskTool.ELLIPSE):
            self._drag_start = pos
            self._preview_rect = QRect(pos, pos)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if self.tool in (MaskTool.BRUSH, MaskTool.ERASER) and self._drawing:
            self._paint_stroke(self._last_pos, pos)
            self._last_pos = pos
        elif self.tool in (MaskTool.RECTANGLE, MaskTool.ELLIPSE) and self._drag_start is not None:
            self._preview_rect = QRect(self._drag_start, pos).normalized()
            self.update()

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        if self.tool in (MaskTool.BRUSH, MaskTool.ERASER):
            self._drawing = False
            self._last_pos = None
            self.mask_changed.emit()
        elif self.tool in (MaskTool.RECTANGLE, MaskTool.ELLIPSE) and self._preview_rect is not None:
            self._commit_shape(self._preview_rect)
            self._preview_rect = None
            self._drag_start = None
            self.update()
            self.mask_changed.emit()

    def _paint_stroke(self, p1: QPoint, p2: QPoint) -> None:
        if self._mask_image is None:
            return
        painter = QPainter(self._mask_image)
        color = Qt.GlobalColor.white if self.tool == MaskTool.BRUSH else Qt.GlobalColor.black
        pen = QPen(color)
        pen.setWidth(self.brush_size)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(p1, p2)
        painter.end()
        self.update()

    def _commit_shape(self, rect: QRect) -> None:
        if self._mask_image is None:
            return
        painter = QPainter(self._mask_image)
        painter.setBrush(Qt.GlobalColor.white)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.tool == MaskTool.RECTANGLE:
            painter.drawRect(rect)
        else:
            painter.drawEllipse(rect)
        painter.end()


class MaskEditorPanel(QWidget):
    mask_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()

        self.canvas = _MaskCanvas()
        self._tool_group = QButtonGroup(self)
        self._tool_buttons = {}
        for tool, label in [(MaskTool.BRUSH, "Brush"), (MaskTool.ERASER, "Eraser"),
                             (MaskTool.RECTANGLE, "Rectangle"), (MaskTool.ELLIPSE, "Ellipse")]:
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, t=tool: self._set_tool(t))
            self._tool_group.addButton(button)
            self._tool_buttons[tool] = button
            toolbar.addWidget(button)
        self._tool_buttons[MaskTool.BRUSH].setChecked(True)

        toolbar.addWidget(QLabel("Size"))
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(1, 200)
        self.size_slider.setValue(24)
        self.size_slider.valueChanged.connect(self._on_size_changed)
        toolbar.addWidget(self.size_slider)

        self.invert_button = QPushButton("Invert")
        self.feather_button = QPushButton("Feather")
        self.expand_button = QPushButton("Expand")
        self.contract_button = QPushButton("Contract")
        self.clear_button = QPushButton("Clear")
        self.fill_button = QPushButton("Fill")
        self.invert_button.clicked.connect(lambda: self._run_op("invert"))
        self.feather_button.clicked.connect(lambda: self._run_op("feather"))
        self.expand_button.clicked.connect(lambda: self._run_op("expand"))
        self.contract_button.clicked.connect(lambda: self._run_op("contract"))
        self.clear_button.clicked.connect(self.canvas.clear)
        self.fill_button.clicked.connect(self.canvas.fill)
        for button in (self.invert_button, self.feather_button, self.expand_button,
                       self.contract_button, self.clear_button, self.fill_button):
            toolbar.addWidget(button)

        layout.addLayout(toolbar)
        layout.addWidget(self.canvas)
        self.canvas.mask_changed.connect(self.mask_changed.emit)

    def load(self, base_image: Image.Image, existing_mask: Optional[Image.Image] = None) -> None:
        self.canvas.load(base_image, existing_mask)

    def current_mask(self) -> Optional[Image.Image]:
        return self.canvas.current_mask()

    def _set_tool(self, tool: MaskTool) -> None:
        self.canvas.tool = tool

    def _on_size_changed(self, value: int) -> None:
        self.canvas.brush_size = value

    def _run_op(self, op_name: str) -> None:
        amount = self.size_slider.value() if op_name in ("feather", "expand", "contract") else 0
        self.canvas.apply_op(op_name, amount)
