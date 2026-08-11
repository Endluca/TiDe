#!/usr/bin/env python3
"""BytePlus ModelArk Responses API 的最小文字／图片调用示例。"""

import argparse
import base64
import mimetypes
import os
from pathlib import Path

from openai import OpenAI


DEFAULT_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
DEFAULT_MODEL = "seed-2-0-lite-260228"


def image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0]
    if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("--image 只支持 JPEG、PNG 或 WebP")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call BytePlus ModelArk through the OpenAI Responses API client."
    )
    parser.add_argument("--input", required=True, help="发送给模型的用户文本")
    parser.add_argument("--system", default="Return a strict JSON object.")
    parser.add_argument("--image", type=Path, help="可选的本地私有图片")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default=os.getenv("ARK_API_KEY"))
    args = parser.parse_args()
    if not args.api_key:
        parser.error("请设置 ARK_API_KEY 或传入 --api-key")
    if args.base_url.rstrip("/").endswith("/responses"):
        parser.error("--base-url 只填写到 /api/v3，不能追加 /responses")
    if args.image and not args.image.is_file():
        parser.error(f"图片不存在：{args.image}")
    return args


def main() -> None:
    args = parse_args()
    content = [{"type": "input_text", "text": args.input}]
    if args.image:
        content.append(
            {
                "type": "input_image",
                "image_url": image_data_url(args.image),
                "detail": "high",
            }
        )

    client = OpenAI(
        base_url=args.base_url.rstrip("/"),
        api_key=args.api_key,
        max_retries=0,
        timeout=30,
    )
    response = client.responses.create(
        model=args.model,
        input=[
            {"role": "system", "content": args.system},
            {"role": "user", "content": content},
        ],
        max_output_tokens=4096,
        store=False,
        temperature=0,
        text={"format": {"type": "json_object"}},
        extra_body={"thinking": {"type": "disabled"}},
    )
    print(response.output_text)


if __name__ == "__main__":
    main()
