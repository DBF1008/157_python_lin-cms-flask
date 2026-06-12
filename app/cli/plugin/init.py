"""
:copyright: © 2020 by the Lin team.
:license: MIT, see LICENSE for more details.
"""

import os
import re
import subprocess
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version as pkg_version

from app import create_app

from .result import InitReport, PluginResult, StepResult, StepStatus

"""
插件初始化流程:
1、输入要初始化的插件名称。（多个用空格隔开，*表示初始化所有）
2、python依赖的安装
2、将插件的配置写入到项目app/config/base.py中
3、将model中的模型插入到数据库中
4、如果有需要，将初始数据插入到数据表中
"""

app = create_app(register_all=False)

# 配置中不参与用户自定义的内部字段
_INTERNAL_KEYS = {"path", "enable", "version"}


class PluginInit:
    # 插件位置默认前缀
    plugin_path = "app.plugin"

    def __init__(self, name, safe=False):
        self.app = create_app(register_all=False)
        self.name = name.strip()
        self.safe = safe
        # 插件相关的路径信息，包含plugin_path(插件路径)，plugin_config_path(插件的配置文件路径)，plugin_info_path(插件的基本信息路径)
        self.path_info = dict()
        # 初始化结果报告
        self.report = InitReport(safe_mode=safe)
        # 根据name生成path，写入到path_info属性中
        self.generate_path()
        # 逐插件执行初始化流程
        for plugin_name in self.path_info:
            self._init_single_plugin(plugin_name)

        # 打印汇总报告
        if self.safe:
            print(self.report.format_report())

    def _init_single_plugin(self, plugin_name):
        """逐个插件执行完整的初始化流程，收集结果"""
        dep_result = self._install_rely(plugin_name)
        cfg_result = self._write_setting(plugin_name)
        data_result = self._create_data(plugin_name)
        self.report.add(PluginResult(name=plugin_name, dependency=dep_result, config=cfg_result, data=data_result))

    def generate_path(self):
        if self.name == "*":
            names = self.__get_all_plugins()
        else:
            names = self.name.split(" ")
        for name in names:
            if self.name == "":
                exit("插件名称不能为空，请重试")
            self.path_info[name] = {
                "plugin_path": self.plugin_path + "." + name,
                "plugin_config_path": self.plugin_path + "." + name + ".config",
                "plugin_info_path": self.plugin_path + "." + name + ".info",
            }

    def _install_rely(self, plugin_name):
        """安装单个插件的依赖，返回 StepResult"""
        print("正在初始化插件" + plugin_name + "...")
        filename = "requirements.txt"
        file_path = self.app.config.root_path + "/plugin/" + plugin_name + "/" + filename

        if not os.path.exists(file_path):
            return StepResult(StepStatus.SKIPPED, "无依赖文件")

        if os.path.getsize(file_path) == 0:
            return StepResult(StepStatus.SKIPPED, "无依赖")

        if self.safe:
            missing = self._check_missing_deps(file_path)
            if not missing:
                print(f"插件 {plugin_name} 的依赖已满足，跳过安装")
                return StepResult(StepStatus.SKIPPED, "依赖已满足")
            print(f"插件 {plugin_name} 需要安装: {', '.join(missing)}")
            cmd = "pip install -r " + file_path
        else:
            print("正在安装" + plugin_name + "插件的依赖，请耐心等待...")
            cmd = "pip install -r " + file_path

        try:
            ret = self.__execute_cmd(cmd=cmd)
            if ret:
                return StepResult(StepStatus.SUCCESS, "安装成功")
            else:
                msg = plugin_name + "插件的依赖安装失败"
                if not self.safe:
                    exit(msg + "，请[手动安装依赖]: https://doc.cms.talelin.com/")
                return StepResult(StepStatus.FAILED, "安装失败")
        except subprocess.CalledProcessError as e:
            msg = f"依赖安装异常: {e}"
            if not self.safe:
                exit(msg)
            return StepResult(StepStatus.FAILED, msg)

    def _write_setting(self, plugin_name):
        """写入单个插件的配置，返回 StepResult"""
        print("正在自动写入插件 " + plugin_name + " 的配置文件...")
        try:
            info_mod = import_module(self.path_info[plugin_name]["plugin_info_path"])
        except ModuleNotFoundError as e:
            msg = f"未找到插件 {plugin_name}，请检查插件名是否正确: {e}"
            if not self.safe:
                raise Exception(msg)
            return StepResult(StepStatus.FAILED, msg)

        new_setting_raw = self._generate_setting(plugin_name, info_mod)
        new_setting = {plugin_name: new_setting_raw}
        old_setting = self.app.config.get("PLUGIN_PATH", dict())
        old_plugin_cfg = old_setting.get(plugin_name)

        if self.safe and old_plugin_cfg is not None:
            merged, change_info = self._cal_setting_safe(new_setting, old_setting)
        else:
            merged = self._cal_setting(new_setting, old_setting)
            change_info = None

        # 判断配置是否有变更
        if old_plugin_cfg is not None and merged.get(plugin_name) == old_plugin_cfg:
            self.__update_setting_direct(merged)
            return StepResult(StepStatus.PRESERVED, "配置已保留")

        self.__update_setting_direct(merged)

        if change_info:
            detail_parts = []
            if change_info.get("added"):
                detail_parts.append(f"新增: {', '.join(change_info['added'])}")
            if change_info.get("preserved"):
                detail_parts.append(f"保留: {', '.join(change_info['preserved'])}")
            if change_info.get("updated"):
                detail_parts.append(f"更新: {', '.join(change_info['updated'])}")
            msg = "字段级合并; " + "; ".join(detail_parts) if detail_parts else "已更新"
        else:
            msg = "写入成功" if old_plugin_cfg is None else "已更新"

        return StepResult(StepStatus.SUCCESS, msg, details=change_info or {})

    def _create_data(self, plugin_name):
        """初始化单个插件的数据，返回 StepResult"""
        print("正在创建插件 " + plugin_name + " 的基础数据...")
        try:
            plugin_module = import_module(self.path_info[plugin_name]["plugin_path"] + ".app.__init__")
            dir_info = dir(plugin_module)
        except ModuleNotFoundError as e:
            msg = f"未找到插件 {plugin_name} 的模块: {e}"
            if not self.safe:
                raise Exception(msg)
            return StepResult(StepStatus.FAILED, msg)

        if "initial_data" not in dir_info:
            return StepResult(StepStatus.SKIPPED, "无初始数据方法")

        if self.safe:
            try:
                plugin_module.initial_data()
                return StepResult(StepStatus.SUCCESS, "数据初始化成功")
            except Exception as e:
                return StepResult(StepStatus.SKIPPED, f"数据已存在或初始化异常: {e}")
        else:
            plugin_module.initial_data()
            return StepResult(StepStatus.SUCCESS, "数据初始化成功")

    # ---- 保持向后兼容的公共方法 ----

    def auto_install_rely(self):
        """兼容旧接口：安装所有插件依赖"""
        for name in self.path_info:
            self._install_rely(name)

    def auto_write_setting(self):
        """兼容旧接口：写入所有插件配置"""
        setting_text = dict()
        for name, val in self.path_info.items():
            try:
                info_mod = import_module(self.path_info[name]["plugin_info_path"])
            except ModuleNotFoundError as e:
                raise Exception(str(e) + "\n未找到插件" + name + "，请检查您输入的插件名是否正确")
            res = self._generate_setting(name, info_mod)
            setting_text[name] = res
        self.__update_setting(new_setting=setting_text)

    def create_data(self):
        """兼容旧接口：创建所有插件数据"""
        print("正在创建基础数据...")
        for name, val in self.path_info.items():
            try:
                plugin_module = import_module(self.path_info[name]["plugin_path"] + ".app.__init__")
                dir_info = dir(plugin_module)
            except ModuleNotFoundError as e:
                raise Exception(
                    str(e) + "\n未找到插件" + name + "，请检查您输入的插件名是否正确或插件中是否有未安装的依赖包"
                )
            if "initial_data" in dir_info:
                plugin_module.initial_data()
        print("插件初始化成功")

    def _generate_setting(self, name, info_mod):
        info_mod_dic = info_mod.__dict__
        ret = {
            "path": self.path_info[name]["plugin_path"],
            "enable": True,
            # info_mod_dic.__version__
            "version": info_mod_dic.pop("__version__", "0.0.1"),
        }
        # 向setting_doc中写入插件的配置项
        cfg_mod = import_module(self.path_info[name]["plugin_config_path"])
        dic = cfg_mod.__dict__
        for key in dic.keys():
            if not key.startswith("__"):
                ret[key] = dic[key]
        return ret

    def __update_setting_direct(self, final_setting):
        """将最终配置直接写入 base.py"""
        sub_str = "PLUGIN_PATH = " + self.__format_setting(final_setting)

        setting_path = self.app.config.root_path + "/config/base.py"
        with open(setting_path, "r", encoding="UTF-8") as f:
            content = f.read()
            pattern = r"PLUGIN_PATH = \{([\s\S]*)\}+.*?"
            if len(re.findall(pattern, content)) == 0:
                content += """
PLUGIN_PATH = {}
"""
            result = re.sub(pattern, sub_str, content)

        with open(setting_path, "w+", encoding="UTF-8") as f:
            f.write(result)

    def __update_setting(self, new_setting):
        # 得到现存的插件配置
        old_setting = self.app.config.get("PLUGIN_PATH", dict())
        final_setting = self._cal_setting(new_setting, old_setting)
        self.__update_setting_direct(final_setting)

    def __get_all_plugins(self):
        # 返回所有插件的目录名称
        ret = []
        path = self.app.config.root_path + "/plugin"
        for file in os.listdir(path=path):
            file_path = os.path.join(path, file)
            if os.path.isdir(file_path):
                ret.append(file)
        return ret

    @classmethod
    def __execute_cmd(cls, cmd):
        code = subprocess.check_call(cmd, shell=True, stdout=subprocess.PIPE)
        if code == 0:
            return True
        elif code == 1:
            return False

    @classmethod
    def __format_setting(cls, setting):
        # 格式化setting字符串
        setting_str = str(setting)
        ret = setting_str.replace("},", "},\n   ").replace("{", "{\n    ", 1)
        replace_reg = re.compile(r"\}$")
        ret = replace_reg.sub("\n}", ret)
        return ret

    @staticmethod
    def _cal_setting(new_setting, old_setting):
        # 将新旧的setting合并，返回一个字典
        # 1、对比old和new，并且将这两个配置合并
        # 2、如果新的存在，旧的不存在，就追加新的；
        # 3、如果旧的存在，新的不存在，就保留旧的；
        # 4、如果新旧都存在，那么在版本号相同的情况下，保留旧的配置项，否则新的配置覆盖旧的配置。

        final_setting = dict()
        all_keys = new_setting.keys() | old_setting.keys()  # 得到新旧配置的并集

        for key in all_keys:
            if key not in old_setting.keys():
                # 不存在，追加新的
                final_setting[key] = new_setting[key]
            else:
                # 存在，对比版本号，看看是否需要更新
                if key not in new_setting:
                    # 新的不存在
                    final_setting[key] = old_setting[key]
                else:
                    # 新的存在
                    if new_setting[key]["version"] == old_setting[key]["version"]:
                        # 版本号相同，使用旧的配置
                        final_setting[key] = old_setting[key]
                    else:
                        # 版本号不同，更新配置为新的
                        final_setting[key] = new_setting[key]

        return final_setting

    @staticmethod
    def _cal_setting_safe(new_setting, old_setting):
        """安全模式的字段级合并，返回 (final_setting, change_info)

        change_info 是一个 dict，按插件名分组，记录每个插件的字段变更：
        {plugin_name: {"added": [...], "preserved": [...], "updated": [...]}}
        """
        final_setting = dict()
        change_info = {}

        all_keys = new_setting.keys() | old_setting.keys()

        for key in all_keys:
            if key not in old_setting:
                # 新插件，直接追加
                final_setting[key] = new_setting[key]
                added_fields = [k for k in new_setting[key] if k not in _INTERNAL_KEYS]
                change_info[key] = {"added": added_fields, "preserved": [], "updated": []}
            elif key not in new_setting:
                # 旧插件但新版本已移除，保留旧配置
                final_setting[key] = old_setting[key]
                preserved_fields = [k for k in old_setting[key] if k not in _INTERNAL_KEYS]
                change_info[key] = {"added": [], "preserved": preserved_fields, "updated": []}
            else:
                # 新旧都存在
                new_cfg = new_setting[key]
                old_cfg = old_setting[key]

                if new_cfg["version"] == old_cfg["version"]:
                    # 版本相同，完全保留旧配置
                    final_setting[key] = old_cfg
                    preserved_fields = [k for k in old_cfg if k not in _INTERNAL_KEYS]
                    change_info[key] = {"added": [], "preserved": preserved_fields, "updated": []}
                else:
                    # 版本不同，字段级合并：以新配置为基础，保留旧配置中用户自定义的非内部字段
                    merged_cfg = dict(new_cfg)  # 从新默认值出发
                    added = []
                    preserved = []
                    updated = []

                    # 保留旧配置中的用户自定义字段（非内部键）
                    for field_key in old_cfg:
                        if field_key not in _INTERNAL_KEYS:
                            merged_cfg[field_key] = old_cfg[field_key]
                            if field_key in new_cfg:
                                if new_cfg[field_key] != old_cfg[field_key]:
                                    preserved.append(field_key)
                                # 值相同则无需记录
                            else:
                                # 新版本中已不存在的旧字段，仍然保留
                                preserved.append(field_key)

                    # 记录新增的字段（新版本有但旧版本没有的非内部键）
                    for field_key in new_cfg:
                        if field_key not in _INTERNAL_KEYS and field_key not in old_cfg:
                            added.append(field_key)

                    # 更新 version 为新版本
                    merged_cfg["version"] = new_cfg["version"]
                    final_setting[key] = merged_cfg
                    change_info[key] = {"added": added, "preserved": preserved, "updated": updated}

        return final_setting, change_info

    @staticmethod
    def _check_missing_deps(requirements_path):
        """检查 requirements.txt 中缺失的依赖包名列表"""
        missing = []
        with open(requirements_path, "r", encoding="UTF-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # 解析包名（支持 ==, >=, <=, ~=, != 等版本说明符）
                match = re.match(r"^([A-Za-z0-9_.-]+)", line)
                if not match:
                    continue
                pkg_name = match.group(1)
                try:
                    installed_ver = pkg_version(pkg_name)
                    # 简单检查：如果 requirements 中指定了 ==版本，则精确比较
                    ver_match = re.search(r"==\s*([^\s#]+)", line)
                    if ver_match:
                        required_ver = ver_match.group(1)
                        if installed_ver != required_ver:
                            missing.append(pkg_name)
                    # 没有 == 指定或版本匹配则视为已满足
                except PackageNotFoundError:
                    missing.append(pkg_name)
        return missing


def init(plugin_name=None, safe=False):
    if plugin_name is None:
        plugin_name = input("请输入要初始化的插件名，如果多个插件请使用空格分隔插件名，输入*表示初始化所有插件:\n")
    PluginInit(plugin_name, safe=safe)
