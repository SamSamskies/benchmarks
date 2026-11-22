(function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

  const state = {
    data: null,
    scope: "overall", // "overall" or task.id
    highlightedModel: null,
  };

  function el(tag, attrs, text) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v !== undefined && v !== null) node.setAttribute(k, String(v));
      }
    }
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function svgEl(tag, attrs, text) {
    const node = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v !== undefined && v !== null) node.setAttribute(k, String(v));
      }
    }
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function formatSeconds(sec) {
    if (!Number.isFinite(sec)) return "—";
    if (sec >= 100) return `${Math.round(sec)}s`;
    return `${sec.toFixed(1)}s`;
  }

  function scoreClass(score) {
    if (score >= 99.9) return "pass";
    if (score >= 50) return "warn";
    return "fail";
  }

  function getTaskEntry(model, taskId) {
    return (model.tasks || []).find((t) => t.id === taskId) || null;
  }

  function getScopedMetrics(model) {
    if (state.scope === "overall") {
      return {
        score: model.summary.intelligence_index,
        time: model.summary.mean_time_per_task_s,
        tps: model.summary.mean_tokens_per_sec,
        checksPassed: (model.tasks || []).reduce((acc, t) => acc + (t.grade?.passed_checks || 0), 0),
        checksTotal: (model.tasks || []).reduce((acc, t) => acc + (t.grade?.total_checks || 0), 0),
      };
    }
    const task = getTaskEntry(model, state.scope);
    if (!task) {
      return { score: 0, time: 0, tps: 0, checksPassed: 0, checksTotal: 10 };
    }
    return {
      score: task.grade?.score ?? 0,
      time: task.inference_time_s ?? 0,
      tps: task.tokens_per_sec ?? 0,
      checksPassed: task.grade?.passed_checks ?? 0,
      checksTotal: task.grade?.total_checks ?? 10,
    };
  }

  function initTheme() {
    const saved = localStorage.getItem("local-eval-theme");
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const initial = saved || (prefersDark ? "dark" : "light");
    applyTheme(initial);

    const btn = document.getElementById("theme-toggle");
    btn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
      localStorage.setItem("local-eval-theme", next);
    });
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const label = document.querySelector("#theme-toggle .theme-label");
    if (label) label.textContent = theme === "dark" ? "Light" : "Dark";
  }

  function roundedTopBarPath(x, y, w, h, r) {
    if (h <= 0) return `M ${x} ${y} H ${x + w}`;
    const radius = Math.min(r, w / 2, h);
    return [
      `M ${x} ${y + h}`,
      `V ${y + radius}`,
      `Q ${x} ${y} ${x + radius} ${y}`,
      `H ${x + w - radius}`,
      `Q ${x + w} ${y} ${x + w} ${y + radius}`,
      `V ${y + h}`,
      "Z",
    ].join(" ");
  }

  function niceMaxAndTicks(maxVal, targetTicks = 4) {
    if (maxVal <= 0) return { max: 10, ticks: [0, 2.5, 5, 7.5, 10] };
    const roughStep = maxVal / targetTicks;
    const mag = Math.pow(10, Math.floor(Math.log10(roughStep)));
    const norm = roughStep / mag;
    let step = mag;
    if (norm > 5) step = 10 * mag;
    else if (norm > 2) step = 5 * mag;
    else if (norm > 1) step = 2 * mag;

    const niceMax = Math.ceil((maxVal * 1.08) / step) * step;
    const ticks = [];
    for (let v = 0; v <= niceMax + step * 0.001; v += step) {
      ticks.push(Math.round(v * 100) / 100);
    }
    return { max: niceMax, ticks };
  }

  function entryKey(model) {
    return model.entry_id || model.model;
  }

  function showTooltip(evt, model, metrics) {
    const tip = document.getElementById("chart-tooltip");
    tip.innerHTML = "";
    tip.appendChild(el("div", { class: "tooltip-title" }, model.display_name || model.model));

    const rows = [
      ["Score", `${metrics.score.toFixed(1)} / 100 (${metrics.checksPassed}/${metrics.checksTotal})`],
      ["Time / Task", `${metrics.time.toFixed(2)}s`],
      ["Decode Speed", `${metrics.tps.toFixed(1)} tok/s`],
    ];
    if (model.think_level) {
      rows.push(["Thinking", model.think_level]);
    }
    if (model.model_info?.parameter_size) {
      rows.push(["Size / Quant", `${model.model_info.parameter_size} · ${model.model_info.quantization_level || "—"}`]);
    }
    for (const [k, v] of rows) {
      const row = el("div", { class: "tooltip-row" });
      row.appendChild(el("span", null, k));
      row.appendChild(el("strong", null, v));
      tip.appendChild(row);
    }

    tip.hidden = false;
    positionTooltip(evt);
  }

  function positionTooltip(evt) {
    const tip = document.getElementById("chart-tooltip");
    if (tip.hidden) return;
    const pad = 14;
    const rect = tip.getBoundingClientRect();
    let x = evt.clientX + pad;
    let y = evt.clientY - rect.height - pad;
    if (x + rect.width > window.innerWidth - 10) x = evt.clientX - rect.width - pad;
    if (y < 10) y = evt.clientY + pad;
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
  }

  function hideTooltip() {
    document.getElementById("chart-tooltip").hidden = true;
  }

  function toggleHighlight(modelName) {
    state.highlightedModel = state.highlightedModel === modelName ? null : modelName;
    renderCharts();
    syncRowHighlight();
  }

  function syncRowHighlight() {
    const rows = document.querySelectorAll("#table-body tr");
    for (const tr of rows) {
      tr.classList.toggle("row-highlighted", tr.dataset.model === state.highlightedModel);
    }
  }

  function renderBarChart({ containerId, models, metricKey, valueFormatter, sortDesc, fixedMax }) {
    const container = document.getElementById(containerId);
    container.innerHTML = "";

    const items = models
      .map((m) => ({ model: m, metrics: getScopedMetrics(m) }))
      .sort((a, b) => {
        const diff = a.metrics[metricKey] - b.metrics[metricKey];
        if (Math.abs(diff) > 1e-9) return sortDesc ? -diff : diff;
        return collator.compare(entryKey(a.model), entryKey(b.model));
      });

    const rawMax = Math.max(...items.map((it) => it.metrics[metricKey]), 0);
    const scale = fixedMax
      ? { max: fixedMax, ticks: [0, 25, 50, 75, 100] }
      : niceMaxAndTicks(rawMax, 4);

    const n = items.length;
    const chartWidth = Math.max(540, n * 96 + 80);
    const margin = { top: 20, right: 20, bottom: n <= 3 ? 112 : 132, left: 42 };
    const plotW = chartWidth - margin.left - margin.right;
    const plotH = 225;
    const chartHeight = margin.top + plotH + margin.bottom;

    const svg = svgEl("svg", {
      viewBox: `0 0 ${chartWidth} ${chartHeight}`,
      class: "bar-chart-svg",
      role: "img",
      "aria-label": `${metricKey} comparison chart`,
    });

    // Horizontal dotted gridlines
    for (const tick of scale.ticks) {
      const y = margin.top + plotH - (tick / scale.max) * plotH;
      svg.appendChild(
        svgEl("line", {
          x1: margin.left,
          x2: chartWidth - margin.right,
          y1: y,
          y2: y,
          class: "grid-line",
        })
      );
      const labelTxt = fixedMax ? String(tick) : `${tick}s`;
      svg.appendChild(
        svgEl(
          "text",
          {
            x: margin.left - 8,
            y: y + 4,
            "text-anchor": "end",
            class: "axis-label",
          },
          labelTxt
        )
      );
    }

    const slotW = plotW / Math.max(1, n);
    const barW = Math.min(62, Math.max(28, slotW * 0.56));

    items.forEach(({ model, metrics }, idx) => {
      const val = metrics[metricKey];
      const barH = Math.max(0, (val / scale.max) * plotH);
      const cx = margin.left + slotW * (idx + 0.5);
      const bx = cx - barW / 2;
      const by = margin.top + plotH - barH;
      const color = model.brand?.color || "#6d4aff";

      const key = entryKey(model);
      const isDimmed = state.highlightedModel && state.highlightedModel !== key;
      const isHi = state.highlightedModel === key;
      const group = svgEl("g", {
        class: `bar-group${isDimmed ? " dimmed" : ""}${isHi ? " highlighted" : ""}`,
        tabindex: "0",
        role: "button",
        "aria-label": `${model.display_name || model.model}: ${valueFormatter(val)}`,
      });

      const barPath = svgEl("path", {
        d: roundedTopBarPath(bx, by, barW, barH, 8),
        fill: color,
        class: "bar-rect",
      });
      group.appendChild(barPath);

      // Value label centered inside bar (or above if bar is short)
      const labelInside = barH >= 32;
      group.appendChild(
        svgEl(
          "text",
          {
            x: cx,
            y: labelInside ? by + barH / 2 + 5 : by - 8,
            "text-anchor": "middle",
            class: labelInside ? "bar-value-inside" : "bar-value-above",
          },
          valueFormatter(val)
        )
      );

      // Family monogram badge directly under baseline (like Artificial Analysis icons)
      const badgeSize = 24;
      const badgeY = margin.top + plotH + 10;
      group.appendChild(
        svgEl("rect", {
          x: cx - badgeSize / 2,
          y: badgeY,
          width: badgeSize,
          height: badgeSize,
          rx: 6,
          fill: color,
          "fill-opacity": "0.14",
          stroke: color,
          "stroke-opacity": "0.35",
          "stroke-width": "1",
        })
      );
      group.appendChild(
        svgEl(
          "text",
          {
            x: cx,
            y: badgeY + 15.5,
            "text-anchor": "middle",
            fill: color,
            class: "monogram-text",
          },
          model.brand?.monogram || "LM"
        )
      );

      // Model label below badge (two-line upright when <= 3, tilted -36deg when >= 4 to prevent collisions)
      const labelY = badgeY + badgeSize + 15;
      const labelBase = model.display_base || model.base_name;
      if (n <= 3) {
        group.appendChild(
          svgEl(
            "text",
            {
              x: cx,
              y: labelY,
              "text-anchor": "middle",
              class: "bar-model-label",
            },
            labelBase
          )
        );
        if (model.tag_name) {
          group.appendChild(
            svgEl(
              "text",
              {
                x: cx,
                y: labelY + 15,
                "text-anchor": "middle",
                class: "bar-model-tag",
              },
              model.tag_name
            )
          );
        }
      } else {
        const tilted = svgEl(
          "text",
          {
            x: cx + 4,
            y: labelY,
            "text-anchor": "end",
            transform: `rotate(-36 ${cx + 4} ${labelY})`,
            class: "bar-model-label",
          },
          labelBase
        );
        group.appendChild(tilted);
      }

      group.addEventListener("mouseenter", (e) => showTooltip(e, model, metrics));
      group.addEventListener("mousemove", positionTooltip);
      group.addEventListener("mouseleave", hideTooltip);
      group.addEventListener("click", () => toggleHighlight(key));
      group.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggleHighlight(key);
        }
      });

      svg.appendChild(group);
    });

    container.appendChild(svg);
  }

  function renderFrontierChart(models) {
    const container = document.getElementById("frontier-chart");
    container.innerHTML = "";

    const points = models.map((m) => ({
      model: m,
      metrics: getScopedMetrics(m),
    }));

    const maxT = Math.max(...points.map((p) => p.metrics.time), 1);
    const xScale = niceMaxAndTicks(maxT, 4);
    const width = 560;
    const height = 250;
    const margin = { top: 20, right: 26, bottom: 40, left: 46 };
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;

    const svg = svgEl("svg", {
      viewBox: `0 0 ${width} ${height}`,
      class: "frontier-svg",
      role: "img",
      "aria-label": "Speed versus intelligence scatter plot",
    });

    // Y gridlines (0..100)
    for (const yVal of [0, 25, 50, 75, 100]) {
      const y = margin.top + plotH - (yVal / 100) * plotH;
      svg.appendChild(
        svgEl("line", {
          x1: margin.left,
          x2: width - margin.right,
          y1: y,
          y2: y,
          class: "grid-line",
        })
      );
      svg.appendChild(
        svgEl(
          "text",
          { x: margin.left - 8, y: y + 4, "text-anchor": "end", class: "axis-label" },
          String(yVal)
        )
      );
    }

    // X ticks
    for (const xVal of xScale.ticks) {
      const x = margin.left + (xVal / xScale.max) * plotW;
      svg.appendChild(
        svgEl("line", {
          x1: x,
          x2: x,
          y1: margin.top,
          y2: margin.top + plotH,
          class: "grid-line",
        })
      );
      svg.appendChild(
        svgEl(
          "text",
          { x, y: margin.top + plotH + 16, "text-anchor": "middle", class: "axis-label" },
          `${xVal}s`
        )
      );
    }

    svg.appendChild(
      svgEl(
        "text",
        {
          x: margin.left + plotW / 2,
          y: height - 4,
          "text-anchor": "middle",
          class: "axis-label",
        },
        "Mean Inference Time per Task (seconds, lower is faster)"
      )
    );

    // Pareto frontier (sorted by time ascending, tracking rising score)
    const sortedByTime = [...points].sort((a, b) => a.metrics.time - b.metrics.time);
    const frontierPts = [];
    let bestScore = -1;
    for (const pt of sortedByTime) {
      if (pt.metrics.score > bestScore) {
        frontierPts.push(pt);
        bestScore = pt.metrics.score;
      }
    }
    if (frontierPts.length >= 2) {
      const d = frontierPts
        .map((pt, i) => {
          const px = margin.left + (pt.metrics.time / xScale.max) * plotW;
          const py = margin.top + plotH - (pt.metrics.score / 100) * plotH;
          return `${i === 0 ? "M" : "L"} ${px} ${py}`;
        })
        .join(" ");
      svg.appendChild(svgEl("path", { d, class: "frontier-line" }));
    }

    const pointNodes = new Map();
    const legendNodes = new Map();

    function setHoverPreview(targetKey) {
      const activeKey = targetKey ?? state.highlightedModel;
      for (const [k, node] of pointNodes.entries()) {
        node.classList.toggle("dimmed", Boolean(activeKey && activeKey !== k));
        node.classList.toggle("highlighted", activeKey === k);
      }
      for (const [k, btn] of legendNodes.entries()) {
        btn.classList.toggle("dimmed", Boolean(activeKey && activeKey !== k));
        btn.classList.toggle("highlighted", activeKey === k);
      }
    }

    for (const { model, metrics } of points) {
      const key = entryKey(model);
      const px = margin.left + (metrics.time / xScale.max) * plotW;
      const py = margin.top + plotH - (metrics.score / 100) * plotH;
      const color = model.brand?.color || "#6d4aff";
      const isDimmed = state.highlightedModel && state.highlightedModel !== key;
      const isHi = state.highlightedModel === key;
      const isHighRing = model.think_level === "high" || model.think_level === "xhigh" || model.think_level === "max";

      const g = svgEl("g", {
        class: `frontier-point${isDimmed ? " dimmed" : ""}${isHi ? " highlighted" : ""}`,
        tabindex: "0",
        role: "button",
        "aria-label": `${model.display_name || model.model}: score ${metrics.score}, time ${metrics.time}s`,
      });
      g.appendChild(
        svgEl("circle", {
          cx: px,
          cy: py,
          r: 14,
          stroke: color,
          class: "frontier-halo",
        })
      );
      g.appendChild(
        svgEl("circle", {
          cx: px,
          cy: py,
          r: 8,
          fill: color,
          class: "frontier-dot",
        })
      );
      if (isHighRing) {
        g.appendChild(
          svgEl("circle", {
            cx: px,
            cy: py,
            r: 4,
            class: "frontier-inner-ring",
          })
        );
      }
      g.appendChild(
        svgEl("circle", {
          cx: px,
          cy: py,
          r: 16,
          class: "frontier-hit",
        })
      );

      g.addEventListener("mouseenter", (e) => {
        showTooltip(e, model, metrics);
        setHoverPreview(key);
      });
      g.addEventListener("mousemove", positionTooltip);
      g.addEventListener("mouseleave", () => {
        hideTooltip();
        setHoverPreview(null);
      });
      g.addEventListener("click", () => toggleHighlight(key));
      g.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggleHighlight(key);
        }
      });

      pointNodes.set(key, g);
      svg.appendChild(g);
    }

    container.appendChild(svg);

    const legend = el("div", { class: "frontier-legend", role: "group", "aria-label": "Frontier chart legend" });
    const sortedLegend = [...points].sort((a, b) => {
      const diff = b.metrics.score - a.metrics.score;
      if (Math.abs(diff) > 1e-9) return diff;
      return a.metrics.time - b.metrics.time;
    });
    for (const { model, metrics } of sortedLegend) {
      const key = entryKey(model);
      const color = model.brand?.color || "#6d4aff";
      const isDimmed = state.highlightedModel && state.highlightedModel !== key;
      const isHi = state.highlightedModel === key;
      const isHighRing = model.think_level === "high" || model.think_level === "xhigh" || model.think_level === "max";

      const btn = el("button", {
        type: "button",
        class: `frontier-legend-item${isDimmed ? " dimmed" : ""}${isHi ? " highlighted" : ""}`,
        "aria-pressed": isHi ? "true" : "false",
      });
      btn.appendChild(
        el("span", {
          class: `frontier-legend-swatch${isHighRing ? " ringed" : ""}`,
          style: isHighRing ? `color:${color};background:${color}` : `background:${color}`,
          "aria-hidden": "true",
        })
      );
      btn.appendChild(el("span", { class: "frontier-legend-name" }, model.display_base || model.base_name));
      btn.appendChild(
        el(
          "span",
          { class: "frontier-legend-stats" },
          `${metrics.score.toFixed(1)} · ${formatSeconds(metrics.time)}`
        )
      );

      btn.addEventListener("mouseenter", () => setHoverPreview(key));
      btn.addEventListener("mouseleave", () => setHoverPreview(null));
      btn.addEventListener("focus", () => setHoverPreview(key));
      btn.addEventListener("blur", () => setHoverPreview(null));
      btn.addEventListener("click", () => toggleHighlight(key));

      legendNodes.set(key, btn);
      legend.appendChild(btn);
    }

    container.appendChild(legend);
  }

  const FULL_ROW_THRESHOLD = 5;

  function renderCharts() {
    const models = state.data?.models || [];
    if (!models.length) return;

    const intelSubtitle = document.getElementById("intel-subtitle");
    const timeSubtitle = document.getElementById("time-subtitle");
    if (state.scope === "overall") {
      intelSubtitle.textContent = "Real-World Engineering Index (0–100) · Higher is better";
      timeSubtitle.textContent = "Mean inference time (seconds) per task · Lower is better";
    } else {
      const task = state.data.tasks.find((t) => t.id === state.scope);
      intelSubtitle.textContent = `${task ? task.title : state.scope} (0–100) · Higher is better`;
      timeSubtitle.textContent = `Inference time (seconds) for ${task ? task.short_label : state.scope} · Lower is better`;
    }

    // Stack charts full-width once there are enough models that half-width text gets tiny
    document.getElementById("hero-charts").classList.toggle("is-stacked", models.length > FULL_ROW_THRESHOLD);

    renderBarChart({
      containerId: "intelligence-chart",
      models,
      metricKey: "score",
      valueFormatter: (v) => (Number.isInteger(v) ? String(v) : v.toFixed(1)),
      sortDesc: true,
      fixedMax: 100,
    });

    renderBarChart({
      containerId: "time-chart",
      models,
      metricKey: "time",
      valueFormatter: (v) => formatSeconds(v),
      sortDesc: false,
      fixedMax: null,
    });

    renderFrontierChart(models);
  }

  function renderScopePills() {
    const wrap = document.getElementById("scope-pills");
    wrap.innerHTML = "";
    const scopes = [{ id: "overall", label: "Overall (All 6 Tasks)" }].concat(
      (state.data.tasks || []).map((t) => ({ id: t.id, label: t.short_label }))
    );

    for (const sc of scopes) {
      const btn = el(
        "button",
        {
          type: "button",
          class: `scope-pill${state.scope === sc.id ? " active" : ""}`,
          "aria-pressed": state.scope === sc.id ? "true" : "false",
        },
        sc.label
      );
      btn.addEventListener("click", () => {
        state.scope = sc.id;
        renderScopePills();
        renderCharts();
      });
      wrap.appendChild(btn);
    }
  }

  function renderTaskOverview() {
    const list = document.getElementById("task-overview-list");
    list.innerHTML = "";
    for (const task of state.data.tasks || []) {
      const li = el("li", { class: "task-overview-item" });
      const left = el("div");
      left.appendChild(el("div", { class: "task-overview-name" }, task.title));
      left.appendChild(el("div", { class: "task-overview-desc" }, task.description));
      li.appendChild(left);
      li.appendChild(el("span", { class: "task-cat-chip" }, task.short_label));
      list.appendChild(li);
    }
  }

  function sortIconSvg() {
    const svg = svgEl("svg", {
      viewBox: "0 0 12 12",
      class: "sort-icon",
      "aria-hidden": "true",
    });
    svg.appendChild(
      svgEl("path", {
        d: "M6 2.2 L9.5 7.2 H2.5 Z",
        fill: "currentColor",
      })
    );
    return svg;
  }

  function buildTable() {
    const headRow = document.getElementById("table-head-row");
    const tbody = document.getElementById("table-body");
    headRow.innerHTML = "";
    tbody.innerHTML = "";

    const tasks = state.data.tasks || [];
    const columns = [
      { key: "model", label: "Model", type: "text" },
      { key: "intelligence", label: "Intelligence", type: "number", defaultSort: "descending" },
      { key: "time", label: "Time / Task", type: "number" },
      { key: "tps", label: "Decode Speed", type: "number" },
      ...tasks.map((t) => ({ key: t.id, label: t.short_label, type: "number" })),
    ];

    for (const col of columns) {
      const th = el("th", {
        scope: "col",
        "data-type": col.type,
      });
      if (col.defaultSort) th.setAttribute("aria-sort", col.defaultSort);

      const btn = el("button", {
        type: "button",
        commandfor: "breakdown-table",
        command: "--sort",
      });
      btn.appendChild(el("span", null, col.label));
      btn.appendChild(sortIconSvg());
      th.appendChild(btn);
      headRow.appendChild(th);
    }

    for (const model of state.data.models || []) {
      const key = entryKey(model);
      const tr = el("tr", { "data-model": key });

      const rowHead = el("th", { scope: "row", "data-value": model.display_name || model.model });
      const cellWrap = el("div", { class: "model-cell" });
      cellWrap.appendChild(
        el(
          "span",
          {
            class: "model-badge-pill",
            style: `background:${model.brand?.color || "#6d4aff"}`,
            "aria-hidden": "true",
          },
          model.brand?.monogram || "LM"
        )
      );
      const textWrap = el("div", { class: "model-cell-text" });
      textWrap.appendChild(el("span", { class: "model-cell-name" }, model.display_base || model.base_name));
      const subParts = [model.tag_name, model.model_info?.quantization_level].filter(Boolean);
      textWrap.appendChild(el("span", { class: "model-cell-sub" }, subParts.join(" · ") || model.model));
      cellWrap.appendChild(textWrap);
      rowHead.appendChild(cellWrap);
      tr.appendChild(rowHead);

      const tdIntel = el("td", {
        "data-label": "Intelligence",
        "data-value": model.summary.intelligence_index,
      });
      tdIntel.appendChild(el("span", { class: "metric-strong" }, `${model.summary.intelligence_index.toFixed(1)}`));
      tdIntel.appendChild(
        el(
          "span",
          { class: "metric-sub" },
          `${model.summary.tasks_perfect}/${model.summary.tasks_total} perfect`
        )
      );
      tr.appendChild(tdIntel);

      const tdTime = el("td", {
        "data-label": "Time / Task",
        "data-value": model.summary.mean_time_per_task_s,
      });
      tdTime.appendChild(el("span", { class: "metric-strong" }, `${model.summary.mean_time_per_task_s.toFixed(1)}s`));
      tdTime.appendChild(el("span", { class: "metric-sub" }, `${model.summary.total_time_s.toFixed(1)}s total`));
      tr.appendChild(tdTime);

      const tdTps = el("td", {
        "data-label": "Decode Speed",
        "data-value": model.summary.mean_tokens_per_sec,
      });
      tdTps.appendChild(el("span", { class: "metric-strong" }, `${model.summary.mean_tokens_per_sec.toFixed(1)}`));
      tdTps.appendChild(el("span", { class: "metric-sub" }, "tok/s"));
      tr.appendChild(tdTps);

      for (const taskDef of tasks) {
        const entry = getTaskEntry(model, taskDef.id);
        const score = entry?.grade?.score ?? 0;
        const sec = entry?.inference_time_s ?? 0;
        const td = el("td", {
          "data-label": taskDef.short_label,
          "data-value": score,
        });

        const btn = el("button", {
          type: "button",
          class: "task-score-btn",
          "aria-label": `Inspect ${model.display_name || model.model} on ${taskDef.title}: score ${score} in ${sec}s`,
        });
        btn.appendChild(el("span", { class: `score-pill ${scoreClass(score)}` }, `${score.toFixed(0)}`));
        btn.appendChild(el("span", { class: "score-time" }, formatSeconds(sec)));
        btn.addEventListener("click", () => openTaskModal(model, taskDef, entry));
        td.appendChild(btn);
        tr.appendChild(td);
      }

      tbody.appendChild(tr);
    }

    wireSortableTable();
  }

  function wireSortableTable() {
    const table = document.getElementById("breakdown-table");
    const tbody = table.tBodies[0];
    const status = document.getElementById("table-sort-status");

    function handleSort(button) {
      const th = button.closest("th");
      if (!th) return;
      const currentSort = th.getAttribute("aria-sort");
      const direction =
        currentSort === "ascending"
          ? "descending"
          : currentSort === "descending"
          ? "ascending"
          : th.dataset.type === "number"
          ? "descending"
          : "ascending";

      for (const header of table.tHead.rows[0].cells) {
        header.removeAttribute("aria-sort");
      }
      th.setAttribute("aria-sort", direction);

      const index = th.cellIndex;
      const type = th.dataset.type;
      const factor = direction === "ascending" ? 1 : -1;

      const rows = [...tbody.rows];
      const getVal = (row) => row.cells[index].dataset.value ?? row.cells[index].textContent.trim();

      rows.sort((a, b) => {
        if (type === "number") {
          return factor * (Number(getVal(a)) - Number(getVal(b)));
        }
        return factor * collator.compare(getVal(a), getVal(b));
      });

      // Reorder existing <tr> elements directly in tbody (per sortable-table guidance)
      for (const row of rows) {
        if (typeof tbody.moveBefore === "function") {
          tbody.moveBefore(row, null);
        } else {
          tbody.appendChild(row);
        }
      }

      status.textContent = `Sorted by ${button.textContent.trim()}, ${direction}, ${rows.length} rows`;
    }

    if (!table.dataset.sortWired) {
      table.dataset.sortWired = "true";
      table.addEventListener("command", (event) => {
        if (event.command !== "--sort" || !event.source) return;
        handleSort(event.source);
      });

      // Fallback for browsers without Invoker Commands support
      if (!("commandForElement" in HTMLButtonElement.prototype)) {
        table.addEventListener("click", (event) => {
          const btn = event.target.closest('thead th button[command="--sort"]');
          if (btn) handleSort(btn);
        });
      }
    }
  }

  function setupModalFallback() {
    const dialog = document.getElementById("task-modal");
    // Mandatory fallback for browsers without <dialog closedby="any"> support (e.g. Safari)
    if (!("closedBy" in HTMLDialogElement.prototype)) {
      const isOutside = (e) => {
        const r = dialog.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      let pressedOutside = false;
      dialog.addEventListener("pointerdown", (e) => {
        pressedOutside = isOutside(e);
      });
      dialog.addEventListener("pointerup", (e) => {
        if (pressedOutside && isOutside(e)) dialog.close();
      });
    }
    dialog.addEventListener("close", () => {
      dialog.returnValue = "";
    });
  }

  function openTaskModal(model, taskDef, entry) {
    const dialog = document.getElementById("task-modal");
    const badges = document.getElementById("modal-badges");
    badges.innerHTML = "";
    badges.appendChild(el("span", { class: "modal-chip" }, model.model));
    if (model.think_level) {
      badges.appendChild(el("span", { class: "modal-chip" }, `think: ${model.think_level}`));
    }
    badges.appendChild(el("span", { class: "modal-chip" }, taskDef.category));

    document.getElementById("modal-title").textContent = taskDef.title;
    document.getElementById("modal-subtitle").textContent = taskDef.description;

    const metrics = document.getElementById("modal-metrics");
    metrics.innerHTML = "";
    const grade = entry?.grade || { score: 0, passed_checks: 0, total_checks: 10, checks: [] };
    const metricBoxes = [
      ["Score", `${grade.score.toFixed(1)} / 100 (${grade.passed_checks}/${grade.total_checks})`],
      ["Inference Time", `${(entry?.inference_time_s ?? 0).toFixed(2)}s`],
      ["Decode Speed", `${(entry?.tokens_per_sec ?? 0).toFixed(1)} tok/s`],
      ["Tokens / Stop", `${entry?.eval_count ?? 0} · ${entry?.done_reason || "—"}`],
    ];
    for (const [label, val] of metricBoxes) {
      const box = el("div", { class: "modal-metric-box" });
      box.appendChild(el("span", { class: "modal-metric-label" }, label));
      box.appendChild(el("span", { class: "modal-metric-value" }, val));
      metrics.appendChild(box);
    }

    const checksList = document.getElementById("modal-checks");
    checksList.innerHTML = "";
    for (const chk of grade.checks || []) {
      const li = el("li", { class: `check-item ${chk.passed ? "pass" : "fail"}` });
      li.appendChild(el("span", { class: "check-mark", "aria-hidden": "true" }, chk.passed ? "✓" : "✗"));
      const textCol = el("div");
      textCol.appendChild(el("div", { class: "check-name" }, chk.name));
      textCol.appendChild(el("div", { class: "check-detail" }, chk.detail || ""));
      li.appendChild(textCol);
      checksList.appendChild(li);
    }

    document.getElementById("modal-output").textContent =
      entry?.content || entry?.grade?.extracted || "(empty output)";

    const thinkingWrap = document.getElementById("modal-thinking-wrap");
    const thinkingEl = document.getElementById("modal-thinking");
    if (entry?.thinking) {
      thinkingWrap.hidden = false;
      thinkingWrap.open = false;
      thinkingEl.textContent = entry.thinking;
    } else {
      thinkingWrap.hidden = true;
      thinkingEl.textContent = "";
    }

    document.getElementById("modal-prompt").textContent = taskDef.prompt || "";
    dialog.showModal();
  }

  function renderMeta() {
    const data = state.data;
    const pills = document.getElementById("meta-pills");
    pills.innerHTML = "";

    const firstModel = data.models?.[0];
    const hw = firstModel?.hardware || {};
    const uniqueModels = new Set((data.models || []).map((m) => m.model)).size;
    const countLabel =
      uniqueModels === data.models.length
        ? `${data.models.length} model${data.models.length === 1 ? "" : "s"}`
        : `${uniqueModels} models · ${data.models.length} configs`;
    const items = [
      `Suite v${data.suite_version}`,
      countLabel,
    ];
    if (firstModel?.ollama_version) items.push(`Ollama ${firstModel.ollama_version}`);
    if (hw.memory_gb) items.push(`${hw.memory_gb} GB RAM`);
    if (hw.cores) items.push(`${hw.cores} cores`);

    for (const label of items) {
      pills.appendChild(el("span", { class: "meta-pill" }, label));
    }

    const footerInfo = document.getElementById("footer-info");
    const tempLabel = firstModel?.options?.temperature ?? "default";
    footerInfo.textContent = `Updated ${data.generated_at_utc} · seed=42 · temp=${tempLabel}`;
  }

  async function loadData() {
    if (window.BENCHMARK_DATA && Array.isArray(window.BENCHMARK_DATA.models)) {
      return window.BENCHMARK_DATA;
    }
    const resp = await fetch("data.json", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  async function init() {
    initTheme();
    setupModalFallback();
    try {
      state.data = await loadData();
    } catch (err) {
      document.getElementById("empty-state").hidden = false;
      document.getElementById("hero-charts").hidden = true;
      document.getElementById("secondary-grid").hidden = true;
      document.getElementById("matrix-section").hidden = true;
      return;
    }

    if (!state.data?.models?.length) {
      document.getElementById("empty-state").hidden = false;
      document.getElementById("hero-charts").hidden = true;
      document.getElementById("secondary-grid").hidden = true;
      document.getElementById("matrix-section").hidden = true;
      return;
    }

    renderMeta();
    renderScopePills();
    renderCharts();
    renderTaskOverview();
    buildTable();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
