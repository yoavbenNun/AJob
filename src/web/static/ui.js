document.addEventListener("submit", (event) => {
  const form = event.target;
  const submitter = event.submitter;
  if (!(form instanceof HTMLFormElement) || !(submitter instanceof HTMLButtonElement)) return;

  form.dataset.pending = "true";
  submitter.classList.add("is-busy");
  submitter.setAttribute("aria-busy", "true");
});

const sourcesMenu = document.querySelector(".sources-menu");
if (sourcesMenu) {
  const sourcesPopover = sourcesMenu.querySelector(".sources-popover");
  const supportsPopover = sourcesPopover
    && typeof sourcesPopover.showPopover === "function"
    && typeof sourcesPopover.hidePopover === "function";
  if (sourcesPopover && !supportsPopover) sourcesMenu.classList.add("popover-fallback");
  const positionSourcesPopover = () => {
    if (!sourcesPopover) return;
    if (!sourcesMenu.open) {
      if (supportsPopover && sourcesPopover.matches(":popover-open")) sourcesPopover.hidePopover();
      return;
    }
    if (supportsPopover && !sourcesPopover.matches(":popover-open")) sourcesPopover.showPopover();
    const summary = sourcesMenu.querySelector("summary");
    if (!summary) return;
    const rect = summary.getBoundingClientRect();
    const right = Math.max(12, window.innerWidth - rect.right);
    const bottomAlignedTop = Math.max(12, window.innerHeight - sourcesPopover.getBoundingClientRect().height - 12);
    const top = Math.min(rect.bottom + 10, bottomAlignedTop);
    sourcesPopover.style.setProperty("--sources-top", `${top}px`);
    sourcesPopover.style.setProperty("--sources-right", `${right}px`);
  };

  sourcesMenu.addEventListener("toggle", positionSourcesPopover);
  if (supportsPopover) {
    sourcesPopover.addEventListener("toggle", () => {
      if (!sourcesPopover.matches(":popover-open") && sourcesMenu.open) sourcesMenu.open = false;
    });
  }
  window.addEventListener("resize", positionSourcesPopover);
  window.addEventListener("scroll", positionSourcesPopover, { passive: true });
}

const popoverMenus = document.querySelectorAll(".sources-menu, .resume-menu, .email-menu");
document.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  const clickedMenu = target
    ? target.closest(".sources-menu, .resume-menu, .email-menu")
    : null;
  const clickedSourcesPopover = target?.closest(".sources-popover");
  for (const menu of popoverMenus) {
    if (menu !== clickedMenu && !(menu === sourcesMenu && clickedSourcesPopover)) menu.open = false;
  }
});
