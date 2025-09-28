const POLL_INTERVAL = 1000;
const LOG_TAIL_REFRESH_INTERVAL = 4000;
const stateIndex = new Map();
const logPanels = new Map();
let logOrder = [];
let logPage = 0;
let layoutColumns = 1;
let pollTimer = null;
let activeTaskId = null;
let refreshJob = null;
const INFO_REFRESH_INTERVAL = 15000;

function toggleTheme() {
  const currentTheme = document.documentElement.getAttribute('data-theme');
  const newTheme = currentTheme === 'light' ? null : 'light';
  
  if (newTheme) {
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
  } else {
    document.documentElement.removeAttribute('data-theme');
    localStorage.setItem('theme', 'dark');
  }
}

function loadTheme() {
  const savedTheme = localStorage.getItem('theme');
  if (savedTheme === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
  }
}

function setupControls() {
  const refreshButton = document.getElementById("refresh-button");
  if (refreshButton) {
    refreshButton.addEventListener("click", () => {
      refreshState();
      restartAutoRefresh();
    });
  }

  const themeToggle = document.getElementById("theme-toggle");
  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      toggleTheme();
    });
  }

  document.querySelectorAll(".layout-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".layout-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      updateLayout(Number(btn.dataset.columns));
      restartAutoRefresh();
    });
  });

  const prevBtn = document.getElementById("log-prev");
  if (prevBtn) {
    prevBtn.addEventListener("click", () => {
      if (logPage > 0) {
        logPage -= 1;
        renderLogGrid();
        restartAutoRefresh();
      }
    });
  }

  const nextBtn = document.getElementById("log-next");
  if (nextBtn) {
    nextBtn.addEventListener("click", () => {
      const totalPages = getTotalPages();
      if (logPage < totalPages - 1) {
        logPage += 1;
        renderLogGrid();
        restartAutoRefresh();
      }
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadTheme();
  setupControls();
  refreshState();
  startAutoRefresh();
  window.addEventListener("beforeunload", () => {
    if (pollTimer) clearInterval(pollTimer);
    logPanels.forEach((panel) => {
      if (panel.socket) panel.socket.close();
    });
  });
});

function startAutoRefresh() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  pollTimer = setInterval(refreshState, POLL_INTERVAL);
}

function restartAutoRefresh() {
  startAutoRefresh();
}

async function refreshState() {
  if (refreshJob) {
    return refreshJob;
  }
  refreshJob = (async () => {
    try {
      const res = await fetch("/api/state");
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      rebuildIndex(data);
      renderSummary(data);
      renderSections(data);
      document.getElementById("last-updated").textContent = `最近刷新: ${new Date().toLocaleTimeString()}`;
      refreshLogStreams();
    } catch (err) {
      console.error(err);
    } finally {
      refreshJob = null;
    }
  })();
  return refreshJob;
}

function rebuildIndex(state) {
  stateIndex.clear();
  [
    ["pending", state.pending || []],
    ["running", state.running || []],
    ["finished", state.finished || []],
    ["errors", state.errors || []],
  ].forEach(([section, items]) => {
    items.forEach((item) => {
      stateIndex.set(item.id, { section, record: item });
    });
  });
}

function renderSummary(state) {
  const summary = state.summary || {};
  const text = `总数 ${summary.total ?? 0} · Pending ${summary.pending ?? 0} · Running ${summary.running ?? 0} · Finished ${summary.finished ?? 0} · Error ${summary.errors ?? 0}`;
  document.getElementById("summary").textContent = text;
}

function renderSections(state) {
  renderSection("pending", state.pending || []);
  renderSection("running", state.running || []);
  renderSection("finished", state.finished || []);
  renderSection("errors", state.errors || []);
  if (activeTaskId) {
    setActiveTask(activeTaskId);
  }
  syncOpenPanelTitles();
}

function refreshLogStreams() {
  const now = Date.now();
  logPanels.forEach((panel, taskId) => {
    const info = stateIndex.get(taskId);
    if (!info) return;
    const currentRunId = info.record.run_id || null;
    const socketReady = panel.socket ? panel.socket.readyState : WebSocket.CLOSED;
    const runChanged = panel.runId !== currentRunId;

    if (runChanged) {
      panel.status.textContent = "重新连接日志...";
      panel.pre.textContent = "加载中...";
      loadLogTail(panel, taskId, currentRunId);
      startLogStream(panel, taskId, currentRunId);
      return;
    }

    if (!panel.socket || socketReady === WebSocket.CLOSING || socketReady === WebSocket.CLOSED) {
      startLogStream(panel, taskId, currentRunId);
    } else if (panel.mode === "log" && now - (panel.lastTailRefresh || 0) >= LOG_TAIL_REFRESH_INTERVAL) {
      loadLogTail(panel, taskId, currentRunId);
    }
  });
}

