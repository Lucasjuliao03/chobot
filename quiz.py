import re
import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from db import get_question_status_map

# --- carga e normalização ---
df = pd.read_excel("perguntascho2026.xlsx")
df.columns = df.columns.str.strip()

# garante ID como string sem espaços
if "ID" not in df.columns:
    raise RuntimeError("Coluna 'ID' não encontrada no Excel.")
df["ID"] = df["ID"].astype(str).str.strip()

# cria mapa estático id -> dict(row)
QUESTIONS_BY_ID = {str(r["ID"]): r.dropna().to_dict() for _, r in df.iterrows()}

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

# =========================
# UI: temas / subtemas / início
# =========================
async def enviar_temas(update, context):
    temas = sorted(df["Tema"].dropna().astype(str).str.strip().unique().tolist())
    keyboard = [[InlineKeyboardButton(t, callback_data=f"TEMA|{t}")] for t in temas]
    await update.message.reply_text(
        "📚 *Escolha o TEMA:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def enviar_subtemas(update, context, tema: str):
    subtemas = sorted(df[df["Tema"].astype(str).str.strip() == tema]["Subtema"].dropna().astype(str).str.strip().unique().tolist())
    keyboard = [[InlineKeyboardButton(s, callback_data=f"SUB|{s}")] for s in subtemas]
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
        (df["Tema"].astype(str).str.strip() == tema) &
        (df["Subtema"].astype(str).str.strip() == subtema)
    ].copy()

    if base.empty:
        await update.effective_chat.send_message("⚠️ Sem questões para esse Tema/Subtema.")
        return

    base["ID"] = base["ID"].astype(str).str.strip()
    qids = base["ID"].tolist()
    status = get_question_status_map(user_id, qids)

    nao_resp, erradas, acertadas = [], [], []
    for qid in qids:
        st = status.get(qid)
        if not st:
            nao_resp.append(qid)
        elif st["ultima_correta"] == 0:
            erradas.append(qid)
        else:
            acertadas.append(qid)

    # embaralha dentro de cada grupo
    nao_resp = base[base["ID"].isin(nao_resp)].sample(frac=1).to_dict("records")
    erradas = base[base["ID"].isin(erradas)].sample(frac=1).to_dict("records")
    acertadas = base[base["ID"].isin(acertadas)].sample(frac=1).to_dict("records")

    fila = (nao_resp + erradas + acertadas)[:limite]

    # padroniza cada item da fila: garante ID string clean
    fila_clean = []
    for item in fila:
        item["ID"] = str(item.get("ID","")).strip()
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
# enviar próxima (usa fila em chat_data)
# =========================
async def enviar_proxima(update, context):
    quiz = context.chat_data.get("quiz")
    if not quiz or quiz["index"] >= len(quiz["perguntas"]):
        await update.effective_chat.send_message("✅ Fim das questões desta sessão.")
        return

    q = quiz["perguntas"][quiz["index"]]
    quiz["index"] += 1

    qid = str(q.get("ID", "")).strip()

    # monta enunciado com alternativas inline (no texto)
    texto = (
        f"📘 *Tema:* {quiz['tema']}\n"
        f"📂 *Subtema:* {quiz['subtema']}\n\n"
        f"*{q.get('Pergunta','')}*\n\n"
        f"A) {q.get('Opção A','')}\n"
        f"B) {q.get('Opção B','')}\n"
        f"C) {q.get('Opção C','')}\n"
        f"D) {q.get('Opção D','')}"
    )

    # CALLBACK contém apenas qid + alternativa marcada
    teclado = [[
        InlineKeyboardButton("A", callback_data=f"RESP|{qid}|A"),
        InlineKeyboardButton("B", callback_data=f"RESP|{qid}|B"),
        InlineKeyboardButton("C", callback_data=f"RESP|{qid}|C"),
        InlineKeyboardButton("D", callback_data=f"RESP|{qid}|D"),
    ]]

    await update.effective_chat.send_message(
        texto,
        reply_markup=InlineKeyboardMarkup(teclado),
        parse_mode="Markdown"
    )
