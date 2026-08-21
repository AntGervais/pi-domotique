# -*- coding: utf-8 -*-
"""Flow Hue reduit : inventaire + le seul endpoint GET /hue/etat (lecture seule)."""
import json, urllib.request

BASE = "http://localhost:1880/nodered"
TAB = "f_hue"

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
node.status({ fill: "green", shape: "dot", text: rooms.length + " pièces / " + scenes.length + " scènes" });
return null;'''

FN_ETAT_REQ = '''if (!global.get("hue")) {
    msg.statusCode = 503;
    msg.headers = { "Content-Type": "text/plain; charset=utf-8" };
    msg.payload = "Inventaire Hue pas encore chargé, réessaie dans quelques secondes.";
    return [null, msg];
}
msg.method = "GET";
msg.headers = { "hue-application-key": env.get("HUE_KEY") };
msg.url = "https://" + env.get("HUE_BRIDGE") + "/clip/v2/resource/grouped_light";
return [msg, null];'''

FN_ETAT_REPLY = '''const inv = global.get("hue") || { rooms: [] };
const glAll = {};
(((msg.payload || {}).data) || []).forEach(g => { glAll[g.id] = g; });
const pct = g => Math.round(((g.dimming || {}).brightness) || 0);
const parts = inv.rooms.map(r => {
    const g = glAll[r.gl];
    if (!g) return r.name + " : état inconnu";
    return g.on.on ? r.name + " allumé à " + pct(g) + " pour cent" : r.name + " éteint";
});
msg.statusCode = 200;
msg.headers = { "Content-Type": "text/plain; charset=utf-8" };
msg.payload = parts.length ? parts.join(", ") + "." : "Aucune pièce Hue trouvée.";
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
     "LECTURE SEULE — un seul endpoint :\n"
     "  GET /hue/etat  -> état de toutes les pièces, en français (pour Siri)\n\n"
     "Les commandes (on/off/toggle/luminosité/scènes) ont été retirées volontairement.\n"
     "Pour les remettre : python3 /home/antoine/hue-flow-complet.py sur le Pi.", "env": []},

    {"id": "tls_hue", "type": "tls-config", "name": "Pont Hue (certificat auto-signé)", "cert": "", "key": "",
     "ca": "", "certname": "", "keyname": "", "caname": "", "servername": "",
     "verifyservercert": False, "alpnprotocol": ""},

    {"id": "inj_hue_inv", "type": "inject", "z": TAB, "name": "au démarrage + toutes les 15 min",
     "props": [{"p": "payload"}], "repeat": "900", "crontab": "", "once": True, "onceDelay": "8",
     "topic": "", "payload": "", "payloadType": "date", "x": 220, "y": 80, "wires": [["fn_hue_inv_req"]]},
    fn("fn_hue_inv_req", "requête inventaire", FN_INV_REQ, 1, [["req_hue_inv"]], 480, 80),
    httpreq("req_hue_inv", "GET /clip/v2/resource", "GET", [["fn_hue_inv_store"]], 700, 80),
    fn("fn_hue_inv_store", "stocke global.hue", FN_INV_STORE, 1, [[]], 920, 80),

    {"id": "in_hue_etat", "type": "http in", "z": TAB, "name": "GET /hue/etat", "url": "/hue/etat",
     "method": "get", "upload": False, "swaggerDoc": "", "x": 200, "y": 220, "wires": [["fn_hue_etat_req"]]},
    fn("fn_hue_etat_req", "requête état", FN_ETAT_REQ, 2, [["req_hue_get"], ["resp_hue"]], 450, 220),
    httpreq("req_hue_get", "GET grouped_light", "GET", [["fn_hue_etat_reply"]], 680, 220),
    fn("fn_hue_etat_reply", "phrase en français", FN_ETAT_REPLY, 1, [["resp_hue"]], 900, 220),
    {"id": "resp_hue", "type": "http response", "z": TAB, "name": "réponse HTTP",
     "statusCode": "", "headers": {}, "x": 1120, "y": 220, "wires": []},

    {"id": "catch_hue", "type": "catch", "z": TAB, "name": "erreurs du flow", "scope": None,
     "uncaught": False, "x": 450, "y": 340, "wires": [["fn_hue_err"]]},
    fn("fn_hue_err", "message d'erreur", FN_ERR, 1, [["resp_hue"]], 680, 340),
]

req = urllib.request.Request(BASE + "/flows", headers={"Node-RED-API-Version": "v2"})
cur = json.loads(urllib.request.urlopen(req, timeout=20).read())
rev, existing = cur["rev"], cur["flows"]
new_ids = {n["id"] for n in nodes}
kept = [n for n in existing if n.get("z") != TAB and n.get("id") not in new_ids]
retires = [n for n in existing if n.get("z") == TAB or n.get("id") in new_ids]
print("nodes du tab Hue remplaces :", len(retires), "-> nouveaux :", len(nodes))
body = json.dumps({"rev": rev, "flows": kept + nodes}).encode()
post = urllib.request.Request(BASE + "/flows", data=body, method="POST", headers={
    "Content-Type": "application/json", "Node-RED-API-Version": "v2",
    "Node-RED-Deployment-Type": "full"})
print("POST /flows ->", json.loads(urllib.request.urlopen(post, timeout=60).read()))
