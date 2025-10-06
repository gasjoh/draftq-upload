import os
import uuid
import datetime
from typing import Optional

from flask import Flask, request, jsonify, make_response
from werkzeug.utils import secure_filename
from flask_cors import CORS  # ✅ Allow WordPress to call this API

# --- Optional S3 imports (only used if env vars are present) ---
try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except Exception:
    boto3 = None
    BotoCoreError = ClientError = Exception  # type: ignore

# =========================
# Config
# =========================
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://www.draftq.ae")  # ✅ Restrict CORS to your site
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "30"))
ALLOWED_EXTS = {".pdf"}

# Local storage path (used if S3 not configured)
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")  # Render persistent disk path
os.makedirs(UPLOAD_DIR, exist_ok=True)

# S3 configuration
S3_BUCKET = os.environ.get("S3_BUCKET_NAME")
S3_REGION = os.environ.get("AWS_DEFAULT_REGION")
S3_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID")
S3_SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")

USE_S3 = all([S3_BUCKET, S3_REGION, S3_ACCESS_KEY, S3_SECRET_KEY, boto3 is not None])

# Flask app
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_MB * 1024 * 1024
CORS(app, origins=[ALLOWED_ORIGIN])  # ✅ Enable CORS for your site only


# =========================
# Helpers
# =========================
def allowed_fileext(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTS


def s3_client():
    return boto3.client(
        "s3",
        region_name=S3_REGION,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )


def s3_put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream"):
    cli = s3_client()
    cli.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
        ACL="private",
    )


def s3_put_fileobj(key: str, fileobj, content_type: str):
    cli = s3_client()
    cli.upload_fileobj(
        Fileobj=fileobj,
        Bucket=S3_BUCKET,
        Key=key,
        ExtraArgs={"ContentType": content_type, "ACL": "private"},
    )


def s3_presigned_get(key: str, expires_seconds: int = 3600) -> Optional[str]:
    try:
        cli = s3_client()
        return cli.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=expires_seconds,
        )
    except Exception:
        return None


# =========================
# Routes
# =========================
@app.route("/api/health", methods=["GET"])
def health():
    """Quick health check for Render"""
    mode = "s3" if USE_S3 else "local"
    return jsonify(
        {
            "status": "ok",
            "storage_mode": mode,
            "bucket": S3_BUCKET if USE_S3 else None,
            "time": datetime.datetime.utcnow().isoformat() + "Z",
        }
    )


@app.route("/api/upload", methods=["OPTIONS"])
def preflight():
    """CORS preflight"""
    resp = make_response(("", 204))
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp


@app.route("/api/upload", methods=["POST"])
def upload():
    """Handle file upload (PDF only) and send to S3"""
    file = request.files.get("file")
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip()
    company = (request.form.get("company") or "").strip()

    # Basic validation
    if not file:
        return jsonify({"status": "error", "error": "file required"}), 400
    if not allowed_fileext(file.filename):
        return jsonify({"status": "error", "error": "Only PDF files allowed"}), 400

    item_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1].lower()
    stored_filename = f"input{ext}"

    if USE_S3:
        base_key = f"uploads/{item_id}/"
        pdf_key = base_key + stored_filename
        meta_key = base_key + "meta.txt"

        try:
            # Upload the PDF file
            s3_put_fileobj(pdf_key, file, "application/pdf")

            # Create meta file (optional info)
            meta_data = (
                f"id={item_id}\n"
                f"name={name}\n"
                f"email={email}\n"
                f"company={company}\n"
                f"original_filename={file.filename}\n"
                f"uploaded_at_utc={datetime.datetime.utcnow().isoformat()}Z\n"
            ).encode("utf-8")
            s3_put_bytes(meta_key, meta_data, "text/plain; charset=utf-8")

            presigned_url = s3_presigned_get(pdf_key, 3600)
            return (
                jsonify(
                    {
                        "status": "ok",
                        "id": item_id,
                        "storage": "s3",
                        "bucket": S3_BUCKET,
                        "pdf_key": pdf_key,
                        "meta_key": meta_key,
                        "pdf_url": presigned_url,
                    }
                ),
                201,
            )

        except (BotoCoreError, ClientError) as e:
            return (
                jsonify(
                    {"status": "error", "error": "S3 upload failed", "detail": str(e)}
                ),
                500,
            )

    # Local fallback (if S3 not configured)
    folder = os.path.join(UPLOAD_DIR, item_id)
    os.makedirs(folder, exist_ok=True)
    pdf_path = os.path.join(folder, stored_filename)
    file.save(pdf_path)

    meta_path = os.path.join(folder, "meta.txt")
    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(
            f"id={item_id}\nname={name}\nemail={email}\ncompany={company}\n"
            f"original_filename={file.filename}\n"
            f"uploaded_at_utc={datetime.datetime.utcnow().isoformat()}Z\n"
        )

    return (
        jsonify(
            {
                "status": "ok",
                "id": item_id,
                "storage": "local",
                "folder": folder,
                "pdf_path": pdf_path,
                "meta_path": meta_path,
            }
        ),
        201,
    )


# =========================
# Entrypoint (for local runs)
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    print(f"🚀 Starting server on 0.0.0.0:{port} | storage={'s3' if USE_S3 else 'local'}")
    app.run(host="0.0.0.0", port=port)