# 可运行 MOD 样例

- `hello-local`：非官方 host-only，菜单 + 配置 + 设置草稿。
- `echo-worker`：非官方 host-worker，真实握手、请求、取消与退出。
- `persona-phrases.json`：现有台词导入格式，不是安装包。
- 角色副本用 `python -m scripts.build_character_mod_example <新的输出目录>` 从内置素材生成，不重复提交媒体。

详细步骤与接口见 [MOD 教程](../../docs/modding/README.md)。输出到新的 `.scratch/` 目录，原素材和已安装包都不修改。
