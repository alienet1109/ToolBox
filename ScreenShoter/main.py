import sys
import os
import json
import time
import threading
import winsound
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Tuple

from PIL import Image

from PySide6.QtCore import Qt, QRect, QPoint, Signal, QObject
from PySide6.QtGui import QPainter, QPen, QColor, QPixmap, QImage, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QFileDialog,
    QTabWidget,
    QGroupBox,
    QRadioButton,
    QButtonGroup,
    QMessageBox,
    QCheckBox,
    QFormLayout,
    QScrollArea,
)

import keyboard  # 全局快捷键


CONFIG_FILE_NAME = "config.json"


@dataclass
class RegionsConfig:
    region_large: Optional[Tuple[int, int, int, int]] = None
    region_small: Optional[Tuple[int, int, int, int]] = None


@dataclass
class AppConfig:
    save_dir: Optional[str] = None
    mode: str = "with_header"  # "with_header" or "normal"
    hotkey_start: str = "F2"   # 清空缓存（开始新一轮）键
    hotkey_capture: str = "F3"
    hotkey_stop: str = "F4"
    only_clipboard: bool = False
    regions: RegionsConfig = field(default_factory=RegionsConfig)

    def to_dict(self):
        d = asdict(self)
        d["regions"] = {
            "region_large": self.regions.region_large,
            "region_small": self.regions.region_small,
        }
        return d

    @staticmethod
    def from_dict(data: dict) -> "AppConfig":
        regions_data = data.get("regions", {})
        regions = RegionsConfig(
            region_large=tuple(regions_data["region_large"]) if regions_data.get("region_large") else None,
            region_small=tuple(regions_data["region_small"]) if regions_data.get("region_small") else None,
        )
        return AppConfig(
            save_dir=data.get("save_dir"),
            mode=data.get("mode", "with_header"),
            hotkey_start=data.get("hotkey_start", "F2"),
            hotkey_capture=data.get("hotkey_capture", "F3"),
            hotkey_stop=data.get("hotkey_stop", "F4"),
            only_clipboard=data.get("only_clipboard", False),
            regions=regions,
        )


class RegionSelector(QWidget):
    regionSelected = Signal(tuple)
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint
            | Qt.FramelessWindowHint
            | Qt.Tool
        )
        self.setWindowState(Qt.WindowFullScreen)
        self.origin = QPoint()
        self.current = QPoint()
        self.selecting = False
        self.cross_pos = QPoint(-1, -1)
        screen = QGuiApplication.primaryScreen()
        self.background = screen.grabWindow(0) if screen is not None else None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            self.origin = pos
            self.current = pos
            self.selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self.selecting:
            self.current = pos
        self.cross_pos = pos
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.selecting:
            self.current = event.position().toPoint()
            self.selecting = False
            rect = self.normalized_rect(self.origin, self.current)
            # 如果基本没有拖动（认为是单击），则作为取消处理，与 Esc 一致
            if rect.width() < 5 and rect.height() < 5:
                self.close()
                return
            self.regionSelected.emit((rect.x(), rect.y(), rect.width(), rect.height()))
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            # 取消选择
            self.close()

    def closeEvent(self, event):
        # 通知主窗口选择器已关闭（无论是选完还是取消）
        self.closed.emit()
        super().closeEvent(event)

    @staticmethod
    def normalized_rect(p1: QPoint, p2: QPoint) -> QRect:
        x1, y1 = p1.x(), p1.y()
        x2, y2 = p2.x(), p2.y()
        left = min(x1, x2)
        top = min(y1, y2)
        right = max(x1, x2)
        bottom = max(y1, y2)
        return QRect(left, top, right - left, bottom - top)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 先画截图背景，如果没有则保持默认背景
        if self.background is not None:
            painter.drawPixmap(0, 0, self.background)

        # 画选区矩形
        if self.selecting or self.current != QPoint():
            rect = self.normalized_rect(self.origin, self.current)
            # 选区内轻微半透明高亮，方便看出区域
            painter.fillRect(rect, QColor(0, 0, 0, 50))
            pen = QPen(QColor(0, 255, 0), 2)
            painter.setPen(pen)
            painter.drawRect(rect)

        # 十字光标
        if self.cross_pos.x() >= 0 and self.cross_pos.y() >= 0:
            pen_cross = QPen(QColor(255, 0, 0, 180), 1)
            painter.setPen(pen_cross)
            painter.drawLine(0, self.cross_pos.y(), self.width(), self.cross_pos.y())
            painter.drawLine(self.cross_pos.x(), 0, self.cross_pos.x(), self.height())


