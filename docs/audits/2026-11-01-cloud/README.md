# AJob — Independent Cloud Security & Mobile QA Audit

תאריך: 1 בנובמבר 2026, Asia/Jerusalem.

## החלטה

**CONDITIONAL לסקירת מיזוג; אין אישור Deployment בדוח הזה.**

לא שוחזר ממצא Critical או High בקוד המועמד שנבדק. קיימים פער UX מוכח ופער
תאימות Cookie בבדיקת Windows WebKit. לפני פריסה חובה להשלים את שערי האימות
בסעיף 7. הצלחת mocks או gateway מצומצם אינה אישור להרשאות הפרויקט המנוהל.

PR #24 מתאים לסקירת מיזוג של ההגנה על מסלול SQL. PR #25 מתאים לסקירת מיזוג
מותנית, על בסיס #24 ובכפוף לבדיקות הירוקות ולממצאים שלהלן. אף PR לא מוזג
במסגרת ה־Audit; לא שונה קוד האפליקציה או קובץ שהיה באחריות Main Dev.

## 1. גרסאות וגבולות

| רכיב | Snapshot |
|---|---|
| main שפורסם בזמן פתיחת הסקירה | `dc647c569ada04b74abeafd508a931a9670140c5` |
| PR #24 | `716bf985684644b99345af372a92e6432badd4de` |
| PR #25 הסופי, כולל #24 | `decbac225ba1d13f7c2f7c55c85afec19133d49b` |
| בסיס הבדיקות הראשוני | `95624a7970af862f2c46c96534228d1d355b921b` |
| ענף Audit נפרד | `codex/independent-cloud-audit` |

הדלתא ל־HEAD הסופי נבדקה: סימון בעלות על roles, הגנות מפני role קיים שאינו
שייך לאפליקציה, ותיקון fixtures של PostgreSQL. קוד Auth, transport והמובייל
לא השתנה; תוצאות שכבר הושלמו נשמרו. חוזה ה־SQL נבדק שוב לאחר העדכון.

הסקירה בוצעה בקוד ובסביבות סינתטיות בלבד. לא נעשה שימוש ב־Supabase חי,
בסיסמת משתמש אמיתית, ב־Render חי או בנתוני הדשבורד המקומי. לא הופעלו Scanner,
SMTP, Cron או Recovery. לא בוצעו Deployment, מיגרציית Production או העברת Notes.

## 2. ממצאים לפי חומרה

| חומרה | מזהה | תוצאה |
|---|---|---|
| Critical | — | לא שוחזר ממצא בגבולות הסקירה; אין בכך הוכחת היעדר פגיעויות. |
| High | — | לא שוחזר מעקף הרשאה בקוד הסופי. פערי ההפעלה בסעיף 7 עדיין חוסמים Deployment. |
| Medium | M-01 | טקסט Notes בענן מציג הצהרת אחסון שגויה. |
| Low / פער תאימות | W-01 | Windows WebKit שולח Cookie בבדיקת כניסה cross-site למרות `SameSite=Strict`; כתיבה זרה עדיין נדחית. |

### M-01 — הצהרת אחסון שגויה ב־Notes בענן

