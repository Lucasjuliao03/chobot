# quiz.py
import os
import re
import random
import math
import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ✅ TROCA: agora vem do Turso (persistente)
from db_turso import get_question_status_map, get_last_perm_for_user_question, record_sent_question


# --- carga e normalização ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_PATH = os.path.join(BASE_DIR, "perguntascho2026.xlsx")


def normalize_qid(raw) -> str:
    """Normaliza ID de questão para evitar '1.0' vs '1' (pandas/Excel)."""
    if raw is None:
        return ""
    # trata NaN do pandas
    try:
        if isinstance(raw, float) and math.isnan(raw):
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

    # se vier como string numérica com .0
    try:
        if "." in s:
            f = float(s)
            if f.is_integer():
                return str(int(f))
    except Exception:
        pass
    return s


df = pd.read_excel(EXCEL_PATH)
df.columns = df.columns.str.strip()

if "ID" not in df.columns:
    raise RuntimeError("Coluna 'ID' não encontrada no Excel.")

df["ID"] = df["ID"].apply(normalize_qid)
df["Tema"] = df["Tema"].astype(str).str.strip()
df["Subtema"] = df["Subtema"].astype(str).str.strip()

QUESTIONS_BY_ID = {str(r["ID"]): r.dropna().to_dict() for _, r in df.iterrows()}

# precomputações
TEMAS = sorted(df["Tema"].dropna().unique().tolist())
TEMA_TO_QIDS = {tema: df[df["Tema"] == tema]["ID"].apply(normalize_qid).tolist() for tema in TEMAS}
TEMA_TO_SUBTEMAS = {tema: sorted(df[df["Tema"] == tema]["Subtema"].dropna().unique().tolist()) for tema in TEMAS}
SUBTEMA_TO_QIDS = {}
for tema in TEMAS:
    for sub in TEMA_TO_SUBTEMAS[tema]:
        SUBTEMA_TO_QIDS[(tema, sub)] = df[(df["Tema"] == tema) & (df["Subtema"] == sub)]["ID"].apply(normalize_qid).tolist()


def _extract_letter(value) -> str:
    s = str(value).strip().upper()
    m = re.search(r"\b([ABCD])\b", s)
    return m.group(1) if m else ""


def get_question_by_id(qid: str) -> dict | None:
    qid = normalize_qid(qid)
    return QUESTIONS_BY_ID.get(qid)


def get_correct_and_explanation(qid: str) -> tuple[str, str]:
    q = get_question_by_id(qid)
    if not q:
        return "", ""
    correta = _extract_letter(q.get("Resposta Correta", ""))
    explicacao = str(q.get("Explicação", "") or "").strip()
    return correta, explicacao


def _subset_status_map(user_id: str, qids: list[str]) -> dict:
    """
    get_question_status_map(user_id) retorna status global: {qid: True/False}
    Aqui filtramos apenas as questões do tema/subtema.

    Importante: normaliza IDs para evitar mismatch ('1' vs '1.0', espaços, etc.)
    """
    all_map = get_question_status_map(str(user_id)) or {}

    qset = set()
    for x in qids:
        nx = normalize_qid(x)
        if nx:
            qset.add(nx)

    norm_all = {}
    for k, v in all_map.items():
        nk = normalize_qid(k)
        if nk:
            norm_all[nk] = v

    return {qid: st for qid, st in norm_all.items() if qid in qset}


def _count_acertos_erros(user_id: str, qids: list[str]) -> tuple[int, int]:
    """
    Retorna (acertos, erros) no conjunto de qids, considerando:
      - True  => acertou ao menos uma vez
      - False => errou e nunca acertou
      - ausente => não respondida
    """
    sub = _subset_status_map(user_id, qids)
    acertos = sum(1 for v in sub.values() if v is True)
    erros = sum(1 for v in sub.values() if v is False)
    return acertos, erros


def _progress_icon(ok: int, total: int) -> str:
    """
    Regra:
    - ⚪ se total==0 ou ok/total <= 50%
    - 🟡 se 50% < ok/total < 100%
    - ✅ se 100%
    """
    if total <= 0:
        return "⚪"
    ratio = ok / total
    if ratio >= 1.0:
        return "✅"
    if ratio > 0.5:
        return "🟡"
    return "⚪"


