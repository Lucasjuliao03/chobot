import os
import asyncio
import json
from dotenv import load_dotenv

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from aiohttp import web

from db_turso import (
    init_db,
    record_answer,
    get_overall_progress,
    get_topic_breakdown,
    reset_user_stats,
    get_sent_correct,
    # 🔥 novos para /score
    get_users_overall_scores,
    get_user_topic_breakdown_full,
)

from quiz import (
    enviar_temas,
    enviar_subtemas,
    iniciar_quiz,
    enviar_proxima,
    get_correct_and_explanation,
    # ✅ NOVO: totais do banco de questões (Excel)
    TEMA_TO_QIDS,
    SUBTEMA_TO_QIDS,
)


from quiz_crs import (
    enviar_menu_crs,
    enviar_temas_crs,
    enviar_subtemas_crs,
    iniciar_quiz_crs,
    enviar_proxima_crs,
    get_correct_and_explanation_crs,
    TEMA_TO_QIDS_CRS,
)

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram")
PORT = int(os.getenv("PORT", "10000"))

if not TOKEN:
    raise RuntimeError("BOT_TOKEN não definido nas variáveis de ambiente.")
if not WEBHOOK_URL:
    raise RuntimeError("WEBHOOK_URL não definido nas variáveis de ambiente.")

if not WEBHOOK_PATH.startswith("/"):
    WEBHOOK_PATH = "/" + WEBHOOK_PATH
WEBHOOK_URL = WEBHOOK_URL.rstrip("/")


async def setup_commands(app: Application):
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Iniciar e escolher Questões Geradas ou CRS"),
            BotCommand("progresso", "Ver seu progresso (Questões Geradas)"),
            BotCommand("progresso_crs", "Ver seu progresso (Questões CRS)"),
            BotCommand("score", "Ranking/Detalhamento (Questões Geradas)"),
            BotCommand("score_crs", "Ranking/Detalhamento (Questões CRS)"),
            BotCommand("zerar", "Zerar suas estatísticas (com confirmação)"),
        ]
    )


async def start(update, context):
    # menu inicial: escolher banco de questões
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧩 Questões Geradas", callback_data="MODE|GEN")],
        [InlineKeyboardButton("🎯 Questões CRS", callback_data="MODE|CRS")],
    ])

    # limpa sessão anterior
    context.chat_data.pop("quiz", None)
    context.chat_data.pop("tema", None)
    context.chat_data.pop("correta_exibida", None)
    context.chat_data.pop("qid_atual", None)
    context.chat_data.pop("perm_atual", None)
    context.chat_data.pop("pending_explain", None)

    await update.message.reply_text(
        "Escolha o banco de questões:",
        reply_markup=teclado
    )


