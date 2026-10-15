document.addEventListener("submit", (event) => {
  const form = event.target;
  const submitter = event.submitter;
  if (!(form instanceof HTMLFormElement) || !(submitter instanceof HTMLButtonElement)) return;

  form.dataset.pending = "true";
  submitter.classList.add("is-busy");
  submitter.setAttribute("aria-busy", "true");
});
