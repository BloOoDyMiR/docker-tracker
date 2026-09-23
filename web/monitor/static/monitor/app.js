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
      let started = false;
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
            answer.textContent += data.text;
          }
        }
      }
      if (!started && errorEl && errorEl.hidden) {
        showError("The model returned an empty response.");
      }
    } catch (err) {
      showError("The model request failed.");
    } finally {
      button.disabled = false;
    }
  });
}
