"use strict";

const $ = (id) => document.getElementById(id);
const GESTURE_LABELS = {
  Thumb_Up: "👍", Thumb_Down: "👎", Victory: "✌️", Open_Palm: "✋",
  Closed_Fist: "✊", Pointing_Up: "☝️", ILoveYou: "🤟",
};
const ACTION_LABELS = {
  lights_on: "Lights on", lights_off: "Lights off", lights_toggle: "Toggle lights",
  photo_booth: "Photo booth", wake: "Wake", sleep: "Sleep",
};

let settings = { bindings: {}, messages: [], seconds_per_photo: 30 };
const timers = {};
// ?preview=1: the small live preview inside the phone app (silent, no camera, no pairing code).
const PREVIEW = new URLSearchParams(location.search).has("preview");
const SHOW_KEYS = ["clock", "date", "greeting", "weather", "forecast", "calendar", "messages", "news", "gesture_help"];

function applySettings(s) {
  const prev = settings;
  settings = s;
  const body = document.body;
  body.classList.toggle("mirror", s.style === "mirror");
  body.classList.toggle("layout-right", s.layout === "right");
  const show = s.show || {};
  for (const key of SHOW_KEYS) body.classList.toggle(`hide-${key}`, show[key] === false);
  const theme = s.theme || {};
  const root = document.documentElement.style;
  const clamp = (v, lo, hi, d) => (typeof v === "number" && isFinite(v) ? Math.min(hi, Math.max(lo, v)) : d);
  if (/^#[0-9a-f]{3,8}$/i.test(theme.accent_color || "")) root.setProperty("--accent", theme.accent_color);
  else root.removeProperty("--accent");
  root.setProperty("--scale", clamp(theme.text_size, 0.6, 1.6, 1));
  root.setProperty("--card-alpha", clamp(theme.card_opacity, 0, 1, 0.5));
  root.setProperty("--photo-dim", clamp(theme.photo_dim, 0, 0.9, 0.35));
  tickClock();
  renderLegend();
  if (JSON.stringify(prev.messages) !== JSON.stringify(s.messages)) { msgIndex = 0; rotateMessage(); }
  if (prev.version) maybeRebuildTiles();
}

async function reloadSettings() {
  try {
    applySettings(await getJSON("/api/settings"));
  } catch (e) {
    console.warn("settings failed", e);
    return;
  }
  loadWeather();
  loadCalendar();
  loadNews().then(rotateNews);
}

let pairTimer = null;
function showPairing(d) {
  clearInterval(pairTimer);
  if (PREVIEW || !d.code) {
    $("pair").hidden = true;
    if (d.paired && !PREVIEW) showStatus("📱 Phone paired", 4000);
    return;
  }
  $("pair-code").textContent = `${d.code.slice(0, 3)} ${d.code.slice(3)}`;
  $("pair").hidden = false;
  const until = Date.now() + d.seconds * 1000;
  const tick = () => {
    const left = Math.max(0, Math.round((until - Date.now()) / 1000));
    $("pair-left").textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    if (!left) { clearInterval(pairTimer); $("pair").hidden = true; }
  };
  tick();
  pairTimer = setInterval(tick, 1000);
}

function later(name, ms, fn) {
  clearTimeout(timers[name]);
  timers[name] = setTimeout(fn, ms);
}

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

/* ---------- clock & greeting ---------- */
function partOfDay(h) {
  return h < 5 ? "night" : h < 12 ? "morning" : h < 17 ? "afternoon" : h < 22 ? "evening" : "night";
}
function tickClock() {
  const now = new Date();
  const h24 = settings.clock_24h;
  let h = now.getHours();
  const m = String(now.getMinutes()).padStart(2, "0");
  $("ampm").textContent = h24 ? "" : (h < 12 ? "AM" : "PM");
  if (!h24) h = h % 12 || 12;
  $("time").textContent = `${h24 ? String(h).padStart(2, "0") : h}:${m}`;
  $("secs").textContent = settings.show_seconds ? String(now.getSeconds()).padStart(2, "0") : "";
  $("date").textContent = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
  $("greeting").textContent = (settings.greeting || "").replace("{part_of_day}", partOfDay(now.getHours()));
}

/* ---------- weather (open-meteo, no key) ---------- */
function weatherInfo(code, isDay = 1) {
  if (code === 0) return [isDay ? "☀️" : "🌙", "Clear"];
  if (code <= 2) return [isDay ? "🌤️" : "☁️", "Partly cloudy"];
  if (code === 3) return ["☁️", "Cloudy"];
  if (code <= 48) return ["🌫️", "Fog"];
  if (code <= 57) return ["🌦️", "Drizzle"];
  if (code <= 67) return ["🌧️", "Rain"];
  if (code <= 77) return ["🌨️", "Snow"];
  if (code <= 82) return ["🌦️", "Showers"];
  if (code <= 86) return ["🌨️", "Snow showers"];
  return ["⛈️", "Thunderstorms"];
}
const hhmm = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: !settings.clock_24h });

