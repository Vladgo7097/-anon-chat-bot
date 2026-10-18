/* Админка. Без сборщика и без CDN: панель должна открываться с сервера,
   который не ходит в интернет. Один запрос -- одна отрисовка. */
(() => {
"use strict";

const TOKEN_KEY = "anon.admin.token";
let TOKEN = "";
// Пусто, пока applyRoute не разберёт адрес: иначе первая отрисовка пропускала
// вкладку из ссылки и всегда открывала "Обзор".
let TAB = "";

/* ============================== безопасность ============================== */

function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* Текст уведомления приходит из Telegram-разметки, которую писал наш же код,
   и весь пользовательский ввод там уже экранирован. Поэтому разметка
   возвращается наружу, но разрешён только белый список без атрибутов --
   ссылку с javascript: всунуть некуда. */
function tg(text) {
  return esc(text).replace(
    /&lt;(\/?)(b|i|s|u|em|strong|code|pre|br)&gt;/gi,
    "<$1$2>");
}

/* ============================== формат ============================== */

function toDate(v) {
  if (!v) return null;
  let s = String(v);
  const offset = /[Zz]|[+-]\d\d:\d\d$/.test(s);
  s = s.replace(/(\.\d{3})\d+/, "$1");          // микросекунды не влезают в Date
  return new Date(offset ? s : s + "Z");         // наивное значение у нас всегда UTC
}

function pad(n) { return n < 10 ? "0" + n : "" + n; }

function fmtDate(v) {
  const d = toDate(v);
  if (!d || isNaN(d)) return "—";
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtFull(v) {
  const d = toDate(v);
  if (!d || isNaN(d)) return "—";
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function fmtAgo(v) {
  const d = toDate(v);
  if (!d || isNaN(d)) return "—";
  const sec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (sec < 60) return "только что";
  if (sec < 3600) return Math.floor(sec / 60) + " мин назад";
  if (sec < 86400) return Math.floor(sec / 3600) + " ч назад";
  return Math.floor(sec / 86400) + " дн назад";
}

function num(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isNaN(n) ? String(v) : n.toLocaleString("ru-RU");
}

function pct(v) {
  if (v === null || v === undefined) return "—";
  return Number(v).toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + "%";
}

function dur(sec) {
  if (!sec && sec !== 0) return "—";
  sec = Math.round(sec);
  const m = Math.floor(sec / 60), s = sec % 60;
  if (!m) return s + " сек";
  return `${m}:${pad(s)}`;
}

function anon(v) { return "#" + String(v || "").replace(/^#/, ""); }

/* Срез до N символов -- ДО разметки: обрезать отданную HTML-строку можно
   посреди тега, и тогда наружу уедет половина `<code>`. */
function clip(text, n) {
  const s = String(text || "").replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, n) + "…" : s;
}

/* Коды ошибок сервисного слоя на человеческий язык. */
const CODE_TEXT = {
  ALREADY_SENT: "Такое сообщение уже отправлено",
  ALREADY_RESOLVED: "Жалоба уже закрыта",
  ALREADY_STARTED: "Рассылка уже запущена",
  NOT_FOUND: "Не найдено",
  INVALID: "Проверьте значения полей",
  EMPTY: "Пустое значение",
  FAILED: "Не выполнено",
};

function codeText(detail) {
  return CODE_TEXT[detail] || detail || "Ошибка";
}

function botChip(label, id) {
  const cls = label === "Бот 1" ? "bot1" : label === "Бот 2" ? "bot2" : "";
  return `<span class="chip ${cls}">${esc(label || "—")}</span>`;
}

/* ============================== запросы ============================== */

function toast(msg, bad) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = "toast on" + (bad ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = "toast"; }, bad ? 4200 : 2200);
}

async function api(path, opts = {}) {
  const init = {
    method: opts.method || "GET",
    headers: { Authorization: "Bearer " + TOKEN },
  };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new Error("нет связи с сервером");
  }
  if (res.status === 401) { showGate("Неверный токен"); throw new Error("BAD_TOKEN"); }
  if (res.status === 404 && !path.startsWith("/api/")) { showGate("Админка выключена"); throw new Error("OFF"); }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(codeText(String(detail)));
  }
  return res.json();
}

/* ============================== вход ============================== */

function showGate(msg) {
  document.getElementById("gate").classList.remove("hidden");
  document.getElementById("app").classList.add("hidden");
  const err = document.getElementById("gate-err");
  err.textContent = msg || "";
  if (msg) document.getElementById("gate-token").select();
}

function showApp() {
  document.getElementById("gate").classList.add("hidden");
  document.getElementById("app").classList.remove("hidden");
}

document.getElementById("gate-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const value = document.getElementById("gate-token").value.trim();
  if (!value) return;
  TOKEN = value;
  api("/api/admin/overview?days=1")
    .then(() => {
      localStorage.setItem(TOKEN_KEY, TOKEN);
      showGate(""); showApp();
      // Разворот через маршрут: render() напрямую вызывать нельзя, пока TAB
      // не разобрана из адреса -- иначе ни одна ветка не сработает.
      TAB = "";
      paintNav(); applyRoute();
    })
    .catch((err) => { TOKEN = ""; showGate(err.message === "BAD_TOKEN" ? "Неверный токен" : err.message); });
});

document.getElementById("logout").addEventListener("click", () => {
  localStorage.removeItem(TOKEN_KEY);
  TOKEN = "";
  TAB = "";               // при следующем входе отрисовка гарантирована
  showGate("");
});

/* ============================== навигация ============================== */

const R = {};   // состояние каждой вкладки
const TABS = ["overview", "users", "notices", "broadcasts", "reports", "audit", "settings"];

/* Перерисовка main() убивала фокус: поле поиска после ввода слова исчезало
   вместе с кареткой, и дописать второе слово можно было только кликом мыши.
   Снимаем снимок до отрисовки и возвращаем курсор на место. */
let FOCUS = null;

function takeFocus() {
  const a = document.activeElement;
  // Пустой снимок прежний не затирает: внутри render() после заглушки
  // "Загрузка…" фокус уже потерян, а снимок, снятый до неё, нужен дальше.
  if (!a || !a.id) return;
  FOCUS = { id: a.id,
            start: "selectionStart" in a ? a.selectionStart : null,
            end: "selectionEnd" in a ? a.selectionEnd : null };
}

function putFocus() {
  const f = FOCUS;
  if (!f) return;
  const el = document.getElementById(f.id);
  if (!el) return;
  el.focus({ preventScroll: true });
  if (f.start !== null && typeof el.setSelectionRange === "function") {
    try { el.setSelectionRange(f.start, f.end); } catch (_) {}
  }
}

function main(html) {
  // Снимок до затирания innerHTML: старый DOM ещё на месте, каретка там, где
  // её оставил пользователь. Дебаунс поиска зовёт рендереры напрямую, мимо
  // render(), поэтому снимок обязан жить именно здесь, а не только в render.
  takeFocus();
  document.getElementById("main").innerHTML = html;
  putFocus();
}

function paintNav() {
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("is-on", b.dataset.tab === TAB));
}

/* Вкладка и открытая карточка живут в адресе: перезагрузка и кнопка "назад"
   возвращают туда, где остался, а вкладку можно скинуть ссылкой. */
