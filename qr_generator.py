"""
UPI QR Code Generator — creates branded QR images for Telegram.
"""

import io
import qrcode
from PIL import Image, ImageDraw, ImageFont
from urllib.parse import quote


def make_qr(amount: float, order_id: str, cfg) -> io.BytesIO:
    """Generate a branded UPI QR code PNG.

    Args:
        amount:   Exact payment amount (session amount with micro-delta).
        order_id: Unique order identifier to embed in the UPI note field.
        cfg:      PaymentConfig — provides upi_id and merchant_name.

    Returns:
        BytesIO buffer containing the PNG image.
    """

    # Build UPI deep link
    upi_uri = (
        f"upi://pay?"
        f"pa={quote(cfg.upi_id)}"
        f"&am={amount:.2f}"
        f"&pn={quote(cfg.merchant_name)}"
        f"&tn={quote(order_id)}"
    )

    # Generate QR
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=8, border=2)
    qr.add_data(upi_uri)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="#1a1a2e", back_color="#ffffff").convert("RGB")
    qr_w, qr_h = qr_img.size

    # Create card with padding
    pad = 32
    top_bar = 56
    bottom_bar = 48
    card_w = qr_w + pad * 2
    card_h = top_bar + qr_h + pad + bottom_bar

    card = Image.new("RGB", (card_w, card_h), "#ffffff")
    draw = ImageDraw.Draw(card)

    # Top bar (dark)
    draw.rectangle([(0, 0), (card_w, top_bar)], fill="#1a1a2e")

    # Try to use a nice font, fallback to default
    try:
        font_title = ImageFont.truetype("arial.ttf", 18)
        font_amount = ImageFont.truetype("arialbd.ttf", 20)
        font_small = ImageFont.truetype("arial.ttf", 13)
    except OSError:
        font_title = ImageFont.load_default()
        font_amount = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Brand name
    draw.text((pad, 16), cfg.merchant_name, fill="#ffffff", font=font_title)

    # Amount on right
    amt_text = f"₹{amount:.2f}"
    bbox = draw.textbbox((0, 0), amt_text, font=font_amount)
    amt_w = bbox[2] - bbox[0]
    draw.text((card_w - pad - amt_w, 14), amt_text, fill="#ffffff", font=font_amount)

    # QR code
    card.paste(qr_img, (pad, top_bar + pad // 2))

    # Bottom text
    draw.text((pad, card_h - bottom_bar + 12), f"Order: {order_id}", fill="#999999", font=font_small)
    scan_text = "Scan with any UPI app"
    bbox2 = draw.textbbox((0, 0), scan_text, font=font_small)
    scan_w = bbox2[2] - bbox2[0]
    draw.text((card_w - pad - scan_w, card_h - bottom_bar + 12), scan_text, fill="#999999", font=font_small)

    # Save
    buf = io.BytesIO()
    card.save(buf, format="PNG", quality=95)
    buf.seek(0)
    return buf
