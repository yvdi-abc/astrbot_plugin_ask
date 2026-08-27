"""astrbot_plugin_ask 插件逻辑测试脚本。

使用 mock 事件对象直接调用插件 handler，验证：
1. 命令解析与空参数处理
2. 权限控制（everyone/admin）
3. 冷却时间
4. /ask clear 清空历史
5. LLM 调用参数（system_prompt 覆盖、多轮上下文）
6. 合并转发逻辑
7. 直接发送逻辑

不向真实 QQ 平台发送任何消息。
"""
import asyncio
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, "/root/.local/share/uv/tools/astrbot")

from astrbot.core.star.filter.command import GreedyStr


class MockEvent:
    """模拟 AstrMessageEvent 的最小实现。"""

    def __init__(self, platform="aiocqhttp", admin=False, session="test:GroupMessage:1111"):
        self._platform = platform
        self._admin = admin
        self.unified_msg_origin = session
        self.message_str = ""
        self._call_llm_flag = True
        self._sent = []

    def get_platform_name(self):
        return self._platform

    def get_self_id(self):
        return "10001"

    def is_admin(self):
        return self._admin

    def should_call_llm(self, val):
        self._call_llm_flag = val

    def plain_result(self, text):
        r = types.SimpleNamespace(chain=[types.SimpleNamespace(text=text)])
        r.text = text
        return r

    def chain_result(self, chain):
        r = types.SimpleNamespace(chain=chain)
        r.text = "[chain]"
        return r


class MockProvider:
    """模拟 Provider.text_chat，记录调用参数并返回固定回答。"""

    def __init__(self, answer="这是 AI 助手的回答。"):
        self.answer = answer
        self.calls = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        resp = types.SimpleNamespace()
        resp.completion_text = self.answer
        return resp


class MockContext:
    """模拟 Context，返回 MockProvider。"""

    def __init__(self, provider=None):
        self._provider = provider or MockProvider()
        self.get_using_provider_calls = 0

    def get_using_provider(self, umo=None):
        self.get_using_provider_calls += 1
        return self._provider


# 导入插件模块
import importlib.util

spec = importlib.util.spec_from_file_location(
    "ask_plugin",
    "/root/.local/share/uv/tools/astrbot/data/plugins/astrbot_plugin_ask/main.py",
)
plugin_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin_mod)

from astrbot.api import AstrBotConfig


def _make_config_file(config_dict):
    """创建临时配置文件并返回路径。"""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(config_dict, f, ensure_ascii=False)
    return path


def make_plugin(config_overrides=None, provider=None):
    config_path = _make_config_file(
        {
            "enable": True,
            "llm_mode": "astrbot",
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": "",
            "system_prompt": "",
            "use_forward": False,
            "forward_threshold": 500,
            "max_history": 10,
            "permission": "everyone",
            "cooldown": 0,
        }
    )
    config = AstrBotConfig(config_path=config_path)
    if config_overrides:
        for k, v in config_overrides.items():
            config[k] = v
    provider = provider or MockProvider()
    ctx = MockContext(provider)
    plugin = plugin_mod.AskPlugin(ctx, config)
    return plugin, provider


def collect(agen):
    """收集 async generator 的所有产出。"""
    out = []

    async def runner():
        async for item in agen:
            out.append(item)

    asyncio.get_event_loop().run_until_complete(runner())
    return out


# ---------- 测试 1: 正常回答，直接发送 ----------
def test_basic_answer():
    plugin, provider = make_plugin()
    event = MockEvent()
    results = collect(plugin.ask(event, GreedyStr("什么是黑洞？")))
    assert len(results) == 1, f"应产出 1 条结果，实际 {len(results)}"
    r = results[0]
    assert r.text == "这是 AI 助手的回答。", f"回答内容错误: {r.text}"
    assert event._call_llm_flag is False, "应阻止默认 LLM 链路"
    # 验证 LLM 调用参数
    call = provider.calls[0]
    assert call["system_prompt"] != "", "system_prompt 不应为空（应使用默认人设）"
    assert "AI 助手" in call["system_prompt"], "默认人设应包含 AI 助手"
    assert call["prompt"] == "什么是黑洞？", "prompt 应为用户问题"
    assert call["contexts"] == [], "首次调用无历史上下文"
    print("PASS 测试1: 正常回答直接发送，system_prompt 覆盖生效")


# ---------- 测试 2: 多轮上下文 ----------
def test_multiturn_context():
    plugin, provider = make_plugin()
    event = MockEvent()
    # 第一轮
    collect(plugin.ask(event, GreedyStr("我叫小明")))
    # 第二轮
    collect(plugin.ask(event, GreedyStr("我叫什么名字？")))
    call = provider.calls[1]
    assert len(call["contexts"]) == 2, f"第二轮应携带 2 条历史，实际 {len(call['contexts'])}"
    assert call["contexts"][0]["role"] == "user"
    assert call["contexts"][0]["content"] == "我叫小明"
    assert call["contexts"][1]["role"] == "assistant"
    print("PASS 测试2: 多轮上下文正确携带历史")


# ---------- 测试 3: /ask clear ----------
def test_clear_history():
    plugin, provider = make_plugin()
    event = MockEvent()
    collect(plugin.ask(event, GreedyStr("第一轮")))
    assert len(plugin._histories.get(event.unified_msg_origin, [])) == 2
    results = collect(plugin.ask(event, GreedyStr("clear")))
    assert "已清空" in results[0].text, f"clear 提示错误: {results[0].text}"
    assert event.unified_msg_origin not in plugin._histories, "历史应被清空"
    # 清空后再次提问不应携带历史
    collect(plugin.ask(event, GreedyStr("新问题")))
    assert provider.calls[-1]["contexts"] == [], "清空后不应携带历史"
    print("PASS 测试3: /ask clear 正确清空历史")


