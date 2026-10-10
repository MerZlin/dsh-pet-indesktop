# 角色资源：制作、安装与切换

[入口](README.md) · [用户指南](USER-GUIDE.md)

角色包只提供媒体，不执行 Python。名称用于显示，ID 用于识别；要做一个新角色，必须改 ID，不能只改文件夹或显示名。路径大小写、相对关系须保留。

## 一、最快的可运行副本
在开发环境仓库根目录执行（输出目录必须不存在）：
```powershell
python -m scripts.build_character_mod_example .scratch/my-mods/mod-demo-shenshen
```
生成完整包目录和同名 ZIP。其 ID 为 `mod-demo-shenshen`，名称为「深深（MOD 示例）」。脚本复制 `content/characters/shenshen`，并把副本的待机动画替换成挥手动画；原包不动。这样切换后不是只看名称，而能从动作判断确实加载了副本。媒体生成物不重复加入 Git。

1. 设置 → 扩展管理 → 导入 ZIP，选择生成的 ZIP。
2. 筛选「角色资源」。新包初始停用，点击「启用」只加入角色列表，不换装。
3. 点击「使用」切换当前实例；其他桌宠实例不一起换。
4. 观察待机时的挥手动作。停用或删除使用中的包，受影响实例应先切回内置角色；原始 ZIP 留在原位置。

## 二、完整资源包目录
```text
mod-demo-shenshen/
  manifest.json
  videos/
    manifest.json
    idle/待机呼吸休闲.webm
    click/点击回应-元气挥手.webm
    ...
```
顶层 `manifest.json` 示例（哈希由制作脚本重算，不要复制旧哈希）：
```json
{
  "id": "mod-demo-shenshen",
  "name": "深深（MOD 示例）",
  "description": "离线角色替换演示：待机使用挥手动作",
  "version": "1.0.0",
  "kind": "content",
  "api_version": "1",
  "core_requires": ">=4.2.5,<6.0.0",
  "platforms": ["windows", "macos", "linux"],
  "dependencies": [],
  "capabilities": ["character", "animation"],
  "entrypoint": null,
  "content": {"characters": ["mod-demo-shenshen"]},
  "integrity": {"sha256": null, "signature": null}
}
```
建议先复制生成包再替换媒体并重新走制作 / 校验流程。ZIP 根目录必须直接有 manifest，不能多套一层随意目录。更新时保留包 ID、提高版本，管理器保留启停状态。

视频格式沿用当前包：透明 WebM / VP9，使用原目录中的动作命名与 `videos/manifest.json`。画布、帧率、时长应符合对应动作；错误编码或丢失透明通道会变黑底。`body_box` 是源像素中的稳定身体区域，`head_box` 是头部交互区域；换体形时重新测量，不要照抄深深数值。省略 body_box 会回退到整画布定位，不等于自动检测身体。

## 三、放在哪里
Setup 项目目录有 `portable.json` 时：
```text
手动素材：<项目目录>\data\characters\<角色ID>\videos\
管理器安装：<项目目录>\data\content\characters\<角色ID>\versions\<版本>\
```
当前示例让包 ID 与角色 ID 相同；若包声明不同角色 ID，以 manifest 的 `content.characters` 为准。管理器目录里还有自己的活动版本状态，不要手动编辑它。非 portable 开发模式按当前 Runtime Layout 的数据根替换上述 `data`。

**手动目录不需要包 manifest**，但须保持视频目录与动画 manifest；它是兼容的手动素材来源，不会被猜成可执行功能包。复制完重新发现角色（必要时重新打开设置 / 启动桌宠）。管理中心标注「手动素材」，不提供可能删除作者源目录的按钮。想要管理启停、版本及删除，请使用完整资源包导入。

## 四、常见错误与边界
- 只改显示名不改 ID：仍可能覆盖同 ID 的素材，不能当独立角色测试。
- 启用后没换装：这是预期；还需点击「使用」。
- 删除提示使用中：让占用它的桌宠自然退出后重试，不手删正在解码的文件。
- 内置项不能删：管理器不会删除 Core 随附素材。
- 图片 / 视频能打开但桌宠不动：核对动作相对路径、编码和 manifest，不要随意改名。

## 实际效果与限制
完整包可以通过同一个列表安装、启停、切换和删除；手动目录仍支持旧素材放置方式，但不具备受管理版本生命周期。角色资源不支持执行脚本。
