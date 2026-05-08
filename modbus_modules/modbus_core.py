"""
Modbus 协议核心模块
====================
提供 CRC16 计算、RTU 帧构建、响应解析等基础功能。
"""

from __future__ import annotations

import re
import struct


# ============================================================
# CRC16 计算
# ============================================================
def calc_crc16(data: bytes) -> int:
    """计算 Modbus RTU CRC16 (Modbus 多项式 0x8005)"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def add_crc(frame: bytes) -> bytes:
    """将 CRC 附加到帧尾 (小端序)"""
    crc = calc_crc16(frame)
    return frame + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def validate_crc(frame: bytes) -> bool:
    """验证帧 CRC 是否正确"""
    if len(frame) < 4:
        return False
    recv_crc = frame[-2] | (frame[-1] << 8)
    calc = calc_crc16(frame[:-2])
    return recv_crc == calc


# ============================================================
# 十六进制转换工具
# ============================================================
def hex_str_to_bytes(hex_str: str) -> bytes:
    """将空格分隔的十六进制字符串转为 bytes"""
    hex_str = hex_str.strip()
    if not hex_str:
        return b""
    hex_str = re.sub(r"\s+", " ", hex_str)
    parts = hex_str.split()
    return bytes(int(p, 16) for p in parts)


def bytes_to_hex_str(data: bytes, sep: str = " ") -> str:
    """将 bytes 转为空格分隔的十六进制字符串"""
    return sep.join(f"{b:02X}" for b in data)


# ============================================================
# Modbus 功能码定义
# ============================================================
MODBUS_FUNCTIONS = {
    0x03: "读保持寄存器 (Read Holding Registers)",
    0x04: "读输入寄存器 (Read Input Registers)",
    0x10: "写多寄存器 (Write Multiple Registers)",
}

# 读取类功能码
READ_FUNCTIONS = {0x03, 0x04}
# 写入类功能码
WRITE_FUNCTIONS = {0x10}


def is_read_function(func: int) -> bool:
    """判断是否为读取类功能码"""
    return func in READ_FUNCTIONS


def is_write_function(func: int) -> bool:
    """判断是否为写入类功能码"""
    return func in WRITE_FUNCTIONS


# ============================================================
# 帧构建
# ============================================================
def build_modbus_frame(slave_id: int, func: int, params: list) -> bytes:
    """
    构建 Modbus RTU 请求帧 (不含 CRC)
    params 依 func 而定:
      01/02: [start_addr, quantity]
      03/04: [start_addr, quantity]
      05: [addr, value]   (value: 0xFF00=ON, 0x0000=OFF)
      06: [addr, value]
      0F: [start_addr, quantity, byte_data...]
      10: [start_addr, quantity, word_data...]
    """
    pdu = bytes([slave_id, func])
    if func in (0x03, 0x04):
        # 读保持/输入寄存器: [起始地址, 寄存器数量]
        pdu += struct.pack(">HH", params[0], params[1])
    elif func == 0x10:
        # 写多寄存器: [起始地址, 寄存器数量, 字节数, 数据...]
        start_addr, quantity = params[0], params[1]
        byte_count = quantity * 2
        data_bytes = params[2]
        pdu += struct.pack(">HHB", start_addr, quantity, byte_count)
        pdu += bytes(data_bytes[:byte_count])
    else:
        raise ValueError(f"不支持的功能码: 0x{func:02X}")
    return pdu


# ============================================================
# 响应解析
# ============================================================
def parse_modbus_response(frame: bytes) -> dict:
    """
    解析 Modbus RTU 响应帧，返回一个字典描述结果
    """
    if len(frame) < 4:
        return {"error": "帧太短 (< 4 bytes)"}

    if not validate_crc(frame):
        return {"error": "CRC 校验失败"}

    data = frame[:-2]  # 去掉 CRC
    slave_id = data[0]
    func = data[1]

    # 异常响应
    if func & 0x80:
        exc_code = data[2]
        exc_msgs = {
            0x01: "非法功能码",
            0x02: "非法数据地址",
            0x03: "非法数据值",
            0x04: "从站设备故障",
            0x05: "确认",
            0x06: "从站设备忙",
            0x07: "否定确认",
            0x08: "存储器奇偶错误",
        }
        return {
            "slave_id": slave_id,
            "function": func & 0x7F,
            "error": f"异常响应: {exc_msgs.get(exc_code, f'代码 0x{exc_code:02X}')}",
            "exception_code": exc_code,
        }

    result: dict = {
        "slave_id": slave_id,
        "function": func,
        "function_name": MODBUS_FUNCTIONS.get(func, f"未知 (0x{func:02X})"),
    }

    if func in (0x03, 0x04):
        byte_count = data[2]
        reg_data = data[3 : 3 + byte_count]
        registers = []
        for i in range(0, len(reg_data), 2):
            if i + 1 < len(reg_data):
                registers.append((reg_data[i] << 8) | reg_data[i + 1])
        result["byte_count"] = byte_count
        result["registers"] = registers
        result["data_hex"] = bytes_to_hex_str(reg_data)
    elif func == 0x10:
        addr = (data[2] << 8) | data[3]
        qty = (data[4] << 8) | data[5]
        result["address"] = addr
        result["quantity"] = qty
        result["data_hex"] = bytes_to_hex_str(data[2:])
    else:
        result["raw_data"] = bytes_to_hex_str(data[2:])
        result["data_hex"] = bytes_to_hex_str(data[2:])

    return result


# ============================================================
# 数据值转换工具
# ============================================================
def convert_register_value(
    raw_value: int,
    signed: bool = False,
    scale: float = 1.0,
    offset: float = 0.0,
) -> float:
    """
    将原始寄存器值按配置转换

    Args:
        raw_value: 原始寄存器值 (0-65535)
        signed: 是否视为有符号数
        scale: 倍率
        offset: 偏移量

    Returns:
        转换后的数值
    """
    if signed:
        # 将 16 位无符号转为有符号
        if raw_value >= 0x8000:
            raw_value -= 0x10000
    return raw_value * scale + offset


def convert_registers_to_value(
    registers: list[int],
    data_type: str = "uint16",
    byte_order: str = "big",
    word_order: str = "big",
) -> float | int:
    """
    将多个寄存器组合转换为数值

    Args:
        registers: 寄存器列表
        data_type: 数据类型 (uint16, int16, uint32, int32, float32)
        byte_order: 字节序 (big/little)
        word_order: 字序 (big/little)

    Returns:
        转换后的数值
    """
    if data_type in ("uint16", "int16"):
        val = registers[0]
        if data_type == "int16" and val >= 0x8000:
            val -= 0x10000
        return val

    # 32 位类型需要两个寄存器
    if len(registers) < 2:
        return 0

    if word_order == "big":
        high, low = registers[0], registers[1]
    else:
        low, high = registers[0], registers[1]

    if byte_order == "big":
        combined = (high << 16) | low
    else:
        combined = (low << 16) | high

    if data_type == "uint32":
        return combined
    elif data_type == "int32":
        if combined >= 0x80000000:
            combined -= 0x100000000
        return combined
    elif data_type == "float32":
        import struct as _struct

        return _struct.unpack(">f", _struct.pack(">I", combined))[0]

    return combined
