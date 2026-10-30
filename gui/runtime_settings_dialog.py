"""运行参数对话框：在 GUI 中修改 engine/config.yml 的高频配置。

仅暴露 ``src.runtime_settings.SETTING_SPECS`` 中声明的参数；保存时定点改值，
保留配置文件里的中文注释，并在当前进程内热重载引擎配置。
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
)

from src.log_manager import log
from src.runtime_settings import SETTING_SPECS, config_yml_path, read_settings, save_settings


class RuntimeSettingsDialog(QDialog):
    """运行参数设置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("运行参数设置")
        self.setMinimumWidth(520)
        self._path = config_yml_path()
        self._values = read_settings(self._path)
        self._widgets: dict[str, object] = {}
        self._build_ui()

    # ── 界面 ─────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        hint = QLabel(f"配置来源: {self._path}\n保存后写入该文件并立即生效。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8b949e;font-size:11px;")
        root.addWidget(hint)

        groups: dict[str, QFormLayout] = {}
        for spec in SETTING_SPECS:
            group = spec.get("group", "其它")
            if group not in groups:
                box = QGroupBox(group)
                form = QFormLayout(box)
                groups[group] = form
                root.addWidget(box)
            widget = self._make_widget(spec)
            self._widgets[spec["key"]] = widget
            label = QLabel(spec["label"])
            if spec.get("help"):
                label.setToolTip(spec["help"])
                widget.setToolTip(spec["help"])
            groups[group].addRow(label, widget)

        root.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("start_btn")
        save.setMinimumWidth(90)
        save.clicked.connect(self._on_save)
        btn_row.addWidget(cancel)
        btn_row.addWidget(save)
        root.addLayout(btn_row)

    def _make_widget(self, spec: dict):
        value = self._values.get(spec["key"], spec["default"])
        kind = spec["type"]
        if kind == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(value))
            return widget
        if kind == "choice":
            widget = QComboBox()
            for label, data in spec["options"]:
                widget.addItem(label, data)
            idx = widget.findData(value)
            widget.setCurrentIndex(idx if idx >= 0 else 0)
            return widget
        if kind == "int":
            widget = QSpinBox()
            widget.setRange(int(spec.get("min", 0)), int(spec.get("max", 99)))
            widget.setValue(int(value))
            return widget
        widget = QDoubleSpinBox()
        widget.setDecimals(2)
        widget.setRange(float(spec.get("min", 0.0)), float(spec.get("max", 99.0)))
        widget.setSingleStep(float(spec.get("step", 0.1)))
        widget.setValue(float(value))
        return widget

    def _collect(self) -> dict:
        values = {}
        for spec in SETTING_SPECS:
            widget = self._widgets[spec["key"]]
            kind = spec["type"]
            if kind == "bool":
                values[spec["key"]] = widget.isChecked()
            elif kind == "choice":
                values[spec["key"]] = widget.currentData()
            elif kind == "int":
                values[spec["key"]] = widget.value()
            else:
                values[spec["key"]] = widget.value()
        return values

    # ── 保存 ─────────────────────────────────────────────
    def _on_save(self):
        values = self._collect()
        try:
            save_settings(self._path, values)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", f"写入 config.yml 失败:\n{exc}")
            return
        self._reload_engine()
        self.accept()

    def _reload_engine(self):
        """让当前进程内的 GloConfig 立即使用新配置。"""
        try:
            engine_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine")
            if engine_dir not in sys.path:
                sys.path.insert(0, engine_dir)
            from config import GloConfig
            GloConfig.config_path = self._path
            GloConfig.init_yaml_data()
        except Exception as exc:
            log.warning(f"引擎配置热重载失败，重启后生效: {exc}")
