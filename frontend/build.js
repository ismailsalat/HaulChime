#!/usr/bin/env node
/** HaulChime static site builder. Run: node build.js */
const fs = require('fs');
const path = require('path');

let local = {};
try { local = require('./site.local.json'); } catch (_) {}

const SITE = {
  name: process.env.SITE_NAME || local.name || 'HaulChime',
  domain: process.env.SITE_URL || local.domain || 'http://localhost:8080',
  apiUrl: process.env.API_URL || local.apiUrl || 'http://localhost:5002',
  phoneDisplay: process.env.PUBLIC_PHONE_DISPLAY || local.phoneDisplay || '',
  phoneHref: process.env.PUBLIC_PHONE_HREF || local.phoneHref || '',
  email: process.env.PUBLIC_EMAIL || local.email || 'hello@haulchime.com',
  region: process.env.PUBLIC_REGION || local.region || 'South King County, WA',
};

const OUT = path.join(__dirname, 'dist');
const STATIC = path.join(__dirname, 'static');
const BUILD_ID = Date.now().toString(36);

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function copyDir(src, dest) {
  if (!fs.existsSync(src)) return;
  fs.mkdirSync(dest, {recursive:true});
  for (const entry of fs.readdirSync(src, {withFileTypes:true})) {
    const s = path.join(src, entry.name), d = path.join(dest, entry.name);
    entry.isDirectory() ? copyDir(s,d) : fs.copyFileSync(s,d);
  }
}

function logo() {
  return `<a class="brand" href="/" aria-label="HaulChime home">
    <span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 48 48"><path d="M7 12h23v20H7z"/><path d="M30 20h7l5 7v5H30z"/><circle cx="16" cy="35" r="4"/><circle cx="35" cy="35" r="4"/><path d="M11 8h19"/></svg></span>
    <span>Haul<span>Chime</span></span>
  </a>`;
}

function header() {
  const call = SITE.phoneDisplay ? `<a class="nav-phone" href="${esc(SITE.phoneHref || '#')}">${esc(SITE.phoneDisplay)}</a>` : '';
  return `<header class="site-header"><div class="shell nav-wrap">
    ${logo()}
    <button class="menu-button" type="button" aria-expanded="false" aria-controls="site-nav">Menu</button>
    <nav id="site-nav" class="site-nav" aria-label="Main navigation">
      <a href="/#services">Services</a><a href="/how-it-works/">How it works</a><a href="/partners/">For partners</a>${call}
      <a class="button button-sm" href="/quote/">Get a free quote</a>
    </nav>
  </div></header>`;
}

function footer() {
  return `<footer class="site-footer"><div class="shell footer-grid">
    <div>${logo()}<p>One detailed request. Local moving, junk-removal and hauling professionals can follow up with quotes.</p></div>
    <div><h3>Explore</h3><a href="/quote/">Request a quote</a><a href="/how-it-works/">How it works</a><a href="/partners/">For partners</a></div>
    <div><h3>Company</h3><a href="/privacy/">Privacy</a><a href="/terms/">Terms</a><a href="mailto:${esc(SITE.email)}">${esc(SITE.email)}</a></div>
  </div><div class="shell footer-bottom"><span>© ${new Date().getFullYear()} HaulChime</span><span>HaulChime is a lead-generation and referral service, not a moving or hauling company.</span></div></footer>`;
}

function layout({title, description, body, pathName='/' , noindex=false}) {
  const canonical = SITE.domain.replace(/\/$/,'') + pathName;
  return `<!doctype html><html lang="en"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${esc(title)}</title><meta name="description" content="${esc(description)}">
  ${noindex ? '<meta name="robots" content="noindex,nofollow">' : ''}
  <link rel="canonical" href="${esc(canonical)}"><link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/css/styles.css?v=${BUILD_ID}">
  <script>window.HAULCHIME_API=${JSON.stringify(SITE.apiUrl)};</script>
  <script src="/js/main.js?v=${BUILD_ID}" defer></script>
  </head><body>${header()}<main>${body}</main>${footer()}</body></html>`;
}

function serviceCard(icon, title, text, bullets, slug) {
  return `<article class="service-card"><div class="service-icon">${icon}</div><h3>${title}</h3><p>${text}</p><ul>${bullets.map(x=>`<li>${x}</li>`).join('')}</ul><a href="/quote/?service=${slug}">Start request <span>→</span></a></article>`;
}

