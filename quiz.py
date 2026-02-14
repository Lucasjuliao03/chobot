import re
import random
import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ✅ TROCA: agora vem do Turso (persistente)
from db_turso import get_question_status_map, get_last_perm_for_user_question, record_sent_question


def _norm_qid(x) -> str:
    s = str(x).strip()
    # Converte "123.0" -> "123" (Excel costuma vir como float)
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".", 1)[0]
    return s


# --- carga e normalização ---
df = pd.read_excel("perguntascho2026.xlsx")
df.columns = df.columns.str.strip()

if "ID" not in df.columns:
    raise RuntimeError("Coluna 'ID' não encontrada no Excel.")

df["ID"] = df["ID"].apply(_norm_qid)
df["Tema"] = df["Tema"].astype(str).str.strip()
df["Subtema"] = df["Subtema"].astype(str).str.strip()

QUESTIONS_BY_ID = {_norm_qid(r["ID"]): r.dropna().to_dict() for _, r in df.iterrows()}

# precomputações
TEMAS = sorted(df["Tema"].dropna().unique().tolist())
TEMA_TO_QIDS = {tema: df[df["Tema"] == tema]["ID"].tolist() for tema in TEMAS}
TEMA_TO_SUBTEMAS = {tema: sorted(df[df["Tema"] == tema]["Subtema"].dropna().unique().tolist()) for tema in TEMAS}
SUBTEMA_TO_QIDS = {}
for tema in TEMAS:
    for sub in TEMA_TO_SUBTEMAS[tema]:
        SUBTEMA_TO_QIDS[(tema, sub)] = df[(df["Tema"] == tema) & (df["Subtema"] == sub)]["ID"].tolist()


def _extract_letter(value) -> str:
    if value is None:
        return ""
    s = str(value).strip().upper()
    if not s:
        return ""
    m = re.match(r"^([A-E])\b", s)
    if m:
        return m.group(1)
    if len(s) == 1 and s in "ABCDE":
        return s
    return ""


def get_correct_and_explanation(qid: str):
    q = QUESTIONS_BY_ID.get(str(qid).strip())
    if not q:
        return "", ""

    correta = _extract_letter(q.get("Gabarito", ""))
    explic = str(q.get("Explicação", "") or "").strip()
    return correta, explic


def _count_acertos_erros(user_id: str, qids: list[str]):
    """
    Conta acertos/erros para um conjunto de qids, baseado no status_map:
      - True  => acertou ao menos uma vez
      - False => errou e nunca acertou
      - ausente => não respondeu
    """
    status_map = get_question_status_map(user_id)
    wanted = set([str(x).strip() for x in qids if str(x).strip()])

    sub = {k: v for k, v in status_map.items() if k in wanted}

    acertos = sum(1 for v in sub.values() if v is True)
    erros = sum(1 for v in sub.values() if v is False)
    total_resp = len(sub)
    total_q = len(wanted)

    return acertos, erros, total_resp, total_q


async def enviar_temas(update, context, user_id: str):
    """
    Lista temas com contagem de acertos/total de questões do tema.
    """
    teclado = []
    for tema in TEMAS:
        qids = TEMA_TO_QIDS.get(tema, [])
        acertos, erros, total_resp, total_q = _count_acertos_erros(user_id, qids)

        label = f"{tema}  —  {acertos}/{total_q}"
        teclado.append([InlineKeyboardButton(label, callback_data=f"TEMA|{tema}")])

    teclado.append([InlineKeyboardButton("📊 Estatísticas", callback_data="STATS|")])
    teclado.append([InlineKeyboardButton("♻️ Resetar estatísticas", callback_data="RST|ASK")])

    await update.message.reply_text(
        "Escolha um *Tema*:",
        reply_markup=InlineKeyboardMarkup(teclado),
        parse_mode="Markdown",
    )


async def enviar_subtemas(update, context, user_id: str, tema: str):
    """
    Lista subtemas do tema, com acertos/total de questões do subtema.
    """
    subs = TEMA_TO_SUBTEMAS.get(tema, [])
    teclado = []

    for sub in subs:
        qids = SUBTEMA_TO_QIDS.get((tema, sub), [])
        acertos, erros, total_resp, total_q = _count_acertos_erros(user_id, qids)

        label = f"{sub}  —  {acertos}/{total_q}"
        teclado.append([InlineKeyboardButton(label, callback_data=f"SUB|{sub}")])

    teclado.append([InlineKeyboardButton("⬅️ Voltar", callback_data="BACK|TEMAS")])

    await update.callback_query.edit_message_text(
        f"Tema: *{tema}*\nEscolha um *Subtema*:",
        reply_markup=InlineKeyboardMarkup(teclado),
        parse_mode="Markdown",
    )


