const PRESET_LOCATIONS = ["Dhaka", "Chattogram", "Sylhet", "Khulna", "Rajshahi", "Barishal", "Remote"];
const PRESET_INDUSTRIES = ["Healthcare", "Finance", "Education", "Government", "Gambling", "Defense", "Retail", "Hospitality"];
const STARTER_LANGUAGES = ["English", "Bangla", "Japanese"];
const LEVELS = [
  ["", "Not set"],
  ["none", "None"],
  ["basic", "Basic"],
  ["conversational", "Conversational"],
  ["fluent", "Fluent"],
];

const state = {
  data: null,
  screen: "constraints",
  locations: [],
  industries: [],
  languages: [],
  auths: [{ country: "", notes: "" }],
  triedInfer: false,
};

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

function show(id, visible) {
  $(id).hidden = !visible;
}

function setAlert(id, message) {
  const node = $(id);
  node.hidden = !message;
  node.textContent = message || "";
}

function checkedValues(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
}

function radioValue(name) {
  const input = document.querySelector(`input[name="${name}"]:checked`);
  return input ? input.value : "";
}

function numberOrNull(id) {
  const value = $(id).value.trim();
  if (!value) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : value;
}

function choiceBox(container, name, value, presets, selected) {
  container.replaceChildren();
  const values = [...presets];
  selected.forEach((item) => {
    if (!values.some((preset) => preset.toLowerCase() === item.toLowerCase())) values.push(item);
  });
  values.forEach((item) => {
    const input = el("input", { type: "checkbox", name, value: item });
    input.checked = selected.some((chosen) => chosen.toLowerCase() === item.toLowerCase());
    container.append(el("label", { className: "choice" }, [input, document.createTextNode(" " + item)]));
  });
}

function levelSelect(selected) {
  const select = el("select", { "aria-label": "Language level" });
  LEVELS.forEach(([value, label]) => {
    const option = el("option", { value, text: label });
    if (value === selected) option.selected = true;
    select.append(option);
  });
  return select;
}

function renderLanguages() {
  const host = $("language-rows");
  host.replaceChildren();
  state.languages.forEach((language, index) => {
    const select = levelSelect(language.level || "");
    select.addEventListener("change", () => {
      state.languages[index].level = select.value;
    });
    const name = el("input", { type: "text", value: language.name, "aria-label": "Language" });
    name.addEventListener("input", () => {
      state.languages[index].name = name.value;
    });
    const remove = el("button", { type: "button", text: "Remove", onClick: () => {
      state.languages.splice(index, 1);
      renderLanguages();
    }});
    host.append(el("div", { className: "row" }, [name, select, remove]));
  });
}

function renderAuths() {
  const host = $("auth-rows");
  host.replaceChildren();
  state.auths.forEach((entry, index) => {
    const country = el("input", { type: "text", value: entry.country, placeholder: "Country", "aria-label": "Country" });
    const notes = el("input", { type: "text", value: entry.notes, placeholder: "Notes", "aria-label": "Work authorization notes" });
    country.addEventListener("input", () => { state.auths[index].country = country.value; });
    notes.addEventListener("input", () => { state.auths[index].notes = notes.value; });
    host.append(el("div", { className: "row" }, [country, notes]));
  });
}

