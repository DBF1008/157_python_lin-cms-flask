"""
:copyright: © 2020 by the Lin team.
:license: MIT, see LICENSE for more details.
"""

import sys

from app import create_app

from .safe_init import (
    FileSettingsStore,
    PluginInitError,
    PluginInitializer,
    default_config_loader,
    default_data_runner,
    metadata_version_lookup,
    pip_installer,
    render_report,
)

"""
插件初始化流程:
1、确定要初始化的插件名称（多个用空格隔开，* 表示初始化所有）。
2、依赖：安全模式下仅安装缺失/版本不满足的依赖，否则每次都安装。
3、配置：把插件配置合并进 app/config/base.py 的 PLUGIN_PATH；安全模式下只补缺失项
   并记录版本变更，保留已有的自定义配置项。
4、数据：如插件提供 initial_data，则执行以写入初始数据。

安全模式（--safe）可重复执行（幂等），并对每个插件分别报告依赖/配置/数据的结果；
任一步骤失败也不会中断整个流程。具体逻辑见 ``safe_init`` 模块。
"""


def _build_initializer(names, *, safe):
    """装配默认协作者并构造编排器。"""
    app = create_app(register_all=False)
    base_py = app.config.root_path + "/config/base.py"
    return PluginInitializer(
        names,
        root_path=app.config.root_path,
        safe=safe,
        settings_store=FileSettingsStore(base_py),
        installer=pip_installer,
        version_lookup=metadata_version_lookup,
        data_runner=default_data_runner,
        config_loader=default_config_loader,
    )


def run_init(names, *, safe=False):
    """执行初始化，打印报告；存在失败时以非零码退出。返回结果列表。"""
    initializer = _build_initializer(names, safe=safe)
    try:
        results = initializer.run()
    except PluginInitError as e:
        # 兼容旧版：非安全模式遇错立即中断
        print(f"插件初始化失败: {e}")
        sys.exit(1)

    print(render_report(results, safe=safe))
    if any(not r.ok for r in results):
        sys.exit(1)
    return results


def init(names=None, safe=False):
    if not names:
        raw = input("请输入要初始化的插件名，如果多个插件请使用空格分隔插件名，输入*表示初始化所有插件:\n")
        names = [raw]
    return run_init(names, safe=safe)


class PluginInit:
    """向后兼容的入口：等价于旧版的「立即、非安全」初始化。"""

    plugin_path = "app.plugin"

    def __init__(self, name):
        run_init([name], safe=False)
