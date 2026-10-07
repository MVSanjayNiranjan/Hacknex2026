"""Map extractor bounding boxes (PDF points, unrotated page space) to
rendered-image pixel coordinates.

Handles page rotation and INDEPENDENT horizontal/vertical scaling (rendered
aspect ratio may differ slightly from the point-space aspect ratio due to
pixel rounding). Pure functions so they can be unit-tested without a browser;
the client only applies the final displayed-size scale factor.
"""
from typing import Optional


def _rotate_point(x: float, y: float, w: float, h: float, rotation: int) -> tuple[float, float]:
    """Rotate a point from unrotated page space (origin top-left, w x h pts)
    into the displayed (rotated) space. Clockwise rotations, per PDF spec."""
    r = rotation % 360
    if r == 0:
        return x, y
    if r == 90:
        return h - y, x
    if r == 180:
        return w - x, h - y
    if r == 270:
        return y, w - x
    raise ValueError(f"unsupported rotation {rotation}")


def rotated_page_dims(page_w_pts: float, page_h_pts: float, rotation: int) -> tuple[float, float]:
    """Displayed page dimensions in points after rotation."""
    return ((page_h_pts, page_w_pts) if rotation % 180 else (page_w_pts, page_h_pts))


def bbox_to_image_px(bbox: list[float], page_w_pts: float, page_h_pts: float,
                     rotation: int, img_w: int, img_h: int,
                     crop_x0: float = 0.0, crop_y0: float = 0.0) -> Optional[list[float]]:
    """Return [x0, y0, x1, y1] in rendered image pixels, or None when the
    mapping cannot be computed (missing dims, out-of-bounds result) —
    callers must then show the page WITHOUT any highlight rather than
    guessing. ``page_*_pts`` describe the CropBox (the rendered region);
    the CropBox origin is subtracted before rotation."""
    if not bbox or not page_w_pts or not page_h_pts or not img_w or not img_h:
        return None
    # Normalize into crop-relative coordinates (crop origin -> 0,0).
    x0, x1 = bbox[0] - crop_x0, bbox[2] - crop_x0
    y0, y1 = bbox[1] - crop_y0, bbox[3] - crop_y0
    disp_w, disp_h = rotated_page_dims(page_w_pts, page_h_pts, rotation)
    corners = [
        _rotate_point(x0, y0, page_w_pts, page_h_pts, rotation),
        _rotate_point(x1, y0, page_w_pts, page_h_pts, rotation),
        _rotate_point(x0, y1, page_w_pts, page_h_pts, rotation),
        _rotate_point(x1, y1, page_w_pts, page_h_pts, rotation),
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    sx = img_w / disp_w   # independent x scale
    sy = img_h / disp_h   # independent y scale
    out = [round(min(xs) * sx, 1), round(min(ys) * sy, 1),
           round(max(xs) * sx, 1), round(max(ys) * sy, 1)]
    # Fail closed: a result outside the rendered image means our coordinate
    # assumptions are wrong for this page — show NO highlight, not a wrong one.
    if out[0] < 0 or out[1] < 0 or out[2] > img_w or out[3] > img_h \
            or out[2] - out[0] < 1 or out[3] - out[1] < 1:
        return None
    return out
