"""
配置解析模块
============
负责加载、验证和管理 Modbus 监控配置（JSON 格式）。

配置结构示例（紧凑数组格式）:
{
  "serial": {
    "baudrate": 9600,
    "databits": 8,
    "stopbits": "1",
    "parity": "无"
  },
  "slave_id": 1,
  "polling": {
    "enabled": true,
    "interval_ms": 2000
  },
  "groups": [
    {
      "name": "温度传感器",
      "function_code": 3,
      "start_address": 0,
      "quantity": 4,
      "points": [
        # 数组格式: [名称, 索引, 有符号, 倍率, 偏移, 单位, 小数位, 数据类型]
        ["通道1温度", 0, true, 0.1, 0, "°C", 1, "int16"],
        ["通道2温度", 1, true, 0.1, 0, "°C", 1, "int16"]
      ]
    }
  ]
}

点位数组格式字段说明:
  [0] name           - 点位名称（必填）
  [1] register_index - 寄存器索引，从0开始
  [2] signed         - 是否有符号 (true/false)
  [3] scale          - 倍率
  [4] offset         - 偏移量
  [5] unit           - 单位
  [6] decimals       - 小数位数
  [7] data_type      - 数据类型 (uint16/int16/uint32/int32/float32)

也支持传统的对象格式（用于需要 bit_index, description 等额外字段时）:
  {"name": "点位名", "register_index": 0, "bit_index": 0, "description": "备注"}
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional


# ============================================================
# 数据类型定义
# ============================================================
@dataclass
class PointConfig:
    """单个监控点配置"""

    name: str  # 点位名称
    register_index: int = 0  # 在寄存器列表中的索引 (0-based)
    signed: bool = False  # 是否有符号
    scale: float = 1.0  # 倍率
    offset: float = 0.0  # 偏移量
    unit: str = ""  # 单位
    decimals: int = 0  # 小数位数 (用于显示)
    data_type: str = "uint16"  # 数据类型: uint16, int16, uint32, int32, float32
    byte_order: str = "big"  # 字节序: big/little
    word_order: str = "big"  # 字序 (32位): big/little
    description: str = ""  # 描述
    min_value: Optional[float] = None  # 最小值告警
    max_value: Optional[float] = None  # 最大值告警
    format_str: str = ""  # 自定义格式化字符串 (如 "{:.1f}")


# 点位数组格式的字段顺序（一行一个数组时按此位置映射）
# 写法: [名称, 索引, 有符号, 倍率, 偏移, 单位, 小数位, 数据类型, 说明]
POINT_ARRAY_FIELDS = [
    "name",  # 0
    "register_index",  # 1
    "signed",  # 2
    "scale",  # 3
    "offset",  # 4
    "unit",  # 5
    "decimals",  # 6
    "data_type",  # 7
    "description",  # 8
]


@dataclass
class GroupConfig:
    """一组 Modbus 配置"""

    name: str  # 组名称
    function_code: int  # 功能码 (03=读保持寄存器, 04=读输入寄存器, 16=写多寄存器)
    start_address: int  # 起始地址
    quantity: int  # 读取数量
    enabled: bool = True  # 是否启用该组
    interval_ms: int = 1000  # 本组的轮询周期 (毫秒)
    points: list[PointConfig] = field(default_factory=list)  # 点位列表


@dataclass
class PollingConfig:
    """轮循配置"""

    enabled: bool = True
    interval_ms: int = 1000


@dataclass
class SerialConfig:
    """串口配置"""

    baudrate: int = 9600
    databits: int = 8
    stopbits: str = "1"
    parity: str = "无"


@dataclass
class AppConfig:
    """完整的应用配置"""

    serial: SerialConfig = field(default_factory=SerialConfig)
    slave_id: int = 1
    polling: PollingConfig = field(default_factory=PollingConfig)
    groups: list[GroupConfig] = field(default_factory=list)


# ============================================================
# 配置加载器
# ============================================================
def load_config(filepath: str) -> AppConfig:
    """
    从 JSON 文件加载配置

    Args:
        filepath: JSON 配置文件路径

    Returns:
        AppConfig 对象

    Raises:
        FileNotFoundError: 文件不存在
        json.JSONDecodeError: JSON 格式错误
        ValueError: 配置内容校验失败
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"配置文件不存在: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        raw = json.load(f)

    return parse_config_dict(raw)


