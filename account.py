"""Pharmacist sign-in and a simulated subscription.

Accounts gate the API so every audit entry names a pharmacist. Checkout is a
simulation: no card number is collected or stored and nothing is charged.
"""
import datetime,hashlib,hmac,secrets

PLANS=[
    {'id':'trial','name':'免費試用','price':0,'period':'30 天','seats':1,'checks':30,'features':['每月 30 次用藥安全檢查','病歷建立與查詢','仿單與交互作用來源核對']},
    {'id':'personal','name':'藥師個人','price':490,'period':'月','seats':1,'checks':500,'features':['每月 500 次用藥安全檢查','食藥署電子仿單更新','病歷 JSON 匯入匯出','MedHop 研究路徑']},
    {'id':'pharmacy','name':'社區藥局','price':1990,'period':'月','seats':3,'checks':None,'recommended':True,'features':['3 位藥師帳號','不限次數用藥安全檢查','藥局共用藥品資料庫','藥師處置與稽核紀錄']},
    {'id':'hospital','name':'醫院','price':None,'period':'','seats':None,'checks':None,'contact':True,'features':['依院內藥師人數規劃','院內病歷系統對接','地端模型部署','專人導入與教育訓練']},
]
PLAN_MAP={p['id']:p for p in PLANS}
DEMO='demo'
# Seed accounts: the demo account is already subscribed; the second one walks through checkout.
SEED=[(DEMO,'陳藥師','示範社區藥局',None,'pharmacy'),('lin','林藥師','安心藥局','medhop2026',None)]

def today():return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
def hash_password(password,salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+'$'+hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),200_000).hex()
def check_password(password,stored):
    salt=stored.split('$',1)[0]
    return hmac.compare_digest(hash_password(password,salt),stored)

class Accounts:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS accounts(username TEXT PRIMARY KEY,display_name TEXT,pharmacy TEXT,password TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS auth_sessions(sid TEXT PRIMARY KEY,username TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS subscriptions(username TEXT PRIMARY KEY,plan TEXT,started TEXT,renews TEXT,payment TEXT);
            CREATE TABLE IF NOT EXISTS usage(username TEXT,month TEXT,checks INTEGER,PRIMARY KEY(username,month));''')
            for username,name,pharmacy,password,plan in SEED:
                if db.execute('SELECT 1 FROM accounts WHERE username=?',(username,)).fetchone():continue
                db.execute('INSERT INTO accounts VALUES(?,?,?,?,?)',(username,name,pharmacy,hash_password(password or secrets.token_urlsafe(24)),today().isoformat(timespec='seconds')))
                if plan:self._subscribe(db,username,plan,'示範帳號預設方案')
    def _subscribe(self,db,username,plan,payment):
        start=today().date()
        db.execute('INSERT OR REPLACE INTO subscriptions VALUES(?,?,?,?,?)',(username,plan,start.isoformat(),(start+datetime.timedelta(days=30)).isoformat(),payment))
    def login(self,sid,username,password):
        with self.store.connect() as db:
            row=db.execute('SELECT password FROM accounts WHERE username=?',(username,)).fetchone()
            if username==DEMO or not row or not check_password(password,row[0]):raise ValueError('帳號或密碼不正確')
            db.execute('INSERT OR REPLACE INTO auth_sessions VALUES(?,?,?)',(sid,username,today().isoformat(timespec='seconds')))
    def demo(self,sid):
        with self.store.connect() as db:db.execute('INSERT OR REPLACE INTO auth_sessions VALUES(?,?,?)',(sid,DEMO,today().isoformat(timespec='seconds')))
    def logout(self,sid):
        with self.store.connect() as db:db.execute('DELETE FROM auth_sessions WHERE sid=?',(sid,))
    def username(self,sid):
        with self.store.connect() as db:row=db.execute('SELECT username FROM auth_sessions WHERE sid=?',(sid,)).fetchone()
        return row[0] if row else None
    def subscribed(self,username):
        with self.store.connect() as db:row=db.execute('SELECT renews FROM subscriptions WHERE username=?',(username,)).fetchone()
        return bool(row) and row[0]>=today().date().isoformat()
    def checkout(self,sid,plan):
        username=self.username(sid)
        if not username:raise ValueError('請先登入')
        if PLAN_MAP.get(plan,{}).get('contact'):raise ValueError('醫院方案需依院內需求規劃，請與團隊聯絡')
        if plan not in PLAN_MAP:raise ValueError('找不到這個方案')
        with self.store.connect() as db:self._subscribe(db,username,plan,'模擬付款（示範卡 •••• 4242，未實際扣款）')
    def record_check(self,sid):
        username=self.username(sid)
        if not username:return
        with self.store.connect() as db:db.execute('INSERT INTO usage VALUES(?,?,1) ON CONFLICT(username,month) DO UPDATE SET checks=checks+1',(username,today().strftime('%Y-%m')))
    def status(self,sid):
        username=self.username(sid)
        result={'account':None,'subscription':None,'plans':PLANS}
        if not username:return result
        with self.store.connect() as db:
            account=db.execute('SELECT username,display_name,pharmacy FROM accounts WHERE username=?',(username,)).fetchone()
            sub=db.execute('SELECT plan,started,renews,payment FROM subscriptions WHERE username=?',(username,)).fetchone()
            used=db.execute('SELECT checks FROM usage WHERE username=? AND month=?',(username,today().strftime('%Y-%m'))).fetchone()
        result['account']={'username':account[0],'name':account[1],'pharmacy':account[2],'demo':username==DEMO}
        if sub and sub[2]>=today().date().isoformat():
            result['subscription']={'plan':sub[0],'name':PLAN_MAP[sub[0]]['name'],'started':sub[1],'renews':sub[2],'payment':sub[3],'checks_used':used[0] if used else 0,'checks_limit':PLAN_MAP[sub[0]]['checks']}
        return result
    def actor(self,sid):
        """Audit name for a session: the signed-in pharmacist, else None."""
        username=self.username(sid)
        if not username:return None
        with self.store.connect() as db:row=db.execute('SELECT display_name,pharmacy FROM accounts WHERE username=?',(username,)).fetchone()
        return f'pharmacist:{username}（{row[0]} · {row[1]}）'
