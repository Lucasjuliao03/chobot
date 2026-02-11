import re
import random
import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ✅ TROCA: agora vem do Sheets (persistente)
from db_sheets import get_question_status_map, get_last_perm_for_user_question, record_sent_question


# --- carga e normalização ---
df = pd.read_excel("perguntascho2026.xlsx")
df.columns = df.columns.str.strip()

if "ID" not in df.columns:
    raise RuntimeError("Coluna 'ID' não encontrada no Excel.")

df["ID"] = df["ID"].astype(str).str.strip()
df["Tema"] = df["Tema"].astype(str).str.strip()
df["Subtema"] = df["Subtema"].astype(str).str.strip()

QUESTIONS_BY_ID = {str(r["ID"]): r.dropna().to_dict() for _, r in df.iterrows()}

# precomputações
TEMAS = sorted(df["Tema"].dropna().unique().tolist())
TEMA_TO_QIDS = {
    tema: df[df["Tema"] == tema]["ID"].astype(str).str.strip().tolist()
    for tema in TEMAS
}
TEMA_TO_SUBTEMAS = {
    tema: sorted(df[df["Tema"] == tema]["Subtema"].dropna().unique().tolist())
    for tema in TEMAS
}
SUBTEMA_TO_QIDS = {}
for tema in TEMAS:
    for sub in TEMA_TO_SUBTEMAS[tema]:
        SUBTEMA_TO_QIDS[(tema, sub)] = (
            df[(df["Tema"] == tema) & (df["Subtema"] == sub)]["ID"].astype(str).str.strip().tolist()
        )


def _extract_letter(value) -> str:
    s = str(value).strip().upper()
    m = re.search(r"\b([ABCD])\b", s)
    return m.group(1) if m else ""


