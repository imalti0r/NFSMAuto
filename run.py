# -*- coding: utf-8 -*-
"""
极品飞车集结自动刷奖励 - 一键启动脚本
功能：自动检测 Python 环境、检查并安装依赖、启动主程序
"""

import sys
import os
import subprocess

# 获取脚本所在目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# 依赖列表 (import名: pip包名)
DEPENDENCIES = {
    "cv2": "opencv-python",
    "numpy": "numpy",
    "pyautogui": "pyautogui",
    "win32gui": "pywin32",
    "PyQt5": "PyQt5",
    "PIL": "pillow",
}


def print_header():
    print("=" * 50)
    print("  极品飞车集结 - 自动刷奖励工具")
    print("  一键环境检测与启动")
    print("=" * 50)
    print()


def check_python():
    """检查 Python 版本"""
    print("[1/3] 检测 Python 环境...")
    ver = sys.version_info
    ver_str = f"{ver.major}.{ver.minor}.{ver.micro}"

    if ver.major < 3 or (ver.major == 3 and ver.minor < 8):
        print(f"[错误] Python 版本过低: {ver_str}，需要 3.8+")
        print("下载地址: https://www.python.org/downloads/")
        return False

    print(f"[OK] Python 版本: {ver_str}")
    print()
    return True


def check_dependencies():
    """检查并安装依赖"""
    print("[2/3] 检查依赖库...")

    missing = []
    for import_name, pip_name in DEPENDENCIES.items():
        try:
            __import__(import_name)
            print(f"[OK] {pip_name}")
        except ImportError:
            print(f"[缺失] {pip_name}")
            missing.append(pip_name)

    print()

    if missing:
        print(f"需要安装 {len(missing)} 个依赖: {' '.join(missing)}")
        print("正在安装，请稍候...")
        print()

        # 安装依赖
        cmd = [sys.executable, "-m", "pip", "install", "--no-build-isolation"] + missing
        result = subprocess.run(cmd)

        if result.returncode != 0:
            print()
            print("[错误] 依赖安装失败，请检查网络连接后重试")
            return False

        print()
        print("[OK] 所有依赖安装完成")
    else:
        print("[OK] 所有依赖已就绪")

    print()
    return True


def start_program():
    """启动主程序"""
    print("[3/3] 启动程序...")
    print()
    print("=" * 50)
    print("  提示:")
    print("   1. 确保游戏已启动并处于可见状态")
    print("   2. 点击悬浮窗的'开始'按钮启动自动刷取")
    print("   3. 鼠标快速移到屏幕左上角可紧急停止")
    print("=" * 50)
    print()

    # 启动主程序（同进程运行）
    try:
        subprocess.run([sys.executable, "nfs_auto.py"], cwd=BASE_DIR)
    except KeyboardInterrupt:
        print("\n[已退出] 用户中断")
    except Exception as e:
        print(f"\n[错误] 程序异常: {e}")
        return False

    return True


def main():
    print_header()

    if not check_python():
        input("\n按回车键退出...")
        sys.exit(1)

    if not check_dependencies():
        input("\n按回车键退出...")
        sys.exit(1)

    start_program()


if __name__ == "__main__":
    main()
