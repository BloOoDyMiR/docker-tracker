const logEl = document.querySelector("#logs");
const LIVE_MS = 30000;
const POLL_MS = 3000;

if (logEl && logEl.dataset.url) {
  const emptyEl = document.querySelector("#logs-empty");
  const stateEl = document.querySelector("#log-state");
  const resumeEl = document.querySelector("#log-resume");
  let poll = 0;
  let clock = 0;
  let endsAt = 0;

  async function tick() {
    try {
      const response = await fetch(logEl.dataset.url, {
        headers: { Accept: "application/json" },
        redirect: "manual",
      });
      if (!response.ok) {
        return;
      }
      const data = await response.json();
      if (typeof data.logs !== "string" || data.logs === logEl.textContent) {
        return;
      }
      const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 48;
      logEl.textContent = data.logs;
      if (emptyEl) {
        emptyEl.hidden = data.logs.length > 0;
      }
      if (atBottom) {
        logEl.scrollTop = logEl.scrollHeight;
      }
    } catch (err) {
      stop("Refresh paused. Use Resume to try again.");
    }
  }

  function stop(message) {
    window.clearInterval(poll);
    window.clearInterval(clock);
    poll = 0;
    clock = 0;
    if (stateEl) {
      stateEl.textContent = message;
    }
    if (resumeEl) {
      resumeEl.hidden = false;
    }
  }

  function start() {
    if (resumeEl) {
      resumeEl.hidden = true;
    }
    endsAt = Date.now() + LIVE_MS;
    if (stateEl) {
      stateEl.textContent = "Live · 30s";
    }
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
      tick();
    }, POLL_MS);
  }

  if (resumeEl) {
    resumeEl.addEventListener("click", start);
  }
  start();
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