async def progresso(update, context):
    """
    AJUSTE MÍNIMO:
    - Mantém somatório geral.
    - Para TEMA e SUBTEMA:
        Linha principal vira RESPONDIDAS / TOTAL_NO_BANCO (Excel)
        E dentro: ✅acertos/❌erros (do total respondido).
    - Agrupamento por TEMA mantido.
    """
    user_id = str(update.effective_user.id)
    geral = get_overall_progress(user_id, source="GEN")
    total_respondidas_geral = geral["acertos"] + geral["erros"]

    linhas = [
        "📊 *Progresso Geral*",
        "",
        f"Respondidas: *{total_respondidas_geral}*",
        f"✅ Acertos: *{geral['acertos']}*",
        f"❌ Erros: *{geral['erros']}*",
        f"🎯 Aproveitamento: *{geral['pct']:.1f}%*",
        "",
        "📌 *Por Tema/Subtema (top 20 por volume):*",
        "",
    ]

    breakdown = get_topic_breakdown(user_id, limit=20, source="GEN")
    if not breakdown:
        linhas.append("—")
        await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")
        return

    # ===== agrupar por tema =====
    grouped = {}
    order_tema = []
    for r in breakdown:
        tema = (r.get("tema") or "—").strip() or "—"
        if tema not in grouped:
            grouped[tema] = []
            order_tema.append(tema)
        grouped[tema].append(r)

    # ===== imprimir com total do BANCO (Excel) =====
    for tema in order_tema:
        items = grouped.get(tema, [])

        # respondidas no banco (somatório do top20 daquele tema)
        resp_total = sum(int(x.get("total") or 0) for x in items)
        resp_acertos = sum(int(x.get("acertos") or 0) for x in items)
        resp_erros = sum(int(x.get("erros") or 0) for x in items)

        # total de questões no banco (Excel) para o TEMA
        bank_total = len(TEMA_TO_QIDS.get(tema, []))

        # % de conclusão (respondidas / total do banco)
        pct_conclusao = (resp_total / bank_total * 100.0) if bank_total else 0.0

        # % de aproveitamento (acertos / respondidas)
        pct_aprov = (resp_acertos / resp_total * 100.0) if resp_total else 0.0

        # Linha do TEMA: RESPONDIDAS / BANCO  +  (✅/❌ do RESPONDIDO)
        linhas.append(
            f"*{tema}* ({pct_conclusao:.1f}%) — *{resp_total}/{bank_total}* (✅{resp_acertos}/❌{resp_erros}) | *{pct_aprov:.1f}%*"
        )

        # Subtemas do tema
        for r in items:
            sub = (r.get("subtema") or "—").strip() or "—"
            s_total = int(r.get("total") or 0)      # respondidas
            s_acertos = int(r.get("acertos") or 0)  # acertos
            s_erros = int(r.get("erros") or 0)      # erros

            bank_total_sub = len(SUBTEMA_TO_QIDS.get((tema, sub), []))
            pct_conc_sub = (s_total / bank_total_sub * 100.0) if bank_total_sub else 0.0
            pct_aprov_sub = (s_acertos / s_total * 100.0) if s_total else 0.0

            linhas.append(
                f"↳ _{sub}_ ({pct_conc_sub:.1f}%) — {s_total}/{bank_total_sub} (✅{s_acertos}/❌{s_erros}) | *{pct_aprov_sub:.1f}%*"
            )

        linhas.append("")

    await update.message.reply_text("\n".join(linhas).rstrip(), parse_mode="Markdown")



async def progresso_crs(update, context):
    """Progresso exclusivo do módulo CRS (não mistura com Geradas)."""
    user_id = str(update.effective_user.id)

    geral = get_overall_progress(user_id, source="CRS")
    total_respondidas_geral = geral["acertos"] + geral["erros"]

    linhas = [
        "📊 *Progresso Geral (CRS)*",
        "",
        f"Respondidas: *{total_respondidas_geral}*",
        f"✅ Acertos: *{geral['acertos']}*",
        f"❌ Erros: *{geral['erros']}*",
        f"🎯 Aproveitamento: *{geral['pct']:.1f}%*",
        "",
        "📌 *Por Tema (top 20 por volume):*",
        "",
    ]

    breakdown = get_topic_breakdown(user_id, limit=20, source="CRS")
    if not breakdown:
        linhas.append("—")
    else:
        # agrupa por TEMA somando subtemas
        agg = {}
        for row in breakdown:
            tema = (row.get("tema") or "").strip() or "—"
            a = int(row.get("acertos") or 0)
            e = int(row.get("erros") or 0)
            t = a + e
            if tema not in agg:
                agg[tema] = {"acertos": 0, "erros": 0, "total": 0}
            agg[tema]["acertos"] += a
            agg[tema]["erros"] += e
            agg[tema]["total"] += t

        # ordena por volume respondido
        items = sorted(agg.items(), key=lambda kv: kv[1]["total"], reverse=True)[:20]
        for tema, d in items:
            total_banco = len(TEMA_TO_QIDS_CRS.get(tema, [])) if tema in TEMA_TO_QIDS_CRS else 0
            resp = d["total"]
            a = d["acertos"]
            e = d["erros"]
            pct = (a / resp * 100.0) if resp else 0.0

            if total_banco > 0:
                linhas.append(f"• *{tema}*: *{resp}/{total_banco}*  (✅{a} / ❌{e} — *{pct:.1f}%*)")
            else:
                linhas.append(f"• *{tema}*: *{resp}*  (✅{a} / ❌{e} — *{pct:.1f}%*)")

    await update.effective_chat.send_message("\n".join(linhas), parse_mode="Markdown")


