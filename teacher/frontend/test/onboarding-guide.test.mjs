import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  ONBOARDING_DEFAULT_VERSION,
  ONBOARDING_ADVANCE_ON,
  ONBOARDING_GUIDE_CODES,
  ONBOARDING_GUIDE_CODE,
  ONBOARDING_GUIDE_ORDER,
  ONBOARDING_GUIDES,
  ONBOARDING_SPOTLIGHT_TARGETS,
  ONBOARDING_STEP_COUNT,
  ONBOARDING_STEP_ROUTES,
  ONBOARDING_STEPS,
  calculateCoachmarkPosition,
  calculateSpotlightRect,
  getOnboardingGuide,
  getOnboardingStepCount,
  getOnboardingStepRoutes,
  getOnboardingSteps,
  getOnboardingSpotlightTargets,
  initialOnboardingGuideState,
  normalizeOnboardingCatalog,
  normalizeOnboardingStatus,
  onboardingRouteIntent,
  onboardingEscapeAction,
  reduceOnboardingGuide,
  resolveOnboardingInteraction,
  shouldLoadOnboarding,
} from "../src/onboarding-guide.js";
import { sanitizeAnalyticsProperties } from "../src/analytics/sanitize.js";

const onboardingStepIndex = (stepId) => {
  const index = ONBOARDING_STEPS.findIndex((step) => step.id === stepId);
  assert.notEqual(index, -1, `Missing onboarding step: ${stepId}`);
  return index;
};

test("normalizes the first-login guide contract and rejects invalid versions", () => {
  assert.deepEqual(normalizeOnboardingStatus({
    required: true,
    guideCode: "UNTRUSTED_CODE",
    guideVersion: "3",
  }), {
    required: true,
    guideCode: ONBOARDING_GUIDE_CODE,
    guideVersion: 3,
  });
  assert.equal(
    normalizeOnboardingStatus({ required: true, guideVersion: "latest" }).guideVersion,
    ONBOARDING_DEFAULT_VERSION,
  );
  assert.equal(
    normalizeOnboardingStatus({ required: true, guideVersion: 0 }).guideVersion,
    ONBOARDING_DEFAULT_VERSION,
  );
  assert.equal(normalizeOnboardingStatus(null).required, false);
  assert.deepEqual(normalizeOnboardingStatus({
    required: true,
    guideCode: ONBOARDING_GUIDE_CODES.taskPath,
    guideVersion: 1,
  }), {
    required: true,
    guideCode: ONBOARDING_GUIDE_CODES.taskPath,
    guideVersion: 1,
  });
});

test("registers eight independently versioned progressive guides in stable order", () => {
  assert.deepEqual(ONBOARDING_GUIDE_ORDER, [
    "FIRST_LOGIN",
    "MY_TIDE_OVERVIEW",
    "SCORE_DETAILS",
    "TASK_PATH",
    "TASK_RESULT",
    "MESSAGES_TICKETS",
    "HELP_ROUTES",
    "PERSONALIZED_TASK_FIRST",
  ]);
  assert.equal(Object.isFrozen(ONBOARDING_GUIDES), true);

  const expectedSteps = {
    FIRST_LOGIN: [
      "global-navigation",
      "my-tide-overview",
      "messages-entry",
      "messages-tabs",
      "help-entry",
    ],
    MY_TIDE_OVERVIEW: [
      "my-tide-overview",
      "my-tide-recommendation",
      "my-tide-dimensions",
    ],
    SCORE_DETAILS: ["score-detail-summary", "score-detail-milestones"],
    TASK_PATH: ["tasks-entry", "primary-task", "task-instructions", "task-workspace"],
    TASK_RESULT: [
      "task-result-summary",
      "task-result-score-sync",
      "task-result-next-action",
    ],
    MESSAGES_TICKETS: ["messages-overview", "messages-tabs"],
    HELP_ROUTES: ["help-route-choices"],
    PERSONALIZED_TASK_FIRST: ["personalized-task-area", "personalized-task-reason"],
  };

  ONBOARDING_GUIDE_ORDER.forEach((guideCode) => {
    const guide = getOnboardingGuide(guideCode);
    assert.equal(guide, ONBOARDING_GUIDES[guideCode]);
    assert.equal(guide.guideCode, guideCode);
    assert.equal(guide.guideVersion, 1);
    assert.equal(guide.version, 1);
    assert.equal(guide.startRoute, guide.steps[0].route);
    assert.deepEqual(guide.steps.map((step) => step.id), expectedSteps[guideCode]);
    assert.equal(getOnboardingStepCount(guideCode), expectedSteps[guideCode].length);
    assert.deepEqual(
      getOnboardingSpotlightTargets(guideCode),
      guide.steps.map((step) => step.target),
    );
    assert.deepEqual(
      getOnboardingStepRoutes(guideCode),
      guide.steps.map((step) => step.route),
    );
  });

  assert.equal(getOnboardingGuide("UNSUPPORTED"), ONBOARDING_GUIDES.FIRST_LOGIN);
  assert.equal(ONBOARDING_STEPS, ONBOARDING_GUIDES.FIRST_LOGIN.steps);
  assert.equal(ONBOARDING_STEP_COUNT, 5);
});

