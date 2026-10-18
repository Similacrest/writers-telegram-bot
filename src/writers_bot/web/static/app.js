// The bot's web app: a Telegram Mini App, or a website with Telegram Login.
// All texts come from the bot's locale file (GET /api/texts), so nothing user-facing is written here.
"use strict";

const tg = window.Telegram && window.Telegram.WebApp;
const state = { token: null, texts: {}, tabs: [], tab: null, launch: null, config: null, timer: null, chat: null };

// --- small helpers ----------------------------------------------------------------------------------

function T(key, values) {
  let text = state.texts[key];
  if (Array.isArray(text)) text = text[0];
  if (text === undefined) return key;
  return String(text).replace(/\{(\w+)\}/g, (match, name) => (values && name in values ? values[name] : match));
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function toast(text, error) {
  const box = document.getElementById("toast");
  box.textContent = text;
  box.className = error ? "error" : "";
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (box.hidden = true), 3500);
}

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = {};
  try { data = await response.json(); } catch (e) { data = {}; }
  if (!response.ok) {
    const error = new Error(data.error || T("error_server"));
    error.status = response.status;
    error.data = data;
    if (response.status === 401) { sessionStorage.removeItem("token"); state.token = null; }
    throw error;
  }
  return data;
}

async function guarded(action) {
  try { return await action(); } catch (e) { toast(e.message, true); return null; }
}

function plural(n, key) {
  const forms = state.texts["plural_" + key] || ["", "", ""];
  const a = Math.abs(n);
  const form = a % 10 === 1 && a % 100 !== 11 ? forms[0] : a % 10 >= 2 && a % 10 <= 4 && !(a % 100 >= 12 && a % 100 <= 14) ? forms[1] : forms[2];
  return n + " " + form;
}

function time(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// --- start ------------------------------------------------------------------------------------------

async function start() {
  state.texts = await (await fetch("/api/texts")).json();
  state.config = await (await fetch("/api/config")).json();
  document.getElementById("title").textContent = T("title");
  document.title = T("title");
  const params = new URLSearchParams(location.search);
  state.launch = params.get("s");
  if (tg && tg.initData) {
    document.documentElement.setAttribute("data-tg", "");
    tg.ready();
    tg.expand();
    const session = await guarded(() => api("POST", "/api/session", { init_data: tg.initData }));
    if (!session) return;
    state.token = session.token;
    state.launch = state.launch || session.start || (tg.initDataUnsafe && tg.initDataUnsafe.start_param) || null;
  } else {
    const fragment = new URLSearchParams(location.hash.slice(1));
    if (fragment.get("session")) {
      sessionStorage.setItem("token", fragment.get("session"));
      history.replaceState(null, "", location.pathname);
    }
    state.token = sessionStorage.getItem("token");
  }
  if (!state.token) return showLogin();
  await showApp();
}

function showLogin() {
  const view = document.getElementById("view");
  view.replaceChildren(el("p", {}, T("login_intro")));
  const bot = (state.config.bots || [])[0];
  if (bot && bot.username) {
    window.onTelegramAuth = async (user) => {
      const session = await guarded(() => api("POST", "/api/session", { login: user }));
      if (session) { state.token = session.token; sessionStorage.setItem("token", session.token); showApp(); }
    };
    const script = el("script", {
      src: "https://telegram.org/js/telegram-widget.js?22",
      "data-telegram-login": bot.username,
      "data-size": "large",
      "data-onauth": "onTelegramAuth(user)",
      "data-request-access": "write",
      async: true,
    });
    view.append(script);
  }
  if (state.config.discord) view.append(el("p", {}, el("a", { href: "/auth/discord" }, T("login_discord"))));
}

async function showApp() {
  const me = await guarded(() => api("GET", "/api/me"));
  if (!me) return showLogin();
  document.getElementById("who").textContent = me.name;
  state.tabs = me.tabs.filter((tab) => TABS[tab]);
  const nav = document.getElementById("tabs");
  nav.replaceChildren(
    ...state.tabs.map((tab) => el("button", { role: "tab", "data-tab": tab, onclick: () => open(tab) }, T("tab_" + tab)))
  );
  let first = state.tabs[0];
  if (state.launch) {
    const launch = await api("GET", "/api/launch?token=" + encodeURIComponent(state.launch)).catch((e) => { toast(e.message, true); return null; });
    if (launch && launch.kind === "chat" && state.tabs.includes("chat")) {
      state.chat = launch.chat_id;
      state.launch = null;
      first = "chat";
    } else if (launch && state.tabs.includes("sprint")) {
      first = "sprint";
    }
  }
  open(first);
}

function open(tab) {
  state.tab = tab;
  clearInterval(state.timer);
  for (const button of document.querySelectorAll("#tabs button")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === tab));
  }
  TABS[tab](document.getElementById("view"));
}

