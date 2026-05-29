from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from printer_nester.ui.viewport import POINTS_PER_INCH, PrintViewport


RULER_STEPS_INCHES = (0.125, 0.25, 0.5, 1, 2, 5, 10, 25, 50, 100, 250, 500)


class RulerWidget(QWidget):
    def __init__(self, orientation: Qt.Orientation, viewport: PrintViewport) -> None:
        super().__init__()

        self._orientation = orientation
        self._viewport = viewport

        if orientation == Qt.Orientation.Horizontal:
            self.setFixedHeight(28)
        else:
            self.setFixedWidth(42)

        self._viewport.viewport_changed.connect(self.update)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#eceff1"))
        painter.setPen(QPen(QColor("#b6bdc4"), 1))

        if self._orientation == Qt.Orientation.Horizontal:
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
            self._draw_horizontal_ticks(painter)
        else:
            painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
            self._draw_vertical_ticks(painter)

    def _draw_horizontal_ticks(self, painter: QPainter) -> None:
        scene_left = self._viewport.mapToScene(0, 0).x()
        scene_right = self._viewport.mapToScene(self._viewport.viewport().width(), 0).x()
        self._draw_ticks(painter, scene_left, scene_right, self.width(), horizontal=True)

    def _draw_vertical_ticks(self, painter: QPainter) -> None:
        scene_top = self._viewport.mapToScene(0, 0).y()
        scene_bottom = self._viewport.mapToScene(0, self._viewport.viewport().height()).y()
        self._draw_ticks(painter, scene_top, scene_bottom, self.height(), horizontal=False)

    def _draw_ticks(self, painter: QPainter, start_points: float, end_points: float, length: int, horizontal: bool) -> None:
        pixels_per_inch = max(1.0, self._viewport.transform().m11() * POINTS_PER_INCH)
        minor_step_in = self._step_for_min_pixels(pixels_per_inch, minimum_pixels=10)
        label_step_in = self._step_for_min_pixels(pixels_per_inch, minimum_pixels=64)
        minor_step_points = minor_step_in * POINTS_PER_INCH

        first_tick = math.floor(start_points / minor_step_points) * minor_step_points
        tick = first_tick

        painter.setFont(QFont("Segoe UI", 8))
        tick_pen = QPen(QColor("#60666d"), 1)
        text_pen = QPen(QColor("#4f565d"), 1)

        while tick <= end_points:
            pixel = self._scene_value_to_ruler_pixel(tick, start_points, end_points, length)
            inch_value = tick / POINTS_PER_INCH
            is_labeled_tick = self._is_multiple(inch_value, label_step_in)
            is_whole_inch = self._is_multiple(inch_value, 1)

            tick_length = 14 if is_labeled_tick else 10 if is_whole_inch else 6
            painter.setPen(tick_pen)
            if horizontal:
                painter.drawLine(pixel, self.height(), pixel, self.height() - tick_length)
            else:
                painter.drawLine(self.width(), pixel, self.width() - tick_length, pixel)

            if is_labeled_tick:
                painter.setPen(text_pen)
                label = str(round(inch_value))
                if horizontal:
                    painter.drawText(pixel + 3, 11, label)
                else:
                    painter.save()
                    painter.translate(12, pixel - 3)
                    painter.rotate(-90)
                    painter.drawText(0, 0, label)
                    painter.restore()

            tick += minor_step_points

    def _scene_value_to_ruler_pixel(self, value: float, start: float, end: float, length: int) -> int:
        if math.isclose(start, end):
            return 0

        return round(((value - start) / (end - start)) * length)

    def _step_for_min_pixels(self, pixels_per_inch: float, minimum_pixels: float) -> float:
        for step in RULER_STEPS_INCHES:
            if step * pixels_per_inch >= minimum_pixels:
                return step

        return RULER_STEPS_INCHES[-1]

    def _is_multiple(self, value: float, step: float) -> bool:
        if step == 0:
            return False

        return math.isclose(value / step, round(value / step), abs_tol=0.001)
