"use strict";
// All user-supplied text is inserted with textContent, never as HTML.

const MAX_BYTES = 20_000_000;
const LABELS = ["positive", "neutral", "negative"];
const CLS = { positive: "pos", neutral: "neu", negative: "neg" };
const num = new Intl.NumberFormat("en");
const $ = (selector) => document.querySelector(selector);

const state = { dataset: null, job: null, timer: null };

// --- small helpers ---------------------------------------------------------

function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

function sized(node, property, percent) {
  node.style[property] = `${Math.max(0, Math.min(100, percent))}%`;
  return node;
}

const pct = (part, whole) => (whole ? Math.round((100 * part) / whole) : 0);

async function api(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("The server could not be reached. Check your connection and try again.");
  }
  if (response.status === 204) return null;
  let body = null;
  try {
    body = await response.json();
  } catch {
    /* not JSON */
  }
  if (!response.ok) throw new Error((body && body.error) || "Something went wrong. Please try again.");
  return body;
}

function show(view) {
  for (const id of ["view-start", "view-map", "view-work", "view-dash"]) {
    $(`#${id}`).hidden = id !== view;
  }
  $("#restart").hidden = view === "view-start";
  const heading = $(`#${view} h1`);
  if (heading) heading.focus();
  window.scrollTo(0, 0);
}

function say(message) {
  $("#status").textContent = message;
}

function fail(id, message) {
  const box = $(id);
  box.textContent = message || "";
  box.hidden = !message;
}

