const state = {
  sessionId:
    localStorage.getItem("dojo-session-id") || crypto.randomUUID(),
  uploadedFile: null,
};

localStorage.setItem("dojo-session-id", state.sessionId);

const elements = {
  form: document.querySelector("#chat-form"),
  input: document.querySelector("#chat-input"),
  messages: document.querySelector("#messages"),
  send: document.querySelector("#send-button"),
  sessionLabel: document.querySelector("#session-label"),
  workflowState: document.querySelector("#workflow-state"),
  file: document.querySelector("#report-file"),
  dropZone: document.querySelector("#drop-zone"),
  uploadStatus: document.querySelector("#upload-status"),
  importSuggestion: document.querySelector("#import-suggestion"),
  context: document.querySelector("#context-list"),
  approvals: document.querySelector("#approval-list"),
  refreshApprovals: document.querySelector("#refresh-approvals"),
  clearSession: document.querySelector("#clear-session"),
  approvalTemplate: document.querySelector("#approval-template"),
};

elements.sessionLabel.textContent = `会话 ${state.sessionId.slice(0, 8)}`;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(readError(payload));
  }
  return payload;
}

function readError(payload) {
  if (typeof payload.detail === "string") {
    return payload.detail;
  }
  if (Array.isArray(payload.detail)) {
    return payload.detail
      .map((item) => item.msg || "请求参数无效")
      .join("；");
  }
  return "请求执行失败，请检查服务状态。";
}

function addMessage(kind, content, details = "") {
  const article = document.createElement("article");
  article.className = `message ${kind}-message`;

  const avatar = document.createElement("span");
  avatar.className = "message-avatar";
  avatar.textContent = kind === "user" ? "你" : "SG";

  const body = document.createElement("div");
  const paragraph = document.createElement("p");
  paragraph.className = "message-text";
  paragraph.textContent = content;
  body.append(paragraph);

  if (details) {
    const summary = document.createElement("div");
    summary.className = "workflow-summary";
    summary.innerHTML = details;
    body.append(summary);
  }

  article.append(avatar, body);
  elements.messages.append(article);
  elements.messages.scrollTop = elements.messages.scrollHeight;
}

const STATUS_LABELS = {
  pending: "等待执行",
  running: "执行中",
  completed: "已完成",
  waiting_approval: "等待审批",
  need_input: "需要补充信息",
  blocked: "被依赖阻塞",
  not_implemented: "尚未实现",
  unknown: "无法识别",
  failed: "执行失败",
};

const INTENT_LABELS = {
  risk_acceptance: "风险接受",
  deduplication: "去重",
  triage: "分诊",
  remediation: "修复计划",
  verification: "修复验证",
  import_scan: "报告导入",
  query_findings: "漏洞查询",
  unknown: "未识别操作",
};

function statusLabel(status) {
  return STATUS_LABELS[status] || status || "未知状态";
}

function intentLabel(intent) {
  return INTENT_LABELS[intent] || intent || "未知步骤";
}

function setContext(context) {
  const values = [
    context.product_id ?? "--",
    context.test_id ?? "--",
    context.finding_ids?.length
      ? `${context.finding_ids.length} 个`
      : "--",
  ];

  [...elements.context.querySelectorAll("dd")].forEach((node, index) => {
    node.textContent = values[index];
  });
}

function stepOutputHtml(result) {
  if (!result || typeof result !== "object") {
    return "";
  }

  const parts = [];
  if (result.message) {
    parts.push(escapeHtml(result.message));
  }

  const output = result.output;
  if (typeof output === "string" && output.trim()) {
    parts.push(
      `<pre class="step-output">${escapeHtml(output.trim())}</pre>`,
    );
  } else if (output && typeof output === "object") {
    parts.push(
      `<pre class="step-output">${escapeHtml(
        JSON.stringify(output, null, 2),
      )}</pre>`,
    );
  }

  const findings = result.findings;
  if (findings && typeof findings === "object") {
    const rows = findings.results || [];
    parts.push(`共 ${rows.length} 个 findings`);
    if (rows.length) {
      const preview = rows
        .slice(0, 20)
        .map(
          (item) =>
            `#${escapeHtml(item.id)} · ${escapeHtml(item.severity || "?")} · ${escapeHtml(item.title || "")}`,
        )
        .join("\n");
      const suffix = rows.length > 20 ? `\n... 其余 ${rows.length - 20} 个略` : "";
      parts.push(
        `<pre class="step-output">${preview}${escapeHtml(suffix)}</pre>`,
      );
    }
  }

  if (Array.isArray(result.candidates) && result.candidates.length) {
    const preview = result.candidates
      .map(
        (item) =>
          `#${escapeHtml(item.finding_id)} · ${escapeHtml(item.severity || "?")} · ${escapeHtml(item.title || "")}`,
      )
      .join("\n");
    parts.push(`<pre class="step-output">${preview}</pre>`);
  }

  return parts.join("<br>");
}