// --- the sprint panel ---------------------------------------------------------------------------------

async function renderSprint(view) {
  const query = state.launch ? "?launch=" + encodeURIComponent(state.launch) : "";
  const data = await guarded(() => api("GET", "/api/sprint" + query));
  if (!data) return;
  drawSprint(view, data.sprint);
  state.timer = setInterval(async () => {
    if (state.tab !== "sprint" || document.activeElement && document.activeElement.tagName === "INPUT") return;
    const fresh = await api("GET", "/api/sprint" + query).catch(() => null);
    if (fresh) drawSprint(view, fresh.sprint);
  }, 20000);
}

function drawSprint(view, sprint) {
  if (!sprint) {
    view.replaceChildren(el("p", { class: "muted" }, T("sprint_none")));
    return;
  }
  const status = T("status_" + sprint.status);
  const head = el("div", { class: "card" },
    el("div", { class: "big" }, status),
    el("div", { class: "muted" }, T("sprint_span", { duration: plural(sprint.duration, "minutes"), start: time(sprint.start), end: time(sprint.end) })),
    sprint.report_until ? el("div", { class: "muted" }, T("sprint_until", { until: time(sprint.report_until) })) : null,
  );
  const parts = [head];
  const act = (path, body) => guarded(async () => {
    const result = await api("POST", `/api/sprint/${sprint.id}/${path}`, body);
    if (result.message) toast(result.message);
    drawSprint(view, result.sprint);
  });
  if (!sprint.joined) {
    if (sprint.status === "planned" || sprint.status === "running") {
      parts.push(el("button", { class: "primary", onclick: () => act("join", {}) }, T("join")));
    } else {
      parts.push(el("p", { class: "muted" }, T("sprint_not_joined")));
    }
    view.replaceChildren(...parts);
    return;
  }
  if (sprint.works_on && sprint.works.some((w) => w.name)) {
    parts.push(el("h2", {}, T("sprint_works")));
    for (const work of sprint.works.filter((w) => w.name)) {
      parts.push(el("label", { class: "switch" },
        el("input", { type: "checkbox", checked: work.in_sprint, onchange: () => act("toggle", { work_id: work.id }) }),
        work.name + (work.unit === "percent" ? " (%)" : "")));
    }
  }
  if (sprint.accepting) {
    parts.push(el("h2", {}, T("sprint_report")));
    const inputs = [];
    const shown = sprint.works.filter((w) => w.in_sprint);
    const rows = shown.length ? shown : [{ id: null, name: null, unit: "words", amount: null }];
    for (const work of rows) {
      const input = el("input", { inputmode: "text", placeholder: work.unit === "percent" ? "40%" : T("report_placeholder"), value: work.amount ?? "" });
      inputs.push([work.id, input]);
      parts.push(el("div", { class: "row" }, el("span", { class: "grow" }, work.name || T("report_total")), el("div", { class: "grow" }, input)));
    }
    parts.push(el("div", { class: "row" },
      el("button", { class: "primary", onclick: () => act("report", { entries: inputs.map(([id, input]) => ({ work_id: id, text: input.value })) }) }, T("report_save")),
      el("button", { class: "plain", onclick: () => act("outcome", { outcome: "done" }) }, T("done")),
      el("button", { class: "plain", onclick: () => act("outcome", { outcome: "failed" }) }, T("failed"))));
    if (sprint.outcome) parts.push(el("p", { class: "muted" }, T("outcome_" + sprint.outcome)));
  } else if (sprint.status === "planned") {
    parts.push(el("p", { class: "muted" }, T("sprint_wait")));
  }
  view.replaceChildren(...parts);
}

// --- works ----------------------------------------------------------------------------------------------