function createIconButton(symbol, label, handler, extraClass = "") {
  const btn = document.createElement("button");
  btn.className = ["icon-btn", extraClass].filter(Boolean).join(" ");
  btn.type = "button";
  btn.title = label;
  btn.setAttribute("aria-label", label);
  btn.innerHTML = `<span class="mdi">${symbol}</span>`;
  btn.addEventListener("click", (event) => {
    event.stopPropagation();
    if (btn.disabled) return;
    handler(event);
  });
  return btn;
}

function calculateDuration(startISO, endISO) {
  const start = new Date(startISO);
  const end = new Date(endISO);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null;
  const diff = end.getTime() - start.getTime();
  if (diff <= 0) return null;
  const minutes = Math.floor(diff / 60000);
  const seconds = Math.floor((diff % 60000) / 1000);
  if (minutes >= 1) {
    return `${minutes}m${seconds.toString().padStart(2, "0")}s`;
  }
  return `${seconds}s`;
}

function setActiveTask(taskId) {
  activeTaskId = taskId;
  document.querySelectorAll(".task-item.active").forEach((elem) => elem.classList.remove("active"));
  if (!taskId) return;
  const target = document.querySelector(`.task-item[data-task-id="${taskId}"]`);
  if (target) {
    target.classList.add("active");
  } else {
    activeTaskId = null;
  }
}

async function handleRetry(taskId) {
  await sendCommand("retry_error", { id: taskId });
  await refreshState();
  restartAutoRefresh();
}

function syncOpenPanelTitles() {
  logPanels.forEach((panel, taskId) => {
    const info = stateIndex.get(taskId);
    if (!info) return;
    panel.section = info.section;
    panel.title.textContent = `${info.record.name} [${info.section}]`;
    updatePanelActionAvailability(panel);
    if (
      panel.mode === "info" &&
      !panel.detailLoading &&
      Date.now() - (panel.infoLastFetched || 0) >= INFO_REFRESH_INTERVAL
    ) {
      populatePanelInfo(panel, taskId);
    }
  });
}

function renderSection(section, items) {
  const container = document.getElementById(`${section}-list`);
  if (!container) return;
  const existing = new Map();
  Array.from(container.children).forEach((child) => {
    if (child.classList.contains("empty")) return;
    const id = child.dataset.taskId;
    if (id) existing.set(id, child);
  });

  if (!items.length) {
    container.replaceChildren(createEmptyPlaceholder());
    return;
  }

  const fragment = document.createDocumentFragment();

  items.forEach((item) => {
    let node = existing.get(item.id);
    if (node) {
      existing.delete(item.id);
      updateTaskItem(node, section, item);
    } else {
      node = createTaskItem(section, item);
    }
    fragment.appendChild(node);
  });

  existing.forEach((node) => node.remove());

  container.replaceChildren(fragment);
}

function createTaskItem(section, item) {
  const wrapper = document.createElement("div");
  wrapper.className = "task-item";
  wrapper.dataset.taskId = item.id;
  wrapper.title = buildTaskTooltip(item);

  if (item.id === activeTaskId) {
    wrapper.classList.add("active");
  }

  const header = document.createElement("div");
  header.className = "task-header";

  const title = document.createElement("div");
  title.className = "task-name";
  title.textContent = item.name || "-";

  const badge = document.createElement("span");
  badge.className = "task-meta task-badge";
  const attempt = item.attempt ?? 0;
  badge.textContent = `attempt ${attempt}`;

  header.appendChild(title);
  header.appendChild(badge);

  const actions = document.createElement("div");
  actions.className = "task-actions";

  const deleteBtn = createIconButton("🗑", "删除记录", async () => {
    if (deleteBtn.disabled) return;
    deleteBtn.disabled = true;
    try {
      await handleDelete(section, item.id);
    } finally {
      setTimeout(() => {
        deleteBtn.disabled = false;
      }, 800);
    }
  }, "danger");
  actions.appendChild(deleteBtn);

  if (section === "errors") {
    const retryBtn = createIconButton("↻", "重跑任务", async () => {
      if (retryBtn.disabled) return;
      retryBtn.disabled = true;
      try {
        await handleRetry(item.id);
      } finally {
        setTimeout(() => {
          retryBtn.disabled = false;
        }, 800);
      }
    }, "warning");
    actions.appendChild(retryBtn);
  }

  const topRow = document.createElement("div");
  topRow.className = "task-top";
  topRow.appendChild(header);
  topRow.appendChild(actions);

  wrapper.appendChild(topRow);
  wrapper.addEventListener("click", () => openLogPanel(item.id));

  return wrapper;
}

