import os
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

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
            BotCommand("start", "Iniciar o bot e escolher tema/subtema"),
            BotCommand("progresso", "Ver seu progresso por tema/subtema"),
            BotCommand("score", "Ranking e detalhamento por usuário (tema/subtema)"),
            BotCommand("zerar", "Zerar suas estatísticas (com confirmação)"),
        ]
    )


async def start(update, context):
    await enviar_temas(update, context)


async def progresso(update, context):
    """
    AJUSTE MÍNIMO: apenas layout do /progresso.
    - Mantém somatório geral como já vinha.
    - Agrupa Subtemas por Tema e padroniza com indent (estilo da imagem).
    - Não altera nenhuma outra função/fluxo.
    """
    user_id = str(update.effective_user.id)
    geral = get_overall_progress(user_id)
    total = geral["acertos"] + geral["erros"]

    linhas = [
        "📊 *Progresso Geral*",
        "",
        f"Respondidas: *{total}*",
        f"✅ Acertos: *{geral['acertos']}*",
        f"❌ Erros: *{geral['erros']}*",
        f"🎯 Aproveitamento: *{geral['pct']:.1f}%*",
        "",
        "📌 *Por Tema/Subtema (top 20 por volume):*",
        "",
    ]

    breakdown = get_topic_breakdown(user_id, limit=20)
    if not breakdown:
        linhas.append("—")
        await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")
        return

    # ===== agrupar por tema =====
    grouped = {}
    order_tema = []  # preserva ordem de aparição (já vem por total desc)
    for r in breakdown:
        tema = (r.get("tema") or "—").strip() or "—"
        if tema not in grouped:
            grouped[tema] = []
            order_tema.append(tema)
        grouped[tema].append(r)

    # ===== imprimir com somatório por TEMA e sublinhas por SUBTEMA =====
    for tema in order_tema:
        items = grouped.get(tema, [])

        t_total = sum(int(x.get("total") or 0) for x in items)
        t_acertos = sum(int(x.get("acertos") or 0) for x in items)
        t_erros = sum(int(x.get("erros") or 0) for x in items)
        t_pct = (t_acertos / t_total * 100.0) if t_total else 0.0

        # Linha do TEMA (somatório do que aparece no top20)
        linhas.append(
            f"*{tema}* (*{t_pct:.1f}%*) — *{t_acertos}/{t_total}* (✅{t_acertos}/❌{t_erros})"
        )

        # Subtemas do tema (mantém a ordem do breakdown)
        for r in items:
            sub = (r.get("subtema") or "—").strip() or "—"
            linhas.append(
                f"↳ _{sub}_ (*{r['pct']:.1f}%*) — {r['acertos']}/{r['total']} (✅{r['acertos']}/❌{r['erros']})"
            )

        linhas.append("")  # espaçamento entre temas

    await update.message.reply_text("\n".join(linhas).rstrip(), parse_mode="Markdown")


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
        geral = get_overall_progress(uid)
        total = geral["acertos"] + geral["erros"]

        blob = get_user_topic_breakdown_full(uid)
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
    scores = get_users_overall_scores(limit=20)

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

    for i, s in enumerate(scores, start=1):
        linhas.append(
            f"{i:02d}. `{s['user_id']}` → *{s['respondidas']}* "
            f"(✅{s['acertos']} ❌{s['erros']}) | *{s['pct']:.1f}%*"
        )

    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")


async def zerar(update, context):
    user_id = str(update.effective_user.id)

    teclado = InlineKeyboardMarkup([[  # noqa
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

            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

            await query.message.reply_text("🧹 Estatísticas zeradas com sucesso. Use /start para recomeçar.")
            return

    # ===== fluxo normal =====
    if data.startswith("TEMA|"):
        tema = data.split("|", 1)[1]
        context.chat_data["tema"] = tema
        await enviar_subtemas(update, context, tema)
        return

    if data.startswith("SUB|"):
        sub = data.split("|", 1)[1]
        tema = context.chat_data.get("tema")
        await iniciar_quiz(update, context, user_id, tema, sub, limite=20)
        return

    if data.startswith("RESP|"):
        _, qid_raw, marcada = data.split("|", 2)
        qid = str(qid_raw).strip()

        message_id = getattr(query.message, "message_id", None)
        correta_exibida = ""
        if message_id is not None:
            try:
                correta_exibida = get_sent_correct(user_id, qid, message_id)
            except Exception:
                correta_exibida = ""

        if not correta_exibida:
            correta_exibida = str(context.chat_data.get("correta_exibida", "")).strip().upper()

        correta_original, explicacao = get_correct_and_explanation(qid)

        if correta_exibida:
            acertou = (marcada == correta_exibida)
        else:
            acertou = (marcada == correta_original)

        sess = context.chat_data.get("quiz", {})
        tema = sess.get("tema", "")
        subtema = sess.get("subtema", "")

        record_answer(user_id, qid, acertou, marcada, tema, subtema)

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        cab = "✅ *Correto!*" if acertou else f"❌ *Errado.* Correta: *{correta_exibida or correta_original or '—'}*"
        texto = f"{cab}\n\n📘 *Explicação:*\n{explicacao if explicacao else '—'}"

        teclado = [[InlineKeyboardButton("➡️ Próxima questão", callback_data="NEXTQ")]]

        await query.message.chat.send_message(
            texto,
            reply_markup=InlineKeyboardMarkup(teclado),
            parse_mode="Markdown",
        )
        return

    if data == "NEXTQ":
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await enviar_proxima(update, context)
        return


def main():
    init_db()

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(setup_commands)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("progresso", progresso))
    app.add_handler(CommandHandler("score", score))
    app.add_handler(CommandHandler("zerar", zerar))
    app.add_handler(CallbackQueryHandler(callback_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH.lstrip("/"),
        webhook_url=f"{WEBHOOK_URL}{WEBHOOK_PATH}",
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()

