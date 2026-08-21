import json, urllib.request

BASE = "http://localhost:1880/nodered"
TAB = "f_hue"

# ---------- code des function nodes ----------
NORM = ('const norm = s => (s||"").normalize("NFD").replace(/[\\u0300-\\u036f]/g,"")'
        '.toLowerCase().replace(/[^a-z0-9]/g,"");\n')

FN_INV_REQ = '''msg.method = "GET";
msg.url = "https://" + env.get("HUE_BRIDGE") + "/clip/v2/resource";
msg.headers = { "hue-application-key": env.get("HUE_KEY") };
return msg;'''

FN_INV_STORE = NORM + '''const data = ((msg.payload || {}).data) || [];
const by = t => data.filter(o => o.type === t);
const devices = {}; by("device").forEach(d => { devices[d.id] = d; });
const lights = {};  by("light").forEach(l => { lights[l.id] = l; });
const rooms = [];
by("room").concat(by("zone")).forEach(r => {
    const gl = (r.services || []).find(s => s.rtype === "grouped_light");
    const lids = [];
    (r.children || []).forEach(c => {
        const d = devices[c.rid];
        if (d) (d.services || []).forEach(s => { if (s.rtype === "light") lids.push(s.rid); });
        if (lights[c.rid]) lids.push(c.rid);
    });
    const nm = (r.metadata || {}).name || "?";
    rooms.push({ name: nm, norm: norm(nm), id: r.id, gl: gl ? gl.rid : null, lights: lids });
});
const scenes = by("scene").map(s => {
    const nm = (s.metadata || {}).name || "?";
    return { name: nm, norm: norm(nm), id: s.id, room: s.group.rid };
});
global.set("hue", { rooms: rooms, scenes: scenes, nbLights: Object.keys(lights).length, ts: Date.now() });
node.status({ fill: "green", shape: "dot", text: rooms.length + " pieces / " + scenes.length + " scenes" });
return null;'''

FN_ROUTER = NORM + '''const inv = global.get("hue");
const txt = t => ({ statusCode: msg.statusCode, headers: { "Content-Type": "text/plain; charset=utf-8" }, payload: t });
if (!inv) {
    msg.statusCode = 503; msg.headers = { "Content-Type": "text/plain; charset=utf-8" };
    msg.payload = "Inventaire Hue pas encore chargé, réessaie dans quelques secondes.";
    return [null, msg];
}
const p = (msg.req && msg.req.params) || {};
const cible = p.cible !== undefined ? norm(p.cible) : null;
let room = null;
if (cible) {
    room = inv.rooms.find(r => r.norm === cible)
        || inv.rooms.find(r => r.norm.indexOf(cible) !== -1)
        || inv.rooms.find(r => cible.indexOf(r.norm) !== -1);
}
let intent;
if (p.nom !== undefined)      intent = { kind: "scene", scene: norm(p.nom) };
else if (cible === null)      intent = { kind: "etat_global" };
else                          intent = { kind: "action", action: norm(p.action) };
if (intent.kind !== "etat_global" && !room) {
    msg.statusCode = 404; msg.headers = { "Content-Type": "text/plain; charset=utf-8" };
    msg.payload = "Pièce inconnue : " + p.cible + ". Connues : " + inv.rooms.map(r => r.name).join(", ") + ".";
    return [null, msg];
}
msg.hue = { intent: intent, room: room };
msg.method = "GET";
msg.headers = { "hue-application-key": env.get("HUE_KEY") };
msg.url = "https://" + env.get("HUE_BRIDGE") + "/clip/v2/resource/grouped_light";
return [msg, null];'''

