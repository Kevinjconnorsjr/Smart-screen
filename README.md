# Smart Screen: smart display + gesture photo booth

A wall display for a regular landscape monitor, run by a Raspberry Pi:

- **Clock, date and greeting.** "Good morning, Connors family!" changes through the day.
- **Weather.** Current conditions, feels-like temperature, chance of rain, sunrise/sunset and a 5-day forecast. Free, no account.
- **Your calendars.** Google, iCloud and Outlook, merged into one "Coming up" list, color-coded per calendar.
- **Photo slideshow background.** Your own photos slowly drift and cross-fade. iPhone HEIC photos work.
  Photo booth pictures join the slideshow automatically.
- **News headlines and rotating messages** along the bottom.
- **Hand gestures** that turn lights on and off and start a **photo booth**. The booth shows a live camera preview, counts down with beeps,
  flashes the screen white as a fill light, takes 4 photos and **prints** them as classic photo strips.
- **Screen sleep.** The screen goes dark when nobody's using it and wakes when you raise a hand.
- **Updates itself from GitHub.** Push a change and the Pi picks it up within 15 minutes.
- **Android phone app.** Control the screen, change its look with a live preview, edit calendars and messages,
  add photos from your phone, and reprint photo booth pictures. See [Phone app](#phone-app).

Gesture recognition runs on the Pi itself (Google MediaPipe). No camera images leave the house.

| Gesture | Default action |
|---|---|
| 👍 Thumb up | Lights on |
| 👎 Thumb down | Lights off |
| ☝️ Pointing up | Toggle lights |
| ✌️ Victory / peace | **Photo booth** |
| ✋ Open palm | Wake the screen |
| ✊ Closed fist | Sleep |

Hold a gesture for about a second. A ring fills up on screen while you hold it, so waving around won't trigger anything.
You can remap gestures in `config.yaml`. 🤟 `ILoveYou` is recognized too and is free to use.

---

## 1. What to buy

| Part | Why / notes | Approx. |
|---|---|---|
| **Raspberry Pi 5, 4 GB or 8 GB** | The Pi 5 runs gesture recognition, the camera and the browser together comfortably. 4 GB is enough; 8 GB gives headroom. A Pi 4 works but is noticeably slower. | $60–80 |
| **Official Raspberry Pi 27 W USB-C power supply** | The Pi 5 needs the full 5 A to power a webcam and printer over USB. Phone chargers cause random freezes. | $12 |
| **Active Cooler** (or the official Pi 5 case, which has a fan) | Gesture recognition keeps the CPU busy all day. | $5–10 |
| **microSD card, 64 GB, "A2" rated** (SanDisk Extreme / Samsung EVO Plus) | Holds the system, your photos and the booth pictures. | $12 |
| **Micro-HDMI to HDMI cable** | ⚠️ The Pi 5 has *micro*-HDMI ports, so a normal HDMI cable won't fit. | $8 |
| **Monitor**, 24"–32", 1080p or higher, landscape | Any monitor or TV with HDMI. If you want to hang it, get one with VESA mounting holes. Built-in speakers let you hear the countdown beeps (optional). | $100–200 |
| **USB webcam, 1080p** (e.g. Logitech C920 / C922 / Brio 100) | Clips onto the top of the monitor, so it's the easiest option for a monitor build. A Pi Camera Module 3 Wide also works but needs a mount and a longer ribbon cable. | $30–70 |
| **VESA mount for the Pi** (screws onto the back of the monitor) | Hides the Pi behind the screen. | $10 |
| **Photo printer**: Canon SELPHY CP1300 / CP1500 + KP-108IN paper & ink kit | Dye-sub 4x6 prints that look like real photos, and the kit costs about $0.35 per print. Before buying, check your exact model in the [Gutenprint supported printers list](https://gimp-print.sourceforge.io/p_Supported_Printers.php), because that's what lets the Pi print to it. | $130 + $35 |
| **Smart lights** (optional) | Anything Home Assistant controls, Philips Hue, or plugs/switches with a web URL (Shelly, Tasmota). | — |
| USB keyboard + mouse | Only needed during setup. | — |

**Typical total: about $350–500** with a new monitor and printer, less if you reuse a monitor.

> Building a real two-way **mirror** instead? Set `display.style: mirror` (black background) and see
> "Mirror build" at the end.

## 2. Set up the Pi (about 30 minutes)

1. **Flash the SD card.** On your computer, install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) and choose:
   - Device: *Raspberry Pi 5*
   - OS: *Raspberry Pi OS (64-bit)*, the regular one with the desktop
   - Click *Edit settings*: set a username/password, your **Wi-Fi**, your time zone, and turn on **SSH**.
2. **Connect everything**: the monitor (micro-HDMI port 0, next to the USB-C power), the webcam, the printer (USB), and the keyboard/mouse.
   Power on and wait for the desktop.
3. **Open a Terminal** and download the code from GitHub:
   ```bash
   git clone https://github.com/Kevinjconnorsjr/Smart-screen.git
   cd Smart-screen
   ```
4. **Run the installer:**
   ```bash
   ./deploy/install.sh
   ```
   This installs everything, downloads the gesture model, creates `config.yaml`, starts the display at boot,
   opens it full-screen when the desktop loads, and turns on automatic updates.
5. **Edit your settings** (weather location, calendars, lights, printer):
   ```bash
   nano config.yaml
   ```
   Save with Ctrl+O, Enter, then exit with Ctrl+X. Then `sudo reboot`.

From then on, the Pi boots straight into the display. To change settings later from your computer,
connect with `ssh YOUR-USER@raspberrypi.local`, edit `config.yaml`, then run `sudo systemctl restart smart-screen`.

## 3. Automatic updates from GitHub

Every 15 minutes (and 3 minutes after boot), the Pi checks GitHub for new commits on the branch it's using.
If there are any, it downloads them, installs any new Python packages, restarts, and the screen reloads itself.

- **To update the display:** push a change to GitHub. That's it.
- **To update right now:** `sudo ./deploy/update.sh`
- **To see what happened:** `journalctl -u smart-screen-update`
- **Your settings and photos are safe, and stay private.** `config.yaml`, `backgrounds/` and `photos/` are never uploaded to GitHub
  (they're in `.gitignore`), so updates never touch them. Because the repo is public, keep it that way: your calendar
  links and light tokens live in `config.yaml` and must never be committed.
- **Edits made on the Pi are kept.** If you edited code directly on the Pi, updates are skipped (and logged) instead of overwriting your work.
- **Using a different branch:** the Pi follows whichever branch is checked out (`git checkout some-branch`).
- **Turning updates off:** `sudo systemctl disable --now smart-screen-update.timer`

The repo is public, so the Pi downloads updates with no login or keys.

**If you ever make the repo private**, the Pi needs read access. The simplest secure way is a *deploy key*:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/smart_display -N ""      # on the Pi
cat ~/.ssh/smart_display.pub                               # copy this
```

On GitHub, open the repo → *Settings → Deploy keys → Add deploy key*, paste the key, and leave "write access" **off**. Then on the Pi:

```bash
printf 'Host github.com\n  IdentityFile ~/.ssh/smart_display\n' >> ~/.ssh/config
git remote set-url origin git@github.com:Kevinjconnorsjr/Smart-screen.git
sudo ./deploy/update.sh    # test it
```

## 4. Your calendars

The display reads each calendar's private **iCal link**, so it needs no sign-in or passwords.
Add as many calendars as you like under `calendar.feeds` in `config.yaml`, each with its own color.

- **Google Calendar** (on a computer): ⚙️ *Settings* → click the calendar on the left → *Integrate calendar* →
  copy **"Secret address in iCal format"**. (Work/school accounts may have this turned off by the admin.)
- **iCloud**: Calendar app → tap ⓘ next to the calendar → turn on **Public Calendar** → *Share Link*.
  The link starts with `webcal://`, which works as is.
- **Outlook / Microsoft 365**: Outlook on the web → ⚙️ *Settings → Calendar → Shared calendars → Publish a calendar* →
  choose *Can view all details* → copy the **ICS** link.

```yaml
calendar:
  feeds:
    - name: Family
      url: "https://calendar.google.com/calendar/ical/.../basic.ics"
      color: "#7cc4ff"
    - name: Kevin
      url: "webcal://p123-caldav.icloud.com/published/2/..."
      color: "#ffb86b"
```

Treat these links like passwords: anyone with the link can read the calendar. That's another reason to keep
`config.yaml` out of GitHub (it's already in `.gitignore`).

## 5. Background photos & videos

Put photos **and videos** in the **`backgrounds/`** folder (subfolders are fine), add them from the phone app
(**Photos → Add photos & videos**), or show a **Google Drive folder** (below). JPG, PNG, iPhone HEIC, MP4 and MOV all work.

- Photos are resized to the screen once and cached, so even huge photos display smoothly.
- Videos are converted once, in the background, to a small silent clip that plays smoothly on the Pi
  (the Pi 5 has no hardware decoder for phone video, and its browser can't play iPhone HEVC at all).
  A video joins the slideshow when its copy is ready; the app shows ⏳ until then. Long videos are cut
  to **Longest video** (60 s by default).
- **How many at once:** in the app, **Look → Photos & videos**: 1 to 6 tiles on screen as a collage, each changing
  at a different moment, and how many videos may play at the same time. One or two stays smooth on a Pi 5.
- Photo booth pictures join the slideshow too (turn off under Look).

### Google Drive photos

The Pi keeps a copy of one Google Drive folder (subfolders included) and checks it for changes every 30 minutes.
Photos you add to the folder appear on the screen, and ones you delete disappear. The Pi only gets **read-only**
access: it can never change or delete anything in your Drive.

1. In Google Drive, make a folder, e.g. **Smart Screen**, and put photos and videos in it. Share it with the family
   so everyone can add to it.
2. Once, **at the Pi's own desktop** (a browser window opens to sign in), run in a Terminal:
   ```bash
   cd ~/smart-screen
   ./deploy/connect-google-drive.sh
   ```
   Sign in, click **Allow**, and it lists your Drive folders.
3. In the phone app: **Photos → Google Drive**, turn it on and type the folder name. Tap **Sync now** to see it straight away.

Drive photos are marked ☁️ in the app. Remove them in Drive, not in the app.
If the folder was shared with you by someone else, turn on **It's a folder someone shared with me**.
(Google *Photos* albums can't be used. Google closed that to apps in 2025. A Google *Drive* folder works.)

Other ways to get photos onto the Pi: drag them in from a **USB stick** with the Pi's file manager, or
`scp -r ~/Pictures/Favorites kevin@smartscreen.local:smart-screen/backgrounds/` from a computer.

## 6. Lights

Pick the light system in the app (**Settings → Lights**), or set `lights.backend` in `config.yaml`. Gestures and the
app's **Home → Lights** panel then turn lights on and off, and with Home Assistant or Hue also set **brightness**
and **colour** (warm, cool, red, blue…), plus **🎉 party** colours. New gesture actions: *Dim lights*,
*Bright lights*, *Warm light*, *Cool light*, *Party colours*.

- **`alexa`**: lights you control with Alexa. See [Alexa](#alexa) below.
- **`home_assistant`** (works with almost any smart-light brand; full brightness and colour)
  - `url`: e.g. `http://homeassistant.local:8123`
  - `token`: in Home Assistant, click your profile → *Security* → *Long-lived access tokens* → *Create token*
  - `entity_ids`: your lights, e.g. `[light.living_room, light.lamp]` (find them in *Settings → Entities*)
- **`hue`** (Philips Hue bridge, no Home Assistant needed; full brightness and colour)
  - Find the bridge IP in the Hue app (*Settings → Bridges*).
  - Press the round button on the bridge, then within 30 seconds run on the Pi:
    `curl -X POST http://BRIDGE_IP/api -d '{"devicetype":"smart_display#pi"}'` and copy the `username` it returns.
  - `light_ids`: open `http://BRIDGE_IP/api/USERNAME/lights` in a browser to see the numbers.
- **`webhook`**: a URL for on, off, and optionally dim/bright, e.g. Shelly `http://IP/relay/0?turn=on`
  or Tasmota `http://IP/cm?cmnd=Power%20On`.

## Alexa

The Smart Screen and Alexa can work in both directions.

### "Alexa, turn on the photo booth" (Alexa → screen)

The Pi appears in the Alexa app as smart plugs (using [fauxmo](https://github.com/n8henrie/fauxmo); everything stays
on your Wi-Fi, with no skill or account needed). Each "plug" runs a screen action when turned on or off.

1. App: **Settings → Alexa voice commands**, turn it on. The screen restarts.
2. Say **"Alexa, discover devices"** (or Alexa app → Devices → + → Add device → Other → Discover).
3. Try **"Alexa, turn on the photo booth"** or **"Alexa, turn off the smart screen"**.

Add your own voice commands in the app: a name Alexa hears, plus what "turn on" and "turn off" do. Each one can
also be used in Alexa **routines**, for example "when I say good night, turn off the smart screen".
The Echo and the Pi must be on the same Wi-Fi. Most Echo models find these devices; if yours doesn't, check
Settings → Alexa voice commands in the app for an error.

### Lights in the Alexa app (screen → Alexa)

If your lights only work through Alexa (no Home Assistant or Hue bridge), the screen can run **Alexa routines**
through the free **Voice Monkey** skill (200 triggers a month for free):

1. In the Alexa app, enable the **Voice Monkey** skill and link it. Then sign in at **app.voicemonkey.io**.
2. At **app.voicemonkey.io/tokens**, click **Create Token** and copy it.
3. At **app.voicemonkey.io/routines**, click **Add New Trigger** for each thing you want, e.g. *Screen lights on*,
   *Screen lights off*, *Screen lights dim*, *Screen party*. Copy each trigger's **ID**.
4. In the Alexa app: **More → Routines → +**. Under *When this happens*: **Smart Home → Alexa Voice Monkey v3 →
   VM Routines → your trigger**. Under *Add action*: what the lights should do (on, off, 30%, a colour, a group…). Save.
   Do this for each trigger.
5. Phone app: **Settings → Lights → Light system: Alexa**. Paste the token, and each trigger ID next to the matching
   button (on, off, dim, bright, warm, cool, party). Leave the ones you don't use empty.

Now 👍/👎 and the app's light buttons run those routines. Each press uses one of the 200 free monthly triggers.

## Vertical (portrait) screen

Stand the monitor on its side, then in the app: **Look → Screen → Orientation: Portrait**. The Pi turns the picture
and switches to a tall layout: big clock on top, weather, a taller calendar, and the photo collage behind.

- If the picture is upside down, change **Monitor was turned** to the other direction.
- If the webcam is clipped to the monitor, it's now on its side too: **Settings → Camera → Camera mounted** so hand
  gestures and photo booth pictures come out upright.
- If the picture doesn't turn (e.g. right after starting), restart the Pi from the app. As a fallback, set the
  rotation in the Pi's **Menu → Preferences → Screen Configuration**.

## Turning it off

Use **Settings → System → Shut down** in the app (or press the Pi 5's power button **twice**). Unplug once the screen
is dark and only the red light is on. Plug it back in to start; everything comes back on its own.

## 7. Photo booth & printer

1. With the printer plugged in and on, open `http://localhost:631` on the Pi → *Administration → Add Printer*.
   Pick the printer, then the Gutenprint driver for your model with 4x6 / postcard paper.
2. Run `lpstat -p` to see the printer's name and put it in `photobooth.printer` (or leave it empty to use the default printer).
3. Test it: hold up ✌️, or run `curl -X POST localhost:8080/api/action/photo_booth`.

Layouts (`photobooth.layout`):

- **`strip`**: four photos stacked, printed twice side by side on 4x6 paper. Cut down the middle for two classic 2x6 strips.
- **`grid`**: 2x2 on landscape 4x6.
- **`single`**: one big photo.

Every session is saved in `photos/<date-time>/`: each shot plus `print.jpg`. The live preview acts like a mirror
(`mirror_preview: true`, which feels natural), while prints come out the right way round (`mirror_photos: false`).

## 8. Other settings worth knowing

All of these are in `config.yaml`; the comments in `config.example.yaml` explain every option.

| Setting | What it does |
|---|---|
| `display.greeting` | e.g. `"Good {part_of_day}, Connors family!"` |
| `display.messages` | Rotating one-liners (reminders, quotes, Wi-Fi password for guests…) |
| `display.clock_24h`, `show_seconds` | Clock style |
| `display.weather` | Your latitude/longitude and °F/°C |
| `news.feeds` | Any RSS feed: news, sports, a blog. Delete them all to hide the ticker. |
| `display.sleep_after_minutes` | Go dark after this long without gestures (0 = never) |
| `display.always_on_hours` | e.g. `"07:00-22:00"`: never sleeps during the day |
| `display.screen_off_command` / `screen_on_command` | Set them to `deploy/screen.sh off` / `deploy/screen.sh on` to actually power the monitor off while asleep |
| `slideshow.seconds_per_photo` | How long each background photo shows |
| `slideshow.size` | Set to your monitor's resolution (e.g. `[2560, 1440]`) |

## Phone app

The **Smart Screen** Android app controls the screen from your phone over your home Wi-Fi.

| Tab | What you can do |
|---|---|
| 🏠 **Home** | Live preview of the screen. Start the photo booth, lights on/off, wake/sleep. Pi temperature, free space, version. |
| 🎨 **Look** | Background (photos or black), which side the clock and calendar are on, text size, highlight color, box darkness, photo dimming, which parts to show, clock style, slideshow speed. **Changes appear on the screen instantly.** |
| 📅 **Content** | Greeting, rotating messages, calendars (add/remove/recolor), weather location (search by city), news feeds. |
| 🖼️ **Photos** | Add slideshow photos straight from your phone's gallery, delete them. Browse photo booth sessions: view, **print again**, save to your phone, delete. |
| ⚙️ **Settings** | Which gesture does what, photo booth options, sleep times, lights setup, paired phones, check for updates, restart. |

### Install it

1. On your Android phone, open **https://github.com/Kevinjconnorsjr/Smart-screen/releases/tag/android-app**
2. Tap **SmartScreen.apk** to download it, then open it.
3. Android asks to allow installing apps from your browser: tap **Settings**, turn on **Allow from this source**, go back and tap **Install**.
   (The app isn't on the Play Store, so Android asks this once.)

### Connect it (once)

1. Open the app. It searches your Wi-Fi and lists **Smart Screen (smartscreen)**. Tap it.
   If nothing shows up, type the Pi's address (e.g. `smartscreen.local` or `192.168.1.20`) and tap **Connect**.
2. Tap **Show code on the screen**. A 6-digit code appears on the TV for 2 minutes.
3. Type it in the app. Done: the phone stays connected from then on.

Anyone else in the house can pair their phone the same way. Remove phones under **Settings → Paired phones**.

### How it works (and why it's safe)

- The app's screens are a web page served by the Pi (`http://<pi>:8080/remote`), so **the app updates itself
  whenever the Pi updates from GitHub**. You only reinstall the app itself for rare changes to the app shell.
- Only **paired** phones can do anything. A phone gets a long random key only after someone types the code that
  was shown on the TV; 5 wrong tries cancel the code. The Pi stores only a hash of each key
  (`.secrets/remote_devices.json`; delete that file to unpair everyone).
- The Pi's own browser never needs pairing, and nothing is reachable from outside your home network.
- A few settings can only be changed on the Pi itself, because they run commands or point at folders:
  screen on/off commands, folders, the server port, and the camera type. The app shows them with a 🔒.
- Secrets (the Home Assistant token, the Hue key) are never sent to the phone. The app shows dots,
  and leaving them alone keeps the saved value.
- Settings saved from the app go into `config.yaml` with your comments kept. You can still edit that file by hand.
- Turn the remote off completely with `remote: enabled: false` in `config.yaml`.

### Updating the app without uninstalling (optional)

Android only installs a new version over an old one if both were signed with the same key. GitHub builds the app,
and without a key of your own each build gets a new one, so you'd have to uninstall before installing a newer version.
To avoid that, create a key once on any computer with Java:

```bash
keytool -genkeypair -v -keystore smart-screen.jks -alias smartscreen -keyalg RSA -keysize 2048 -validity 10000 -storetype PKCS12
base64 -w0 smart-screen.jks > smart-screen.jks.b64     # on a Mac: base64 -i smart-screen.jks -o smart-screen.jks.b64
```

Then on GitHub: the repo → **Settings → Secrets and variables → Actions → New repository secret**, and add
`SMART_SCREEN_KEYSTORE_B64` (the contents of the `.b64` file) and `SMART_SCREEN_KEYSTORE_PASSWORD` (the password you chose).
Keep `smart-screen.jks` somewhere safe, and never commit it.

### Building the app yourself

The `android/` folder is a normal Android Studio project: open it, or run `./gradlew assembleDebug` inside it.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| Black screen / page doesn't open on boot | Run `journalctl -u smart-screen -f` to see errors. Open `http://localhost:8080` in Chromium to test. |
| Gestures trigger by accident | Raise `hold_seconds` (e.g. 1.2) or `min_score` (e.g. 0.75). |
| Gestures don't trigger | Improve the lighting, keep your hand 0.5–2 m from the camera with the palm facing it, or lower `min_score` to 0.55. |
| "Could not open the camera" | For a USB webcam, try `camera.index: 1`. For a Pi Camera Module, set `camera.backend: picamera2` and check it with `rpicam-hello`. |
| Calendar empty | Open the iCal link in a browser: it should download a `.ics` file. Check the log for "Calendar … could not be loaded". |
| Weather missing | Check `latitude`/`longitude` and that the Pi has internet. |
| Nothing prints | Run `lpstat -t` and check `http://localhost:631`. The screen says "Saved (printing failed)" and the log shows the error. |
| App can't find the screen | Type the Pi's IP address instead (shown in your router's app or admin page, or run `hostname -I` on the Pi). The phone must be on the same Wi-Fi, not a guest network. |
| App says the code didn't match | Codes last 2 minutes and allow 5 tries. Tap **Show a new code**. |
| Camera feels slow | Make sure you're on the latest version (it asks the webcam for its fast MJPEG mode; the log shows `Camera: 1920x1080 @ 30 fps (MJPG)`). Lower `camera.width/height` to 1280x720 for more speed. |
| Update skipped | Run `journalctl -u smart-screen-update`. Usually a file was edited on the Pi: `git status` shows which. Undo with `git checkout -- FILE`. |
| Display looks too small/big | Use Chromium zoom: add `--force-device-scale-factor=1.25` to the kiosk command (`~/.config/labwc/autostart`). |

## 10. Adding features

- **New action:** add a method to `Mirror` in `mirror/main.py`, register it in `self.actions`, and bind a gesture to it
  in `config.yaml`. Examples: start music, trigger a Home Assistant scene, or start a timer.
- **New gestures** beyond the built-in 7: train a custom model with
  [MediaPipe Model Maker](https://ai.google.dev/edge/mediapipe/solutions/customization/gesture_recognizer)
  and point `gestures.model_path` at it.
- **Try it on a laptop** without a Pi:
  ```bash
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
  curl -L -o models/gesture_recognizer.task https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task
  .venv/bin/python -m mirror.main            # open http://localhost:8080 (add --no-camera for screen only)
  .venv/bin/python -m pytest tests           # run the tests
  ```

## Mirror build (optional)

For a real smart mirror, put a two-way mirror in front of the monitor and set `display.style: mirror`.
Black pixels then reflect like a mirror, and the white text shows through. Mount the camera *above* the glass:
a two-way mirror blocks about 70% of the light, which is too dark for good photos.

## How it's built

```
camera ─► mirror/camera.py ─┬─► gestures.py (MediaPipe, hold-to-fire) ─► main.py: gesture → action
                            │                                              ├─ lights.py (Home Assistant / Hue / webhook)
                            │                                              ├─ power.py (screen sleep / wake)
                            └─► live preview + full-res shots ◄────────────└─ photobooth.py (countdown → 4x6 → `lp`)

web.py ─► Server-Sent Events + JSON ─► static/ page in Chromium kiosk mode
  ├─ calendar_feed.py   iCal feeds, repeating events expanded
  ├─ backgrounds.py     photo folders → screen-sized cached JPEGs
  ├─ news.py            RSS headlines
  └─ remote.py          phone app: pairing, settings (settings_store.py), photos, booth gallery
android/ ─► phone app shell: finds the Pi (mDNS), shows /remote, gallery picker, downloads
deploy/update.sh + systemd timer ─► git fast-forward from GitHub → restart → page reloads itself
```
