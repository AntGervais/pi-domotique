#!/usr/bin/env bash
# Carte du miroir : chemin_local | chemin_sur_le_Pi | service à recharger
# Services connus : compose, mosquitto, zigbee2mqtt, nodered, nginx, cloudflared, none
PI_HOST="${PI_HOST:-pigervais}"
PI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MAP=(
  "zigbee/docker-compose.yml|/home/antoine/zigbee/docker-compose.yml|compose"
  "zigbee/mosquitto.conf|/home/antoine/zigbee/mosquitto/config/mosquitto.conf|mosquitto"
  "zigbee2mqtt/configuration.yaml|/home/antoine/zigbee/zigbee2mqtt/data/configuration.yaml|zigbee2mqtt"
  "nodered/settings.js|/home/antoine/zigbee/nodered/data/settings.js|nodered"
  "nodered/flows.json|/home/antoine/zigbee/nodered/data/flows.json|nodered"
  "nodered/package.json|/home/antoine/zigbee/nodered/data/package.json|nodered"
  "nginx/pigervais-proxy.conf|/etc/nginx/sites-available/pigervais-proxy.conf|nginx"
  "cloudflared/config.yml|/etc/cloudflared/config.yml|cloudflared"
  "scripts/hue-flow-complet.py|/home/antoine/hue-flow-complet.py|none"
  "scripts/hue-flow-etat.py|/home/antoine/hue-flow-etat.py|none"
)

m_local()   { echo "${1%%|*}"; }
m_remote()  { local r="${1#*|}"; echo "${r%%|*}"; }
m_service() { echo "${1##*|}"; }

die() { echo "✗ $*" >&2; exit 1; }

pi_check() {
  ssh -o BatchMode=yes "$PI_HOST" true 2>/dev/null \
    || die "Pi injoignable ($PI_HOST). Hors du LAN ? essaie : PI_HOST=pigervais-ts $0"
}
