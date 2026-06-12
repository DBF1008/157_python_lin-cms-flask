"""
插件初始化（安全/幂等模式）的回归测试。

为了脱离完整的 Flask 应用栈运行，这里直接按文件路径加载 ``app/cli/plugin/safe_init.py``
（该模块只依赖标准库 + 可选的 packaging）。编排器的所有外部协作者（配置读写、依赖
安装、版本查询、插件导入/数据初始化）都用假实现注入，因此测试不会触碰 pip、数据库或
真实的 base.py。

:copyright: © 2020 by the Lin team.
:license: MIT, see LICENSE for more details.
"""

import copy
import importlib.util
import pathlib
import sys

import pytest

# --- 按路径加载被测核心模块（避免触发 app/__init__ 的重型导入） ---
_CORE = pathlib.Path(__file__).resolve().parents[1] / "app" / "cli" / "plugin" / "safe_init.py"
_spec = importlib.util.spec_from_file_location("lin_safe_init_under_test", _CORE)
safe_init = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = safe_init  # 让 dataclass/enum 正确解析自身模块
_spec.loader.exec_module(safe_init)

si = safe_init


# ---------------------------------------------------------------------------
# 测试替身（fakes）
# ---------------------------------------------------------------------------
class InMemoryStore:
    """内存版 SettingsStore，记录每次保存的快照。"""

    def __init__(self, data=None):
        self._data = copy.deepcopy(data or {})
        self.saved = []

    def load(self):
        return copy.deepcopy(self._data)

    def save(self, settings):
        self._data = copy.deepcopy(settings)
        self.saved.append(copy.deepcopy(settings))


class FakeInstaller:
    """记录调用并返回固定结果的安装器。"""

    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def __call__(self, requirements_path):
        self.calls.append(requirements_path)
        return self.ok


def make_version_lookup(installed):
    """installed: {发行包名: 版本}。未命中返回 None。"""
    return lambda name: installed.get(name)


def make_config_loader(table):
    """table: {插件名: (defaults_dict, version)}。未命中抛 ModuleNotFoundError。"""

    def loader(plugin_pkg, name):
        if name not in table:
            raise ModuleNotFoundError(f"No plugin config for {name}")
        defaults, version = table[name]
        return dict(defaults), version

    return loader


def make_data_runner(outcome=None):
    calls = []
    result = outcome or si.DataOutcome(si.DataStatus.NO_DATA, "无初始数据")

    def runner(plugin_import_path):
        calls.append(plugin_import_path)
        return result

    runner.calls = calls
    return runner


def make_plugin(root, name, requirements=None):
    """在 ``root/plugin/<name>`` 下建插件目录；requirements 为 None 时不建 requirements.txt。"""
    plugin_dir = root / "plugin" / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    if requirements is not None:
        (plugin_dir / "requirements.txt").write_text(requirements, encoding="utf-8")
    return plugin_dir


def build_initializer(tmp_path, names, *, safe=True, store=None, installer=None,
                      installed=None, data_runner=None, config_loader=None):
    return si.PluginInitializer(
        names,
        root_path=str(tmp_path),
        safe=safe,
        settings_store=store if store is not None else InMemoryStore(),
        installer=installer if installer is not None else FakeInstaller(),
        version_lookup=make_version_lookup(installed or {}),
        data_runner=data_runner if data_runner is not None else make_data_runner(),
        config_loader=config_loader if config_loader is not None else make_config_loader({}),
    )


# ---------------------------------------------------------------------------
# 纯函数：配置合并规则（“保留已有，只补缺失”）
# ---------------------------------------------------------------------------
def test_merge_creates_new_entry():
    entry, outcome = si.merge_plugin_entry(None, {"limit": 20}, path="app.plugin.poem", version="0.0.1")
    assert outcome.status is si.ConfigStatus.CREATED
    assert entry == {"path": "app.plugin.poem", "enable": True, "version": "0.0.1", "limit": 20}


def test_merge_adds_missing_default_key_and_preserves_existing():
    existing = {"path": "app.plugin.oss", "enable": True, "version": "0.0.1", "access_key_id": "MINE"}
    defaults = {"access_key_id": "not complete", "bucket_name": "not complete"}
    entry, outcome = si.merge_plugin_entry(existing, defaults, path="app.plugin.oss", version="0.0.1")

    assert entry["access_key_id"] == "MINE"  # 已有值原样保留，不被默认值覆盖
    assert entry["bucket_name"] == "not complete"  # 缺失的默认项补上
    assert outcome.status is si.ConfigStatus.UPDATED
    assert outcome.added == ["bucket_name"]


