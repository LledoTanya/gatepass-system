"""qr_manager.py – Permanent static QR per installation."""
import base64, hmac, io, secrets
from pathlib import Path
import qrcode

BASE_DIR = Path(__file__).resolve().parent.parent
_TOKEN_FILE = BASE_DIR / ".qr_token"


def _load_token() -> str:
    if _TOKEN_FILE.exists():
        return _TOKEN_FILE.read_text().strip()
    token = secrets.token_hex(8)   # 16-char hex, saved permanently
    _TOKEN_FILE.write_text(token)
    return token


def current_token() -> str:
    return _load_token()


def is_valid(token: str) -> bool:
    return hmac.compare_digest(token, current_token())


def consume(token: str, employee_id: str) -> bool:
    """Static QR: always allow (staff can use the same QR repeatedly)."""
    return True


def build_scan_url(base_url: str) -> str:
    return f"{base_url.rstrip(chr(47))}/scan-confirm?token={current_token()}"


def qr_data_url(base_url: str) -> str:
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                        box_size=10, border=2)
    qr.add_data(build_scan_url(base_url))
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