const home = layout({
  title:'HaulChime | Moving, Junk Removal & Hauling Quotes',
  description:'Tell us what needs moving or hauling, upload photos, and request quotes from local service professionals.',
  body:`
  <section class="hero"><div class="shell hero-grid"><div class="hero-copy">
    <div class="eyebrow"><span></span> Moving, junk removal and hauling</div>
    <h1>Clear the space.<br><em>Move what matters.</em></h1>
    <p class="hero-lede">Describe the job once, add addresses and photos, and get your request in front of local professionals equipped for the work.</p>
    <div class="hero-actions"><a class="button button-lg" href="/quote/">Build my request</a><a class="text-link" href="/how-it-works/">See how it works <span>→</span></a></div>
    <div class="trust-row"><div><strong>Phone verified</strong><span>Less fake contact info</span></div><div><strong>Photos supported</strong><span>Clearer job details</span></div><div><strong>ZIP matched</strong><span>Only relevant providers</span></div></div>
  </div><div class="hero-visual" aria-label="HaulChime job request preview">
    <div class="visual-orbit orbit-a"></div><div class="visual-orbit orbit-b"></div>
    <div class="job-ticket ticket-main"><div class="ticket-top"><span class="ticket-badge premium">Request ready</span><span>#HC-1048</span></div><h3>2-bedroom local move</h3><div class="route"><span>98030</span><i></i><span>98055</span></div><div class="ticket-grid"><div><small>Move date</small><b>Saturday</b></div><div><small>Access</small><b>1 flight</b></div><div><small>Photos</small><b>8 uploaded</b></div><div><small>Details</small><b>Complete</b></div></div></div>
    <div class="mini-ticket mini-one"><span>Junk removal</span><b>Half-truck load</b></div><div class="mini-ticket mini-two"><span>Verified</span><b>Text confirmation ✓</b></div>
  </div></div></section>

  <section class="logo-strip"><div class="shell"><span>Built for real job details</span><div>Pickup address</div><div>Destination</div><div>Photos</div><div>Access notes</div><div>Preferred date</div></div></section>

  <section id="services" class="section"><div class="shell"><div class="section-heading"><div><span class="kicker">Choose a service</span><h2>One platform for the jobs that need a truck.</h2></div><p>Each service has its own questionnaire, so providers receive the details they need before contacting you.</p></div>
  <div class="service-grid">
  ${serviceCard('↗','Local moving','For apartments, homes, offices and labor-only moves.',['Pickup and destination','Bedroom or inventory size','Stairs, elevators and parking'],'local_move')}
  ${serviceCard('▦','Junk removal','For furniture, appliances, cleanouts, yard debris and mixed loads.',['Load size','Heavy-item notes','Photos of the load'],'junk_removal')}
  ${serviceCard('⇢','Hauling','For pickup, delivery, dump runs and material transport.',['Pickup and drop-off','Material or item type','Loading help needed'],'hauling')}
  </div></div></section>

  <section class="dark-section"><div class="shell dark-grid"><div><span class="kicker light">Simple and direct</span><h2>You provide the details. The provider gives the quote.</h2><p>HaulChime does not estimate the cost of the job. A matched service partner reviews your request, contacts you, and discusses pricing and scheduling directly with you.</p><a class="button button-light" href="/quote/">Create a request</a></div>
  <div class="detail-stack"><div><span>01</span><div><b>One short form</b><small>Only the most important information is required.</small></div></div><div><span>02</span><div><b>Verified phone</b><small>A one-time text code confirms your contact number.</small></div></div><div><span>03</span><div><b>Direct conversation</b><small>The partner discusses the price and job details with you.</small></div></div></div></div></section>

  <section class="section process-section"><div class="shell"><div class="section-heading"><div><span class="kicker">The process</span><h2>Detailed enough to quote. Simple enough to finish.</h2></div></div><div class="process-grid">
  <article><span>1</span><h3>Choose the job</h3><p>Select moving, junk removal or hauling. The questions adapt automatically.</p></article>
  <article><span>2</span><h3>Add the details</h3><p>Provide the addresses and a short item list. Extra notes and photos are optional.</p></article>
  <article><span>3</span><h3>Verify your phone</h3><p>Text verification helps confirm that the request is genuine.</p></article>
  <article><span>4</span><h3>Get contacted</h3><p>A matched local provider can contact you to discuss the job, price and availability.</p></article>
  </div></div></section>

  <section class="cta-band"><div class="shell"><div><span>Ready when you are</span><h2>Send the details once. Hear from a local provider.</h2></div><a class="button button-dark" href="/quote/">Get a free quote</a></div></section>`
});

function quoteBody(){
return `<section class="quote-hero"><div class="shell quote-hero-grid"><div><span class="kicker">Free request · No obligation</span><h1>Tell us what needs to move.</h1><p>Answer a few simple questions and verify your phone. A service partner can then contact you directly to discuss the job and provide a quote.</p><div class="quote-points"><span>✓ Only key details required</span><span>✓ Photos are optional</span><span>✓ Usually 2–3 minutes</span></div></div><div class="quote-card" id="quote-app" data-quote-app><noscript>Please enable JavaScript to use the quote form.</noscript></div></div></section>`;
}