function table(headers, rows, caption) {
  const numeric = headers.map((header) => header.endsWith("#"));
  return h(
    "table",
    null,
    caption && h("caption", { text: caption }),
    h("thead", null, h("tr", null, headers.map((header, i) =>
      h("th", { scope: "col", class: numeric[i] && "n", text: header.replace(/#$/, "") })))),
    h("tbody", null, rows.map((row) => h("tr", null, row.map((cell, i) =>
      h("td", { class: numeric[i] ? "n" : "clip", text: cell === null || cell === undefined ? "" : typeof cell === "number" ? num.format(cell) : cell })))))
  );
}

const dataTable = (headers, rows, caption) =>
  h("details", null, h("summary", { text: "Show the numbers as a table" }), h("div", { class: "scroll" }, table(headers, rows, caption)));

// --- start: upload or sample -----------------------------------------------

async function loadSamples() {
  try {
    const samples = await api("/api/samples");
    $("#samples").replaceChildren(...samples.map((sample) =>
      h("li", null,
        h("button", { type: "button", class: "link", text: sample.title, onclick: () => begin(() => api(`/api/samples/${sample.id}`, { method: "POST" }), sample.title) }),
        h("span", { class: "fine", text: sample.note }))));
  } catch {
    $(".samples").hidden = true;
  }
}

async function begin(request, name) {
  fail("#start-error", "");
  $("#start-busy").hidden = false;
  $("#start-busy-text").textContent = `Reading ${name}`;
  say(`Reading ${name}`);
  try {
    state.dataset = await request();
    renderMapping();
    show("view-map");
    say("File read. Check the columns, then choose Analyse.");
  } catch (error) {
    fail("#start-error", error.message);
  } finally {
    $("#start-busy").hidden = true;
    $("#file").value = "";
  }
}

function upload(file) {
  if (!file) return;
  if (file.size > MAX_BYTES) {
    fail("#start-error", "This file is larger than 20 MB. Upload a smaller file.");
    return;
  }
  begin(() => api("/api/upload", { method: "POST", body: file, headers: { "X-Filename": encodeURIComponent(file.name) } }), file.name);
}

// --- mapping -----------------------------------------------------------------

function fillSelect(select, role, optional) {
  const { mapping, profile } = state.dataset;
  const suggested = mapping.candidates[role];
  const others = profile.columns.map((column) => column.name).filter((name) => !suggested.includes(name));
  const option = (name) => h("option", { value: name, text: name, selected: mapping[role] === name });
  select.replaceChildren(
    optional && h("option", { value: "", text: "None", selected: !mapping[role] }),
    suggested.length ? h("optgroup", { label: "Suggested" }, suggested.map(option)) : null,
    others.length ? h("optgroup", { label: "Other columns" }, others.map(option)) : null
  );
}

function renderMapping() {
  const data = state.dataset;
  const { profile } = data;
  $("#map-lede").textContent = `${data.filename}: ${num.format(profile.rows)} rows, ${profile.columns.length} columns. ` +
    (data.mapping.text ? "We guessed which column is which. Change any that look wrong." : "We could not find a text column. Choose one below.");
  const warnings = $("#map-warnings");
  warnings.replaceChildren(...data.warnings.map((warning) => h("li", { text: warning })));
  warnings.hidden = !data.warnings.length;
  fillSelect($("#col-text"), "text", false);
  fillSelect($("#col-date"), "date", true);
  fillSelect($("#col-category"), "category", true);
  const names = profile.columns.map((column) => column.name);
  $("#map-preview").replaceChildren(table(names, data.preview.map((row) => names.map((name) => {
    const value = row[name];
    return value === null || value === undefined ? "" : String(value).slice(0, 160);
  }))));
  fail("#map-error", "");
}

async function saveMapping() {
  const choice = { text: $("#col-text").value, date: $("#col-date").value || null, category: $("#col-category").value || null };
  if (!choice.text) throw new Error("Choose the column that holds the text.");
  state.dataset = await api(`/api/datasets/${state.dataset.dataset_id}/mapping`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(choice),
  });
}

async function analyse(event) {
  event.preventDefault();
  const button = $("#analyse");
  button.disabled = true;
  fail("#map-error", "");
  try {
    await saveMapping();
    const { job_id: job } = await api("/api/analyse", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dataset_id: state.dataset.dataset_id }),
    });
    state.job = job;
    $("#work-bar").value = 0;
    $("#work-text").textContent = "Waiting to start";
    show("view-work");
    say("Analysis started.");
    poll();
  } catch (error) {
    fail("#map-error", error.message);
  } finally {
    button.disabled = false;
  }
}

async function poll() {
  try {
    const job = await api(`/api/jobs/${state.job}`);
    if (job.status === "done") {
      renderDashboard(job.result);
      show("view-dash");
      say("Analysis finished.");
      return;
    }
    if (job.status === "failed") throw new Error(job.error);
    if (job.status === "cancelled") throw new Error("The analysis was cancelled.");
    if (job.total) {
      $("#work-bar").value = job.done / job.total;
      $("#work-text").textContent = `Scored ${num.format(job.done)} of ${num.format(job.total)} rows`;
    } else if (job.status === "queued") {
      $("#work-text").textContent = job.position ? `Waiting: ${job.position} ${job.position === 1 ? "analysis" : "analyses"} ahead of yours` : "Waiting: yours is next";
    } else {
      $("#work-text").textContent = "Reading the text";
    }
    state.timer = setTimeout(poll, 1000);
  } catch (error) {
    show("view-map");
    fail("#map-error", error.message);
  }
}

function cancelJob() {
  clearTimeout(state.timer);
  if (state.job) api(`/api/jobs/${state.job}`, { method: "DELETE" }).catch(() => {});
  state.job = null;
  show("view-map");
  say("Analysis cancelled.");
}

async function restart() {
  clearTimeout(state.timer);
  if (state.job) api(`/api/jobs/${state.job}`, { method: "DELETE" }).catch(() => {});
  if (state.dataset) api(`/api/datasets/${state.dataset.dataset_id}`, { method: "DELETE" }).catch(() => {});
  state.dataset = null;
  state.job = null;
  $("#view-dash").replaceChildren();
  fail("#start-error", "");
  show("view-start");
}

// --- dashboard ----------------------------------------------------------------

function section(number, title, takeaway, ...content) {
  const id = `sec-${number}`;
  return h("section", { class: "sec", "aria-labelledby": id },
    h("div", { class: "rail" }, h("span", { class: "num", text: number }), h("h2", { id, text: title })),
    h("div", { class: "body" }, takeaway && h("p", { class: "take", text: takeaway }), content));
}

const empty = (message) => h("p", { class: "empty", text: message });

function legend(counts, total) {
  return h("ul", { class: "key" }, LABELS.map((label) =>
    h("li", null, h("i", { class: CLS[label] }), label[0].toUpperCase() + label.slice(1), h("b", { text: `${pct(counts[label], total)}%` }), ` (${num.format(counts[label])})`)));
}

function stackedBar(counts, total, description) {
  return h("div", { class: "stack", role: "img", "aria-label": description },
    LABELS.map((label) => sized(h("span", { class: CLS[label] }), "width", total ? (100 * counts[label]) / total : 0)));
}

function summarySection(result) {
  const s = result.summary;
  const skipped = Object.entries(s.skipped).map(([reason, count]) => `${num.format(count)} ${reason}`).join(", ");
  const cell = (label, value, note) => h("div", null, h("dt", { text: label }), h("dd", null, value, note && h("small", { text: note })));
  return section("01", "Summary", s.takeaway,
    h("dl", { class: "strip" },
      cell("Rows analysed", num.format(s.scored), `of ${num.format(s.rows)}`),
      cell("Positive", `${pct(s.counts.positive, s.scored)}%`),
      cell("Neutral", `${pct(s.counts.neutral, s.scored)}%`, "no clear lean"),
      cell("Negative", `${pct(s.counts.negative, s.scored)}%`),
      cell("Average confidence", s.scored ? `${Math.round(100 * s.mean_confidence)}%` : "n/a", skipped ? `skipped: ${skipped}` : "no rows skipped")));
}

function overviewSection(result) {
  const p = result.profile;
  const text = p.text;
  const columns = h("div", { class: "scroll" }, h("table", null,
    h("thead", null, h("tr", null, ["Column", "Kind", "Missing", "", "Distinct"].map((label, i) => h("th", { scope: "col", class: i === 4 && "n", text: label })))),
    h("tbody", null, p.columns.map((column) => h("tr", null,
      h("td", { text: column.name }), h("td", { text: column.kind }),
      h("td", { class: "n", text: `${column.missing_pct}%` }),
      h("td", null, h("span", { class: "meter", role: "img", "aria-label": `${column.missing_pct}% missing` }, sized(h("i"), "width", column.missing_pct))),
      h("td", { class: "n", text: num.format(column.unique) }))))));
  let lengths = empty("There is no text to measure.");
  if (text && text.length) {
    const tallest = Math.max(...text.histogram.map((bin) => bin.count), 1);
    lengths = h("div", null,
      h("div", { class: "hist", role: "img", "aria-label": `Text length: median ${Math.round(text.length.median)} characters, longest ${num.format(text.length.max)}` },
        text.histogram.map((bin) => sized(h("span", { title: `${bin.from}-${bin.to} characters: ${num.format(bin.count)} rows` }), "height", (100 * bin.count) / tallest))),
      h("div", { class: "axis" }, h("span", { text: "0 characters" }), h("span", { text: `${num.format(text.histogram.at(-1).to)}${text.longer_than_histogram ? "+" : ""}` })),
      dataTable(["From (characters)#", "To#", "Rows#"], text.histogram.map((bin) => [bin.from, bin.to, bin.count]), "Rows by text length"));
  }
  const takeaway = `${num.format(p.rows)} rows and ${p.columns.length} columns, with ${num.format(p.duplicate_rows)} duplicate ${p.duplicate_rows === 1 ? "row" : "rows"}` +
    (text && text.length ? `. A typical text is ${Math.round(text.length.median)} characters long.` : ".");
  const names = p.columns.map((column) => column.name);
  return section("02", "Data overview", takeaway,
    h("h3", { class: "sub", text: "Columns" }), columns,
    h("h3", { class: "sub", text: `Length of "${result.mapping.text}"` }), lengths,
    h("h3", { class: "sub", text: "Sample rows" }),
    h("div", { class: "scroll", tabindex: "0", role: "region", "aria-label": "Sample rows" },
      table(names, state.dataset.preview.slice(0, 5).map((row) => names.map((name) => (row[name] === null ? "" : String(row[name]).slice(0, 160)))))));
}

function distributionSection(result) {
  const s = result.summary;
  if (!s.scored) return section("03", "Sentiment split", null, empty("No rows could be scored, so there is no split to show."));
  const description = LABELS.map((label) => `${pct(s.counts[label], s.scored)}% ${label}`).join(", ");
  return section("03", "Sentiment split", `Out of ${num.format(s.scored)} scored rows: ${description}.`,
    stackedBar(s.counts, s.scored, description), legend(s.counts, s.scored));
}

function timeChart(points) {
  const W = 720, H = 200, left = 0, bottom = 0, top = 0;
  const live = points.map((point, index) => ({ ...point, index })).filter((point) => point.total > 0);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const el = (tag, attrs, text) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text) node.textContent = text;
    svg.append(node);
    return node;
  };
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "time");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Share of positive, neutral and negative rows in each period. The table below has the numbers.");
  const x = (index) => left + (points.length > 1 ? (index / (points.length - 1)) * (W - left - 4) : (W - left) / 2);
  const y = (share) => top + (1 - share) * (H - top - bottom);
  const fill = { positive: "var(--pos)", neutral: "var(--neu)", negative: "var(--neg)" };
  if (live.length === 1) {
    let from = 0;
    for (const label of LABELS) {
      const share = live[0][label] / live[0].total;
      el("rect", { x: x(0) - 40, width: 80, y: y(from + share), height: y(from) - y(from + share), fill: fill[label] });
      from += share;
    }
  } else {
    let lower = live.map(() => 0);
    for (const label of LABELS) {
      const upper = live.map((point, i) => lower[i] + point[label] / point.total);
      const path = live.map((point, i) => `${i ? "L" : "M"}${x(point.index).toFixed(1)},${y(upper[i]).toFixed(1)}`).join("") +
        live.map((point, i) => i).reverse().map((i) => `L${x(live[i].index).toFixed(1)},${y(lower[i]).toFixed(1)}`).join("") + "Z";
      el("path", { d: path, fill: fill[label] });
      lower = upper;
    }
  }
  // Labels live in HTML below the chart: text inside a scaled SVG shrinks to
  // unreadable sizes on a phone.
  for (const share of [0.25, 0.5, 0.75]) el("line", { x1: left, x2: W, y1: y(share), y2: y(share), class: "grid" });
  return svg;
}

