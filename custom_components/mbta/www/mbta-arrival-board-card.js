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
 * refresh never restarts the alert marquee. The two nodes sit flush inside one
 * ha-card so the alert looks attached to the bottom of the board. Tapping the
 * alert expands it to the full, wrapped text; tapping again collapses it.
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
  per_destination: "Per-group count (0 = single combined list)",
  group_by: "Group by (per-destination mode)",
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


class MbtaArrivalBoardCard extends HTMLElement {
  constructor() {
    super();
    this._uid = `mbta${++INSTANCE}`;
    this._config = {};
    this._hass = null;
    this._clockTimer = null;
    this._boardSig = null;
    this._alertKey = null;
    this._alertExpanded = false;
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
      group_by: "destination",
      show_alerts: true,
      show_clock: true,
      ...config,
    };
    this._boardSig = null; // force a rebuild on config change
    this._alertKey = null;
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

  _groupKey(d) {
    // In "direction" mode, branches that share a direction (e.g. Ashmont and
    // Braintree, both Red Line southbound) collapse into one group while each
    // row still shows its own terminus. Otherwise group by terminus/headsign.
    if (this._config.group_by === "direction") {
      const dir = d.direction_id != null ? d.direction_id : d.direction != null ? d.direction : "";
      return `${d.route_id || d.route || ""}|dir:${dir}`;
    }
    return d.headsign || d.route || "?";
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
        const key = this._groupKey(d);
        if (!groups.has(key)) groups.set(key, []);
        const arr = groups.get(key);
        if (arr.length < per) arr.push(d);
      }
      // Order groups deterministically — by direction, then destination name —
      // so they keep a fixed position instead of reshuffling as the soonest
      // train changes on each refresh. Departures within a group stay in time
      // order.
      const ordered = [...groups.entries()]
        .sort((a, b) => {
          const da = a[1][0];
          const db = b[1][0];
          const dirA = da.direction_id != null ? da.direction_id : 99;
          const dirB = db.direction_id != null ? db.direction_id : 99;
          if (dirA !== dirB) return dirA - dirB;
          const ka = da.headsign || da.route || a[0];
          const kb = db.headsign || db.route || b[0];
          return ka < kb ? -1 : ka > kb ? 1 : 0;
        })
        .map((e) => e[1]);
      return [].concat(...ordered).slice(0, GROUP_CAP);
    }

    return deps.slice(0, cfg.rows || 6);
  }

  _ensureDom() {
    if (this._card) return;
    // The board and alert are separate nodes (so the alert refreshes
    // independently of departures), but with squared edges and no gap they read
    // as one card — the ha-card's overflow/rounding stitches them together so
    // the alert looks attached to the bottom of the board.
    this.innerHTML =
      '<ha-card style="overflow:hidden;background:#0b0e14">' +
      '<div class="mbta-board" style="line-height:0"></div>' +
      '<div class="mbta-alert" style="line-height:0;cursor:pointer;margin-top:-1px;display:none"></div>' +
      "</ha-card>";
    this._card = this.querySelector("ha-card");
    this._boardEl = this.querySelector(".mbta-board");
    this._alertEl = this.querySelector(".mbta-alert");
    // Tap the alert to expand it to the full text (and tap again to collapse).
    this._alertEl.addEventListener("click", () => {
      this._alertExpanded = !this._alertExpanded;
      this._render();
    });
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

    // Rebuild the alert banner only when its text or expanded state changes —
    // this keeps the marquee from restarting on every departure refresh.
    if (!alertText) this._alertExpanded = false;
    const alertKey = alertText ? `${alertText}|${this._alertExpanded ? "x" : "c"}` : "";
    if (alertKey !== this._alertKey) {
      this._alertKey = alertKey;
      this._alertEl.style.display = alertText ? "block" : "none";
      this._alertEl.innerHTML = alertText
        ? this._alertSvg(alertText, this._alertExpanded)
        : "";
    }
  }

  _clockText() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  _boardSvg(title, clock, stateObj, departures) {
    const rowsH = Math.max(departures.length, 1) * ROW_H;
    const totalH = HEADER_H + rowsH + PAD;
    let svg = `<svg viewBox="0 0 ${VIEW_W} ${totalH}" width="100%" preserveAspectRatio="xMidYMid meet" style="display:block" font-family="${MONO}" role="img" aria-label="${escapeXml(title)} arrival board">`;

    // Square corners — the ha-card rounds/clips the outer edges so the alert
    // banner can sit flush against the bottom.
    svg += `<rect x="0" y="0" width="${VIEW_W}" height="${totalH}" fill="#0b0e14"/>`;

    // Header
    svg += `<rect x="0" y="0" width="${VIEW_W}" height="${HEADER_H}" fill="#11151f"/>`;
    svg += `<circle cx="${PAD + 16}" cy="${HEADER_H / 2}" r="12" fill="#FFC72C"/>`;
    svg += `<text x="${PAD + 16}" y="${HEADER_H / 2 + 5}" text-anchor="middle" font-size="13" font-weight="700" fill="#10131a">T</text>`;
    svg += `<text x="${PAD + 38}" y="${HEADER_H / 2 + 7}" font-size="20" font-weight="700" fill="#ffffff">${escapeXml(title)}</text>`;
    if (clock) {
      svg += `<text x="${VIEW_W - PAD - 4}" y="${HEADER_H / 2 + 7}" text-anchor="end" font-size="18" fill="#FFB000" letter-spacing="1">${escapeXml(clock)}</text>`;
    }
    // Divider under the header (same rule used between destination groups).
    svg += `<line x1="8" y1="${HEADER_H}" x2="${VIEW_W - 8}" y2="${HEADER_H}" stroke="#3d4759" stroke-width="2.5"/>`;

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
        const key = this._groupKey(d);
        const boundary = grouped && i > 0 && key !== prevKey;
        svg += this._row(d, HEADER_H + i * ROW_H, i, boundary);
        prevKey = key;
      });
    }

    svg += `</svg>`;
    return svg;
  }

  _centeredMessage(text, top, h, color) {
    return `<text x="${VIEW_W / 2}" y="${top + h / 2 + 8}" text-anchor="middle" font-size="22" fill="${color}" letter-spacing="2">${escapeXml(text)}</text>`;
  }

  _row(d, y, i, boundary) {
    const cy = y + ROW_H / 2;
    let out = "";
    if (i % 2 === 1) {
      out += `<rect x="6" y="${y}" width="${VIEW_W - 12}" height="${ROW_H}" fill="#0f131c"/>`;
    }
    // The first row sits directly under the header rule, so it needs no line of
    // its own; group boundaries get the heavier divider, others a thin line.
    if (i > 0) {
      const rule = boundary
        ? 'stroke="#3d4759" stroke-width="2.5"'
        : 'stroke="#1c2230" stroke-width="1"';
      out += `<line x1="8" y1="${y}" x2="${VIEW_W - 8}" y2="${y}" ${rule}/>`;
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

  _alertSvg(text, expanded) {
    if (expanded) return this._alertExpandedSvg(text);

    const fontSize = 16;
    const charW = fontSize * 0.6;
    const clipL = 22; // left edge of the text, just past the red bar
    const iconCx = VIEW_W - 22; // expand/collapse affordance
    const availW = iconCx - 16 - clipL; // room for the scrolling text
    const H = ALERT_PANEL_H;
    const msg = `ALERT •  ${text.replace(/\s+/g, " ").trim()}`;
    const textW = msg.length * charW;
    const baseY = H / 2 + 5;

    let inner;
    if (textW <= availW) {
      // Fits — show it all, no scrolling.
      inner = `<text x="${clipL}" y="${baseY}" font-size="${fontSize}" fill="#ffd2d2" letter-spacing="0.5">${escapeXml(msg)}</text>`;
    } else {
      // Start with the beginning of the message visible, hold briefly, then
      // scroll the whole thing to the left and loop. A CSS animation is used
      // (rather than SMIL) because it reliably starts on dynamically inserted
      // nodes — SMIL sometimes fails to begin until the page is refreshed.
      const id = `${this._uid}-${++this._alertSeq}`;
      const gap = 56; // trailing blank before it loops back to the start
      const dist = textW + gap;
      const dur = Math.max(8, dist / 70);
      const style =
        `<style>.amsg-${id}{animation:ascroll-${id} ${dur}s linear infinite;}` +
        `@keyframes ascroll-${id}{0%{transform:translateX(0)}10%{transform:translateX(0)}` +
        `100%{transform:translateX(-${dist}px)}}</style>`;
      inner =
        style +
        `<defs><clipPath id="aclip-${id}"><rect x="${clipL}" y="0" width="${availW}" height="${H}"/></clipPath></defs>` +
        `<g clip-path="url(#aclip-${id})">` +
        `<text class="amsg-${id}" x="${clipL}" y="${baseY}" font-size="${fontSize}" fill="#ffd2d2" letter-spacing="0.5">${escapeXml(msg)}</text>` +
        `</g>`;
    }

    return (
      `<svg viewBox="0 0 ${VIEW_W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet" style="display:block" font-family="${MONO}" role="img" aria-label="Service alert — tap to expand">` +
      `<rect x="0" y="0" width="${VIEW_W}" height="${H}" fill="#2a1414"/>` +
      `<rect x="10" y="${H / 2 - 9}" width="4" height="18" rx="2" fill="#ff5252"/>` +
      inner +
      this._expandIcon(iconCx, H / 2, false) +
      `</svg>`
    );
  }

  _alertExpandedSvg(text) {
    const fontSize = 15;
    const lineH = 21;
    const padX = 22;
    const padY = 14;
    const iconCx = VIEW_W - 22;
    const availW = iconCx - 16 - padX;
    const maxChars = Math.max(12, Math.floor(availW / (fontSize * 0.6)));
    const lines = this._wrapText(text, maxChars);
    const H = padY * 2 + lines.length * lineH;

    let body = "";
    lines.forEach((line, i) => {
      body += `<text x="${padX}" y="${padY + (i + 1) * lineH - 6}" font-size="${fontSize}" fill="#ffd2d2">${escapeXml(line)}</text>`;
    });

    return (
      `<svg viewBox="0 0 ${VIEW_W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet" style="display:block" font-family="${MONO}" role="img" aria-label="Service alert (expanded) — tap to collapse">` +
      `<rect x="0" y="0" width="${VIEW_W}" height="${H}" fill="#2a1414"/>` +
      `<rect x="10" y="${padY}" width="4" height="${H - padY * 2}" rx="2" fill="#ff5252"/>` +
      body +
      this._expandIcon(iconCx, padY + 5, true) +
      `</svg>`
    );
  }

  _expandIcon(cx, cy, minus) {
    const c = "#ff9b9b";
    let s = `<rect x="${cx - 7}" y="${cy - 1.5}" width="14" height="3" rx="1.5" fill="${c}"/>`;
    if (!minus) s += `<rect x="${cx - 1.5}" y="${cy - 7}" width="3" height="14" rx="1.5" fill="${c}"/>`;
    return s;
  }

  _wrapText(text, maxChars) {
    const out = [];
    for (const para of String(text).split("\n")) {
      const words = para.trim().split(/\s+/).filter(Boolean);
      if (!words.length) {
        out.push("");
        continue;
      }
      let line = "";
      for (const w of words) {
        if (!line) line = w;
        else if ((line + " " + w).length <= maxChars) line += " " + w;
        else {
          out.push(line);
          line = w;
        }
        while (line.length > maxChars) {
          out.push(line.slice(0, maxChars));
          line = line.slice(maxChars);
        }
      }
      if (line) out.push(line);
    }
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
      {
        name: "group_by",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "destination", label: "Terminus / destination (Ashmont, Braintree, …)" },
              { value: "direction", label: "Direction (combine branches into one group)" },
            ],
          },
        },
      },
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
      group_by: "destination",
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
