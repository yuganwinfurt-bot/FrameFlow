"""线框图标：用内置 SVG 现场着色渲染，避免 Emoji 和系统彩色图标。"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

INK = "#405477"
MUTED = "#64748B"

_STROKE = ('stroke="{color}" stroke-width="{width}" fill="none" '
           'stroke-linecap="round" stroke-linejoin="round"')

_SVG = {
    # 文件夹
    "folder": (
        '<path d="M3 6.5a1.5 1.5 0 0 1 1.5-1.5h4l1.6 2h6.4A1.5 1.5 0 0 1 18 8.5v8.6a1.5 1.5 0 0 1-1.5 1.5h-12A1.5 1.5 0 0 1 3 17.1z"/>'
    ),
    # 文档
    "file": (
        '<path d="M6 3.2h6.4L16 7v13H6z"/><path d="M12.2 3.3V7H16"/>'
    ),
    # 齿轮
    "gear": (
        '<circle cx="12" cy="12" r="3.1"/>'
        '<path d="M12 3.2v2.2M12 18.6v2.2M3.2 12h2.2M18.6 12h2.2'
        'M5.8 5.8l1.6 1.6M16.6 16.6l1.6 1.6M18.2 5.8l-1.6 1.6M7.4 16.6l-1.6 1.6"/>'
    ),
    # 加号
    "plus": '<path d="M12 5.5v13M5.5 12h13"/>',
    # 播放
    "play": '<path d="M8.5 5.4 18 12l-9.5 6.6z"/>',
    # 关闭
    "close": '<path d="M6.4 6.4l11.2 11.2M17.6 6.4 6.4 17.6"/>',
    # 列表
    "list": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    # 复制
    "copy": (
        '<rect x="9" y="9" width="10.5" height="10.5" rx="2"/>'
        '<path d="M15 9V6.2a2 2 0 0 0-2-2H6.2a2 2 0 0 0-2 2V13a2 2 0 0 0 2 2H9"/>'
    ),
    # 导出（箭头出框）
    "export": '<path d="M12 15.5V4.8M8.4 8.2 12 4.6l3.6 3.6"/><path d="M5 14v4.4a1.6 1.6 0 0 0 1.6 1.6h10.8A1.6 1.6 0 0 0 19 18.4V14"/>',
    # 波形
    "wave": '<path d="M4 12h1.6M8 7.6v8.8M12 5.4v13.2M16 8.6v6.8M20 11h-1.4"/>',
    "layers": '<path d="m3 8 9-5 9 5-9 5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
    "sliders": '<path d="M3 7h5m4 0h9M3 17h9m4 0h5"/><circle cx="10" cy="7" r="2"/><circle cx="14" cy="17" r="2"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="m8.4 12.2 2.4 2.5 4.8-5"/>',
    "trash": '<path d="M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/>',
    "video": '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9 5 3-5 3z"/>',
    "robot": '<rect x="4" y="7" width="16" height="13" rx="3"/><path d="M12 7V3m-3 9v1m6-1v1m-5 3h4M2 11v5m20-5v5"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="9" r="1.4"/><path d="m4 17 5-5 4 3 3-4 4 4"/>',
    "timeline": '<path d="M4 4v16h16M8 15v-4m4 4V6m4 9V9m4 6v-3"/>',
    "frames": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M8 5v14m8-14v14M3 10h5m8 0h5M3 14h5m8 0h5"/>',
    "previous": '<path d="M6 6v12m11-12L8 12l9 6z"/>',
    "next": '<path d="M18 6v12M7 6l9 6-9 6z"/>',
    "pause": '<path d="M9 6v12m6-12v12"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "volume": '<path d="M3 9h4l5-4v14l-5-4H3zM15 9a4 4 0 0 1 0 6m2-9a8 8 0 0 1 0 12"/>',
    "mute": '<path d="M3 9h4l5-4v14l-5-4H3zM16 10l5 5m0-5-5 5"/>',
    "fullscreen": '<path d="M4 9V4h5m6 0h5v5m0 6v5h-5m-6 0H4v-5"/>',
    "exitFullscreen": '<path d="M4 9h5V4m6 0v5h5m0 6h-5v5m-6 0v-5H4"/>',
}

VIEWBOX = 24.0


def svg_text(name: str, color: str = INK, width: float = 1.6) -> str:
    body = _SVG[name]
    style = _STROKE.format(color=color, width=width)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            f'<g {style}>{body}</g></svg>')


@lru_cache(maxsize=64)
def _render(name: str, size: int, color: str, width: float, ratio: float) -> QPixmap:
    document = QByteArray(svg_text(name, color, width).encode("utf-8"))
    renderer = QSvgRenderer(document)
    canvas = int(round(size * ratio))
    result = QPixmap(canvas, canvas)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, canvas, canvas))
    painter.end()
    result.setDevicePixelRatio(ratio)
    return result


def pixmap(name: str, size: int = 18, color: str = INK, width: float = 1.6,
           ratio: float = 2.0) -> QPixmap:
    return QPixmap(_render(name, int(size), color, float(width), float(ratio)))


@lru_cache(maxsize=64)
def _icon_cached(name: str, size: int, color: str, width: float) -> QIcon:
    return QIcon(_render(name, int(size), color, float(width), 2.0))


def icon(name: str, size: int = 18, color: str = INK, width: float = 1.6) -> QIcon:
    return QIcon(_icon_cached(name, int(size), color, float(width)))
