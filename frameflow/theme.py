"""帧转的视觉规格：颜色、圆角、排版和两份全局样式表。

主窗口与编辑工作台共用这里的 token，避免颜色散落在各个 widget 的 paintEvent 里。
改一个色只需要改这里，两个界面会一起变。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsDropShadowEffect

# Win11 风格蓝白工作台：白卡片落在浅蓝灰底上，主色只留给主操作、进度和选中态。
TOKENS = {
    "bg": "#F0F4FD",
    "surface": "#FFFFFF",
    "surface_alt": "#F5F8FD",
    "sunken": "#EEF3FB",
    "track": "#E1E9F6",
    "border": "#D5E1F6",
    "border_soft": "#E7EEF9",
    "divider": "#E7EEF9",
    "text": "#18263D",
    "text2": "#66758C",
    "text3": "#8C9AAF",
    "accent": "#1677FF",
    "accent_hover": "#0F63DB",
    "accent_press": "#0B51B8",
    "accent_soft": "#E0EBFF",
    "accent_line": "#BDD8FF",
    "accent_text": "#0969DA",
    "success": "#0F7B0F",
    "success_soft": "#E7F4E7",
    "warning": "#9D5D00",
    "warning_soft": "#FFF4CE",
    "danger": "#C42B1C",
    "danger_soft": "#FCE8E8",
}

RADIUS = 10
RADIUS_SMALL = 6


class C:
    """按名字访问 token，方便自绘控件直接取色。"""

    bg = QColor(TOKENS["bg"])
    surface = QColor(TOKENS["surface"])
    surface_alt = QColor(TOKENS["surface_alt"])
    sunken = QColor(TOKENS["sunken"])
    track = QColor(TOKENS["track"])
    border = QColor(TOKENS["border"])
    border_soft = QColor(TOKENS["border_soft"])
    divider = QColor(TOKENS["divider"])
    text = QColor(TOKENS["text"])
    text2 = QColor(TOKENS["text2"])
    text3 = QColor(TOKENS["text3"])
    accent = QColor(TOKENS["accent"])
    accent_hover = QColor(TOKENS["accent_hover"])
    accent_press = QColor(TOKENS["accent_press"])
    accent_soft = QColor(TOKENS["accent_soft"])
    accent_line = QColor(TOKENS["accent_line"])
    accent_text = QColor(TOKENS["accent_text"])
    success = QColor(TOKENS["success"])
    success_soft = QColor(TOKENS["success_soft"])
    warning = QColor(TOKENS["warning"])
    warning_soft = QColor(TOKENS["warning_soft"])
    danger = QColor(TOKENS["danger"])
    danger_soft = QColor(TOKENS["danger_soft"])

    @staticmethod
    def hex(color: QColor) -> str:
        return color.name()


# 主窗口。Fusion 风格下 QMenuBar / QMenu / QStatusBar 都有系统底色，这里统一压平成白灰。
STYLE = """
QWidget { color: %(text)s; font-size: 13px; }
QMainWindow, QWidget#shell, QWidget#pageWorkspace { background: %(bg)s; }
QLabel[muted="true"] { color: %(text2)s; }
QLabel#brand { font-size: 20px; font-weight: 700; color: %(text)s; }
QLabel#caption { color: %(text2)s; font-size: 13px; }

QMenuBar { background: %(surface)s; border-bottom: 1px solid %(divider)s; padding: 1px 4px; }
QMenuBar::item { background: transparent; padding: 4px 10px; border-radius: 4px; color: %(text2)s; }
QMenuBar::item:selected { background: %(accent_soft)s; color: %(text)s; }
QMenu { background: %(surface)s; border: 1px solid %(border)s; padding: 4px; }
QMenu::item { background: transparent; padding: 5px 26px 5px 12px; border-radius: 4px; }
QMenu::item:selected { background: %(accent_soft)s; color: %(text)s; }
QMenu::item:disabled { color: %(text3)s; }
QMenu::separator { height: 1px; background: %(divider)s; margin: 4px 6px; }