function timeSection(result) {
  const time = result.time;
  if (!time) {
    return section("04", "Sentiment over time", null,
      empty(result.mapping.date ? "None of the scored rows has a readable date, so there is no timeline." : "No date column was chosen, so there is no timeline. Start again and pick one under Date to see how sentiment moves."));
  }
  const holder = h("div");
  const names = Object.keys(time.series);
  const buttons = names.map((name) => h("button", { type: "button", text: name === "week" ? "Weekly" : "Monthly", "aria-pressed": String(name === time.default), onclick: () => draw(name) }));
  function draw(name) {
    const points = time.series[name];
    buttons.forEach((button, i) => button.setAttribute("aria-pressed", String(names[i] === name)));
    const middle = points[Math.floor((points.length - 1) / 2)];
    holder.replaceChildren(timeChart(points),
      h("div", { class: "axis" }, h("span", { text: points[0].period }), points.length > 2 && h("span", { text: middle.period }), points.length > 1 && h("span", { text: points.at(-1).period })),
      h("p", { class: "fine", text: "Each column of the chart is one period, filled from 0% at the bottom to 100% at the top; lines mark 25%, 50% and 75%." }),
      dataTable([name === "week" ? "Week starting" : "Month starting", "Positive#", "Neutral#", "Negative#", "Rows#"],
        points.map((point) => [point.period, point.positive, point.neutral, point.negative, point.total]), `Rows per ${name}`));
  }
  draw(time.default);
  return section("04", "Sentiment over time", time.takeaway,
    names.length > 1 && h("div", { class: "toggle", role: "group", "aria-label": "Group by" }, buttons),
    holder,
    h("ul", { class: "key" }, LABELS.map((label) => h("li", null, h("i", { class: CLS[label] }), label[0].toUpperCase() + label.slice(1)))),
    time.undated ? h("p", { class: "fine", text: `${num.format(time.undated)} scored rows have no readable date and are left out.` }) : null);
}

