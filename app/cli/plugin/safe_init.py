"""
plugin safe-init core of Lin
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

可重复执行（幂等）的插件初始化核心逻辑，与 Flask 解耦，便于单元测试。

设计要点：
- 本模块只依赖标准库（外加可选的 ``packaging``），不导入 ``app`` / Flask，
  这样回归测试可以脱离完整的应用栈直接加载本文件。
- 真正与应用相关的依赖（读写 ``base.py``、安装依赖、导入插件等）都以
  「协作者」的形式注入 :class:`PluginInitializer`，默认实现见本文件末尾，
  在 ``app/cli/plugin/init.py`` 中完成装配。

:copyright: © 2020 by the Lin team.
:license: MIT, see LICENSE for more details.
"""

import ast
import importlib
import importlib.metadata
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

try:  # packaging 是已声明的依赖，这里仍做兜底以便核心逻辑可独立运行
    from packaging.requirements import Requirement

    _HAS_PACKAGING = True
except Exception:  # pragma: no cover - 仅在缺少 packaging 时触发
    Requirement = None  # type: ignore[assignment,misc]
    _HAS_PACKAGING = False


# ---------------------------------------------------------------------------
# 结果状态枚举
# ---------------------------------------------------------------------------
class DependencyStatus(str, Enum):
    NO_DEPENDENCIES = "no_dependencies"  # 没有依赖需要安装
    ALREADY_SATISFIED = "already_satisfied"  # 依赖已满足，安全模式下跳过安装
    INSTALLED = "installed"  # 执行了安装
    FAILED = "failed"  # 安装失败


class ConfigStatus(str, Enum):
    CREATED = "created"  # 新增了插件配置条目
    UPDATED = "updated"  # 补充了缺失项或记录了版本变更
    UNCHANGED = "unchanged"  # 无任何变化（幂等）
    FAILED = "failed"  # 读取插件配置失败


class DataStatus(str, Enum):
    NO_DATA = "no_data"  # 插件没有 initial_data
    EXECUTED = "executed"  # 执行了 initial_data
    FAILED = "failed"  # initial_data 执行失败


# ---------------------------------------------------------------------------
# 结果数据结构
# ---------------------------------------------------------------------------
@dataclass
class DependencyOutcome:
    status: DependencyStatus
    installed: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    message: str = ""


@dataclass
class ConfigOutcome:
    status: ConfigStatus
    added: List[str] = field(default_factory=list)
    preserved: List[str] = field(default_factory=list)
    version_from: Optional[str] = None
    version_to: Optional[str] = None
    message: str = ""


@dataclass
class DataOutcome:
    status: DataStatus
    message: str = ""


@dataclass
class PluginInitResult:
    name: str
    found: bool = True
    dependency: Optional[DependencyOutcome] = None
    config: Optional[ConfigOutcome] = None
    data: Optional[DataOutcome] = None
    ok: bool = False
    error: Optional[str] = None


class PluginInitError(Exception):
    """非安全模式下，任一步骤失败时抛出，用于保留旧版「立即中断」行为。"""


# 插件条目中的「结构性」字段，始终由初始化器维护
_META_ORDER: Tuple[str, ...] = ("path", "enable", "version")


# ---------------------------------------------------------------------------
# 纯函数：依赖解析与判定
# ---------------------------------------------------------------------------
def parse_requirements(text: str) -> List[str]:
    """解析 requirements.txt 文本，返回依赖声明列表（忽略空行、注释与 pip 选项行）。"""
    requirements: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 去掉行内注释（pip 要求 `#` 前有空白）
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        # 跳过 pip 选项行，如 -r、-e、--hash 等
        if not line or line.startswith("-"):
            continue
        requirements.append(line)
    return requirements


def requirement_name(spec: str) -> str:
    """从依赖声明中提取发行包名，如 ``oss2==2.6.1`` -> ``oss2``。"""
    if _HAS_PACKAGING:
        try:
            return Requirement(spec).name
        except Exception:
            pass
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
    return match.group(1) if match else spec


