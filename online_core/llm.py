import os
from typing import Generator, Optional
from openai import OpenAI
from dotenv import load_dotenv
import requests
import pdb
import json

load_dotenv()

class BaseLLM:
    def generate(self, prompt: str) -> str:
        raise NotImplementedError

class OpenAI_LLM(BaseLLM):
    def __init__(self, default_set, logger, cost_tracker=None):
        self.logger = logger
        self.set = default_set
        self.client = OpenAI(
            api_key=os.getenv(self.set['apikey']),
            base_url=default_set['baseurl'])#通过DeepSeek官方接口访问
        self.model=default_set['model']#or "deepseek-reasoner"
        self.logger.info(f"using model {self.model}")

        # Token 计费追踪（可选）
        self.cost_tracker = cost_tracker
        if cost_tracker:
            self.logger.info(f"CostTracker 已启用: model={self.model}")

    def generate(self, prompt: list, response_format=None) -> str:
        """调用 LLM 生成回复。

        Args:
            prompt: 消息列表 [{"role": ..., "content": ...}, ...]
            response_format: OpenAI response_format 参数，例如 {"type": "json_object"}
        """
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=prompt,
            stream=False,
            response_format=response_format,
        )
        self.logger.info(f"DeepSeekLLM response received")

        # 记录 usage 到 CostTracker
        if self.cost_tracker:
            try:
                from utils.llm_monitor import record_usage_from_response
                record_usage_from_response(resp, self.model, self.cost_tracker)
            except Exception as e:
                self.logger.warning(f"CostTracker 记录失败: {e}")

        return resp

    def generate_stream(self, prompt: list) -> Generator[str, None, None]:
        """流式调用 LLM，逐 token 产出。

        最后一个 chunk 可能包含 usage 信息（若 API 支持 stream_options）。

        Args:
            prompt: 消息列表 [{"role": ..., "content": ...}, ...]

        Yields:
            逐个 token 文本
        """
        extra_kwargs = {}
        if self.cost_tracker:
            # 请求 API 在流式最后一个 chunk 返回 usage
            extra_kwargs["stream_options"] = {"include_usage": True}

        response = self.client.chat.completions.create(
            model=self.model,
            messages=prompt,
            stream=True,
            **extra_kwargs,
        )

        usage_chunk = None
        output_token_count = 0

        for chunk in response:
            # 检查最后一个 chunk 是否包含 usage
            if hasattr(chunk, "usage") and chunk.usage:
                usage_chunk = chunk.usage
                continue  # usage chunk 不含 content，跳过产出

            delta = chunk.choices[0].delta
            if delta and delta.content:
                output_token_count += 1
                yield delta.content

        # 记录 usage
        if self.cost_tracker:
            try:
                if usage_chunk:
                    # API 返回了 usage chunk → 精确记录
                    self.cost_tracker.record(
                        usage=usage_chunk, model=self.model
                    )
                    self.logger.info(
                        f"流式 usage 已记录: prompt={getattr(usage_chunk, 'prompt_tokens', '?')}, "
                        f"completion={getattr(usage_chunk, 'completion_tokens', '?')}"
                    )
                else:
                    # Fallback: 估算 input + 实际 output 计数
                    from utils.llm_monitor import estimate_usage_from_messages
                    self.logger.warning(
                        "API 流式响应不含 usage chunk，使用估算值"
                    )
                    estimate_usage_from_messages(
                        messages=prompt,
                        output_token_count=output_token_count,
                        model=self.model,
                        cost_tracker=self.cost_tracker,
                    )
            except Exception as e:
                self.logger.warning(f"CostTracker 流式记录失败: {e}")

    async def generate_stream_async(self, prompt: list) -> Generator[str, None, None]:
        """异步流式调用 LLM（基于 generate_stream 的异步包装）。

        Args:
            prompt: 消息列表 [{"role": ..., "content": ...}, ...]

        Yields:
            逐个 token 文本
        """
        import asyncio
        loop = asyncio.get_event_loop()
        # 在线程池中运行同步生成器，逐个 token 产出
        gen = self.generate_stream(prompt)
        while True:
            try:
                token = await loop.run_in_executor(None, next, gen)
                yield token
            except StopIteration:
                break

def open_router_func():
    response = requests.post(
    url="https://openrouter.ai/api/v1/chat/completions",
    headers={
        "Authorization": "Bearer <OPENROUTER_API_KEY>",
        "HTTP-Referer": "<YOUR_SITE_URL>", # Optional. Site URL for rankings on openrouter.ai.
        "X-OpenRouter-Title": "<YOUR_SITE_NAME>", # Optional. Site title for rankings on openrouter.ai.
    },
    data=json.dumps({
        "model": "openai/gpt-5.2", # Optional
        "messages": [
        {
            "role": "user",
            "content": "What is the meaning of life?"
        }
        ]
    })
    )

class MiniMaxLLM(BaseLLM):
    url = "https://api.edgefn.net/v1/chat/completions"
    headers = {
            "Authorization": f"Bearer {os.getenv('BAISHAN_API_KEY')}", #通过白山云访问
            "Content-Type": "application/json"
        }
    def __init__(self, model, logger):
        self.model = model
        self.logger = logger
        self.logger.info(f"MiniMaxLLM using model {self.model}")

    def generate(self, prompt: str) -> str:
        
        data={
        "model":"MiniMax-M2.5",
        "messages":prompt}
        resp = requests.post(self.url, headers=self.headers, json=data)
        self.logger.info(f"MiniMaxLLM response received")
        return resp
    

def set_LLM(config, logger_manager, set_name='default_set', cost_tracker=None):
    #pdb.set_trace()
    set =  config.get(set_name)
    default_set = config.get("llm."+set)
    logger = logger_manager.llm_logger
    try:
        return OpenAI_LLM(default_set, logger, cost_tracker=cost_tracker)
    except:
        logger.error(f"Unsupported LLM setting.")
        raise ValueError(f"Unsupported LLM setting.")