function route() {
  const parts = (location.hash || "").replace(/^#/, "").split("/");
  const tab = TABS.includes(parts[0]) ? parts[0] : "overview";
  // Сегментов бывает ровно два: лишние молча отбрасываем, а не трактуем.
  const card = parts.length === 2 ? parts[1] : null;
  return { tab, user: card && /^u\d+$/.test(card) ? card.slice(1) : null };
}

function applyRoute() {
  const r = route();
  if (r.tab !== TAB) { TAB = r.tab; paintNav(); render(); }
  if (r.user) openUserCard(r.user);
  else closeDrawer();
}

function go(tab) {
  if (!TABS.includes(tab)) return;
  if (location.hash === "#" + tab) { paintNav(); render(); return; }
  location.hash = tab;   // отрисовку ведёт hashchange
}

document.getElementById("nav").addEventListener("click", (e) => {
  const btn = e.target.closest(".nav-item");
  if (btn) go(btn.dataset.tab);
});

document.getElementById("drawer-close").addEventListener("click", requestCloseDrawer);
document.getElementById("scrim").addEventListener("click", requestCloseDrawer);

/* ============================== горячие клавиши ============================== */

let pendingSearch = false;

function focusSearch() {
  const el = document.querySelector("[data-search]");
  if (el) { el.focus(); el.select(); return; }
  pendingSearch = true;      // поиск по людям -- основной, туда и уходим
  go("users");
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { requestCloseDrawer(); return; }
  const t = e.target;
  const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
                       t.tagName === "SELECT" || t.isContentEditable);
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "/") { e.preventDefault(); focusSearch(); return; }
  const n = Number(e.key);
  if (Number.isInteger(n) && n >= 1 && n <= TABS.length) { go(TABS[n - 1]); return; }
  // "к" -- это "r" на русской раскладке.
  if (e.key === "r" || e.key === "к") { render(); toast("Обновлено"); }
});

function openDrawer(title, html) {
  document.getElementById("drawer-title").textContent = title;
  document.getElementById("drawer-body").innerHTML = html;
  document.getElementById("drawer").classList.add("on");
  document.getElementById("scrim").classList.add("on");
}

/* Только DOM. Закрытие не должно трогать адрес -- иначе рекурсия через
   hashchange: закрытие меняет hash, hashchange снова зовёт закрытие. */
function closeDrawer() {
  document.getElementById("drawer").classList.remove("on");
  document.getElementById("scrim").classList.remove("on");
}

/* Явное закрытие (крестик, Esc, клик мимо) убирает карточку и из адреса,
   чтобы кнопка "назад" браузера не открыла её снова. */
function requestCloseDrawer() {
  const r = route();
  if (r.user) location.hash = r.tab;
  else closeDrawer();
}

async function render() {
  // Прямой вызов до разбора адреса: TAB ещё пустая, иначе ни одна ветка ниже
  // не сработает и панель навсегда останется на "Загрузка…".
  if (!TAB) { applyRoute(); return; }
  takeFocus();
  main('<div class="loading">Загрузка…</div>');
  try {
    if (TAB === "overview") await renderOverview();
    else if (TAB === "users") await renderUsers();
    else if (TAB === "notices") await renderNotices();
    else if (TAB === "broadcasts") await renderBroadcasts();
    else if (TAB === "reports") await renderReports();
    else if (TAB === "audit") await renderAudit();
    else if (TAB === "settings") await renderSettings();
  } catch (err) {
    if (err.message === "BAD_TOKEN" || err.message === "OFF") return;
    main(`<div class="empty">Не удалось загрузить: ${esc(err.message)}
      <div style="margin-top:12px"><button class="btn btn-sm" data-retry>Повторить</button></div>
    </div>`);
    const b = document.querySelector("[data-retry]");
    if (b) b.addEventListener("click", render);
  }
}

/* Панель сама догоняет реальность: раз в 30 секунд, и только когда её видно
   -- на скрытой вкладке опрашивать сервер глупо. Открытая карточка не
   перерисовывается: пользователь мог начать там что-то писать. */
setInterval(async () => {
  if (document.hidden) return;
  const app = document.getElementById("app");
  if (app.classList.contains("hidden")) return;
  try {
    const data = await api(`/api/admin/overview?days=${R.days || 1}`);
    setTopSys(data.system || {});
    setBadge((data.system || {}).reports_pending || 0);
    if (TAB === "overview" && !document.querySelector(":focus")) paintOverview(data);
  } catch (_) { /* фоновая догонка: ошибка не должна дёргать пользователя */ }
}, 30000);

/* ============================== пагинация ============================== */

/* Общая для "Людей", "Уведомлений" и "Действий". До правки везде были только
   стрелки на страницу, и на пяти тысячах людей это боль: до последней
   страницы приходилось идти по одной. */
function pagerHTML(f, sizes) {
  const pages = Math.max(1, Math.ceil(f.total / f.size));
  const atFirst = f.page <= 1, atLast = f.page >= pages;
  return `<div class="pager">
    <span class="info">стр. ${num(f.page)} из ${num(pages)} · всего ${num(f.total)}</span>
    <button class="btn btn-sm" data-pg="first" ${atFirst ? "disabled" : ""} title="Первая страница">«</button>
    <button class="btn btn-sm" data-pg="-1" ${atFirst ? "disabled" : ""} title="Назад">←</button>
    <input class="field pgnum" id="pg-input" data-pg-input value="${f.page}"
           inputmode="numeric" aria-label="Номер страницы">
    <button class="btn btn-sm" data-pg="1" ${atLast ? "disabled" : ""} title="Вперёд">→</button>
    <button class="btn btn-sm" data-pg="last" ${atLast ? "disabled" : ""} title="Последняя страница">»</button>
    <select class="field pgsel" data-size aria-label="Строк на странице">
      ${(sizes || [25, 50, 100]).map((n) =>
        `<option value="${n}" ${Number(f.size) === n ? "selected" : ""}>${n} стр.</option>`).join("")}
    </select>
  </div>`;
}

function bindPager(f, rerender) {
  document.querySelectorAll("[data-pg]").forEach((b) => b.addEventListener("click", () => {
    const pages = Math.max(1, Math.ceil(f.total / f.size));
    const v = b.dataset.pg;
    f.page = v === "first" ? 1 : v === "last" ? pages
           : Math.min(pages, Math.max(1, f.page + Number(v)));
    rerender();
  }));
  const jump = document.querySelector("[data-pg-input]");
  if (jump) jump.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const pages = Math.max(1, Math.ceil(f.total / f.size));
    const n = parseInt(jump.value, 10);
    if (Number.isFinite(n)) { f.page = Math.min(pages, Math.max(1, n)); rerender(); }
  });
  const sel = document.querySelector("[data-size]");
  if (sel) sel.addEventListener("change", (e) => {
    f.size = Number(e.target.value) || 25;
    f.page = 1;
    rerender();
  });
}

/* Копирование по клику: id и анон приходится переносить руками, а это
   основной способ найти человека. Обработчик висит на document, но всплытие
   до строки доходит раньше -- там и отсекается, чтобы не открывать карточку. */
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-copy]");
  if (!el) return;
  const text = el.dataset.copy;
  if (!text) return;
  const done = () => toast("Скопировано: " + clip(text, 40));
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => toast("Не удалось скопировать", true));
  } else {
    toast("Не удалось скопировать", true);
  }
});

/* Подсветка совпадения поиска. Режем исходную строку, а не готовый HTML:
   иначе срез уехал бы посреди entity вроде `&amp;`. */
