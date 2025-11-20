# Improved LoveSense AI - integrated: trial, premium, admin panel, logs, orders, Flask API
import os, asyncio, json, time, requests, logging, datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from flask import Flask, jsonify, request as flask_request
from threading import Thread

# --- Config ---
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN') or os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID') or 935939738)
SERVER_PORT = int(os.getenv('PORT', 8080))
DATA_DIR = os.getenv('DATA_DIR', 'data')
LOGS_DIR = os.getenv('LOGS_DIR', 'logs')

USERS_FILE = os.path.join(DATA_DIR, 'users.json')
ORDERS_FILE = os.path.join(DATA_DIR, 'orders.json')
ACTIONS_LOG = os.path.join(LOGS_DIR, 'actions.log')
ERRORS_LOG = os.path.join(LOGS_DIR, 'errors.log')

CARD_NUMBER = "4400 4302 7114 7016"
CARD_OWNER = "Andrey.G"
PRICE_STR = "2500 ₸ / месяц"

# --- Ensure directories ---
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# --- Logging ---
logging.basicConfig(level=logging.INFO, filename=ERRORS_LOG, format='%(asctime)s %(levelname)s: %(message)s')
action_logger = logging.getLogger('actions')
action_logger.setLevel(logging.INFO)
ah = logging.FileHandler(ACTIONS_LOG, encoding='utf-8')
ah.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
action_logger.addHandler(ah)

# --- I18n ---
LANGS = {'en':'🇬🇧 English','ru':'🇷🇺 Русский','kz':'🇰🇿 Қазақша'}
DEFAULT_LANG = 'ru'
I18N = {
  'welcome':{'en':'Welcome to LoveSense AI — press a button to start.','ru':'Добро пожаловать в LoveSense AI — нажмите кнопку, чтобы начать.','kz':'LoveSense AI-ге қош келіңіз — бастау үшін батырманы басыңыз.'},
  'choose_lang':{'en':'Choose language','ru':'Выберите язык','kz':'Тілді таңдаңыз'},
  'mini':{'en':'Generating mini-analysis...','ru':'Генерируется мини-анализ...','kz':'Мини-талдау жасалып жатыр...'},
  'premium_active':{'en':'You have Premium ✅','ru':'У вас есть Premium ✅','kz':'Сізде Premium бар ✅'},
  'no_premium':{'en':'No active Premium. Buy to unlock.','ru':'Нет активного Premium. Купите, чтобы разблокировать.','kz':'Premium белсенді емес. Сатып алыңыз'}
}

# --- Helpers for JSON files ---
def read_json(path):
    try:
        with open(path,'r',encoding='utf-8') as f: return json.load(f)
    except Exception:
        return {} if path.endswith('.json') and os.path.basename(path)!='orders.json' else []

def write_json(path,obj):
    with open(path,'w',encoding='utf-8') as f:
        json.dump(obj,f,ensure_ascii=False,indent=2)

# Initialize files if missing
if not os.path.exists(USERS_FILE):
    write_json(USERS_FILE, {})
if not os.path.exists(ORDERS_FILE):
    write_json(ORDERS_FILE, [])

# --- Bot init ---
if not TELEGRAM_TOKEN:
    print('TELEGRAM_TOKEN is not set. Set environment variable and restart.')
    raise SystemExit(1)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# --- Utilities ---
def get_user(tid):
    users = read_json(USERS_FILE)
    return users.get(str(tid), {"trial_left":2, "premium": False, "premium_until":0, "lang": DEFAULT_LANG, "requests": 0})

def save_user(tid, info):
    users = read_json(USERS_FILE)
    users = users or {}
    users[str(tid)] = users.get(str(tid), {})
    users[str(tid)].update(info)
    write_json(USERS_FILE, users)

def check_premium(tid):
    u = get_user(tid)
    until = u.get('premium_until', 0)
    if until and int(time.time()) < int(until):
        return True
    return u.get('premium', False)

def grant_premium(tid, days=30):
    u = get_user(tid)
    u['premium'] = True
    u['premium_until'] = int(time.time()) + days*24*3600
    save_user(tid, u)
    action_logger.info(f"grant_premium {tid} days={days} by_admin")

def revoke_premium(tid):
    u = get_user(tid)
    u['premium'] = False
    u['premium_until'] = 0
    save_user(tid, u)
    action_logger.info(f"revoke_premium {tid} by_admin")

def add_order_manual(tid):
    orders = read_json(ORDERS_FILE) or []
    entry = {"id": f"man_{int(time.time())}", "telegram_id": str(tid), "timestamp": int(time.time()), "status": "pending"}
    orders.append(entry)
    write_json(ORDERS_FILE, orders)
    action_logger.info(f"manual_order {tid}")
    return entry

