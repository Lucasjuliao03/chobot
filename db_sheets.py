import os
import json
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# caches
_SH = None
_WS_STATS = None
_WS_SENT = None


def _get_sheet():
    """
    MANTIDO por compatibilidade: retorna a worksheet principal (sheet1).
    """
    return _get_ws_stats()


def _get_sh():
    global _SH
    if _SH is not None:
        return _SH

    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    creds_json = os.getenv("GOOGLE_CREDS_JSON")

    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID não definido.")
    if not creds_json:
        raise RuntimeError("GOOGLE_CREDS_JSON não definido.")

    info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    gc = gspread.authorize(creds)

    _SH = gc.open_by_key(sheet_id)
    return _SH


def _get_ws_stats():
    global _WS_STATS
    if _WS_STATS is not None:
        return _WS_STATS

    sh = _get_sh()
    _WS_STATS = sh.sheet1
    return _WS_STATS


def _get_ws_sent():
    """
    Worksheet para persistir embaralhamento por envio (à prova de restart).
    """
    global _WS_SENT
    if _WS_SENT is not None:
        return _WS_SENT

    sh = _get_sh()
    title = "sent"

    try:
        ws = sh.worksheet(title)
    except Exception:
        ws = sh.add_worksheet(title=title, rows=2000, cols=10)

    _WS_SENT = ws
    return _WS_SENT


def init_db():
    """
    Garante cabeçalho na planilha (stats) E na planilha (sent).
    """
    # === stats (sheet1) ===
    ws = _get_ws_stats()
    headers = ws.row_values(1)

    expected = ["user_id", "qid", "acertou", "marcada", "tema", "subtema", "timestamp"]

    if not headers or headers[:7] != expected:
        ws.clear()
        ws.append_row(expected)

    # === sent (worksheet separada) ===
    ws2 = _get_ws_sent()
    headers2 = ws2.row_values(1)
    expected2 = ["user_id", "qid", "message_id", "correta_exibida", "perm", "timestamp"]

    if not headers2 or headers2[:6] != expected2:
        ws2.clear()
        ws2.append_row(expected2)


def record_answer(user_id: str, qid: str, acertou: bool, marcada: str, tema: str, subtema: str):
    ws = _get_ws_stats()
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
    ws = _get_ws_stats()
    return ws.get_all_records()


def get_overall_progress(user_id: str):
    rows = _all_rows()
    uid = str(user_id)

    acertos = 0
    erros = 0

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

    out.sort(key=lambda x: x["total"], reverse=True)
    return out[:limit]


# ==========================================================
# ✅ status por QUESTÃO (para botões e priorização)
# ==========================================================
def get_question_status_map(user_id: str):
    rows = _all_rows()
    uid = str(user_id)

    status = {}  # qid -> bool (True=acertou, False=errou)

    for r in rows:
        if str(r.get("user_id")) != uid:
            continue

        qid = str(r.get("qid") or "").strip()
        if not qid:
            continue

        acertou = (str(r.get("acertou")) == "1")

        if acertou:
            status[qid] = True
        else:
            if qid not in status:
                status[qid] = False

    return status


# ==========================================================
# ✅ reset por usuário (para /zerar)
# ==========================================================
def reset_user_stats(user_id: str):
    ws = _get_ws_stats()
    uid = str(user_id)

    values = ws.get_all_values()
    if not values:
        init_db()
        return

    header = values[0]
    data = values[1:]

    kept = [row for row in data if len(row) > 0 and str(row[0]) != uid]

    ws.clear()
    ws.append_row(header)

    if kept:
        ws.append_rows(kept, value_input_option="RAW")


# ==========================================================
# 🔥 persistência de embaralhamento por envio (restart-proof)
# ==========================================================
def record_sent_question(user_id: str, qid: str, message_id: int, correta_exibida: str, perm: str):
    ws = _get_ws_sent()
    ts = datetime.now(timezone.utc).isoformat()
    ws.append_row([
        str(user_id),
        str(qid),
        str(message_id),
        str(correta_exibida or ""),
        str(perm or ""),
        ts
    ])


