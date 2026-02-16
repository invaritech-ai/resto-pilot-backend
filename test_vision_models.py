#!/usr/bin/env python3
"""
Vision Model OCR Benchmark Script

Tests multiple vision models on the same invoice image to find the best OCR accuracy.
Outputs results in a comparable format for evaluation against gold standard.
"""

import base64
import httpx
import json
import time
from pathlib import Path
from datetime import datetime

# Test configuration
IMAGE_PATH = "/Users/avi/Downloads/IMG_7646.jpg"
OUTPUT_DIR = Path("./ocr_price_benchmark_results")

# Models to test (in order)
MODELS = [
    {
        "name": "Qwen 3.5 Plus",
        "model": "qwen/qwen3.5-plus-02-15",
        "provider": "openrouter",
    },
    {
        "name": "Qwen 3.5 397B",
        "model": "qwen/qwen3.5-397b-a17b",
        "provider": "openrouter",
    },
    {
        "name": "Opus 4.6",
        "model": "anthropic/claude-opus-4.6",
        "provider": "openrouter",
    },
    {"name": "GPT-5.2 Pro", "model": "openai/gpt-5.2-pro", "provider": "openrouter"},
    {
        "name": "Kimi K2.5",
        "model": "moonshotai/kimi-k2.5",
        "provider": "openrouter",
    },
    {
        "name": "ByteDance Seed 1.6",
        "model": "bytedance-seed/seed-1.6",
        "provider": "openrouter",
    },
    {
        "name": "Gemini 3 Flash",
        "model": "google/gemini-3-flash-preview",
        "provider": "openrouter",
    },
    {
        "name": "GPT 5.2 Chat",
        "model": "openai/gpt-5.2-chat",
        "provider": "openrouter",
    },
    {
        "name": "GPT 5.2",
        "model": "openai/gpt-5.2",
        "provider": "openrouter",
    },
    {
        "name": "GLM V4.6",
        "model": "z-ai/glm-4.6v",
        "provider": "openrouter",
    },
    {
        "name": "Mistral Large",
        "model": "mistralai/mistral-large-2512",
        "provider": "openrouter",
    },
    {
        "name": "Gemini 3 Pro",
        "model": "google/gemini-3-pro-preview",
        "provider": "openrouter",
    },
]

# Pricing (per million tokens) - from OpenRouter
PRICING = {
    "qwen/qwen3.5-plus-02-15": {"input": 0.42, "output": 1.40},
    "qwen/qwen3.5-397b-a17b": {"input": 0.42, "output": 1.90},
    "anthropic/claude-opus-4.6": {"input": 15.0, "output": 75.0},
    "openai/gpt-5.2-pro": {"input": 15.0, "output": 120.0},
    "moonshotai/kimi-k2.5": {"input": 0.14, "output": 0.42},
    "bytedance-seed/seed-1.6": {"input": 2.5, "output": 10.0},
    "google/gemini-3-flash-preview": {"input": 0.075, "output": 0.30},
    "openai/gpt-5.2-chat": {"input": 3.0, "output": 24.0},
    "openai/gpt-5.2": {"input": 3.0, "output": 24.0},
    "z-ai/glm-4.6v": {"input": 0.20, "output": 0.60},
    "mistralai/mistral-large-2512": {"input": 2.0, "output": 6.0},
    "google/gemini-3-pro-preview": {"input": 1.25, "output": 5.0},
}

# OCR Prompt (exact same as production)
OCR_PROMPT = """Extract all text from this page (1/1). This may contain handwritten text - carefully distinguish similar-looking characters (4 vs 9, 1 vs 7, 0 vs 6, 3 vs 8, 5 vs S). Preserve tables using Markdown table syntax. Keep all headers, footers, and page numbers. Use headings (##, ###) for section titles. Return ONLY Markdown - no commentary."""

# API Configuration
OPENROUTER_API_KEY = None  # Will load from env
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def load_api_key():
    """Load API key from .env file"""
    global OPENROUTER_API_KEY
    from pathlib import Path

    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("APP_OPENAI_API_KEY="):
                OPENROUTER_API_KEY = line.split("=", 1)[1].strip().strip('"')
                break

    if not OPENROUTER_API_KEY:
        raise Exception("API key not found in .env file")


def resize_image(image_path: str, max_side: int = 4000) -> bytes:
    """Resize image if needed (same logic as production)"""
    from PIL import Image
    import io

    image = Image.open(image_path)
    original_size = (image.width, image.height)

    if max(image.width, image.height) > max_side:
        ratio = max_side / max(image.width, image.height)
        new_width = int(image.width * ratio)
        new_height = int(image.height * ratio)
        image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        print(
            f"  Resized: {original_size[0]}x{original_size[1]} → {new_width}x{new_height}"
        )

    # Convert to bytes
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=95)
    return output.getvalue()


