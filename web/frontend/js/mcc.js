// ── MCC Builder ──────────────────────────────────────────────────────────────
// Visual offline GUI for building MCC layouts without AI.
// Calls /api/execute directly (no LLM involved).
// ─────────────────────────────────────────────────────────────────────────────
(function () {
  'use strict';

  const MOD_PX = 18;   // pixels per 1 module height in the diagram

  // ── Minimum mod heights per starter type ──────────────────────────────────
  // Only types with a genuine lower bound are listed here.
  // VFD, SS, and CUSTOM blocks exist at all sizes 1–24, so they have no entry.
  const MIN_MOD = {
    // (none currently enforced — remove this comment and add entries if needed)
  };

  // ── State ─────────────────────────────────────────────────────────────────
  let _projectId    = null;
  let _projectName  = null;   // human-readable project name (may be same as ID if unnamed)
  let _projectState = null;
  let _busy         = false;

  // ── API ───────────────────────────────────────────────────────────────────
  async function exec(tool, params = {}) {
    const res = await fetch('/api/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tool, params }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  // ── Unit type colours ─────────────────────────────────────────────────────
  const TYPE_COLOR = {
    FEEDER:      '#3b82f6',
    DUALFEEDER:  '#0ea5e9',
    FVNR:        '#10b981',
    FVR:         '#6366f1',
    '2S2W':      '#14b8a6',
    '2S1W':      '#0891b2',
    VFD:         '#f59e0b',
    VVVF:        '#f59e0b',
    SS:          '#ef4444',
    SOFTSTART:   '#ef4444',
    STARDELTATR: '#8b5cf6',
    CUSTOM:      '#a78bfa',   // purple — custom/special units
    SPACE:       '#3f4f68',
  };
  function typeColor(t) {
    // Strip digits and non-alpha so e.g. "DUAL_FEEDER" → "DUALFEEDER", "2S2W" → "SW"
    return TYPE_COLOR[(t || '').toUpperCase().replace(/[^A-Z]/g, '')] || '#64748b';
  }

  // ── Status bar helper ─────────────────────────────────────────────────────
  function setStatus(msg, isError = false) {
    const el = document.getElementById('mcc-statusbar');
    if (!el) return;
    el.textContent = msg;
    el.className = 'mcc-statusbar' + (isError ? ' mcc-statusbar-error' : '');
  }

  // ── Friendly error messages ───────────────────────────────────────────────
  // Maps common backend error strings to actionable guidance shown in the UI.
  function _friendlyError(raw) {
    if (!raw) return 'Unknown error.';
    const r = raw.toLowerCase();
    if (r.includes('cannot connect to autocad') || r.includes('autocad not running'))
      return '⚠ AutoCAD is not running. Start AutoCAD Electrical, then try again.';
    if (r.includes('not open in autocad') || r.includes('.dwg is not open'))
      return `⚠ ${raw}`;   // already human-readable from Python
    if (r.includes('block file not found') || r.includes('section block file not found'))
      return `⚠ ${raw}`;   // already includes the searched-paths hint
    if (r.includes('mcc_block_library'))
      return `⚠ Block library not configured. Set MCC_BLOCK_LIBRARY in your .env file.`;
    return raw;
  }

  // ── Right-panel form helpers ───────────────────────────────────────────────
  function showRp(title, html) {
    document.getElementById('mcc-rp-title').textContent = title;
    document.getElementById('mcc-rp-body').innerHTML = html;
    document.getElementById('mcc-rp').classList.add('mcc-rp-open');
  }
  function hideRp() {
    document.getElementById('mcc-rp').classList.remove('mcc-rp-open');
  }

  // ── Render MCC diagram ────────────────────────────────────────────────────
  function renderDiagram(state) {
    const canvas = document.getElementById('mcc-canvas');
    if (!canvas) return;
    canvas.innerHTML = '';

    // Allow drops anywhere on the canvas so the cursor never shows "not allowed"
    canvas.addEventListener('dragover', e => { if (_drag) e.preventDefault(); });
    canvas.addEventListener('dragleave', () => {
      // Clear any stale active drop zone when cursor leaves the canvas
      document.querySelectorAll('.mcc-dz-active').forEach(z => z.classList.remove('mcc-dz-active'));
    });

    if (!state || !state.sections || Object.keys(state.sections).length === 0) {
      canvas.innerHTML = `
        <div class="mcc-empty-canvas">
          <div class="mcc-empty-icon">⬛</div>
          <p>No sections yet.</p>
          <button class="btn-primary" id="mcc-empty-add-sec">＋ Add First Section</button>
        </div>`;
      document.getElementById('mcc-empty-add-sec')
              ?.addEventListener('click', openAddSectionForm);
      return;
    }

    for (const [secId, sec] of Object.entries(state.sections)) {
      canvas.appendChild(buildSectionCol(secId, sec));
    }

    // Ghost "add section" column
    const ghost = document.createElement('div');
    ghost.className = 'mcc-col mcc-col-ghost';
    ghost.innerHTML = `<div class="mcc-ghost-inner">＋<br><small>Add Section</small></div>`;
    ghost.addEventListener('click', openAddSectionForm);
    canvas.appendChild(ghost);
  }

  // ── Drag-and-drop state ───────────────────────────────────────────────────
  let _drag = null;   // { unit_no, from_section } while a drag is in flight

  function _makeDz(secId, index) {
    const dz = document.createElement('div');
    dz.className = 'mcc-drop-zone';
    dz.dataset.sec   = secId;
    dz.dataset.index = index;
    dz.addEventListener('dragover',  e => { if (!_drag) return; e.preventDefault(); dz.classList.add('mcc-dz-active'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('mcc-dz-active'));
    dz.addEventListener('drop',      e => { e.preventDefault(); dz.classList.remove('mcc-dz-active'); _commitMove(secId, index); });
    return dz;
  }

  async function _commitMove(toSec, toIdx) {
    if (!_drag || !_projectId) return;
    const { unit_no } = _drag;
    _drag = null;
    document.getElementById('mcc-canvas')?.classList.remove('mcc-dragging');
    if (_busy) return;
    setBusy(true);
    try {
      const res = await exec('move_unit', {
        project_id:        _projectId,
        unit_no,
        target_section_id: toSec,
        target_index:      toIdx,
      });
      if (res.success) {
        if (res.moved === false) {
          setStatus('No change.');
        } else {
          let msg = `Moved ${unit_no} → ${toSec}`;
          if (res.cascaded?.length) {
            msg += ' | cascaded: ' + res.cascaded.map(c => `${c.unit_no} → ${c.to_section}`).join(', ');
          }
          setStatus(msg);
        }
        await refreshState();
      } else {
        setStatus((res.overflow_warning ? '⚠ ' : '✗ ') + (res.error ?? 'Move failed.'), true);
      }
    } catch (err) {
      setStatus('✗ ' + err.message, true);
    } finally { setBusy(false); }
  }

  function buildSectionCol(secId, sec) {
    const capacity  = sec.capacity_mods  ?? 24.5;
    const used      = sec.used_mods      ?? 0;
    const remaining = sec.remaining_mods ?? (capacity - used);
    const widthMM   = sec.section_width  ?? 500;
    const pct       = Math.min(100, (used / capacity) * 100).toFixed(0);
    const units     = sec.units ?? [];

    const col = document.createElement('div');
    col.className   = 'mcc-col';
    col.dataset.sec = secId;

    col.innerHTML = `
      <div class="mcc-col-header">
        <span class="mcc-col-id">${secId}</span>
        <span class="mcc-col-mm">${widthMM}mm</span>
      </div>
      <div class="mcc-col-progress">
        <div class="mcc-col-bar">
          <div class="mcc-col-bar-fill" style="width:${pct}%"
               title="${used} / ${capacity} mod used"></div>
        </div>
        <span class="mcc-col-pct">${used}/${capacity}</span>
      </div>
      <div class="mcc-col-units" id="mcc-units-${secId}"></div>
    `;

    const unitsDiv = col.querySelector('.mcc-col-units');
    // Pin height to exactly capacity × MOD_PX so all sections with the same
    // capacity are identical in height regardless of unit count or borders.
    unitsDiv.style.height   = (capacity * MOD_PX) + 'px';
    unitsDiv.style.overflow = 'hidden';

    // Drop zone above unit[0]
    unitsDiv.appendChild(_makeDz(secId, 0));

    units.forEach((unit, i) => {
      unitsDiv.appendChild(buildUnitBlock(unit, secId));
      unitsDiv.appendChild(_makeDz(secId, i + 1));
    });

    // Empty slot — also a drop target for "drop at bottom"
    if (remaining > 0.1) {
      const slot = document.createElement('div');
      slot.className    = 'mcc-unit-empty';
      slot.style.height = (remaining * MOD_PX) + 'px';
      slot.innerHTML    = `<span>＋ ${remaining.toFixed(1)} mod free</span>`;
      slot.addEventListener('click',     () => openAddUnitForm(secId));
      slot.addEventListener('dragover',  e  => { if (!_drag) return; e.preventDefault(); slot.classList.add('mcc-dz-active'); });
      slot.addEventListener('dragleave', ()  => slot.classList.remove('mcc-dz-active'));
      slot.addEventListener('drop',      e  => { e.preventDefault(); slot.classList.remove('mcc-dz-active'); _commitMove(secId, units.length); });
      unitsDiv.appendChild(slot);
    }

    return col;
  }

  function buildUnitBlock(unit, secId) {
    const rawType   = unit.fields?.TYPE || unit.fields?.STARTER || unit.starter_type || '';
    const col       = typeColor(rawType);
    // If a custom tag was set, show it as the label; otherwise show the type name.
    const typeLabel = (unit.tag && unit.tag.trim()) ? unit.tag.trim() : rawType;
    const h       = (unit.mod_height ?? 4) * MOD_PX;

    const el = document.createElement('div');
    el.className         = 'mcc-unit-block';
    el.style.height      = h + 'px';
    el.draggable         = true;
    el.dataset.unitNo    = unit.unit_no;
    el.dataset.secId     = secId;

    const isDual = (unit.starter_type ?? '').toUpperCase() === 'DUAL_FEEDER';
    const unitLabel = (unit.unit_no ?? '').startsWith('_SPACE_') ? '' : (unit.unit_no ?? '');

    // For dual feeders show L / R sub-unit info side-by-side
    const bodyInner = isDual ? `
        <div class="mcc-unit-tag" style="color:${col}">DUAL FEEDER</div>
        <div class="mcc-unit-dual-row">
          <div class="mcc-unit-dual-cell">
            <div class="mcc-unit-no">${unit.left_unit_no ?? unitLabel + 'L'}</div>
            <div class="mcc-unit-meta">${unit.left_amp ? unit.left_amp + 'A' : '—'}</div>
          </div>
          <div class="mcc-unit-dual-sep">|</div>
          <div class="mcc-unit-dual-cell">
            <div class="mcc-unit-no">${unit.right_unit_no ?? unitLabel + 'R'}</div>
            <div class="mcc-unit-meta">${unit.right_amp ? unit.right_amp + 'A' : '—'}</div>
          </div>
        </div>
        <div class="mcc-unit-meta">${unit.mod_height} mod</div>
    ` : `
        <div class="mcc-unit-tag" style="color:${col}">${typeLabel || '—'}</div>
        <div class="mcc-unit-no">${unitLabel}</div>
        <div class="mcc-unit-desc">${unit.fields?.DESCRIPTION1 || ''}</div>
        <div class="mcc-unit-meta">
          ${unit.mod_height} mod
          ${unit.fields?.['HP/KW'] ? '· ' + unit.fields['HP/KW'] : ''}
          ${unit.fields?.FLA       ? '· ' + unit.fields.FLA + 'A' : ''}
        </div>
    `;

    el.innerHTML = `
      <div class="mcc-unit-stripe" style="background:${col}"></div>
      <div class="mcc-unit-body">${bodyInner}</div>
      <div class="mcc-unit-delete-btn" title="Delete unit">✕</div>
      <div class="mcc-unit-drag-handle" title="Drag to reorder">⠿</div>
    `;

    // Click on the unit body → open edit form
    el.querySelector('.mcc-unit-body').addEventListener('click', e => {
      e.stopPropagation();
      openEditUnitForm(unit, secId);
    });

    // Delete button — must be wired up after innerHTML is set
    el.querySelector('.mcc-unit-delete-btn').addEventListener('click', async e => {
      e.stopPropagation();
      const label = (unit.unit_no ?? '').startsWith('_SPACE_') ? 'this SPACE' : `"${unit.unit_no}"`;
      if (!confirm(`Delete ${label} (${unit.mod_height} mod) from section ${secId}?`)) return;
      setStatus(`Deleting ${label}…`);
      try {
        const r = await exec('remove_unit', { project_id: _projectId, unit_no: unit.unit_no });
        if (r.success) {
          setStatus(`Deleted ${label}.`);
          await refreshState();
        } else {
          setStatus(`Delete failed: ${r.error}`, true);
        }
      } catch (err) {
        setStatus(`Delete error: ${err}`, true);
      }
    });

    el.addEventListener('dragstart', e => {
      _drag = { unit_no: unit.unit_no, from_section: secId };
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', unit.unit_no);
      document.getElementById('mcc-canvas')?.classList.add('mcc-dragging');
      requestAnimationFrame(() => el.classList.add('mcc-unit-dragging'));
    });
    el.addEventListener('dragend', () => {
      _drag = null;
      el.classList.remove('mcc-unit-dragging');
      document.getElementById('mcc-canvas')?.classList.remove('mcc-dragging');
      document.querySelectorAll('.mcc-dz-active').forEach(z => z.classList.remove('mcc-dz-active'));
    });
    // Allow dropping on a unit block — inserts before it (highlight top drop zone)
    el.addEventListener('dragover', e => {
      if (!_drag || _drag.unit_no === unit.unit_no) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = 'move';
      // Highlight the drop zone immediately above this block
      document.querySelectorAll('.mcc-dz-active').forEach(z => z.classList.remove('mcc-dz-active'));
      const prev = el.previousElementSibling;
      if (prev?.classList.contains('mcc-drop-zone')) prev.classList.add('mcc-dz-active');
    });
    el.addEventListener('drop', e => {
      if (!_drag || _drag.unit_no === unit.unit_no) return;
      e.preventDefault();
      e.stopPropagation();
      document.querySelectorAll('.mcc-dz-active').forEach(z => z.classList.remove('mcc-dz-active'));
      // Find this unit's index inside the section unit list
      const allBlocks = [...el.parentElement.querySelectorAll('.mcc-unit-block')];
      const idx = allBlocks.indexOf(el);
      _commitMove(secId, idx);   // insert before this unit
    });

    return el;
  }

  // ── Shared form helpers ────────────────────────────────────────────────────

  // Canonical starter type option list (no DOL, includes 2S2W / 2S1W)
  function _STARTER_OPTS(selected = 'FVNR') {
    const types = [
      ['FEEDER',       'FEEDER'],
      ['DUAL_FEEDER',  'DUAL FEEDER (2-in-1 bucket)'],
      ['FVNR',         'FVNR'],
      ['FVR',          'FVR (Reversing)'],
      ['2S2W',         '2S2W (Two-Speed 2-Winding)'],
      ['2S1W',         '2S1W (Two-Speed 1-Winding)'],
      ['VFD',          'VFD'],
      ['SS',           'SS (Soft Starter)'],
      ['CUSTOM',       'CUSTOM (PLC, panelboard, special)'],
      ['SPACE',        'SPACE (empty slot)'],
    ];
    return types.map(([v, l]) =>
      `<option value="${v}"${v === selected ? ' selected' : ''}>${l}</option>`
    ).join('');
  }

  function _MOD_OPTS(def = 4) {
    // Full range 1–24 in whole-number steps, with 3.5 inserted after 3
    // (used by feeder blocks).  All sizes have corresponding DWG blocks.
    const sizes = [];
    for (let m = 1; m <= 24; m++) {
      sizes.push(m);
      if (m === 3) sizes.push(3.5);
    }
    return sizes.map(m =>
      `<option value="${m}"${m === def ? ' selected' : ''}>${m} mod</option>`
    ).join('');
  }

  // Returns HTML for all UDATALIN detail fields, namespaced with prefix p.
  // Fields are grouped by category to keep the form scannable.
  function _DETAIL_FIELDS(p) {
    const row  = (label, id, placeholder = '', extra = '') =>
      `<div class="mcc-form-row"><label>${label}</label><input id="${p}-${id}" class="form-input" placeholder="${placeholder}" ${extra}/></div>`;
    const sel  = (label, id, opts) =>
      `<div class="mcc-form-row"><label>${label}</label><select id="${p}-${id}" class="form-input">${opts}</select></div>`;
    const hdr  = title =>
      `<div class="mcc-detail-group-hdr">${title}</div>`;

    return `
      ${hdr('General')}
      ${sel('Drawout / Fixed', 'udf', '<option value="D" selected>D – Drawout</option><option value="F">F – Fixed</option>')}
      ${row('Qty', 'uqty', '1', 'value="1"')}
      ${row('EEMAC Size', 'usz', 'e.g. 2')}

      ${hdr('Motor')}
      ${row('HP / kW', 'uhp', 'e.g. 15')}
      ${row('FLA', 'ufla', 'e.g. 28.5')}

      ${hdr('Protection')}
      ${row('Frame', 'uframe', 'e.g. CD63A')}
      ${row('Trip (A)', 'utrip', 'e.g. 63')}
      ${row('Switch', 'uswitch', 'e.g. 63A')}
      ${row('Fuse Type', 'uftype', 'e.g. HRC')}
      ${row('Fuse', 'ufuse', 'e.g. 63A')}

      ${hdr('Contactor / Overload')}
      ${row('Contactor Qty', 'ucqty', 'e.g. 1')}
      ${row('Contactor', 'ucont', 'Cat #')}
      ${row('Coil Voltage', 'ucoil', 'e.g. 120V')}
      ${row('OL Qty', 'uolqty', 'e.g. 1')}
      ${row('Overload', 'uol', 'e.g. 28-40A')}

      ${hdr('Control Circuit')}
      ${row('CCT VA', 'ucct', 'VA rating')}
      ${row('CCT Sec Fuse', 'ucctfsec', 'e.g. 2A')}
      ${row('CCT Pri Fuse', 'ucctfpri', 'e.g. 2A')}
      ${row('Ctrl Fuse Qty', 'ucfuseqty', 'e.g. 1')}
      ${row('Ctrl Fuse Size', 'ucfuse', 'e.g. 2A')}

      ${hdr('Pushbuttons / Selector')}
      ${row('Stop PBs', 'ustop', 'qty')}
      ${row('Start PBs', 'ustart', 'qty')}
      ${row('PTC Resets', 'uptc', 'qty')}
      ${row('Selector Positions', 'upos', 'e.g. 2')}
      ${row('Selector Legend', 'uss', 'e.g. AUTO/OFF/HAND')}

      ${hdr('Pilot Lights')}
      ${row('Red PLs', 'uplred', 'qty')}
      ${row('Green PLs', 'uplgrn', 'qty')}
      ${row('Yellow PLs', 'uplylw', 'qty')}
      ${row('White PLs', 'uplwht', 'qty')}

      ${hdr('Timers')}
      ${row('Timer Qty', 'utmrqty', 'qty')}
      ${row('ON Delay', 'uon', 'X if used')}
      ${row('OFF Delay', 'uoff', 'X if used')}

      ${hdr('Control Relays')}
      ${row('CR Qty', 'ucrqty', 'qty')}
      ${row('NO Contacts', 'ucr-no', 'qty')}
      ${row('NC Contacts', 'unc', 'qty')}
      ${row('PTC Aux', 'uptcaux', 'qty')}
      ${row('Hour Meters', 'uhour', 'qty')}

      ${hdr('Metering')}
      ${row('Voltmeter Scale', 'uvm', 'e.g. 600V')}
      ${row('Ammeter Scale', 'uam', 'e.g. 100A')}
      ${row("CT's", 'ucts', 'qty')}

      ${hdr('Transformer')}
      ${row('Xmer Phases', 'uxmerph', 'e.g. 1')}
      ${row('Xmer KVA', 'ukva', 'e.g. 0.5')}

      ${hdr('Panel Board')}
      ${row('Panel Phases', 'upnlph', 'e.g. 3')}
      ${row("Panel Circuits", 'upnlcct', 'qty')}

      ${hdr('Drawing')}
      ${row('Drawing No.', 'udwg', 'Schematic dwg #')}

      ${hdr('Nameplate')}
      ${row('Line 1', 'unpl1', 'e.g. PUMP 1')}
      ${row('Line 2', 'unpl2', 'e.g. 15 HP')}
      ${row('Line 3', 'unpl3', '')}
      ${row('Line 4', 'unpl4', '')}
      ${row('NP Qty', 'unpqty', 'e.g. 1')}
      ${row('NP Size', 'unpsz', 'e.g. 2"x4"')}
      ${row('NP Style', 'unpstyle', 'e.g. B-1')}
    `;
  }

  // Collect all detail field values into a params object.
  // Keys match the Python add_unit() / bulk_add_units() parameter names.
  function _collectDetailFields(p) {
    const g = id => (document.getElementById(`${p}-${id}`)?.value ?? '').trim();
    const params = {};
    // [element-id-suffix, python-param-name, coerce-to-int?]
    const map = [
      ['udf',      'drawout_fixed',  false],
      ['uqty',     'qty',            true ],
      ['usz',      'eemac_size',     false],
      ['uhp',      'hp_kw',          false],
      ['ufla',     'fla',            false],
      ['uframe',   'frame',          false],
      ['utrip',    'trip',           false],
      ['uswitch',  'switch',         false],
      ['uftype',   'fuse_type',      false],
      ['ufuse',    'fuse',           false],
      ['ucqty',    'cont_qty',       false],
      ['ucont',    'contactor',      false],
      ['ucoil',    'coil',           false],
      ['uolqty',   'ol_qty',         false],
      ['uol',      'overload',       false],
      // control circuit
      ['ucct',     'CCT',            false],
      ['ucctfsec', 'CCT-FSEC',       false],
      ['ucctfpri', 'CCT-FPRI',       false],
      ['ucfuseqty','CFUSE-QTY',      false],
      ['ucfuse',   'CFUSE',          false],
      // pushbuttons / selector
      ['ustop',    'STOP',           false],
      ['ustart',   'START',          false],
      ['uptc',     'PTC',            false],
      ['upos',     'POS',            false],
      ['uss',      'SS',             false],
      // pilot lights
      ['uplred',   'PL-RED',         false],
      ['uplgrn',   'PL-GRN',         false],
      ['uplylw',   'PL-YEL',         false],
      ['uplwht',   'PL-WHT',         false],
      // timers
      ['utmrqty',  'TMR-QTY',        false],
      ['uon',      'ON',             false],
      ['uoff',     'OFF',            false],
      // control relays
      ['ucrqty',   'CR-QTY',         false],
      ['ucr-no',   'NO',             false],
      ['unc',      'NC',             false],
      ['uptcaux',  'PTC-AUX',        false],
      ['uhour',    'HOUR',           false],
      // metering
      ['uvm',      'VOLTMETER',      false],
      ['uam',      'AMMETER',        false],
      ['ucts',     "CT'S",           false],
      // transformer
      ['uxmerph',  'XMER-PH',        false],
      ['ukva',     'KVA',            false],
      // panel board
      ['upnlph',   'PNL-PH',         false],
      ['upnlcct',  "CCT'S",          false],
      // drawing
      ['udwg',     'drawing',        false],
      // nameplate
      ['unpl1',    'np_line1',       false],
      ['unpl2',    'np_line2',       false],
      ['unpl3',    'np_line3',       false],
      ['unpl4',    'np_line4',       false],
      ['unpqty',   'np_qty',         false],
      ['unpsz',    'np_size',        false],
      ['unpstyle', 'np_style',       false],
    ];
    for (const [fid, key, asInt] of map) {
      const val = g(fid);
      if (val) params[key] = asInt ? parseInt(val, 10) : val;
    }
    return params;
  }

  // Clear all detail fields back to defaults
  function _clearDetailFields(p) {
    const ids = [
      'usz','uhp','ufla','uframe','utrip','uswitch','uftype','ufuse',
      'ucqty','ucont','ucoil','uolqty','uol',
      'ucct','ucctfsec','ucctfpri','ucfuseqty','ucfuse',
      'ustop','ustart','uptc','upos','uss',
      'uplred','uplgrn','uplylw','uplwht',
      'utmrqty','uon','uoff',
      'ucrqty','ucr-no','unc','uptcaux','uhour',
      'uvm','uam','ucts','uxmerph','ukva','upnlph','upnlcct','udwg',
      'unpl1','unpl2','unpl3','unpl4','unpqty','unpsz','unpstyle',
    ];
    for (const id of ids) {
      const el = document.getElementById(`${p}-${id}`);
      if (el) el.value = '';
    }
    const qty = document.getElementById(`${p}-uqty`);
    if (qty) qty.value = '1';
    const df = document.getElementById(`${p}-udf`);
    if (df) df.value = 'D';
  }

  // ── Forms ──────────────────────────────────────────────────────────────────
  // ── Edit unit form ─────────────────────────────────────────────────────────
  function openEditUnitForm(unit, secId) {
    const isDual = (unit.starter_type ?? '').toUpperCase() === 'DUAL_FEEDER';
    const f      = unit.fields ?? {};

    // Helper: current value of a field, falling back to ''
    const cur = key => f[key] ?? f[key?.toLowerCase()] ?? '';

    showRp(`Edit Unit — ${unit.unit_no ?? '(space)'}`, `
      <div class="mcc-form">
        <div class="mcc-form-hint">Unit <b>${unit.unit_no ?? ''}</b> in section <b>${secId}</b></div>

        <div class="mcc-form-row">
          <label>Starter Type</label>
          <select id="e-utype" class="form-input">
            ${_STARTER_OPTS(unit.starter_type ?? 'FVNR')}
          </select>
        </div>
        <div class="mcc-form-row">
          <label>Tag <span style="font-size:0.7rem;color:var(--text-muted)">(shown in MCC_LAYOUT block)</span></label>
          <input id="e-tag" class="form-input" value="${unit.tag ?? ''}" placeholder="e.g. FVNR-1 (leave blank = type name)" />
        </div>
        <div class="mcc-form-row">
          <label>Mod Height <span id="e-umods-hint" style="font-size:0.7rem;color:var(--text-muted)"></span></label>
          <select id="e-umods" class="form-input">
            ${_MOD_OPTS(unit.mod_height ?? 4)}
          </select>
        </div>

        <!-- CUSTOM unit width -->
        <div id="e-custom-fields" style="display:${(unit.starter_type ?? '').toUpperCase() === 'CUSTOM' ? '' : 'none'}">
          <div class="mcc-form-row">
            <label>Unit Width</label>
            <select id="e-custom-width" class="form-input">
              <option value="400"${unit.custom_width === 400 ? ' selected' : ''}>400 mm (with 100mm wireway)</option>
              <option value="500"${(!unit.custom_width || unit.custom_width === 500) ? ' selected' : ''}>500 mm (full width)</option>
              <option value="600"${unit.custom_width === 600 ? ' selected' : ''}>600 mm (full width)</option>
              <option value="800"${unit.custom_width === 800 ? ' selected' : ''}>800 mm (full width)</option>
            </select>
          </div>
        </div>

        <!-- Dual feeder amp fields -->
        <div id="e-dual-fields" style="display:${isDual ? '' : 'none'}">
          <div class="mcc-form-row">
            <label>Left Feeder Amps</label>
            <input id="e-left-amp" class="form-input" value="${unit.left_amp ?? ''}" placeholder="e.g. 100" />
          </div>
          <div class="mcc-form-row">
            <label>Right Feeder Amps</label>
            <input id="e-right-amp" class="form-input" value="${unit.right_amp ?? ''}" placeholder="e.g. 100" />
          </div>
        </div>

        <!-- Standard detail fields, pre-filled -->
        <div id="e-details-block" style="display:${isDual ? 'none' : ''}">
          <details class="mcc-form-details" open>
            <summary>Details</summary>
            ${_DETAIL_FIELDS('e')}
          </details>
        </div>

        <div class="mcc-form-actions">
          <button class="btn-primary" id="e-unit-ok">Save Changes</button>
          <button class="btn-sm"      id="e-unit-cancel">Cancel</button>
        </div>
        <div class="mcc-form-result" id="e-unit-res"></div>
      </div>
    `);

    // Pre-fill standard detail fields with current values
    const prefill = {
      'e-udf':      cur('D/F')       || 'D',
      'e-uqty':     cur('QTY')       || '1',
      'e-usz':      cur('SIZE'),
      'e-uhp':      cur('HP/KW'),
      'e-ufla':     cur('FLA'),
      'e-uframe':   cur('FRAME'),
      'e-utrip':    cur('TRIP'),
      'e-uswitch':  cur('SWITCH'),
      'e-uftype':   cur('F.TYPE'),
      'e-ufuse':    cur('FUSE'),
      'e-ucqty':    cur('CONT-QTY'),
      'e-ucont':    cur('CONTACTOR'),
      'e-ucoil':    cur('COIL'),
      'e-uolqty':   cur('OL-QTY'),
      'e-uol':      cur('OVERLOAD'),
      'e-ucct':     cur('CCT'),
      'e-ucctfsec': cur('CCT-FSEC'),
      'e-ucctfpri': cur('CCT-FPRI'),
      'e-ucfuseqty':cur('CFUSE-QTY'),
      'e-ucfuse':   cur('CFUSE'),
      'e-ustop':    cur('STOP'),
      'e-ustart':   cur('START'),
      'e-uptc':     cur('PTC'),
      'e-upos':     cur('POS'),
      'e-uss':      cur('SS'),
      'e-uplred':   cur('PL-RED'),
      'e-uplgrn':   cur('PL-GRN'),
      'e-uplylw':   cur('PL-YEL'),
      'e-uplwht':   cur('PL-WHT'),
      'e-utmrqty':  cur('TMR-QTY'),
      'e-uon':      cur('ON'),
      'e-uoff':     cur('OFF'),
      'e-ucrqty':   cur('CR-QTY'),
      'e-ucr-no':   cur('NO'),
      'e-unc':      cur('NC'),
      'e-uptcaux':  cur('PTC-AUX'),
      'e-uhour':    cur('HOUR'),
      'e-uvm':      cur('VOLTMETER'),
      'e-uam':      cur('AMMETER'),
      'e-ucts':     cur("CT'S"),
      'e-uxmerph':  cur('XMER-PH'),
      'e-ukva':     cur('KVA'),
      'e-upnlph':   cur('PNL-PH'),
      'e-upnlcct':  cur("CCT'S"),
      'e-udwg':     cur('DRAWING'),
    };
    // Nameplate fields come from unit.nameplate_fields, not unit.fields
    const np = unit.nameplate_fields ?? {};
    const npPrefill = {
      'e-unpl1':    np['LINE.1'] ?? '',
      'e-unpl2':    np['LINE.2'] ?? '',
      'e-unpl3':    np['LINE.3'] ?? '',
      'e-unpl4':    np['LINE.4'] ?? '',
      'e-unpqty':   np.QTY    ?? '',
      'e-unpsz':    np.SIZE   ?? '',
      'e-unpstyle': np.STYLE  ?? '',
    };
    for (const [id, val] of Object.entries(prefill)) {
      const el = document.getElementById(id);
      if (el && val) el.value = val;
    }
    for (const [id, val] of Object.entries(npPrefill)) {
      const el = document.getElementById(id);
      if (el && val) el.value = val;
    }

    // Show/hide dual vs standard fields on type change
    function editConstraints() {
      const typeEl    = document.getElementById('e-utype');
      const modsEl    = document.getElementById('e-umods');
      const hintEl    = document.getElementById('e-umods-min-hint');
      const dualEl    = document.getElementById('e-dual-fields');
      const customEl  = document.getElementById('e-custom-fields');
      const detailsEl = document.getElementById('e-details-block');
      if (!typeEl || !modsEl) return;
      const isDualNow   = typeEl.value === 'DUAL_FEEDER';
      const isCustomNow = typeEl.value === 'CUSTOM';
      if (dualEl)    dualEl.style.display    = isDualNow   ? '' : 'none';
      if (customEl)  customEl.style.display  = isCustomNow ? '' : 'none';
      if (detailsEl) detailsEl.style.display = isDualNow   ? 'none' : '';
      const minMod = MIN_MOD[typeEl.value] ?? 0;
      if (minMod > 0) {
        for (const opt of modsEl.options) opt.disabled = parseFloat(opt.value) < minMod;
        if (parseFloat(modsEl.value) < minMod) {
          for (const opt of modsEl.options) { if (!opt.disabled) { modsEl.value = opt.value; break; } }
        }
        if (hintEl) hintEl.textContent = `(min ${minMod} mod for ${typeEl.value})`;
      } else {
        for (const opt of modsEl.options) opt.disabled = false;
        if (hintEl) hintEl.textContent = '';
      }
    }
    document.getElementById('e-utype').addEventListener('change', editConstraints);
    document.getElementById('e-umods').addEventListener('change', editConstraints);

    document.getElementById('e-unit-cancel').addEventListener('click', hideRp);
    document.getElementById('e-unit-ok').addEventListener('click', async () => {
      const newType    = document.getElementById('e-utype').value;
      const newMods    = parseFloat(document.getElementById('e-umods').value);
      const isDualNow   = newType === 'DUAL_FEEDER';
      const isCustomNow = newType === 'CUSTOM';
      const tag = (document.getElementById('e-tag')?.value ?? '').trim();

      const params = {
        project_id:   _projectId,
        unit_no:      unit.unit_no,
        mod_height:   newMods,
        starter_type: newType,
        tag,
        ...(isDualNow ? {
          left_amp:  (document.getElementById('e-left-amp')?.value  ?? '').trim(),
          right_amp: (document.getElementById('e-right-amp')?.value ?? '').trim(),
        } : isCustomNow ? {
          custom_width: parseInt(document.getElementById('e-custom-width')?.value ?? '500', 10),
          ..._collectDetailFields('e'),
        } : _collectDetailFields('e')),
      };

      setBusy(true);
      try {
        const res = await exec('edit_unit', params);
        const resEl = document.getElementById('e-unit-res');
        if (res.success) {
          resEl.className = 'mcc-form-result mcc-ok';
          resEl.textContent = `✓ ${unit.unit_no} updated.`;
          await refreshState();
        } else {
          resEl.className = 'mcc-form-result mcc-err';
          resEl.textContent = _friendlyError(res.error ?? 'Unknown error');
        }
      } catch (e) {
        document.getElementById('e-unit-res').className = 'mcc-form-result mcc-err';
        document.getElementById('e-unit-res').textContent = '✗ ' + e.message;
      } finally { setBusy(false); }
    });
  }

  function openAddSectionForm() {
    if (!_projectId) { promptNewProject(); return; }
    showRp('Add Section', `
      <div class="mcc-form">
        <div class="mcc-form-row">
          <label>Section ID</label>
          <input id="f-sid" class="form-input" placeholder="F1" maxlength="10" />
        </div>
        <div class="mcc-form-row">
          <label>Width</label>
          <select id="f-sw" class="form-input">
            <option value="500" selected>500 mm</option>
            <option value="600">600 mm</option>
            <option value="800">800 mm</option>
            <option value="1000">1000 mm</option>
          </select>
        </div>
        <div class="mcc-form-actions">
          <button class="btn-primary" id="f-sec-ok">Insert into AutoCAD</button>
          <button class="btn-sm"      id="f-sec-cancel">Cancel</button>
        </div>
        <div class="mcc-form-result" id="f-sec-res"></div>
      </div>
    `);
    document.getElementById('f-sec-cancel').addEventListener('click', hideRp);
    document.getElementById('f-sec-ok').addEventListener('click', async () => {
      const sid = document.getElementById('f-sid').value.trim().toUpperCase();
      const sw  = parseInt(document.getElementById('f-sw').value);
      if (!sid) { document.getElementById('f-sec-res').textContent = 'Section ID required.'; return; }
      setBusy(true);
      try {
        const res = await exec('add_section', {
          project_id: _projectId, section_id: sid, section_width: sw,
        });
        if (res.success) {
          document.getElementById('f-sec-res').className = 'mcc-form-result mcc-ok';
          document.getElementById('f-sec-res').textContent = `✓ Section ${sid} inserted.`;
          await refreshState();
        } else {
          document.getElementById('f-sec-res').className = 'mcc-form-result mcc-err';
          document.getElementById('f-sec-res').textContent = '✗ ' + (res.error ?? 'Unknown error');
        }
      } catch (e) {
        document.getElementById('f-sec-res').className = 'mcc-form-result mcc-err';
        document.getElementById('f-sec-res').textContent = '✗ ' + e.message;
      } finally { setBusy(false); }
    });
  }

  function openBulkAddForm() {
    if (!_projectId) { promptNewProject(); return; }
    showRp('Bulk Add Starters', `
      <div class="mcc-form">
        <div class="mcc-form-hint">Auto-creates sections and fills them with identical starters.</div>

        <div class="mcc-form-row">
          <label>Quantity</label>
          <input id="b-count" class="form-input" type="number" min="1" value="12" />
        </div>
        <div class="mcc-form-row">
          <label>Starter Type</label>
          <select id="b-utype" class="form-input">${_STARTER_OPTS('FVNR')}</select>
        </div>
        <div class="mcc-form-row">
          <label>Mod Height <span id="b-umods-hint" style="font-size:0.7rem;color:var(--text-muted)"></span></label>
          <select id="b-umods" class="form-input">${_MOD_OPTS(4)}</select>
        </div>
        <div class="mcc-form-row">
          <label>Section Prefix</label>
          <input id="b-prefix" class="form-input" value="F" maxlength="5" placeholder="e.g. F" />
        </div>
        <div class="mcc-form-row">
          <label>Section Width</label>
          <select id="b-sw" class="form-input">
            <option value="500" selected>500 mm</option>
            <option value="600">600 mm</option>
            <option value="800">800 mm</option>
            <option value="1000">1000 mm</option>
          </select>
        </div>
        <div class="mcc-form-row">
          <label>Starting Section # <small style="color:var(--text-muted)">(leave blank = auto)</small></label>
          <input id="b-startnum" class="form-input" type="number" min="1" placeholder="auto" />
        </div>

        <details class="mcc-form-details">
          <summary>Details (optional — applied to all starters)</summary>
          ${_DETAIL_FIELDS('b')}
        </details>

        <div class="mcc-form-actions">
          <button class="btn-primary" id="b-ok">Insert into AutoCAD</button>
          <button class="btn-sm"      id="b-cancel">Cancel</button>
        </div>
        <div class="mcc-form-result" id="b-res"></div>
      </div>
    `);

    // Enforce min-mod per type
    function bulkConstraints() {
      const typeEl = document.getElementById('b-utype');
      const modsEl = document.getElementById('b-umods');
      const hintEl = document.getElementById('b-umods-hint');
      if (!typeEl || !modsEl) return;
      const minMod = MIN_MOD[typeEl.value] ?? 0;
      const cur    = parseFloat(modsEl.value);
      if (minMod > 0) {
        for (const opt of modsEl.options) opt.disabled = parseFloat(opt.value) < minMod;
        if (cur < minMod) {
          for (const opt of modsEl.options) { if (!opt.disabled) { modsEl.value = opt.value; break; } }
        }
        if (hintEl) hintEl.textContent = `(min ${minMod} mod for ${typeEl.value})`;
      } else {
        for (const opt of modsEl.options) opt.disabled = false;
        if (hintEl) hintEl.textContent = '';
      }
    }
    document.getElementById('b-utype').addEventListener('change', bulkConstraints);
    document.getElementById('b-umods').addEventListener('change', bulkConstraints);

    document.getElementById('b-cancel').addEventListener('click', hideRp);
    document.getElementById('b-ok').addEventListener('click', async () => {
      const count  = parseInt(document.getElementById('b-count').value, 10);
      const utype  = document.getElementById('b-utype').value;
      const umods  = parseFloat(document.getElementById('b-umods').value);
      const prefix = document.getElementById('b-prefix').value.trim().toUpperCase() || 'F';
      const sw     = parseInt(document.getElementById('b-sw').value, 10);
      const startRaw = document.getElementById('b-startnum').value.trim();
      const startNum = startRaw ? parseInt(startRaw, 10) : undefined;
      const resEl  = document.getElementById('b-res');

      if (!count || count < 1) { resEl.className='mcc-form-result mcc-err'; resEl.textContent='✗ Quantity must be ≥ 1.'; return; }

      setBusy(true);
      try {
        const params = {
          project_id:    _projectId,
          count,
          starter_type:  utype,
          mod_height:    umods,
          section_prefix: prefix,
          section_width:  sw,
          ..._collectDetailFields('b'),
        };
        if (startNum) params.starting_section_number = startNum;

        const res = await exec('bulk_add_units', params);
        if (res.success || res.total_added > 0) {
          resEl.className = 'mcc-form-result mcc-ok';
          const secList = res.sections_created?.join(', ') || 'existing sections';
          resEl.textContent = `✓ ${res.total_added} starters added` +
            (res.sections_created?.length ? ` (new: ${secList})` : '') +
            (res.errors?.length ? ` · ${res.errors.length} error(s)` : '') + '.';
          await refreshState();
        } else {
          resEl.className = 'mcc-form-result mcc-err';
          resEl.textContent = _friendlyError(res.error ?? res.errors?.[0] ?? 'Failed.');
        }
      } catch (e) {
        resEl.className = 'mcc-form-result mcc-err';
        resEl.textContent = _friendlyError(e.message);
      } finally { setBusy(false); }
    });
  }

  function openAddUnitForm(secId) {
    if (!_projectId) { promptNewProject(); return; }

    const sec = _projectState?.sections?.[secId];
    const remaining = sec?.remaining_mods ?? '?';

    // Auto-suggest next unit number
    const existingNos = (sec?.units ?? []).map(u => u.unit_no);
    const lastNo = existingNos[existingNos.length - 1] ?? (secId + '@');
    const nextChar = String.fromCharCode(lastNo.charCodeAt(lastNo.length - 1) + 1);
    const suggestedNo = secId + nextChar;

    showRp(`Add Unit → ${secId}`, `
      <div class="mcc-form">
        <div class="mcc-form-hint">${remaining} mod available in ${secId}</div>

        <div class="mcc-form-row">
          <label>Unit No</label>
          <input id="f-uno" class="form-input" value="${suggestedNo}" />
        </div>
        <div class="mcc-form-row">
          <label>Starter Type</label>
          <select id="f-utype" class="form-input">
            ${_STARTER_OPTS()}
          </select>
        </div>
        <div class="mcc-form-row">
          <label>Tag <span style="font-size:0.7rem;color:var(--text-muted)">(shown in MCC_LAYOUT block)</span></label>
          <input id="f-tag" class="form-input" placeholder="e.g. FVNR-1 (leave blank = type name)" />
        </div>
        <div class="mcc-form-row">
          <label>Mod Height <span id="f-umods-min-hint" style="font-size:0.7rem;color:var(--text-muted)"></span></label>
          <select id="f-umods" class="form-input">
            ${_MOD_OPTS(4)}
          </select>
        </div>

        <!-- CUSTOM unit width — shown only when CUSTOM type selected -->
        <div id="f-custom-fields" style="display:none">
          <div class="mcc-form-row">
            <label>Unit Width</label>
            <select id="f-custom-width" class="form-input">
              <option value="400">400 mm (with 100mm wireway)</option>
              <option value="500">500 mm (full width)</option>
              <option value="600">600 mm (full width)</option>
              <option value="800">800 mm (full width)</option>
            </select>
          </div>
        </div>

        <!-- Dual feeder amperage fields — shown only when DUAL_FEEDER is selected -->
        <div id="f-dual-fields" style="display:none">
          <div class="mcc-form-row">
            <label>Left Feeder Amps</label>
            <input id="f-left-amp" class="form-input" placeholder="e.g. 100" />
          </div>
          <div class="mcc-form-row">
            <label>Right Feeder Amps</label>
            <input id="f-right-amp" class="form-input" placeholder="e.g. 100" />
          </div>
        </div>

        <details class="mcc-form-details" id="f-details-block">
          <summary>Details (optional)</summary>
          ${_DETAIL_FIELDS('f')}
        </details>

        <div class="mcc-form-actions">
          <button class="btn-primary" id="f-unit-ok">Insert into AutoCAD</button>
          <button class="btn-sm"      id="f-unit-cancel">Cancel</button>
        </div>
        <div class="mcc-form-result" id="f-unit-res"></div>
      </div>
    `);

    // ── Enforce minimums and type restrictions ─────────────────────────────
    // Called on BOTH type-change AND mod-height-change so each stays consistent.
    function applyConstraints() {
      const typeEl     = document.getElementById('f-utype');
      const modsEl     = document.getElementById('f-umods');
      const hintEl     = document.getElementById('f-umods-min-hint');
      const dualEl     = document.getElementById('f-dual-fields');
      const detailsEl  = document.getElementById('f-details-block');
      const customEl   = document.getElementById('f-custom-fields');
      if (!typeEl || !modsEl) return;

      const currentMods = parseFloat(modsEl.value);
      const currentType = typeEl.value;
      const isDual      = currentType === 'DUAL_FEEDER';
      const isCustom    = currentType === 'CUSTOM';

      if (dualEl)   dualEl.style.display   = isDual   ? '' : 'none';
      if (customEl) customEl.style.display  = isCustom ? '' : 'none';
      if (detailsEl) detailsEl.style.display = isDual  ? 'none' : '';

      // 3-mod rule: standard starters need ≥ 3 mod.
      // VFD, SS, CUSTOM, SPACE have blocks at all sizes and are always enabled.
      const _FREE_SIZE_TYPES = new Set(['VFD','VVVF','SS','SOFTSTART','CUSTOM','SPACE']);
      const tooSmallForStarter = currentMods < 3;
      for (const opt of typeEl.options) {
        opt.disabled = tooSmallForStarter && !_FREE_SIZE_TYPES.has(opt.value);
      }
      if (tooSmallForStarter && !_FREE_SIZE_TYPES.has(currentType)) {
        typeEl.value = 'SPACE';
      }

      // Min-mod rule per type (from MIN_MOD map)
      const minMod = MIN_MOD[typeEl.value] ?? 0;
      let hint = '';
      if (minMod > 0) {
        for (const opt of modsEl.options) {
          opt.disabled = parseFloat(opt.value) < minMod;
        }
        if (currentMods < minMod) {
          for (const opt of modsEl.options) {
            if (parseFloat(opt.value) >= minMod) { modsEl.value = opt.value; break; }
          }
        }
        hint = `(min ${minMod} mod for ${typeEl.value})`;
      } else {
        for (const opt of modsEl.options) {
          // Re-enable anything disabled only by the min-mod rule
          // (keep disabled if blocked by the 3-mod rule above)
          if (!tooSmallForStarter || opt.value === 'SPACE') opt.disabled = false;
        }
      }
      if (hintEl) hintEl.textContent = hint;
    }
    document.getElementById('f-utype').addEventListener('change', applyConstraints);
    document.getElementById('f-umods').addEventListener('change', applyConstraints);
    applyConstraints();   // run once immediately on form open

    document.getElementById('f-unit-cancel').addEventListener('click', hideRp);
    document.getElementById('f-unit-ok').addEventListener('click', async () => {
      const uno   = document.getElementById('f-uno').value.trim().toUpperCase();
      const utype = document.getElementById('f-utype').value;
      const umods = parseFloat(document.getElementById('f-umods').value);

      // Guard: enforce 3-mod rule for standard starters.
      // VFD, SS, and CUSTOM have blocks at all sizes so they bypass this check.
      const _FREE_SIZE = ['VFD','VVVF','SS','SOFTSTART','CUSTOM','SPACE'];
      if (umods < 3 && !_FREE_SIZE.includes(utype)) {
        const resEl = document.getElementById('f-unit-res');
        resEl.className = 'mcc-form-result mcc-err';
        resEl.textContent = `✗ Units under 3 mod must be type SPACE (or VFD / SS / CUSTOM).`;
        return;
      }
      const minRequired = MIN_MOD[utype] ?? 0;
      if (umods < minRequired) {
        const resEl = document.getElementById('f-unit-res');
        resEl.className = 'mcc-form-result mcc-err';
        resEl.textContent = `✗ ${utype} requires a minimum of ${minRequired} mod.`;
        return;
      }
      // Unit number required for all types except SPACE (spacers can be anonymous)
      if (!uno && utype !== 'SPACE') {
        document.getElementById('f-unit-res').textContent = 'Unit number required.';
        return;
      }
      setBusy(true);
      try {
        const isDualSubmit   = utype === 'DUAL_FEEDER';
        const isCustomSubmit = utype === 'CUSTOM';
        const tag = (document.getElementById('f-tag')?.value ?? '').trim();
        const params = {
          project_id:   _projectId,
          section_id:   secId,
          unit_no:      uno,
          mod_height:   umods,
          starter_type: utype,
          ...(tag ? { tag } : {}),
          ...(isDualSubmit ? {
            left_amp:  (document.getElementById('f-left-amp')?.value  ?? '').trim(),
            right_amp: (document.getElementById('f-right-amp')?.value ?? '').trim(),
          } : isCustomSubmit ? {
            custom_width: parseInt(document.getElementById('f-custom-width')?.value ?? '500', 10),
            ..._collectDetailFields('f'),
          } : _collectDetailFields('f')),
        };
        const res = await exec('add_unit', params);
        const resEl = document.getElementById('f-unit-res');
        if (res.success) {
          resEl.className = 'mcc-form-result mcc-ok';
          resEl.textContent = `✓ ${uno || '(space)'} inserted.`;
          await refreshState();
          // Suggest next unit number based on refreshed state
          const updatedSec = _projectState?.sections?.[secId];
          const units = (updatedSec?.units ?? []).filter(u => !u.unit_no?.startsWith('_SPACE_') && u.unit_no);
          const lastNo = units[units.length - 1]?.unit_no ?? (secId + '@');
          const nextNo = secId + String.fromCharCode(lastNo.charCodeAt(lastNo.length - 1) + 1);
          const unoEl = document.getElementById('f-uno');
          if (unoEl) unoEl.value = nextNo;
          _clearDetailFields('f');
        } else {
          resEl.className = 'mcc-form-result mcc-err';
          resEl.textContent = _friendlyError(res.error ?? 'Unknown error');
        }
      } catch (e) {
        document.getElementById('f-unit-res').className = 'mcc-form-result mcc-err';
        document.getElementById('f-unit-res').textContent = '✗ ' + e.message;
      } finally { setBusy(false); }
    });
  }

  // ── Drawing assignment helpers ─────────────────────────────────────────────
  // Build <option> elements from a list of {name, full_path} drawing objects.
  function _dwgOptions(drawings, selectedName = '') {
    const blank = `<option value="">— select drawing —</option>`;
    const opts  = drawings.map(d => {
      const sel = d.name === selectedName ? ' selected' : '';
      return `<option value="${d.name}"${sel}>${d.name}</option>`;
    }).join('');
    return blank + opts;
  }

  // Render 4 drawing-assignment rows into a container element.
  // roles: [{key, label}], drawings: [{name, full_path}], currentMap: {}
  function _renderDwgAssign(container, drawings, currentMap = {}) {
    const roles = [
      { key: 'layout',       label: 'Layout sheet'       },
      { key: 'unitdata',     label: 'Unit data sheet'    },
      { key: 'nameplate',    label: 'Nameplate sheet'    },
      { key: 'general_data', label: 'General data sheet (optional)' },
    ];
    container.innerHTML = roles.map(r => `
      <div class="mcc-form-row">
        <label>${r.label}</label>
        <select id="f-dwg-${r.key}" class="form-input" style="width:100%">
          ${_dwgOptions(drawings, currentMap[r.key] || '')}
        </select>
      </div>
    `).join('');
  }

  // Collect dwg_map from the 4 selects; returns null + shows error if required roles blank.
  // layout/unitdata/nameplate are required; general_data is optional (can be left unset).
  function _collectDwgMap(errEl) {
    const required = ['layout', 'unitdata', 'nameplate'];
    const optional = ['general_data'];
    const map = {};
    for (const r of required) {
      const val = document.getElementById(`f-dwg-${r}`)?.value || '';
      if (!val) {
        if (errEl) { errEl.className = 'mcc-form-result mcc-err'; errEl.textContent = `Please assign a drawing for "${r}".`; }
        return null;
      }
      map[r] = val;
    }
    for (const r of optional) {
      const val = document.getElementById(`f-dwg-${r}`)?.value || '';
      if (val) map[r] = val;  // only include if selected
    }
    return map;
  }

  // ── New Project dialog ─────────────────────────────────────────────────────
  async function promptNewProject() {
    // Fetch open drawings first so we can populate dropdowns immediately.
    let drawings = [];
    try {
      const r = await exec('list_open_drawings', {});
      if (r.success) drawings = r.drawings || [];
    } catch (_) {}

    const noDrawingsHint = drawings.length === 0
      ? `<p class="mcc-form-hint" style="color:var(--color-error,#c00)">
           ⚠ No drawings found open in AutoCAD.  Open your drawings first, then click ↻ Refresh.
         </p>`
      : `<p class="mcc-form-hint">
           Select which open drawing corresponds to each sheet.
           Click ↻ Refresh if you just opened a drawing.
         </p>`;

    showRp('New MCC Project', `
      <div class="mcc-form">
        <div class="mcc-form-section-label">Project</div>
        <div class="mcc-form-row">
          <label>Project Name</label>
          <input id="f-pname" class="form-input" placeholder="e.g. Plant A MCC-1" />
        </div>

        <div class="mcc-form-section-label" style="display:flex;align-items:center;gap:6px">
          Drawing Assignment
          <button class="btn-sm" id="f-np-refresh" title="Refresh list of open drawings" style="padding:1px 6px;font-size:0.7rem">↻ Refresh</button>
        </div>
        ${noDrawingsHint}
        <div id="f-dwg-assign-rows"></div>

        <div class="mcc-form-section-label">Layout sheet coordinates</div>
        <div class="mcc-form-row">
          <label>First Section Origin X</label>
          <input id="f-ox" class="form-input" value="95" type="number" step="0.5" />
        </div>
        <div class="mcc-form-row">
          <label>First Section Origin Y</label>
          <input id="f-oy" class="form-input" value="120" type="number" step="0.5" />
        </div>
        <div class="mcc-form-row">
          <label>First Unit Y (top of first unit slot)</label>
          <input id="f-fuy" class="form-input" value="224" type="number" step="0.5" />
        </div>

        <div class="mcc-form-section-label">Unit data sheet coordinates</div>
        <p class="mcc-form-hint" style="font-size:0.7rem">
          In AutoCAD, hover over the first empty UDATALIN row to get these coordinates.
        </p>
        <div class="mcc-form-row">
          <label>First Row X</label>
          <input id="f-udx" class="form-input" value="20" type="number" step="0.5" />
        </div>
        <div class="mcc-form-row">
          <label>First Row Y</label>
          <input id="f-udy" class="form-input" value="230" type="number" step="0.5" />
        </div>

        <div class="mcc-form-actions">
          <button class="btn-primary" id="f-np-ok">Create Project</button>
          <button class="btn-sm"      id="f-np-cancel">Cancel</button>
        </div>
        <div class="mcc-form-result" id="f-np-res"></div>
      </div>
    `);

    // Populate drawing dropdowns
    const assignRows = document.getElementById('f-dwg-assign-rows');
    _renderDwgAssign(assignRows, drawings);

    // Refresh button re-fetches open drawings without closing the panel
    document.getElementById('f-np-refresh').addEventListener('click', async () => {
      try {
        const r = await exec('list_open_drawings', {});
        if (r.success) {
          drawings = r.drawings || [];
          _renderDwgAssign(assignRows, drawings);
        }
      } catch (_) {}
    });

    document.getElementById('f-np-cancel').addEventListener('click', hideRp);
    document.getElementById('f-np-ok').addEventListener('click', doNewProject);
  }

  async function doNewProject() {
    const errEl = document.getElementById('f-np-res');
    const pname = (document.getElementById('f-pname')?.value ?? '').trim();
    const ox  = parseFloat(document.getElementById('f-ox')?.value  ?? '95');
    const oy  = parseFloat(document.getElementById('f-oy')?.value  ?? '120');
    const fuy = parseFloat(document.getElementById('f-fuy')?.value ?? '224');
    const udx = parseFloat(document.getElementById('f-udx')?.value ?? '20');
    const udy = parseFloat(document.getElementById('f-udy')?.value ?? '230');

    const dwg_map = _collectDwgMap(errEl);
    if (!dwg_map) return;   // validation error already shown

    setBusy(true);
    try {
      const res = await exec('new_mcc_project', {
        layout_origin_x:  ox,
        layout_origin_y:  oy,
        first_unit_y:     fuy,
        unitdata_row_x:   udx,
        unitdata_row_y:   udy,
        project_name:     pname,
        dwg_map,
      });
      if (res.success) {
        _projectId = res.project_id;
        _projectName = res.project_name || res.project_id;
        await refreshProjects();
        await refreshState();
        hideRp();
        const label = _projectName !== res.project_id ? `"${_projectName}" (${res.project_id})` : res.project_id;
        setStatus(`Project ${label} ready. Add a section to start.`);
      } else {
        if (errEl) { errEl.className = 'mcc-form-result mcc-err'; errEl.textContent = _friendlyError(res.error); }
      }
    } catch (e) {
      if (errEl) { errEl.className = 'mcc-form-result mcc-err'; errEl.textContent = _friendlyError(e.message); }
    } finally { setBusy(false); }
  }

  // ── Reassign Drawings dialog ───────────────────────────────────────────────
  async function promptReassignDrawings() {
    if (!_projectId) { setStatus('No active project — create or load one first.', true); return; }

    // Fetch current map + open drawings in parallel
    let drawings = [];
    let currentMap = {};
    try {
      const [drawRes, stateRes] = await Promise.all([
        exec('list_open_drawings', {}),
        exec('list_projects', {}),
      ]);
      if (drawRes.success) drawings = drawRes.drawings || [];
      // Pull the dwg_map from the active project in the list
      if (stateRes.success) {
        const proj = (stateRes.projects || []).find(p => p.project_id === _projectId);
        if (proj) currentMap = proj.dwg_map || {};
      }
    } catch (_) {}

    showRp('Reassign Drawings', `
      <div class="mcc-form">
        <p class="mcc-form-hint">
          Select which drawing currently open in AutoCAD corresponds to each sheet.
          Click ↻ Refresh if you just opened a drawing.
        </p>

        <div class="mcc-form-section-label" style="display:flex;align-items:center;gap:6px">
          Drawing Assignment
          <button class="btn-sm" id="f-ra-refresh" style="padding:1px 6px;font-size:0.7rem">↻ Refresh</button>
        </div>
        <div id="f-ra-assign-rows"></div>

        <div class="mcc-form-actions">
          <button class="btn-primary" id="f-ra-ok">Save Assignments</button>
          <button class="btn-sm"      id="f-ra-cancel">Cancel</button>
        </div>
        <div class="mcc-form-result" id="f-ra-res"></div>
      </div>
    `);

    const assignRows = document.getElementById('f-ra-assign-rows');
    _renderDwgAssign(assignRows, drawings, currentMap);

    document.getElementById('f-ra-refresh').addEventListener('click', async () => {
      try {
        const r = await exec('list_open_drawings', {});
        if (r.success) { drawings = r.drawings || []; _renderDwgAssign(assignRows, drawings, currentMap); }
      } catch (_) {}
    });

    document.getElementById('f-ra-cancel').addEventListener('click', hideRp);
    document.getElementById('f-ra-ok').addEventListener('click', async () => {
      const errEl = document.getElementById('f-ra-res');
      const map   = _collectDwgMap(errEl);
      if (!map) return;

      setBusy(true);
      try {
        // Reassign each role sequentially
        const roles = ['layout', 'unitdata', 'nameplate', 'general_data'];
        for (const role of roles) {
          const res = await exec('reassign_drawing', {
            project_id: _projectId,
            role,
            dwg_name: map[role],
          });
          if (!res.success) {
            if (errEl) { errEl.className = 'mcc-form-result mcc-err'; errEl.textContent = `${role}: ${_friendlyError(res.error)}`; }
            return;
          }
        }
        hideRp();
        setStatus(`Drawing assignments updated for project "${_projectName || _projectId}".`);
      } catch (e) {
        if (errEl) { errEl.className = 'mcc-form-result mcc-err'; errEl.textContent = _friendlyError(e.message); }
      } finally { setBusy(false); }
    });
  }

  // ── Save / load project ────────────────────────────────────────────────────
  async function doSaveProject() {
    if (!_projectId) { setStatus('No active project to save.', true); return; }
    setBusy(true);
    try {
      const res = await exec('save_project', { project_id: _projectId });
      if (res.success) {
        const fname = res.filepath ?? 'file';
        setStatus(`Saved → ${fname}`);
      } else {
        setStatus('✗ ' + res.error, true);
      }
    } catch (e) { setStatus('✗ ' + e.message, true); }
    finally { setBusy(false); }
  }

  async function promptLoadProject() {
    showRp('Load Saved Project', '<div class="mcc-form"><p class="mcc-form-hint">Scanning saved projects…</p></div>');
    setBusy(true);
    let projects = [];
    try {
      const res = await exec('list_saved_projects', {});
      projects = res.projects ?? [];
    } catch (e) {
      showRp('Load Saved Project', `<div class="mcc-form"><p class="mcc-form-result mcc-err">✗ ${e.message}</p></div>`);
      setBusy(false);
      return;
    } finally { setBusy(false); }

    if (projects.length === 0) {
      showRp('Load Saved Project', `
        <div class="mcc-form">
          <p class="mcc-form-hint">No saved project files found in the <b>projects/</b> folder.</p>
          <p class="mcc-form-hint">Save a project first using the <b>Save</b> toolbar button.</p>
        </div>`);
      return;
    }

    const rows = projects.map((p, i) => {
      const name    = p.project_name || p.project_id;
      const subline = `${p.total_sections} section(s) · ${p.total_units} unit(s)`;
      const saved   = p.saved_at ? new Date(p.saved_at).toLocaleString() : '';
      return `
        <div class="mcc-load-row" data-idx="${i}" style="
          display:flex;align-items:center;gap:10px;padding:8px;
          border-bottom:1px solid var(--border,#333);cursor:pointer;border-radius:4px;">
          <div style="flex:1;min-width:0">
            <div style="font-weight:600;font-size:0.85rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${name}</div>
            <div style="font-size:0.72rem;color:var(--text-secondary,#888)">${subline}${saved ? ' · ' + saved : ''}</div>
            <div style="font-size:0.68rem;color:var(--text-secondary,#888);opacity:0.6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${p.filepath}</div>
          </div>
          <button class="btn-primary btn-load-file" data-filepath="${p.filepath}" data-name="${name}" style="flex-shrink:0;font-size:0.75rem;padding:4px 10px">Load</button>
        </div>`;
    }).join('');

    showRp('Load Saved Project', `
      <div class="mcc-form">
        <p class="mcc-form-hint">Select a project to load into AutoCAD. The three MCC drawing files must be open.</p>
        <div id="lp-list" style="max-height:55vh;overflow-y:auto">${rows}</div>
        <div class="mcc-form-result" id="lp-res"></div>
      </div>`);

    document.querySelectorAll('.btn-load-file').forEach(btn => {
      btn.addEventListener('click', async () => {
        const filepath = btn.dataset.filepath;
        const name     = btn.dataset.name;
        setBusy(true);
        try {
          const res = await exec('load_project', { filepath });
          const resEl = document.getElementById('lp-res');
          if (res.success) {
            _projectId   = res.project_id;
            _projectName = res.project_name || name;
            await refreshProjects();
            await refreshState();
            hideRp();
            const label = _projectName !== _projectId ? `"${_projectName}" [${_projectId}]` : _projectId;
            setStatus(`Loaded ${label} · ${res.sections?.length ?? 0} section(s)`);
          } else if (resEl) {
            resEl.className = 'mcc-form-result mcc-err';
            resEl.textContent = '✗ ' + _friendlyError(res.error);
          }
        } catch (e) {
          const resEl = document.getElementById('lp-res');
          if (resEl) { resEl.className = 'mcc-form-result mcc-err'; resEl.textContent = '✗ ' + e.message; }
        } finally { setBusy(false); }
      });
    });
  }

  // ── Project selector ───────────────────────────────────────────────────────
  async function refreshProjects() {
    try {
      const res = await exec('list_projects');
      const sel = document.getElementById('mcc-project-select');
      if (!sel) return;
      const prev = sel.value;
      sel.innerHTML = '<option value="">— select project —</option>';
      for (const p of (res.projects ?? [])) {
        const opt = document.createElement('option');
        opt.value       = p.project_id;
        const label = p.project_name ? `${p.project_name}  [${p.project_id}]` : p.project_id;
        opt.textContent = `${label}  (${p.total_sections}S / ${p.total_units}U)`;
        sel.appendChild(opt);
      }
      if (_projectId) sel.value = _projectId;
      else if (prev) sel.value = prev;
    } catch (_) { /* no projects yet */ }
  }

  async function refreshState() {
    if (!_projectId) { renderDiagram(null); return; }
    try {
      const res = await exec('get_project_state', { project_id: _projectId });
      if (res.success) {
        _projectState = res;
        renderDiagram(res);
        const nameLabel = _projectName && _projectName !== _projectId
          ? `"${_projectName}" [${_projectId}]`
          : _projectId;
        setStatus(`${nameLabel} · ${res.total_sections} section(s) · ${res.total_units} unit(s)`);
      } else {
        setStatus('Error loading state: ' + res.error, true);
      }
    } catch (e) { setStatus('Error: ' + e.message, true); }
    await refreshProjects();
  }

  // ── Busy state ─────────────────────────────────────────────────────────────
  function setBusy(busy) {
    _busy = busy;
    document.getElementById('mcc-canvas')?.classList.toggle('mcc-canvas-busy', busy);
  }

  // ── General Data form ──────────────────────────────────────────────────────
  //
  // Maps attdef name → [label, placeholder]
  const GD_FIELDS = {
    // Power
    VOLT:          ['Voltage',              'e.g. 600'],
    PHASE:         ['Phase',               'e.g. 3'],
    WIRES:         ['Wires',               'e.g. 4'],
    FREQ:          ['Frequency',           'e.g. 60 Hz'],
    // General options
    WIRING:        ['Wiring (EEMAC)',       'e.g. MODIFIED'],
    ENCLOSURE:     ['Enclosure (custom)',   'e.g. SPRINKLERPROOF, WEATHERPROOF'],
    FINISH:        ['Finish',              'e.g. SAND ENAMEL HS544H75'],
    ARRANGEMENT:   ['Arrangement',         ''],
    TERMINALBOARD: ['Master Terminal Brd', 'e.g. BUS LINKS'],
    // Main device (GENRA001B)
    MAIN_KA:       ['Master Terminal Brd Line 2', ''],
    // Cable 1
    CABLE1_INC:    ['Cable 1 — Incl.',    'X'],
    CABLE1_QTY:    ['Cable 1 — Qty',      '1'],
    CABLE1_SIZE:   ['Cable 1 — Size',     'e.g. 3/C #8'],
    // Neutral cable
    NEUTRAL_INC:   ['Neutral — Incl.',    'X'],
    NEUTRAL_QTY:   ['Neutral — Qty',      '1'],
    NEUTRAL_SIZE:  ['Neutral — Size',     'e.g. #8'],
    // Cable 2
    CABLE2_INC:    ['Cable 2 — Incl.',    'X'],
    CABLE2_QTY:    ['Cable 2 — Qty',      '1'],
    CABLE2_SIZE:   ['Cable 2 — Size',     'e.g. 3/C #8'],
    // Horizontal bus
    HORIZ_AMPS:    ['Horiz. Bus Amps',    'e.g. 600'],
    HORIZ_BUSSES:  ['Horiz. Busses',      'e.g. 1'],
    HORIZ_FIRST:   ['Horiz. Bus 1st',     '1/4"'],
    HORIZ_SECOND:  ['Horiz. Bus 2nd',     '1 1/2"'],
    // Neutral bus
    NEUT_AMPS:     ['Neutral Bus Amps',   'e.g. 300'],
    NEUT_FIRST:    ['Neutral Bus 1st',    ''],
    NEUT_SECOND:   ['Neutral Bus 2nd',    ''],
    // Vertical bus
    VERT_AMPS:     ['Vertical Bus Amps',  'e.g. 440'],
    VERT_FIRST:    ['Vert. Bus 1st',      ''],
    VERT_SECOND:   ['Vert. Bus 2nd',      ''],
    // OS / outgoing section
    OS_AMPS:       ['O/S Bus Amps',       ''],
    OS_FIRST:      ['O/S Bus 1st',        ''],
    OS_SECOND:     ['O/S Bus 2nd',        ''],
    // Ground bus
    GROUND_FIRST:  ['Ground Bus 1st',     ''],
    GROUND_SECOND: ['Ground Bus 2nd',     ''],
    // Protection
    KA:            ['Disconnect kA',      'e.g. 42'],
    MOTOR_FUSE:    ['Motor Fuse Class',   'e.g. J(T)'],
    FEEDER_FUSE:   ['Feeder Fuse Class',  'e.g. J(T)'],
    // Control
    CVOLT:         ['Control Voltage',    'e.g. 120V'],
    SS2_SEL:       ['2-Pos Sel. Legend',  'e.g. HAND/AUTO'],
    SS3_SEL:       ['3-Pos Sel. Legend',  'e.g. OFF/AUTO/HAND'],
    FV_PILOT:      ['FV Pilot Voltage',   'e.g. 120'],
    FV_PILOT_24V:  ['FV Pilot 24V Alt.',  'e.g. 120'],
    PTT_PILOT:     ['PTT Pilot Voltage',  'e.g. 120'],
    // Labels
    NAMEPLATE:     ['Nameplates',         ''],
    WIREMARKERS:   ['Wiremarkers',        ''],
    TERMINAL:      ['Terminal Type',      ''],
  };

  // Checkbox groups: [group label, [[id, short label], ...]]
  const GD_CB_GROUPS = [
    ['EEMAC Wiring', [
      ['EEMAC_IA',       'Class IA'],
      ['EEMAC_IB',       'Class IB'],
      ['EEMAC_IC',       'Class IC'],
      ['EEMAC_IIB',      'Class IIB'],
      ['EEMAC_IIC',      'Class IIC'],
      ['EEMAC_MODIFIED', 'Modified'],
    ]],
    ['Enclosure (EEMAC)', [
      ['ENCL_1',         'Type 1'],
      ['ENCL_1A',        'Type 1A'],
      ['ENCL_12',        'Type 12'],
      ['ENCL_2',         'Type 2'],
      ['ENCL_SPRINKLER', 'Custom'],   // custom type — text value in Enclosure field below
    ]],
    ['Enclosure Finish', [
      ['FINISH_ASA61GREY',   'ASA 61 Grey'],
      ['FINISH_SAND_ENAMEL', 'Sand Enamel HS544H75'],
    ]],
    ['Arrangement', [
      ['ARRANGE_FOB',    'FOB'],
      ['ARRANGE_BTB',    'BTB'],
      ['ARRANGE_CUSTOM', 'Custom'],
    ]],
    ['Master Terminal Board', [
      ['TERMBD_TOP',    'Top'],
      ['TERMBD_BOT',    'Bottom'],
      ['TERMBD_CUSTOM', 'Custom'],   // custom location — type value in Master Terminal Brd field below
    ]],
    ['Main Lug / Breaker', [
      ['MAIN_LUG',     'Main Lug'],
      ['MAIN_BREAKER', 'Main Breaker'],
      ['MAIN_CUSTOM',  'Custom'],
    ]],
    ['System ISC Rating', [
      ['ISC_18KA', '18 kA'],
      ['ISC_22KA', '22 kA'],
      ['ISC_25KA', '25 kA'],
      ['ISC_35KA', '35 kA'],
      ['ISC_42KA', '42 kA'],
      ['ISC_50KA', '50 kA'],
      ['ISC_65KA', '65 kA'],
    ]],
    ['CSA C22.2 Label', [
      ['CSA_STRUCT_UNIT',  'Structural Unit'],
      ['CSA_WHERE_APPLIC', 'Where Applicable'],
      ['CSA_SPECIAL',      'Special'],
    ]],
    ['Cable 1 Entry', [
      ['CABLE1_TOP',   'Top'],
      ['CABLE1_BOT',   'Bottom'],
    ]],
    ['Neutral Cable Entry', [
      ['NEUTRAL_TOP',  'Top'],
      ['NEUTRAL_BOT',  'Bottom'],
    ]],
    ['Cable 2 Entry', [
      ['CABLE2_TOP',   'Top'],
      ['CABLE2_BOT',   'Bottom'],
    ]],
    ['Busduct', [
      ['BUSDUCT_TOP',  'Top'],
      ['BUSDUCT_BOT',  'Bottom'],
      ['BUSDUCT_3W',   '3-Wire'],
      ['BUSDUCT_4W',   '4-Wire'],
    ]],
    ['Busbar Bracing', [
      ['BRACE_22KA',   '22 kA'],
      ['BRACE_42KA',   '42 kA'],
      ['BRACE_65KA',   '65 kA'],
    ]],
    ['Horizontal Bus Material', [
      ['HORIZ_AL',       'Aluminum'],
      ['HORIZ_CU',       'Copper'],
      ['PLATING_TIN',    'Tin plated'],
      ['PLATING_SILVER', 'Silver plated'],
    ]],
    ['Insulated Bus', [
      ['INSBUS_HORIZ',  'Horizontal'],
      ['INSBUS_VERT',   'Vertical'],
    ]],
    ['Ground Bus Location', [
      ['GROUND_TOP',   'Top'],
      ['GROUND_BOT',   'Bottom'],
      ['GROUND_VERT',  'Vertical'],
    ]],
    ['Disconnect Device', [
      ['DISC_FUSIBLE',  'Fusible switch'],
      ['DISC_BREAKER',  'Breaker'],
      ['DISC_KA',       'kA rated'],
    ]],
    ['Motor Fuse Type', [
      ['MFUSE_FR2_C',   'FR2 / Cl. C'],
      ['MFUSE_FRI_J',   'FR. I Cl. J'],
      ['MFUSE_FRI_JT',  'FR. I Cl. J(T)'],
    ]],
    ['Feeder Fuse Type', [
      ['FFUSE_FR2_C',   'FR2 / Cl. C'],
      ['FFUSE_FRI_J',   'FR. I Cl. J'],
      ['FFUSE_FRI_JT',  'FR. I Cl. J(T)'],
    ]],
    ['Supply Fuses', [
      ['SUPPLY_ALL',      'All fuses'],
      ['SUPPLY_FITTINGS', 'With fittings'],
    ]],
    ['Control Circuit Voltage', [
      ['CTRL_120V',  '120 V'],
      ['CTRL_240V',  '240 V'],
      ['CTRL_CVOLT', 'Custom'],
    ]],
    ['Control Circuit Supply', [
      ['CSUPPLY_INDIV',    'Individual'],
      ['CSUPPLY_GROUP',    'Group'],
      ['CSUPPLY_LINE',     'Line'],
      ['CSUPPLY_SEPARATE', 'Separate'],
    ]],
    ['Nameplates', [
      ['NP_ENGLISH', 'English'],
      ['NP_FRENCH',  'French'],
      ['NP_CUSTOM',  'Custom'],
    ]],
    ['Wiremarker Type', [
      ['WMT_ZMARKERS',   'Z-markers'],
      ['WMT_HEATSHRINK', 'Heat shrink'],
      ['WMT_CUSTOM',     'Custom'],
    ]],
    ['Wiremarkers — Scope', [
      ['WM_UNIT_CTRL',  'Unit control'],
      ['WM_UNIT_PWR',   'Unit power'],
      ['WM_MTB_CTRL',   'MTB control'],
      ['WM_MTB_PWR',    'MTB power'],
      ['WM_INTERWIRING','Inter-wiring'],
    ]],
    ['Selector Switch 2-Position', [
      ['SS2_MAINTAINED', 'Maintained'],
      ['SS2_SPRING',     'Spring return'],
    ]],
    ['Selector Switch 3-Position', [
      ['SS3_MAINTAINED', 'Maintained'],
      ['SS3_SPRING',     'Spring return'],
    ]],
    ['Pilot Light — FV', [
      ['PL_120VAC',    '120 VAC'],
      ['PL_24VAC',     '24 VAC (alt)'],
    ]],
    ['Pilot Light — FV PTT', [
      ['PL_PTT_120VAC', '120 VAC'],
      ['PL_PTT_24VAC',  '24 VAC'],
    ]],
    ['Terminal Type', [
      ['TERM_8WH1',   '8WH1'],
      ['TERM_CF4_10', 'CF4-10'],
      ['TERM_CUSTOM', 'Custom'],
    ]],
  ];

  // Field groups for the text attdef section
  const GD_FIELD_GROUPS = [
    ['Power Supply',     ['VOLT','PHASE','WIRES','FREQ']],
    ['General Options',  ['WIRING','ENCLOSURE','FINISH','ARRANGEMENT','TERMINALBOARD','MAIN_KA']],
    ['Cable 1',          ['CABLE1_INC','CABLE1_QTY','CABLE1_SIZE']],
    ['Neutral Cable',    ['NEUTRAL_INC','NEUTRAL_QTY','NEUTRAL_SIZE']],
    ['Cable 2',          ['CABLE2_INC','CABLE2_QTY','CABLE2_SIZE']],
    ['Horizontal Bus',   ['HORIZ_AMPS','HORIZ_BUSSES','HORIZ_FIRST','HORIZ_SECOND']],
    ['Neutral Bus',      ['NEUT_AMPS','NEUT_FIRST','NEUT_SECOND']],
    ['Vertical Bus',     ['VERT_AMPS','VERT_FIRST','VERT_SECOND']],
    ['O/S Bus',          ['OS_AMPS','OS_FIRST','OS_SECOND']],
    ['Ground Bus',       ['GROUND_FIRST','GROUND_SECOND']],
    ['Protection',       ['KA','MOTOR_FUSE','FEEDER_FUSE']],
    ['Control Circuit',  ['CVOLT','SS2_SEL','SS3_SEL','FV_PILOT','FV_PILOT_24V','PTT_PILOT']],
    ['Labels',           ['NAMEPLATE','WIREMARKERS','TERMINAL']],
  ];

  function _gdFieldHtml() {
    return GD_FIELD_GROUPS.map(([grpLabel, keys]) => `
      <div class="mcc-detail-group-hdr">${grpLabel}</div>
      ${keys.map(k => {
        const [label, ph] = GD_FIELDS[k] ?? [k, ''];
        return `<div class="mcc-form-row">
          <label>${label}</label>
          <input id="gd-f-${k}" class="form-input" placeholder="${ph}" />
        </div>`;
      }).join('')}
    `).join('');
  }

  function _gdCheckboxHtml(checkedSet) {
    return GD_CB_GROUPS.map(([grpLabel, items]) => `
      <div class="mcc-detail-group-hdr" style="margin-top:8px">${grpLabel}</div>
      <div class="gd-cb-group">
        ${items.map(([id, lbl]) => `
          <label class="gd-cb-label">
            <input type="checkbox" class="gd-cb" data-cbid="${id}"
                   ${checkedSet.has(id) ? 'checked' : ''} />
            ${lbl}
          </label>`).join('')}
      </div>
    `).join('');
  }

  async function openGeneralDataForm() {
    showRp('General Data Sheet', `
      <div class="mcc-form">
        <div class="mcc-form-hint" id="gd-hint">
          Loading from AutoCAD… (General_Data Sheet.dwg must be open)
        </div>
        <div id="gd-body"></div>
      </div>
    `);

    // Try to read current values
    let currentFields = {};
    let currentChecked = new Set();
    try {
      setBusy(true);
      const res = await exec('get_general_data', { project_id: _projectId ?? undefined });
      if (res.success === false) {
        document.getElementById('gd-hint').className = 'mcc-form-result mcc-err';
        document.getElementById('gd-hint').textContent = '✗ ' + (res.error ?? 'Failed to read General Data block.');
        setBusy(false);
        return;
      }
      currentFields  = res.fields   ?? {};
      currentChecked = new Set(res.checked_boxes ?? []);
      document.getElementById('gd-hint').textContent =
        'Edit fields and checkboxes, then click Write to AutoCAD.';
    } catch (e) {
      // If AutoCAD / drawing not open, show a warning but still let the user fill in the form
      document.getElementById('gd-hint').className = 'mcc-form-result mcc-err';
      document.getElementById('gd-hint').textContent =
        '⚠ Could not read current values: ' + e.message +
        '. Fill in the form and write when AutoCAD is ready.';
    } finally { setBusy(false); }

    // Build the form body
    document.getElementById('gd-body').innerHTML = `
      <details class="mcc-form-details" open>
        <summary>Text Fields</summary>
        ${_gdFieldHtml()}
      </details>

      <details class="mcc-form-details" open>
        <summary>Checkboxes</summary>
        <div id="gd-cb-container">
          ${_gdCheckboxHtml(currentChecked)}
        </div>
      </details>

      <div class="mcc-form-actions">
        <button class="btn-primary" id="gd-write">Write to AutoCAD</button>
        <button class="btn-sm"      id="gd-cancel">Cancel</button>
      </div>
      <div class="mcc-form-result" id="gd-res"></div>
    `;

    // Pre-fill text fields
    for (const [key] of Object.entries(GD_FIELDS)) {
      const el = document.getElementById(`gd-f-${key}`);
      if (el && currentFields[key]) el.value = currentFields[key];
    }

    document.getElementById('gd-cancel').addEventListener('click', hideRp);
    document.getElementById('gd-write').addEventListener('click', async () => {
      const resEl = document.getElementById('gd-res');
      resEl.className = 'mcc-form-result';
      resEl.textContent = '';

      // Collect text fields (only non-empty)
      const fields = {};
      for (const [key] of Object.entries(GD_FIELDS)) {
        const el = document.getElementById(`gd-f-${key}`);
        if (el && el.value.trim()) fields[key] = el.value.trim();
      }

      // Collect checkboxes (complete desired state)
      const checkboxes = [];
      document.querySelectorAll('.gd-cb').forEach(cb => {
        if (cb.checked) checkboxes.push(cb.dataset.cbid);
      });

      setBusy(true);
      try {
        const res = await exec('set_general_data', {
          fields:     Object.keys(fields).length     ? fields     : null,
          checkboxes: checkboxes,
          project_id: _projectId ?? undefined,
        });
        if (res.success) {
          resEl.className = 'mcc-form-result mcc-ok';
          const cbMsg = res.checked_added?.length || res.checked_removed?.length
            ? ` (${res.checked_added?.length ?? 0} checked, ${res.checked_removed?.length ?? 0} cleared)`
            : '';
          resEl.textContent = '✓ General Data block updated.' + cbMsg;
        } else {
          resEl.className = 'mcc-form-result mcc-err';
          resEl.textContent = '✗ ' + _friendlyError(res.error ?? 'Write failed.');
        }
      } catch (e) {
        resEl.className = 'mcc-form-result mcc-err';
        resEl.textContent = '✗ ' + _friendlyError(e.message);
      } finally { setBusy(false); }
    });
  }

  // ── Title Block form ───────────────────────────────────────────────────────
  // Field definitions: [key, label, placeholder]
  const TB_FIELDS = [
    ['DATE',         'Date Drawn',           'e.g. 08.19.26'],
    ['CUSTOMER_1',   'Customer (line 1)',     'Company name'],
    ['CUSTOMER_2',   'Customer (line 2)',     'e.g. division / address'],
    ['ORDER_NO',     'Order No.',             'e.g. N/A'],
    ['BY',           'Drawn By',              'Initials'],
    ['DRAWING_NO',   'Drawing No.',           'e.g. 8PX3-SAMPLE-U001'],
    ['PROJECT_1',    'Title (line 1)',        'e.g. MCC UNIT DATA'],
    ['PROJECT_2',    'Title (line 2)',        'e.g. SAMPLE MCC'],
    ['REV_NO',       'Current Rev.',          'e.g. A'],
  ];
  const TB_REV_ROWS = [
    { prefix: 'REV_A', label: 'Rev A' },
    { prefix: 'REV_B', label: 'Rev B' },
    { prefix: 'REV_C', label: 'Rev C' },
  ];

  async function openTitleblockForm() {
    if (!_projectId) { setStatus('No active project — create or load one first.', true); return; }

    const tbRow = ([key, label, ph]) =>
      `<div class="mcc-form-row">
         <label>${label}</label>
         <input id="tb-${key}" class="form-input" placeholder="${ph}" />
       </div>`;

    const revSection = TB_REV_ROWS.map(({ prefix, label }) => `
      <div class="mcc-form-row" style="align-items:flex-start">
        <label style="padding-top:4px">${label}</label>
        <div style="flex:1;display:grid;grid-template-columns:1fr 1fr 1fr 60px;gap:4px">
          <input id="tb-${prefix}_DESC"   class="form-input" placeholder="Description" />
          <input id="tb-${prefix}_BY"     class="form-input" placeholder="By" />
          <input id="tb-${prefix}_DATE"   class="form-input" placeholder="Date" />
          <input id="tb-${prefix}_LETTER" class="form-input" placeholder="Ltr" />
        </div>
      </div>`).join('');

    showRp('Title Block', `
      <div class="mcc-form">
        <p class="mcc-form-hint">
          Edit the <b>TITLE3</b> block. Check which drawings to apply changes to,
          then click "Read from AutoCAD" to load current values, edit, and "Write to AutoCAD".
        </p>

        <div class="mcc-form-section-label">Apply to drawings</div>
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px">
          <label style="display:flex;align-items:center;gap:4px;font-size:0.8rem">
            <input type="checkbox" id="tb-tgt-layout"       checked /> Layout
          </label>
          <label style="display:flex;align-items:center;gap:4px;font-size:0.8rem">
            <input type="checkbox" id="tb-tgt-unitdata"     checked /> Unit Data
          </label>
          <label style="display:flex;align-items:center;gap:4px;font-size:0.8rem">
            <input type="checkbox" id="tb-tgt-nameplate"    checked /> Nameplate
          </label>
          <label style="display:flex;align-items:center;gap:4px;font-size:0.8rem">
            <input type="checkbox" id="tb-tgt-general_data"        /> General Data
          </label>
        </div>

        <div class="mcc-form-section-label">Fields</div>
        ${TB_FIELDS.map(tbRow).join('')}

        <div class="mcc-form-section-label">
          Revision History
          <span style="font-size:0.65rem;font-weight:normal;margin-left:6px;opacity:.7">
            Desc · By · Date · Letter
          </span>
        </div>
        ${revSection}

        <div class="mcc-form-actions" style="flex-wrap:wrap;gap:6px">
          <button class="btn-sm"      id="tb-read">↓ Read from AutoCAD</button>
          <button class="btn-primary" id="tb-write">↑ Write to AutoCAD</button>
          <button class="btn-sm"      id="tb-cancel">Cancel</button>
        </div>
        <p class="mcc-form-hint" style="font-size:0.65rem;margin-top:6px">
          ⚠ Attribute index order is approximate. Run
          <code>python scripts/dump_title3.py</code> to verify exact indices if
          values appear in wrong fields.
        </p>
        <div class="mcc-form-result" id="tb-res"></div>
      </div>
    `);

    document.getElementById('tb-cancel').addEventListener('click', hideRp);

    // Helper: collect all field values into a fields dict
    function collectTbFields() {
      const fields = {};
      for (const [key] of TB_FIELDS) {
        const v = document.getElementById(`tb-${key}`)?.value ?? '';
        if (v) fields[key] = v;
      }
      for (const { prefix } of TB_REV_ROWS) {
        for (const suf of ['DESC', 'BY', 'DATE', 'LETTER']) {
          const v = document.getElementById(`tb-${prefix}_${suf}`)?.value ?? '';
          if (v) fields[`${prefix}_${suf}`] = v;
        }
      }
      return fields;
    }

    // Helper: populate form from fields dict
    function populateTbFields(fields) {
      for (const [key] of TB_FIELDS) {
        const el = document.getElementById(`tb-${key}`);
        if (el) el.value = fields[key] ?? '';
      }
      for (const { prefix } of TB_REV_ROWS) {
        for (const suf of ['DESC', 'BY', 'DATE', 'LETTER']) {
          const el = document.getElementById(`tb-${prefix}_${suf}`);
          if (el) el.value = fields[`${prefix}_${suf}`] ?? '';
        }
      }
    }

    // Collect target roles from checkboxes
    function collectTargets() {
      return ['layout', 'unitdata', 'nameplate', 'general_data']
        .filter(r => document.getElementById(`tb-tgt-${r}`)?.checked);
    }

    // Read button — load current values from the first checked drawing
    document.getElementById('tb-read').addEventListener('click', async () => {
      const resEl = document.getElementById('tb-res');
      const targets = collectTargets();
      const readRole = targets[0] ?? 'layout';
      setBusy(true);
      try {
        const res = await exec('get_titleblock', { project_id: _projectId, role: readRole });
        if (res.success) {
          populateTbFields(res.fields);
          resEl.className = 'mcc-form-result mcc-ok';
          resEl.textContent = `✓ Loaded from ${res.doc}`;
        } else {
          resEl.className = 'mcc-form-result mcc-err';
          resEl.textContent = '✗ ' + _friendlyError(res.error);
        }
      } catch (e) {
        resEl.className = 'mcc-form-result mcc-err';
        resEl.textContent = '✗ ' + _friendlyError(e.message);
      } finally { setBusy(false); }
    });

    // Write button
    document.getElementById('tb-write').addEventListener('click', async () => {
      const resEl  = document.getElementById('tb-res');
      const fields  = collectTbFields();
      const targets = collectTargets();
      if (!targets.length) {
        resEl.className = 'mcc-form-result mcc-err';
        resEl.textContent = 'Select at least one drawing to apply changes to.';
        return;
      }
      if (!Object.keys(fields).length) {
        resEl.className = 'mcc-form-result mcc-err';
        resEl.textContent = 'No fields to write — fill in at least one value.';
        return;
      }
      setBusy(true);
      try {
        const res = await exec('set_titleblock', { fields, project_id: _projectId, targets });
        if (res.success) {
          resEl.className = 'mcc-form-result mcc-ok';
          const updated = (res.updated ?? []).join(', ');
          const skipped = (res.skipped ?? []).join('; ');
          resEl.textContent = `✓ Updated: ${updated || '—'}` + (skipped ? `  |  Skipped: ${skipped}` : '');
        } else {
          resEl.className = 'mcc-form-result mcc-err';
          const errs = (res.errors ?? []).join('; ') || res.error || 'Write failed.';
          resEl.textContent = '✗ ' + _friendlyError(errs);
        }
      } catch (e) {
        resEl.className = 'mcc-form-result mcc-err';
        resEl.textContent = '✗ ' + _friendlyError(e.message);
      } finally { setBusy(false); }
    });
  }

  // ── Init ────────────────────────────────────────────────────────────────────
  function init() {
    // Toolbar buttons
    document.getElementById('btn-new-mcc-project')
            ?.addEventListener('click', promptNewProject);
    document.getElementById('btn-add-mcc-section')
            ?.addEventListener('click', openAddSectionForm);
    document.getElementById('btn-bulk-add-units')
            ?.addEventListener('click', openBulkAddForm);
    document.getElementById('btn-general-data')
            ?.addEventListener('click', openGeneralDataForm);
    document.getElementById('btn-titleblock')
            ?.addEventListener('click', openTitleblockForm);
    document.getElementById('btn-refresh-mcc')
            ?.addEventListener('click', refreshState);
    document.getElementById('btn-save-mcc')
            ?.addEventListener('click', doSaveProject);
    document.getElementById('btn-load-mcc')
            ?.addEventListener('click', promptLoadProject);
    document.getElementById('btn-reassign-dwg')
            ?.addEventListener('click', promptReassignDrawings);

    // Project selector
    document.getElementById('mcc-project-select')?.addEventListener('change', async e => {
      _projectId = e.target.value || null;
      if (_projectId) {
        // Pick up the name from the option label (stored between the brackets)
        const opt = e.target.selectedOptions[0];
        const m = opt?.textContent?.match(/^(.+?)\s+\[/);
        _projectName = m ? m[1].trim() : _projectId;
        await refreshState();
      } else {
        _projectId = null; _projectName = null;
        _projectState = null; renderDiagram(null); setStatus('Select a project.');
      }
    });

    // Close right panel
    document.getElementById('mcc-rp-close')?.addEventListener('click', hideRp);

    // Refresh when switching to MCC tab
    const mccNavBtn = document.querySelector('.nav-btn[data-panel="mcc"]');
    if (mccNavBtn) {
      mccNavBtn.addEventListener('click', async () => {
        await refreshProjects();
        if (!_projectId) {
          // auto-select if there's only one project
          const sel = document.getElementById('mcc-project-select');
          if (sel && sel.options.length === 2) {
            sel.selectedIndex = 1;
            _projectId = sel.value;
          }
        }
        if (_projectId) await refreshState();
        else { renderDiagram(null); setStatus('Click New Project to start.'); }
      });
    }
  }

  // Script loads at end of <body> so DOM is already ready — call init() directly.
  // DOMContentLoaded would never fire here since it already fired before this script loaded.
  init();

  // Export for debugging / AI chat integration
  window._mcc = { refreshState, refreshProjects, exec };
})();
