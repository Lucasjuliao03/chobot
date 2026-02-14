# db_turso.py
import os
from datetime import datetime, timezone

import libsql

# ==========================================================
# Config
# ==========================================================
TURSO_URL = os.getenv("TURSO_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

if not TURSO_URL:
    raise RuntimeError("TURSO_URL não definido nas variáveis de ambiente.")
if not TURSO_AUTH_TOKEN:
    raise RuntimeError("TURSO_AUTH_TOKEN não definido nas variáveis de ambiente.")

# Uma conexão global (simples e rápida). Para carga alta, dá pra evoluir.
_CONN = libsql.connect(database=TURSO_URL, auth_token=TURSO_AUTH_TOKEN)


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_qid(raw) -> str:
    """Normaliza ID de questão para evitar '1.0' vs '1'."""
    if raw is None:
        return ""
    try:
        # NaN
        if isinstance(raw, float) and raw != raw:
            return ""
    except Exception:
        pass

    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, float):
        return str(int(raw)) if raw.is_integer() else str(raw).strip()

    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none"):
        return ""
    try:
        if "." in s:
            f = float(s)
            if f.is_integer():
                return str(int(f))
    except Exception:
        pass
    return s


def _fetchall(sql: str, params: tuple = ()):
    cur = _CONN.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def _fetchone(sql: str, params: tuple = ()):
    cur = _CONN.cursor()
    cur.execute(sql, params)
    return cur.fetchone()


def _exec(sql: str, params: tuple = ()):
    cur = _CONN.cursor()
    cur.execute(sql, params)
    _CONN.commit()


# ==========================================================
# API COMPATÍVEL COM db_sheets.py (mantém todas as funções)
# ==========================================================
def init_db():
    """
    Cria as tabelas e índices equivalentes ao uso atual:
      - respostas  (equivale à sheet1 stats)
      - sent       (equivale à worksheet 'sent')
    """
    _exec(
        """
    CREATE TABLE IF NOT EXISTS respostas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        qid TEXT NOT NULL,
        acertou INTEGER NOT NULL,   -- 1 ou 0
        marcada TEXT,
        tema TEXT,
        subtema TEXT,
        timestamp TEXT
    )
    """
    )

    _exec("CREATE INDEX IF NOT EXISTS idx_respostas_user ON respostas(user_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_respostas_user_qid ON respostas(user_id, qid)")
    _exec("CREATE INDEX IF NOT EXISTS idx_respostas_tema_sub ON respostas(tema, subtema)")
    _exec("CREATE INDEX IF NOT EXISTS idx_respostas_qid ON respostas(qid)")

    _exec(
        """
    CREATE TABLE IF NOT EXISTS sent (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        qid TEXT NOT NULL,
        message_id INTEGER NOT NULL,
        correta_exibida TEXT,
        perm TEXT,
        timestamp TEXT
    )
    """
    )

    _exec("CREATE INDEX IF NOT EXISTS idx_sent_user_qid_mid ON sent(user_id, qid, message_id)")
    _exec("CREATE INDEX IF NOT EXISTS idx_sent_user_qid ON sent(user_id, qid)")


def record_answer(user_id: str, qid: str, acertou: bool, marcada: str, tema: str, subtema: str):
    ts = _utc_now_iso()
    _exec(
        """
        INSERT INTO respostas (user_id, qid, acertou, marcada, tema, subtema, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (str(user_id), normalize_qid(qid), 1 if acertou else 0, str(marcada), str(tema or ""), str(subtema or ""), ts),
    )


def get_overall_progress(user_id: str):
    uid = str(user_id)

    row = _fetchone(
        """
        SELECT
            COALESCE(SUM(acertou), 0) AS acertos,
            COUNT(*) - COALESCE(SUM(acertou), 0) AS erros,
            COUNT(*) AS total
        FROM respostas
        WHERE user_id = ?
        """,
        (uid,),
    )

    acertos = int(row[0] or 0)
    erros = int(row[1] or 0)
    total = int(row[2] or 0)
    pct = (acertos / total * 100.0) if total else 0.0
    return {"acertos": acertos, "erros": erros, "pct": pct}


def get_topic_breakdown(user_id: str, limit: int = 20):
    uid = str(user_id)
    lim = max(0, int(limit))

    rows = _fetchall(
        """
        SELECT
            COALESCE(tema, '') AS tema,
            COALESCE(subtema, '') AS subtema,
            COALESCE(SUM(acertou), 0) AS acertos,
            COUNT(*) - COALESCE(SUM(acertou), 0) AS erros,
            COUNT(*) AS total
        FROM respostas
        WHERE user_id = ?
        GROUP BY tema, subtema
        ORDER BY total DESC
        LIMIT ?
        """,
        (uid, lim),
    )

    out = []
    for tema, subtema, acertos, erros, total in rows:
        acertos = int(acertos or 0)
        erros = int(erros or 0)
        total = int(total or 0)
        pct = (acertos / total * 100.0) if total else 0.0
        out.append(
            {
                "tema": str(tema or ""),
                "subtema": str(subtema or ""),
                "acertos": acertos,
                "erros": erros,
                "total": total,
                "pct": pct,
            }
        )
    return out


def get_question_status_map(user_id: str):
    """
    Regra mantida:
      - True  => acertou ao menos uma vez na questão
      - False => errou e nunca acertou
      - ausente => não respondeu (não aparece no dict)
    """
    uid = str(user_id)

    rows = _fetchall(
        """
        SELECT qid, MAX(acertou) AS ok
        FROM respostas
        WHERE user_id = ?
        GROUP BY qid
        """,
        (uid,),
    )

    status = {}
    for qid, ok in rows:
        q = normalize_qid(qid)
        if not q:
            continue
        status[q] = True if int(ok or 0) == 1 else False
    return status


def reset_user_stats(user_id: str):
    uid = str(user_id)
    _exec("DELETE FROM respostas WHERE user_id = ?", (uid,))
    _exec("DELETE FROM sent WHERE user_id = ?", (uid,))


def record_sent_question(user_id: str, qid: str, message_id: int, correta_exibida: str, perm: str):
    ts = _utc_now_iso()
    _exec(
        """
        INSERT INTO sent (user_id, qid, message_id, correta_exibida, perm, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(user_id),
            normalize_qid(qid),
            int(message_id),
            str(correta_exibida or "").strip().upper(),
            str(perm or "").strip(),
            ts,
        ),
    )


