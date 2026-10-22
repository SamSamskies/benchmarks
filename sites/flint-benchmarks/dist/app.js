"use strict";

const $ = (id) => document.getElementById(id);
const number = (value, digits = 2) => Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
const integer = (value) => Number(value).toLocaleString();
const svgNS = "http://www.w3.org/2000/svg";
let dataset = { schemaVersion: 1, runs: [] };
let selectedId;
let metric = "speed";

function notify(message, error = false) {
  $("message").textContent = message;
  $("message").classList.toggle("error", error);
  $("message").hidden = !message;
}

function currentRun() {
  return dataset.runs.find((run) => run.id === selectedId);
}

function validateDataset(value) {
  if (value?.schemaVersion !== 1 || !Array.isArray(value.runs) || !value.runs.length || value.runs.length > 100) throw new Error("Choose a benchmark JSON exported by this dashboard or scripts/export_dashboard.py.");
  const ids = new Set();
  for (const run of value.runs) {
    const config = run.configuration;
    if (!run || typeof run.id !== "string" || ids.has(run.id) || typeof run.model?.name !== "string" || typeof run.model?.digest !== "string" || !Number.isFinite(Date.parse(run.startedAt))) throw new Error("The file has an invalid or duplicate benchmark identity.");
    ids.add(run.id);
    if (!config || !Number.isInteger(config.runsRequested) || config.runsRequested < 1 || config.runsRequested > 1000 || !Number.isInteger(config.tokenBudget) || config.tokenBudget < 1 || typeof config.prompt !== "string" || !Array.isArray(run.samples)) throw new Error("The file is missing benchmark settings or samples.");
    const indexes = new Set();
    for (const sample of run.samples) {
      if (!sample || !Number.isInteger(sample.index) || sample.index < 1 || sample.index > config.runsRequested || indexes.has(sample.index) || !["success", "failed"].includes(sample.status)) throw new Error("The file has duplicate or invalid sample numbers.");
      indexes.add(sample.index);
      if (sample.status === "success" && (!Number.isFinite(sample.tokensPerSecond) || sample.tokensPerSecond <= 0 || !Number.isInteger(sample.tokens) || sample.tokens <= 0)) throw new Error("Successful samples must contain positive speeds and token counts.");
    }
    const good = run.samples.filter((sample) => sample.status === "success");
    if (good.length) {
      if (!run.stats || ![run.stats.average, run.stats.min, run.stats.max].every((n) => Number.isFinite(n) && n > 0)) throw new Error("The file is missing a valid throughput summary.");
      const speeds = good.map((sample) => sample.tokensPerSecond);
      if (Math.abs(run.stats.average - speeds.reduce((a, b) => a + b, 0) / speeds.length) > 0.02 || Math.abs(run.stats.min - Math.min(...speeds)) > 0.01 || Math.abs(run.stats.max - Math.max(...speeds)) > 0.01) throw new Error("The summary does not match the individual samples.");
    } else if (run.stats != null) throw new Error("A run without successful samples cannot have a throughput summary.");
    if (run.files && Object.values(run.files).some((file) => typeof file !== "string")) throw new Error("The file contains invalid download data.");
  }
  return value;
}

function renderSelector() {
  $("run-select").replaceChildren();
  for (const run of dataset.runs) {
    const option = document.createElement("option");
    option.value = run.id;
    option.textContent = `${new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(run.startedAt))} — ${run.model.name}`;
    $("run-select").append(option);
  }
  $("run-select").value = selectedId;
}

function svgElement(name, attrs = {}, content) {
  const element = document.createElementNS(svgNS, name);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
  if (content !== undefined) element.textContent = content;
  return element;
}