const quote = layout({title:'Request a Moving or Hauling Quote | HaulChime',description:'Submit a detailed moving, junk removal or hauling request with addresses, timing and photos.',pathName:'/quote/',body:quoteBody(),noindex:true});

const how = layout({title:'How HaulChime Works',description:'Learn how HaulChime verifies and routes moving, junk-removal and hauling requests.',pathName:'/how-it-works/',body:`<section class="page-hero"><div class="shell narrow"><span class="kicker">How it works</span><h1>A simple request. A direct conversation.</h1><p>HaulChime gathers the important job details, verifies your phone number, and routes the request to a service partner that covers your area.</p></div></section><section class="section"><div class="shell steps-long"><article><span>01</span><div><h2>Choose the service</h2><p>Select moving, junk removal or hauling. The form only shows questions that fit your request.</p></div></article><article><span>02</span><div><h2>Add the important details</h2><p>Provide the pickup address, destination for a move, job size and a short item list. Dates, access notes and photos are optional.</p></div></article><article><span>03</span><div><h2>Verify your mobile number</h2><p>We text a one-time code before submission so partners receive a real, reachable contact.</p></div></article><article><span>04</span><div><h2>A partner contacts you</h2><p>A matched provider reviews the request and contacts you directly. You and the provider agree on pricing, scheduling and service terms together.</p></div></article></div></section><section class="cta-band"><div class="shell"><div><span>Start now</span><h2>Send your request in a few minutes.</h2></div><a class="button button-dark" href="/quote/">Request a quote</a></div></section>`});

const partners = layout({title:'HaulChime for Moving and Hauling Partners',description:'Learn how HaulChime supplies verified moving, junk-removal and hauling opportunities.',pathName:'/partners/',body:`<section class="partner-hero"><div class="shell partner-grid"><div><span class="kicker light">For service partners</span><h1>Receive structured local opportunities without giving up a percentage of the job.</h1><p>HaulChime is designed for movers, junk-removal crews and independent haulers who want verified contact details, addresses and a clear description of the requested work.</p><a class="button button-light" href="mailto:${esc(SITE.email)}?subject=HaulChime%20partner%20interest">Apply as a partner</a></div><div class="partner-panel"><div><small>Lead access</small><strong>Prepaid</strong></div><div><small>Territory control</small><strong>ZIP based</strong></div><div><small>Customer contact</small><strong>Verified</strong></div><div><small>Job agreement</small><strong>Direct with customer</strong></div></div></div></section><section class="section"><div class="shell"><div class="section-heading"><div><span class="kicker">Structured leads</span><h2>Know the request before you follow up.</h2></div><p>Customers provide the key job information and verify their mobile number. HaulChime does not quote the work or take a percentage of the completed job.</p></div><div class="partner-feature-grid"><article><span>01</span><h3>Service and location</h3><p>See the requested category and receive opportunities only in approved ZIP codes.</p></article><article><span>02</span><h3>Useful job details</h3><p>Review addresses, item lists, timing, access notes and optional photos.</p></article><article><span>03</span><h3>Direct customer contact</h3><p>You discuss the price, schedule and final scope directly with the customer.</p></article></div></div></section><section class="section alt"><div class="shell split-list"><div><span class="kicker">Eligibility rules</span><h2>You control what you can receive.</h2></div><ul><li>Approved ZIP codes and maximum travel area</li><li>Moving, junk removal or hauling categories</li><li>Truck size, crew size and heavy-item capability</li><li>Daily opportunity limits and account balance</li><li>One-time purchases or prepaid account credit</li></ul></div></section>`});

const privacy = layout({title:'Privacy Policy | HaulChime',description:'How HaulChime collects and shares quote-request information.',pathName:'/privacy/',body:`<section class="page-hero"><div class="shell narrow"><span class="kicker">Privacy</span><h1>Privacy policy</h1><p>Last updated August 3, 2026</p></div></section><section class="legal"><div class="shell narrow"><h2>Information we collect</h2><p>We collect information you submit, including your name, phone number, email, pickup address, destination address when relevant, service date, job details, access information and uploaded photos. We may also collect referral and advertising parameters.</p><h2>How we use it</h2><p>We use the information to validate and route your request, prevent duplicate or fraudulent submissions, communicate about your request, and improve the service.</p><h2>Sharing</h2><p>Your request may be shared with independent moving, junk-removal or hauling providers that appear able to serve the requested ZIP code and job type. These providers are not HaulChime employees.</p><h2>Text messages</h2><p>We may send a one-time verification code and request-related messages. Submission does not enroll you in unrelated marketing messages.</p><h2>Photos and addresses</h2><p>Addresses and photos may contain sensitive information. Production deployments should use private object storage, access controls and a defined deletion policy.</p><h2>Contact</h2><p>Questions can be sent to <a href="mailto:${esc(SITE.email)}">${esc(SITE.email)}</a>.</p></div></section>`});

