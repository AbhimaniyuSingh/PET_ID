"""PDF generation: collar tag, lost-pet poster, health card."""
import io
import os
from datetime import date

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from helpers import age_text, nice_date


def _s(text):
    """Standard PDF fonts only support Latin letters; show '?' for anything else."""
    return (text or "").encode("latin-1", "replace").decode("latin-1")


def _qr(url):
    buf = io.BytesIO()
    qrcode.make(url, box_size=10, border=2).save(buf, "PNG")
    buf.seek(0)
    return ImageReader(buf)


def _wrap(text, font, size, max_width):
    lines, cur = [], ""
    for word in _s(text).split():
        trial = (cur + " " + word).strip()
        if stringWidth(trial, font, size) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _finish(c):
    c.showPage()
    c.save()


# ---------- collar tag ----------
def build_tag_pdf(pet, url):
    qr = _qr(url)
    short_url = _s(url.replace("https://", "").replace("http://", ""))

    def card(c, x, y, w, h):
        qr_size = w - 10 * mm
        c.setLineWidth(0.8)
        c.setDash(3, 2)
        c.roundRect(x, y, w, h, 4 * mm)
        c.setDash()
        qr_y = y + h - 5 * mm - qr_size
        c.drawImage(qr, x + 5 * mm, qr_y, qr_size, qr_size)
        size = 14 if w > 50 * mm else 10
        c.setFont("Helvetica-Bold", size)
        c.drawCentredString(x + w / 2, qr_y - 6 * mm, _s(pet["name"])[:18])
        c.setFont("Helvetica", size - 3)
        c.drawCentredString(x + w / 2, qr_y - 11 * mm, "Scan if found")
        c.setFont("Helvetica", 6)
        c.drawCentredString(x + w / 2, y + 4 * mm, short_url)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _w, page_h = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, page_h - 20 * mm, f"PetID tag for {_s(pet['name'])}")
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, page_h - 27 * mm,
                 "Print, cut along the dotted line, laminate, and attach to the collar.")
    card(c, 20 * mm, page_h - 35 * mm - 80 * mm, 60 * mm, 80 * mm)    # large tag
    card(c, 100 * mm, page_h - 35 * mm - 58 * mm, 40 * mm, 58 * mm)   # small tag
    _finish(c)
    buf.seek(0)
    return buf


# ---------- lost pet poster ----------
def build_poster_pdf(pet, url, upload_dir, phone=None):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4

    c.setFillColorRGB(0.78, 0.16, 0.16)
    c.rect(0, H - 45 * mm, W, 45 * mm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 60)
    species = (pet["species"] or "").upper()
    title = f"LOST {species}" if species in ("DOG", "CAT", "RABBIT", "BIRD") else "LOST PET"
    c.drawCentredString(W / 2, H - 31 * mm, title)
    c.setFillColorRGB(0, 0, 0)

    y = H - 52 * mm
    if pet["photo"]:
        try:
            img = ImageReader(os.path.join(upload_dir, os.path.basename(pet["photo"])))
            iw, ih = img.getSize()
            box_w, box_h = 130 * mm, 95 * mm
            scale = min(box_w / iw, box_h / ih)
            dw, dh = iw * scale, ih * scale
            c.drawImage(img, (W - dw) / 2, y - dh, dw, dh)
            y -= dh + 8 * mm
        except Exception:
            pass

    c.setFont("Helvetica-Bold", 34)
    c.drawCentredString(W / 2, y - 8 * mm, _s(pet["name"]))
    y -= 18 * mm
    c.setFont("Helvetica", 14)
    details = [f"{pet['species']}" + (f" ({pet['breed']})" if pet["breed"] else "")]
    if pet["color"]:
        details.append(f"Color: {pet['color']}")
    age = age_text(pet["birth_month"], pet["birth_year"])
    if age != "Unknown":
        details.append(f"Age: {age}")
    if pet["temperament"]:
        details.append(f"Nature: {pet['temperament']}")
    for line in details:
        for wrapped in _wrap(line, "Helvetica", 14, W - 40 * mm):
            c.drawCentredString(W / 2, y, wrapped)
            y -= 6.5 * mm
    y -= 2 * mm
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(W / 2, y, _s(f"Home area: {pet['area']}, {pet['city']}"))
    y -= 11 * mm

    if phone:
        c.setFont("Helvetica-Bold", 26)
        c.drawCentredString(W / 2, y, _s(f"Call: {phone}"))
    else:
        c.setFont("Helvetica-Bold", 18)
        c.drawCentredString(W / 2, y, "Scan the QR code to contact the owner")
    y -= 8 * mm

    qr_size = 52 * mm
    c.drawImage(_qr(url), (W - qr_size) / 2, 14 * mm + 5 * mm, qr_size, qr_size)
    c.setFont("Helvetica", 8)
    c.drawCentredString(W / 2, 14 * mm, _s(url.replace("https://", "").replace("http://", "")))
    _finish(c)
    buf.seek(0)
    return buf


