# Home server on a Raspberry Pi 3B

A 2016 Raspberry Pi 3B with **905 MB of usable RAM**, running DNS-level ad blocking, Zigbee
thermometers, measurement history, a dashboard, alerts, voice endpoints and remote access.

RAM is the constraint behind every decision below. With everything running, **~460 MB stay
available**.

## Services

| Service | Type | Role | RAM |
|---|---|---|---|
| 🛡️ **Pi-hole v6** | systemd | DNS filtering, 24 lists, ~711k domains | 22 MB |
| 📡 **Zigbee2MQTT** | Docker | Zigbee coordinator → MQTT, 4 sensors | 113 MB |
| 📨 **Mosquitto** | Docker | MQTT broker between Zigbee2MQTT and Node-RED | 1 MB |
| 🔀 **Node-RED** | Docker | History, dashboard, alerts, voice endpoints, weather | 131 MB |
| 🌐 **nginx** | systemd | Reverse proxy, every service behind one port | 20 MB |
| 🔐 **Tailscale** | systemd | Remote access with no inbound port opened | 66 MB |
| 🐳 **Docker** | systemd | Container runtime (`dockerd` + `containerd`) | 114 MB |
| 💾 **log2ram** | systemd | Keeps `/var/log` in RAM to spare the SD card | negligible |
| | | **Total** | **467 MB** |

RAM is process RSS, summed per service. The column totals 467 MB while `free` reports
441 MB actually in use: shared pages get counted twice, so read it as a ranking rather
than exact numbers. `docker stats` reports zero on this kernel, which was booted without
`cgroup_memory=1`.

---

## 🛡️ Pi-hole

Runs on the host rather than in Docker: it needs port 53 and the container adds a network
layer for no benefit. Web UI is proxied under `/pihole`.

Worth knowing: bare `/pihole/` returns 403, since there is no index. The real entry points are
`/pihole` (301) and `/pihole/admin/` (302).

## 📡 Zigbee2MQTT

Sonoff ZBDongle-E on `/dev/ttyUSB0`, `ember` adapter, channel 20. Four Sonoff sensors
(SNZB-02D, SNZB-02DR2, SNZB-02WD), all battery-powered end devices talking directly to the
coordinator. There is no router in the mesh.

`last_seen: ISO_8601` is enabled, and it matters more than it looks: see the Node-RED
notes below.

`availability` is on with `passive.timeout: 90` minutes. The 1500-minute default (25 h) is
useless here: the slowest sensor reports every ~9 minutes, so 90 minutes of silence is
unambiguously a fault.

Devices publish with `retain: true`, so the dashboard shows a value immediately on load
instead of waiting for the next report.

## 📨 Mosquitto

Deliberately minimal: an anonymous listener on 1883 for the LAN, plus on-disk persistence
(`persistence true`, `persistence_location /mosquitto/data/`). That second line is the one
that matters. The dashboard and the voice endpoints read the last *retained* value when they
connect, so without persistence a broker restart would blank both until the next sensor
report — and an SNZB-02D only reports on threshold crossings, which can be an hour away.

## 🔀 Node-RED

The one component doing actual work. Four things are worth extracting:

**SQLite with no dependency and no compilation.** History uses **`node:sqlite`, built into
Node.js 24**, exposed to function nodes through `functionGlobalContext` in `settings.js`:

```js
functionGlobalContext: { sqlite: require('node:sqlite') }
```

Then in a node: `const { DatabaseSync } = global.get('sqlite')`. No npm package, no extra
RAM, and crucially **no node-gyp**. Building `node-red-node-sqlite` on a Cortex-A53 takes
over half an hour.

**Timestamp with the sensor's clock, not arrival time.** MQTT replays retained messages on
every reconnect, so `Date.now()` fabricates **a fake measurement on every restart**. Use
Zigbee2MQTT's `last_seen` as the key:

```sql
mesures(ts INTEGER, capteur TEXT, temp REAL, hum REAL, batterie INTEGER,
        PRIMARY KEY (ts, capteur))
```

Inserts become genuinely idempotent.

**Zigbee2MQTT publishes in bursts.** A sensor emits **one message per Zigbee attribute
received**, each with a `last_seen` distinct to the millisecond, so the primary key alone
is not enough. Discard a measurement identical to the previous one and less than a minute
old, or you get 9 rows for 2 real readings.

**Reloading a Dashboard 2.0 chart from the database.** To replay history with its **real
timestamps** in a `ui-chart`, all three properties must be typed `property`. Keys are then
read from each point of the array rather than from the message:

```
category: "capteur"   categoryType: "property"
xAxisProperty: "ts"   xAxisPropertyType: "property"
yAxisProperty: "temp" yAxisPropertyType: "property"
```

The default `timestamp` type uses arrival time, which collapses the whole history onto the
moment of startup. Send an array with `msg.action = "replace"`.

### Push notifications

