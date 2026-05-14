// Theme toggle: cycles auto → light → dark → auto.
(function () {
  function applyMode(mode) {
    document.documentElement.setAttribute('data-theme-mode', mode);
    if (mode === 'auto') {
      localStorage.removeItem('theme-mode');
      document.documentElement.removeAttribute('data-theme');
    } else {
      localStorage.setItem('theme-mode', mode);
      document.documentElement.setAttribute('data-theme', mode);
    }
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-theme-toggle]');
    if (!btn) return;
    var current = document.documentElement.getAttribute('data-theme-mode') || 'auto';
    var next = current === 'auto' ? 'light' : current === 'light' ? 'dark' : 'auto';
    applyMode(next);
  });
})();

// Wiki-chip switch: if we're on /article/X, preserve the article context across the wiki switch.
(function () {
  document.addEventListener('click', function (e) {
    var chip = e.target.closest('.chip--switchable[data-target-wiki]');
    if (!chip) return;
    var match = window.location.pathname.match(/^\/article\/(.+)$/);
    if (match) {
      e.preventDefault();
      window.location.href =
        '/switch-wiki?to=' + encodeURIComponent(chip.dataset.targetWiki) +
        '&article=' + encodeURIComponent(decodeURIComponent(match[1]));
      return;
    }
    var article = new URLSearchParams(window.location.search).get('article');
    if (article) {
      e.preventDefault();
      window.location.href =
        '/switch-wiki?to=' + encodeURIComponent(chip.dataset.targetWiki) +
        '&article=' + encodeURIComponent(article);
    }
  });
})();

// Kebab action menu: toggle on click, dismiss on outside-click.
(function () {
  function closeAll(except) {
    document.querySelectorAll('.action-dropdown').forEach(function (d) {
      if (d !== except) d.classList.remove('open');
    });
  }
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-toggle]');
    if (btn) {
      var el = document.getElementById(btn.getAttribute('data-toggle'));
      if (!el) return;
      var opening = !el.classList.contains('open');
      closeAll(el);
      if (opening) el.classList.add('open');
      e.stopPropagation();
      return;
    }
    closeAll();
  });
})();

// Search results: dismiss the dropdown on outside-click of the search panel.
(function () {
  document.addEventListener('click', function (e) {
    if (e.target.closest('.search-hero') || e.target.closest('.search-results')) return;
    var results = document.getElementById('results');
    if (results) results.innerHTML = '';
  });
})();