function fillConstraints(filters) {
  const data = filters || {};
  state.locations = [...(data.locations || [])];
  state.industries = [...(data.exclude_industries || [])];
  const savedLanguages = data.languages || [];
  const names = new Set(STARTER_LANGUAGES.map((name) => name.toLowerCase()));
  state.languages = STARTER_LANGUAGES.map((name) => {
    const found = savedLanguages.find((item) => item.name.toLowerCase() === name.toLowerCase());
    return { name, level: found ? found.level : "" };
  });
  savedLanguages.forEach((item) => {
    if (!names.has(item.name.toLowerCase())) state.languages.push({ name: item.name, level: item.level });
  });
  state.auths = (data.work_authorization && data.work_authorization.length)
    ? data.work_authorization.map((item) => ({ country: item.country, notes: item.notes }))
    : [{ country: "", notes: "" }];
  choiceBox($("location-choices"), "location", "location", PRESET_LOCATIONS, state.locations);
  choiceBox($("industry-choices"), "industry", "industry", PRESET_INDUSTRIES, state.industries);
  document.querySelectorAll('input[name="relocate"]').forEach((input) => {
    input.checked = input.value === (data.willing_to_relocate || "");
  });
  document.querySelectorAll('input[name="remote"]').forEach((input) => {
    input.checked = input.value === (data.remote_preference || "");
  });
  document.querySelectorAll('input[name="employment"]').forEach((input) => {
    input.checked = (data.employment_types || []).includes(input.value);
  });
  $("weekly-hours").value = data.weekly_hours ?? "";
  $("min-salary").value = data.min_salary ?? "";
  $("salary-currency").value = data.salary_currency || "";
  $("salary-flexible").checked = Boolean(data.salary_flexible);
  $("earliest-start").value = data.earliest_start || "";
  $("companies").value = (data.exclude_companies || []).join("\n");
  renderLanguages();
  renderAuths();
}

function readConstraints() {
  const locations = checkedValues("location");
  const extraLocation = $("location-extra").value.trim();
  if (extraLocation) locations.push(extraLocation);
  const industries = checkedValues("industry");
  const extraIndustry = $("industry-extra").value.trim();
  if (extraIndustry) industries.push(extraIndustry);
  return {
    locations,
    willing_to_relocate: radioValue("relocate"),
    remote_preference: radioValue("remote"),
    employment_types: checkedValues("employment"),
    weekly_hours: numberOrNull("weekly-hours"),
    min_salary: numberOrNull("min-salary"),
    salary_currency: $("salary-currency").value,
    salary_flexible: $("salary-flexible").checked,
    languages: state.languages
      .map((item) => ({ name: item.name.trim(), level: item.level }))
      .filter((item) => item.name && item.level),
    work_authorization: state.auths
      .map((item) => ({ country: item.country.trim(), notes: item.notes.trim() }))
      .filter((item) => item.country || item.notes),
    exclude_industries: industries,
    exclude_companies: $("companies").value.split(/\n|,/).map((item) => item.trim()).filter(Boolean),
    earliest_start: $("earliest-start").value,
  };
}

function lineTitle(item) {
  if (item.source === "reflection" && item.polarity === "avoid") return "Less of: " + item.name;
  if (item.source === "reflection") return "More of: " + item.name;
  return item.name;
}

function renderReview(data) {
  $("review-note").textContent = data.inference_note || "Confirm a line to lock it. Reject a line to give it a negative weight. Unconfirmed lines stay out of the profile.";
  const list = $("review-list");
  list.replaceChildren();
  const items = data.items || [];
  const showResume = !data.has_resume || items.length === 0;
  $("resume-tools").hidden = !showResume;
  const empty = $("review-empty");
  if (!items.length) {
    empty.hidden = false;
    empty.textContent = data.has_resume
      ? "Nothing to quote from the resume yet. You can continue with constraints only."
      : "No resume is stored yet. Upload one, or continue with constraints only.";
  } else {
    empty.hidden = true;
  }
  items.forEach((item) => {
    const evidence = (item.evidence || []).map((quote) => "“" + quote + "”").join(" ");
    const meta = [item.kind, item.source, item.document_backed ? "document-backed" : item.writer || "proposal"]
      .filter(Boolean)
      .join(" · ");
    const status = item.locked
      ? "Locked"
      : item.status === "rejected"
        ? "Rejected, negative weight"
        : item.status === "confirmed"
          ? "Confirmed"
          : "Not in the profile yet";
    const actions = el("div", { className: "actions" });
    if (!item.locked) {
      actions.append(
        el("button", { type: "button", className: "primary", text: "Confirm", onClick: () => decide(item.key, "confirm") }),
        el("button", { type: "button", text: "Edit", onClick: () => editLine(item) }),
        el("button", { type: "button", className: "danger", text: "Reject", onClick: () => decide(item.key, "reject") }),
      );
    } else {
      actions.append(el("button", { type: "button", text: "Unlock", onClick: () => unlock(item.key) }));
    }
    list.append(el("article", { className: "line" }, [
      el("h3", { text: lineTitle(item) }),
      el("p", { className: "muted", text: meta }),
      el("p", { className: "evidence", text: evidence ? "Evidence: " + evidence : "No evidence" }),
      el("p", { text: status }),
      item.experience_ref ? el("p", { className: "muted", text: "From " + item.experience_ref }) : null,
      actions,
    ]));
  });
  const finishing = data.status === "reflection_done";
  $("review-next").textContent = finishing ? "Finish onboarding" : "Continue";
}