async function renderWorks(view) {
  const data = await guarded(() => api("GET", "/api/works"));
  if (!data) return;
  const parts = [el("p", { class: "muted" }, T("works_intro"))];
  for (const work of data.works) parts.push(workCard(work, data, () => renderWorks(view)));
  if (data.works.length < 5) {
    const name = el("input", { placeholder: T("work_name") });
    const unit = data.percent_on ? el("select", {}, el("option", { value: "words" }, T("unit_words")), el("option", { value: "percent" }, T("unit_percent"))) : null;
    parts.push(el("div", { class: "card" }, el("h2", {}, T("work_new")), el("div", { class: "row" }, el("div", { class: "grow" }, name), unit),
      el("p", {}, el("button", { class: "primary", onclick: () => guarded(async () => {
        await api("POST", "/api/works", { name: name.value, unit: unit ? unit.value : "words" });
        renderWorks(view);
      }) }, T("add")))));
  }
  view.replaceChildren(...parts);
}

function workCard(work, data, refresh) {
  const save = (body) => guarded(async () => { await api("PATCH", `/api/works/${work.id}`, body); toast(T("saved")); refresh(); });
  const name = el("input", { value: work.name });
  const url = el("input", { value: work.url || "", placeholder: "https://…" });
  const goalAmount = el("input", { type: "number", min: "0", value: work.goal ? work.goal.amount : "", placeholder: "1000" });
  const goalPeriod = el("select", {}, ...["day", "week", "month"].map((p) => el("option", { value: p, selected: work.goal && work.goal.period === p }, T("per_" + p))));
  const goalRow = work.unit === "words" ? el("div", { class: "row" }, el("span", { class: "grow" }, T("work_goal")), el("div", { class: "grow" }, goalAmount), goalPeriod,
    el("button", { class: "plain", onclick: () => save({ goal: goalAmount.value ? { amount: Number(goalAmount.value), period: goalPeriod.value } : null }) }, T("save"))) : null;
  const dmNote = work.unit === "words" && !data.can_dm && data.bot ? el("p", { class: "muted" }, T("goal_dm_first"), " ", el("a", { href: `https://t.me/${data.bot}?start=works`, target: "_blank" }, "@" + data.bot)) : null;
  return el("div", { class: "card" },
    el("div", { class: "row" }, el("div", { class: "grow" }, name), el("button", { class: "plain", onclick: () => save({ name: name.value }) }, T("save"))),
    el("div", { class: "row" }, el("div", { class: "grow" }, url), el("button", { class: "plain", onclick: () => save({ url: url.value }) }, T("save"))),
    work.total !== null && work.unit === "words" ? el("p", { class: "muted" }, T("work_total", { total: plural(work.total, "words") })) : null,
    goalRow, dmNote,
    data.percent_on ? el("label", { class: "switch" }, el("input", { type: "checkbox", checked: work.unit === "percent", onchange: (e) => save({ unit: e.target.checked ? "percent" : "words" }) }), T("work_percent")) : null,
    el("label", { class: "switch" }, el("input", { type: "checkbox", checked: work.hide_title, onchange: (e) => save({ hide_title: e.target.checked }) }), T("work_hide_title")),
    el("label", { class: "switch" }, el("input", { type: "checkbox", checked: work.keep, onchange: (e) => save({ keep: e.target.checked }) }), T("work_keep")),
    el("p", {}, el("button", { class: "danger", onclick: () => confirm(T("work_delete_confirm", { name: work.name })) && guarded(async () => { await api("DELETE", `/api/works/${work.id}`); refresh(); }) }, T("delete"))),
  );
}

// --- progress ------------------------------------------------------------------------------------------

