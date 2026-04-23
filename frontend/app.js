// --------------------------------------------------------------------------- //
// Telegram Mini App client for the ai_bot decoder API.
// --------------------------------------------------------------------------- //

const BACKEND_URL = (window.BACKEND_URL && !window.BACKEND_URL.includes("__BACKEND_URL__"))
  ? window.BACKEND_URL.replace(/\/+$/, "")
  : "";

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
if (tg) {
  try {
    tg.ready();
    tg.expand();
    tg.enableClosingConfirmation && tg.enableClosingConfirmation();
    if (tg.setHeaderColor) tg.setHeaderColor("secondary_bg_color");
  } catch (_) { /* noop */ }
}

const state = {
  action: null,           // "decrypt" | "view"
  mode: null,             // "netcfg" | "mxcfg"
  file: null,
  resultBlob: null,
  resultFilename: null,
};

const SCREENS = ["home", "type", "upload", "loading", "result"];
const screenStack = ["home"];

function screen(id) {
  SCREENS.forEach((name) => {
    const el = document.getElementById("screen-" + name);
    if (el) el.classList.toggle("active", name === id);
  });
  updateBackButton();
  updateMainButton();
}

function pushScreen(id) {
  if (screenStack[screenStack.length - 1] !== id) {
    screenStack.push(id);
  }
  screen(id);
}

function popScreen() {
  if (screenStack.length > 1) {
    screenStack.pop();
    screen(screenStack[screenStack.length - 1]);
  } else {
    if (tg && tg.close) tg.close();
  }
}

function resetToHome() {
  state.action = null;
  state.mode = null;
  state.file = null;
  state.resultBlob = null;
  state.resultFilename = null;
  const input = document.getElementById("file-input");
  if (input) input.value = "";
  const selected = document.getElementById("selected-file");
  selected.classList.add("hidden");
  const btn = document.getElementById("btn-process");
  btn.disabled = true;
  btn.textContent = "⚙ Обработать";
  screenStack.length = 0;
  screenStack.push("home");
  screen("home");
}

function updateBackButton() {
  if (!tg || !tg.BackButton) return;
  const current = screenStack[screenStack.length - 1];
  if (current === "home" || current === "loading") {
    tg.BackButton.hide();
  } else {
    tg.BackButton.show();
  }
}

function updateMainButton() {
  if (!tg || !tg.MainButton) return;
  const current = screenStack[screenStack.length - 1];
  const mb = tg.MainButton;

  if (current === "result") {
    if (state.resultBlob) {
      mb.setText("⬇ Скачать файл");
      mb.show();
    } else {
      mb.setText("🏠 В меню");
      mb.show();
    }
  } else {
    mb.hide();
  }
}

function toast(msg, isError) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.toggle("error", !!isError);
  t.classList.remove("hidden");
  clearTimeout(toast._tid);
  toast._tid = setTimeout(() => t.classList.add("hidden"), 4200);
  if (isError && tg && tg.HapticFeedback) {
    try { tg.HapticFeedback.notificationOccurred("error"); } catch (_) {}
  }
}

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(2) + " MB";
}

function setSelectedFile(file) {
  const el = document.getElementById("selected-file");
  if (!file) {
    el.classList.add("hidden");
    return;
  }
  el.innerHTML =
    '<span>📎</span>' +
    '<span class="name">' + escapeHtml(file.name) + '</span>' +
    '<span class="size">' + formatBytes(file.size) + '</span>';
  el.classList.remove("hidden");
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// --------------------------------------------------------------------------- //
// Screen: home
// --------------------------------------------------------------------------- //

document.querySelectorAll("#screen-home .card").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.action = btn.dataset.action;
    const title = state.action === "decrypt" ? "🔓 Дешифрование" : "👁 Просмотр";
    document.getElementById("type-title").textContent = title;

    const list = document.getElementById("type-list");
    list.innerHTML = "";

    const types = state.action === "view"
      ? [{ id: "mxcfg", name: "MXCFG", desc: "Красивый просмотр MXCFG" }]
      : [
          { id: "netcfg", name: "NETCFG", desc: "Расшифровать NETCFG" },
          { id: "mxcfg",  name: "MXCFG",  desc: "Расшифровать MXCFG" },
        ];

    for (const t of types) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "card";
      b.innerHTML =
        '<div class="card-icon">📄</div>' +
        '<div class="card-body">' +
          '<div class="card-title">' + escapeHtml(t.name) + '</div>' +
          '<div class="card-desc">' + escapeHtml(t.desc) + '</div>' +
        '</div>' +
        '<div class="card-chevron">›</div>';
      b.addEventListener("click", () => {
        state.mode = t.id;
        document.getElementById("upload-title").textContent = "Загрузи файл " + t.name;
        state.file = null;
        setSelectedFile(null);
        document.getElementById("btn-process").disabled = true;
        document.getElementById("file-input").value = "";
        pushScreen("upload");
      });
      list.appendChild(b);
    }

    pushScreen("type");
  });
});