function categorySection(result) {
  const category = result.category;
  const title = `Sentiment by ${result.mapping.category || "category"}`;
  if (!category) {
    return section("05", title, null, empty(result.mapping.category ? "The category column has no values on the scored rows." : "No category column was chosen, so there is nothing to compare. Start again and pick one under Category."));
  }
  return section("05", title, category.takeaway,
    h("div", { role: "img", "aria-label": "Negative share to the left of the centre line, positive share to the right, one bar per category. The table below has the numbers." },
      category.groups.map((group) => h("div", { class: "div-row" },
        h("span", { class: "div-name", text: group.value }),
        h("span", { class: "div-bar" },
          sized(h("span", { class: "neg" }), "width", (100 * group.negative) / group.total),
          sized(h("span", { class: "pos" }), "width", (100 * group.positive) / group.total)),
        h("span", { class: "mono", text: `${pct(group.negative, group.total)}% − · ${pct(group.positive, group.total)}% + · ${num.format(group.total)}` })))),
    h("p", { class: "fine", text: "Left of the line: share negative. Right: share positive. Sorted from most positive to most negative." }),
    dataTable(["Category", "Positive#", "Neutral#", "Negative#", "Rows#"], category.groups.map((group) => [group.value, group.positive, group.neutral, group.negative, group.total]), "Rows per category"));
}

function termList(items, cls, valueOf, unit) {
  if (!items.length) return empty("Nothing stands out.");
  const top = Math.max(...items.map(valueOf));
  return h("ul", { class: "plain terms" }, items.slice(0, 10).map((item) =>
    h("li", null, h("span", { text: item.term }), h("span", { class: "bar" }, sized(h("i", { class: cls }), "width", (100 * valueOf(item)) / top)), h("span", { class: "mono", text: `${num.format(item.rows)} ${unit}` }))));
}