async function renderProgress(view) {
  const data = await guarded(() => api("GET", "/api/stats"));
  if (!data) return;
  const stat = (label, value) => el("div", { class: "stat" }, el("div", { class: "muted" }, label), el("div", { class: "big" }, value));
  const parts = [el("div", { class: "stats" },
    stat(T("today"), data.today), stat(T("week"), data.week), stat(T("month"), data.month), stat(T("streak"), data.streak))];
  parts.push(chart(data.days));
  parts.push(el("h2", {}, T("calendar")));
  const first = new Date(data.days[0].day + "T12:00:00");
  const blanks = (first.getDay() + 6) % 7;  // weeks start on Monday
  parts.push(el("div", { class: "days" }, ...Array.from({ length: blanks }, () => el("div", {})), ...data.days.map((d) => {
    const label = new Date(d.day + "T12:00:00").getDate();
    const fill = !d.active && d.fillable;
    return el("div", { class: "day" + (d.active ? " on" : "") + (fill ? " fill" : ""), title: fill ? T("fill_day") : "",
      onclick: fill ? () => guarded(async () => { await api("POST", "/api/progress", { text: "✓", day: d.day }); renderProgress(view); }) : null }, label);
  })));
  const input = el("input", { placeholder: T("progress_placeholder") });
  const day = el("select", {}, el("option", { value: "" }, T("day_today")), el("option", { value: offset(1) }, T("day_yesterday")), el("option", { value: offset(2) }, T("day_before")));
  const send = async (workId) => {
    try {
      const result = await api("POST", "/api/progress", { text: input.value, day: day.value || undefined, work_id: workId });
      toast(result.message);
      renderProgress(view);
    } catch (e) {
      if (e.status === 409 && e.data.ask) {
        askWork(e.data.ask, send);
      } else toast(e.message, true);
    }
  };
  parts.push(el("div", { class: "card" }, el("h2", {}, T("progress_add")), el("div", { class: "row" }, el("div", { class: "grow" }, input), day,
    el("button", { class: "primary", onclick: () => send() }, T("save")))));
  if (data.works.length) {
    parts.push(el("table", {}, el("tr", {}, el("th", {}, T("work")), el("th", {}, T("month")), el("th", {}, T("work_goal"))),
      ...data.works.map((w) => el("tr", {}, el("td", {}, w.name), el("td", {}, w.month === null ? "%" : w.month),
        el("td", {}, w.goal ? `${w.goal_done} / ${w.goal.amount} ${T("per_" + w.goal.period)}` : "")))));
  }
  parts.push(el("p", { class: "muted" }, T("sprints_line", { sprints: data.sprints, minutes: data.minutes })));
  if (data.recent && data.recent.length) {
    parts.push(el("h2", {}, T("recent_sprints")), el("table", {},
      el("tr", {}, el("th", {}, T("when")), el("th", {}, T("minutes")), el("th", {}, T("result"))),
      ...data.recent.map((r) => el("tr", {},
        el("td", {}, new Date(r.start).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })),
        el("td", {}, r.minutes),
        el("td", {}, r.words ? plural(r.words, "words") : r.percent !== null ? r.percent + "%" : r.completed ? "✓" : "—")))));
  }
  view.replaceChildren(...parts);
}

function askWork(options, send) {
  const box = el("div", { class: "card" }, el("p", {}, T("which_work")),
    el("div", { class: "row" }, ...options.map((o) => el("button", { class: "plain", onclick: () => { box.remove(); send(o.id); } }, o.name))));
  document.getElementById("view").prepend(box);
}

function offset(days) {
  const d = new Date(Date.now() - 3 * 3600 * 1000 - days * 86400 * 1000);
  return d.toISOString().slice(0, 10);
}

function chart(days) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "chart");
  svg.setAttribute("viewBox", "0 0 300 100");
  svg.setAttribute("preserveAspectRatio", "none");
  const max = Math.max(1, ...days.map((d) => d.words));
  days.forEach((d, i) => {
    const h = d.words ? Math.max(3, (d.words / max) * 96) : d.active ? 3 : 1;
    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("x", String(i * 10 + 1));
    rect.setAttribute("y", String(100 - h));
    rect.setAttribute("width", "8");
    rect.setAttribute("height", String(h));
    rect.setAttribute("class", d.active ? "on" : "off");
    const title = document.createElementNS(ns, "title");
    title.textContent = `${d.day}: ${d.words}`;
    rect.append(title);
    svg.append(rect);
  });
  return svg;
}

// --- settings ------------------------------------------------------------------------------------------

async function renderSettings(view) {
  const data = await guarded(() => api("GET", "/api/works"));
  if (!data) return;
  const parts = [
    el("label", { class: "switch" }, el("input", { type: "checkbox", checked: data.hide_numbers, onchange: (e) => guarded(async () => {
      await api("POST", "/api/privacy", { hide_numbers: e.target.checked }); toast(T("saved"));
    }) }), T("hide_numbers")),
    el("p", { class: "muted" }, T("hide_numbers_hint")),
  ];
  for (const extra of SETTINGS_EXTRAS) parts.push(...(await extra()));
  parts.push(el("h2", {}, T("my_data")), el("p", { class: "muted" }, T("my_data_hint")));
  const exportButton = tg && tg.initData
    ? el("button", { class: "plain", onclick: () => guarded(async () => toast((await api("POST", "/api/export/send", {})).message)) }, T("export_send"))
    : el("button", { class: "plain", onclick: () => guarded(downloadExport) }, T("export_download"));
  parts.push(el("div", { class: "row" }, exportButton,
    el("button", { class: "danger", onclick: () => confirm(T("forget_confirm")) && guarded(async () => {
      await api("DELETE", "/api/me"); toast(T("forgotten")); renderSettings(view);
    }) }, T("forget"))));
  view.replaceChildren(...parts);
}

