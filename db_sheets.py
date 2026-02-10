import os
import json
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_SHEET = None


def _get_sheet():
    global _SHEET
    if _SHEET is not None:
        return _SHEET

    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    creds_json = os.getenv("GOOGLE_CREDS_JSON")

    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID não definido.")
    if not creds_json:
        raise RuntimeError("GOOGLE_CREDS_JSON não definido.")

    info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(sheet_id)
    _SHEET = sh.sheet1
    return _SHEET


def init_db():
    """
    Garante cabeçalho na planilha.
    """
    ws = _get_sheet()
    headers = ws.row_values(1)
    if not headers or headers[:7] != ["user_id", "qid", "acertou", "marcada", "tema", "subtema", "timestamp"]:
        ws.clear()
        ws.append_row(["user_id", "qid", "acertou", "marcada", "tema", "subtema", "timestamp"])


def record_answer(user_id: str, qid: str, acertou: bool, marcada: str, tema: str, subtema: str):
    ws = _get_sheet()
    ts = datetime.now(timezone.utc).isoformat()
    ws.append_row([
        str(user_id),
        str(qid),
        "1" if acertou else "0",
        str(marcada),
        str(tema or ""),
        str(subtema or ""),
        ts
    ])


def _all_rows():
    ws = _get_sheet()
    # retorna lista de dicts a partir do cabeçalho
    return ws.get_all_records()  # usa linha 1 como header


def get_overall_progress(user_id: str):
    rows = _all_rows()
    acertos = 0
    erros = 0
    uid = str(user_id)

    for r in rows:
        if str(r.get("user_id")) != uid:
            continue
        if str(r.get("acertou")) == "1":
            acertos += 1
        else:
            erros += 1

    total = acertos + erros
    pct = (acertos / total * 100.0) if total else 0.0
    return {"acertos": acertos, "erros": erros, "pct": pct}


def get_topic_breakdown(user_id: str, limit: int = 20):
    rows = _all_rows()
    uid = str(user_id)

    agg = {}  # (tema, subtema) -> {acertos, erros}
    for r in rows:
        if str(r.get("user_id")) != uid:
            continue
        tema = str(r.get("tema") or "")
        sub = str(r.get("subtema") or "")
        key = (tema, sub)
        if key not in agg:
            agg[key] = {"tema": tema, "subtema": sub, "acertos": 0, "erros": 0}
        if str(r.get("acertou")) == "1":
            agg[key]["acertos"] += 1
        else:
            agg[key]["erros"] += 1

    out = []
    for v in agg.values():
        total = v["acertos"] + v["erros"]
        pct = (v["acertos"] / total * 100.0) if total else 0.0
        out.append({
            "tema": v["tema"],
            "subtema": v["subtema"],
            "acertos": v["acertos"],
            "erros": v["erros"],
            "total": total,
            "pct": pct
        })

    # ordena por maior total respondido
    out.sort(key=lambda x: x["total"], reverse=True)
    return out[:limit]