function mark(text, q) {
  const raw = String(text || "");
  const needle = String(q || "").trim();
  if (!needle) return esc(raw);
  const i = raw.toLowerCase().indexOf(needle.toLowerCase());
  if (i < 0) return esc(raw);
  return esc(raw.slice(0, i)) + "<mark>" + esc(raw.slice(i, i + needle.length)) +
         "</mark>" + esc(raw.slice(i + needle.length));
}

function setBadge(pending) {
  const badge = document.getElementById("rep-badge");
  badge.textContent = pending;
  badge.classList.toggle("on", pending > 0);
}

/* ============================== обзор ============================== */

const DAY_LABELS = { 1: "сегодня", 7: "за 7 дней", 30: "за 30 дней" };

async function renderOverview() {
  const days = R.days || 1;
  const data = await api(`/api/admin/overview?days=${days}`);
  R.days = days;
  paintOverview(data);
}

/* Отрисовка отделена от запроса: фоновая догонка приносит уже готовые данные
   и не должна ходить в сеть второй раз. */
function paintOverview(data) {
  const days = R.days || 1;
  const s = data.system || {};
  setTopSys(s);
  setBadge(s.reports_pending || 0);

  const o = data;
  const growth = o.growth_7d_pct;
  const growthHtml = growth === null || growth === undefined ? ""
    : `<span class="${growth >= 0 ? "up" : "down"}">${growth >= 0 ? "▲" : "▼"} ${pct(Math.abs(growth))}</span>`;

  const cards = [
    ["Пользователей всего", num(o.users), ""],
    [`Новых ${DAY_LABELS[days] || ""}`, num(o.new), growthHtml],
    ["Диалогов", num(o.chats), `<span class="d">средняя ${dur(o.avg_seconds)}</span>`],
    ["Дошло до меню", num(o.onboarding_completed),
      o.onboarding_conversion !== null && o.onboarding_conversion !== undefined
        ? `<span class="d">${pct(o.onboarding_conversion)} новых</span>` : ""],
    ["Premium активных", num(o.premium), ""],
    ["Звёзд за период", num(o.stars),
      o.stars_usd ? `<span class="d">$${num(o.stars_usd)}</span>` : ""],
    ["Онлайн сейчас", num(s.online), ""],
    ["Очередь уведомлений", num(s.queued), `<span class="d">рассылок активно: ${num(s.broadcasts_active)}</span>`],
  ].map(([k, v, d]) => `<div class="card"><div class="k">${k}</div>
      <div class="v">${v}</div>${d ? `<div class="d">${d}</div>` : ""}</div>`).join("");

  const funnel = (o.funnel && o.funnel.stages) || [];
  const first = funnel.length ? funnel[0][1] : 0;
  const funnelHtml = funnel.map(([label, count]) => {
    const share = first ? Math.round(100 * count / first) : 0;
    return `<div class="fstep"><div>${esc(label)}</div>
      <div class="fbar"><i style="width:${share}%"></i></div>
      <div class="pct">${num(count)}</div></div>`;
  }).join("") || '<div class="empty">Нет данных за период</div>';

  const funnelExtra = o.funnel ? `<div class="hint" style="margin-top:10px">
      Среднее время до правил: ${o.funnel.avg_minutes !== null && o.funnel.avg_minutes !== undefined
        ? o.funnel.avg_minutes + " мин" : "—"}
      · отсеяно по возрасту: ${num(o.funnel.age_rejected)}</div>` : "";

  const conv = [
    ["Поиск → диалог", o.search_conversion, o.searchers, o.matched],
    ["Регистрация → платёж", o.purchase_conversion, o.new, o.buyers],
  ].map(([k, p, a, b]) => `<div class="fstep" style="grid-template-columns:180px 1fr 90px">
      <div>${k}</div><div class="muted dim">${num(b)} из ${num(a)}</div>
      <div class="pct">${pct(p)}</div></div>`).join("");

  const bots = (data.bots || []).map((b) => `
    <tr>
      <td>${botChip(b.label, b.bot_id)}<div class="dim mono" style="font-size:11.5px">${esc(b.bot_id === null ? "NULL" : b.bot_id)}</div></td>
      <td class="num">${num(b.new_today)}</td>
      <td class="num">${num(b.new_week)}</td>
      <td class="num">${num(b.menu_today)}</td>
      <td>
        <div class="bar-mini"><i style="width:${b.new_week ? Math.round(100 * b.menu_today / Math.max(1, b.new_today) || 0) : 0}%;background:var(--accent2)"></i></div>
        <div class="dim" style="font-size:11.5px;margin-top:3px">${num(b.menu_today)} / ${num(b.new_today)} дошло до меню</div>
      </td>
    </tr>`).join("");

  const sys = [
    ["База", s.db, s.db ? "отвечает" : "недоступна"],
    ["Redis", s.redis, s.redis ? "отвечает" : "недоступен"],
    ["Воркер", s.heartbeat_age !== null && s.heartbeat_age !== undefined,
      s.heartbeat_age === null || s.heartbeat_age === undefined
        ? "нет сигнала" : "heartbeat " + dur(s.heartbeat_age) + " назад"],
    ["Черновиков рассылок", true, num(o.drafts)],
    ["Жалоб в очереди", true, num(s.reports_pending || 0)],
  ].map(([k, ok, v]) => `<div class="fstep" style="grid-template-columns:150px 1fr">
      <div class="muted">${k}</div>
      <div><span class="chip ${ok ? "ok" : "bad"}">${ok ? "✓" : "✕"}</span> ${esc(v)}</div>
    </div>`).join("");

  const launches = (data.recent || []).map((r) => {
    const active = r.type === "owner_activated";
    return `<div class="launch">
      <span class="when">${fmtAgo(r.at)}</span>
      ${botChip(r.bot, r.bot_id)}
      <span class="chip ${active ? "acc" : "ok"}">${active ? "дошёл до меню" : "запуск"}</span>
      ${r.telegram_id ? `<span class="mono">${esc(r.telegram_id)}</span>` : ""}
      <span class="muted">${tg(clip(r.text, 150))}</span>
    </div>`;
  }).join("") || '<div class="empty">Сегодня пусто</div>';

  main(`
    <div class="sec-head">
      <h1>Обзор</h1>
      <span class="hint">период</span>
      <div class="tabs">
        ${[1, 7, 30].map((d) => `<button class="tab ${d === days ? "is-on" : ""}" data-days="${d}">${DAY_LABELS[d]}</button>`).join("")}
      </div>
      <div class="spacer"></div>
      <button class="btn btn-sm" data-act="refresh">Обновить</button>
    </div>

    <div class="cards">${cards}</div>

    <div class="panel">
      <div class="panel-head">Последние запуски<span class="spacer"></span>
        <span class="hint">кто открыл бота · последние 8</span></div>
      <div class="panel-body flush"><div class="launches">${launches}</div></div>
    </div>

    <div class="grid2">
      <div class="panel">
        <div class="panel-head">Воронка онбординга<span class="spacer"></span>
          <span class="hint">${DAY_LABELS[days]}</span></div>
        <div class="panel-body">
          <div class="funnel">${funnelHtml}</div>${funnelExtra}
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">Конверсии<span class="spacer"></span><span class="hint">${DAY_LABELS[days]}</span></div>
        <div class="panel-body">
          <div class="funnel">${conv}</div>
          <div class="hint" style="margin-top:12px">
            Возврат D1 ${pct(o.retention_d1)} · D7 ${pct(o.retention_d7)} · рейтинг ${pct(o.rating)}
          </div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">Боты<span class="spacer"></span>
        <span class="hint">сегодня и за последние 7 дней</span></div>
      <div class="panel-body flush tw">
        ${bots ? `<table><thead><tr>
          <th>Бот</th><th class="num">Сегодня</th><th class="num">За 7 дней</th>
          <th class="num">Сегодня до меню</th><th>Онбординг</th>
        </tr></thead><tbody>${bots}</tbody></table>` : '<div class="empty">Пока пусто</div>'}
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">Состояние системы</div>
      <div class="panel-body"><div class="funnel">${sys}</div></div>
    </div>
  `);

  document.querySelectorAll("[data-days]").forEach((b) =>
    b.addEventListener("click", () => { R.days = Number(b.dataset.days); renderOverview(); }));
  document.querySelector('[data-act="refresh"]').addEventListener("click", renderOverview);
}

