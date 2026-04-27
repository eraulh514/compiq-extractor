import os
import json
import base64
import fitz
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import tempfile
from datetime import datetime
import anthropic

app = Flask(__name__)
CORS(app)

SYSTEM_PROMPT = """You are a CRE data extraction specialist. Extract every deal row from this CRE comp sheet and return ONLY a valid JSON array. Each element = one deal.

SPLITTING RULES:
- Property Name and Property Address are often stacked in one column — split into separate "Property Name" and "Property Address" fields
- Market and Submarket are often stacked (submarket in parentheses) — split into separate fields, remove parentheses
- Sale Price and Price PSF are often stacked (PSF in parentheses) — split into separate fields, remove parentheses
- Remove ALL parentheses from all values

RULES:
- Extract EVERY field: seller, buyer, sale date, SF, cap rate, clear height, WALT, dock doors, occupancy, tenancy, year built, configuration, comments, etc.
- Join bullet point comments with " | "
- Use null for missing fields
- Return ONLY raw JSON array starting with [ and ending with ]
- No markdown, no explanation, no ```json fences"""

USER_PROMPT = "Extract all deal rows from this CRE comp sheet. Return only a raw JSON array."


def get_client():
    return anthropic.Anthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        timeout=25.0
    )


def parse_json(text):
    clean = text.strip().replace("```json", "").replace("```", "").strip()
    s, e = clean.find("["), clean.rfind("]")
    if s != -1 and e != -1 and e > s:
        try:
            result = json.loads(clean[s:e+1])
            if isinstance(result, list):
                return result
        except Exception:
            pass
    s, e = clean.find("{"), clean.rfind("}")
    if s != -1 and e != -1:
        try:
            obj = json.loads(clean[s:e+1])
            if isinstance(obj, dict):
                return [obj]
        except Exception:
            pass
    return []


def normalize_row(raw):
    out = {}
    for k, v in raw.items():
        key = k.strip()
        val = "" if (v is None or str(v).strip().lower() in ("null", "n/a", "none", "—", "-", "")) else str(v).strip()
        out[key] = val
    return out


def extract_comps_from_pdf(pdf_bytes):
    client = get_client()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # Extract text from all pages
    all_text = []
    for i, page in enumerate(doc):
        text = page.get_text().strip()
        if text:
            all_text.append(f"=== PAGE {i+1} ===\n{text}")
    full_text = "\n\n".join(all_text)

    # Strategy 1: text-based PDF — send text directly (fastest, ~5s)
    if len(full_text) > 200:
        try:
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": USER_PROMPT + "\n\n" + full_text}]
            )
            text_resp = "".join(b.text for b in message.content if hasattr(b, "text"))
            rows = parse_json(text_resp)
            if rows:
                return [normalize_row(r) for r in rows]
        except Exception:
            pass  # Fall through to vision

    # Strategy 2: image-based PDF — render each page at low res and send as images
    # Process in batches of 3 pages to stay under timeout
    all_rows = []
    batch_size = 3
    pages = list(range(doc.page_count))

    for batch_start in range(0, len(pages), batch_size):
        batch = pages[batch_start:batch_start + batch_size]
        content = []

        for i in batch:
            page = doc[i]
            # Low resolution (1.5x) to keep payload small and fast
            mat = fitz.Matrix(1.5, 1.5)
            pix = page.get_pixmap(matrix=mat)
            b64 = base64.standard_b64encode(pix.tobytes("png")).decode("utf-8")
            content.append({"type": "text", "text": f"=== PAGE {i+1} ==="})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64}
            })

        content.append({"type": "text", "text": USER_PROMPT})

        try:
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}]
            )
            text_resp = "".join(b.text for b in message.content if hasattr(b, "text"))
            rows = parse_json(text_resp)
            all_rows.extend([normalize_row(r) for r in rows])
        except Exception:
            continue  # Skip failed batch, keep going

    return all_rows