function renderChart(run) {
  const chart = $("chart");
  chart.replaceChildren();
  const samples = Array.from({ length: run.configuration.runsRequested }, (_, index) => run.samples.find((sample) => sample.index === index + 1) || { index: index + 1, status: "missing" });
  const successful = samples.filter((sample) => sample.status === "success");
  if (!successful.length) {
    const message = document.createElement("p");
    message.className = "empty-chart";
    message.textContent = "No successful timed runs. Open the run log to inspect the failure.";
    chart.append(message);
    return;
  }
  const isSpeed = metric === "speed";
  const reference = isSpeed ? run.stats.average : run.configuration.tokenBudget;
  const values = successful.map((sample) => isSpeed ? sample.tokensPerSecond : sample.tokens);
  const max = Math.max(...values, reference);
  const step = isSpeed ? (max < 20 ? 5 : 20) : Math.max(50, 10 ** Math.floor(Math.log10(max)));
  const axisMax = Math.ceil(max * 1.1 / step) * step;
  const padding = getComputedStyle(chart);
  const width = Math.max(300, chart.clientWidth - parseFloat(padding.paddingLeft) - parseFloat(padding.paddingRight));
  const narrow = width < 500;
  const left = narrow ? 43 : 62, right = narrow ? 60 : 100, top = 25, rowGap = narrow ? 43 : 50;
  const plotWidth = width - left - right;
  const height = top + samples.length * rowGap + 32;
  const axisY = top + samples.length * rowGap;
  const x = (value) => left + value / axisMax * plotWidth;
  const svg = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": isSpeed ? "Generation throughput for each timed run" : "Actual generated tokens for each timed run" });
  svg.append(svgElement("title", {}, samples.map((sample) => `Run ${sample.index}: ${sample.status === "success" ? (isSpeed ? `${sample.tokensPerSecond} tokens per second` : `${sample.tokens} tokens`) : sample.status}`).join(". ")));
  for (let tick = 0; tick <= 4; tick++) {
    const value = axisMax * tick / 4;
    svg.append(svgElement("line", { x1: x(value), x2: x(value), y1: top - 5, y2: axisY - 5, stroke: "#e8ecf5", "stroke-width": 1 }));
    svg.append(svgElement("text", { x: x(value), y: axisY + 18, "text-anchor": "middle" }, integer(value)));
  }
  samples.forEach((sample, index) => {
    const y = top + index * rowGap;
    svg.append(svgElement("text", { x: left - 13, y: y + 17, "text-anchor": "end", class: "run-label" }, `Run ${sample.index}`));
    if (sample.status === "success") {
      const value = isSpeed ? sample.tokensPerSecond : sample.tokens;
      svg.append(svgElement("rect", { x: left, y, width: plotWidth, height: 26, rx: 5, fill: "#f3f5fc" }));
      svg.append(svgElement("rect", { x: left, y, width: x(value) - left, height: 26, rx: 5, fill: ["#375dfb", "#5878fa", "#7992fa"][index % 3] }));
      svg.append(svgElement("text", { x: x(value) + 12, y: y + 18, class: "value-label" }, isSpeed ? number(value) : integer(value)));
    } else {
      svg.append(svgElement("text", { x: left + 10, y: y + 18 }, sample.status === "failed" ? "Failed" : "Not recorded"));
    }
  });
  svg.append(svgElement("line", { x1: x(reference), x2: x(reference), y1: top - 8, y2: axisY - 12, stroke: "#8b7cce", "stroke-width": 1.7, "stroke-dasharray": "4 4" }));
  chart.append(svg);
  $("legend-label").textContent = isSpeed ? "Generation throughput" : "Actual generated tokens";
  $("mean-label").textContent = isSpeed ? `Average ${number(reference)}` : `Budget ${integer(reference)}`;
  $("speed-view").setAttribute("aria-pressed", String(isSpeed));
  $("tokens-view").setAttribute("aria-pressed", String(!isSpeed));
}

