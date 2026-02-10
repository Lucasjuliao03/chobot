import os
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from db import init_db, record_answer, get_overall_progress, get_topic_breakdown
from quiz import (
    enviar_temas,
    enviar_subtemas,
    iniciar_quiz,
    enviar_proxima,
    get_correct_and_explanation
)

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram")
PORT = int(os.getenv("PORT", 8443))

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
        "📌 *Por Tema/Subtema (top 20 por volume):*"
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

async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("TEMA|"):
        tema = data.split("|", 1)[1]
        context.chat_data["tema"] = tema
        await enviar_subtemas(update, context, tema)
        return

    if data.startswith("SUB|"):
        sub = data.split("|", 1)[1]
        tema = context.chat_data.get("tema")
        user_id = str(update.effective_user.id)
        await iniciar_quiz(update, context, user_id, tema, sub, limite=20)
        return

    if data.startswith("RESP|"):
        _, qid_raw, marcada = data.split("|", 2)
        qid = str(qid_raw).strip()
        user_id = str(update.effective_user.id)

        # pega correta e explicação pelo ID (lookup seguro)
        correta, explicacao = get_correct_and_explanation(qid)
        acertou = (marcada == correta)

        # determina tema/subtema atuais da sessão (pode ser vazio)
        sess = context.chat_data.get("quiz", {})
        tema = sess.get("tema", "")
        subtema = sess.get("subtema", "")

        # grava com tema/subtema
        record_answer(user_id, qid, acertou, marcada, tema, subtema)

        # remove botões da mensagem original
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except:
            pass

        cab = "✅ *Correto!*" if acertou else f"❌ *Errado.* Correta: *{correta or '—'}*"
        texto = f"{cab}\n\n📘 *Explicação:*\n{explicacao if explicacao else '—'}"

        # botão "Próxima questão" — só quando o usuário pedir
        teclado = [[InlineKeyboardButton("➡️ Próxima questão", callback_data="NEXTQ")]]

        await query.message.chat.send_message(
            texto,
            reply_markup=InlineKeyboardMarkup(teclado),
            parse_mode="Markdown"
        )
        return

    if data == "NEXTQ":
        # evita múltiplos cliques
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except:
            pass
        await enviar_proxima(update, context)
        return

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("progresso", progresso))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH.lstrip("/"),
        webhook_url=f"{WEBHOOK_URL}{WEBHOOK_PATH}",
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
