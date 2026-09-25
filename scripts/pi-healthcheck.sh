#!/usr/bin/env bash
# Bilan de sante de la stack pigervais. Silence si tout va bien, alerte ntfy
# sinon — sinon une panne comme celle du 3 septembre (conteneur zigbee2mqtt
# plante, personne prevenu, 2 semaines sans capteurs) repasse inapercue.
# Prevu pour tourner sur le Pi lui-meme (cron), pas le Mac : le NTFY_URL est
# deja dans ~/zigbee/.env, aucun secret supplementaire requis.
#
# Auto-guerison (ajoute le 22 septembre 2026, 2e occurrence du meme crash
# USB) : un conteneur arrete est relance tout seul plutot que juste signale,
# pour ne pas dependre d'une intervention manuelle entre deux passages du
# cron. L'alerte precise si ca a marche ou pas.
#
# Anti-spam (24 septembre 2026) : passe a l'heure, ce script sans garde-fou
# renvoyait la MEME alerte a chaque passage tant que le probleme n'etait pas
# resolu. Notification uniquement au CHANGEMENT d'etat (meme principe que
# l'alerte 30 C / fenetre dans Node-RED), pas a chaque execution.
set -uo pipefail

ENV_FILE=/home/antoine/zigbee/.env
[ -r "$ENV_FILE" ] && source "$ENV_FILE"
ETAT_FILE=/home/antoine/.pigervais-healthcheck-signature

PROBLEMES=()

# Format lisible "Xj Yh" (ou "Yh" sous 1 jour) a partir d'une duree en minutes.
duree_lisible() {
  local min=$1 jours heures
  jours=$(( min / 1440 ))
  heures=$(( (min % 1440) / 60 ))
  if [ "$jours" -gt 0 ]; then
    echo "${jours}j ${heures}h"
  else
    echo "${heures}h"
  fi
}

# 1. Conteneurs Docker : tous doivent etre "running" (pas juste "existants").
# Un conteneur arrete (pas "absent", qui indiquerait un vrai probleme de
# config) est relance automatiquement plutot que juste signale.
for c in mosquitto zigbee2mqtt nodered; do
  etat=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo "absent")
  if [ "$etat" = "running" ]; then
    continue
  fi
  if [ "$etat" = "exited" ]; then
    (cd /home/antoine/zigbee && docker compose up -d "$c") >/dev/null 2>&1
    sleep 10
    etat2=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo "absent")
    if [ "$etat2" = "running" ]; then
      PROBLEMES+=("conteneur $c : etait $etat, relance automatiquement avec succes")
      continue
    fi
    PROBLEMES+=("conteneur $c : etait $etat, relance automatique ECHOUEE (etat: $etat2)")
  else
    PROBLEMES+=("conteneur $c : $etat")
  fi
done

# 2. Pont Zigbee2MQTT en ligne (retained sur le broker).
etat_z2m=$(timeout 8 docker exec mosquitto mosquitto_sub -h 127.0.0.1 -t 'zigbee2mqtt/bridge/state' -C 1 -W 5 2>/dev/null || true)
case "$etat_z2m" in
  *'"online"'*) ;;
  *) PROBLEMES+=("pont Zigbee2MQTT hors ligne (dernier etat : ${etat_z2m:-aucun})") ;;
esac

# 3. Capteurs pas trop silencieux. Seuil 3h : les SNZB-02D publient au
# changement ou ~1x/h, le Linky en continu — au-dela de 3h sans nouvelle,
# c'est le meme symptome que le decrochage Bureau deja documente.
for capteur in Chambre Bureau Cuisine "Extérieur" Linky; do
  msg=$(timeout 8 docker exec mosquitto mosquitto_sub -h 127.0.0.1 -t "zigbee2mqtt/$capteur" -C 1 -W 5 2>/dev/null || true)
  ts=$(printf '%s' "$msg" | grep -o '"last_seen":"[^"]*"' | head -1 | cut -d'"' -f4)
  if [ -z "$ts" ]; then
    PROBLEMES+=("$capteur : aucune donnee recue")
  else
    epoch=$(date -d "$ts" +%s 2>/dev/null || echo 0)
    if [ "$epoch" -eq 0 ]; then
      PROBLEMES+=("$capteur : last_seen illisible ($ts)")
    else
      age_min=$(( ( $(date +%s) - epoch ) / 60 ))
      [ "$age_min" -le 180 ] || PROBLEMES+=("$capteur : muet depuis $(duree_lisible "$age_min")")
    fi
  fi
done

# 4. Sante materielle (memes seuils que bin/pi-status).
disque_pct=$(df / | awk 'NR==2{print $5}' | tr -d '%')
[ "$disque_pct" -lt 90 ] || PROBLEMES+=("disque a ${disque_pct}%")
throttle=$(vcgencmd get_throttled | cut -d= -f2)
if [ "$throttle" != "0x0" ]; then
  # Ne remonter que si le bit "now" (under-voltage/throttle EN CE MOMENT, pas
  # l'historique depuis le dernier boot) est encore la 5s plus tard : un pic
  # de courant transitoire (ex: redemarrage du dongle Zigbee) ne doit pas
  # declencher une fausse alerte.
  sleep 5
  throttle2=$(vcgencmd get_throttled | cut -d= -f2)
  if [ $(( throttle2 & 0x7 )) -ne 0 ]; then
    PROBLEMES+=("sous-tension/throttle EN COURS ($throttle2)")
  fi
fi

# 5. Dashboard joignable.
code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 http://127.0.0.1/nodered/dashboard/home)
[ "$code" = "200" ] || PROBLEMES+=("dashboard Node-RED : HTTP $code")

if [ "${#PROBLEMES[@]}" -gt 0 ]; then
  texte=$(printf '%s\n' "${PROBLEMES[@]}")
  echo "$texte"

  # Signature stable du probleme (chiffres retires : une duree "muet depuis
  # 3h" qui devient "4h" au passage suivant reste le MEME probleme, pas un
  # nouveau).
  signature=$(printf '%s\n' "${PROBLEMES[@]}" | tr -d '0-9' | sort)
  derniere_signature=$(cat "$ETAT_FILE" 2>/dev/null || echo "")
  if [ "$signature" != "$derniere_signature" ] && [ -n "${NTFY_URL:-}" ]; then
    curl -s -m 15 -H "Title: pigervais — problème détecté" -H "Tags: warning" -H "Priority: high" \
         -d "$texte" "$NTFY_URL" >/dev/null 2>&1
  fi
  echo "$signature" > "$ETAT_FILE"
  exit 1
fi

# Retour a la normale apres un probleme : une seule notification, discrete.
if [ -s "$ETAT_FILE" ] && [ -n "${NTFY_URL:-}" ]; then
  curl -s -m 15 -H "Title: pigervais — retour à la normale" -H "Tags: white_check_mark" \
       -d "Tous les indicateurs sont de nouveau au vert." "$NTFY_URL" >/dev/null 2>&1
fi
: > "$ETAT_FILE"
echo "✓ tout va bien"