class HotkeySignals(QObject):
    start_signal = Signal()
    capture_signal = Signal()
    stop_signal = Signal()
    hotkey_captured = Signal(str, object)  # hotkey, target QLineEdit


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SerifShoter 截图工具")

        self.config_path = os.path.join(self._app_dir(), CONFIG_FILE_NAME)
        self.config = self.load_config()

        # 截图缓存与状态
        self.frames: List[Image.Image] = []
        self.first_shot_done = False  # 带头图模式下是否已截过头图
        self.region_selector = None  # 保持引用，避免被回收
        self.capturing_hotkey = False
        self.cache_info_label = None
        self.cache_thumb_container = None
        self.cache_thumb_layout = None
        self.shot_sound = None  # 自定义音效
        self.hotkey_dirty = False
        self.hotkey_hint_label = None

        # 热键信号桥
        self.hotkey_signals = HotkeySignals()
        self.hotkey_signals.start_signal.connect(self.on_hotkey_start)
        self.hotkey_signals.capture_signal.connect(self.on_hotkey_capture)
        self.hotkey_signals.stop_signal.connect(self.on_hotkey_stop)
        self.hotkey_signals.hotkey_captured.connect(self.on_hotkey_captured)
        self.hotkey_handles = []

        self.init_ui()
        self.init_sound()
        self.register_hotkeys()

    @staticmethod
    def _app_dir() -> str:
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    # ---------------- 音效初始化 ----------------
    def init_sound(self):
        """尝试加载 shot.mp3 作为完成提示音，若失败则退回系统提示音。"""
        sound_path = os.path.join(self._app_dir(), "shot.mp3")
        if not os.path.exists(sound_path):
            return
        try:
            # 延迟导入 pygame，避免在缺少依赖时直接崩溃
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            self.shot_sound = pygame.mixer.Sound(sound_path)
        except Exception as e:
            print("初始化音效失败：", e, file=sys.stderr)
            self.shot_sound = None

    # ---------------- 配置读写 ----------------
    def load_config(self) -> AppConfig:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return AppConfig.from_dict(data)
            except Exception:
                pass
        return AppConfig()

    def save_config(self):
        try:
            data = self.config.to_dict()
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"写入配置文件失败：{e}")

    # ---------------- UI ----------------
    def init_ui(self):
        tab_widget = QTabWidget()
        tab_widget.addTab(self._build_capture_tab(), "截图与保存")
        tab_widget.addTab(self._build_hotkey_tab(), "快捷键设置")
        self.setCentralWidget(tab_widget)
        self.resize(600, 400)
        # 初始化一次缓存显示
        self.update_cache_view()

    @staticmethod
    def _wrap_row_widget(row_layout: QHBoxLayout) -> QWidget:
        """将行布局包装为一个 QWidget，便于放入表单布局中。"""
        w = QWidget()
        w.setLayout(row_layout)
        return w

    def _build_capture_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 模式选择
        mode_group_box = QGroupBox("截图模式")
        mode_layout = QHBoxLayout(mode_group_box)
        self.rb_with_header = QRadioButton("带头图模式（CG + 台词）")
        self.rb_normal = QRadioButton("普通模式（仅台词区域）")
        mode_btn_group = QButtonGroup(mode_group_box)
        mode_btn_group.addButton(self.rb_with_header)
        mode_btn_group.addButton(self.rb_normal)
        mode_layout.addWidget(self.rb_with_header)
        mode_layout.addWidget(self.rb_normal)
        layout.addWidget(mode_group_box)

        if self.config.mode == "with_header":
            self.rb_with_header.setChecked(True)
        else:
            self.rb_normal.setChecked(True)

        self.rb_with_header.toggled.connect(self.on_mode_changed)

        # 截图范围设置
        region_box = QGroupBox("截图范围")
        region_layout = QFormLayout(region_box)

        self.btn_set_large = QPushButton("设定大范围（头图）")
        self.btn_set_small = QPushButton("设定小范围（文本）")
        self.btn_set_large.clicked.connect(lambda: self.start_region_select("large"))
        self.btn_set_small.clicked.connect(lambda: self.start_region_select("small"))

        self.label_large_region = QLineEdit()
        self.label_large_region.setReadOnly(True)
        self.label_small_region = QLineEdit()
        self.label_small_region.setReadOnly(True)

        region_layout.addRow(self.btn_set_large, self.label_large_region)
        region_layout.addRow(self.btn_set_small, self.label_small_region)
        layout.addWidget(region_box)

        self.update_region_labels()

        # 保存路径设置
        save_box = QGroupBox("保存设置")
        save_layout = QFormLayout(save_box)

        self.edit_save_dir = QLineEdit()
        if self.config.save_dir:
            self.edit_save_dir.setText(self.config.save_dir)
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(self.on_browse_folder)
        h_save = QHBoxLayout()
        h_save.addWidget(self.edit_save_dir)
        h_save.addWidget(btn_browse)

        self.checkbox_only_clipboard = QCheckBox("仅复制到剪贴板，不保存到磁盘")
        self.checkbox_only_clipboard.setChecked(self.config.only_clipboard)

        save_layout.addRow(QLabel("默认保存文件夹："), h_save)
        save_layout.addRow(self.checkbox_only_clipboard)

        layout.addWidget(save_box)

        # 截图缓存预览
        cache_box = QGroupBox("当前截图缓存")
        cache_vlayout = QVBoxLayout(cache_box)

        self.cache_info_label = QLabel("当前截图数：0")
        cache_vlayout.addWidget(self.cache_info_label)

        self.cache_thumb_container = QWidget()
        self.cache_thumb_layout = QHBoxLayout(self.cache_thumb_container)
        self.cache_thumb_layout.setContentsMargins(0, 0, 0, 0)
        self.cache_thumb_layout.setSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.cache_thumb_container)
        scroll.setFixedHeight(120)

        cache_vlayout.addWidget(scroll)

        layout.addWidget(cache_box)

        layout.addStretch()
        return widget

    def _build_hotkey_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        info_label = QLabel("说明：快捷键基于 keyboard 库，全局生效。\n"
                            "建议使用功能键（如 F2/F3/F4）或组合键（如 ctrl+alt+a）。")
        layout.addWidget(info_label)

        # 未保存提示（红字），仅在有改动且未保存时显示
        self.hotkey_hint_label = QLabel("快捷键已修改，请点击下方“保存并应用”后才会生效。")
        self.hotkey_hint_label.setStyleSheet("color: red;")
        self.hotkey_hint_label.hide()
        layout.addWidget(self.hotkey_hint_label)

        form = QFormLayout()
        self.edit_hotkey_start = QLineEdit(self.config.hotkey_start)
        self.edit_hotkey_capture = QLineEdit(self.config.hotkey_capture)
        self.edit_hotkey_stop = QLineEdit(self.config.hotkey_stop)

        # 监听输入变化，标记为“有未保存改动”
        self.edit_hotkey_start.textChanged.connect(self.mark_hotkey_dirty)
        self.edit_hotkey_capture.textChanged.connect(self.mark_hotkey_dirty)
        self.edit_hotkey_stop.textChanged.connect(self.mark_hotkey_dirty)

        # 为每个快捷键行增加“录制”按钮，通过监听键盘获取组合键
        row_start = QHBoxLayout()
        row_start.addWidget(self.edit_hotkey_start)
        btn_record_start = QPushButton("录制")
        btn_record_start.clicked.connect(lambda: self.record_hotkey(self.edit_hotkey_start))
        row_start.addWidget(btn_record_start)

        row_capture = QHBoxLayout()
        row_capture.addWidget(self.edit_hotkey_capture)
        btn_record_capture = QPushButton("录制")
        btn_record_capture.clicked.connect(lambda: self.record_hotkey(self.edit_hotkey_capture))
        row_capture.addWidget(btn_record_capture)

        row_stop = QHBoxLayout()
        row_stop.addWidget(self.edit_hotkey_stop)
        btn_record_stop = QPushButton("录制")
        btn_record_stop.clicked.connect(lambda: self.record_hotkey(self.edit_hotkey_stop))
        row_stop.addWidget(btn_record_stop)

        form.addRow("开始/清空键：", self._wrap_row_widget(row_start))
        form.addRow("截图键：", self._wrap_row_widget(row_capture))
        form.addRow("停止/拼接保存键：", self._wrap_row_widget(row_stop))

        layout.addLayout(form)

        btn_save = QPushButton("保存并应用快捷键")
        btn_save.clicked.connect(self.on_save_hotkeys_clicked)
        layout.addWidget(btn_save)

        layout.addStretch()
        return widget

    def update_region_labels(self):
        if self.config.regions.region_large:
            x, y, w, h = self.config.regions.region_large
            self.label_large_region.setText(f"{x}, {y}, {w}, {h}")
        else:
            self.label_large_region.setText("未设置")

        if self.config.regions.region_small:
            x, y, w, h = self.config.regions.region_small
            self.label_small_region.setText(f"{x}, {y}, {w}, {h}")
        else:
            self.label_small_region.setText("未设置")

    # ---------------- 模式切换 ----------------
    def on_mode_changed(self, checked: bool):
        self.config.mode = "with_header" if checked else "normal"
        self.save_config()
        # 切换模式时清空当前会话，避免混乱
        self.clear_frames()

    # ---------------- 范围选择 ----------------
    def start_region_select(self, which: str):
        # which: "large" or "small"
        self.hide()
        # 保存为成员，防止被 GC 回收导致窗口一闪而过
        self.region_selector = RegionSelector()

        def on_selected(rect):
            x, y, w, h = rect
            if w <= 0 or h <= 0:
                QMessageBox.warning(self, "无效区域", "选择的区域无效，请重新选择。")
                return
            if which == "large":
                self.config.regions.region_large = (x, y, w, h)
            else:
                self.config.regions.region_small = (x, y, w, h)
            self.update_region_labels()
            self.save_config()

        # 无论是正常选择完成还是按 ESC 关闭，窗口关闭时都回到主界面
        def on_selector_closed():
            self.region_selector = None
            if not self.isVisible():
                self.showNormal()
                self.activateWindow()

        self.region_selector.regionSelected.connect(on_selected)
        self.region_selector.closed.connect(on_selector_closed)
        self.region_selector.show()

    # ---------------- 保存路径 ----------------
    def on_browse_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "选择保存文件夹", self.config.save_dir or self._app_dir())
        if directory:
            self.edit_save_dir.setText(directory)
            self.config.save_dir = directory
            self.save_config()

    # ---------------- 热键处理 ----------------
    def register_hotkeys(self):
        # 清理旧的
        for h in self.hotkey_handles:
            try:
                keyboard.remove_hotkey(h)
            except Exception:
                pass
        self.hotkey_handles.clear()

        # 注册新的
        try:
            h_start = keyboard.add_hotkey(self.config.hotkey_start, lambda: self.hotkey_signals.start_signal.emit())
            h_capture = keyboard.add_hotkey(self.config.hotkey_capture, lambda: self.hotkey_signals.capture_signal.emit())
            h_stop = keyboard.add_hotkey(self.config.hotkey_stop, lambda: self.hotkey_signals.stop_signal.emit())
            self.hotkey_handles.extend([h_start, h_capture, h_stop])
        except Exception as e:
            QMessageBox.warning(self, "快捷键注册失败", f"注册全局快捷键失败：\n{e}\n"
                                                      f"可能需要以管理员权限运行程序。")

    def on_save_hotkeys_clicked(self):
        # 先读取用户输入的新热键字符串
        new_start = self.edit_hotkey_start.text().strip() or "F2"
        new_capture = self.edit_hotkey_capture.text().strip() or "F3"
        new_stop = self.edit_hotkey_stop.text().strip() or "F4"

        # 记住旧设置，方便恢复
        old_start = self.config.hotkey_start
        old_capture = self.config.hotkey_capture
        old_stop = self.config.hotkey_stop

        # 先验证新热键是否合法（通过 keyboard 尝试注册再立即移除）
        if not self.validate_hotkeys(new_start, new_capture, new_stop):
            # 如果旧配置本身也非法，则退回默认 F2/F3/F4
            if not self.validate_hotkeys(old_start, old_capture, old_stop, silent=True):
                QMessageBox.warning(
                    self,
                    "快捷键已重置",
                    "检测到原有快捷键配置也不合法，已自动恢复为默认设置：F2 / F3 / F4。"
                )
                self.config.hotkey_start = "F2"
                self.config.hotkey_capture = "F3"
                self.config.hotkey_stop = "F4"
                self.edit_hotkey_start.setText("F2")
                self.edit_hotkey_capture.setText("F3")
                self.edit_hotkey_stop.setText("F4")
                self.save_config()
                self.register_hotkeys()
            else:
                # 无效时还原文本框为旧值
                self.edit_hotkey_start.setText(old_start)
                self.edit_hotkey_capture.setText(old_capture)
                self.edit_hotkey_stop.setText(old_stop)
            # 恢复为旧值/默认后视为“已同步”，不再提示未保存
            self.hotkey_dirty = False
            if self.hotkey_hint_label is not None:
                self.hotkey_hint_label.hide()
            return

        # 通过验证后再真正写入配置
        self.config.hotkey_start = new_start
        self.config.hotkey_capture = new_capture
        self.config.hotkey_stop = new_stop
        self.config.only_clipboard = self.checkbox_only_clipboard.isChecked()
        self.config.save_dir = self.edit_save_dir.text().strip() or None
        self.save_config()
        self.register_hotkeys()
        QMessageBox.information(self, "已保存", "快捷键和保存设置已保存并应用。")
        # 保存成功后清除未保存标记
        self.hotkey_dirty = False
        if self.hotkey_hint_label is not None:
            self.hotkey_hint_label.hide()

    # ------------- 快捷键录制（监听键盘更新文本框） -------------
    def record_hotkey(self, target_edit: QLineEdit):
        if self.capturing_hotkey:
            return
        self.capturing_hotkey = True

        def worker():
            try:
                # 读取组合键字符串，例如 "ctrl+alt+a"
                hotkey = keyboard.read_hotkey(suppress=False)
                self.hotkey_signals.hotkey_captured.emit(hotkey, target_edit)
            except Exception as e:
                print("录制快捷键失败：", e, file=sys.stderr)
                self.hotkey_signals.hotkey_captured.emit("", target_edit)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def on_hotkey_captured(self, hotkey: str, target_edit: QLineEdit):
        if hotkey:
            target_edit.setText(hotkey)
        self.capturing_hotkey = False
        # textChanged 已经会标记 dirty，这里无需额外处理

    def validate_hotkeys(self, start: str, capture: str, stop: str, silent: bool = False) -> bool:
        """验证热键：1) 三个键不能重复；2) 通过 keyboard 实际尝试注册。"""
        # 不允许三个热键有任何重复
        if len({start, capture, stop}) < 3:
            if not silent:
                QMessageBox.warning(self, "快捷键无效", "开始键、截图键、停止键不能相同，请设置为不同的键。")
            return False

        # 再通过 keyboard 库实际尝试注册热键来验证格式是否合法
        temp_ids = []
        try:
            temp_ids.append(keyboard.add_hotkey(start, lambda: None))
            temp_ids.append(keyboard.add_hotkey(capture, lambda: None))
            temp_ids.append(keyboard.add_hotkey(stop, lambda: None))
        except Exception as e:
            # 清理已注册的部分热键
            for h in temp_ids:
                try:
                    keyboard.remove_hotkey(h)
                except Exception:
                    pass
            if not silent:
                QMessageBox.warning(self, "快捷键无效", f"快捷键设置不合法，请重新输入。\n\n错误信息：{e}")
            return False

        # 全部注册成功后立刻移除，只作为验证
        for h in temp_ids:
            try:
                keyboard.remove_hotkey(h)
            except Exception:
                pass
        return True

    def mark_hotkey_dirty(self, *_):
        """标记快捷键设置有未保存改动，并显示红字提示。"""
        self.hotkey_dirty = True
        if self.hotkey_hint_label is not None:
            self.hotkey_hint_label.show()

    # ---------------- 截图核心逻辑 ----------------
    def clear_frames(self):
        self.frames.clear()
        self.first_shot_done = False
        self.update_cache_view()

    def on_hotkey_start(self):
        # 清空缓存，不做拼接保存
        self.clear_frames()

    def on_hotkey_capture(self):
        try:
            if self.config.mode == "with_header":
                self.capture_with_header_mode()
            else:
                self.capture_normal_mode()
        except Exception as e:
            # 避免在游戏中弹出窗口干扰，这里只在控制台打印
            print("截图失败：", e, file=sys.stderr)

    def on_hotkey_stop(self):
        if not self.frames:
            return
        try:
            combined = self.combine_images(self.frames)
            self.copy_to_clipboard(combined)
            if not self.config.only_clipboard:
                self.save_image_to_disk(combined)
            # 成功完成拼接与复制/保存后给出非打扰式提示
            self.notify_capture_done()
        except Exception as e:
            print("拼接/保存失败：", e, file=sys.stderr)
        finally:
            self.clear_frames()

    def notify_capture_done(self):
        """在停止键完成拼接保存后给出提示音，不弹出窗口、不影响前台程序。"""
        # 优先使用自定义 shot.mp3 的前 0.05~0.1 秒（这里取约 0.08 秒）
        if self.shot_sound is not None:
            try:
                import pygame
                channel = self.shot_sound.play()
                if channel is not None:
                    def stopper():
                        time.sleep(0.5)
                        try:
                            channel.stop()
                        except Exception:
                            pass
                    threading.Thread(target=stopper, daemon=True).start()
                return
            except Exception as e:
                print("播放自定义音效失败：", e, file=sys.stderr)

        # 回退到系统提示音
        try:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            QApplication.beep()

    def capture_with_header_mode(self):
        if not self.config.regions.region_large or not self.config.regions.region_small:
            QMessageBox.warning(self, "未设置范围", "带头图模式需要先设置大范围和小范围。")
            return
        if not self.first_shot_done:
            # 先截大范围
            x, y, w, h = self.config.regions.region_large
            img = self.grab_region(x, y, w, h)
            if img:
                self.frames.append(img)
                self.first_shot_done = True
                self.update_cache_view()
        else:
            # 再截小范围
            x, y, w, h = self.config.regions.region_small
            img = self.grab_region(x, y, w, h)
            if img:
                self.frames.append(img)
                self.update_cache_view()

    def capture_normal_mode(self):
        if not self.config.regions.region_small:
            QMessageBox.warning(self, "未设置范围", "普通模式需要先设置小范围。")
            return
        x, y, w, h = self.config.regions.region_small
        img = self.grab_region(x, y, w, h)
        if img:
            self.frames.append(img)
            self.update_cache_view()

    @staticmethod
    def grab_region(x: int, y: int, w: int, h: int) -> Optional[Image.Image]:
        if w <= 0 or h <= 0:
            return None
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        # 使用 Qt 自己的截图接口，坐标系与选择器一致，避免高 DPI 缩放问题
        pixmap = screen.grabWindow(0, x, y, w, h)
        if pixmap.isNull():
            return None
        qimg = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
        width = qimg.width()
        height = qimg.height()
        # 直接从 QImage 拿字节数据，避免对 bits()/memoryview 调用 setsize 带来的兼容性问题
        buffer = qimg.bits().tobytes()
        img = Image.frombuffer("RGBA", (width, height), buffer, "raw", "RGBA", 0, 1)
        return img.convert("RGB")

    @staticmethod
    def combine_images(images: List[Image.Image]) -> Image.Image:
        if not images:
            raise ValueError("没有图片可拼接")

        # 全部转为 RGB，避免模式不一致
        imgs = [im.convert("RGB") for im in images]
        widths = [im.width for im in imgs]
        heights = [im.height for im in imgs]
        max_width = max(widths)
        total_height = sum(heights)

        # 背景填充为黑色
        combined = Image.new("RGB", (max_width, total_height), (0, 0, 0))

        y_offset = 0
        for im in imgs:
            combined.paste(im, (0, y_offset))
            y_offset += im.height

        return combined

    def copy_to_clipboard(self, img: Image.Image):
        # PIL.Image -> QImage -> QPixmap -> 剪贴板
        img_rgba = img.convert("RGBA")
        data = img_rgba.tobytes("raw", "RGBA")
        qimg = QImage(data, img_rgba.width, img_rgba.height, QImage.Format_RGBA8888)
        pix = QPixmap.fromImage(qimg)
        QApplication.clipboard().setPixmap(pix)

    @staticmethod
    def pil_to_qpixmap(img: Image.Image, max_size: Tuple[int, int] = (120, 120)) -> QPixmap:
        """将 PIL Image 转为缩略 QPixmap，用于界面预览。"""
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        thumb = img.copy()
        thumb.thumbnail(max_size)
        data = thumb.tobytes("raw", "RGBA")
        qimg = QImage(data, thumb.width, thumb.height, QImage.Format_RGBA8888)
        return QPixmap.fromImage(qimg)

    def update_cache_view(self):
        """更新 GUI 中的缓存数量和缩略图显示。"""
        if self.cache_info_label is None or self.cache_thumb_layout is None:
            return

        count = len(self.frames)
        self.cache_info_label.setText(f"当前截图数：{count}")

        # 清空旧的缩略图控件
        while self.cache_thumb_layout.count():
            item = self.cache_thumb_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # 为每张截图生成缩略图（最多显示前 20 张，避免过多占用界面）
        max_show = 20
        for idx, img in enumerate(self.frames[:max_show]):
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            pix = self.pil_to_qpixmap(img, max_size=(100, 100))
            label.setPixmap(pix)
            label.setToolTip(f"第 {idx + 1} 张")
            self.cache_thumb_layout.addWidget(label)

        if count > max_show:
            more_label = QLabel(f"... 共 {count} 张")
            self.cache_thumb_layout.addWidget(more_label)

    def save_image_to_disk(self, img: Image.Image):
        if not self.config.save_dir:
            return
        os.makedirs(self.config.save_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        filename = f"{timestamp}.png"
        path = os.path.join(self.config.save_dir, filename)
        img.save(path, format="PNG")

    def closeEvent(self, event):
        # 退出前，尝试清除热键
        for h in self.hotkey_handles:
            try:
                keyboard.remove_hotkey(h)
            except Exception:
                pass
        self.hotkey_handles.clear()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

