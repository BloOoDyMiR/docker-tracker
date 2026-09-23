const logEl = document.querySelector("#logs");

if (logEl && logEl.dataset.url) {
  const emptyEl = document.querySelector("#logs-empty");
  const stateEl = document.querySelector("#log-state");

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
      if (stateEl) {
        stateEl.textContent = "Refresh paused. Reload the page to try again.";
      }
    }
  }

  setInterval(tick, 3000);
}