async function loadWeather() {
  const w = settings.weather;
  if (!w || w.latitude == null) return;
  const unit = w.units === "celsius" ? "celsius" : "fahrenheit";
  const url = `https://api.open-meteo.com/v1/forecast?latitude=${w.latitude}&longitude=${w.longitude}` +
    `&current=temperature_2m,apparent_temperature,weather_code,is_day` +
    `&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_probability_max` +
    `&forecast_days=6&timezone=auto&temperature_unit=${unit}`;
  try {
    const d = await getJSON(url);
    const [icon, sky] = weatherInfo(d.current.weather_code, d.current.is_day);
    $("w-icon").textContent = icon;
    $("temp").textContent = `${Math.round(d.current.temperature_2m)}°`;
    const rain = d.daily.precipitation_probability_max[0];
    $("sky").textContent = `${sky} · feels ${Math.round(d.current.apparent_temperature)}° · ` +
      `H ${Math.round(d.daily.temperature_2m_max[0])}° L ${Math.round(d.daily.temperature_2m_min[0])}°` +
      (rain >= 20 ? ` · ${rain}% rain` : "");
    $("sun").textContent = `☀ ${hhmm(d.daily.sunrise[0])}   ☾ ${hhmm(d.daily.sunset[0])}`;
    const ol = $("forecast");
    ol.replaceChildren();
    for (let i = 1; i < d.daily.time.length; i++) {
      const li = document.createElement("li");
      const day = new Date(d.daily.time[i] + "T12:00").toLocaleDateString([], { weekday: "short" });
      li.innerHTML = `<div class="d"></div><div class="i"></div><div><span class="hi"></span><span class="lo"></span></div>`;
      li.querySelector(".d").textContent = day;
      li.querySelector(".i").textContent = weatherInfo(d.daily.weather_code[i])[0];
      li.querySelector(".hi").textContent = `${Math.round(d.daily.temperature_2m_max[i])}°`;
      li.querySelector(".lo").textContent = `${Math.round(d.daily.temperature_2m_min[i])}°`;
      ol.append(li);
    }
    $("weather").hidden = false;
  } catch (e) {
    console.warn("weather failed", e);
  }
}

/* ---------- calendar ---------- */
function dayLabel(date) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((new Date(date).setHours(0, 0, 0, 0) - today) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  return date.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
}

let calendarEvents = [];
async function loadCalendar() {
  try {
    calendarEvents = await getJSON("/api/calendar");
  } catch (e) {
    console.warn("calendar failed", e);
    return;
  }
  renderAgenda();
}

function renderAgenda() {
  const events = calendarEvents;
  const box = $("events");
  box.replaceChildren();
  if (!events.length) {
    $("agenda").hidden = true;
    return;
  }
  const now = new Date();
  let current = null;
  let group = null;
  for (const ev of events) {
    const start = new Date(ev.start);
    const end = new Date(ev.end);
    const label = start < now ? "Today" : dayLabel(start);
    if (label !== current) {
      current = label;
      group = document.createElement("div");
      group.className = "day";
      const name = document.createElement("div");
      name.className = "day-name";
      name.textContent = label;
      group.append(name);
      box.append(group);
    }
    const row = document.createElement("div");
    row.className = "event" + (!ev.all_day && start <= now && now < end ? " now" : "");
    row.innerHTML = `<span class="dot"></span><span class="when"></span><span class="what"></span>`;
    if (ev.color) row.querySelector(".dot").style.background = ev.color;
    row.querySelector(".when").textContent = ev.all_day ? "All day" : hhmm(ev.start);
    row.querySelector(".what").textContent = ev.title;
    group.append(row);
  }
  $("agenda").hidden = false;
  fitAgenda();
}