async def score(update, context):
    """
    /score
      - sem args: lista usuários (top 20 por respondidas)
      - com args: /score <user_id> => detalha por tema e por tema/subtema
    """
    args = getattr(context, "args", []) or []

    # detalhe: /score <user_id>
    if args:
        uid = str(args[0]).strip()
        geral = get_overall_progress(uid, source="GEN")
        total = geral["acertos"] + geral["erros"]

        blob = get_user_topic_breakdown_full(uid, source="GEN")
        temas = blob["temas"]
        tema_sub = blob["tema_subtema"]

        linhas = [
            f"👤 *SCORE do usuário:* `{uid}`",
            "",
            f"Respondidas: *{total}*",
            f"✅ Acertos: *{geral['acertos']}*",
            f"❌ Erros: *{geral['erros']}*",
            f"🎯 Aproveitamento: *{geral['pct']:.1f}%*",
            "",
            "📌 *Por TEMA (top 15 por volume):*",
        ]

        if not temas:
            linhas.append("—")
        else:
            for t in temas[:15]:
                linhas.append(
                    f"• *{t['tema'] or '—'}* → {t['total']} (✅{t['acertos']} ❌{t['erros']}) | *{t['pct']:.1f}%*"
                )

        linhas.append("")
        linhas.append("📌 *Por TEMA / SUBTEMA (top 30 por volume):*")

        if not tema_sub:
            linhas.append("—")
        else:
            for r in tema_sub[:30]:
                linhas.append(
                    f"• *{r['tema'] or '—'}* / _{r['subtema'] or '—'}_ → "
                    f"{r['total']} (✅{r['acertos']} ❌{r['erros']}) | *{r['pct']:.1f}%*"
                )

        await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")
        return

    # lista geral: /score
    scores = get_users_overall_scores(limit=20, source="GEN")

    linhas = [
        "🏆 *SCORE (Top 20 por respondidas)*",
        "",
        "_Use_ `/score <user_id>` _para ver por TEMA e SUBTEMA._",
        "",
    ]

    if not scores:
        linhas.append("— sem dados ainda —")
        await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")
        return

    # monta ranking
    for i, s in enumerate(scores, start=1):
        uid = s["user_id"]
        total = s["respondidas"]
        ac = s["acertos"]
        er = s["erros"]
        pct = s["pct"]
        linhas.append(f"{i:02d}) `{uid}` — *{total}* (✅{ac} ❌{er}) | *{pct:.1f}%*")

    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")



async def score_crs(update, context):
    args = getattr(context, "args", []) or []

    if args:
        uid = str(args[0]).strip()
        geral = get_overall_progress(uid, source="CRS")
        total = geral["acertos"] + geral["erros"]
        blob = get_user_topic_breakdown_full(uid, source="CRS")
        temas = blob["temas"]
        tema_sub = blob["tema_subtema"]

        linhas = [
            f"👤 *SCORE CRS do usuário:* `{uid}`",
            "",
            f"Respondidas: *{total}*",
            f"✅ Acertos: *{geral['acertos']}*",
            f"❌ Erros: *{geral['erros']}*",
            f"🎯 Aproveitamento: *{geral['pct']:.1f}%*",
            "",
            "📌 *Por TEMA (top 15 por volume):*",
        ]

        if not temas:
            linhas.append("—")
        else:
            for t in temas[:15]:
                linhas.append(f"• *{t['tema'] or '—'}* → {t['total']} (✅{t['acertos']} ❌{t['erros']}) | *{t['pct']:.1f}%*")

        linhas.append("")
        linhas.append("📌 *Por TEMA / SUBTEMA (top 30 por volume):*")
        linhas.append("")

        if not tema_sub:
            linhas.append("—")
        else:
            for r in tema_sub[:30]:
                linhas.append(
                    f"• *{r['tema'] or '—'}* / _{r['subtema'] or '—'}_ → {r['total']} (✅{r['acertos']} ❌{r['erros']}) | *{r['pct']:.1f}%*"
                )

        await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")
        return

    scores = get_users_overall_scores(limit=20, source="CRS")

    linhas = [
        "🏆 *SCORE CRS (Top 20 por respondidas)*",
        "",
        "_Use_ `/score_crs <user_id>` _para ver por TEMA e SUBTEMA._",
        "",
    ]

    if not scores:
        linhas.append("— sem dados ainda —")
        await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")
        return

    for i, s in enumerate(scores, start=1):
        linhas.append(f"{i:02d}. `{s['user_id']}` → *{s['respondidas']}* (✅{s['acertos']} ❌{s['erros']}) | *{s['pct']:.1f}%*")

    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")