def list_pending_orders():
    orders = read_json(ORDERS_FILE) or []
    return [o for o in orders if o.get('status')=='pending']

def update_order_status(order_id, status):
    orders = read_json(ORDERS_FILE) or []
    for o in orders:
        if o.get('id') == order_id:
            o['status'] = status
    write_json(ORDERS_FILE, orders)

def hf_request(prompt):
    HF = os.getenv('HF_API_URL','https://api-inference.huggingface.co/models/Qwen/Qwen2.5-7B-Instruct')
    KEY = os.getenv('HF_API_KEY')
    if not KEY: return "AI not configured. Ask admin to set HF_API_KEY."
    try:
        r = requests.post(HF, headers={'Authorization':f'Bearer {KEY}'}, json={'inputs':prompt,'parameters':{'max_new_tokens':300}}, timeout=25)
        j = r.json()
        if isinstance(j, list) and 'generated_text' in j[0]: return j[0]['generated_text']
        if isinstance(j, dict) and 'generated_text' in j: return j['generated_text']
        return str(j)
    except Exception as e:
        logging.exception('hf_request')
        return f"AI error: {e}"

# --- Keyboards ---
def main_kb(user_id, lang):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton({'en':'🧠 Mini personality','ru':'🧠 Мини-анализ','kz':'🧠 Мини-талдау'}[lang], callback_data='mini')],
        [InlineKeyboardButton({'en':'❤️ Compatibility','ru':'❤️ Совместимость','kz':'❤️ Сәйкестік'}[lang], callback_data='compat')],
        [InlineKeyboardButton({'en':'🔮 AI Advice','ru':'🔮 Совет AI','kz':'🔮 AI кеңес'}[lang], callback_data='advice')],
        [InlineKeyboardButton({'en':'💎 Premium analysis','ru':'💎 Премиум анализ','kz':'💎 Премиум талдау'}[lang], callback_data='premium')],
        [InlineKeyboardButton({'en':'💳 Buy Premium','ru':'💳 Купить Premium','kz':'💳 Premium сатып алу'}[lang], callback_data='buy')],
        [InlineKeyboardButton({'en':'📊 My status','ru':'📊 Мой статус','kz':'📊 Жағдайым'}[lang], callback_data='status')],
        [InlineKeyboardButton({'en':'🌐 Language','ru':'🌐 Язык','kz':'🌐 Тіл'}[lang], callback_data='lang')],
    ])
    # admin button visible only to ADMIN_ID
    if user_id == ADMIN_ID:
        kb.inline_keyboard.append([InlineKeyboardButton(text='🛠 Admin panel', callback_data='admin_panel')])
    return kb

def lang_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(LANGS[k], callback_data=f"set_lang_{k}") for k in LANGS]])
    return kb

def admin_main_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📊 Статистика', callback_data='adm_stats'), InlineKeyboardButton(text='👤 Пользователи', callback_data='adm_users')],
        [InlineKeyboardButton(text='💳 Заявки', callback_data='adm_orders'), InlineKeyboardButton(text='📝 Логи', callback_data='adm_logs')],
        [InlineKeyboardButton(text='⭐ Управлять Premium', callback_data='adm_manage'), InlineKeyboardButton(text='⬅️ Назад', callback_data='adm_back')],
    ])
    return kb

# --- Bot handlers (based on your original bot.py) ---
@dp.message(Command(commands=['start']))
async def start(m: types.Message):
    tid = m.from_user.id
    users = read_json(USERS_FILE) or {}
    if str(tid) not in users:
        users[str(tid)] = {"trial_left":2, "premium": False, "premium_until":0, "lang": DEFAULT_LANG, "requests":0}
        write_json(USERS_FILE, users)
    lang = users[str(tid)].get('lang', DEFAULT_LANG)
    await m.answer(I18N['welcome'][lang], reply_markup=main_kb(tid, lang))
    action_logger.info(f"start {tid}")

@dp.callback_query(lambda c: c.data and c.data.startswith('set_lang_'))
async def set_lang_cb(c: types.CallbackQuery):
    lang = c.data.split('_')[2]
    users = read_json(USERS_FILE) or {}
    u = users.get(str(c.from_user.id), {})
    u['lang'] = lang
    users[str(c.from_user.id)] = u
    write_json(USERS_FILE, users)
    await c.answer("Language set ✅", show_alert=False)
    await c.message.delete()
    await c.message.answer(I18N['welcome'][lang], reply_markup=main_kb(c.from_user.id, lang))