def build_excel(all_rows, all_columns):
    wb = Workbook()
    ws = wb.active
    ws.title = "Comps"
    hf = Font(name="Arial", bold=True, color="FFFFFF", size=10)
    hfill = PatternFill("solid", start_color="1F3864")
    ha = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cf = Font(name="Arial", size=9)
    ca = Alignment(vertical="top", wrap_text=False)
    caw = Alignment(vertical="top", wrap_text=True)
    alt = PatternFill("solid", start_color="EEF2F7")
    wht = PatternFill("solid", start_color="FFFFFF")
    thin = Side(style="thin", color="CCCCCC")
    bdr = Border(left=thin, right=thin, top=thin, bottom=thin)
    header = ["#", "Source File"] + all_columns
    for ci, cn in enumerate(header, 1):
        c = ws.cell(row=1, column=ci, value=cn)
        c.font = hf; c.fill = hfill; c.alignment = ha; c.border = bdr
    ws.row_dimensions[1].height = 28
    for ri, row in enumerate(all_rows, 2):
        fill = alt if ri % 2 == 0 else wht
        ws.cell(row=ri, column=1, value=ri-1).font = cf
        ws.cell(row=ri, column=1).fill = fill
        ws.cell(row=ri, column=1).border = bdr
        ws.cell(row=ri, column=1).alignment = ca
        ws.cell(row=ri, column=2, value=row.get("__source", "")).font = cf
        ws.cell(row=ri, column=2).fill = fill
        ws.cell(row=ri, column=2).border = bdr
        ws.cell(row=ri, column=2).alignment = ca
        for ci, col in enumerate(all_columns, 3):
            val = row.get(col, "")
            c = ws.cell(row=ri, column=ci, value=val)
            c.font = cf; c.fill = fill; c.border = bdr
            c.alignment = caw if "comment" in col.lower() else ca
        ws.row_dimensions[ri].height = 42
    widths = {
        "Property Name": 28, "Property Address": 32, "Address": 30,
        "Market": 16, "Submarket": 20, "Sale Date": 10, "SF": 12,
        "Sale Price": 16, "Price": 16, "Price PSF": 10, "PSF": 10,
        "Cap Rate": 11, "Seller": 24, "Buyer": 24, "Year Built": 10,
        "Clear Height": 11, "WALT": 10, "Comments": 55
    }
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 22
    for ci, col in enumerate(all_columns, 3):
        ws.column_dimensions[get_column_letter(ci)].width = min(widths.get(col, max(len(col)+2, 12)), 60)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(header))}1"
    return wb


@app.route('/')
def serve_index():
    return send_from_directory('../frontend', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('../frontend', path)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "CompIQ Extractor is running"})


@app.route("/extract", methods=["POST"])
def extract():
    if "files[]" not in request.files:
        return jsonify({"error": "No files uploaded"}), 400
    files = request.files.getlist("files[]")
    all_rows, all_columns_ordered, seen_columns, results = [], [], set(), []
    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            results.append({"filename": file.filename, "status": "skipped", "reason": "Not a PDF"})
            continue
        try:
            rows = extract_comps_from_pdf(file.read())
            for row in rows:
                row["__source"] = file.filename
                for key in row.keys():
                    if key != "__source" and key not in seen_columns:
                        all_columns_ordered.append(key)
                        seen_columns.add(key)
            all_rows.extend(rows)
            results.append({"filename": file.filename, "status": "success", "rows_extracted": len(rows)})
        except Exception as e:
            results.append({"filename": file.filename, "status": "error", "reason": str(e)})
    return jsonify({"results": results, "total_rows": len(all_rows), "columns": all_columns_ordered, "rows": all_rows})


@app.route("/export", methods=["POST"])
def export():
    data = request.get_json()
    if not data or not data.get("rows"):
        return jsonify({"error": "No data"}), 400
    try:
        wb = build_excel(data["rows"], data.get("columns", []))
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        wb.save(tmp.name); tmp.close()
        return send_file(
            tmp.name, as_attachment=True,
            download_name=f"CRE_Comps_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