def is_requirement_satisfied(spec: str, version_lookup: Callable[[str], Optional[str]]) -> bool:
    """判断依赖是否已满足。

    ``version_lookup`` 接收发行包名，返回已安装版本号或 ``None``。
    在有 ``packaging`` 且声明了版本约束时校验版本，否则只判断是否已安装。
    """
    name = requirement_name(spec)
    installed = version_lookup(name)
    if installed is None:
        return False
    if _HAS_PACKAGING:
        try:
            req = Requirement(spec)
            if req.specifier and not req.specifier.contains(installed, prereleases=True):
                return False
        except Exception:
            return True
    return True


# ---------------------------------------------------------------------------
# 纯函数：配置合并（安全模式核心规则）
# ---------------------------------------------------------------------------
def merge_plugin_entry(
    existing: Optional[Dict],
    defaults: Dict,
    *,
    path: str,
    version: str,
) -> Tuple[Dict, ConfigOutcome]:
    """合并单个插件的配置条目。

    规则（「保留已有，只补缺失」）：
    - ``path`` 始终重写为规范值；``enable`` 保留已有、缺省为 ``True``；
      ``version`` 记录为最新版本。
    - 已存在的所有配置项（包括用户自定义项与被改过的默认项）原样保留，绝不覆盖。
    - 仅当默认配置（来自插件 ``config.py``）中的某个键在现有条目里缺失时才补上。
    - 状态：原本不存在 -> ``CREATED``；有新增项或版本号变化 -> ``UPDATED``；否则 ``UNCHANGED``。
    """
    existing_present = existing is not None
    existing = dict(existing) if existing else {}
    old_version = existing.get("version")

    # 先原样保留现有条目，再覆盖结构性字段
    entry: Dict = dict(existing)
    entry["path"] = path
    entry["enable"] = existing.get("enable", True)
    entry["version"] = version

    added: List[str] = []
    for key, value in defaults.items():
        if key.startswith("__"):
            continue
        if key not in entry:
            entry[key] = value
            added.append(key)

    # 被保留下来的现有键（path/version 属于每次重写的结构性字段，不计入）
    preserved = [k for k in existing.keys() if k not in ("path", "version")]

    if not existing_present:
        status = ConfigStatus.CREATED
        message = f"新增插件配置 (version {version})"
    elif added or old_version != version:
        status = ConfigStatus.UPDATED
        bits: List[str] = []
        if old_version != version:
            bits.append(f"version {old_version} → {version}")
        if added:
            bits.append("新增配置项: " + ", ".join(sorted(added)))
        message = "；".join(bits)
    else:
        status = ConfigStatus.UNCHANGED
        message = f"未变更 (version {version})"

    outcome = ConfigOutcome(
        status=status,
        added=sorted(added),
        preserved=sorted(preserved),
        version_from=old_version,
        version_to=version,
        message=message,
    )
    return entry, outcome


