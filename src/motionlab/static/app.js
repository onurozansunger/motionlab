const $ = (id) => document.getElementById(id);
const video = $("video"),
  canvas = $("overlay"),
  context = canvas.getContext("2d");
let current = null,
  index = null,
  results = [],
  selected = -1,
  activeTrack = null;
let pollTimer = null,
  searchGeneration = 0,
  animation = null,
  toastTimer = null;
const colorNames = {
  red: "Red",
  blue: "Blue",
  green: "Green",
  yellow: "Yellow",
  orange: "Orange",
  purple: "Purple",
  black: "Black",
  white: "White",
  gray: "Gray",
  unknown: "Unknown",
};
const classNames = {
  person: "Person",
  car: "Car",
  truck: "Truck",
  bus: "Bus",
  motorcycle: "Motorcycle",
  bicycle: "Bicycle",
  backpack: "Backpack",
  handbag: "Bag",
  suitcase: "Suitcase",
  dog: "Dog",
  cat: "Cat",
  chair: "Chair",
  "dining table": "Table",
  bottle: "Bottle",
  laptop: "Laptop",
  book: "Book",
  bird: "Bird",
  boat: "Boat",
  train: "Train",
  airplane: "Airplane",
  cup: "Cup",
  couch: "Couch",
  "cell phone": "Phone",
};
const swatches = {
  red: "#bd5850",
  blue: "#54799e",
  green: "#698d65",
  yellow: "#d6bf68",
  orange: "#cc9260",
  purple: "#9e79ac",
  black: "#414940",
  white: "#f1f0e9",
  gray: "#a1a99c",
  unknown: "#d7dccf",
};
const esc = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const time = (s) =>
  `${String(Math.floor((s || 0) / 60)).padStart(2, "0")}:${String(Math.floor((s || 0) % 60)).padStart(2, "0")}`;
const asset = (name) =>
  `/api/videos/${current.id}/assets/${encodeURIComponent(name)}`;
