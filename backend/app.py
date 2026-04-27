import os
import json
import base64
import fitz  # PyMuPDF
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

SYSTEM_PROMPT = """You are a CRE (commercial real estate) data extraction specialist. You will receive a comp sheet PDF.

Extract every deal/property row and return ONLY a valid JSON array. Each element = one deal object.

SPLITTING RULES:
1. Property Name and Property Address are often stacked in one column — split into separate "Property Name" and "Property Address" fields.
2. Market and Submarket are often stacked (submarket in parentheses) — split into separate fields, remove parentheses.
3. Sale Price and Price PSF are often stacked (PSF in parentheses) — split into separate fields, remove parentheses.

RULES:
- Read visually — may be a scanned image PDF
- Use human-readable key names
- Extract ALL fields visible on the page
- Join bullet point comments with " | "
- Use null for missing fields
- Return ONLY the raw JSON array. No markdown. No ```json. No explanation. Start your response with [ and end with ]"""

USER_PROMPT = "Extract all deal rows from this CRE comp sheet as a JSON array. One object per deal. Return only raw JSON starting with [ and ending with ]."


def get_client():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def parse_json_response(text):
    """Robustly parse JSON from Claude response."""
    # Strip any markdown fences
    clean = text.strip()
    clean = clean.replace("```json", "").replace("```", "").strip()

    # Find the first [ and last ]
    start = clean.find("[")
    end = clean.rfind("]")

    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(clean[start:end+1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON parse error: {e}. Raw response (first 500 chars): {clean[:500]}")

    # Try single object fallback
    start = clean.find("{")
    end = clean.rfind("}")
    if start != -1 and end != -1:
        try:
            obj = json.loads(clean[start:end+1])
            if isinstance(obj, dict):
                return [obj]
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON found. Claude returned (first 500 chars): {clean[:500]}")


def normalize_row(raw):
    out = {}
    for k, v in raw.items():
        key = k.strip()
        if v is None or str(v).strip().lower() in ("null", "n/a", "none", "—", "-", ""):
            out[key] = ""
        else:
            out[key] = str(v).strip()
    return out


def extract_comps_from_pdf(pdf_bytes):
    client = get_client()
    b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8096,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": b64
                    }
                },
                {
                    "type": "text",
                    "text": USER_PROMPT
                }
            ]
        }]
    )

    response_text = "".join(b.text for b in message.content if hasattr(b, "text"))
    rows = parse_json_response(response_text)
    return [normalize_row(r) for r in rows]


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

    special_widths = {
        "Property Name": 28, "Property Address": 32, "Address": 30,
        "Market": 16, "Submarket": 20, "Sale Date": 10, "SF": 12,
        "Sale Price": 16, "Price": 16, "Price PSF": 10, "PSF": 10,
        "Cap Rate": 11, "Seller": 24, "Buyer": 24, "Year Built": 10,
        "Clear Height": 11, "WALT": 10, "Comments": 55,
    }
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 22
    for ci, col in enumerate(all_columns, 3):
        w = special_widths.get(col, max(len(col) + 2, 12))
        ws.column_dimensions[get_column_letter(ci)].width = min(w, 60)

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
    if not files:
        return jsonify({"error": "No files received"}), 400

    all_rows = []
    all_columns_ordered = []
    seen_columns = set()
    results = []

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            results.append({"filename": file.filename, "status": "skipped", "reason": "Not a PDF"})
            continue
        try:
            pdf_bytes = file.read()
            rows = extract_comps_from_pdf(pdf_bytes)
            for row in rows:
                row["__source"] = file.filename
                for key in row.keys():
                    if key != "__source" and key not in seen_columns:
                        all_columns_ordered.append(key)
                        seen_columns.add(key)
            all_rows.extend(rows)
            results.append({
                "filename": file.filename,
                "status": "success",
                "rows_extracted": len(rows)
            })
        except Exception as e:
            results.append({
                "filename": file.filename,
                "status": "error",
                "reason": str(e)
            })

    return jsonify({
        "results": results,
        "total_rows": len(all_rows),
        "columns": all_columns_ordered,
        "rows": all_rows
    })


@app.route("/export", methods=["POST"])
def export():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    rows = data.get("rows", [])
    columns = data.get("columns", [])
    if not rows:
        return jsonify({"error": "No rows to export"}), 400
    try:
        wb = build_excel(rows, columns)
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        wb.save(tmp.name)
        tmp.close()
        filename = f"CRE_Comps_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
        return send_file(tmp.name, as_attachment=True, download_name=filename,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
