"use strict";
/* Smart Screen phone remote. Runs in the Android app (or any phone browser) on the same Wi-Fi as the Pi. */

const $ = (sel, root = document) => root.querySelector(sel);
const APP = window.SmartScreenApp; // the Android app's bridge, if running inside it

const GESTURES = {
  Thumb_Up: "👍 Thumb up", Thumb_Down: "👎 Thumb down", Victory: "✌️ Peace sign", Open_Palm: "✋ Open hand",
  Closed_Fist: "✊ Fist", Pointing_Up: "☝️ Pointing up", ILoveYou: "🤟 I love you",
};
const ACTIONS = {
  "": "Nothing", lights_on: "Lights on", lights_off: "Lights off", lights_toggle: "Toggle lights",
  photo_booth: "Photo booth", wake: "Wake screen", sleep: "Sleep screen",
};
const ACCENTS = ["#ffd36e", "#ff8a65", "#f06292", "#ba68c8", "#7b9cff", "#4fc3f7", "#4db6ac", "#aed581", "#ffffff"];
const NEWS_PRESETS = [
  ["NYT", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"],
  ["BBC", "https://feeds.bbci.co.uk/news/rss.xml"],
  ["ESPN", "https://www.espn.com/espn/rss/news"],
  ["NYT Tech", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"],
];

const S = { cfg: null, locked: new Set(), actions: [], status: null, page: "home" };

/* ---------- helpers ---------- */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k in node && typeof v !== "string") node[k] = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) node.append(c.nodeType ? c : String(c));
  return node;
}

let toastTimer;
function toast(text, ms = 2500) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

async function api(method, url, body, isForm) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  }
  let r;
  try {
    r = await fetch(url, opts);
  } catch (e) {
    throw new Error("Can't reach the screen. Is it on and on the same Wi-Fi?");
  }
  if (r.status === 401) { showPair(); throw new Error("Not connected"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.ok === false) throw new Error(data.error || `Something went wrong (${r.status})`);
  return data;
}

const get = (path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), S.cfg);
function set(path, value) {
  const keys = path.split(".");
  let o = S.cfg;
  for (const k of keys.slice(0, -1)) o = o[k] ?? (o[k] = {});
  o[keys.at(-1)] = value;
  scheduleSave();
}
const isLocked = (path) => [...S.locked].some((l) => path === l || path.startsWith(l + "."));

/* ---------- saving (automatic) ---------- */
let saveTimer;
let saving = Promise.resolve();
function saveState(text, cls = "") {
  const s = $("#save-state");
  s.textContent = text;
  s.className = "save-state " + cls;
}
function scheduleSave() {
  saveState("Saving…");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { saving = saving.then(saveNow); }, 600);
}
async function saveNow() {
  try {
    const res = await api("PUT", "/api/remote/config", S.cfg);
    if (res.applied === "restart") {
      saveState("Restarting screen…", "ok");
      toast("Saved. The screen restarts to apply this (about 10 s).", 4000);
      setTimeout(reloadPreview, 9000);
    } else {
      saveState("Saved ✓", "ok");
    }
  } catch (e) {
    saveState("Not saved", "err");
    toast(e.message, 4000);
  }
}

/* ---------- form rows ---------- */
function rowShell(path, label, hint, control, opts = {}) {
  const locked = path && isLocked(path);
  const row = el("div", { class: `row${opts.stack ? " stack" : ""}${locked ? " locked" : ""}` },
    el("div", { class: "label" }, label, hint ? el("span", { class: "hint" }, hint) : null,
      locked ? el("span", { class: "lock-note" }, "🔒 Change this on the Pi (config.yaml)") : null),
    control);
  if (locked) row.querySelectorAll("input,select,button,textarea").forEach((c) => { c.disabled = true; });
  return row;
}

function toggleRow(path, label, hint) {
  const input = el("input", { type: "checkbox", checked: !!get(path), onchange: (e) => set(path, e.target.checked) });
  return rowShell(path, label, hint, el("label", { class: "switch" }, input, el("span")));
}