function setTopSys(s) {
  const bits = [];
  bits.push(`<span class="pill"><span class="chip ${s.db ? "ok" : "bad"}">БД</span></span>`);
  bits.push(`<span class="pill"><span class="chip ${s.redis ? "ok" : "bad"}">Redis</span></span>`);
  if (s.heartbeat_age !== null && s.heartbeat_age !== undefined) {
    const stale = s.heartbeat_age > 180;
    bits.push(`<span class="pill"><span class="chip ${stale ? "bad" : "ok"}">воркер ${dur(s.heartbeat_age)}</span></span>`);
  } else {
    bits.push('<span class="pill"><span class="chip bad">воркер: нет сигнала</span></span>');
  }
  bits.push(`<span class="pill">онлайн <b>${num(s.online)}</b></span>`);
  bits.push(`<span class="pill">в очереди <b>${num(s.queued)}</b></span>`);
  document.getElementById("topsys").innerHTML = bits.join("");
}

/* ============================== люди ============================== */

R.users = { q: "", bot: "", banned: 0, premium: 0, only_new: 0, page: 1, size: 25 };

async function renderUsers() {
  const f = R.users;
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.bot !== "") p.set("bot", f.bot);
  if (f.banned) p.set("banned", "1");
  if (f.premium) p.set("premium", "1");
  if (f.only_new) p.set("only_new", "1");
  p.set("page", f.page); p.set("size", f.size);
  const data = await api("/api/admin/users?" + p);
  f.total = data.total;

  const needle = f.q;
  const rows = data.items.map((u) => `
    <tr class="clickable" data-id="${u.telegram_id}">
      <td class="mono nowrap copy" data-copy="${esc(u.telegram_id)}" title="Скопировать ID">${esc(u.telegram_id)}</td>
      <td class="mono copy" data-copy="${esc(anon(u.anon_id))}" title="Скопировать анон">${mark(anon(u.anon_id), needle)}</td>
      <td class="copy" data-copy="${esc(u.username || "")}">${u.username ? "@" + mark(u.username, needle) : '<span class="dim">—</span>'}</td>
      <td class="copy" data-copy="${esc(u.first_name || "")}">${mark(u.first_name, needle) || '<span class="dim">—</span>'}</td>
      <td>${botChip(u.bot, u.bot_id)}</td>
      <td class="dim">${esc(u.language || "—")}</td>
      <td class="num">${u.age || "—"}</td>
      <td class="num">${num(u.chats_count)}</td>
      <td class="num">${pct(u.positive_rating_pct)}</td>
      <td>${badges(u)}</td>
      <td class="nowrap dim">${fmtAgo(u.last_online)}</td>
    </tr>`).join("");

  main(`
    <div class="sec-head">
      <h1>Люди</h1>
      <span class="hint">${num(data.total)} всего · ${num(data.summary.new_today)} новых сегодня ·
        ${num(data.summary.banned)} в бане · ${num(data.summary.premium)} premium</span>
      <div class="spacer"></div>
      <button class="btn btn-sm" data-act="refresh">Обновить</button>
    </div>

    <div class="panel"><div class="panel-body">
      <div class="form-row">
        <div class="form-col" style="flex:1;min-width:220px">
          <label>Поиск</label>
          <input class="field" id="u-q" data-search placeholder="id, #анон, юзернейм или имя" value="${esc(f.q)}">
        </div>
        <div class="form-col">
          <label>Бот</label>
          <select class="field" id="u-bot">
            <option value="">все</option>
            <option value="8873771495" ${f.bot === "8873771495" ? "selected" : ""}>Бот 1</option>
            <option value="7828301604" ${f.bot === "7828301604" ? "selected" : ""}>Бот 2</option>
            <option value="0" ${f.bot === "0" ? "selected" : ""}>не определён</option>
          </select>
        </div>
        <div class="form-col"><label>Фильтры</label>
          <div class="tabs">
            <button class="tab ${f.only_new ? "is-on" : ""}" data-f="only_new">новые сегодня</button>
            <button class="tab ${f.premium ? "is-on" : ""}" data-f="premium">premium</button>
            <button class="tab ${f.banned ? "is-on" : ""}" data-f="banned">в бане</button>
          </div>
        </div>
      </div>
    </div></div>

    <div class="panel">
      <div class="panel-body flush tw">
        ${rows ? `<table><thead><tr>
          <th>ID</th><th>Анон</th><th>Юзернейм</th><th>Имя</th><th>Бот</th>
          <th>Яз.</th><th class="num">Возр.</th><th class="num">Диал.</th>
          <th class="num">Рейт.</th><th>Статус</th><th>Активность</th>
        </tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">Никто не найден</div>'}
      </div>
      ${pagerHTML(f)}
    </div>
  `);

  if (pendingSearch) {
    pendingSearch = false;
    const el = document.getElementById("u-q");
    if (el) { el.focus(); el.select(); }
  }

  const q = document.getElementById("u-q");
  let timer;
  q.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { f.q = q.value.trim(); f.page = 1; renderUsers(); }, 320);
  });
  q.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { clearTimeout(timer); f.q = q.value.trim(); f.page = 1; renderUsers(); }
  });
  document.getElementById("u-bot").addEventListener("change", (e) => {
    f.bot = e.target.value; f.page = 1; renderUsers();
  });
  document.querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", () => {
    f[b.dataset.f] = f[b.dataset.f] ? 0 : 1; f.page = 1; renderUsers();
  }));
  bindPager(f, renderUsers);
  document.querySelector('[data-act="refresh"]').addEventListener("click", renderUsers);
  document.querySelectorAll("tr[data-id]").forEach((tr) =>
    tr.addEventListener("click", (e) => {
      // Клик по id или анону -- это копирование, а не открытие карточки.
      // Всплытие доходит до tr раньше, чем до document, поэтому проверяем тут.
      if (e.target.closest("[data-copy]")) return;
      openUser(tr.dataset.id);
    }));
}

function badges(u) {
  const out = [];
  if (u.deleted) out.push('<span class="chip">удалён</span>');
  if (u.is_banned) out.push('<span class="chip bad">бан</span>');
  if (u.is_muted) out.push('<span class="chip warn">мут</span>');
  if (u.premium_expires) out.push('<span class="chip acc">premium</span>');
  if (!u.onboarding_completed) out.push('<span class="chip warn">не дошёл</span>');
  if (!out.length) out.push('<span class="chip ok">норма</span>');
  return out.join(" ");
}

/* ============================== карточка человека ============================== */

/* Открыть карточку человека. Адрес меняется первым, а отрисовку ведёт
   hashchange: так кнопка "назад" браузера просто закрывает карточку, а
   перезагрузка возвращает к тому же человеку. */
function openUser(id) {
  const want = TAB + "/u" + id;
  if (location.hash.replace(/^#/, "") === want) openUserCard(id);
  else location.hash = want;
}

async function openUserCard(id) {
  openDrawer("Загрузка…", '<div class="loading">Загрузка…</div>');
  let data;
  try {
    data = await api("/api/admin/users/" + encodeURIComponent(id));
  } catch (err) {
    openDrawer("Ошибка", `<div class="empty">${esc(err.message)}</div>`);
    return;
  }
  const u = data.user;

  const kv = [
    ["Телеграм ID", `<span class="mono">${esc(u.telegram_id)}</span>`],
    ["Анон", `<span class="mono">${esc(anon(u.anon_id))}</span>`],
    ["Юзернейм", u.username ? "@" + esc(u.username) : "не указан"],
    ["Имя", esc(u.first_name) || "не указано"],
    ["Бот", botChip(u.bot, u.bot_id)],
    ["Язык", esc(u.language || "—")],
    ["Пол", esc(u.gender || "—") + (u.age ? ` · ${u.age} лет` : "")],
    ["Био", esc(u.bio) || "—"],
    ["Реф-код", `<span class="mono">${esc(u.referrer_id) || "—"}</span>`],
    ["Привёл / активировали",
      `${num(u.referrals_count)} / ${num(u.referrals_activated)}`],
    ["Пришёл откуда", esc(data.source) || "—"],
    ["Диалогов", num(u.chats_count) + ` · всего ${dur(u.total_chat_seconds)}`],
    ["Рейтинг", `${pct(u.positive_rating_pct)} · ♥ ${num(u.likes_received)} / ${num(u.dislikes_received)}`],
    ["Потрачено звёзд", num(u.total_stars_spent)],
    ["Premium до", u.premium_expires ? fmtFull(u.premium_expires) : "—"],
    ["Бан до", u.ban_expires ? fmtFull(u.ban_expires) : (u.is_banned ? "бессрочно" : "—")],
    ["Шаг онбординга", esc(u.onboarding_step) + (u.onboarding_completed ? " · завершён" : "")],
    ["Правила v", esc(u.rules_version) || "—"],
    ["Регистрация", fmtFull(u.created_at)],
    ["Активность", fmtAgo(u.last_online)],
  ].map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");

  const notices = (data.notices || []).map((n) => `
    <div class="notice">
      <div class="meta">${fmtDate(n.scheduled_at)} · ${esc(n.type)} · ${botChip(n.bot, n.bot_id)}
        · <span class="chip ${n.status === "sent" ? "ok" : n.status === "failed" ? "bad" : "warn"}">${esc(n.status)}</span></div>
      <pre>${tg(n.text)}</pre>
    </div>`).join("") || '<div class="dim">Уведомлений владельцу нет</div>';

  const chats = (data.chats || []).map((c) => `
    <tr>
      <td class="mono dim">${esc((c.session_id || "").slice(0, 10))}</td>
      <td class="nowrap">${fmtDate(c.started_at)}</td>
      <td class="num">${dur(c.duration_seconds)}</td>
      <td>${c.status === "ended" ? '<span class="chip">завершён</span>' : `<span class="chip acc">${esc(c.status)}</span>`}</td>
      <td class="mono">${esc(anon(c.partner_anon_id))}</td>
      <td>${botChip(c.partner_bot, "")}</td>
      <td>${c.rating === "l" ? '<span class="chip ok">♥</span>' :
            c.rating === "d" ? '<span class="chip bad">✕</span>' : '<span class="dim">—</span>'}</td>
    </tr>`).join("");

  openDrawer(`${anon(u.anon_id)} · ${u.telegram_id}`, `
    <div class="chips" style="margin-bottom:14px">${badges(u)}</div>
    <dl class="kv">${kv}</dl>

    <div class="actions">
      <button class="btn btn-danger" data-u="ban">Забанить</button>
      <button class="btn" data-u="unban">Разбанить</button>
      <button class="btn" data-u="mute">Замутить</button>
      <button class="btn" data-u="unmute">Снять мут</button>
      <button class="btn" data-u="premium">Выдать premium</button>
      <button class="btn" data-u="msg">Написать в личку</button>
    </div>

    <div id="u-form"></div>

    <h4>Уведомления владельцу</h4>${notices}

    <h4>Последние диалоги</h4>
    ${chats ? `<div class="tw"><table><thead><tr>
      <th>Сессия</th><th>Начало</th><th class="num">Длит.</th><th>Статус</th>
      <th>Собеседник</th><th>Бот</th><th>Оценка</th>
    </tr></thead><tbody>${chats}</tbody></table></div>` : '<div class="dim">Диалогов нет</div>'}
  `);

  const form = document.getElementById("u-form");
  document.querySelectorAll("#drawer-body [data-u]").forEach((btn) =>
    btn.addEventListener("click", () => userAction(btn.dataset.u, id, form)));
}

async function userAction(kind, id, host) {
  if (kind === "unban") {
    await post(`/api/admin/users/${id}/unban`, {}, "Разбанен");
    return refreshUser(id);
  }
  if (kind === "unmute") {
    await post(`/api/admin/users/${id}/unmute`, {}, "Мут снят");
    return refreshUser(id);
  }
  host.innerHTML = "";

  if (kind === "mute") {
    host.innerHTML = `<div class="action-box"><label>Причина</label>
      <input class="field" id="a-reason" style="width:100%" placeholder="нарушение правил">
      <div class="row"><label style="margin:0">Часов (пусто = бессрочно)</label>
      <input class="field" id="a-hours" style="width:110px" placeholder="24" inputmode="numeric"></div>
      <div class="row"><button class="btn btn-primary" id="a-go">Замутить</button>
      <button class="btn btn-ghost" id="a-cancel">Отмена</button></div></div>`;
  } else if (kind === "ban") {
    host.innerHTML = `<div class="action-box"><label>Причина</label>
      <input class="field" id="a-reason" style="width:100%" placeholder="нарушение правил">
      <div class="row"><label style="margin:0">Часов (пусто = бессрочно)</label>
      <input class="field" id="a-hours" style="width:110px" placeholder="24" inputmode="numeric"></div>
      <div class="row"><button class="btn btn-danger" id="a-go">Забанить</button>
      <button class="btn btn-ghost" id="a-cancel">Отмена</button></div></div>`;
  } else if (kind === "premium") {
    host.innerHTML = `<div class="action-box"><label>Дней</label>
      <input class="field" id="a-days" style="width:110px" value="30" inputmode="numeric">
      <label style="margin-top:8px">Причина</label>
      <input class="field" id="a-reason" style="width:100%" placeholder="компенсация">
      <div class="row"><button class="btn btn-primary" id="a-go">Выдать</button>
      <button class="btn btn-ghost" id="a-cancel">Отмена</button></div></div>`;
  } else {
    host.innerHTML = `<div class="action-box"><label>Сообщение</label>
      <textarea class="field" id="a-text" placeholder="Текст, который увидит пользователь"></textarea>
      <div class="row"><button class="btn btn-primary" id="a-go">Отправить</button>
      <button class="btn btn-ghost" id="a-cancel">Отмена</button></div></div>`;
  }

  document.getElementById("a-cancel").addEventListener("click", () => { host.innerHTML = ""; });
  document.getElementById("a-go").addEventListener("click", async () => {
    const val = (sel) => (document.getElementById(sel) || {}).value || "";
    try {
      if (kind === "ban") {
        const hours = parseInt(val("a-hours"), 10);
        await post(`/api/admin/users/${id}/ban`,
          { reason: val("a-reason"), hours: isNaN(hours) ? null : hours }, "Забанен");
      } else if (kind === "mute") {
        const hours = parseInt(val("a-hours"), 10);
        await post(`/api/admin/users/${id}/mute`,
          { reason: val("a-reason"), hours: isNaN(hours) ? null : hours }, "Замучен");
      } else if (kind === "premium") {
        const days = parseInt(val("a-days"), 10) || 30;
        await post(`/api/admin/users/${id}/premium`,
          { days, reason: val("a-reason") }, "Premium выдан");
      } else {
        await post(`/api/admin/users/${id}/message`, { text: val("a-text") }, "Сообщение отправлено");
      }
      refreshUser(id);
    } catch (err) {
      toast(err.message, true);
    }
  });
}

async function refreshUser(id) {
  try { await openUser(id); } catch (_) {}
  if (TAB === "users") renderUsers();
  else if (TAB === "notices") renderNotices();
}

async function post(path, body, ok) {
  await api(path, { method: "POST", body });
  if (ok) toast(ok);
  return true;
}

/* ============================== уведомления ============================== */

R.notices = { kind: "owner", bot: "", status: "", page: 1, size: 30 };

const NOTICE_KINDS = [
  ["owner", "Владельцу"],
  ["owner_new_user", "Запустил"],
  ["owner_activated", "Дошёл до меню"],
  ["owner_digest", "Сводки"],
  ["all", "Всё"],
];

async function renderNotices() {
  const f = R.notices;
  const p = new URLSearchParams();
  p.set("kind", f.kind);
  if (f.bot !== "") p.set("bot", f.bot);
  if (f.status) p.set("status", f.status);
  p.set("page", f.page); p.set("size", f.size);
  const data = await api("/api/admin/notifications?" + p);
  f.total = data.total;

  const counts = data.counts || {};
  const chips = NOTICE_KINDS.map(([k, label]) => {
    const n = counts[k];
    return `<button class="tab ${f.kind === k ? "is-on" : ""}" data-k="${k}">${label}${
      n !== undefined && k !== "owner" && k !== "all" ? " · " + n : ""}</button>`;
  }).join("");

  const today = (data.today_by_bot || []).map((t) => {
    const cls = t.status === "sent" ? "ok" : t.status === "failed" ? "bad" : "warn";
    return `${botChip(t.bot, t.bot_id)}<span class="chip ${cls}">${esc(t.status)} · ${num(t.count)}</span>`;
  }).join(" ");

  const statusChips = ["", "sent", "queued", "failed"].map((st) =>
    `<button class="tab ${f.status === st ? "is-on" : ""}" data-st="${st}">${st || "любой статус"}</button>`
  ).join("");

  const rows = data.items.map((n) => `
    <tr class="clickable" data-nid="${n.id}">
      <td class="nowrap">${fmtDate(n.scheduled_at)}</td>
      <td>${esc(n.type)}</td>
      <td>${botChip(n.bot, n.bot_id)}</td>
      <td><span class="chip ${n.status === "sent" ? "ok" : n.status === "failed" ? "bad" : "warn"}">${esc(n.status)}</span></td>
      <td class="mono">${n.telegram_id ? esc(n.telegram_id) : '<span class="dim">—</span>'}</td>
      <td class="muted">${tg(clip(n.text, 150))}</td>
    </tr>`).join("");

  main(`
    <div class="sec-head">
      <h1>Уведомления</h1>
      <div class="spacer"></div>
      <button class="btn btn-sm" data-act="refresh">Обновить</button>
    </div>

    <div class="panel"><div class="panel-body">
      <div class="tabs" style="margin-bottom:10px">${chips}</div>
      <div class="tabs" style="margin-bottom:10px">${statusChips}</div>
      <div class="form-row" style="margin:0">
        <div class="form-col"><label>Бот</label>
          <select class="field" id="n-bot">
            <option value="">все</option>
            <option value="8873771495" ${f.bot === "8873771495" ? "selected" : ""}>Бот 1</option>
            <option value="7828301604" ${f.bot === "7828301604" ? "selected" : ""}>Бот 2</option>
            <option value="0" ${f.bot === "0" ? "selected" : ""}>не определён</option>
          </select></div>
        ${today ? `<div class="form-col" style="flex:1"><label>Сегодня по ботам</label>
          <div class="chips">${today}</div></div>` : ""}
      </div>
    </div></div>

    <div class="panel">
      <div class="panel-body flush tw">
        ${rows ? `<table><thead><tr>
          <th>Время</th><th>Тип</th><th>Бот</th><th>Статус</th><th>О ком</th><th>Текст</th>
        </tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">Пусто</div>'}
      </div>
      ${pagerHTML(f)}
    </div>
  `);

  document.querySelectorAll("[data-k]").forEach((b) => b.addEventListener("click", () => {
    f.kind = b.dataset.k; f.page = 1; renderNotices();
  }));
  document.querySelectorAll("[data-st]").forEach((b) => b.addEventListener("click", () => {
    f.status = b.dataset.st; f.page = 1; renderNotices();
  }));
  document.getElementById("n-bot").addEventListener("change", (e) => {
    f.bot = e.target.value; f.page = 1; renderNotices();
  });
  bindPager(f, renderNotices);
  document.querySelector('[data-act="refresh"]').addEventListener("click", renderNotices);
  document.querySelectorAll("tr[data-nid]").forEach((tr) =>
    tr.addEventListener("click", () => {
      const n = data.items.find((x) => String(x.id) === tr.dataset.nid);
      if (!n) return;
      openDrawer(`${n.type} · ${fmtDate(n.scheduled_at)}`, `
        <div class="chips" style="margin-bottom:12px">
          ${botChip(n.bot, n.bot_id)}
          <span class="chip ${n.status === "sent" ? "ok" : n.status === "failed" ? "bad" : "warn"}">${esc(n.status)}</span>
          ${n.telegram_id ? `<span class="chip acc">${esc(n.telegram_id)}</span>` : ""}
        </div>
        <pre style="white-space:pre-wrap;font:inherit;background:var(--panel2);padding:11px;border-radius:8px;margin:0">${tg(n.text)}</pre>
        ${n.last_error ? `<div class="notice" style="border-color:var(--danger);margin-top:10px"><span class="chip bad">${esc(n.last_error)}</span></div>` : ""}
        <div class="kv" style="margin-top:14px">
          <dt>Создано</dt><dd>${fmtFull(n.scheduled_at)}</dd>
          <dt>Отправлено</dt><dd>${n.sent_at ? fmtFull(n.sent_at) : "—"}</dd>
          <dt>ID</dt><dd class="mono">${esc(n.id)}</dd>
        </div>`);
    }));
}