const label = (track) =>
  `${colorNames[track.color] || ""} ${classNames[track.label] || track.label}`.trim();
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 6000);
}
async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = "The request could not be completed.";
    try {
      const data = await response.json();
      message = typeof data.detail === "string" ? data.detail : message;
    } catch {}
    throw new Error(message);
  }
  return response.json();
}
function busy(on) {
  $("demo").disabled = on;
  $("empty-demo").disabled = on;
  $("upload").disabled = on;
  document.querySelector(".upload-label").style.opacity = on ? ".5" : "1";
}
async function refreshVideos(selectedId) {
  const videos = await api("/api/videos");
  const select = $("video-select");
  select.replaceChildren();
  if (!videos.length) select.add(new Option("No video selected", ""));
  for (const job of videos) select.add(new Option(job.name, job.id));
  if (selectedId) select.value = selectedId;
  return videos;
}
function resetView() {
  searchGeneration++;
  $("search-button").disabled = false;
  index = null;
  results = [];
  selected = -1;
  activeTrack = null;
  video.pause();
  video.removeAttribute("src");
  video.load();
  $("stage").classList.remove("has-video");
  $("object-id").textContent = "—";
  $("results").innerHTML =
    '<div class="empty"><strong>Find a moment in the video</strong><p>Describe an object or a scene.</p></div>';
  $("result-count").textContent = "RESULTS";
  $("search-mode").textContent = "";
  $("search-note").textContent = "";
  $("object-content").innerHTML =
    '<div class="object-placeholder"><p>Select a result or an object in the video.</p></div>';
  $("object-list").replaceChildren();
  $("object-list-count").textContent = "0";
  $("export-json").classList.add("disabled");
  $("export-csv").classList.add("disabled");
  $("stat-duration").textContent = "—";
  $("stat-tracks").textContent = "—";
  $("stat-samples").textContent = "—";
  renderTimeline();
  draw();
}
async function activate(job) {
  clearTimeout(pollTimer);
  resetView();
  current = job;
  $("video-select").value = job.id;
  $("video-empty").hidden = true;
  await updateJob(job);
}
async function updateJob(job) {
  if (!current || current.id !== job.id) return;
  current = job;
  $("processing").hidden = job.status !== "processing";
  busy(job.status === "processing");
  $("stat-status").textContent =
    job.status === "ready"
      ? "Ready to search"
      : job.status === "error"
        ? "Processing failed"
        : "Indexing";
  if (job.status === "processing") {
    $("processing-stage").textContent = job.stage;
    $("progress-bar").style.width = `${Math.round(job.progress * 100)}%`;
    $("progress-label").textContent = `${Math.round(job.progress * 100)}%`;
    pollTimer = setTimeout(async () => {
      try {
        await updateJob(await api(`/api/videos/${job.id}`));
      } catch (e) {
        toast(e.message);
      }
    }, 1200);
    return;
  }
  if (job.status === "error") {
    $("video-empty").hidden = false;
    toast(job.stage);
    return;
  }
  const loaded = await api(`/api/videos/${job.id}/index`);
  if (current?.id !== job.id) return;
  index = loaded;
  video.src = asset("video.mp4");
  $("stage").classList.add("has-video");
  $("video-resolution").textContent = `${job.width} × ${job.height}`;
  $("stat-duration").textContent = time(job.duration);
  $("stat-tracks").textContent = job.track_count;
  $("stat-samples").textContent = job.sample_count;
  $("timeline-mid").textContent = time(job.duration / 2);
  $("timeline-end").textContent = time(job.duration);
  $("to").placeholder = job.duration.toFixed(1);
  for (const kind of ["json", "csv"]) {
    const anchor = $(`export-${kind}`);
    anchor.href = `/api/videos/${job.id}/export/${kind}`;
    anchor.classList.remove("disabled");
  }
  renderObjects();
}
async function startDemo() {
  busy(true);
  try {
    const job = await api("/api/demo", { method: "POST" });
    await refreshVideos(job.id);
    await activate(job);
  } catch (e) {
    busy(false);
    toast(e.message);
  }
}
$("demo").onclick = startDemo;
$("empty-demo").onclick = startDemo;
$("upload").onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > 200 * 1024 * 1024) {
    toast("Videos must be 200 MB or smaller.");
    event.target.value = "";
    return;
  }
  busy(true);
  toast("Uploading video…");
  try {
    const form = new FormData();
    form.append("file", file);
    const job = await api("/api/videos", { method: "POST", body: form });
    await refreshVideos(job.id);
    await activate(job);
  } catch (e) {
    busy(false);
    toast(e.message);
  } finally {
    event.target.value = "";
  }
};
$("video-select").onchange = async () => {
  try {
    if ($("video-select").value)
      await activate(await api(`/api/videos/${$("video-select").value}`));
  } catch (e) {
    toast(e.message);
  }
};
async function search() {
  if (!index || current.status !== "ready") {
    toast("Upload a video and wait for indexing to finish.");
    return;
  }
  const query = $("query").value.trim();
  if (!query) return;
  const generation = ++searchGeneration;
  $("search-button").disabled = true;
  const parameters = new URLSearchParams({
    q: query,
    start: $("from").value || "0",
  });
  if ($("to").value) parameters.set("end", $("to").value);
  try {
    const data = await api(`/api/videos/${current.id}/search?${parameters}`);
    if (generation !== searchGeneration) return;
    results = data.results;
    selected = -1;
    activeTrack = null;
    $("search-note").textContent = data.note;
    $("search-mode").textContent = data.mode === "object" ? "OBJECT" : "SCENE";
    $("result-count").textContent =
      `${data.total} MATCHES${data.total > results.length ? " · FIRST 30" : ""}`;
    renderResults();
    renderTimeline();
    if (results.length) selectResult(0, false);
    else {
      $("object-id").textContent = "—";
      $("object-content").innerHTML =
        '<div class="object-placeholder"><p>No matching objects.</p></div>';
      draw();
      $("results").innerHTML =
        '<div class="empty"><strong>No matching objects</strong><p>Try another object, color, or a wider time range.</p></div>';
    }
  } catch (e) {
    toast(e.message);
  } finally {
    if (generation === searchGeneration) $("search-button").disabled = false;
  }
}
$("search-form").onsubmit = (event) => {
  event.preventDefault();
  search();
};
document.querySelectorAll("[data-query]").forEach(
  (button) =>
    (button.onclick = () => {
      $("query").value = button.dataset.query;
      search();
    }),
);
function renderResults() {
  const root = $("results");
  root.replaceChildren();
  results.forEach((result, i) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `result-card${selected === i ? " active" : ""}`;
    button.setAttribute(
      "aria-label",
      `${result.kind === "object" ? label(result) : "Scene match"}, ${time(result.start)} to ${time(result.end)}`,
    );
    button.innerHTML = `<img src="${asset(result.thumbnail)}" alt="Preview of the matching moment"><span class="result-text"><strong>${esc(result.kind === "object" ? label(result) : "Scene match")}</strong><span class="result-time">${time(result.start)} — ${time(result.end)}</span><span class="result-kind">${result.track_id !== null ? `Object #${result.track_id} · jump to moment` : "Visual similarity · jump to moment"}</span></span>`;
    button.onclick = () => selectResult(i, true);
    root.append(button);
  });
}
function renderTimeline() {
  const root = $("timeline");
  root.querySelectorAll("button").forEach((e) => e.remove());
  $("timeline-info").textContent = results.length
    ? `${results.length} intervals`
    : "No matches yet";
  if (!index) return;
  results.forEach((r, i) => {
    const button = document.createElement("button");
    button.style.left = `${(100 * r.start) / index.duration}%`;
    button.style.width = `${(100 * (r.end - r.start)) / index.duration}%`;
    button.className = selected === i ? "active" : "";
    button.title = `${time(r.start)} — ${time(r.end)}`;
    button.setAttribute("aria-label", `${i + 1}. play match`);
    button.onclick = (event) => {
      event.stopPropagation();
      selectResult(i, true);
    };
    root.append(button);
  });
}
function selectResult(i, play) {
  const result = results[i];
  if (!result) return;
  selected = i;
  activeTrack = result.track_id;
  video.currentTime = play ? result.start : result.seek;
  renderResults();
  renderTimeline();
  renderObject(result.track_id, result);
  draw();
  if (play) video.play().catch(() => {});
}
function renderObjects() {
  $("object-list-count").textContent = index.tracks.length;
  const root = $("object-list");
  root.replaceChildren();
  for (const track of [...index.tracks].sort(
    (a, b) => b.observations.length - a.observations.length,
  )) {
    const button = document.createElement("button");
    button.className = `object-item${activeTrack === track.id ? " active" : ""}`;
    button.innerHTML = `<i class="dot" style="background:${swatches[track.color] || "#ccc"}"></i><span>${esc(classNames[track.label] || track.label)}</span><span>#${track.id}</span>`;
    button.onclick = () => {
      activeTrack = track.id;
      video.currentTime = track.start;
      renderObject(track.id);
      draw();
    };
    root.append(button);
  }
}
function renderObject(trackId, result = null) {
  const track = index.tracks.find((t) => t.id === trackId);
  const root = $("object-content");
  $("object-id").textContent = track ? `#${track.id}` : "SCENE";
  if (!track) {
    if (result) {
      root.innerHTML = `<div class="object-detail"><img src="${asset(result.thumbnail)}" alt="Scene preview"><h3>Scene match</h3><dl><div><dt>Time</dt><dd>${time(result.start)}–${time(result.end)}</dd></div><div><dt>Cosine similarity</dt><dd>${result.score.toFixed(3)}</dd></div></dl><button id="download-clip" class="button secondary">↓ Download clip</button><p class="detail-note">Similarity is not a probability. Scene matches are not grounded to an object box.</p></div>`;
      $("download-clip").onclick = () => downloadClip(result.start, result.end);
    }
    renderObjects();
    return;
  }
  root.innerHTML = `<div class="object-detail"><img src="${asset(track.thumbnail)}" alt="${esc(label(track))}"><h3>${esc(classNames[track.label] || track.label)}</h3><dl><div><dt>Approximate color</dt><dd>${esc(colorNames[track.color] || track.color)}</dd></div><div><dt>First seen</dt><dd>${time(track.start)}</dd></div><div><dt>Last seen</dt><dd>${time(track.end)}</dd></div><div><dt>Observations</dt><dd>${track.observations.length}</dd></div></dl><button id="download-clip" class="button secondary">↓ Download clip</button><p class="detail-note">Color is estimated from the object crop. No brand or real-world identity is inferred.</p></div>`;
  $("download-clip").onclick = () =>
    downloadClip(result?.start ?? track.start, result?.end ?? track.end);
  renderObjects();
}
async function downloadClip(start, end) {
  const button = $("download-clip"),
    jobId = current.id;
  button.disabled = true;
  button.textContent = "Preparing clip…";
  try {
    const result = await api(`/api/videos/${jobId}/clip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start: Math.max(0, start - 1),
        end: Math.min(index.duration, end + 1),
      }),
    });
    const link = document.createElement("a");
    link.href = result.url;
    link.download = result.filename;
    link.className = "button secondary";
    link.textContent = "↓ Download MP4";
    button.replaceWith(link);
    toast("Clip ready. Select Download MP4 to save it.");
  } catch (e) {
    toast(e.message);
    button.disabled = false;
    button.textContent = "↓ Download clip";
  }
}
function observationAt(track, t) {
  let previous = null,
    next = null;
  for (const observation of track.observations) {
    if (observation.time <= t) previous = observation;
    else {
      next = observation;
      break;
    }
  }
  if (previous && next && next.time - previous.time <= 0.45) {
    const mix = (t - previous.time) / (next.time - previous.time);
    return {
      box: previous.box.map((value, i) => value + (next.box[i] - value) * mix),
    };
  }
  if (previous && t - previous.time <= 0.23) return previous;
  if (next && next.time - t <= 0.13) return next;
  return null;
}
function geometry() {
  const width = canvas.clientWidth,
    height = canvas.clientHeight;
  const scale = index
    ? Math.min(width / index.width, height / index.height)
    : 1;
  return {
    width,
    height,
    scale,
    x: index ? (width - index.width * scale) / 2 : 0,
    y: index ? (height - index.height * scale) / 2 : 0,
  };
}
function draw() {
  const dpr = window.devicePixelRatio || 1;
  const g = geometry();
  canvas.width = g.width * dpr;
  canvas.height = g.height * dpr;
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, g.width, g.height);
  if (!index) return;
  const t = video.currentTime;
  for (const track of index.tracks) {
    if (!$("show-boxes").checked && track.id !== activeTrack) continue;
    const observation = observationAt(track, t);
    if (!observation) continue;
    const [x1, y1, x2, y2] = observation.box;
    const selectedTrack = track.id === activeTrack;
    context.strokeStyle = selectedTrack ? "#b5ffcc" : "#95e9bd";
    context.lineWidth = selectedTrack ? 2.5 : 1;
    context.globalAlpha = selectedTrack ? 1 : 0.7;
    context.strokeRect(
      g.x + x1 * g.scale,
      g.y + y1 * g.scale,
      (x2 - x1) * g.scale,
      (y2 - y1) * g.scale,
    );
    if (selectedTrack) {
      const trail = track.observations.filter(
        (o) => o.time <= t && o.time >= t - 2,
      );
      context.beginPath();
      trail.forEach((o, i) => {
        const x = g.x + ((o.box[0] + o.box[2]) / 2) * g.scale,
          y = g.y + o.box[3] * g.scale;
        i ? context.lineTo(x, y) : context.moveTo(x, y);
      });
      context.stroke();
    }
    context.font = "10px -apple-system, sans-serif";
    const text = `#${track.id} ${classNames[track.label] || track.label}`;
    const x = g.x + x1 * g.scale,
      y = Math.max(17, g.y + y1 * g.scale);
    const width = context.measureText(text).width + 10;
    context.fillStyle = selectedTrack ? "#b5ffcc" : "#95e9bd";
    context.fillRect(x, y - 17, width, 16);
    context.fillStyle = "#163829";
    context.globalAlpha = 1;
    context.fillText(text, x + 5, y - 5);
  }
  $("timestamp").textContent = `${time(t)} / ${time(index.duration)}`;
  $("playhead").style.left = `${(100 * t) / index.duration}%`;
}
canvas.onclick = (event) => {
  if (!index) return;
  const rect = canvas.getBoundingClientRect(),
    g = geometry();
  const x = (event.clientX - rect.left - g.x) / g.scale,
    y = (event.clientY - rect.top - g.y) / g.scale;
  const candidates = index.tracks.filter((track) => {
    const o = observationAt(track, video.currentTime);
    return (
      o && x >= o.box[0] && x <= o.box[2] && y >= o.box[1] && y <= o.box[3]
    );
  });
  if (candidates.length) {
    activeTrack = candidates[0].id;
    renderObject(activeTrack);
    draw();
  }
};
function animate() {
  draw();
  if (!video.paused) animation = requestAnimationFrame(animate);
}
video.onplay = () => {
  cancelAnimationFrame(animation);
  animate();
};
video.onpause = draw;
video.ontimeupdate = draw;
video.onseeked = draw;
video.onloadedmetadata = draw;
$("show-boxes").onchange = draw;
window.addEventListener("resize", draw);
$("timeline").onclick = (event) => {
  if (!index) return;
  const rect = $("timeline").getBoundingClientRect();
  video.currentTime = Math.max(
    0,
    Math.min(
      index.duration,
      ((event.clientX - rect.left) / rect.width) * index.duration,
    ),
  );
  draw();
};
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "k") {
    event.preventDefault();
    $("query").focus();
  }
});
(async () => {
  try {
    const videos = await refreshVideos();
    if (videos.length)
      await activate(
        videos.find((job) => job.status === "processing") ||
          videos.find((job) => job.status === "ready") ||
          videos[0],
      );
    const health = await api("/health");
    if (!health.demo_available) {
      $("demo").hidden = true;
      $("empty-demo").hidden = true;
    }
  } catch (e) {
    toast(e.message);
  }
})();