# ---------- 测试 4: 权限控制 ----------
def test_permission():
    # admin 模式 + 非管理员
    plugin, _ = make_plugin({"permission": "admin"})
    event = MockEvent(admin=False)
    results = collect(plugin.ask(event, GreedyStr("问题")))
    assert "权限不足" in results[0].text, "非管理员应被拒绝"
    # admin 模式 + 管理员
    event2 = MockEvent(admin=True)
    results2 = collect(plugin.ask(event2, GreedyStr("问题")))
    assert "权限不足" not in results2[0].text, "管理员应被放行"
    print("PASS 测试4: 权限控制生效")


# ---------- 测试 5: 冷却时间 ----------
def test_cooldown():
    plugin, _ = make_plugin({"cooldown": 5})
    event = MockEvent()
    results = collect(plugin.ask(event, GreedyStr("第一次")))
    assert "冷却" not in results[0].text
    results2 = collect(plugin.ask(event, GreedyStr("第二次")))
    assert "过于频繁" in results2[0].text, "冷却期内应被拒绝"
    print("PASS 测试5: 冷却时间生效")


# ---------- 测试 6: 空参数 ----------
def test_empty_question():
    plugin, provider = make_plugin()
    event = MockEvent()
    results = collect(plugin.ask(event, GreedyStr("")))
    assert "用法" in results[0].text, "空参数应提示用法"
    assert provider.calls == [], "空参数不应调用 LLM"
    print("PASS 测试6: 空参数提示用法且不调用 LLM")


# ---------- 测试 7: 合并转发（长回答 + QQ 平台 + use_forward） ----------
def test_forward_send():
    long_answer = ("这是第一段内容，讲述基础知识。" * 30) + "\n" + ("这是第二段内容，深入分析。" * 30)
    provider = MockProvider(answer=long_answer)
    plugin, _ = make_plugin(
        {"use_forward": True, "forward_threshold": 100}, provider=provider
    )
    event = MockEvent(platform="aiocqhttp")
    results = collect(plugin.ask(event, GreedyStr("长问题")))
    r = results[0]
    # 合并转发应产出 Nodes 组件
    from astrbot.core.message.components import Nodes
    assert any(isinstance(c, Nodes) for c in r.chain), "长回答应打包为合并转发"
    nodes_comp = [c for c in r.chain if isinstance(c, Nodes)][0]
    assert len(nodes_comp.nodes) > 1, "合并转发应包含多个节点"
    print(f"PASS 测试7: 合并转发生效（{len(nodes_comp.nodes)} 个节点）")


# ---------- 测试 8: 短回答 + use_forward 不打包 ----------
def test_short_no_forward():
    provider = MockProvider(answer="短回答")
    plugin, _ = make_plugin(
        {"use_forward": True, "forward_threshold": 500}, provider=provider
    )
    event = MockEvent(platform="aiocqhttp")
    results = collect(plugin.ask(event, GreedyStr("短问题")))
    r = results[0]
    from astrbot.core.message.components import Nodes
    assert not any(isinstance(c, Nodes) for c in r.chain), "短回答不应打包"
    print("PASS 测试8: 短回答不打包，直接发送")


# ---------- 测试 9: 非 QQ 平台合并转发降级 ----------
def test_forward_fallback():
    long_answer = ("这是第一段内容，讲述基础知识。" * 30) + "\n" + ("这是第二段内容，深入分析。" * 30)
    provider = MockProvider(answer=long_answer)
    plugin, _ = make_plugin(
        {"use_forward": True, "forward_threshold": 100}, provider=provider
    )
    event = MockEvent(platform="telegram")
    results = collect(plugin.ask(event, GreedyStr("长问题")))
    r = results[0]
    from astrbot.core.message.components import Nodes
    assert not any(isinstance(c, Nodes) for c in r.chain), "非 QQ 平台应降级为直接发送"
    print("PASS 测试9: 非 QQ 平台自动降级为直接发送")


# ---------- 测试 10: LLM 调用失败返回友好提示 ----------
def test_llm_error():
    class ErrorProvider(MockProvider):
        async def text_chat(self, **kwargs):
            raise Exception("API timeout")

    provider = ErrorProvider()
    ctx = MockContext(provider)
    config_path = _make_config_file(
        {
            "enable": True,
            "llm_mode": "astrbot",
            "use_forward": False,
            "max_history": 10,
            "permission": "everyone",
            "cooldown": 0,
        }
    )
    config = AstrBotConfig(config_path=config_path)
    plugin = plugin_mod.AskPlugin(ctx, config)
    event = MockEvent()
    results = collect(plugin.ask(event, GreedyStr("问题")))
    assert "失败" in results[0].text, f"错误提示错误: {results[0].text}"
    print("PASS 测试10: LLM 失败返回友好提示")


if __name__ == "__main__":
    test_basic_answer()
    test_multiturn_context()
    test_clear_history()
    test_permission()
    test_cooldown()
    test_empty_question()
    test_forward_send()
    test_short_no_forward()
    test_forward_fallback()
    test_llm_error()
    print("\n✅ 全部 10 项测试通过")