function updateTaskItem(wrapper, section, item) {
  wrapper.dataset.taskId = item.id;
  wrapper.title = buildTaskTooltip(item);
  wrapper.classList.toggle("active", item.id === activeTaskId);

  const title = wrapper.querySelector(".task-name");
  if (title) {
    title.textContent = item.name || "-";
  }

  const badge = wrapper.querySelector(".task-badge");
  if (badge) {
    const attempt = item.attempt ?? 0;
    badge.textContent = `attempt ${attempt}`;
  }
}

function createEmptyPlaceholder() {
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.textContent = "暂无记录";
  return empty;
}

function buildTaskTooltip(item) {
  const parts = [];
  if (item.created_at) parts.push(`创建: ${item.created_at}`);
  if (item.started_at) parts.push(`开始: ${item.started_at}`);
  if (item.completed_at) parts.push(`完成: ${item.completed_at}`);
  if (item.started_at && item.completed_at) {
    const duration = calculateDuration(item.started_at, item.completed_at);
    if (duration) parts.push(`耗时: ${duration}`);
  }
  return parts.join("\n");
}

async function handleDelete(section, taskId) {
  if (section === "pending") {
    await sendCommand("remove_pending", { id: taskId });
  } else if (section === "running") {
    await sendCommand("terminate_running", { id: taskId });
  } else if (section === "finished") {
    await sendCommand("remove_finished", { id: taskId });
  } else if (section === "errors") {
    await sendCommand("remove_error", { id: taskId });
  }
  
  // Auto-close monitoring panel when experiment is deleted
  closeLogPanel(taskId);
  
  // Force immediate refresh and ensure state is updated
  await refreshState();
  
  // Add a small delay to ensure UI is updated, then trigger another refresh
  setTimeout(async () => {
    await refreshState();
  }, 100);
  
  restartAutoRefresh();
}

async function sendCommand(action, payload) {
  try {
    const res = await fetch("/api/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, payload }),
    });
    if (!res.ok) throw new Error(await res.text());
  } catch (err) {
    console.error(err);
  }
}