function renderReflection(data) {
  const form = $("reflection-form");
  form.replaceChildren();
  const questions = data.questions || [];
  $("reflection-empty").hidden = questions.length > 0;
  questions.forEach((question) => {
    const previous = (data.answers || []).find((answer) => answer.question_key === question.question_key);
    const group = el("fieldset", { className: "card stack" });
    group.append(
      el("legend", { text: question.prompt }),
      el("p", { className: "kicker", text: question.experience_kind + " · " + question.experience_ref }),
    );
    question.chips.forEach((chip) => {
      const input = el("input", { type: "radio", name: question.question_key, value: chip });
      if (previous && !previous.skipped && previous.choice === chip) input.checked = true;
      group.append(el("label", { className: "choice" }, [input, document.createTextNode(" " + chip)]));
    });
    const skip = el("input", { type: "checkbox" });
    if (previous && previous.skipped) skip.checked = true;
    skip.addEventListener("change", () => {
      group.querySelectorAll('input[type="radio"]').forEach((input) => {
        input.disabled = skip.checked;
        if (skip.checked) input.checked = false;
      });
    });
    group.append(el("label", { className: "choice" }, [skip, document.createTextNode(" Skip")]));
    group.dataset.questionKey = question.question_key;
    form.append(group);
  });
}

function showScreen(name) {
  ["constraints", "review", "reflection", "done"].forEach((screen) => {
    $("screen-" + screen).hidden = screen !== name;
  });
  state.screen = name;
}

function render() {
  const data = state.data;
  if (!data) return;
  $("status-line").textContent = data.status_label + " · About 2 minutes. Every question is optional.";
  const params = new URLSearchParams(location.search);
  let screen = "constraints";
  if (params.get("edit") === "constraints") screen = "constraints";
  else if (data.status === "not_started") screen = "constraints";
  else if (data.status === "constraints_done") screen = "review";
  else if (data.status === "profile_confirmed") screen = "reflection";
  else if (data.status === "reflection_done") screen = "review";
  else screen = "done";
  fillConstraints(data.constraints);
  renderReview(data);
  renderReflection(data);
  showScreen(screen);
  show("boot-loading", false);
  show("app", true);
}

async function load() {
  show("boot-loading", true);
  setAlert("boot-error", "");
  try {
    let data = await send("/api/onboarding/state");
    if (!state.triedInfer && data.status === "constraints_done" && !data.inference_note) {
      state.triedInfer = true;
      show("boot-loading", true);
      $("boot-loading").textContent = "Reading your resume…";
      data = await send("/api/onboarding/infer", { method: "POST" });
    }
    if (data.status === "profile_confirmed" && !(data.questions || []).length) {
      data = await send("/api/onboarding/reflection", { method: "POST" });
    }
    state.data = data;
    render();
  } catch (error) {
    show("boot-loading", false);
    show("app", false);
    setAlert("boot-error", error.message + " ");
    const retry = el("button", { type: "button", text: "Try again", onClick: () => load() });
    $("boot-error").append(retry);
  }
}

async function decide(key, action) {
  setAlert("review-error", "");
  show("review-loading", true);
  try {
    state.data = await send("/api/onboarding/items/" + action, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    render();
  } catch (error) {
    setAlert("review-error", error.message);
  } finally {
    show("review-loading", false);
  }
}

function editLine(item) {
  const name = window.prompt("Edit this line", item.name);
  if (name === null) return;
  setAlert("review-error", "");
  send("/api/onboarding/items/edit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: item.key, name }),
  }).then((data) => {
    state.data = data;
    render();
  }).catch((error) => setAlert("review-error", error.message));
}