function driversSection(result) {
  const drivers = result.drivers;
  const column = (title, body) => h("div", null, h("h3", { class: "sub", text: title }), body);
  return section("06", "What drives it", drivers.takeaway,
    h("div", { class: "cols" },
      column("Terms pushing positive", termList(drivers.terms.positive, "pos", (item) => item.weight, "rows")),
      column("Terms pushing negative", termList(drivers.terms.negative, "neg", (item) => item.weight, "rows"))),
    h("p", { class: "fine", text: "Bar length is how much the term moved the scores in total; the count is how many rows contain it." }),
    h("div", { class: "cols" },
      column("Common phrases in positive rows", termList(drivers.phrases.positive, "pos", (item) => item.rows, "rows")),
      column("Common phrases in negative rows", termList(drivers.phrases.negative, "neg", (item) => item.rows, "rows"))));
}

function examplesSection(result) {
  const examples = result.examples;
  if (!examples.length) return section("07", "Examples", null, empty("No rows were scored, so there are no examples."));
  const body = h("tbody");
  const count = h("p", { class: "fine", role: "status" });
  const group = h("select", { id: "ex-group" }, ["Most positive", "Most negative", "Least confident"].map((name) => h("option", { value: name, text: name })));
  const search = h("input", { type: "search", id: "ex-search", placeholder: "Type to filter", autocomplete: "off" });
  function draw() {
    const needle = search.value.trim().toLowerCase();
    const rows = examples.filter((example) => example.group === group.value && (!needle || example.text.toLowerCase().includes(needle)));
    body.replaceChildren(...rows.map((example) => h("tr", null,
      h("td", { class: "n", text: num.format(example.row) }),
      h("td", null, example.text, example.terms.length ? h("div", { class: "terms-inline fine", text: `because of: ${example.terms.join(", ")}` }) : null),
      h("td", null, h("span", { class: "tag" }, h("i", { class: CLS[example.label] }), example.label)),
      h("td", { class: "n", text: example.probability.toFixed(2) }))));
    count.textContent = `${rows.length} ${rows.length === 1 ? "row" : "rows"} shown`;
  }
  group.addEventListener("change", draw);
  search.addEventListener("input", draw);
  draw();
  return section("07", "Examples", "The rows the model is surest about, and the ones it is least sure about.",
    h("div", { class: "filters" }, h("label", { for: "ex-group" }, "Show", group), h("label", { for: "ex-search" }, "Search these rows", search)),
    count,
    h("div", { class: "scroll" }, h("table", null,
      h("thead", null, h("tr", null, h("th", { scope: "col", class: "n", text: "Row" }), h("th", { scope: "col", text: "Text" }), h("th", { scope: "col", text: "Label" }), h("th", { scope: "col", class: "n", title: "Probability that the text is positive", text: "P(positive)" }))),
      body)));
}

function renderDashboard(result) {
  const report = (format) => `/api/report/${state.job}?format=${format}`;
  $("#view-dash").replaceChildren(
    h("h1", { class: "sr-only", tabindex: "-1", text: `Results for ${result.filename}` }),
    summarySection(result), overviewSection(result), distributionSection(result), timeSection(result),
    categorySection(result), driversSection(result), examplesSection(result),
    section("08", "About the model", null, h("p", { class: "lede", text: result.model.takeaway })),
    section("09", "Download", null,
      h("div", { class: "downloads" },
        h("a", { class: "button", href: report("html"), download: "", text: "Download report" }),
        h("a", { class: "button quiet", href: report("csv"), download: "", text: "Download scored rows (CSV)" }),
        h("a", { class: "button quiet", href: report("xlsx"), download: "", text: "Download scored rows (XLSX)" })),
      h("p", { class: "fine", text: "The report is a single web page you can open, share or print to PDF. The CSV and XLSX hold every row with its label and score." })));
}

// --- wiring ---------------------------------------------------------------------

$("#file").addEventListener("change", (event) => upload(event.target.files[0]));
const drop = $("#drop");
for (const type of ["dragenter", "dragover"]) drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.add("over"); });
for (const type of ["dragleave", "drop"]) drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.remove("over"); });
drop.addEventListener("drop", (event) => upload(event.dataTransfer.files[0]));
$("#map-form").addEventListener("submit", analyse);
$("#restart").addEventListener("click", restart);
$("#cancel").addEventListener("click", cancelJob);
loadSamples();
