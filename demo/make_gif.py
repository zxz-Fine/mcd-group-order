"""把逐帧 PNG 合成为优化后的 GIF。

用法：
    python make_gif.py <frames_dir> <out.gif> [fps] [width] [colors]

帧从哪来
--------
本脚本只负责合成，截帧由外部完成。被截的页面现在是
``docs/demo/animation.html``（已随站点移入 docs/）。

⚠️ 截帧时**必须关掉页面的自动播放**，否则 requestAnimationFrame 会覆盖
你传入的 ``render(t)``，截出来全是同一时刻：
    · 加载 ``docs/demo/animation.html?static=1``，或
    · 在截帧前先执行 ``window.stopAutoplay()``
舞台固定 900x700，视口按 900x700 截即可。
"""

import os
import sys

from PIL import Image


def main() -> int:
    frames_dir = sys.argv[1]
    out_path = sys.argv[2]
    fps = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0
    width = int(sys.argv[4]) if len(sys.argv) > 4 else 880
    colors = int(sys.argv[5]) if len(sys.argv) > 5 else 128

    names = sorted(f for f in os.listdir(frames_dir) if f.endswith(".png"))
    if not names:
        print("没有找到帧文件")
        return 1

    images = []
    for name in names:
        im = Image.open(os.path.join(frames_dir, name)).convert("RGB")
        if im.width != width:
            h = round(im.height * width / im.width)
            im = im.resize((width, h), Image.LANCZOS)
        images.append(im)

    # 逐帧自适应量化到 P 模式。
    # 注意：不要用 quantize(palette=...) —— 那样得到的帧与 append_images 不兼容，
    # 只会存下第一帧（实测踩过）。用 convert('P', palette=ADAPTIVE) 是标准做法。
    pal_frames = [
        im.convert("P", palette=Image.ADAPTIVE, colors=colors) for im in images
    ]

    pal_frames[0].save(
        out_path,
        save_all=True,
        append_images=pal_frames[1:],
        duration=round(1000 / fps),
        loop=0,
        optimize=True,
    )

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"{out_path}: {len(pal_frames)} 帧, {images[0].width}x{images[0].height}, {size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