# ---------------------------------------------------------------------------
# 纯函数：PLUGIN_PATH 的格式化（保证字节级稳定，从而幂等写入）
# ---------------------------------------------------------------------------
def format_plugin_path(settings: Dict[str, Dict], base_indent: str = "") -> str:
    """把 PLUGIN_PATH 字典格式化为可写入 base.py 的赋值语句字符串。

    输出是确定性的（插件名排序、条目内部 path/enable/version 在前其余键排序、值用
    ``repr``），相同输入必得到相同字节，这样重复运行不会产生无谓的 diff。
    """
    inner = base_indent + "    "
    lines = [f"{base_indent}PLUGIN_PATH = {{"]
    for name in sorted(settings.keys()):
        entry = settings[name]
        ordered_keys = [k for k in _META_ORDER if k in entry]
        ordered_keys += sorted(k for k in entry.keys() if k not in _META_ORDER)
        items = ", ".join(f"{key!r}: {entry[key]!r}" for key in ordered_keys)
        lines.append(f"{inner}{name!r}: {{{items}}},")
    lines.append(f"{base_indent}}}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 配置持久化：读写 base.py 中的 PLUGIN_PATH
# ---------------------------------------------------------------------------
class FileSettingsStore:
    """以文件路径为基础读写 ``app/config/base.py`` 中的 ``PLUGIN_PATH``。

    只依赖标准库（``re`` / ``ast``），不需要 Flask，可在测试中指向临时文件。
    """

    _ASSIGN_RE = re.compile(r"^([ \t]*)PLUGIN_PATH[ \t]*=[ \t]*\{", re.MULTILINE)

    def __init__(self, path: str):
        self.path = path

    def load(self) -> Dict[str, Dict]:
        with open(self.path, "r", encoding="utf-8") as f:
            content = f.read()
        block = self._find_block(content)
        if block is None:
            return {}
        literal = content[block["dict_start"] : block["dict_end"]]
        try:
            value = ast.literal_eval(literal)
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def save(self, settings: Dict[str, Dict]) -> None:
        with open(self.path, "r", encoding="utf-8") as f:
            content = f.read()
        block = self._find_block(content)
        if block is None:
            # base.py 里还没有 PLUGIN_PATH：作为 BaseConfig 的类属性追加到文件末尾
            rendered = format_plugin_path(settings, base_indent="    ")
            separator = "" if content.endswith("\n") else "\n"
            new_content = f"{content}{separator}\n{rendered}\n"
        else:
            rendered = format_plugin_path(settings, base_indent=block["indent"])
            new_content = content[: block["line_start"]] + rendered + content[block["dict_end"] :]
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(new_content)

    def _find_block(self, content: str) -> Optional[Dict]:
        match = self._ASSIGN_RE.search(content)
        if not match:
            return None
        dict_start = content.index("{", match.start())
        dict_end = self._match_brace(content, dict_start)
        if dict_end is None:
            return None
        return {
            "line_start": match.start(),
            "indent": match.group(1),
            "dict_start": dict_start,
            "dict_end": dict_end,
        }

    @staticmethod
    def _match_brace(content: str, open_index: int) -> Optional[int]:
        """返回与 ``open_index`` 处 ``{`` 匹配的 ``}`` 的下一个位置（开区间），找不到返回 None。

        会跳过字符串字面量，避免字符串内部的花括号干扰匹配。
        """
        depth = 0
        in_str: Optional[str] = None
        i = open_index
        length = len(content)
        while i < length:
            ch = content[i]
            if in_str is not None:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
            elif ch in ("'", '"'):
                in_str = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return None


# ---------------------------------------------------------------------------
# 默认协作者实现（被 init.py 装配；测试中以假实现替换）
# ---------------------------------------------------------------------------
def pip_installer(requirements_path: str) -> bool:
    """用当前解释器执行 ``pip install -r``，成功返回 True。"""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", requirements_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return completed.returncode == 0
    except Exception:
        return False


def metadata_version_lookup(name: str) -> Optional[str]:
    """查询已安装发行包的版本号，未安装返回 None。"""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def default_config_loader(plugin_pkg: str, name: str) -> Tuple[Dict, str]:
    """导入插件的 info/config 模块，返回 (默认配置项, 版本号)。"""
    info_mod = importlib.import_module(f"{plugin_pkg}.{name}.info")
    version = getattr(info_mod, "__version__", "0.0.1")
    try:
        cfg_mod = importlib.import_module(f"{plugin_pkg}.{name}.config")
        defaults = {k: v for k, v in vars(cfg_mod).items() if not k.startswith("__")}
    except ModuleNotFoundError:
        defaults = {}
    return defaults, version


def default_data_runner(plugin_import_path: str) -> DataOutcome:
    """导入插件 app 包并在存在 ``initial_data`` 时调用它。"""
    app_pkg = f"{plugin_import_path}.app"
    try:
        module = importlib.import_module(app_pkg)
    except Exception as e:  # 依赖缺失或插件代码异常
        return DataOutcome(DataStatus.FAILED, f"无法导入 {app_pkg}: {e}")
    if not hasattr(module, "initial_data"):
        return DataOutcome(DataStatus.NO_DATA, "无初始数据")
    try:
        module.initial_data()
        return DataOutcome(DataStatus.EXECUTED, "已执行 initial_data")
    except Exception as e:
        return DataOutcome(DataStatus.FAILED, f"initial_data 执行失败: {e}")


# ---------------------------------------------------------------------------
# 编排器
# ---------------------------------------------------------------------------
class PluginInitializer:
    """编排插件初始化：依赖 -> 配置 -> 数据，逐插件产出结构化结果。

    ``safe=True``（安全模式）：跳过已满足的依赖、只补缺失/记录版本变更的配置并保留
    自定义项、任一步骤失败也只记录不中断，最终返回每个插件的完整结果。
    ``safe=False``（兼容旧版）：依赖每次都装、任一步骤失败立即抛出 :class:`PluginInitError`。
    """

    def __init__(
        self,
        names: List[str],
        *,
        root_path: str,
        plugin_pkg: str = "app.plugin",
        safe: bool = False,
        settings_store,
        installer: Callable[[str], bool] = pip_installer,
        version_lookup: Callable[[str], Optional[str]] = metadata_version_lookup,
        data_runner: Callable[[str], DataOutcome] = default_data_runner,
        config_loader: Callable[[str, str], Tuple[Dict, str]] = default_config_loader,
    ):
        self.root_path = root_path
        self.plugin_pkg = plugin_pkg
        self.safe = safe
        self.settings_store = settings_store
        self.installer = installer
        self.version_lookup = version_lookup
        self.data_runner = data_runner
        self.config_loader = config_loader
        self.names = self._resolve_names(names, root_path)

    def run(self) -> List[PluginInitResult]:
        results: List[PluginInitResult] = []
        # 以现有设置为基底，未参与本次初始化的插件保持不动
        final: Dict[str, Dict] = dict(self.settings_store.load())
        try:
            for name in self.names:
                results.append(self._init_one(name, final))
        finally:
            # 即便非安全模式中途抛出，也保留已生成的配置（与旧版「先写配置再处理数据」一致）
            self.settings_store.save(final)
        return results

    # -- 单插件三步 -------------------------------------------------------
    def _init_one(self, name: str, final: Dict[str, Dict]) -> PluginInitResult:
        result = PluginInitResult(name=name)
        plugin_dir = os.path.join(self.root_path, "plugin", name)
        if not os.path.isdir(plugin_dir):
            result.found = False
            result.ok = False
            result.error = f"未找到插件目录: {plugin_dir}"
            if not self.safe:
                raise PluginInitError(result.error)
            return result

        result.dependency = self._init_dependencies(plugin_dir)
        self._maybe_abort(result.dependency.status is DependencyStatus.FAILED, result.dependency.message)

        result.config = self._init_config(name, final)
        self._maybe_abort(result.config.status is ConfigStatus.FAILED, result.config.message)

        result.data = self.data_runner(f"{self.plugin_pkg}.{name}")
        self._maybe_abort(result.data.status is DataStatus.FAILED, result.data.message)

        result.ok = (
            result.dependency.status is not DependencyStatus.FAILED
            and result.config.status is not ConfigStatus.FAILED
            and result.data.status is not DataStatus.FAILED
        )
        return result

    def _init_dependencies(self, plugin_dir: str) -> DependencyOutcome:
        req_file = os.path.join(plugin_dir, "requirements.txt")
        if not os.path.exists(req_file) or os.path.getsize(req_file) == 0:
            return DependencyOutcome(DependencyStatus.NO_DEPENDENCIES, message="无依赖")
        with open(req_file, "r", encoding="utf-8") as f:
            requirements = parse_requirements(f.read())
        if not requirements:
            return DependencyOutcome(DependencyStatus.NO_DEPENDENCIES, message="无依赖")

        all_names = [requirement_name(r) for r in requirements]

        if self.safe:
            missing = [r for r in requirements if not is_requirement_satisfied(r, self.version_lookup)]
            if not missing:
                return DependencyOutcome(
                    DependencyStatus.ALREADY_SATISFIED,
                    installed=all_names,
                    message="依赖已满足: " + ", ".join(all_names),
                )
            missing_names = [requirement_name(r) for r in missing]
            if self.installer(req_file):
                return DependencyOutcome(
                    DependencyStatus.INSTALLED,
                    installed=missing_names,
                    message="已安装缺失依赖: " + ", ".join(missing_names),
                )
            return DependencyOutcome(
                DependencyStatus.FAILED,
                missing=missing_names,
                message="依赖安装失败: " + ", ".join(missing_names),
            )

        # 兼容旧版：每次都安装
        if self.installer(req_file):
            return DependencyOutcome(
                DependencyStatus.INSTALLED,
                installed=all_names,
                message="已安装依赖: " + ", ".join(all_names),
            )
        return DependencyOutcome(
            DependencyStatus.FAILED,
            missing=all_names,
            message="依赖安装失败: " + ", ".join(all_names),
        )

    def _init_config(self, name: str, final: Dict[str, Dict]) -> ConfigOutcome:
        try:
            defaults, version = self.config_loader(self.plugin_pkg, name)
        except Exception as e:
            return ConfigOutcome(ConfigStatus.FAILED, message=f"读取插件配置失败: {e}")
        entry, outcome = merge_plugin_entry(
            final.get(name),
            defaults,
            path=f"{self.plugin_pkg}.{name}",
            version=version,
        )
        final[name] = entry
        return outcome

    def _maybe_abort(self, failed: bool, message: str) -> None:
        if failed and not self.safe:
            raise PluginInitError(message)

    # -- 名称解析 ---------------------------------------------------------
    @staticmethod
    def _resolve_names(names: List[str], root_path: str) -> List[str]:
        """把输入的名称（可能含空格分隔或 ``*``）解析为去重后的插件名列表。"""
        flat: List[str] = []
        for raw in names:
            flat.extend(str(raw).split())

        discovered: List[str] = []
        if "*" in flat:
            plugin_dir = os.path.join(root_path, "plugin")
            if os.path.isdir(plugin_dir):
                for entry in sorted(os.listdir(plugin_dir)):
                    if entry.startswith((".", "_")):
                        continue
                    if os.path.isdir(os.path.join(plugin_dir, entry)):
                        discovered.append(entry)

        result: List[str] = []
        for token in flat:
            if token == "*":
                for d in discovered:
                    if d not in result:
                        result.append(d)
            elif token and token not in result:
                result.append(token)
        return result


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------
def render_report(results: List[PluginInitResult], *, safe: bool = False) -> str:
    """把初始化结果渲染为人类可读的报告文本。"""
    divider = "─" * 56
    lines: List[str] = []
    lines.append("插件初始化报告" + ("（安全模式）" if safe else ""))
    lines.append(divider)

    ok_count = 0
    for r in results:
        if r.ok:
            ok_count += 1
        lines.append(f"{r.name}    [{'OK' if r.ok else 'FAILED'}]")
        if not r.found:
            lines.append(f"  错误    {r.error}")
            continue
        if r.dependency is not None:
            lines.append(f"  依赖    {r.dependency.message}")
        if r.config is not None:
            lines.append(f"  配置    {r.config.message}")
        if r.data is not None:
            lines.append(f"  数据    {r.data.message}")

    lines.append(divider)
    lines.append(f"成功 {ok_count}/{len(results)}")
    return "\n".join(lines)
