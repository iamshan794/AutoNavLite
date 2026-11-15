// AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
// Entry screen: wake the navigation computer on Enter, redirect when it is healthy; ?exit=1 puts it to sleep.
(() => {
  'use strict';
  const btn = document.getElementById('enter');
  const box = document.getElementById('status');
  const say = (cls, text) => { box.className = 'status ' + cls; box.lastElementChild.textContent = text; };
  const status = () => fetch('api/status', { cache: 'no-store' }).then((r) => r.json());
  const appUrl = (s) => s.app_url.replaceAll('{host}', location.hostname);
  let entering = false;

  function show(s) {
    if (entering && s.state !== 'error') return;
    if (s.state === 'awake') say('awake', 'navigation computer is awake');
    else if (s.state === 'waking') say('busy', 'navigation computer is waking up…');
    else if (s.state === 'sleeping') say('busy', 'navigation computer is shutting down…');
    else if (s.state === 'error') say('error', s.error || 'navigation computer failed to start');
    else say('asleep', 'navigation computer is asleep · Enter wakes it');
    btn.disabled = s.state === 'sleeping';
  }

  async function enter() {
    entering = true;
    btn.disabled = true;
    say('busy', 'waking the navigation computer…');
    const t0 = Date.now();
    try {
      const r = await fetch('api/wake', { method: 'POST' });
      if (!r.ok) throw new Error((await r.json()).error || r.statusText);
      for (;;) {
        const s = await status();
        if (s.state === 'awake') { say('awake', 'ready · opening AutoNav-0…'); location.href = appUrl(s); return; }
        if (s.state === 'error') throw new Error(s.error);
        say('busy', `waking the navigation computer… ${Math.round((Date.now() - t0) / 1000)} s`);
        await new Promise((res) => setTimeout(res, 1000));
      }
    } catch (e) {
      entering = false;
      say('error', String(e.message || e));
      btn.disabled = false;
    }
  }
  btn.onclick = enter;

  async function poll() {
    try { show(await status()); } catch (e) { say('error', 'entry station cannot reach its API'); }
    setTimeout(poll, 4000);
  }

  // Coming back from the app's Exit button: put the navigation computer to sleep.
  if (new URLSearchParams(location.search).has('exit')) {
    history.replaceState(null, '', location.pathname);
    say('busy', 'navigation computer is shutting down…');
    fetch('api/sleep', { method: 'POST' }).finally(poll);
  } else {
    poll();
  }
})();