def _format_question(qid: str):
    q = QUESTIONS_BY_ID.get(str(qid).strip())
    if not q:
        return "Questão não encontrada.", [], ""

    enun = str(q.get("Enunciado", "") or "").strip()
    opc_a = str(q.get("A", "") or "").strip()
    opc_b = str(q.get("B", "") or "").strip()
    opc_c = str(q.get("C", "") or "").strip()
    opc_d = str(q.get("D", "") or "").strip()
    opc_e = str(q.get("E", "") or "").strip()

    correta = _extract_letter(q.get("Gabarito", ""))

    # teclado
    teclado = [
        [
            InlineKeyboardButton("A", callback_data=f"RESP|{qid}|A"),
            InlineKeyboardButton("B", callback_data=f"RESP|{qid}|B"),
            InlineKeyboardButton("C", callback_data=f"RESP|{qid}|C"),
            InlineKeyboardButton("D", callback_data=f"RESP|{qid}|D"),
            InlineKeyboardButton("E", callback_data=f"RESP|{qid}|E"),
        ]
    ]

    msg = (
        f"*Questão {qid}*\n\n"
        f"{enun}\n\n"
        f"*A)* {opc_a}\n"
        f"*B)* {opc_b}\n"
        f"*C)* {opc_c}\n"
        f"*D)* {opc_d}\n"
        f"*E)* {opc_e}\n"
    )

    return msg, teclado, correta


async def iniciar_quiz(update, context, user_id: str, tema: str, subtema: str, limite: int = 20):
    """
    Seleciona perguntas do subtema e inicia o fluxo.
    """
    qids = SUBTEMA_TO_QIDS.get((tema, subtema), [])
    if not qids:
        await update.callback_query.edit_message_text(
            "Nenhuma questão encontrada para esse subtema."
        )
        return

    # embaralha e pega limite
    qids = qids[:]
    random.shuffle(qids)
    qids = qids[: int(limite)]

    # salva estado
    context.chat_data["tema"] = tema
    context.chat_data["subtema"] = subtema
    context.chat_data["fila_qids"] = qids
    context.chat_data["idx"] = 0

    await enviar_proxima(update, context, user_id, via_edit=True)


async def enviar_proxima(update, context, user_id: str, via_edit: bool = False):
    fila = context.chat_data.get("fila_qids") or []
    idx = int(context.chat_data.get("idx") or 0)

    if idx >= len(fila):
        txt = "✅ *Fim do quiz!*"
        if via_edit and update.callback_query:
            await update.callback_query.edit_message_text(txt, parse_mode="Markdown")
        else:
            await update.message.reply_text(txt, parse_mode="Markdown")
        return

    qid = str(fila[idx]).strip()

    msg, teclado, correta = _format_question(qid)

    # Permissão para "reforço": evita repetir a mesma correta como padrão quando há bug
    perm = get_last_perm_for_user_question(user_id, qid) or ""
    # registra "sent" com correta exibida + perm
    # message_id vai ser conhecido após enviar; então registra depois de enviar (aqui fazemos em 2 etapas)
    if via_edit and update.callback_query:
        sent = await update.callback_query.edit_message_text(
            msg,
            reply_markup=InlineKeyboardMarkup(teclado),
            parse_mode="Markdown",
        )
        message_id = getattr(sent, "message_id", None)
    else:
        sent = await update.message.reply_text(
            msg,
            reply_markup=InlineKeyboardMarkup(teclado),
            parse_mode="Markdown",
        )
        message_id = getattr(sent, "message_id", None)

    if message_id is not None:
        record_sent_question(user_id, qid, message_id, correta, perm)


def get_question_meta(qid: str):
    q = QUESTIONS_BY_ID.get(str(qid).strip())
    if not q:
        return "", ""
    tema = str(q.get("Tema", "") or "").strip()
    sub = str(q.get("Subtema", "") or "").strip()
    return tema, sub