# ---------- health card ----------
def build_health_card_pdf(pet, vaccs, owner):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    left = 18 * mm
    y = H - 22 * mm

    c.setFont("Helvetica-Bold", 20)
    c.drawString(left, y, "PetID Health Card")
    c.setFont("Helvetica", 9)
    c.drawRightString(W - left, y, f"Generated {date.today().strftime('%d %b %Y')}")
    y -= 4 * mm
    c.setLineWidth(1)
    c.line(left, y, W - left, y)
    y -= 10 * mm

    def row(label, value):
        nonlocal y
        if not value:
            return
        c.setFont("Helvetica-Bold", 10)
        c.drawString(left, y, _s(label))
        c.setFont("Helvetica", 10)
        for i, line in enumerate(_wrap(str(value), "Helvetica", 10, W - 2 * left - 45 * mm)):
            c.drawString(left + 45 * mm, y, line)
            y -= 5 * mm
        y -= 1 * mm

    row("Name", pet["name"])
    row("Species / breed", pet["species"] + (f" / {pet['breed']}" if pet["breed"] else ""))
    row("Gender", pet["gender"])
    row("Color", pet["color"])
    age = age_text(pet["birth_month"], pet["birth_year"])
    row("Age", None if age == "Unknown" else age)
    row("Microchip no.", pet["microchip"])
    row("Sterilized", pet["sterilized"] + (f" ({nice_date(pet['sterilized_date'])})"
                                          if pet["sterilized_date"] else ""))
    row("Owner", f"{owner['name']}  |  {owner['phone']}")
    row("Regular vet", " | ".join(x for x in (pet["vet_name"], pet["vet_phone"]) if x))
    row("Allergies / medical", pet["medical_notes"])
    row("Food notes", pet["food_notes"])

    y -= 4 * mm
    c.setFont("Helvetica-Bold", 13)
    c.drawString(left, y, "Vaccination record")
    y -= 7 * mm
    cols = [left, left + 62 * mm, left + 95 * mm, left + 128 * mm]
    c.setFont("Helvetica-Bold", 9)
    for x, head in zip(cols, ("Vaccine", "Given on", "Next due", "Verified by")):
        c.drawString(x, y, head)
    y -= 2 * mm
    c.line(left, y, W - left, y)
    y -= 5 * mm
    c.setFont("Helvetica", 9)
    if not vaccs:
        c.drawString(left, y, "No vaccinations recorded.")
    for v in vaccs:
        if y < 25 * mm:
            c.showPage()
            y = H - 25 * mm
            c.setFont("Helvetica", 9)
        c.drawString(cols[0], y, _s(v["name"])[:34])
        c.drawString(cols[1], y, v["given"])
        c.drawString(cols[2], y, v["next"])
        c.drawString(cols[3], y, _s(v["verified_by"] or "-")[:30])
        y -= 6 * mm

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(left, 14 * mm, "Records entered by the owner. Entries marked with a clinic name "
                                "were verified by that veterinarian on PetID.")
    _finish(c)
    buf.seek(0)
    return buf