async function fetchTaskDetails(taskId) {
  const res = await fetch(`/api/tasks/${taskId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function populatePanelInfo(panel, taskId) {
  panel.infoView.innerHTML = '<div class="info-placeholder">加载中...</div>';
  panel.detailLoaded = false;
  panel.detailLoading = true;
  try {
    const data = await fetchTaskDetails(taskId);
    panel.detailLoaded = true;
    panel.detailData = data;
    panel.section = data.section;
    panel.infoLastFetched = Date.now();
    renderInfoView(panel, taskId, data);
  } catch (err) {
    panel.infoView.innerHTML = `<div class="info-placeholder error">${err}</div>`;
  } finally {
    panel.detailLoading = false;
    if (!panel.infoLastFetched) {
      panel.infoLastFetched = Date.now();
    }
  }
}

function renderInfoView(panel, taskId, data) {
  const container = panel.infoView;
  container.innerHTML = "";

  const infoSection = document.createElement("section");
  infoSection.className = "info-section";
  const title = document.createElement("h3");
  title.textContent = "基本信息";
  infoSection.appendChild(title);

  const list = document.createElement("ul");
  list.className = "info-list";

  const entries = [
    ["开始时间", data.task?.started_at],
    ["完成时间", data.task?.completed_at],
  ].filter(([, value]) => Boolean(value));

  if (entries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "info-placeholder";
    empty.textContent = "暂无时间信息";
    infoSection.appendChild(empty);
  } else {
    entries.forEach(([label, value]) => {
      const item = document.createElement("li");
      const labelNode = document.createElement("span");
      labelNode.textContent = label;
      const valueNode = document.createElement("code");
      valueNode.textContent = value || "-";
      item.appendChild(labelNode);
      item.appendChild(valueNode);
      list.appendChild(item);
    });
    infoSection.appendChild(list);
  }
  container.appendChild(infoSection);

  if (data.metadata) {
    const metadataSection = document.createElement("section");
    metadataSection.className = "info-section";
    const metadataTitle = document.createElement("h3");
    metadataTitle.textContent = "Metadata";
    const metadataPre = document.createElement("pre");
    metadataPre.className = "info-metadata";
    metadataPre.textContent = JSON.stringify(data.metadata, null, 2);
    metadataSection.appendChild(metadataTitle);
    metadataSection.appendChild(metadataPre);
    container.appendChild(metadataSection);
  }

  const logs = data.terminal_logs || [];
  const logSection = document.createElement("section");
  logSection.className = "info-section";
  const logTitle = document.createElement("h3");
  logTitle.textContent = "日志";
  logSection.appendChild(logTitle);

  if (logs.length) {
    const logList = document.createElement("ul");
    logList.className = "info-list";
    logs.forEach((log) => {
      const li = document.createElement("li");
      const button = createIconButton("🗒", `查看 ${log.name}`, () => {
        openLogPanel(taskId, log.run_id);
      });
      const label = document.createElement("span");
      label.textContent = `${log.name} (${log.updated_at || "-"})`;
      li.appendChild(label);
      li.appendChild(button);
      logList.appendChild(li);
    });
    logSection.appendChild(logList);
  } else {
    const empty = document.createElement("div");
    empty.className = "info-placeholder";
    empty.textContent = "暂无日志";
    logSection.appendChild(empty);
  }
  container.appendChild(logSection);

  const metrics = data.metrics || [];
  const metricSection = document.createElement("section");
  metricSection.className = "info-section";
  const metricTitle = document.createElement("h3");
  metricTitle.textContent = "指标";
  metricSection.appendChild(metricTitle);

  if (metrics.length) {
    const metricList = document.createElement("ul");
    metricList.className = "info-list";
    metrics.forEach((metric) => {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = `${metric.name} (${metric.rows} rows)`;
      const button = createIconButton("⬇", `下载 ${metric.name}`, () => {
        downloadMetric(taskId, metric.name);
      });
      li.appendChild(label);
      li.appendChild(button);
      metricList.appendChild(li);
    });
    metricSection.appendChild(metricList);
  } else {
    const emptyMetric = document.createElement("div");
    emptyMetric.className = "info-placeholder";
    emptyMetric.textContent = "暂无指标";
    metricSection.appendChild(emptyMetric);
  }

  container.appendChild(metricSection);
}

async function downloadMetric(taskId, metricName) {
  try {
    const res = await fetch(`/api/tasks/${taskId}/metrics/${encodeURIComponent(metricName)}`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${taskId}-${metricName}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    console.error(err);
  }
}

function updateLayout(columns) {
  layoutColumns = columns;
  const grid = document.getElementById("log-grid");
  grid.classList.remove("columns-1", "columns-2", "columns-4");
  grid.classList.add(`columns-${columns}`);
  renderLogGrid();
}

async function openLogPanel(taskId, runId = null) {
  const info = stateIndex.get(taskId);
  if (!info) return;

  let panel = logPanels.get(taskId);
  if (!panel) {
    panel = createLogPanel(info.record, info.section);
    panel.taskId = taskId;
    panel.socket = null;
    panel.runId = null;
    logPanels.set(taskId, panel);
  } else {
    panel.title.textContent = `${info.record.name} [${info.section}]`;
    panel.taskId = taskId;
  }

  panel.section = info.section;
  updatePanelActionAvailability(panel);
  const targetRun = runId || panel.runId || info.record.run_id || null;

  // Move panel to the front (first position) instead of back
  logOrder = logOrder.filter((id) => id !== taskId);
  logOrder.unshift(taskId);
  logPage = 0; // Go to first page to show the newly added panel
  renderLogGrid();
  setActiveTask(taskId);
  switchPanelMode(panel, "log");

  panel.pre.textContent = "加载中...";
  panel.status.textContent = "";

  await loadLogTail(panel, taskId, targetRun);
  startLogStream(panel, taskId, targetRun);

  populatePanelInfo(panel, taskId);
}

async function loadLogTail(panel, taskId, runId) {
  try {
    const params = new URLSearchParams({ tail: "200" });
    if (runId) params.set("run_id", runId);
    const res = await fetch(`/api/tasks/${taskId}/logs?${params.toString()}`);
    if (!logPanels.has(taskId)) return;
    if (res.ok) {
      const payload = await res.json();
      const lines = Array.isArray(payload.lines) ? payload.lines : [];
      panel.pre.textContent = lines.join("\n");
      panel.pre.scrollTop = panel.pre.scrollHeight;
      panel.lastTailRefresh = Date.now();
    } else {
      panel.pre.textContent = await res.text();
    }
  } catch (err) {
    panel.pre.textContent = String(err);
  }
  if (!panel.lastTailRefresh) {
    panel.lastTailRefresh = Date.now();
  }
}

function startLogStream(panel, taskId, runId) {
  if (panel.socket) {
    try {
      panel.socket.close();
    } catch (err) {
      console.debug("close socket failed", err);
    }
  }

  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${window.location.host}/ws/logs/${taskId}${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`;
  const socket = new WebSocket(url);
  panel.socket = socket;
  panel.runId = runId || null;

  socket.onmessage = (event) => {
    if (!logPanels.has(taskId)) return;
    const message = JSON.parse(event.data);
    if (message.event === "append") {
      const joined = `${panel.pre.textContent}\n${message.lines.join("\n")}`;
      panel.pre.textContent = joined.trimStart();
      panel.pre.scrollTop = panel.pre.scrollHeight;
    } else if (message.event === "info") {
      panel.status.textContent = message.message;
    } else if (message.event === "error") {
      panel.status.textContent = message.message;
    }
  };

  socket.onclose = () => {
    if (!logPanels.has(taskId)) return;
    panel.status.textContent = "连接已关闭";
  };

  socket.onerror = () => {
    if (!logPanels.has(taskId)) return;
    panel.status.textContent = "连接错误";
  };
}

function createLogPanel(record, section) {
  let panel = null;
  const wrapper = document.createElement("div");
  wrapper.className = "log-panel";
  wrapper.dataset.taskId = record.id;

  const header = document.createElement("header");
  header.className = "log-header";
  const title = document.createElement("div");
  title.className = "log-title";
  title.textContent = `${record.name} [${section}]`;

  const controls = document.createElement("div");
  controls.className = "log-header-actions";
  const deleteBtn = createIconButton("🗑", "删除记录", async () => {
    if (deleteBtn.disabled) return;
    const currentPanel = logPanels.get(record.id); // Look up panel dynamically
    if (!currentPanel) return;
    deleteBtn.disabled = true;
    try {
      await handleDelete(currentPanel.section, currentPanel.taskId);
    } finally {
      setTimeout(() => {
        deleteBtn.disabled = false;
      }, 800);
    }
  }, "danger");
  const retryBtn = createIconButton("↻", "重跑任务", async () => {
    if (retryBtn.disabled) return;
    const currentPanel = logPanels.get(record.id); // Look up panel dynamically
    if (!currentPanel) return;
    retryBtn.disabled = true;
    try {
      await handleRetry(currentPanel.taskId);
    } finally {
      setTimeout(() => {
        retryBtn.disabled = false;
      }, 800);
    }
  }, "warning");
  retryBtn.classList.add("hidden");
  const toggleBtn = createIconButton("ℹ", "查看基本信息", () => {
    const currentPanel = logPanels.get(record.id); // Look up panel dynamically
    if (currentPanel) {
      switchPanelMode(currentPanel);
    }
  });
  const closeBtn = createIconButton("✕", "关闭面板", () => closeLogPanel(record.id));
  controls.appendChild(deleteBtn);
  controls.appendChild(retryBtn);
  controls.appendChild(toggleBtn);
  controls.appendChild(closeBtn);

  header.appendChild(title);
  header.appendChild(controls);

  const body = document.createElement("div");
  body.className = "log-body";

  const pre = document.createElement("pre");
  pre.textContent = "等待数据...";
  const infoView = document.createElement("div");
  infoView.className = "info-view hidden";

  body.appendChild(pre);
  body.appendChild(infoView);

  const footer = document.createElement("footer");
  const status = document.createElement("span");
  status.textContent = "";
  footer.appendChild(status);

  wrapper.appendChild(header);
  wrapper.appendChild(body);
  wrapper.appendChild(footer);

  panel = {
    element: wrapper,
    pre,
    infoView,
    status,
    title,
    toggleBtn,
  section,
    deleteBtn,
    retryBtn,
    mode: "log",
    taskId: record.id,
    detailLoaded: false,
    detailLoading: false,
    lastTailRefresh: 0,
    infoLastFetched: 0,
  };

  updatePanelActionAvailability(panel);

  return panel;
}

function updatePanelActionAvailability(panel) {
  if (!panel.deleteBtn) return;
  const section = panel.section;
  let deleteLabel = "删除记录";
  if (section === "pending") deleteLabel = "移除排队";
  else if (section === "running") deleteLabel = "终止运行";
  else if (section === "errors") deleteLabel = "删除错误记录";
  panel.deleteBtn.title = deleteLabel;
  panel.deleteBtn.setAttribute("aria-label", deleteLabel);
  // Ensure buttons are always enabled
  panel.deleteBtn.disabled = false;
  if (panel.retryBtn) {
    const shouldShow = section === "errors";
    panel.retryBtn.classList.toggle("hidden", !shouldShow);
    panel.retryBtn.disabled = false;
  }
}

function switchPanelMode(panel, nextMode = null) {
  const desired = nextMode || (panel.mode === "log" ? "info" : "log");
  panel.mode = desired;
  if (desired === "info") {
    panel.pre.classList.add("hidden");
    panel.infoView.classList.remove("hidden");
    const icon = panel.toggleBtn.querySelector(".mdi");
    if (icon) icon.textContent = "🖥";
    panel.toggleBtn.title = "查看日志";
    panel.toggleBtn.setAttribute("aria-label", "查看日志");
    if (!panel.detailLoaded && !panel.detailLoading && panel.taskId) {
      populatePanelInfo(panel, panel.taskId);
    }
  } else {
    panel.pre.classList.remove("hidden");
    panel.infoView.classList.add("hidden");
    const icon = panel.toggleBtn.querySelector(".mdi");
    if (icon) icon.textContent = "ℹ";
    panel.toggleBtn.title = "查看基本信息";
    panel.toggleBtn.setAttribute("aria-label", "查看基本信息");
  }
}

function closeLogPanel(taskId) {
  const panel = logPanels.get(taskId);
  if (!panel) return;
  if (panel.socket) {
    try {
      panel.socket.close();
    } catch (err) {
      console.debug("close socket failed", err);
    }
  }
  panel.element.remove();
  logPanels.delete(taskId);
  logOrder = logOrder.filter((id) => id !== taskId);
  if (logOrder.length === 0) {
    logPage = 0;
  }
  const nextActive = logOrder.length ? logOrder[logOrder.length - 1] : null;
  if (taskId === activeTaskId) {
    setActiveTask(nextActive);
  }
  const totalPages = getTotalPages();
  if (logPage >= totalPages) logPage = Math.max(totalPages - 1, 0);
  renderLogGrid();
}

function renderLogGrid() {
  const grid = document.getElementById("log-grid");
  const controls = document.getElementById("log-controls");
  grid.innerHTML = "";

  if (logOrder.length === 0) {
    controls.classList.add("hidden");
    updatePager(0);
    return;
  }

  const totalPages = getTotalPages();
  if (logPage >= totalPages) logPage = totalPages - 1;
  if (logPage < 0) logPage = 0;

  const start = logPage * layoutColumns;
  const end = start + layoutColumns;
  const visible = logOrder.slice(start, end);

  visible.forEach((taskId) => {
    const panel = logPanels.get(taskId);
    if (panel) {
      grid.appendChild(panel.element);
      switchPanelMode(panel, panel.mode); // ensure mode state reflects DOM classes
    }
  });

  controls.classList.toggle("hidden", totalPages <= 1 && logOrder.length <= layoutColumns);
  updatePager(totalPages);
}

function updatePager(totalPages) {
  const indicator = document.getElementById("log-page-indicator");
  const prevBtn = document.getElementById("log-prev");
  const nextBtn = document.getElementById("log-next");

  if (logOrder.length === 0) {
    indicator.textContent = "暂无监控窗口";
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    return;
  }

  indicator.textContent = `第 ${logPage + 1} 页 / 共 ${totalPages} 页`;
  prevBtn.disabled = logPage <= 0;
  nextBtn.disabled = logPage >= totalPages - 1;
}

function getTotalPages() {
  if (logOrder.length === 0) return 1;
  return Math.ceil(logOrder.length / layoutColumns);
}