def _sent_all_records():
    ws = _get_ws_sent()
    return ws.get_all_records()


def get_sent_correct(user_id: str, qid: str, message_id: int) -> str:
    uid = str(user_id)
    q = str(qid).strip()
    mid = str(message_id)

    rows = _sent_all_records()
    for r in reversed(rows):
        if str(r.get("user_id")) == uid and str(r.get("qid")).strip() == q and str(r.get("message_id")) == mid:
            return str(r.get("correta_exibida") or "").strip().upper()

    return ""


def get_last_perm_for_user_question(user_id: str, qid: str) -> str:
    uid = str(user_id)
    q = str(qid).strip()

    rows = _sent_all_records()
    for r in reversed(rows):
        if str(r.get("user_id")) == uid and str(r.get("qid")).strip() == q:
            return str(r.get("perm") or "").strip()

    return ""


# ==========================================================
# 🔥 NOVO: agregações para /score (todos usuários)
# ==========================================================
def get_users_overall_scores(limit: int | None = None):
    """
    Retorna lista:
      [{"user_id": "...", "respondidas": N, "acertos": A, "erros": E, "pct": P}, ...]
    Ordenado por respondidas desc.
    """
    rows = _all_rows()

    agg = {}  # user_id -> {acertos, erros}
    for r in rows:
        uid = str(r.get("user_id") or "").strip()
        if not uid:
            continue

        if uid not in agg:
            agg[uid] = {"user_id": uid, "acertos": 0, "erros": 0}

        if str(r.get("acertou")) == "1":
            agg[uid]["acertos"] += 1
        else:
            agg[uid]["erros"] += 1

    out = []
    for v in agg.values():
        total = v["acertos"] + v["erros"]
        pct = (v["acertos"] / total * 100.0) if total else 0.0
        out.append({
            "user_id": v["user_id"],
            "respondidas": total,
            "acertos": v["acertos"],
            "erros": v["erros"],
            "pct": pct
        })

    out.sort(key=lambda x: x["respondidas"], reverse=True)

    if limit is not None:
        return out[:max(0, int(limit))]
    return out


def get_user_topic_breakdown_full(user_id: str):
    """
    Retorna duas visões:
      - por tema (agregado)
      - por tema/subtema (detalhado)
    """
    rows = _all_rows()
    uid = str(user_id).strip()

    # por tema
    tema_agg = {}  # tema -> {acertos, erros}
    # por tema/subtema
    ts_agg = {}    # (tema, subtema) -> {acertos, erros}

    for r in rows:
        if str(r.get("user_id") or "").strip() != uid:
            continue

        tema = str(r.get("tema") or "").strip()
        sub = str(r.get("subtema") or "").strip()

        if tema not in tema_agg:
            tema_agg[tema] = {"tema": tema, "acertos": 0, "erros": 0}
        key = (tema, sub)
        if key not in ts_agg:
            ts_agg[key] = {"tema": tema, "subtema": sub, "acertos": 0, "erros": 0}

        if str(r.get("acertou")) == "1":
            tema_agg[tema]["acertos"] += 1
            ts_agg[key]["acertos"] += 1
        else:
            tema_agg[tema]["erros"] += 1
            ts_agg[key]["erros"] += 1

    temas = []
    for v in tema_agg.values():
        total = v["acertos"] + v["erros"]
        pct = (v["acertos"] / total * 100.0) if total else 0.0
        temas.append({
            "tema": v["tema"],
            "acertos": v["acertos"],
            "erros": v["erros"],
            "total": total,
            "pct": pct
        })
    temas.sort(key=lambda x: x["total"], reverse=True)

    tema_sub = []
    for v in ts_agg.values():
        total = v["acertos"] + v["erros"]
        pct = (v["acertos"] / total * 100.0) if total else 0.0
        tema_sub.append({
            "tema": v["tema"],
            "subtema": v["subtema"],
            "acertos": v["acertos"],
            "erros": v["erros"],
            "total": total,
            "pct": pct
        })
    tema_sub.sort(key=lambda x: x["total"], reverse=True)

    return {"temas": temas, "tema_subtema": tema_sub}
