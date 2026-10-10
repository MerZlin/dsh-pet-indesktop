# 台词与人格模板（persona-phrases/v1）

[入口](README.md) · [示例 JSON](../../examples/mods/persona-phrases.json)

这是已有设置的导入 / 导出格式，**不是可单独卸载的功能 DLC**，不会在 MOD 列表中伪装成一项。

## 操作
1. 在设置的台词 / 表达风格编辑区域使用「导出台词模板」，将空白字段模板复制到剪贴板；它不包含现有台词，不是当前配置备份。修改前可复制当前编辑框文本留存。
2. 复制示例 JSON，修改 `name` 和 `phrases` 中已有事件的文本。
3. 把 JSON 文本粘贴到同一区域的「导入模板」输入框，再点击导入；这是文本粘贴，不是选择文件。检查各事件编辑框，保存 / 完成后才正式应用。取消整个设置可放弃尚未保存的编辑。
4. 触发相应事件观察效果；AI / 余额类事件还需对应功能可用，不要以不触发事件判断导入失败。

```json
{
  "template": "persona-phrases/v1",
  "mode": "custom",
  "name": "我的问候风格",
  "phrases": {"thinking": ["让我想一想……"]}
}
```
`phrases` 是事件名到字符串列表的映射。从原导出文件复制真实事件名与变量占位符；不要自行添加未经支持的事件。模板可附带 `entries`、`variables`、`agents` 等现有编辑信息；最小例子无需包含它们。保留所用事件要求的占位符（例如 `{text}`），不要写入 Key 或个人聊天记录。

## 校验
```powershell
python -m json.tool examples/mods/persona-phrases.json
```
语法有效只是第一步，仍需在编辑页导入、保存并触发事件。`pet/persona_template.py` 是格式实现参考，不属于 v1 功能 MOD 的运行时依赖。不要改动 Core 自带 presets 来分发个人模板。

## 实际效果与限制
模板改变文字风格，不改变事件触发逻辑或赋予新 API 能力；无需做 Python MOD。卸载功能包不会替你删除已导入的个人台词配置。