def parse_config_dict(raw: dict) -> AppConfig:
    """将字典解析为 AppConfig 对象"""
    config = AppConfig()

    # 解析串口配置
    serial_raw = raw.get("serial", {})
    config.serial = SerialConfig(
        baudrate=int(serial_raw.get("baudrate", 9600)),
        databits=int(serial_raw.get("databits", 8)),
        stopbits=str(serial_raw.get("stopbits", "1")),
        parity=str(serial_raw.get("parity", "无")),
    )

    # 解析从站 ID
    config.slave_id = int(raw.get("slave_id", 1))

    # 解析轮循配置
    polling_raw = raw.get("polling", {})
    config.polling = PollingConfig(
        enabled=bool(polling_raw.get("enabled", True)),
        interval_ms=int(polling_raw.get("interval_ms", 1000)),
    )

    # 解析组
    groups_raw = raw.get("groups", [])
    for g in groups_raw:
        group = GroupConfig(
            name=str(g.get("name", f"组_{g.get('start_address', 0)}")),
            function_code=_parse_func_code(g.get("function_code", 3)),
            start_address=int(g.get("start_address", 0)),
            quantity=int(g.get("quantity", 1)),
            enabled=bool(g.get("enabled", True)),
            interval_ms=int(g.get("interval_ms", 1000)),
        )

        # 校验功能码（轮循只支持读取命令 03/04，写命令 16 用于手动触发）
        if group.function_code not in (0x03, 0x04, 0x10):
            raise ValueError(
                f"组 '{group.name}': 不支持的功能码 0x{group.function_code:02X}, "
                f"仅支持 03(读寄存器)、04(读输入寄存器)、16(写寄存器)"
            )
        # 写组不能用于轮循
        if group.function_code == 0x10 and group.quantity < 1:
            raise ValueError(f"写组 '{group.name}': quantity 必须 > 0")

        # 解析点位（支持数组格式和对象格式）
        points_raw = g.get("points", [])
        for p in points_raw:
            if isinstance(p, list):
                point = _parse_point_array(p)
            else:
                point = PointConfig(
                    name=str(p.get("name", f"点_{group.start_address}")),
                    register_index=int(p.get("register_index", 0)),
                    signed=bool(p.get("signed", False)),
                    scale=float(p.get("scale", 1.0)),
                    offset=float(p.get("offset", 0.0)),
                    unit=str(p.get("unit", "")),
                    decimals=int(p.get("decimals", 0)),
                    data_type=str(p.get("data_type", "uint16")),
                    byte_order=str(p.get("byte_order", "big")),
                    word_order=str(p.get("word_order", "big")),
                    description=str(p.get("description", "")),
                    min_value=p.get("min_value"),
                    max_value=p.get("max_value"),
                    format_str=str(p.get("format_str", "")),
                )
            group.points.append(point)

        config.groups.append(group)

    return config


def _parse_point_array(arr: list[Any]) -> PointConfig:
    """
    从数组解析点位配置

    数组格式（按 POINT_ARRAY_FIELDS 顺序）:
        [name, register_index, signed, scale, offset, unit, decimals, data_type, description]

    示例:
        ["温度1", 0, true, 0.1, 0, "°C", 1, "int16", "传感器1号"]
        ["状态字", 0, false, 1, 0, "", 0, "uint16"]
    """
    fields = {}
    for i, field_name in enumerate(POINT_ARRAY_FIELDS):
        if i < len(arr):
            fields[field_name] = arr[i]

    return PointConfig(
        name=str(fields.get("name", f"点_{fields.get('register_index', 0)}")),
        register_index=int(fields.get("register_index", 0)),
        signed=bool(fields.get("signed", False)),
        scale=float(fields.get("scale", 1.0)),
        offset=float(fields.get("offset", 0.0)),
        unit=str(fields.get("unit", "")),
        decimals=int(fields.get("decimals", 0)),
        data_type=str(fields.get("data_type", "uint16")),
        description=str(fields.get("description", "")),
    )


def _parse_func_code(value) -> int:
    """将功能码解析为整数"""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("0x") or value.startswith("0X"):
            return int(value, 16)
        return int(value)
    raise ValueError(f"无法解析功能码: {value}")


# ============================================================
# 配置生成工具
# ============================================================
def generate_example_config() -> str:
    """生成示例配置 JSON 字符串（展示 03 读保持寄存器和 04 读输入寄存器）"""
    example = {
        "serial": {"baudrate": 9600, "databits": 8, "stopbits": "1", "parity": "无"},
        "slave_id": 1,
        "polling": {"enabled": True, "interval_ms": 2000},
        "groups": [
            {
                "name": "温度传感器",
                "function_code": 3,
                "start_address": 0,
                "quantity": 4,
                "points": [
                    ["通道1温度", 0, True, 0.1, 0, "°C", 1, "int16"],
                    ["通道2温度", 1, True, 0.1, 0, "°C", 1, "int16"],
                    ["通道3温度", 2, True, 0.1, 0, "°C", 1, "int16"],
                    ["通道4温度", 3, True, 0.1, 0, "°C", 1, "int16"],
                ],
            },
            {
                "name": "输入电压",
                "function_code": 4,
                "start_address": 0,
                "quantity": 2,
                "points": [
                    ["电压L1", 0, False, 0.1, 0, "V", 1, "uint16"],
                    ["电压L2", 1, False, 0.1, 0, "V", 1, "uint16"],
                ],
            },
            {
                "name": "运行统计",
                "function_code": 3,
                "start_address": 100,
                "quantity": 2,
                "points": [
                    ["总运行时间", 0, False, 1, 0, "小时", 0, "uint32"],
                    ["累计产量", 1, False, 1, 0, "个", 0, "uint32"],
                ],
            },
        ],
    }
    return json.dumps(example, ensure_ascii=False, indent=2)


# ============================================================
# 配置格式校验
# ============================================================
def validate_config(config: AppConfig) -> list[str]:
    """
    校验配置有效性，返回警告/错误列表

    Returns:
        错误信息列表，为空则表示配置有效
    """
    errors: list[str] = []

    if config.slave_id < 1 or config.slave_id > 247:
        errors.append(f"从站 ID 应在 1-247 之间: {config.slave_id}")

    if config.polling.interval_ms < 100:
        errors.append(f"轮循间隔太短 ({config.polling.interval_ms}ms)，最小 100ms")

    for g in config.groups:
        if not g.enabled:
            continue

        if g.function_code in (0x03, 0x04) and (g.quantity < 1 or g.quantity > 2000):
            errors.append(f"组 '{g.name}': 读取数量应在 1-2000 之间")
        if g.function_code == 0x10 and (g.quantity < 1 or g.quantity > 123):
            errors.append(f"写组 '{g.name}': 寄存器数量应在 1-123 之间")

        for p in g.points:
            if p.register_index >= g.quantity:
                errors.append(
                    f"组 '{g.name}' 点位 '{p.name}': "
                    f"register_index ({p.register_index}) 超出范围 (0-{g.quantity - 1})"
                )

    return errors
