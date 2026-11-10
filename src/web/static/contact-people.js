/* Local drafts only: no contact/profile request is ever sent to LinkedIn. */
(() => {
  "use strict";
  const section = document.getElementById("outreach");
  if (!section) return;
  section.querySelectorAll(".contact-person-card__avatar img").forEach((image) => {
    if (image.complete && image.naturalWidth === 0) image.remove();
    image.addEventListener("error", () => image.remove(), {once: true});
  });
  const dialog = section.querySelector(".contact-draft-dialog");
  if (!dialog || typeof dialog.showModal !== "function") return;
  const body = dialog.querySelector("#contact-draft-body");
  const style = dialog.querySelector("#contact-draft-style");
  const status = dialog.querySelector("#contact-draft-status");
  const count = dialog.querySelector("#contact-draft-count");
  const copy = dialog.querySelector(".contact-draft-copy");
  const revise = dialog.querySelector(".contact-draft-regenerate");
  const profile = dialog.querySelector(".contact-draft-profile");
  const title = dialog.querySelector("#contact-draft-title");
  let controller = null;
  let trigger = null;
  let requestId = 0;
  let limit = 200;
  let revision = 0;
  let busy = false;

  function updateCount() {
    const length = body.value.length;
    count.textContent = `${length} / ${limit} characters`;
    count.dataset.overLimit = String(length > limit);
    copy.disabled = busy || length === 0 || length > limit;
  }

  function setBusy(value) {
    busy = value;
    style.disabled = value;
    revise.disabled = value;
    body.readOnly = value;
    dialog.setAttribute("aria-busy", String(value));
    updateCount();
  }

  async function generate() {
    if (!trigger) return;
    if (controller) controller.abort();
    controller = new AbortController();
    const activeController = controller;
    const currentRequest = ++requestId;
    const timer = window.setTimeout(() => activeController.abort(), 12000);
    const selectedStyle = style.value;
    limit = selectedStyle === "short" ? 200 : 2000;
    setBusy(true);
    status.textContent = "Preparing a draft…";
    try {
      const response = await fetch(section.dataset.draftUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          profile_url: trigger.dataset.profileUrl,
          style: selectedStyle,
          csrf_token: section.dataset.csrfToken,
          revision: revision
        }),
        signal: activeController.signal
      });
      if (!response.ok) throw new Error("Draft unavailable");
      const result = await response.json();
      if (typeof result.body !== "string" || !Number.isInteger(result.limit) ||
          result.limit < 1 || result.limit > 2000 || result.style !== selectedStyle) {
        throw new Error("Invalid draft response");
      }
      if (currentRequest !== requestId || !dialog.open) return;
      body.value = result.body;
      limit = result.limit;
      status.textContent = "Draft ready. Review and edit before copying.";
    } catch (error) {
      if (currentRequest !== requestId || !dialog.open) return;
      status.textContent = "Draft unavailable. Your text is unchanged. Please try again.";
    } finally {
      window.clearTimeout(timer);
      if (currentRequest === requestId) {
        controller = null;
        setBusy(false);
      }
    }
  }

  section.querySelectorAll(".contact-draft-trigger").forEach((button) => {
    button.disabled = false;
    button.addEventListener("click", () => {
      trigger = button;
      revision = 0;
      style.value = "short";
      title.textContent = `Draft for ${button.dataset.contactName}`;
      profile.href = button.dataset.profileUrl;
      body.value = "";
      dialog.showModal();
      generate();
    });
  });
  dialog.querySelector(".contact-draft-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    requestId++;
    if (controller) controller.abort();
    controller = null;
    setBusy(false);
    if (trigger) trigger.focus();
  });
  style.addEventListener("change", () => {
    revision = 0;
    generate();
  });
  revise.addEventListener("click", () => {
    revision++;
    generate();
  });
  body.addEventListener("input", () => {
    status.textContent = "Edited draft. Review before copying.";
    updateCount();
  });
  copy.addEventListener("click", async () => {
    updateCount();
    if (copy.disabled) return;
    try {
      await navigator.clipboard.writeText(body.value);
      status.textContent = "Copied. Open the profile and send manually if you choose.";
    } catch (error) {
      body.focus();
      body.select();
      status.textContent = "Clipboard unavailable. Your draft is selected; copy it manually.";
    }
  });
})();
