"use strict";
(function () {
  const DATA = window.CODEMAP;
  if (!DATA) {
    document.body.insertAdjacentHTML(
      "beforeend",
      '<div class="missing">codemap.data.js is missing. Run python3 tools/codemap/generate_codemap.py</div>'
    );
    return;
  }

  const THEME_KEY = "radon-codemap-theme";
  const nodes = DATA.nodes;
  const edges = DATA.edges;
  const groups = DATA.groups;
  const N = nodes.length;
  const E = edges.length;
  const testCount = nodes.filter((n) => n.test).length;

  const outAdj = new Map();
  const inAdj = new Map();
  edges.forEach(([s, t]) => {
    (outAdj.get(s) || outAdj.set(s, []).get(s)).push(t);
    (inAdj.get(t) || inAdj.set(t, []).get(t)).push(s);
  });

  function hexRgb(name) {
    let value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    if (value.startsWith("rgb")) {
      const parts = value.match(/[\d.]+/g) || ["0", "0", "0"];
      return parts.slice(0, 3).map((n) => Number(n) / 255);
    }
    if (value[0] === "#" && value.length === 4) {
      value = "#" + value[1] + value[1] + value[2] + value[2] + value[3] + value[3];
    }
    if (value[0] === "#" && value.length >= 7) {
      const n = parseInt(value.slice(1, 7), 16);
      return [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
    }
    return [0.5, 0.5, 0.5];
  }

  function palette() {
    return {
      canvas: hexRgb("--bg-canvas"),
      web: hexRgb("--area-web"),
      scripts: hexRgb("--area-scripts"),
      site: hexRgb("--area-site"),
      cloud: hexRgb("--area-cloud"),
      lib: hexRgb("--area-lib"),
      tests: hexRgb("--area-tests"),
      tools: hexRgb("--area-tools"),
      out: hexRgb("--signal-strong"),
      inn: hexRgb("--warn"),
    };
  }

  function cssHex(name) {
    const rgb = hexRgb(name);
    const to = (c) => Math.round(c * 255).toString(16).padStart(2, "0");
    return "#" + to(rgb[0]) + to(rgb[1]) + to(rgb[2]);
  }

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
  }

  const themeParam = new URLSearchParams(location.search).get("theme");
  const stored = themeParam || localStorage.getItem(THEME_KEY);
  applyTheme(stored === "dark" || stored === "light" ? stored : "light");

  const areaOn = {};
  Object.keys(DATA.meta.areas).forEach((area) => {
    areaOn[area] = true;
  });
  let showTests = true;
  let selected = -1;
  let hovered = -1;
  const visible = new Uint8Array(N);

  function nodeArea(i) {
    return groups[nodes[i].g].area;
  }
  function recomputeVisible() {
    for (let i = 0; i < N; i++) {
      visible[i] = areaOn[nodeArea(i)] && (showTests || !nodes[i].test) ? 1 : 0;
    }
  }

  function formatCount(n) {
    return n.toLocaleString("en-US");
  }
  document.getElementById("m-files").textContent = formatCount(N);
  document.getElementById("m-edges").textContent = formatCount(E);
  document.getElementById("m-product").textContent = formatCount(N - testCount);
  document.getElementById("m-tests").textContent = formatCount(testCount);
  document.getElementById("m-groups").textContent = formatCount(groups.length);
  document.getElementById("meta-footer").textContent =
    "Generated " + DATA.meta.generated_at + ". python3 tools/codemap/generate_codemap.py";

  const canvas = document.getElementById("gl");
  const gl = canvas.getContext("webgl", { antialias: true, alpha: false, failIfMajorPerformanceCaveat: false })
    || canvas.getContext("experimental-webgl");
  if (!gl) {
    canvas.parentElement.insertAdjacentHTML(
      "beforeend",
      '<div class="missing">WebGL is unavailable in this browser.</div>'
    );
    return;
  }

  function compile(type, src) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, src);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    return shader;
  }
  function program(vs, fs) {
    const p = gl.createProgram();
    gl.attachShader(p, compile(gl.VERTEX_SHADER, vs));
    gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    return p;
  }

  const VS_COMMON =
    "uniform vec2 u_cam; uniform float u_scale; uniform vec2 u_half;" +
    "vec2 project(vec2 pos) { return (pos - u_cam) * u_scale / u_half * vec2(1.0, -1.0); }";
  const nodeProg = program(
    VS_COMMON +
      "attribute vec2 a_pos; attribute vec4 a_color; attribute float a_size; varying vec4 v_color;" +
      "void main() { gl_Position = vec4(project(a_pos), 0.0, 1.0); gl_PointSize = a_size; v_color = a_color; }",
    "precision mediump float; varying vec4 v_color;" +
      "void main() { vec2 d = gl_PointCoord - vec2(0.5); float r = length(d);" +
      "float a = 1.0 - smoothstep(0.42, 0.5, r); if (a <= 0.0) discard;" +
      "gl_FragColor = vec4(v_color.rgb, v_color.a * a); }"
  );
  const edgeProg = program(
    VS_COMMON +
      "attribute vec2 a_pos; attribute vec4 a_color; varying vec4 v_color;" +
      "void main() { gl_Position = vec4(project(a_pos), 0.0, 1.0); v_color = a_color; }",
    "precision mediump float; varying vec4 v_color; void main() { gl_FragColor = v_color; }"
  );

  const nodePos = new Float32Array(N * 2);
  for (let i = 0; i < N; i++) {
    nodePos[i * 2] = nodes[i].x;
    nodePos[i * 2 + 1] = nodes[i].y;
  }
  const nodeColor = new Float32Array(N * 4);
  const nodeSize = new Float32Array(N);
  const edgePos = new Float32Array(E * 4);
  for (let e = 0; e < E; e++) {
    const [s, t] = edges[e];
    edgePos[e * 4] = nodes[s].x;
    edgePos[e * 4 + 1] = nodes[s].y;
    edgePos[e * 4 + 2] = nodes[t].x;
    edgePos[e * 4 + 3] = nodes[t].y;
  }
  const edgeColor = new Float32Array(E * 8);

  function makeBuffer(data, dynamic) {
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, data, dynamic ? gl.DYNAMIC_DRAW : gl.STATIC_DRAW);
    return buffer;
  }
  const bNodePos = makeBuffer(nodePos, false);
  const bNodeColor = makeBuffer(nodeColor, true);
  const bNodeSize = makeBuffer(nodeSize, true);
  const bEdgePos = makeBuffer(edgePos, false);
  const bEdgeColor = makeBuffer(edgeColor, true);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

  const dpr = window.devicePixelRatio || 1;
  const cam = { x: 0, y: 0, scale: 1 };
  let width = 0;
  let height = 0;

  function canvasPoint(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    return [clientX - rect.left, clientY - rect.top];
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    width = Math.max(1, rect.width);
    height = Math.max(1, rect.height);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    gl.viewport(0, 0, canvas.width, canvas.height);
    requestDraw();
  }

  function fitView() {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (let i = 0; i < N; i++) {
      minX = Math.min(minX, nodePos[i * 2]);
      maxX = Math.max(maxX, nodePos[i * 2]);
      minY = Math.min(minY, nodePos[i * 2 + 1]);
      maxY = Math.max(maxY, nodePos[i * 2 + 1]);
    }
    cam.x = (minX + maxX) / 2;
    cam.y = (minY + maxY) / 2;
    cam.scale = 0.9 * Math.min(width / (maxX - minX + 1), height / (maxY - minY + 1));
    requestDraw();
  }

  function screenToWorld(sx, sy) {
    return [(sx - width / 2) / cam.scale + cam.x, (sy - height / 2) / cam.scale + cam.y];
  }
  function worldToScreen(wx, wy) {
    return [(wx - cam.x) * cam.scale + width / 2, (wy - cam.y) * cam.scale + height / 2];
  }

  const CELL = 40;
  const grid = new Map();
  for (let i = 0; i < N; i++) {
    const key = Math.floor(nodePos[i * 2] / CELL) + ":" + Math.floor(nodePos[i * 2 + 1] / CELL);
    (grid.get(key) || grid.set(key, []).get(key)).push(i);
  }
  function pick(sx, sy) {
    const [wx, wy] = screenToWorld(sx, sy);
    const tol = 12 / cam.scale;
    const cx = Math.floor(wx / CELL);
    const cy = Math.floor(wy / CELL);
    let best = -1;
    let bestD = tol * tol;
    const reach = Math.max(1, Math.ceil(tol / CELL));
    for (let dx = -reach; dx <= reach; dx++) {
      for (let dy = -reach; dy <= reach; dy++) {
        const cell = grid.get(cx + dx + ":" + (cy + dy));
        if (!cell) continue;
        for (const i of cell) {
          if (!visible[i]) continue;
          const ddx = nodePos[i * 2] - wx;
          const ddy = nodePos[i * 2 + 1] - wy;
          const d = ddx * ddx + ddy * ddy;
          if (d < bestD) {
            bestD = d;
            best = i;
          }
        }
      }
    }
    return best;
  }

  function restyle() {
    const colors = palette();
    const neighborsOut = new Set(selected >= 0 ? outAdj.get(selected) || [] : []);
    const neighborsIn = new Set(selected >= 0 ? inAdj.get(selected) || [] : []);
    const hasSel = selected >= 0;
    const dim = currentTheme() === "light" ? 0.14 : 0.07;
    const idleEdge = currentTheme() === "light" ? 0.16 : 0.1;

    for (let i = 0; i < N; i++) {
      const n = nodes[i];
      const base = colors[nodeArea(i)] || colors.tests;
      let a = n.test ? 0.55 : 0.92;
      let size = 3 + Math.sqrt(n.in + n.out) * 1.5;
      if (!visible[i]) a = 0;
      else if (hasSel) {
        if (i === selected) {
          a = 1;
          size += 4;
        } else if (neighborsOut.has(i) || neighborsIn.has(i)) a = 1;
        else a = dim;
      }
      if (i === hovered && visible[i]) {
        a = 1;
        size += 2;
      }
      const zoomBoost = Math.pow(Math.max(cam.scale, 0.02), 0.3);
      nodeSize[i] = Math.min(26, Math.max(2.5, size * zoomBoost)) * dpr;
      nodeColor[i * 4] = base[0];
      nodeColor[i * 4 + 1] = base[1];
      nodeColor[i * 4 + 2] = base[2];
      nodeColor[i * 4 + 3] = a;
    }

    for (let e = 0; e < E; e++) {
      const [s, t] = edges[e];
      let cs;
      let ct;
      let a;
      if (!visible[s] || !visible[t]) {
        a = 0;
        cs = ct = [0, 0, 0];
      } else if (hasSel) {
        if (s === selected) {
          cs = ct = colors.out;
          a = 0.85;
        } else if (t === selected) {
          cs = ct = colors.inn;
          a = 0.85;
        } else {
          cs = ct = colors.tests;
          a = currentTheme() === "light" ? 0.04 : 0.02;
        }
      } else {
        cs = colors[nodeArea(s)] || colors.tests;
        ct = colors[nodeArea(t)] || colors.tests;
        a = idleEdge;
      }
      edgeColor[e * 8] = cs[0];
      edgeColor[e * 8 + 1] = cs[1];
      edgeColor[e * 8 + 2] = cs[2];
      edgeColor[e * 8 + 3] = a;
      edgeColor[e * 8 + 4] = ct[0];
      edgeColor[e * 8 + 5] = ct[1];
      edgeColor[e * 8 + 6] = ct[2];
      edgeColor[e * 8 + 7] = a;
    }

    gl.bindBuffer(gl.ARRAY_BUFFER, bNodeColor);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, nodeColor);
    gl.bindBuffer(gl.ARRAY_BUFFER, bNodeSize);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, nodeSize);
    gl.bindBuffer(gl.ARRAY_BUFFER, bEdgeColor);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, edgeColor);
    requestDraw();
  }

  let drawQueued = false;
  function requestDraw() {
    if (drawQueued) return;
    drawQueued = true;
    requestAnimationFrame(draw);
  }
  function bindAttr(prog, name, buffer, size) {
    const loc = gl.getAttribLocation(prog, name);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
  }
  function setUniforms(prog) {
    gl.uniform2f(gl.getUniformLocation(prog, "u_cam"), cam.x, cam.y);
    gl.uniform1f(gl.getUniformLocation(prog, "u_scale"), cam.scale);
    gl.uniform2f(gl.getUniformLocation(prog, "u_half"), width / 2, height / 2);
  }
  function draw() {
    drawQueued = false;
    const bg = palette().canvas;
    gl.clearColor(bg[0], bg[1], bg[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(edgeProg);
    setUniforms(edgeProg);
    bindAttr(edgeProg, "a_pos", bEdgePos, 2);
    bindAttr(edgeProg, "a_color", bEdgeColor, 4);
    gl.drawArrays(gl.LINES, 0, E * 2);
    gl.useProgram(nodeProg);
    setUniforms(nodeProg);
    bindAttr(nodeProg, "a_pos", bNodePos, 2);
    bindAttr(nodeProg, "a_color", bNodeColor, 4);
    bindAttr(nodeProg, "a_size", bNodeSize, 1);
    gl.drawArrays(gl.POINTS, 0, N);
    positionLabels();
  }

  const labelHost = document.getElementById("labels");
  const labelEls = groups.map((grp) => {
    const el = document.createElement("div");
    el.className = "group-label";
    el.textContent = grp.id + " · " + grp.count;
    labelHost.appendChild(el);
    return el;
  });
  function positionLabels() {
    for (let i = 0; i < groups.length; i++) {
      const grp = groups[i];
      const el = labelEls[i];
      if (!areaOn[grp.area]) {
        el.style.display = "none";
        continue;
      }
      const rPx = grp.r * cam.scale;
      if (rPx < 28) {
        el.style.display = "none";
        continue;
      }
      const [sx, sy] = worldToScreen(grp.x, grp.y - grp.r * 1.02);
      if (sx < -200 || sx > width + 200 || sy < -50 || sy > height + 50) {
        el.style.display = "none";
        continue;
      }
      el.style.display = "block";
      el.style.left = sx + "px";
      el.style.top = sy + "px";
      el.style.opacity = String(Math.min(1, (rPx - 28) / 40));
    }
  }

  const tooltip = document.getElementById("tooltip");
  let dragging = false;
  let moved = false;
  let lastX = 0;
  let lastY = 0;

  canvas.addEventListener("mousedown", (ev) => {
    dragging = true;
    moved = false;
    lastX = ev.clientX;
    lastY = ev.clientY;
    canvas.classList.add("dragging");
  });
  window.addEventListener("mouseup", (ev) => {
    canvas.classList.remove("dragging");
    if (dragging && !moved && ev.target === canvas) {
      const [sx, sy] = canvasPoint(ev.clientX, ev.clientY);
      select(pick(sx, sy));
    }
    dragging = false;
  });
  window.addEventListener("mousemove", (ev) => {
    if (dragging) {
      const dx = ev.clientX - lastX;
      const dy = ev.clientY - lastY;
      if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
      cam.x -= dx / cam.scale;
      cam.y -= dy / cam.scale;
      lastX = ev.clientX;
      lastY = ev.clientY;
      requestDraw();
      return;
    }
    if (ev.target !== canvas) {
      setHover(-1, ev);
      return;
    }
    const [sx, sy] = canvasPoint(ev.clientX, ev.clientY);
    setHover(pick(sx, sy), ev);
  });
  function setHover(i, ev) {
    if (i !== hovered) {
      hovered = i;
      restyle();
    }
    if (i >= 0) {
      tooltip.style.display = "block";
      tooltip.textContent =
        nodes[i].id + "  (" + nodes[i].loc + " loc · in " + nodes[i].in + " · out " + nodes[i].out + ")";
      tooltip.style.left = Math.min(ev.clientX + 14, window.innerWidth - 440) + "px";
      tooltip.style.top = ev.clientY + 14 + "px";
    } else {
      tooltip.style.display = "none";
    }
  }

  let touchMode = null;
  let touchMoved = false;
  let lastTouch = null;
  let lastPinch = null;
  function touchPoints(ev) {
    return Array.from(ev.touches).map((t) => [t.clientX, t.clientY]);
  }
  canvas.addEventListener(
    "touchstart",
    (ev) => {
      ev.preventDefault();
      const pts = touchPoints(ev);
      if (pts.length === 1) {
        touchMode = "pan";
        touchMoved = false;
        lastTouch = pts[0];
      } else if (pts.length >= 2) {
        touchMode = "pinch";
        touchMoved = true;
        const dx = pts[1][0] - pts[0][0];
        const dy = pts[1][1] - pts[0][1];
        lastPinch = { dist: Math.hypot(dx, dy), cx: (pts[0][0] + pts[1][0]) / 2, cy: (pts[0][1] + pts[1][1]) / 2 };
      }
    },
    { passive: false }
  );
  canvas.addEventListener(
    "touchmove",
    (ev) => {
      ev.preventDefault();
      const pts = touchPoints(ev);
      if (touchMode === "pan" && pts.length === 1) {
        const dx = pts[0][0] - lastTouch[0];
        const dy = pts[0][1] - lastTouch[1];
        if (Math.abs(dx) + Math.abs(dy) > 4) touchMoved = true;
        cam.x -= dx / cam.scale;
        cam.y -= dy / cam.scale;
        lastTouch = pts[0];
        requestDraw();
      } else if (pts.length >= 2) {
        touchMode = "pinch";
        const dx = pts[1][0] - pts[0][0];
        const dy = pts[1][1] - pts[0][1];
        const pinch = { dist: Math.hypot(dx, dy), cx: (pts[0][0] + pts[1][0]) / 2, cy: (pts[0][1] + pts[1][1]) / 2 };
        if (lastPinch && lastPinch.dist > 0) {
          const [px, py] = canvasPoint(pinch.cx, pinch.cy);
          const [wx, wy] = screenToWorld(px, py);
          cam.scale = Math.min(60, Math.max(0.01, cam.scale * (pinch.dist / lastPinch.dist)));
          const [wx2, wy2] = screenToWorld(px, py);
          cam.x += wx - wx2;
          cam.y += wy - wy2;
          restyle();
        }
        lastPinch = pinch;
      }
    },
    { passive: false }
  );
  canvas.addEventListener(
    "touchend",
    (ev) => {
      ev.preventDefault();
      if (ev.touches.length === 0) {
        if (touchMode === "pan" && !touchMoved && lastTouch) {
          const [sx, sy] = canvasPoint(lastTouch[0], lastTouch[1]);
          select(pick(sx, sy));
        }
        touchMode = null;
        lastTouch = null;
        lastPinch = null;
      } else if (ev.touches.length === 1) {
        touchMode = "pan";
        touchMoved = true;
        lastTouch = touchPoints(ev)[0];
        lastPinch = null;
      }
    },
    { passive: false }
  );

  canvas.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      const [sx, sy] = canvasPoint(ev.clientX, ev.clientY);
      const factor = Math.exp(-ev.deltaY * 0.0016);
      const [wx, wy] = screenToWorld(sx, sy);
      cam.scale = Math.min(60, Math.max(0.01, cam.scale * factor));
      const [wx2, wy2] = screenToWorld(sx, sy);
      cam.x += wx - wx2;
      cam.y += wy - wy2;
      restyle();
    },
    { passive: false }
  );

  const selEmpty = document.getElementById("sel-empty");
  const selDetail = document.getElementById("sel-detail");
  function depRow(i) {
    const el = document.createElement("button");
    el.type = "button";
    el.textContent = nodes[i].id;
    el.style.color = cssHex("--area-" + nodeArea(i));
    el.addEventListener("click", () => {
      select(i);
      centerOn(i);
    });
    return el;
  }
  const filterScroll = document.querySelector(".inspector-scroll");
  function select(i) {
    selected = i;
    if (filterScroll) filterScroll.classList.toggle("compact", i >= 0);
    if (i < 0) {
      selEmpty.hidden = false;
      selDetail.hidden = true;
    } else {
      const n = nodes[i];
      selEmpty.hidden = true;
      selDetail.hidden = false;
      document.getElementById("sel-path").textContent = n.id;
      document.getElementById("sel-stats").textContent =
        n.lang + " · " + n.loc + " loc · " + (n.test ? "test file" : "product file");
      const outs = (outAdj.get(i) || []).slice().sort((a, b) => nodes[a].id.localeCompare(nodes[b].id));
      const ins = (inAdj.get(i) || []).slice().sort((a, b) => nodes[a].id.localeCompare(nodes[b].id));
      document.getElementById("out-count").textContent = "· " + outs.length;
      document.getElementById("in-count").textContent = "· " + ins.length;
      const outHost = document.getElementById("sel-out");
      const inHost = document.getElementById("sel-in");
      outHost.replaceChildren();
      inHost.replaceChildren();
      outs.forEach((d) => outHost.appendChild(depRow(d)));
      ins.forEach((d) => inHost.appendChild(depRow(d)));
    }
    restyle();
  }
  function centerOn(i) {
    cam.x = nodes[i].x;
    cam.y = nodes[i].y;
    cam.scale = Math.max(cam.scale, 4);
    restyle();
  }

  const searchInput = document.getElementById("search");
  const searchResults = document.getElementById("search-results");
  searchInput.addEventListener("input", () => {
    const q = searchInput.value.trim().toLowerCase();
    searchResults.replaceChildren();
    if (q.length < 2) return;
    let shown = 0;
    for (let i = 0; i < N && shown < 20; i++) {
      if (nodes[i].id.toLowerCase().includes(q)) {
        const el = document.createElement("button");
        el.type = "button";
        el.textContent = nodes[i].id;
        el.addEventListener("click", () => {
          select(i);
          centerOn(i);
        });
        searchResults.appendChild(el);
        shown++;
      }
    }
  });

  const legend = document.getElementById("legend");
  Object.keys(DATA.meta.areas).forEach((area) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "legend-row";
    row.innerHTML =
      '<span class="swatch"></span><span>' +
      area +
      '</span><span class="count">' +
      DATA.meta.areas[area] +
      "</span>";
    row.querySelector(".swatch").style.background = "var(--area-" + area + ")";
    row.addEventListener("click", () => {
      areaOn[area] = !areaOn[area];
      row.classList.toggle("off", !areaOn[area]);
      recomputeVisible();
      restyle();
    });
    legend.appendChild(row);
  });

  const testsBox = document.getElementById("tests-box");
  document.getElementById("toggle-tests").addEventListener("click", () => {
    showTests = !showTests;
    testsBox.textContent = showTests ? "✓" : "";
    recomputeVisible();
    restyle();
  });
  document.getElementById("tests-count").textContent = String(testCount);
  document.getElementById("reset").addEventListener("click", () => {
    select(-1);
    fitView();
    restyle();
  });

  document.getElementById("theme-toggle").addEventListener("click", () => {
    applyTheme(currentTheme() === "dark" ? "light" : "dark");
    restyle();
  });

  const inspector = document.getElementById("inspector");
  const inspectorToggle = document.getElementById("inspector-toggle");
  inspectorToggle.addEventListener("click", () => {
    inspector.classList.toggle("collapsed");
    inspectorToggle.setAttribute("aria-expanded", inspector.classList.contains("collapsed") ? "false" : "true");
  });
  if (window.innerWidth <= 900) inspector.classList.add("collapsed");

  window.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") select(-1);
    if ((ev.key === "/" || ev.key === "f") && document.activeElement !== searchInput) {
      ev.preventDefault();
      searchInput.focus();
    }
  });

  const stage = canvas.parentElement;
  if (window.ResizeObserver) new ResizeObserver(resize).observe(stage);
  else window.addEventListener("resize", resize);

  resize();
  fitView();
  recomputeVisible();
  restyle();
  const preselect = new URLSearchParams(location.search).get("select");
  if (preselect) {
    const idx = nodes.findIndex((n) => n.id === preselect);
    if (idx >= 0) {
      select(idx);
      centerOn(idx);
    }
  }
})();