# ==========================================================
# 🔥 embaralhamento não repetido por usuário/questão
# ==========================================================
LETRAS = ["A", "B", "C", "D"]


def _shuffle_alternatives(q: dict, seed_key: str) -> tuple[list[tuple[str, str]], str]:
    """
    Embaralha alternativas A-D de forma estável por (user_id + qid),
    retornando:
      - lista [(letra_original, texto), ...] em nova ordem
      - mapeamento perm (ex: "CADB") para persistir (a ordem das letras originais)
    """
    opts = []
    for L in LETRAS:
        col = f"Opção {L}"
        val = str(q.get(col, "") or "").strip()
        if val:
            opts.append((L, val))

    if not opts:
        return [], ""

    rng = random.Random(seed_key)
    rng.shuffle(opts)

    perm = "".join([L for L, _ in opts])  # ex: "CADB"
    return opts, perm


def _apply_perm_to_correct(correta_original: str, perm: str) -> str:
    """
    Se perm="CADB" significa:
      posição 0 exibida = letra original C
      posição 1 exibida = letra original A
      ...
    Queremos a letra exibida (A/B/C/D) que corresponde à correta original.
    """
    correta_original = str(correta_original or "").strip().upper()
    perm = str(perm or "").strip().upper()
    if not correta_original or correta_original not in LETRAS:
        return ""
    if len(perm) != len(LETRAS):
        return ""

    # índice da correta original na perm => letra exibida é LETRAS[idx]
    try:
        idx = perm.index(correta_original)
    except ValueError:
        return ""
    return LETRAS[idx]


# ==========================================================
# UI: temas/subtemas com contagem e emoji
# ==========================================================
async def enviar_temas(update, context):
    user_id = str(update.effective_user.id)

    teclado = []
    row = []
    for tema in TEMAS:
        qids = TEMA_TO_QIDS.get(tema, [])
        total = len(qids)
        acertos, _erros = _count_acertos_erros(user_id, qids)
        icon = _progress_icon(acertos, total)
        txt = f"{icon} {tema} ({acertos}/{total})"
        row.append(InlineKeyboardButton(txt, callback_data=f"TEMA|{tema}"))
        if len(row) == 2:
            teclado.append(row)
            row = []
    if row:
        teclado.append(row)

    await update.message.reply_text(
        "📚 *Escolha um Tema:*",
        reply_markup=InlineKeyboardMarkup(teclado),
        parse_mode="Markdown",
    )


async def enviar_subtemas(update, context, tema: str):
    user_id = str(update.effective_user.id)
    subs = TEMA_TO_SUBTEMAS.get(tema, [])

    teclado = []
    row = []
    for sub in subs:
        qids = SUBTEMA_TO_QIDS.get((tema, sub), [])
        total = len(qids)
        acertos, _erros = _count_acertos_erros(user_id, qids)
        icon = _progress_icon(acertos, total)
        txt = f"{icon} {sub} ({acertos}/{total})"
        row.append(InlineKeyboardButton(txt, callback_data=f"SUB|{sub}"))
        if len(row) == 2:
            teclado.append(row)
            row = []
    if row:
        teclado.append(row)

    query = update.callback_query
    if query:
        await query.edit_message_text(
            f"📌 *Tema:* {tema}\n\nEscolha um *Subtema:*",
            reply_markup=InlineKeyboardMarkup(teclado),
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"📌 *Tema:* {tema}\n\nEscolha um *Subtema:*",
            reply_markup=InlineKeyboardMarkup(teclado),
            parse_mode="Markdown",
        )