QFrame#card { background: %(surface)s; border: 1px solid %(border)s; border-radius: 8px; }
QDialog#detailsDialog { background: %(bg)s; }
QDialog { background: %(bg)s; }
QFrame#outputLocationCard { background: #F8FAFD; border: 1px solid #DCE2EA; border-radius: 8px; }
QLabel#outputFileName { color: %(text)s; font-size: 12px; font-weight: 600; }
QFrame#header { background: #EAF0FC; border: none; border-bottom: 1px solid %(border)s; }
QFrame#panelHeader { background: transparent; border: none; border-bottom: 1px solid %(divider)s; }
QFrame#footer { background: %(surface)s; border: none; border-top: 1px solid %(divider)s; }
QFrame#bottomTaskBar { background: #F8FAFF; border: none; border-top: 1px solid %(border)s; }
QFrame#bottomTaskBar QProgressBar { background: #C9D4E9; min-height: 7px; max-height: 7px; }
QFrame#bottomTaskBar QPushButton#primary { font-size: 16px; font-weight: 700; color: #FFFFFF; }
QFrame#bottomTaskBar QPushButton#primary:disabled { color: %(text3)s; }
QLabel#taskStatus, QLabel#taskPercent { color: %(text)s; font-size: 15px; font-weight: 700; }
QFrame#queueFooter { background: transparent; border: none; border-top: 1px solid %(divider)s; }
QLabel#queueTitle { color: %(text)s; font-size: 15px; font-weight: 700; }
QLabel#queueCount { font-size: 12px; }
QFrame#inspectorActions { background: transparent; border: none; border-top: 1px solid %(divider)s; }
QFrame#dialogFooter { background: %(surface_alt)s; border: none; border-top: 1px solid %(border)s; }
QFrame#columnHeader { background: %(surface_alt)s; border: none; border-bottom: 1px solid %(divider)s; }
QLabel#columnTitle { color: %(text3)s; font-size: 12px; }
QLabel#sectionTitle { color: %(text2)s; font-size: 12px; font-weight: 600; }
QLabel#panelTitle { color: %(text)s; font-size: 14px; font-weight: 600; }
QLabel#chip { background: %(sunken)s; color: %(text2)s; border-radius: 9px; padding: 1px 9px; font-size: 12px; }
QLabel#privacy { background: %(success_soft)s; color: %(success)s; border-radius: 9px;
                 padding: 2px 10px; font-size: 12px; }
QLabel#preview { background: %(sunken)s; color: %(text3)s; border-radius: 8px;
                 border: 1px solid %(divider)s; }
QLabel#thumb { background: %(sunken)s; color: %(text3)s; border-radius: 4px; font-size: 12px; }
QWidget#fieldCell { background: transparent; }
QLabel#fieldLabel { color: %(text2)s; font-size: 12px; }
QLabel#inspectorName { color: %(text)s; font-size: 14px; font-weight: 600; }
QLabel#inspectorMeta { color: %(text2)s; font-size: 12px; }

/* 输出设置里的模块卡：白色模块与浅蓝重点模块两档，避免整片同色 */
QFrame#panelModule {
    background: %(surface)s; border: 1px solid %(border_soft)s; border-radius: 10px;
}
QFrame#panelModule[tint="mode"] { background: #E8F0FF; border: 1px solid #CFE0FF; }
QFrame#panelModule[tint="quality"] { background: #F0F1FF; border: 1px solid #D6DBFF; }
QLabel#moduleTitle { color: %(text)s; font-size: 13px; font-weight: 600; }
QLabel#moduleHint { color: %(accent_text)s; font-size: 11px; }
QLabel#rowLabel { color: %(text2)s; font-size: 12px; }
QLabel#rowHint { color: %(text3)s; font-size: 11px; }
QLabel#rowValue { color: %(text)s; font-size: 12px; padding: 4px 0; }
QLabel#estimateLabel { color: %(text2)s; font-size: 12px; }
QLabel#estimateValue { color: %(text)s; font-size: 12px; }
QLabel#estimateValueStrong { color: %(text)s; font-size: 13px; font-weight: 600; }
QLabel#estimateDelta {
    color: #0F7B0F; background: #E7F4E7; border-radius: 8px; padding: 1px 8px; font-size: 11px;
}

QScrollArea#inspectorScroll { background: transparent; border: none; }
QScrollArea#studioScroll { background: transparent; border: none; }
QScrollArea#inspectorScroll > QWidget > QWidget,
QScrollArea#detailsScroll > QWidget > QWidget { background: transparent; }
QWidget#inspectorBody { background: transparent; }

QWidget#detailsView, QWidget#detailsBody,
QWidget#detailGroups, QWidget#detailRow, QScrollArea#detailsScroll { background: transparent; }
QWidget#detailHero {
    background: #F8FAFD;
    border: 1px solid #DCE2EA;
    border-radius: 8px;
}
QLabel#detailTitle { color: %(text)s; font-size: 16px; font-weight: 600; }
QLabel#detailSub { color: %(text3)s; font-size: 12px; }
QLabel#detailSection { color: %(text3)s; font-size: 11px; font-weight: 600; letter-spacing: .4px; }
QLabel#detailLabel { color: %(text2)s; font-size: 12px; }
QLabel#detailValue { color: %(text)s; font-size: 12px; }
QLabel#detailEmpty { color: %(text3)s; font-size: 12px; }
QLabel#detailThumb {
    background: %(sunken)s;
    border: 1px solid %(border_soft)s;
    border-radius: 6px;
    color: %(text3)s;
    font-size: 11px;
    font-weight: 600;
}
QLabel#detailThumb[filled="true"] { border: none; background: %(sunken)s; }
QWidget#detailStats {
    background: %(surface_alt)s;
    border: 1px solid %(border_soft)s;
    border-radius: 6px;
}
QLabel#statValue { color: %(accent)s; font-size: 15px; font-weight: 600; }
QLabel#statLabel { color: %(text3)s; font-size: 11px; }
QFrame#statRule { background: %(border)s; border: none; }
QFrame#detailRule { background: %(border_soft)s; border: none; }