function textRow(path, label, opts = {}) {
  const input = el("input", {
    type: opts.type || "text", value: get(path) ?? "", placeholder: opts.placeholder || "",
    autocomplete: "off", autocapitalize: opts.caps ? "sentences" : "off",
    oninput: (e) => set(path, e.target.value),
  });
  return rowShell(path, label, opts.hint, input, { stack: opts.stack !== false });
}

function numberRow(path, label, opts = {}) {
  const input = el("input", {
    type: "number", value: get(path) ?? "", min: opts.min, max: opts.max, step: opts.step || 1, inputmode: "decimal",
    onchange: (e) => {
      let v = Number(e.target.value);
      if (!isFinite(v)) return;
      if (opts.min != null) v = Math.max(opts.min, v);
      if (opts.max != null) v = Math.min(opts.max, v);
      e.target.value = v;
      set(path, v);
    },
  });
  return rowShell(path, label, opts.hint, input);
}

function rangeRow(path, label, opts) {
  const fmt = opts.fmt || ((v) => v);
  const value = el("span", { class: "value" }, fmt(get(path) ?? opts.min));
  const input = el("input", {
    type: "range", min: opts.min, max: opts.max, step: opts.step, value: get(path) ?? opts.min,
    oninput: (e) => { value.textContent = fmt(Number(e.target.value)); set(path, Number(e.target.value)); },
  });
  return rowShell(path, label, opts.hint, el("div", { class: "row", style: "padding:0;min-height:0;flex:1;width:100%" }, input, value), { stack: true });
}

function selectRow(path, label, options, hint) {
  const select = el("select", { onchange: (e) => set(path, e.target.value) },
    options.map(([v, l]) => el("option", { value: v, selected: (get(path) ?? "") === v }, l)));
  return rowShell(path, label, hint, select);
}

function segRow(path, label, options, hint) {
  const seg = el("div", { class: "seg" });
  const draw = () => seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === String(get(path))));
  for (const [v, l] of options) {
    seg.append(el("button", { type: "button", "data-v": v, onclick: () => { set(path, v); draw(); } }, l));
  }
  draw();
  return rowShell(path, label, hint, seg, { stack: true });
}

function colorRow(path, label) {
  const wrap = el("div", { class: "swatches" });
  const draw = () => wrap.querySelectorAll(".swatch").forEach((b) => b.classList.toggle("on", b.dataset.c === get(path)));
  for (const c of ACCENTS) {
    wrap.append(el("button", { type: "button", class: "swatch", "data-c": c, style: `background:${c}`, "aria-label": c,
      onclick: () => { set(path, c); picker.value = c; draw(); } }));
  }
  const picker = el("input", { type: "color", value: get(path) || "#ffd36e", "aria-label": "Custom color",
    oninput: (e) => { set(path, e.target.value); draw(); } });
  wrap.append(picker);
  draw();
  return rowShell(path, label, null, wrap, { stack: true });
}

function hoursRow(path, label, hint) {
  const [a, b] = String(get(path) || "").split("-");
  const on = !!get(path);
  const start = el("input", { type: "time", value: a || "07:00", disabled: !on });
  const end = el("input", { type: "time", value: b || "22:00", disabled: !on });
  const update = () => set(path, toggle.checked ? `${start.value}-${end.value}` : "");
  const toggle = el("input", { type: "checkbox", checked: on, onchange: () => { start.disabled = end.disabled = !toggle.checked; update(); } });
  start.onchange = end.onchange = update;
  return rowShell(path, label, hint, el("div", { style: "display:flex;gap:8px;align-items:center;width:100%" },
    el("label", { class: "switch" }, toggle, el("span")), start, "to", end), { stack: true });
}

