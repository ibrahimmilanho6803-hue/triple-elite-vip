/* Triple Elite VIP - espace client : génération des combinés (avec suivi d'avancement),
 * affichage des coupons, historique et bilan.
 *
 * Aucune donnée n'est jamais injectée en HTML : tout passe par textContent / createElement,
 * donc aucun risque d'injection, et la politique de sécurité (CSP) du site interdit de
 * toute façon les scripts et styles en ligne.
 */
(function () {
  'use strict';

  var app = document.getElementById('app');
  if (!app) { return; }

  var CACHE_MINUTES = parseInt(app.getAttribute('data-cache-minutes'), 10) || 180;
  var POLL_DELAY = 1500;               // ms entre deux demandes d'avancement
  var POLL_MAX_FAILURES = 6;           // échecs réseau consécutifs tolérés
  var POLL_MAX_DURATION = 6 * 60 * 1000;
  var HISTORY_REFRESH_AFTER = 60 * 1000;
  var NBSP = '\u00a0';

  var MSG_NETWORK = 'Connexion impossible. Vérifie ta connexion internet puis réessaie.';
  var MSG_GENERIC = 'Une erreur est survenue. Réessaie dans quelques minutes.';
  var MSG_LOST = 'La génération n’a pas abouti. Réessaie dans quelques minutes.';
  var MSG_TOO_LONG = 'L’analyse prend plus de temps que prévu. Réessaie dans quelques minutes.';
  var MSG_EXPIRED = 'Ces combinés ne sont plus d’actualité (un match a commencé ou ils datent de plus de ' +
    Math.round(CACHE_MINUTES / 60) + NBSP + 'h). Génère-en de nouveaux.';

  function $(id) { return document.getElementById(id); }

  // ------------------------------------------------------------------ construction du DOM

  function append(el, children) {
    if (children === null || children === undefined || children === false) { return; }
    if (!Array.isArray(children)) { children = [children]; }
    children.forEach(function (child) {
      if (child === null || child === undefined || child === false) { return; }
      if (Array.isArray(child)) { append(el, child); return; }
      el.appendChild(typeof child === 'object' ? child : document.createTextNode(String(child)));
    });
  }

  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (name) {
        var value = attrs[name];
        if (value !== null && value !== undefined && value !== false) { el.setAttribute(name, value); }
      });
    }
    append(el, children);
    return el;
  }

  var SVG_NS = 'http://www.w3.org/2000/svg';

  function icon(kind) {
    var svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 20 20');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    function add(tag, attrs) {
      var node = document.createElementNS(SVG_NS, tag);
      Object.keys(attrs).forEach(function (name) { node.setAttribute(name, attrs[name]); });
      svg.appendChild(node);
    }
    var stroke = { fill: 'none', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' };
    function line(d, color) {
      var attrs = { d: d, stroke: color };
      Object.keys(stroke).forEach(function (k) { attrs[k] = stroke[k]; });
      add('path', attrs);
    }
    if (kind === 'won') {
      add('circle', { cx: '10', cy: '10', r: '9', fill: 'currentColor' });
      line('M5.6 10.4l3 3 5.8-6.2', '#e8eaf1');
    } else if (kind === 'lost') {
      add('circle', { cx: '10', cy: '10', r: '9', fill: 'currentColor' });
      line('M6.7 6.7l6.6 6.6M13.3 6.7l-6.6 6.6', '#e8eaf1');
    } else if (kind === 'void') {
      add('circle', { cx: '10', cy: '10', r: '8.2', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6' });
      line('M6.2 10h7.6', 'currentColor');
    } else {
      add('circle', { cx: '10', cy: '10', r: '8.2', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6' });
      line('M10 5.6V10l2.9 1.9', 'currentColor');
    }
    return svg;
  }

  // ------------------------------------------------------------------ formats français

  var nfOdds = new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var dfDay = new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', month: 'short' });
  var dfShort = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short' });
  var dfShortYear = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });
  var dfTime = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

  function toDate(iso) {
    if (!iso) { return null; }
    var d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }

  function fmtOdds(value) {
    var n = parseFloat(value);
    return isFinite(n) ? nfOdds.format(n) : '–';
  }

  function fmtPct(value) {
    var n = parseFloat(value);
    return isFinite(n) ? Math.round(n) + NBSP + '%' : null;
  }

  function fmtTime(d) { return dfTime.format(d).replace(':', NBSP + 'h' + NBSP); }

  function fmtKickoff(iso) {
    var d = toDate(iso);
    return d ? dfDay.format(d) + ' à' + NBSP + fmtTime(d) : '';
  }

  function fmtShortDay(iso) {
    var d = toDate(iso);
    if (!d) { return ''; }
    var sameYear = d.getFullYear() === new Date().getFullYear();
    return (sameYear ? dfShort : dfShortYear).format(d);
  }

  // Apostrophes typographiques dans les messages venus du serveur.
  function typo(text) {
    return String(text === null || text === undefined ? '' : text)
      .replace(/([A-Za-zÀ-ÿ])'(?=[A-Za-zÀ-ÿ])/g, '$1’');
  }

  // ------------------------------------------------------------------ appels au serveur

  function request(method, url) {
    return fetch(url, {
      method: method,
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' }
    }).then(function (res) {
      return res.json().then(function (data) { return data; }, function () { return {}; })
        .then(function (data) { return { status: res.status, ok: res.ok, data: data || {} }; });
    });
  }

  // Session ou abonnement expiré : retour à la page de connexion avec une explication.
  function sessionLost(r) {
    if (r.status !== 401) { return false; }
    var why = (r.data && r.data.raison) || 'session';
    window.location.href = '/login?raison=' + encodeURIComponent(why);
    return true;
  }

  // ------------------------------------------------------------------ le coupon

  var STAMPS = { won: 'Gagné', lost: 'Perdu', pending: 'En attente', void: 'Annulé' };

  function stamp(status) {
    var key = STAMPS[status] ? status : 'pending';
    return h('span', { 'class': 'stamp stamp--' + key }, STAMPS[key]);
  }

  function resultNode(p) {
    var kind = p.outcome === 'won' ? 'won' : p.outcome === 'lost' ? 'lost' : p.outcome === 'void' ? 'void' : 'wait';
    var score = p.score ? String(p.score).replace(' - ', ' – ') : '';
    var text;
    if (kind === 'won') { text = 'Gagné, score ' + score; }
    else if (kind === 'lost') { text = 'Perdu, score ' + score; }
    else if (kind === 'void') { text = 'Match reporté ou annulé' + NBSP + ': pronostic non compté'; }
    else {
      var kickoff = toDate(p.kickoff);
      text = kickoff && kickoff.getTime() > Date.now() ? 'Match à venir' : 'Résultat en attente';
    }
    return h('p', { 'class': 'leg__result leg__result--' + kind }, [icon(kind), text]);
  }

  function legNode(p, withResult) {
    var estimated = p.odds_source !== 'bookmakers';
    var conf = fmtPct(p.confidence);
    var kickoff = fmtKickoff(p.kickoff);
    return h('li', { 'class': 'leg' }, [
      h('div', null, [
        h('p', { 'class': 'leg__teams' }, (p.home_team || '') + ' – ' + (p.away_team || '')),
        h('p', { 'class': 'leg__pick' }, p.type_name || ''),
        p.league ? h('p', { 'class': 'leg__meta' }, p.league) : null,
        kickoff ? h('p', { 'class': 'leg__meta' }, kickoff) : null
      ]),
      h('div', { 'class': 'leg__side' }, [
        h('p', { 'class': 'leg__odds' }, [
          estimated ? h('span', { 'class': 'approx', 'aria-hidden': 'true' }, '≈') : null,
          estimated ? h('span', { 'class': 'sr-only' }, 'environ ') : null,
          fmtOdds(p.estimated_odds)
        ]),
        conf ? h('p', { 'class': 'leg__conf' }, 'confiance ' + conf) : null
      ]),
      withResult ? resultNode(p) : null
    ]);
  }

  function slipNode(combo, opts) {
    var history = opts.mode === 'history';
    var chance = fmtPct(combo.success_probability);
    var title = history ? 'Combiné du ' + fmtShortDay(combo.generated_at) : 'Combiné ' + (opts.index + 1);
    var generated = history ? toDate(combo.generated_at) : null;
    var el = h('article', { 'class': 'slip' + (opts.print ? ' slip--print' : ''), 'aria-label': title }, [
      h('div', { 'class': 'slip__paper' }, [
        h('div', { 'class': 'slip__head' }, [
          h('div', null, [
            h('h2', { 'class': 'slip__title' }, title),
            generated ? h('p', { 'class': 'slip__sub' }, 'Généré à ' + fmtTime(generated)) : null
          ]),
          history ? stamp(combo.status) : null
        ]),
        h('ol', { 'class': 'legs' }, (combo.predictions || []).map(function (p) { return legNode(p, history); }))
      ]),
      h('div', { 'class': 'slip__stub' }, [
        h('div', null, [
          h('span', { 'class': 'slip__label' }, 'Cote totale'),
          h('span', { 'class': 'slip__total-value' }, fmtOdds(combo.total_odds))
        ]),
        chance ? h('div', { 'class': 'slip__chance' }, [
          h('span', { 'class': 'slip__label' }, 'Chance estimée'),
          h('span', { 'class': 'slip__chance-value' }, chance)
        ]) : null
      ])
    ]);
    if (opts.print) { el.style.setProperty('--i', String(opts.index)); }
    return el;
  }

  // ------------------------------------------------------------------ génération

  var gen = { busy: false, failures: 0, startedAt: 0, pollTimer: null, expiryTimer: null };

  function clearNotice() { $('notice').textContent = ''; }

  function setNotice(kind, text) {
    var box = $('notice');
    box.textContent = '';
    if (!text) { return; }
    box.appendChild(h('div', { 'class': 'notice notice--' + kind, 'role': kind === 'erreur' ? 'alert' : 'status' },
      h('p', null, typo(text))));
  }

  function showIdle(message) {
    gen.busy = false;
    clearTimeout(gen.pollTimer);
    $('progress').hidden = true;
    $('results').hidden = true;
    $('generate').hidden = false;
    $('btn-generate').disabled = false;
    $('btn-generate').textContent = 'Générer les combinés';
    if (message) { setNotice('info', message); }
  }

  function showProgress(progress) {
    var percent = Math.max(1, Math.min(100, parseInt(progress && progress.percent, 10) || 1));
    $('generate').hidden = true;
    $('results').hidden = true;
    $('progress').hidden = false;
    $('progress-msg').textContent = typo((progress && progress.message) || 'Analyse en cours…');
    $('progress-bar').value = percent;
  }

  function showError(message) {
    gen.busy = false;
    clearTimeout(gen.pollTimer);
    $('progress').hidden = true;
    $('results').hidden = true;
    $('generate').hidden = false;
    $('btn-generate').disabled = false;
    $('btn-generate').textContent = 'Réessayer';
    setNotice('erreur', message || MSG_GENERIC);
  }

  function showResults(data) {
    gen.busy = false;
    clearTimeout(gen.pollTimer);
    $('progress').hidden = true;
    $('generate').hidden = true;
    clearNotice();
    if (data.stale && data.notice) { setNotice('info', data.notice); }

    var combos = data.combos || [];
    var grid = $('results-grid');
    grid.textContent = '';
    combos.forEach(function (combo, index) {
      grid.appendChild(slipNode(combo, { mode: 'live', index: index, print: true }));
    });

    var generated = toDate(data.generated_at);
    var meta = data.meta || {};
    var bits = [];
    if (generated) { bits.push(['Générés à ', h('strong', null, fmtTime(generated))]); }
    if (meta.matches_total) {
      var analyzed = meta.matches_analyzed || meta.matches_total;
      var leagues = (meta.leagues || []).length || 5;
      bits.push(analyzed + ' matchs analysés' + (meta.matches_total > analyzed ? ' sur ' + meta.matches_total : '') +
        ' dans ' + leagues + ' championnats');
    }
    var metaBox = $('results-meta');
    metaBox.textContent = '';
    bits.forEach(function (bit, i) {
      if (i > 0) { metaBox.appendChild(document.createTextNode('. ')); }
      append(metaBox, bit);
    });
    if (bits.length) { metaBox.appendChild(document.createTextNode('.')); }
    $('results').hidden = false;
    scheduleExpiry(data);
  }

  // Quand les combinés cessent d'être valables (cache écoulé ou match commencé), on le dit.
  function scheduleExpiry(data) {
    clearTimeout(gen.expiryTimer);
    if (data.stale) { return; }
    var ends = [];
    var generated = toDate(data.generated_at);
    if (generated) { ends.push(generated.getTime() + CACHE_MINUTES * 60000); }
    (data.combos || []).forEach(function (combo) {
      (combo.predictions || []).forEach(function (p) {
        var kickoff = toDate(p.kickoff);
        if (kickoff) { ends.push(kickoff.getTime()); }
      });
    });
    if (!ends.length) { return; }
    var delay = Math.max(30000, Math.min.apply(null, ends) - Date.now() + 1000);
    gen.expiryTimer = setTimeout(refreshStatus, Math.min(delay, 2147000000));
  }

  function refreshStatus() {
    request('GET', '/api/generate/status').then(function (r) {
      if (sessionLost(r)) { return; }
      var d = r.data || {};
      if (d.state === 'done') { showResults(d); }
      else if (d.state === 'running') { adoptRunning(d); }
      else { showIdle(MSG_EXPIRED); }
    }, function () { gen.expiryTimer = setTimeout(refreshStatus, 60000); });
  }

  function adoptRunning(d) {
    gen.busy = true;
    gen.failures = 0;
    gen.startedAt = Date.now();
    showProgress(d.progress || {});
    schedulePoll();
  }

  function onState(r, fromPoll) {
    if (sessionLost(r)) { return; }
    var d = r.data || {};
    if (d.state === 'running') {
      gen.busy = true;
      showProgress(d.progress || {});
      schedulePoll();
    } else if (d.state === 'done') {
      showResults(d);
    } else if (d.state === 'error') {
      showError(d.error || MSG_GENERIC);
    } else if (d.state === 'idle' && fromPoll) {
      showError(MSG_LOST);
    } else {
      showError(d.error || MSG_GENERIC);
    }
  }

  function schedulePoll() {
    clearTimeout(gen.pollTimer);
    gen.pollTimer = setTimeout(poll, POLL_DELAY);
  }

  function poll() {
    if (Date.now() - gen.startedAt > POLL_MAX_DURATION) { showError(MSG_TOO_LONG); return; }
    request('GET', '/api/generate/status').then(function (r) {
      gen.failures = 0;
      onState(r, true);
    }, function () {
      gen.failures += 1;
      if (gen.failures >= POLL_MAX_FAILURES) { showError(MSG_NETWORK); } else { schedulePoll(); }
    });
  }

  function startGeneration() {
    if (gen.busy) { return; }
    gen.busy = true;
    gen.failures = 0;
    gen.startedAt = Date.now();
    clearNotice();
    $('btn-generate').disabled = true;
    showProgress({ percent: 1, message: 'Démarrage de l’analyse…' });
    request('POST', '/api/generate').then(function (r) { onState(r, false); }, function () { showError(MSG_NETWORK); });
  }

  // ------------------------------------------------------------------ historique

  var hist = { loading: false, loadedAt: 0, shown: false };

  function showHistoryError(message) {
    var box = $('history-error');
    box.textContent = '';
    if (!message) { return; }
    box.appendChild(h('div', { 'class': 'notice notice--erreur', 'role': 'alert' }, h('p', null, typo(message))));
  }

  function renderHistory(data) {
    var s = data.summary || {};
    var combos = data.combos || [];
    var settled = s.combos_settled || 0;
    var legsSettled = (s.legs_won || 0) + (s.legs_lost || 0);

    $('bilan-combos').textContent = settled ? (s.won || 0) + ' / ' + settled : '–';
    $('bilan-combos-sub').textContent = settled
      ? 'soit ' + (s.combo_win_rate || 0) + NBSP + '% des combinés réglés'
      : 'aucun combiné réglé pour le moment';
    $('bilan-legs').textContent = legsSettled ? (s.legs_won || 0) + ' / ' + legsSettled : '–';
    $('bilan-legs-sub').textContent = legsSettled
      ? 'soit ' + (s.leg_win_rate || 0) + NBSP + '% des pronostics joués'
      : 'aucun pronostic joué pour le moment';
    $('bilan-pending').textContent = String(s.pending || 0);

    var note = 'Un combiné est gagné quand ses trois pronostics le sont, et perdu dès qu’un seul échoue. ' +
      'Avec des pronostics à environ 70' + NBSP + '%, un combiné réussit en moyenne une fois sur trois. ' +
      'Les matchs reportés ou annulés ne sont pas comptés.';
    $('bilan-note').textContent = note;

    var grid = $('history-grid');
    grid.textContent = '';
    combos.forEach(function (combo, index) {
      grid.appendChild(slipNode(combo, { mode: 'history', index: index, print: false }));
    });

    var empty = combos.length === 0;
    $('history-empty').hidden = !empty;
    $('bilan').hidden = empty;
    $('bilan-note').hidden = empty;
    $('history-content').hidden = false;
    hist.shown = true;
  }

  function loadHistory(force) {
    if (hist.loading) { return; }
    if (!force && hist.loadedAt && Date.now() - hist.loadedAt < HISTORY_REFRESH_AFTER) { return; }
    hist.loading = true;
    showHistoryError('');
    if (!hist.shown) { $('history-loading').hidden = false; }
    request('GET', '/api/history').then(function (r) {
      hist.loading = false;
      $('history-loading').hidden = true;
      if (sessionLost(r)) { return; }
      if (!r.ok || !r.data || !Array.isArray(r.data.combos)) {
        showHistoryError((r.data && r.data.error) || MSG_GENERIC);
        return;
      }
      hist.loadedAt = Date.now();
      renderHistory(r.data);
    }, function () {
      hist.loading = false;
      $('history-loading').hidden = true;
      showHistoryError(MSG_NETWORK);
    });
  }

  // ------------------------------------------------------------------ onglets

  var TABS = ['combos', 'history'];

  function selectTab(name, focus) {
    TABS.forEach(function (n) {
      var tab = $('tab-' + n);
      var on = n === name;
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
      tab.setAttribute('tabindex', on ? '0' : '-1');
      $('panel-' + n).hidden = !on;
      if (on && focus) { tab.focus(); }
    });
    try {
      window.history.replaceState(null, '', name === 'history' ? '#historique' : window.location.pathname);
    } catch (e) { /* sans importance */ }
    if (name === 'history') { loadHistory(false); }
  }

  TABS.forEach(function (name, index) {
    var tab = $('tab-' + name);
    tab.addEventListener('click', function () { selectTab(name, false); });
    tab.addEventListener('keydown', function (event) {
      var target = null;
      if (event.key === 'ArrowRight') { target = TABS[(index + 1) % TABS.length]; }
      else if (event.key === 'ArrowLeft') { target = TABS[(index + TABS.length - 1) % TABS.length]; }
      else if (event.key === 'Home') { target = TABS[0]; }
      else if (event.key === 'End') { target = TABS[TABS.length - 1]; }
      if (target) { event.preventDefault(); selectTab(target, true); }
    });
  });

  $('btn-generate').addEventListener('click', startGeneration);
  $('btn-to-combos').addEventListener('click', function () { selectTab('combos', true); });

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState !== 'visible') { return; }
    if (gen.busy) { clearTimeout(gen.pollTimer); poll(); }
    else if (!$('results').hidden) { refreshStatus(); }
  });

  // ------------------------------------------------------------------ démarrage

  request('GET', '/api/generate/status').then(function (r) {
    if (sessionLost(r)) { return; }
    var d = r.data || {};
    if (d.state === 'running') { adoptRunning(d); }
    else if (d.state === 'done') { showResults(d); }
    else if (d.state === 'error') { showError(d.error || MSG_GENERIC); }
    else if (!r.ok) { showError(d.error || MSG_GENERIC); }
  }, function () { setNotice('erreur', MSG_NETWORK); });

  if (window.location.hash === '#historique') { selectTab('history', false); }
})();
