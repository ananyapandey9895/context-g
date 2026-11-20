const input = document.getElementById("port");
chrome.storage.sync.get({ port: 8765 }, ({ port }) => { input.value = port; });
document.getElementById("save").addEventListener("click", () => {
  const port = Math.min(65535, Math.max(1, Number(input.value) || 8765));
  chrome.storage.sync.set({ port }, () => { document.getElementById("msg").textContent = "Saved."; });
});