/* A list of short texts (messages, light names, ...) */
function textList(path, placeholder, addLabel) {
  const card = el("div", { class: "card" });
  const draw = () => {
    card.replaceChildren();
    const list = get(path) || [];
    list.forEach((text, i) => {
      card.append(el("div", { class: "item" }, el("div", { class: "item-head" },
        el("input", { type: "text", value: text, placeholder, autocapitalize: "sentences",
          oninput: (e) => { list[i] = e.target.value; set(path, list); } }),
        el("button", { class: "icon-btn", "aria-label": "Remove", onclick: () => { list.splice(i, 1); set(path, list); draw(); } }, "✕"))));
    });
    card.append(el("div", { class: "add-row" }, el("button", { class: "btn small", onclick: () => {
      set(path, [...list, ""]);
      draw();
      card.querySelectorAll("input").forEach((i, n, all) => { if (n === all.length - 1) i.focus(); });
    } }, addLabel)));
  };
  draw();
  return card;
}

/* Calendars / news feeds: name + link (+ color) */
function feedList(path, { color, placeholder, presets } = {}) {
  const card = el("div", { class: "card" });
  const draw = () => {
    card.replaceChildren();
    const list = get(path) || [];
    list.forEach((feed, i) => {
      const upd = (k, v) => { list[i] = { ...list[i], [k]: v }; set(path, list); };
      card.append(el("div", { class: "item" },
        el("div", { class: "item-head" },
          color ? el("input", { type: "color", value: feed.color || "#7cc4ff", "aria-label": "Color", oninput: (e) => upd("color", e.target.value) }) : null,
          el("input", { type: "text", value: feed.name || "", placeholder: "Name", oninput: (e) => upd("name", e.target.value) }),
          el("button", { class: "icon-btn", "aria-label": "Remove", onclick: () => {
            if (!confirm(`Remove "${feed.name || "this"}"?`)) return;
            list.splice(i, 1); set(path, list); draw();
          } }, "✕")),
        el("input", { type: "url", value: feed.url && !feed.url.includes("PASTE") ? feed.url : "", placeholder,
          autocomplete: "off", autocapitalize: "off", oninput: (e) => upd("url", e.target.value.trim()) })));
    });
    if (!list.length) card.append(el("div", { class: "empty-note" }, "None yet"));
    const add = el("div", { class: "add-row" }, el("button", { class: "btn small", onclick: () => {
      set(path, [...list, color ? { name: "", url: "", color: ACCENTS[(list.length + 4) % ACCENTS.length] } : { name: "", url: "" }]);
      draw();
    } }, "+ Add"));
    for (const [name, url] of presets || []) {
      if (!list.some((f) => f.url === url)) {
        add.append(el("button", { class: "btn small", onclick: () => { set(path, [...list, { name, url }]); draw(); } }, `+ ${name}`));
      }
    }
    card.append(add);
  };
  draw();
  return card;
}

const section = (title, ...rows) => [el("h2", {}, title), el("div", { class: "card" }, ...rows)];

/* ---------- live preview ---------- */
function fitPreview() {
  const box = $("#preview-box");
  $("#preview").style.transform = `scale(${box.clientWidth / 1920})`;
}
function reloadPreview() {
  $("#preview").src = "/?preview=1&t=" + Date.now();
}

/* ---------- pages ---------- */
function renderHome() {
  const page = $('[data-page="home"]');
  const act = async (name, label) => {
    try {
      await api("POST", `/api/action/${name}`);
      toast(label);
      setTimeout(loadStatus, 800);
    } catch (e) { toast(e.message); }
  };
  const button = (name, icon, label, big) => el("button", { class: `action${big ? " big" : ""}`, onclick: () => act(name, label) },
    el("span", {}, icon), label);
  page.replaceChildren(
    el("div", { class: "actions" },
      button("photo_booth", "📸", "Start photo booth", true),
      button("lights_on", "💡", "Lights on"),
      button("lights_off", "🌑", "Lights off"),
      button("lights_toggle", "🔁", "Toggle lights"),
      button("wake", "☀️", "Wake screen"),
      button("sleep", "😴", "Sleep screen"),
      el("button", { class: "action", onclick: () => { reloadPreview(); loadStatus(); toast("Refreshed"); } }, el("span", {}, "🔄"), "Refresh")),
    el("h2", {}, "Status"),
    el("div", { class: "card" }, el("div", { id: "stats", class: "stats" })));
  drawStatus();
}