// Drop events from the end until the list fits on screen (no half-cut rows, no empty day headings).
function fitAgenda() {
  const card = $("agenda");
  const box = $("events");
  const fits = () => card.scrollHeight <= card.clientHeight + 1;
  let removed = 0;
  const dropLast = () => {
    const rows = box.querySelectorAll(".event");
    if (rows.length <= 1) return false;
    const last = rows[rows.length - 1];
    const group = last.parentElement;
    last.remove();
    removed++;
    if (!group.querySelector(".event")) group.remove();
    return true;
  };
  while (!fits() && dropLast()) { /* keep dropping */ }
  if (!removed) return;
  const more = document.createElement("div");
  more.className = "empty";
  more.textContent = `+ ${removed} more`;
  box.append(more);
  while (!fits() && dropLast()) more.textContent = `+ ${removed} more`;
}

/* ---------- slideshow: 1-6 tiles of photos and videos ---------- */
// Tile layouts for a landscape screen as [columns, rows, [[col, row, colSpan, rowSpan], ...]].
// On a portrait screen the same layouts are turned on their side.
const TILE_LAYOUTS = {
  1: [1, 1, [[1, 1, 1, 1]]],
  2: [2, 1, [[1, 1, 1, 1], [2, 1, 1, 1]]],
  3: [3, 2, [[1, 1, 2, 2], [3, 1, 1, 1], [3, 2, 1, 1]]],
  4: [2, 2, [[1, 1, 1, 1], [2, 1, 1, 1], [1, 2, 1, 1], [2, 2, 1, 1]]],
  5: [6, 2, [[1, 1, 2, 1], [3, 1, 2, 1], [5, 1, 2, 1], [1, 2, 3, 1], [4, 2, 3, 1]]],
  6: [3, 2, [[1, 1, 1, 1], [2, 1, 1, 1], [3, 1, 1, 1], [1, 2, 1, 1], [2, 2, 1, 1], [3, 2, 1, 1]]],
};
const media = { pool: [], i: 0, tiles: [], key: "", paused: false };

async function refillPool() {
  try {
    media.pool = await getJSON("/api/backgrounds");
  } catch (e) {
    console.warn("backgrounds failed", e);
  }
  media.i = 0;
}

function videosPlaying(except) {
  return media.tiles.filter((t) => t !== except && t.item && t.item.type === "video").length;
}

async function pickItem(tile) {
  if (media.i >= media.pool.length) await refillPool();
  const n = media.pool.length;
  const showing = new Set(media.tiles.filter((t) => t !== tile && t.item).map((t) => t.item.id));
  const maxVideos = Number(settings.videos_at_once ?? 1);
  for (let tries = 0; tries < n; tries++) {
    if (media.i >= n) { await refillPool(); if (!media.pool.length) return null; }
    const item = media.pool[media.i++];
    if (showing.has(item.id) && n > media.tiles.length) continue;
    if (item.type === "video" && videosPlaying(tile) >= maxVideos) continue;
    return item;
  }
  return null;
}

function buildTiles() {
  const box = $("tiles");
  for (const t of media.tiles) { clearTimeout(t.timer); t.layers.forEach(clearLayer); }
  box.replaceChildren();
  media.tiles = [];
  if (settings.style === "mirror") return;
  const n = Math.max(1, Math.min(6, Number(settings.tiles) || 1));
  const portrait = innerHeight > innerWidth;
  let [cols, rows, cells] = TILE_LAYOUTS[n];
  if (portrait) {
    [cols, rows] = [rows, cols];
    cells = cells.map(([c, r, cs, rs]) => [r, c, rs, cs]);
  }
  box.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  box.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
  box.classList.toggle("many", n > 1);
  cells.forEach(([c, r, cs, rs], i) => {
    const el = document.createElement("div");
    el.className = "tile";
    el.style.gridArea = `${r} / ${c} / span ${rs} / span ${cs}`;
    const layers = [document.createElement("div"), document.createElement("div")];
    layers.forEach((l) => { l.className = "layer"; el.append(l); });
    box.append(el);
    const tile = { el, layers, cur: 0, item: null, timer: null };
    media.tiles.push(tile);
    // Stagger the tiles so they don't all change at the same moment.
    tile.timer = setTimeout(() => advance(tile), i * 900);
  });
}

function clearLayer(layer) {
  const v = layer.querySelector("video");
  if (v) { v.pause(); v.removeAttribute("src"); v.load(); }
  layer.replaceChildren();
  layer.style.backgroundImage = "";
  layer.classList.remove("show", "img");
}

