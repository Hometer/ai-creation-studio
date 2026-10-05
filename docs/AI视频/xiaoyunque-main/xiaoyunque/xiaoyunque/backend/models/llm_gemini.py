# -*- coding: utf-8 -*-
"""
Google Gemini LLM 客户端 (OpenAI 兼容格式)
支持 gemini-2.5-flash, gemini-2.5-pro 等模型

可用模型:
    - gemini-2.5-flash (性价比高)
    - gemini-2.5-flash-preview
    - gemini-2.5-pro (效果最好)
    - gemini-2.5-pro-preview
    - gemini-2.0-flash
"""

import os
import sys

models_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(models_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import time
import logging
from openai import OpenAI
from typing import List
from config import Config
from models.request_safety import completion_text, initialize_client, positive_seconds

logger = logging.getLogger(__name__)


class Gemini:
    """
    Gemini LLM 客户端，使用 OpenAI 兼容格式调用
    """
    def __init__(self, base_url: str = "", api_key: str = "", timeout: float = 300, total_timeout=None):
        """
        初始化 Gemini 客户端
        :param base_url: OpenAI 兼容的 Base URL
        :param api_key: Gemini API Key
        """
        # 确保 base_url 以 /v1 结尾
        default_url = "https://generativelanguage.googleapis.com/v1beta"
        self.base_url = base_url or Config.GOOGLE_GEMINI_BASE_URL or default_url
        if self.base_url and not self.base_url.endswith("/v1"):
            self.base_url = self.base_url.rstrip("/") + "/v1"
        self.api_key = api_key or Config.GEMINI_API_KEY
        self.timeout = positive_seconds(timeout)
        self.total_timeout = positive_seconds(total_timeout if total_timeout is not None else timeout)
        kwargs = {"api_key": self.api_key, "base_url": self.base_url, "timeout": self.timeout, "max_retries": 0}
        proxy = Config.provider_proxy("gemini")
        self.client = initialize_client(OpenAI, kwargs, logger, proxy=proxy)
        self.max_attempts = 10
        self.max_tokens = 8000

    def query(self, prompt: str, image_urls: List[str] = [], model: str = "gemini-2.5-flash") -> str:
        """
        调用 Gemini LLM
        :param prompt: 文本提示
        :param image_urls: 图片 URL 列表（可选，用于多模态模型）
        :param model: 模型名
        :return: 生成的文本
        """
        if not model:
            model = "gemini-2.5-flash"

        # 构建消息格式
        content: list = [{"type": "text", "text": prompt}]

        # 添加图片 (如果有多模态模型支持)
        if image_urls:
            for img_url in image_urls:
                if img_url.startswith("http"):
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": img_url}
                    })

        messages = [{"role": "user", "content": content}]

        return completion_text(self, {
            "model": model, "messages": messages, "max_tokens": self.max_tokens,
            "temperature": 0.7,
        }, logger)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import Config

    # 支持的模型列表
    MODELS = ["gemini-2.5-flash", "gemini-2.0-flash"]

    print("=== Gemini LLM 可用性测试 ===")
    api_key = Config.GEMINI_API_KEY
    base_url = Config.GOOGLE_GEMINI_BASE_URL
    if not api_key:
        print("✗ GEMINI_API_KEY 未设置，跳过")
        sys.exit(1)
    print("  API Key：已配置（不显示内容）")
    print("  Base URL：使用后端配置（不显示地址）")
    client = Gemini(api_key=api_key, base_url=base_url)
    prompt = "用一句话介绍你自己。"
    print(f"  Prompt: {prompt}")

    for model in MODELS:
        print(f"\n--- 测试模型: {model} ---")
        t0 = time.time()
        try:
            resp = client.query(prompt, model=model)
            elapsed = time.time() - t0
            print("✓ 获得响应（正文与媒体地址不显示）")
        except Exception as e:
            print(f"✗ 失败: {type(e).__name__}")