function drawStatus() {
  const box = $("#stats");
  if (!box) return;
  const s = S.status;
  const stat = (value, label) => el("div", { class: "stat" }, el("b", {}, value), el("small", {}, label));
  if (!s) { box.replaceChildren(stat("…", "Loading")); return; }
  box.replaceChildren(
    stat(s.asleep ? "😴 Asleep" : "☀️ Awake", "Screen"),
    stat(s.lights_on ? "💡 On" : "Off", "Lights"),
    stat(s.cpu_temp_c != null ? `${s.cpu_temp_c}°C` : "–", "Pi temperature"),
    stat(`${s.disk_free_gb} GB`, `free of ${s.disk_total_gb} GB`),
    stat(s.camera ? "📷 Ready" : "Off", (s.problems && s.problems[0]) || "Camera"),
    stat(s.commit || s.version, "Software version"));
}

async function loadStatus() {
  try {
    S.status = await api("GET", "/api/remote/status");
    drawStatus();
  } catch (e) { /* shown elsewhere */ }
}

function renderLook() {
  const page = $('[data-page="look"]');
  page.replaceChildren(
    el("p", { class: "help" }, "Changes show on the screen right away."),
    ...section("Layout",
      segRow("display.style", "Background", [["photos", "🖼️ Photos"], ["mirror", "⬛ Black"]]),
      segRow("display.layout", "Clock & calendar side", [["left", "◧ Left"], ["right", "◨ Right"]]),
      rangeRow("display.theme.text_size", "Text size", { min: 0.7, max: 1.5, step: 0.05, fmt: (v) => `${Math.round(v * 100)}%` }),
      colorRow("display.theme.accent_color", "Highlight color")),
    ...section("Readability",
      rangeRow("display.theme.card_opacity", "Box darkness", { min: 0, max: 1, step: 0.05, fmt: (v) => `${Math.round(v * 100)}%` }),
      rangeRow("display.theme.photo_dim", "Dim the photos", { min: 0, max: 0.9, step: 0.05, fmt: (v) => `${Math.round(v * 100)}%` })),
    ...section("Show on screen",
      toggleRow("display.show.clock", "🕐 Clock"),
      toggleRow("display.show.date", "📆 Date"),
      toggleRow("display.show.greeting", "👋 Greeting"),
      toggleRow("display.show.weather", "⛅ Weather"),
      toggleRow("display.show.forecast", "📊 5-day forecast"),
      toggleRow("display.show.calendar", "📅 Calendar"),
      toggleRow("display.show.messages", "💬 Messages"),
      toggleRow("display.show.news", "📰 News headlines"),
      toggleRow("display.show.gesture_help", "✋ Gesture hints")),
    ...section("Clock",
      toggleRow("display.clock_24h", "24-hour clock"),
      toggleRow("display.show_seconds", "Show seconds")),
    ...section("Photo slideshow",
      rangeRow("slideshow.seconds_per_photo", "Time per photo", { min: 5, max: 300, step: 5, fmt: (v) => (v < 60 ? `${v} s` : `${Math.round(v / 6) / 10} min`) }),
      toggleRow("slideshow.include_booth_photos", "Include photo booth pictures")));
}

function weatherSearch() {
  const results = el("div", { class: "results" });
  const input = el("input", { type: "text", placeholder: "Search a city or town…", autocomplete: "off" });
  let t;
  input.oninput = () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      const q = input.value.trim();
      results.replaceChildren();
      if (q.length < 2) return;
      try {
        const r = await fetch(`https://geocoding-api.open-meteo.com/v1/search?count=5&name=${encodeURIComponent(q)}`);
        const data = await r.json();
        for (const p of data.results || []) {
          const name = [p.name, p.admin1, p.country].filter(Boolean).join(", ");
          results.append(el("button", { onclick: () => {
            const w = get("display.weather") || {};
            set("display.weather", { ...w, latitude: Math.round(p.latitude * 100) / 100, longitude: Math.round(p.longitude * 100) / 100 });
            input.value = "";
            results.replaceChildren();
            current.textContent = `📍 ${name}`;
            toast(`Weather set to ${p.name}`);
          } }, name));
        }
      } catch (e) { results.append(el("div", { class: "empty-note" }, "Search needs internet on this phone")); }
    }, 350);
  };
  const w = get("display.weather") || {};
  const current = el("span", { class: "hint" }, w.latitude != null ? `📍 ${w.latitude}, ${w.longitude}` : "Not set");
  return [rowShell("display.weather", "Location", null, el("div", { style: "width:100%" }, current, input), { stack: true }), results];
}