def test_merge_version_bump_preserves_edited_and_custom_only_adds_new():
    existing = {
        "path": "app.plugin.poem",
        "enable": False,  # 用户禁用了插件
        "version": "0.0.1",
        "limit": 50,  # 用户改过的默认项
        "mine": 1,  # 用户自定义项
    }
    defaults = {"limit": 20, "page": 5}
    entry, outcome = si.merge_plugin_entry(existing, defaults, path="app.plugin.poem", version="0.0.2")

    assert entry["limit"] == 50  # 版本变更也不覆盖已有值
    assert entry["mine"] == 1  # 自定义项保留
    assert entry["enable"] is False  # enable 保留
    assert entry["page"] == 5  # 仅新增全新默认项
    assert entry["version"] == "0.0.2"  # 记录新版本
    assert outcome.status is si.ConfigStatus.UPDATED
    assert outcome.added == ["page"]
    assert outcome.version_from == "0.0.1" and outcome.version_to == "0.0.2"


def test_merge_unchanged_is_idempotent():
    existing = {"path": "app.plugin.poem", "enable": True, "version": "0.0.1", "limit": 20}
    e1, o1 = si.merge_plugin_entry(existing, {"limit": 20}, path="app.plugin.poem", version="0.0.1")
    assert o1.status is si.ConfigStatus.UNCHANGED
    e2, o2 = si.merge_plugin_entry(e1, {"limit": 20}, path="app.plugin.poem", version="0.0.1")
    assert e2 == e1
    assert o2.status is si.ConfigStatus.UNCHANGED


# ---------------------------------------------------------------------------
# 纯函数：依赖解析与判定
# ---------------------------------------------------------------------------
def test_parse_requirements_filters_comments_blanks_and_options():
    text = "oss2==2.6.1\n# 注释\n\n-r base.txt\nqiniu  # 行内注释\n--hash=sha256:x\n"
    assert si.parse_requirements(text) == ["oss2==2.6.1", "qiniu"]


def test_is_requirement_satisfied():
    lookup = make_version_lookup({"oss2": "2.6.1"})
    assert si.is_requirement_satisfied("oss2", lookup) is True  # 已安装、无版本约束
    assert si.is_requirement_satisfied("oss2==2.6.1", lookup) is True  # 版本匹配
    assert si.is_requirement_satisfied("missing", lookup) is False  # 未安装
    if si._HAS_PACKAGING:
        assert si.is_requirement_satisfied("oss2==9.9.9", lookup) is False  # 版本不匹配


# ---------------------------------------------------------------------------
# 编排器：依赖步骤
# ---------------------------------------------------------------------------
def test_dependency_already_satisfied_skips_install(tmp_path):
    make_plugin(tmp_path, "oss", requirements="oss2==2.6.1\n")
    installer = FakeInstaller(ok=True)
    init = build_initializer(
        tmp_path, ["oss"], installer=installer, installed={"oss2": "2.6.1"},
        config_loader=make_config_loader({"oss": ({}, "0.0.1")}),
    )
    (res,) = init.run()
    assert res.dependency.status is si.DependencyStatus.ALREADY_SATISFIED
    assert installer.calls == []  # 未触发安装 -> 可重复执行


def test_dependency_missing_triggers_install(tmp_path):
    make_plugin(tmp_path, "oss", requirements="oss2==2.6.1\n")
    installer = FakeInstaller(ok=True)
    init = build_initializer(
        tmp_path, ["oss"], installer=installer, installed={},  # oss2 未安装
        config_loader=make_config_loader({"oss": ({}, "0.0.1")}),
    )
    (res,) = init.run()
    assert res.dependency.status is si.DependencyStatus.INSTALLED
    assert len(installer.calls) == 1


def test_no_requirements_means_no_dependencies(tmp_path):
    make_plugin(tmp_path, "poem", requirements="")  # 空文件
    installer = FakeInstaller()
    init = build_initializer(
        tmp_path, ["poem"], installer=installer,
        config_loader=make_config_loader({"poem": ({"limit": 20}, "0.0.1")}),
    )
    (res,) = init.run()
    assert res.dependency.status is si.DependencyStatus.NO_DEPENDENCIES
    assert installer.calls == []