async def zerar(update, context):
    user_id = str(update.effective_user.id)

    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirmar zerar", callback_data=f"RST|YES|{user_id}"),
        InlineKeyboardButton("❌ Cancelar", callback_data=f"RST|NO|{user_id}"),
    ]])

    await update.message.reply_text(
        "⚠️ *ATENÇÃO*\n\nIsso vai apagar *todas* as suas estatísticas (geral, por tema/subtema e por questão).\n\nConfirma?",
        reply_markup=teclado,
        parse_mode="Markdown",
    )


async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = str(update.effective_user.id)

    # ===== confirmação de reset =====
    if data.startswith("RST|"):
        _, decision, owner_id = data.split("|", 2)

        if owner_id != user_id:
            await query.answer("Este comando não é seu.", show_alert=True)
            return

        if decision == "NO":
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            await query.message.reply_text("✅ Cancelado. Nenhuma estatística foi alterada.")
            return

        if decision == "YES":
            reset_user_stats(user_id)

            context.chat_data.pop("quiz", None)
            context.chat_data.pop("tema", None)
            context.chat_data.pop("correta_exibida", None)
            context.chat_data.pop("qid_atual", None)
            context.chat_data.pop("perm_atual", None)
	    context.chat_data.pop("pending_explain", None)

            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

            await query.message.reply_text("🧹 Estatísticas zeradas com sucesso. Use /start para recomeçar.")
            return


    # ===== seleção do banco de questões =====
    if data == "MODE|GEN":
        # Questões Geradas (fluxo atual)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await enviar_temas(update, context)
        return

    if data == "MODE|CRS":
        # Questões CRS (novo fluxo)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await enviar_menu_crs(update, context)
        return

    # ===== menu CRS =====
    if data == "CRS|MENU|TEMA":
        await enviar_temas_crs(update, context)
        return

    if data == "CRS|MENU|VAR":
        await iniciar_quiz_crs(update, context, user_id=user_id, tema=None, subtema=None, modo="VAR", limite=20)
        return

    if data.startswith("CRSTEMA|"):
        tema_crs = data.split("|", 1)[1]
        context.chat_data["tema_crs"] = tema_crs
        await enviar_subtemas_crs(update, context, tema_crs)
        return

    if data.startswith("CRSSUBI|"):
        token = data.split("|", 1)[1] if "|" in data else ""
        tema_crs = str(context.chat_data.get("crs_subtema_tema", "") or "").strip()
        sub_map = context.chat_data.get("crs_subtema_map", {}) or {}
        sub_crs = str(sub_map.get(token, "") or "").strip()

        if not tema_crs or not sub_crs:
            await update.effective_chat.send_message("⚠️ Subtema CRS inválido/expirado. Use /start e tente novamente.")
            return

        await iniciar_quiz_crs(update, context, user_id=user_id, tema=tema_crs, subtema=sub_crs, modo="TEMA", limite=20)
        return

    if data.startswith("CRSSUB|"):
        parts = data.split("|", 2)
        if len(parts) == 3:
            _, tema_crs, sub_crs = parts
        else:
            sub_crs = parts[1] if len(parts) > 1 else ""
            tema_crs = str(context.chat_data.get("tema_crs", "") or "").strip()
        await iniciar_quiz_crs(update, context, user_id=user_id, tema=tema_crs, subtema=sub_crs, modo="TEMA", limite=20)
        return

    # ===== fluxo normal =====
    if data.startswith("TEMA|"):
        tema = data.split("|", 1)[1]
        context.chat_data["tema"] = tema
        await enviar_subtemas(update, context, tema)
        return

    if data.startswith("SUBI|"):
        # SUBTEMA tokenizado (evita estourar 64 bytes no callback_data)
        token = data.split("|", 1)[1] if "|" in data else ""
        tema = str(context.chat_data.get("subtema_tema", "") or "").strip()
        sub_map = context.chat_data.get("subtema_map", {}) or {}
        sub = str(sub_map.get(token, "") or "").strip()

        if not tema or not sub:
            await update.effective_chat.send_message("⚠️ Subtema inválido/expirado. Use /start e tente novamente.")
            return

        await iniciar_quiz(update, context, user_id, tema, sub, limite=20)
        return

    if data.startswith("SUB|"):
        parts = data.split("|", 2)
        if len(parts) == 3:
            _, tema, sub = parts
        else:
            sub = parts[1] if len(parts) > 1 else ""
            tema = context.chat_data.get("tema")
        await iniciar_quiz(update, context, user_id, tema, sub, limite=20)
        return

    if data.startswith("RESP|"):
        _, qid_raw, marcada = data.split("|", 2)
        qid = str(qid_raw).strip()

        sess = context.chat_data.get("quiz", {}) or {}
        source = str(sess.get("source") or "GEN").upper()

        message_id = getattr(query.message, "message_id", None)
        correta_exibida = ""
        if message_id is not None:
            try:
                correta_exibida = get_sent_correct(user_id, qid, message_id, source=source)
            except Exception:
                correta_exibida = ""

        if not correta_exibida:
            correta_exibida = str(context.chat_data.get("correta_exibida", "")).strip().upper()

        if source == "CRS":
            correta_original, explicacao = get_correct_and_explanation_crs(qid)
        else:
            correta_original, explicacao = get_correct_and_explanation(qid)


        if correta_exibida:
            acertou = (marcada == correta_exibida)
        else:
            acertou = (marcada == correta_original)

        tema = sess.get("tema", "")
        subtema = sess.get("subtema", "")

        record_answer(user_id, qid, acertou, marcada, tema, subtema, source=source)

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        # ====== NOVA LÓGICA: feedback + botões Próxima / Explicação ======
        context.chat_data["pending_explain"] = {
            "qid": qid,
            "source": source,
            "correta": (correta_exibida or correta_original or "—"),
            "acertou": bool(acertou),
            "texto": (explicacao or "—"),
        }

        cab = "✅ *Correto!*" if acertou else f"❌ *Errado.* Correta: *{correta_exibida or correta_original or '—'}*"

        teclado = [[
            InlineKeyboardButton("➡️ Próxima questão", callback_data="NEXTQ"),
            InlineKeyboardButton("📖 Explicação", callback_data="SHOWEXPL"),
        ]]

        await query.message.chat.send_message(
            cab,
            reply_markup=InlineKeyboardMarkup(teclado),
            parse_mode="Markdown",
        )
        return


    if data == "SHOWEXPL":
        pending = context.chat_data.get("pending_explain") or {}
        texto = str(pending.get("texto") or "").strip()
        correta = str(pending.get("correta") or "—").strip()

        if not texto:
            await query.message.chat.send_message("⚠️ Explicação indisponível. Responda uma questão primeiro.")
            return

        msg = f"📘 *Explicação* (correta: *{correta}*)\n\n{texto}"

        teclado = [[InlineKeyboardButton("➡️ Próxima questão", callback_data="NEXTQ")]]

        await query.message.chat.send_message(
            msg,
            reply_markup=InlineKeyboardMarkup(teclado),
            parse_mode="Markdown",
        )
        return

    if data == "NEXTQ":
        context.chat_data.pop("pending_explain", None)

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        sess = context.chat_data.get("quiz", {}) or {}
        if str(sess.get("source") or "").upper() == "CRS":
            await enviar_proxima_crs(update, context)
        else:
            await enviar_proxima(update, context)

        return


