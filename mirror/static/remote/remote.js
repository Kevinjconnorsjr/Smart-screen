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
  lights_dim: "Dim lights", lights_bright: "Bright lights", lights_warm: "Warm light",
  lights_cool: "Cool light", lights_party: "Party colours",
};
const LIGHT_COLORS = {
  warm: "#ffb86b", daylight: "#fff4e0", cool: "#d6ecff", red: "#ff2a1e", orange: "#ff8c14",
  yellow: "#ffdc3c", green: "#28dc50", blue: "#285aff", purple: "#963cff", pink: "#ff50b4",
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
  // Settings that show or hide other settings: redraw the page.
  if (RERENDER.has(path)) setTimeout(() => RENDER[S.page](), 0);
}
const RERENDER = new Set(["display.orientation", "slideshow.include_videos", "alexa.voice_commands", "cloud.enabled"]);
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
  saveTimer = setTimeout(() => { saveTimer = null; saving = saving.then(saveNow); }, 600);
}
// Save right away (used before testing a device or camera that was just typed in).
function flushSave() {
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
    saving = saving.then(saveNow);
  }
  return saving;
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

function selectRow(path, label, options, hint, numeric = false) {
  const select = el("select", { onchange: (e) => set(path, numeric ? Number(e.target.value) : e.target.value) },
    options.map(([v, l]) => el("option", { value: v, selected: String(get(path) ?? "") === String(v) }, l)));
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
    el("h2", {}, "Lights"),
    el("div", { class: "card", id: "lights-panel" }),
    homeDevices(),
    el("h2", {}, "Status"),
    el("div", { class: "card" }, el("div", { id: "stats", class: "stats" })));
  drawStatus();
  drawLights();
}

function homeDevices() {
  const devices = (get("devices") || []).filter((d) => deviceSlug(d.name));
  const cams = (get("cameras.list") || []).filter((c) => c.url);
  const blinkCams = (S.status?.blink?.state === "signed_in" ? S.status.blink.cameras : []) || [];
  if (!devices.length && !cams.length && !blinkCams.length) return null;
  const box = el("div", {});
  if (cams.length || blinkCams.length) {
    box.append(el("h2", {}, "Cameras"), el("div", { class: "card" },
      el("div", { class: "row" }, el("div", { class: "label" }, "📹 On the screen"),
        el("div", { style: "display:flex;gap:8px" },
          el("button", { class: "btn small", onclick: () => api("POST", "/api/action/show_cameras").catch((e) => toast(e.message)) }, "Show / hide"))),
      ...cams.map((c, i) => el("div", { class: "row" }, el("div", { class: "label" }, c.name || `Camera ${i + 1}`),
        el("button", { class: "btn small", onclick: () => openCamera(i) }, "👁 View"))),
      ...blinkCams.map((c, i) => el("div", { class: "row" }, el("div", { class: "label" }, `${c.name} (Blink)`),
        el("button", { class: "btn small", onclick: () => openBlink(i) }, "👁 View")))));
  }
  if (devices.length) {
    box.append(el("h2", {}, "Devices"), el("div", { class: "card" }, ...devices.map((d) => {
      const id = deviceSlug(d.name);
      const send = (what) => api("POST", `/api/remote/smart/${id}/${what}`).then(() => toast(`${d.name} ${what}`)).catch((e) => toast(e.message, 5000));
      return el("div", { class: "row" }, el("div", { class: "label" }, d.name),
        el("div", { class: "seg", style: "min-width:150px" },
          el("button", { onclick: () => send("on") }, "On"), el("button", { onclick: () => send("off") }, "Off")));
    })));
  }
  return box;
}

async function lightsCmd(body) {
  try {
    const r = await api("POST", "/api/remote/lights", body);
    if (S.status) S.status.lights = r.lights;
    drawLights();
  } catch (e) { toast(e.message, 4000); }
}