// --------------------------------------------------------------------------- //
// Screen: upload
// --------------------------------------------------------------------------- //

const fileInput = document.getElementById("file-input");
const btnProcess = document.getElementById("btn-process");

fileInput.addEventListener("change", () => {
  const f = fileInput.files && fileInput.files[0];
  state.file = f || null;
  setSelectedFile(f);
  btnProcess.disabled = !f;
});

// Drag & drop
const uploader = document.querySelector(".uploader");
if (uploader) {
  ["dragenter", "dragover"].forEach((ev) =>
    uploader.addEventListener(ev, (e) => {
      e.preventDefault();
      uploader.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    uploader.addEventListener(ev, (e) => {
      e.preventDefault();
      uploader.classList.remove("dragover");
    })
  );
  uploader.addEventListener("drop", (e) => {
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
      fileInput.files = e.dataTransfer.files;
      fileInput.dispatchEvent(new Event("change"));
    }
  });
}

btnProcess.addEventListener("click", processFile);

async function processFile() {
  if (!state.file || !state.action || !state.mode) return;

  pushScreen("loading");

  try {
    const fd = new FormData();
    fd.append("file", state.file);

    const base = BACKEND_URL || "";
    const url = base + "/api/" + state.mode + "/" + state.action;

    const headers = {};
    if (tg && tg.initData) headers["X-Telegram-Init-Data"] = tg.initData;

    const resp = await fetch(url, { method: "POST", headers, body: fd });

    if (!resp.ok) {
      let msg = "HTTP " + resp.status;
      try {
        const data = await resp.json();
        if (data && data.detail) msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      } catch (_) { /* keep default */ }
      throw new Error(msg);
    }

    const body = document.getElementById("result-body");
    const downloadBtn = document.getElementById("btn-download");

    if (state.action === "view") {
      const data = await resp.json();
      body.innerHTML = data.html || "";
      state.resultBlob = null;
      state.resultFilename = null;
      downloadBtn.classList.add("hidden");
      document.getElementById("result-title").textContent = "👁 Содержимое MXCFG";
    } else {
      const blob = await resp.blob();
      const cd = resp.headers.get("Content-Disposition") || "";
      let filename = null;
      const m1 = /filename\*=UTF-8''([^;]+)/.exec(cd);
      if (m1) {
        try { filename = decodeURIComponent(m1[1]); } catch (_) { filename = m1[1]; }
      }
      if (!filename) {
        const m2 = /filename="([^"]+)"/.exec(cd);
        if (m2) filename = m2[1];
      }
      if (!filename) filename = "decoded_" + (state.file.name || "file.bin");

      state.resultBlob = blob;
      state.resultFilename = filename;

      body.innerHTML =
        '<div class="success-card">' +
          '<div class="icon">✅</div>' +
          '<div class="info">' +
            '<div class="filename">' + escapeHtml(filename) + '</div>' +
            '<div class="meta">' + formatBytes(blob.size) + ' · нажми «Скачать»</div>' +
          '</div>' +
        '</div>';
      downloadBtn.classList.remove("hidden");
      document.getElementById("result-title").textContent = "✅ Файл расшифрован";
    }

    // Replace the loading screen in the stack with the result.
    if (screenStack[screenStack.length - 1] === "loading") {
      screenStack.pop();
    }
    pushScreen("result");

    if (tg && tg.HapticFeedback) {
      try { tg.HapticFeedback.notificationOccurred("success"); } catch (_) {}
    }
  } catch (err) {
    // Pop the loading screen if present.
    if (screenStack[screenStack.length - 1] === "loading") {
      screenStack.pop();
      screen(screenStack[screenStack.length - 1]);
    }
    toast("💥 " + (err && err.message ? err.message : String(err)), true);
  }
}

// --------------------------------------------------------------------------- //
// Screen: result
// --------------------------------------------------------------------------- //

document.getElementById("btn-download").addEventListener("click", downloadResult);

function downloadResult() {
  if (!state.resultBlob) return;
  const url = URL.createObjectURL(state.resultBlob);
  const a = document.createElement("a");
  a.href = url;
  a.download = state.resultFilename || "decoded.bin";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  if (tg && tg.HapticFeedback) {
    try { tg.HapticFeedback.impactOccurred("medium"); } catch (_) {}
  }
}

// --------------------------------------------------------------------------- //
// Telegram back / main buttons
// --------------------------------------------------------------------------- //

if (tg) {
  if (tg.BackButton) tg.BackButton.onClick(popScreen);
  if (tg.MainButton) {
    tg.MainButton.onClick(() => {
      const current = screenStack[screenStack.length - 1];
      if (current === "result") {
        if (state.resultBlob) {
          downloadResult();
        } else {
          resetToHome();
        }
      }
    });
  }
}

updateBackButton();
updateMainButton();