FN_DECIDE = '''const br = env.get("HUE_BRIDGE"), key = env.get("HUE_KEY");
const inv = global.get("hue") || { rooms: [], scenes: [] };
const glAll = {};
(((msg.payload || {}).data) || []).forEach(g => { glAll[g.id] = g; });
const pct = g => Math.round(((g.dimming || {}).brightness) || 0);
const plain = () => { msg.headers = { "Content-Type": "text/plain; charset=utf-8" }; };
const h = msg.hue, room = h.room;

if (h.intent.kind === "etat_global") {
    const parts = inv.rooms.map(r => {
        const g = glAll[r.gl];
        if (!g) return r.name + " : état inconnu";
        return g.on.on ? r.name + " allumé à " + pct(g) + " pour cent" : r.name + " éteint";
    });
    plain(); msg.payload = parts.join(", ") + ".";
    return [null, msg];
}

const g = glAll[room.gl];

if (h.intent.kind === "scene") {
    const dispo = inv.scenes.filter(s => s.room === room.id);
    const sc = dispo.find(s => s.norm === h.intent.scene)
            || dispo.find(s => s.norm.indexOf(h.intent.scene) !== -1);
    if (!sc) {
        plain(); msg.statusCode = 404;
        msg.payload = "Scène inconnue pour " + room.name + ". Disponibles : " + dispo.map(s => s.name).join(", ") + ".";
        return [null, msg];
    }
    msg.method = "PUT";
    msg.url = "https://" + br + "/clip/v2/resource/scene/" + sc.id;
    msg.headers = { "hue-application-key": key, "Content-Type": "application/json" };
    msg.payload = { recall: { action: "active" } };
    msg.hue.done = "Scène " + sc.name + " activée dans " + room.name + ".";
    return [msg, null];
}

const a = h.intent.action;
let body = null, done = null;
if (a === "on" || a === "allume" || a === "allumer") { body = { on: { on: true } }; done = room.name + " allumé."; }
else if (a === "off" || a === "eteint" || a === "eteins" || a === "eteindre") { body = { on: { on: false } }; done = room.name + " éteint."; }
else if (a === "toggle" || a === "bascule") {
    const now = !!(g && g.on.on);
    body = { on: { on: !now } };
    done = room.name + (now ? " éteint." : " allumé.");
}
else if (a === "etat") {
    plain();
    msg.payload = g ? (g.on.on ? room.name + " allumé à " + pct(g) + " pour cent." : room.name + " éteint.") : "État inconnu.";
    return [null, msg];
}
else if (/^[0-9]{1,3}$/.test(a)) {
    const v = Math.min(100, Math.max(0, parseInt(a, 10)));
    body = v === 0 ? { on: { on: false } } : { on: { on: true }, dimming: { brightness: v } };
    done = v === 0 ? room.name + " éteint." : room.name + " à " + v + " pour cent.";
}
else {
    plain(); msg.statusCode = 400;
    msg.payload = "Action inconnue : " + a + ". Utilise on, off, toggle, etat ou un nombre de 0 à 100.";
    return [null, msg];
}
msg.method = "PUT";
msg.url = "https://" + br + "/clip/v2/resource/grouped_light/" + room.gl;
msg.headers = { "hue-application-key": key, "Content-Type": "application/json" };
msg.payload = body;
msg.hue.done = done;
return [msg, null];'''

FN_REPLY = '''const errs = ((msg.payload || {}).errors) || [];
msg.headers = { "Content-Type": "text/plain; charset=utf-8" };
if (errs.length) {
    msg.statusCode = 502;
    msg.payload = "Le pont a refusé : " + errs.map(e => e.description).join(" ; ");
} else {
    msg.statusCode = 200;
    msg.payload = (msg.hue && msg.hue.done) || "OK.";
}
return msg;'''

FN_ERR = '''if (!msg.req) { node.warn("Hue (hors requête HTTP) : " + ((msg.error || {}).message || "erreur")); return null; }
msg.statusCode = 502;
msg.headers = { "Content-Type": "text/plain; charset=utf-8" };
msg.payload = "Le pont Hue ne répond pas (" + ((msg.error || {}).message || "erreur inconnue") + ").";
return msg;'''

def fn(nid, name, code, outputs, wires, x, y):
    return {"id": nid, "type": "function", "z": TAB, "name": name, "func": code,
            "outputs": outputs, "timeout": 0, "noerr": 0, "initialize": "", "finalize": "",
            "libs": [], "x": x, "y": y, "wires": wires}

def httpreq(nid, name, method, wires, x, y):
    return {"id": nid, "type": "http request", "z": TAB, "name": name, "method": method,
            "ret": "obj", "paytoqs": "ignore", "url": "", "tls": "tls_hue", "persist": False,
            "proxy": "", "insecureHTTPParser": False, "authType": "", "senderr": False,
            "headers": [], "credentials": {"user": "", "password": ""}, "x": x, "y": y, "wires": wires}

