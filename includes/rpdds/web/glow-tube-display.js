(() => {
  const DIGIT_SVG_TRANSLATE_X = 11;
  const DIGIT_SVG_SCALE_X = 0.78;

  const GLYPHS = {
    "0": `<ellipse class="trace" cx="50" cy="80" rx="27" ry="56"></ellipse>`,
    "1": `<path class="trace" d="M50 24 L50 136"></path>`,
    "2": `<path class="trace" d="M28 42 C28 24 42 15 58 15 C73 15 82 25 82 40 C82 56 69 65 55 75 C38 87 28 96 28 118 L82 118"></path>`,
    "3": `<path class="trace" d="M30 31 C38 18 53 14 66 17 C77 20 83 30 83 42 C83 58 70 68 53 71 C70 73 83 83 83 98 C83 112 75 122 62 126 C49 129 35 124 26 113"></path>`,
    "4": `<path class="trace" d="M72 18 L72 136 M24 88 L80 88 M24 88 L60 18"></path>`,
    "5": `<path class="trace" d="M80 18 L34 18 L34 71 C42 65 53 63 64 65 C78 68 85 80 85 95 C85 112 72 124 56 127 C42 129 30 124 23 113"></path>`,
    "6": `<path class="trace" d="M74 31 C67 19 56 14 44 16 C31 18 22 30 19 48 C17 66 20 95 31 112 C40 126 58 129 70 118 C81 108 82 89 72 78 C61 66 42 68 31 80"></path>`,
    "7": `<path class="trace" d="M24 18 L82 18 L46 136"></path>`,
    "8": `<ellipse class="trace" cx="50" cy="46" rx="22" ry="26"></ellipse><ellipse class="trace" cx="50" cy="103" rx="28" ry="32"></ellipse>`,
    "9": `<path class="trace" d="M31 109 C38 121 50 127 63 124 C76 121 84 106 84 85 C84 64 80 36 68 22 C57 9 38 12 28 26 C20 39 21 59 32 70 C43 81 61 80 72 69"></path>`,
    "-": `<path class="trace" d="M28 82 L72 82"></path>`,
    ".": `<circle class="trace dot-fill" cx="50" cy="122" r="7"></circle>`,
    ":": `<circle class="trace dot-fill" cx="50" cy="54" r="8"></circle><circle class="trace dot-fill" cx="50" cy="108" r="8"></circle>`,
    " ": ``
  };

  const DIGIT_STACK = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];
  const STACK_META = {
    "0": { z: -20, dx: -2.0, dy: -2.6, scale: 1.03 },
    "1": { z: -15, dx:  1.3, dy: -1.8, scale: 0.95 },
    "2": { z: -10, dx: -1.2, dy:  0.4, scale: 0.97 },
    "3": { z:  -6, dx:  1.2, dy:  1.4, scale: 0.99 },
    "4": { z:  -1, dx:  1.6, dy: -0.9, scale: 1.01 },
    "5": { z:   4, dx: -0.4, dy:  1.0, scale: 1.03 },
    "6": { z:   9, dx: -1.4, dy:  2.0, scale: 1.05 },
    "7": { z:  14, dx:  1.1, dy: -1.8, scale: 1.07 },
    "8": { z:  18, dx:  0.2, dy:  0.9, scale: 1.09 },
    "9": { z:  22, dx: -0.8, dy: -0.6, scale: 1.11 }
  };

  const HTML_ESCAPE = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  };

  function escapeHTML(value = "") {
    return String(value).replace(/[&<>"']/g, (m) => HTML_ESCAPE[m]);
  }

  function categoryOf(ch) {
    if (/[0-9]/.test(ch)) return "digit";
    if (ch === " ") return "space";
    if (GLYPHS[ch] !== undefined) return "symbol";
    return "space";
  }

  function slotSignatureOf(ch) {
    const category = categoryOf(ch);
    if (category === "digit") return "digit";
    if (category === "symbol") return `symbol:${ch}`;
    return "space";
  }

  function symbolClass(ch) {
    switch (ch) {
      case ":":
        return "symbol-colon";
      case ".":
        return "symbol-dot";
      case "-":
        return "symbol-minus";
      default:
        return "symbol-generic";
    }
  }

  function wrapDigitGlyph(markup) {
    return `<g class="digit-shape" transform="translate(${DIGIT_SVG_TRANSLATE_X} 0) scale(${DIGIT_SVG_SCALE_X} 1)">${markup}</g>`;
  }

  function renderGlyphPass(markup, passClass, isDigit = false) {
    const body = isDigit ? wrapDigitGlyph(markup) : markup;
    return `<svg class="glyph ${passClass}" viewBox="0 0 100 160" aria-hidden="true">${body}</svg>`;
  }

  function fmt(value) {
    return Number(value.toFixed(3));
  }

  let instanceCounter = 0;

  class GlowTubeDisplay extends HTMLElement {
    static get observedAttributes() {
      return ["value"];
    }

    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._instanceId = `glow-tube-${++instanceCounter}`;
      this._layoutSignature = "";
      this._raf = 0;
      this._resizeObserver = typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => this._scheduleDecorRefresh())
        : null;
    }

    connectedCallback() {
      if (!this.hasAttribute("value")) {
        this.setAttribute("value", "000857758:");
      }
      this.render();
    }

    disconnectedCallback() {
      if (this._resizeObserver) this._resizeObserver.disconnect();
      if (this._raf) cancelAnimationFrame(this._raf);
      this._raf = 0;
    }

    attributeChangedCallback(name, oldValue, newValue) {
      if (name !== "value" || oldValue === newValue || !this.isConnected) return;

      const nextSignature = this._signature(newValue || "");
      if (!this.shadowRoot.innerHTML || nextSignature !== this._layoutSignature) {
        this.render();
      } else {
        this.updateValue(false);
      }
    }

    get value() {
      return this.getAttribute("value") ?? "";
    }

    set value(nextValue) {
      this.setAttribute("value", String(nextValue));
    }

    _signature(value) {
      return Array.from(value).map(slotSignatureOf).join("|");
    }

    _renderTubeHardware() {
      return `
        <div class="tube-hardware" aria-hidden="true">
          <span class="lead lead-left"></span>
          <span class="lead lead-right"></span>
          <span class="strap strap-top"></span>
          <span class="strap strap-bottom"></span>
        </div>
      `;
    }

    _renderGlyphStack(markup, isDigit = false) {
      return [
        renderGlyphPass(markup, "metal-pass", isDigit),
        renderGlyphPass(markup, "gas-pass", isDigit),
        renderGlyphPass(markup, "glow-pass", isDigit),
        renderGlyphPass(markup, "core-pass", isDigit)
      ].join("");
    }

    _renderCathode(char, activeChar) {
      const meta = STACK_META[char];
      const isActive = char === activeChar;
      const activeClass = isActive ? " active" : "";
      return `
        <div class="cathode${activeClass}" data-char="${char}" style="--z:${meta.z};--dx:${meta.dx}px;--dy:${meta.dy}px;--scale:${meta.scale};">
          ${this._renderGlyphStack(GLYPHS[char], true)}
        </div>
      `;
    }

    _renderDigitSlot(char, index) {
      const cathodes = DIGIT_STACK.map((item) => this._renderCathode(item, char)).join("");
      return `
        <div class="slot digit-slot lit" data-kind="digit" data-index="${index}">
          <div class="ambient"></div>
          ${this._renderTubeHardware()}
          ${cathodes}
          <div class="tube-shell" aria-hidden="true"></div>
        </div>
      `;
    }

    _renderSymbolSlot(char, index) {
      const glyph = GLYPHS[char] ?? "";
      const cls = symbolClass(char);
      return `
        <div class="slot symbol-slot ${cls} lit" data-kind="symbol" data-index="${index}" data-char="${escapeHTML(char)}">
          <div class="ambient"></div>
          ${this._renderTubeHardware()}
          <div class="symbol-glyph ignite">
            ${this._renderGlyphStack(glyph)}
          </div>
          <div class="tube-shell" aria-hidden="true"></div>
        </div>
      `;
    }

    _renderSpaceSlot(index) {
      return `<div class="slot-spacer" data-kind="space" data-index="${index}" aria-hidden="true"></div>`;
    }

    _renderSlot(char, index) {
      const kind = categoryOf(char);
      if (kind === "digit") return this._renderDigitSlot(char, index);
      if (kind === "symbol") return this._renderSymbolSlot(char, index);
      return this._renderSpaceSlot(index);
    }

    _buildMeshSVG(width, height, centers) {
      const meshId = `${this._instanceId}-mesh`;
      const side = 2.35;
      const rise = Math.sqrt(3) * side / 2;
      const pitchX = side * 1.5;
      const pitchY = rise * 2;
      const patternWidth = side * 3;
      const patternHeight = rise * 4;

      const hexPath = (cx, cy) => {
        const points = [
          [cx - side, cy],
          [cx - side / 2, cy - rise],
          [cx + side / 2, cy - rise],
          [cx + side, cy],
          [cx + side / 2, cy + rise],
          [cx - side / 2, cy + rise]
        ].map(([x, y]) => `${fmt(x)} ${fmt(y)}`).join(" L");
        return `M${points} Z`;
      };

      const hexes = [
        [0, rise],
        [0, rise + pitchY],
        [pitchX, 0],
        [pitchX, pitchY],
        [pitchX, patternHeight]
      ].map(([cx, cy]) => `<path d="${hexPath(cx, cy)}"></path>`).join("");

      const warmEllipses = centers.map((c) => {
        const rx = Math.max(26, c.width * 0.48);
        const ry = Math.max(18, c.height * 0.20);
        return `<ellipse cx="${fmt(c.x)}" cy="${fmt(c.y)}" rx="${fmt(rx)}" ry="${fmt(ry)}" style="fill: rgba(var(--gt-neon-rgb), 0.18);"></ellipse>`;
      }).join("");

      const coreEllipses = centers.map((c) => {
        const rx = Math.max(9, c.width * 0.18);
        const ry = Math.max(7, c.height * 0.08);
        return `<ellipse cx="${fmt(c.x)}" cy="${fmt(c.y)}" rx="${fmt(rx)}" ry="${fmt(ry)}" style="fill: rgba(var(--gt-core-rgb), 0.10);"></ellipse>`;
      }).join("");

      return `
        <svg class="mesh-svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <linearGradient id="${meshId}-metal" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#23272b"></stop>
              <stop offset="35%" stop-color="#111315"></stop>
              <stop offset="78%" stop-color="#08090a"></stop>
              <stop offset="100%" stop-color="#1b2024"></stop>
            </linearGradient>
            <linearGradient id="${meshId}-shadow-grad" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stop-color="#000000" stop-opacity="0.88"></stop>
              <stop offset="100%" stop-color="#0e1216" stop-opacity="0.0"></stop>
            </linearGradient>
            <linearGradient id="${meshId}-shine-grad" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stop-color="#ffffff" stop-opacity="0.12"></stop>
              <stop offset="24%" stop-color="#d7dde3" stop-opacity="0.045"></stop>
              <stop offset="100%" stop-color="#ffffff" stop-opacity="0"></stop>
            </linearGradient>
            <filter id="${meshId}-warm-blur" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="4.8"></feGaussianBlur>
            </filter>
            <filter id="${meshId}-core-blur" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="2.2"></feGaussianBlur>
            </filter>
            <pattern id="${meshId}-shadow" x="0" y="0" width="${fmt(patternWidth)}" height="${fmt(patternHeight)}" patternUnits="userSpaceOnUse">
              <g transform="translate(0.12,0.16)" fill="none" stroke="#000000" stroke-opacity="0.62" stroke-width="0.34" stroke-linecap="round" stroke-linejoin="round">
                ${hexes}
              </g>
            </pattern>
            <pattern id="${meshId}-body" x="0" y="0" width="${fmt(patternWidth)}" height="${fmt(patternHeight)}" patternUnits="userSpaceOnUse">
              <g fill="none" stroke="url(#${meshId}-metal)" stroke-width="0.20" stroke-linecap="round" stroke-linejoin="round">
                ${hexes}
              </g>
            </pattern>
            <pattern id="${meshId}-shine" x="0" y="0" width="${fmt(patternWidth)}" height="${fmt(patternHeight)}" patternUnits="userSpaceOnUse">
              <g transform="translate(-0.06,-0.06)" fill="none" stroke="url(#${meshId}-shine-grad)" stroke-width="0.08" stroke-linecap="round" stroke-linejoin="round">
                ${hexes}
              </g>
            </pattern>
            <pattern id="${meshId}-mask-pattern" x="0" y="0" width="${fmt(patternWidth)}" height="${fmt(patternHeight)}" patternUnits="userSpaceOnUse">
              <g fill="none" stroke="#ffffff" stroke-width="0.54" stroke-linecap="round" stroke-linejoin="round">
                ${hexes}
              </g>
            </pattern>
            <mask id="${meshId}-line-mask">
              <rect x="0" y="0" width="${width}" height="${height}" fill="#000000"></rect>
              <rect x="0" y="0" width="${width}" height="${height}" fill="url(#${meshId}-mask-pattern)"></rect>
            </mask>
          </defs>
          <g class="mesh-reactive" mask="url(#${meshId}-line-mask)">
            <g filter="url(#${meshId}-warm-blur)">
              ${warmEllipses}
            </g>
            <g filter="url(#${meshId}-core-blur)">
              ${coreEllipses}
            </g>
          </g>
          <rect class="mesh-shadow-fill" x="0" y="0" width="${width}" height="${height}" fill="url(#${meshId}-shadow)"></rect>
          <rect class="mesh-body-fill" x="0" y="0" width="${width}" height="${height}" fill="url(#${meshId}-body)"></rect>
          <rect class="mesh-shine-fill" x="0" y="0" width="${width}" height="${height}" fill="url(#${meshId}-shine)"></rect>
        </svg>
      `;
    }

    _scheduleDecorRefresh() {
      if (!this.isConnected) return;
      if (this._raf) cancelAnimationFrame(this._raf);
      this._raf = requestAnimationFrame(() => {
        this._raf = 0;
        this._refreshDecorations();
      });
    }

    _refreshDecorations() {
      const meshHost = this.shadowRoot.querySelector(".mesh-host");
      const underlight = this.shadowRoot.querySelector(".mesh-underlight");
      if (!meshHost || !underlight) return;

      const width = meshHost.clientWidth;
      const height = meshHost.clientHeight;
      if (!width || !height) return;

      const meshRect = meshHost.getBoundingClientRect();
      const slots = Array.from(this.shadowRoot.querySelectorAll(".slot.lit"));
      const centers = slots.map((slot) => {
        const rect = slot.getBoundingClientRect();
        return {
          x: rect.left - meshRect.left + rect.width / 2,
          y: rect.top - meshRect.top + rect.height / 2,
          width: rect.width,
          height: rect.height
        };
      });

      const gradients = centers.flatMap((c) => {
        const outerRx = Math.max(34, c.width * 0.56);
        const outerRy = Math.max(18, c.height * 0.22);
        const innerRx = Math.max(12, c.width * 0.20);
        const innerRy = Math.max(7, c.height * 0.08);
        return [
          `radial-gradient(${fmt(outerRx)}px ${fmt(outerRy)}px at ${fmt(c.x)}px ${fmt(c.y)}px, rgba(var(--gt-neon-rgb), 0.10), rgba(var(--gt-neon-rgb), 0.025) 42%, rgba(var(--gt-neon-rgb), 0.00) 74%)`,
          `radial-gradient(${fmt(innerRx)}px ${fmt(innerRy)}px at ${fmt(c.x)}px ${fmt(c.y)}px, rgba(var(--gt-core-rgb), 0.06), rgba(var(--gt-core-rgb), 0.00) 78%)`
        ];
      }).join(",");

      underlight.style.backgroundImage = gradients || "none";
      meshHost.innerHTML = this._buildMeshSVG(width, height, centers);
    }

    render() {
      const value = this.value || "";
      const chars = Array.from(value);
      this._layoutSignature = this._signature(value);

      const slots = chars.length
        ? chars.map((char, index) => this._renderSlot(char, index)).join("")
        : this._renderDigitSlot("0", 0);

      this.shadowRoot.innerHTML = `
        <style>
          :host {
            --gt-neon-rgb: 255, 96, 30;
            --gt-core-rgb: 255, 238, 224;
            --gt-wire-rgb: 124, 112, 103;
            --gt-wire-highlight-rgb: 187, 176, 169;
            --gt-mesh-dark-rgb: 14, 16, 18;
            --gt-mesh-light-rgb: 97, 104, 110;
            --gt-frame-top: #666f76;
            --gt-frame-mid: #262b30;
            --gt-frame-bottom: #14171a;
            --gt-window-top: #150806;
            --gt-window-bottom: #040202;
            --gt-slot-width: 64px;
            --gt-slot-height: 116px;
            --gt-gap: 8px;
            --gt-padding: 16px;
            --gt-frame-radius: 18px;
            --gt-depth-unit: 1.95px;
            --gt-inactive-opacity: 0.20;
            --gt-mesh-opacity: 0.70;
            --gt-view-rotate-x: 0deg;
            --gt-view-rotate-y: 0deg;
            display: inline-block;
            vertical-align: middle;
            color: rgb(var(--gt-neon-rgb));
            font-size: 0;
            line-height: 0;
          }

          *, *::before, *::after {
            box-sizing: border-box;
          }

          .display {
            position: relative;
            display: inline-block;
          }

          .display-shell {
            position: relative;
            padding: 10px;
            border-radius: calc(var(--gt-frame-radius) + 6px);
            background:
              linear-gradient(180deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.00) 10%),
              linear-gradient(180deg, var(--gt-frame-top), var(--gt-frame-mid) 48%, var(--gt-frame-bottom));
            box-shadow:
              inset 0 1px 0 rgba(255, 255, 255, 0.10),
              inset 0 -1px 0 rgba(0, 0, 0, 0.40),
              0 18px 42px rgba(0, 0, 0, 0.42);
          }

          .window {
            position: relative;
            padding: var(--gt-padding);
            border-radius: var(--gt-frame-radius);
            overflow: hidden;
            background:
              radial-gradient(130% 80% at 50% -8%, rgba(255, 255, 255, 0.05), transparent 38%),
              radial-gradient(120% 120% at 50% 118%, rgba(0, 0, 0, 0.34), transparent 48%),
              linear-gradient(180deg, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0.00) 12%, rgba(0, 0, 0, 0.12) 78%, rgba(var(--gt-neon-rgb), 0.035)),
              linear-gradient(180deg, var(--gt-window-top), var(--gt-window-bottom));
            box-shadow:
              inset 0 1px 0 rgba(255, 255, 255, 0.08),
              inset 0 -1px 0 rgba(255, 160, 100, 0.08),
              inset 0 0 30px rgba(var(--gt-neon-rgb), 0.04),
              0 12px 28px rgba(0, 0, 0, 0.30);
          }

          .window::before {
            content: "";
            position: absolute;
            inset: 0;
            pointer-events: none;
            background:
              linear-gradient(180deg, rgba(255, 255, 255, 0.02), rgba(255, 255, 255, 0.00) 18%),
              radial-gradient(90% 24% at 50% 0%, rgba(255, 255, 255, 0.05), transparent 70%),
              radial-gradient(100% 34% at 50% 100%, rgba(0, 0, 0, 0.18), transparent 72%);
            z-index: 0;
          }

          .row {
            position: relative;
            display: flex;
            align-items: stretch;
            gap: var(--gt-gap);
            perspective: none;
            transform-style: flat;
            transform: none;
            z-index: 1;
          }

          .slot,
          .slot-spacer {
            position: relative;
            flex: 0 0 auto;
          }

          .slot {
            width: var(--gt-slot-width);
            height: var(--gt-slot-height);
            overflow: visible;
            transform-style: preserve-3d;
          }

          .slot::before {
            content: "";
            position: absolute;
            inset: 11px 10px 8px;
            border-radius: 999px;
            background:
              radial-gradient(86% 62% at 50% 40%, rgba(0, 0, 0, 0.12), rgba(0, 0, 0, 0.00) 72%),
              linear-gradient(180deg, rgba(0, 0, 0, 0.08), rgba(0, 0, 0, 0.24));
            filter: blur(7px);
            opacity: 0.62;
            z-index: 0;
            pointer-events: none;
          }

          .symbol-slot {
            width: calc(var(--gt-slot-width) * 0.46);
          }

          .symbol-minus {
            width: calc(var(--gt-slot-width) * 0.82);
          }

          .slot-spacer {
            width: calc(var(--gt-slot-width) * 0.22);
          }

          .ambient {
            position: absolute;
            inset: 8px 12px;
            z-index: 1;
            pointer-events: none;
            background:
              radial-gradient(62% 52% at 50% 52%, rgba(var(--gt-neon-rgb), 0.16), rgba(var(--gt-neon-rgb), 0.035) 42%, transparent 78%),
              radial-gradient(22% 16% at 50% 52%, rgba(var(--gt-core-rgb), 0.14), transparent 82%);
            filter: blur(12px);
            opacity: 0.16;
            transition: opacity 200ms ease;
          }

          .slot.lit .ambient {
            opacity: 0.74;
          }

          .tube-hardware {
            position: absolute;
            inset: 12px 17px;
            z-index: 1;
            pointer-events: none;
            opacity: 0.78;
          }

          .symbol-slot .tube-hardware {
            inset: 12px 10px;
          }

          .symbol-minus .tube-hardware {
            inset: 12px 15px;
          }

          .lead,
          .strap {
            position: absolute;
            border-radius: 999px;
          }

          .lead {
            bottom: 0;
            width: 1.55px;
            height: 22px;
            background:
              linear-gradient(180deg, rgba(var(--gt-wire-highlight-rgb), 0.22), rgba(var(--gt-wire-rgb), 0.70) 34%, rgba(var(--gt-wire-rgb), 0.12));
            box-shadow: 0 0 0 0.5px rgba(0, 0, 0, 0.32);
          }

          .lead-left {
            left: 32%;
          }

          .lead-right {
            right: 32%;
          }

          .strap {
            left: 50%;
            transform: translateX(-50%);
            width: 20px;
            height: 1.55px;
            background:
              linear-gradient(90deg, rgba(var(--gt-wire-rgb), 0.05), rgba(var(--gt-wire-highlight-rgb), 0.32) 18%, rgba(var(--gt-wire-rgb), 0.64) 50%, rgba(var(--gt-wire-highlight-rgb), 0.28) 82%, rgba(var(--gt-wire-rgb), 0.05));
            box-shadow: 0 0 0 0.5px rgba(0, 0, 0, 0.26);
          }

          .strap-top {
            top: 8px;
          }

          .strap-bottom {
            bottom: 28px;
          }

          .tube-shell {
            position: absolute;
            inset: 4px 10px 4px;
            z-index: 4;
            border-radius: 999px;
            pointer-events: none;
            background:
              radial-gradient(120% 18% at 50% 3%, rgba(255, 255, 255, 0.12), rgba(255, 255, 255, 0.00) 72%),
              radial-gradient(120% 18% at 50% 97%, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0.00) 72%),
              linear-gradient(90deg, rgba(255, 255, 255, 0.16) 0%, rgba(255, 255, 255, 0.05) 6%, rgba(255, 255, 255, 0.00) 18%, rgba(255, 255, 255, 0.00) 44%, rgba(255, 255, 255, 0.03) 70%, rgba(255, 255, 255, 0.10) 100%),
              linear-gradient(180deg, rgba(38, 17, 14, 0.10), rgba(8, 4, 4, 0.16));
            box-shadow:
              inset 0 0 0 1px rgba(255, 255, 255, 0.055),
              inset 0 10px 10px rgba(255, 255, 255, 0.015),
              inset 0 -14px 18px rgba(0, 0, 0, 0.16),
              inset 0 0 20px rgba(var(--gt-neon-rgb), 0.03);
          }

          .tube-shell::before {
            content: "";
            position: absolute;
            inset: 0;
            border-radius: inherit;
            background:
              linear-gradient(104deg, rgba(255, 255, 255, 0.16) 0%, rgba(255, 255, 255, 0.06) 8%, rgba(255, 255, 255, 0.00) 18%),
              linear-gradient(90deg, rgba(255, 255, 255, 0.00) 52%, rgba(255, 255, 255, 0.018) 72%, rgba(255, 255, 255, 0.07) 98%);
            opacity: 0.82;
          }

          .tube-shell::after {
            content: "";
            position: absolute;
            inset: 6px 10px;
            border-radius: inherit;
            background:
              linear-gradient(90deg, rgba(255, 255, 255, 0.10) 0 2%, rgba(255, 255, 255, 0.00) 11%, rgba(255, 255, 255, 0.00) 74%, rgba(255, 255, 255, 0.06) 95%, rgba(255, 255, 255, 0.00) 100%);
            opacity: 0.42;
            mix-blend-mode: screen;
          }

          .symbol-slot .tube-shell {
            inset: 4px 5px 4px;
          }

          .symbol-minus .tube-shell {
            inset: 4px 7px 4px;
          }

          .cathode,
          .symbol-glyph {
            position: absolute;
            inset: 10px 12px;
            display: grid;
            place-items: center;
            transform-style: preserve-3d;
            z-index: 2;
          }

          .symbol-slot .symbol-glyph {
            inset: 10px 7px;
          }

          .symbol-minus .symbol-glyph {
            inset: 10px 12px;
          }

          .cathode {
            transform: translate(var(--dx), var(--dy)) scale(var(--scale));
            opacity: var(--gt-inactive-opacity);
            transition:
              opacity 220ms ease,
              filter 220ms ease,
              transform 220ms ease;
            will-change: transform, opacity, filter;
          }

          .cathode.active {
            opacity: 1;
            filter: saturate(1.06) brightness(1.08);
          }

          .cathode.active.ignite,
          .symbol-glyph.ignite {
            animation: gt-ignite 240ms cubic-bezier(0.24, 0.82, 0.25, 1);
          }

          .glyph {
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            overflow: visible;
          }

          .trace {
            fill: none;
            stroke-linecap: round;
            stroke-linejoin: round;
            vector-effect: non-scaling-stroke;
          }

          .metal-pass,
          .gas-pass,
          .glow-pass,
          .core-pass {
            transition: opacity 220ms ease, filter 220ms ease;
          }

          .metal-pass {
            opacity: 0.48;
            filter: drop-shadow(0 0 0.6px rgba(var(--gt-wire-highlight-rgb), 0.10));
          }

          .metal-pass .trace {
            stroke: rgba(var(--gt-wire-rgb), 0.84);
            stroke-width: 4.2;
          }

          .metal-pass .dot-fill {
            fill: rgba(var(--gt-wire-rgb), 0.84);
          }

          .gas-pass {
            opacity: 0;
            filter: blur(7px);
          }

          .gas-pass .trace {
            stroke: rgba(var(--gt-neon-rgb), 0.38);
            stroke-width: 15.2;
          }

          .gas-pass .dot-fill {
            fill: rgba(var(--gt-neon-rgb), 0.38);
          }

          .glow-pass {
            opacity: 0;
            filter:
              blur(2.8px)
              drop-shadow(0 0 7px rgba(var(--gt-neon-rgb), 0.64))
              drop-shadow(0 0 15px rgba(var(--gt-neon-rgb), 0.22));
          }

          .glow-pass .trace {
            stroke: rgba(var(--gt-neon-rgb), 0.99);
            stroke-width: 6.6;
          }

          .glow-pass .dot-fill {
            fill: rgba(var(--gt-neon-rgb), 0.98);
          }

          .core-pass {
            opacity: 0;
            filter:
              drop-shadow(0 0 1.6px rgba(var(--gt-core-rgb), 0.62))
              drop-shadow(0 0 3.6px rgba(var(--gt-neon-rgb), 0.14));
          }

          .core-pass .trace {
            stroke: rgba(var(--gt-core-rgb), 0.98);
            stroke-width: 2.35;
          }

          .core-pass .dot-fill {
            fill: rgba(var(--gt-core-rgb), 0.98);
          }

          .cathode.active .metal-pass {
            opacity: 0.20;
          }

          .cathode.active .gas-pass {
            opacity: 0.84;
          }

          .cathode.active .glow-pass {
            opacity: 0.86;
          }

          .cathode.active .core-pass {
            opacity: 1;
          }

          .symbol-glyph {
            opacity: 1;
            filter: saturate(1.06) brightness(1.06);
          }

          .symbol-glyph .metal-pass {
            opacity: 0.20;
          }

          .symbol-glyph .gas-pass {
            opacity: 0.82;
          }

          .symbol-glyph .glow-pass {
            opacity: 0.86;
          }

          .symbol-glyph .core-pass {
            opacity: 1;
          }

          .mesh-underlight,
          .mesh-host {
            position: absolute;
            inset: calc(var(--gt-padding) - 2px);
            pointer-events: none;
          }

          .mesh-underlight {
            z-index: 5;
            opacity: 0.16;
            filter: blur(9px);
            mix-blend-mode: screen;
          }

          .mesh-host {
            z-index: 6;
          }

          .mesh-svg {
            width: 100%;
            height: 100%;
            overflow: visible;
            shape-rendering: geometricPrecision;
            filter:
              drop-shadow(0 0.35px 0 rgba(255, 255, 255, 0.02))
              drop-shadow(0 1.1px 1.8px rgba(0, 0, 0, 0.34));
          }

          .mesh-reactive {
            opacity: calc(var(--gt-mesh-opacity) * 0.32);
          }

          .mesh-shadow-fill {
            opacity: calc(var(--gt-mesh-opacity) * 0.34);
          }

          .mesh-body-fill {
            opacity: calc(var(--gt-mesh-opacity) * 0.62);
          }

          .mesh-shine-fill {
            opacity: calc(var(--gt-mesh-opacity) * 0.10);
          }

          @keyframes gt-ignite {
            0% {
              opacity: 0.54;
              filter: saturate(0.76) brightness(0.74);
            }
            48% {
              opacity: 1.10;
              filter: saturate(1.18) brightness(1.22);
            }
            100% {
              opacity: 1;
              filter: saturate(1.06) brightness(1.08);
            }
          }

          @media (prefers-reduced-motion: reduce) {
            .cathode,
            .symbol-glyph,
            .ambient,
            .metal-pass,
            .gas-pass,
            .glow-pass,
            .core-pass {
              transition: none;
              animation: none;
            }
          }
        </style>

        <div class="display" part="display" role="img" aria-label="${escapeHTML(value)}">
          <div class="display-shell" part="frame">
            <div class="window" part="window">
              <div class="row" part="row">
                ${slots}
              </div>
              <div class="mesh-underlight" aria-hidden="true"></div>
              <div class="mesh-host" aria-hidden="true"></div>
            </div>
          </div>
        </div>
      `;

      const windowEl = this.shadowRoot.querySelector(".window");
      if (this._resizeObserver && windowEl) {
        this._resizeObserver.disconnect();
        this._resizeObserver.observe(windowEl);
      }

      this.updateValue(true);
      this._scheduleDecorRefresh();
    }

    updateValue(initial = false) {
      const chars = Array.from(this.value || "");
      const slotNodes = Array.from(this.shadowRoot.querySelectorAll("[data-kind]"));

      slotNodes.forEach((slotNode, index) => {
        const char = chars[index] ?? " ";
        const kind = slotNode.dataset.kind;

        if (kind === "digit") {
          let lit = false;
          const cathodes = slotNode.querySelectorAll(".cathode");

          cathodes.forEach((cathode) => {
            const active = cathode.dataset.char === char;
            cathode.classList.toggle("active", active);
            if (active) lit = true;

            if (active && !initial) {
              cathode.classList.remove("ignite");
              void cathode.offsetWidth;
              cathode.classList.add("ignite");
            }
          });

          slotNode.classList.toggle("lit", lit);
        } else if (kind === "symbol") {
          const lit = categoryOf(char) === "symbol";
          slotNode.classList.toggle("lit", lit);
          const glyphRoot = slotNode.querySelector(".symbol-glyph");
          if (lit && glyphRoot && !initial) {
            glyphRoot.classList.remove("ignite");
            void glyphRoot.offsetWidth;
            glyphRoot.classList.add("ignite");
          }
        }
      });

      this.setAttribute("aria-label", this.value || "");
      this._scheduleDecorRefresh();
    }
  }

  if (!customElements.get("glow-tube-display")) {
    customElements.define("glow-tube-display", GlowTubeDisplay);
  }
})();