@dp.callback_query(lambda c: c.data == 'lang')
async def lang_menu(c: types.CallbackQuery):
    await c.answer()
    await c.message.answer("Choose language / Выберите язык / Тілді таңдаңыз", reply_markup=lang_kb())

# Free AI handlers with trial handling
async def _use_trial_or_premium(user_id, increment_request=True):
    users = read_json(USERS_FILE) or {}
    u = users.get(str(user_id), {"trial_left":2, "premium": False, "premium_until":0, "lang": DEFAULT_LANG, "requests":0})
    now = int(time.time())
    # check premium by expiry
    if u.get('premium_until',0) and now < int(u.get('premium_until')):
        premium = True
    else:
        premium = u.get('premium', False)
    if increment_request:
        u['requests'] = u.get('requests',0) + 1
    if not premium:
        # trial management
        u['trial_left'] = max(0, u.get('trial_left',2))
        if u['trial_left'] <= 0:
            users[str(user_id)] = u
            write_json(USERS_FILE, users)
            return False, u
        else:
            u['trial_left'] = u.get('trial_left',2) - 1
    users[str(user_id)] = u
    write_json(USERS_FILE, users)
    return True, u

@dp.callback_query(lambda c: c.data == 'mini')
async def mini_cb(c: types.CallbackQuery):
    lang = get_user(c.from_user.id).get('lang', DEFAULT_LANG)
    allowed, u = await _use_trial_or_premium(c.from_user.id)
    if not allowed:
        await c.answer("Trial exhausted. Buy Premium to continue.", show_alert=True)
        await c.message.answer("Your trial is over. Please buy Premium.", reply_markup=main_kb(c.from_user.id, lang))
        return
    await c.answer(I18N['mini'][lang])
    prompt = f"Short 3-sentence mini analysis for: {c.from_user.full_name} (language: {lang})"
    res = hf_request(prompt)
    await c.message.answer(res)
    action_logger.info(f"mini {c.from_user.id}")

@dp.callback_query(lambda c: c.data == 'compat')
async def compat_cb(c: types.CallbackQuery):
    lang = get_user(c.from_user.id).get('lang', DEFAULT_LANG)
    allowed, u = await _use_trial_or_premium(c.from_user.id)
    if not allowed:
        await c.answer("Trial exhausted. Buy Premium to continue.", show_alert=True)
        await c.message.answer("Your trial is over. Please buy Premium.", reply_markup=main_kb(c.from_user.id, lang))
        return
    await c.answer("Compatibility analysis...")
    prompt = f"Short compatibility analysis for: {c.from_user.full_name} (lang:{lang})"
    res = hf_request(prompt)
    await c.message.answer(res)
    action_logger.info(f"compat {c.from_user.id}")

@dp.callback_query(lambda c: c.data == 'advice')
async def advice_cb(c: types.CallbackQuery):
    lang = get_user(c.from_user.id).get('lang', DEFAULT_LANG)
    allowed, u = await _use_trial_or_premium(c.from_user.id)
    if not allowed:
        await c.answer("Trial exhausted. Buy Premium to continue.", show_alert=True)
        await c.message.answer("Your trial is over. Please buy Premium.", reply_markup=main_kb(c.from_user.id, lang))
        return
    await c.answer("AI Advice...")
    prompt = f"Give a short actionable advice for: {c.from_user.full_name} (lang:{lang})"
    res = hf_request(prompt)
    await c.message.answer(res)
    action_logger.info(f"advice {c.from_user.id}")

@dp.callback_query(lambda c: c.data == 'buy')
async def buy_cb(c: types.CallbackQuery):
    await c.answer()
    lang = get_user(c.from_user.id).get('lang', DEFAULT_LANG)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Перевести на карту: {CARD_NUMBER}", callback_data='copy_card')],
        [InlineKeyboardButton(text="Я оплатил", callback_data='i_paid')],
        [InlineKeyboardButton({'en':'Back','ru':'Назад','kz':'Артқа'}[lang], callback_data='back')],
    ])
    await c.message.answer(f"{PRICE_STR}\nПереведите на карту: {CARD_NUMBER}\nИмя: {CARD_OWNER}", reply_markup=kb)

@dp.callback_query(lambda c: c.data == 'copy_card')
async def copy_card_cb(c: types.CallbackQuery):
    await c.answer('Скопировано (вставьте в приложение банка).', show_alert=True)