nodes = [
    {"id": TAB, "type": "tab", "label": "Hue", "disabled": False, "info":
     "Pont Philips Hue via l'API CLIP v2 (HTTPS, certificat auto-signé -> tls-config sans vérification).\n"
     "Clé d'application dans la variable d'env HUE_KEY du conteneur (fichier zigbee/.env), pas dans ce flow.\n\n"
     "Endpoints (prefixes par /nodered) :\n"
     "  GET /hue/etat                  -> état de toutes les pièces, en français\n"
     "  GET /hue/<pièce>/<action>      -> on | off | toggle | etat | 0-100 (luminosité)\n"
     "  GET /hue/<pièce>/scene/<nom>   -> active une scène de la pièce\n"
     "Sans authentification : accessible LAN + Tailscale uniquement.", "env": []},

    {"id": "tls_hue", "type": "tls-config", "name": "Pont Hue (certificat auto-signé)", "cert": "", "key": "",
     "ca": "", "certname": "", "keyname": "", "caname": "", "servername": "",
     "verifyservercert": False, "alpnprotocol": ""},

    # --- inventaire ---
    {"id": "inj_hue_inv", "type": "inject", "z": TAB, "name": "au demarrage + toutes les 15 min",
     "props": [{"p": "payload"}], "repeat": "900", "crontab": "", "once": True, "onceDelay": "8",
     "topic": "", "payload": "", "payloadType": "date", "x": 200, "y": 80, "wires": [["fn_hue_inv_req"]]},
    fn("fn_hue_inv_req", "requête inventaire", FN_INV_REQ, 1, [["req_hue_inv"]], 460, 80),
    httpreq("req_hue_inv", "GET /clip/v2/resource", "GET", [["fn_hue_inv_store"]], 680, 80),
    fn("fn_hue_inv_store", "stocke global.hue", FN_INV_STORE, 1, [[]], 900, 80),

    # --- commandes ---
    {"id": "in_hue_etat", "type": "http in", "z": TAB, "name": "GET /hue/etat", "url": "/hue/etat",
     "method": "get", "upload": False, "swaggerDoc": "", "x": 180, "y": 200, "wires": [["fn_hue_router"]]},
    {"id": "in_hue_action", "type": "http in", "z": TAB, "name": "GET /hue/:cible/:action",
     "url": "/hue/:cible/:action", "method": "get", "upload": False, "swaggerDoc": "",
     "x": 200, "y": 250, "wires": [["fn_hue_router"]]},
    {"id": "in_hue_scene", "type": "http in", "z": TAB, "name": "GET /hue/:cible/scene/:nom",
     "url": "/hue/:cible/scene/:nom", "method": "get", "upload": False, "swaggerDoc": "",
     "x": 210, "y": 300, "wires": [["fn_hue_router"]]},

    fn("fn_hue_router", "résout la pièce", FN_ROUTER, 2, [["req_hue_get"], ["resp_hue"]], 470, 250),
    httpreq("req_hue_get", "GET grouped_light", "GET", [["fn_hue_decide"]], 680, 250),
    fn("fn_hue_decide", "décide l'action", FN_DECIDE, 2, [["req_hue_put"], ["resp_hue"]], 880, 250),
    httpreq("req_hue_put", "PUT vers le pont", "PUT", [["fn_hue_reply"]], 1080, 250),
    fn("fn_hue_reply", "réponse", FN_REPLY, 1, [["resp_hue"]], 1270, 250),
    {"id": "resp_hue", "type": "http response", "z": TAB, "name": "réponse HTTP",
     "statusCode": "", "headers": {}, "x": 1450, "y": 250, "wires": []},

    # --- erreurs ---
    {"id": "catch_hue", "type": "catch", "z": TAB, "name": "erreurs du flow", "scope": None,
     "uncaught": False, "x": 470, "y": 380, "wires": [["fn_hue_err"]]},
    fn("fn_hue_err", "message d'erreur", FN_ERR, 1, [["resp_hue"]], 700, 380),
]

# ---------- merge avec les flows existants ----------
req = urllib.request.Request(BASE + "/flows", headers={"Node-RED-API-Version": "v2"})
cur = json.loads(urllib.request.urlopen(req, timeout=20).read())
rev, existing = cur["rev"], cur["flows"]
new_ids = {n["id"] for n in nodes}
kept = [n for n in existing if n.get("z") != TAB and n.get("id") not in new_ids]
print(f"flows existants : {len(existing)} nodes -> conserves : {len(kept)}, ajoutes : {len(nodes)}")

body = json.dumps({"rev": rev, "flows": kept + nodes}).encode()
post = urllib.request.Request(BASE + "/flows", data=body, method="POST", headers={
    "Content-Type": "application/json",
    "Node-RED-API-Version": "v2",
    "Node-RED-Deployment-Type": "full",
})
print("POST /flows ->", json.loads(urllib.request.urlopen(post, timeout=60).read()))