QListWidget#queue, QListWidget#previewFiles {
    background: transparent; border: none; outline: 0; padding: 0;
}
QListWidget#queue::item, QListWidget#previewFiles::item { background: transparent; border: none; }
QListWidget#queue::item:selected, QListWidget#previewFiles::item:selected { background: transparent; }

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background: %(surface)s; color: %(text)s; border: 1px solid %(border)s;
    border-radius: 6px; min-height: 24px; padding: 0px 8px;
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: #C2D3EC; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: %(accent)s; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background: %(surface_alt)s; color: %(text3)s; border-color: %(border_soft)s;
}
QComboBox { padding-right: 26px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QAbstractItemView {
    background: %(surface)s; border: 1px solid %(border)s; padding: 3px; outline: 0;
    selection-background-color: %(accent_soft)s; selection-color: %(text)s;
}
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border;
    subcontrol-position: top right; width: 16px; border: none; background: transparent; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border;
    subcontrol-position: bottom right; width: 16px; border: none; background: transparent; }

QPlainTextEdit#fileDetails {
    background: %(surface_alt)s; color: %(text)s; border: 1px solid %(border_soft)s;
    border-radius: 5px; padding: 6px 8px; font-size: 12px;
    selection-background-color: %(accent_soft)s; selection-color: %(text)s;
}
QPlainTextEdit#runStatus {
    background: %(surface_alt)s; color: %(text2)s; border: 1px solid %(border_soft)s;
    border-radius: 5px; padding: 4px 7px; font-size: 12px;
}

QProgressBar { background: %(track)s; border: 0; border-radius: 3px; min-height: 6px;
               max-height: 6px; text-align: center; }
QProgressBar::chunk { background: %(accent)s; border-radius: 3px; }

QScrollBar:vertical { background: transparent; width: 11px; margin: 2px 2px 2px 1px; }
QScrollBar::handle:vertical { background: #C4CEDB; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #A7B4C4; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 1px 2px 2px 2px; }
QScrollBar::handle:horizontal { background: #C4CEDB; border-radius: 3px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QToolTip { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; padding: 3px 6px; }
QWidget#qualityRow { background: transparent; }
QLabel#qualityTitle { color: %(text2)s; font-size: 12px; }
QLabel#qualityValue { color: %(accent)s; font-size: 12px; font-weight: 600; }
QLabel#qualityHint { color: %(text3)s; font-size: 11px; }
QSlider#qualitySlider { min-height: 20px; }
QSlider#qualitySlider::groove:horizontal { height: 4px; background: %(track)s; border-radius: 2px; }
QSlider#qualitySlider::sub-page:horizontal { background: %(accent)s; border-radius: 2px; }
QSlider#qualitySlider::add-page:horizontal { background: %(track)s; border-radius: 2px; }
QSlider#qualitySlider::handle:horizontal {
    background: #FFFFFF; width: 14px; margin: -5px 0; border: 2px solid %(accent)s;
    border-radius: 9px;
}
QSlider#qualitySlider::handle:horizontal:hover { border-color: %(accent_hover)s; }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 8px; }
QSplitter::handle:vertical { height: 8px; }
""" % TOKENS

# 编辑工作台保留浅色设置栏，播放器使用深色画布，形成清楚的视频工作区层级。
STUDIO_STYLE = """
QWidget#studio { background: %(bg)s; }
QWidget { color: %(text)s; font-size: 13px; }
QFrame[card="true"] { background: %(surface)s; border: 1px solid %(border)s; border-radius: 8px; }
QWidget#inspectorGroup { background: #F8FAFD; border: 1px solid #DCE2EA; border-radius: 8px; }
QFrame#outputLocationCard { background: #F8FAFD; border: 1px solid #DCE2EA; border-radius: 8px; }
QLabel#outputFileName { color: %(text)s; font-size: 12px; font-weight: 600; }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }
QSplitter::handle:hover, QSplitter::handle:pressed { background: %(accent_line)s; }
QLabel[muted="true"] { color: %(text2)s; }
QLabel#studioTitle { color: %(text)s; font-size: 19px; font-weight: 700; }
QLabel#studioSection { color: %(text)s; font-size: 13px; font-weight: 600; }
QLabel#studioKicker { color: %(text3)s; font-size: 11px; letter-spacing: 1px; }
QLabel#studioHint { color: %(text2)s; font-size: 11px; }
QLabel#studioBadge { color: %(text2)s; background: %(sunken)s; border-radius: 4px;
                     padding: 1px 7px; font-size: 11px; }