@dp.callback_query(lambda c: c.data == 'i_paid')
async def i_paid_cb(c: types.CallbackQuery):
    tid = c.from_user.id
    add_order_manual(tid)
    await c.answer('Thanks, awaiting verification. Admin notified.')
    try:
        await bot.send_message(ADMIN_ID, f"Поступила заявка на проверку оплаты от @{c.from_user.username or c.from_user.full_name} (ID: {tid}).", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton('✔ Grant Premium', callback_data=f'admin_grant:{tid}')],
                [InlineKeyboardButton('✖ Reject', callback_data=f'admin_reject:{tid}')],
            ]))
    except Exception as e:
        logging.exception('notify admin failed')

@dp.callback_query(lambda c: c.data and c.data.startswith('admin_grant:'))
async def admin_grant_cb(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: await c.answer('Unauthorized', show_alert=True); return
    tid = c.data.split(':')[1]
    grant_premium(tid)
    await c.answer('Premium granted ✅', show_alert=True)
    await c.message.edit_text(f'Premium granted for user {tid} ✅')

@dp.callback_query(lambda c: c.data and c.data.startswith('admin_reject:'))
async def admin_reject_cb(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: await c.answer('Unauthorized', show_alert=True); return
    tid = c.data.split(':')[1]
    # remove pending orders
    orders = read_json(ORDERS_FILE) or []
    orders = [o for o in orders if o.get('telegram_id') != str(tid)]
    write_json(ORDERS_FILE, orders)
    await c.answer('Payment rejected', show_alert=True)
    await c.message.edit_text(f'Payment rejected for user {tid} ❌')

@dp.callback_query(lambda c: c.data == 'premium')
async def premium_menu(c: types.CallbackQuery):
    lang = get_user(c.from_user.id).get('lang', DEFAULT_LANG)
    if check_premium(c.from_user.id):
        await c.answer(I18N['premium_active'][lang])
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton({'en':'🧠 Deep portrait','ru':'🧠 Глубокий портрет','kz':'🧠 Терең портрет'}[lang], callback_data='deep_portrait')],
            [InlineKeyboardButton({'en':'❤️ Relationship PRO','ru':'❤️ Relationship PRO','kz':'❤️ Relationship PRO'}[lang], callback_data='relationship_pro')],
            [InlineKeyboardButton({'en':'🔍 Partner analysis','ru':'🔍 Разбор партнёра','kz':'🔍 Серіктес талдауы'}[lang], callback_data='partner')],
            [InlineKeyboardButton({'en':'🔁 Back','ru':'🔁 Назад','kz':'🔁 Артқа'}[lang], callback_data='back')],
        ])
        await c.message.answer('Premium menu:', reply_markup=kb)
    else:
        await c.answer(I18N['no_premium'][lang])

@dp.callback_query(lambda c: c.data == 'deep_portrait')
async def deep_portrait_cb(c: types.CallbackQuery):
    await c.answer('Generating deep portrait...')
    prompt = f"Detailed psychological portrait for: {c.from_user.full_name}"
    res = hf_request(prompt)
    await c.message.answer(res)

@dp.callback_query(lambda c: c.data == 'relationship_pro')
async def relpro_cb(c: types.CallbackQuery):
    await c.answer('Generating Relationship PRO...')
    prompt = f"Detailed relationship analysis for: {c.from_user.full_name}"
    res = hf_request(prompt)
    await c.message.answer(res)

@dp.callback_query(lambda c: c.data == 'partner')
async def partner_cb(c: types.CallbackQuery):
    await c.answer('Generating partner analysis...')
    prompt = f"Analyze partner behavior for: {c.from_user.full_name}"
    res = hf_request(prompt)
    await c.message.answer(res)

@dp.callback_query(lambda c: c.data == 'status')
async def status_cb(c: types.CallbackQuery):
    u = get_user(c.from_user.id)
    if check_premium(c.from_user.id):
        await c.answer('You have Premium ✅')
        await c.message.answer('Ваш Premium активен ✅')
    else:
        await c.answer('No Premium')
        await c.message.answer('У вас нет Premium. Купите, чтобы получить доступ.')

@dp.callback_query(lambda c: c.data == 'back')
async def back_cb(c: types.CallbackQuery):
    await c.message.answer('Back to menu', reply_markup=main_kb(get_user(c.from_user.id).get('lang', DEFAULT_LANG)))