test("keeps contextual modules explanatory except for the two task-path navigation clicks", () => {
  const taskPathSteps = getOnboardingSteps(ONBOARDING_GUIDE_CODES.taskPath);
  assert.deepEqual(taskPathSteps.slice(0, 2).map((step) => ({
    advanceOn: step.advanceOn,
    actionId: step.actionId,
  })), [
    {
      advanceOn: ONBOARDING_ADVANCE_ON.targetClick,
      actionId: "open-tasks",
    },
    {
      advanceOn: ONBOARDING_ADVANCE_ON.targetClick,
      actionId: "open-primary-task",
    },
  ]);
  taskPathSteps.slice(2).forEach((step) => {
    assert.equal(step.advanceOn, ONBOARDING_ADVANCE_ON.nextButton);
    assert.equal(step.actionId, undefined);
  });

  const explanatoryCodes = ONBOARDING_GUIDE_ORDER.filter((code) => (
    code !== ONBOARDING_GUIDE_CODE && code !== ONBOARDING_GUIDE_CODES.taskPath
  ));
  explanatoryCodes.forEach((guideCode) => {
    getOnboardingSteps(guideCode).forEach((step) => {
      assert.equal(step.advanceOn, ONBOARDING_ADVANCE_ON.nextButton);
      assert.equal(step.actionId, undefined);
    });
  });

  const contextualStepIds = ONBOARDING_GUIDE_ORDER
    .filter((code) => code !== ONBOARDING_GUIDE_CODE)
    .flatMap((code) => (
      getOnboardingSteps(code).map((step) => step.id)
    ));
  assert.equal(contextualStepIds.some((id) => /g01|g04|single-class|course-execution/i.test(id)), false);
});

test("normalizes the guide catalog and fills missing contextual modules", () => {
  const acknowledgedAt = "2026-08-10T10:00:00.000Z";
  const catalog = normalizeOnboardingCatalog({
    required: false,
    guideCode: "FIRST_LOGIN",
    guideVersion: 1,
    status: "COMPLETED",
    acknowledgedAt,
    guides: [
      {
        guideCode: "TASK_PATH",
        guideVersion: 3,
        required: false,
        status: "SKIPPED",
        acknowledgedAt,
      },
      {
        guideCode: "NOT_ALLOWED",
        guideVersion: 1,
        required: true,
        status: null,
      },
      {
        guideCode: "HELP_ROUTES",
        guideVersion: 0,
        required: false,
        status: "COMPLETED",
      },
    ],
  });

  assert.equal(catalog.length, 8);
  assert.deepEqual(catalog.map((item) => item.guideCode), ONBOARDING_GUIDE_ORDER);
  assert.deepEqual(catalog[0], {
    guideCode: "FIRST_LOGIN",
    guideVersion: 1,
    required: false,
    status: "COMPLETED",
    acknowledgedAt,
  });
  assert.deepEqual(catalog.find((item) => item.guideCode === "TASK_PATH"), {
    guideCode: "TASK_PATH",
    guideVersion: 3,
    required: false,
    status: "SKIPPED",
    acknowledgedAt,
  });
  assert.deepEqual(catalog.find((item) => item.guideCode === "HELP_ROUTES"), {
    guideCode: "HELP_ROUTES",
    guideVersion: 1,
    required: true,
    status: null,
    acknowledgedAt: null,
  });
  assert.equal(catalog.some((item) => item.guideCode === "NOT_ALLOWED"), false);

  const emptyCatalog = normalizeOnboardingCatalog(null);
  assert.equal(emptyCatalog[0].required, false);
  assert.equal(emptyCatalog.slice(1).every((item) => item.required), true);
});

