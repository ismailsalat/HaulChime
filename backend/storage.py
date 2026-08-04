"""
Storage abstraction for uploaded photos.

LocalStorage writes to disk (development). To move to cloud object storage,
implement the same three methods in S3Storage and set STORAGE_BACKEND=s3.
Application code only ever talks to `get_storage(app)`.
"""
import io
import os
import secrets

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:  # lets the API boot and accept no-photo leads if Pillow is missing
    Image = None
    class UnidentifiedImageError(Exception):
        pass


class StorageError(Exception):
    pass


class BaseStorage:
    def save(self, file_storage, max_dimension):
        raise NotImplementedError

    def url_for(self, key):
        raise NotImplementedError

    def delete(self, key):
        raise NotImplementedError


def _process_image(file_storage, max_dimension):
    """Validate the file is a real image; downscale oversized photos.
    Returns (bytes, extension). Raises StorageError on invalid input."""
    if Image is None:
        raise StorageError("Photo processing is unavailable. Install Pillow.")
    raw = file_storage.read()
    file_storage.seek(0)
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()  # detect truncated / fake images
        img = Image.open(io.BytesIO(raw))  # reopen after verify()
    except (UnidentifiedImageError, OSError):
        raise StorageError("File is not a valid image.")

    fmt = (img.format or "").lower()
    if fmt not in ("jpeg", "png", "webp"):
        raise StorageError("Only JPG, PNG and WEBP images are accepted.")

    if max(img.size) > max_dimension:
        img.thumbnail((max_dimension, max_dimension))
        buf = io.BytesIO()
        if fmt == "jpeg":
            img = img.convert("RGB")
            img.save(buf, "JPEG", quality=85, optimize=True)
        else:
            img.save(buf, fmt.upper())
        raw = buf.getvalue()

    ext = "jpg" if fmt == "jpeg" else fmt
    return raw, ext


class LocalStorage(BaseStorage):
    def __init__(self, base_dir, public_route="/api/photos"):
        self.base_dir = base_dir
        self.public_route = public_route
        os.makedirs(base_dir, exist_ok=True)

    def save(self, file_storage, max_dimension):
        data, ext = _process_image(file_storage, max_dimension)
        # Randomized filename: never trust the client-supplied name.
        key = f"{secrets.token_urlsafe(16)}.{ext}"
        with open(os.path.join(self.base_dir, key), "wb") as fh:
            fh.write(data)
        return key

    def url_for(self, key):
        return f"{self.public_route}/{key}"

    def delete(self, key):
        path = os.path.join(self.base_dir, os.path.basename(key))
        if os.path.exists(path):
            os.remove(path)


class S3Storage(BaseStorage):
    """S3-compatible object storage: AWS S3, Cloudflare R2, Backblaze B2.

    Photos are customer addresses in visual form, so the bucket stays PRIVATE
    and every read goes through a short-lived pre-signed URL. Nothing is ever
    made publicly readable.

    Needed environment variables:
        STORAGE_BACKEND=s3
        S3_BUCKET=haulchime-photos
        S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY
        S3_ENDPOINT_URL   (R2/B2 only; leave blank for AWS)
        S3_REGION         (default us-east-1)
    """

    def __init__(self, config):
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError:
            raise StorageError(
                "STORAGE_BACKEND=s3 needs boto3. Add it to requirements.txt.")
        self.bucket = config.get("S3_BUCKET") or ""
        if not self.bucket:
            raise StorageError("STORAGE_BACKEND=s3 but S3_BUCKET is not set.")
        self.prefix = (config.get("S3_PREFIX") or "photos").strip("/")
        self.expiry = int(config.get("S3_URL_EXPIRY_SECONDS") or 900)
        kwargs = {
            "aws_access_key_id": config.get("S3_ACCESS_KEY_ID") or None,
            "aws_secret_access_key": config.get("S3_SECRET_ACCESS_KEY") or None,
            "region_name": config.get("S3_REGION") or "us-east-1",
            # SigV4 is required by R2 and B2, and harmless on AWS.
            "config": BotoConfig(signature_version="s3v4"),
        }
        endpoint = (config.get("S3_ENDPOINT_URL") or "").strip()
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        self.client = boto3.client("s3", **kwargs)

    def _full_key(self, key):
        return f"{self.prefix}/{key}" if self.prefix else key

    def save(self, file_storage, max_dimension):
        data, ext = _process_image(file_storage, max_dimension)
        key = f"{secrets.token_urlsafe(16)}.{ext}"
        content_type = {"jpg": "image/jpeg", "png": "image/png",
                        "webp": "image/webp"}[ext]
        self.client.put_object(
            Bucket=self.bucket, Key=self._full_key(key), Body=data,
            ContentType=content_type,
            # Belt and braces: never let a bucket policy make these public.
            ACL="private" if not self.client.meta.endpoint_url.endswith(
                "r2.cloudflarestorage.com") else "private",
        )
        return key

    def url_for(self, key):
        """A pre-signed URL that expires. Never a permanent public link."""
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._full_key(key)},
            ExpiresIn=self.expiry)

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket,
                                  Key=self._full_key(os.path.basename(key)))


def get_storage(config):
    if config["STORAGE_BACKEND"] == "s3":
        return S3Storage(config)
    return LocalStorage(config["UPLOAD_DIR"])
