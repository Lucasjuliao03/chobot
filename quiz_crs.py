
import re
import random
import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ✅ persistência (mesma do módulo atual)
from db_turso import (
    get_question_status_map,
    get_last_perm_for_user_question,
    record_sent_question,
    get_last_answered_map,
)


def _norm_qid(x) -> str:
    s = str(x).strip()
    if not s or s.lower() == "nan":
        return ""
    if re.match(r"^\d+\.0+$", s):
        return s.split(".")[0]
    if "." in s:
        s2 = s.rstrip("0").rstrip(".")
        if s2.isdigit():
            return s2
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except Exception:
        pass
    return s


def _extract_letter(value) -> str:
    s = str(value).strip().upper()
    m = re.search(r"\b([ABCD])\b", s)
    return m.group(1) if m else ""


# ==========================================================
# Carga do Excel CRS + saneamento de IDs
# ==========================================================
df = pd.read_excel("QUESTOES PROVA CRS.xlsx")
df.columns = df.columns.str.strip()

required = ["Tema", "Pergunta", "Opção A", "Opção B", "Opção C", "Opção D", "Resposta Correta"]
for col in required:
    if col not in df.columns:
        raise RuntimeError(f"Coluna obrigatória '{col}' não encontrada em QUESTOES PROVA CRS.xlsx")

if "ID" not in df.columns:
    df.insert(0, "ID", range(1, len(df) + 1))
df["ID"] = df["ID"].apply(_norm_qid)

mask_empty = (df["ID"] == "") | (df["ID"].isna())
if mask_empty.any():
    start = 1
    used = set(x for x in df["ID"].tolist() if str(x).strip())
    new_ids = []
    for _ in range(int(mask_empty.sum())):
        while str(start) in used:
            start += 1
        new_ids.append(str(start))
        used.add(str(start))
        start += 1
    df.loc[mask_empty, "ID"] = new_ids

df["Tema"] = df["Tema"].astype(str).str.strip()
if "Subtema" in df.columns:
    df["Subtema"] = df["Subtema"].astype(str).str.strip()
else:
    df["Subtema"] = ""

if "CURSOS" not in df.columns:
    df["CURSOS"] = ""
if "materia" not in df.columns:
    df["materia"] = ""

QUESTIONS_BY_ID_CRS = {str(r["ID"]): r.dropna().to_dict() for _, r in df.iterrows()}

TEMAS_CRS = sorted(df["Tema"].dropna().unique().tolist())
TEMA_TO_QIDS_CRS = {
    tema: df[df["Tema"] == tema]["ID"].apply(_norm_qid).tolist()
    for tema in TEMAS_CRS
}
TEMA_TO_SUBTEMAS_CRS = {
    tema: sorted([s for s in df[df["Tema"] == tema]["Subtema"].dropna().astype(str).str.strip().unique().tolist() if s and s.lower() != "nan"])
    for tema in TEMAS_CRS
}
SUBTEMA_TO_QIDS_CRS = {}
for tema in TEMAS_CRS:
    for sub in TEMA_TO_SUBTEMAS_CRS.get(tema, []):
        SUBTEMA_TO_QIDS_CRS[(tema, sub)] = (
            df[(df["Tema"] == tema) & (df["Subtema"] == sub)]["ID"].apply(_norm_qid).tolist()
        )


def get_question_by_id_crs(qid: str) -> dict | None:
    qid = _norm_qid(qid)
    return QUESTIONS_BY_ID_CRS.get(qid)


def get_correct_and_explanation_crs(qid: str) -> tuple[str, str]:
    qid = _norm_qid(qid)
    q = get_question_by_id_crs(qid)
    if not q:
        return "", ""
    correta = _extract_letter(q.get("Resposta Correta", ""))
    explicacao = str(q.get("Explicação", "") or "").strip()
    return correta, explicacao


def _subset_status_map(user_id: str, qids: list[str]) -> dict:
    all_map = get_question_status_map(str(user_id), source="CRS")
    qset = set(_norm_qid(x) for x in qids)
    return {qid: st for qid, st in all_map.items() if _norm_qid(qid) in qset}


