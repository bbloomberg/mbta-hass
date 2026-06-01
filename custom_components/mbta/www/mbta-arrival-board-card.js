/*
 * MBTA Arrival Board card
 *
 * A dependency-free Lovelace custom card that renders an MBTA stop's upcoming
 * departures as an SVG station arrival board, with a scrolling alert banner.
 *
 *   type: custom:mbta-arrival-board-card
 *   entity: sensor.park_street_next_departure
 *   alert_entity: binary_sensor.park_street_service_alert   # optional
 *   title: Park Street                                       # optional
 *   rows: 6                                                  # optional
 *   show_alerts: true                                        # optional
 *   show_clock: true                                         # optional
 */

const VIEW_W = 520;
const HEADER_H = 56;
const ROW_H = 46;
const ALERT_H = 34;
const PAD = 16;

const LINE_COLORS = {
  Red: "#DA291C",
  Mattapan: "#DA291C",
  Orange: "#ED8B00",
  Blue: "#003DA5",
  "Green-B": "#00843D",
  "Green-C": "#00843D",
  "Green-D": "#00843D",
  "Green-E": "#00843D",
  Green: "#00843D",
};

const BADGE_TEXT = {
  Red: "RL",
  Orange: "OL",
  Blue: "BL",
  Mattapan: "M",
  "Green-B": "B",
  "Green-C": "C",
  "Green-D": "D",
  "Green-E": "E",
};

let INSTANCE = 0;

// Schema for the visual (UI) editor, rendered with Home Assistant's <ha-form>.
const EDITOR_SCHEMA = [
  { name: "entity", required: true, selector: { entity: { domain: "sensor" } } },
  { name: "title", selector: { text: {} } },
  { name: "alert_entity", selector: { entity: { domain: "binary_sensor" } } },
  { name: "rows", selector: { number: { min: 1, max: 20, mode: "box" } } },
  { name: "show_alerts", selector: { boolean: {} } },
  { name: "show_clock", selector: { boolean: {} } },
];
const EDITOR_LABELS = {
  entity: "Departure sensor (required)",
  title: "Title (defaults to the stop name)",
  alert_entity: "Alert binary sensor (optional)",
  rows: "Rows to show",
  show_alerts: "Show alert banner",
  show_clock: "Show clock",
};

function escapeXml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function routeColor(d) {
  const id = d.route_id || "";
  if (LINE_COLORS[id]) return LINE_COLORS[id];
  if (id.startsWith("CR-")) return "#80276C"; // Commuter Rail purple
  if (id.startsWith("Boat-")) return "#008EAA"; // Ferry teal
  return "#FFC72C"; // Bus / silver line — MBTA yellow
}

function badgeText(d) {
  const id = d.route_id || "";
  if (BADGE_TEXT[id]) return BADGE_TEXT[id];
  if (id.startsWith("CR-")) return "CR";
  if (id.startsWith("Boat-")) return "FERRY";
  return String(d.route || id || "?").slice(0, 5);
}

function contrastText(hex) {
  const c = hex.replace("#", "");
  const r = parseInt(c.slice(0, 2), 16);
  const g = parseInt(c.slice(2, 4), 16);
  const b = parseInt(c.slice(4, 6), 16);
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance > 0.6 ? "#10131a" : "#ffffff";
}

function timeInfo(d) {
  if (d.cancelled) return { text: "CXL", color: "#ff5252" };
  if (d.status) {
    const live = /board|arriv|approach/i.test(d.status);
    return { text: String(d.status).toUpperCase(), color: live ? "#38d66b" : "#FFB000" };
  }
  if (d.minutes == null) return { text: "--", color: "#7a8290" };
  if (d.minutes <= 0) return { text: "ARR", color: "#38d66b" };
  return { text: `${d.minutes} MIN`, color: "#FFB000" };
}