QFrame#videoPlayer { background: #0B111B; border: none; border-radius: 12px; }
QLabel#playerBadge { color: #F1F5FE; background: #252E3C; border-radius: 7px;
                     padding: 3px 9px; font-size: 12px; font-weight: 600; }
QLabel#videoCanvas { background: #080F1C; color: #A7B7CA; border: none;
                     border-top-left-radius: 12px; border-top-right-radius: 12px; }
QFrame#playerControls { background: #151F2D; border: none;
                        border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; }
QLabel#playerTime { color: #F1F5FE; font-size: 12px; font-family: "Segoe UI"; font-weight: 500; }
QPushButton[transport="true"] {
    background: #20334D; color: #E4EFFC; border: 1px solid #385270;
    border-radius: 7px; padding: 0; font-size: 19px; font-weight: 600;
}
QPushButton[transport="true"]:hover { background: #2C4566; border-color: #4E6F97; }
QPushButton[transport="true"]:pressed { background: #182A41; }
QPushButton[transport="true"]:disabled { background: #182334; color: #71839A; border-color: #2A3A50; }
QPushButton[transportPrimary="true"]:enabled { background: %(accent)s; color: #FFFFFF; border-color: %(accent)s; }
QPushButton[transportPrimary="true"]:hover { background: %(accent_hover)s; border-color: %(accent_hover)s; }
QPushButton[transportPrimary="true"]:pressed { background: %(accent_press)s; border-color: %(accent_press)s; }
QSlider#playerSeek { min-height: 22px; }
QSlider#playerSeek::groove:horizontal { height: 4px; background: #40536B; border-radius: 2px; }
QSlider#playerSeek::sub-page:horizontal { background: %(accent)s; border-radius: 2px; }
QSlider#playerSeek::add-page:horizontal { background: #40536B; border-radius: 2px; }
QSlider#playerSeek::handle:horizontal { background: #FFFFFF; width: 12px; margin: -5px 0;
                                          border: 2px solid %(accent)s; border-radius: 7px; }
QFrame#mediaSummary { background: #F8FAFD; border: 1px solid #DCE2EA; border-radius: 8px; }
QListWidget#previewFiles { background: transparent; border: none; outline: none; }
QFrame#mediaInfoCard { background: #F5F8FF; border: 1px solid %(border)s; border-radius: 10px; }
QFrame#mediaInfoHeader { background: #E6EEFD; border-top-left-radius: 10px; border-top-right-radius: 10px; }
QFrame#mediaInfoRule { background: #BAC9E2; border: none; }
QFrame#mediaPreviewPanel, QFrame#studioInspector {
    background: #FCFDFF; border: 1px solid %(border)s; border-radius: 14px;
}
QFrame#timelineCard { background: #F8FAFF; border: 1px solid %(border)s; border-radius: 10px; }
QTabWidget#mediaTabs::pane { background: transparent; border: none; border-top: 1px solid %(divider)s; }
QTabWidget#mediaTabs QTabBar::tab, QTabBar#timelineViews::tab {
    color: %(text)s; font-size: 14px; font-weight: 600;
    padding: 6px 13px; border-bottom: 3px solid transparent;
}
QTabWidget#mediaTabs QTabBar::tab:selected, QTabBar#timelineViews::tab:selected {
    color: %(accent_text)s; border-bottom-color: %(accent)s;
}
QTabBar#timelineViews::tab { font-size: 13px; padding: 4px 6px; }
QTabWidget#mediaTabs QTabBar#mediaTabBar[compact="true"]::tab {
    font-size: 13px; padding-left: 6px; padding-right: 6px;
}
QFrame#timelineCard QPushButton { min-height: 26px; padding: 2px 7px; }
QFrame#timelineCard QLineEdit#frameLocate { min-height: 24px; padding: 2px 6px; }
QFrame#estimateCard { background: %(surface_alt)s; border: 1px solid %(border)s; border-radius: 8px; }
QLabel#estimateTitle { color: %(text)s; font-size: 12px; font-weight: 600; }
QLabel#estimateTag { color: %(text3)s; font-size: 11px; }
QLabel#estimateLabel { color: %(text2)s; font-size: 12px; }
QLabel#estimateValue { color: %(text)s; font-size: 12px; }
QLabel#estimateDelta[tone="down"] { color: %(success)s; }
QLabel#estimateDelta[tone="up"] { color: %(warning)s; }
QLabel#mediaInfoHeading { color: %(text)s; font-size: 12px; font-weight: 700; }
QLabel#mediaInfoHint, QLabel#mediaInfoLabel { color: %(text3)s; font-size: 10px; }
QLabel#mediaInfoValue { color: %(text)s; font-size: 13px; font-weight: 500; }
QWidget#mediaInfoCell { background: #EEF3F9; border-radius: 5px; }
QTabWidget::pane { border: 1px solid %(border)s; border-radius: 8px; background: %(surface)s;
                   top: -1px; }
