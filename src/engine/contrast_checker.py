"""
WCAG 2.1/2.2 Color Contrast Checker (Criterion 1.4.3 / 1.4.6 / 1.4.11)
Calculates relative luminance and contrast ratios between text glyphs and background.
"""

import math
from typing import Tuple


class ContrastChecker:
    """
    Computes WCAG compliant relative luminance and contrast ratios.
    """

    @staticmethod
    def hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
        """Converts '#RRGGBB' to (R, G, B)."""
        hex_str = hex_str.lstrip("#")
        if len(hex_str) == 3:
            hex_str = "".join(2 * s for s in hex_str)
        if len(hex_str) != 6:
            return (0, 0, 0)
        try:
            return tuple(int(hex_str[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return (0, 0, 0)

    @classmethod
    def get_relative_luminance(cls, r: int, g: int, b: int) -> float:
        """
        Calculates sRGB relative luminance according to WCAG 2.1 definition.
        """
        rs = r / 255.0
        gs = g / 255.0
        bs = b / 255.0

        # WCAG 2.x linearization: threshold is 0.03928 (not 0.04045, which is sRGB)
        r_lin = rs / 12.92 if rs <= 0.03928 else math.pow((rs + 0.055) / 1.055, 2.4)
        g_lin = gs / 12.92 if gs <= 0.03928 else math.pow((gs + 0.055) / 1.055, 2.4)
        b_lin = bs / 12.92 if bs <= 0.03928 else math.pow((bs + 0.055) / 1.055, 2.4)

        return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin

    @classmethod
    def calculate_contrast_ratio(cls, fg_hex: str, bg_hex: str = "#FFFFFF") -> float:
        """
        Calculates (L1 + 0.05) / (L2 + 0.05) where L1 is the lighter of the colors.
        """
        fg_rgb = cls.hex_to_rgb(fg_hex)
        bg_rgb = cls.hex_to_rgb(bg_hex)

        l1 = cls.get_relative_luminance(*fg_rgb)
        l2 = cls.get_relative_luminance(*bg_rgb)

        lighter = max(l1, l2)
        darker = min(l1, l2)

        # Return the exact ratio; compliance comparison must use the unrounded
        # value so a ratio of e.g. 4.496 does not round up to a false PASS at 4.5.
        return (lighter + 0.05) / (darker + 0.05)

    @classmethod
    def check_compliance(cls, font_size: float, is_bold: bool, fg_hex: str, bg_hex: str = "#FFFFFF") -> Tuple[bool, float, float]:
        """
        Returns (is_compliant, contrast_ratio, required_ratio).
        """
        ratio = cls.calculate_contrast_ratio(fg_hex, bg_hex)
        is_large_text = font_size >= 18.0 or (font_size >= 14.0 and is_bold)
        required_ratio = 3.0 if is_large_text else 4.5
        return (ratio >= required_ratio, round(ratio, 2), required_ratio)