# ==========================================================
# Quiz: seleção e envio de questões
# ==========================================================
def _choose_questions_for_user(user_id: str, qids: list[str], limit: int = 20) -> list[str]:
    """
    Prioridade:
      1) nunca respondidas
      2) erradas (False)
      3) menos respondidas (aqui aproximado pelo que foi respondido ou não; mantendo regra simples)
    """
    status = get_question_status_map(str(user_id)) or {}

    # normaliza ids do conjunto e do status
    qids_norm = [normalize_qid(x) for x in qids if normalize_qid(x)]
    status_norm = {}
    for k, v in status.items():
        nk = normalize_qid(k)
        if nk:
            status_norm[nk] = v

    nunca = [qid for qid in qids_norm if qid not in status_norm]
    erradas = [qid for qid in qids_norm if status_norm.get(qid) is False]
    certas = [qid for qid in qids_norm if status_norm.get(qid) is True]

    random.shuffle(nunca)
    random.shuffle(erradas)
    random.shuffle(certas)

    chosen = (nunca + erradas + certas)[: max(1, int(limit))]
    return chosen


async def iniciar_quiz(update, context, user_id: str, tema: str, subtema: str, limite: int = 20):
    qids = SUBTEMA_TO_QIDS.get((tema, subtema), [])
    qids = [normalize_qid(x) for x in qids if normalize_qid(x)]
    if not qids:
        await update.callback_query.answer("Sem questões nesse subtema.", show_alert=True)
        return

    selecionadas = _choose_questions_for_user(user_id, qids, limit=limite)

    context.chat_data["quiz"] = {
        "tema": tema,
        "subtema": subtema,
        "lista": selecionadas,
        "idx": 0,
    }

    await enviar_proxima(update, context)


async def enviar_proxima(update, context):
    sess = context.chat_data.get("quiz")
    if not sess:
        return

    user_id = str(update.effective_user.id)

    idx = int(sess.get("idx", 0))
    lista = sess.get("lista") or []
    if idx >= len(lista):
        await update.effective_chat.send_message("✅ Fim do quiz. Use /start para escolher outro tema/subtema.")
        return

    qid = normalize_qid(lista[idx])
    q = get_question_by_id(qid)
    if not q:
        # pula se não achar
        sess["idx"] = idx + 1
        context.chat_data["quiz"] = sess
        await enviar_proxima(update, context)
        return

    enunciado = str(q.get("Enunciado", "") or "").strip()
    if not enunciado:
        enunciado = str(q.get("Pergunta", "") or "").strip()

    correta_original, _exp = get_correct_and_explanation(qid)

    # perm estável: tenta recuperar do histórico; se não existir, gera e grava no 'sent'
    perm = get_last_perm_for_user_question(user_id, qid)
    if not perm:
        _opts, perm = _shuffle_alternatives(q, seed_key=f"{user_id}:{qid}")
    else:
        # monta opts conforme perm
        _opts = []
        for Lorig in perm:
            col = f"Opção {Lorig}"
            val = str(q.get(col, "") or "").strip()
            if val:
                _opts.append((Lorig, val))

    correta_exibida = _apply_perm_to_correct(correta_original, perm)

    # guarda em chat_data como fallback
    context.chat_data["correta_exibida"] = correta_exibida
    context.chat_data["qid_atual"] = qid
    context.chat_data["perm_atual"] = perm

    # registra o envio no Turso (para recuperar correta_exibida por message_id depois)
    texto = f"*{idx+1}/{len(lista)}* — ID {qid}\n\n{enunciado}\n"
    teclado = []

    # opções exibidas como A/B/C/D (posições), mas callback envia letra exibida
    for i, (Lorig, txt) in enumerate(_opts):
        Ldisp = LETRAS[i] if i < len(LETRAS) else ""
        if not Ldisp:
            continue
        texto += f"\n*{Ldisp})* {txt}"
        teclado.append([InlineKeyboardButton(f"{Ldisp}", callback_data=f"RESP|{qid}|{Ldisp}")])

    msg = await update.effective_chat.send_message(
        texto,
        reply_markup=InlineKeyboardMarkup(teclado),
        parse_mode="Markdown",
    )

    try:
        record_sent_question(user_id, qid, msg.message_id, correta_exibida, perm)
    except Exception:
        pass

    # avança índice
    sess["idx"] = idx + 1
    context.chat_data["quiz"] = sess



