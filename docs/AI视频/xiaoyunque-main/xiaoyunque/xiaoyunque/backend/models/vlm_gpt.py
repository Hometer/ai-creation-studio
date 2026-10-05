# -*- coding: utf-8 -*-
"""
OpenAI GPT 多模态大模型 API 客户端
支持 gpt-4o, gpt-5.4 (未来版本) 等视觉模型

代理由 provider 的 enable_proxy 决定，通过 httpx.Client 注入到 OpenAI SDK。
"""

import os
import sys

models_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(models_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import time
import base64
import logging
from openai import OpenAI
from typing import Dict, List, Optional
from config import Config
from models.request_safety import completion_text, initialize_client, positive_seconds

logger = logging.getLogger(__name__)


class GPTVLClient:
    """
    GPT VLM 客户端，支持多模态对话
    """
    def __init__(self,
                 api_key: Optional[str] = None,
                 base_url: Optional[str] = None,
                 proxy: Optional[str] = None,
                 timeout: float = 60.0, total_timeout=None):
        """
        GPT 多模态客户端
        :param api_key: OpenAI API Key
        :param base_url: 自定义 Base URL（如果传入，则不使用本地代理）
        :param timeout: 超时时间
        """
        # 优先使用传入的 api_key，否则从 config.yaml 读取
        self.api_key = api_key or Config.OPENAI_API_KEY
        self.timeout = positive_seconds(timeout)
        self.total_timeout = positive_seconds(total_timeout if total_timeout is not None else timeout)
        
        kwargs = {"api_key": self.api_key, "timeout": self.timeout, "max_retries": 0}
        
        self.base_url = base_url
        if proxy is None:
            proxy = Config.provider_proxy("openai")
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self.client = initialize_client(OpenAI, kwargs, logger, proxy=proxy)
        self.max_attempts = 5
        self.max_tokens = 4096

    def _encode_image(self, image_path: str) -> str:
        """将本地图片编码为 base64"""
        abs_path = os.path.abspath(image_path)
        with open(abs_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _get_mime_type(self, image_path: str) -> str:
        """根据文件扩展名获取 MIME 类型"""
        ext = os.path.splitext(image_path)[1].lower()
        mime_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif"
        }
        return mime_types.get(ext, "image/jpeg")

    def chat(self, text: str, images: List[str], model: str = "gpt-5.4",
             parameters: Optional[Dict] = None) -> str:
        """
        使用 GPT 进行多模态对话（文本+图片）
        :param text: 文本内容
        :param images: 图片路径列表（支持本地路径或URL）
        :param model: 模型名（如 gpt-4o, gpt-5.4）
        :param parameters: 其他API参数
        :return: API响应内容
        """
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY 未设置")

        # 构建消息格式
        content: list = [{"type": "text", "text": text}]

        # 处理图片
        if images:
            for img_path in images:
                if img_path.startswith("data:"):
                    # Base64 数据 URL
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": img_path}
                    })
                elif img_path.startswith("http"):
                    # URL 图片
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": img_path}
                    })
                else:
                    # 本地图片 - 转为 base64
                    mime_type = self._get_mime_type(img_path)
                    base64_data = self._encode_image(img_path)
                    data_url = f"data:{mime_type};base64,{base64_data}"
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": data_url}
                    })

        messages = [{"role": "user", "content": content}]

        return completion_text(self, {
            "model": model, "messages": messages, "max_tokens": self.max_tokens,
            "temperature": parameters.get("temperature", 0.7) if parameters else 0.7,
        }, logger, backoff=lambda attempt: 2 * attempt)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import Config

    print("=== GPT VL 多模态可用性测试 ===")
    if not Config.OPENAI_API_KEY:
        print("✗ 未设置 OPENAI_API_KEY，跳过")
        sys.exit(1)
    client = GPTVLClient(api_key=Config.OPENAI_API_KEY, base_url=Config.OPENAI_BASE_URL, proxy=Config.provider_proxy("openai"))
    
    # 支持的 VLM 模型列表，包含用户要求的 gpt-5.4
    MODELS = ["gpt-5.4"]
    
    img_path = "code/result/image/test_avail/test_input.png"
    if not os.path.exists(img_path):
        print(f"✗ 测试图片不存在: {img_path}")
    else:
        text = "请描述这张图片的内容"
        for model in MODELS:
            print(f"\n--- 测试模型: {model} ---")
            print(f"输入文本: {text}")
            print(f"输入图片: {img_path}")
            t0 = time.time()
            try:
                result = client.chat(text=text, images=[img_path], model=model)
                elapsed = time.time() - t0
                if result:
                    print("✓ 获得响应（正文与媒体地址不显示）")
                else:
                    print(f"✗ 返回空结果 ({elapsed:.1f}s)")
            except Exception as e:
                print(f"✗ 失败: {type(e).__name__}")
