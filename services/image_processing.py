"""
Avatar image processing

proves the upload is a real image, fixes phone rotation, strips all
metadata (EXIF incl. GPS location) and re-encodes to a small square WebP.
"""

from io import BytesIO
from PIL import Image, ImageOps, UnidentifiedImageError

AVATAR_SIZE = 512
MAX_PIXELS = 40_000_000 # ~40 MP; guards against "decompression bomb" images


class InvalidImageError(ValueError):
    """raised when uploaded bytes are not a usable image"""

def process_avatar(data: bytes) -> bytes:
    """return a 512x512 centre-cropped WebP with all metadata removed.""" 
    try:
        with Image.open(BytesIO(data)) as img:
            #Image.open reads the header, so this check is cheap
            if img.width * img.height > MAX_PIXELS:
                raise InvalidImageError("Image dimensions are too large")
            img = ImageOps.exif_transpose(img) # apply phone rotation flag
            img = img.convert("RGB")
            img = ImageOps.fit(img, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise InvalidImageError("File is not a valid image.") from e
    
    out = BytesIO()
    img.save(out, format="WEBP", quality=82, method=6)
    return out.getvalue()