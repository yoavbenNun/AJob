document.addEventListener("click", async (event) => {
  const button = event.target.closest(".copy-draft");
  if (!button) return;
  const textarea = document.getElementById(button.dataset.copyTarget);
  const form = document.getElementById(button.dataset.activityForm);
  const status = document.getElementById("copy-status");
  try {
    await navigator.clipboard.writeText(textarea.value);
    await fetch(form.action, { method: "POST", body: new URLSearchParams(new FormData(form)), credentials: "same-origin" });
    status.textContent = "Copied. Paste and send manually on LinkedIn.";
  } catch (_) {
    textarea.focus();
    textarea.select();
    status.textContent = "Automatic copy was unavailable. The text is selected; copy it manually.";
  }
});
