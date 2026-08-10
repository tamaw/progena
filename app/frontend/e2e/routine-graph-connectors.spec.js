import { test, expect } from '@playwright/test';

/**
 * Routine graph connectors.
 *
 * Every rail, stem and stub in a fork / for-each scope is a CSS
 * pseudo-element positioned from fixed offsets — deliberately, so the wiring
 * reflows with the layout instead of being measured in JS. The cost is that
 * nothing in the component tree can tell whether the pieces still meet: a
 * one-pixel offset in a `top` or a `margin` silently disconnects a line, and
 * unit tests never see it because jsdom has no layout.
 *
 * These read the rendered geometry back and pin the invariants that have
 * actually regressed: segments meeting end to end, everything sharing the
 * scope's centre axis, symmetric fan-out/fan-in clearance, and a graph that
 * only ever overflows rightward.
 *
 * Requires the running stack (see playwright.config.js).
 */

// Sub-1/64px. Anything larger is a real offset, not LayoutUnit rounding.
const AXIS_EPSILON = 0.02;
// Segments may overlap freely; they may never leave a gap.
const GAP_EPSILON = 0.02;

/** Runs in the page: absolute geometry of every scope box and its wiring. */
function readGraph() {
  const px = (v) => parseFloat(v) || 0;

  /** Absolute box of a pseudo-element, resolved against its host's padding box. */
  const pseudo = (host, which) => {
    if (!host) return null;
    const s = getComputedStyle(host, which);
    if (s.content === 'none' || s.display === 'none') return null;
    const b = host.getBoundingClientRect();
    const cs = getComputedStyle(host);
    const padTop = b.y + px(cs.borderTopWidth);
    const padHeight = b.height - px(cs.borderTopWidth) - px(cs.borderBottomWidth);
    const tf = new DOMMatrixReadOnly(s.transform === 'none' ? '' : s.transform);
    const height = px(s.height);
    // `bottom`-anchored pseudo-elements (the fan-in rail and stub) have `top: auto`.
    const top = s.top !== 'auto' ? padTop + px(s.top) : padTop + padHeight - px(s.bottom) - height;
    const padLeft = b.x + px(cs.borderLeftWidth);
    const padWidth = b.width - px(cs.borderLeftWidth) - px(cs.borderRightWidth);
    // The rails are `left`+`right` anchored and have no width of their own.
    const left = s.left !== 'auto' ? padLeft + px(s.left) : padLeft + padWidth - px(s.right) - px(s.width);
    const right = s.right !== 'auto' ? padLeft + padWidth - px(s.right) : left + px(s.width);
    return {
      left: left + tf.m41,
      right: right + tf.m41,
      cx: (left + right) / 2 + tf.m41,
      top: top + tf.m42,
      bottom: top + tf.m42 + height,
    };
  };

  const box = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    return { cx: b.x + b.width / 2, top: b.top, bottom: b.bottom, width: b.width };
  };

  const scopes = [];
  for (const [selector, bodyClass] of [
    ['.routine-foreach', 'routine-foreach__body'],
    ['.routine-fork', 'routine-fork__branches'],
  ]) {
    const base = selector.slice(1);
    for (const scope of document.querySelectorAll(selector)) {
      const head = scope.querySelector(`:scope > .${base}__head`);
      const body = scope.querySelector(`:scope > .${bodyClass}`);
      if (!head || !body) continue;

      const card = head.querySelector('.canvas-node') || head.firstElementChild;
      const prev = scope.previousElementSibling;
      const incoming = prev && prev.classList.contains('routine-connector')
        ? prev.querySelector(':scope > .routine-connector__line')
        : null;
      const entry = pseudo(scope, '::before');
      const headToBox = pseudo(body, '::before');
      const inner = pseudo(body, '::after');

      const lanes = [];
      for (const lane of body.querySelectorAll(':scope > .routine-fork__lanes > .routine-fork__lane')) {
        const label = lane.querySelector(':scope > .routine-fork__header')
          || lane.querySelector(':scope > .routine-fork__add-slot');
        const wire = lane.querySelector(':scope > .routine-fork__wire');
        const firstConnector = label && label.nextElementSibling;
        const axis = [];
        const push = (name, value) => { if (value != null) axis.push({ name, cx: value }); };
        push('fanOutStub', pseudo(lane, '::before')?.cx);
        for (const [i, line] of [...lane.querySelectorAll(':scope > .routine-connector > .routine-connector__line')].entries()) {
          push(`line${i}`, box(line).cx);
        }
        for (const [i, btn] of [...lane.querySelectorAll(':scope > .routine-connector .routine-connector__btn')].entries()) {
          push(`plus${i}`, box(btn).cx);
        }
        const fansIn = lane.classList.contains('routine-fork__lane--fanin');
        if (fansIn) push('fanInStub', pseudo(lane, '::after')?.cx);

        lanes.push({
          name: label ? label.textContent.trim().replace(/[▾▸×]/g, '').trim() || '(add)' : '(unlabelled)',
          collapsed: lane.classList.contains('routine-fork__lane--collapsed'),
          fansIn,
          cx: box(lane).cx,
          bottom: box(lane).bottom,
          label: box(label),
          labelLine: firstConnector && firstConnector.classList.contains('routine-connector')
            ? box(firstConnector.querySelector('.routine-connector__line'))
            : null,
          fanOutStub: pseudo(lane, '::before'),
          fanOutRail: pseudo(wire, '::before'),
          fanInStub: fansIn ? pseudo(lane, '::after') : null,
          fanInRail: fansIn ? pseudo(wire, '::after') : null,
          axis,
        });
      }

      const joinEl = scope.querySelector(':scope > .routine-fork__join');
      const joinPlus = joinEl && joinEl.querySelector('.routine-connector__btn');
      const joinCard = joinEl && joinEl.querySelector('.canvas-node');
      const join = joinEl && scope.classList.contains('routine-fork--joined') && joinPlus && joinCard
        ? { stem: pseudo(joinEl, '::before'), plus: box(joinPlus), card: box(joinCard) }
        : null;

      const firstInBody = body.firstElementChild;
      scopes.push({
        kind: base,
        cx: box(scope).cx,
        incoming: incoming ? box(incoming) : null,
        entry,
        card: box(card),
        headToBox,
        inner,
        body: box(body),
        bodyFirstLine: firstInBody && firstInBody.classList.contains('routine-connector')
          ? box(firstInBody.querySelector('.routine-connector__line'))
          : null,
        lanes,
        join,
      });
    }
  }

  const chain = document.querySelector('.routine-chain');
  let leftmost = Infinity;
  for (const el of chain.querySelectorAll('*')) {
    const b = el.getBoundingClientRect();
    if (b.width || b.height) leftmost = Math.min(leftmost, b.x);
  }
  return { scopes, chainLeft: box(chain).top != null ? chain.getBoundingClientRect().x : 0, leftmost };
}

