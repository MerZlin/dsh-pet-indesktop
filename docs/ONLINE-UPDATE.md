# 在线更新协议与发布清单

> 基线：`v4.2.1` 代码工作树，2026-09-24。本文描述当前 `pet/updater.py` 与 Inno Setup 安装包的实现，不代表已经发布的静态 `update.json` 已自动生成。

## 1. 用户流程

1. 右键菜单“检查更新”、更新提示气泡和设置页“更新”入口都进入同一套 `pet.updater` 服务。
2. 客户端按顺序查询 GitHub Releases API，以及 `cdn.jsdelivr.net` 的三个边缘节点镜像；某一源超时或返回非法 JSON 时继续尝试下一源。
3. 发现新版本后，设置页展示版本说明。Windows 冻结版且找到匹配安装包时，“下载并安装”按钮可用；源码运行、便携/非 Windows 运行方式或缺少可信安装包时只提供下载页，不执行自动安装。
4. 安装包下载到用户配置目录下的 `updates/`，先写 `.part` 临时文件，再校验大小和 SHA-256，成功后原子改名。
5. 通过独立进程启动 Inno Setup 安装器。安装器使用 `CloseApplications=yes` 关闭占用中的桌宠进程，静默安装结束后由 `[Run]` 重新启动已安装程序。更新失败不会替换当前版本。

## 2. manifest 合同

旧版 `assets: {"文件名": "URL"}` 仍可读取。新发布应使用结构化资产：

```json
{
  "version": "4.2.2",
  "html_url": "https://github.com/MerZlin/dsh-pet-indesktop/releases/tag/v4.2.2",
  "notes": "版本说明",
  "assets": {
    "dsh-pet-standalone-webm-chat-setup.exe": {
      "fileName": "dsh-pet-standalone-webm-chat-setup.exe",
      "urls": [
        "https://github.com/MerZlin/dsh-pet-indesktop/releases/download/v4.2.2/dsh-pet-standalone-webm-chat-setup.exe",
        "https://可信镜像.example/releases/v4.2.2/dsh-pet-standalone-webm-chat-setup.exe"
      ],
      "size": 123456789,
      "sha256": "64 个十六进制字符",
      "platform": "windows",
      "kind": "installer",
      "variant": "webm-chat"
    }
  }
}
```

`size` 和 `sha256` 是自动安装的必要校验信息；`urls` 按顺序回退。客户端只会下载 HTTPS 且位于内置可信域名集合中的地址，当前允许 GitHub Release 资产与 jsDelivr/Fastly/Gcore 镜像。未知镜像即使出现在 manifest 中，也只能被展示，不能被当作可执行文件启动。

## 3. GitHub 不可达时的处理

- manifest 查询不依赖单一 GitHub 域名：API 失败后会尝试三个 jsDelivr 镜像。
- **二进制下载必须配置真实可达的镜像 URL**。镜像 manifest 本身不会自动代理 GitHub 二进制；发布者必须把镜像地址写入同一资产的 `urls` 数组，并将镜像域名加入客户端可信列表后再发布。
- Quark 网盘是人工下载兜底入口，不参与自动安装，因为网盘页面不是稳定的、可校验的直接安装包 URL。
- 系统代理/VPN 会影响 urllib 请求；排查“昨天能更新、今天不能更新”时，先按 [`NETWORK-PROXY-AND-VPN-2026-09-22.md`](NETWORK-PROXY-AND-VPN-2026-09-22.md) 的现场探针确认出口，不要通过关闭 SHA-256 校验来规避网络问题。

## 4. 发布前清单

1. 使用 [`ONEDIR_PACKAGING.md`](ONEDIR_PACKAGING.md) 生成对应变体的 Inno Setup 安装包。
2. 对每个安装包记录最终文件名、字节数和 SHA-256；不要对安装包重新压缩或改名后再计算摘要。
3. 将 GitHub Release URL 与每个可用镜像 URL 写入结构化 manifest；确认 URL 指向直接文件而不是网页。
4. 先用源码运行的测试夹具验证解析、镜像回退、大小校验、摘要校验和不可信源拒绝，再在 Windows 冻结版上实测安装器关闭旧进程并重启。
5. 发布前确认 manifest 的 `version` 不低于代码 `pet.__version__`，且 chat/no-chat 安装包名称与构建产物一致。

## 5. 安全与回滚边界

- 下载只进入用户配置目录的 `updates/`，不覆盖安装目录中的现有文件。
- `.part`、大小不符、摘要不符或全部镜像失败的文件会被删除；不会启动安装器。
- 客户端没有新增持久设置键；更新页面是一次性命令和状态展示，不把“是否自动更新”伪装成设置项。
- Inno Setup 安装失败时，旧程序仍由安装器的标准失败语义保留；需要人工恢复时打开更新页的下载页，或使用 Quark 兜底包。

## 6. 相关文档

- 设置入口和页面准入：[`SETTINGS-CHANGE-GATES.md`](SETTINGS-CHANGE-GATES.md)
- 网络代理/VPN 排查：[`NETWORK-PROXY-AND-VPN-2026-09-22.md`](NETWORK-PROXY-AND-VPN-2026-09-22.md)
- onedir 与安装包构建：[`ONEDIR_PACKAGING.md`](ONEDIR_PACKAGING.md)
- 若未来更新 DLC/插件目录，另读 [`PLUGIN-UPDATE-PROTOCOL.md`](plugin-phase-04-updates/PLUGIN-UPDATE-PROTOCOL.md)；该协议的原子激活/回滚模型不替代当前 Core 安装包更新。