def test_model(model_config: dict, image_bytes: bytes) -> dict:
    """Test a single model and return results"""
    print(f"\n{'=' * 80}")
    print(f"Testing: {model_config['name']}")
    print(f"Model: {model_config['model']}")
    print(f"{'=' * 80}")

    # Encode image
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    # Build request
    url = f"{OPENROUTER_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": OCR_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}",
                    },
                },
            ],
        }
    ]

    payload = {
        "model": model_config["model"],
        "messages": messages,
        "temperature": 0.0,  # Maximum repeatability
    }

    # Make request
    start_time = time.time()
    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()

        latency = time.time() - start_time

        # Extract response
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        # Calculate cost
        cost_usd = 0.0
        if model_config["model"] in PRICING:
            pricing = PRICING[model_config["model"]]
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            cost_usd = (prompt_tokens / 1_000_000) * pricing["input"] + (
                completion_tokens / 1_000_000
            ) * pricing["output"]

        result = {
            "model": model_config["model"],
            "model_name": model_config["name"],
            "success": True,
            "latency_seconds": round(latency, 2),
            "content": content,
            "usage": usage,
            "cost_usd": round(cost_usd, 6),
            "error": None,
        }

        print(f"✅ Success in {latency:.2f}s")
        print(
            f"   Tokens: {usage.get('prompt_tokens', '?')} prompt + {usage.get('completion_tokens', '?')} completion"
        )
        print(f"   Cost: ${cost_usd:.6f}")
        print(f"   Output length: {len(content)} chars")

        return result

    except Exception as e:
        latency = time.time() - start_time
        result = {
            "model": model_config["model"],
            "model_name": model_config["name"],
            "success": False,
            "latency_seconds": round(latency, 2),
            "content": None,
            "usage": None,
            "cost_usd": 0.0,
            "error": str(e),
        }

        print(f"❌ Failed: {e}")
        return result


def save_results(results: list):
    """Save results to files"""
    OUTPUT_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save summary JSON
    summary_file = OUTPUT_DIR / f"summary_{timestamp}.json"
    summary = {
        "timestamp": timestamp,
        "image_path": IMAGE_PATH,
        "models_tested": len(results),
        "results": [
            {
                "model": r["model_name"],
                "success": r["success"],
                "latency_seconds": r["latency_seconds"],
                "tokens": r["usage"].get("total_tokens") if r["usage"] else None,
                "cost_usd": r.get("cost_usd", 0.0),
                "error": r["error"],
            }
            for r in results
        ],
    }
    summary_file.write_text(json.dumps(summary, indent=2))
    print(f"\n📊 Summary saved to: {summary_file}")

    # Save individual outputs
    for i, result in enumerate(results, 1):
        if result["success"] and result["content"]:
            output_file = (
                OUTPUT_DIR
                / f"{i:02d}_{result['model_name'].replace(' ', '_')}_{timestamp}.md"
            )
            output_file.write_text(result["content"])
            print(f"   {i}. {result['model_name']}: {output_file}")

    # Save full results
    full_file = OUTPUT_DIR / f"full_results_{timestamp}.json"
    full_file.write_text(json.dumps(results, indent=2))
    print(f"\n💾 Full results saved to: {full_file}")


def main():
    print("=" * 80)
    print("VISION MODEL OCR BENCHMARK")
    print("=" * 80)

    # Load API key
    print("\n📋 Loading configuration...")
    load_api_key()
    if OPENROUTER_API_KEY:
        print(f"   API Key: ...{OPENROUTER_API_KEY[-8:]}")

    # Load and resize image
    print(f"\n🖼️  Loading image: {IMAGE_PATH}")
    image_bytes = resize_image(IMAGE_PATH, max_side=4000)
    print(f"   Image size: {len(image_bytes):,} bytes")

    print(f"\n🧪 Testing {len(MODELS)} models...")

    # Test each model
    results = []
    for i, model_config in enumerate(MODELS, 1):
        print(f"\n[{i}/{len(MODELS)}]")
        result = test_model(model_config, image_bytes)
        results.append(result)

        # Brief pause between requests
        if i < len(MODELS):
            time.sleep(2)

    # Save results
    print("\n" + "=" * 80)
    print("SAVING RESULTS")
    print("=" * 80)
    save_results(results)

    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    successful = sum(1 for r in results if r["success"])
    print(f"✅ Successful: {successful}/{len(results)}")
    print(f"❌ Failed: {len(results) - successful}/{len(results)}")

    if successful > 0:
        avg_latency = (
            sum(r["latency_seconds"] for r in results if r["success"]) / successful
        )
        avg_cost = (
            sum(r.get("cost_usd", 0) for r in results if r["success"]) / successful
        )
        print(f"⏱️  Average latency: {avg_latency:.2f}s")
        print(f"💰 Average cost: ${avg_cost:.6f}")

        # Print comparison table
        print("\n" + "=" * 80)
        print("COMPARISON TABLE (Successful Models)")
        print("=" * 80)
        print(f"{'Model':<25} {'Speed':<10} {'Tokens':<12} {'Cost':<12}")
        print("-" * 80)

        for r in results:
            if r["success"]:
                model = r["model_name"][:24]
                speed = f"{r['latency_seconds']:.2f}s"
                tokens = (
                    f"{r['usage'].get('total_tokens', 0):,}" if r["usage"] else "N/A"
                )
                cost = f"${r.get('cost_usd', 0):.6f}"
                print(f"{model:<25} {speed:<10} {tokens:<12} {cost:<12}")

        print("-" * 80)
        print(f"{'AVERAGE':<25} {avg_latency:>8.2f}s {'':<12} ${avg_cost:>10.6f}")

    print("\n✨ Done! Check the ocr_benchmark_results/ directory for outputs.")
    print("\nNext steps:")
    print("1. Review outputs in ocr_benchmark_results/")
    print("2. Compare against gold_standard.json")
    print("3. Choose best model based on: accuracy + speed + cost")


if __name__ == "__main__":
    main()