function drawLights() {
  const box = $("#lights-panel");
  if (!box) return;
  const L = S.status?.lights;
  if (!L) { box.replaceChildren(el("div", { class: "empty-note" }, "…")); return; }
  const none = (get("lights.backend") || "none") === "none";
  const rows = [
    el("div", { class: "row" }, el("div", { class: "label" }, L.on ? "💡 On" : "Off",
      none ? el("span", { class: "hint" }, "No lights set up yet: Settings → Lights") : null),
      el("div", { class: "seg", style: "min-width:150px" },
        el("button", { class: L.on ? "on" : "", onclick: () => lightsCmd({ power: "on" }) }, "On"),
        el("button", { class: L.on ? "" : "on", onclick: () => lightsCmd({ power: "off" }) }, "Off"))),
  ];
  if (L.supports_brightness) {
    const value = el("span", { class: "value" }, `${L.brightness}%`);
    const slider = el("input", { type: "range", min: 1, max: 100, step: 1, value: L.brightness,
      oninput: (e) => { value.textContent = `${e.target.value}%`; },
      onchange: (e) => lightsCmd({ brightness: Number(e.target.value) }) });
    rows.push(el("div", { class: "row stack" }, el("div", { class: "label" }, "Brightness"),
      el("div", { class: "row", style: "padding:0;min-height:0" }, slider, value)));
  }
  const colors = L.supports_color ? L.colors : [];
  if (colors.length || (L.supports_brightness && !L.supports_color)) {
    rows.push(el("div", { class: "row stack" }, el("div", { class: "label" }, "Colour"),
      el("div", { class: "swatches" },
        ...colors.map((c) => el("button", { class: `swatch${L.color === c ? " on" : ""}`, title: c, "aria-label": c,
          style: `background:${LIGHT_COLORS[c] || "#888"}`, onclick: () => lightsCmd({ color: c }) })),
        el("button", { class: "btn small", onclick: () => lightsCmd({ color: "party" }) }, "🎉 Party"))));
  } else if (!none) {
    rows.push(el("div", { class: "row" }, el("div", { class: "label" }, "Party colours"),
      el("button", { class: "btn small", onclick: () => lightsCmd({ color: "party" }) }, "🎉 Party")));
  }
  box.replaceChildren(...rows);
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
    const blinkReady = S.status.blink?.state === "signed_in";
    if (S.page === "home" && blinkReady !== !!S.blinkShown) { S.blinkShown = blinkReady; renderHome(); }
    drawStatus();
    drawLights();
  } catch (e) { /* shown elsewhere */ }
}