def get_question_by_id(qid: str) -> dict | None:
    qid = str(qid).strip()
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
    """
    all_map = get_question_status_map(str(user_id))
    qset = set(str(x).strip() for x in qids)
    return {qid: st for qid, st in all_map.items() if str(qid).strip() in qset}


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
# 🔥 NOVO: embaralhamento não repetido por usuário/questão
# - Perm é uma lista de letras originais na ordem exibida
#   ex: "C,D,A,B" significa:
#     exibida A=orig C, B=orig D, C=orig A, D=orig B
# ==========================================================
LETRAS = ["A", "B", "C", "D"]

def _make_perm_no_repeat(user_id: str, qid: str) -> list[str]:
    """
    Gera perm (ordem de letras originais) evitando repetir a última perm desse user/qid.
    """
    last_perm = get_last_perm_for_user_question(str(user_id), str(qid).strip())
    last = [p.strip().upper() for p in last_perm.split(",")] if last_perm else []

    base = LETRAS[:]  # ["A","B","C","D"]

    for _ in range(12):  # tentativas suficientes
        cand = base[:]
        random.shuffle(cand)
        if cand != last:
            return cand

    # fallback: se por alguma razão não mudar (quase impossível), retorna mesmo assim
    cand = base[:]
    random.shuffle(cand)
    return cand


def _apply_perm(q: dict, perm: list[str], correta_original: str):
    """
    perm: lista de letras ORIGINAIS na ordem exibida A,B,C,D
    retorna:
      alternativas_exibidas: dict {"A":texto, ...}
      correta_exibida: "A"/"B"/"C"/"D"
    """
    orig_to_text = {
        "A": q.get("Opção A", ""),
        "B": q.get("Opção B", ""),
        "C": q.get("Opção C", ""),
        "D": q.get("Opção D", ""),
    }

    exibidas = {}
    correta_exibida = ""

    for i, letra_exibida in enumerate(LETRAS):
        letra_orig = perm[i]  # ex: "C"
        exibidas[letra_exibida] = orig_to_text.get(letra_orig, "")
        if letra_orig == correta_original:
            correta_exibida = letra_exibida

    return exibidas, correta_exibida


# =========================
# UI: temas / subtemas
# =========================
async def enviar_temas(update, context):
    user_id = str(update.effective_user.id)

    keyboard = []
    for tema in TEMAS:
        qids = TEMA_TO_QIDS.get(tema, [])
        total = len(qids)

        acertos, _erros = _count_acertos_erros(user_id, qids)
        icon = _progress_icon(acertos, total)

        # texto: tema à esquerda, contador no final
        label = f"{tema}  |  {icon} {acertos}/{total}"

        keyboard.append([InlineKeyboardButton(label, callback_data=f"TEMA|{tema}")])

    await update.message.reply_text(
        "📚 *Escolha o TEMA:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def enviar_subtemas(update, context, tema: str):
    user_id = str(update.effective_user.id)

    subtemas = TEMA_TO_SUBTEMAS.get(tema, [])
    keyboard = []

    for s in subtemas:
        qids = SUBTEMA_TO_QIDS.get((tema, s), [])
        total = len(qids)

        acertos, _erros = _count_acertos_erros(user_id, qids)
        icon = _progress_icon(acertos, total)

        label = f"{s}  |  {icon} {acertos}/{total}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"SUB|{s}")])

    await update.callback_query.edit_message_text(
        f"📘 *Tema:* {tema}\n\n📂 Escolha o *SUBTEMA:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# =========================
# montar fila com prioridade
# =========================
async def iniciar_quiz(update, context, user_id: str, tema: str, subtema: str, limite: int = 20):
    base = df[
        (df["Tema"] == str(tema).strip()) &
        (df["Subtema"] == str(subtema).strip())
    ].copy()

    if base.empty:
        await update.effective_chat.send_message("⚠️ Sem questões para esse Tema/Subtema.")
        return

    base["ID"] = base["ID"].astype(str).str.strip()
    qids = base["ID"].tolist()

    # ✅ status via Sheets: qid -> True/False
    all_status = get_question_status_map(str(user_id))

    nao_resp, erradas, acertadas = [], [], []
    for qid in qids:
        st = all_status.get(str(qid).strip())
        if st is None:
            nao_resp.append(qid)
        elif st is False:
            erradas.append(qid)
        else:
            acertadas.append(qid)

    # embaralha dentro de cada grupo
    nao_resp = base[base["ID"].isin(nao_resp)].sample(frac=1).to_dict("records")
    erradas = base[base["ID"].isin(erradas)].sample(frac=1).to_dict("records")
    acertadas = base[base["ID"].isin(acertadas)].sample(frac=1).to_dict("records")

    fila = (nao_resp + erradas + acertadas)[:limite]

    fila_clean = []
    for item in fila:
        item["ID"] = str(item.get("ID", "")).strip()
        fila_clean.append(item)

    context.chat_data["quiz"] = {
        "user_id": str(user_id),
        "tema": tema,
        "subtema": subtema,
        "perguntas": fila_clean,
        "index": 0
    }

    await update.effective_chat.send_message(
        f"🎯 *Quiz iniciado*\n📘 Tema: *{tema}*\n📂 Subtema: *{subtema}*\n\nPrioridade: *não respondidas → erradas → restantes*",
        parse_mode="Markdown"
    )

    await enviar_proxima(update, context)


# =========================
# enviar próxima
# =========================
async def enviar_proxima(update, context):
    quiz = context.chat_data.get("quiz")
    if not quiz or quiz["index"] >= len(quiz["perguntas"]):
        await update.effective_chat.send_message("✅ Fim das questões desta sessão.")
        return

    q = quiz["perguntas"][quiz["index"]]
    quiz["index"] += 1

    qid = str(q.get("ID", "")).strip()
    user_id = str(quiz.get("user_id") or "")

    # correta original do Excel
    correta_original, _exp = get_correct_and_explanation(qid)

    # 🔥 perm sem repetir para esse usuário/questão
    perm = _make_perm_no_repeat(user_id, qid)  # lista ["C","D","A","B"]
    alternativas_exibidas, correta_exibida = _apply_perm(q, perm, correta_original)

    # guarda em sessão também (fallback)
    context.chat_data["correta_exibida"] = correta_exibida
    context.chat_data["qid_atual"] = qid
    context.chat_data["perm_atual"] = ",".join(perm)

    texto = (
        f"📘 *Tema:* {quiz['tema']}\n"
        f"📂 *Subtema:* {quiz['subtema']}\n\n"
        f"*{q.get('Pergunta','')}*\n\n"
        f"A) {alternativas_exibidas.get('A','')}\n"
        f"B) {alternativas_exibidas.get('B','')}\n"
        f"C) {alternativas_exibidas.get('C','')}\n"
        f"D) {alternativas_exibidas.get('D','')}"
    )

    teclado = [[
        InlineKeyboardButton("A", callback_data=f"RESP|{qid}|A"),
        InlineKeyboardButton("B", callback_data=f"RESP|{qid}|B"),
        InlineKeyboardButton("C", callback_data=f"RESP|{qid}|C"),
        InlineKeyboardButton("D", callback_data=f"RESP|{qid}|D"),
    ]]

    # ⚠️ CAPTURA message_id PARA PERSISTÊNCIA (restart-proof)
    msg = await update.effective_chat.send_message(
        texto,
        reply_markup=InlineKeyboardMarkup(teclado),
        parse_mode="Markdown"
    )

    # grava envio (user_id + qid + message_id) => correta_exibida + perm
    try:
        record_sent_question(
            user_id=user_id,
            qid=qid,
            message_id=msg.message_id,
            correta_exibida=correta_exibida,
            perm=",".join(perm)
        )
    except Exception:
        # se falhar, ainda funciona via session (não quebra o fluxo)
        pass