Alerts go to a phone through [ntfy.sh](https://ntfy.sh): no account, no app registration,
one HTTP POST. The topic name is the only secret, so it lives in `NTFY_URL` and never in
the flow. Two alerts are wired:

| Trigger | Message |
|---|---|
| Bedroom above 30 C | `Chambre: 30.4 C (seuil 30 C depasse)`, high priority |
| Outside cooler than inside | window advice, on request |

The threshold alert uses **hysteresis**, and it is the whole trick. A naive `temp > 30`
fires on every message the sensor sends, which is one every few minutes all afternoon. A
flag in flow context makes it fire once on the way up and once on the way back down:

```js
var was = flow.get('over30') || false;
if (temp > 30 && !was) { flow.set('over30', true);  /* notify */ }
else if (temp <= 30 && was) { flow.set('over30', false); /* notify back to normal */ }
```

`pi-backup` uses the same channel to report its own failures, reading the topic from the
macOS keychain rather than a file.

### Weather

[Open-Meteo](https://open-meteo.com): free, no API key, no registration, and over France
it is backed by Météo-France's AROME model. One HTTP request every 15 minutes feeds two
cards, current conditions and a 7-day forecast, from a single WMO 4677 code table. The API
also serves 15-minute resolution and up to 16 days.

### Philips Hue

The bridge is queried with a plain `http request` rather than a dedicated library, one
less dependency on a 905 MB machine. CLIP v2 requires HTTPS with a self-signed
certificate: a `tls` config node with `verifyservercert: false` is enough, without touching
`NODE_TLS_REJECT_UNAUTHORIZED` globally. `/clip/v2/resource` with no type returns the whole
bridge in one call.

### Voice endpoints

`http in` nodes return ready-to-speak sentences for an iOS Shortcut (*Get Contents of URL*
+ *Speak Text*):

| Endpoint | Response |
|---|---|
| `/thermo/resume` | temperature, 24 h min/max, comparison with yesterday |
| `/fenetre` | *"It's 23.2 degrees outside, 4.1 cooler than the bedroom. You can open up."* |
| `/hue/etat` | which rooms are lit |

Detail that matters: don't start the Shortcut's name with "Temperature", or Siri answers
with the weather forecast instead.

## 🌐 nginx

Single entry point, everything under one port. Config lives in `/etc/nginx` and is the only
part of the stack needing `sudo`.

Deployment goes through a wrapper (`pi-root/pigervais-nginx-apply`, root-owned, 755)
allowed password-free via `sudoers.d`. **That one command only**, no arguments accepted,
paths hard-coded, so it grants no arbitrary write. It backs up the current config, runs
`nginx -t`, and reloads only if valid; otherwise it restores the previous file and exits 1.
nginx is never left down.

## 🔐 Tailscale

Remote access without opening a single port on the router. Same tooling works from outside
the LAN with `PI_HOST=pigervais-ts`.

---

## Tooling (`bin/`)

The Pi's config is **mirrored on the workstation** and version-controlled. Edit locally
with real tools, then push.

| Command | Role |
|---|---|
| `pi-status` | health: power/throttling, RAM, services, containers, endpoints |
| `pi-pull` | Pi → local (never writes to the Pi) |
| `pi-diff` | compare, read-only on both sides |
| `pi-push` | local → Pi, reloading only the affected services |
| `pi-backup` | SQL dump of measurements, secrets encrypted with `age` |
| `pi-compare` | compare sensors on **simultaneous** readings |
| `pi-publish` | generate this public repo from the private mirror |

File mapping lives in `bin/_manifest.sh`: one line per file, with the service to reload.
Adding a file to the mirror is adding a line.

Two implementation notes:

- Transfers use `ssh 'cat …'`, not rsync. macOS 26 ships openrsync (protocol 29) while the
  Pi runs rsync 3.4.1; reconciling them isn't worth it for ~15 small files, and SSH
  connection multiplexing makes it instant anyway.
- `pi-compare` exists because comparing the *latest values* of several sensors is
  meaningless when they only publish on threshold crossings. Two displayed values can be
  fifteen minutes apart. It only compares readings falling inside the same ten-minute
  window, and separates a calibration offset (low standard deviation, fixable via
  `temperature_calibration`) from a genuine difference in conditions.

`nodered/flows.json` is also written by the web editor. Always `pi-pull` before touching
it, or `pi-push` will overwrite work done in the browser.

## Getting started

The `zigbee/` directory is laid out exactly as Docker expects it, so the stack runs
straight from a clone. Everything below happens inside `zigbee/` — the Compose file lives
there and its volume paths are relative to it.

```sh
git clone <this-repo> && cd pi-domotique/zigbee
cp zigbee2mqtt/data/configuration.exemple.yaml zigbee2mqtt/data/configuration.yaml
```

Point `serial.port` in that file at your own coordinator. The stable path is under
`/dev/serial/by-id/`, never `/dev/ttyUSB0`, which moves between boots:

```sh
ls -l /dev/serial/by-id/
```

Create `.env` next to `docker-compose.yml`:

```sh
HUE_BRIDGE=<hue-bridge-ip>
HUE_KEY=<key-obtained-by-pressing-the-bridge-button>
NTFY_URL=https://ntfy.sh/<your-private-channel>
```

Then:

```sh
docker compose up -d
docker exec -w /data nodered npm install   # dashboard package from package.json
docker compose restart nodered
```

`network_key: GENERATE` makes Zigbee2MQTT generate its own key on first boot. The dashboard
lands on `http://<host>:1880/nodered/dashboard/home`, the Zigbee2MQTT frontend on `:8080`.
Pair your own sensors from that frontend, then rename them to match the flow: it keys cards
and history on the sensor name, and the spoken endpoints expect `Chambre`.

The published `flows.json` is the real one, so it references sensor names and a Hue bridge
that are not yours. Expect the dashboard cards to stay empty until your own devices publish
under those names.

Adapt `bin/_manifest.sh` (SSH host, paths) and the nginx config to your setup.

## What is not here

No secrets, no personal data. A home's temperature log tells you when it is occupied.
Everything sensitive lives in a separate private repo, secrets encrypted with `age`:
asymmetric, so the scheduled backup runs without any private key sitting on the machine.

## Why not Home Assistant

Considered, rejected: roughly 400-500 MB in a container, which does not fit alongside the
rest on a 3B. Node-RED covers the need (charts, alerts, voice endpoints) for a fraction
of the memory. On a Pi 4 or 5 the trade-off would go the other way.
