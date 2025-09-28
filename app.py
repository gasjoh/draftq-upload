import os, uuid, datetime
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "30"))
ALLOWED_EXTS = {".pdf"}
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "./uploads")  # local now, /data/uploads on Render

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_MB * 1024 * 1024
os.makedirs(UPLOAD_DIR, exist_ok=True)

def cors(r):
    r.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    r.headers["Vary"] = "Origin"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    r.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS, GET"
    return r

@app.after_request
def add_cors(r): 
    return cors(r)

@app.route("/api/upload", methods=["OPTIONS"])
def opts(): 
    return cors(("", 204)[1])

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status":"ok","time": datetime.datetime.utcnow().isoformat()+"Z"})

@app.route("/api/upload", methods=["POST"])
def upload():
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip()
    company = (request.form.get("company") or "").strip()
    file = request.files.get("file")
    if not name or not email or not file:
        return jsonify({"status":"error","error":"name, email, file required"}), 400

    original = secure_filename(file.filename or "")
    ext = os.path.splitext(original)[1].lower()
    if ext not in ALLOWED_EXTS:
        return jsonify({"status":"error","error":"Only PDF files allowed"}), 400

    item_id = str(uuid.uuid4())
    folder = os.path.join(UPLOAD_DIR, item_id)
    os.makedirs(folder, exist_ok=True)

    file.save(os.path.join(folder, f"input{ext}"))
    with open(os.path.join(folder, "meta.txt"), "w", encoding="utf-8") as f:
        f.write(
            f"id={item_id}\nname={name}\nemail={email}\ncompany={company}\n"
            f"original_filename={original}\nuploaded_at_utc={datetime.datetime.utcnow().isoformat()}Z\n"
        )
    return jsonify({"status":"ok","id":item_id})

if __name__ == "__main__":
    print("🚀 Starting server on port", os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))