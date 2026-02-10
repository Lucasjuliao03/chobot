import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path("quizbot.db")

def _conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    with _conn() as con:
        cur = con.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id TEXT PRIMARY KEY,
            acertos INTEGER NOT NULL DEFAULT 0,
            erros   INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_question (
            user_id TEXT NOT NULL,
            qid     TEXT NOT NULL,
            tentativas INTEGER NOT NULL DEFAULT 0,
            acertos     INTEGER NOT NULL DEFAULT 0,
            erros       INTEGER NOT NULL DEFAULT 0,
            ultima_resposta TEXT,
            ultima_correta  INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            PRIMARY KEY (user_id, qid)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_topic_stats (
            user_id TEXT NOT NULL,
            tema TEXT NOT NULL,
            subtema TEXT NOT NULL,
            acertos INTEGER NOT NULL DEFAULT 0,
            erros   INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            PRIMARY KEY (user_id, tema, subtema)
        )
        """)

        con.commit()

def record_answer(user_id: str, qid: str, correta: bool, marcada: str, tema: str, subtema: str):
    now = datetime.now().isoformat(timespec="seconds")

    with _conn() as con:
        cur = con.cursor()

        # ===== geral =====
        cur.execute("SELECT acertos, erros FROM user_stats WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO user_stats (user_id, acertos, erros, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, 1 if correta else 0, 0 if correta else 1, now)
            )
        else:
            acertos, erros = row
            acertos += 1 if correta else 0
            erros += 0 if correta else 1
            cur.execute(
                "UPDATE user_stats SET acertos = ?, erros = ?, updated_at = ? WHERE user_id = ?",
                (acertos, erros, now, user_id)
            )

        # ===== tema/subtema =====
        tema = (tema or "").strip()
        subtema = (subtema or "").strip()
        if tema and subtema:
            cur.execute("""
                SELECT acertos, erros FROM user_topic_stats
                WHERE user_id = ? AND tema = ? AND subtema = ?
            """, (user_id, tema, subtema))
            trow = cur.fetchone()

            if trow is None:
                cur.execute("""
                    INSERT INTO user_topic_stats (user_id, tema, subtema, acertos, erros, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (user_id, tema, subtema, 1 if correta else 0, 0 if correta else 1, now))
            else:
                ta, te = trow
                ta += 1 if correta else 0
                te += 0 if correta else 1
                cur.execute("""
                    UPDATE user_topic_stats
                    SET acertos = ?, erros = ?, updated_at = ?
                    WHERE user_id = ? AND tema = ? AND subtema = ?
                """, (ta, te, now, user_id, tema, subtema))

        # ===== por questão =====
        cur.execute("""
            SELECT tentativas, acertos, erros FROM user_question
            WHERE user_id = ? AND qid = ?
        """, (user_id, qid))
        qrow = cur.fetchone()

        if qrow is None:
            cur.execute("""
                INSERT INTO user_question
                (user_id, qid, tentativas, acertos, erros, ultima_resposta, ultima_correta, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, qid,
                1,
                1 if correta else 0,
                0 if correta else 1,
                marcada,
                1 if correta else 0,
                now
            ))
        else:
            tent, acc, err = qrow
            tent += 1
            acc += 1 if correta else 0
            err += 0 if correta else 1
            cur.execute("""
                UPDATE user_question
                SET tentativas = ?, acertos = ?, erros = ?, ultima_resposta = ?, ultima_correta = ?, updated_at = ?
                WHERE user_id = ? AND qid = ?
            """, (tent, acc, err, marcada, 1 if correta else 0, now, user_id, qid))

        con.commit()

def get_overall_progress(user_id: str):
    with _conn() as con:
        cur = con.cursor()
        cur.execute("SELECT acertos, erros, updated_at FROM user_stats WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if not row:
            return {"acertos": 0, "erros": 0, "pct": 0.0, "updated_at": None}
        acertos, erros, updated_at = row
        total = acertos + erros
        pct = (acertos / total * 100.0) if total else 0.0
        return {"acertos": acertos, "erros": erros, "pct": pct, "updated_at": updated_at}

def get_topic_breakdown(user_id: str, limit: int = 20):
    with _conn() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT tema, subtema, acertos, erros
            FROM user_topic_stats
            WHERE user_id = ?
            ORDER BY (acertos + erros) DESC, tema ASC, subtema ASC
            LIMIT ?
        """, (user_id, limit))
        rows = cur.fetchall()

    out = []
    for tema, subtema, acertos, erros in rows:
        total = acertos + erros
        pct = (acertos / total * 100.0) if total else 0.0
        out.append({"tema": tema, "subtema": subtema, "acertos": acertos, "erros": erros, "pct": pct, "total": total})
    return out

def get_question_status_map(user_id: str, qids: list[str]) -> dict[str, dict]:
    """
    Retorna um mapa:
    qid -> {tentativas, ultima_correta, acertos}

    IMPORTANTE:
    - Para contar "✅ acertada ao menos uma vez", use acertos > 0.
    - Para priorização (erradas/ok), pode continuar usando ultima_correta.
    """
    if not qids:
        return {}

    placeholders = ",".join(["?"] * len(qids))
    with _conn() as con:
        cur = con.cursor()
        cur.execute(f"""
            SELECT qid, tentativas, ultima_correta, acertos
            FROM user_question
            WHERE user_id = ? AND qid IN ({placeholders})
        """, [user_id, *qids])
        rows = cur.fetchall()

    out = {}
    for qid, tent, ultima_correta, acertos in rows:
        out[str(qid)] = {
            "tentativas": int(tent),
            "ultima_correta": int(ultima_correta),
            "acertos": int(acertos),
        }
    return out

