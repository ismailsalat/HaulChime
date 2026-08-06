/* DOM tests for the quote form.

   These run the real built main.js inside jsdom against the real built HTML,
   so they catch what a regex over the source cannot: the `hidden` attribute
   being overridden by a stylesheet, a badge rendered twice by two different
   helpers, a page that still carries navigation it shouldn't.

   Run with:  node test/quote-form.test.js
*/
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const DIST = path.join(__dirname, '..', 'dist');
let failures = 0;
let passes = 0;

function check(name, condition, detail) {
  if (condition) {
    passes += 1;
    console.log('  ok   ' + name);
  } else {
    failures += 1;
    console.log('  FAIL ' + name + (detail ? '\n         ' + detail : ''));
  }
}

/* Boot the quote page in jsdom with the real CSS applied and fetch stubbed,
   then hand back the window once the app has rendered its first step. */
async function bootQuotePage() {
  const html = fs.readFileSync(path.join(DIST, 'quote', 'index.html'), 'utf8');
  const css = fs.readFileSync(path.join(DIST, 'css', 'styles.css'), 'utf8');
  const js = fs.readFileSync(path.join(DIST, 'js', 'main.js'), 'utf8');

  const dom = new JSDOM(html, {
    runScripts: 'outside-only',
    url: 'https://haulchime.com/quote/',
    pretendToBeVisual: true,
  });
  const { window } = dom;

  // Inline the stylesheet so getComputedStyle sees the real rules — this is
  // the whole point of the duplicate-file-input test.
  const style = window.document.createElement('style');
  style.textContent = css;
  window.document.head.appendChild(style);

  // The form calls /api/config on boot; answer it without a network.
  window.fetch = () => Promise.resolve({
    ok: true,
    json: () => Promise.resolve({
      brand: 'HaulChime', phoneVerificationEnabled: true,
      phoneVerificationRequired: true, addressLookupEnabled: false,
      maxPhotos: 10, maxPhotoMb: 8, resendDelaySeconds: 60,
      consentText: 'I agree that HaulChime may share my request.',
    }),
  });
  window.scrollTo = () => {};
  window.URL.createObjectURL = () => 'blob:stub';

  dom.window.eval(js);
  await new Promise((resolve) => setTimeout(resolve, 60));
  return window;
}

/* Drive the form to a given step by clicking real buttons. */
function pick(window, key, value) {
  const button = window.document.querySelector(
    `[data-pick="${key}"][data-value="${value}"]`);
  if (!button) throw new Error(`no card for ${key}=${value}`);
  button.click();
}
function toggle(window, key, value) {
  const button = window.document.querySelector(
    `[data-toggle="${key}"][data-value="${value}"]`);
  if (!button) throw new Error(`no chip for ${key}=${value}`);
  button.click();
}
function next(window) {
  window.document.querySelector('#next').click();
}

