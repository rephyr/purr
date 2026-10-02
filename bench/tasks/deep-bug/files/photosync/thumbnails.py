"""Thumbnail sizes for the server's gallery."""


def thumbnail_size(width, height, max_px=320):
    if width <= max_px and height <= max_px:
        return width, height
    scale = max_px / max(width, height)
    return max(1, round(width * scale)), max(1, round(height * scale))
