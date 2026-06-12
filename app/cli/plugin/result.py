"""
插件初始化结果追踪模块

:copyright: © 2020 by the Lin team.
:license: MIT, see LICENSE for more details.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class StepStatus(Enum):
    """单步骤执行状态"""

    SKIPPED = "skipped"  # 已满足条件，跳过
    SUCCESS = "success"  # 执行成功
    FAILED = "failed"  # 执行失败
    PRESERVED = "preserved"  # 保留已有配置


@dataclass
class StepResult:
    """单步骤执行结果"""

    status: StepStatus
    message: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class PluginResult:
    """单个插件的完整初始化结果"""

    name: str
    dependency: StepResult  # 依赖安装结果
    config: StepResult  # 配置写入结果
    data: StepResult  # 数据初始化结果

    @property
    def success(self) -> bool:
        """所有步骤是否均为成功状态（含跳过和保留）"""
        return all(
            getattr(self, step).status in (StepStatus.SUCCESS, StepStatus.SKIPPED, StepStatus.PRESERVED)
            for step in ("dependency", "config", "data")
        )


@dataclass
class InitReport:
    """插件初始化汇总报告"""

    results: List[PluginResult] = field(default_factory=list)
    safe_mode: bool = False

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.success)

    def add(self, result: PluginResult):
        self.results.append(result)

    def format_report(self) -> str:
        """生成格式化的汇总报告字符串"""
        mode_label = "safe 模式" if self.safe_mode else "标准模式"
        lines = [
            f"====== 插件初始化报告 ({mode_label}) ======",
            f"{'插件':<12} | {'依赖':<16} | {'配置':<16} | {'数据':<16}",
            "-" * 70,
        ]

        for r in self.results:
            dep = self._format_step(r.dependency)
            cfg = self._format_step(r.config)
            dat = self._format_step(r.data)
            lines.append(f"{r.name:<12} | {dep:<16} | {cfg:<16} | {dat:<16}")

        lines.append("-" * 70)
        lines.append(f"成功: {self.success_count}, 失败: {self.fail_count}, 总计: {len(self.results)}")
        return "\n".join(lines)

    @staticmethod
    def _format_step(step: StepResult) -> str:
        status_map = {
            StepStatus.SUCCESS: "✓ " + step.message,
            StepStatus.SKIPPED: "✓ " + (step.message or "已跳过"),
            StepStatus.PRESERVED: "✓ " + (step.message or "已保留"),
            StepStatus.FAILED: "✗ " + step.message,
        }
        return status_map.get(step.status, str(step.status))