async function unlock(key) {
  setAlert("review-error", "");
  try {
    state.data = await send("/api/onboarding/items/unlock", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    render();
  } catch (error) {
    setAlert("review-error", error.message);
  }
}

function addPreset(inputId, listName) {
  const value = $(inputId).value.trim();
  if (!value) return;
  const selected = checkedValues(listName);
  if (!selected.some((item) => item.toLowerCase() === value.toLowerCase())) selected.push(value);
  const presets = listName === "location" ? PRESET_LOCATIONS : PRESET_INDUSTRIES;
  choiceBox($(listName === "location" ? "location-choices" : "industry-choices"), listName, listName, presets, selected);
  $(inputId).value = "";
}

$("add-location").addEventListener("click", () => addPreset("location-extra", "location"));
$("add-industry").addEventListener("click", () => addPreset("industry-extra", "industry"));
$("add-language").addEventListener("click", () => {
  const name = $("language-name").value.trim();
  if (!name) return;
  state.languages.push({ name, level: "" });
  $("language-name").value = "";
  renderLanguages();
});
$("add-auth").addEventListener("click", () => {
  state.auths.push({ country: "", notes: "" });
  renderAuths();
});

$("constraints-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setAlert("constraints-error", "");
  show("constraints-loading", true);
  try {
    await send("/api/onboarding/constraints", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readConstraints()),
    });
    show("constraints-loading", false);
    show("review-loading", true);
    state.data = await send("/api/onboarding/infer", { method: "POST" });
    history.replaceState(null, "", "/onboarding");
    render();
  } catch (error) {
    setAlert("constraints-error", error.message);
    showScreen("constraints");
  } finally {
    show("constraints-loading", false);
    show("review-loading", false);
  }
});

$("upload-resume").addEventListener("click", async () => {
  setAlert("review-error", "");
  show("review-loading", true);
  try {
    const file = $("resume-file").files[0];
    const text = $("resume-text").value.trim();
    if (!file && !text) throw new Error("Upload a PDF or paste resume text.");
    if (file) {
      const body = new FormData();
      body.append("file", file);
      await send("/api/onboarding/resume/pdf", { method: "POST", body });
    } else {
      await send("/api/onboarding/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
    }
    state.data = await send("/api/onboarding/infer", { method: "POST" });
    render();
  } catch (error) {
    setAlert("review-error", error.message);
  } finally {
    show("review-loading", false);
  }
});

$("back-constraints").addEventListener("click", () => {
  history.replaceState(null, "", "/onboarding?edit=constraints");
  showScreen("constraints");
});
$("done-edit").addEventListener("click", () => {
  history.replaceState(null, "", "/onboarding?edit=constraints");
  showScreen("constraints");
});

$("review-next").addEventListener("click", async () => {
  setAlert("review-error", "");
  show("review-loading", true);
  try {
    if ((state.data || {}).status === "reflection_done") {
      state.data = await send("/api/onboarding/complete", { method: "POST" });
      history.replaceState(null, "", "/onboarding");
      render();
      return;
    }
    state.data = await send("/api/onboarding/review/continue", { method: "POST" });
    state.data = await send("/api/onboarding/reflection", { method: "POST" });
    render();
  } catch (error) {
    setAlert("review-error", error.message);
  } finally {
    show("review-loading", false);
  }
});

function reflectionAnswers(skipAll) {
  return [...$("reflection-form").querySelectorAll("fieldset")].map((group) => {
    const key = group.dataset.questionKey;
    const skip = group.querySelector('input[type="checkbox"]');
    const selected = group.querySelector('input[type="radio"]:checked');
    const skipped = skipAll || (skip && skip.checked) || !selected;
    return { question_key: key, choice: skipped || !selected ? "" : selected.value, skipped };
  });
}

async function saveReflection(skipAll) {
  setAlert("reflection-error", "");
  show("reflection-loading", true);
  try {
    state.data = await send("/api/onboarding/reflection/answers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers: reflectionAnswers(skipAll) }),
    });
    render();
  } catch (error) {
    setAlert("reflection-error", error.message);
  } finally {
    show("reflection-loading", false);
  }
}

$("save-reflection").addEventListener("click", () => saveReflection(false));
$("skip-reflection").addEventListener("click", () => saveReflection(true));

load();