/* ============================== рассылки ============================== */

R.bc = { segment: "all", kind: "text" };

async function renderBroadcasts() {
  const data = await api("/api/admin/broadcasts");
  const f = R.bc;

  const cards = (data.items || []).map((j) => {
    const share = j.total ? Math.round(100 * (j.sent + j.failed + j.blocked) / j.total) : 0;
    const st = j.status === "sent" ? "ok" : j.status === "failed" ? "bad"
      : j.status === "draft" ? "warn" : "acc";
    return `<div class="panel" style="margin-bottom:10px">
      <div class="panel-body">
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
          <b class="mono">#${esc(j.id)}</b>
          <span class="chip">${esc(j.segment)}</span>
          <span class="chip">${esc(j.kind)}</span>
          <span class="chip ${st}">${esc(j.status)}</span>
          ${j.owned ? "" : '<span class="chip warn">чужая</span>'}
          <span class="dim" style="font-size:12px">${fmtFull(j.created_at)}</span>
          <span class="spacer" style="flex:1"></span>
          ${j.status === "draft" && j.owned ? `<button class="btn btn-sm btn-primary" data-send="${j.id}">Отправить</button>` : ""}
          ${["queued", "sending"].includes(j.status) && j.owned
            ? `<button class="btn btn-sm btn-danger" data-stop="${j.id}">Остановить</button>` : ""}
        </div>
        <div style="display:flex;gap:14px;align-items:center;margin-top:9px;flex-wrap:wrap">
          <div class="progress" style="flex:1;min-width:140px"><i style="width:${share}%"></i></div>
          <span class="dim" style="font-size:12.5px">
            отправлено ${num(j.sent)} · ошибок ${num(j.failed)} · блок ${num(j.blocked)} · аудитория ${num(j.total)}
          </span>
        </div>
        <div class="muted" style="margin-top:9px;font-size:12.5px;white-space:pre-wrap">${esc(j.content)}</div>
      </div>
    </div>`;
  }).join("");

  main(`
    <div class="sec-head">
      <h1>Рассылки</h1>
      <div class="spacer"></div>
      <button class="btn btn-sm" data-act="refresh">Обновить</button>
    </div>

    <div class="panel">
      <div class="panel-head">Новая рассылка</div>
      <div class="panel-body">
        <div class="form-row">
          <div class="form-col"><label>Сегмент</label>
            <select class="field" id="b-seg">
              ${data.segments.map((s) => `<option value="${s}" ${f.segment === s ? "selected" : ""}>${s}</option>`).join("")}
            </select></div>
          <div class="form-col"><label>Тип</label>
            <select class="field" id="b-kind">
              <option value="text" ${f.kind === "text" ? "selected" : ""}>текст</option>
              <option value="poll" ${f.kind === "poll" ? "selected" : ""}>опрос</option>
            </select></div>
          <div class="form-col"><label>Аудитория</label>
            <button class="btn" id="b-size">Посчитать</button></div>
        </div>
        <div class="form-col" style="margin-bottom:10px"><label>Текст${f.kind === "poll" ? " (вопрос, до 300 символов)" : " (до 4000 символов)"}</label>
          <textarea class="field" id="b-text" placeholder="Текст рассылки"></textarea></div>
        ${f.kind === "poll" ? `<div class="form-col"><label>Варианты, по одному в строке (2–10)</label>
          <textarea class="field" id="b-opts" style="min-height:70px" placeholder="Да&#10;Нет"></textarea></div>` : ""}
        <div class="actions">
          <button class="btn btn-primary" id="b-draft">Создать черновик</button>
          <span class="hint" id="b-out"></span>
        </div>
      </div>
    </div>

    ${cards || '<div class="panel"><div class="empty">Рассылок ещё не было</div></div>'}
  `);

  document.getElementById("b-seg").addEventListener("change", (e) => { f.segment = e.target.value; });
  document.getElementById("b-kind").addEventListener("change", (e) => { f.kind = e.target.value; renderBroadcasts(); });

  document.getElementById("b-size").addEventListener("click", async () => {
    const out = document.getElementById("b-out");
    out.textContent = "считаю…";
    try {
      const r = await api("/api/admin/broadcasts/audience?segment=" + encodeURIComponent(f.segment));
      out.textContent = "аудитория: " + num(r.audience);
    } catch (e) { out.textContent = e.message; }
  });

  document.getElementById("b-draft").addEventListener("click", async () => {
    const out = document.getElementById("b-out");
    const content = document.getElementById("b-text").value;
    const opts = (document.getElementById("b-opts") || {}).value || "";
    if (!content.trim()) { toast("Пустой текст", true); return; }
    try {
      const r = await api("/api/admin/broadcasts", {
        method: "POST",
        body: { segment: f.segment, kind: f.kind, content, options: opts },
      });
      toast(`Черновик создан · аудитория ${r.audience}`);
      renderBroadcasts();
    } catch (e) { toast(e.message, true); }
  });

  document.querySelectorAll("[data-send]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Отправить рассылку всем в сегменте? Отменить после старта будет нельзя.")) return;
    try {
      await api(`/api/admin/broadcasts/${b.dataset.send}/confirm`, { method: "POST", body: {} });
      toast("Рассылка запущена");
      renderBroadcasts();
    } catch (e) { toast(e.message, true); }
  }));

  document.querySelectorAll("[data-stop]").forEach((b) => b.addEventListener("click", async () => {
    try {
      await api(`/api/admin/broadcasts/${b.dataset.stop}/cancel`, { method: "POST", body: {} });
      toast("Остановлена");
      renderBroadcasts();
    } catch (e) { toast(e.message, true); }
  }));

  document.querySelector('[data-act="refresh"]').addEventListener("click", renderBroadcasts);
}

