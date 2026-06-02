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
 *   rows: 6                 # max rows in flat mode
 *   per_destination: 3      # group by destination, N each (0 = flat list)
 *   routes: [Red Line, 1]   # only show these routes (empty = all)
 *   destinations: [Harvard, Nubian]   # only show these destinations (empty = all)
 *   show_alerts: true
 *   show_clock: true
 *
 * The departures board and the alert banner are rendered into separate DOM
 * nodes and each is rebuilt only when its own content changes, so a departure
 * refresh never restarts the alert marquee.
 */

const VIEW_W = 520;
const HEADER_H = 56;
const ROW_H = 46;
const ALERT_PANEL_H = 40;
const PAD = 12;
const GROUP_CAP = 30; // safety cap on total rows when grouping by destination
const MONO = "'DejaVu Sans Mono','Roboto Mono',ui-monospace,monospace";

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

const EDITOR_LABELS = {
  entity: "Departure sensor (required)",
  title: "Title (defaults to the stop name)",
  alert_entity: "Alert binary sensor (optional)",
  routes: "Routes to show (empty = all)",
  destinations: "Destinations to show (empty = all)",
  per_destination: "Per-destination count (0 = single combined list)",
  rows: "Max rows (flat mode)",
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

function uniq(arr) {
  return [...new Set(arr)];
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

function sortKey(d) {
  return d && d.minutes != null ? d.minutes : Number.POSITIVE_INFINITY;
}

class MbtaArrivalBoardCard extends HTMLElement {
  constructor() {
    super();
    this._uid = `mbta${++INSTANCE}`;
    this._config = {};
    this._hass = null;
    this._clockTimer = null;
    this._boardSig = null;
    this._alertText = null;
    this._alertSeq = 0;
    this._lastCount = 1;
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("You must define an 'entity' (the *_next_departure sensor).");
    }
    this._config = {
      rows: 6,
      per_destination: 0,
      show_alerts: true,
      show_clock: true,
      ...config,
    };
    this._boardSig = null; // force a rebuild on config change
    this._alertText = null;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    // Keep the clock fresh; the diffing in _render() means this is cheap and
    // never touches the alert banner.
    this._clockTimer = setInterval(() => this._render(), 30000);
    this._render();
  }

  disconnectedCallback() {
    if (this._clockTimer) clearInterval(this._clockTimer);
    this._clockTimer = null;
  }

  getCardSize() {
    return 1 + Math.min(this._lastCount, 10);
  }

  _alertEntityId() {
    if (this._config.alert_entity) return this._config.alert_entity;
    const e = this._config.entity || "";
    if (e.startsWith("sensor.") && e.endsWith("_next_departure")) {
      return "binary_sensor." + e.slice("sensor.".length).replace(/_next_departure$/, "_service_alert");
    }
    return null;
  }

  _computeDepartures(stateObj) {
    const cfg = this._config;
    let deps = (stateObj && stateObj.attributes.departures) || [];

    // Route filter (match either the display name or the route id).
    if (Array.isArray(cfg.routes) && cfg.routes.length) {
      const set = new Set(cfg.routes);
      deps = deps.filter((d) => set.has(d.route) || set.has(d.route_id));
    }
    // Destination filter.
    if (Array.isArray(cfg.destinations) && cfg.destinations.length) {
      const set = new Set(cfg.destinations);
      deps = deps.filter((d) => set.has(d.headsign));
    }

    const per = Number(cfg.per_destination) || 0;
    if (per > 0) {
      // Group by destination, keep the next `per` of each, order groups by the
      // soonest departure so the most imminent destinations sit at the top.
      const groups = new Map();
      for (const d of deps) {
        const key = d.headsign || d.route || "?";
        if (!groups.has(key)) groups.set(key, []);
        const arr = groups.get(key);
        if (arr.length < per) arr.push(d);
      }
      const ordered = [...groups.values()].sort((a, b) => sortKey(a[0]) - sortKey(b[0]));
      return [].concat(...ordered).slice(0, GROUP_CAP);
    }

    return deps.slice(0, cfg.rows || 6);
  }

  _ensureDom() {
    if (this._card) return;
    this.innerHTML =
      '<ha-card style="overflow:hidden">' +
      '<div class="mbta-board" style="padding:8px 8px 0 8px"></div>' +
      '<div class="mbta-alert" style="padding:0 8px 8px 8px;display:none"></div>' +
      "</ha-card>";
    this._card = this.querySelector("ha-card");
    this._boardEl = this.querySelector(".mbta-board");
    this._alertEl = this.querySelector(".mbta-alert");
  }

  _render() {
    if (!this._hass) return;
    const cfg = this._config;
    const stateObj = this._hass.states[cfg.entity];

    // ---- alert text (independent of the board) ----
    const alertId = cfg.show_alerts === false ? null : this._alertEntityId();
    const alertObj = alertId ? this._hass.states[alertId] : null;
    const alertActive = alertObj && alertObj.state === "on";
    const alertText = alertActive
      ? alertObj.attributes.alert_text || (alertObj.attributes.headers || []).join("  •  ")
      : "";

    // ---- board content ----
    const title =
      cfg.title ||
      (stateObj && (stateObj.attributes.stop_name || stateObj.attributes.friendly_name)) ||
      "MBTA";
    const departures = this._computeDepartures(stateObj);
    this._lastCount = Math.max(departures.length, 1);
    const clock = cfg.show_clock === false ? "" : this._clockText();

    this._ensureDom();

    // Rebuild the board only when something visible changed.
    const boardSig = JSON.stringify({
      title,
      clock,
      found: !!stateObj,
      deps: departures.map((d) => [d.route_id, d.route, d.headsign, d.minutes, d.status, d.cancelled]),
    });
    if (boardSig !== this._boardSig) {
      this._boardSig = boardSig;
      this._boardEl.innerHTML = this._boardSvg(title, clock, stateObj, departures);
    }

    // Rebuild the alert banner only when the text changes — this is what keeps
    // the marquee from restarting on every departure refresh.
    if (alertText !== this._alertText) {
      this._alertText = alertText;
      this._alertEl.style.display = alertText ? "block" : "none";
      this._alertEl.innerHTML = alertText ? this._alertSvg(alertText) : "";
    }
  }

  _clockText() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  _boardSvg(title, clock, stateObj, departures) {
    const rowsH = Math.max(departures.length, 1) * ROW_H;
    const totalH = HEADER_H + rowsH + PAD;
    let svg = `<svg viewBox="0 0 ${VIEW_W} ${totalH}" width="100%" preserveAspectRatio="xMidYMid meet" font-family="${MONO}" role="img" aria-label="${escapeXml(title)} arrival board">`;

    svg += `<rect x="0" y="0" width="${VIEW_W}" height="${totalH}" rx="14" fill="#0b0e14" stroke="#1c2230" stroke-width="2"/>`;

    // Header
    svg += `<rect x="0" y="0" width="${VIEW_W}" height="${HEADER_H}" rx="14" fill="#11151f"/>`;
    svg += `<rect x="0" y="${HEADER_H - 14}" width="${VIEW_W}" height="14" fill="#11151f"/>`;
    svg += `<circle cx="${PAD + 16}" cy="${HEADER_H / 2}" r="12" fill="#FFC72C"/>`;
    svg += `<text x="${PAD + 16}" y="${HEADER_H / 2 + 5}" text-anchor="middle" font-size="13" font-weight="700" fill="#10131a">T</text>`;
    svg += `<text x="${PAD + 38}" y="${HEADER_H / 2 + 7}" font-size="20" font-weight="700" fill="#ffffff">${escapeXml(title)}</text>`;
    if (clock) {
      svg += `<text x="${VIEW_W - PAD - 4}" y="${HEADER_H / 2 + 7}" text-anchor="end" font-size="18" fill="#FFB000" letter-spacing="1">${escapeXml(clock)}</text>`;
    }

    if (!stateObj) {
      svg += this._centeredMessage("ENTITY NOT FOUND", HEADER_H, rowsH, "#ff5252");
    } else if (departures.length === 0) {
      svg += this._centeredMessage("NO DEPARTURES", HEADER_H, rowsH, "#7a8290");
    } else {
      // In per-destination mode the list is ordered by destination group; draw
      // a heavier rule where the destination changes to separate directions.
      const grouped = (Number(this._config.per_destination) || 0) > 0;
      let prevKey = null;
      departures.forEach((d, i) => {
        const key = d.headsign || d.route || "?";
        const newGroup = grouped && i > 0 && key !== prevKey;
        svg += this._row(d, HEADER_H + i * ROW_H, i, newGroup);
        prevKey = key;
      });
    }

    svg += `</svg>`;
    return svg;
  }

  _centeredMessage(text, top, h, color) {
    return `<text x="${VIEW_W / 2}" y="${top + h / 2 + 8}" text-anchor="middle" font-size="22" fill="${color}" letter-spacing="2">${escapeXml(text)}</text>`;
  }

  _row(d, y, i, newGroup) {
    const cy = y + ROW_H / 2;
    let out = "";
    if (i % 2 === 1) {
      out += `<rect x="6" y="${y}" width="${VIEW_W - 12}" height="${ROW_H}" fill="#0f131c"/>`;
    }
    if (newGroup) {
      // Heavier divider between destination groups.
      out += `<line x1="8" y1="${y}" x2="${VIEW_W - 8}" y2="${y}" stroke="#3d4759" stroke-width="2.5"/>`;
    } else {
      out += `<line x1="8" y1="${y}" x2="${VIEW_W - 8}" y2="${y}" stroke="#1c2230" stroke-width="1"/>`;
    }

    const color = routeColor(d);
    const label = badgeText(d);
    const badgeW = Math.max(40, label.length * 12 + 14);
    const badgeX = PAD;
    out += `<rect x="${badgeX}" y="${cy - 14}" width="${badgeW}" height="28" rx="6" fill="${color}"/>`;
    out += `<text x="${badgeX + badgeW / 2}" y="${cy + 6}" text-anchor="middle" font-size="16" font-weight="700" fill="${contrastText(color)}">${escapeXml(label)}</text>`;

    const time = timeInfo(d);
    const timeW = Math.max(72, time.text.length * 13 + 8);
    const destX = badgeX + badgeW + 14;
    const destAvail = VIEW_W - PAD - timeW - destX - 12;
    const maxChars = Math.max(4, Math.floor(destAvail / 13));
    let dest = d.headsign || d.route || "";
    if (dest.length > maxChars) dest = dest.slice(0, maxChars - 1) + "…";
    out += `<text x="${destX}" y="${cy + 7}" font-size="21" fill="#e8ecf3" letter-spacing="0.5">${escapeXml(dest)}</text>`;

    out += `<text x="${VIEW_W - PAD}" y="${cy + 7}" text-anchor="end" font-size="21" font-weight="700" fill="${time.color}" letter-spacing="0.5">${escapeXml(time.text)}</text>`;
    return out;
  }

  _alertSvg(text) {
    const msg = `ALERT •  ${text.replace(/\s+/g, " ").trim()}        `;
    const fontSize = 16;
    const charW = fontSize * 0.6;
    const textW = msg.length * charW;
    const dur = Math.max(8, (VIEW_W + textW) / 70);
    const clipId = `${this._uid}-alert-${++this._alertSeq}`;
    const H = ALERT_PANEL_H;
    let out = `<svg viewBox="0 0 ${VIEW_W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet" font-family="${MONO}" role="img" aria-label="Service alert">`;
    out += `<rect x="0" y="0" width="${VIEW_W}" height="${H}" rx="12" fill="#2a1414" stroke="#5a2a2a" stroke-width="1.5"/>`;
    out += `<rect x="10" y="${H / 2 - 9}" width="4" height="18" rx="2" fill="#ff5252"/>`;
    out += `<defs><clipPath id="${clipId}"><rect x="22" y="0" width="${VIEW_W - 34}" height="${H}"/></clipPath></defs>`;
    out += `<g clip-path="url(#${clipId})">`;
    out += `<text x="0" y="${H / 2 + 5}" font-size="${fontSize}" fill="#ffd2d2" letter-spacing="0.5">`;
    out += `<animateTransform attributeName="transform" type="translate" from="${VIEW_W} 0" to="${-textW} 0" dur="${dur}s" repeatCount="indefinite"/>`;
    out += escapeXml(msg);
    out += `</text></g></svg>`;
    return out;
  }

  static getConfigElement() {
    return document.createElement("mbta-arrival-board-card-editor");
  }

  static getStubConfig(hass) {
    const sensor = Object.keys(hass.states).find(
      (e) => e.startsWith("sensor.") && e.endsWith("_next_departure")
    );
    return { entity: sensor || "sensor.mbta_next_departure", per_destination: 3 };
  }
}

customElements.define("mbta-arrival-board-card", MbtaArrivalBoardCard);

/* Visual editor — uses Home Assistant's <ha-form> with selectors so it works
 * without bundling any frontend dependencies. Route/destination options are
 * derived live from the selected sensor's current departures. */
class MbtaArrivalBoardCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _departures() {
    const e = this._config && this._config.entity;
    const st = e && this._hass && this._hass.states[e];
    return (st && st.attributes.departures) || [];
  }

  _schema() {
    const deps = this._departures();
    const routeOpts = uniq(deps.map((d) => d.route).filter(Boolean)).map((o) => ({ value: o, label: o }));
    const destOpts = uniq(deps.map((d) => d.headsign).filter(Boolean)).map((o) => ({ value: o, label: o }));
    return [
      { name: "entity", required: true, selector: { entity: { domain: "sensor" } } },
      { name: "title", selector: { text: {} } },
      { name: "alert_entity", selector: { entity: { domain: "binary_sensor" } } },
      { name: "routes", selector: { select: { multiple: true, custom_value: true, mode: "list", options: routeOpts } } },
      { name: "destinations", selector: { select: { multiple: true, custom_value: true, mode: "list", options: destOpts } } },
      { name: "per_destination", selector: { number: { min: 0, max: 10, mode: "box" } } },
      { name: "rows", selector: { number: { min: 1, max: 20, mode: "box" } } },
      { name: "show_alerts", selector: { boolean: {} } },
      { name: "show_clock", selector: { boolean: {} } },
    ];
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
          new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true })
        );
      });
      this.appendChild(this._form);
    }

    // Only reassign the schema when its option set actually changes, so the
    // form doesn't reset while the user is typing.
    const schema = this._schema();
    const sig = JSON.stringify(
      schema.map((s) => [s.name, s.selector.select ? s.selector.select.options : null])
    );
    if (sig !== this._schemaSig) {
      this._schemaSig = sig;
      this._form.schema = schema;
    }
    this._form.hass = this._hass;
    this._form.data = {
      show_alerts: true,
      show_clock: true,
      rows: 6,
      per_destination: 0,
      ...this._config,
    };
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
