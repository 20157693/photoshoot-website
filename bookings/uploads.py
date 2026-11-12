from pathlib import Path

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

MAX_SIZE = 25 * 1024 * 1024


def validate_photo(upload):
    if Path(upload.name).suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ValidationError("Only JPG and PNG files can be uploaded.")
    if upload.size > MAX_SIZE:
        raise ValidationError("Each photo must be 25 MB or smaller.")
    try:
        with Image.open(upload) as image:
            if image.format not in {"JPEG", "PNG"} or (image.format == "PNG") != (
                Path(upload.name).suffix.lower() == ".png"
            ):
                raise ValidationError("Only JPG and PNG files can be uploaded.")
            image.verify()
        upload.seek(0)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
        raise ValidationError("Only JPG and PNG files can be uploaded.")
