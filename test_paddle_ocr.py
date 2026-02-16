#!/usr/bin/env python3
"""Test PaddleOCR in isolation to see the exact output format."""

from paddleocr import PaddleOCR
from PIL import Image
import numpy as np
import json

# Initialize PaddleOCR
print("Initializing PaddleOCR...")
ocr = PaddleOCR(
    use_angle_cls=True,
    lang='ch'
)

# Load image
image_path = "/Users/avi/Downloads/IMG_7646.jpg"
print(f"\nLoading image: {image_path}")
image = Image.open(image_path)
print(f"Original size: {image.width}x{image.height}")

# Resize
max_side = 2000
if max(image.width, image.height) > max_side:
    ratio = max_side / max(image.width, image.height)
    new_width = int(image.width * ratio)
    new_height = int(image.height * ratio)
    image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
    print(f"Resized to: {new_width}x{new_height} (ratio: {ratio:.2f})")

img_array = np.array(image)

# Run OCR
print("\nRunning OCR...")
result = ocr.ocr(img_array)

# Inspect result structure
print("\n" + "="*80)
print("RESULT STRUCTURE:")
print("="*80)
print(f"Type: {type(result)}")
print(f"Length: {len(result) if result else 'None'}")

if result and len(result) > 0:
    print(f"\nResult[0] type: {type(result[0])}")

    # Check if it's a dict or list
    if isinstance(result[0], dict):
        print(f"Result[0] is a dict with keys: {result[0].keys()}")
        print(f"\nFull result[0]:")
        import pprint
        pprint.pprint(result[0])
    elif isinstance(result[0], list):
        print(f"First page has {len(result[0])} text lines")

        # Show first 3 lines in detail
        print("\nFirst 3 lines (detailed):")
        for i, line in enumerate(result[0][:3]):
            print(f"\nLine {i}:")
            print(f"  Type: {type(line)}")
            print(f"  Length: {len(line)}")
            print(f"  [0] (box): {line[0]}")
            print(f"  [1] type: {type(line[1])}")
            print(f"  [1] value: {line[1]}")
            if isinstance(line[1], (tuple, list)):
                print(f"  [1] length: {len(line[1])}")
                for j, item in enumerate(line[1]):
                    print(f"    [1][{j}]: {item} (type: {type(item)})")

        # Extract and show all text
        print("\n" + "="*80)
        print("ALL EXTRACTED TEXT:")
        print("="*80)
        for i, line in enumerate(result[0]):
            box = line[0]
            text_info = line[1]

            if isinstance(text_info, (tuple, list)) and len(text_info) >= 1:
                text = text_info[0]
                conf = text_info[1] if len(text_info) >= 2 else "N/A"
            else:
                text = str(text_info)
                conf = "N/A"

            print(f"{i+1:3d}. {text:40s} (conf: {conf})")
    else:
        print("Result[0] is neither dict nor list!")

else:
    print("\nNo results found!")

print("\n" + "="*80)
print("Done!")