def get_sent_correct(user_id: str, qid: str, message_id: int) -> str:
    uid = str(user_id)
    q = normalize_qid(qid)
    mid = int(message_id)

    row = _fetchone(
        """
        SELECT correta_exibida
        FROM sent
        WHERE user_id = ? AND qid = ? AND message_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (uid, q, mid),
    )
    if not row:
        return ""
    return str(row[0] or "").strip().upper()


def get_last_perm_for_user_question(user_id: str, qid: str) -> str:
    uid = str(user_id)
    q = normalize_qid(qid)

    row = _fetchone(
        """
        SELECT perm
        FROM sent
        WHERE user_id = ? AND qid = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (uid, q),
    )
    if not row:
        return ""
    return str(row[0] or "").strip()


def get_users_overall_scores(limit: int | None = None):
    """
    Retorna lista:
      [{"user_id": "...", "respondidas": N, "acertos": A, "erros": E, "pct": P}, ...]
    Ordenado por respondidas desc.
    """
    if limit is None:
        rows = _fetchall(
            """
            SELECT
                user_id,
                COUNT(*) AS respondidas,
                COALESCE(SUM(acertou), 0) AS acertos,
                COUNT(*) - COALESCE(SUM(acertou), 0) AS erros
            FROM respostas
            GROUP BY user_id
            ORDER BY respondidas DESC
            """
        )
    else:
        lim = max(0, int(limit))
        rows = _fetchall(
            """
            SELECT
                user_id,
                COUNT(*) AS respondidas,
                COALESCE(SUM(acertou), 0) AS acertos,
                COUNT(*) - COALESCE(SUM(acertou), 0) AS erros
            FROM respostas
            GROUP BY user_id
            ORDER BY respondidas DESC
            LIMIT ?
            """,
            (lim,),
        )

    out = []
    for uid, respondidas, acertos, erros in rows:
        respondidas = int(respondidas or 0)
        acertos = int(acertos or 0)
        erros = int(erros or 0)
        pct = (acertos / respondidas * 100.0) if respondidas else 0.0
        out.append(
            {
                "user_id": str(uid),
                "respondidas": respondidas,
                "acertos": acertos,
                "erros": erros,
                "pct": pct,
            }
        )
    return out


def get_user_topic_breakdown_full(user_id: str):
    """
    Retorna duas visões:
      - por tema (agregado)
      - por tema/subtema (detalhado)
    """
    uid = str(user_id).strip()

    rows_tema = _fetchall(
        """
        SELECT
            COALESCE(tema, '') AS tema,
            COALESCE(SUM(acertou), 0) AS acertos,
            COUNT(*) - COALESCE(SUM(acertou), 0) AS erros,
            COUNT(*) AS total
        FROM respostas
        WHERE user_id = ?
        GROUP BY tema
        ORDER BY total DESC
        """,
        (uid,),
    )

    rows_det = _fetchall(
        """
        SELECT
            COALESCE(tema, '') AS tema,
            COALESCE(subtema, '') AS subtema,
            COALESCE(SUM(acertou), 0) AS acertos,
            COUNT(*) - COALESCE(SUM(acertou), 0) AS erros,
            COUNT(*) AS total
        FROM respostas
        WHERE user_id = ?
        GROUP BY tema, subtema
        ORDER BY total DESC
        """,
        (uid,),
    )

    tema_out = []
    for tema, acertos, erros, total in rows_tema:
        acertos = int(acertos or 0)
        erros = int(erros or 0)
        total = int(total or 0)
        pct = (acertos / total * 100.0) if total else 0.0
        tema_out.append({"tema": str(tema or ""), "acertos": acertos, "erros": erros, "total": total, "pct": pct})

    det_out = []
    for tema, subtema, acertos, erros, total in rows_det:
        acertos = int(acertos or 0)
        erros = int(erros or 0)
        total = int(total or 0)
        pct = (acertos / total * 100.0) if total else 0.0
        det_out.append(
            {
                "tema": str(tema or ""),
                "subtema": str(subtema or ""),
                "acertos": acertos,
                "erros": erros,
                "total": total,
                "pct": pct,
            }
        )

    return {"por_tema": tema_out, "por_tema_subtema": det_out}