class MbtaArrivalBoardCard extends HTMLElement {
  constructor() {
    super();
    this._uid = `mbta${++INSTANCE}`;
    this._config = {};
    this._hass = null;
    this._clockTimer = null;
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("You must define an 'entity' (the *_next_departure sensor).");
    }
    this._config = {
      rows: 6,
      show_alerts: true,
      show_clock: true,
      ...config,
    };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    // Refresh periodically so the clock (and any board styling) stays live.
    this._clockTimer = setInterval(() => this._render(), 30000);
    this._render();
  }

  disconnectedCallback() {
    if (this._clockTimer) clearInterval(this._clockTimer);
    this._clockTimer = null;
  }

  getCardSize() {
    return 1 + Math.min(this._config.rows || 6, 6);
  }

  _alertEntityId() {
    if (this._config.alert_entity) return this._config.alert_entity;
    // Derive from the departure sensor by convention.
    const e = this._config.entity || "";
    if (e.startsWith("sensor.") && e.endsWith("_next_departure")) {
      return "binary_sensor." + e.slice("sensor.".length).replace(/_next_departure$/, "_service_alert");
    }
    return null;
  }

  _render() {
    if (!this._hass) return;
    const cfg = this._config;
    const stateObj = this._hass.states[cfg.entity];

    const alertId = cfg.show_alerts ? this._alertEntityId() : null;
    const alertObj = alertId ? this._hass.states[alertId] : null;
    const alertActive = alertObj && alertObj.state === "on";
    const alertText = alertActive
      ? alertObj.attributes.alert_text ||
        (alertObj.attributes.headers || []).join("  •  ")
      : "";

    const title =
      cfg.title ||
      (stateObj && (stateObj.attributes.stop_name || stateObj.attributes.friendly_name)) ||
      "MBTA";

    let departures = (stateObj && stateObj.attributes.departures) || [];
    departures = departures.slice(0, cfg.rows || 6);

    const rowsH = Math.max(departures.length, 1) * ROW_H;
    const alertBarH = alertActive && alertText ? ALERT_H : 0;
    const totalH = HEADER_H + rowsH + alertBarH + PAD;

    let svg = "";
    svg += `<svg viewBox="0 0 ${VIEW_W} ${totalH}" width="100%" preserveAspectRatio="xMidYMid meet" font-family="'DejaVu Sans Mono','Roboto Mono',ui-monospace,monospace" role="img" aria-label="${escapeXml(title)} arrival board">`;

    // Board background
    svg += `<rect x="0" y="0" width="${VIEW_W}" height="${totalH}" rx="14" fill="#0b0e14" stroke="#1c2230" stroke-width="2"/>`;

    // Header bar
    svg += `<rect x="0" y="0" width="${VIEW_W}" height="${HEADER_H}" rx="14" fill="#11151f"/>`;
    svg += `<rect x="0" y="${HEADER_H - 14}" width="${VIEW_W}" height="14" fill="#11151f"/>`;
    svg += `<circle cx="${PAD + 12}" cy="${HEADER_H / 2}" r="12" fill="#FFC72C"/>`;
    svg += `<text x="${PAD + 12}" y="${HEADER_H / 2 + 5}" text-anchor="middle" font-size="13" font-weight="700" fill="#10131a">T</text>`;
    svg += `<text x="${PAD + 34}" y="${HEADER_H / 2 + 7}" font-size="20" font-weight="700" fill="#ffffff">${escapeXml(title)}</text>`;
    if (cfg.show_clock) {
      const now = new Date();
      const clock = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      svg += `<text x="${VIEW_W - PAD}" y="${HEADER_H / 2 + 7}" text-anchor="end" font-size="18" fill="#FFB000" letter-spacing="1">${escapeXml(clock)}</text>`;
    }

    // Rows
    if (!stateObj) {
      svg += this._centeredMessage(`ENTITY NOT FOUND`, HEADER_H, rowsH, "#ff5252");
    } else if (departures.length === 0) {
      svg += this._centeredMessage("NO DEPARTURES", HEADER_H, rowsH, "#7a8290");
    } else {
      departures.forEach((d, i) => {
        const y = HEADER_H + i * ROW_H;
        svg += this._row(d, y, i);
      });
    }

    // Alert banner (scrolling marquee)
    if (alertBarH) {
      svg += this._alertBanner(alertText, totalH - alertBarH);
    }

    svg += `</svg>`;

    if (!this._card) {
      this.innerHTML = `<ha-card style="overflow:hidden"><div class="mbta-board" style="padding:8px"></div></ha-card>`;
      this._card = this.querySelector(".mbta-board");
    }
    this._card.innerHTML = svg;
  }

  _centeredMessage(text, top, h, color) {
    return `<text x="${VIEW_W / 2}" y="${top + h / 2 + 8}" text-anchor="middle" font-size="22" fill="${color}" letter-spacing="2">${escapeXml(text)}</text>`;
  }

  _row(d, y, i) {
    const cy = y + ROW_H / 2;
    let out = "";
    // Zebra striping
    if (i % 2 === 1) {
      out += `<rect x="6" y="${y}" width="${VIEW_W - 12}" height="${ROW_H}" fill="#0f131c"/>`;
    }
    out += `<line x1="8" y1="${y}" x2="${VIEW_W - 8}" y2="${y}" stroke="#1c2230" stroke-width="1"/>`;

    // Route badge
    const color = routeColor(d);
    const label = badgeText(d);
    const badgeW = Math.max(40, label.length * 12 + 14);
    const badgeX = PAD;
    const badgeY = cy - 14;
    out += `<rect x="${badgeX}" y="${badgeY}" width="${badgeW}" height="28" rx="6" fill="${color}"/>`;
    out += `<text x="${badgeX + badgeW / 2}" y="${cy + 6}" text-anchor="middle" font-size="16" font-weight="700" fill="${contrastText(color)}">${escapeXml(label)}</text>`;

    // Destination / headsign (truncated to fit)
    const time = timeInfo(d);
    const timeW = Math.max(72, time.text.length * 13 + 8);
    const destX = badgeX + badgeW + 14;
    const destAvail = VIEW_W - PAD - timeW - destX - 12;
    const maxChars = Math.max(4, Math.floor(destAvail / 13));
    let dest = d.headsign || d.route || "";
    if (dest.length > maxChars) dest = dest.slice(0, maxChars - 1) + "…";
    out += `<text x="${destX}" y="${cy + 7}" font-size="21" fill="#e8ecf3" letter-spacing="0.5">${escapeXml(dest)}</text>`;

    // Time, right aligned
    out += `<text x="${VIEW_W - PAD}" y="${cy + 7}" text-anchor="end" font-size="21" font-weight="700" fill="${time.color}" letter-spacing="0.5">${escapeXml(time.text)}</text>`;
    return out;
  }

  _alertBanner(text, y) {
    const msg = `ALERT •  ${text.replace(/\s+/g, " ").trim()}        `;
    const fontSize = 16;
    const charW = fontSize * 0.6;
    const textW = msg.length * charW;
    const speed = 70; // px per second
    const dur = Math.max(8, (VIEW_W + textW) / speed);
    const clipId = `${this._uid}-alertclip`;
    let out = "";
    out += `<rect x="0" y="${y}" width="${VIEW_W}" height="${ALERT_H}" fill="#3a1d1d"/>`;
    out += `<rect x="0" y="${y}" width="6" height="${ALERT_H}" fill="#ff5252"/>`;
    out += `<defs><clipPath id="${clipId}"><rect x="8" y="${y}" width="${VIEW_W - 16}" height="${ALERT_H}"/></clipPath></defs>`;
    out += `<g clip-path="url(#${clipId})">`;
    out += `<text x="0" y="${y + ALERT_H / 2 + 5}" font-size="${fontSize}" fill="#ffd2d2" letter-spacing="0.5">`;
    out += `<animateTransform attributeName="transform" type="translate" from="${VIEW_W} 0" to="${-textW} 0" dur="${dur}s" repeatCount="indefinite"/>`;
    out += escapeXml(msg);
    out += `</text></g>`;
    return out;
  }

  static getConfigElement() {
    return document.createElement("mbta-arrival-board-card-editor");
  }

  static getStubConfig(hass) {
    const sensor = Object.keys(hass.states).find(
      (e) => e.startsWith("sensor.") && e.endsWith("_next_departure")
    );
    return { entity: sensor || "sensor.mbta_next_departure" };
  }
}

customElements.define("mbta-arrival-board-card", MbtaArrivalBoardCard);

/* Visual editor — uses Home Assistant's <ha-form> with selectors so it works
 * without bundling any frontend dependencies. */
class MbtaArrivalBoardCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => EDITOR_LABELS[s.name] || s.name;
      this._form.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        const config = { type: "custom:mbta-arrival-board-card", ...ev.detail.value };
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config },
            bubbles: true,
            composed: true,
          })
        );
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = EDITOR_SCHEMA;
    // Surface the runtime defaults so toggles reflect effective behaviour.
    this._form.data = { show_alerts: true, show_clock: true, rows: 6, ...this._config };
  }
}

customElements.define("mbta-arrival-board-card-editor", MbtaArrivalBoardCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "mbta-arrival-board-card",
  name: "MBTA Arrival Board",
  description: "An SVG station arrival board for an MBTA stop, with live alerts.",
  preview: true,
  documentationURL: "https://github.com/bbloomberg/mbta-hass",
});

console.info("%c MBTA-ARRIVAL-BOARD-CARD %c loaded ", "color:#10131a;background:#FFC72C;font-weight:700", "color:#FFC72C");