test("waits for authenticated data and also checks first-login deep links", () => {
  const ready = {
    authenticated: true,
    dataReady: true,
    pathname: "/",
    checked: false,
    loading: false,
  };
  assert.equal(shouldLoadOnboarding(ready), true);
  assert.equal(shouldLoadOnboarding({ ...ready, pathname: "/task/G04" }), true);
  assert.equal(shouldLoadOnboarding({ ...ready, pathname: "/path" }), true);
  assert.equal(shouldLoadOnboarding({ ...ready, dataReady: false }), false);
  assert.equal(shouldLoadOnboarding({ ...ready, checked: true }), false);
});

test("keeps the complete guide state bounded and preserves retry commands", () => {
  let state = { ...initialOnboardingGuideState };
  for (let index = 0; index < ONBOARDING_STEP_COUNT + 2; index += 1) {
    state = reduceOnboardingGuide(state, { type: "NEXT" });
  }
  assert.equal(ONBOARDING_STEP_COUNT, 5);
  assert.deepEqual(ONBOARDING_SPOTLIGHT_TARGETS, [
    "main-navigation",
    "my-tide-overview",
    "messages-entry",
    "messages-tabs",
    "help-entry",
  ]);
  assert.deepEqual(ONBOARDING_STEP_ROUTES, [
    "MY_TIDE",
    "MY_TIDE",
    "MY_TIDE",
    "MESSAGES",
    "MESSAGES",
  ]);
  assert.equal(new Set(ONBOARDING_STEPS.map((step) => step.id)).size, ONBOARDING_STEP_COUNT);
  assert.equal(new Set(ONBOARDING_SPOTLIGHT_TARGETS).size, ONBOARDING_STEP_COUNT);
  assert.equal(state.step, 4);

  const command = {
    outcome: "COMPLETED",
    destination: "TASKS",
    idempotencyKey: "onboarding-command-1",
  };
  state = reduceOnboardingGuide(state, { type: "SUBMIT", command });
  state = reduceOnboardingGuide(state, { type: "FAILED" });
  assert.equal(state.phase, "error");
  assert.equal(state.pendingCommand, command);

  state = reduceOnboardingGuide(state, { type: "BACK" });
  assert.equal(state.step, 3);
  assert.equal(onboardingEscapeAction("automatic"), "SKIP");
  assert.equal(onboardingEscapeAction("replay"), "CLOSE");
});

test("uses one real message click within the explanatory first-login tour", () => {
  const globalStep = onboardingStepIndex("global-navigation");
  const overviewStep = onboardingStepIndex("my-tide-overview");
  const messagesEntryStep = onboardingStepIndex("messages-entry");
  const messagesTabsStep = onboardingStepIndex("messages-tabs");
  const helpStep = onboardingStepIndex("help-entry");

  assert.equal(resolveOnboardingInteraction(globalStep, {
    kind: ONBOARDING_ADVANCE_ON.nextButton,
  }), "NEXT");
  assert.equal(resolveOnboardingInteraction(overviewStep, {
    kind: ONBOARDING_ADVANCE_ON.nextButton,
  }), "NEXT");
  assert.equal(resolveOnboardingInteraction(messagesEntryStep, {
    kind: ONBOARDING_ADVANCE_ON.targetClick,
    actionId: "open-messages",
  }), "NEXT");
  assert.equal(resolveOnboardingInteraction(messagesEntryStep, {
    kind: ONBOARDING_ADVANCE_ON.targetClick,
    actionId: "open-tasks",
  }), null);
  assert.equal(resolveOnboardingInteraction(messagesTabsStep, {
    kind: ONBOARDING_ADVANCE_ON.nextButton,
  }), "NEXT");
  assert.equal(resolveOnboardingInteraction(helpStep, {
    kind: ONBOARDING_ADVANCE_ON.nextButton,
  }), "COMPLETE");
});

