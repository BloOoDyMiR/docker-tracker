const logEl = document.querySelector("#logs");
const LIVE_MS = 30000;
const POLL_MS = 3000;
const HEALTH_LINE = /(?:GET|HEAD)\s+\/(?:healthz?|readyz?|livez?)(?:[/?\s"']|$)/i;

if (logEl && logEl.dataset.url) {
  const emptyEl = document.querySelector("#logs-empty");
  const stateEl = document.querySelector("#log-state");
  const liveEl = document.querySelector("#log-live");
  const hideEl = document.querySelector("#log-hide-health");
  const downloadEl = document.querySelector("#log-download");
  const downloadChoices = document.querySelector("#log-download-choices");
  let poll = 0;
  let clock = 0;
  let endsAt = 0;
  let generation = 0;
  let rawLogs = logEl.textContent;

  function shownText(raw) {
    if (!hideEl || !hideEl.checked) {
      return raw;
    }
    return raw
      .split("\n")
      .filter((line) => !HEALTH_LINE.test(line))
      .join("\n");
  }

  function applyLogs(raw, jumpToEnd) {
    rawLogs = raw;
    const shown = shownText(raw);
    const atBottom = jumpToEnd || logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 48;
    if (shown !== logEl.textContent) {
      logEl.textContent = shown;
      if (atBottom) {
        logEl.scrollTop = logEl.scrollHeight;
      }
    }
    if (emptyEl) {
      emptyEl.hidden = shown.trim().length > 0;
    }
  }

  function markTail(tail) {
    document.querySelectorAll(".log-actions [data-tail]").forEach((button) => {
      button.classList.toggle("is-on", Number(button.dataset.tail) === tail);
    });
  }

  async function fetchLogs(tail, gen, jumpToEnd) {
    const response = await fetch(`${logEl.dataset.url}?tail=${tail}`, {
      headers: { Accept: "application/json" },
      redirect: "manual",
    });
    if (gen !== generation || !response.ok) {
      return false;
    }
    const data = await response.json();
    if (gen !== generation || typeof data.logs !== "string") {
      return false;
    }
    if (data.logs !== rawLogs) {
      applyLogs(data.logs, jumpToEnd);
    }
    return true;
  }

  function stop(message) {
    generation += 1;
    window.clearInterval(poll);
    window.clearInterval(clock);
    poll = 0;
    clock = 0;
    if (stateEl) {
      stateEl.textContent = message;
    }
    if (liveEl) {
      liveEl.disabled = false;
      liveEl.classList.remove("is-on");
    }
  }

  function start() {
    if (liveEl && liveEl.disabled) {
      return;
    }
    window.clearInterval(poll);
    window.clearInterval(clock);
    generation += 1;
    const gen = generation;
    markTail(0);
    if (liveEl) {
      liveEl.disabled = true;
      liveEl.classList.add("is-on");
    }
    endsAt = Date.now() + LIVE_MS;
    if (stateEl) {
      stateEl.textContent = "Live · 30s";
    }
    const follow = () => {
      fetchLogs(200, gen, true).catch(() => {
        if (gen === generation) {
          stop("Refresh paused. Press Live to try again.");
        }
      });
    };
    follow();
    clock = window.setInterval(() => {
      const left = endsAt - Date.now();
      if (left <= 0) {
        stop("Live ended");
        return;
      }
      if (stateEl) {
        stateEl.textContent = `Live · ${Math.ceil(left / 1000)}s`;
      }
    }, 250);
    poll = window.setInterval(() => {
      if (Date.now() >= endsAt) {
        stop("Live ended");
        return;
      }
      follow();
    }, POLL_MS);
  }

  function saveText(text, tail) {
    const safeName = (logEl.dataset.name || "logs").replace(/[^A-Za-z0-9_.-]+/g, "_");
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${safeName}-last-${tail}.txt`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  async function downloadTail(tail) {
    if (downloadChoices) {
      downloadChoices.hidden = true;
    }
    const wasLive = Boolean(liveEl && liveEl.disabled);
    if (!wasLive && stateEl) {
      stateEl.textContent = `Downloading ${tail} lines`;
    }
    try {
      const response = await fetch(`${logEl.dataset.url}?tail=${tail}`, {
        headers: { Accept: "application/json" },
        redirect: "manual",
      });
      if (!response.ok) {
        throw new Error("download failed");
      }
      const data = await response.json();
      if (typeof data.logs !== "string") {
        throw new Error("download failed");
      }
      saveText(shownText(data.logs), tail);
      if (!wasLive && stateEl) {
        stateEl.textContent = `Downloaded last ${tail} lines`;
      }
    } catch (err) {
      if (!wasLive && stateEl) {
        stateEl.textContent = "Could not download logs.";
      }
    }
  }

  async function loadSnapshot(tail) {
    stop(`Last ${tail} lines`);
    const gen = generation;
    markTail(tail);
    if (stateEl) {
      stateEl.textContent = `Loading ${tail} lines`;
    }
    try {
      const loaded = await fetchLogs(tail, gen, true);
      if (gen !== generation) {
        return;
      }
      if (!loaded) {
        stop("Could not load logs.");
        return;
      }
      if (stateEl) {
        stateEl.textContent = `Last ${tail} lines`;
      }
    } catch (err) {
      if (gen === generation) {
        stop("Could not load logs.");
      }
    }
  }

  document.querySelectorAll(".log-actions [data-tail]").forEach((button) => {
    button.addEventListener("click", () => loadSnapshot(Number(button.dataset.tail)));
  });
  if (hideEl) {
    hideEl.addEventListener("change", () => applyLogs(rawLogs, false));
  }
  if (downloadEl && downloadChoices) {
    downloadEl.addEventListener("click", () => {
      downloadChoices.hidden = !downloadChoices.hidden;
    });
    downloadChoices.querySelectorAll("[data-download]").forEach((button) => {
      button.addEventListener("click", () => downloadTail(Number(button.dataset.download)));
    });
  }
  if (liveEl) {
    liveEl.addEventListener("click", start);
  }
  applyLogs(rawLogs, false);
}

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderInline(text) {
  let html = escapeHtml(text);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  return html;
}

function splitRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function isRule(line) {
  const cells = splitRow(line);
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function renderMarkdown(source) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim().startsWith("```")) {
      const buf = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        buf.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) {
        i += 1;
      }
      out.push(`<pre><code>${escapeHtml(buf.join("\n"))}</code></pre>`);
      continue;
    }
    if (line.trim().startsWith("|") && i + 1 < lines.length && isRule(lines[i + 1])) {
      const header = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      const head = header.map((cell) => `<th>${renderInline(cell)}</th>`).join("");
      const body = rows
        .map((row) => `<tr>${row.map((cell) => `<td>${renderInline(cell)}</td>`).join("")}</tr>`)
        .join("");
      out.push(`<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`);
      continue;
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      const level = heading[1].length + 1;
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line);
      const items = [];
      const itemRe = ordered ? /^\s*\d+\.\s+/ : /^\s*[-*]\s+/;
      while (i < lines.length && itemRe.test(lines[i])) {
        items.push(`<li>${renderInline(lines[i].replace(itemRe, ""))}</li>`);
        i += 1;
      }
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const para = [line];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(#{1,3}\s|```|\s*([-*]|\d+\.)\s+|\s*\|)/.test(lines[i])
    ) {
      para.push(lines[i]);
      i += 1;
    }
    out.push(`<p>${renderInline(para.join(" "))}</p>`);
  }
  return out.join("");
}

