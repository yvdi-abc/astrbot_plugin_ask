# AI 助手问答（astrbot_plugin_ask）

使用 `/ask` 命令，让 AstrBot 以**固定的 AI 助手人设**回答用户问题，**不继承** AstrBot 主配置中设置的机器人人设/性格。

## 功能特性

- ✅ **固定人设**：`/ask` 回答时使用插件自带的 AI 助手人设，不继承主配置人设
- ✅ **多轮上下文**：同一会话内连续使用 `/ask` 自动携带历史问答（可配置记忆轮数）
- ✅ **合并转发开关**：配置页可选择长回答以合并转发（QQ 转发消息）发出，还是直接发送
- ✅ **LLM 来源可选**：可复用 AstrBot 已配置的模型，也可在插件内单独配置 API
- ✅ **权限控制**：可设置为所有人可用或仅管理员可用
- ✅ **冷却时间**：防止短时间连续触发刷屏
- ✅ **清空历史**：`/ask clear` 一键清空当前会话上下文

## 安装

将本目录放入 AstrBot 的 `data/plugins/` 目录，然后在 AstrBot 管理面板中启用插件。

## 使用方法

```
/ask 什么是黑洞？
/ask 用一句话解释相对论
/ask clear          # 清空当前会话的多轮上下文
```

## 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable` | bool | true | 插件总开关 |
| `llm_mode` | string | astrbot | `astrbot`=复用 AstrBot 已配置模型；`custom`=插件内自配 API |
| `base_url` | string | https://api.openai.com/v1 | 自定义 API 地址（custom 模式） |
| `api_key` | string | 空 | 自定义 API 密钥（custom 模式） |
| `model` | string | 空 | 自定义模型名（custom 模式） |
| `system_prompt` | text | AI 助手模板 | `/ask` 回答使用的系统提示词（人设），可自定义 |
| `use_forward` | bool | false | 是否开启合并转发发送（仅 aiocqhttp/QQ 平台生效） |
| `forward_threshold` | int | 500 | 回答超过此长度（字符数）才使用合并转发 |
| `max_history` | int | 10 | 多轮上下文记忆轮数（0=不记忆） |
| `permission` | string | everyone | `everyone`=所有人可用；`admin`=仅管理员可用 |
| `cooldown` | int | 0 | 冷却时间（秒，0=关闭） |

## 说明

- 合并转发仅受 aiocqhttp（QQ）平台支持，其他平台会自动降级为直接发送
- astrbot 模式下使用 AstrBot 管理面板已配置的 LLM 与密钥，无需重复配置
- 多轮上下文保存在内存中，AstrBot 重启后清空

## 许可证

MIT License

## 作者

[yvdi-abc](https://github.com/yvdi-abc)
