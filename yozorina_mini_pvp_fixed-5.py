#!/usr/bin/env python3
# YOZORINA — Telegram group games bot (virtual coins only)
# Python 3.10+; standard library only.
# Put your NEW BotFather token in TOKEN. Never share it.
import json, os, random, re, sqlite3, threading, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from functools import lru_cache

TOKEN = "PASTE_NEW_BOTFATHER_TOKEN_HERE"
GEMINI_API_KEY = "PASTE_GEMINI_API_KEY_HERE"
GEMINI_MODEL = "gemini-3.5-flash"
OWNER_ID = 7005081437
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yozorina.db")
POLL_TIMEOUT = 5
CARD_WAIT_SECONDS = 60
START_COINS = 5000
DAILY_COINS = 2000
MAX_STAKE = 100000
DEFAULT_ADMIN_PASSWORD = "CHANGE_THIS_ADMIN_PASSWORD"
IST = timezone(timedelta(hours=5, minutes=30))

if TOKEN == "PASTE_NEW_BOTFATHER_TOKEN_HERE":
    raise SystemExit("Open this file and set TOKEN to your NEW BotFather token.")

API = f"https://api.telegram.org/bot{TOKEN}/"

def now():
    return int(time.time())

def today():
    return datetime.now(IST).strftime("%Y-%m-%d")

db = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("PRAGMA busy_timeout=30000")
db.execute("PRAGMA journal_mode=WAL")

