/* HaulChime quote flow.

   Four short steps. A simple job answers about eight required questions; the
   detailed ones only appear when an earlier answer makes them relevant.

   Two rules kept throughout:
     1. Never re-render while someone is typing — it eats focus and caret
        position. Text inputs write straight to state; only taps re-render.
     2. Nothing is a dead end. Every question a real person might not know has
        a "Not sure" answer, and every third-party service (address lookup,
        SMS) degrades to something that still works.

   And one thing this file must never do: show the customer a price. HaulChime
   does not quote jobs. The partner inspects the work and agrees the price
   directly with the customer.
*/
(function () {
  'use strict';

  var menu = document.querySelector('.menu-button');
  var nav = document.querySelector('.site-nav');
  if (menu && nav) {
    menu.addEventListener('click', function () {
      menu.setAttribute('aria-expanded', String(nav.classList.toggle('open')));
    });
  }

  var RECEIPT_KEY = 'haulchime_receipt_v1';
  var DRAFT_KEY = 'haulchime_quote_v3';

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // The thank-you page reads its receipt from sessionStorage rather than the
  // URL, so a phone number never lands in browser history or a referrer.
  var receiptSlot = document.querySelector('[data-receipt]');
  if (receiptSlot) {
    var receipt = {};
    try { receipt = JSON.parse(sessionStorage.getItem(RECEIPT_KEY) || '{}'); }
    catch (e) { receipt = {}; }
    var reference = receipt.reference ||
      new URLSearchParams(location.search).get('ref') || '';
    var rows = [
      ['Reference number', reference],
      ['Requested service', receipt.service],
      ['Contact number', receipt.phone],
      ['They will reach you by', receipt.contact]
    ].filter(function (row) { return row[1]; });
    if (rows.length) {
      receiptSlot.innerHTML = rows.map(function (row) {
        return '<div><span>' + row[0] + '</span><strong>' + esc(row[1]) + '</strong></div>';
      }).join('');
    }
    return;
  }

  var mount = document.querySelector('[data-quote-app]');
  if (!mount) return;

  var API = window.HAULCHIME_API || 'http://localhost:5002';
  var params = new URLSearchParams(location.search);

  var config = {
    brand: 'HaulChime',
    phoneVerificationEnabled: true,
    phoneVerificationRequired: true,
    addressLookupEnabled: true,
    maxPhotos: 10,
    maxPhotoMb: 8,
    resendDelaySeconds: 60,
    consentText: 'I agree that HaulChime may share my request and contact information ' +
      'with matched local service partners who may call or text me about this job.'
  };

  // ---------------------------------------------------------------- state
  var state = {
    step: 0,
    service_pick: '', service_type: params.get('service') || '', job_type: '',
    pickup_address: '', pickup_unit: '', pickup_city: '', pickup_state: '', zip_code: '',
    pickup_confirmed: false, manual_address: false,
    destination_known: '', destination_address: '', destination_unit: '',
    destination_city: '', destination_state: '', destination_zip: '',
    destination_confirmed: false, destination_rough: '',
    timing: '', service_date: '', preferred_time: '', property_type: '',
    job_size: '', item_categories: [], extra_services: [],
    special_item_types: [], special_items_note: '',
    access_issues: [], stairs_flights: '',
    destination_same_access: true, destination_access_issues: [],
    destination_stairs_flights: '', description: '',
    full_name: '', phone: '', email: '',
    preferred_contact: '', contact_time: '', consent: false,
    photos: [], errors: {},
    otp_sent: false, otp_code: '', phone_verified: false, verified_phone: '',
    verification_attempt_id: '', otp_status: '', otp_error: '', resend_in: 0,
    busy: false, submitting: false, restored: false,
    quote_draft_id: 'qd_' + Math.random().toString(36).slice(2, 14),
    session_id: 'sess_' + Math.random().toString(36).slice(2, 14)
  };

  var resendTimer = null;
  var SKIP_SAVE = ['photos', 'errors', 'busy', 'submitting', 'restored', 'otp_code'];

  function save() {
    try {
      var copy = {};
      Object.keys(state).forEach(function (k) {
        if (SKIP_SAVE.indexOf(k) < 0) copy[k] = state[k];
      });
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify(copy));
    } catch (e) { /* private mode: no autosave, everything else still works */ }
  }

  (function restore() {
    try {
      var raw = sessionStorage.getItem(DRAFT_KEY);
      if (!raw) return;
      var saved = JSON.parse(raw);
      Object.keys(saved).forEach(function (k) { if (k in state) state[k] = saved[k]; });
      state.errors = {};
      state.busy = false;
      state.submitting = false;
      state.restored = state.step > 0 || !!state.service_pick;
    } catch (e) { /* corrupt draft: start clean rather than crash */ }
  })();
  if (params.get('service')) {
    state.service_type = params.get('service');
    state.service_pick = params.get('service');
  }

  // ---------------------------------------------------------------- catalog
  var SERVICES = [
    ['local_move', '📦', 'Moving', 'Home, apartment or office'],
    ['junk_removal', '🧹', 'Junk removal', 'Cleanouts, old furniture, debris'],
    ['hauling', '🚚', 'Hauling', 'Pickup, delivery or a dump run'],
    ['not_sure', '🤔', 'Not sure yet', "We'll help you narrow it down"]
  ];

  var JOB_TYPES = {
    moving: [
      ['full_home_move', '🏠', 'Full home move'],
      ['apartment_move', '🏢', 'Apartment move'],
      ['few_items_move', '🪑', 'Just a few items'],
      ['single_heavy_item', '🏋️', 'One heavy item'],
      ['office_move', '💼', 'Office or business'],
      ['load_unload_only', '💪', 'Loading or unloading only'],
      ['long_distance', '🗺️', 'Long-distance move', 'Another city or state'],
      ['not_sure', '🤔', 'Not sure']
    ],
    junk_removal: [
      ['one_item', '📦', 'One item'],
      ['a_few_items', '🪑', 'A few items'],
      ['room_cleanout', '🚪', 'Room cleanout'],
      ['garage_basement_cleanout', '🧰', 'Garage or basement'],
      ['full_property_cleanout', '🏚️', 'Full property cleanout'],
      ['yard_construction_debris', '🌿', 'Yard or construction debris'],
      ['not_sure', '🤔', 'Not sure']
    ],
    hauling: [
      ['pickup_delivery', '🛻', 'Pickup and delivery'],
      ['dump_run', '🗑️', 'Dump run'],
      ['furniture_appliance', '🛋️', 'Furniture or appliance'],
      ['material_transport', '🧱', 'Building materials'],
      ['equipment_hauling', '⚙️', 'Equipment hauling'],
      ['other', '➕', 'Something else'],
      ['not_sure', '🤔', 'Not sure']
    ]
  };

  var SIZES = {
    moving: [['few_items', 'A few items'], ['studio', 'Studio'], ['1br', '1 bedroom'],
      ['2br', '2 bedrooms'], ['3br', '3 bedrooms'], ['4br_plus', '4+ bedrooms'],
      ['office', 'Office or commercial'], ['not_sure', 'Not sure']],
    junk_removal: [['single_item', 'One item'], ['few_items', 'A few items'],
      ['quarter_truck', 'About ¼ truck'], ['half_truck', 'About ½ truck'],
      ['full_truck', 'A full truck'], ['multi_truck', 'More than one truck'],
      ['not_sure', 'Not sure']],
    hauling: [['single_item', 'One item'], ['few_items', 'A few items'],
      ['small_load', 'Small load'], ['medium_load', 'Medium load'],
      ['large_load', 'Large load'], ['multiple_loads', 'Multiple loads'],
      ['not_sure', 'Not sure']]
  };

  var ITEMS = {
    moving: [['boxes', 'Boxes'], ['furniture', 'Furniture'], ['appliances', 'Appliances'],
      ['mattresses', 'Mattresses'], ['electronics', 'Electronics'],
      ['office_equipment', 'Office equipment'], ['heavy_specialty', 'Heavy or specialty'],
      ['other', 'Other'], ['not_sure', 'Not sure']],
    junk_removal: [['furniture', 'Furniture'], ['mattresses', 'Mattresses'],
      ['appliances', 'Appliances'], ['electronics', 'Electronics'],
      ['household', 'Boxes or household'], ['yard_waste', 'Yard waste'],
      ['construction_debris', 'Construction debris'],
      ['garage_storage', 'Garage or storage'], ['other', 'Other'], ['not_sure', 'Not sure']],
    hauling: [['furniture', 'Furniture'], ['appliances', 'Appliances'],
      ['building_materials', 'Building materials'], ['yard_waste', 'Yard material'],
      ['equipment', 'Equipment'], ['household', 'Boxes or household'],
      ['other', 'Other'], ['not_sure', 'Not sure']]
  };

  var EXTRAS = [['packing', 'Packing'], ['disassembly', 'Take furniture apart'],
    ['reassembly', 'Put furniture back together'], ['loading_only', 'Loading only'],
    ['unloading_only', 'Unloading only'], ['blankets_protection', 'Blankets or padding'],
    ['none', 'None of these'], ['not_sure', 'Not sure']];

  var SPECIAL_ITEMS = [['piano', 'Piano'], ['safe', 'Safe'], ['pool_table', 'Pool table'],
    ['large_appliance', 'Large appliance'], ['oversized_furniture', 'Oversized furniture'],
    ['heavy_equipment', 'Heavy equipment'], ['hazardous', 'Chemicals or hazardous'],
    ['none', 'None of these'], ['not_sure', 'Not sure']];

  var ACCESS = [['stairs', 'Stairs'], ['elevator', 'Elevator'],
    ['long_walk', 'Long walk to the door'], ['narrow', 'Narrow doorway or hallway'],
    ['limited_parking', 'Limited truck parking'], ['gate_security', 'Gate or security'],
    ['none', 'No access issues'], ['not_sure', 'Not sure']];

  var FLIGHTS = [['1', '1 flight'], ['2', '2 flights'], ['3_plus', '3 or more'],
    ['not_sure', 'Not sure']];

  var TIMING = [['asap', '⚡', 'As soon as possible'], ['2_3_days', '📆', 'Within 2–3 days'],
    ['one_week', '🗓️', 'Within a week'], ['specific_date', '📅', 'Pick a date'],
    ['flexible', '🌤️', "I'm flexible"]];

  var PREFERRED_TIMES = [['morning', 'Morning'], ['afternoon', 'Afternoon'],
    ['evening', 'Evening'], ['no_preference', 'No preference']];

  var PROPERTIES = [['house', '🏡', 'House'], ['apartment', '🏢', 'Apartment or condo'],
    ['commercial', '🏬', 'Office or business'], ['storage_unit', '🔐', 'Storage unit'],
    ['construction_site', '🚧', 'Construction site'], ['other', '📍', 'Other'],
    ['not_sure', '🤔', 'Not sure']];

  var CONTACT_METHODS = [['text', '💬', 'Text me'], ['phone', '📞', 'Call me'],
    ['either', '👍', 'Either is fine']];

  var CONTACT_TIMES = [['morning', 'Morning'], ['afternoon', 'Afternoon'],
    ['evening', 'Evening'], ['anytime', 'Anytime']];

  var STEP_NAMES = ['Service', 'Where & when', 'The details', 'Contact'];

  // ---------------------------------------------------------------- helpers
  function family() {
    if (state.service_type === 'local_move' || state.service_type === 'long_distance_move') return 'moving';
    if (state.service_type === 'junk_removal') return 'junk_removal';
    if (state.service_type === 'hauling') return 'hauling';
    return '';
  }
  function isMove() { return family() === 'moving'; }

  // Junk removal never asks where things are going — the truck decides that.
  function needsDestination() {
    if (isMove()) return true;
    return state.service_type === 'hauling' &&
      ['pickup_delivery', 'material_transport', 'equipment_hauling'].indexOf(state.job_type) >= 0;
  }

  function labelFor(list, value) {
    for (var i = 0; i < list.length; i++) {
      if (list[i][0] === value) return list[i].length >= 3 ? list[i][2] : list[i][1];
    }
    return '';
  }

  function serviceLabel() {
    if (state.service_type === 'long_distance_move') return 'Long-distance move';
    return labelFor(SERVICES, state.service_type) || 'Not chosen yet';
  }

  function digitsOf(value) { return String(value || '').replace(/\D/g, ''); }
  function todayISO() { return new Date().toISOString().slice(0, 10); }

  function question(id, title, opts, body) {
    opts = opts || {};
    var tag = opts.optional ? '<span class="tag tag-opt">Optional</span>'
      : '<span class="tag tag-req">Required</span>';
    return '<section class="q' + (opts.reveal ? ' q-reveal' : '') + '" id="q-' + id + '">' +
      '<div class="q-head"><h3 class="q-title">' + title + '</h3>' + tag +
      (opts.sub ? '<p class="q-sub">' + opts.sub + '</p>' : '') + '</div>' +
      body +
      (state.errors[id] ? '<p class="q-error">' + esc(state.errors[id]) + '</p>' : '') +
      '</section>';
  }

  function cards(key, options, opts) {
    opts = opts || {};
    return '<div class="opt-grid ' + (opts.cls || '') + '">' + options.map(function (o) {
      var value = o[0];
      var hasEmoji = o.length >= 3;
      var emoji = hasEmoji ? o[1] : '';
      var label = hasEmoji ? o[2] : o[1];
      var sub = hasEmoji ? (o[3] || '') : (o[2] || '');
      var on = state[key] === value;
      return '<button type="button" class="opt' + (on ? ' on' : '') + '" data-pick="' +
        key + '" data-value="' + esc(value) + '" aria-pressed="' + on + '">' +
        (emoji ? '<span class="opt-emoji" aria-hidden="true">' + emoji + '</span>' : '') +
        '<span class="opt-text"><b>' + esc(label) + '</b>' +
        (sub ? '<small>' + esc(sub) + '</small>' : '') + '</span>' +
        (on ? '<span class="opt-check" aria-hidden="true">✓</span>' : '') + '</button>';
    }).join('') + '</div>';
  }

  function multi(key, options, opts) {
    opts = opts || {};
    var chosen = state[key] || [];
    return '<div class="opt-grid ' + (opts.cls || 'tight') + '">' + options.map(function (o) {
      var value = o[0];
      var label = o.length >= 3 ? o[2] : o[1];
      var on = chosen.indexOf(value) >= 0;
      return '<button type="button" class="opt' + (on ? ' on' : '') + '" data-toggle="' +
        key + '" data-value="' + esc(value) + '" aria-pressed="' + on + '">' +
        '<span class="opt-box" aria-hidden="true">' + (on ? '✓' : '') + '</span>' +
        '<span class="opt-text"><b>' + esc(label) + '</b></span></button>';
    }).join('') + '</div>';
  }

  function inputField(key, label, opts) {
    opts = opts || {};
    var tag = opts.optional ? '<span class="tag tag-opt">Optional</span>'
      : '<span class="tag tag-req">Required</span>';
    return '<div class="field"><label for="f-' + key + '"><span>' + esc(label) + '</span>' +
      tag + '</label><input id="f-' + key + '" data-key="' + key + '" type="' +
      (opts.type || 'text') + '" value="' + esc(state[key] || '') + '" placeholder="' +
      esc(opts.placeholder || '') + '"' +
      (opts.inputmode ? ' inputmode="' + opts.inputmode + '"' : '') +
      (opts.autocomplete ? ' autocomplete="' + opts.autocomplete + '"' : '') +
      (opts.maxlength ? ' maxlength="' + opts.maxlength + '"' : '') + '>' +
      (opts.hint ? '<p class="hint">' + opts.hint + '</p>' : '') + '</div>';
  }

  // ---------------------------------------------------------------- steps
  function stepService() {
    var out = '<div class="step-intro"><span class="step-kicker">Step 1 · Service</span>' +
      '<h2>What can we haul for you?</h2>' +
      '<p class="sub">Tap one. Everything after this adapts to your answer.</p></div>';

    out += question('service', 'What do you need help with?', {},
      cards('service_pick', SERVICES));

    // "Not sure" is never a dead end — one friendly follow-up gets us to a
    // real service so the request reaches partners who do that work.
    if (state.service_pick === 'not_sure') {
      out += question('service_closest', 'No problem — which is closest?',
        { reveal: true, sub: 'A rough guess is fine. The partner will confirm on the call.' },
        cards('service_type', [
          ['local_move', '📦', 'Something needs moving'],
          ['junk_removal', '🧹', 'Something needs taking away'],
          ['hauling', '🚚', 'Something needs a truck ride']
        ]));
    }

    if (family()) {
      out += question('job_type', 'What best describes the job?', { reveal: true },
        cards('job_type', JOB_TYPES[family()]));
    }
    return out;
  }

  function stepLocation() {
    var out = '<div class="step-intro"><span class="step-kicker">Step 2 · Where & when</span>' +
      '<h2>Where is it happening?</h2>' +
      '<p class="sub">Start typing an address and we\'ll fill in the rest.</p></div>';

    out += question('pickup', isMove() ? 'Where should the move start?' : 'Where is the job?',
      { sub: 'We use your address to match you with an available local partner.' },
      addressField('pickup'));

    if (needsDestination()) {
      out += question('destination_known', 'Where is it going?', { reveal: true },
        cards('destination_known', [
          ['yes', '📍', 'I know the address'],
          ['no', '🤷', "I don't know it yet"]
        ], { cls: 'cols-2' }));

      if (state.destination_known === 'yes') {
        out += question('destination', 'Drop-off address', { reveal: true },
          addressField('destination'));
      } else if (state.destination_known === 'no') {
        out += question('destination_rough', 'Which city or ZIP, roughly?',
          { reveal: true, sub: 'Close enough is fine — it just tells us how far the trip is.' },
          inputField('destination_rough', 'Destination city or ZIP',
            { placeholder: 'Renton, or 98055', autocomplete: 'address-level2' }));
      }
    }

    out += question('timing', 'When do you need help?', {}, cards('timing', TIMING));

    if (state.timing === 'specific_date') {
      out += question('service_date', 'Which day?', { reveal: true },
        '<div class="field"><label for="f-service_date"><span>Preferred date</span>' +
        '<span class="tag tag-req">Required</span></label>' +
        '<input id="f-service_date" data-key="service_date" type="date" min="' +
        todayISO() + '" value="' + esc(state.service_date) + '"></div>');
    }

    if (state.timing) {
      out += question('preferred_time', 'Any preferred time of day?',
        { optional: true, reveal: true },
        cards('preferred_time', PREFERRED_TIMES, { cls: 'tight' }));
    }

    out += question('property_type', 'What type of property is this?', {},
      cards('property_type', PROPERTIES));
    return out;
  }

  function stepDetails() {
    var fam = family() || 'hauling';
    var sizeTitle = fam === 'moving' ? 'How big is the move?'
      : fam === 'junk_removal' ? 'About how much needs to go?' : 'How much are you hauling?';

    var out = '<div class="step-intro"><span class="step-kicker">Step 3 · The details</span>' +
      '<h2>Tell us about the load.</h2>' +
      '<p class="sub">Rough answers are perfectly fine. "Not sure" is a real answer here.</p></div>';

    out += question('job_size', sizeTitle,
      { sub: fam === 'junk_removal' ? 'No cubic-yard maths required.' : '' },
      cards('job_size', SIZES[fam], { cls: 'tight' }));

    out += question('item_categories',
      fam === 'moving' ? 'What are you moving?'
        : fam === 'junk_removal' ? 'What needs to be removed?' : 'What are you hauling?',
      { sub: 'Pick as many as you like.' }, multi('item_categories', ITEMS[fam]));

    if (fam === 'moving') {
      out += question('extra_services', 'Need any extra help?',
        { optional: true, sub: 'Pick as many as you like.' }, multi('extra_services', EXTRAS));
    }

    out += question('special_item_types', 'Anything heavy or special?',
      { sub: 'This stops us sending a crew without the right equipment.' },
      multi('special_item_types', SPECIAL_ITEMS));

    var realSpecials = (state.special_item_types || []).filter(function (s) {
      return s !== 'none' && s !== 'not_sure';
    });
    if (realSpecials.length) {
      out += question('special_items_note', 'Tell us what the item is',
        { optional: true, reveal: true },
        inputField('special_items_note', 'Quick description',
          { optional: true, maxlength: 200,
            placeholder: 'Upright piano, gun safe, chest freezer…' }));
    }

    out += question('access_issues', isMove() ? 'Any access issues at the pickup?' : 'Any access issues?',
      { sub: 'Pick as many as you like.' }, multi('access_issues', ACCESS));

    if ((state.access_issues || []).indexOf('stairs') >= 0) {
      out += question('stairs_flights', 'How many flights of stairs?', { reveal: true },
        cards('stairs_flights', FLIGHTS, { cls: 'tight' }));
    }

    if (isMove()) {
      out += question('destination_same_access', 'And at the destination?', { reveal: true },
        cards('destination_same_access_choice', [
          ['same', '↔️', 'Same as pickup', 'Copy the answers above'],
          ['different', '🔀', "It's different", 'Let me pick separately']
        ], { cls: 'cols-2' }));

      if (!state.destination_same_access) {
        out += question('destination_access_issues', 'Access at the destination',
          { reveal: true, sub: 'Pick as many as you like.' },
          multi('destination_access_issues', ACCESS));
        if ((state.destination_access_issues || []).indexOf('stairs') >= 0) {
          out += question('destination_stairs_flights', 'How many flights there?',
            { reveal: true }, cards('destination_stairs_flights', FLIGHTS, { cls: 'tight' }));
        }
      }
    }

    out += question('photos', 'Add a few photos', { optional: true },
      '<div class="photo-drop"><p class="hint">Photos help partners understand the job and ' +
      'come back with a better-informed quote. Up to ' + config.maxPhotos + ' images, ' +
      config.maxPhotoMb + ' MB each.</p><div class="photo-buttons">' +
      '<button type="button" class="button button-outline" id="photo-take">📷 Take a photo</button>' +
      '<button type="button" class="button button-outline" id="photo-choose">🖼️ Choose photos</button>' +
      '</div><input id="photo-input" type="file" multiple accept="image/jpeg,image/png,image/webp" hidden>' +
      '<input id="photo-camera" type="file" accept="image/*" capture="environment" hidden>' +
      photoPreview() + '</div>');

    out += question('description', 'Anything else the partner should know?', { optional: true },
      '<div class="field"><label for="f-description"><span>Extra notes</span>' +
      '<span class="tag tag-opt">Optional</span></label>' +
      '<textarea id="f-description" data-key="description" maxlength="1000" ' +
      'placeholder="The couch is upstairs, parking is behind the building, I need help ' +
      'taking a bed apart…">' + esc(state.description) + '</textarea>' +
      '<p class="char-count" id="desc-count">' + (state.description || '').length +
      ' / 1000</p></div>');
    return out;
  }

  function stepContact() {
    var out = '<div class="step-intro"><span class="step-kicker">Step 4 · Contact</span>' +
      '<h2>Last bit — how do we reach you?</h2>' +
      '<p class="sub">Verify your number and your request is on its way. ' +
      'Free to submit, and no payment is ever taken here.</p></div>';

    out += '<div class="recap">' +
      '<div><span>Service</span><strong>' + esc(serviceLabel()) + '</strong></div>' +
      '<div><span>Pickup</span><strong>' + esc(state.zip_code || '—') + '</strong></div>' +
      (needsDestination() ? '<div><span>Drop-off</span><strong>' +
        esc(state.destination_zip || state.destination_rough || '—') + '</strong></div>' : '') +
      '</div>';

    out += question('full_name', 'What is your name?', {},
      inputField('full_name', 'Full name', { placeholder: 'Alex Johnson', autocomplete: 'name' }));

    out += question('phone', 'What number should the partner use?',
      { sub: "We'll text a 6-digit code to make sure it's really you." }, verifyBlock());

    out += question('preferred_contact', 'How should they get in touch?', {},
      cards('preferred_contact', CONTACT_METHODS, { cls: 'cols-2' }));

    out += question('email', 'Your email', { optional: true },
      inputField('email', 'Email address', {
        type: 'email', optional: true, placeholder: 'you@example.com',
        autocomplete: 'email', inputmode: 'email',
        hint: 'We can send your request confirmation here.'
      }));

    out += question('contact_time', 'Best time to reach you?', { optional: true },
      cards('contact_time', CONTACT_TIMES, { cls: 'tight' }));

    out += question('consent', 'One last tick', {},
      '<label class="check-row' + (state.consent ? ' on' : '') + '">' +
      '<input id="consent" type="checkbox"' + (state.consent ? ' checked' : '') + '>' +
      '<span>' + esc(config.consentText) + '</span></label>' +
      // Honeypot: invisible to people, irresistible to bots.
      '<input type="text" id="company-website" tabindex="-1" autocomplete="off" ' +
      'aria-hidden="true" style="position:absolute;left:-9999px;width:1px;height:1px;opacity:0">');
    return out;
  }

  // ---------------------------------------------------------------- address
  function addressKeys(which) {
    return which === 'pickup'
      ? { street: 'pickup_address', unit: 'pickup_unit', city: 'pickup_city',
          state: 'pickup_state', zip: 'zip_code', confirmed: 'pickup_confirmed' }
      : { street: 'destination_address', unit: 'destination_unit', city: 'destination_city',
          state: 'destination_state', zip: 'destination_zip', confirmed: 'destination_confirmed' };
  }

  function addressField(which) {
    var k = addressKeys(which);

    if (state[k.confirmed]) {
      var full = [state[k.street], state[k.city],
        (state[k.state] + ' ' + state[k.zip]).trim()].filter(Boolean).join(', ');
      return '<div class="addr-confirmed"><span aria-hidden="true">✓</span>' +
        '<span>' + esc(full) + '</span>' +
        '<button type="button" data-addr-change="' + which + '">Change</button></div>' +
        '<div class="field" style="margin-top:14px">' +
        '<label for="f-' + k.unit + '"><span>Apartment, suite or unit</span>' +
        '<span class="tag tag-opt">Optional</span></label>' +
        '<input id="f-' + k.unit + '" data-key="' + k.unit + '" type="text" maxlength="30" ' +
        'value="' + esc(state[k.unit]) + '" placeholder="Apt 4B" autocomplete="address-line2"></div>';
    }

    // No lookup available, or the customer asked to type it out. New builds
    // and rural addresses often aren't in the database yet.
    if (state.manual_address || !config.addressLookupEnabled) {
      return '<div class="field-grid">' +
        '<div class="field" style="grid-column:1/-1">' +
        '<label for="f-' + k.street + '"><span>Street address</span>' +
        '<span class="tag tag-req">Required</span></label>' +
        '<input id="f-' + k.street + '" data-key="' + k.street + '" type="text" value="' +
        esc(state[k.street]) + '" placeholder="123 Main St" autocomplete="address-line1"></div>' +
        '<div class="field"><label for="f-' + k.city + '"><span>City</span>' +
        '<span class="tag tag-req">Required</span></label>' +
        '<input id="f-' + k.city + '" data-key="' + k.city + '" type="text" value="' +
        esc(state[k.city]) + '" placeholder="Kent" autocomplete="address-level2"></div>' +
        '<div class="field"><label for="f-' + k.zip + '"><span>ZIP code</span>' +
        '<span class="tag tag-req">Required</span></label>' +
        '<input id="f-' + k.zip + '" data-key="' + k.zip + '" type="text" inputmode="numeric" ' +
        'maxlength="5" value="' + esc(state[k.zip]) + '" placeholder="98030" ' +
        'autocomplete="postal-code"></div>' +
        '<div class="field"><label for="f-' + k.unit + '"><span>Unit</span>' +
        '<span class="tag tag-opt">Optional</span></label>' +
        '<input id="f-' + k.unit + '" data-key="' + k.unit + '" type="text" maxlength="30" ' +
        'value="' + esc(state[k.unit]) + '" placeholder="Apt 4B"></div></div>';
    }

    return '<div class="field addr" data-addr="' + which + '">' +
      '<label for="f-addr-' + which + '"><span>Street address</span>' +
      '<span class="tag tag-req">Required</span></label>' +
      '<input id="f-addr-' + which + '" type="text" autocomplete="off" role="combobox" ' +
      'aria-expanded="false" aria-autocomplete="list" aria-controls="menu-' + which + '" ' +
      'value="' + esc(state[k.street]) + '" placeholder="Start typing: 123 Main…">' +
      '<ul class="addr-menu" id="menu-' + which + '" role="listbox" hidden></ul>' +
      '<button type="button" class="addr-manual" data-addr-manual="1">' +
      "Can't find it? Type it in manually</button></div>";
  }

  function bindAddress(which) {
    var wrap = mount.querySelector('[data-addr="' + which + '"]');
    if (!wrap) return;
    var input = wrap.querySelector('input');
    var list = wrap.querySelector('.addr-menu');
    var k = addressKeys(which);
    var timer = null;
    var results = [];
    var cursor = -1;

    function close() {
      list.hidden = true;
      list.innerHTML = '';
      input.setAttribute('aria-expanded', 'false');
      cursor = -1;
    }

    function paint(items, note) {
      results = items || [];
      if (!results.length && !note) { close(); return; }
      list.innerHTML = results.length
        ? results.map(function (item, i) {
            return '<li role="option"><button type="button" class="addr-opt" data-i="' + i +
              '"><b>' + esc(item.label) + '</b><small>' + esc(item.sublabel) +
              '</small></button></li>';
          }).join('')
        : '<li class="addr-note">' + esc(note) + '</li>';
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    }

    function lookup(text, selected) {
      fetch(API + '/api/address/suggest?q=' + encodeURIComponent(text) +
        (selected ? '&selected=' + encodeURIComponent(selected) : ''))
        .then(function (r) { return r.json(); })
        .then(function (body) {
          if (!body.available) {
            // Lookup is off or unhappy: hand over a plain form rather than
            // blocking someone on a service they never asked for.
            config.addressLookupEnabled = false;
            state.manual_address = true;
            render();
            return;
          }
          if (document.activeElement !== input) return;
          paint(body.suggestions, 'No matches yet — keep typing.');
        })
        .catch(close);
    }

    input.addEventListener('input', function () {
      state[k.street] = input.value;
      save();
      clearTimeout(timer);
      if (input.value.trim().length < 3) { close(); return; }
      // Debounced: one request per pause, not one per keystroke.
      timer = setTimeout(function () { lookup(input.value.trim(), ''); }, 220);
    });

    input.addEventListener('keydown', function (event) {
      if (list.hidden) return;
      var options = list.querySelectorAll('.addr-opt');
      if (!options.length) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        cursor += event.key === 'ArrowDown' ? 1 : -1;
        if (cursor < 0) cursor = options.length - 1;
        if (cursor >= options.length) cursor = 0;
        Array.prototype.forEach.call(options, function (o, i) {
          o.classList.toggle('active', i === cursor);
        });
        options[cursor].scrollIntoView({ block: 'nearest' });
      } else if (event.key === 'Enter' && cursor >= 0) {
        event.preventDefault();
        options[cursor].click();
      } else if (event.key === 'Escape') {
        close();
      }
    });

    list.addEventListener('click', function (event) {
      var button = event.target.closest('.addr-opt');
      if (!button) return;
      var item = results[Number(button.dataset.i)];
      if (!item) return;
      if (item.needs_unit) {
        // A building with many units: ask for its unit list rather than
        // accepting an address that arrives without an apartment number.
        input.value = item.street + ' ';
        input.focus();
        lookup(item.street, item.selected);
        return;
      }
      state[k.street] = item.street;
      state[k.city] = item.city;
      state[k.state] = item.state;
      state[k.zip] = item.zip;
      if (item.secondary) state[k.unit] = item.secondary;
      state[k.confirmed] = true;
      delete state.errors[which];
      close();
      render();
    });

    document.addEventListener('click', function (event) {
      if (!wrap.contains(event.target)) close();
    });
  }

  // ---------------------------------------------------------------- verify
  function verifyBlock() {
    if (state.phone_verified) {
      return '<div class="verify is-verified">' +
        '<div class="verify-head"><div class="verify-icon" aria-hidden="true">✓</div>' +
        '<div><b>' + esc(state.verified_phone || state.phone) + '</b>' +
        '<p>Nice — that number is confirmed.</p></div></div>' +
        '<div class="verify-row"><span class="verified-badge">✓ Phone number verified</span>' +
        '<button type="button" class="link-button" id="change-phone">Change phone number</button>' +
        '</div></div>';
    }

    var block = '<div class="field" style="margin-bottom:18px">' +
      '<label for="f-phone"><span>Mobile number</span>' +
      '<span class="tag tag-req">Required</span></label>' +
      '<input id="f-phone" data-key="phone" type="tel" inputmode="tel" autocomplete="tel" ' +
      'value="' + esc(state.phone) + '" placeholder="(253) 555-0123"></div>' +
      '<div class="verify"><div class="verify-head">' +
      '<div class="verify-icon" aria-hidden="true">💬</div>' +
      '<div><b>Quick text check</b><p>Partners only receive numbers that answer. ' +
      'Takes about ten seconds.</p></div></div>';

    if (!state.otp_sent) {
      block += '<button type="button" class="button verify-send" id="send-code"' +
        (state.busy || !config.phoneVerificationEnabled ? ' disabled' : '') + '>' +
        (state.busy ? '<span class="loading"></span> Sending…' : 'Text me a code') + '</button>';
    } else {
      var boxes = '';
      for (var i = 0; i < 6; i++) {
        var digit = (state.otp_code || '')[i] || '';
        boxes += '<input id="otp-' + i + '" data-otp="' + i + '" type="text" ' +
          'inputmode="numeric" maxlength="1" aria-label="Digit ' + (i + 1) + '" ' +
          (i === 0 ? 'autocomplete="one-time-code" ' : 'autocomplete="off" ') +
          'value="' + esc(digit) + '"' + (digit ? ' class="filled"' : '') + '>';
      }
      block += '<div><label for="otp-0" style="display:block;font-weight:850;' +
        'font-size:.86rem;margin-bottom:10px">Enter the 6-digit code</label>' +
        '<div class="otp-boxes" id="otp-boxes">' + boxes + '</div></div>' +
        '<div class="verify-row">' +
        '<button type="button" class="link-button" id="resend-code"' +
        (state.resend_in > 0 || state.busy ? ' disabled' : '') + '>' +
        (state.resend_in > 0 ? 'Resend in ' + state.resend_in + 's' : 'Send a new code') +
        '</button><button type="button" class="link-button" id="change-phone">' +
        'Use a different number</button></div>';
    }

    var statusText = state.otp_error || state.otp_status;
    if (statusText) {
      block += '<p class="verify-status ' + (state.otp_error ? 'status-error' : 'status-note') +
        '" role="status" aria-live="polite">' + esc(statusText) + '</p>';
    }
    return block + '</div>';
  }

  function startResendCountdown() {
    clearInterval(resendTimer);
    state.resend_in = config.resendDelaySeconds;
    resendTimer = setInterval(function () {
      state.resend_in -= 1;
      var button = mount.querySelector('#resend-code');
      if (state.resend_in <= 0) {
        clearInterval(resendTimer);
        state.resend_in = 0;
        if (button) { button.disabled = false; button.textContent = 'Send a new code'; }
      } else if (button) {
        button.textContent = 'Resend in ' + state.resend_in + 's';
      }
    }, 1000);
  }

  function sendCode() {
    syncInputs();
    if (digitsOf(state.phone).length < 10) {
      state.errors.phone = 'Enter a 10-digit US mobile number first.';
      render();
      return;
    }
    delete state.errors.phone;
    state.busy = true;
    state.otp_error = '';
    state.otp_status = 'Sending your code…';
    render();

    postJSON('/api/quotes/phone-verification/start', {
      phone: state.phone,
      quote_draft_id: state.quote_draft_id,
      session_id: state.session_id,
      company_website: ''
    }, 'We could not send the code just now.').then(function (body) {
      state.busy = false;
      state.verification_attempt_id = body.verification_attempt_id || '';
      state.otp_code = '';
      if (body.already_verified) {
        markVerified();
      } else {
        state.otp_sent = true;
        state.otp_status = 'Code sent to ' + (body.masked_phone || 'your phone') + '.';
        startResendCountdown();
      }
      render();
      var first = mount.querySelector('#otp-0');
      if (first) first.focus();
    }).catch(function (error) {
      state.busy = false;
      state.otp_error = error.message;
      state.otp_status = '';
      render();
    });
  }

  function verifyCode() {
    var code = (state.otp_code || '').trim();
    if (!/^\d{6}$/.test(code)) return;
    state.busy = true;
    state.otp_error = '';
    state.otp_status = 'Checking…';
    render();

    postJSON('/api/quotes/phone-verification/complete', {
      quote_draft_id: state.quote_draft_id,
      verification_attempt_id: state.verification_attempt_id,
      session_id: state.session_id,
      code: code
    }, 'That code did not work.').then(function () {
      state.busy = false;
      markVerified();
      render();
    }).catch(function (error) {
      state.busy = false;
      state.otp_code = '';
      state.otp_error = error.message;
      state.otp_status = '';
      render();
      var first = mount.querySelector('#otp-0');
      if (first) first.focus();
    });
  }

  function postJSON(path, payload, fallbackMessage) {
    return fetch(API + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) throw new Error(body.error || fallbackMessage);
        return body;
      });
    });
  }

  function markVerified() {
    clearInterval(resendTimer);
    state.phone_verified = true;
    state.verified_phone = state.phone;
    state.otp_status = '';
    state.otp_error = '';
    state.resend_in = 0;
    delete state.errors.phone;
    save();
  }

  function resetVerification(message) {
    clearInterval(resendTimer);
    state.phone_verified = false;
    state.otp_sent = false;
    state.otp_code = '';
    state.verification_attempt_id = '';
    state.verified_phone = '';
    state.resend_in = 0;
    state.otp_error = '';
    state.otp_status = message || '';
    save();
  }

  function bindOtp() {
    var boxes = mount.querySelectorAll('[data-otp]');
    if (!boxes.length) return;

    function collect() {
      var code = '';
      Array.prototype.forEach.call(boxes, function (box) {
        code += (box.value || '').replace(/\D/g, '');
      });
      state.otp_code = code;
      return code;
    }

    Array.prototype.forEach.call(boxes, function (box, index) {
      box.addEventListener('input', function () {
        var typed = box.value.replace(/\D/g, '');
        if (typed.length > 1) {
          // Pasted, or autofilled from the SMS — spread it across the boxes.
          typed.split('').forEach(function (char, offset) {
            if (boxes[index + offset]) boxes[index + offset].value = char;
          });
          boxes[Math.min(index + typed.length, boxes.length - 1)].focus();
        } else {
          box.value = typed;
          if (typed && boxes[index + 1]) boxes[index + 1].focus();
        }
        Array.prototype.forEach.call(boxes, function (b) {
          b.classList.toggle('filled', !!b.value);
        });
        // Verifies itself the moment the sixth digit lands — no extra button.
        if (collect().length === 6) verifyCode();
      });

      box.addEventListener('keydown', function (event) {
        if (event.key === 'Backspace' && !box.value && boxes[index - 1]) {
          event.preventDefault();
          boxes[index - 1].value = '';
          boxes[index - 1].focus();
          collect();
        }
        if (event.key === 'ArrowLeft' && boxes[index - 1]) boxes[index - 1].focus();
        if (event.key === 'ArrowRight' && boxes[index + 1]) boxes[index + 1].focus();
      });

      box.addEventListener('focus', function () { box.select(); });
    });
  }

  // ---------------------------------------------------------------- photos
  function photoPreview() {
    if (!state.photos.length) return '';
    return '<ul class="photo-preview">' + state.photos.map(function (file, i) {
      var url = file._url || (file._url = URL.createObjectURL(file));
      return '<li class="photo-thumb"><img src="' + url + '" alt="' + esc(file.name) + '">' +
        '<button type="button" class="photo-remove" data-photo-remove="' + i +
        '" aria-label="Remove photo">×</button></li>';
    }).join('') + '</ul>';
  }

  function addPhotos(files) {
    var limit = config.maxPhotoMb * 1024 * 1024;
    var rejected = 0;
    Array.prototype.slice.call(files).forEach(function (file) {
      if (state.photos.length >= config.maxPhotos) return;
      if (file.size > limit) { rejected += 1; return; }
      state.photos.push(file);
    });
    if (rejected) {
      state.errors.photos = rejected + ' photo' + (rejected > 1 ? 's were' : ' was') +
        ' over ' + config.maxPhotoMb + ' MB and skipped.';
    } else {
      delete state.errors.photos;
    }
    render();
  }

  // ---------------------------------------------------------------- validate
  function validate() {
    var errors = {};
    if (state.step === 0) {
      if (!state.service_pick) errors.service = 'Pick the service you need.';
      else if (state.service_pick === 'not_sure' && !state.service_type) {
        errors.service_closest = 'Pick whichever is closest — you can change it on the call.';
      }
      if (family() && !state.job_type) errors.job_type = 'Pick the option that fits best.';
    }

    if (state.step === 1) {
      if (!state.pickup_address || state.pickup_address.trim().length < 5) {
        errors.pickup = 'Add the street address for the job.';
      } else if (!/^\d{5}$/.test(state.zip_code)) {
        errors.pickup = 'We still need a 5-digit ZIP code for this address.';
      }
      if (needsDestination()) {
        if (!state.destination_known) {
          errors.destination_known = 'Let us know whether you have the address yet.';
        }
        if (state.destination_known === 'yes') {
          if (!state.destination_address || state.destination_address.trim().length < 5) {
            errors.destination = 'Add the drop-off address.';
          } else if (isMove() && !/^\d{5}$/.test(state.destination_zip)) {
            errors.destination = 'We still need a ZIP code for the destination.';
          }
        }
        if (state.destination_known === 'no' && !state.destination_rough.trim()) {
          errors.destination_rough = 'A city or ZIP is enough.';
        }
      }
      if (!state.timing) errors.timing = 'Pick when you need help.';
      if (state.timing === 'specific_date' && !state.service_date) {
        errors.service_date = 'Choose a date.';
      }
      if (!state.property_type) errors.property_type = 'Pick the property type.';
    }

    if (state.step === 2) {
      if (!state.job_size) errors.job_size = 'Pick the closest size — a guess is fine.';
      if (!state.item_categories.length) errors.item_categories = 'Pick at least one.';
      if (!state.special_item_types.length) {
        errors.special_item_types = 'Pick one, or choose "None of these".';
      }
      if (!state.access_issues.length) {
        errors.access_issues = 'Pick one, or choose "No access issues".';
      }
      if (state.access_issues.indexOf('stairs') >= 0 && !state.stairs_flights) {
        errors.stairs_flights = 'How many flights?';
      }
      if (isMove() && !state.destination_same_access && !state.destination_access_issues.length) {
        errors.destination_access_issues = 'Pick one, or choose "No access issues".';
      }
    }

    if (state.step === 3) {
      if (state.full_name.trim().length < 2) errors.full_name = 'Add your name.';
      if (digitsOf(state.phone).length < 10) errors.phone = 'Add a 10-digit US mobile number.';
      else if (!state.phone_verified && config.phoneVerificationRequired) {
        errors.phone = 'Verify your number with the code we texted you.';
      }
      if (!state.preferred_contact) errors.preferred_contact = 'Pick how they should reach you.';
      if (state.email && !/^\S+@\S+\.\S+$/.test(state.email)) {
        errors.email = 'That email address looks incomplete.';
      }
      if (!state.consent) errors.consent = 'We need your OK before sharing the request.';
    }
    return errors;
  }

  // ---------------------------------------------------------------- render
  function render() {
    var focusId = document.activeElement && document.activeElement.id;
    var caret = null;
    try {
      if (document.activeElement && 'selectionStart' in document.activeElement) {
        caret = document.activeElement.selectionStart;
      }
    } catch (e) { caret = null; }

    var lastStep = state.step === 3;
    var body = [stepService, stepLocation, stepDetails, stepContact][state.step]();
    var progress = Math.round(((state.step + 1) / 4) * 100);

    mount.innerHTML =
      '<div class="quote-head"><div class="quote-head-top">' +
        '<div class="quote-step-name"><em>' + (state.step + 1) + '.</em> ' +
        esc(STEP_NAMES[state.step]) + '</div>' +
        '<div class="quote-step-count">Step ' + (state.step + 1) + ' of 4</div>' +
      '</div><div class="progress-track" role="progressbar" aria-valuenow="' + progress +
      '" aria-valuemin="0" aria-valuemax="100"><div class="progress-fill" style="width:' +
      progress + '%"></div></div></div>' +
      '<div class="quote-body">' +
        (state.errors._top ? '<div class="error-box" role="alert">' +
          esc(state.errors._top) + '</div>' : '') +
        body +
        '<div class="quote-actions">' +
          (state.step ? '<button type="button" class="back-button" id="back">← Back</button>'
            : '<span></span>') +
          '<button type="button" class="button button-lg action-next" id="next"' +
          (state.submitting ? ' disabled' : '') + '>' +
          (lastStep ? (state.submitting ? '<span class="loading"></span> Sending…'
            : 'Send My Request') : 'Continue →') + '</button>' +
        '</div>' +
        (lastStep
          ? '<p class="submit-note">Free to submit. No payment is required. A local partner ' +
            'will contact you to discuss the job and provide a quote.</p>'
          : (state.restored && state.step === 0
            ? '<p class="saved-note">✓ We saved your earlier answers.</p>' : '')) +
      '</div>';

    bind();

    if (focusId) {
      var again = document.getElementById(focusId);
      if (again) {
        again.focus();
        if (caret != null && 'setSelectionRange' in again) {
          try { again.setSelectionRange(caret, caret); } catch (e) { /* date inputs */ }
        }
      }
    }
    save();
  }

  function bind() {
    mount.querySelectorAll('[data-pick]').forEach(function (element) {
      element.addEventListener('click', function () {
        pick(element.dataset.pick, element.dataset.value);
      });
    });

    mount.querySelectorAll('[data-toggle]').forEach(function (element) {
      element.addEventListener('click', function () {
        toggle(element.dataset.toggle, element.dataset.value);
      });
    });

    // Text inputs never re-render on input — that would steal the caret.
    mount.querySelectorAll('[data-key]').forEach(function (element) {
      element.addEventListener('input', function () {
        var key = element.dataset.key;
        if (key === 'phone' && (state.phone_verified || state.otp_sent) &&
            state.phone !== element.value) {
          state.phone = element.value;
          resetVerification('Number changed — send a new code when you are ready.');
          render();
          return;
        }
        state[key] = element.value;
        if (key === 'description') {
          var counter = mount.querySelector('#desc-count');
          if (counter) counter.textContent = element.value.length + ' / 1000';
        }
        delete state.errors[key];
        save();
      });
    });

    mount.querySelectorAll('[data-addr]').forEach(function (element) {
      bindAddress(element.dataset.addr);
    });

    mount.querySelectorAll('[data-addr-change]').forEach(function (element) {
      element.addEventListener('click', function () {
        var k = addressKeys(element.dataset.addrChange);
        state[k.confirmed] = false;
        state[k.street] = '';
        state[k.zip] = '';
        render();
      });
    });

    var manual = mount.querySelector('[data-addr-manual]');
    if (manual) manual.addEventListener('click', function () {
      state.manual_address = true;
      render();
    });

    bindOtp();

    var send = mount.querySelector('#send-code');
    if (send) send.addEventListener('click', sendCode);
    var resend = mount.querySelector('#resend-code');
    if (resend) resend.addEventListener('click', sendCode);

    var change = mount.querySelector('#change-phone');
    if (change) change.addEventListener('click', function () {
      resetVerification('');
      state.phone = '';
      render();
      var input = mount.querySelector('#f-phone');
      if (input) input.focus();
    });

    var consent = mount.querySelector('#consent');
    if (consent) consent.addEventListener('change', function () {
      state.consent = consent.checked;
      if (state.consent) delete state.errors.consent;
      render();
    });

    var choose = mount.querySelector('#photo-choose');
    var take = mount.querySelector('#photo-take');
    var picker = mount.querySelector('#photo-input');
    var camera = mount.querySelector('#photo-camera');
    if (choose && picker) choose.addEventListener('click', function () { picker.click(); });
    if (take && camera) take.addEventListener('click', function () { camera.click(); });
    if (picker) picker.addEventListener('change', function () { addPhotos(picker.files); });
    if (camera) camera.addEventListener('change', function () { addPhotos(camera.files); });
    mount.querySelectorAll('[data-photo-remove]').forEach(function (element) {
      element.addEventListener('click', function () {
        state.photos.splice(Number(element.dataset.photoRemove), 1);
        render();
      });
    });

    var back = mount.querySelector('#back');
    if (back) back.addEventListener('click', function () {
      syncInputs();
      state.errors = {};
      state.step = Math.max(0, state.step - 1);
      render();
      scrollToCard();
    });

    var next = mount.querySelector('#next');
    if (next) next.addEventListener('click', function () {
      syncInputs();
      var errors = validate();
      if (Object.keys(errors).length) {
        state.errors = errors;
        render();
        var first = mount.querySelector('.q-error');
        if (first) (first.closest('.q') || first).scrollIntoView({
          behavior: 'smooth', block: 'center'
        });
        return;
      }
      state.errors = {};
      if (state.step < 3) {
        state.step += 1;
        render();
        scrollToCard();
      } else {
        submit();
      }
    });
  }

  /* Between renders the DOM owns the text inputs, so pull their values back
     into state before validating or moving on. */
  function syncInputs() {
    mount.querySelectorAll('[data-key]').forEach(function (element) {
      state[element.dataset.key] = element.value;
    });
    mount.querySelectorAll('[data-addr]').forEach(function (wrap) {
      var input = wrap.querySelector('input');
      if (input) state[addressKeys(wrap.dataset.addr).street] = input.value;
    });
    var consent = mount.querySelector('#consent');
    if (consent) state.consent = consent.checked;
    save();
  }

  function pick(key, value) {
    // The "same as pickup" shortcut is a boolean behind a two-card question.
    if (key === 'destination_same_access_choice') {
      state.destination_same_access = value === 'same';
      if (state.destination_same_access) {
        state.destination_access_issues = [];
        state.destination_stairs_flights = '';
      }
      render();
      return;
    }

    // Tapping an optional answer twice clears it.
    var optional = ['preferred_time', 'contact_time'];
    state[key] = (optional.indexOf(key) >= 0 && state[key] === value) ? '' : value;
    delete state.errors[key];

    if (key === 'service_pick') {
      delete state.errors.service;
      state.service_type = value === 'not_sure' ? '' : value;
      // Changing the service invalidates every answer shaped by it.
      state.job_type = '';
      state.job_size = '';
      state.item_categories = [];
      state.extra_services = [];
    }

    if (key === 'service_type') {
      delete state.errors.service_closest;
      state.job_type = '';
      state.job_size = '';
      state.item_categories = [];
    }

    if (key === 'job_type') {
      // The one job type that changes the underlying service.
      if (value === 'long_distance') state.service_type = 'long_distance_move';
      else if (isMove()) state.service_type = 'local_move';
      if (!needsDestination()) {
        state.destination_known = '';
        state.destination_address = '';
        state.destination_confirmed = false;
      }
    }

    if (key === 'timing' && value !== 'specific_date') state.service_date = '';
    if (key === 'destination_known') {
      if (value === 'yes') state.destination_rough = '';
      else { state.destination_address = ''; state.destination_confirmed = false; }
    }

    render();
  }

  function toggle(key, value) {
    var list = (state[key] || []).slice();
    if (value === 'none' || value === 'not_sure') {
      // "None" and "Not sure" clear everything else — they can't coexist.
      list = list.indexOf(value) >= 0 ? [] : [value];
    } else {
      list = list.filter(function (v) { return v !== 'none' && v !== 'not_sure'; });
      var at = list.indexOf(value);
      if (at >= 0) list.splice(at, 1); else list.push(value);
    }
    state[key] = list;
    delete state.errors[key];
    if (key === 'access_issues' && list.indexOf('stairs') < 0) state.stairs_flights = '';
    if (key === 'destination_access_issues' && list.indexOf('stairs') < 0) {
      state.destination_stairs_flights = '';
    }
    render();
  }

  function scrollToCard() {
    var card = document.querySelector('.quote-card');
    if (!card) return;
    window.scrollTo({
      top: card.getBoundingClientRect().top + window.pageYOffset - 96,
      behavior: 'smooth'
    });
  }

  // ---------------------------------------------------------------- submit
  function submit() {
    if (state.submitting) return;
    state.submitting = true;
    render();

    // "Renton" or "98055" — work out which one they gave us.
    var roughZip = (state.destination_rough.match(/\b\d{5}\b/) || [''])[0];
    var roughCity = roughZip ? '' : state.destination_rough.trim();

    var fields = {
      service_type: state.service_type,
      job_type: state.job_type,
      job_size: state.job_size,
      pickup_address: state.pickup_address,
      pickup_unit: state.pickup_unit,
      pickup_city: state.pickup_city,
      pickup_state: state.pickup_state,
      zip_code: state.zip_code,
      destination_known: needsDestination() ? String(state.destination_known === 'yes') : 'true',
      destination_address: state.destination_address,
      destination_unit: state.destination_unit,
      destination_city: state.destination_city || roughCity,
      destination_state: state.destination_state,
      destination_zip: state.destination_zip || roughZip,
      property_type: state.property_type,
      timing: state.timing,
      service_date: state.service_date,
      preferred_time: state.preferred_time,
      item_categories: state.item_categories.join(','),
      extra_services: state.extra_services.join(','),
      special_item_types: state.special_item_types.join(','),
      special_items_note: state.special_items_note,
      access_issues: state.access_issues.join(','),
      stairs_flights: state.stairs_flights,
      destination_access_issues: state.destination_same_access
        ? state.access_issues.join(',') : state.destination_access_issues.join(','),
      destination_stairs_flights: state.destination_same_access
        ? state.stairs_flights : state.destination_stairs_flights,
      description: state.description,
      full_name: state.full_name,
      phone: state.phone,
      email: state.email,
      preferred_contact: state.preferred_contact,
      contact_time: state.contact_time || 'anytime',
      verification_attempt_id: state.verification_attempt_id,
      quote_draft_id: state.quote_draft_id,
      session_id: state.session_id,
      consent: 'true',
      company_website: (mount.querySelector('#company-website') || {}).value || '',
      landing_page: location.href,
      referrer_url: document.referrer || ''
    };
    ['utm_source', 'utm_medium', 'utm_campaign', 'gclid', 'fbclid'].forEach(function (key) {
      if (params.get(key)) fields[key] = params.get(key);
    });

    var data = new FormData();
    Object.keys(fields).forEach(function (key) { data.append(key, fields[key] || ''); });
    state.photos.forEach(function (file) { data.append('photos', file); });

    fetch(API + '/api/leads', { method: 'POST', body: data }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) {
          throw new Error(body.error || (body.errors && Object.keys(body.errors)
            .map(function (k) { return body.errors[k]; }).join(' ')) ||
            'We could not submit your request.');
        }
        return body;
      });
    }).then(function (body) {
      try {
        sessionStorage.setItem(RECEIPT_KEY, JSON.stringify({
          reference: body.reference,
          service: serviceLabel(),
          phone: state.phone,
          contact: labelFor(CONTACT_METHODS, state.preferred_contact) || 'Text me'
        }));
        sessionStorage.removeItem(DRAFT_KEY);
      } catch (e) { /* the thank-you page falls back to the ?ref parameter */ }
      location.href = '/thank-you/?ref=' + encodeURIComponent(body.reference || '');
    }).catch(function (error) {
      state.submitting = false;
      state.errors._top = error.message;
      render();
      window.scrollTo({ top: mount.offsetTop - 100, behavior: 'smooth' });
    });
  }

  // ---------------------------------------------------------------- boot
  fetch(API + '/api/config').then(function (response) {
    if (!response.ok) throw new Error('config');
    return response.json();
  }).then(function (loaded) {
    config.brand = loaded.brand || config.brand;
    config.consentText = loaded.consentText || config.consentText;
    config.phoneVerificationEnabled = loaded.phoneVerificationEnabled !== false;
    config.phoneVerificationRequired = loaded.phoneVerificationRequired !== false;
    config.addressLookupEnabled = loaded.addressLookupEnabled !== false;
    config.maxPhotos = loaded.maxPhotos || config.maxPhotos;
    config.maxPhotoMb = loaded.maxPhotoMb || config.maxPhotoMb;
    config.resendDelaySeconds = loaded.resendDelaySeconds || config.resendDelaySeconds;
    if (!config.phoneVerificationEnabled) {
      state.otp_status = 'Text verification is unavailable right now — you can still submit.';
    }
    render();
  }).catch(render);

  render();
})();
