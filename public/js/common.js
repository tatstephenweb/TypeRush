// Small helpers used by every page.
async function api(url, options = {}) {
  try {
    const res = await fetch(url, options);
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) window.location = "/login";
    return { ok: res.ok, data };
  } catch (err) {
    return { ok: false, data: { error: "Network error. Check your connection and try again." } };
  }
}
function postJSON(url, body) {
  return api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
function esc(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}
function avatarHTML(url, name, size = "h-10 w-10") {
  if (url) return `<img src="${esc(url)}" alt="" class="${size} shrink-0 rounded-full object-cover">`;
  return `<div class="${size} flex shrink-0 items-center justify-center rounded-full bg-amber font-display font-bold text-ink">${esc(name.charAt(0).toUpperCase())}</div>`;
}
function showMessage(el, text, ok = false) {
  el.textContent = text;
  el.className = "rounded-lg px-4 py-2 text-sm " + (ok ? "bg-emerald-400/10 text-emerald-300" : "bg-coral/10 text-coral");
}

// ---------- PWA: service worker + "Install app" button ----------
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
(function () {
  const installBtn = document.getElementById("installBtn");
  const installed = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if (!installBtn || installed) return;
  let installEvent = null;
  window.addEventListener("beforeinstallprompt", (e) => {   // Chrome, Edge, Android
    e.preventDefault();
    installEvent = e;
    installBtn.classList.remove("hidden");
  });
  window.addEventListener("appinstalled", () => installBtn.classList.add("hidden"));
  if (/iphone|ipad|ipod/i.test(navigator.userAgent)) installBtn.classList.remove("hidden");  // iOS has no install prompt
  installBtn.addEventListener("click", async () => {
    if (installEvent) {
      installEvent.prompt();
      await installEvent.userChoice;
      installEvent = null;
      installBtn.classList.add("hidden");
    } else {
      alert("To install: tap the Share button, then choose Add to Home Screen.");
    }
  });
})();