function resultDetails(result) {
  const steps = result?.steps || [];
  const lines = steps
    .map((step) => {
      const head =
        `<span class="step-status step-${escapeHtml(step.status)}">` +
        `${escapeHtml(statusLabel(step.status))}</span> ` +
        `<b>${escapeHtml(step.step_id)}</b> ${escapeHtml(intentLabel(step.intent))}`;
      const detail = stepOutputHtml(step.result);
      return detail ? `${head}<div class="step-detail">${detail}</div>` : head;
    })
    .join("");
  const approval = steps.find(
    (step) => step.result?.approval_id,
  )?.result?.approval_id;

  return [
    `<b>工作流状态: ${escapeHtml(statusLabel(result?.status || "completed"))}</b>`,
    lines || "未返回步骤结果",
    approval ? `<br>审批编号: ${escapeHtml(approval)}` : "",
  ].join("");
}

function workflowDetails(response) {
  return resultDetails(response.result);
}

function renderProgress(progress) {
  const steps = progress?.steps || [];
  if (!steps.length) {
    return `<b>${escapeHtml(progress?.message || "正在解析请求并规划工作流...")}</b>`;
  }
  const lines = steps
    .map(
      (step) =>
        `<span class="step-status step-${escapeHtml(step.status)}">` +
        `${escapeHtml(statusLabel(step.status))}</span> ` +
        `<b>${escapeHtml(step.step_id)}</b> ${escapeHtml(intentLabel(step.intent))}`,
    )
    .join("");
  return `<b>${escapeHtml(progress.message || "工作流执行中...")}</b>${lines}`;
}

function createPendingMessage() {
  const article = document.createElement("article");
  article.className = "message assistant-message pending-message";

  const avatar = document.createElement("span");
  avatar.className = "message-avatar";
  avatar.textContent = "SG";

  const body = document.createElement("div");
  const paragraph = document.createElement("p");
  paragraph.className = "message-text";
  paragraph.innerHTML =
    'Agent 正在运行<span class="typing-dots"><span></span><span></span><span></span></span>';
  const summary = document.createElement("div");
  summary.className = "workflow-summary";
  summary.innerHTML = "<b>正在解析请求并规划工作流...</b>";
  body.append(paragraph, summary);

  article.append(avatar, body);
  elements.messages.append(article);
  elements.messages.scrollTop = elements.messages.scrollHeight;
  return { article, summary };
}

async function pollProgress(pending) {
  try {
    const progress = await api(
      `/api/sessions/${state.sessionId}/progress`,
    );
    if (progress.phase === "idle") {
      return;
    }
    pending.summary.innerHTML = renderProgress(progress);
    const runningStep = (progress.steps || []).find(
      (step) => step.status === "running",
    );
    elements.workflowState.textContent = runningStep
      ? `Agent 运行中 · ${intentLabel(runningStep.intent)}`
      : progress.phase === "planning"
        ? "Agent 运行中 · 规划工作流"
        : "Agent 运行中";
  } catch (error) {
    // 轮询失败不打断对话，仅保留当前显示。
  }
}

async function sendMessage(message) {
  addMessage("user", message);
  elements.send.disabled = true;
  elements.workflowState.textContent = "Agent 运行中";
  elements.workflowState.classList.add("state-running");

  const pending = createPendingMessage();
  const progressTimer = setInterval(() => pollProgress(pending), 1500);

  try {
    const context = state.uploadedFile
      ? {
          file_path: state.uploadedFile.file_path,
          scan_type: state.uploadedFile.scan_type,
        }
      : {};
    const response = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        session_id: state.sessionId,
        context,
      }),
    });

    clearInterval(progressTimer);
    pending.article.remove();
    addMessage(
      "assistant",
      response.result?.message || "工作流已处理，但未返回结果说明。",
      workflowDetails(response),
    );
    setContext(response.context);
    elements.workflowState.textContent = statusLabel(
      response.result?.status || "completed",
    );

    if (response.result?.status === "waiting_approval") {
      await loadApprovals();
    }
  } catch (error) {
    clearInterval(progressTimer);
    pending.article.remove();
    addMessage("assistant", `请求失败：${error.message}`);
    elements.workflowState.textContent = "需要检查";
  } finally {
    clearInterval(progressTimer);
    elements.workflowState.classList.remove("state-running");
    elements.send.disabled = false;
  }
}

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.input.value.trim();
  if (!message) {
    return;
  }
  elements.input.value = "";
  await sendMessage(message);
});

