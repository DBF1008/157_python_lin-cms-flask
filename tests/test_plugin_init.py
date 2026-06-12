"""
插件初始化安全模式的回归测试

覆盖：
- 配置合并逻辑（旧 + 安全模式）
- 依赖检查
- 结果追踪与报告
- safe 模式端到端流程
- 非 safe 模式行为不变
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.cli.plugin.init import PluginInit, _INTERNAL_KEYS
from app.cli.plugin.result import InitReport, PluginResult, StepResult, StepStatus


# ============================================================
# 1. _cal_setting 旧合并逻辑回归测试
# ============================================================


class TestCalSetting:
    """测试 _cal_setting（旧合并逻辑）的回归"""

    def test_new_plugin_added(self):
        """新插件追加到配置中"""
        new_setting = {"cos": {"path": "app.plugin.cos", "enable": True, "version": "0.0.1", "access_key": "xxx"}}
        old_setting = {}
        result = PluginInit._cal_setting(new_setting, old_setting)
        assert "cos" in result
        assert result["cos"]["version"] == "0.0.1"

    def test_old_plugin_preserved(self):
        """旧插件在新配置中不存在时保留"""
        new_setting = {}
        old_setting = {"poem": {"path": "app.plugin.poem", "enable": True, "version": "0.0.1", "limit": 20}}
        result = PluginInit._cal_setting(new_setting, old_setting)
        assert "poem" in result
        assert result["poem"]["limit"] == 20

    def test_same_version_keeps_old(self):
        """版本号相同时保留旧配置"""
        new_setting = {
            "cos": {"path": "app.plugin.cos", "enable": True, "version": "0.0.1", "access_key": "new_key"}
        }
        old_setting = {
            "cos": {"path": "app.plugin.cos", "enable": True, "version": "0.0.1", "access_key": "my_custom_key"}
        }
        result = PluginInit._cal_setting(new_setting, old_setting)
        assert result["cos"]["access_key"] == "my_custom_key"

    def test_different_version_overwrites(self):
        """版本号不同时整块覆盖（旧行为）"""
        new_setting = {
            "cos": {"path": "app.plugin.cos", "enable": True, "version": "0.0.2", "access_key": "new_key", "new_field": "val"}
        }
        old_setting = {
            "cos": {"path": "app.plugin.cos", "enable": True, "version": "0.0.1", "access_key": "my_custom_key"}
        }
        result = PluginInit._cal_setting(new_setting, old_setting)
        # 旧行为：整块覆盖
        assert result["cos"]["version"] == "0.0.2"
        assert result["cos"]["access_key"] == "new_key"

    def test_empty_old_setting(self):
        """空旧配置"""
        new_setting = {"oss": {"path": "app.plugin.oss", "enable": True, "version": "0.0.1"}}
        result = PluginInit._cal_setting(new_setting, {})
        assert "oss" in result

    def test_empty_new_setting(self):
        """空新配置保留所有旧配置"""
        old_setting = {"poem": {"path": "app.plugin.poem", "enable": True, "version": "0.0.1"}}
        result = PluginInit._cal_setting({}, old_setting)
        assert "poem" in result


# ============================================================
# 2. _cal_setting_safe 安全模式字段级合并测试
# ============================================================


class TestCalSettingSafe:
    """测试 _cal_setting_safe（安全模式字段级合并）"""

    def test_new_plugin_added(self):
        """新插件追加，change_info 记录 added 字段"""
        new_setting = {
            "cos": {"path": "app.plugin.cos", "enable": True, "version": "0.0.1", "access_key": "default", "region": "default_region"}
        }
        old_setting = {}
        result, change_info = PluginInit._cal_setting_safe(new_setting, old_setting)
        assert "cos" in result
        assert "access_key" in change_info["cos"]["added"]
        assert "region" in change_info["cos"]["added"]

    def test_old_plugin_preserved_when_not_in_new(self):
        """新版本已移除的插件，旧配置保留"""
        old_setting = {"legacy": {"path": "app.plugin.legacy", "enable": True, "version": "0.0.1", "custom_field": "val"}}
        new_setting = {}
        result, change_info = PluginInit._cal_setting_safe(new_setting, old_setting)
        assert "legacy" in result
        assert result["legacy"]["custom_field"] == "val"
        assert "custom_field" in change_info["legacy"]["preserved"]

    def test_same_version_keeps_old_completely(self):
        """版本号相同时完全保留旧配置"""
        new_setting = {
            "poem": {"path": "app.plugin.poem", "enable": True, "version": "0.0.1", "limit": 50}
        }
        old_setting = {
            "poem": {"path": "app.plugin.poem", "enable": True, "version": "0.0.1", "limit": 100}
        }
        result, change_info = PluginInit._cal_setting_safe(new_setting, old_setting)
        assert result["poem"]["limit"] == 100
        assert "limit" in change_info["poem"]["preserved"]

    def test_version_change_field_level_merge(self):
        """版本变更时字段级合并：保留旧用户自定义字段，补入新默认字段"""
        new_setting = {
            "cos": {
                "path": "app.plugin.cos",
                "enable": True,
                "version": "0.0.2",
                "access_key": "new_default",
                "region": "new_region",
                "new_feature": "enabled",
            }
        }
        old_setting = {
            "cos": {
                "path": "app.plugin.cos",
                "enable": True,
                "version": "0.0.1",
                "access_key": "my_custom_key",
                "region": "my_region",
            }
        }
        result, change_info = PluginInit._cal_setting_safe(new_setting, old_setting)
        # 版本更新
        assert result["cos"]["version"] == "0.0.2"
        # 用户自定义字段保留
        assert result["cos"]["access_key"] == "my_custom_key"
        assert result["cos"]["region"] == "my_region"
        # 新增字段补入
        assert result["cos"]["new_feature"] == "enabled"
        assert "new_feature" in change_info["cos"]["added"]
        # 用户自定义字段记录为 preserved
        assert "access_key" in change_info["cos"]["preserved"]

    def test_version_change_preserves_old_only_fields(self):
        """版本变更时，旧配置中有但新配置中没有的字段也被保留"""
        new_setting = {
            "cos": {"path": "app.plugin.cos", "enable": True, "version": "0.0.2", "access_key": "default"}
        }
        old_setting = {
            "cos": {
                "path": "app.plugin.cos",
                "enable": True,
                "version": "0.0.1",
                "access_key": "custom",
                "legacy_field": "keep_me",
            }
        }
        result, change_info = PluginInit._cal_setting_safe(new_setting, old_setting)
        assert result["cos"]["legacy_field"] == "keep_me"
        assert "legacy_field" in change_info["cos"]["preserved"]

    def test_internal_keys_not_tracked(self):
        """内部键（path, enable, version）不出现在 change_info 中"""
        new_setting = {"cos": {"path": "app.plugin.cos", "enable": True, "version": "0.0.2", "key": "new"}}
        old_setting = {"cos": {"path": "app.plugin.cos", "enable": True, "version": "0.0.1", "key": "old"}}
        _, change_info = PluginInit._cal_setting_safe(new_setting, old_setting)
        all_tracked = change_info["cos"]["added"] + change_info["cos"]["preserved"] + change_info["cos"]["updated"]
        for internal_key in _INTERNAL_KEYS:
            assert internal_key not in all_tracked


# ============================================================
# 3. _check_missing_deps 依赖检查测试
# ============================================================


class TestCheckMissingDeps:
    """测试 _check_missing_deps"""

    def test_empty_requirements(self, tmp_path):
        """空 requirements.txt 返回空列表"""
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("")
        result = PluginInit._check_missing_deps(str(req_file))
        assert result == []

    def test_comments_and_blank_lines(self, tmp_path):
        """注释行和空行被忽略"""
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("# this is a comment\n\n   \n")
        result = PluginInit._check_missing_deps(str(req_file))
        assert result == []

    def test_installed_package_satisfied(self, tmp_path):
        """已安装的包且版本匹配返回空列表"""
        # flask 应该已经安装在环境中
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("flask\n")
        result = PluginInit._check_missing_deps(str(req_file))
        assert "flask" not in result

    def test_nonexistent_package_reported(self, tmp_path):
        """不存在的包被报告为缺失"""
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("this-package-does-not-exist-xyz==9.9.9\n")
        result = PluginInit._check_missing_deps(str(req_file))
        assert "this-package-does-not-exist-xyz" in result

    def test_pinned_version_mismatch(self, tmp_path):
        """已安装但版本不匹配的包被报告"""
        req_file = tmp_path / "requirements.txt"
        # flask 已安装，但版本不可能是 0.0.1
        req_file.write_text("flask==0.0.1\n")
        result = PluginInit._check_missing_deps(str(req_file))
        assert "flask" in result


# ============================================================
# 4. 结果追踪与报告测试
# ============================================================


class TestResultTracking:
    """测试 PluginResult 和 InitReport"""

    def test_plugin_result_success_all_ok(self):
        """所有步骤成功时 success 为 True"""
        pr = PluginResult(
            name="cos",
            dependency=StepResult(StepStatus.SUCCESS, "安装成功"),
            config=StepResult(StepStatus.SUCCESS, "写入成功"),
            data=StepResult(StepStatus.SUCCESS, "数据初始化成功"),
        )
        assert pr.success is True

    def test_plugin_result_success_with_skipped(self):
        """包含 SKIPPED 和 PRESERVED 时 success 仍为 True"""
        pr = PluginResult(
            name="poem",
            dependency=StepResult(StepStatus.SKIPPED, "无依赖"),
            config=StepResult(StepStatus.PRESERVED, "配置已保留"),
            data=StepResult(StepStatus.SKIPPED, "数据已存在"),
        )
        assert pr.success is True

    def test_plugin_result_failure(self):
        """任何步骤失败时 success 为 False"""
        pr = PluginResult(
            name="cos",
            dependency=StepResult(StepStatus.FAILED, "安装失败"),
            config=StepResult(StepStatus.SKIPPED, "跳过"),
            data=StepResult(StepStatus.SKIPPED, "跳过"),
        )
        assert pr.success is False

    def test_report_format(self):
        """报告格式化输出包含所有关键信息"""
        report = InitReport(safe_mode=True)
        report.add(
            PluginResult(
                name="cos",
                dependency=StepResult(StepStatus.SUCCESS, "安装成功"),
                config=StepResult(StepStatus.PRESERVED, "配置已保留"),
                data=StepResult(StepStatus.SUCCESS, "数据初始化成功"),
            )
        )
        report.add(
            PluginResult(
                name="qiniu",
                dependency=StepResult(StepStatus.FAILED, "安装失败"),
                config=StepResult(StepStatus.SKIPPED, "跳过"),
                data=StepResult(StepStatus.SKIPPED, "跳过"),
            )
        )
        text = report.format_report()
        assert "safe 模式" in text
        assert "cos" in text
        assert "qiniu" in text
        assert "成功: 1" in text
        assert "失败: 1" in text
        assert "总计: 2" in text

    def test_report_standard_mode_label(self):
        """非 safe 模式报告显示标准模式"""
        report = InitReport(safe_mode=False)
        text = report.format_report()
        assert "标准模式" in text

    def test_report_counts(self):
        """报告计数正确"""
        report = InitReport()
        report.add(
            PluginResult(
                name="a",
                dependency=StepResult(StepStatus.SUCCESS),
                config=StepResult(StepStatus.SUCCESS),
                data=StepResult(StepStatus.SUCCESS),
            )
        )
        report.add(
            PluginResult(
                name="b",
                dependency=StepResult(StepStatus.FAILED),
                config=StepResult(StepStatus.SKIPPED),
                data=StepResult(StepStatus.SKIPPED),
            )
        )
        report.add(
            PluginResult(
                name="c",
                dependency=StepResult(StepStatus.SKIPPED),
                config=StepResult(StepStatus.PRESERVED),
                data=StepResult(StepStatus.SUCCESS),
            )
        )
        assert report.success_count == 2
        assert report.fail_count == 1


# ============================================================
# 5. safe 模式端到端流程测试
# ============================================================


class _MockConfig(dict):
    """支持属性访问的 dict，模拟 Flask app.config"""

    def __init__(self, *args, root_path="", **kwargs):
        super().__init__(*args, **kwargs)
        self.root_path = root_path


def _make_mock_app(tmp_path, plugin_path=None):
    """创建一个最小化的 mock Flask app"""
    mock_app = MagicMock()
    mock_app.config = _MockConfig({"PLUGIN_PATH": plugin_path or {}}, root_path=str(tmp_path))
    return mock_app


class TestSafeModeFlow:
    """测试 safe 模式下 PluginInit 的端到端流程（使用 mock）"""

    @patch("app.cli.plugin.init.create_app")
    def test_safe_mode_skip_installed_deps(self, mock_create_app, tmp_path):
        """safe 模式下已安装的依赖被跳过"""
        # 创建临时 plugin 目录和 requirements.txt
        plugin_dir = tmp_path / "plugin" / "testplugin"
        plugin_dir.mkdir(parents=True)
        req_file = plugin_dir / "requirements.txt"
        req_file.write_text("flask\n")

        mock_app = _make_mock_app(tmp_path)
        mock_create_app.return_value = mock_app

        # mock import_module 避免实际导入
        with patch("app.cli.plugin.init.import_module") as mock_import:
            mock_info = MagicMock()
            mock_info.__dict__ = {"__version__": "0.0.1", "__name__": "testplugin"}
            mock_config = MagicMock()
            mock_config.__dict__ = {"limit": 10}
            mock_plugin_app = MagicMock()
            # 模拟 initial_data 不存在
            del mock_plugin_app.initial_data

            def import_side_effect(path):
                if "info" in path:
                    return mock_info
                elif "config" in path:
                    return mock_config
                elif "app.__init__" in path:
                    return mock_plugin_app
                raise ModuleNotFoundError(path)

            mock_import.side_effect = import_side_effect

            # 创建 base.py 文件
            config_dir = tmp_path / "config"
            config_dir.mkdir(exist_ok=True)
            base_py = config_dir / "base.py"
            base_py.write_text("class BaseConfig:\n    pass\n")

            pi = PluginInit("testplugin", safe=True)

            assert len(pi.report.results) == 1
            result = pi.report.results[0]
            # flask 已安装，依赖应该被跳过
            assert result.dependency.status == StepStatus.SKIPPED
            assert result.success is True

    @patch("app.cli.plugin.init.create_app")
    def test_safe_mode_config_preserves_custom_values(self, mock_create_app, tmp_path):
        """safe 模式下配置保留用户自定义值"""
        plugin_dir = tmp_path / "plugin" / "testplugin"
        plugin_dir.mkdir(parents=True)
        req_file = plugin_dir / "requirements.txt"
        req_file.write_text("")  # 空依赖

        # 预设旧配置中有用户自定义值
        old_config = {
            "testplugin": {
                "path": "app.plugin.testplugin",
                "enable": True,
                "version": "0.0.1",
                "limit": 999,  # 用户自定义值
                "custom_field": "user_value",
            }
        }

        mock_app = _make_mock_app(tmp_path, plugin_path=old_config)
        mock_create_app.return_value = mock_app

        with patch("app.cli.plugin.init.import_module") as mock_import:
            mock_info = MagicMock()
            mock_info.__dict__ = {"__version__": "0.0.1", "__name__": "testplugin"}
            mock_config = MagicMock()
            mock_config.__dict__ = {"limit": 10}  # 默认值
            mock_plugin_app = MagicMock()
            del mock_plugin_app.initial_data

            def import_side_effect(path):
                if "info" in path:
                    return mock_info
                elif "config" in path:
                    return mock_config
                elif "app.__init__" in path:
                    return mock_plugin_app
                raise ModuleNotFoundError(path)

            mock_import.side_effect = import_side_effect

            config_dir = tmp_path / "config"
            config_dir.mkdir(exist_ok=True)
            base_py = config_dir / "base.py"
            base_py.write_text("class BaseConfig:\n    pass\n")

            pi = PluginInit("testplugin", safe=True)

            result = pi.report.results[0]
            # 版本相同，配置应保留
            assert result.config.status == StepStatus.PRESERVED

    @patch("app.cli.plugin.init.create_app")
    def test_safe_mode_data_init_error_skipped(self, mock_create_app, tmp_path):
        """safe 模式下数据初始化异常被标记为 SKIPPED 而非崩溃"""
        plugin_dir = tmp_path / "plugin" / "testplugin"
        plugin_dir.mkdir(parents=True)
        req_file = plugin_dir / "requirements.txt"
        req_file.write_text("")

        mock_app = _make_mock_app(tmp_path)
        mock_create_app.return_value = mock_app

        with patch("app.cli.plugin.init.import_module") as mock_import:
            mock_info = MagicMock()
            mock_info.__dict__ = {"__version__": "0.0.1", "__name__": "testplugin"}
            mock_config = MagicMock()
            mock_config.__dict__ = {}
            mock_plugin_app = MagicMock()
            mock_plugin_app.initial_data.side_effect = Exception("table already exists")

            def import_side_effect(path):
                if "info" in path:
                    return mock_info
                elif "config" in path:
                    return mock_config
                elif "app.__init__" in path:
                    return mock_plugin_app
                raise ModuleNotFoundError(path)

            mock_import.side_effect = import_side_effect

            config_dir = tmp_path / "config"
            config_dir.mkdir(exist_ok=True)
            base_py = config_dir / "base.py"
            base_py.write_text("class BaseConfig:\n    pass\n")

            pi = PluginInit("testplugin", safe=True)

            result = pi.report.results[0]
            assert result.data.status == StepStatus.SKIPPED
            assert "table already exists" in result.data.message
            # 虽然 data 跳过了，但整体仍然算成功（safe 模式容忍）
            assert result.success is True


# ============================================================
# 6. 回归测试：非 safe 模式行为不变
# ============================================================


class TestBackwardCompatibility:
    """确保非 safe 模式的行为与改造前一致"""

    def test_cal_setting_unchanged(self):
        """_cal_setting 的行为未被修改"""
        new = {"a": {"path": "p", "enable": True, "version": "0.0.2", "k": "new"}}
        old = {"a": {"path": "p", "enable": True, "version": "0.0.1", "k": "old"}}
        result = PluginInit._cal_setting(new, old)
        # 旧行为：版本不同时整块覆盖
        assert result["a"] == new["a"]

    @patch("app.cli.plugin.init.create_app")
    def test_non_safe_mode_install_failure_exits(self, mock_create_app, tmp_path):
        """非 safe 模式下依赖安装失败调用 exit"""
        plugin_dir = tmp_path / "plugin" / "testplugin"
        plugin_dir.mkdir(parents=True)
        req_file = plugin_dir / "requirements.txt"
        req_file.write_text("nonexistent-pkg-xyz==1.0.0\n")

        mock_app = _make_mock_app(tmp_path)
        mock_create_app.return_value = mock_app

        # 模拟 pip install 失败
        with patch("app.cli.plugin.init.PluginInit._PluginInit__execute_cmd", return_value=False):
            with pytest.raises(SystemExit):
                PluginInit("testplugin", safe=False)

    def test_init_function_default_params(self):
        """init() 函数默认参数兼容"""
        from app.cli.plugin.init import init
        import inspect

        sig = inspect.signature(init)
        params = sig.parameters
        assert "plugin_name" in params
        assert params["plugin_name"].default is None
        assert "safe" in params
        assert params["safe"].default is False