/* ============================== жалобы ============================== */

async function renderReports() {
  const data = await api("/api/admin/reports");
  const items = data.items || [];

  const cards = items.map((r) => `
    <div class="panel">
      <div class="panel-body">
        <div style="display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-bottom:9px">
          <b class="mono">#${esc(r.id)}</b>
          <span class="chip bad">${esc(r.reason)}</span>
          <span class="dim" style="font-size:12px">${fmtDate(r.created_at)}</span>
        </div>
        <dl class="kv">
          <dt>Кто пожаловался</dt><dd class="mono">${esc(r.reporter_id)}</dd>
          <dt>На кого</dt><dd class="mono">${esc(r.target_id)}</dd>
          ${r.message ? `<dt>Текст</dt><dd class="muted">${esc(r.message)}</dd>` : ""}
        </dl>
        <div class="actions">
          <button class="btn btn-danger btn-sm" data-r="${r.id}" data-a="ban1">Бан на 24 часа</button>
          <button class="btn btn-danger btn-sm" data-r="${r.id}" data-a="banf">Бан навсегда</button>
          <button class="btn btn-sm" data-r="${r.id}" data-a="mute1">Мут 1 час</button>
          <button class="btn btn-sm" data-r="${r.id}" data-a="mute24">Мут 24 часа</button>
          <button class="btn btn-sm" data-r="${r.id}" data-a="mute7">Мут 7 дней</button>
          <button class="btn btn-ghost btn-sm" data-r="${r.id}" data-a="dismiss">Отклонить</button>
          <button class="btn btn-ghost btn-sm" data-user="${esc(r.target_id)}">Открыть карточку</button>
        </div>
      </div>
    </div>`).join("");

  main(`
    <div class="sec-head">
      <h1>Жалобы</h1>
      <span class="hint">${items.length} в очереди</span>
      <div class="spacer"></div>
      <button class="btn btn-sm" data-act="refresh">Обновить</button>
    </div>
    ${cards || '<div class="panel"><div class="empty">Очередь пуста</div></div>'}
  `);

  document.querySelectorAll("[data-r]").forEach((b) => b.addEventListener("click", async () => {
    const a = b.dataset.a;
    const CONFIRM = {
      ban1: "Забанить на 24 часа?",
      banf: "Забанить навсегда? Это действие останется в журнале.",
      mute1: "Выдать мут на 1 час?",
      mute24: "Выдать мут на 24 часа?",
      mute7: "Выдать мут на 7 дней?",
    };
    if (a !== "dismiss" && !confirm(CONFIRM[a] || "Подтвердить решение?")) return;
    const DONE = { dismiss: "Жалоба отклонена", ban1: "Пользователь забанен", banf: "Пользователь забанен",
      mute1: "Выдан мут на 1 час", mute24: "Выдан мут на 24 часа", mute7: "Выдан мут на 7 дней" };
    try {
      await post(`/api/admin/reports/${b.dataset.r}/resolve`, { action: a }, DONE[a] || "Готово");
      renderReports();
    } catch (e) { toast(e.message, true); }
  }));
  document.querySelectorAll("[data-user]").forEach((b) => b.addEventListener("click", () => openUser(b.dataset.user)));
  document.querySelector('[data-act="refresh"]').addEventListener("click", renderReports);
}

