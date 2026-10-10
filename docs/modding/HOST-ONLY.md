# host-only：离线打招呼 MOD

[入口](README.md) · [API](API-V1.md) · [源码](../../examples/mods/hello-local/host/factory.py)

## 准备与打包
在仓库根的开发环境安装项目依赖（`python -m pip install -r requirements-dev.txt`；其中运行依赖按项目 README 安装）。不需要 API Key，不访问网络。
```powershell
python -m scripts.build_mod_example examples/mods/hello-local .scratch/my-mods/hello-local-1
python -m scripts.build_mod_example .scratch/my-mods/hello-local-1 --check
```
输出完整目录及旁边的 `hello-local-1.zip`；输出已存在就选择新目录，不自动覆盖。

```text
hello-local/
  mod.json              # 作者元数据：非官方 ID、名称、说明、版本、factory
  host/
    __init__.py
    factory.py          # create_host() -> FeatureDefinition，默认 mod/v1 挂载
    settings.py         # QWidget 与草稿/保存/放弃/释放协议
```
打包器生成 `manifest.json` 和文件大小 / SHA-256 清单。发布包只有 host 和 manifest，无需带开发环境、密钥或 `.pyc`。

## 运行步骤与预期效果
1. 导入生成 ZIP；应显示「离线打招呼」且未启用。
2. 点启用，再点设置，修改消息并「保存此扩展设置」。
3. 桌宠右键菜单 → 扩展 → 打个招呼：出现普通文本消息框。消息来自该 MOD 的配置，不是 Core 硬编码。
4. 停用：菜单消失，旧操作句柄失效；再次启用可继续。删除后源 ZIP 与 `data/feature-data/demo.hello-local/memory.json` 保留。

`factory.py` 的导入和 `create_host()` 必须无副作用：不要创建 QApplication、联网、截图或启动线程。Qt、Settings 类和 WorkerClient 放进实际 runtime / settings 构造中延迟导入，确保无 GUI 的检查入口也能读取定义。

## 改成自己的包
复制源码目录而不是已安装版本；将 mod.json 的 `id` 与 factory.py 的 `OWNER` 同时改成唯一 ID，例如 `myname.greeting`，同步 name、description、version。保留 `factory` 的现有有效字符串或换成自己的标识；加载入口仍为 `host/factory.py:create_host`，不按官方 ID 分支。

设置的 `commit_namespace(..., expected_revision=...)` 使用乐观版本校验。失败时保留文本；不要捕获后假装保存成功。配置刷新不得重建未保存编辑器。较慢操作移到工作线程并用 queued signal 回 GUI 线程。

## 常见错误
- 包 ID 与定义 owner 不一致：拒绝加载；两个位置一起改。
- 顶层导入 Qt 或启动业务：无 GUI 检查可能失败；使用样例的延迟导入。
- 调用全局 Config、PetWindow 或其他 DLC 私有模块：不属于 v1 兼容合同。
- 保存后旧消息：每次命令读 `context.configuration`，不要只在构造时缓存。

## 实际效果与限制
样例不联网，演示真实菜单、持久设置和生命周期。host-only 与 Core 同进程，信任作者并不等于沙箱隔离。