function render() {
  const run = currentRun();
  if (!run) return;
  const samples = run.samples.filter((sample) => sample.status === "success");
  const config = run.configuration;
  const hardware = run.hardware || {};
  const complete = samples.length === config.runsRequested && run.exitCode === 0;
  const name = run.model.name.split(":")[0];
  const title = name.charAt(0).toUpperCase() + name.slice(1);
  document.title = `${title} | Benchmark lab`;
  $("model-name").textContent = title;
  $("model-tag").textContent = run.model.name;
  $("page-title").textContent = `How fast does ${title} generate?`;
  $("runner").textContent = run.model.runner?.toUpperCase() || "Runner unknown";
  $("quantization").textContent = run.model.quantization || "Quantization unknown";
  $("chip").textContent = hardware.chip || "Hardware unknown";
  $("memory").textContent = hardware.memoryBytes ? `${number(hardware.memoryBytes / 1024 ** 3, 0)} GiB` : "Unknown";
  $("gpu").textContent = hardware.gpu?.[0]?.sppci_cores || "Unknown";
  $("version").textContent = run.server?.version || "Unknown";
  $("os").textContent = hardware.os || "Unknown";
  $("run-status").textContent = `${samples.length}/${config.runsRequested} runs ${complete ? "complete" : "recorded"}`;
  $("run-status").classList.toggle("partial", !complete);
  $("average").textContent = run.stats ? number(run.stats.average) : "—";
  $("range").textContent = run.stats ? `${number(run.stats.min)}–${number(run.stats.max)}` : "No results";
  $("run-count").textContent = `${samples.length} of ${config.runsRequested}`;
  const counts = [...new Set(samples.map((sample) => sample.tokens))];
  $("token-count").textContent = counts.length === 1 ? `${integer(counts[0])} tokens` : counts.length ? `${integer(Math.min(...counts))}–${integer(Math.max(...counts))} tokens` : "—";
  $("result-caption").textContent = `${samples.length} successful timed runs after ${config.warmupRuns ?? "an unknown number of"} warmup${config.warmupRuns === 1 ? "" : "s"}.`;
  $("chart-subtitle").textContent = "The warmup is excluded from these results.";
  $("sample-rows").replaceChildren();
  for (let index = 1; index <= config.runsRequested; index++) {
    const sample = run.samples.find((item) => item.index === index);
    const success = sample?.status === "success";
    const row = document.createElement("tr");
    for (const value of [`Run ${index}`, success ? number(sample.tokensPerSecond) : "—", success ? integer(sample.tokens) : "—", success ? "Complete" : sample ? "Failed" : "Missing"]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    row.lastElementChild.className = success ? "pass-label" : "fail-label";
    $("sample-rows").append(row);
  }
  const settings = [
    ["Generation budget", `${integer(config.tokenBudget)} tokens`], ["Timed runs requested", integer(config.runsRequested)], ["Warmup runs", config.warmupRuns ?? "Unknown"],
    ["Temperature", config.temperature ?? "Default"], ["Seed", config.seed ?? "Default"], ["Stop sequences", JSON.stringify(config.stop ?? "Default")],
    ["Loaded context length", run.runtime?.contextLength ? integer(run.runtime.contextLength) : "Unknown"],
    ["Reported device allocation", run.runtime?.vramBytes ? `${number(run.runtime.vramBytes / 1024 ** 3, 1)} GiB` : "Unknown"], ["Remaining options", config.otherOptions || "Server/model defaults"],
    ["Started (UTC)", run.startedAt], ["Finished (UTC)", run.finishedAt || "Not recorded"], ["Exit status", run.exitCode ?? "Unknown"],
    ["Script SHA-256", run.source?.scriptSha256 || "Unknown"], ["Repository revision", run.source?.revision || "Unknown"], ["Working tree", run.source?.dirty === true ? "Had local changes" : run.source?.dirty === false ? "Clean" : "Unknown"],
  ];
  $("settings").replaceChildren();
  for (const [label, value] of settings) {
    const group = document.createElement("div"), term = document.createElement("dt"), definition = document.createElement("dd");
    term.textContent = label;
    definition.textContent = value;
    group.append(term, definition);
    $("settings").append(group);
  }
  $("prompt").textContent = config.prompt;
  $("digest").textContent = run.model.digest;
  $("measurement-date").textContent = "Measured " + new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(new Date(run.startedAt));
  for (const [button, file] of [["download-csv", "csv"], ["download-log", "log"], ["download-context", "context"]]) $(button).disabled = !run.files?.[file];
  renderChart(run);
}

function download(content, filename, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$("run-select").addEventListener("change", (event) => { selectedId = event.target.value; render(); notify(""); });
for (const [id, view] of [["speed-view", "speed"], ["tokens-view", "tokens"]]) $(id).addEventListener("click", () => { metric = view; renderChart(currentRun()); });
for (const [button, file, extension, type] of [["download-csv", "csv", "csv", "text/csv"], ["download-log", "log", "log", "text/plain"], ["download-context", "context", "md", "text/markdown"]]) {
  $(button).addEventListener("click", () => {
    const run = currentRun();
    if (run?.files?.[file]) download(run.files[file], `${run.id}-${file}.${extension}`, type);
  });
}
$("download-json").addEventListener("click", () => {
  const run = currentRun();
  if (run) download(JSON.stringify({ schemaVersion: 1, runs: [run] }, null, 2), `${run.id}.json`, "application/json");
});
$("import-file").addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    if (file.size > 5 * 1024 * 1024) throw new Error("Choose a benchmark JSON file smaller than 5 MB.");
    const imported = validateDataset(JSON.parse(await file.text()));
    const merged = new Map(dataset.runs.map((run) => [run.id, run]));
    for (const run of imported.runs) merged.set(run.id, run);
    dataset.runs = [...merged.values()].sort((a, b) => Date.parse(a.startedAt) - Date.parse(b.startedAt));
    selectedId = imported.runs[imported.runs.length - 1].id;
    renderSelector();
    render();
    notify(`Imported ${imported.runs.length} benchmark${imported.runs.length === 1 ? "" : "s"}. Files stay in this browser session.`);
  } catch (error) {
    notify(error instanceof SyntaxError ? "That file is not valid JSON. Choose an exported benchmark JSON file." : error.message, true);
  } finally { event.target.value = ""; }
});

try {
  dataset = validateDataset(window.BENCHMARK_DATA);
  selectedId = dataset.runs[dataset.runs.length - 1].id;
  renderSelector();
  render();
} catch (error) {
  $("run-status").textContent = "No valid results";
  $("result-caption").textContent = "Import an exported benchmark JSON file to view results.";
  notify(error.message, true);
}

let previousChartWidth = 0;
new ResizeObserver(([entry]) => {
  if (Math.abs(entry.contentRect.width - previousChartWidth) >= 1) {
    previousChartWidth = entry.contentRect.width;
    const run = currentRun();
    if (run) renderChart(run);
  }
}).observe($("chart"));