async function graphAt(page, routineId) {
  await page.goto(`/routines/${routineId}`);
  await expect(page.locator('.routine-chain')).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('.canvas-node').first()).toBeVisible({ timeout: 20_000 });
  return page.evaluate(readGraph);
}

/** `next` must start at or above where `prev` ends — overlap fine, gap never. */
function expectMeets(prev, next, what) {
  expect(prev, `${what}: missing start segment`).toBeTruthy();
  expect(next, `${what}: missing end segment`).toBeTruthy();
  expect(next.top - prev.bottom, `${what} leaves a ${(next.top - prev.bottom).toFixed(2)}px gap`)
    .toBeLessThanOrEqual(GAP_EPSILON);
}

test.describe('routine graph connectors', () => {
  // A fork with a real join, and a for-each whose body contains a whole fork.
  const ROUTINES = ['routine-branch-join', 'routine-all-controls'];

  for (const routineId of ROUTINES) {
    test(`${routineId}: every scope wires its card to its box without a gap`, async ({ page }) => {
      const { scopes } = await graphAt(page, routineId);
      expect(scopes.length).toBeGreaterThan(0);

      for (const scope of scopes) {
        const at = `${routineId} ${scope.kind}`;
        // The card sits above the box, not inside it.
        expect(scope.card.bottom, `${at}: card is not above its box`)
          .toBeLessThanOrEqual(scope.body.top);

        if (scope.incoming) expectMeets(scope.incoming, scope.entry, `${at} incoming -> entry stem`);
        expectMeets(scope.entry, scope.card, `${at} entry stem -> card`);
        expectMeets(scope.card, scope.headToBox, `${at} card -> box stem`);
        expectMeets(scope.headToBox, scope.inner, `${at} box stem -> inner stem`);
        // The stem must cross the border, not stop short of it.
        expect(scope.headToBox.bottom, `${at}: box stem stops short of the box`)
          .toBeGreaterThanOrEqual(scope.body.top);

        // The inner stem hands off to the fan-out rail, or to the first `+`.
        if (scope.lanes.length) {
          expectMeets(scope.inner, scope.lanes[0].fanOutRail, `${at} inner stem -> fan-out rail`);
        } else {
          expectMeets(scope.inner, scope.bodyFirstLine, `${at} inner stem -> first connector`);
        }
      }
    });

    test(`${routineId}: every segment shares its scope's centre axis`, async ({ page }) => {
      const { scopes } = await graphAt(page, routineId);

      for (const scope of scopes) {
        const at = `${routineId} ${scope.kind}`;
        for (const [name, part] of [
          ['entry stem', scope.entry], ['card', scope.card],
          ['box stem', scope.headToBox], ['inner stem', scope.inner],
        ]) {
          expect(Math.abs(part.cx - scope.cx), `${at} ${name} is ${(part.cx - scope.cx).toFixed(3)}px off axis`)
            .toBeLessThanOrEqual(AXIS_EPSILON);
        }
        // Within a lane, the stubs, connector lines and `+` buttons are one line.
        for (const lane of scope.lanes) {
          for (const { name, cx } of lane.axis) {
            expect(Math.abs(cx - lane.cx), `${at} lane "${lane.name}" ${name} is ${(cx - lane.cx).toFixed(3)}px off axis`)
              .toBeLessThanOrEqual(AXIS_EPSILON);
          }
        }
        if (scope.join) {
          expect(Math.abs(scope.join.plus.cx - scope.cx), `${at} join + is off axis`)
            .toBeLessThanOrEqual(AXIS_EPSILON);
        }
      }
    });

    test(`${routineId}: each rail reaches every lane it serves`, async ({ page }) => {
      const { scopes } = await graphAt(page, routineId);
      const forks = scopes.filter((s) => s.lanes.length);
      expect(forks.length, `${routineId} renders no fork`).toBeGreaterThan(0);

      // Each lane contributes one clipped segment of a shared rail. Nesting is
      // where this breaks: a lane can hold a whole fork, so a selector that is
      // not a direct-child combinator clips the inner fork's rails with the
      // outer lane's modifier and the rail collapses into disjoint stubs.
      for (const scope of forks) {
        for (const [rail, stub, serves] of [
          ['fanOutRail', 'fanOutStub', () => true],
          ['fanInRail', 'fanInStub', (l) => l.fansIn],
        ]) {
          const lanes = scope.lanes.filter((l) => l[rail]);
          if (!lanes.length) continue;
          const at = `${routineId} ${scope.kind} ${rail}`;

          const segments = lanes.map((l) => l[rail]).sort((a, b) => a.left - b.left);
          for (const [i, next] of segments.slice(1).entries()) {
            expect(next.left - segments[i].right, `${at} breaks into disjoint segments`)
              .toBeLessThanOrEqual(GAP_EPSILON);
          }

          // The rail must cover the full width of every stub hanging off it,
          // including the terminal lanes' — the ends are clipped to the outer
          // edge of the first and last stub, not to their centres.
          const spanLeft = Math.min(...segments.map((s) => s.left));
          const spanRight = Math.max(...segments.map((s) => s.right));
          for (const lane of scope.lanes.filter(serves)) {
            expect(lane[stub], `${at}: lane "${lane.name}" has no stub`).toBeTruthy();
            expect(spanLeft, `${at} starts right of lane "${lane.name}"`)
              .toBeLessThanOrEqual(lane[stub].left + GAP_EPSILON);
            expect(spanRight, `${at} ends left of lane "${lane.name}"`)
              .toBeGreaterThanOrEqual(lane[stub].right - GAP_EPSILON);
            expectMeets(
              rail === 'fanOutRail' ? lane[rail] : lane[stub],
              rail === 'fanOutRail' ? lane[stub] : lane[rail],
              `${at}: lane "${lane.name}" stub`,
            );
          }
        }
      }
    });
  }

  test('a fork nested in a branch lane keeps its own rails', async ({ page }) => {
    // Every gallery example has at most one router, so only a persisted routine
    // exercises nesting -- and nesting is what the rail selectors get wrong: a
    // lane holding a whole fork will clip the inner rails with the outer lane's
    // modifier unless every rule uses a direct-child combinator.
    await page.goto('/routines/repo-setup-worker');
    // `waitFor`, not `isVisible`: the latter samples immediately, so it would
    // skip or run depending on whether the graph had loaded yet.
    const rendered = await page.locator('.routine-fork').first()
      .waitFor({ state: 'visible', timeout: 15_000 })
      .then(() => true, () => false);
    test.skip(!rendered, 'repo-setup-worker is not provisioned in this environment');

    const { scopes } = await page.evaluate(readGraph);
    const forks = scopes.filter((s) => s.lanes.length);
    expect(forks.length, 'repo-setup-worker should render nested forks').toBeGreaterThan(1);

    for (const scope of forks) {
      const segments = scope.lanes.map((l) => l.fanOutRail).sort((a, b) => a.left - b.left);
      for (const [i, next] of segments.slice(1).entries()) {
        expect(next.left - segments[i].right, `a ${scope.lanes.length}-lane fan-out rail is disjoint`)
          .toBeLessThanOrEqual(GAP_EPSILON);
      }
      const spanLeft = Math.min(...segments.map((s) => s.left));
      const spanRight = Math.max(...segments.map((s) => s.right));
      for (const lane of scope.lanes) {
        expect(spanLeft).toBeLessThanOrEqual(lane.fanOutStub.left + GAP_EPSILON);
        expect(spanRight).toBeGreaterThanOrEqual(lane.fanOutStub.right - GAP_EPSILON);
      }
    }
  });

  test('fan-out clears the branch labels by the same margin the fan-in clears the lane', async ({ page }) => {
    const { scopes } = await graphAt(page, 'routine-branch-join');
    const fork = scopes.find((s) => s.kind === 'routine-fork' && s.lanes.some((l) => l.fansIn));
    expect(fork, 'routine-branch-join has no fan-in lane').toBeTruthy();

    // The label straddles the lane's top border, so clearance is measured from
    // the label; below there is no label, so it is measured from the border.
    const clearances = fork.lanes.map((lane) => ({
      name: lane.name,
      above: lane.label.top - lane.fanOutRail.bottom,
      below: lane.fansIn ? lane.fanInRail.top - lane.bottom : null,
    }));

    for (const { name, above } of clearances) {
      // Enough to read as a gap; the bug was 2px, which looked glued.
      expect(above, `lane "${name}" leaves only ${above.toFixed(2)}px under the fan-out rail`)
        .toBeGreaterThanOrEqual(6);
    }
    const below = clearances.filter((c) => c.below != null);
    expect(below.length).toBeGreaterThan(0);
    for (const c of below) {
      expect(Math.abs(c.above - c.below), `lane "${c.name}" clears ${c.above.toFixed(2)}px above but ${c.below.toFixed(2)}px below`)
        .toBeLessThanOrEqual(AXIS_EPSILON);
    }
  });

  test('the label stem reaches the label, and a collapsed lane draws none', async ({ page }) => {
    await page.goto('/routines/routine-branch-join');
    await expect(page.locator('.routine-fork').first()).toBeVisible({ timeout: 20_000 });

    const expanded = await page.evaluate(readGraph);
    const lanes = expanded.scopes.flatMap((s) => s.lanes).filter((l) => l.labelLine);
    expect(lanes.length).toBeGreaterThan(0);
    for (const lane of lanes) {
      // The stem is the connector's own line grown upward, so it must start at
      // or under the label rather than a fraction of a pixel below it.
      expect(lane.labelLine.top, `lane "${lane.name}" stem starts ${(lane.labelLine.top - lane.label.bottom).toFixed(2)}px below its label`)
        .toBeLessThanOrEqual(lane.label.bottom + GAP_EPSILON);
    }

    const before = expanded.scopes.flatMap((s) => s.lanes)[0];
    await page.locator('.routine-fork__lane .routine-fork__header-collapse').first().click();
    await page.waitForTimeout(400);
    const after = (await page.evaluate(readGraph)).scopes.flatMap((s) => s.lanes)[0];

    expect(after.collapsed, 'clicking the chevron did not collapse the lane').toBe(true);
    expect(after.labelLine, 'a collapsed lane still draws a stem below its label').toBeNull();
    // Collapsing must not move the rails: the lane keeps its slot.
    expect(Math.abs(after.cx - before.cx)).toBeLessThanOrEqual(AXIS_EPSILON);
  });

  test("the join's + sits evenly between the branch box and the join card", async ({ page }) => {
    const { scopes } = await graphAt(page, 'routine-branch-join');
    const fork = scopes.find((s) => s.join);
    expect(fork, 'routine-branch-join renders no join').toBeTruthy();

    const rail = fork.lanes.find((l) => l.fansIn).fanInRail;
    expectMeets(rail, fork.join.stem, 'fan-in rail -> join stem');
    expect(fork.join.stem.bottom, 'join stem stops short of the branch box')
      .toBeGreaterThanOrEqual(fork.body.bottom - GAP_EPSILON);

    const middle = (fork.join.plus.top + fork.join.plus.bottom) / 2;
    const above = middle - fork.body.bottom;
    const below = fork.join.card.top - middle;
    expect(Math.abs(above - below), `join + sits ${above.toFixed(2)}px below the box but ${below.toFixed(2)}px above the card`)
      .toBeLessThanOrEqual(0.5);
  });


  test("a fan_in join stays on the fork's own centre axis, not offset toward the real branches", async ({ page }) => {
    // Every node in the chain -- START, the router card, the join -- sits on
    // one shared vertical axis (margin: 0 auto against .routine-fork, the
    // same axis every other scope/segment check in this file pins). The
    // "+ Add …" trailer lanes make the branch row visually asymmetric, but
    // the join must NOT compensate for that by re-centring on just the real
    // branches -- that would pull it off the axis everything above and
    // below the fork shares.
    await page.goto('/routines/feedback-categoriser');
    const rendered = await page.locator('.routine-fork--joined').first()
      .waitFor({ state: 'visible', timeout: 15_000 })
      .then(() => true, () => false);
    test.skip(!rendered, 'feedback-categoriser is not provisioned in this environment');

    const geometry = await page.evaluate(() => {
      const fork = document.querySelector('.routine-fork--joined');
      const forkRect = fork.getBoundingClientRect();
      const join = fork.querySelector('.routine-fork__join').getBoundingClientRect();
      const start = document.querySelector('.routine-cap--start')?.getBoundingClientRect() || null;
      return {
        forkCentre: (forkRect.left + forkRect.right) / 2,
        joinCentre: (join.left + join.right) / 2,
        startCentre: start ? (start.left + start.right) / 2 : null,
      };
    });

    expect(
      Math.abs(geometry.joinCentre - geometry.forkCentre),
      `join centres at ${geometry.joinCentre.toFixed(1)}, the fork itself centres at ${geometry.forkCentre.toFixed(1)}`,
    ).toBeLessThanOrEqual(AXIS_EPSILON);
    if (geometry.startCentre != null) {
      expect(
        Math.abs(geometry.joinCentre - geometry.startCentre),
        `join centres at ${geometry.joinCentre.toFixed(1)}, off the chain's own START-anchored axis (${geometry.startCentre.toFixed(1)})`,
      ).toBeLessThanOrEqual(AXIS_EPSILON);
    }
  });

  test("a fan_in lane's rail never overhangs past its own outer edge on the fork's flanks", async ({ page }) => {
    // .routine-fork__lane--fanout-first/--fanout-last clip the shared bar to
    // each flank lane's own centre so it only ever draws toward its
    // neighbour, never past the fork's outer edge. A specificity mismatch
    // against the unclipped base rule (right/left: -11px on every lane) can
    // silently reinstate the overhang the clip was meant to remove.
    await page.goto('/routines/feedback-categoriser');
    const rendered = await page.locator('.routine-fork--joined').first()
      .waitFor({ state: 'visible', timeout: 15_000 })
      .then(() => true, () => false);
    test.skip(!rendered, 'feedback-categoriser is not provisioned in this environment');

    const geometry = await page.evaluate(() => {
      const first = document.querySelector('.routine-fork__lane--fanout-first');
      const wire = first.querySelector('.routine-fork__wire');
      const s = getComputedStyle(wire, '::after');
      const laneRect = first.getBoundingClientRect();
      const wireRect = wire.getBoundingClientRect();
      const railLeft = wireRect.x + (parseFloat(s.left) || 0);
      return { railLeft, laneLeft: laneRect.left, laneCx: laneRect.left + laneRect.width / 2 };
    });

    expect(
      geometry.railLeft,
      `first lane's rail starts at ${geometry.railLeft.toFixed(1)}, ${(geometry.laneCx - geometry.railLeft).toFixed(1)}px left of its own centre (${geometry.laneCx.toFixed(1)})`,
    ).toBeGreaterThanOrEqual(geometry.laneCx - AXIS_EPSILON - 1);
  });

  test('neither rail extends into an unclicked "+ Add …" slot, and the real lane still meets the join', async ({ page }) => {
    // "+ Add default" / "+ Add branch" are editor affordances a user hasn't
    // turned into a real branch yet -- neither the fan-out (top, router down
    // into each lane) nor the fan-in (bottom, lane up into the join) bar may
    // visually claim a connection to one. This is NOT done by reassigning
    // --fanout-last onto the real last lane (its clip formula, "stop at the
    // clipped lane's own centre", is calibrated for the add-slot's position,
    // not the real lane's -- moving it strands the join's stem short of the
    // bar). Add-slot lanes instead render no bar segment at all, and the
    // real last lane's own unclipped overhang -- unchanged -- keeps meeting
    // the join exactly as before.
    await page.goto('/routines/feedback-categoriser');
    const rendered = await page.locator('.routine-fork--joined').first()
      .waitFor({ state: 'visible', timeout: 15_000 })
      .then(() => true, () => false);
    test.skip(!rendered, 'feedback-categoriser is not provisioned in this environment');

    const result = await page.evaluate(() => {
      const fork = document.querySelector('.routine-fork--joined');
      const join = fork.querySelector('.routine-fork__join').getBoundingClientRect();
      const joinCx = join.left + join.width / 2;
      const lanes = [...fork.querySelectorAll(':scope > .routine-fork__branches > .routine-fork__lanes > .routine-fork__lane')];
      const addLanes = lanes.filter((l) => l.className.includes('--add'));
      const realLanes = lanes.filter((l) => !l.className.includes('--add'));
      const lastReal = realLanes.at(-1);
      const wire = lastReal.querySelector('.routine-fork__wire');
      const after = getComputedStyle(wire, '::after');
      const wireRect = wire.getBoundingClientRect();
      const railRight = wireRect.x + wireRect.width - (parseFloat(after.right) || 0);
      return {
        addLaneCount: addLanes.length,
        before: addLanes.map((l) => getComputedStyle(l.querySelector('.routine-fork__wire'), '::before').display),
        after: addLanes.map((l) => getComputedStyle(l.querySelector('.routine-fork__wire'), '::after').display),
        railRight,
        joinCx,
      };
    });

    expect(result.addLaneCount, 'expected at least one "+ Add …" slot').toBeGreaterThan(0);
    for (const display of result.before) {
      expect(display, 'an "+ Add …" slot rendered a fan-out bar segment').toBe('none');
    }
    for (const display of result.after) {
      expect(display, 'an "+ Add …" slot rendered a fan-in bar segment').toBe('none');
    }
    expect(
      result.railRight,
      `real last lane's rail ends at ${result.railRight.toFixed(1)}, ${(result.joinCx - result.railRight).toFixed(1)}px short of the join's centre (${result.joinCx.toFixed(1)})`,
    ).toBeCloseTo(result.joinCx, 0);
  });

  test('a graph wider than the canvas overflows rightward only', async ({ page }) => {
    // Nested scopes are what used to produce negative offsets, which no scroll
    // container can reach; 900px guarantees the chain is wider than the pane.
    await page.setViewportSize({ width: 900, height: 800 });
    const { leftmost, chainLeft } = await graphAt(page, 'routine-all-controls');

    expect(leftmost - chainLeft, `content starts ${(leftmost - chainLeft).toFixed(1)}px left of the chain`)
      .toBeGreaterThanOrEqual(-0.5);
  });
});