async def _run():
    init_db()

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(setup_commands)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("progresso", progresso))
    application.add_handler(CommandHandler("progresso_crs", progresso_crs))
    application.add_handler(CommandHandler("score", score))
    application.add_handler(CommandHandler("score_crs", score_crs))
    application.add_handler(CommandHandler("zerar", zerar))
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Inicializa e inicia o PTB
    await application.initialize()
    await application.start()

    # Registra webhook no Telegram
    full_webhook = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
    await application.bot.set_webhook(url=full_webhook, drop_pending_updates=True)

    # ===== Servidor HTTP (aiohttp) =====
    aio = web.Application()

    async def root(_request):
        return web.Response(text="OK", status=200)

    async def health(_request):
        return web.json_response({"ok": True, "service": "chobot"}, status=200)

    async def telegram_webhook(request: web.Request):
        # Telegram envia JSON
        try:
            data = await request.json()
        except Exception:
            return web.Response(text="invalid json", status=400)

        update = Update.de_json(data, application.bot)
        # Joga na fila do PTB (processamento assíncrono padrão)
        await application.update_queue.put(update)
        return web.Response(text="ok", status=200)

    aio.router.add_get("/", root)
    aio.router.add_get("/health", health)
    aio.router.add_post(WEBHOOK_PATH, telegram_webhook)

    runner = web.AppRunner(aio)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()

    # Mantém rodando
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    finally:
        # Shutdown limpo
        await runner.cleanup()
        await application.stop()
        await application.shutdown()


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()