/* ============================== действия (аудит) ============================== */

R.audit = { page: 1, size: 25, action: "" };

async function renderAudit() {
  const f = R.audit;
  const p = new URLSearchParams({ page: f.page, size: f.size });
  if (f.action) p.set("action", f.action);
  const data = await api("/api/admin/audit?" + p);
  f.total = data.total;

  const chips = ['<button class="tab ' + (f.action ? "" : "is-on") + '" data-a="">все</button>']
    .concat((data.actions || []).map((a) =>
      `<button class="tab ${f.action === a.action ? "is-on" : ""}" data-a="${esc(a.action)}">${esc(a.action)} · ${num(a.count)}</button>`))
    .join("");

  const rows = data.items.map((r) => {
    const diff = diffText(r.before, r.after);
    return `<tr>
      <td class="nowrap">${fmtDate(r.created_at)}</td>
      <td class="mono">${esc(r.admin_id)}</td>
      <td><span class="chip acc">${esc(r.action)}</span></td>
      <td class="dim">${esc(r.target_type)} <span class="mono">${esc(r.target_id)}</span></td>
      <td class="muted">${diff}</td>
    </tr>`;
  }).join("");

  main(`
    <div class="sec-head">
      <h1>Действия</h1>
      <span class="hint">журнал админов</span>
      <div class="spacer"></div>
      <button class="btn btn-sm" data-act="refresh">Обновить</button>
    </div>

    <div class="panel"><div class="panel-body"><div class="tabs">${chips}</div></div></div>

    <div class="panel">
      <div class="panel-body flush tw">
        ${rows ? `<table><thead><tr>
          <th>Время</th><th>Админ</th><th>Действие</th><th>Цель</th><th>Изменение</th>
        </tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">Пока пусто</div>'}
      </div>
      ${pagerHTML(f)}
    </div>
  `);

  document.querySelectorAll("[data-a]").forEach((b) => b.addEventListener("click", () => {
    f.action = b.dataset.a; f.page = 1; renderAudit();
  }));
  bindPager(f, renderAudit);
  document.querySelector('[data-act="refresh"]').addEventListener("click", renderAudit);
}

function diffText(before, after) {
  const keys = new Set([...Object.keys(before || {}), ...Object.keys(after || {})]);
  if (!keys.size) return '<span class="dim">—</span>';
  return [...keys].map((k) => {
    const b = before ? before[k] : undefined;
    const a = after ? after[k] : undefined;
    if (JSON.stringify(b) === JSON.stringify(a)) return `<div>${esc(k)}: ${esc(fmtVal(b))}</div>`;
    return `<div>${esc(k)}: <span class="dim">${esc(fmtVal(b))}</span> → <b>${esc(fmtVal(a))}</b></div>`;
  }).join("");
}

function fmtVal(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/* ============================== настройки ============================== */

async function renderSettings() {
  const data = await api("/api/admin/settings");
  const rows = data.items.map((s) => `
    <tr>
      <td>${esc(s.title)}<div class="dim mono" style="font-size:11.5px">${esc(s.key)}</div></td>
      <td class="num" style="width:90px">${num(s.value)}</td>
      <td style="width:130px"><input class="field" data-k="${esc(s.key)}" data-min="${s.min}" data-max="${s.max}"
            style="width:100%" value="${esc(s.value)}" inputmode="numeric"></td>
      <td class="dim nowrap" style="width:150px">${num(s.min)} – ${num(s.max)} · дефолт ${num(s.default)}</td>
      <td style="width:110px"><button class="btn btn-sm" data-save="${esc(s.key)}">Сохранить</button></td>
    </tr>`).join("");

  main(`
    <div class="sec-head">
      <h1>Настройки</h1>
      <span class="hint">применяются сразу, без деплоя</span>
      <div class="spacer"></div>
      <button class="btn btn-sm" data-act="refresh">Обновить</button>
    </div>
    <div class="panel"><div class="panel-body flush tw">
      <table><thead><tr><th>Параметр</th><th class="num">Сейчас</th><th>Новое</th><th>Диапазон</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>
    </div></div>
  `);

  document.querySelectorAll("[data-save]").forEach((b) => b.addEventListener("click", async () => {
    const key = b.dataset.save;
    const input = document.querySelector(`input[data-k="${CSS.escape(key)}"]`);
    const value = parseInt(input.value, 10);
    const min = Number(input.dataset.min), max = Number(input.dataset.max);
    if (isNaN(value) || value < min || value > max) {
      toast(`Значение вне диапазона ${min} – ${max}`, true);
      return;
    }
    try {
      await post("/api/admin/settings", { key, value }, "Сохранено");
      renderSettings();
    } catch (e) { toast(e.message, true); }
  }));
  document.querySelector('[data-act="refresh"]').addEventListener("click", renderSettings);
}

/* ============================== старт ============================== */

/* Токен приходит в #-части ссылки: она не уходит на сервер, поэтому не
   оседает ни в access.log, ни в Referer. ?key= принимаем для старых
   закладок и тут же вычищаем вместе с hash -- в адресе ключ не живёт. */
const fromUrl = new URLSearchParams(location.hash.slice(1)).get("key")
             || new URLSearchParams(location.search).get("key");
if (fromUrl) {
  localStorage.setItem(TOKEN_KEY, fromUrl);
  history.replaceState({}, "", location.pathname);
}
TOKEN = localStorage.getItem(TOKEN_KEY) || "";

window.addEventListener("hashchange", applyRoute);

if (!TOKEN) {
  showGate("");
} else {
  api("/api/admin/overview?days=1")
    .then(() => { showApp(); paintNav(); applyRoute(); })
    .catch((err) => {
      if (err.message === "BAD_TOKEN") { localStorage.removeItem(TOKEN_KEY); TOKEN = ""; showGate("Неверный токен"); }
      else if (err.message === "OFF") showGate("Админка выключена: в .env нет ADMIN_WEB_TOKEN");
      else showGate(err.message);
    });
}

})();
