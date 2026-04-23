(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;

  if (!tg) {
    document.getElementById("user-info").textContent =
      "⚠️ Открой эту страницу из Telegram — кнопки не будут работать в обычном браузере.";
    return;
  }

  tg.ready();
  tg.expand();

  // Show who's logged in (if Telegram exposes initDataUnsafe).
  const user = tg.initDataUnsafe && tg.initDataUnsafe.user;
  const userInfoEl = document.getElementById("user-info");
  if (user) {
    const name = [user.first_name, user.last_name].filter(Boolean).join(" ") || "—";
    userInfoEl.textContent = `Авторизован как ${name} (id ${user.id})`;
  } else {
    userInfoEl.textContent = "Авторизация через Telegram...";
  }

  function send(payload) {
    try {
      tg.sendData(JSON.stringify(payload));
    } catch (err) {
      tg.showAlert("Не удалось отправить данные: " + err.message);
    }
  }

  function haptic(type) {
    if (tg.HapticFeedback && typeof tg.HapticFeedback.impactOccurred === "function") {
      try {
        tg.HapticFeedback.impactOccurred(type);
      } catch (e) {
        // ignore
      }
    }
  }

  function parseId(raw) {
    if (!raw) return null;
    const trimmed = String(raw).trim();
    if (!/^-?\d+$/.test(trimmed)) return null;
    const n = Number(trimmed);
    if (!Number.isFinite(n) || !Number.isInteger(n)) return null;
    return n;
  }

  document.querySelectorAll("button[data-op]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const op = btn.getAttribute("data-op");

      if (op === "stats" || op === "list") {
        haptic("light");
        send({ op });
        return;
      }

      if (op === "add" || op === "remove") {
        const inputId = op === "add" ? "add-input" : "remove-input";
        const input = document.getElementById(inputId);
        const id = parseId(input.value);
        if (id === null) {
          tg.showAlert("Введите корректный Telegram ID (целое число).");
          input.focus();
          return;
        }
        haptic("medium");
        send({ op, id });
        return;
      }

      tg.showAlert("Неизвестная операция: " + op);
    });
  });
})();
