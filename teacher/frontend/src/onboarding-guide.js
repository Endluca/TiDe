export const ONBOARDING_GUIDE_CODE = "FIRST_LOGIN";
export const ONBOARDING_DEFAULT_VERSION = 1;

export const ONBOARDING_GUIDE_CODES = Object.freeze({
  firstLogin: "FIRST_LOGIN",
  myTideOverview: "MY_TIDE_OVERVIEW",
  scoreDetails: "SCORE_DETAILS",
  taskPath: "TASK_PATH",
  taskResult: "TASK_RESULT",
  messagesTickets: "MESSAGES_TICKETS",
  helpRoutes: "HELP_ROUTES",
  personalizedTaskFirst: "PERSONALIZED_TASK_FIRST",
});

export const ONBOARDING_ADVANCE_ON = Object.freeze({
  nextButton: "NEXT_BUTTON",
  targetClick: "TARGET_CLICK",
});

function defineGuide(code, steps) {
  const frozenSteps = Object.freeze(steps.map((step) => Object.freeze(step)));
  return Object.freeze({
    code,
    version: 1,
    guideCode: code,
    guideVersion: 1,
    startRoute: frozenSteps[0]?.route || "CURRENT",
    steps: frozenSteps,
  });
}

export const ONBOARDING_GUIDE_REGISTRY = Object.freeze({
  [ONBOARDING_GUIDE_CODES.firstLogin]: defineGuide(
    ONBOARDING_GUIDE_CODES.firstLogin,
    [
      {
        id: "global-navigation",
        target: "main-navigation",
        route: "MY_TIDE",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
      {
        id: "my-tide-overview",
        target: "my-tide-overview",
        route: "MY_TIDE",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
      {
        id: "messages-entry",
        target: "messages-entry",
        route: "MY_TIDE",
        advanceOn: ONBOARDING_ADVANCE_ON.targetClick,
        actionId: "open-messages",
      },
      {
        id: "messages-tabs",
        target: "messages-tabs",
        route: "MESSAGES",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
      {
        id: "help-entry",
        target: "help-entry",
        route: "MESSAGES",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
    ],
  ),
  [ONBOARDING_GUIDE_CODES.myTideOverview]: defineGuide(
    ONBOARDING_GUIDE_CODES.myTideOverview,
    [
      {
        id: "my-tide-overview",
        target: "my-tide-overview",
        route: "MY_TIDE",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
      {
        id: "my-tide-recommendation",
        target: "my-tide-recommendation",
        route: "MY_TIDE",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
      {
        id: "my-tide-dimensions",
        target: "my-tide-dimensions",
        route: "MY_TIDE",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
    ],
  ),
  [ONBOARDING_GUIDE_CODES.scoreDetails]: defineGuide(
    ONBOARDING_GUIDE_CODES.scoreDetails,
    [
      {
        id: "score-detail-summary",
        target: "score-detail-summary",
        route: "MY_TIDE",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
      {
        id: "score-detail-milestones",
        target: "score-detail-milestones",
        route: "MY_TIDE",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
    ],
  ),
  [ONBOARDING_GUIDE_CODES.taskPath]: defineGuide(
    ONBOARDING_GUIDE_CODES.taskPath,
    [
      {
        id: "tasks-entry",
        target: "tasks-entry",
        route: "MESSAGES",
        advanceOn: ONBOARDING_ADVANCE_ON.targetClick,
        actionId: "open-tasks",
      },
      {
        id: "primary-task",
        target: "primary-task-action",
        route: "TASKS",
        advanceOn: ONBOARDING_ADVANCE_ON.targetClick,
        actionId: "open-primary-task",
      },
      {
        id: "task-instructions",
        target: "task-instructions",
        route: "TASK_DETAIL",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
      {
        id: "task-workspace",
        target: "task-workspace",
        route: "TASK_DETAIL",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
    ],
  ),
  [ONBOARDING_GUIDE_CODES.taskResult]: defineGuide(
    ONBOARDING_GUIDE_CODES.taskResult,
    [
      {
        id: "task-result-summary",
        target: "task-result-summary",
        route: "TASK_DETAIL",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
      {
        id: "task-result-score-sync",
        target: "task-result-score-sync",
        route: "TASK_DETAIL",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
      {
        id: "task-result-next-action",
        target: "task-result-next-action",
        route: "TASK_DETAIL",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
    ],
  ),
  [ONBOARDING_GUIDE_CODES.messagesTickets]: defineGuide(
    ONBOARDING_GUIDE_CODES.messagesTickets,
    [
      {
        id: "messages-overview",
        target: "messages-overview",
        route: "MESSAGES",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
      {
        id: "messages-tabs",
        target: "messages-tabs",
        route: "MESSAGES",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
    ],
  ),
  [ONBOARDING_GUIDE_CODES.helpRoutes]: defineGuide(
    ONBOARDING_GUIDE_CODES.helpRoutes,
    [
      {
        id: "help-route-choices",
        target: "help-route-choices",
        route: "CURRENT",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
    ],
  ),
  [ONBOARDING_GUIDE_CODES.personalizedTaskFirst]: defineGuide(
    ONBOARDING_GUIDE_CODES.personalizedTaskFirst,
    [
      {
        id: "personalized-task-area",
        target: "personalized-task-area",
        route: "TASKS",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
      {
        id: "personalized-task-reason",
        target: "personalized-task-reason",
        route: "TASKS",
        advanceOn: ONBOARDING_ADVANCE_ON.nextButton,
      },
    ],
  ),
});

export const ONBOARDING_GUIDE_ORDER = Object.freeze(
  Object.keys(ONBOARDING_GUIDE_REGISTRY),
);

export const ONBOARDING_GUIDES = ONBOARDING_GUIDE_REGISTRY;

export function getOnboardingGuideDefinition(guideCode = ONBOARDING_GUIDE_CODE) {
  return ONBOARDING_GUIDE_REGISTRY[guideCode]
    || ONBOARDING_GUIDE_REGISTRY[ONBOARDING_GUIDE_CODE];
}

export const getOnboardingGuide = getOnboardingGuideDefinition;

export function getOnboardingSteps(guideCode = ONBOARDING_GUIDE_CODE) {
  return getOnboardingGuideDefinition(guideCode).steps;
}

export function getOnboardingStepCount(guideCode = ONBOARDING_GUIDE_CODE) {
  return getOnboardingSteps(guideCode).length;
}

export function getOnboardingSpotlightTargets(guideCode = ONBOARDING_GUIDE_CODE) {
  return Object.freeze(getOnboardingSteps(guideCode).map((step) => step.target));
}

export function getOnboardingStepRoutes(guideCode = ONBOARDING_GUIDE_CODE) {
  return Object.freeze(getOnboardingSteps(guideCode).map((step) => step.route));
}

// Compatibility exports for the original FIRST_LOGIN integration.
export const ONBOARDING_STEPS = ONBOARDING_GUIDE_REGISTRY[ONBOARDING_GUIDE_CODE].steps;

export const ONBOARDING_SPOTLIGHT_TARGETS = Object.freeze(
  ONBOARDING_STEPS.map((step) => step.target),
);
export const ONBOARDING_STEP_ROUTES = Object.freeze(
  ONBOARDING_STEPS.map((step) => step.route),
);
export const ONBOARDING_STEP_COUNT = ONBOARDING_STEPS.length;

export function resolveOnboardingInteraction(
  stepIndex,
  interaction = {},
  guideCode = ONBOARDING_GUIDE_CODE,
) {
  const steps = getOnboardingSteps(guideCode);
  const step = steps[stepIndex];
  if (!step || step.advanceOn !== interaction.kind) return null;
  if (
    step.advanceOn === ONBOARDING_ADVANCE_ON.targetClick
    && step.actionId !== interaction.actionId
  ) {
    return null;
  }
  return stepIndex === steps.length - 1 ? "COMPLETE" : "NEXT";
}

export function onboardingRouteIntent(
  fromStepIndex,
  toStepIndex,
  { kind = ONBOARDING_ADVANCE_ON.nextButton, targetNavigates = false } = {},
  guideCode = ONBOARDING_GUIDE_CODE,
) {
  if (kind === ONBOARDING_ADVANCE_ON.targetClick && targetNavigates) return null;
  const steps = getOnboardingSteps(guideCode);
  const fromRoute = steps[fromStepIndex]?.route;
  const toRoute = steps[toStepIndex]?.route;
  return toRoute && toRoute !== fromRoute ? toRoute : null;
}

export const ONBOARDING_OUTCOMES = Object.freeze({
  completed: "COMPLETED",
  skipped: "SKIPPED",
});

export function normalizeOnboardingStatus(payload) {
  const guide = getOnboardingGuideDefinition(payload?.guideCode);
  const rawVersion = payload?.guideVersion;
  const parsedVersion = typeof rawVersion === "string" && /^\d+$/.test(rawVersion.trim())
    ? Number(rawVersion)
    : rawVersion;
  const guideVersion = Number.isSafeInteger(parsedVersion) && parsedVersion > 0
    ? parsedVersion
    : ONBOARDING_DEFAULT_VERSION;

  return {
    required: payload?.required === true,
    guideCode: guide.code,
    guideVersion,
  };
}

function positiveGuideVersion(value, fallback) {
  const parsed = typeof value === "string" && /^\d+$/.test(value.trim())
    ? Number(value)
    : value;
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * Normalizes the progressive-guide catalog while retaining compatibility with
 * the original response, whose top-level fields only described FIRST_LOGIN.
 * Missing contextual modules remain eligible; their runtime prerequisites are
 * evaluated by the page that owns each module.
 */
export function normalizeOnboardingCatalog(payload) {
  const rawGuides = Array.isArray(payload?.guides) ? payload.guides : [];
  const validGuides = new Map();
  rawGuides.forEach((item) => {
    if (
      item
      && ONBOARDING_GUIDE_REGISTRY[item.guideCode]
      && positiveGuideVersion(item.guideVersion, null) !== null
    ) {
      validGuides.set(item.guideCode, item);
    }
  });

  return ONBOARDING_GUIDE_ORDER.map((guideCode) => {
    const guide = getOnboardingGuideDefinition(guideCode);
    const legacyFirstLogin = guideCode === ONBOARDING_GUIDE_CODE ? payload : null;
    const source = validGuides.get(guideCode) || legacyFirstLogin;
    return {
      guideCode,
      guideVersion: positiveGuideVersion(source?.guideVersion, guide.guideVersion),
      required: source
        ? source.required === true
        : guideCode !== ONBOARDING_GUIDE_CODE,
      status: typeof source?.status === "string" ? source.status : null,
      acknowledgedAt: typeof source?.acknowledgedAt === "string"
        ? source.acknowledgedAt
        : null,
    };
  });
}

export function shouldLoadOnboarding({
  authenticated,
  dataReady,
  checked,
  loading,
}) {
  return Boolean(
    authenticated
    && dataReady
    && !checked
    && !loading
  );
}

export const initialOnboardingGuideState = Object.freeze({
  step: 0,
  phase: "idle",
  pendingCommand: null,
});

function finiteNumber(value, fallback = 0) {
  return Number.isFinite(value) ? value : fallback;
}

function clamp(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum);
}

/**
 * Expands a DOMRect-like target and clips the spotlight hole to the viewport.
 * Keeping this calculation DOM-free makes resize and mobile edge cases easy to
 * verify without a browser.
 */
export function calculateSpotlightRect(
  targetRect,
  viewport,
  { padding = 10, margin = 8 } = {},
) {
  const viewportWidth = Math.max(0, finiteNumber(viewport?.width));
  const viewportHeight = Math.max(0, finiteNumber(viewport?.height));
  const safePadding = Math.max(0, finiteNumber(padding));
  const safeMargin = Math.max(0, finiteNumber(margin));
  const left = finiteNumber(targetRect?.left);
  const top = finiteNumber(targetRect?.top);
  const right = Number.isFinite(targetRect?.right)
    ? targetRect.right
    : left + Math.max(0, finiteNumber(targetRect?.width));
  const bottom = Number.isFinite(targetRect?.bottom)
    ? targetRect.bottom
    : top + Math.max(0, finiteNumber(targetRect?.height));
  const maximumRight = Math.max(safeMargin, viewportWidth - safeMargin);
  const maximumBottom = Math.max(safeMargin, viewportHeight - safeMargin);
  const clippedLeft = clamp(left - safePadding, safeMargin, maximumRight);
  const clippedTop = clamp(top - safePadding, safeMargin, maximumBottom);
  const clippedRight = clamp(right + safePadding, clippedLeft, maximumRight);
  const clippedBottom = clamp(bottom + safePadding, clippedTop, maximumBottom);

  return {
    top: clippedTop,
    left: clippedLeft,
    right: clippedRight,
    bottom: clippedBottom,
    width: clippedRight - clippedLeft,
    height: clippedBottom - clippedTop,
  };
}

/**
 * Positions a coachmark below or above its spotlight when possible. On wide,
 * short viewports it moves beside the spotlight instead of covering the target.
 * If no side fully fits, the roomier vertical side wins and the card is clamped.
 */
export function calculateCoachmarkPosition(
  spotlightRect,
  viewport,
  coachmark,
  { gap = 16, margin = 12 } = {},
) {
  const viewportWidth = Math.max(0, finiteNumber(viewport?.width));
  const viewportHeight = Math.max(0, finiteNumber(viewport?.height));
  const safeGap = Math.max(0, finiteNumber(gap));
  const safeMargin = Math.max(0, finiteNumber(margin));
  const width = Math.min(
    Math.max(0, finiteNumber(coachmark?.width)),
    Math.max(0, viewportWidth - (safeMargin * 2)),
  );
  const height = Math.min(
    Math.max(0, finiteNumber(coachmark?.height)),
    Math.max(0, viewportHeight - (safeMargin * 2)),
  );
  const roomBelow = viewportHeight - spotlightRect.bottom - safeGap - safeMargin;
  const roomAbove = spotlightRect.top - safeGap - safeMargin;
  const roomRight = viewportWidth - spotlightRect.right - safeGap - safeMargin;
  const roomLeft = spotlightRect.left - safeGap - safeMargin;
  let placement;
  if (roomBelow >= height) placement = "below";
  else if (roomAbove >= height) placement = "above";
  else if (roomRight >= width || roomLeft >= width) {
    placement = roomRight >= width && roomRight >= roomLeft ? "right" : "left";
  } else {
    placement = roomBelow >= roomAbove ? "below" : "above";
  }
  const idealTop = placement === "below"
    ? spotlightRect.bottom + safeGap
    : placement === "above"
      ? spotlightRect.top - safeGap - height
      : spotlightRect.top + ((spotlightRect.height - height) / 2);
  const maximumTop = Math.max(safeMargin, viewportHeight - height - safeMargin);
  const maximumLeft = Math.max(safeMargin, viewportWidth - width - safeMargin);
  const idealLeft = placement === "right"
    ? spotlightRect.right + safeGap
    : placement === "left"
      ? spotlightRect.left - safeGap - width
      : spotlightRect.left + ((spotlightRect.width - width) / 2);

  return {
    placement,
    top: clamp(idealTop, safeMargin, maximumTop),
    left: clamp(idealLeft, safeMargin, maximumLeft),
  };
}

export function reduceOnboardingGuide(state, action) {
  const stepCount = Number.isInteger(action.stepCount) && action.stepCount > 0
    ? action.stepCount
    : ONBOARDING_STEP_COUNT;
  switch (action.type) {
    case "RESET":
      return { ...initialOnboardingGuideState };
    case "NEXT":
      return {
        ...state,
        step: Math.min(stepCount - 1, state.step + 1),
        phase: "idle",
      };
    case "BACK":
      return {
        ...state,
        step: Math.max(0, state.step - 1),
        phase: "idle",
      };
    case "SUBMIT":
      return {
        ...state,
        phase: "submitting",
        pendingCommand: action.command,
      };
    case "FAILED":
      return {
        ...state,
        phase: "error",
      };
    default:
      return state;
  }
}

export function onboardingEscapeAction(mode) {
  return mode === "replay" ? "CLOSE" : "SKIP";
}
