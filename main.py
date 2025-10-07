import os
import logging
from typing import Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from openai import OpenAI

# ====== 1) Read your secrets from environment variables (we set them later on Railway) ======
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# ====== 2) Basic logging so you can see what's happening in Railway logs ======
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("pm-tutor")

# ====== 3) OpenAI client (used for answers, quizzes) ======
client = None
if OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        log.error("Failed to initialize OpenAI client: %s", e)

# ====== 4) The tutor’s “persona” (what it knows + how it speaks) ======
SYSTEM_PROMPT = """You are a Project Management tutor for practitioners preparing for real projects and certifications (PMBOK® Guide, PRINCE2®, ISO 21502).
Goals: give concise, step-by-step answers; show small checklists, tables, and examples; and cite sources (standard + section/clause) when you rely on them.
Scope you cover:
• PM life cycle and delivery models (predictive, agile, hybrid)
• Governance, roles, tailoring, business case & benefits
• Planning: scope/WBS, schedule, cost, risk, quality, resources, procurement, comms, stakeholders
• Delivery: change control, baselines, reporting, Earned Value (EVM)
• Agile: Scrum events/artifacts, Kanban flow, metrics
Rules:
• If a question needs company-specific policy, ask for it, then offer a generic best-practice fallback.
• Prefer structured outputs (bullets, short tables). Show calculations for EVM and scheduling.
• Be factual; when citing a standard, include a short pinpoint (e.g., “PMBOK 7, Tailoring; ISO 21502:2020 §6.4”).
• If out of scope, say so and suggest a close topic you do cover.
"""

# ====== 5) Small built-in “lesson cards” (so the bot is useful before any custom data) ======
LESSON_CARDS: Dict[str, str] = {
    "Foundations": (
        "Foundations (Governance & Tailoring)\n"
        "• Purpose & value: from idea → benefits realization\n"
        "• Key roles: sponsor, PM, steering committee, team leads\n"
        "• Governance: decision rights, escalation path\n"
        "• Tailoring: choose predictive/agile/hybrid based on uncertainty, compliance, team maturity\n"
        "Checklist: charter, governance map, benefits profile, constraints/assumptions\n"
        "Sources: PMBOK7 – Principles; ISO 21502:2020 §5–6"
    ),
    "Planning": (
        "Planning Essentials (Scope/Schedule/Cost)\n"
        "• Scope: WBS, deliverables, acceptance criteria\n"
        "• Schedule: activities, durations, dependencies, critical path\n"
        "• Cost: estimate → budget → baseline\n"
        "Checklist: WBS, activity list, network diagram, cost baseline\n"
        "Sources: ISO 21502 §6.4; PMBOK7 – Planning/Measurement Domains"
    ),
    "Risk": (
        "Risk & Quality\n"
        "• Risk: identify → analyze (qual/quant) → plan responses → monitor\n"
        "• Responses: avoid, mitigate, transfer, accept; opportunities: exploit, enhance, share\n"
        "• Quality: define metrics, standards, assurance vs control\n"
        "Artifacts: risk register, issue log, quality plan\n"
        "Sources: PMBOK7 – Uncertainty; ISO 21502 §6.5"
    ),
    "Delivery": (
        "Delivery Control\n"
        "• Baselines: scope, schedule, cost; manage changes formally\n"
        "• Reporting: status, variance, forecast (EAC)\n"
        "• Change control: impact analysis before approval\n"
        "Artifacts: change request, decision log, status report\n"
        "Sources: PRINCE2 – Change; PMBOK7 – Measurement Domain"
    ),
    "EVM": (
        "EVM Fast Track\n"
        "Formulas: SPI = EV/PV; CPI = EV/AC; EAC = AC + (BAC−EV)/CPI\n"
        "Example: BAC 500k; PV 200k; EV 180k; AC 220k → SPI .90; CPI .82; EAC ≈ 820k\n"
        "Interpretation: behind schedule, over cost; actions: descoping, leveling, risk response\n"
        "Sources: PMBOK7 – Measurement Domain"
    ),
    "Agile": (
        "Agile & Hybrid\n"
        "• When to hybridize: fixed regulatory milestones + agile increments\n"
        "• Scrum: roles, events, artifacts; Kanban: WIP limits, flow\n"
        "Artifacts: roadmap, release plan, Definition of Done, board metrics\n"
        "Sources: PMBOK7 – Development approaches; Scrum Guide 2020 (concepts)"
    ),
    "Stakeholders": (
        "Procurement & Stakeholders\n"
        "• Stakeholders: identify, analyze, plan engagement, monitor\n"
        "• Comms plan: channels, frequency, content, responsibilities\n"
        "• Procurement: make/buy, contract types, selection criteria\n"
        "Artifacts: RACI/RASCI, stakeholder register, comms matrix\n"
        "Sources: ISO 21502 §6.6–6.7; PRINCE2 – Organization/Plans"
    ),
}
TOPIC_SLUGS = list(LESSON_CARDS.keys())

def evm_calc(PV: float, EV: float, AC: float, BAC: float = None) -> Dict[str, Any]:
    """Simple Earned Value calculator. Returns SPI, CPI and optional EAC."""
    res: Dict[str, Any] = {}
    res["SPI"] = None if PV == 0 else EV / PV
    res["CPI"] = None if AC == 0 else EV / AC
    if res["CPI"] and BAC is not None:
        res["EAC"] = AC + (BAC - EV) / res["CPI"]
    else:
        res["EAC"] = None
    return res