function renderLook() {
  const page = $('[data-page="look"]');
  page.replaceChildren(
    el("p", { class: "help" }, "Changes show on the screen right away."),
    ...section("Screen",
      segRow("display.orientation", "Orientation", [["landscape", "▭ Landscape"], ["portrait", "▯ Portrait"]],
        "Portrait = the monitor standing on its side"),
      get("display.orientation") === "portrait"
        ? segRow("display.portrait_turned", "Monitor was turned", [["right", "↻ Right"], ["left", "↺ Left"]],
          "If the picture is upside down, pick the other one")
        : null),
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
    ...section("Photos & videos",
      segRow("slideshow.tiles", "On screen at once", [1, 2, 3, 4, 5, 6].map((n) => [n, String(n)])),
      toggleRow("slideshow.include_videos", "Play videos"),
      get("slideshow.include_videos") !== false
        ? segRow("slideshow.videos_at_once", "Videos playing at once", [[1, "1"], [2, "2"], [3, "3"]],
          "Each video uses a lot of the Pi's power; 1–2 stays smooth") : null,
      get("slideshow.include_videos") !== false
        ? rangeRow("slideshow.video_max_seconds", "Longest video", { min: 10, max: 300, step: 10,
          fmt: (v) => (v < 60 ? `${v} s` : `${Math.round(v / 6) / 10} min`), hint: "Longer videos are cut" }) : null,
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

function driveCard() {
  const card = el("div", { class: "card", style: "margin-bottom:14px" }, el("div", { class: "empty-note" }, "Google Drive…"));
  api("GET", "/api/remote/cloud").then((c) => {
    if (!c.installed || !c.connected) {
      card.replaceChildren(el("div", { class: "row stack" },
        el("div", { class: "label" }, "☁️ Google Drive",
          el("span", { class: "hint" }, "Show a Google Drive folder on the screen. Connect your account once: on the Pi, open a Terminal and run"),
          el("code", { style: "display:block;margin-top:6px;font-size:14px" }, "cd ~/smart-screen && ./deploy/connect-google-drive.sh"))));
      return;
    }
    const when = c.last_run ? new Date(c.last_run).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "never";
    const status = c.running ? "Syncing now…" : c.last_ok === false ? `⚠️ ${c.last_error}` : `${c.files} files · last checked ${when}`;
    card.replaceChildren(
      toggleRow("cloud.enabled", "☁️ Show my Google Drive folder", status),
      get("cloud.enabled") ? textRow("cloud.folder", "Folder name in Google Drive", { placeholder: "Smart Screen",
        hint: "Subfolders are included. Changes in Drive show up here automatically." }) : null,
      get("cloud.enabled") ? toggleRow("cloud.shared_with_me", "It's a folder someone shared with me") : null,
      get("cloud.enabled") ? numberRow("cloud.every_minutes", "Check for new photos every (minutes)", { min: 5, max: 1440 }) : null,
      get("cloud.enabled") ? el("div", { class: "row" }, el("div", { class: "label" }, "Check now"),
        el("button", { class: "btn small", onclick: async () => {
          try { await api("POST", "/api/remote/cloud/sync"); toast("Checking Google Drive…"); setTimeout(renderPhotos, 8000); } catch (e) { toast(e.message); }
        } }, "Sync now")) : null);
  }).catch((e) => card.replaceChildren(el("div", { class: "empty-note" }, e.message)));
  return card;
}

async function drawSlideshow(body) {
  const items = await api("GET", "/api/remote/backgrounds");
  const input = el("input", { type: "file", accept: "image/*,video/*", multiple: true, onchange: async (e) => {
    const files = [...e.target.files];
    if (!files.length) return;
    label.firstChild.textContent = `Uploading ${files.length} photo${files.length > 1 ? "s" : ""}…`;
    let done = 0;
    try {
      // A few at a time (videos one by one) keeps big uploads reliable.
      const batches = [];
      for (const f of files) {
        const last = batches.at(-1);
        if (!last || last.length >= 5 || f.type.startsWith("video/") || last.some((x) => x.type.startsWith("video/"))) batches.push([f]);
        else last.push(f);
      }
      for (const [b, batch] of batches.entries()) {
        label.firstChild.textContent = `Uploading ${b + 1} of ${batches.length}…`;
        const form = new FormData();
        batch.forEach((f) => form.append("photos", f, f.name));
        const res = await api("POST", "/api/remote/backgrounds", form, true);
        done += res.saved.length;
      }
      toast(`Added ${done} photo${done === 1 ? "" : "s"} to the slideshow`);
    } catch (err) { toast(err.message, 4000); }
    renderPhotos();
  } });
  const label = el("label", { class: "upload" }, "＋ Add photos & videos from this phone", input);
  const grid = el("div", { class: "grid" });
  for (const p of items) {
    const video = p.type === "video";
    grid.append(el("div", { class: "thumb" },
      el("img", { src: `/api/remote/thumb/${p.id}.jpg`, loading: "lazy", alt: p.name,
        onclick: () => (video ? (p.state === "ready" ? openVideo(`/bg/${p.id}.mp4`) : toast("Still preparing this video for the screen…"))
          : openViewer(`/bg/${p.id}.jpg`)) }),
      video ? el("span", { class: "badge" }, p.state === "ready" ? "▶" : p.state === "failed" ? "⚠️" : "⏳") : null,
      p.cloud ? el("span", { class: "badge cloud" }, "☁️") : el("button", { class: "del", "aria-label": "Delete", onclick: async () => {
        if (!confirm("Remove this photo from the slideshow? It is deleted from the Pi.")) return;
        try { await api("DELETE", `/api/remote/backgrounds/${p.id}`); renderPhotos(); } catch (e) { toast(e.message); }
      } }, "🗑")));
  }
  const nVideos = items.filter((i) => i.type === "video").length;
  body.replaceChildren(driveCard(), label,
    el("p", { class: "help" }, `${items.length - nVideos} photo${items.length - nVideos === 1 ? "" : "s"}` +
      (nVideos ? ` and ${nVideos} video${nVideos === 1 ? "" : "s"}` : "") + " in the slideshow" +
      (items.some((i) => i.state === "processing") ? " · ⏳ = getting a video ready for the screen" : "")),
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
  $("#viewer-video").hidden = true;
  $("#viewer-img").hidden = false;
  $("#viewer-img").src = src;
  $("#viewer").showModal();
}

function openVideo(src) {
  const v = $("#viewer-video");
  $("#viewer-img").hidden = true;
  v.hidden = false;
  v.src = src;
  $("#viewer").showModal();
  v.play().catch(() => {});
}

/* ---------- settings ---------- */
function renderMore() {
  const page = $('[data-page="more"]');
  const actionOptions = currentActionOptions();
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
      textRow("lights.webhook.urls.dim", "Link that dims them (optional)", { type: "url" }),
      textRow("lights.webhook.urls.bright", "Link that makes them bright (optional)", { type: "url" }),
    ],
    alexa: [
      el("div", { class: "row stack" }, el("div", { class: "label" }, "How this works",
        el("span", { class: "hint" }, "The screen runs Alexa routines through the free Voice Monkey skill. " +
          "For each button below, make a routine trigger at app.voicemonkey.io, then an Alexa routine that runs " +
          "when it fires (e.g. turn the living room lights on). Paste each trigger's ID here. Full steps: README → Alexa."))),
      textRow("lights.alexa.token", "Voice Monkey token", { type: "password", hint: "app.voicemonkey.io → API Tokens" }),
      ...[["on", "Lights on"], ["off", "Lights off"], ["dim", "Dim"], ["bright", "Bright"], ["warm", "Warm light"],
        ["cool", "Cool light"], ["party", "Party colours"]].map(([k, label]) =>
        textRow(`lights.alexa.routines.${k}`, `${label}: trigger ID`, { placeholder: "leave empty if not used", stack: false })),
    ],
  };
  // Hue light numbers are a list of numbers in the config; edit them as "1, 2".
  if (lightsBackend === "hue") {
    const ids = lightRows.hue[2].querySelector("input");
    ids.value = (get("lights.hue.light_ids") || []).join(", ");
    ids.oninput = () => set("lights.hue.light_ids", ids.value.split(/[,\s]+/).filter(Boolean).map(Number).filter(isFinite));
  }
  const lightsSelect = selectRow("lights.backend", "Light system",
    [["none", "None"], ["alexa", "Alexa (lights in the Alexa app)"], ["home_assistant", "Home Assistant"],
      ["hue", "Philips Hue"], ["webhook", "Web links (Shelly, Tasmota…)"]]);
  lightsSelect.querySelector("select").addEventListener("change", () => setTimeout(renderMore, 0));

  page.replaceChildren(
    el("h2", {}, "Hand gestures"),
    el("p", { class: "help" }, "Hold a gesture for about a second in front of the camera. Any gesture can switch " +
      "lights, devices, the cameras, the photo booth…"),
    gesturesCard(actionOptions),
    teachCard(),
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
    el("h2", {}, "Devices"),
    el("p", { class: "help" }, "Fans, plugs, switches, scenes, Alexa routines… Each device can then be switched by a " +
      "gesture, an Alexa voice command, or the Home tab."),
    devicesCard(),
    el("h2", {}, "Cameras"),
    el("p", { class: "help" }, "Security cameras to show on the screen (pick a gesture for “Show/hide cameras” above)."),
    camerasCard(),
    el("h2", {}, "Blink cameras"),
    blinkCard(),
    ...section("Connections",
      el("div", { class: "row stack" }, el("div", { class: "label" }, "Home Assistant",
        el("span", { class: "hint" }, "For devices, cameras and lights in Home Assistant"))),
      textRow("connections.home_assistant.url", "Address", { placeholder: "http://homeassistant.local:8123" }),
      textRow("connections.home_assistant.token", "Long-lived access token", { type: "password",
        hint: "Home Assistant → your profile → Security → Long-lived access tokens" }),
      textRow("connections.voice_monkey.token", "Voice Monkey token (Alexa routines)", { type: "password",
        hint: "app.voicemonkey.io → API Tokens. See README → Alexa" })),
    el("h2", {}, "Alexa voice commands"),
    el("p", { class: "help" }, "Say “Alexa, turn on the photo booth”. The screen shows up in the Alexa app as smart plugs. " +
      "After turning this on, say “Alexa, discover devices” once."),
    alexaVoiceCard(actionOptions),
    ...section("Camera",
      selectRow("camera.rotate", "Camera mounted", [["0", "Upright"], ["90", "On its side (turned right)"],
        ["270", "On its side (turned left)"], ["180", "Upside down"]], "Turn it so your hands look upright in the photo booth preview", true)),
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
      el("div", { class: "row" }, el("div", { class: "label" }, "Restart the Pi"),
        el("button", { class: "btn small", onclick: () => power("reboot") }, "Restart Pi")),
      el("div", { class: "row" }, el("div", { class: "label" }, "Shut down the Pi",
        el("span", { class: "hint" }, "Do this before unplugging it. Unplug once the screen is dark.")),
        el("button", { class: "btn small danger", onclick: () => power("shutdown") }, "Shut down")),
      APP?.chooseScreen ? el("div", { class: "row" }, el("div", { class: "label" }, "Connect to a different screen"),
        el("button", { class: "btn small", onclick: () => APP.chooseScreen() }, "Switch")) : null));
  loadDevices();
}

/* ---------- actions (including devices typed in but not saved yet) ---------- */
function deviceSlug(name) {
  return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
}
function currentActionOptions() {
  const labels = { ...ACTIONS, ...(S.actionLabels || {}) };
  const out = [["", "Nothing"]];
  for (const [k, l] of Object.entries(labels)) if (k && !k.startsWith("device_")) out.push([k, l]);
  for (const d of get("devices") || []) {
    const id = deviceSlug(d.name);
    if (!id) continue;
    out.push([`device_toggle:${id}`, `${d.name} on/off`], [`device_on:${id}`, `${d.name} on`], [`device_off:${id}`, `${d.name} off`]);
  }
  return out;
}

/* ---------- gestures: built-in + taught ---------- */
function gesturesCard(actionOptions) {
  const card = el("div", { class: "card" });
  const rows = Object.entries(GESTURES).map(([g, label]) => selectRow(`gestures.bindings.${g}`, label, actionOptions));
  for (const [gid, label] of Object.entries(S.custom || {})) {
    const row = selectRow(`gestures.bindings.${gid}`, label, actionOptions, "Taught by you");
    row.querySelector(".label").append(el("button", { class: "btn small danger", style: "margin-top:6px", onclick: async () => {
      if (!confirm(`Forget the gesture ${label}?`)) return;
      try {
        await api("DELETE", `/api/remote/gestures/${gid}`);
        delete S.custom[gid];
        if (S.cfg.gestures?.bindings) delete S.cfg.gestures.bindings[gid];
        renderMore();
      } catch (e) { toast(e.message); }
    } }, "Forget"));
    rows.push(row);
  }
  card.append(...rows,
    el("div", { class: "row" }, el("div", { class: "label" }, "Camera sees right now"),
      el("span", { id: "seeing", class: "value", style: "min-width:120px" }, "…")),
    rangeRow("gestures.hold_seconds", "Hold time", { min: 0.3, max: 2, step: 0.1, fmt: (v) => `${v.toFixed(1)} s`,
      hint: "Longer = fewer accidental triggers" }),
    rangeRow("gestures.min_score", "Strictness", { min: 0.4, max: 0.95, step: 0.05, fmt: (v) => `${Math.round(v * 100)}%` }));
  return card;
}

let seeingTimer = null;
async function pollSeeing() {
  clearTimeout(seeingTimer);
  const box = document.getElementById("seeing");
  if (!box || S.page !== "more") return;
  try {
    const g = await api("GET", "/api/remote/gestures");
    const s = g.seeing;
    box.textContent = !g.camera ? "Camera off" : !s.hand ? "No hand" :
      s.name ? `${(S.custom || {})[s.name] || GESTURES[s.name] || s.label} ${Math.round(s.score * 100)}%` : "A hand (no gesture)";
    const t = g.teaching || {};
    const status = document.getElementById("teach-status");
    if (status && t.phase && t.phase !== "idle") {
      status.textContent = t.phase === "get_ready" ? `Get ready… ${t.n}` :
        t.phase === "recording" ? `Learning… ${Math.round((t.progress || 0) * 100)}%` :
        t.ok ? `✅ Learned ${t.label}. Choose what it does above.` : `⚠️ ${t.error}`;
      if (t.phase === "done" && t.ok && !(S.custom || {})[t.id]) {
        S.custom = { ...(S.custom || {}), [t.id]: t.label };
        setTimeout(renderMore, 1500);
      }
    }
  } catch (e) { /* try again */ }
  seeingTimer = setTimeout(pollSeeing, 1000);
}

function teachCard() {
  const name = el("input", { type: "text", placeholder: "Name, e.g. Rock on", autocapitalize: "sentences" });
  const emoji = el("input", { type: "text", placeholder: "🤘", style: "max-width:70px;text-align:center" });
  const status = el("span", { id: "teach-status", class: "hint" });
  const card = el("div", { class: "card", style: "margin-top:10px" },
    el("div", { class: "row stack" },
      el("div", { class: "label" }, "Teach a new gesture",
        el("span", { class: "hint" }, "Stand where you normally would, tap Start, and hold the pose up to the camera when " +
          "the screen counts down. It takes 4 seconds. Move your hand a little while holding it, so it learns small " +
          "differences. Only still poses work (not swipes).")),
      el("div", { style: "display:flex;gap:8px;width:100%" }, emoji, name),
      el("button", { class: "btn primary", onclick: async () => {
        if (!name.value.trim()) { toast("Give the gesture a name first"); return; }
        try {
          await api("POST", "/api/remote/gestures/teach", { name: name.value.trim(), emoji: emoji.value.trim() });
          status.textContent = "Look at the screen…";
          pollSeeing();
        } catch (e) { toast(e.message, 5000); }
      } }, "Start"),
      status));
  setTimeout(pollSeeing, 0);
  return card;
}

/* ---------- devices ---------- */
function devicesCard() {
  const card = el("div", { class: "card" });
  const draw = () => {
    const list = get("devices") || [];
    const rows = [];
    list.forEach((d, i) => {
      const upd = (k, v) => { list[i] = { ...list[i], [k]: v }; set("devices", list); };
      const input = (k, placeholder, type = "text") => el("input", { type, value: d[k] || "", placeholder,
        autocomplete: "off", autocapitalize: "off", oninput: (e) => upd(k, e.target.value.trim()) });
      const type = el("select", { onchange: (e) => { upd("type", e.target.value); draw(); } },
        [["home_assistant", "Home Assistant"], ["alexa", "Alexa routine"], ["webhook", "Web link"]].map(([v, l]) =>
          el("option", { value: v, selected: (d.type || "home_assistant") === v }, l)));
      const fields = {
        home_assistant: [input("entity_id", "Entity, e.g. switch.fan or scene.movie_night")],
        alexa: [input("on_routine", "Voice Monkey trigger ID for ON"), input("off_routine", "Trigger ID for OFF (optional)")],
        webhook: [input("on_url", "Link for ON", "url"), input("off_url", "Link for OFF", "url")],
      }[d.type || "home_assistant"];
      const test = (what) => el("button", { class: "btn small", onclick: async () => {
        try { await flushSave(); await api("POST", `/api/remote/smart/${deviceSlug(d.name)}/${what}`); toast(`${d.name} ${what}`); }
        catch (e) { toast(e.message, 5000); }
      } }, `Test ${what}`);
      rows.push(el("div", { class: "item" },
        el("div", { class: "item-head" },
          el("input", { type: "text", value: d.name || "", placeholder: "Name, e.g. Fan", oninput: (e) => upd("name", e.target.value) }),
          el("button", { class: "icon-btn", "aria-label": "Remove", onclick: () => {
            if (!confirm(`Remove ${d.name || "this device"}?`)) return;
            list.splice(i, 1); set("devices", list); draw();
          } }, "✕")),
        type, ...fields,
        el("div", { style: "display:flex;gap:8px" }, test("on"), test("off"))));
    });
    if (!list.length) rows.push(el("div", { class: "empty-note" }, "No devices yet"));
    rows.push(el("div", { class: "add-row" }, el("button", { class: "btn small", onclick: () => {
      set("devices", [...list, { name: "", type: "home_assistant", entity_id: "" }]); draw();
    } }, "+ Add a device")));
    card.replaceChildren(...rows);
  };
  draw();
  return card;
}

/* ---------- cameras ---------- */
function camerasCard() {
  const card = el("div", { class: "card" });
  const draw = () => {
    const cams = get("cameras.list") || [];
    const rows = [];
    cams.forEach((c, i) => {
      const upd = (k, v) => { cams[i] = { ...cams[i], [k]: v }; set("cameras.list", cams); };
      rows.push(el("div", { class: "item" },
        el("div", { class: "item-head" },
          el("input", { type: "text", value: c.name || "", placeholder: "Name, e.g. Front door", oninput: (e) => upd("name", e.target.value) }),
          el("button", { class: "icon-btn", "aria-label": "Remove", onclick: () => {
            if (!confirm(`Remove ${c.name || "this camera"}?`)) return;
            cams.splice(i, 1); set("cameras.list", cams); draw();
          } }, "✕")),
        el("input", { type: "url", value: c.url || "", placeholder: "rtsp://user:password@192.168.1.50:554/stream2",
          autocomplete: "off", autocapitalize: "off", oninput: (e) => upd("url", e.target.value.trim()) }),
        el("div", {}, el("button", { class: "btn small", onclick: async () => { await flushSave(); openCamera(i); } }, "👁 View here"))));
    });
    if (!cams.length) rows.push(el("div", { class: "empty-note" }, "No cameras yet"));
    rows.push(el("div", { class: "add-row" },
      el("button", { class: "btn small", onclick: () => { set("cameras.list", [...cams, { name: "", url: "" }]); draw(); } }, "+ Add a camera")));
    rows.push(numberRow("cameras.show_seconds", "Hide them again after (seconds)", { min: 0, max: 3600, hint: "0 = stay until hidden" }));
    rows.push(el("details", { class: "help", style: "padding:10px 14px" }, el("summary", {}, "Which cameras work, and their address"),
      el("p", {}, "Cameras with an RTSP stream: Reolink, Amcrest, Tapo, Eufy, Hikvision, Dahua, Annke, Wyze (RTSP firmware or bridge). " +
        "The camera's app or manual lists its RTSP address; use the smaller “sub stream” (often ending in 1 or 2), which is lighter for the Pi."),
      el("p", {}, "Any camera in Home Assistant: type ha: and its entity, e.g. ha:camera.front_door (needs Connections → Home Assistant)."),
      el("p", {}, "A web address that shows an MJPEG stream or a snapshot (.jpg) also works."),
      el("p", {}, "Blink: use the Blink cameras section below. Ring, Nest and Arlo only stream through their own cloud, so they can't be shown directly.")));
    card.replaceChildren(...rows);
  };
  draw();
  return card;
}

/* ---------- Blink ---------- */
function blinkCard() {
  const card = el("div", { class: "card" }, el("div", { class: "empty-note" }, "…"));
  const draw = async () => {
    let b;
    try { b = await api("GET", "/api/remote/blink"); } catch (e) { card.replaceChildren(el("div", { class: "empty-note" }, e.message)); return; }
    const err = b.error ? el("div", { class: "row" }, el("span", { class: "error" }, `⚠️ ${b.error}`)) : null;
    if (b.state === "unavailable") { card.replaceChildren(err || el("div", { class: "empty-note" }, "Blink isn't available")); return; }
    if (b.state === "needs_code") {
      const code = el("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", placeholder: "Code from Blink" });
      card.replaceChildren(el("div", { class: "row stack" },
        el("div", { class: "label" }, "Enter the code Blink just sent you", el("span", { class: "hint" }, "Check your email or texts")),
        code, el("button", { class: "btn primary", onclick: async () => {
          try { await api("POST", "/api/remote/blink/verify", { code: code.value }); toast("Blink connected"); draw(); }
          catch (e) { toast(e.message, 5000); }
        } }, "Verify")), err);
      return;
    }
    if (b.state !== "signed_in") {
      const user = el("input", { type: "email", placeholder: "Blink email", autocomplete: "username", autocapitalize: "off" });
      const pass = el("input", { type: "password", placeholder: "Blink password", autocomplete: "current-password" });
      card.replaceChildren(el("div", { class: "row stack" },
        el("div", { class: "label" }, "Sign in to Blink",
          el("span", { class: "hint" }, "Your password is only used to sign in; the Pi keeps just Blink's access key.")),
        user, pass, el("button", { class: "btn primary", onclick: async (ev) => {
          ev.target.disabled = true;
          try { await api("POST", "/api/remote/blink/login", { username: user.value, password: pass.value }); draw(); }
          catch (e) { toast(e.message, 5000); ev.target.disabled = false; }
        } }, "Sign in")), err);
      return;
    }
    const rows = b.cameras.map((c, i) => el("div", { class: "row" },
      el("div", { class: "label" }, `📷 ${c.name}`, el("span", { class: "hint" },
        [c.battery ? `Battery ${c.battery}` : "", c.last_motion ? `motion ${new Date(c.last_motion).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : ""]
          .filter(Boolean).join(" · "))),
      el("div", { style: "display:flex;gap:6px" },
        el("button", { class: "btn small", onclick: () => openBlink(i) }, "👁"),
        el("button", { class: "btn small", onclick: async (ev) => {
          ev.target.disabled = true; toast("Asking the camera for a new picture…");
          try { await api("POST", `/api/remote/blink/${i}/snap`); openBlink(i); } catch (e) { toast(e.message, 5000); }
          ev.target.disabled = false;
        } }, "📸 New"))));
    card.replaceChildren(
      ...(rows.length ? rows : [el("div", { class: "empty-note" }, "Signed in, but no cameras found")]),
      toggleRow("blink.motion_popups", "Pop up on motion", "Shows the new picture when a camera records motion (Blink must be armed)"),
      numberRow("blink.popup_seconds", "Pop-up stays for (seconds)", { min: 5, max: 600 }),
      numberRow("blink.check_every_seconds", "Check for motion every (seconds)", { min: 30, max: 3600, hint: "60 or more is kinder to Blink" }),
      toggleRow("blink.new_picture_on_show", "New pictures when cameras are shown", "Uses some battery each time"),
      toggleRow("blink.live_view", "Live video (experimental)", "Blink's unofficial live stream; drains battery cameras quickly"),
      err,
      el("div", { class: "row" }, el("div", { class: "label" }, "Blink account"),
        el("button", { class: "btn small danger", onclick: async () => {
          if (!confirm("Sign out of Blink on the screen?")) return;
          await api("POST", "/api/remote/blink/logout").catch((e) => toast(e.message)); draw();
        } }, "Sign out")));
  };
  draw();
  return card;
}

function openBlink(i) {
  $("#viewer-video").hidden = true;
  const img = $("#viewer-img");
  img.hidden = false;
  img.src = get("blink.live_view") ? `/api/blink/${i}.mjpg?t=${Date.now()}` : `/api/blink/${i}.jpg?t=${Date.now()}`;
  const dlg = $("#viewer");
  dlg.addEventListener("close", () => img.removeAttribute("src"), { once: true });
  dlg.showModal();
}

function openCamera(i) {
  $("#viewer-video").hidden = true;
  const img = $("#viewer-img");
  img.hidden = false;
  img.src = `/api/cameras/${i}.mjpg?t=${Date.now()}`;
  const dlg = $("#viewer");
  dlg.addEventListener("close", () => img.removeAttribute("src"), { once: true });
  dlg.showModal();
}

async function power(what) {
  const ask = what === "shutdown"
    ? "Shut down the Pi? Wait until the screen is dark (about 15 seconds) before unplugging it. To turn it back on, unplug and plug it in again."
    : "Restart the Pi? The screen comes back in about a minute.";
  if (!confirm(ask)) return;
  try {
    await api("POST", `/api/remote/power/${what}`);
    toast(what === "shutdown" ? "Shutting down… unplug once the screen is dark" : "Restarting… back in about a minute", 8000);
  } catch (e) { toast(e.message, 6000); }
}

function alexaVoiceCard(actionOptions) {
  const card = el("div", { class: "card" });
  const options = [["", "Nothing"], ...actionOptions.filter(([k]) => k)];
  const draw = () => {
    const list = get("alexa.devices") || [];
    const rows = [toggleRow("alexa.voice_commands", "Turn on Alexa voice commands")];
    if (get("alexa.voice_commands")) {
      list.forEach((d, i) => {
        const upd = (k, v) => { list[i] = { ...list[i], [k]: v }; set("alexa.devices", list); };
        const pick = (k) => el("select", { onchange: (e) => upd(k, e.target.value) },
          options.map(([v, l]) => el("option", { value: v, selected: (d[k] || "") === v }, l)));
        rows.push(el("div", { class: "item" },
          el("div", { class: "item-head" },
            el("input", { type: "text", value: d.name || "", placeholder: "Name Alexa hears, e.g. Photo booth",
              oninput: (e) => upd("name", e.target.value) }),
            el("button", { class: "icon-btn", "aria-label": "Remove", onclick: () => { list.splice(i, 1); set("alexa.devices", list); draw(); } }, "✕")),
          el("div", { class: "row", style: "padding:0;min-height:0;gap:8px" }, el("span", { class: "muted" }, "“turn on”"), pick("on")),
          el("div", { class: "row", style: "padding:0;min-height:0;gap:8px" }, el("span", { class: "muted" }, "“turn off”"), pick("off"))));
      });
      rows.push(el("div", { class: "add-row" }, el("button", { class: "btn small", onclick: () => {
        set("alexa.devices", [...list, { name: "", on: "", off: "" }]); draw();
      } }, "+ Add a voice command")));
      const st = S.status?.alexa;
      if (st && st.enabled && !st.running && st.error) rows.push(el("div", { class: "empty-note" }, `⚠️ ${st.error}`));
    }
    card.replaceChildren(...rows);
  };
  draw();
  return card;
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
    S.actionLabels = data.action_labels || {};
    S.custom = data.custom_gestures || {};
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