function renderInfo() {
  const page = $('[data-page="info"]');
  page.replaceChildren(
    ...section("Greeting",
      textRow("display.greeting", "Greeting", { placeholder: "Good {part_of_day}, Connors family!", caps: true,
        hint: "{part_of_day} becomes morning / afternoon / evening" })),
    el("h2", {}, "Messages"),
    el("p", { class: "help" }, "One shows at a time, changing every minute. Reminders, quotes, the guest Wi-Fi password…"),
    textList("display.messages", "Type a message", "+ Add message"),
    el("h2", {}, "Calendars"),
    feedList("calendar.feeds", { color: true, placeholder: "Paste the calendar's iCal link" }),
    el("details", { class: "help" }, el("summary", {}, "Where do I find the iCal link?"),
      el("p", {}, el("b", {}, "Google: "), "on a computer, Google Calendar → ⚙️ Settings → click the calendar → Integrate calendar → copy “Secret address in iCal format”."),
      el("p", {}, el("b", {}, "iCloud: "), "Calendar app → ⓘ next to the calendar → turn on Public Calendar → Share Link."),
      el("p", {}, el("b", {}, "Outlook: "), "Outlook on the web → Settings → Calendar → Shared calendars → Publish a calendar → copy the ICS link.")),
    el("div", { class: "card", style: "margin-top:10px" },
      numberRow("calendar.days_ahead", "Days to show", { min: 1, max: 60 }),
      numberRow("calendar.max_events", "Most events at once", { min: 1, max: 30 })),
    el("h2", {}, "Weather"),
    el("div", { class: "card" }, ...weatherSearch(),
      segRow("display.weather.units", "Units", [["fahrenheit", "°F"], ["celsius", "°C"]])),
    el("h2", {}, "News headlines"),
    feedList("news.feeds", { placeholder: "RSS feed link", presets: NEWS_PRESETS }));
}

/* ---------- photos ---------- */
let photoTab = "slideshow";
function renderPhotos() {
  const page = $('[data-page="photos"]');
  const seg = el("div", { class: "seg", style: "margin:4px 0 14px" },
    el("button", { class: photoTab === "slideshow" ? "on" : "", onclick: () => { photoTab = "slideshow"; renderPhotos(); } }, "🖼️ Slideshow"),
    el("button", { class: photoTab === "booth" ? "on" : "", onclick: () => { photoTab = "booth"; renderPhotos(); } }, "📸 Photo booth"));
  const body = el("div", {}, el("div", { class: "empty-note" }, "Loading…"));
  page.replaceChildren(seg, body);
  (photoTab === "slideshow" ? drawSlideshow : drawBooth)(body).catch((e) => body.replaceChildren(el("div", { class: "empty-note" }, e.message)));
}

