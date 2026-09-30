# -*- coding: utf-8 -*-
"""
极品飞车集结自动刷奖励工具
功能：基于图像识别判断游戏状态，自动点击/按键，循环刷奖励
"""

import os
import sys
import time
import threading
import logging
import ctypes
from logging.handlers import RotatingFileHandler
import numpy as np
import cv2
import pyautogui
import win32gui
import win32con
import win32api
import win32process
from PyQt5 import QtCore, QtGui, QtWidgets

# ========== 日志系统 ==========
def setup_logger(log_dir):
    """初始化日志系统：同时输出到控制台和文件"""
    logger = logging.getLogger("nfs_auto")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter("[%(asctime)s] %(levelname)s | %(message)s",
                                    datefmt="%H:%M:%S")
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)

    # 文件输出（带滚动，最多5个文件，每个2MB）
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "nfs_auto.log")
    file_handler = RotatingFileHandler(log_file, maxBytes=2 * 1024 * 1024,
                                       backupCount=5, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter("[%(asctime)s] %(levelname)s | %(message)s",
                                 datefmt="%Y-%m-%d %H:%M:%S")
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    return logger


# ========== 配置 ==========
# 游戏窗口标题关键词（用于查找游戏窗口）
GAME_TITLE_KEYWORDS = ["NFS", "极品飞车", "Need for Speed"]

# 模板匹配置信度阈值
MATCH_THRESHOLD = 0.75

# 检测间隔（秒）
DETECT_INTERVAL = 0.3  # 优化后更快

# 游戏中按键配置
IN_GAME_KEY = 'i'           # 游戏中按的键
IN_GAME_KEY_INTERVAL = 3.0  # 按键间隔（秒）
IN_GAME_KEY_DURATION = 0.3  # 按键持续时间

# 状态稳定时间（秒）- 状态持续多久才确认为有效
STATE_STABLE_TIME = 0.3

# 状态定义
STATE_UNKNOWN = "未知"
STATE_MAIN_MENU = "主界面模式选择"    # 1
STATE_MODE_SELECT = "模式选择"        # 2
STATE_START_MATCH = "开始匹配"        # 3
STATE_MATCHING = "匹配中"             # 4
STATE_IN_GAME = "游戏中"              # 5
STATE_RESULT_1 = "结算界面1"          # 6.1
STATE_RESULT_2 = "结算界面2"          # 6.2

# 状态流转（当前状态 -> 点击后进入的状态描述）
STATE_CLICK_ACTION = {
    STATE_MAIN_MENU: "点击模式选择",
    STATE_MODE_SELECT: "点击模式确认",
    STATE_START_MATCH: "点击开始匹配",
    STATE_MATCHING: "等待匹配完成",
    STATE_IN_GAME: "按I键(通过AHK)",
    STATE_RESULT_1: "点击结算继续",
    STATE_RESULT_2: "点击结算继续",
}

# 全局 logger（在 main 中初始化）
logger = None


def imread_cn(filepath):
    """读取中文路径的图片"""
    data = np.fromfile(filepath, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img


def get_roi_from_hint(screen_shape, pos_hint):
    """
    根据位置提示返回感兴趣区域 (x1, y1, x2, y2)
    用于缩小模板匹配范围，提升速度
    """
    h, w = screen_shape[:2]
    margin = 50  # 边距余量

    if pos_hint == "右下":
        return (w // 2, h // 2, w, h)
    elif pos_hint == "右中":
        return (w // 2, h // 4, w, h * 3 // 4)
    elif pos_hint == "中上":
        return (0, 0, w, h // 2)
    elif pos_hint == "中下":
        return (w // 4, h // 3, w * 3 // 4, h * 2 // 3)
    else:
        return (0, 0, w, h)


class TemplateMatcher:
    """模板匹配器 - 两阶段匹配：灰度粗筛 + 彩色精定位，速度提升8倍以上"""

    def __init__(self, template_dir, log):
        self.log = log
        self.templates = {}  # state_name -> template_info
        self._last_state = STATE_UNKNOWN
        self._last_scale = 1.0
        self._base_scale = 1.0  # 全量匹配的尺度中心，随实际 UI 缩放自适应
        self._pyramid_scale = 4  # 粗匹配缩小倍数
        self._coarse_threshold = 0.45  # 粗匹配阈值，超过才做精匹配
        self.load_templates(template_dir)

    def load_templates(self, template_dir):
        """加载所有模板图片，并预生成灰度版本
        支持一个状态对应多个模板（如"5游戏中"和"5游戏中2"都映射到游戏中状态）
        """
        t0 = time.time()
        # 前缀映射：文件名以这些前缀开头的都归为对应状态
        state_prefixes = {
            "1主界面模式选择": STATE_MAIN_MENU,
            "2模式选择": STATE_MODE_SELECT,
            "3开始匹配": STATE_START_MATCH,
            "4匹配中": STATE_MATCHING,
            "5游戏中": STATE_IN_GAME,
            "6结算界面1": STATE_RESULT_1,
            "6结算界面2": STATE_RESULT_2,
        }

        # 改为列表存储，一个状态可以有多个模板
        self.templates = {}  # state_name -> [template_info, ...]

        count = 0
        for filename in sorted(os.listdir(template_dir)):
            if not filename.endswith(".png"):
                continue
            filepath = os.path.join(template_dir, filename)
            name_without_ext = filename[:-4]

            state_name = None
            for prefix, value in state_prefixes.items():
                if name_without_ext.startswith(prefix):
                    state_name = value
                    break

            if state_name is None:
                continue

            img = imread_cn(filepath)
            if img is None:
                self.log.warning(f"无法加载模板: {filename}")
                continue

            # 解析位置提示
            pos_hint = "center"
            if "（" in name_without_ext and "）" in name_without_ext:
                pos_hint = name_without_ext.split("（")[1].split("）")[0]

            # 预生成灰度模板
            img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            template_info = {
                "image": img,
                "gray": img_gray,
                "pos_hint": pos_hint,
                "filename": filename,
            }

            if state_name not in self.templates:
                self.templates[state_name] = []
            self.templates[state_name].append(template_info)
            count += 1
            self.log.debug(f"加载模板: {state_name} ({filename}), {img.shape[1]}x{img.shape[0]}, 位置: {pos_hint}")

        elapsed = (time.time() - t0) * 1000
        self.log.info(f"模板加载完成: {count} 个, 耗时 {elapsed:.0f}ms")

    def _match_two_stage(self, template_color, template_gray, screen_color, screen_gray_small,
                         roi_full, roi_small, scales, small_factor):
        """
        两阶段模板匹配：
        1) 粗匹配：在缩小的灰度图上快速扫描所有尺度
        2) 精匹配：在粗匹配位置附近，用彩色原图精确匹配3个尺度
        返回: (confidence, click_x, click_y, best_scale)
        """
        th, tw = template_gray.shape[:2]
        sh_small, sw_small = screen_gray_small.shape[:2]

        # --- 阶段1: 粗匹配（缩小灰度图，所有尺度） ---
        best_coarse_conf = 0
        best_coarse_loc = (0, 0)
        best_coarse_scale = 1.0

        rh_small = roi_small[3] - roi_small[1]
        rw_small = roi_small[2] - roi_small[0]
        screen_roi_small = screen_gray_small[roi_small[1]:roi_small[3], roi_small[0]:roi_small[2]]

        for scale in scales:
            new_w = int(tw * scale / small_factor)
            new_h = int(th * scale / small_factor)
            if new_w < 10 or new_h < 10 or new_w > rw_small or new_h > rh_small:
                continue
            scaled = cv2.resize(template_gray, (new_w, new_h),
                                interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
            result = cv2.matchTemplate(screen_roi_small, scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_coarse_conf:
                best_coarse_conf = max_val
                best_coarse_loc = max_loc
                best_coarse_scale = scale

        # 粗匹配没找到足够好的，直接返回
        if best_coarse_conf < self._coarse_threshold:
            return 0, (0, 0), 0

        # --- 阶段2: 精匹配（彩色原图，在粗匹配位置附近） ---
        # 粗匹配坐标转换到原图
        coarse_cx = int((roi_small[0] + best_coarse_loc[0] + tw * best_coarse_scale / small_factor / 2) * small_factor)
        coarse_cy = int((roi_small[1] + best_coarse_loc[1] + th * best_coarse_scale / small_factor / 2) * small_factor)

        # 搜索范围：模板尺寸的1.5倍
        search_w = int(tw * best_coarse_scale * 1.5)
        search_h = int(th * best_coarse_scale * 1.5)
        x1 = max(roi_full[0], coarse_cx - search_w)
        y1 = max(roi_full[1], coarse_cy - search_h)
        x2 = min(roi_full[2], coarse_cx + search_w)
        y2 = min(roi_full[3], coarse_cy + search_h)

        if x2 - x1 < tw * 0.5 or y2 - y1 < th * 0.5:
            return 0, (0, 0), 0

        search_roi = screen_color[y1:y2, x1:x2]
        sh_fine, sw_fine = search_roi.shape[:2]

        best_fine_conf = 0
        best_fine_click = (0, 0)
        best_fine_scale = best_coarse_scale

        # 精匹配只试3个尺度
        fine_scales = [best_coarse_scale * 1.08, best_coarse_scale, best_coarse_scale * 0.92]
        for scale in fine_scales:
            new_w = int(tw * scale)
            new_h = int(th * scale)
            if new_w < 10 or new_h < 10 or new_w > sw_fine or new_h > sh_fine:
                continue
            scaled = cv2.resize(template_color, (new_w, new_h),
                                interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
            result = cv2.matchTemplate(search_roi, scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_fine_conf:
                best_fine_conf = max_val
                best_fine_scale = scale
                click_x = x1 + max_loc[0] + new_w // 2
                click_y = y1 + max_loc[1] + new_h // 2
                best_fine_click = (click_x, click_y)

        return best_fine_conf, best_fine_click, best_fine_scale

    def _scale_ladder(self, quick=False):
        """
        生成多尺度搜索梯子。
        游戏窗口化 2560x1440 时截取的模板，按 F11 全屏后 UI 放大 1.5 倍，
        因此尺度必须向上覆盖放大档，不能只往下缩。
        """
        # 中心缩放因子±15% 步进，覆盖约 0.44x ~ 1.67x
        factors = [1.0, 0.85, 1.18, 0.72, 1.38, 0.62, 1.6, 0.52]
        ladder = [self._base_scale * f for f in factors]
        if quick:
            # 快速路径只取靠近上次命中尺度的 3 档
            ladder = sorted(ladder, key=lambda s: abs(s - self._last_scale))[:3]
        return ladder

    def match_all(self, screen_img):
        """
        两阶段匹配所有模板（支持一个状态多个模板）
        返回: (state_name, confidence, click_position)
        """
        t0 = time.time()
        sh, sw = screen_img.shape[:2]

        # 预计算缩小灰度图（所有模板共享）
        screen_gray = cv2.cvtColor(screen_img, cv2.COLOR_BGR2GRAY)
        small_w = sw // self._pyramid_scale
        small_h = sh // self._pyramid_scale
        screen_gray_small = cv2.resize(screen_gray, (small_w, small_h))

        best_state = STATE_UNKNOWN
        best_confidence = 0
        best_click_pos = (0, 0)

        # ---- 快速路径：只试上次成功的状态 ----
        if self._last_state != STATE_UNKNOWN and self._last_state in self.templates:
            template_list = self.templates[self._last_state]
            # 遍历该状态的所有模板
            for tinfo in template_list:
                th, tw = tinfo["gray"].shape[:2]

                if th > sh or tw > sw:
                    fit_scale = min(sh / th, sw / tw) * 0.95
                    quick_scales = [fit_scale]
                else:
                    quick_scales = self._scale_ladder(quick=True)

                roi_full = get_roi_from_hint(screen_img.shape, tinfo["pos_hint"])
                roi_small = (roi_full[0] // self._pyramid_scale,
                             roi_full[1] // self._pyramid_scale,
                             roi_full[2] // self._pyramid_scale,
                             roi_full[3] // self._pyramid_scale)

                conf, click, scale = self._match_two_stage(
                    tinfo["image"], tinfo["gray"], screen_img, screen_gray_small,
                    roi_full, roi_small, quick_scales, self._pyramid_scale)

                if conf >= MATCH_THRESHOLD:
                    elapsed = (time.time() - t0) * 1000
                    self.log.debug(f"匹配[快速]: {self._last_state} ({tinfo['filename']}), conf={conf:.3f}, {elapsed:.0f}ms")
                    self._last_scale = scale
                    return self._last_state, conf, click

        # ---- 全量匹配：遍历所有状态的所有模板 ----
        for state_name, template_list in self.templates.items():
            # 快速路径试过的跳过（避免重复匹配）
            if state_name == self._last_state and self._last_state != STATE_UNKNOWN:
                continue

            for tinfo in template_list:
                th, tw = tinfo["gray"].shape[:2]

                roi_full = get_roi_from_hint(screen_img.shape, tinfo["pos_hint"])
                roi_small = (roi_full[0] // self._pyramid_scale,
                             roi_full[1] // self._pyramid_scale,
                             roi_full[2] // self._pyramid_scale,
                             roi_full[3] // self._pyramid_scale)

                if th > sh or tw > sw:
                    fit_scale = min(sh / th, sw / tw) * 0.95
                    if fit_scale < 0.1:
                        continue
                    test_scales = [fit_scale]
                else:
                    test_scales = self._scale_ladder()

                conf, click, scale = self._match_two_stage(
                    tinfo["image"], tinfo["gray"], screen_img, screen_gray_small,
                    roi_full, roi_small, test_scales, self._pyramid_scale)

                if conf > best_confidence:
                    best_confidence = conf
                    best_state = state_name
                    best_click_pos = click
                    self._last_scale = scale

                    # 高置信度提前退出
                    if conf > 0.95:
                        break

            if best_confidence > 0.95:
                break

        elapsed = (time.time() - t0) * 1000

        if best_confidence >= MATCH_THRESHOLD:
            self._last_state = best_state
            # 以实际命中尺度为新基准，分辨率再变化（如 F11 全屏切换）时
            # 快速路径能从正确的尺度附近继续搜索
            if best_confidence > 0.9:
                self._base_scale = max(0.4, min(2.0, self._last_scale))
            self.log.debug(f"匹配[全量]: {best_state}, conf={best_confidence:.3f}, {elapsed:.0f}ms")
            return best_state, best_confidence, best_click_pos
        else:
            # 全量匹配失败：尺度基准可能已失效（如 F11 切换分辨率），
            # 重置为 1.0 重新扫全梯子；同时更新基准为当前屏幕与模板的粗略比例
            self._last_state = STATE_UNKNOWN
            self._retune_base_scale(sw, sh)
            self.log.debug(f"匹配[未知], 最高={best_confidence:.3f}, {elapsed:.0f}ms")
            return STATE_UNKNOWN, best_confidence, (0, 0)

    def _retune_base_scale(self, screen_w, screen_h):
        """匹配失败时按屏幕分辨率估算 UI 缩放基准。
        模板取自 2560x1440 窗口，若当前屏幕更大，基准相应放大。"""
        # 模板均取自窗口化 2560x1440；屏幕分辨率与该基准的比值作为中心尺度
        template_ref_w = 2560.0
        ratio = screen_w / template_ref_w
        # 基准只做温和修正，避免被异常截图带偏
        self._base_scale = max(0.4, min(2.0, ratio))


class WindowCapture:
    """窗口捕获器 - 负责捕获游戏窗口画面"""

    def __init__(self, title_keywords, log):
        self.log = log
        self.title_keywords = title_keywords
        self.hwnd = None
        self.find_game_window()

    def find_game_window(self):
        """查找游戏窗口（过滤掉资源管理器等系统窗口）"""
        t0 = time.time()
        results = []

        # 需要过滤的窗口类名（资源管理器、任务栏等）
        exclude_classes = {
            "CabinetWClass",      # 资源管理器
            "ExploreWClass",      # 旧版资源管理器
            "Shell_TrayWnd",      # 任务栏
            "Progman",            # 桌面
            "WorkerW",            # 桌面
            "Windows.UI.Core.CoreWindow",  # UWP系统窗口
        }

        def enum_cb(hwnd, _):
            # 过滤：不可见的窗口跳过
            if not win32gui.IsWindowVisible(hwnd):
                return
            # 过滤：窗口类名排除
            try:
                class_name = win32gui.GetClassName(hwnd)
                if class_name in exclude_classes:
                    return
            except Exception:
                pass

            title = win32gui.GetWindowText(hwnd)
            if not title:
                return

            for kw in self.title_keywords:
                if kw in title:
                    results.append(hwnd)
                    break

        win32gui.EnumWindows(enum_cb, None)

        if results:
            self.hwnd = results[0]
            title = win32gui.GetWindowText(self.hwnd)
            try:
                class_name = win32gui.GetClassName(self.hwnd)
            except Exception:
                class_name = "unknown"
            elapsed = (time.time() - t0) * 1000
            self.log.info(f"找到游戏窗口: hwnd={self.hwnd}, 类名={class_name}, 耗时={elapsed:.0f}ms")
            self.log.debug(f"窗口标题: {title[:80]}")
            return True
        else:
            self.log.warning("未找到游戏窗口")
            return False

    def bring_to_front(self):
        """将游戏窗口调到前台（兼容权限受限的情况）"""
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            self.log.warning("无法激活窗口：窗口句柄无效")
            return False

        try:
            if win32gui.IsIconic(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        except Exception:
            pass

        # 方式1：直接SetForegroundWindow
        try:
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception:
            # 方式2：AttachThreadInput + SetForegroundWindow
            try:
                import win32process
                fore_thread = win32process.GetWindowThreadProcessId(
                    win32gui.GetForegroundWindow())[0]
                cur_thread = win32api.GetCurrentThreadId()
                ctypes.windll.user32.AttachThreadInput(fore_thread, cur_thread, True)
                win32gui.SetForegroundWindow(self.hwnd)
                ctypes.windll.user32.AttachThreadInput(fore_thread, cur_thread, False)
            except Exception:
                # 方式3：用SetActiveWindow或发送ALT键
                try:
                    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # ALT down
                    win32gui.SetForegroundWindow(self.hwnd)
                    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # ALT up
                except Exception as e:
                    self.log.debug(f"激活窗口失败: {e}")

        time.sleep(0.3)
        self.log.debug("游戏窗口已尝试激活到前台")
        return True

    def capture(self):
        """
        捕获屏幕画面（全屏截图）
        返回: BGR格式的numpy数组
        """
        t0 = time.time()
        screenshot = pyautogui.screenshot()
        img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
        elapsed = (time.time() - t0) * 1000
        self.log.debug(f"截图: {img.shape[1]}x{img.shape[0]}, 耗时={elapsed:.0f}ms")
        return img

    def get_client_size(self):
        """获取游戏窗口客户区尺寸 (宽, 高)；窗口无效时返回 (0, 0)"""
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            return (0, 0)
        try:
            left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
            return (right - left, bottom - top)
        except Exception:
            return (0, 0)


class InputController:
    """
    输入控制器 - 模拟鼠标点击和键盘按键
    使用 SendInput API（比pyautogui更底层，对游戏更有效）
    注意：某些游戏可能需要管理员权限才能接收模拟输入
    """

    # SendInput 相关常量
    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_ABSOLUTE = 0x8000
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008

    # 虚拟键码 -> 扫描码映射（标准美式键盘）
    # UE游戏直接读硬件扫描码，虚拟键码可能被忽略
    SCAN_CODES = {
        'i': 0x17,       # I 键扫描码
        'I': 0x17,
        'w': 0x11,
        'a': 0x1E,
        's': 0x1F,
        'd': 0x20,
        'f1': 0x3B,      # F1 键扫描码
        'f2': 0x3C,
        'f3': 0x3D,
        'f4': 0x3E,
        'f5': 0x3F,
        'f6': 0x40,
        'f7': 0x41,
        'f8': 0x42,
        'f9': 0x43,
        'f10': 0x44,
        'f11': 0x57,
        'f12': 0x58,
        'space': 0x39,
        'enter': 0x1C,
        'esc': 0x01,
        'shift': 0x2A,
        'ctrl': 0x1D,
        'alt': 0x38,
        '1': 0x02, '2': 0x03, '3': 0x04, '4': 0x05, '5': 0x06,
        '6': 0x07, '7': 0x08, '8': 0x09, '9': 0x0A, '0': 0x0B,
    }

    # 虚拟键码（用于PostMessage）
    VK_CODES = {
        'i': 0x49, 'I': 0x49,
        'w': 0x57, 'a': 0x41, 's': 0x53, 'd': 0x44,
        'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73,
        'f5': 0x74, 'f6': 0x75, 'f7': 0x76, 'f8': 0x77,
        'f9': 0x78, 'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B,
        'space': 0x20, 'enter': 0x0D, 'esc': 0x1B,
    }

    def __init__(self, hwnd, log):
        self.hwnd = hwnd
        self.log = log
        self.user32 = ctypes.windll.user32
        self._check_admin()
        # 获取屏幕分辨率（用于SendInput绝对坐标）
        self.screen_w = self.user32.GetSystemMetrics(0)  # SM_CXSCREEN
        self.screen_h = self.user32.GetSystemMetrics(1)  # SM_CYSCREEN

    def _check_admin(self):
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()
            if not is_admin:
                self.log.warning("当前不是管理员权限，部分游戏可能无法接收模拟输入")
                self.log.warning("如点击无效，请右键以管理员身份运行")
        except Exception:
            pass

    def _focus_game_window(self):
        """尝试将游戏窗口设为前台（多种降级方案）"""
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            return False

        # 已经是前台窗口就不用动了
        try:
            if win32gui.GetForegroundWindow() == self.hwnd:
                return True
        except Exception:
            pass

        try:
            if win32gui.IsIconic(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        except Exception:
            pass

        # 方案1: 直接设置
        try:
            win32gui.SetForegroundWindow(self.hwnd)
            return True
        except Exception:
            pass

        # 方案2: AttachThreadInput
        try:
            fore_hwnd = win32gui.GetForegroundWindow()
            fore_thread = win32process.GetWindowThreadProcessId(fore_hwnd)[0]
            cur_thread = win32api.GetCurrentThreadId()
            ctypes.windll.user32.AttachThreadInput(fore_thread, cur_thread, True)
            win32gui.SetForegroundWindow(self.hwnd)
            ctypes.windll.user32.AttachThreadInput(fore_thread, cur_thread, False)
            return True
        except Exception:
            pass

        # 方案3: ALT键 trick
        try:
            self.user32.keybd_event(0x12, 0, 0, 0)
            win32gui.SetForegroundWindow(self.hwnd)
            self.user32.keybd_event(0x12, 0, 2, 0)
            return True
        except Exception:
            pass

        return False

    def _screen_to_absolute(self, x, y):
        """将屏幕像素坐标转换为SendInput的绝对坐标(0-65535)"""
        ax = int(x * 65535 / (self.screen_w - 1))
        ay = int(y * 65535 / (self.screen_h - 1))
        return ax, ay

    def _send_mouse_input(self, dx, dy, flags):
        """通过SendInput发送鼠标事件"""
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
            ]

        class INPUT(ctypes.Structure):
            class _INPUT(ctypes.Union):
                _fields_ = [("mi", MOUSEINPUT)]
            _anonymous_ = ("_input",)
            _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT)]

        inp = INPUT()
        inp.type = self.INPUT_MOUSE
        inp.mi.dx = dx
        inp.mi.dy = dy
        inp.mi.mouseData = 0
        inp.mi.dwFlags = flags
        inp.mi.time = 0
        inp.mi.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))

        self.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def _send_keyboard_input(self, scan_code, is_keyup=False):
        """通过SendInput发送键盘事件（使用扫描码，游戏兼容性更好）"""
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
            ]

        class INPUT(ctypes.Structure):
            class _INPUT(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]
            _anonymous_ = ("_input",)
            _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT)]

        flags = self.KEYEVENTF_SCANCODE
        if is_keyup:
            flags |= self.KEYEVENTF_KEYUP

        inp = INPUT()
        inp.type = self.INPUT_KEYBOARD
        inp.ki.wVk = 0  # 使用扫描码时wVk设为0
        inp.ki.wScan = scan_code
        inp.ki.dwFlags = flags
        inp.ki.time = 0
        inp.ki.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))

        self.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def click(self, x, y):
        """
        点击指定屏幕坐标
        使用 SendInput API（比pyautogui更底层，游戏兼容性更好）
        """
        t0 = time.time()

        # 转换为绝对坐标
        ax, ay = self._screen_to_absolute(x, y)

        # 移动鼠标到目标位置 + 按下左键 + 释放左键
        self._send_mouse_input(ax, ay, self.MOUSEEVENTF_MOVE | self.MOUSEEVENTF_ABSOLUTE)
        time.sleep(0.02)
        self._send_mouse_input(0, 0, self.MOUSEEVENTF_LEFTDOWN)
        time.sleep(0.05)
        self._send_mouse_input(0, 0, self.MOUSEEVENTF_LEFTUP)

        elapsed = (time.time() - t0) * 1000
        self.log.info(f"点击: ({x}, {y}), 耗时={elapsed:.0f}ms")

    def _keybd_event_key(self, scan_code, is_keyup=False):
        """使用keybd_event API发送按键（最老的API，游戏兼容性最好）"""
        KEYEVENTF_KEYUP = 0x0002
        KEYEVENTF_SCANCODE = 0x0008
        flags = KEYEVENTF_SCANCODE
        if is_keyup:
            flags |= KEYEVENTF_KEYUP
        self.user32.keybd_event(0, scan_code, flags, 0)

    def _postmessage_key(self, vk_code, is_keyup=False):
        """向游戏窗口PostMessage发送键盘消息"""
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            return False
        WM_KEYDOWN = 0x0100
        WM_KEYUP = 0x0101
        WM_CHAR = 0x0102

        # 构造lParam
        # KeyDown: 0x00000001 (1次重复, 扫描码=0, 扩展=0, 之前未按下)
        # KeyUp:   0xC0000001 (重复=1, 扫描码=0, 扩展=0, 之前按下过, 正在释放)
        keydown_lparam = 0x00000001
        keyup_lparam = 0xC0000001

        try:
            if is_keyup:
                win32api.PostMessage(self.hwnd, WM_KEYUP, vk_code, keyup_lparam)
            else:
                win32api.PostMessage(self.hwnd, WM_KEYDOWN, vk_code, keydown_lparam)
                # 有些游戏还需要WM_CHAR
                if vk_code < 0x80:
                    win32api.PostMessage(self.hwnd, WM_CHAR, vk_code, keydown_lparam)
            return True
        except Exception as e:
            self.log.debug(f"PostMessage失败: {e}")
            return False

    def press_key(self, key, duration=0.3):
        """
        按下按键并持续一段时间
        三重输入方式同时发送，确保游戏能收到：
        1. keybd_event (最老的API，兼容性最好)
        2. SendInput (扫描码方式)
        3. PostMessage (直接发消息到窗口)
        """
        scan = self.SCAN_CODES.get(key.lower())
        vk = self.VK_CODES.get(key.lower())

        if scan is None and vk is None:
            self.log.warning(f"未知按键: {key}")
            return

        # 确保游戏窗口有焦点
        self._focus_game_window()

        self.log.debug(f"按键 {key}: 三重发送开始 (scan=0x{scan or 0:02X}, vk=0x{vk or 0:02X})")

        # ---- 按下 ----
        # 方式1: keybd_event (扫描码)
        if scan is not None:
            self._keybd_event_key(scan, is_keyup=False)

        # 方式2: SendInput (扫描码)
        if scan is not None:
            self._send_keyboard_input(scan, is_keyup=False)

        # 方式3: PostMessage (虚拟键码)
        if vk is not None:
            self._postmessage_key(vk, is_keyup=False)

        # 持续
        time.sleep(duration)

        # ---- 抬起 ----
        # 方式3: PostMessage
        if vk is not None:
            self._postmessage_key(vk, is_keyup=True)

        # 方式2: SendInput
        if scan is not None:
            self._send_keyboard_input(scan, is_keyup=True)

        # 方式1: keybd_event
        if scan is not None:
            self._keybd_event_key(scan, is_keyup=True)

        self.log.info(f"按键: {key}(扫描码=0x{scan or 0:02X}), 持续 {duration}s")


class FloatingWindow(QtWidgets.QWidget):
    """悬浮窗 - 显示当前状态和控制按钮"""

    toggle_signal = QtCore.pyqtSignal(bool)
    add_log_signal = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = False
        self.on_toggle = None
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("NFS Auto")
        self.setGeometry(10, 10, 240, 300)
        self.setFixedSize(240, 300)
        self.setWindowFlags(QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.WindowCloseButtonHint)
        self.setStyleSheet("background-color: #1e1e1e; color: #ffffff;")

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # 状态显示
        self.state_label = QtWidgets.QLabel("状态: 就绪")
        self.state_label.setFont(QtGui.QFont("微软雅黑", 11, QtGui.QFont.Bold))
        self.state_label.setStyleSheet("color: #4fc3f7;")
        layout.addWidget(self.state_label)

        # 信息行
        info_layout = QtWidgets.QHBoxLayout()
        self.confidence_label = QtWidgets.QLabel("置信度: --")
        self.confidence_label.setFont(QtGui.QFont("微软雅黑", 9))
        self.confidence_label.setStyleSheet("color: #aaaaaa;")
        info_layout.addWidget(self.confidence_label)

        self.round_label = QtWidgets.QLabel("轮数: 0")
        self.round_label.setFont(QtGui.QFont("微软雅黑", 9))
        self.round_label.setStyleSheet("color: #ffb74d;")
        info_layout.addWidget(self.round_label)
        layout.addLayout(info_layout)

        # 控制按钮
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(6)

        self.toggle_btn = QtWidgets.QPushButton("开始")
        self.toggle_btn.setStyleSheet("""
            QPushButton { background-color: #4caf50; color: white; border: none;
                padding: 6px 12px; border-radius: 4px; font-size: 12px; }
            QPushButton:hover { background-color: #45a049; }
        """)
        self.toggle_btn.clicked.connect(self.toggle)
        btn_layout.addWidget(self.toggle_btn)

        quit_btn = QtWidgets.QPushButton("退出")
        quit_btn.setStyleSheet("""
            QPushButton { background-color: #f44336; color: white; border: none;
                padding: 6px 12px; border-radius: 4px; font-size: 12px; }
            QPushButton:hover { background-color: #da190b; }
        """)
        quit_btn.clicked.connect(self.close)
        btn_layout.addWidget(quit_btn)
        layout.addLayout(btn_layout)

        # 日志面板
        log_label = QtWidgets.QLabel("运行日志:")
        log_label.setFont(QtGui.QFont("微软雅黑", 9))
        log_label.setStyleSheet("color: #888; margin-top: 4px;")
        layout.addWidget(log_label)

        self.log_text = QtWidgets.QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(200)
        self.log_text.setStyleSheet("""
            QPlainTextEdit { background-color: #0d0d0d; color: #cccccc;
                border: 1px solid #333; border-radius: 3px; font-size: 11px; }
        """)
        self.log_text.setFont(QtGui.QFont("Consolas", 9))
        layout.addWidget(self.log_text, 1)

        self.setLayout(layout)

        # 连接日志信号
        self.add_log_signal.connect(self._append_log)

    def toggle(self):
        self.running = not self.running
        if self.running:
            self.toggle_btn.setText("暂停")
            self.toggle_btn.setStyleSheet("""
                QPushButton { background-color: #ff9800; color: white; border: none;
                    padding: 6px 12px; border-radius: 4px; font-size: 12px; }
                QPushButton:hover { background-color: #e68900; }
            """)
        else:
            self.toggle_btn.setText("开始")
            self.toggle_btn.setStyleSheet("""
                QPushButton { background-color: #4caf50; color: white; border: none;
                    padding: 6px 12px; border-radius: 4px; font-size: 12px; }
                QPushButton:hover { background-color: #45a049; }
            """)
        if self.on_toggle:
            self.on_toggle(self.running)

    def update_state(self, state, confidence):
        """更新状态显示"""
        self.state_label.setText(f"状态: {state}")
        conf_text = f"{confidence:.2f}" if confidence > 0 else "--"
        self.confidence_label.setText(f"置信度: {conf_text}")

    def update_round(self, count):
        """更新轮数显示"""
        self.round_label.setText(f"轮数: {count}")

    def _append_log(self, text):
        """向日志面板追加一行（UI线程调用）"""
        self.log_text.appendPlainText(text)
        # 滚动到底部
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def run(self):
        self.show()


class QtLogHandler(logging.Handler):
    """将日志转发到Qt悬浮窗的日志处理器"""

    def __init__(self, signal):
        super().__init__()
        self.signal = signal
        self.setLevel(logging.INFO)

    def emit(self, record):
        msg = self.format(record)
        self.signal.emit(msg)


class NFSAutoBot(QtCore.QObject):
    """极品飞车自动刷奖励主逻辑"""

    update_state_signal = QtCore.pyqtSignal(str, float)
    update_round_signal = QtCore.pyqtSignal(int)

    def __init__(self, template_dir, log):
        super().__init__()
        self.log = log
        self.matcher = TemplateMatcher(template_dir, log)
        self.capturer = WindowCapture(GAME_TITLE_KEYWORDS, log)
        self.controller = InputController(self.capturer.hwnd, log)
        self.floating_window = FloatingWindow()

        self.current_state = STATE_UNKNOWN
        self.running = False
        self.round_count = 0
        self.last_key_time = 0
        self.last_state = STATE_UNKNOWN
        self.state_stable_time = 0
        self._last_client_size = self.capturer.get_client_size()

        # 连接UI信号
        self.update_state_signal.connect(self.floating_window.update_state)
        self.update_round_signal.connect(self.floating_window.update_round)
        self.floating_window.on_toggle = self.on_toggle

        # 把日志也输出到悬浮窗
        qt_handler = QtLogHandler(self.floating_window.add_log_signal)
        qt_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
        self.log.addHandler(qt_handler)

    def on_toggle(self, running):
        """开关回调"""
        self.running = running
        if running:
            self.log.info("=== 开始自动刷奖励 ===")
            # 重置状态
            self.last_state = STATE_UNKNOWN
            self.current_state = STATE_UNKNOWN
            self.state_stable_time = 0
            # 激活游戏窗口
            self.capturer.bring_to_front()
            # 启动检测线程
            self.detect_thread = threading.Thread(target=self.detect_loop, daemon=True)
            self.detect_thread.start()
        else:
            self.log.info("=== 已暂停 ===")

    def detect_loop(self):
        """检测循环（在子线程中运行）"""
        self.log.debug("检测线程启动")
        loop_count = 0

        while True:
            if not self.running:
                time.sleep(0.1)
                continue

            loop_count += 1
            t_total = time.time()

            try:
                # 0. 检测游戏窗口尺寸变化（F11 全屏切换会导致分辨率改变，
                #    模板尺度随之变化，需要立即重置匹配状态）
                client_size = self.capturer.get_client_size()
                if client_size != self._last_client_size:
                    if client_size != (0, 0) and self._last_client_size != (0, 0):
                        ratio = client_size[0] / max(1, self._last_client_size[0])
                        self.log.info(
                            f"检测到游戏窗口尺寸变化: {self._last_client_size[0]}x{self._last_client_size[1]}"
                            f" -> {client_size[0]}x{client_size[1]} (比例{ratio:.2f})，重置模板匹配状态")
                        # 按尺寸比例修正基准尺度，让下一次全量匹配直接命中
                        self.matcher._base_scale = max(0.4, min(2.0,
                            self.matcher._base_scale * ratio))
                        self.matcher._last_state = STATE_UNKNOWN
                        self.matcher._last_scale = self.matcher._base_scale
                    self._last_client_size = client_size

                # 1. 截图
                t0 = time.time()
                screen = self.capturer.capture()
                t_cap = time.time() - t0

                # 2. 模板匹配
                t0 = time.time()
                state, confidence, click_pos = self.matcher.match_all(screen)
                t_match = time.time() - t0

                # 3. 更新UI
                self.update_state_signal.emit(state, confidence)

                # 4. 状态稳定检测
                if state != self.last_state:
                    self.last_state = state
                    self.state_stable_time = time.time()
                    self.log.debug(f"状态变化: {state} (置信度={confidence:.3f})")
                    time.sleep(DETECT_INTERVAL)
                    continue

                stable_elapsed = time.time() - self.state_stable_time
                if stable_elapsed < STATE_STABLE_TIME:
                    time.sleep(DETECT_INTERVAL)
                    continue

                if state == STATE_UNKNOWN:
                    time.sleep(DETECT_INTERVAL)
                    continue

                # 5. 根据状态执行动作
                self.process_state(state, click_pos)

            except Exception as e:
                self.log.error(f"检测循环出错: {e}")
                import traceback
                self.log.debug(traceback.format_exc())

            total_ms = (time.time() - t_total) * 1000
            self.log.debug(f"循环#{loop_count} 总耗时={total_ms:.0f}ms")

            time.sleep(max(0, DETECT_INTERVAL - total_ms / 1000))

    def process_state(self, state, click_pos):
        """根据当前状态执行对应动作
        逻辑：进入新状态时点击一次；如果点击后3秒状态没变，重新点击（防卡）
        """
        now = time.time()

        if state == STATE_IN_GAME:
            # 游戏中：定时按键
            if now - self.last_key_time >= IN_GAME_KEY_INTERVAL:
                self.log.info(f"[游戏中] 按{IN_GAME_KEY.upper()}键")
                self.controller.press_key(IN_GAME_KEY, IN_GAME_KEY_DURATION)
                self.last_key_time = now

        elif state == STATE_MATCHING:
            # 匹配中：等待，不操作
            if self.current_state != STATE_MATCHING:
                self.log.info("[匹配中] 等待匹配完成...")

        else:
            # 其他状态：点击对应按钮
            if state != self.current_state:
                # 第一次进入该状态，点击
                action = STATE_CLICK_ACTION.get(state, "点击")
                self.log.info(f"[{state}] {action}")
                time.sleep(0.2)
                if click_pos and click_pos[0] > 0 and click_pos[1] > 0:
                    self.controller.click(click_pos[0], click_pos[1])
                    self._last_click_time = now
                    self._last_click_state = state
                    self._retry_count = 0

                    # 结算界面 -> 完成一轮
                    if state in (STATE_RESULT_1, STATE_RESULT_2) and self.current_state == STATE_IN_GAME:
                        self.round_count += 1
                        self.update_round_signal.emit(self.round_count)
                        self.log.info(f"=== 第 {self.round_count} 轮完成 ===")
            else:
                # 状态没变，检查是否需要重新点击（防卡死）
                RETRY_DELAY = 3.0  # 3秒后重试
                MAX_RETRY = 5      # 最多重试5次
                if (hasattr(self, '_last_click_time')
                        and self._last_click_state == state
                        and now - self._last_click_time >= RETRY_DELAY
                        and getattr(self, '_retry_count', 0) < MAX_RETRY):
                    self._retry_count += 1
                    self.log.warning(
                        f"[{state}] 状态未变化 {RETRY_DELAY:.0f}秒，重新点击 (第{self._retry_count}次重试)")
                    if click_pos and click_pos[0] > 0 and click_pos[1] > 0:
                        self.controller.click(click_pos[0], click_pos[1])
                        self._last_click_time = now

        self.current_state = state

    def run(self, app):
        """运行程序（启动悬浮窗，阻塞）"""
        self.log.info("=" * 45)
        self.log.info("  极品飞车集结 - 自动刷奖励工具")
        self.log.info("=" * 45)
        self.log.info(f"  模板数量: {len(self.matcher.templates)}")
        self.log.info(f"  匹配阈值: {MATCH_THRESHOLD}")
        self.log.info(f"  检测间隔: {DETECT_INTERVAL}s")
        self.log.info("=" * 45)
        self.log.info("  提示: 点击'开始'启动自动刷取")
        self.log.info("        鼠标移到左上角可紧急停止")
        self.log.info("=" * 45)

        self.floating_window.run()
        sys.exit(app.exec_())


def main():
    global logger

    # 获取脚本所在目录
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    # 日志目录
    log_dir = os.path.join(base_dir, "logs")
    logger = setup_logger(log_dir)

    template_dir = base_dir

    # 创建Qt应用
    app = QtWidgets.QApplication(sys.argv)

    try:
        bot = NFSAutoBot(template_dir, logger)
        bot.run(app)
    except Exception as e:
        if logger:
            logger.error(f"程序异常退出: {e}")
            import traceback
            logger.debug(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
