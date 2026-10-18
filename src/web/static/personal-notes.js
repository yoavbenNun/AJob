// Notes never leave the local dashboard API. Render all user input as text.
let activeNote = null;

function positionNote() {
  if (!activeNote) return;
  const { panel, trigger } = activeNote;
  const rect = trigger.getBoundingClientRect();
  panel.style.setProperty('--note-left', `${Math.max(16, Math.min(rect.right - panel.offsetWidth, innerWidth - panel.offsetWidth - 16))}px`);
  panel.style.setProperty('--note-top', `${Math.max(16, Math.min(rect.bottom + 8, innerHeight - panel.offsetHeight - 16))}px`);
}

function closeNote(discard = false, focus = true) {
  if (!activeNote || activeNote.busy) return;
  const note = activeNote;
  if (discard) note.text.value = note.saved;
  if (typeof note.panel.hidePopover === 'function' && note.panel.matches(':popover-open')) note.panel.hidePopover();
  note.panel.classList.remove('is-open');
  note.root.classList.remove('is-editing');
  note.trigger.setAttribute('aria-expanded', 'false');
  activeNote = null;
  if (focus) note.trigger.focus();
}

document.querySelectorAll('.personal-note').forEach((root) => {
  const trigger = root.querySelector('.note-trigger');
  const panel = root.querySelector('.note-editor');
  const form = root.querySelector('.note-form');
  const text = form.elements.note;
  const preview = root.querySelector('.note-preview');
  const error = root.querySelector('.note-error');
  const save = form.querySelector('[type=submit]');
  const cancel = root.querySelector('.note-cancel');
  const note = { root, trigger, panel, form, text, saved: text.value, busy: false };

  trigger.addEventListener('click', () => {
    if (activeNote === note) { closeNote(true); return; }
    if (activeNote?.busy) return;
    closeNote(false, false); // Keep an unfinished draft if switching cards.
    activeNote = note;
    root.classList.add('is-editing');
    panel.classList.add('is-open');
    if (typeof panel.showPopover === 'function') panel.showPopover();
    trigger.setAttribute('aria-expanded', 'true');
    error.hidden = true;
    positionNote();
    text.focus();
  });
  cancel.addEventListener('click', () => closeNote(true));
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (note.busy) return;
    const body = new URLSearchParams(new FormData(form));
    note.busy = true;
    save.disabled = cancel.disabled = trigger.disabled = text.disabled = true;
    error.hidden = true;
    try {
      const response = await fetch(form.action, { method: 'POST', body, credentials: 'same-origin' });
      if (!response.ok) throw new Error('save-failed');
      const result = await response.json();
      if (typeof result.note !== 'string') throw new Error('invalid-response');
      note.saved = result.note;
      text.value = result.note;
      preview.textContent = result.note || 'Add a personal note';
      root.classList.toggle('has-note', Boolean(result.note));
      note.busy = false;
      closeNote();
    } catch {
      error.textContent = 'Could not save your note. Your text is still here. Please try again.';
      error.hidden = false;
    } finally {
      note.busy = false;
      save.disabled = cancel.disabled = trigger.disabled = text.disabled = false;
      save.classList.remove('is-busy');
      save.removeAttribute('aria-busy');
      delete form.dataset.pending;
      if (activeNote === note) text.focus();
      else if (!activeNote) trigger.focus();
    }
  });
});
document.addEventListener('click', (event) => {
  if (activeNote && !activeNote.root.contains(event.target)) {
    closeNote(false, false); // Dismiss without saving or discarding the draft.
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && activeNote && !activeNote.busy) {
    event.preventDefault();
    closeNote(true);
  }
});
window.addEventListener('resize', positionNote);
window.addEventListener('scroll', positionNote, { passive: true });