function swapIn(tile, fill) {
  const next = tile.layers[tile.cur ^ 1];
  const prev = tile.layers[tile.cur];
  clearLayer(next);
  fill(next);
  void next.offsetWidth; // restart the drift animation
  next.classList.add("show");
  prev.classList.remove("show");
  tile.cur ^= 1;
  setTimeout(() => { if (!prev.classList.contains("show")) clearLayer(prev); }, 2600);
}

async function advance(tile) {
  clearTimeout(tile.timer);
  if (!media.tiles.includes(tile)) return;
  const seconds = Math.max(5, Number(settings.seconds_per_photo) || 30);
  const item = await pickItem(tile);
  if (!item) { tile.timer = setTimeout(() => advance(tile), 15000); return; }
  if (item.type === "video") {
    const v = document.createElement("video");
    v.muted = true;
    v.playsInline = true;
    v.preload = "auto";
    v.src = `/bg/${item.id}.mp4`;
    let started = false;
    v.oncanplay = () => {
      if (started) return;
      started = true;
      tile.item = item;
      swapIn(tile, (layer) => layer.append(v));
      if (!media.paused) v.play().catch(() => {});
    };
    v.onended = () => advance(tile);
    v.onerror = () => { tile.timer = setTimeout(() => advance(tile), 1000); };
    // A video that never loads shouldn't freeze the tile.
    tile.timer = setTimeout(() => { if (!started) advance(tile); }, 15000);
  } else {
    const url = `/bg/${item.id}.jpg`;
    const img = new Image();
    img.onload = () => {
      tile.item = item;
      swapIn(tile, (layer) => { layer.classList.add("img"); layer.style.backgroundImage = `url("${url}")`; });
      tile.timer = setTimeout(() => advance(tile), seconds * 1000);
    };
    img.onerror = () => { tile.timer = setTimeout(() => advance(tile), 1000); };
    img.src = url;
  }
}

function pauseMedia(on) {
  media.paused = on;
  for (const v of document.querySelectorAll("#tiles video")) on ? v.pause() : v.play().catch(() => {});
}

function maybeRebuildTiles() {
  const key = [settings.style, settings.tiles, settings.include_videos, settings.videos_at_once,
    innerHeight > innerWidth].join("|");
  if (key === media.key) return;
  media.key = key;
  media.pool = [];
  media.i = 0;
  buildTiles();
}

/* ---------- messages & news ---------- */
let msgIndex = 0;
function rotateMessage() {
  const list = settings.messages || [];
  const el = $("message");
  if (!list.length) return;
  el.classList.add("fade");
  setTimeout(() => {
    el.textContent = list[msgIndex++ % list.length];
    el.classList.remove("fade");
  }, 800);
}

let headlines = [];
let newsIndex = 0;
async function loadNews() {
  try {
    headlines = await getJSON("/api/news");
  } catch (e) {
    console.warn("news failed", e);
  }
  $("news").hidden = !headlines.length;
}
function rotateNews() {
  if (!headlines.length) return;
  const el = $("news");
  el.classList.add("fade");
  setTimeout(() => {
    const h = headlines[newsIndex++ % headlines.length];
    $("news-source").textContent = h.source;
    $("news-title").textContent = h.title;
    el.classList.remove("fade");
  }, 800);
}

/* ---------- legend ---------- */
function renderLegend() {
  const ul = $("legend");
  ul.replaceChildren();
  for (const [gesture, action] of Object.entries(settings.bindings || {})) {
    const li = document.createElement("li");
    const b = document.createElement("b");
    b.textContent = GESTURE_LABELS[gesture] || gesture;
    li.append(b, " ", ACTION_LABELS[action] || action);
    ul.append(li);
  }
}

/* ---------- overlays ---------- */
function showStatus(text, ms = 4000) {
  const el = $("status");
  el.textContent = text;
  el.hidden = false;
  later("status", ms, () => { el.hidden = true; });
}

function showHold(name, progress) {
  const el = $("hold");
  if (!name || progress <= 0 || !(name in (settings.bindings || {}))) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  $("hold-label").textContent = GESTURE_LABELS[name] || name;
  $("ring").style.strokeDashoffset = String(276.5 * (1 - progress));
}

