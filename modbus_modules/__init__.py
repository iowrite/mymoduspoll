"""
Modbus 主机上位机 v2.0 - 模块化包
==================================
基于配置驱动的 Modbus 监控上位机。

模块:
    modbus_core.py      - Modbus 协议核心 (CRC, 帧构建, 解析)
    config_parser.py    - JSON 配置加载与验证
    serial_manager.py   - 串口管理
    polling_engine.py   - 周期轮循引擎
    data_table.py       - 数据表格显示
    data_monitor.py     - 底层数据监控 (TX/RX 原始数据)
    group_navigator.py  - 组导航侧边栏
    gui_app.py          - 主 GUI 应用
"""

from __future__ import annotations

__version__ = "2.0.0"
__author__ = "Modbus Master"