# --- Admin panel callbacks ---
@dp.callback_query(lambda c: c.data == 'admin_panel')
async def admin_panel_cb(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        await c.answer('Unauthorized', show_alert=True); return
    await c.answer(); await c.message.answer('Admin panel', reply_markup=admin_main_kb())

@dp.callback_query(lambda c: c.data and c.data.startswith('adm_'))
async def adm_cb(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        await c.answer('Unauthorized', show_alert=True); return
    cmd = c.data
    if cmd == 'adm_stats':
        users = read_json(USERS_FILE) or {}
        total = len(users)
        premium = sum(1 for u in users.values() if u.get('premium_until',0) and int(time.time()) < int(u.get('premium_until')))
        trials = sum(1 for u in users.values() if u.get('trial_left',0) > 0)
        text = f"📊 Статистика\n\nВсего пользователей: {total}\nPremium активны: {premium}\nТриал остался у: {trials}"
        await c.message.edit_text(text, reply_markup=admin_main_kb()); await c.answer()
    elif cmd == 'adm_users':
        users = read_json(USERS_FILE) or {}
        lines = []
        for uid,u in list(users.items())[:100]:
            pu = "Да" if u.get('premium_until',0) and int(time.time()) < int(u.get('premium_until')) else "Нет"
            lines.append(f"{uid} | premium:{pu} | trial_left:{u.get('trial_left',0)}")
        await c.message.edit_text("👥 Пользователи:\n" + ("\n".join(lines) if lines else "Нет пользователей"), reply_markup=admin_main_kb()); await c.answer()
    elif cmd == 'adm_orders':
        orders = read_json(ORDERS_FILE) or []
        lines = []
        for o in orders[::-1][:50]:
            ts = datetime.datetime.fromtimestamp(o.get('timestamp',0)).strftime("%Y-%m-%d %H:%M")
            lines.append(f"{o.get('telegram_id')} | {ts} | {o.get('status')} | {o.get('id')}")
        await c.message.edit_text("💳 Заявки:\n" + ("\n".join(lines) if lines else "Нет заявок"), reply_markup=admin_main_kb()); await c.answer()
    elif cmd == 'adm_logs':
        if os.path.exists(ACTIONS_LOG):
            await c.message.answer_document(FSInputFile(ACTIONS_LOG))
        else:
            await c.message.answer("Логов нет."); await c.answer()
    elif cmd == 'adm_manage':
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Выдать Premium (ID)', callback_data='adm_grant_prompt')],
            [InlineKeyboardButton(text='Забрать Premium (ID)', callback_data='adm_revoke_prompt')],
            [InlineKeyboardButton(text='⬅️ Назад', callback_data='adm_back')],
        ])
        await c.message.edit_text("⭐ Управление Premium", reply_markup=kb); await c.answer()
    elif cmd == 'adm_grant_prompt':
        await c.message.answer("Отправь: grant:<user_id>"); await c.answer()
    elif cmd == 'adm_revoke_prompt':
        await c.message.answer("Отправь: revoke:<user_id>"); await c.answer()
    elif cmd == 'adm_back':
        await c.message.edit_text("Admin panel", reply_markup=admin_main_kb()); await c.answer()

@dp.message(lambda m: m.from_user.id == ADMIN_ID and m.text and (m.text.startswith('grant:') or m.text.startswith('revoke:')))
async def admin_text_actions(message: types.Message):
    parts = message.text.split(':',1)
    if len(parts)<2: return
    action, uid = parts[0], parts[1].strip()
    if action == 'grant':
        grant_premium(uid)
        await message.reply(f"✅ Premium выдан пользователю {uid}")
    elif action == 'revoke':
        revoke_premium(uid)
        await message.reply(f"❌ Premium отозван у {uid}")

# --- Flask API (for Replit web) ---
app = Flask('lovesense_api')

@app.route('/user_status/<uid>', methods=['GET'])
def api_user_status(uid):
    users = read_json(USERS_FILE) or {}
    return jsonify(users.get(str(uid), {}))

@app.route('/orders', methods=['GET'])
def api_orders():
    return jsonify(read_json(ORDERS_FILE) or [])

@app.route('/admin/grant', methods=['POST'])
def api_admin_grant():
    data = flask_request.get_json() or {}
    if str(data.get('admin_id')) != str(ADMIN_ID): return jsonify({'error':'unauthorized'}), 403
    uid = data.get('uid')
    grant_premium(uid)
    return jsonify({'ok':True})

@app.route('/admin/revoke', methods=['POST'])
def api_admin_revoke():
    data = flask_request.get_json() or {}
    if str(data.get('admin_id')) != str(ADMIN_ID): return jsonify({'error':'unauthorized'}), 403
    uid = data.get('uid')
    revoke_premium(uid)
    return jsonify({'ok':True})

def run_flask():
    app.run(host='0.0.0.0', port=SERVER_PORT)

# --- Runner ---
def start_bot():
    asyncio.run(dp.start_polling(bot))

if __name__ == '__main__':
    # run flask in background thread for Replit
    t = Thread(target=run_flask, daemon=True)
    t.start()
    print("Starting bot...")
    start_bot()