async function downloadExport() {
  const response = await fetch("/api/export", { headers: { Authorization: "Bearer " + state.token } });
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || T("error_server")); }
  const url = URL.createObjectURL(await response.blob());
  const link = el("a", { href: url, download: "my-writing-data.zip" });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// --- chat settings (chat admins) -----------------------------------------------------------------------

async function pickChat(view, render) {
  if (state.chat) return state.chat;
  const data = await guarded(() => api("GET", "/api/chats"));
  if (!data) return null;
  if (!data.chats.length) { view.replaceChildren(el("p", { class: "muted" }, T("chat_none"))); return null; }
  if (data.chats.length === 1) { state.chat = data.chats[0].id; return state.chat; }
  view.replaceChildren(el("p", {}, T("chat_pick")), ...data.chats.map((c) => el("p", {},
    el("button", { class: "plain", onclick: () => { state.chat = c.id; render(view); } }, c.title))));
  return null;
}

async function renderChat(view) {
  const chat = await pickChat(view, renderChat);
  if (!chat) return;
  const data = await guarded(() => api("GET", `/api/chat/${chat}/settings`));
  if (!data) return;
  const save = (body) => guarded(async () => { await api("PATCH", `/api/chat/${chat}/settings`, body); toast(T("saved")); renderChat(view); });
  const parts = [el("h2", {}, data.title)];
  if (data.community) parts.push(el("p", { class: "muted" }, T("chat_community", { name: data.community })));
  for (const s of data.settings) {
    let input;
    if (s.kind === "bool") input = el("input", { type: "checkbox", checked: s.value, onchange: (e) => save({ [s.key]: e.target.checked }) });
    else if (s.kind === "int") input = el("input", { type: "number", min: s.min, max: s.max, value: s.value, onchange: (e) => save({ [s.key]: Number(e.target.value) }) });
    else if (s.kind === "enum") input = el("select", { onchange: (e) => save({ [s.key]: e.target.value }) }, ...s.choices.map((c) => el("option", { value: c.value, selected: c.value === s.value }, c.label)));
    else input = el("textarea", { rows: "3", onchange: (e) => save({ [s.key]: e.target.value || null }) }, s.value || "");
    const inherited = s.source === "community" ? el("span", { class: "muted" }, " · " + T("inherited")) : null;
    const reset = s.source === "chat" ? el("button", { class: "plain", onclick: () => save({ [s.key]: null }) }, T("reset")) : null;
    parts.push(el("div", { class: "card" },
      s.kind === "bool" ? el("label", { class: "switch" }, input, s.label, inherited) : el("div", {}, el("div", {}, s.label, inherited), input),
      el("p", { class: "muted" }, s.help), reset));
  }
  if (data.modules.includes("replies")) parts.push(...(await repliesSection(chat, () => renderChat(view))));
  view.replaceChildren(...parts);
}

async function repliesSection(chat, refresh) {
  const data = await api("GET", `/api/chat/${chat}/replies`).catch(() => null);
  if (!data) return [];
  const trigger = el("input", { placeholder: T("reply_trigger") });
  const answer = el("input", { placeholder: T("reply_answer") });
  return [
    el("h2", {}, T("replies")),
    ...data.replies.map((r) => el("div", { class: "card" }, el("b", {}, r.trigger), el("div", { class: "muted" }, r.answers.join(" · ")),
      el("button", { class: "danger", onclick: () => guarded(async () => { await api("DELETE", `/api/chat/${chat}/replies/${r.id}`); refresh(); }) }, T("delete")))),
    el("div", { class: "card" }, el("div", { class: "row" }, el("div", { class: "grow" }, trigger), el("div", { class: "grow" }, answer),
      el("button", { class: "primary", onclick: () => guarded(async () => { await api("POST", `/api/chat/${chat}/replies`, { trigger: trigger.value, answer: answer.value }); refresh(); }) }, T("add"))),
      el("p", { class: "muted" }, T("reply_hint"))),
  ];
}