const terms = layout({title:'Terms of Use | HaulChime',description:'Terms for using HaulChime’s lead-generation and referral service.',pathName:'/terms/',body:`<section class="page-hero"><div class="shell narrow"><span class="kicker">Terms</span><h1>Terms of use</h1><p>Last updated August 3, 2026</p></div></section><section class="legal"><div class="shell narrow"><h2>Referral service only</h2><p>HaulChime is a lead-generation and referral service. It does not perform moving, junk-removal or hauling work and does not employ the service providers who may contact you.</p><h2>No guaranteed match or price</h2><p>Submitting a request does not guarantee that a provider will respond, accept the work or offer a particular price. Final pricing, scheduling, licensing, insurance and service terms are between you and the provider.</p><h2>Accurate submissions</h2><p>You agree to provide accurate information and upload only content you are authorized to share. Do not submit unlawful, hazardous or deceptive requests.</p><h2>Provider leads</h2><p>Providers purchase access to customer opportunities, not guaranteed booked jobs. Credit or replacement eligibility is governed by the applicable partner agreement.</p><h2>Legal review</h2><p>This starter policy is not legal advice. Have the terms, privacy policy, consent language and provider agreement reviewed before launching publicly.</p></div></section>`});

const thankYou = layout({title:'Thank you — request received | HaulChime',description:'Your HaulChime request was received and a local partner will contact you shortly.',pathName:'/thank-you/',noindex:true,body:`<section class="thank-you"><div class="shell narrow">
  <div class="success-mark">\u2713</div>
  <span class="kicker">Request received</span>
  <h1>Thank you!</h1>
  <p class="thanks-lede">Your request is in, and <strong>one of our local service partners will contact you shortly</strong> to talk through the job and give you a quote.</p>
  <div class="thanks-timeline">
    <div class="done"><span>\u2713</span><div><b>Request received</b><small>Just now</small></div></div>
    <div class="active"><span>2</span><div><b>A local partner picks it up</b><small>Usually within a few hours</small></div></div>
    <div><span>3</span><div><b>They contact you directly</b><small>Most customers hear back the same day</small></div></div>
  </div>
  <div class="receipt" data-receipt><div><span>Reference number</span><strong id="reference">Check your confirmation</strong></div></div>
  <p class="thanks-note">Nothing is owed and nothing is booked yet. The partner confirms the details, the schedule and the price with you directly \u2014 HaulChime never sets the price.</p>
  <div class="thanks-actions"><a class="button" href="/">Back to HaulChime</a><a class="button button-outline" href="/how-it-works/">What happens next</a></div>
</div></section><script>const r=new URLSearchParams(location.search).get('ref');const el=document.getElementById('reference');if(r&&el)el.textContent=r;</script>`});

const notFound = layout({title:'Page not found | HaulChime',description:'The requested page could not be found.',pathName:'/404.html',noindex:true,body:`<section class="thank-you"><div class="shell narrow"><span class="kicker">404</span><h1>That page moved.</h1><p>Return home or start a new quote request.</p><a class="button" href="/">Home</a> <a class="button button-outline" href="/quote/">Get a quote</a></div></section>`});

function write(rel, html){const p=path.join(OUT,rel);fs.mkdirSync(path.dirname(p),{recursive:true});fs.writeFileSync(p,html);}
fs.rmSync(OUT,{recursive:true,force:true});fs.mkdirSync(OUT,{recursive:true});copyDir(STATIC,OUT);
write('index.html',home);write('quote/index.html',quote);write('how-it-works/index.html',how);write('partners/index.html',partners);write('privacy/index.html',privacy);write('terms/index.html',terms);write('thank-you/index.html',thankYou);write('404.html',notFound);
write('robots.txt',`User-agent: *\nAllow: /\nDisallow: /quote/\nDisallow: /thank-you/\nSitemap: ${SITE.domain.replace(/\/$/,'')}/sitemap.xml\n`);
write('sitemap.xml',`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${['/','/how-it-works/','/partners/','/privacy/','/terms/'].map(p=>`<url><loc>${SITE.domain.replace(/\/$/,'')}${p}</loc></url>`).join('')}</urlset>`);
console.log(`Built HaulChime to ${OUT}`);
console.log(`API: ${SITE.apiUrl}`);