def _count_acertos_erros(user_id: str, qids: list[str]) -> tuple[int, int]:
    sub = _subset_status_map(user_id, qids)
    acertos = sum(1 for v in sub.values() if v is True)
    erros = sum(1 for v in sub.values() if v is False)
    return acertos, erros


def _progress_icon(ok: int, total: int) -> str:
    if total <= 0:
        return "⚪"
    ratio = ok / total
    if ratio >= 1.0:
        return "✅"
    if ratio > 0.5:
        return "🟡"
    return "⚪"


LETRAS = ["A", "B", "C", "D"]


def _make_perm_no_repeat(user_id: str, qid: str) -> list[str]:
    qid = _norm_qid(qid)
    last_perm = get_last_perm_for_user_question(str(user_id), str(qid).strip(), source="CRS")
    last = [p.strip().upper() for p in last_perm.split(",")] if last_perm else []

    base = LETRAS[:]
    for _ in range(12):
        cand = base[:]
        random.shuffle(cand)
        if cand != last:
            return cand
    cand = base[:]
    random.shuffle(cand)
    return cand


def _apply_perm(q: dict, perm: list[str], correta_original: str):
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


# ==========================================================
# UI CRS
# ==========================================================
async def enviar_menu_crs(update, context):
    keyboard = [
        [InlineKeyboardButton("📌 SELECIONAR TEMA", callback_data="CRS|MENU|TEMA")],
        [InlineKeyboardButton("🎲 VARIADAS", callback_data="CRS|MENU|VAR")],
    ]
    await update.effective_chat.send_message(
        "🧭 *Questões CRS*\n\nEscolha o modo:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def enviar_temas_crs(update, context):
    user_id = str(update.effective_user.id)
    keyboard = []
    for tema in TEMAS_CRS:
        qids = TEMA_TO_QIDS_CRS.get(tema, [])
        total = len(qids)
        acertos, _ = _count_acertos_erros(user_id, qids)
        icon = _progress_icon(acertos, total)
        label = f"{tema}  |  {icon} {acertos}/{total}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"CRSTEMA|{tema}")])

    if getattr(update, "callback_query", None):
        await update.callback_query.edit_message_text(
            "📚 *Selecione o TEMA (CRS):*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    else:
        await update.effective_chat.send_message(
            "📚 *Selecione o TEMA (CRS):*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )


async def enviar_subtemas_crs(update, context, tema: str):
    user_id = str(update.effective_user.id)
    subs = TEMA_TO_SUBTEMAS_CRS.get(tema, [])
    keyboard = []

    if not subs:
        # Se não houver subtema preenchido, cai direto no tema
        await iniciar_quiz_crs(update, context, user_id=user_id, tema=tema, subtema=None, modo="TEMA", limite=20)
        return

    for sub in subs:
        qids = SUBTEMA_TO_QIDS_CRS.get((tema, sub), [])
        total = len(qids)
        acertos, _ = _count_acertos_erros(user_id, qids)
        icon = _progress_icon(acertos, total)
        label = f"{sub}  |  {icon} {acertos}/{total}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"CRSSUB|{sub}")])

    context.chat_data["tema_crs"] = tema
    await update.callback_query.edit_message_text(
        f"📘 *Tema CRS:* {tema}\n\n📂 Selecione o *SUBTEMA:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


def _priorizar_fila_crs(base_df: pd.DataFrame, user_id: str, limite: int):
    base = base_df.copy()
    base["ID"] = base["ID"].apply(_norm_qid)
    qids = base["ID"].tolist()

    status_map = get_question_status_map(str(user_id), source="CRS")
    last_map = get_last_answered_map(str(user_id), source="CRS")  # qid -> iso timestamp

    nao_resp_ids, erradas_ids, acertadas_ids = [], [], []
    for qid in qids:
        qn = _norm_qid(qid)
        st = status_map.get(qn)
        if st is None:
            nao_resp_ids.append(qn)
        elif st is False:
            erradas_ids.append(qn)
        else:
            acertadas_ids.append(qn)

    # 1) não respondidas aleatórias
    nao_resp = base[base["ID"].isin(nao_resp_ids)].sample(frac=1).to_dict("records") if nao_resp_ids else []

    # 2) erradas aleatórias
    erradas = base[base["ID"].isin(erradas_ids)].sample(frac=1).to_dict("records") if erradas_ids else []

    # 3) acertadas: mais antigas primeiro (quem ficou há mais tempo sem revisão)
    acertadas_df = base[base["ID"].isin(acertadas_ids)].copy()
    if not acertadas_df.empty:
        acertadas_df["__last"] = acertadas_df["ID"].map(lambda x: last_map.get(_norm_qid(x), "9999"))
        acertadas_df = acertadas_df.sort_values(by="__last", ascending=True)
        acertadas = acertadas_df.drop(columns=["__last"]).to_dict("records")
    else:
        acertadas = []

    return (nao_resp + erradas + acertadas)[:limite]


async def iniciar_quiz_crs(update, context, user_id: str, tema: str | None, subtema: str | None, modo: str, limite: int = 20):
    """
    modo:
      - "TEMA": filtra pelo tema (e opcionalmente subtema)
      - "VAR": variado (embaralha o dataframe todo com priorização)
    """
    base = df.copy()

    tema = (tema or "").strip()
    subtema = (subtema or "").strip()

    if modo == "TEMA":
        if tema:
            base = base[base["Tema"] == tema].copy()
        if subtema:
            base = base[base["Subtema"] == subtema].copy()

    if base.empty:
        await update.effective_chat.send_message("⚠️ Sem questões para esse filtro (CRS).")
        return

    fila_clean = _priorizar_fila_crs(base, str(user_id), limite)

    context.chat_data["quiz"] = {
        "user_id": str(user_id),
        "tema": tema if tema else "CRS (Variadas)",
        "subtema": subtema,
        "perguntas": fila_clean,
        "index": 0,
        "source": "CRS",
    }

    header = "🎯 *Quiz CRS iniciado*"
    if modo == "TEMA":
        if tema:
            header += f"\n📌 Tema: *{tema}*"
        if subtema:
            header += f"\n📂 Subtema: *{subtema}*"
    else:
        header += "\n🎲 *Modo:* Variadas"

    await update.effective_chat.send_message(header, parse_mode="Markdown")
    await enviar_proxima_crs(update, context)


async def enviar_proxima_crs(update, context):
    quiz = context.chat_data.get("quiz")
    if not quiz or quiz.get("source") != "CRS":
        return

    if quiz["index"] >= len(quiz["perguntas"]):
        await update.effective_chat.send_message("✅ Fim das questões desta sessão (CRS).")
        return

    q = quiz["perguntas"][quiz["index"]]
    quiz["index"] += 1

    qid = _norm_qid(q.get("ID", ""))
    user_id = str(quiz.get("user_id") or "")

    correta_original, _exp = get_correct_and_explanation_crs(qid)
    perm = _make_perm_no_repeat(user_id, qid)
    alternativas_exibidas, correta_exibida = _apply_perm(q, perm, correta_original)

    context.chat_data["correta_exibida"] = correta_exibida
    context.chat_data["qid_atual"] = qid
    context.chat_data["perm_atual"] = ",".join(perm)

    curso = str(q.get("CURSOS", "") or "").strip()
    materia = str(q.get("materia", "") or "").strip()
    tema_row = str(q.get("Tema", "") or "").strip()
    subtema_row = str(q.get("Subtema", "") or "").strip()

    cabecalho = []
    if curso:
        cabecalho.append(f"🎓 *Curso:* {curso}")
    if materia:
        cabecalho.append(f"📚 *Matéria:* {materia}")
    if tema_row and tema_row.lower() != "nan":
        cabecalho.append(f"📘 *Tema:* {tema_row}")
    if subtema_row and subtema_row.lower() != "nan":
        cabecalho.append(f"📂 *Subtema:* {subtema_row}")

    cab = "\n".join(cabecalho).strip()
    texto = (
        f"{cab}\n\n"
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
        parse_mode="Markdown",
    )

    try:
        record_sent_question(
            user_id=user_id,
            source="CRS",
            qid=qid,
            message_id=msg.message_id,
            correta_exibida=correta_exibida,
            perm=",".join(perm),
        )
    except Exception:
        pass

