// Disable a submit button after the first click so a rapid double-tap or a
// slow-network user hitting the button twice cannot create two rows for the
// same person. Server-side guards (unique phone, name+parent check) are the
// real defense; this just closes the visible race window.
document.addEventListener('DOMContentLoaded', function () {
  // data-confirm runs first so a cancelled confirm() doesn't fire the
  // single-submit handler and leave the button disabled.
  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll('form[data-single-submit]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (event.defaultPrevented) return;
      var btn = form.querySelector('button[type="submit"], input[type="submit"]');
      if (!btn || btn.disabled) return;
      var originalText = btn.textContent;
      btn.disabled = true;
      btn.setAttribute('aria-busy', 'true');
      btn.textContent = '…';
      // If the request stalls or the response keeps us on the same page
      // (validation error), re-enable so the user is not stuck.
      window.setTimeout(function () {
        if (btn.disabled) {
          btn.disabled = false;
          btn.removeAttribute('aria-busy');
          btn.textContent = originalText;
        }
      }, 8000);
    });
  });
});

// Tapping a date in the weekly timeline shows only that day's schedule;
// tapping it again (or the same day twice) restores the full week view.
document.addEventListener('DOMContentLoaded', function () {
  var dateButtons = document.querySelectorAll('.week-grid__date');
  var dayBlocks = document.querySelectorAll('.week-grid__day');
  if (!dateButtons.length || !dayBlocks.length) return;

  var activeWeekday = null;

  function applyFilter() {
    dayBlocks.forEach(function (day) {
      var match = activeWeekday === null || day.dataset.weekday === activeWeekday;
      day.classList.toggle('is-hidden', !match);
    });
    dateButtons.forEach(function (btn) {
      btn.classList.toggle('is-selected', btn.dataset.weekday === activeWeekday);
    });
  }

  function toggleDay(weekday) {
    activeWeekday = activeWeekday === weekday ? null : weekday;
    applyFilter();
  }

  dateButtons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      toggleDay(btn.dataset.weekday);
    });
    btn.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        toggleDay(btn.dataset.weekday);
      }
    });
  });
});