def test_safe_mode_continues_after_dependency_failure(tmp_path):
    make_plugin(tmp_path, "oss", requirements="oss2==2.6.1\n")
    make_plugin(tmp_path, "poem", requirements="")
    store = InMemoryStore()
    init = build_initializer(
        tmp_path, ["oss", "poem"], store=store, installer=FakeInstaller(ok=False), installed={},
        config_loader=make_config_loader({"oss": ({}, "0.0.1"), "poem": ({"limit": 20}, "0.0.1")}),
    )
    results = init.run()  # 不应抛出
    assert results[0].name == "oss"
    assert results[0].dependency.status is si.DependencyStatus.FAILED
    assert results[0].ok is False
    assert results[1].name == "poem"
    assert results[1].ok is True  # 后续插件仍被处理
    # 失败插件的配置在安全模式下仍会合并写入
    assert set(store.load().keys()) == {"oss", "poem"}


def test_non_safe_mode_aborts_on_dependency_failure(tmp_path):
    make_plugin(tmp_path, "oss", requirements="oss2==2.6.1\n")
    init = build_initializer(
        tmp_path, ["oss"], safe=False, installer=FakeInstaller(ok=False), installed={"oss2": "2.6.1"},
        config_loader=make_config_loader({"oss": ({}, "0.0.1")}),
    )
    # 非安全模式即使依赖已安装也会再装一次；这里安装失败 -> 立即中断
    with pytest.raises(si.PluginInitError):
        init.run()


# ---------------------------------------------------------------------------
# 编排器：配置与数据步骤
# ---------------------------------------------------------------------------
def test_data_executed_is_reported(tmp_path):
    make_plugin(tmp_path, "poem", requirements="")
    runner = make_data_runner(si.DataOutcome(si.DataStatus.EXECUTED, "已执行 initial_data"))
    init = build_initializer(
        tmp_path, ["poem"], data_runner=runner,
        config_loader=make_config_loader({"poem": ({"limit": 20}, "0.0.1")}),
    )
    (res,) = init.run()
    assert res.data.status is si.DataStatus.EXECUTED
    assert runner.calls == ["app.plugin.poem"]
    assert res.ok is True


def test_data_failure_marks_not_ok_but_does_not_raise_in_safe(tmp_path):
    make_plugin(tmp_path, "poem", requirements="")
    runner = make_data_runner(si.DataOutcome(si.DataStatus.FAILED, "boom"))
    init = build_initializer(
        tmp_path, ["poem"], data_runner=runner,
        config_loader=make_config_loader({"poem": ({"limit": 20}, "0.0.1")}),
    )
    (res,) = init.run()
    assert res.data.status is si.DataStatus.FAILED
    assert res.ok is False


def test_config_load_failure_is_captured_in_safe(tmp_path):
    make_plugin(tmp_path, "broken", requirements="")
    init = build_initializer(
        tmp_path, ["broken"],
        config_loader=make_config_loader({}),  # 任何名字都抛 ModuleNotFoundError
    )
    (res,) = init.run()
    assert res.config.status is si.ConfigStatus.FAILED
    assert res.ok is False


# ---------------------------------------------------------------------------
# 编排器：名称解析、未找到、报告、幂等
# ---------------------------------------------------------------------------
def test_unknown_plugin_is_reported_not_raised_in_safe(tmp_path):
    make_plugin(tmp_path, "poem", requirements="")
    init = build_initializer(
        tmp_path, ["ghost", "poem"],
        config_loader=make_config_loader({"poem": ({"limit": 20}, "0.0.1")}),
    )
    results = init.run()
    assert results[0].found is False
    assert results[0].ok is False
    assert results[0].error
    assert results[1].ok is True


def test_star_resolves_all_plugin_dirs(tmp_path):
    make_plugin(tmp_path, "poem", requirements="")
    make_plugin(tmp_path, "oss", requirements="")
    (tmp_path / "plugin" / "__pycache__").mkdir()  # 应被忽略
    init = build_initializer(
        tmp_path, ["*"],
        config_loader=make_config_loader({"poem": ({"limit": 20}, "0.0.1"), "oss": ({}, "0.0.1")}),
    )
    assert sorted(init.names) == ["oss", "poem"]
    results = init.run()
    assert {r.name for r in results} == {"oss", "poem"}


