(() => {
  const picker = document.querySelector('.status-picker');
  if (picker) {
    picker.addEventListener('change', () => {
      const labels = [...picker.querySelectorAll('input:checked')].map(input => input.parentElement.textContent.trim());
      document.querySelector('#status-selection').textContent = labels.join(', ') || 'All active statuses';
    });
    document.addEventListener('click', event => { if (!picker.contains(event.target)) picker.open = false; });
    picker.addEventListener('keydown', event => {
      if (event.key === 'Escape') { picker.open = false; picker.querySelector('summary').focus(); }
    });
  }
  const feedback = document.querySelector('.job-feedback');
  if (!feedback) return;
  const undo = feedback.querySelector('.undo-dismiss');
  let undoAction = null;
  function notify(text, action = null) {
    feedback.querySelector('span').textContent = text;
    feedback.hidden = false;
    undoAction = action;
    undo.hidden = !action;
  }
  feedback.querySelector('.close-feedback').addEventListener('click', () => { feedback.hidden = true; });
  async function save(id, action, token) {
    const response = await fetch(`/jobs/${id}/dismissal`, {
      method: 'POST', credentials: 'same-origin',
      body: new URLSearchParams({action, csrf_token: token})
    });
    if (!response.ok) throw new Error('Could not save this change. Please try again.');
    return response.json();
  }
  undo.addEventListener('click', async () => {
    if (!undoAction) return;
    undo.disabled = true;
    try { await undoAction(); } catch (error) { notify(error.message, undoAction); }
    finally { undo.disabled = false; }
  });
  document.querySelectorAll('.dismiss-job').forEach(button => {
    button.addEventListener('click', async () => {
      const card = button.closest('.job-card');
      const action = button.dataset.action;
      if (action === 'dismiss' && !window.confirm('Dismiss this job? You can restore it using the Dismissed status filter.')) return;
      button.disabled = true;
      try {
        const result = await save(card.dataset.jobId, action, button.dataset.token);
        const selected = new URLSearchParams(location.search).getAll('status').filter(value => value && value !== 'all').map(value => value === 'dismissed' ? 'ignored' : value);
        const visible = selected.length ? selected.includes(result.status) : result.status !== 'ignored';
        card.hidden = !visible;
        card.dataset.status = result.status;
        button.dataset.action = result.status === 'ignored' ? 'restore' : 'dismiss';
        button.textContent = result.status === 'ignored' ? 'Restore' : 'Dismiss';
        button.setAttribute('aria-label', `${button.textContent} ${card.querySelector('h2').textContent.trim()}`);
        const badge = card.querySelector('.status');
        badge.className = `status status--${result.status}`;
        badge.textContent = result.status === 'ignored' ? 'Dismissed' : result.status[0].toUpperCase() + result.status.slice(1);
        const updateCount = () => { document.querySelector('.count').textContent = `${document.querySelectorAll('.job-card:not([hidden])').length} shown`; };
        updateCount();
        if (action === 'dismiss') {
          notify('Job dismissed.', async () => { await save(card.dataset.jobId, 'restore', button.dataset.token); location.reload(); });
          undo.focus();
        } else {
          notify('Job restored.');
          if (!visible) feedback.querySelector('.close-feedback').focus();
        }
      } catch (error) { notify(error.message); }
      finally { button.disabled = false; }
    });
  });
})();
