#!/usr/bin/env python3
"""PromptForge — локальный Prompt Engineering Agent.

Точка входа приложения. Запускает GUI (PySide6), инициализирует
конфигурацию, логирование и менеджеры Core-слоя.
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.utils import paths
from app.utils.config import Config
from app.utils.i18n import set_language
from app.utils.log import get_logger, setup_logging
from app.ui.main_window import MainWindow


def main() -> int:
    paths.ensure_dirs()
    setup_logging()
    log = get_logger("main")
    log.info("PromptForge starting")

    config = Config()
    set_language(config.get("ui.language", "ru"))

    app = QApplication(sys.argv)
    app.setApplicationName("PromptForge")
    app.setOrganizationName("PromptForge")
    app.setStyle("Fusion")
    from app.ui import theme
    theme.apply_theme(config.get("ui.theme", "dark"), app)

    # Однократно: запуск Strata через run-iq2_xs.bat (путь и аргументы можно изменить в Настройки → Strata)
    if not config.get("strata.launcher_set", False):
        config.set("strata.executable", r"C:\STRATA_AI\Strata\run-iq2_xs.bat", False)
        config.set("strata.arguments", [], False)
        config.set("strata.launcher_set", True)

    from app.ui import anim
    anim.set_enabled(bool(config.get("ui.animations", True)))
    window = MainWindow(config)
    window.resize(1500, 940)
    if anim.enabled():
        window.setWindowOpacity(0.0)      # без вспышки: проявляем сразу после показа
    window.show()
    anim.fade_window(window, 320)

    if config.get("strata.auto_start", True):
        window.start_strata()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
