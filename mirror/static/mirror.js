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
  if (prev.seconds_per_photo !== s.seconds_per_photo && prev.version) later("slide", 1000, nextSlide);
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

// Drop events from the end until the list fits on screen (no half-cut rows).
function fitAgenda() {
  const card = $("agenda");
  let rows = card.querySelectorAll(".event");
  let removed = 0;
  while (card.scrollHeight > card.clientHeight + 1 && rows.length > 1) {
    const last = rows[rows.length - 1];
    const group = last.parentElement;
    last.remove();
    removed++;
    if (!group.querySelector(".event")) group.remove();
    rows = card.querySelectorAll(".event");
  }
  if (removed) {
    const more = document.createElement("div");
    more.className = "empty";
    more.textContent = `+ ${removed} more`;
    $("events").append(more);
    if (card.scrollHeight > card.clientHeight + 1 && rows.length > 1) {
      more.remove();
      rows[rows.length - 1].remove();
    }
  }
}

/* ---------- slideshow ---------- */
const slides = { ids: [], i: 0, layer: 0 };
async function nextSlide() {
  if (settings.style === "mirror") return;
  try {
    if (slides.i >= slides.ids.length) {
      slides.ids = await getJSON("/api/backgrounds");
      slides.i = 0;
    }
  } catch (e) {
    console.warn("backgrounds failed", e);
  }
  if (slides.ids.length) {
    const url = `/bg/${slides.ids[slides.i++]}.jpg`;
    const img = new Image();
    img.onload = () => {
      const layers = document.querySelectorAll(".bg");
      const next = layers[slides.layer ^ 1];
      next.style.backgroundImage = `url("${url}")`;
      next.classList.remove("show");
      void next.offsetWidth; // restart the drift animation
      next.classList.add("show");
      layers[slides.layer].classList.remove("show");
      slides.layer ^= 1;
    };
    img.src = url;
  }
  later("slide", Math.max(5, settings.seconds_per_photo) * 1000, nextSlide);
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

  on("wake", () => document.body.classList.remove("asleep"));
  on("sleep", () => { document.body.classList.add("asleep"); showHold(null, 0); });
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
  nextSlide();
  connect();
  // Re-fit the agenda whenever the layout changes size (fonts loading, weather appearing, rotation).
  let fitTimer = null;
  const refit = new ResizeObserver(() => { clearTimeout(fitTimer); fitTimer = setTimeout(renderAgenda, 200); });
  for (const el of [$("dash"), document.querySelector(".clock"), $("weather"), document.querySelector(".footer")]) refit.observe(el);
  document.fonts.ready.then(renderAgenda);
  setTimeout(renderAgenda, 2500);
}
init();
