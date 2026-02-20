import re
import random
import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ✅ TROCA: agora vem do Turso (persistente)
from db_turso import get_question_status_map, get_last_perm_for_user_question, record_sent_question


def _norm_qid(x) -> str:
    """
    Ajuste MÍNIMO e necessário:
    - Normaliza IDs vindos do Excel/fluxo para evitar '908.0' vs '908'
    - Mantém o resto do arquivo intacto.
    """
    s = str(x).strip()
    if not s:
        return ""
    # '908.0', '908.00' -> '908'
    if re.match(r"^\d+\.0+$", s):
        return s.split(".")[0]
    # '908.' -> '908' | '908.000' -> '908'
    if "." in s:
        s2 = s.rstrip("0").rstrip(".")
        if s2.isdigit():
            return s2
    # fallback: float inteiro -> int
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass
    return s


# --- carga e normalização ---
df = pd.read_excel("perguntascho2026.xlsx")
df.columns = df.columns.str.strip()

if "ID" not in df.columns:
    raise RuntimeError("Coluna 'ID' não encontrada no Excel.")

# ✅ AJUSTE NECESSÁRIO: normaliza IDs para não virar 'xxx.0'
df["ID"] = df["ID"].apply(_norm_qid)
df["Tema"] = df["Tema"].astype(str).str.strip()
df["Subtema"] = df["Subtema"].astype(str).str.strip()

QUESTIONS_BY_ID = {str(r["ID"]): r.dropna().to_dict() for _, r in df.iterrows()}

# precomputações
TEMAS = sorted(df["Tema"].dropna().unique().tolist())
TEMA_TO_QIDS = {
    tema: df[df["Tema"] == tema]["ID"].apply(_norm_qid).tolist()
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
            df[(df["Tema"] == tema) & (df["Subtema"] == sub)]["ID"].apply(_norm_qid).tolist()
        )


def _extract_letter(value) -> str:
    s = str(value).strip().upper()
    m = re.search(r"\b([ABCD])\b", s)
    return m.group(1) if m else ""


def get_question_by_id(qid: str) -> dict | None:
    # ✅ AJUSTE NECESSÁRIO: normaliza antes de buscar
    qid = _norm_qid(qid)
    return QUESTIONS_BY_ID.get(qid)


def get_correct_and_explanation(qid: str) -> tuple[str, str]:
    qid = _norm_qid(qid)  # ✅ necessário
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
    all_map = get_question_status_map(str(user_id), source="GEN")
    # ✅ AJUSTE NECESSÁRIO: normaliza qids do filtro
    qset = set(_norm_qid(x) for x in qids)
    return {qid: st for qid, st in all_map.items() if _norm_qid(qid) in qset}


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


def _make_perm_no_repeat(user_id: str, qid: str) -> list[str]:
    """
    Gera perm (ordem de letras originais) evitando repetir a última perm desse user/qid.
    """
    # ✅ AJUSTE NECESSÁRIO: normaliza qid para bater com o Turso
    qid = _norm_qid(qid)

    last_perm = get_last_perm_for_user_question(str(user_id), str(qid).strip(), source="GEN")
    last = [p.strip().upper() for p in last_perm.split(",")] if last_perm else []

    base = LETRAS[:]  # ["A","B","C","D"]

    for _ in range(12):
        cand = base[:]
        random.shuffle(cand)
        if cand != last:
            return cand

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
        letra_orig = perm[i]
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

        label = f"{tema}  |  {icon} {acertos}/{total}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"TEMA|{tema}")])

    texto = "📚 *Escolha o TEMA:*"
    markup = InlineKeyboardMarkup(keyboard)

    # pode ser chamado por /start (mensagem) ou por callback (botão)
    if getattr(update, "message", None):
        await update.message.reply_text(
            texto,
            reply_markup=markup,
            parse_mode="Markdown",
        )
    else:
        await update.effective_chat.send_message(
            texto,
            reply_markup=markup,
            parse_mode="Markdown",
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

    # ✅ AJUSTE NECESSÁRIO: normaliza IDs do recorte também
    base["ID"] = base["ID"].apply(_norm_qid)
    qids = base["ID"].tolist()

    all_status = get_question_status_map(str(user_id), source="GEN")

    nao_resp, erradas, acertadas = [], [], []
    for qid in qids:
        qn = _norm_qid(qid)  # ✅ necessário
        st = all_status.get(qn)
        if st is None:
            nao_resp.append(qn)
        elif st is False:
            erradas.append(qn)
        else:
            acertadas.append(qn)

    nao_resp = base[base["ID"].isin(nao_resp)].sample(frac=1).to_dict("records")
    erradas = base[base["ID"].isin(erradas)].sample(frac=1).to_dict("records")
    acertadas = base[base["ID"].isin(acertadas)].sample(frac=1).to_dict("records")

    fila = (nao_resp + erradas + acertadas)[:limite]

    fila_clean = []
    for item in fila:
        # ✅ AJUSTE NECESSÁRIO: garante ID normalizado no payload
        item["ID"] = _norm_qid(item.get("ID", ""))
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

    qid = _norm_qid(q.get("ID", ""))  # ✅ AJUSTE NECESSÁRIO
    user_id = str(quiz.get("user_id") or "")

    correta_original, _exp = get_correct_and_explanation(qid)

    perm = _make_perm_no_repeat(user_id, qid)
    alternativas_exibidas, correta_exibida = _apply_perm(q, perm, correta_original)

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

    msg = await update.effective_chat.send_message(
        texto,
        reply_markup=InlineKeyboardMarkup(teclado),
        parse_mode="Markdown"
    )

    try:
        record_sent_question(
            user_id=user_id,
            source="GEN",
            qid=qid,  # ✅ já normalizado
            message_id=msg.message_id,
            correta_exibida=correta_exibida,
            perm=",".join(perm)
        )
    except Exception:
        pass


