"""
icons.py — minimalist line icons (SF Symbols / iOS style), rendered from inline
SVG so there are no external assets and the color/size match the glass theme.
"""

from functools import lru_cache
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

# 24×24 viewBox, 1.6px rounded strokes. {c} = stroke colour, {f} = fill.
_SVG = {
    "folder": '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.2H19.5A1.5 1.5 0 0 1 21 9.7v8.3A1.5 1.5 0 0 1 19.5 19.5H4.5A1.5 1.5 0 0 1 3 18Z" fill="none" stroke="{c}" stroke-width="1.6" stroke-linejoin="round"/>',
    "camera": '<rect x="3" y="7" width="18" height="13" rx="2.4" fill="none" stroke="{c}" stroke-width="1.6"/><path d="M8.5 7l1.3-2.2h4.4L15.5 7" fill="none" stroke="{c}" stroke-width="1.6" stroke-linejoin="round"/><circle cx="12" cy="13.3" r="3.3" fill="none" stroke="{c}" stroke-width="1.6"/>',
    "gear": '<circle cx="12" cy="12" r="3" fill="none" stroke="{c}" stroke-width="1.6"/><path d="M12 3.5v2.2M12 18.3v2.2M20.5 12h-2.2M5.7 12H3.5M18 6l-1.6 1.6M7.6 16.4 6 18M18 18l-1.6-1.6M7.6 7.6 6 6" stroke="{c}" stroke-width="1.6" stroke-linecap="round"/>',
    "play": '<path d="M8 5.5v13l11-6.5Z" fill="{f}" stroke="{f}" stroke-width="1.4" stroke-linejoin="round"/>',
    "stop": '<rect x="6.5" y="6.5" width="11" height="11" rx="2.6" fill="{f}"/>',
    "mic": '<rect x="9" y="3.5" width="6" height="11" rx="3" fill="none" stroke="{c}" stroke-width="1.6"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5" stroke="{c}" stroke-width="1.6" stroke-linecap="round" fill="none"/>',
}


@lru_cache(maxsize=64)
def icon(name: str, color: str = "#1d1d1f", size: int = 20) -> QIcon:
    """Return a crisp QIcon for the given name/colour."""
    body = _SVG[name].replace("{c}", color).replace("{f}", color)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'{body}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(size * 2, size * 2)          # 2× for retina crispness
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p)
    p.end()
    pm.setDevicePixelRatio(2.0)
    return QIcon(pm)