def test_report_lists_each_dimension(tmp_path):
    make_plugin(tmp_path, "poem", requirements="")
    init = build_initializer(
        tmp_path, ["poem"],
        data_runner=make_data_runner(si.DataOutcome(si.DataStatus.EXECUTED, "已执行 initial_data")),
        config_loader=make_config_loader({"poem": ({"limit": 20}, "0.0.1")}),
    )
    results = init.run()
    for r in results:
        if r.found:
            assert r.dependency is not None
            assert r.config is not None
            assert r.data is not None

    text = si.render_report(results, safe=True)
    assert "插件初始化报告" in text
    assert "安全模式" in text
    assert "poem" in text
    assert "依赖" in text and "配置" in text and "数据" in text
    assert "成功 1/1" in text


def test_full_run_is_idempotent(tmp_path):
    make_plugin(tmp_path, "poem", requirements="")
    make_plugin(tmp_path, "oss", requirements="oss2==2.6.1\n")
    store = InMemoryStore()
    installer = FakeInstaller(ok=True)
    config_loader = make_config_loader(
        {
            "poem": ({"limit": 20}, "0.0.1"),
            "oss": ({"access_key_id": "not complete", "bucket_name": "not complete"}, "0.0.1"),
        }
    )

    def fresh():
        return si.PluginInitializer(
            ["poem", "oss"],
            root_path=str(tmp_path),
            safe=True,
            settings_store=store,
            installer=installer,
            version_lookup=make_version_lookup({"oss2": "2.6.1"}),
            data_runner=make_data_runner(),
            config_loader=config_loader,
        )

    first = fresh().run()
    assert all(r.ok for r in first)
    snapshot1 = si.format_plugin_path(store.load())

    second = fresh().run()
    assert all(r.ok for r in second)
    for r in second:
        assert r.config.status is si.ConfigStatus.UNCHANGED
        assert r.dependency.status in (
            si.DependencyStatus.NO_DEPENDENCIES,
            si.DependencyStatus.ALREADY_SATISFIED,
        )

    snapshot2 = si.format_plugin_path(store.load())
    assert snapshot1 == snapshot2  # 字节级稳定 => 可重复执行
    assert installer.calls == []  # 依赖已满足，从未安装


def test_existing_unrelated_plugins_are_left_untouched(tmp_path):
    make_plugin(tmp_path, "poem", requirements="")
    store = InMemoryStore(
        {"oss": {"path": "app.plugin.oss", "enable": True, "version": "0.0.1", "access_key_id": "KEEP"}}
    )
    init = build_initializer(
        tmp_path, ["poem"], store=store,
        config_loader=make_config_loader({"poem": ({"limit": 20}, "0.0.1")}),
    )
    init.run()
    final = store.load()
    assert final["oss"]["access_key_id"] == "KEEP"  # 未参与本次初始化的插件保持不动
    assert "poem" in final


# ---------------------------------------------------------------------------
# FileSettingsStore：读写 base.py（仅用标准库，无需 Flask）
# ---------------------------------------------------------------------------
def test_file_settings_store_insert_roundtrip_and_byte_stable(tmp_path):
    base = tmp_path / "base.py"
    base.write_text(
        "class BaseConfig(object):\n    SECRET_KEY = 'x'\n    DEBUG = False\n",
        encoding="utf-8",
    )
    store = si.FileSettingsStore(str(base))

    assert store.load() == {}  # 起初没有 PLUGIN_PATH

    settings = {
        "poem": {"path": "app.plugin.poem", "enable": True, "version": "0.0.1", "limit": 20},
    }
    store.save(settings)

    # 作为 BaseConfig 的类属性写入（4 空格缩进），且可被重新解析
    after_first = base.read_text(encoding="utf-8")
    compile(after_first, str(base), "exec")  # 仍是合法 Python
    assert "    PLUGIN_PATH = {" in after_first
    assert store.load() == settings

    # 再次保存相同内容应字节级稳定（幂等）
    store.save(store.load())
    assert base.read_text(encoding="utf-8") == after_first


def test_file_settings_store_preserves_string_with_braces(tmp_path):
    base = tmp_path / "base.py"
    base.write_text("class BaseConfig(object):\n    DEBUG = False\n", encoding="utf-8")
    store = si.FileSettingsStore(str(base))
    settings = {"poem": {"path": "app.plugin.poem", "enable": True, "version": "0.0.1", "tip": "use {curly} braces"}}
    store.save(settings)
    assert store.load() == settings  # 字符串里的花括号不影响块的定位