let audio = null;
function beep(freq, ms) {
  if (PREVIEW) return;
  try {
    audio = audio || new AudioContext();
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.25, audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + ms / 1000);
    osc.connect(gain).connect(audio.destination);
    osc.start();
    osc.stop(audio.currentTime + ms / 1000);
  } catch (e) { /* no speakers: fine */ }
}

function boothActive(on) {
  document.body.classList.toggle("busy", on);
  pauseMedia(on || document.body.classList.contains("asleep")); // free the Pi for the camera
  $("booth").hidden = !on;
  if (on && settings.camera && !PREVIEW) {
    $("preview").src = "/stream.mjpg?t=" + Date.now();
  } else {
    $("preview").removeAttribute("src"); // closes the stream
    $("countdown").textContent = "";
    $("shot-label").textContent = "";
  }
}

function showCountdown(n) {
  const el = $("countdown");
  el.textContent = n;
  el.classList.remove("tick");
  void el.offsetWidth;
  el.classList.add("tick");
  beep(660, 150);
}

function showPhoto(url) {
  $("photo-img").src = url + "?t=" + Date.now();
  $("photo").hidden = false;
  document.body.classList.add("busy");
  later("photo", 15000, () => {
    $("photo").hidden = true;
    document.body.classList.remove("busy");
  });
}

/* ---------- events from the server ---------- */
function connect() {
  const es = new EventSource("/events");
  const on = (type, fn) => es.addEventListener(type, (e) => fn(JSON.parse(e.data || "{}")));
  let dropped = false;

  es.onerror = () => { dropped = true; };
  es.onopen = async () => {
    if (!dropped) return;
    // The server restarted (for example after an update): reload if the code changed.
    try {
      const s = await getJSON("/api/settings");
      if (s.version !== settings.version) location.reload();
    } catch (e) { /* try again on the next reconnect */ }
  };

  on("wake", () => { document.body.classList.remove("asleep"); pauseMedia(false); });
  on("sleep", () => { document.body.classList.add("asleep"); showHold(null, 0); pauseMedia(true); });
  on("hold", (d) => showHold(d.name, d.progress));
  on("gesture", (d) => {
    showHold(null, 0);
    if (d.action && d.action !== "photo_booth") {
      showStatus(`${GESTURE_LABELS[d.name] || d.name}  ${ACTION_LABELS[d.action] || d.action}`, 2000);
    }
  });
  on("lights", (d) => {
    $("lights").hidden = !d.on;
    showStatus(d.on ? "💡 Lights on" : "Lights off", 2000);
  });
  on("booth", (d) => boothActive(d.active));
  on("countdown", (d) => showCountdown(d.n));
  on("flash", (d) => {
    $("flash").hidden = !d.on;
    if (d.on) { $("countdown").textContent = ""; beep(1200, 250); }
  });
  on("photo", (d) => showPhoto(d.url));
  on("settings", reloadSettings);
  on("pair", showPairing);
  on("status", (d) => {
    if (!$("booth").hidden && d.text.startsWith("Photo ")) $("shot-label").textContent = d.text;
    else showStatus(d.text, 5000);
  });
}

async function init() {
  try {
    applySettings(await getJSON("/api/settings"));
  } catch (e) {
    console.warn("settings failed", e);
  }
  if (PREVIEW) document.body.classList.add("preview");
  else if (settings.problems && settings.problems.length) showStatus("⚠️ " + settings.problems.join(" · "), 15000);
  setInterval(tickClock, 1000);
  renderLegend();
  rotateMessage();
  setInterval(rotateMessage, 60 * 1000);
  loadWeather();
  setInterval(loadWeather, 15 * 60 * 1000);
  loadCalendar();
  setInterval(loadCalendar, 5 * 60 * 1000);
  await loadNews();
  rotateNews();
  setInterval(rotateNews, 12 * 1000);
  setInterval(loadNews, 30 * 60 * 1000);
  maybeRebuildTiles();
  addEventListener("resize", () => later("tiles", 500, maybeRebuildTiles));
  connect();
  // Re-fit the agenda whenever the layout changes size (fonts loading, weather appearing, rotation).
  let fitTimer = null;
  const refit = new ResizeObserver(() => { clearTimeout(fitTimer); fitTimer = setTimeout(renderAgenda, 200); });
  for (const el of [$("dash"), document.querySelector(".clock"), $("weather"), document.querySelector(".footer")]) refit.observe(el);
  document.fonts.ready.then(renderAgenda);
  setTimeout(renderAgenda, 2500);
}
init();