# ---------- Commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["last_topic"] = "Foundations"
    kb = [
        [
            InlineKeyboardButton("📘 Lessons", callback_data="menu_lessons"),
            InlineKeyboardButton("📝 Quiz me", callback_data="menu_quiz"),
        ],
        [
            InlineKeyboardButton("📐 EVM calc", callback_data="menu_evm"),
            InlineKeyboardButton("ℹ️ Scope", callback_data="menu_scope"),
        ],
    ]
    await update.message.reply_text(
        "Hi! I’m your Project-Management tutor. Ask a question (e.g., “How to build a WBS?”) "
        "or use the buttons below.",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def scope_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "I cover PM life cycle, governance, planning (scope/schedule/cost/risk/quality), "
        "delivery control (baselines, change), EVM, agile & hybrid, procurement, comms, stakeholders.\n"
        "Use /lesson <Foundations|Planning|Risk|Delivery|EVM|Agile|Stakeholders>\n"
        "Try /calc evm 200000 180000 220000 500000"
    )

async def sources_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Sources I cite:\n"
        "• PMBOK® Guide – Seventh Edition (your summaries/notes)\n"
        "• ISO 21502:2020 (your notes)\n"
        "• PRINCE2® 2017/2023 (your notes)\n"
        "• Scrum Guide 2020 (concepts)"
    )

async def lesson_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Please choose a topic: " + ", ".join(TOPIC_SLUGS) + "\nExample: /lesson Planning"
        )
        return
    topic = " ".join(args).strip().title()
    if topic not in LESSON_CARDS:
        await update.message.reply_text(
            f"Unknown topic '{topic}'. Choose one of: {', '.join(TOPIC_SLUGS)}"
        )
        return
    context.user_data["last_topic"] = topic
    await update.message.reply_text(LESSON_CARDS[topic])

async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = context.user_data.get("last_topic", "Foundations")
    if client is None:
        await update.message.reply_text(
            "Quiz feature needs the AI key. Ask your admin to set OPENAI_API_KEY."
        )
        return
    prompt = (
        f"Create 3 MCQs on {topic}. Format:\n"
        f"Q) ...\nA. ...\nB. ...\nC. ...\nD. ...\nAnswer: <letter> | Why: <1-2 lines>\n"
        f"Vary difficulty; include one numerical EVM if topic matches."
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        text = resp.choices[0].message.content
        await update.message.reply_text(text)
    except Exception as e:
        log.exception("OpenAI quiz error")
        await update.message.reply_text(f"Sorry, quiz failed: {e}")

async def calc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) not in (3, 4):
        await update.message.reply_text(
            "Usage: /calc evm PV EV AC [BAC]\nExample: /calc evm 200000 180000 220000 500000"
        )
        return
    try:
        nums = list(map(float, args))
        PV, EV, AC = nums[0], nums[1], nums[2]
        BAC = nums[3] if len(nums) == 4 else None
    except Exception:
        await update.message.reply_text("Please provide numbers. Example: /calc evm 200000 180000 220000 500000")
        return
    res = evm_calc(PV, EV, AC, BAC)
    lines = [
        f"PV={PV:,.0f}, EV={EV:,.0f}, AC={AC:,.0f}" + (f", BAC={BAC:,.0f}" if BAC else ""),
        f"SPI = EV/PV = {('n/a' if res['SPI'] is None else f'{res['SPI']:.2f}')}",
        f"CPI = EV/AC = {('n/a' if res['CPI'] is None else f'{res['CPI']:.2f}')}",
    ]
    if BAC is not None and res["EAC"] is not None:
        lines.append(f"EAC ≈ {res['EAC']:,.0f}")
    await update.message.reply_text("\n".join(lines))

async def on_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "menu_lessons":
        kb = [[InlineKeyboardButton(t, callback_data=f"lesson_{t}")] for t in TOPIC_SLUGS]
        await query.edit_message_text("Pick a lesson:", reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("lesson_"):
        topic = data.split("_", 1)[1]
        context.user_data["last_topic"] = topic
        await query.edit_message_text(LESSON_CARDS[topic])
    elif data == "menu_quiz":
        fake_update = Update(update.update_id, message=update.effective_message)
        await quiz_cmd(fake_update, context)
    elif data == "menu_evm":
        await query.edit_message_text("Use: /calc evm PV EV AC [BAC]\nExample: /calc evm 200000 180000 220000 500000")
    elif data == "menu_scope":
        await query.edit_message_text(
            "Scope: PM life cycle, governance, planning, delivery control, EVM, agile & hybrid, procurement, comms, stakeholders.\n"
            "Try /lesson Foundations or ask: “How to run change control?”"
        )
    else:
        await query.edit_message_text("Unknown action.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.message.text.strip()
    if client is None:
        await update.message.reply_text(
            "I need an AI key to answer in detail. Ask your admin to set OPENAI_API_KEY."
        )
        return
    user_topic = context.user_data.get("last_topic", "Foundations")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Topic hint: {user_topic}\nQuestion: {q}\n"
                                    f"Reply with steps or a mini-table and add a short 'Sources:' line at the end."}
    ]
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.2,
        )
        answer = resp.choices[0].message.content
        MAX = 3800
        if len(answer) <= MAX:
            await update.message.reply_text(answer)
        else:
            for i in range(0, len(answer), MAX):
                await update.message.reply_text(answer[i:i+MAX])
    except Exception as e:
        log.exception("OpenAI answer error")
        await update.message.reply_text(f"Sorry, I couldn't answer: {e}")

def main():
    if not BOT_TOKEN:
        log.error("Missing TELEGRAM_BOT_TOKEN.")
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in environment variables.")
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("scope", scope_cmd))
    application.add_handler(CommandHandler("sources", sources_cmd))
    application.add_handler(CommandHandler("lesson", lesson_cmd))
    application.add_handler(CommandHandler("quiz", quiz_cmd))
    application.add_handler(CommandHandler("calc", calc_cmd))
    application.add_handler(CallbackQueryHandler(on_buttons))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot started. Waiting for messages...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