const explainForm = document.querySelector("#explain-form");

if (explainForm) {
  const button = document.querySelector("#explain-button");
  const panel = document.querySelector("#explain-panel");
  const loader = document.querySelector("#explain-loader");
  const answer = document.querySelector("#explain-answer");
  const errorEl = document.querySelector("#explain-error");

  function showError(message) {
    if (loader) {
      loader.hidden = true;
    }
    if (answer) {
      answer.hidden = true;
    }
    if (errorEl) {
      errorEl.hidden = false;
      errorEl.textContent = message;
    }
  }

  explainForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!button || button.disabled) {
      return;
    }
    button.disabled = true;
    if (panel) {
      panel.hidden = false;
    }
    if (loader) {
      loader.hidden = false;
    }
    if (answer) {
      answer.hidden = true;
      answer.textContent = "";
    }
    if (errorEl) {
      errorEl.hidden = true;
      errorEl.textContent = "";
    }

    let paintTimer = 0;
    try {
      const response = await fetch(explainForm.action, {
        method: "POST",
        body: new FormData(explainForm),
        headers: { Accept: "text/event-stream" },
      });
      const type = response.headers.get("content-type") || "";
      if (!response.ok || !type.includes("text/event-stream") || !response.body) {
        showError("The explanation could not start. Reload the page and try again.");
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let markdown = "";
      let started = false;
      let failed = false;
      const paint = () => {
        answer.innerHTML = renderMarkdown(markdown);
      };
      while (true) {
        const step = await reader.read();
        if (step.done) {
          break;
        }
        buffer += decoder.decode(step.value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        for (const part of parts) {
          const line = part.split("\n").find((item) => item.startsWith("data: "));
          if (!line) {
            continue;
          }
          let data;
          try {
            data = JSON.parse(line.slice(6));
          } catch (err) {
            continue;
          }
          if (data.error) {
            failed = true;
            showError(data.error);
            return;
          }
          if (typeof data.text === "string" && data.text && answer) {
            if (!started) {
              started = true;
              if (loader) {
                loader.hidden = true;
              }
              answer.hidden = false;
            }
            markdown += data.text;
            window.clearTimeout(paintTimer);
            paintTimer = window.setTimeout(paint, 80);
          }
        }
      }
      if (!started && !failed && errorEl && errorEl.hidden) {
        showError("The model returned an empty response.");
      }
      window.clearTimeout(paintTimer);
      if (started && !failed) {
        paint();
      }
    } catch (err) {
      window.clearTimeout(paintTimer);
      showError("The model request failed.");
    } finally {
      window.clearTimeout(paintTimer);
      button.disabled = false;
    }
  });
}