def init_db():
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', name TEXT DEFAULT 'Player',
      coins INTEGER NOT NULL DEFAULT 1000, last_claim INTEGER NOT NULL DEFAULT 0,
      premium_until INTEGER NOT NULL DEFAULT 0, protect_until INTEGER NOT NULL DEFAULT 0,
      games INTEGER NOT NULL DEFAULT 0, kills INTEGER NOT NULL DEFAULT 0, robbed INTEGER NOT NULL DEFAULT 0,
      last_seen INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS groups(chat_id INTEGER PRIMARY KEY, title TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS pvp_games(
      id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, p1 INTEGER NOT NULL,
      p2 INTEGER NOT NULL DEFAULT 0, stake INTEGER NOT NULL, target_players INTEGER NOT NULL DEFAULT 2,
      round_no INTEGER NOT NULL DEFAULT 1, scores TEXT NOT NULL DEFAULT '{}',
      numbers TEXT NOT NULL DEFAULT '{}', choices TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL DEFAULT 'waiting', created INTEGER NOT NULL, message_id INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS word_games(
      chat_id INTEGER PRIMARY KEY, starter_id INTEGER NOT NULL, secret TEXT NOT NULL,
      guesses TEXT NOT NULL DEFAULT '[]', created INTEGER NOT NULL, message_id INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS card_queue(
      id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
      stake INTEGER NOT NULL, created INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS card_games(
      chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, stake INTEGER NOT NULL,
      round_no INTEGER NOT NULL, user_points INTEGER NOT NULL DEFAULT 0,
      bot_points INTEGER NOT NULL DEFAULT 0, user_numbers TEXT NOT NULL,
      bot_number INTEGER NOT NULL, created INTEGER NOT NULL,
      PRIMARY KEY(chat_id,user_id)
    );
    CREATE TABLE IF NOT EXISTS tx(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
      reason TEXT NOT NULL, created INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS complaint_pending(
      user_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, created INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ai_history(
      chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL,
      content TEXT NOT NULL, created INTEGER NOT NULL
    );
    """)
    pvp_cols = [row[1] for row in db.execute("PRAGMA table_info(pvp_games)").fetchall()]
    if "mode" not in pvp_cols:
        db.execute("ALTER TABLE pvp_games ADD COLUMN mode TEXT NOT NULL DEFAULT 'card'")
    card_cols = [row[1] for row in db.execute("PRAGMA table_info(card_games)").fetchall()]
    if "phase" not in card_cols:
        db.execute("ALTER TABLE card_games ADD COLUMN phase TEXT NOT NULL DEFAULT 'user'")
    if "used_letters" not in card_cols:
        db.execute("ALTER TABLE card_games ADD COLUMN used_letters TEXT NOT NULL DEFAULT '[]'")
    user_cols = [row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()]
    if "last_seen" not in user_cols:
        db.execute("ALTER TABLE users ADD COLUMN last_seen INTEGER NOT NULL DEFAULT 0")
    if not db.execute("SELECT 1 FROM settings WHERE key='admin_password'").fetchone():
        set_setting("admin_password", DEFAULT_ADMIN_PASSWORD)
    # Migrate older ai_history tables that were created without an `id` column.
    cols = [row[1] for row in db.execute("PRAGMA table_info(ai_history)").fetchall()]
    if "id" not in cols:
        db.execute("ALTER TABLE ai_history RENAME TO ai_history_legacy")
        db.execute("""CREATE TABLE ai_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL,
          content TEXT NOT NULL, created INTEGER NOT NULL
        )""")
        legacy_cols = [row[1] for row in db.execute("PRAGMA table_info(ai_history_legacy)").fetchall()]
        required = ["chat_id", "user_id", "role", "content", "created"]
        if all(col in legacy_cols for col in required):
            db.execute("INSERT INTO ai_history(chat_id,user_id,role,content,created) "
                       "SELECT chat_id,user_id,role,content,created FROM ai_history_legacy")
        db.execute("DROP TABLE ai_history_legacy")
    db.commit()

def api(method, data=None):
    # Keep Telegram calls responsive: short timeout and only one quick retry.
    body = urllib.parse.urlencode(data or {}).encode()
    last_error = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(API + method, data=body)
            with urllib.request.urlopen(req, timeout=6) as r:
                result = json.loads(r.read().decode("utf-8"))
            if not result.get("ok"):
                raise RuntimeError(result.get("description", "Telegram API error"))
            return result["result"]
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_error = e
            if attempt == 0 and method not in ("sendMessage", "sendChatAction"):
                time.sleep(0.2)
            elif attempt == 0:
                break
    raise last_error

def dashboard_keyboard():
    # Both buttons open the same full dashboard/help panel.
    return json.dumps({"inline_keyboard": [
        [{"text": "➕ Add more details", "callback_data": "dashboard_details"}],
        [{"text": "❓ Help / Commands", "callback_data": "dashboard_help"}]
    ]})

def dashboard_text():
    return (
        "🎀 <b>YOZORINA HELP</b>\n"
        "👤 /profile • /bal • /claim • /rank\n"
        "🏆 /leaderboard • /history\n\n"
        "🎮 <b>GAMES</b>\n"
        "/card AMOUNT 2 • /bet AMOUNT • /flip A/B/C/D (each card once per match) • /word\n"
        "/guess AMOUNT • /guessstatus\n"
        "/dice AMOUNT • /bet AMOUNT\n"
        "/rps rock|paper|scissors AMOUNT\n\n"
        "💬 <b>OTHER</b>\n"
        "/joke • /mood • /roll • /8ball QUESTION\n"
        "/choose A | B • /complaint • /ping\n"
        "🛠️ /admin • /setwelcome\n\n"
        "🪙 Virtual coins only 🎀"
    )

_MINI_TRANSLATION = str.maketrans({
    "A":"ᴀ", "B":"ʙ", "C":"ᴄ", "D":"ᴅ", "E":"ᴇ", "F":"ꜰ", "G":"ɢ",
    "H":"ʜ", "I":"ɪ", "J":"ᴊ", "K":"ᴋ", "L":"ʟ", "M":"ᴍ", "N":"ɴ",
    "O":"ᴏ", "P":"ᴘ", "Q":"ǫ", "R":"ʀ", "S":"ꜱ", "T":"ᴛ", "U":"ᴜ",
    "V":"ᴠ", "W":"ᴡ", "X":"x", "Y":"ʏ", "Z":"ᴢ",
    "a":"ᴀ", "b":"ʙ", "c":"ᴄ", "d":"ᴅ", "e":"ᴇ", "f":"ꜰ", "g":"ɢ",
    "h":"ʜ", "i":"ɪ", "j":"ᴊ", "k":"ᴋ", "l":"ʟ", "m":"ᴍ", "n":"ɴ",
    "o":"ᴏ", "p":"ᴘ", "q":"ǫ", "r":"ʀ", "s":"ꜱ", "t":"ᴛ", "u":"ᴜ",
    "v":"ᴠ", "w":"ᴡ", "x":"x", "y":"ʏ", "z":"ᴢ"
})

def mini_text(value):
    """Apply small-cap styling to bot text without breaking HTML or usable commands."""
    value = str(value)
    protected = re.compile(r'(<code>.*?</code>|<[^>]+>|/\w+(?:@\w+)?)', re.IGNORECASE | re.DOTALL)
    chunks = protected.split(value)
    for i in range(0, len(chunks), 2):
        chunks[i] = chunks[i].translate(_MINI_TRANSLATION)
    return "".join(chunks)

def send(chat_id, text, reply_to=None, reply_markup=None, parse_mode=None):
    text = mini_text(text)
    data = {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_to:
        data["reply_to_message_id"] = reply_to
    if reply_markup:
        data["reply_markup"] = reply_markup
    try:
        return api("sendMessage", data)
    except Exception as e:
        print("sendMessage error:", repr(e))
        return None

def ensure_user(u):
    uid = int(u["id"])
    name = (u.get("first_name") or "Player")[:64]
    username = u.get("username") or ""
    db.execute("""INSERT OR IGNORE INTO users(user_id, username, name, coins)
                  VALUES(?,?,?,?)""", (uid, username, name, START_COINS))
    db.execute("UPDATE users SET username=?, name=?, last_seen=? WHERE user_id=?", (username, name, now(), uid))
    db.commit()

def user(uid):
    return db.execute("SELECT * FROM users WHERE user_id=?", (int(uid),)).fetchone()

def change(uid, amount, reason):
    db.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (int(amount), int(uid)))
    db.execute("INSERT INTO tx(user_id,amount,reason,created) VALUES(?,?,?,?)",
               (int(uid), int(amount), reason, now()))
    db.commit()

def group_register(chat):
    if chat.get("type") in ("group", "supergroup"):
        db.execute("INSERT OR REPLACE INTO groups(chat_id,title) VALUES(?,?)",
                   (chat["id"], chat.get("title") or "Group"))
        db.commit()

def premium(uid):
    u = user(uid)

    return bool(u and u["premium_until"] > now())


def protected(uid):
    u = user(uid)
    return bool(u and u["protect_until"] > now())

def nice_time(seconds):
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days: return f"{days}d {hours}h"
    if hours: return f"{hours}h {minutes}m"
    return f"{minutes}m"

def target_from_message(msg, parts):
    reply = msg.get("reply_to_message")
    if reply and reply.get("from") and not reply["from"].get("is_bot"):
        return int(reply["from"]["id"]), reply["from"].get("first_name", "Player")
    if len(parts) > 1 and parts[1].isdigit():
        uid = int(parts[1])
        u = user(uid)
        if u: return uid, u["name"]
    return None, None

def help_text():
    return dashboard_text()

def set_setting(key, value):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    db.commit()

def get_setting(key, default="on"):
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default

def expire_card_queue():
    # Refund every expired real-player lobby (card, RPS, coin flip and XOXO).
    expired_games = db.execute("SELECT * FROM pvp_games WHERE status='waiting' AND created<=?", (now()-CARD_WAIT_SECONDS,)).fetchall()
    for game in expired_games:
        # Delete first to avoid duplicate refunds if the process restarts mid-loop.
        db.execute("UPDATE pvp_games SET status='cancelled' WHERE id=? AND status='waiting'", (game['id'],))
        if db.execute("SELECT changes()").fetchone()[0]:
            change(int(game['p1']), int(game['stake']), f"PvP {game['mode']} timeout refund")
            try:
                if int(game['message_id'] or 0): api('deleteMessage', {'chat_id': int(game['chat_id']), 'message_id': int(game['message_id'])})
            except Exception: pass
            send(int(game['chat_id']), f"⏰ Match cancelled — no opponent joined. {int(game['stake']):,} coins refunded.")
    expired = db.execute("SELECT * FROM card_queue WHERE created<=?", (now()-CARD_WAIT_SECONDS,)).fetchall()
    for row in expired:
        # Refund only if this queue entry still exists.
        change(row["user_id"], row["stake"], "Card match timeout refund")
        send(row["chat_id"], f"⏰ Card match cancelled: no opponent joined in 2 minutes. "
             f"{row['stake']:,} coins refunded to player {row['user_id']}.")
        db.execute("DELETE FROM card_queue WHERE id=?", (row["id"],))
    db.commit()


def html_escape(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def same_digit_sum_pair():
    # Two distinct 2-digit values with the same digit sum; repeats are allowed across rounds.
    buckets = {}
    for n in range(10, 100):
        buckets.setdefault(sum(map(int, str(n))), []).append(n)
    sums = [k for k, vals in buckets.items() if len(vals) >= 2]
    sm = random.choice(sums)
    a, b = random.sample(buckets[sm], 2)
    return a, b, sm


@lru_cache(maxsize=1)
def _card_maps_by_total():
    """Build the valid point maps once, then reuse them for every match."""
    by_total = {}
    for a in range(1, 11):
        for b in range(1, 11):
            for c in range(1, 10):
                for d in range(1, 9):
                    card_map = {"A": a, "B": b, "C": c, "D": d}
                    by_total.setdefault(a + b + c + d, []).append(card_map)
    return {total: tuple(maps) for total, maps in by_total.items() if len(maps) >= 2}


def card_points_pair(previous=None):
    """Generate two different random card maps with the same total.

    A and B: 1..10 points; C: 1..9 points; D: 1..8 points.
    Each map is generated once when the match starts and stored in the game,
    so the points remain fixed for all four rounds. The optional argument is
    retained for compatibility with earlier versions.
    """
    by_total = _card_maps_by_total()
    total = random.choice(tuple(by_total))
    first, second = random.sample(by_total[total], 2)
    return first.copy(), second.copy(), total


def ai_chat_reply(chat_id, user_id, user_name, incoming_text):
    """Reply to ordinary conversation only; command/game logic never calls AI."""
    if not GEMINI_API_KEY or GEMINI_API_KEY == "PASTE_GEMINI_API_KEY_HERE":
        return "AI chat ke liye GEMINI_API_KEY set karna baaki hai. Game commands phir bhi locally chalenge."
    try:
        with sqlite3.connect(DB_FILE, timeout=15) as ai_db:
            ai_db.row_factory = sqlite3.Row
            rows = ai_db.execute(
                "SELECT role,content FROM ai_history WHERE chat_id=? AND user_id=? ORDER BY id DESC LIMIT 10",
                (int(chat_id), int(user_id))
            ).fetchall()
        history = [{"role": r["role"], "parts": [{"text": r["content"]}]} for r in reversed(rows)]
        history.append({"role": "user", "parts": [{"text": incoming_text[:4000]}]})
        payload = {
            "systemInstruction": {"parts": [{"text": (
                "You are YOZORINA, a virtual female-presenting Telegram chat persona, not a real human. "
                "Talk naturally and casually like a lively young woman in Hindi/Hinglish or the language the user uses. "
                "Have a confident, sassy, playful attitude; sometimes be mock-angry or teasing when it fits, "
                "but never be cruel, threatening, sexually suggestive, or constantly rude. Vary your wording; "
                "avoid robotic repeated greetings and keep short replies short. Welcome people warmly and chat naturally. "
                "If asked whether you are human, be honest that you are a virtual AI persona. "
                "Never run, modify, or decide game commands, turns, bets, scores, payouts, refunds, or admin actions; "
                "those are controlled only by the bot's local Python code. Do not claim to have executed commands."
            )}]},
            "contents": history,
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 700}
        }
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
        answer = "".join(part.get("text", "") for part in result.get("candidates", [{}])[0].get("content", {}).get("parts", [] )).strip()
        if not answer:
            return "Sorry, abhi jawab generate nahi ho paya. Thodi der baad try karo."
        answer = answer[:3500]
        with sqlite3.connect(DB_FILE, timeout=15) as ai_db:
            ai_db.execute("INSERT INTO ai_history(chat_id,user_id,role,content,created) VALUES(?,?,?,?,?)",
                          (int(chat_id), int(user_id), "user", incoming_text[:4000], now()))
            ai_db.execute("INSERT INTO ai_history(chat_id,user_id,role,content,created) VALUES(?,?,?,?,?)",
                          (int(chat_id), int(user_id), "model", answer, now()))
            ai_db.execute("DELETE FROM ai_history WHERE chat_id=? AND user_id=? AND id NOT IN "
                          "(SELECT id FROM ai_history WHERE chat_id=? AND user_id=? ORDER BY id DESC LIMIT 12)",
                          (int(chat_id), int(user_id), int(chat_id), int(user_id)))
        return answer
    except Exception as e:
        print("AI reply error:", type(e).__name__, str(e)[:200])
        return "AI abhi respond nahi kar pa raha. Thodi der baad dobara try karo."

def pvp_keyboard(bot_username, game_id):
    # A deep link lets a member open the bot DM and activate /start.
    return json.dumps({"inline_keyboard": [[{"text": "📩 Check DM", "url": f"https://t.me/{bot_username}?start=card_{game_id}"}]]})

def pvp_status(game, final=False):
    p1, p2 = int(game['p1']), int(game['p2'])
    a, b = user(p1), user(p2)
    scores = json.loads(game['scores'] or '{}')
    s1, s2 = int(scores.get(str(p1), 0)), int(scores.get(str(p2), 0))
    name1 = html_escape(a['name'] if a else 'Player 1')
    name2 = html_escape(b['name'] if b else 'Player 2')
    link1 = f'<a href="tg://user?id={p1}">{name1}</a>'
    link2 = f'<a href="tg://user?id={p2}">{name2}</a>'
    if final:
        if s1 > s2: winner = link1
        elif s2 > s1: winner = link2
        else: winner = '🤝 Draw'
        return (f'🏁 <b>FINAL · ROUND 4/4</b>\n{link1}: <b>{s1}</b> · {link2}: <b>{s2}</b>\n'
                f'🏆 Winner: {winner}')
    return f'🎴 <b>ROUND {game["round_no"]}/4</b>\n{link1} · {s1}  —  {link2} · {s2}\n🔒 Take turns in order shown below. Only the named player can move.'

def word_feedback(secret, guess):
    # Wordle-style feedback, correctly handling repeated letters.
    out = ['⬜'] * len(secret)
    remaining = {}
    for i, ch in enumerate(guess):
        if ch == secret[i]: out[i] = '🟩'
        else: remaining[secret[i]] = remaining.get(secret[i], 0) + 1
    for i, ch in enumerate(guess):
        if out[i] == '🟩': continue
        if remaining.get(ch, 0): out[i] = '🟨'; remaining[ch] -= 1
        else: out[i] = '🟥'
    return ''.join(out)

def _ai_reply_worker(chat_id, user_id, user_name, prompt, reply_to):
    answer = ai_chat_reply(chat_id, user_id, user_name, prompt)
    send(chat_id, answer, reply_to)


def handle(msg):
    chat = msg.get("chat", {})
    uid_obj = msg.get("from", {})
    if not chat or not uid_obj or uid_obj.get("is_bot"):
        return
    group_register(chat)
    ensure_user(uid_obj)
    uid = int(uid_obj["id"])
    cid = int(chat["id"])
    mid = msg.get("message_id")
    text = (msg.get("text") or "").strip()
    if msg.get("new_chat_members") and get_setting(f"welcome:{cid}") == "on":
        for m in msg["new_chat_members"]:
            if not m.get("is_bot"):
                ensure_user(m)
                send(cid, f"🌸 Hii {m.get('first_name','friend')}! Welcome to {chat.get('title','our group')} 💗\n"
                          "I'm YOZORINA. Use /help for games and commands.")
        return
    if not text:
        return
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()
    u = user(uid)

    # /start deep link from the group's Check DM button.
    if cmd == "/start" and len(parts) > 1 and parts[1].startswith("card_"):
        try:
            gid = int(parts[1].split("_", 1)[1])
            game = db.execute("SELECT * FROM pvp_games WHERE id=? AND status='playing' AND (p1=? OR p2=?)", (gid, uid, uid)).fetchone()
            if not game:
                send(cid, "This match is no longer active, or you haven't joined it."); return
            if game['mode'] == 'rps':
                send(cid, f"✊ RPS Match #{gid}\nChoose: /rps rock · /rps paper · /rps scissors")
            elif game['mode'] == 'coinflip':
                send(cid, f"🪙 Coin Flip Match #{gid}\nChoose: /flip heads · /flip tails")
            else:
                nums = json.loads(game['numbers'] or '{}')
                points = nums.get(str(uid), {})
                if isinstance(points, dict):
                    lines = "\n".join(f"Flip {letter} = {points[letter]} points" for letter in ("A", "B", "C", "D"))
                    game_choices = json.loads(game['choices'] or '{}')
                    turn_id = int(game_choices.get('_current_turn', game['p1']))
                    turn_user = user(turn_id)
                    turn_name = html_escape(turn_user['name'] if turn_user else 'Player')
                    my_row = user(uid)
                    turn_text = f"👉 YOUR TURN NOW, {html_escape(my_row['name'] if my_row else 'Player')}!" if turn_id == uid else f"⏳ It's {turn_name}'s turn right now."
                    send(cid, f"🎴 Match #{gid} · Round {game['round_no']}/4\n{lines}\nTotal = {sum(points.values())} points\n\n{turn_text}\nChoose an unused card: /flip A, /flip B, /flip C or /flip D")
                else:
                    send(cid, f"🎴 Match #{gid} · Round {game['round_no']}/4\nUse /flip A, /flip B, /flip C or /flip D.")
            return
        except Exception:
            send(cid, "Couldn't open that match. Please use /help."); return

    # Real-player PvP card game. /card AMOUNT [PLAYER_COUNT], default 2.
    if cmd == "/card":
        if chat.get("type") not in ("group", "supergroup"):
            send(cid, "Start /card in a group, then both real players use /start in my DM first.", mid); return
        if len(parts) not in (2, 3) or not parts[1].isdigit() or (len(parts)==3 and not parts[2].isdigit()):
            send(cid, "🎴 Use: /card AMOUNT [PLAYERS]\nExample: /card 2000 2 — 2 real players, 2,000 coins each.", mid); return
        stake = int(parts[1]); target_players = int(parts[2]) if len(parts)==3 else 2
        if not 1 <= stake <= MAX_STAKE or target_players != 2:
            send(cid, "For now, matches support exactly 2 real players. Bet must be 1–100,000 coins.", mid); return
        if int(u['coins']) < stake:
            send(cid, f"💸 Not enough coins! Your balance: {u['coins']:,}. Need {stake:,}. Use /claim for daily coins or /bal to check your wallet.", mid); return
        waiting = db.execute("SELECT * FROM pvp_games WHERE chat_id=? AND status='waiting' AND mode='card' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        if waiting:
            send(cid, "A card match is already waiting in this group. Join it with /bet AMOUNT.", mid); return
        change(uid, -stake, "PvP card match stake")
        cur = db.execute("INSERT INTO pvp_games(chat_id,p1,stake,target_players,status,created,mode) VALUES(?,?,?,2,'waiting',?,'card')", (cid,uid,stake,now()))
        gid = cur.lastrowid; db.commit()
        botname = get_setting('bot_username', 'YozorinaBot')
        lobby_msg = send(cid, f"🎴 <b>PLAYER MATCH · 4 ROUNDS</b>\n👤 <a href=\"tg://user?id={uid}\">{html_escape(u['name'])}</a> is waiting.\n🪙 Bet: <b>{stake:,} each</b>\n👥 1/2 players joined.\nJoin with <code>/bet {stake}</code>\n📩 Check DM appears after the second player joins.", mid, parse_mode="HTML")
        if lobby_msg:
            db.execute("UPDATE pvp_games SET message_id=? WHERE id=?", (int(lobby_msg.get('message_id',0)), gid)); db.commit()
        return

    # Real-player Tic-Tac-Toe (XOXO), virtual coins only.
    if cmd == "/xoxo":
        if chat.get("type") not in ("group", "supergroup"):
            send(cid, "Start XOXO in a group: /xoxo AMOUNT", mid); return
        if len(parts) != 2 or not parts[1].isdigit():
            send(cid, "⭕ Use: /xoxo AMOUNT\nExample: /xoxo 2000 — opponent joins with /bet 2000.", mid); return
        stake = int(parts[1])
        if not 1 <= stake <= MAX_STAKE or int(u["coins"]) < stake:
            send(cid, f"Bet must be 1–{MAX_STAKE:,} and within your balance.", mid); return
        waiting = db.execute("SELECT * FROM pvp_games WHERE chat_id=? AND status='waiting' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        if waiting:
            send(cid, "A match is already waiting here. Join it with /bet AMOUNT.", mid); return
        change(uid, -stake, "PvP XOXO stake")
        cur = db.execute("INSERT INTO pvp_games(chat_id,p1,stake,target_players,status,created,mode,choices,numbers,scores) VALUES(?,?,?,2,'waiting',?,'xoxo',?,?,?)", (cid, uid, stake, now(), json.dumps({"board":[" "]*9,"turn":uid}), "{}", "{}"))
        gid = cur.lastrowid; db.commit()
        botname = get_setting('bot_username', 'YozorinaBot')
        lobby = send(cid, f"⭕ <b>XOXO · REAL PLAYERS</b>\nHost: <a href=\"tg://user?id={uid}\">{html_escape(u['name'])}</a> (❌)\n🪙 Bet: <b>{stake:,} each</b>\nJoin: <code>/bet {stake}</code>\n⏱️ No opponent in 2 minutes = refund.", mid, pvp_keyboard(botname, gid), parse_mode="HTML")
        if lobby:
            db.execute("UPDATE pvp_games SET message_id=? WHERE id=?", (int(lobby.get('message_id',0)), gid)); db.commit()
        return

    if cmd == "/bet" and chat.get("type") in ("group", "supergroup"):
        if len(parts) not in (2, 3) or not parts[1].isdigit() or (len(parts) == 3 and parts[2].lower() not in ("coin", "coins", "gem", "gems")):
            send(cid, "🎮 Join the waiting match with /bet AMOUNT. Example: /bet 2000 (also accepts /bet 2000 coins). Check balance with /bal.", mid); return
        stake=int(parts[1])
        game=db.execute("SELECT * FROM pvp_games WHERE chat_id=? AND status='waiting' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        if not game:
            send(cid, "No open player match. Start one with /card AMOUNT 2.", mid); return
        if int(game['p1'])==uid:
            send(cid, "You're already the host of this match.", mid); return
        if stake != int(game['stake']):
            send(cid, f"Exact match bet required: /bet {game['stake']}", mid); return
        if int(u['coins']) < stake:
            send(cid, f"Not enough coins. Balance: {u['coins']:,}.", mid); return
        # Preflight both DMs BEFORE taking the joiner's stake. Telegram bots cannot start DMs.
        # If either user has not pressed Start, leave the lobby and balances untouched.
        try:
            if game['mode'] == 'rps':
                dm_prompt = f"✊ Match #{game['id']} ready. Choose privately: /rps rock, /rps paper, or /rps scissors"
            elif game['mode'] == 'coinflip':
                dm_prompt = f"🪙 Match #{game['id']} ready. Choose privately: /flip heads or /flip tails"
            else:
                dm_prompt = f"🎴 Match #{game['id']} ready. Your moves are private; use /flip A, /flip B, /flip C or /flip D."
            api('sendMessage', {'chat_id': int(game['p1']), 'text': 'Opponent is joining. ' + dm_prompt})
            api('sendMessage', {'chat_id': uid, 'text': dm_prompt})
        except Exception:
            send(cid, "📩 Both players must open the bot DM and press Start first. No coins were taken.", mid)
            return
        change(uid,-stake,f"PvP {game['mode']} stake")
        if game['mode'] == 'card':
            a_points, b_points, sm = card_points_pair()
            nums={str(game['p1']):a_points,str(uid):b_points}
            initial_choices={"_current_turn": int(game['p1']), "_round_starter": int(game['p1'])}
        else:
            nums={}
            initial_choices={}
        db.execute("UPDATE pvp_games SET p2=?,status='playing',round_no=1,numbers=?,scores=?,choices=?,created=? WHERE id=? AND status='waiting'",
                   (uid,json.dumps(nums),json.dumps({str(game['p1']):0,str(uid):0}),json.dumps(initial_choices),now(),game['id']))
        db.commit()
        if game['mode'] == "xoxo":
            choices = {"board": [" "] * 9, "turn": int(game['p1'])}
            db.execute("UPDATE pvp_games SET choices=?,created=? WHERE id=?", (json.dumps(choices), now(), game['id'])); db.commit()
            for player in (int(game['p1']), uid):
                try: api('sendMessage', {'chat_id': player, 'text': f"⭕ XOXO Match #{game['id']} started. You play {'❌' if player == int(game['p1']) else '⭕'}. Tap a square in the group board to move."})
                except Exception: pass
            xmsg = send(cid, xoxo_board_text(int(game['id']), int(game['p1']), uid, choices['board'], choices['turn']), reply_markup=xoxo_keyboard(int(game['id']), choices['board']), parse_mode='HTML')
            if xmsg:
                db.execute("UPDATE pvp_games SET message_id=? WHERE id=?", (int(xmsg.get('message_id',0)), game['id'])); db.commit()
            return
        if game['mode'] in ("rps", "coinflip"):
            # Single-round real-player duel; both choices happen privately in DMs.
            updated=db.execute("SELECT * FROM pvp_games WHERE id=?",(game['id'],)).fetchone()
            for player in (int(game['p1']), uid):
                try:
                    if game['mode'] == "rps":
                        prompt = "✊ PvP RPS · choose in this DM: /rps rock, /rps paper, or /rps scissors"
                    else:
                        prompt = "🪙 PvP Coin Flip · choose in this DM: /flip heads or /flip tails"
                    api('sendMessage', {'chat_id': player, 'text': f"Match #{game['id']} · Bet {int(game['stake']):,} each\n{prompt}"})
                except Exception:
                    pass
            botname=get_setting('bot_username','YozorinaBot')
            mode_label = "ROCK · PAPER · SCISSORS" if game['mode']=="rps" else "HEADS · TAILS"
            smsg=send(cid, f"🎮 <b>{mode_label} · MATCH #{game['id']}</b>\nBoth players joined. Make your choice in YOZORINA's DM.\n🪙 Pot: <b>{int(game['stake'])*2:,}</b>", mid, pvp_keyboard(botname,int(game['id'])), parse_mode="HTML")
            if smsg:
                db.execute("UPDATE pvp_games SET message_id=? WHERE id=?",(int(smsg.get('message_id',0)),int(game['id']))); db.commit()
            return
        updated=db.execute("SELECT * FROM pvp_games WHERE id=?",(game['id'],)).fetchone()
        botname=get_setting('bot_username','YozorinaBot')
        first_turn_id = int(game['p1'])
        first_turn_row = user(first_turn_id)
        first_turn_name = html_escape(first_turn_row['name'] if first_turn_row else 'Player')
        for player in (int(game['p1']),uid):
            try:
                points = nums[str(player)]
                point_lines = "\n".join(f"Flip {letter} = {points[letter]} points" for letter in ("A", "B", "C", "D"))
                turn_line = "👉 YOUR TURN NOW!" if player == first_turn_id else f"⏳ First turn: {first_turn_name}. Please wait."
                api('sendMessage', {'chat_id':player,'text':f"🎴 Match #{game['id']} · Round 1/4\n{point_lines}\nTotal = {sum(points.values())} points\n\n{turn_line}\nUse /flip A, /flip B, /flip C or /flip D when it is your turn."})
            except Exception:
                pass
        status_msg = send(cid, pvp_status(updated) + f"\n👉 <b>First turn: {first_turn_name}</b>", mid, pvp_keyboard(botname, int(game['id'])), parse_mode="HTML")
        if status_msg:
            db.execute("UPDATE pvp_games SET message_id=? WHERE id=?", (int(status_msg.get('message_id',0)), int(game['id']))); db.commit()
        return

    # Real-player RPS and coin-flip lobbies. Start these in a group; choices are DM-only.
    if cmd in ("/rps", "/flip") and chat.get("type") in ("group", "supergroup") and len(parts) in (2, 3) and parts[1].isdigit() and (len(parts) == 2 or parts[2] == "2"):
        mode = "rps" if cmd=="/rps" else "coinflip"
        stake=int(parts[1])
        if not 1 <= stake <= MAX_STAKE or int(u["coins"]) < stake:
            send(cid, f"Bet must be 1–{MAX_STAKE:,} and within your balance.", mid); return
        waiting=db.execute("SELECT * FROM pvp_games WHERE chat_id=? AND status='waiting' ORDER BY id DESC LIMIT 1",(cid,)).fetchone()
        if waiting:
            send(cid,"A match is already waiting here. Join it with /bet AMOUNT.",mid); return
        change(uid,-stake,f"PvP {mode} stake")
        cur=db.execute("INSERT INTO pvp_games(chat_id,p1,stake,target_players,status,created,mode) VALUES(?,?,?,2,'waiting',?,?)",(cid,uid,stake,now(),mode))
        gid=cur.lastrowid; db.commit()
        label="Rock · Paper · Scissors" if mode=="rps" else "Heads · Tails"
        botname=get_setting('bot_username','YozorinaBot')
        lobby=send(cid,f"🎮 <b>{label} · PvP</b>\nHost: <a href=\"tg://user?id={uid}\">{html_escape(u['name'])}</a>\n🪙 Bet: <b>{stake:,} each</b> · 1/2 players\nJoin: <code>/bet {stake}</code>\n📩 Check DM appears after the opponent joins.\n⏱️ No opponent in 1 minute = refund.",mid,parse_mode="HTML")
        if lobby:
            db.execute("UPDATE pvp_games SET message_id=? WHERE id=?",(int(lobby.get('message_id',0)),gid)); db.commit()
        return

    if cmd == "/rps" and chat.get("type")=="private" and len(parts)==2 and parts[1].lower() in ("rock","paper","scissors"):
        mode="rps"; choice=parts[1].lower()
    elif cmd == "/flip" and chat.get("type")=="private" and len(parts)==2 and parts[1].lower() in ("heads","head","tails","tail"):
        mode="coinflip"; choice="heads" if parts[1].lower() in ("head","heads") else "tails"
    else:
        mode=None; choice=None
    if mode:
        if chat.get("type") != "private":
            send(cid, "🔒 Cheating roko: RPS/Flip choices sirf bot ke DM mein lock hongi. Lobby ka Check DM button kholo.", mid); return
        game=db.execute("SELECT * FROM pvp_games WHERE status='playing' AND mode=? AND (p1=? OR p2=?) ORDER BY id DESC LIMIT 1",(mode,uid,uid)).fetchone()
        if not game:
            send(cid,"No active PvP match for this game. Join/start one in the group first."); return
        choices=json.loads(game['choices'] or '{}'); round_choices=choices.setdefault('1',{})
        if str(uid) in round_choices:
            send(cid,"Your choice is already locked. Waiting for the opponent."); return
        round_choices[str(uid)]=choice
        db.execute("UPDATE pvp_games SET choices=?,created=? WHERE id=?",(json.dumps(choices),now(),game['id'])); db.commit()
        if mode == "rps":
            # Never reveal the selected RPS move in the group; only confirm it was locked.
            player_row = user(uid)
            player_name = html_escape(player_row['name'] if player_row else 'Player')
            send(int(game['chat_id']),
                 f"🎮 <b>RPS MOVE</b>\n🔢 <b>ROUND 1/1</b>\n✅ <b>Player {player_name}</b> — Choice Successful!",
                 parse_mode="HTML")
        if len(round_choices)<2:
            send(cid,"✅ Choice locked successfully. Waiting for the other player."); return
        p1,p2=int(game['p1']),int(game['p2']); c1,c2=round_choices[str(p1)],round_choices[str(p2)]
        if mode=="rps":
            beats={"rock":"scissors","paper":"rock","scissors":"paper"}
            winner=p1 if c1!=c2 and beats[c1]==c2 else p2 if c1!=c2 else 0
            result="🔒 Both choices were locked privately."
        else:
            toss=random.choice(("heads","tails"))
            winner=p1 if c1==toss and c2!=toss else p2 if c2==toss and c1!=toss else 0
            result=f"🪙 Toss: {toss.title()} · {c1.title()} vs {c2.title()}"
        stake=int(game['stake']); pot=stake*2
        if winner:
            change(winner,pot,"PvP match win")
        else:
            change(p1,stake,"PvP draw refund"); change(p2,stake,"PvP draw refund")
        db.execute("UPDATE users SET games=games+1 WHERE user_id IN (?,?)",(p1,p2)); db.execute("DELETE FROM pvp_games WHERE id=?",(game['id'],)); db.commit()
        try:
            if int(game['message_id'] or 0): api('deleteMessage',{'chat_id':int(game['chat_id']),'message_id':int(game['message_id'])})
        except Exception: pass
        a,b=user(p1),user(p2)
        link1=f'<a href="tg://user?id={p1}">{html_escape(a["name"])}</a>'; link2=f'<a href="tg://user?id={p2}">{html_escape(b["name"])}</a>'
        outcome=(f"🏆 Winner: {link1 if winner==p1 else link2} · +{pot:,} coins") if winner else "🤝 Draw · both bets refunded"
        if mode == "rps":
            final_text = f"🎮 <b>FINAL RESULT</b>\n{result}\n{outcome}"
        else:
            final_text = f"🎮 <b>FINAL RESULT</b>\n{link1}: <b>{c1}</b> · {link2}: <b>{c2}</b>\n{result}\n{outcome}"
        send(int(game['chat_id']), final_text, parse_mode="HTML")
        send(cid,"🏁 Match completed. Final result posted in the group."); return

    # PvP card moves: values remain fixed for the whole round and are private.
    if cmd == "/flip" and len(parts) == 2 and parts[1].upper() in ("A", "B", "C", "D"):
        game=db.execute("SELECT * FROM pvp_games WHERE status='playing' AND mode='card' AND (p1=? OR p2=?) ORDER BY id DESC LIMIT 1",(uid,uid)).fetchone()
        if not game:
            send(cid, "No active real-player Card match. Start one in a group with /card AMOUNT 2 and let another player join with /bet AMOUNT.", mid); return
        if chat.get('type') != 'private':
            send(cid,"📩 Make your move in my DM: open the group's Check DM button, then send /flip A/B/C/D.",mid); return
        choices=json.loads(game['choices'] or '{}')
        round_key=str(game['round_no'])
        round_choices=choices.setdefault(round_key,{})
        current_turn = int(choices.get('_current_turn', game['p1']))
        if uid != current_turn:
            turn_row = user(current_turn)
            turn_name = html_escape(turn_row['name'] if turn_row else 'Player')
            send(cid, f"🚫 It's not your turn. Abhi {turn_name} ki turn hai.")
            return
        if str(uid) in round_choices:
            send(cid,"Your move for this round is already locked."); return
        letter=parts[1].upper()
        # A/B/C/D are each single-use per player for the entire 4-round match.
        used_letters=[]
        for old_round, old_choices in choices.items():
            if str(old_round).isdigit() and isinstance(old_choices, dict):
                previous_letter=old_choices.get(str(uid))
                if previous_letter in ("A", "B", "C", "D"):
                    used_letters.append(previous_letter)
        if letter in used_letters:
            strikes=choices.setdefault("_strikes", {})
            strikes[str(uid)]=int(strikes.get(str(uid),0))+1
            db.execute("UPDATE pvp_games SET choices=? WHERE id=?", (json.dumps(choices), game['id']))
            db.commit()
            remaining=[x for x in ("A","B","C","D") if x not in used_letters]
            send(cid, f"🚫 MISS STRIKE! Flip {letter} was already used in this match. It cannot be used again until the game ends.\nChoose an unused card: {', '.join(remaining) if remaining else 'none left'}.")
            return
        round_choices[str(uid)]=letter
        nums=json.loads(game['numbers'] or '{}')
        own_value=int(nums[str(uid)][letter])
        p1_id,p2_id=str(game['p1']),str(game['p2'])
        other=int(game['p2']) if uid==int(game['p1']) else int(game['p1'])
        actor_row=user(uid)
        group_name=html_escape(actor_row['name'] if actor_row else 'Player')
        other_row=user(other)
        other_name=html_escape(other_row['name'] if other_row else 'Player')
        if len(round_choices)<2:
            choices['_current_turn'] = other
            db.execute("UPDATE pvp_games SET choices=?,created=? WHERE id=?",(json.dumps(choices),now(),game['id'])); db.commit()
            send(int(game['chat_id']),
                 f"🎴 <b>CARD MOVE</b>\n🔢 <b>ROUND {game['round_no']}/4</b>\n👤 {group_name} → <b>{own_value}</b>\n"
                 f"👉 <a href=\"tg://user?id={other}\">{other_name}</a>, it's your turn now.",
                 parse_mode='HTML')
            send(cid,f"✅ Your move is locked: {letter} = {own_value} points. Waiting for {other_name}.")
            return
        db.execute("UPDATE pvp_games SET choices=?,created=? WHERE id=?",(json.dumps(choices),now(),game['id'])); db.commit()
        letter1,letter2=round_choices[p1_id],round_choices[p2_id]
        value1=int(nums[p1_id][letter1]); value2=int(nums[p2_id][letter2])
        scores=json.loads(game['scores'] or '{}')
        round_winner=0
        if value1>value2:
            scores[p1_id]=int(scores.get(p1_id,0))+1; round_winner=int(p1_id)
        elif value2>value1:
            scores[p2_id]=int(scores.get(p2_id,0))+1; round_winner=int(p2_id)
        rnd=int(game['round_no']); p1,p2=int(p1_id),int(p2_id)
        p1row,p2row=user(p1),user(p2)
        name1=html_escape(p1row['name'] if p1row else 'Player 1'); name2=html_escape(p2row['name'] if p2row else 'Player 2')
        if round_winner==p1: round_line=f"🏆 Round winner: {name1}"
        elif round_winner==p2: round_line=f"🏆 Round winner: {name2}"
        else: round_line="🤝 Round draw — no point awarded"
        round_text=(f"🎴 <b>CARD RESULT</b>\n🔢 <b>ROUND {rnd}/4</b>\n"
                    f"👤 {name1} → <b>{value1}</b>\n"
                    f"👤 {name2} → <b>{value2}</b>\n{round_line}\n"
                    f"📊 Match score: <b>{scores.get(p1_id,0)} - {scores.get(p2_id,0)}</b>")
        if rnd>=4:
            s1=int(scores.get(p1_id,0)); s2=int(scores.get(p2_id,0)); pot=int(game['stake'])*2
            if s1>s2:
                change(p1,pot,"PvP card match win"); final=f"🏆 Final winner: {name1} · +{pot:,} coins"
            elif s2>s1:
                change(p2,pot,"PvP card match win"); final=f"🏆 Final winner: {name2} · +{pot:,} coins"
            else:
                change(p1,int(game['stake']),"PvP card match draw refund"); change(p2,int(game['stake']),"PvP card match draw refund"); final="🤝 Match draw — both bets refunded"
            db.execute("UPDATE pvp_games SET scores=?,status='finished' WHERE id=?",(json.dumps(scores),game['id']))
            db.execute("UPDATE users SET games=games+1 WHERE user_id IN (?,?)",(p1,p2)); db.commit()
            send(int(game['chat_id']),round_text+"\n\n🏁 <b>FINAL RESULT</b>\n"+final,parse_mode='HTML')
            db.execute("DELETE FROM pvp_games WHERE id=?",(game['id'],)); db.commit()
            send(cid,"🏁 Match completed. Final result and payout are posted in the group."); return
        # Keep point maps fixed. Alternate the first turn each round for fairness.
        previous_starter = int(choices.get('_round_starter', p1))
        next_starter = p2 if previous_starter == p1 else p1
        choices['_round_starter'] = next_starter
        choices['_current_turn'] = next_starter
        next_starter_row = user(next_starter)
        next_starter_name = html_escape(next_starter_row['name'] if next_starter_row else 'Player')
        db.execute("UPDATE pvp_games SET scores=?,choices=?,round_no=?,created=? WHERE id=?",
                   (json.dumps(scores),json.dumps(choices),rnd+1,now(),game['id'])); db.commit()
        send(int(game['chat_id']),round_text+f"\n\n➡️ <b>ROUND {rnd+1}/4</b>\n👉 <b>First turn: {next_starter_name}</b>\nPoints stay fixed for this whole match.",parse_mode='HTML')
        for player in (p1,p2):
            try:
                points=nums[str(player)]
                used=[]
                for old_round, old_choices in choices.items():
                    if str(old_round).isdigit() and isinstance(old_choices, dict):
                        used_letter=old_choices.get(str(player))
                        if used_letter in ("A","B","C","D") and used_letter not in used:
                            used.append(used_letter)
                available=[letter for letter in ("A","B","C","D") if letter not in used]
                lines="\n".join(f"Flip {letter} = {points[letter]} points" for letter in ("A","B","C","D"))
                turn_line = "👉 YOUR TURN NOW!" if player == next_starter else f"⏳ First turn: {next_starter_name}. Please wait."
                api('sendMessage',{'chat_id':player,'text':f"🎴 Match #{game['id']} · Round {rnd+1}/4\n{lines}\nTotal = {sum(points.values())} points\nAlready used: {', '.join(used) if used else 'none'}\nAvailable: {', '.join(available)}\n\n{turn_line}\nUse /flip A, /flip B, /flip C or /flip D when it is your turn."})
            except Exception as dm_error:
                print("Card DM error:",repr(dm_error))
        return

    # /word starts a short word-guess game; any 3–4 letter message is a guess.
    if cmd == "/word":
        if chat.get('type') not in ('group','supergroup'):
            send(cid,"Start /word in a group.",mid); return
        existing=db.execute("SELECT * FROM word_games WHERE chat_id=?",(cid,)).fetchone()
        if existing:
            send(cid,"A word game is already running here. Guess the word with 3–4 letters.",mid); return
        words=["POT","HOT","CAT","DOG","SUN","MAP","RED","BOX","BOOK","MOON","STAR","FISH","TREE","PLAY","GAME","WORD","FIRE","BLUE","MINT","RING","LAMP","WIND","RAIN","SHIP","FROG","CAKE","MILK","BIRD","HOME","GOLD"]
        secret=random.choice(words).lower()
        cur=db.execute("INSERT INTO word_games(chat_id,starter_id,secret,guesses,created,message_id) VALUES(?,?,?,?,?,?)",(cid,uid,secret,'[]',now(),mid or 0)); db.commit()
        send(cid,"🔤 <b>WORD · 10 TRIES</b>\nGuess a 3–4 letter word. 🟩 correct letter/place · 🟨 letter exists elsewhere · 🟥 not in word.\nExample: <code>/word</code> then send <code>pot</code>.",mid,parse_mode='HTML'); return

    # Capture plain text guesses for an active word game before AI auto-reply.
    if not text.startswith('/') and 3 <= len(text.strip()) <= 4 and text.strip().isalpha():
        wg=db.execute("SELECT * FROM word_games WHERE chat_id=?",(cid,)).fetchone()
        guess=text.strip().lower()
        if wg:
            guesses=json.loads(wg['guesses'] or '[]')
            if len(guess)!=len(wg['secret']):
                send(cid,f"Use a {len(wg['secret'])}-letter word.",mid); return
            guesses.append({'uid':uid,'name':u['name'],'word':guess,'feedback':word_feedback(wg['secret'],guess)})
            lines=[f"🔤 <b>WORD · TRY {len(guesses)}/10</b>"]
            for item in guesses:
                lines.append(f"<a href=\"tg://user?id={item['uid']}\">{html_escape(item['name'])}</a>  <code>{html_escape(item['word'].upper())}</code>  {item['feedback']}")
            if guess==wg['secret']:
                lines.append(f"🏆 <a href=\"tg://user?id={uid}\">{html_escape(u['name'])}</a> guessed it!")
                db.execute("DELETE FROM word_games WHERE chat_id=?",(cid,)); db.commit()
            elif len(guesses)>=10:
                lines.append(f"⌛ Game cancelled. Word: <b>{wg['secret'].upper()}</b>")
                db.execute("DELETE FROM word_games WHERE chat_id=?",(cid,)); db.commit()
            else:
                db.execute("UPDATE word_games SET guesses=? WHERE chat_id=?",(json.dumps(guesses),cid)); db.commit()
            send(cid,"\n".join(lines),mid,parse_mode='HTML'); return

    # Complaint flow: collect one message, then forward it privately to the owner.
    if cmd == "/complaint":
        db.execute("INSERT OR REPLACE INTO complaint_pending(user_id,chat_id,created) VALUES(?,?,?)",
                   (uid, cid, now()))
        db.commit()
        send(cid, "📩 <b>Contact Admin</b>\n\nPlease describe your complaint in your next message.\nIt will be sent privately to admin @INCR3ADBLE along with your name and Telegram ID.\n\nSend /cancel to cancel.", mid, parse_mode="HTML")
        return
    pending = db.execute("SELECT * FROM complaint_pending WHERE user_id=?", (uid,)).fetchone()
    if pending and cmd == "/cancel":
        db.execute("DELETE FROM complaint_pending WHERE user_id=?", (uid,))
        db.commit()
        send(cid, "✅ Complaint cancelled.", mid)
        return
    if pending:
        db.execute("DELETE FROM complaint_pending WHERE user_id=?", (uid,))
        db.commit()
        username = ("@" + uid_obj["username"]) if uid_obj.get("username") else "No username"
        display_name = (uid_obj.get("first_name") or "Unknown")
        if uid_obj.get("last_name"):
            display_name += " " + uid_obj["last_name"]
        origin = chat.get("title") or "Private chat"
        report = (
            "📩 NEW YOZORINA COMPLAINT\n\n"
            f"👤 From: {display_name}\n"
            f"🔗 Username: {username}\n"
            f"🆔 User ID: {uid}\n"
            f"💬 Chat: {origin} (ID: {cid})\n\n"
            f"📝 Complaint:\n{text}"
        )
        forwarded = send(OWNER_ID, report)
        if forwarded:
            send(cid, "✅ Your complaint has been sent to admin @INCR3ADBLE. Thank you.", mid)
        else:
            send(cid, "⚠️ I couldn't deliver the complaint to admin right now. Please contact @INCR3ADBLE directly.", mid)
        return

    if cmd == "/give":
        # Owner-only coin removal command; available only in the owner's private DM.
        if uid != OWNER_ID or chat.get("type") != "private":
            send(cid, "🔒 ᴏɴʟʏ ᴛʜᴇ ʙᴏᴛ ᴏᴡɴᴇʀ ᴄᴀɴ ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪɴ ᴘʀɪᴠᴀᴛᴇ ᴅᴍ.", mid); return
        if len(parts) != 4 or parts[1].lower() != "remove" or not parts[2].isdigit() or not parts[3].isdigit() or int(parts[3]) < 1:
            send(cid, "ᴜꜱᴀɢᴇ: /give remove USER_ID AMOUNT\nᴇxᴀᴍᴘʟᴇ: /give remove 123456789 500", mid); return
        target, amount = int(parts[2]), int(parts[3])
        target_user = user(target)
        if not target_user:
            send(cid, "ᴛʜɪꜱ ᴜꜱᴇʀ ɪꜱɴ'ᴛ ʀᴇɢɪꜱᴛᴇʀᴇᴅ. ᴛʜᴇʏ ᴍᴜꜱᴛ ꜱᴛᴀʀᴛ ᴛʜᴇ ʙᴏᴛ ꜰɪʀꜱᴛ.", mid); return
        if amount > int(target_user["coins"]):
            send(cid, f"⚠️ ɴᴏᴛ ᴇɴᴏᴜɢʜ ᴄᴏɪɴꜱ. ᴛʜɪꜱ ᴜꜱᴇʀ ʜᴀꜱ {int(target_user['coins']):,} ᴄᴏɪɴꜱ.", mid); return
        change(target, -amount, "Admin removed coins")
        send(cid, f"✅ ʀᴇᴍᴏᴠᴇᴅ {amount:,} ᴄᴏɪɴꜱ ꜰʀᴏᴍ {target_user['name']} (ɪᴅ {target}). ɴᴇᴡ ʙᴀʟᴀɴᴄᴇ: {int(user(target)['coins']):,}.", mid); return

    if cmd == "/admin" and len(parts) >= 2 and parts[1].lower() == "givecoins":
        if uid != OWNER_ID or chat.get("type") != "private" or len(parts) != 4:
            send(cid, "Usage (owner DM only): /admin givecoins USER_ID AMOUNT", mid); return
        if not parts[2].isdigit() or not parts[3].isdigit() or int(parts[3]) < 1:
            send(cid, "User ID aur positive amount number mein do.", mid); return
        target, amount = int(parts[2]), int(parts[3])
        if not user(target):
            send(cid, "Ye user registered nahi hai. Pehle bot ko /start karwayein.", mid); return
        change(target, amount, "Admin coin grant")
        send(cid, f"✅ {amount:,} virtual coins {user(target)['name']} (ID {target}) ko de diye.", mid); return
    if cmd == "/admin" and len(parts) >= 2 and parts[1].lower() == "setpassword":
        if uid != OWNER_ID or chat.get("type") != "private" or len(parts) != 3 or len(parts[2]) < 8:
            send(cid, "Owner DM only. Usage: /admin setpassword NEW_PASSWORD (minimum 8 characters)", mid); return
        set_setting("admin_password", parts[2])
        send(cid, "✅ Admin password change ho gaya.", mid); return
    if cmd == "/admin":
        if chat.get("type") != "private":
            send(cid, "🔐 Security ke liye /admin ko bot ke DM mein use karo.", mid); return
        if uid != OWNER_ID:
            send(cid, "🔒 Sirf owner is admin panel ko access kar sakta hai.", mid); return
        if len(parts) < 2:
            send(cid, "🔐 Admin password bhejo: /admin YOUR_PASSWORD", mid); return
        if parts[1] != get_setting("admin_password", DEFAULT_ADMIN_PASSWORD):
            send(cid, "❌ Password galat hai.", mid); return
        panel = (
            "╭━━━ 🛠️ YOZORINA ADMIN PANEL ━━━╮\n\n"
            "👋 WELCOME SETTINGS\n"
            "• /setwelcome on — Enable welcome messages.\n"
            "• /setwelcome off — Disable welcome messages.\n\n"
            "📚 /help — View all bot commands.\n\n"
            "👑 OWNER-ONLY CONTROLS\n"
            "• /premiumadd USER_ID DAYS — Grant premium.\n"
            "• /broadcast MESSAGE — Announce to registered groups.\n\n"
            "ℹ️ Use these settings only in groups where you have admin rights.\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
        )
        send(cid, panel, mid)
        return

    if cmd == "/totalusers":
        if uid != OWNER_ID:
            send(cid, "🔒 Owner-only command.", mid); return
        rows = db.execute("SELECT user_id,username,name,last_seen,coins FROM users WHERE last_seen>=? ORDER BY last_seen DESC LIMIT 50", (now()-7*86400,)).fetchall()
        if not rows:
            send(cid, "📊 Pichhle 7 days mein active users abhi nahi mile.", mid); return
        lines = [f"👥 <b>ACTIVE USERS — LAST 7 DAYS ({len(rows)}/50)</b>", ""]
        for i,r in enumerate(rows,1):
            label = ("@"+r["username"]) if r["username"] else (r["name"] or "Player")
            label = str(label).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            lines.append(f"<b>{i}.</b> <a href=\"tg://user?id={int(r['user_id'])}\">{label}</a> | ID: <code>{r['user_id']}</code> | 🪙 {r['coins']:,}")
        send(cid, "\n".join(lines), mid, parse_mode="HTML"); return

    if cmd == "/admin" and len(parts)>1 and parts[1].lower()=="givecoins":
        pass

    if cmd == "/start":
        send(cid, dashboard_text(), mid, dashboard_keyboard()); return
    if cmd in ("/help", "/commands"):
        send(cid, help_text(), mid, dashboard_keyboard()); return
    if cmd == "/joke":
        jokes = [
            "Why did the computer go to the doctor? It caught a virus. 😭💻",
            "Teacher: Why are you late? Student: The sign said ‘School Ahead, Go Slow.’ 😂",
            "My wallet is like an onion… opening it makes me cry. 🥲💸",
            "I told my phone I needed space. It deleted 2,000 photos. 😭📱",
            "Why don't eggs tell secrets? They might crack up. 🥚😂",
        ]
        send(cid, random.choice(jokes), mid); return
    if cmd == "/compliment":
        compliments = [
            "Tiny reminder: you deserve kindness, including from yourself. 🌷",
            "Your vibe is one of a kind. Keep being you. ✨",
            "You made it through another day — give yourself a little credit. 💗",
            "Someone's day can get better just because you showed up. 🌸",
            "You've got your own kind of spark. Don't underestimate it. 🎀",
        ]
        send(cid, random.choice(compliments), mid); return
    if cmd == "/8ball":
        if len(parts) < 2:
            send(cid, "🎱 Ask me something! Example: /8ball Will I win today?", mid); return
        answers = ["Definitely yes ✨", "Looks promising 👀", "Probably! 💗", "Ask me again in a bit 😅", "I can't tell yet 🌙", "Not looking likely 🙈", "Maybe — trust your judgement too 🎀"]
        send(cid, "🎱 " + random.choice(answers), mid); return
    if cmd == "/choose":
        if len(parts) < 2 or "|" not in text:
            send(cid, "Usage: /choose tea | coffee", mid); return
        options = [x.strip() for x in text.split(maxsplit=1)[1].split("|") if x.strip()]
        if len(options) < 2:
            send(cid, "Give me at least two options, separated by |. Example: /choose tea | coffee", mid); return
        send(cid, "Hmm, I'd pick: " + random.choice(options) + " ✨", mid); return
    if cmd == "/mood":
        moods = ["🌸 Soft and cozy", "⚡ Full of energy", "🌙 Quiet little vibe", "🎀 Playful mood", "☕ Need a snack and a break", "✨ Main-character energy"]
        send(cid, "Today's random vibe: " + random.choice(moods), mid); return
    if cmd == "/roll":
        send(cid, f"🎲 You rolled a {random.randint(1, 6)}!", mid); return
    if cmd == "/about":
        send(cid, "╭━━━ 🎀 ABOUT YOZORINA 🎀 ━━━╮\n\n"
             "I'm YOZORINA — a friendly Telegram chat and community games bot.\n"
             "💬 Chat: private messages, group mentions and replies\n"
             "🎮 Games: virtual coins, challenges and player rankings\n"
             "🛡️ Group tools: welcome messages and chat reply controls\n\n"
             "🪙 All coins are fictional in-bot points.\n"
             "╰━━━━━━━━━━━━━━━━━━━━╯", mid); return
    if cmd == "/ping":
        send(cid, "🏓 Pong! YOZORINA is online and responding. 🎀", mid); return
    if cmd == "/stats":
        total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_groups = db.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        total_games = db.execute("SELECT COALESCE(SUM(games),0) FROM users").fetchone()[0]
        total_coins = db.execute("SELECT COALESCE(SUM(coins),0) FROM users").fetchone()[0]
        send(cid, "╭━━━ 📊 YOZORINA STATS ━━━╮\n\n"
             f"👥 Registered players: {total_users:,}\n"
             f"👨‍👩‍👧‍👦 Registered groups: {total_groups:,}\n"
             f"🎮 Games recorded: {total_games:,}\n"
             f"🪙 Coins in player wallets: {total_coins:,}\n"
             "╰━━━━━━━━━━━━━━━━━━━━╯", mid); return
    if cmd == "/rank":
        rank = db.execute("SELECT COUNT(*) + 1 FROM users WHERE coins > ?", (u["coins"],)).fetchone()[0]
        total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        send(cid, f"🏆 PLAYER RANK\n\n👤 {u['name']}\n💰 Wallet: {u['coins']:,} coins\n🥇 Position: #{rank} of {total}", mid); return
    if cmd == "/id":
        chat_type = chat.get("type", "unknown").title()
        chat_title = (chat.get("title") or "Private Chat").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines = [
            "╭━━━ 🎀 ʏᴏᴢᴏʀɪɴᴀ ━━━╮",
            "👤 ᴜꜱᴇʀ ɪᴅ",
            f"🆔 <code>{uid}</code>",
            "💬 ᴄʜᴀᴛ ɪᴅ",
            f"🆔 <code>{cid}</code>",
        ]
        if chat.get("type") in ("group", "supergroup"):
            lines.extend(["👥 ɢʀᴏᴜᴘ ɪᴅ", f"🆔 <code>{cid}</code>"])
        lines.extend([
            f"🏷️ ᴄʜᴀᴛ ᴛʏᴘᴇ: {chat_type}",
            f"📌 ᴄʜᴀᴛ ɴᴀᴍᴇ: {chat_title}",
            "╰━━━━━━━━━━━━━━━━━━╯",
        ])
        send(cid, "\n".join(lines), mid, parse_mode="HTML"); return
    if cmd in ("/bal", "/wallet"):
        target, name = target_from_message(msg, parts)
        if target is None:
            target, name = uid, u["name"]
        tu = user(target)
        if not tu:
            send(cid, "That player needs to start the bot or use a command first.", mid); return
        premium_status = ("💎 ACTIVE · " + nice_time(tu["premium_until"]-now())
                          if tu["premium_until"] > now() else "▫️ Standard")
        protection_status = ("🛡️ ACTIVE · " + nice_time(tu["protect_until"]-now())
                             if tu["protect_until"] > now() else "⚔️ Not active")
        player_name = ("@" + tu["username"]) if tu["username"] else tu["name"]
        send(cid,
             "╭━━━ 🎀 YOZORINA PROFILE ━━━╮\n"
             f"👤 Player: {player_name}\n"
             f"🆔 ID: {target}\n"
             f"💰 Wallet: {tu['coins']:,} coins 🪙\n"
             f"💎 Premium: {premium_status}\n"
             f"🛡️ Protection: {protection_status}\n"
             "💚 Game status: 🟢 ALIVE (fictional game status)\n"
             f"🎮 Games played: {tu['games']:,}\n"
             f"⚔️ Game wins/kills: {tu['kills']:,}\n"
             f"🦊 Successful steals: {tu['robbed']:,}\n"
             "╰━━━━━━━━━━━━━━━━━━━━╯\n"
             "✨ /profile for full details · /help for commands", mid); return
    if cmd in ("/claim", "/daily"):
        remain = 86400 - (now()-u["last_claim"])
        if remain > 0:
            send(cid, f"⏳ Daily claim is cooling down. Come back in {nice_time(remain)}.", mid); return
        db.execute("UPDATE users SET last_claim=? WHERE user_id=?", (now(), uid)); db.commit()
        change(uid, DAILY_COINS, "Daily claim")
        send(cid, f"🎁 Yayy! You got +{DAILY_COINS:,} coins. 💗", mid); return
    if cmd == "/profile":
        u = user(uid)
        premium_left = nice_time(u["premium_until"]-now()) if premium(uid) else "Not active"
        protect_left = nice_time(u["protect_until"]-now()) if protected(uid) else "Not active"
        next_claim = max(0, 86400-(now()-u["last_claim"]))
        claim_text = "READY NOW 🎁" if next_claim == 0 else f"In {nice_time(next_claim)}"
        player_name = ("@" + u["username"]) if u["username"] else u["name"]
        send(cid,
             "╭━━━ 🌷 YOZORINA PLAYER CARD ━━━╮\n"
             f"👤 Name: {player_name}\n"
             f"🆔 User ID: {uid}\n"
             f"💰 Wallet: {u['coins']:,} coins 🪙\n"
             f"💎 Premium: {premium_left}\n"
             f"🛡️ Protection: {protect_left}\n"
             "💚 Game status: 🟢 ALIVE (fictional game status)\n"
             f"🎮 Games played: {u['games']:,}\n"
             f"⚔️ Game wins/kills: {u['kills']:,}\n"
             f"🦊 Successful steals: {u['robbed']:,}\n"
             f"🎁 Daily reward: {claim_text}\n"
             "╰━━━━━━━━━━━━━━━━━━━━━━━━╯\n"
             "🧭 Quick commands: /bal · /claim · /history · /leaderboard", mid); return
    if cmd == "/premium":
        send(cid, "✨ Premium is " + (f"ACTIVE for {nice_time(user(uid)['premium_until']-now())} 💎" if premium(uid)
             else "not active right now. Ask the bot owner about premium."), mid); return
    if cmd == "/protect":
        if len(parts) < 2 or parts[1].lower() not in ("1d", "24h"):
            send(cid, "Usage: /protect 1d — costs 1,000 virtual coins and lasts 24 hours.", mid); return
        cost = 1000
        u = user(uid)
        if u["coins"] < cost:
            send(cid, f"You need {cost:,} coins for protection.", mid); return
        change(uid, -cost, "Protection 1 day")
        until = max(now(), u["protect_until"]) + 86400
        db.execute("UPDATE users SET protect_until=? WHERE user_id=?", (until, uid)); db.commit()
        send(cid, f"🛡 You're protected for 1 day! /rob and /kill won't affect you. (-{cost:,} coins)", mid); return
    if cmd in ("/leaderboard", "/top"):
        rows = db.execute("SELECT user_id,username,name,coins FROM users ORDER BY coins DESC, name COLLATE NOCASE ASC LIMIT 10").fetchall()
        lines = ["🏆 <b>YOZORINA • TOP 10</b>", "<i>Richest players</i>", ""]
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for i, r in enumerate(rows, 1):
            raw_name = ("@" + r["username"]) if r["username"] else (r["name"] or "Player")
            raw_name = " ".join(str(raw_name).split())
            if len(raw_name) > 19:
                raw_name = raw_name[:18] + "…"
            label = (raw_name.replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace('"', "&quot;"))
            rank = medals.get(i, f"{i:>2}.")
            # Fixed-width columns keep player names and coin totals visually aligned.
            safe_rank = rank.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"{safe_rank}  <a href=\"tg://user?id={int(r['user_id'])}\">{label}</a>  <b>🪙 {int(r['coins']):,}</b>")
        if not rows:
            lines.append("No players yet — play a game to appear here!")
        send(cid, "\n".join(lines), mid, parse_mode="HTML"); return
    if cmd == "/history":
        rows = db.execute("SELECT amount,reason,created FROM tx WHERE user_id=? ORDER BY id DESC LIMIT 8", (uid,)).fetchall()
        if not rows: send(cid, "No coin history yet.", mid); return
        lines = ["🧾 Recent coin history:"]
        for r in rows:
            sign = "+" if r["amount"] >= 0 else ""
            lines.append(f"{sign}{r['amount']:,} — {r['reason']}")
        send(cid, "\\n".join(lines), mid); return
    if cmd == "/sendcoins":
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            send(cid, "Usage: /sendcoins USER_ID AMOUNT", mid); return
        target, amount = int(parts[1]), int(parts[2])
        if target == uid or amount < 1 or u["coins"] < amount or not user(target):
            send(cid, "Check target ID, amount, and your balance. Target must have used the bot first.", mid); return
        change(uid, -amount, "Sent coins")
        change(target, amount, "Received coins")
        send(cid, f"💌 Sent {amount:,} coins to {user(target)['name']}!", mid); return

    # Legacy AI card challenge removed: card matches must always use real-player PvP.
    if cmd == "/cardcancel":
        send(cid, "There are no solo/AI card games. Real-player matches cancel automatically if nobody joins.", mid); return

    # Never fall through to the old AI card game when no real-player match exists.
    if cmd == "/flip" and len(parts) == 2 and parts[1].upper() in ("A", "B", "C", "D"):
        send(cid, "No active real-player Card match. Start one in a group with /card AMOUNT 2 and let another player join with /bet AMOUNT.", mid); return

    # No bot-opponent coin games: amount-based /flip and /bet only work through group PvP lobbies.
    if chat.get("type") == "private" and cmd in ("/flip", "/bet") and len(parts)==2 and parts[1].isdigit():
        send(cid, "Real-player games start in a group: /card AMOUNT 2, /rps AMOUNT, or /flip AMOUNT. Join with /bet AMOUNT."); return

    # Lightweight virtual-coin games; only dice remains a solo game.
    if cmd == "/dice":
        if len(parts) != 2 or not parts[1].isdigit():
            send(cid, f"Usage: {cmd} AMOUNT", mid); return
        amount = int(parts[1]); u = user(uid)
        if amount < 1 or amount > MAX_STAKE or u["coins"] < amount:
            send(cid, f"Stake must be 1–{MAX_STAKE:,} and within your balance.", mid); return
        change(uid, -amount, f"{cmd[1:]} stake")
        win = random.random() < 0.5
        if win:
            change(uid, amount*2, f"{cmd[1:]} win")
            result = "You won!"
        else:
            result = "Not this time!"
        db.execute("UPDATE users SET games=games+1 WHERE user_id=?", (uid,)); db.commit()
        extra = f"🎲 Result: {random.randint(1,6)}" if cmd == "/dice" else ("🪙 Heads!" if random.random()<0.5 else "🪙 Tails!")
        send(cid, f"{extra}\\n{result} {'+'+format(amount,',')+' coins' if win else '-'+format(amount,',')+' coins'}", mid); return
    if cmd == "/rps":
        send(cid, "No AI opponent is available. Start a real-player match in a group with /rps AMOUNT, then your opponent joins using /bet AMOUNT.", mid); return

    if cmd == "/rob":
        target, name = target_from_message(msg, parts)
        if not target:
            send(cid, "Reply to a player's message with /rob. You can't rob yourself.", mid); return
        if target == uid:
            send(cid, "You can't rob yourself, silly. 🌸", mid); return
        if protected(target):
            send(cid, f"🛡 {name} is protected! Your rob attempt was blocked.", mid); return
        target_user = user(target)
        if not target_user: send(cid, "That player needs to use the bot first.", mid); return
        if random.random() < 0.45 and target_user["coins"] > 0:
            amount = min(target_user["coins"], random.randint(50, 500))
            change(target, -amount, "Robbed by player")
            change(uid, amount, "Successful rob")
            db.execute("UPDATE users SET robbed=robbed+1 WHERE user_id=?", (uid,)); db.commit()
            send(cid, f"🦊 Rob successful! You got {amount:,} coins from {name}.", mid)
        else:
            fine = min(user(uid)["coins"], random.randint(0, 100))
            if fine:
                change(uid, -fine, "Rob attempt failed")
            send(cid, f"🚨 Rob failed! You were caught; fine: {fine:,} coins.", mid)
        return
    if cmd == "/kill":
        target, name = target_from_message(msg, parts)
        if not target:
            send(cid, "Reply to a player's message with /kill. This is only a fictional game action.", mid); return
        if target == uid:
            send(cid, "You can't target yourself.", mid); return
        if protected(target):
            send(cid, f"🛡 {name} has protection active. Nothing happened.", mid); return
        if random.random() < 0.5:
            amount = min(user(target)["coins"], random.randint(25, 250))
            if amount:
                change(target, -amount, "Lost coins in fictional kill game")
                change(uid, amount, "Won coins in fictional kill game")
            db.execute("UPDATE users SET kills=kills+1 WHERE user_id=?", (uid,)); db.commit()
            send(cid, f"⚔️ {name} was defeated in the game! You won {amount:,} coins. (Fictional game action)", mid)
        else:
            send(cid, f"💨 {name} escaped your attack! No coins changed.", mid)
        return
    if cmd == "/guess":
        key = f"guessgame:{cid}:{uid}"
        active = get_setting(key, "")
        if len(parts) != 2 or not parts[1].isdigit():
            send(cid, "🎯 Start: /guess AMOUNT\nThen guess: /guess NUMBER (1–500)", mid); return
        value = int(parts[1])
        if active:
            if not 1 <= value <= 500:
                send(cid, "🔢 Guess a number from 1 to 500.", mid); return
            try:
                secret_s, stake_s, tries_s, low_s, high_s = active.split("|")
                secret, stake, tries, low, high = int(secret_s), int(stake_s), int(tries_s), int(low_s), int(high_s)
            except Exception:
                set_setting(key, "")
                send(cid, "⚠️ Game state reset. Start again with /guess AMOUNT.", mid); return
            if value < low or value > high:
                send(cid, f"🎯 Guess inside the current range: {low}–{high}.", mid); return
            tries += 1
            if value == secret:
                payout = stake * 2
                change(uid, payout, "Guess game win payout")
                set_setting(key, "")
                send(cid, f"🎉 CORRECT!\n🔢 Number: {secret}\n🎯 Attempts: {tries}/10\n🪙 Prize: {payout:,} coins\n💰 Wallet: {user(uid)['coins']:,} coins", mid); return
            if value < secret:
                low = value + 1
                hint = "⬆️ Higher!"
            else:
                high = value - 1
                hint = "⬇️ Lower!"
            if tries >= 10 or low > high:
                set_setting(key, "")
                send(cid, f"❌ Game over! The number was {secret}.\n🎯 Attempts used: {tries}/10\n🪙 Your stake ({stake:,} coins) is lost.", mid); return
            set_setting(key, f"{secret}|{stake}|{tries}|{low}|{high}")
            send(cid, f"{hint}\n🎯 Current range: {low}–{high}\n🔄 Attempt: {tries}/10\nGuess again with /guess NUMBER", mid); return
        # No active game: this command starts a new game using the amount as stake.
        stake = value
        if stake < 1 or stake > MAX_STAKE:
            send(cid, f"💰 Enter a stake from 1 to {MAX_STAKE:,} coins.\nExample: /guess 100", mid); return
        if user(uid)["coins"] < stake:
            send(cid, f"❌ Not enough coins. Your balance: {user(uid)['coins']:,}.", mid); return
        change(uid, -stake, "Guess game stake")
        secret = random.randint(1, 500)
        set_setting(key, f"{secret}|{stake}|0|1|500")
        send(cid, f"🎯 <b>YURINA GUESS</b>\n🪙 Stake: {stake:,} coins\n🔢 I picked a secret number from 1–500.\n🎮 You have 10 guesses.\n\nNow guess with /guess NUMBER\nExample: /guess 250", mid, parse_mode="HTML"); return
    if cmd == "/guessstatus":
        key=f"guessgame:{cid}:{uid}"
        active=get_setting(key, "")
        if not active:
            send(cid, "🎯 No active game. Start with /guess AMOUNT.", mid); return
        try:
            _, stake, tries, low, high = active.split("|")
            send(cid, f"🎯 Active game\n🪙 Stake: {int(stake):,} coins\n🔢 Range: {low}–{high}\n🔄 Attempts used: {tries}/10", mid)
        except Exception:
            send(cid, "🎯 No active game. Start with /guess AMOUNT.", mid)
        return

    if cmd == "/setwelcome":
        if chat.get("type") not in ("group","supergroup"):
            send(cid, "This command works in groups.", mid); return
        try:
            member=api("getChatMember",{"chat_id":cid,"user_id":uid})
            admin=member.get("status") in ("administrator","creator")
        except Exception: admin=False
        if not admin and uid != OWNER_ID:
            send(cid, "Only group admins can change this.", mid); return
        if len(parts)!=2 or parts[1].lower() not in ("on","off"):
            send(cid, f"Usage: {cmd} on|off", mid); return
        key = "welcome"
        set_setting(f"{key}:{cid}",parts[1].lower())
        send(cid, f"🌸 {key.title()} is now {parts[1].lower()}.", mid); return
    if cmd == "/premiumadd":
        if uid != OWNER_ID:
            send(cid, "Owner only.", mid); return
        if len(parts)!=3 or not parts[1].isdigit() or not parts[2].isdigit():
            send(cid, "Usage: /premiumadd USER_ID DAYS", mid); return
        target, days=int(parts[1]),int(parts[2])
        tu=user(target)
        if not tu or not 1<=days<=3650:
            send(cid, "Target must have used the bot; days must be 1–3650.", mid); return
        until=max(now(),tu["premium_until"])+days*86400
        db.execute("UPDATE users SET premium_until=? WHERE user_id=?", (until,target)); db.commit()
        send(cid, f"💎 Premium enabled for {tu['name']} for {days} day(s).", mid); return
    if cmd == "/broadcast":
        if uid != OWNER_ID:
            send(cid, "Owner only.", mid); return
        if len(parts)<2:
            send(cid, "Usage: /broadcast your message", mid); return
        message=text.split(maxsplit=1)[1]
        rows=db.execute("SELECT chat_id FROM groups").fetchall()
        sent=0
        for row in rows:
            if send(row["chat_id"], "📢 YOZORINA UPDATE\n\n"+message): sent+=1
            time.sleep(0.05)
        send(cid, f"Broadcast sent to {sent} group(s).", mid); return

    # Command-help intent: answer command questions with local deterministic replies.
    low = text.lower()
    if not text.startswith("/") and any(k in low for k in ("game command", "games command", "game ka command", "game ke command", "commands kya", "command kya", "help command")):
        send(cid, "🎮 Games: /flip AMOUNT · /dice AMOUNT · /bet AMOUNT · /rps rock|paper|scissors AMOUNT · /card AMOUNT · /xoxo AMOUNT · /guess NUMBER\n💰 Wallet: /profile · /bal · /claim · /leaderboard\nFull list: /help", mid); return

    # AI handles normal conversation only. Every slash command has already been
    # handled locally above; unknown slash commands must never be interpreted by AI.
    if text.startswith("/"):
        return
    bot_username = get_setting("bot_username", "")
    is_private = chat.get("type") == "private"
    mentions_bot = bool(bot_username and re.search(r"@" + re.escape(bot_username) + r"\b", text, re.IGNORECASE))
    reply_to_bot = bool((msg.get("reply_to_message") or {}).get("from", {}).get("is_bot"))
    if is_private or mentions_bot or reply_to_bot:
        prompt = re.sub(r"@" + re.escape(bot_username) + r"\b", "", text, flags=re.IGNORECASE).strip() if bot_username else text
        # Run network AI calls in the background so slow AI responses never block
        # game commands, turn timers, auto-flips, refunds, or Telegram polling.
        threading.Thread(
            target=_ai_reply_worker,
            args=(cid, uid, u["name"] if u else "friend", prompt or text, mid),
            daemon=True,
        ).start()
        return

def xoxo_keyboard(gid, board):
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            label = board[i] if board[i].strip() else str(i + 1)
            row.append({"text": label, "callback_data": f"xoxo:{gid}:{i}"})
        rows.append(row)
    return {"inline_keyboard": rows}

def xoxo_board_text(gid, p1, p2, board, turn, note=""):
    a, b = user(p1), user(p2)
    current = user(turn)
    return (f"⭕ <b>XOXO · MATCH #{gid}</b>\n"
            f"❌ <a href=\"tg://user?id={p1}\">{html_escape(a['name'])}</a>  vs  "
            f"⭕ <a href=\"tg://user?id={p2}\">{html_escape(b['name'])}</a>\n"
            f"Turn: <a href=\"tg://user?id={turn}\">{html_escape(current['name'])}</a> {('❌' if turn == p1 else '⭕')}\n{note}")

def xoxo_winner(board):
    lines = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for a,b,c in lines:
        if board[a] != " " and board[a] == board[b] == board[c]: return board[a]
    return "draw" if all(x != " " for x in board) else None

def handle_callback(callback):
    """Inline dashboard buttons show the same full dashboard/help details."""
    callback_id = callback.get("id")
    data = callback.get("data", "")
    message = callback.get("message", {})
    chat = message.get("chat", {})
    cid = chat.get("id")
    if data.startswith("xoxo:"):
        try:
            _, gid_s, cell_s = data.split(":", 2)
            gid, cell = int(gid_s), int(cell_s)
            actor = int(callback.get("from", {}).get("id", 0))
            game = db.execute("SELECT * FROM pvp_games WHERE id=? AND mode='xoxo' AND status='playing'", (gid,)).fetchone()
            if not game:
                if callback_id: api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "This match has ended."})
                return
            if actor not in (int(game['p1']), int(game['p2'])):
                if callback_id: api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Only match players can move."})
                return
            state = json.loads(game['choices'] or '{}'); board = state.get('board', [' ']*9); turn = int(state.get('turn', game['p1']))
            if actor != turn:
                if callback_id: api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Wait for your turn."})
                return
            if not 0 <= cell < 9 or board[cell] != " ":
                if callback_id: api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "That square is already taken."})
                return
            mark = '❌' if actor == int(game['p1']) else '⭕'; board[cell] = mark
            outcome = xoxo_winner(board)
            if outcome:
                if outcome == 'draw':
                    change(int(game['p1']), int(game['stake']), 'XOXO draw refund'); change(int(game['p2']), int(game['stake']), 'XOXO draw refund'); note = '🤝 Draw · both bets refunded.'
                else:
                    winner = int(game['p1']) if outcome == '❌' else int(game['p2']); loser = int(game['p2']) if winner == int(game['p1']) else int(game['p1'])
                    change(winner, int(game['stake'])*2, 'XOXO winner pot'); db.execute("UPDATE users SET games=games+1 WHERE user_id IN (?,?)", (winner, loser)); note = f"🏆 Winner: <a href=\"tg://user?id={winner}\">{html_escape(user(winner)['name'])}</a> · +{int(game['stake'])*2:,} coins"
                db.execute("DELETE FROM pvp_games WHERE id=?", (gid,)); db.commit()
                try: api("editMessageText", {"chat_id": cid, "message_id": message.get('message_id'), "text": xoxo_board_text(gid, int(game['p1']), int(game['p2']), board, actor, note), "parse_mode":"HTML", "reply_markup": {"inline_keyboard": []}})
                except Exception: pass
            else:
                next_turn = int(game['p2']) if actor == int(game['p1']) else int(game['p1'])
                state = {"board": board, "turn": next_turn}
                db.execute("UPDATE pvp_games SET choices=?,created=? WHERE id=?", (json.dumps(state), now(), gid)); db.commit()
                try: api("editMessageText", {"chat_id": cid, "message_id": message.get('message_id'), "text": xoxo_board_text(gid, int(game['p1']), int(game['p2']), board, next_turn), "parse_mode":"HTML", "reply_markup": xoxo_keyboard(gid, board)})
                except Exception: pass
            if callback_id: api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Move accepted."})
        except Exception as e:
            if callback_id:
                try: api("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Couldn't process move."})
                except Exception: pass
        return
    if callback_id:
        try:
            api("answerCallbackQuery", {"callback_query_id": callback_id,
                                         "text": "Opening YOZORINA dashboard 🎀"})
        except urllib.error.HTTPError as e:
            # Telegram may reject an expired callback query (400); this is harmless.
            if e.code != 400:
                print("Callback acknowledgement error:", repr(e))
        except Exception as e:
            print("Callback acknowledgement error:", repr(e))
    if cid and data in ("dashboard_details", "dashboard_help"):
        send(cid, dashboard_text(), reply_markup=dashboard_keyboard())

def main():
    init_db()
    # Delete any stale webhook so long polling can work.
    try:
        api("deleteWebhook", {"drop_pending_updates": "false"})
    except Exception as e:
        print("deleteWebhook note:", repr(e))
    # Keep retrying if the phone temporarily has no internet/DNS resolution.
    while True:
        try:
            me = api("getMe")
            break
        except KeyboardInterrupt:
            print("Stopping YOZORINA.")
            return
        except Exception as e:
            print("Telegram connection unavailable:", type(e).__name__, str(e))
            print("Check internet/DNS. Retrying in 5 seconds; keep the console open.")
            time.sleep(5)
    bot_username = me.get("username", "")
    set_setting("bot_username", bot_username)
    print(f"Connected as @{bot_username}. Keep this console running.")
    print("AI chat enabled for ordinary messages; commands and game logic run locally.")
    offset=None
    while True:
        try:
            expire_card_queue()
            # Refund a PvP match if nobody joins within two minutes.
            for waiting in db.execute("SELECT * FROM pvp_games WHERE status='waiting'").fetchall():
                limit = 60
                if int(waiting['created']) > now()-limit:
                    continue
                change(int(waiting['p1']), int(waiting['stake']), "PvP match timeout refund")
                wait_text = "1 minute"
                send(int(waiting['chat_id']), f"⏰ No real opponent joined in {wait_text}. Match cancelled; {int(waiting['stake']):,} coins refunded.")
                db.execute("DELETE FROM pvp_games WHERE id=?", (waiting['id'],))
            # Cancel inactive non-card games (including XOXO) after 60 seconds,
            # refunding both players' full stakes exactly once.
            for stalled in db.execute("SELECT * FROM pvp_games WHERE status='playing' AND mode IN ('rps','coinflip','xoxo') AND created<=?", (now()-60,)).fetchall():
                p1, p2 = int(stalled['p1']), int(stalled['p2'])
                db.execute("DELETE FROM pvp_games WHERE id=? AND status='playing'", (stalled['id'],))
                if db.execute("SELECT changes()").fetchone()[0]:
                    if p2:
                        change(p1, int(stalled['stake']), "PvP timeout refund")
                        change(p2, int(stalled['stake']), "PvP timeout refund")
                    try:
                        if int(stalled['message_id'] or 0):
                            api('deleteMessage', {'chat_id': int(stalled['chat_id']), 'message_id': int(stalled['message_id'])})
                    except Exception: pass
                    send(int(stalled['chat_id']), "⏰ Match cancelled: no move/choice within 60 seconds. The game is cancelled and all bets are refunded.")
            db.commit()
            # PvP card: after 30 seconds, automatically choose a random card for
            # one player who has not moved. The timer resets, giving the other
            # player another 30 seconds; if still idle, their card auto-flips too.
            for card_game in db.execute("SELECT * FROM pvp_games WHERE status='playing' AND mode='card' AND created<=?", (now()-30,)).fetchall():
                try:
                    card_choices = json.loads(card_game['choices'] or '{}')
                    round_key = str(card_game['round_no'])
                    round_choices = card_choices.setdefault(round_key, {})
                    missing = [int(pid) for pid in (card_game['p1'], card_game['p2']) if str(pid) not in round_choices]
                    if not missing:
                        continue
                    scheduled_turn = int(card_choices.get('_current_turn', missing[0]))
                    auto_uid = scheduled_turn if scheduled_turn in missing else missing[0]
                    used_for_player=[]
                    for old_round, old_choices in card_choices.items():
                        if str(old_round).isdigit() and isinstance(old_choices, dict):
                            old_letter=old_choices.get(str(auto_uid))
                            if old_letter in ('A','B','C','D') and old_letter not in used_for_player:
                                used_for_player.append(old_letter)
                    available_for_player=[letter for letter in ('A','B','C','D') if letter not in used_for_player]
                    if not available_for_player:
                        continue
                    auto_letter = random.choice(available_for_player)
                    player_row = user(auto_uid)
                    fake_msg = {
                        'message_id': 0,
                        'chat': {'id': auto_uid, 'type': 'private'},
                        'from': {'id': auto_uid, 'first_name': player_row['name'] if player_row else 'Player', 'is_bot': False},
                        'text': '/flip ' + auto_letter
                    }
                    auto_name = html_escape(player_row['name'] if player_row else 'Player')
                    auto_notice = '⏰ 30 seconds are over! A card was auto-flipped for <a href="tg://user?id=' + str(auto_uid) + '">' + auto_name + '</a>.'
                    send(int(card_game['chat_id']), auto_notice, parse_mode='HTML')
                    handle(fake_msg)
                except Exception as auto_error:
                    print("PvP card auto-flip error:", repr(auto_error))
            # Legacy solo-card games also retain their 30-second auto-flip behavior.
            # Auto-flip an unused card after 30 seconds without a player choice.
            expired_games = db.execute(
                "SELECT * FROM card_games WHERE phase='user' AND created<=?",
                (now() - 30,)
            ).fetchall()
            for expired_game in expired_games:
                try:
                    available_letters = [letter for letter in ("A", "B", "C", "D")
                                         if letter not in json.loads(expired_game["used_letters"] or "[]")]
                    if available_letters:
                        auto_letter = random.choice(available_letters)
                        player_row = user(int(expired_game["user_id"]))
                        fake_msg = {
                            "message_id": 0,
                            "chat": {"id": int(expired_game["chat_id"])},
                            "from": {"id": int(expired_game["user_id"]),
                                     "first_name": player_row["name"] if player_row else "Player",
                                     "is_bot": False},
                            "text": "/flip " + auto_letter
                        }
                        send(int(expired_game["chat_id"]),
                             f"⏰ 30 seconds are over! Auto-flipping unused card <b>{auto_letter}</b>.",
                             parse_mode="HTML")
                        handle(fake_msg)
                except Exception as auto_error:
                    print("Auto-flip error:", repr(auto_error))
            data={"timeout":POLL_TIMEOUT,"allowed_updates":json.dumps(["message", "callback_query"])}
            if offset is not None: data["offset"]=offset
            updates=api("getUpdates",data)
            for upd in updates:
                offset=upd["update_id"]+1
                msg=upd.get("message")
                if msg:
                    try: handle(msg)
                    except Exception as e: print("Handler error:",repr(e))
                callback=upd.get("callback_query")
                if callback:
                    try: handle_callback(callback)
                    except Exception as e: print("Callback handler error:",repr(e))
        except KeyboardInterrupt:
            print("Stopping YOZORINA."); break
        except Exception as e:
            print("Telegram connection temporarily unavailable:", type(e).__name__)
            print("Checking connection again in 5 seconds. Keep the console open.")
            time.sleep(5)

if __name__ == "__main__":
    init_db()
    main()