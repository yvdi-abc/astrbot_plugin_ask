import asyncio
import re
import time
from collections import deque
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger, AstrBotConfig
from astrbot.api.star import Context, Star
import astrbot.api.message_components as Comp
from astrbot.core.star.filter.command import GreedyStr

DEFAULT_SYSTEM_PROMPT = (
    "你是一个乐于助人的 AI 助手。请用专业、客观、清晰的中文回答用户的问题。"
    "回答要简洁准确，避免无关内容。"
)


class AskPlugin(Star):
    """AI 助手问答插件。

    /ask <问题> —— 使用固定的 AI 助手人设回答，不继承 AstrBot 主配置的人设。
    /ask clear  —— 清空当前会话的多轮上下文。
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 多轮上下文历史：session_key -> deque[{"role","content"}]，长度 = max_history * 2
        self._histories: dict[str, deque] = {}
        # 冷却记录：session_key -> 上次调用时间戳
        self._last_use: dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # 工具方法                                                             #
    # ------------------------------------------------------------------ #

    def _check_permission(self, event: AstrMessageEvent) -> bool:
        """根据配置检查权限。everyone=所有人，admin=仅管理员。"""
        permission = str(self.config.get("permission", "everyone"))
        if permission == "admin":
            return event.is_admin()
        return True

    def _check_cooldown(self, session_key: str) -> Optional[int]:
        """返回剩余冷却秒数；0/None 表示可以继续。"""
        cooldown = int(self.config.get("cooldown", 0) or 0)
        if cooldown <= 0:
            return None
        now = time.time()
        last = self._last_use.get(session_key, 0)
        remain = int(cooldown - (now - last))
        if remain > 0:
            return remain
        return None

    def _get_history(self, session_key: str) -> deque:
        """获取会话历史（自动按 max_history 扩容）。"""
        max_history = max(0, int(self.config.get("max_history", 10) or 10))
        if max_history == 0:
            return deque(maxlen=0)
        history = self._histories.get(session_key)
        if history is None:
            history = deque(maxlen=max_history * 2)
            self._histories[session_key] = history
        return history

    def _split_text(self, text: str, max_seg: int = 200) -> list[str]:
        """按中文标点/换行分段，每段尽量不超过 max_seg 字符。"""
        text = text.strip()
        if not text:
            return []
        parts = re.split(r"(?<=[。！？；!?;\n])", text)
        segments: list[str] = []
        buf = ""
        for part in parts:
            if not part:
                continue
            if buf and len(buf) + len(part) > max_seg:
                segments.append(buf)
                buf = part
            else:
                buf += part
        if buf:
            segments.append(buf)
        return [seg for seg in segments if seg.strip()]

    async def _call_llm(
        self,
        event: AstrMessageEvent,
        question: str,
        system_prompt: str,
    ) -> str:
        """根据 llm_mode 调用 LLM，返回回答文本。"""
        history = list(self._get_history(event.unified_msg_origin))

        if self.config.get("llm_mode", "astrbot") == "custom":
            # 自定义模式：直接调用 OpenAI 兼容 API
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ValueError(
                    "custom 模式需要安装 openai 库（pip install openai），或切换到 astrbot 模式复用已配置模型"
                )

            base_url = str(self.config.get("base_url", "") or "").rstrip("/")
            api_key = str(self.config.get("api_key", "") or "")
            model = str(self.config.get("model", "") or "").strip()
            if not base_url or not api_key or not model:
                raise ValueError("custom 模式下请完整配置 base_url、api_key、model")

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history)
            messages.append({"role": "user", "content": question})

            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=float(self.config.get("timeout", 120) or 120),
            )
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                )
            finally:
                await client.close()
            choices = resp.choices
            text = (choices[0].message.content or "").strip() if choices else ""
        else:
            # 复用 AstrBot 已配置的 Provider
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
            if provider is None:
                raise ValueError(
                    "未检测到已配置的 LLM Provider，请先在 AstrBot 管理面板配置模型，"
                    "或在插件配置中切换到 custom 模式并填写 API 信息。"
                )
            resp = await provider.text_chat(
                prompt=question,
                contexts=history,
                system_prompt=system_prompt,
            )
            text = (resp.completion_text or "").strip()

        return text

    def _record_history(
        self,
        session_key: str,
        question: str,
        answer: str,
    ) -> None:
        """记录一轮问答到历史。"""
        history = self._get_history(session_key)
        if history.maxlen == 0:
            return
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})

    # ------------------------------------------------------------------ #
    # 命令处理                                                             #
    # ------------------------------------------------------------------ #

    @filter.command("ask")
    async def ask(self, event: AstrMessageEvent, question: GreedyStr):
        """AI 助手问答：/ask <问题>。使用固定 AI 助手人设，不继承 AstrBot 主配置人设。"""
        # 阻止 AstrBot 默认 LLM 链路，避免重复回复
        event.should_call_llm(False)

        if not self.config.get("enable", True):
            yield event.plain_result("❌ 插件已停用")
            return

        # 权限检查
        if not self._check_permission(event):
            yield event.plain_result("❌ 权限不足：仅管理员可使用 /ask 命令")
            return

        # 冷却检查
        session_key = event.unified_msg_origin
        remain = self._check_cooldown(session_key)
        if remain:
            yield event.plain_result(f"⏳ 请求过于频繁，请 {remain} 秒后再试")
            return

        question = (question or "").strip()
        # /ask clear —— 清空会话历史（不受冷却限制，且清除冷却记录）
        if question.lower() in ("clear", "清除", "清空"):
            self._histories.pop(session_key, None)
            self._last_use.pop(session_key, None)
            yield event.plain_result("✅ 已清空当前会话的 /ask 对话历史")
            return

        # 冷却检查（放在 clear 之后，冷却中也能清空历史）
        remain = self._check_cooldown(session_key)
        if remain:
            yield event.plain_result(f"⏳ 请求过于频繁，请 {remain} 秒后再试")
            return

        if not question:
            yield event.plain_result(
                "用法: /ask <问题>\n例如: /ask 什么是黑洞？\n/ask clear —— 清空会话历史"
            )
            return

        self._last_use[session_key] = time.time()
        system_prompt = str(self.config.get("system_prompt", "") or "").strip()
        if not system_prompt:
            system_prompt = DEFAULT_SYSTEM_PROMPT

        try:
            answer = await self._call_llm(event, question, system_prompt)
        except Exception as e:
            logger.error(f"/ask 调用 LLM 失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ AI 助手请求失败: {str(e)[:200]}")
            return

        if not answer:
            yield event.plain_result("❌ AI 助手返回了空内容")
            return

        # 记录历史（供后续多轮上下文使用）
        self._record_history(session_key, question, answer)

        # 合并转发发送
        use_forward = bool(self.config.get("use_forward", False))
        forward_threshold = int(self.config.get("forward_threshold", 500) or 500)
        is_qq = event.get_platform_name() == "aiocqhttp"

        if use_forward and is_qq and len(answer) > forward_threshold:
            segments = self._split_text(answer)
            if len(segments) > 1:
                try:
                    nodes = []
                    for seg in segments:
                        node = Comp.Node(
                            name="AI 助手",
                            uin=event.get_self_id(),
                            content=[Comp.Plain(seg)],
                        )
                        nodes.append(node)
                    yield event.chain_result([Comp.Nodes(nodes=nodes)])
                    return
                except Exception as e:
                    logger.warning(f"合并转发失败，降级为直接发送: {e}")
                    # 降级：直接发送
            # 单段或降级：直接发送

        yield event.plain_result(answer)
