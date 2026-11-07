"""GIF encoding shared by the frequency-mode and trajectory animation dialogs.

Kept free of PySide6 so it can be regression-tested without stubbing Qt.
"""

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    Image = None
    HAS_PIL = False


def encode_frames_to_gif(images, path, fps, transparent, use_hq):
    """Convert captured plotter screenshots into a GIF and write it to path."""
    duration = int(1000 / fps)
    processed_images = []
    for img in images:
        if use_hq:
            if transparent:
                # Alpha preservation with adaptive palette
                alpha = img.split()[3]
                img_rgb = img.convert("RGB")
                # Quantize to 255 colors to leave room for transparency
                img_p = img_rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=255)
                # Set transparency
                mask = Image.eval(alpha, lambda a: 255 if a <= 128 else 0)
                img_p.paste(255, mask)
                img_p.info["transparency"] = 255
                processed_images.append(img_p)
            else:
                processed_images.append(
                    img.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
                )
        else:
            if transparent:
                processed_images.append(img.convert("RGBA"))
            else:
                processed_images.append(img.convert("RGB"))

    processed_images[0].save(
        path,
        save_all=True,
        append_images=processed_images[1:],
        duration=duration,
        loop=0,
        disposal=2,
    )
