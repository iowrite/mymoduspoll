"""
轮循引擎模块
============
按配置周期性地发送 Modbus 读取命令，并解析响应数据。
支持多组轮循、超时处理、事件回调。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from modbus_modules.config_parser import AppConfig, GroupConfig, PointConfig
from modbus_modules.modbus_core import (
    add_crc,
    build_modbus_frame,
    convert_register_value,
    convert_registers_to_value,
)


@dataclass
class PointValue:
    """单点的实时数据"""

    name: str
    raw_value: int | list[int] | None
    converted_value: float | int | str | None
    unit: str
    quality: str  # "good", "error", "timeout", "alarm"
    group_name: str
    register_index: int = 0
    timestamp: str = ""
    hex_str: str = ""

    def display_value(self, decimals: int = 2) -> str:
        """获取格式化显示值"""
        if self.converted_value is None:
            return "---"
        if isinstance(self.converted_value, str):
            return self.converted_value
        if isinstance(self.raw_value, int):
            if self.raw_value == 0xFFFF or self.raw_value == 0x8000:
                # 可能是无效值
                pass
        if isinstance(self.converted_value, float):
            return f"{self.converted_value:.{decimals}f}"
        return str(self.converted_value)


@dataclass
class GroupPollResult:
    """一组的轮循结果"""

    group_name: str
    success: bool
    timestamp: str
    error_message: str = ""
    points: list[PointValue] = field(default_factory=list)
    raw_hex: str = ""


class PollingEngine:
    """
    轮循引擎
    在独立线程中周期性地轮循配置的 Modbus 组。
    """

    def __init__(self):
        self._config: Optional[AppConfig] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._current_group_index = 0
        self._target_group: Optional[str] = None  # 单组轮循：不为 None 时只轮循该组
        self._lock = threading.Lock()

        # 回调: 提供给外部发送 Modbus 请求
        # send_request(bytes) -> bytes | None (返回响应或 None)
        self.send_request: Optional[Callable[[bytes], Optional[bytes]]] = None

        # 回调: 通知外部有新的轮循结果
        self.on_poll_result: Optional[Callable[[GroupPollResult], None]] = None

        # 回调: 通知外部引擎状态变化
        self.on_status_change: Optional[Callable[[bool, str], None]] = None

        # 统计数据
        self.total_polls = 0
        self.successful_polls = 0
        self.failed_polls = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def config(self) -> Optional[AppConfig]:
        return self._config

    def load_config(self, config: AppConfig) -> None:
        """加载配置"""
        self._config = config

    def set_target_group(self, group_name: Optional[str]) -> None:
        """设置单组轮循目标，None=轮循所有读组"""
        self._target_group = group_name

    def start(self, target_group: Optional[str] = None) -> bool:
        """启动轮循（跳过写组 0x10）
        Args:
            target_group: 指定只轮循该组，None=轮循所有读组
        """
        if self._running:
            return False

        if not self._config or not self._config.groups:
            return False

        # 检查是否有启用的读组
        enabled_groups = [
            g
            for g in self._config.groups
            if g.enabled and g.function_code in (0x03, 0x04)
        ]
        if not enabled_groups:
            return False

        self._target_group = target_group
        self._running = True
        self._current_group_index = 0
        self.total_polls = 0
        self.successful_polls = 0
        self.failed_polls = 0

        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

        if self.on_status_change:
            self.on_status_change(True, "轮循已启动")

        return True

    def stop(self) -> None:
        """停止轮循"""
        self._running = False
        if self.on_status_change:
            self.on_status_change(False, "轮循已停止")

    def _poll_loop(self) -> None:
        """轮循主循环
        - 如果设置了 target_group，只轮循该组
        - 否则轮循所有读组（03/04），循环切换
        """
        config = self._config
        if not config:
            return

        # 只轮循读组（03/04），跳过写组（0x10）
        enabled_groups = [
            g for g in config.groups if g.enabled and g.function_code in (0x03, 0x04)
        ]
        if not enabled_groups:
            return

        while self._running:
            # 单组模式：只轮循指定的组
            if self._target_group:
                group = None
                for g in enabled_groups:
                    if g.name == self._target_group:
                        group = g
                        break
                if group is None:
                    # 目标组不存在或不是读组，等待后重试
                    time.sleep(0.5)
                    continue
            else:
                # 多组模式：循环切换
                group = enabled_groups[self._current_group_index]
                self._current_group_index = (self._current_group_index + 1) % len(
                    enabled_groups
                )

            result = self._poll_group(group, config.slave_id)

            if result:
                self.total_polls += 1
                if result.success:
                    self.successful_polls += 1
                else:
                    self.failed_polls += 1

                if self.on_poll_result:
                    self.on_poll_result(result)

            # 使用本组的间隔等待
            time.sleep(group.interval_ms / 1000.0)

    def _poll_group(
        self, group: GroupConfig, slave_id: int
    ) -> Optional[GroupPollResult]:
        """轮循单个组"""
        import datetime

        if not self.send_request:
            return None

        timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:12]

        try:
            # 构建请求帧
            params = [group.start_address, group.quantity]
            pdu = build_modbus_frame(slave_id, group.function_code, params)
            frame = add_crc(pdu)

            # 发送并等待响应
            response = self.send_request(frame)

            if response is None:
                return GroupPollResult(
                    group_name=group.name,
                    success=False,
                    timestamp=timestamp,
                    error_message="无响应 (超时)",
                    points=[],
                    raw_hex="",
                )

            # 解析响应
            from modbus_modules.modbus_core import parse_modbus_response

            parsed = parse_modbus_response(response)

            if "error" in parsed:
                return GroupPollResult(
                    group_name=group.name,
                    success=False,
                    timestamp=timestamp,
                    error_message=parsed["error"],
                    points=[],
                    raw_hex=parsed.get("data_hex", ""),
                )

            # 转换点位数据
            point_values = self._extract_point_values(group, parsed)

            return GroupPollResult(
                group_name=group.name,
                success=True,
                timestamp=timestamp,
                points=point_values,
                raw_hex=parsed.get("data_hex", ""),
            )

        except Exception as e:
            return GroupPollResult(
                group_name=group.name,
                success=False,
                timestamp=timestamp,
                error_message=str(e),
                points=[],
                raw_hex="",
            )

    def _extract_point_values(
        self, group: GroupConfig, parsed: dict
    ) -> list[PointValue]:
        """从解析结果中提取各点位的数值（仅支持 03/04 寄存器读取）"""
        point_values: list[PointValue] = []
        registers = parsed.get("registers", [])

        for point in group.points:
            raw = None
            converted = None
            quality = "good"
            idx = point.register_index

            if idx < len(registers):
                raw = registers[idx]

                # 根据 data_type 判断是否需要多寄存器组合
                if point.data_type in ("uint32", "int32", "float32"):
                    if idx + 1 < len(registers):
                        two_regs = registers[idx : idx + 2]
                        raw = two_regs
                        converted = convert_registers_to_value(
                            two_regs,
                            data_type=point.data_type,
                            byte_order=point.byte_order,
                            word_order=point.word_order,
                        )
                    else:
                        converted = None
                        quality = "error"
                else:
                    converted = convert_register_value(
                        registers[idx],
                        signed=point.signed,
                        scale=point.scale,
                        offset=point.offset,
                    )

                # 告警检查
                if isinstance(converted, (int, float)):
                    if point.min_value is not None and converted < point.min_value:
                        quality = "alarm_low"
                    elif point.max_value is not None and converted > point.max_value:
                        quality = "alarm_high"
            else:
                quality = "error"

            hex_str = ""
            if isinstance(raw, int):
                hex_str = f"0x{raw:04X}"
            elif isinstance(raw, list):
                hex_str = " ".join(f"0x{r:04X}" for r in raw)

            point_values.append(
                PointValue(
                    name=point.name,
                    raw_value=raw,
                    converted_value=converted,
                    unit=point.unit,
                    quality=quality,
                    group_name=group.name,
                    register_index=point.register_index,
                    timestamp="",
                    hex_str=hex_str,
                )
            )

        return point_values

    def get_stats(self) -> dict:
        """获取统计数据"""
        return {
            "total": self.total_polls,
            "success": self.successful_polls,
            "failed": self.failed_polls,
            "success_rate": (
                (self.successful_polls / self.total_polls * 100)
                if self.total_polls > 0
                else 0
            ),
        }
