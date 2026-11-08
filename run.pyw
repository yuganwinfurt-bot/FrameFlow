import os
import sys


def main() -> int:
    if os.name == "nt":
        from ctypes import windll

        windll.kernel32.FreeConsole()

    from PySide6.QtCore import QLibraryInfo, QTranslator
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from frameflow.media import MediaError, Tools
    from frameflow.theme import app_palette
    from frameflow.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("FrameFlow")
    app.setStyle("Fusion")
    app.setPalette(app_palette())
    if os.name == "nt":
        app.setFont(QFont("Microsoft YaHei UI", 9))
    translation = QTranslator(app)
    if translation.load("qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translation)
    tools = None
    error = ""
    try:
        tools = Tools.find()
    except MediaError as exc:
        error = str(exc)
    window = MainWindow(tools, error)
    window.show()
    if len(sys.argv) > 1:
        window.add_paths(sys.argv[1:])
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
