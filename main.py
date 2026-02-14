import os
import re
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


def _norm_qid(x) -> str:
    s = str(x).strip()
    # Converte "123.0" -> "123" (Excel costuma vir como float)
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".", 1)[0]
    return s


TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram")
PORT = int(os.getenv("PORT", "10000"))

if not TOKEN:
    raise RuntimeError("BOT_TOKEN não definido.")

# init db
init_db()


async def cmd_start(update, context):
    await enviar_temas(update, context, str(update.effective_user.id))


async def cmd_temas(update, context):
    await enviar_temas(update, context, str(update.effective_user.id))


async def cmd_stats(update, context):
    user_id = str(update.effective_user.id)
    prog = get_overall_progress(user_id)
    breakdown = get_topic_breakdown(user_id)

    linhas = [
        "*📊 Estatísticas*",
        "",
        f"Total respondidas: *{prog['total']}*",
        f"Acertos: *{prog['acertos']}*",
        f"Erros: *{prog['erros']}*",
        f"Aproveitamento: *{prog['pct']}%*",
        "",
        "*Por Tema/Subtema:*",
    ]

    if not breakdown:
        linhas.append("_Sem registros ainda._")
    else:
        for row in breakdown:
            tema = row["tema"] or "-"
            sub = row["subtema"] or "-"
            linhas.append(f"- {tema} / {sub}: *{row['acertos']}* de *{row['total']}* ({row['pct']}%)")

    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")


async def cmd_reset(update, context):
    user_id = str(update.effective_user.id)
    teclado = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Confirmar RESET", callback_data="RST|DO")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="RST|NO")],
        ]
    )
    await update.message.reply_text(
        "⚠️ Tem certeza que deseja *zerar* suas estatísticas?",
        reply_markup=teclado,
        parse_mode="Markdown",
    )


async def cmd_score(update, context):
    """
    Ranking geral simples por pct.
    """
    ranking = get_users_overall_scores(limit=10)
    if not ranking:
        await update.message.reply_text("Sem dados ainda.")
        return

    linhas = ["*🏆 Ranking Geral (Top 10)*", ""]
    for i, r in enumerate(ranking, start=1):
        uid = r["user_id"]
        linhas.append(f"{i:02d}) `{uid}` — *{r['pct']}%* (A:{r['acertos']}/T:{r['total']})")

    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")


async def cmd_score_full(update, context):
    """
    Ranking detalhado por tema/subtema (admin/debug).
    """
    rows = get_user_topic_breakdown_full()
    if not rows:
        await update.message.reply_text("Sem dados.")
        return

    # corta pra não estourar mensagem
    rows = rows[:50]
    linhas = ["*📌 Breakdown (amostra)*", ""]
    for r in rows:
        linhas.append(
            f"`{r['user_id']}` — {r['tema']}/{r['subtema']}: *{r['pct']}%* (A:{r['acertos']}/T:{r['total']})"
        )

    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")


async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = str(update.effective_user.id)

    # ===== confirmação de reset =====
    if data.startswith("RST|"):
        action = data.split("|", 1)[1]
        if action == "ASK":
            teclado = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Confirmar RESET", callback_data="RST|DO")],
                    [InlineKeyboardButton("❌ Cancelar", callback_data="RST|NO")],
                ]
            )
            await query.edit_message_text(
                "⚠️ Tem certeza que deseja *zerar* suas estatísticas?",
                reply_markup=teclado,
                parse_mode="Markdown",
            )
            return

        if action == "DO":
            reset_user_stats(user_id)
            await query.edit_message_text("✅ Estatísticas zeradas.")
            return

        if action == "NO":
            await query.edit_message_text("Cancelado.")
            return

    # ===== estatísticas =====
    if data.startswith("STATS|"):
        prog = get_overall_progress(user_id)
        breakdown = get_topic_breakdown(user_id)

        linhas = [
            "*📊 Estatísticas*",
            "",
            f"Total respondidas: *{prog['total']}*",
            f"Acertos: *{prog['acertos']}*",
            f"Erros: *{prog['erros']}*",
            f"Aproveitamento: *{prog['pct']}%*",
            "",
            "*Por Tema/Subtema:*",
        ]

        if not breakdown:
            linhas.append("_Sem registros ainda._")
        else:
            for row in breakdown:
                tema = row["tema"] or "-"
                sub = row["subtema"] or "-"
                linhas.append(f"- {tema} / {sub}: *{row['acertos']}* de *{row['total']}* ({row['pct']}%)")

        await query.edit_message_text("\n".join(linhas), parse_mode="Markdown")
        return

    # ===== navegação =====
    if data.startswith("BACK|"):
        back_to = data.split("|", 1)[1]
        if back_to == "TEMAS":
            # volta para lista de temas
            # precisamos mandar uma nova msg ou editar: vamos editar a msg atual com temas
            # hack: simula um update.message usando query.message
            class _Tmp:
                message = query.message

            tmp_update = _Tmp()
            await enviar_temas(tmp_update, context, user_id)
            return

    if data.startswith("TEMA|"):
        tema = data.split("|", 1)[1]
        context.chat_data["tema"] = tema
        await enviar_subtemas(update, context, user_id, tema)
        return

    if data.startswith("SUB|"):
        sub = data.split("|", 1)[1]
        tema = context.chat_data.get("tema")
        await iniciar_quiz(update, context, user_id, tema, sub, limite=20)
        return

    if data.startswith("RESP|"):
        _, qid_raw, marcada = data.split("|", 2)
        qid = _norm_qid(qid_raw)

        message_id = getattr(query.message, "message_id", None)
        correta_exibida = ""
        if message_id is not None:
            try:
                correta_exibida = get_sent_correct(user_id, qid, message_id)
            except Exception:
                correta_exibida = ""

        correta, explic = get_correct_and_explanation(qid)
        acertou = (marcada.strip().upper() == str(correta).strip().upper())

        tema = context.chat_data.get("tema") or ""
        sub = context.chat_data.get("subtema") or ""

        record_answer(user_id, qid, acertou, marcada, tema, sub)

        # feedback
        if acertou:
            txt = f"✅ *Correto!* ({marcada})"
        else:
            txt = f"❌ *Errado.* Você marcou {marcada}. Correta: *{correta}*"

        if explic:
            txt += f"\n\n_{explic}_"

        # incrementa índice e envia próxima
        context.chat_data["idx"] = int(context.chat_data.get("idx") or 0) + 1

        await query.edit_message_text(txt, parse_mode="Markdown")
        # envia próxima como nova mensagem (pra não sobrescrever feedback)
        await enviar_proxima(update, context, user_id, via_edit=False)
        return


async def set_commands(app: Application):
    cmds = [
        BotCommand("start", "Iniciar"),
        BotCommand("temas", "Listar temas"),
        BotCommand("stats", "Ver estatísticas"),
        BotCommand("reset", "Zerar estatísticas"),
        BotCommand("score", "Ranking geral"),
    ]
    await app.bot.set_my_commands(cmds)


def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("temas", cmd_temas))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("reset", cmd_reset))
    application.add_handler(CommandHandler("score", cmd_score))
    application.add_handler(CommandHandler("scorefull", cmd_score_full))
    application.add_handler(CallbackQueryHandler(callback_handler))

    application.post_init = set_commands

    # WEBHOOK (Render)
    if WEBHOOK_URL:
        # WEBHOOK_URL deve ser o domínio base, sem path duplicado
        full_url = WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_PATH.strip("/"),
            webhook_url=full_url,
        )
    else:
        application.run_polling()


if __name__ == "__main__":
    main()