QTabBar::tab { background: transparent; color: %(text2)s; padding: 6px 14px; margin-right: 2px;
               border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: %(text)s; background: %(sunken)s; }
QTabBar::tab:selected { color: %(text)s; border-bottom: 2px solid %(accent)s; font-weight: 600; }
QTabBar#picturePreset { background: %(sunken)s; border-radius: 6px; padding: 2px; }
QTabBar#picturePreset::tab { background: transparent; margin: 0; padding: 4px 8px;
                             border: none; border-radius: 4px; }
QTabBar#picturePreset::tab:hover { background: %(accent_soft)s; color: %(accent)s; }
QTabBar#picturePreset::tab:selected { background: %(surface)s; color: %(accent)s;
                                      font-weight: 600; border: 1px solid %(border)s; }
QPlainTextEdit#runStatus {
    background: %(surface_alt)s; color: %(text2)s; border: 1px solid %(border_soft)s;
    border-radius: 5px; font-size: 12px; padding: 4px 7px;
}
QPlainTextEdit#fileDetails {
    background: %(surface_alt)s; color: %(text)s; border: 1px solid %(border_soft)s;
    border-radius: 6px; padding: 8px; font-size: 12px;
    selection-background-color: %(accent_soft)s; selection-color: %(text)s;
}
QPushButton {
    background: %(surface)s; color: %(text)s; border: 1px solid %(border)s;
    border-radius: 6px; padding: 5px 10px; min-height: 28px; font-size: 13px;
}
QPushButton:hover { background: #EEF3F9; border-color: #C4CEDB; }
QPushButton:pressed { background: #E4E9F0; border-color: #A7B4C4; }
QPushButton:disabled { background: %(surface_alt)s; color: %(text3)s; border-color: %(border_soft)s; }
QPushButton[toolLink="true"] {
    background: transparent; color: %(accent)s; border: none; border-bottom: 1px solid transparent;
    padding: 3px 6px;
}
QPushButton[toolLink="true"]:hover { background: %(accent_soft)s; }
QPushButton[toolLink="true"]:disabled { background: transparent; color: %(text3)s; }
QComboBox, QSpinBox, QDoubleSpinBox {
    background: #F8FAFF; color: #243047; border: 1px solid #D9E4F4; border-radius: 7px;
    min-height: 28px; padding: 0px 9px; font-size: 13px;
}
QLineEdit#frameLocate, QLineEdit#frameField {
    background: %(surface)s; color: %(text)s; border: 1px solid %(border)s;
    border-radius: 5px; min-height: 26px; padding: 2px 8px;
}
QLineEdit#frameLocate:focus, QLineEdit#frameField:focus { border-color: %(accent)s; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background: %(surface_alt)s; color: %(text3)s; border-color: %(border_soft)s;
}
QComboBox { padding-right: 25px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QAbstractItemView { background: %(surface)s; border: 1px solid %(border)s;
    selection-background-color: %(accent_soft)s; selection-color: %(text)s; outline: 0; padding: 3px; }
QListWidget { background: %(surface)s; border: 1px solid %(border)s; border-radius: 6px; outline: 0; }
QListWidget::item { padding: 3px 7px; }
QListWidget::item:selected { background: %(accent_soft)s; color: %(text)s; }
QProgressBar { background: %(track)s; border: 0; border-radius: 3px; text-align: center;
               max-height: 6px; font-size: 11px; color: %(text2)s; }
QProgressBar::chunk { background: %(accent)s; border-radius: 3px; }
QMenu { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; padding: 4px; }
QMenu::item { background: transparent; padding: 5px 22px 5px 10px; border-radius: 4px; }
QMenu::item:selected { background: %(accent_soft)s; color: %(text)s; }
QMenu::item:disabled { color: %(text3)s; }
QMenu::separator { height: 1px; background: %(divider)s; margin: 4px 5px; }
QScrollBar:vertical { background: transparent; width: 11px; margin: 2px; }
QScrollBar::handle:vertical { background: #C4CEDB; border-radius: 3px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #A7B4C4; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
""" % TOKENS


def app_palette():
    """Fusion 的调色板：下拉弹出层、提示、滚动条这些走 palette 的地方也变成深色。

    只改 QSS 不够——QComboBox 的弹出列表是独立窗口，背景由 palette 决定，
    不设这个就会出现「白底浅灰字」的下拉框。
    """
    from PySide6.QtGui import QPalette

    palette = QPalette()
    window, base, alt = C.surface, C.surface, C.sunken
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, C.text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, alt)
    palette.setColor(QPalette.ColorRole.Text, C.text)
    palette.setColor(QPalette.ColorRole.Button, window)
    palette.setColor(QPalette.ColorRole.ButtonText, C.text)
    palette.setColor(QPalette.ColorRole.ToolTipBase, base)
    palette.setColor(QPalette.ColorRole.ToolTipText, C.text)
    palette.setColor(QPalette.ColorRole.Highlight, C.accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, C.text3)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, C.text3)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, C.text3)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, C.text3)
    return palette


def app_icon(size: int = 256) -> QIcon:
    """任务栏和标题栏图标：蓝底圆角方块里放一个白色播放三角。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    body = QRectF(0, 0, size, size).adjusted(size * .04, size * .04, -size * .04, -size * .04)
    path = QPainterPath()
    path.addRoundedRect(body, size * .22, size * .22)
    painter.fillPath(path, C.accent)
    play = QPainterPath()
    play.moveTo(size * .40, size * .30)
    play.lineTo(size * .72, size * .50)
    play.lineTo(size * .40, size * .70)
    play.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.fillPath(play, QColor("#FFFFFF"))
    painter.end()
    return QIcon(pixmap)


def brand_mark(size: int = 30) -> QPixmap:
    """标题区左侧的小方块标志，比完整图标更紧凑。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(C.accent)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * .26, size * .26)
    play = QPainterPath()
    play.moveTo(size * .40, size * .30)
    play.lineTo(size * .72, size * .50)
    play.lineTo(size * .40, size * .70)
    play.closeSubpath()
    painter.fillPath(play, QColor("#FFFFFF"))
    painter.end()
    return pixmap


def shadow(widget, blur: int = 18, dy: int = 2, alpha: int = 26) -> QGraphicsDropShadowEffect:
    """给卡片加一层很轻的投影：卡片浮在浅灰底上，但不会抢视线。"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(23, 43, 77, alpha))
    widget.setGraphicsEffect(effect)
    return effect


def chevron(painter: QPainter, center: QPointF, color: QColor, direction: str = "down", size: float = 4.0):
    """统一的折线箭头，按钮、下拉框和目录选择共用。"""
    painter.save()
    painter.setPen(QPen(color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    if direction == "down":
        points = [QPointF(center.x() - size, center.y() - size / 2),
                  QPointF(center.x(), center.y() + size / 2),
                  QPointF(center.x() + size, center.y() - size / 2)]
    else:
        points = [QPointF(center.x() - size / 2, center.y() - size),
                  QPointF(center.x() + size / 2, center.y()),
                  QPointF(center.x() - size / 2, center.y() + size)]
    painter.drawPolyline(points)
    painter.restore()


# 预览输出设置使用独立选择器，滚动区只让视口和内容底板透明。
EXPORT_STYLE = """
QFrame#exportSettingsPanel {
    background: transparent;
    border: none;
}
QWidget#sharedExportBody { background: transparent; border: none; }
QScrollArea#studioScroll, QWidget#exportViewport, QWidget#exportBody { background: transparent; border: none; }
QWidget#exportPanelHeader QLabel#cardTitle { font-size: 16px; font-weight: 700; }
QFrame#exportModeCard {
    background: #E4EEFF;
    border: 1px solid #BCD3FB;
    border-radius: 10px;
}
QFrame#qualityCard {
    background: #E9ECFF;
    border: 1px solid #D5DDFC;
    border-radius: 10px;
}
QFrame#estimatedSizeCard {
    background: #F3F7FF;
    border: 1px solid #DCE8FA;
    border-radius: 10px;
}
QFrame#estimatedSizeHeader {
    background: #DCE8FB;
    border: none;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
}
QPushButton#estimateLimitToggle {
    background: transparent; color: %(accent_text)s; border: none; border-radius: 5px;
    padding: 0 5px; min-height: 18px; font-size: 12px; font-weight: 500;
}
QPushButton#estimateLimitToggle:hover { background: #CFE0FC; color: %(accent)s; }
QPushButton#estimateLimitToggle:pressed { background: #BDD4FA; }
QPushButton#estimateLimitToggle:focus { outline: none; border: none; }
QFrame#saveDirectoryCard {
    background: transparent;
    border: none;
}
QFrame#outputPathCard {
    background: #F8FAFF;
    border: 1px solid %(border)s;
    border-radius: 8px;
}
QWidget#paramRow, QWidget#paramCaption { background: transparent; }
QWidget#paramRow QComboBox, QWidget#paramRow QSpinBox { min-height: 20px; padding: 0 6px; }
QFrame#exportSettingsPanel QComboBox, QWidget#codecDisplay {
    min-height: 28px; background: #F8FAFF; border: 1px solid #CDDAF1; border-radius: 7px;
}
QFrame#exportSettingsPanel QComboBox { padding: 0 25px 0 9px; }
QFrame#exportSettingsPanel QComboBox:hover { border-color: #96B8F0; }
QFrame#exportSettingsPanel QComboBox:focus { border-color: %(accent)s; }
QFrame#exportSettingsPanel QComboBox:disabled { background: %(sunken)s; color: %(text3)s; }
QLabel#cardTitle { color: %(text)s; font-size: 14px; font-weight: 600; }
QLabel#cardHint { color: %(text2)s; font-size: 12px; }
QLabel#paramLabel { color: %(text)s; font-size: 13px; font-weight: 600; }
QLabel#paramValue { color: %(text)s; font-size: 13px; font-weight: 500; }
QLabel#paramHint { color: %(text2)s; font-size: 12px; }
QLabel#estimateLabel { color: %(text2)s; font-size: 12px; font-weight: 400; }
QLabel#estimateValue { color: %(text)s; font-size: 13px; font-weight: 500; }
QLabel#estimateValueStrong { color: %(text)s; font-size: 14px; font-weight: 600; }
QLabel#estimateDelta {
    color: #16803D; background: #DCF8E2; border-radius: 7px;
    padding: 1px 8px; font-size: 12px; font-weight: 500;
}
QLabel#estimateDelta[tone="up"] { color: #9D5D00; background: #FFF4CE; }
QLabel#estimateDelta[tone="same"] { color: %(text2)s; background: %(track)s; }
QFrame#audioSelectionCard, QFrame#pictureExportCard {
    background: #F8FAFF; border: 1px solid #D8E4F7; border-radius: 10px;
}
QLabel#audioSelectionStatus, QLabel#pictureCaption { color: %(text2)s; font-size: 12px; }
QLabel#audioFieldLabel, QLabel#pictureFieldLabel { color: %(text2)s; font-size: 12px; font-weight: 400; }
QLabel#pictureHint { color: %(text2)s; font-size: 11px; }
QLabel#pictureValue { color: %(text)s; font-size: 13px; font-weight: 500; }
QWidget#audioInsertRow, QWidget#audioAdvancedOptions { background: transparent; }
QPushButton#secondaryAction {
    background: #F5F8FF; color: %(accent_text)s; border: 1px solid #D3E0F4;
    border-radius: 7px; padding: 3px 9px; min-height: 24px; font-size: 12px; font-weight: 500;
}
QPushButton#secondaryAction:hover { background: #EAF1FF; border-color: #AFC9F1; }
QPushButton#secondaryAction:pressed { background: #DCE8FB; }
QFrame#outputPathCard:hover { border-color: #9EBBE8; }
QLineEdit#outputPath {
    background: transparent; color: %(text)s; border: none; border-radius: 0;
    padding: 0 2px; min-height: 24px; selection-background-color: #C8DDFF; selection-color: #17345A;
}
QLineEdit#outputPath:hover, QLineEdit#outputPath:focus {
    background: transparent; border: none; outline: none;
}
QLabel#qualityTitle { color: %(text)s; font-size: 14px; font-weight: 600; }
QLabel#qualityValue { color: %(text)s; font-size: 13px; font-weight: 500; }
QLabel#qualityHint { color: %(text2)s; font-size: 12px; }
QLabel#qualityHintEnd { color: %(text2)s; font-size: 11px; }
QFrame#qualityCard QLabel#qualityValue {
    background: #F7F9FF; border: 1px solid #CAD7F2; border-radius: 7px;
    padding: 3px 10px; font-weight: 600;
}
QSlider#qualitySlider { min-height: 20px; }
QSlider#qualitySlider::groove:horizontal { height: 4px; background: #C9D4E9; border-radius: 2px; }
QSlider#qualitySlider::sub-page:horizontal { background: %(accent)s; border-radius: 2px; }
QSlider#qualitySlider::handle:horizontal { background: #FFFFFF; width: 13px; margin: -6px 0;
                                            border: 2px solid %(accent)s; border-radius: 8px; }
"""

CHOICE_STYLE = """
QListView#choiceList {
    background: #FFFFFF; border: 1px solid #D9E4F4; border-radius: 8px;
    outline: 0; padding: 4px; selection-background-color: #E4EEFF; selection-color: #243047;
}
QListView#choiceList::item { border: none; border-radius: 7px; padding: 0; }
QListView#choiceList::item:hover { background: #F1F6FF; }
QListView#choiceList::item:selected { background: #E4EEFF; color: #243047; }
QFrame#conversionInspector {
    background: #FCFDFF; border: 1px solid %(border)s; border-radius: 14px;
}
QLabel#conversionSettingsHeading { font-size: 16px; font-weight: 700; }
QPushButton#primary:enabled { color: #FFFFFF; }
QWidget#exportViewport { background: transparent; border: none; }
"""

DETAIL_STYLE = """
QWidget#detailHero { background: #EFF5FF; border: 1px solid %(border)s; border-radius: 12px; }
QWidget#detailStats { background: #F5F8FF; border: 1px solid %(border)s; border-radius: 9px; }
QFrame#detailGroup { background: #FCFDFF; border: 1px solid %(border)s; border-radius: 10px; }
QLabel#detailSection { color: %(text)s; font-size: 13px; font-weight: 700; }
QLabel#detailPath { color: %(text2)s; font-size: 12px; }
"""

STYLE += (CHOICE_STYLE + DETAIL_STYLE) % TOKENS
STUDIO_STYLE += (EXPORT_STYLE + CHOICE_STYLE + DETAIL_STYLE + """
QDialog#fullscreenPlayer { background: #080F1C; }
QLabel#waveCanvas, QLabel#imageCanvas {
    background: #F1F6FF; border: 1px solid %(border)s; border-radius: 10px;
}
QFrame#waveToolbar { background: #EDF4FF; border: 1px solid %(border)s; border-radius: 8px; }
QListWidget#audioClips { background: #F8FAFF; border: 1px solid %(border)s; border-radius: 8px; }
QSlider#timelineZoom::groove:horizontal { background: %(track)s; height: 4px; border-radius: 2px; }
QSlider#timelineZoom::handle:horizontal {
    background: %(accent)s; width: 11px; margin: -4px 0; border-radius: 5px;
}
""") % TOKENS


_POPUP_QSS = """
QFrame#batchMenu {
    background: %(surface)s;
    border: 1px solid #D6E3F7;
    border-radius: 10px;
}
QFrame#batchMenu QScrollArea, QFrame#batchMenu QScrollArea > QWidget > QWidget {
    background: transparent; border: none;
}
QLabel#menuGroup {
    color: #7184A3; font-size: 12px; font-weight: 600;
    padding-left: 12px; padding-top: 6px; padding-bottom: 2px;
}
QFrame#menuDivider { background: #E8EFF9; border: none; }
QPushButton#menuChoice {
    background: transparent; border: none; border-radius: 6px;
    color: #182B49; font-size: 13px;
    text-align: left; padding-left: 12px; padding-right: 12px;
    min-height: 34px;
}
QPushButton#menuChoice:hover { background: #F0F5FF; }
QPushButton#menuChoice[selected="true"] {
    background: #E3EDFF; color: #1677FF; font-weight: 600;
}
QFrame#batchMenu QScrollBar:vertical {
    background: transparent; width: 4px; margin: 4px 2px 4px 0;
}
QFrame#batchMenu QScrollBar::handle:vertical {
    background: #C7D8F2; border-radius: 2px; min-height: 24px;
}
QFrame#batchMenu QScrollBar::add-line:vertical,
QFrame#batchMenu QScrollBar::sub-line:vertical { height: 0; }
QFrame#batchMenu QScrollBar::add-page:vertical,
QFrame#batchMenu QScrollBar::sub-page:vertical { background: transparent; }

QFrame#volumePopup {
    background: %(surface)s; border: 1px solid #D6E3F7; border-radius: 10px;
}
QFrame#volumePopup QLabel#volumePercent {
    color: #182B49; font-size: 12px; font-weight: 600;
}
QSlider#volumeSlider::groove:vertical {
    background: #DEE7F5; width: 4px; border-radius: 2px;
}
QSlider#volumeSlider::sub-page:vertical {
    background: #1677FF; width: 4px;
}
QSlider#volumeSlider::handle:vertical {
    background: #FFFFFF; border: 2px solid #1677FF;
    width: 12px; height: 12px; margin: 0 -6px; border-radius: 8px;
}
"""

STYLE = STYLE + (_POPUP_QSS % TOKENS)
STUDIO_STYLE = STUDIO_STYLE + (_POPUP_QSS % TOKENS)
