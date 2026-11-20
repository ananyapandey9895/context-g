// Adds a "ContextPilot it" button to GitHub issue pages. Clicking it opens your local ContextPilot app with the
// issue title, link and description pre-filled. Nothing is sent anywhere except your own computer (127.0.0.1).
(() => {
  const BTN_ID = "ctxly-it-btn";
  const MAX = 5000;

  function text(sel) {
    const n = document.querySelector(sel);
    return n ? n.innerText.trim() : "";
  }
  function issueData() {
    const title = text('[data-testid="issue-title"]') || text(".js-issue-title") || document.title.split(" · ")[0];
    const body = text('[data-testid="issue-body"] .markdown-body') || text(".comment-body") || text(".markdown-body");
    const m = location.pathname.match(/^\/([^/]+)\/([^/]+)\/issues\/(\d+)/);
    return { title, body, repo: m ? `${m[1]}/${m[2]}` : "", number: m ? m[3] : "", url: location.href.split("#")[0] };
  }
  function buildTask(d) {
    let body = d.body.replace(/\n{3,}/g, "\n\n");
    if (body.length > MAX) body = body.slice(0, MAX) + "\n…";
    return `Fix GitHub issue ${d.repo}#${d.number}: ${d.title}\n${d.url}\n\n${body}`.trim();
  }
  function open() {
    chrome.storage.sync.get({ port: 8765 }, ({ port }) => {
      const url = `http://127.0.0.1:${Number(port) || 8765}/?task=${encodeURIComponent(buildTask(issueData()))}`;
      window.open(url, "_blank", "noopener");
    });
  }
  function place() {
    if (!/\/issues\/\d+/.test(location.pathname) || document.getElementById(BTN_ID)) return;
    const anchor = document.querySelector('[data-testid="issue-title"]') || document.querySelector(".gh-header-title") ||
      document.querySelector("h1");
    if (!anchor) return;
    const b = document.createElement("button");
    b.id = BTN_ID;
    b.type = "button";
    b.className = "ctxly-it";
    b.textContent = "ContextPilot it";
    b.title = "Open this issue in your local ContextPilot app";
    b.addEventListener("click", open);
    anchor.insertAdjacentElement("afterend", b);
  }
  place();
  // GitHub is a single-page app: re-check when the page content changes.
  new MutationObserver(() => place()).observe(document.body, { childList: true, subtree: true });
})();