async function drawSlideshow(body) {
  const items = await api("GET", "/api/remote/backgrounds");
  const input = el("input", { type: "file", accept: "image/*", multiple: true, onchange: async (e) => {
    const files = [...e.target.files];
    if (!files.length) return;
    label.firstChild.textContent = `Uploading ${files.length} photo${files.length > 1 ? "s" : ""}…`;
    let done = 0;
    try {
      for (let i = 0; i < files.length; i += 5) { // a few at a time keeps big batches reliable
        const form = new FormData();
        files.slice(i, i + 5).forEach((f) => form.append("photos", f, f.name));
        const res = await api("POST", "/api/remote/backgrounds", form, true);
        done += res.saved.length;
      }
      toast(`Added ${done} photo${done === 1 ? "" : "s"} to the slideshow`);
    } catch (err) { toast(err.message, 4000); }
    renderPhotos();
  } });
  const label = el("label", { class: "upload" }, "＋ Add photos from this phone", input);
  const grid = el("div", { class: "grid" });
  for (const p of items) {
    grid.append(el("div", { class: "thumb" },
      el("img", { src: `/api/remote/thumb/${p.id}.jpg`, loading: "lazy", alt: p.name,
        onclick: () => openViewer(`/bg/${p.id}.jpg`) }),
      el("button", { class: "del", "aria-label": "Delete", onclick: async () => {
        if (!confirm("Remove this photo from the slideshow? It is deleted from the Pi.")) return;
        try { await api("DELETE", `/api/remote/backgrounds/${p.id}`); renderPhotos(); } catch (e) { toast(e.message); }
      } }, "🗑")));
  }
  body.replaceChildren(label, el("p", { class: "help" }, `${items.length} photo${items.length === 1 ? "" : "s"} in the slideshow`),
    items.length ? grid : el("div", { class: "empty-note" }, "No photos yet. Add some above!"));
}

async function drawBooth(body) {
  const sessions = await api("GET", "/api/remote/booth");
  if (!sessions.length) {
    body.replaceChildren(el("div", { class: "empty-note" }, "No photo booth pictures yet. Hold up ✌️ in front of the screen, or tap “Start photo booth” on Home."));
    return;
  }
  const card = el("div", { class: "card" });
  for (const s of sessions) {
    const main = s.has_print ? "print.jpg" : s.shots[0];
    const when = new Date(s.time).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    const base = `/api/remote/booth/${s.id}`;
    card.append(el("div", { class: "session" },
      el("img", { src: `${base}/${main}?thumb=1`, loading: "lazy", alt: "", onclick: () => openViewer(`${base}/${main}`) }),
      el("div", {},
        el("b", {}, when),
        el("div", { class: "muted", style: "font-size:14px" }, `${s.shots.length} photo${s.shots.length === 1 ? "" : "s"}`),
        el("div", { class: "btns" },
          s.has_print ? el("button", { class: "btn small", onclick: async () => {
            try { await api("POST", `${base}/print`, { copies: 1 }); toast("🖨️ Printing…"); } catch (e) { toast(e.message, 4000); }
          } }, "🖨️ Print again") : null,
          el("a", { class: "btn small", href: `${base}/${main}?download=1`, download: `photobooth-${s.id}.jpg`, style: "text-decoration:none" }, "⬇️ Save"),
          el("button", { class: "btn small danger", onclick: async () => {
            if (!confirm("Delete these photos from the Pi?")) return;
            try { await api("DELETE", base); renderPhotos(); } catch (e) { toast(e.message); }
          } }, "Delete")))));
  }
  body.replaceChildren(card);
}

function openViewer(src) {
  $("#viewer-img").src = src;
  $("#viewer").showModal();
}