test("resolves progress independently for each contextual module", () => {
  const scoreCode = ONBOARDING_GUIDE_CODES.scoreDetails;
  assert.equal(resolveOnboardingInteraction(0, {
    kind: ONBOARDING_ADVANCE_ON.nextButton,
  }, scoreCode), "NEXT");
  assert.equal(resolveOnboardingInteraction(1, {
    kind: ONBOARDING_ADVANCE_ON.nextButton,
  }, scoreCode), "COMPLETE");
  assert.equal(resolveOnboardingInteraction(0, {
    kind: ONBOARDING_ADVANCE_ON.targetClick,
    actionId: "open-score-details",
  }, scoreCode), null);

  let state = { ...initialOnboardingGuideState };
  state = reduceOnboardingGuide(state, { type: "NEXT", stepCount: 2 });
  state = reduceOnboardingGuide(state, { type: "NEXT", stepCount: 2 });
  assert.equal(state.step, 1);

  const messagesCode = ONBOARDING_GUIDE_CODES.messagesTickets;
  assert.equal(onboardingRouteIntent(0, 1, {}, messagesCode), null);
  assert.equal(resolveOnboardingInteraction(1, {
    kind: ONBOARDING_ADVANCE_ON.nextButton,
  }, messagesCode), "COMPLETE");
  assert.equal(resolveOnboardingInteraction(0, {
    kind: ONBOARDING_ADVANCE_ON.nextButton,
  }, ONBOARDING_GUIDE_CODES.helpRoutes), "COMPLETE");

  const taskPathCode = ONBOARDING_GUIDE_CODES.taskPath;
  assert.equal(resolveOnboardingInteraction(0, {
    kind: ONBOARDING_ADVANCE_ON.targetClick,
    actionId: "open-tasks",
  }, taskPathCode), "NEXT");
  assert.equal(resolveOnboardingInteraction(1, {
    kind: ONBOARDING_ADVANCE_ON.targetClick,
    actionId: "open-primary-task",
  }, taskPathCode), "NEXT");
  assert.equal(resolveOnboardingInteraction(2, {
    kind: ONBOARDING_ADVANCE_ON.nextButton,
  }, taskPathCode), "NEXT");
  assert.equal(resolveOnboardingInteraction(3, {
    kind: ONBOARDING_ADVANCE_ON.nextButton,
  }, taskPathCode), "COMPLETE");
});

test("does not duplicate navigation after a real target link handles the route", () => {
  const messagesEntryStep = onboardingStepIndex("messages-entry");
  const messagesTabsStep = onboardingStepIndex("messages-tabs");
  const overviewStep = onboardingStepIndex("my-tide-overview");

  assert.equal(onboardingRouteIntent(messagesEntryStep, messagesTabsStep, {
    kind: ONBOARDING_ADVANCE_ON.targetClick,
    targetNavigates: true,
  }), null);
  assert.equal(onboardingRouteIntent(messagesTabsStep, messagesEntryStep, {
    kind: "BACK",
  }), "MY_TIDE");
  assert.equal(onboardingRouteIntent(overviewStep, messagesEntryStep), null);

  const taskPathCode = ONBOARDING_GUIDE_CODES.taskPath;
  assert.equal(onboardingRouteIntent(0, 1, {
    kind: ONBOARDING_ADVANCE_ON.targetClick,
    targetNavigates: true,
  }, taskPathCode), null);
  assert.equal(onboardingRouteIntent(1, 2, {
    kind: ONBOARDING_ADVANCE_ON.targetClick,
    targetNavigates: true,
  }, taskPathCode), null);
  assert.equal(onboardingRouteIntent(2, 1, {
    kind: "BACK",
  }, taskPathCode), "TASKS");
  assert.equal(onboardingRouteIntent(1, 0, {
    kind: "BACK",
  }, taskPathCode), "MESSAGES");
  assert.equal(onboardingRouteIntent(2, 3, {}, taskPathCode), null);
});

test("expands spotlight targets and clamps the hole inside the viewport", () => {
  assert.deepEqual(calculateSpotlightRect({
    left: 40,
    top: 30,
    width: 100,
    height: 60,
  }, { width: 300, height: 200 }), {
    top: 20,
    left: 30,
    right: 150,
    bottom: 100,
    width: 120,
    height: 80,
  });

  assert.deepEqual(calculateSpotlightRect({
    left: -20,
    top: -5,
    right: 30,
    bottom: 20,
  }, { width: 120, height: 80 }), {
    top: 8,
    left: 8,
    right: 40,
    bottom: 30,
    width: 32,
    height: 22,
  });
});

