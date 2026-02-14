# main.py
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
    normalize_qid,
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
        data = get_user_topic_breakdown_full(uid)

        linhas = [f"🏁 *Score detalhado* — user_id: `{uid}`", ""]
        linhas.append("📌 *Por Tema:*")
        por_tema = data.get("por_tema") or []
        if not por_tema:
            linhas.append("—")
        else:
            for r in por_tema[:30]:
                linhas.append(f"• *{r['tema']}* → {r['total']} (✅{r['acertos']} ❌{r['erros']}) | *{r['pct']:.1f}%*")

        linhas.append("")
        linhas.append("📌 *Por Tema/Subtema:*")
        det = data.get("por_tema_subtema") or []
        if not det:
            linhas.append("—")
        else:
            for r in det[:50]:
                linhas.append(
                    f"• *{r['tema']}* / _{r['subtema']}_ → {r['total']} (✅{r['acertos']} ❌{r['erros']}) | *{r['pct']:.1f}%*"
                )

        await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")
        return

    # lista usuários
    top = get_users_overall_scores(limit=20)
    linhas = ["🏆 *Ranking (Top 20 por respondidas)*", ""]
    if not top:
        linhas.append("—")
    else:
        for i, r in enumerate(top, 1):
            linhas.append(
                f"{i:02d}) `{r['user_id']}` → {r['respondidas']} (✅{r['acertos']} ❌{r['erros']}) | *{r['pct']:.1f}%*"
            )

    linhas.append("")
    linhas.append("Para detalhar: `/score <user_id>`")
    await update.message.reply_text("\n".join(linhas), parse_mode="Markdown")


async def zerar(update, context):
    teclado = [
        [
            InlineKeyboardButton("✅ SIM, zerar", callback_data="ZERAR|YES"),
            InlineKeyboardButton("❌ NÃO", callback_data="ZERAR|NO"),
        ]
    ]
    await update.message.reply_text(
        "⚠️ *Atenção:* isso vai apagar todas as suas estatísticas.\n\nConfirmar?",
        reply_markup=InlineKeyboardMarkup(teclado),
        parse_mode="Markdown",
    )


async def callback_handler(update, context):
    query = update.callback_query
    if not query:
        return

    data = str(query.data or "")
    user_id = str(update.effective_user.id)

    # ===== confirmação do /zerar =====
    if data.startswith("ZERAR|"):
        decision = data.split("|", 1)[1].strip().upper()

        if decision not in ("YES", "NO"):
            await query.answer("Opção inválida.", show_alert=True)
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
        qid = normalize_qid(qid_raw)

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

