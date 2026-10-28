const $ = (id) => document.getElementById(id);

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([key, value]) => {
    if (key === "className") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value === false || value === null || value === undefined) return;
    else node.setAttribute(key, value === true ? "" : String(value));
  });
  (children || []).forEach((child) => {
    if (child) node.append(child);
  });
  return node;
}

async function errorDetail(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
  } catch (_error) {
    return "The request failed.";
  }
  return "The request failed.";
}

async function send(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await errorDetail(response));
  return response.json();
}

let profileState = null;

function weightText(field) {
  const weight = Number(field.weight);
  if (!Number.isFinite(weight)) return "unset";
  return weight.toFixed(2);
}

function renderFields(fields) {
  const host = $("fields");
  host.replaceChildren();
  if (!fields.length) {
    host.append(el("div", { className: "notice", text: "This version has no fields yet." }));
    return;
  }
  fields.forEach((field) => {
    const hard = field.source === "constraint";
    const title = el("h3", { text: field.name });
    const meta = el("p", {
      className: "muted",
      text: [
        field.kind,
        "source " + field.source,
        "confidence " + (field.confidence ?? "—"),
        "weight " + weightText(field),
        field.locked ? "locked" : "unlocked",
      ].join(" · "),
    });
    const evidence = (field.evidence || []).length
      ? el("p", { className: "evidence", text: "Evidence: " + field.evidence.map((quote) => "“" + quote + "”").join(" ") })
      : el("p", { className: "evidence", text: hard ? "Set by you as a hard filter." : "No quoted evidence." });
    const actions = el("div", { className: "actions" });
    if (!hard) {
      const input = el("input", { type: "text", value: field.name, "aria-label": "Edit " + field.name });
      input.disabled = Boolean(field.locked);
      const save = el("button", {
        type: "button",
        text: "Save edit",
        onClick: () => editField(field.key, input.value),
      });
      save.disabled = Boolean(field.locked);
      const lock = el("button", {
        type: "button",
        text: field.locked ? "Unlock" : "Lock",
        onClick: () => lockField(field.key, !field.locked),
      });
      actions.append(input, save, lock);
    } else {
      actions.append(el("a", { href: "/onboarding?edit=constraints", text: "Edit constraints" }));
    }
    host.append(el("article", { className: "line" }, [title, meta, evidence, actions]));
  });
}

function renderHistory(history) {
  const host = $("history");
  host.replaceChildren();
  $("history-empty").hidden = history.length > 0;
  history.slice().reverse().forEach((version) => {
    const details = el("details", { className: "card" });
    if (version.version === (profileState.profile || {}).version) details.open = true;
    const change = version.major_change ? "major change" : "no major change";
    details.append(el("summary", { text: "Version " + version.version + " · " + version.reason + " · " + change }));
    details.append(el("p", { className: "kicker", text: version.created_at }));
    (version.fields || []).forEach((field) => {
      details.append(el("p", {
        text: field.name + " · " + field.source + " · " + weightText(field) + " · confidence " + (field.confidence ?? "—"),
      }));
    });
    host.append(details);
  });
}

function render() {
  const data = profileState;
  const profile = data.profile;
  $("boot-loading").hidden = true;
  $("boot-error").hidden = true;
  $("app").hidden = false;
  $("empty").hidden = Boolean(profile);
  $("status-line").textContent = profile
    ? "Version " + profile.version + " · " + (data.stable ? "Stable" : "Not stable yet")
    : "No profile yet · " + data.status_label;
  if (profile) renderFields(profile.fields || []);
  else $("fields").replaceChildren();
  renderHistory(data.history || []);
  const gate = data.gate || {};
  $("override").value = gate.override || "";
  $("gate-copy").textContent = gate.detail || "";
}

async function load() {
  $("boot-loading").hidden = false;
  $("boot-error").hidden = true;
  try {
    profileState = await send("/api/onboarding/profile");
    render();
  } catch (error) {
    $("boot-loading").hidden = true;
    $("app").hidden = true;
    $("boot-error").hidden = false;
    $("boot-error").textContent = error.message + " ";
    $("boot-error").append(el("button", { type: "button", text: "Try again", onClick: () => load() }));
  }
}

async function editField(key, name) {
  try {
    profileState = await send("/api/onboarding/profile/fields/edit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, name }),
    });
    render();
  } catch (error) {
    showGate(error.message, true);
  }
}

async function lockField(key, locked) {
  try {
    profileState = await send("/api/onboarding/profile/fields/lock", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, locked }),
    });
    render();
  } catch (error) {
    showGate(error.message, true);
  }
}

function showGate(message, isError) {
  const node = $("gate-result");
  node.hidden = !message;
  node.textContent = message || "";
  node.className = isError ? "notice error" : "notice";
}

$("save-gate").addEventListener("click", async () => {
  showGate("", false);
  try {
    profileState = await send("/api/onboarding/gate", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ override: $("override").value }),
    });
    render();
    showGate("Flag saved.", false);
  } catch (error) {
    showGate(error.message, true);
  }
});

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    showGate("", false);
    const action = button.getAttribute("data-action");
    try {
      const response = await fetch("/api/onboarding/actions/" + action, { method: "POST" });
      const body = await response.json();
      if (!response.ok) {
        showGate(typeof body.detail === "string" ? body.detail : "That action is blocked.", true);
        return;
      }
      showGate(body.detail + (body.performed ? "" : " Nothing was run."), false);
    } catch (_error) {
      showGate("The action check failed.", true);
    }
  });
});

load();