test("keeps the surrounding page visible behind the spotlight", async () => {
  const styles = await readFile(
    new URL("../src/components/onboarding-guide.css", import.meta.url),
    "utf8",
  );

  assert.match(styles, /\.onboarding-spotlight-mask\s*\{[\s\S]*?rgba\(7, 25, 46, 0\.38\)/);
  assert.doesNotMatch(styles, /\.onboarding-spotlight-mask\s*\{[\s\S]*?rgba\(7, 25, 46, 0\.72\)/);
});

test("places the coachmark below when it fits and above near the viewport bottom", () => {
  assert.deepEqual(calculateCoachmarkPosition({
    top: 40,
    left: 100,
    right: 180,
    bottom: 80,
    width: 80,
    height: 40,
  }, { width: 400, height: 500 }, { width: 120, height: 100 }), {
    placement: "below",
    top: 96,
    left: 80,
  });

  assert.deepEqual(calculateCoachmarkPosition({
    top: 420,
    left: 100,
    right: 180,
    bottom: 460,
    width: 80,
    height: 40,
  }, { width: 400, height: 500 }, { width: 120, height: 100 }), {
    placement: "above",
    top: 304,
    left: 80,
  });

  assert.deepEqual(calculateCoachmarkPosition({
    top: 330,
    left: 900,
    right: 1000,
    bottom: 370,
    width: 100,
    height: 40,
  }, { width: 1200, height: 700 }, { width: 368, height: 350 }), {
    placement: "left",
    top: 175,
    left: 516,
  });
});

test("allows the non-sensitive onboarding analytics dimensions", () => {
  assert.deepEqual(sanitizeAnalyticsProperties({
    guideCode: "FIRST_LOGIN",
    guideVersion: 1,
    onboardingStep: 2,
  }), {
    guideCode: "FIRST_LOGIN",
    guideVersion: 1,
    onboardingStep: 2,
  });
});

test("uses the server status, idempotent acknowledgement and accessible modal contract", async () => {
  const [
    apiSource,
    appSource,
    mainSource,
    componentSource,
    styleSource,
  ] = await Promise.all([
    readFile(new URL("../src/api/tide-api.js", import.meta.url), "utf8"),
    readFile(new URL("../src/App.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/main.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/OnboardingGuide.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/onboarding-guide.css", import.meta.url), "utf8"),
  ]);

  assert.match(apiSource, /api\/v1\/me\/onboarding"/);
  assert.match(apiSource, /api\/v1\/me\/onboarding\/acknowledge/);
  assert.match(apiSource, /"Idempotency-Key"/);
  assert.match(apiSource, /body: \{ guideCode, guideVersion, outcome \}/);
  assert.match(appSource, /getOnboardingStatus\(controller\.signal\)/);
  assert.match(appSource, /navigate\("\/", \{ replace: true \}\)/);
  assert.match(appSource, /requestState\.retryCount < 2/);
  assert.match(mainSource, /import\.meta\.env\.DEV/);
  assert.match(mainSource, /\/preview\/onboarding/);
  assert.match(mainSource, /MemoryRouter initialEntries=\{\["\/"\]\}/);
  assert.match(mainSource, /if \(!isOnboardingPreview\) startAnalyticsRuntime\(\)/);
  assert.match(appSource, /<MyTitPage/);
  assert.match(appSource, /<GrowthPathPage/);
  assert.match(appSource, /previewMode/);
  assert.match(appSource, /analyticsEnabled=\{false\}/);
  assert.match(componentSource, /role="dialog"/);
  assert.match(componentSource, /aria-modal=\{!requiresTargetAction\}/);
  assert.match(componentSource, /focusableSelector/);
  assert.match(componentSource, /restoreFocusRef\.current\?\.focus/);
  assert.match(componentSource, /onboardingEscapeAction\(mode\)/);
  assert.match(componentSource, /data-onboarding-target/);
  assert.match(componentSource, /ResizeObserver/);
  assert.match(componentSource, /localizedModule\.steps\[stepDefinition\?\.id\]/);
  assert.match(componentSource, /getOnboardingGuideDefinition\(guideCode\)/);
  assert.match(componentSource, /getOnboardingStepCount\(effectiveGuideCode\)/);
  assert.match(componentSource, /onboardingRouteIntent/);
  assert.match(componentSource, /onOpenPrimaryTask/);
  assert.match(componentSource, /onOpenMyTide/);
  assert.match(componentSource, /onOpenTasks/);
  assert.match(componentSource, /document\.addEventListener\("click", handleTargetClick, true\)/);
  assert.match(componentSource, /targetFlowRef\.current\.moveToStep/);
  assert.doesNotMatch(componentSource, /className="onboarding-spotlight-hole"[\s\S]*?onClick=/);
  assert.match(componentSource, /onClick=\{handlePrimaryControl\}/);
  assert.match(componentSource, /localized\.clickHighlightedArea/);
  assert.match(appSource, /data-onboarding-target="main-navigation"/);
  assert.match(appSource, /data-onboarding-target="messages-entry"/);
  assert.match(appSource, /data-onboarding-action="open-messages"/);
  assert.match(appSource, /data-onboarding-target="help-entry"/);
  assert.match(appSource, /data-onboarding-target="tasks-entry"/);
  assert.match(appSource, /data-onboarding-action="open-tasks"/);
  assert.match(appSource, /data-onboarding-target=\{priority === "primary" \? "primary-task-action"/);
  assert.match(appSource, /data-onboarding-action=\{priority === "primary" \? "open-primary-task"/);
  assert.match(appSource, /data-onboarding-target="task-instructions"/);
  assert.match(appSource, /className="task-workspace"[\s\S]*?data-onboarding-target="task-workspace"/);
  assert.match(appSource, /chainedOnboardingGuideRef/);
  assert.match(appSource, /outcome === ONBOARDING_OUTCOMES\.completed[\s\S]*?onboardingStatus\.guideCode === ONBOARDING_GUIDE_CODE/);
  assert.match(appSource, /chainedOnboardingGuideRef\.current = ONBOARDING_GUIDE_CODES\.taskPath/);
  assert.match(appSource, /window\.setTimeout\(\(\) => setOnboardingOpen\(true\), 0\)/);
  assert.doesNotMatch(appSource, /workspace-start-link|start-primary-task-workspace|Start first step|开始第一步/);
  assert.match(componentSource, /ONBOARDING_GUIDE_CODES\.myTideOverview/);
  assert.match(componentSource, /ONBOARDING_GUIDE_CODES\.scoreDetails/);
  assert.match(componentSource, /ONBOARDING_GUIDE_CODES\.messagesTickets/);
  assert.match(componentSource, /ONBOARDING_GUIDE_CODES\.helpRoutes/);
  assert.match(styleSource, /onboarding-spotlight-mask/);
  assert.match(styleSource, /\.onboarding-spotlight-hole[\s\S]*?background: transparent/);
  assert.match(styleSource, /\.onboarding-spotlight-hole\.is-target-action[\s\S]*?pointer-events: none/);
  assert.match(styleSource, /\.onboarding-click-instruction/);
  assert.match(styleSource, /repeat\(var\(--onboarding-step-count\), minmax\(0, 1fr\)\)/);
  assert.doesNotMatch(styleSource, /\.workspace-start-link/);
  assert.match(styleSource, /@media \(max-width: 390px\)/);
  assert.match(styleSource, /prefers-reduced-motion: reduce/);
});

test("connects every approved contextual guide to real UI without adding page actions", async () => {
  const [appSource, messageSource, helpSource, librarySource] = await Promise.all([
    readFile(new URL("../src/App.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/MessageCenter.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/FaqHelpDialog.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/GuideLibraryDialog.jsx", import.meta.url), "utf8"),
  ]);

  [
    "my-tide-overview",
    "my-tide-recommendation",
    "my-tide-dimensions",
    "score-detail-summary",
    "score-detail-milestones",
    "task-instructions",
    "task-workspace",
    "task-result-summary",
    "task-result-score-sync",
    "task-result-next-action",
    "personalized-task-area",
    "personalized-task-reason",
  ].forEach((target) => assert.match(
    appSource,
    new RegExp(`data-onboarding-target=[\\s\\S]{0,100}${target}`),
  ));
  assert.match(messageSource, /data-onboarding-target="messages-overview"/);
  assert.match(messageSource, /data-onboarding-target="messages-tabs"/);
  assert.match(helpSource, /data-onboarding-target="help-route-choices"/);
  assert.doesNotMatch(appSource, /data-onboarding-target="(?:tasks-recommendations|growth-map)"/);
  assert.doesNotMatch(messageSource, /data-onboarding-target="messages-detail"/);
  assert.doesNotMatch(helpSource, /data-onboarding-target="help-overview"/);
  assert.doesNotMatch(helpSource, /data-onboarding-action="close-help"/);
  assert.match(appSource, /messagesLoaded[\s\S]*supportTicketsLoaded/);
  assert.doesNotMatch(appSource, /messageTotalCount > 0 \|\| supportTickets\.length > 0/);
  assert.match(appSource, /onboardingAvailability/);
  assert.match(appSource, /guideShownThisSessionRef/);
  assert.match(appSource, /<GuideLibraryDialog/);
  assert.match(librarySource, /GUIDE_LIBRARY_ORDER/);
  assert.match(librarySource, /Replaying one never changes task progress or points/);
  assert.doesNotMatch(appSource, /onboarding-preview-reopen|Start first step|开始第一步/);
});
