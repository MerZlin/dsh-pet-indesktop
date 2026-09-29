"""Pure image comparison; media library only loaded on use."""

from typing import Any


def image_dhash(img: Any) -> int:
    """计算图像的 64 位 dHash（差异哈希）。

    任意通道与尺寸输入先转为灰度 'L'，缩放到 (9, 8)，
    逐行相邻像素比较（left > right）生成 64 位整数。
    """
    # 转换为灰度图像并缩放到 9x8（宽 9，高 8）
    # 显式使用 NEAREST 采样，兼容各 Pillow 版本
    try:
        from PIL import Image

        resample = getattr(Image, "Resampling", Image).NEAREST
    except Exception:
        resample = 0
    gray = img.convert("L").resize((9, 8), resample)
    # 获取展平后的像素数据（兼容旧版 getdata 与新版 get_flattened_data）
    if hasattr(gray, "get_flattened_data"):
        pixels = list(gray.get_flattened_data())
    elif hasattr(gray, "getdata"):
        pixels = list(gray.getdata())
    else:
        pixels = list(gray.tobytes())

    diff = 0
    width = 9
    for row in range(8):
        row_offset = row * width
        for col in range(8):
            left = pixels[row_offset + col]
            right = pixels[row_offset + col + 1]
            diff = (diff << 1) | (1 if left > right else 0)

    return diff


def hamming_distance(h1: int, h2: int) -> int:
    """计算两个哈希值之间的 Hamming 距离（0~64）。"""
    return bin(h1 ^ h2).count("1")