// --- prompts (chat admins) -----------------------------------------------------------------------------

async function renderPrompts(view) {
  const chat = await pickChat(view, renderPrompts);
  if (!chat) return;
  const data = await guarded(() => api("GET", `/api/chat/${chat}/prompts`));
  if (!data) return;
  const act = (id, action) => guarded(async () => { await api("POST", `/api/chat/${chat}/prompts/${id}/${action}`, {}); toast(T("saved")); renderPrompts(view); });
  const parts = [
    el("label", { class: "switch" }, el("input", { type: "checkbox", checked: data.use_shared, onchange: (e) => guarded(async () => {
      await api("PATCH", `/api/chat/${chat}/settings`, { "prompts.use_shared": e.target.checked }); toast(T("saved"));
    }) }), T("use_shared")),
  ];
  if (data.suggestions.length) {
    parts.push(el("h2", {}, T("suggestions")));
    for (const p of data.suggestions) parts.push(el("div", { class: "card" }, el("b", {}, p.category), el("div", {}, p.text || ""),
      el("div", { class: "row" }, el("button", { class: "primary", onclick: () => act(p.id, "accept") }, T("accept")),
        el("button", { class: "plain", onclick: () => act(p.id, "reject") }, T("reject")))));
  }
  const category = el("input", { placeholder: T("prompt_category") });
  const text = el("input", { placeholder: T("prompt_text") });
  parts.push(el("div", { class: "card" }, el("h2", {}, T("prompt_new")), el("div", { class: "row" }, el("div", { class: "grow" }, category), el("div", { class: "grow" }, text),
    el("button", { class: "primary", onclick: () => guarded(async () => { await api("POST", `/api/chat/${chat}/prompts`, { category: category.value, text: text.value }); renderPrompts(view); }) }, T("add")))));
  parts.push(el("h2", {}, T("chat_prompts")));
  if (!data.prompts.length) parts.push(el("p", { class: "muted" }, T("chat_prompts_none")));
  for (const p of data.prompts) parts.push(el("div", { class: "card" }, el("b", {}, p.category), " ", p.text || (p.url ? el("a", { href: p.url, target: "_blank" }, T("image")) : T("image")),
    el("div", { class: "row" }, el("button", { class: "plain", onclick: () => act(p.id, "submit") }, T("submit_shared")),
      el("button", { class: "danger", onclick: () => confirm(T("delete") + "?") && act(p.id, "delete") }, T("delete")))));
  view.replaceChildren(...parts);
}

// Later phases add tabs and settings sections here.
const SETTINGS_EXTRAS = [];
// --- achievements (chat admins) ---------------------------------------------------------------------------

async function renderAchievements(view) {
  const chat = await pickChat(view, renderAchievements);
  if (!chat) return;
  const data = await guarded(() => api("GET", `/api/chat/${chat}/achievements`));
  if (!data) return;
  const parts = [];
  if (!data.achievements.length) parts.push(el("p", { class: "muted" }, T("achievements_none")));
  for (const a of data.achievements) {
    const name = el("input", { value: a.name });
    const description = el("input", { value: a.description || "", placeholder: T("description") });
    const save = () => guarded(async () => {
      await api("PATCH", `/api/chat/${chat}/achievements/${a.id}`, { name: name.value, description: description.value });
      toast(T("saved"));
    });
    parts.push(el("div", { class: "card" },
      el("div", { class: "row" }, el("div", { class: "grow" }, name), el("div", { class: "grow" }, description),
        el("button", { class: "plain", onclick: save }, T("save"))),
      a.holders.length ? el("p", { class: "muted" }, T("holders", { list: a.holders.map((h) => h.name).join(", ") })) : null,
      el("button", { class: "danger", onclick: () => confirm(T("delete") + " «" + a.name + "»?") && guarded(async () => {
        await api("DELETE", `/api/chat/${chat}/achievements/${a.id}`); renderAchievements(view);
      }) }, T("delete"))));
  }
  view.replaceChildren(...parts);
}

const TABS = { sprint: renderSprint, works: renderWorks, progress: renderProgress, settings: renderSettings, chat: renderChat, prompts: renderPrompts, achievements: renderAchievements };

start().catch((e) => toast(e.message || String(e), true));
