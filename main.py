import os
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from db_sheets import init_db, record_answer, get_overall_progress, get_topic_breakdown
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
            BotCommand("zerar", "Zerar suas estatísticas (com confirmação)"),
        ]
    )


async def start(update, context):
    await enviar_temas(update, context)


async def progresso(update, context):
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
    ]

    breakdown = get_topic_breakdown(user_id, limit=20)
    if not breakdown:
        linhas.append("—")
    else:
        for r in breakdown:
            linhas.append(
                f"• *{r['tema']}* / _{r['subtema']}_ → "
                f"{r['total']} (✅{r['acertos']} ❌{r['erros']}) | *{r['pct']:.1f}%*"
            )

    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")


async def zerar(update, context):
    """
    Pede confirmação antes de apagar.
    Callback carrega o user_id para impedir outro usuário confirmar em grupo.
    """
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

        # impede outro usuário clicar e apagar o de alguém em chat/grupo
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

            # limpa sessão atual, se existir
            context.chat_data.pop("quiz", None)
            context.chat_data.pop("tema", None)

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

        correta, explicacao = get_correct_and_explanation(qid)
        acertou = (marcada == correta)

        sess = context.chat_data.get("quiz", {})
        tema = sess.get("tema", "")
        subtema = sess.get("subtema", "")

        record_answer(user_id, qid, acertou, marcada, tema, subtema)

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        cab = "✅ *Correto!*" if acertou else f"❌ *Errado.* Correta: *{correta or '—'}*"
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