async function uploadFile(file) {
  if (!file) {
    return;
  }

  elements.uploadStatus.textContent = `正在上传 ${file.name}...`;
  const body = new FormData();
  body.append("file", file);

  try {
    const uploaded = await api("/api/uploads", {
      method: "POST",
      body,
    });
    state.uploadedFile = uploaded;
    elements.uploadStatus.textContent =
      `已暂存 ${uploaded.original_name} (${formatBytes(uploaded.size_bytes)})`;
    elements.importSuggestion.disabled = false;
  } catch (error) {
    state.uploadedFile = null;
    elements.uploadStatus.textContent = `上传失败：${error.message}`;
    elements.importSuggestion.disabled = true;
  }
}

elements.file.addEventListener("change", (event) => {
  uploadFile(event.target.files[0]);
});

["dragenter", "dragover"].forEach((name) => {
  elements.dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    elements.dropZone.classList.add("dragging");
  });
});

["dragleave", "drop"].forEach((name) => {
  elements.dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    elements.dropZone.classList.remove("dragging");
  });
});

elements.dropZone.addEventListener("drop", (event) => {
  uploadFile(event.dataTransfer.files[0]);
});

elements.importSuggestion.addEventListener("click", () => {
  elements.input.value =
    "导入刚刚上传的扫描报告，然后进行去重和分诊";
  elements.input.focus();
});

function formatBytes(value) {
  if (value < 1024 * 1024) {
    return `${Math.ceil(value / 1024)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

async function loadApprovals() {
  try {
    const data = await api("/api/approvals");
    renderApprovals(data.items);
  } catch (error) {
    elements.approvals.innerHTML =
      `<div class="empty-state">无法加载审批：${escapeHtml(error.message)}</div>`;
  }
}

function renderApprovals(items) {
  elements.approvals.innerHTML = "";

  if (!items.length) {
    elements.approvals.innerHTML =
      '<div class="empty-state">暂无待审批操作。风险接受建议会在这里暂停，等待人工决定。</div>';
    return;
  }

  items.forEach((approval) => {
    const node = elements.approvalTemplate.content.cloneNode(true);
    node.querySelector(".risk-tag").textContent =
      approval.risk_level.toUpperCase();
    node.querySelector("time").textContent = new Date(
      approval.created_at,
    ).toLocaleString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
    node.querySelector("h3").textContent = approval.title;
    node.querySelector(".approval-description").textContent =
      approval.description;

    const candidates = approval.payload?.approved_candidates || [];
    node.querySelector(".candidate-list").innerHTML = candidates
      .map(
        (candidate) =>
          `<div class="candidate">#${candidate.finding_id} · ${escapeHtml(candidate.severity)}<br>${escapeHtml(candidate.title)}</div>`,
      )
      .join("");

    node
      .querySelector(".approve-button")
      .addEventListener("click", () =>
        decideApproval(approval.approval_id, "approve"),
      );
    node
      .querySelector(".reject-button")
      .addEventListener("click", () =>
        decideApproval(approval.approval_id, "reject"),
      );
    elements.approvals.append(node);
  });
}

function escapeHtml(value) {
  return String(value || "").replace(
    /[&<>'"]/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "'": "&#39;",
        '"': "&quot;",
      })[character],
  );
}

async function decideApproval(approvalId, decision) {
  const action = decision === "approve" ? "批准" : "拒绝";
  if (!confirm(`确认${action}该风险接受操作？`)) {
    return;
  }

  try {
    await api(`/api/approvals/${approvalId}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        reviewer: "web-user",
      }),
    });
    addMessage("assistant", `审批 ${approvalId.slice(0, 8)} 已${action}。`);
    await loadApprovals();
  } catch (error) {
    addMessage("assistant", `审批失败：${error.message}`);
  }
}

elements.refreshApprovals.addEventListener("click", loadApprovals);

elements.clearSession.addEventListener("click", async () => {
  try {
    await api(`/api/sessions/${state.sessionId}`, {
      method: "DELETE",
    });
    setContext({});
    resetMessages();
    addMessage(
      "assistant",
      "本会话上下文与历史消息已清除。后续请求不会沿用之前的 ID。",
    );
  } catch (error) {
    addMessage("assistant", `清除上下文失败：${error.message}`);
  }
});

function resetMessages() {
  [...elements.messages.querySelectorAll(".message")]
    .slice(1)
    .forEach((node) => node.remove());
}

async function loadHistory() {
  try {
    const [history, context] = await Promise.all([
      api(`/api/sessions/${state.sessionId}/messages`),
      api(`/api/sessions/${state.sessionId}`),
    ]);
    setContext(context);
    history.items.forEach((item) => {
      addMessage(
        item.role === "user" ? "user" : "assistant",
        item.content,
        item.role === "assistant" && item.result?.steps
          ? resultDetails(item.result)
          : "",
      );
    });
  } catch (error) {
    // 历史加载失败不影响新对话，静默保留欢迎消息。
  }
}

loadHistory();
loadApprovals();
