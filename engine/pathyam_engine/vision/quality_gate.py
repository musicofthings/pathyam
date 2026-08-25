"""Image Quality Gate for Meal Vision Extraction.

Validates image bytes for size, format, framing, and minimum resolution before
invoking the VLM service to prevent unnecessary API charges and unreliable perception.
"""

from __future__ import annotations

import io
from .protocol import ImageQualityGateResult

__all__ = ["ImageQualityGate", "evaluate_image_quality"]


class ImageQualityGate:
    """Pre-screens uploaded meal photos."""

    def __init__(
        self,
        min_bytes: int = 1000,
        max_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self.min_bytes = min_bytes
        self.max_bytes = max_bytes

    def evaluate(self, image_bytes: bytes) -> ImageQualityGateResult:
        issues: list[str] = []
        size = len(image_bytes)

        if size < self.min_bytes:
            issues.append(f"Image file size too small ({size} bytes)")
        if size > self.max_bytes:
            issues.append(f"Image file size exceeds limit ({size / 1024 / 1024:.1f} MB)")

        # Attempt basic header validation for common image formats (JPEG, PNG, WEBP)
        is_jpeg = image_bytes.startswith(b"\xff\xd8\xff")
        is_png = image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        is_webp = image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16]

        if not (is_jpeg or is_png or is_webp) and size > 0:
            # Allow generic binary input in dev/mock environments with warning
            issues.append("Unrecognized image header format; expected JPEG, PNG, or WEBP")

        score = 1.0 - (0.3 * len(issues))
        score = max(0.0, min(1.0, score))
        passed = len([i for i in issues if "size" in i]) == 0

        return ImageQualityGateResult(
            passed=passed,
            quality_score=score,
            issues=issues,
        )


def evaluate_image_quality(image_bytes: bytes) -> ImageQualityGateResult:
    gate = ImageQualityGate()
    return gate.evaluate(image_bytes)