**ראיה:** [`dashboard.html:137`](https://github.com/yoavbenNun/AJob/blob/decbac225ba1d13f7c2f7c55c85afec19133d49b/src/web/templates/dashboard.html#L137)
מציג `Saved only on this computer`, גם כש־`cloud_mode` פעיל וה־Notes נשמרים
ב־backend המתמשך. ה־context כבר מכיל `cloud_mode` ב־
[`app.py:291`](https://github.com/yoavbenNun/AJob/blob/decbac225ba1d13f7c2f7c55c85afec19133d49b/src/web/app.py#L291).

**שחזור:** לפתוח app עם CloudServices סינתטיים, להתחבר, לפתוח עורך Note ברשימת
המשרות. הטקסט השגוי מופיע גם ב־375px וגם ב־390px בשני המנועים. זהו פער UX
ופרטיות, לא ראיה לכך שה־Notes אכן נשמרים בדיסק הזמני של Render.

**תיקון ממוקד:** טקסט מותנה ב־`cloud_mode`; לשמר את ההסבר המקומי ב־Local.
מצורף [patch מוצע](notes-storage-hint.patch), שעבר `git apply --check` בלבד.
הוא **לא הוחל** על קוד האפליקציה. יש לתקן לפני Deployment. בדיקת הרגרסיה
`test_cloud_note_storage_hint_is_cloud_accurate` מסומנת `xfail(strict=True)`
כממצא ידוע; היא אינה נספרת כבדיקה שעברה.

### W-01 — פער Windows WebKit ב־SameSite

**ראיות:** השרת שולח בפועל `Secure`, `HttpOnly`, `SameSite=Strict` מ־
[`cloud_auth.py:249`](https://github.com/yoavbenNun/AJob/blob/decbac225ba1d13f7c2f7c55c85afec19133d49b/src/web/cloud_auth.py#L249).
בבדיקת HTTPS לולאתית עם אתר תוקף שהוחזר כולו מתוך route fixture:

| מדידה | Chromium / Windows | WebKit / Windows |
|---|---|---|
| Cookie נשלח בבקרת same-site | כן | כן |
| Cookie נשלח בכניסה cross-site ראשונית | לא | כן |
| כתיבת סטטוס cross-site | נדחתה | נדחתה ב־403 |
| הסטטוס הסינתטי השתנה | לא | לא |

המדידה מבוססת על דגלי `Set-Cookie` וקליטת בקשות ב־ASGI, לא רק metadata של
Playwright: ב־Windows WebKit גם metadata של Cookie ושל headers היה חלקי.
רק ה־assertion האחרון של אי־שליחת Cookie מסומן כ־strict xfail עבור Windows
WebKit; כשל setup או CSRF לפניו נשאר כשל רגיל. אין כאן טענה למעקף ב־Safari.

**פעולה:** להריץ את התרחיש על Safari אמיתי באייפון לפני Deployment. אין הצדקה
לשנות את קוד השרת שכבר שולח Strict כדי לתקן תוצאת port של Windows.
[תיעוד WebKit](https://docs.webkit.org/Ports/Introduction.html) מבדיל במפורש
בין ports; בדיקת engine אינה תחליף למכשיר iOS ולמקלדת פיזית.

## 3. אבטחה שאומתה בקוד ובסימולציה

- **Auth/JWT:** הטוקן המדויק מאומת דרך `/auth/v1/user` לפני אמון ב־claims;
  נבדקים UUID מאושר, role, issuer, audience, expiry ו־issued-at. זה אינו
  אימות חתימה מקומי ב־`_claims`. ראו
  [`data_api_transport.py:136–175`](https://github.com/yoavbenNun/AJob/blob/decbac225ba1d13f7c2f7c55c85afec19133d49b/src/web/data_api_transport.py#L136).
- **Sessions:** bearer אקראי, hash בצד השרת, CSRF קשור ל־Session, תוקף מוחלט
  ו־idle, rotation ו־logout/revoke. Session מזויף או שפג אינו משנה משרה או Note.
  replay של Session שבוטל נדחה; replay של bearer תקף עד תוקפו הוא סיכון bearer
  רגיל, לא פגיעות חדשה שנמצאה.
- **CSRF/Origin/Host:** Host מדויק ו־headers כפולים נבדקים לפני גישה למידע;
  Origin ו־CSRF נבדקים לכל שלוש פעולות הכתיבה. נבדקו null/foreign Origin,
  token ריק/מזויף/Unicode, session אחר ו־logout מזויף.
  [`cloud_auth.py:171–257`](https://github.com/yoavbenNun/AJob/blob/decbac225ba1d13f7c2f7c55c85afec19133d49b/src/web/cloud_auth.py#L171).
- **Transport:** 11 RPCs קבועים, project origin מוגבל, ללא redirects/proxies,
  הגבלת response ו־timeouts. אין retry אוטומטי לכתיבה שתגובתה אבדה; כשל עשוי
  להיות commit לא ודאי ולכן אין להניח שהכתיבה לא התרחשה.
- **הרשאות:** כל RPC מחייב owner ו־role; `SECURITY DEFINER` משתמש ב־executor
  מוגבל וב־`search_path=pg_catalog`. אין generic SQL RPC. ה־owner הוא אישי
  יחיד; אין טענה לתמיכה רב־דיירית.
  [`data-api.sql:177–226`](https://github.com/yoavbenNun/AJob/blob/decbac225ba1d13f7c2f7c55c85afec19133d49b/deployment/data-api.sql#L177).
- **בעלות פונקציות:** ownership, ביטול PUBLIC EXECUTE ו־GRANT ל־role המדויק
  נעשים באותה טרנזקציה. סימון roles קודמים והחזרת membership options נבדקו
  בדלתא הסופית ובבדיקת ה־SQL האמיתית של מנהל שאינו Superuser.
  [`data-api.sql:20–60`](https://github.com/yoavbenNun/AJob/blob/decbac225ba1d13f7c2f7c55c85afec19133d49b/deployment/data-api.sql#L20),
  [`data-api.sql:400–437`](https://github.com/yoavbenNun/AJob/blob/decbac225ba1d13f7c2f7c55c85afec19133d49b/deployment/data-api.sql#L400).
- **HTTP/Secrets:** כשלים מוחזרים כ־503 כללי, בלי URL חיבור או פרטי חריגה;
  Data API mode דוחה משתני SQL/Service-role/JWT signing secret ידועים. אין
  טוקן Supabase ב־HTML או Cookie של הדפדפן. אלו אינן סריקת Secrets חיה ב־Render.

## 4. בדיקות ותוצאות — שלוש רמות ראיה נפרדות

### A. תוצאות עצמאיות מקומיות

| חבילה | תוצאה | מה נבדק בפועל |
|---|---|---|
| Auth עצמאי | 47 עברו | Middleware, adapters ו־routes אמיתיים; provider HTTP ב־MockTransport, SQLite סינתטי. |
| Data API עצמאי + suites קיימים | 108 עברו, מהם 40 חדשים | אימות זהות לפני RPC, renew/cache, allowlist, redaction ואי־replay; לא Supabase חי. |
| Runtime עצמאי | 12 עברו | אין disk fallback/מיגרציה בהכנה, credentials אסורים, import ללא חיבורים, Local SQLite בלי Cloud Auth או Render. |
| Mobile | 13 עברו, 2 strict xfail | 8 מסלולי native HTTPS בשני engines, 4 מסלולים קיימים, Chromium cross-site; M-01 ו־W-01 מפורשים. |
| SQL contract אחרי עדכון ה־snapshot | עבר | התאמת 11 ה־RPCs ל־SQL הסופי. |

נסיונות מוקדמים שנכשלו בגלל setup, tap target או המתנה ל־request במקום
response היו שגיאות harness; הם אובחנו ולא סווגו כפגיעויות באפליקציה. תוצאת
W-01 נמדדה מחדש לאחר תיקון ה־harness; M-01 הוא כשל מוצר מוכח. אין כשל לא מוסבר
שנמחק מהדוח. אזהרת Starlette/httpx הקיימת היא deprecation, לא כשל אבטחה.

### B. PostgreSQL/PostgREST אמיתיים וזמניים

נבדק ישירות [לוג CI של `decbac225`](https://github.com/yoavbenNun/AJob/actions/runs/41256700702):
**1,179 עברו, 3 דולגו**, ובתוכם **23 PostgreSQL** ו־**22 PostgREST**. אלה אינם
mock DBs: מדובר ב־PostgreSQL 17 וב־PostgREST 13.0.7 זמניים. נבדקו חתימות/expiry,
role/subject זר, RLS, RPCs, Dismiss/Restore מקביל, rollback, Session persistence,
הרשאות ובעלות פונקציות תחת מנהל שאינו Superuser. תצורת Auth issuance ו־`/user`
בסביבת ה־gateway היא סינתטית — לא Supabase Auth אמיתי.

נוספו גם שש בדיקות אמיתיות עצמאיות ב־
`tests/test_independent_sql_gateway_audit.py`: ID ידוע אינו מעניק Notes לזהות זרה,
rollback של Dismiss באמצע טרנזקציה, Dismiss כפול במקביל, JWT חתום עם subject
זר, גבול HTTP בנוכחות הרשאות PUBLIC של net, וביטול מיפוי הרשאת owner תוך
שימוש חוזר באותו JWT חתום שעדיין תקף קריפטוגרפית. מקומית הן דולגו במכוון: Docker
engine אינו פעיל ולא נעשה שימוש ב־DSN קיים. הן משתמשות ב־fixtures הזמניים
בלבד ורצות ב־CI הנדרש של ענף ה־Audit; תוצאתן נרשמת ב־check-run של ה־PR,
בנפרד מתוצאות mocks. אין בדוח טענה שהן הורצו מול DB מקומי שלא היה זמין.

### C. Supabase/Render אמיתיים — לא אומתו

אין הוכחת Production לגבי claims שהונפקו בפועל, Auth hook, exposed schemas,
PUBLIC grants, פונקציות קיימות, quotas, TLS של השירות, Safari פיזי או זמן
התעוררות Render. אין לבצע הסקה מ־B ל־C.

## 5. מה גבול ה־PUBLIC מוכיח — ומה לא

תפקידי Data API הם NOLOGIN, אך בדיקת SQL הסינתטית מדגימה ש־PUBLIC עדיין יכול
להעניק להם הרשאות על `net`. המנגנון אינו מתיימר להסיר הרשאות managed שאינן
ניתנות לביטול. האבטחה נשענת על גבול HTTP ועל כל המשטח החשוף.

[`test_data_api_gateway.py:68`](https://github.com/yoavbenNun/AJob/blob/decbac225ba1d13f7c2f7c55c85afec19133d49b/tests/test_data_api_gateway.py#L68)
מגדיר gateway עם `ajob_api` בלבד. הדחייה של net/Vault/Cron/outbox בו מוכיחה
חסימה בתצורה הזו; היא **אינה** מוכיחה שאין בפרויקט הקיים public/GraphQL RPC
שנותן ל־role דרך עקיפה. גם `ajob_preflight` אינו inventory של כל gateway קיים.
הפער מתועד כבר ב־[תכנון Data API:33–36,79–86](../../cloud-data-api.md).

[Supabase](https://supabase.com/docs/guides/api/securing-your-api) מפריד בין grants
לבין RLS ומזהיר מפני PUBLIC EXECUTE על functions. לכן בדיקת טבלאות/RLS בלבד
אינה מספיקה לאישור Deployment. זהו שער הפעלה פתוח, לא exploit חדש שהוכח ב־PR.

## 6. מובייל, אמינות ו־Rollback

- Login, רשימה, search, company/status multiselect, פרטים, שינוי סטטוס,
  Notes save/reload, כשל רשת ושימור draft, Undo, Logout ו־expiry נבדקו בשני
  engines ב־375/390px. Restore נבדק גם ב־suite הקיים ובחוזה DB הסינתטי/אמיתי.
- restart של שרת HTTPS זמני שמר Session ו־Note דרך store סינתטי מתמשך;
  store לא זמין החזיר 503 מצונזר, ולאחר התאוששות המידע חזר. זה אינו cold start
  אמיתי של Render או מבחן durability של Supabase.
- מקלדת/סיבוב מסך נבדקו בסימולציית `visualViewport`. אין טענה לבדיקה עם מקלדת
  iPhone, Safari UI, autofill, iCloud או רשת סלולרית אמיתיים.
- Cloud factory משתמש ב־services מתמשכים ואינו פותח SQLite; startup מבצע
  preflight ולא migration. Import אינו יוצר `data/`. local factory נשאר
  SQLite ואינו דורש Cloud Authentication או הגדרות Render.
- rollback של נתונים אינו זהה ל־rollback קוד. מסמך ההפעלה דורש להשבית את Render,
  לבטל רק את הזהות/הרשאות ה־API החדשות, להחזיר hook/settings מה־baseline ולשמר
  Notes וסטטוסים. אין לחזור ל־SQL login המסוכן, למחוק טבלאות או לשחזר outbox.
  זהו נוהל שנסקר, לא rollback Production שבוצע. בדיקה עצמאית זמנית מסירה
  ומחזירה מיפוי owner ומחייבת דחייה של JWT תקף בתקופת הביטול; אין בכך הרשאה
  להריץ את הפעולה על הפרויקט האמיתי. [Supabase מתעד](https://supabase.com/docs/guides/auth/signout)
  ש־access token עשוי להישאר תקף עד expiry אחרי sign-out. לכן השבתת חשבון או
  logout לבדם אינם הוכחה לביטול מיידי; playbook מאושר חייב לבטל הרשאת API
  ולבדוק שוב token ו־Session קיימים.

## 7. תנאים לפני מיזוג ולפני Deployment

**לפני מיזוג:** סקירה אנושית של #24 ואז #25, CI/Security ירוקים על השילוב הסופי,
סקירת הבדיקות העצמאיות, והכרה מפורשת ב־M-01 וב־W-01. לא נמצא חסם Critical/High
חדש שמחייב תיקון קוד לפני סקירת מיזוג. אין למזג אוטומטית על סמך הדוח.

**לפני Deployment, נדרשת הרשאה נפרדת ולא פעולה שבוצעה כאן:**

1. לתקן M-01; להסיר את סימון ה־xfail רק לאחר שבדיקת הטקסט עוברת.
2. לשמור baseline של hook/roles/grants/API settings בלי לחשוף ערכי Secrets.
3. לאמת את כל exposed schemas, public/GraphQL RPCs ו־effective PUBLIC privileges;
   להוכיח שאין נתיב ל־net, Vault, Cron, outbox או SQL כללי עם הזהות המיועדת.
4. לאמת Auth hook ו־UUID מאושר מול טוקנים אמיתיים שהספק הנפיק: owner, זר,
   anonymous, wrong role, expired ו־forged. לבצע בסביבה מאושרת, לא תקיפה לא מורשית.
5. לבדוק שאין SQL/service-role/signing credentials ב־Render, ושאין משתמש SQL LOGIN
   לזהות Data API. לא לשנות managed grants כהדגמה.
6. לאשר ידנית migration/provisioning וכל העברת מידע, עם גיבוי ומיפוי לפי URL;
   אין הרצה אוטומטית. לשמר את ה־Local ואת סמנטיקת ה־scanner/outbox.
7. לאמת Safari אמיתי, Cookie cross-site, מקלדת, Session expiry, restart ו־Render
   idle wake-up; לבצע write/undo רק עם נתונים שאושרו במפורש.
8. לבדוק שה־rollback מבטל גישה חדשה בלי למחוק נתונים או להחזיר DB credentials
   מסוכנים. `/healthz` הוא liveness בלבד, לא הוכחת readiness של מסד הנתונים.

## 8. שחזור וביקורת

קובצי האפליקציה וה־SQL נשארו זהים ל־`decbac225`. התוספות הן חמישה קובצי
`test_independent_*_audit.py`, דוח זה ו־patch מוצע שלא הוחל. בדיקות דפדפן יוצרות
תעודת TLS סינתטית זמנית ומאפשרות אותה רק ב־context של loopback; שום CA לא הותקן.
תמונות ונתוני fixture נשארים מחוץ ל־Git.

להרצה מקומית: לנקות משתני AJOB/Supabase/SMTP/PG ב־child בלבד, להשתמש ב־Python
של הפרויקט, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `--rootdir=. --confcutdir=.`
ו־`--basetemp` חדש ומבודד. OpenSSL ושני browser binaries נדרשים; אין לדווח pass
אם חסר engine. אין להשתמש ב־Production DSN גם לצורך בדיקת כשל.

בדיקות PostgreSQL/PostgREST דורשות **רק** cluster זמני בבעלות ההרצה ו־
`AJOB_TEST_DATA_API_GATEWAY=1`; CI הקיים מספק אותם בלולאה מקומית. יש לבדוק את
תוצאות ה־[Audit branch CI](https://github.com/yoavbenNun/AJob/actions?query=branch%3Acodex%2Findependent-cloud-audit)
ולא להחליף skipped מקומי בטענה לאימות DB. כל ממצא חדש של Critical/High מחייב
עדכון ההחלטה והשהיית פריסה.