(async function run() {
  console.log('\nQuote form — DOM tests\n');

  // ---------------------------------------------------------------- photos
  {
    const window = await bootQuotePage();
    pick(window, 'service_pick', 'junk_removal');
    pick(window, 'job_type', 'room_cleanout');
    next(window);
    // Step 2 needs an address; jump the state by filling manual fields.
    const step2 = window.document.querySelector('#f-pickup_address');
    check('manual address fallback renders when lookup is off', !!step2);
    if (step2) {
      step2.value = '123 Main St';
      step2.dispatchEvent(new window.Event('input', { bubbles: true }));
      const city = window.document.querySelector('#f-pickup_city');
      const zip = window.document.querySelector('#f-zip_code');
      city.value = 'Kent';
      city.dispatchEvent(new window.Event('input', { bubbles: true }));
      zip.value = '98030';
      zip.dispatchEvent(new window.Event('input', { bubbles: true }));
    }
    pick(window, 'timing', 'flexible');
    pick(window, 'property_type', 'house');
    next(window);

    const fileInputs = [...window.document.querySelectorAll('input[type="file"]')];
    check('two file inputs exist (gallery + camera)', fileInputs.length === 2,
          `found ${fileInputs.length}`);

    const visible = fileInputs.filter((input) => {
      const display = window.getComputedStyle(input).display;
      return display !== 'none';
    });
    check('NO native file input is visible to the customer',
          visible.length === 0,
          `${visible.length} visible — this is the "Choose File / No file chosen" bug`);

    const styledButtons = [...window.document.querySelectorAll(
      '#photo-take, #photo-choose')];
    check('exactly two styled photo buttons are shown',
          styledButtons.length === 2, `found ${styledButtons.length}`);
    check('the photo limit is stated to the customer',
          /10 images/.test(window.document.body.textContent)
          && /8 MB/.test(window.document.body.textContent));
    check('hidden inputs are out of the tab order',
          fileInputs.every((i) => i.getAttribute('tabindex') === '-1'));

    // ------------------------------------------------------ required labels
    const questions = [...window.document.querySelectorAll('.q')];
    check('step 3 rendered questions', questions.length > 3);
    const doubled = questions.filter((q) => q.querySelectorAll('.tag-req').length > 1);
    check('no question shows more than one REQUIRED badge',
          doubled.length === 0,
          doubled.map((q) => q.id + ' has ' +
            q.querySelectorAll('.tag-req').length + ' badges').join('; '));
    window.close();
  }

  // -------------------------------------------- required labels on step 2
  {
    const window = await bootQuotePage();
    pick(window, 'service_pick', 'local_move');
    pick(window, 'job_type', 'apartment_move');
    next(window);
    pick(window, 'destination_known', 'yes');

    const destination = window.document.querySelector('#q-destination');
    check('the drop-off address question exists', !!destination);
    if (destination) {
      const required = destination.querySelectorAll('.tag-req');
      check('"Drop-off address" section shows ONE required badge, not one per box',
            required.length <= 1,
            `found ${required.length} — this was the screenshot bug`);
      const inputs = [...destination.querySelectorAll('input[required]')];
      check('the address boxes are genuinely required, badge or not',
            inputs.length >= 2,
            `only ${inputs.length} required inputs in the address block`);
    }

    const required = [...window.document.querySelectorAll('input[required]')];
    check('required fields carry a real required attribute',
          required.length > 0, 'validation must not depend on the badge alone');
    check('required fields are announced to screen readers',
          required.every((i) => i.getAttribute('aria-required') === 'true'));
    window.close();
  }

  // ------------------------------------------------------ ad landing page
  {
    const html = fs.readFileSync(path.join(DIST, 'quote', 'index.html'), 'utf8');
    const links = [...html.matchAll(/href="([^"]+)"/g)].map((m) => m[1])
      .filter((href) => href.startsWith('/') && !href.startsWith('/css')
                        && !href.startsWith('/js') && href !== '/favicon.svg');
    const allowed = ['/quote/', '/privacy/', '/terms/'];
    const strays = links.filter((href) => !allowed.includes(href));
    check('/quote has no navigation away from the form',
          strays.length === 0, 'stray links: ' + strays.join(', '));
    check('/quote is noindex (ad landing pages should not be crawled)',
          /noindex/.test(html));
    check('/quote mounts the quote app', /data-quote-app/.test(html));
    check('/quote points at the production API',
          /window\.HAULCHIME_API="https:\/\//.test(html),
          'built without --prod? it would call localhost');
    check('/quote/index.html exists so a direct visit and refresh both work',
          fs.existsSync(path.join(DIST, 'quote', 'index.html')));
  }

  // ------------------------------------------------ partner CTAs
  {
    const partners = fs.readFileSync(path.join(DIST, 'partners', 'index.html'), 'utf8');
    check('"Apply as a partner" opens the portal, not an email client',
          /href="https?:\/\/[^"]+\/partner\/apply"/.test(partners),
          'a mailto: here means applicants have to compose an email by hand');
    check('the partners page offers a sign-in link too',
          /\/partner\/login/.test(partners));
    check('no partner CTA is a mailto link',
          !/mailto:[^"]*partner/i.test(partners));

    // Every page: the company address must be the real one.
    const pages = ['index.html', 'partners/index.html', 'privacy/index.html',
                   'terms/index.html', 'how-it-works/index.html'];
    const wrong = pages.filter((page) => {
      const file = path.join(DIST, page);
      if (!fs.existsSync(file)) return false;
      const html = fs.readFileSync(file, 'utf8');
      return /hello@haulchime\.com|no-reply@example|owner@example/.test(html);
    });
    check('no page still shows an old or placeholder email address',
          wrong.length === 0, 'stale email on: ' + wrong.join(', '));
  }

  console.log(`\n${passes} passed, ${failures} failed\n`);
  process.exit(failures ? 1 : 0);
})().catch((error) => {
  console.error('\nTest run crashed:', error);
  process.exit(1);
});
