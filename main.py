"""
Modbus 主机上位机 v2.0 - 配置驱动版
====================================
基于模块化架构，支持 JSON 配置驱动、周期轮循、数据表格显示。
使用 pyserial + tkinter 构建。

用法:
    python main.py                  # 启动 GUI
    python main.py --config config.json  # 启动并自动加载配置
"""

from __future__ import annotations

import sys
import tkinter as tk


def main():
    # 设置 DPI 感知 (Windows)
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()

    # 延迟导入，确保 tkinter 初始化完成
    from modbus_modules.gui_app import ModbusMasterApp

    app = ModbusMasterApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)

    # 命令行参数：自动加载配置
    if len(sys.argv) > 2 and sys.argv[1] == "--config":
        config_path = sys.argv[2]
        root.after(500, lambda: app._load_config(config_path))

    root.mainloop()


if __name__ == "__main__":
    main()
