(function () {
  const DIMENSIONS = [
    ["role_family", "Role families"],
    ["industry", "Industries"],
    ["seniority", "Seniority"],
    ["skill", "Skills"],
    ["location", "Location and remote"],
    ["pay", "Pay"],
    ["dislike", "Dislikes"],
  ];

  const postingLoading = document.getElementById("posting-loading");
  const postingEmpty = document.getElementById("posting-empty");
  const postingEmptyText = document.getElementById("posting-empty-text");
  const postingError = document.getElementById("posting-error");
  const postingErrorText = document.getElementById("posting-error-text");
  const postingCard = document.getElementById("posting-card");
  const postingActions = document.getElementById("posting-actions");
  const postingMeta = document.getElementById("posting-meta");
  const reasonPanel = document.getElementById("reason-panel");
  const reasonPrompt = document.getElementById("reason-prompt");
  const reasonChips = document.getElementById("reason-chips");
  const profileLoading = document.getElementById("profile-loading");
  const profileEmpty = document.getElementById("profile-empty");
  const profileEmptyText = document.getElementById("profile-empty-text");
  const profileError = document.getElementById("profile-error");
  const profileErrorText = document.getElementById("profile-error-text");
  const profileBody = document.getElementById("profile-body");
  const profileLabel = document.getElementById("profile-label");
  const profileLines = document.getElementById("profile-lines");
  const stableBadge = document.getElementById("stable-badge");

  let queue = [];
  let current = null;
  let pendingVerdict = "";
  let busy = false;
  let editing = false;
  let drag = null;

  function show(node) { node.hidden = false; }
  function hide(node) { node.hidden = true; }

  function swipeThreshold() {
    const raw = getComputedStyle(document.documentElement).getPropertyValue("--discover-swipe");
    const value = parseFloat(raw);
    return Number.isFinite(value) ? value : 48;
  }

  function setStable(stable) {
    stableBadge.hidden = !stable;
  }

  async function readJson(response) {
    try {
      return await response.json();
    } catch (error) {
      return {};
    }
  }

  function showPostingError(message) {
    hide(postingLoading);
    hide(postingEmpty);
    hide(postingCard);
    hide(postingActions);
    hide(reasonPanel);
    postingErrorText.textContent = message;
    show(postingError);
  }

  function renderCard(posting) {
    current = posting;
    pendingVerdict = "";
    document.getElementById("posting-family").textContent = posting.role_family_label || "Role";
    document.getElementById("posting-title").textContent = posting.title || "";
    document.getElementById("posting-company").textContent = posting.company || "Company not listed";
    const place = [posting.location, posting.is_remote ? "Remote" : ""].filter(Boolean).join(" · ");
    document.getElementById("posting-place").textContent = place || "Location not listed";
    const salary = document.getElementById("posting-salary");
    if (posting.salary) {
      salary.textContent = posting.salary;
      show(salary);
    } else {
      salary.textContent = "";
      hide(salary);
    }
    document.getElementById("posting-summary").textContent = posting.summary || "No description was included with this posting.";
    const skills = document.getElementById("posting-skills");
    skills.replaceChildren();
    (posting.skills || []).forEach(function (skill) {
      const item = document.createElement("li");
      item.textContent = skill;
      skills.appendChild(item);
    });
    if (!posting.skills || !posting.skills.length) {
      const item = document.createElement("li");
      item.textContent = "None listed";
      skills.appendChild(item);
    }
    hide(reasonPanel);
    show(postingCard);
    show(postingActions);
    postingCard.style.transform = "";
  }

  function showNext() {
    if (!queue.length) {
      current = null;
      hide(postingLoading);
      hide(postingError);
      hide(postingCard);
      hide(postingActions);
      hide(reasonPanel);
      show(postingEmpty);
      return;
    }
    hide(postingLoading);
    hide(postingEmpty);
    hide(postingError);
    renderCard(queue[0]);
  }

  async function loadFeed() {
    hide(postingEmpty);
    hide(postingError);
    hide(postingCard);
    hide(postingActions);
    hide(reasonPanel);
    show(postingLoading);
    try {
      const response = await fetch("/api/discover/feed");
      const body = await readJson(response);
      if (!response.ok) {
        showPostingError(body.detail || "The job boards did not respond.");
        return;
      }
      queue = body.postings || [];
      const bits = [(body.verdict_count || 0) + " reactions", "next summary in " + (body.next_summary_in || 25)];
      if (body.thin_family_labels && body.thin_family_labels.length) {
        bits.push("Thin this round: " + body.thin_family_labels.join(", "));
      }
      postingMeta.textContent = bits.join(" · ");
      if (body.warnings && body.warnings.length && !queue.length) {
        postingEmptyText.textContent = body.warnings.join(" ");
      } else {
        postingEmptyText.textContent = "No postings left to react to.";
      }
      setStable(Boolean(body.stable));
      showNext();
    } catch (error) {
      showPostingError("The job boards did not respond.");
    }
  }

  function countsText(counts) {
    const bucket = counts || {};
    return (bucket.yes || 0) + " yes · " + (bucket.no || 0) + " no · " + (bucket.maybe || 0) + " maybe";
  }

  function lineArticle(line) {
    const article = document.createElement("article");
    article.className = "discover-line";
    const text = document.createElement("p");
    text.textContent = line.text;
    const meta = document.createElement("p");
    meta.className = "discover-muted";
    const percent = Math.round(Number(line.confidence || 0) * 100);
    meta.textContent = "Confidence " + percent + "% · " + countsText(line.counts);
    article.append(text, meta);
    if (line.locked) {
      const note = document.createElement("p");
      note.className = "discover-muted";
      note.textContent = "Locked";
      article.appendChild(note);
    }
    const row = document.createElement("div");
    row.className = "discover-row";
    if (!line.locked) {
      row.appendChild(actionButton("Confirm", function () { decideLine("confirm", line.key); }));
    }
    row.appendChild(actionButton("Edit", function () { startEdit(article, line); }));
    if (!line.locked) {
      row.appendChild(actionButton("Reject", function () { decideLine("reject", line.key); }, "danger"));
    } else {
      row.appendChild(actionButton("Unlock", function () { decideLine("unlock", line.key); }));
    }
    article.appendChild(row);
    return article;
  }

  function actionButton(label, onClick, tone) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "discover-button" + (tone ? " " + tone : "");
    button.textContent = label;
    button.addEventListener("click", onClick);
    return button;
  }

  function startEdit(article, line) {
    editing = true;
    const row = document.createElement("div");
    row.className = "discover-stack";
    const input = document.createElement("input");
    input.className = "discover-input";
    input.type = "text";
    input.value = line.text;
    const save = actionButton("Save edit", function () {
      decideLine("edit", line.key, input.value);
    }, "primary");
    row.append(input, save);
    article.appendChild(row);
    input.focus();
  }

  async function decideLine(action, key, text) {
    if (busy) return;
    busy = true;
    const payload = action === "edit" ? { key: key, text: text || "" } : { key: key };
    try {
      const response = await fetch("/api/discover/lines/" + action, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await readJson(response);
      if (!response.ok) {
        showProfileError(body.detail || "That profile line could not be saved.");
        return;
      }
      editing = false;
      await loadProfile();
    } catch (error) {
      showProfileError("The profile could not be saved.");
    } finally {
      busy = false;
    }
  }

  function showProfileError(message) {
    hide(profileLoading);
    hide(profileEmpty);
    hide(profileBody);
    profileErrorText.textContent = message;
    show(profileError);
  }

  function renderProfile(body) {
    hide(profileLoading);
    hide(profileError);
    setStable(Boolean(body.stable));
    const summary = body.summary;
    if (!summary || !(summary.lines || []).length) {
      hide(profileBody);
      const seen = body.verdict_count || 0;
      profileEmptyText.textContent = summary
        ? "Nothing left in this summary. Rejected lines are dropped."
        : "React to postings and a summary appears after every 25 verdicts. " + seen + " so far, " + (body.next_summary_in || 25) + " until the next one.";
      show(profileEmpty);
      return;
    }
    hide(profileEmpty);
    profileLabel.textContent = summary.label || "";
    profileLines.replaceChildren();
    DIMENSIONS.forEach(function (entry) {
      const group = (summary.lines || []).filter(function (line) { return line.dimension === entry[0]; });
      if (!group.length) return;
      const block = document.createElement("section");
      block.className = "discover-stack";
      const heading = document.createElement("h3");
      heading.className = "discover-title";
      heading.textContent = entry[1];
      block.appendChild(heading);
      group.forEach(function (line) { block.appendChild(lineArticle(line)); });
      profileLines.appendChild(block);
    });
    show(profileBody);
  }

  async function loadProfile() {
    if (!profileBody.hidden && editing) return;
    hide(profileEmpty);
    hide(profileError);
    hide(profileBody);
    show(profileLoading);
    try {
      const response = await fetch("/api/discover/profile");
      const body = await readJson(response);
      if (!response.ok) {
        showProfileError(body.detail || "The profile could not be loaded.");
        return;
      }
      renderProfile(body);
    } catch (error) {
      showProfileError("The profile could not be loaded.");
    }
  }

  function openReasons(verdict) {
    pendingVerdict = verdict;
    hide(postingActions);
    reasonChips.replaceChildren();
    const chips = (current && current.chips) || [];
    reasonPrompt.textContent = chips.length
      ? "Optional reason for " + verdict + ". One tap, or skip."
      : "No reason chips fit this posting.";
    chips.forEach(function (chip) {
      const button = actionButton(chip.label, function () { submitVerdict(verdict, [chip.id]); });
      reasonChips.appendChild(button);
    });
    show(reasonPanel);
  }

  function choose(verdict) {
    if (busy || !current || pendingVerdict) return;
    if (verdict === "maybe" || !current.chips || !current.chips.length) {
      submitVerdict(verdict, []);
      return;
    }
    openReasons(verdict);
  }

  async function submitVerdict(verdict, chips) {
    if (busy || !current) return;
    busy = true;
    const postingId = current.posting_id;
    try {
      const response = await fetch("/api/discover/verdicts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ posting_id: postingId, verdict: verdict, reason_chips: chips }),
      });
      const body = await readJson(response);
      if (!response.ok) {
        showPostingError(body.detail || "That reaction could not be saved.");
        return;
      }
      queue = queue.filter(function (posting) { return posting.posting_id !== postingId; });
      pendingVerdict = "";
      setStable(Boolean(body.stable));
      showNext();
      editing = false;
      loadProfile();
    } catch (error) {
      showPostingError("That reaction could not be saved.");
    } finally {
      busy = false;
    }
  }

  document.getElementById("choice-yes").addEventListener("click", function () { choose("yes"); });
  document.getElementById("choice-maybe").addEventListener("click", function () { choose("maybe"); });
  document.getElementById("choice-no").addEventListener("click", function () { choose("no"); });
  document.getElementById("reason-skip").addEventListener("click", function () {
    if (pendingVerdict) submitVerdict(pendingVerdict, []);
  });
  document.getElementById("posting-retry").addEventListener("click", loadFeed);
  document.getElementById("profile-retry").addEventListener("click", function () {
    editing = false;
    loadProfile();
  });

  document.addEventListener("keydown", function (event) {
    if (event.metaKey || event.ctrlKey || event.altKey || event.repeat) return;
    const tag = (event.target && event.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    if (pendingVerdict && event.key === "Escape") {
      submitVerdict(pendingVerdict, []);
      return;
    }
    if (pendingVerdict) return;
    const key = event.key.toLowerCase();
    if (key === "y") choose("yes");
    if (key === "m") choose("maybe");
    if (key === "n") choose("no");
  });

  postingCard.addEventListener("pointerdown", function (event) {
    if (busy || pendingVerdict || event.button !== 0) return;
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY };
    postingCard.setPointerCapture(event.pointerId);
  });
  postingCard.addEventListener("pointermove", function (event) {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    postingCard.style.transform = "translate(" + dx + "px, " + dy + "px)";
  });
  function endDrag(event) {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    drag = null;
    postingCard.style.transform = "";
    const threshold = swipeThreshold();
    if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) >= threshold) {
      choose(dx > 0 ? "yes" : "no");
    } else if (dy <= -threshold && Math.abs(dy) > Math.abs(dx)) {
      choose("maybe");
    }
  }
  postingCard.addEventListener("pointerup", endDrag);
  postingCard.addEventListener("pointercancel", function (event) {
    if (!drag || drag.id !== event.pointerId) return;
    drag = null;
    postingCard.style.transform = "";
  });

  loadFeed();
  loadProfile();
})();