/* ---------- settings ---------- */
function renderMore() {
  const page = $('[data-page="more"]');
  const actionOptions = Object.entries(ACTIONS).filter(([k]) => !k || S.actions.includes(k));
  const lightsBackend = get("lights.backend") || "none";
  const lightRows = {
    none: [],
    home_assistant: [
      textRow("lights.home_assistant.url", "Home Assistant address", { placeholder: "http://homeassistant.local:8123" }),
      textRow("lights.home_assistant.token", "Access token", { type: "password", hint: "Profile → Security → Long-lived access tokens" }),
      el("div", { class: "row stack" }, el("div", { class: "label" }, "Lights", el("span", { class: "hint" }, "e.g. light.living_room")),
        textList("lights.home_assistant.entity_ids", "light.living_room", "+ Add light")),
    ],
    hue: [
      textRow("lights.hue.bridge_ip", "Hue bridge IP address", { placeholder: "192.168.1.50" }),
      textRow("lights.hue.username", "Hue API username", { type: "password", hint: "See the README, “Lights → hue”" }),
      textRow("lights.hue.light_ids", "Light numbers", { placeholder: "1, 2" }),
    ],
    webhook: [
      textRow("lights.webhook.on_url", "Link that turns lights ON", { type: "url" }),
      textRow("lights.webhook.off_url", "Link that turns lights OFF", { type: "url" }),
    ],
  };
  // Hue light numbers are a list of numbers in the config; edit them as "1, 2".
  if (lightsBackend === "hue") {
    const ids = lightRows.hue[2].querySelector("input");
    ids.value = (get("lights.hue.light_ids") || []).join(", ");
    ids.oninput = () => set("lights.hue.light_ids", ids.value.split(/[,\s]+/).filter(Boolean).map(Number).filter(isFinite));
  }
  const lightsSelect = selectRow("lights.backend", "Light system",
    [["none", "None"], ["home_assistant", "Home Assistant"], ["hue", "Philips Hue"], ["webhook", "Web links (Shelly, Tasmota…)"]]);
  lightsSelect.querySelector("select").addEventListener("change", () => setTimeout(renderMore, 0));

  page.replaceChildren(
    el("h2", {}, "Hand gestures"),
    el("p", { class: "help" }, "Hold a gesture for about a second in front of the camera."),
    el("div", { class: "card" }, ...Object.entries(GESTURES).map(([g, label]) =>
      selectRow(`gestures.bindings.${g}`, label, actionOptions))),
    el("div", { class: "card", style: "margin-top:10px" },
      rangeRow("gestures.hold_seconds", "Hold time", { min: 0.3, max: 2, step: 0.1, fmt: (v) => `${v.toFixed(1)} s`,
        hint: "Longer = fewer accidental triggers" }),
      rangeRow("gestures.min_score", "Strictness", { min: 0.4, max: 0.95, step: 0.05, fmt: (v) => `${Math.round(v * 100)}%` })),
    ...section("Photo booth",
      numberRow("photobooth.countdown_seconds", "Countdown (seconds)", { min: 1, max: 10 }),
      numberRow("photobooth.shots", "Photos per session", { min: 1, max: 8 }),
      segRow("photobooth.layout", "Print layout", [["strip", "Strips"], ["grid", "2×2"], ["single", "Single"]]),
      textRow("photobooth.caption", "Caption on the print", { caps: true }),
      toggleRow("photobooth.print", "Print automatically"),
      numberRow("photobooth.copies", "Copies", { min: 1, max: 5 }),
      textRow("photobooth.printer", "Printer name", { hint: "Empty = the Pi's default printer" }),
      toggleRow("photobooth.screen_flash", "White screen flash", "Lights up faces"),
      toggleRow("photobooth.mirror_preview", "Mirror the live preview"),
      toggleRow("photobooth.mirror_photos", "Mirror the photos")),
    ...section("Sleep",
      numberRow("display.sleep_after_minutes", "Sleep after (minutes)", { min: 0, max: 600, hint: "0 = never" }),
      hoursRow("display.always_on_hours", "Always on between", "The screen never sleeps during these hours"),
      toggleRow("display.wake_on_hand", "Wake when a hand is raised")),
    el("h2", {}, "Lights"),
    el("div", { class: "card" }, lightsSelect, ...(lightRows[lightsBackend] || [])),
    el("h2", {}, "Paired phones"),
    el("div", { class: "card", id: "devices" }, el("div", { class: "empty-note" }, "Loading…")),
    el("h2", {}, "System"),
    el("div", { class: "card" },
      el("div", { class: "row" }, el("div", { class: "label" }, "Check for updates now", el("span", { class: "hint" }, "Normally automatic every 15 minutes")),
        el("button", { class: "btn small", onclick: async () => {
          try { await api("POST", "/api/remote/update"); toast("Checking GitHub for updates…", 4000); setTimeout(reloadPreview, 20000); } catch (e) { toast(e.message, 5000); }
        } }, "Update")),
      el("div", { class: "row" }, el("div", { class: "label" }, "Restart the screen"),
        el("button", { class: "btn small", onclick: async () => {
          try { await api("POST", "/api/remote/restart"); toast("Restarting… back in about 10 seconds", 4000); setTimeout(reloadPreview, 10000); } catch (e) { toast(e.message); }
        } }, "Restart")),
      APP?.chooseScreen ? el("div", { class: "row" }, el("div", { class: "label" }, "Connect to a different screen"),
        el("button", { class: "btn small", onclick: () => APP.chooseScreen() }, "Switch")) : null));
  loadDevices();
}

async function loadDevices() {
  const box = $("#devices");
  try {
    const list = await api("GET", "/api/remote/devices");
    box.replaceChildren(...list.map((d) => el("div", { class: "row" },
      el("div", { class: "label" }, `📱 ${d.name}${d.this_phone ? " (this phone)" : ""}`,
        el("span", { class: "hint" }, `Paired ${new Date(d.paired).toLocaleDateString()}`)),
      el("button", { class: "btn small danger", onclick: async () => {
        if (!confirm(d.this_phone ? "Disconnect this phone? You'll need a new code to connect again." : `Remove ${d.name}?`)) return;
        await api("DELETE", `/api/remote/devices/${d.id}`).catch((e) => toast(e.message));
        if (d.this_phone) location.reload(); else loadDevices();
      } }, "Remove"))));
    if (!list.length) box.replaceChildren(el("div", { class: "empty-note" }, "Only the Pi itself"));
  } catch (e) { box.replaceChildren(el("div", { class: "empty-note" }, e.message)); }
}

/* ---------- navigation ---------- */
const RENDER = { home: renderHome, look: renderLook, info: renderInfo, photos: renderPhotos, more: renderMore };
const PREVIEW_PAGES = new Set(["home", "look", "info"]);
function showPage(name) {
  S.page = name;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".page").forEach((p) => { p.hidden = p.dataset.page !== name; });
  document.body.classList.toggle("no-preview", !PREVIEW_PAGES.has(name));
  RENDER[name]();
  fitPreview();
  window.scrollTo(0, 0);
}

/* ---------- pairing ---------- */
function showPair() {
  $("#app").hidden = true;
  $("#pair-view").hidden = false;
}

async function startPair() {
  $("#pair-error").hidden = true;
  try {
    await api("POST", "/api/pair/start");
    $("#pair-form").hidden = false;
    $("#pair-start").textContent = "Show a new code";
    $("#pair-code").focus();
  } catch (e) {
    $("#pair-error").textContent = e.message;
    $("#pair-error").hidden = false;
  }
}

async function finishPair(ev) {
  ev.preventDefault();
  $("#pair-error").hidden = true;
  try {
    const name = (APP?.deviceName && APP.deviceName()) || (/Android/.test(navigator.userAgent) ? "Android phone" : "Phone");
    await api("POST", "/api/pair/finish", { code: $("#pair-code").value, name });
    APP?.paired?.();
    location.reload();
  } catch (e) {
    $("#pair-error").textContent = e.message;
    $("#pair-error").hidden = false;
  }
}

/* ---------- start ---------- */
async function start() {
  $("#pair-start").onclick = startPair;
  $("#pair-form").onsubmit = finishPair;
  let status;
  try {
    status = await (await fetch("/api/pair/status", { credentials: "same-origin" })).json();
  } catch (e) {
    toast("Can't reach the screen", 5000);
    return;
  }
  if (!status.paired) { showPair(); return; }
  try {
    const data = await api("GET", "/api/remote/config");
    S.cfg = data.config;
    S.locked = new Set(data.locked);
    S.actions = data.actions;
  } catch (e) {
    toast(e.message, 5000);
    return;
  }
  $("#title").textContent = get("remote.name") || "Smart Screen";
  $("#pair-view").hidden = true;
  $("#app").hidden = false;
  document.querySelectorAll(".tabs button").forEach((b) => { b.onclick = () => showPage(b.dataset.tab); });
  window.addEventListener("resize", fitPreview);
  reloadPreview();
  showPage("home");
  loadStatus();
  setInterval(() => { if (S.page === "home" && !document.hidden) loadStatus(); }, 10000);
}
start();
