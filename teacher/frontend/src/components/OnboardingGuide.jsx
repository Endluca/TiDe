import {
  ArrowLeft,
  ArrowRight,
  CheckCircle,
  ClipboardText,
  Compass,
  ListChecks,
  Sparkle,
  Star,
  Target,
  X,
} from "@phosphor-icons/react";
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import { newCommandKey } from "../api/api-client";
import { trackProductEvent } from "../analytics/product-analytics";
import {
  ONBOARDING_GUIDE_CODE,
  ONBOARDING_GUIDE_CODES,
  ONBOARDING_ADVANCE_ON,
  ONBOARDING_OUTCOMES,
  calculateCoachmarkPosition,
  calculateSpotlightRect,
  getOnboardingGuideDefinition,
  getOnboardingStepCount,
  getOnboardingSteps,
  initialOnboardingGuideState,
  onboardingRouteIntent,
  onboardingEscapeAction,
  reduceOnboardingGuide,
  resolveOnboardingInteraction,
} from "../onboarding-guide";
import { publicAsset } from "../public-assets";
import "./onboarding-guide.css";

const TARGET_FALLBACK_DELAY_MS = 1000;
const TARGET_RETRY_DELAY_MS = 120;
const DEFAULT_COACHMARK_SIZE = Object.freeze({ width: 368, height: 330 });

const guideCopy = {
  en: {
    overallProgress: (step, stepCount) => `Step ${step} of ${stepCount}`,
    languageLabel: "Guide language",
    skip: "Skip guide",
    close: "Close guide",
    back: "Back",
    retry: "Try again",
    continue: "Continue for now",
    saving: "Saving…",
    findingTarget: "Finding the next step…",
    targetFallback: "This area is still loading. You can continue, and the guide will keep looking for it.",
    targetActionHint: "Click the yellow highlighted area on the page. The guide continues only after you click it.",
    clickHighlightedArea: "Click the highlighted area to continue",
    syncErrorTitle: "Your choice was not saved",
    syncErrorBody: "You can try again, or continue now. The guide may appear again the next time you sign in.",
    modules: {
      [ONBOARDING_GUIDE_CODES.firstLogin]: {
        stepLabel: "QUICK TOUR",
        steps: {
          "global-navigation": {
            eyebrow: "START WITH THE MAP",
            title: "Know what each part of the camp is for",
            body: "My TIDE shows your growth, Tasks keeps the learning path, and Messages keeps system updates and support replies. We’ll visit the useful parts before you begin a task.",
            button: "See My TIDE",
            image: "/assets/toki/wave.png",
          },
          "my-tide-overview": {
            eyebrow: "MY TIDE",
            title: "Check your growth and the clearest next step",
            body: "This is your growth home. Use it to see your current stage, points and Toki’s recommendation without searching through the whole plan.",
            button: "Show Messages",
            image: "/assets/toki/worktoki.png",
          },
          "messages-entry": {
            eyebrow: "MESSAGES",
            title: "Task updates and support replies arrive here",
            body: "Click the real Messages navigation. Entering the page does not open a message or change its read state.",
            image: "/assets/toki/worktoki.png",
          },
          "messages-tabs": {
            eyebrow: "TWO HISTORIES",
            title: "System updates and your tickets stay separate",
            body: "System messages contain task and review updates. My submitted tickets keeps conversations that need operations support. An empty list simply means there is nothing new yet.",
            button: "Remember Help",
            image: "/assets/toki/worktoki.png",
          },
          "help-entry": {
            eyebrow: "HELP",
            title: "Choose the right support when you need it",
            body: "Use the FAQ assistant for approved knowledge and submit a ticket when operations needs to step in. Next, we’ll take you into your first real task.",
            button: "Start the first-task guide",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.myTideOverview]: {
        stepLabel: "MY TIDE OVERVIEW",
        steps: {
          "my-tide-overview": {
            eyebrow: "MY TIDE",
            title: "Your growth home keeps today in one place",
            body: "Use this page to see the next useful action, your latest growth picture and the broad areas shaping it.",
            button: "Show my recommendation",
            image: "/assets/toki/wave.png",
          },
          "my-tide-recommendation": {
            eyebrow: "TODAY’S RECOMMENDATION",
            title: "Start with one clear next step",
            body: "Toki updates this recommendation as your task status changes, so you do not need to search the whole plan every time.",
            button: "Show growth dimensions",
            image: "/assets/toki/worktoki.png",
          },
          "my-tide-dimensions": {
            eyebrow: "GROWTH DIMENSIONS",
            title: "See the broad sources shaping your growth",
            body: "These dimensions summarize the latest available results. Open a dimension later when you need its supporting details.",
            button: "Finish overview",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.scoreDetails]: {
        stepLabel: "SCORE DETAILS",
        steps: {
          "score-detail-summary": {
            eyebrow: "SCORE SUMMARY",
            title: "Separate earned points from points still available",
            body: "Earned points come from the latest returned scorecard. Available points show the unfinished required-task points that can still be earned; the update time tells you how current this view is.",
            button: "Show milestones",
            image: "/assets/toki/worktoki.png",
          },
          "score-detail-milestones": {
            eyebrow: "MILESTONES",
            title: "Use the system result for qualification",
            body: "Points are only part of graduation and Gold Teacher qualification. Review the milestone and rule areas here, while treating the system’s qualification result as final.",
            button: "Finish score guide",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.taskPath]: {
        stepLabel: "YOUR FIRST TASK",
        steps: {
          "tasks-entry": {
            eyebrow: "START A TASK",
            title: "Open the place that holds your learning path",
            body: "Click the real Tasks navigation. This page keeps the recommended next action and the full required path together.",
            image: "/assets/toki/worktoki.png",
          },
          "primary-task": {
            eyebrow: "DO THIS NEXT",
            title: "The recommended task is placed first",
            body: "The system follows the configured learning order, not a score ranking. Check the timing and due date, then open the highlighted real task.",
            image: "/assets/toki/worktoki.png",
          },
          "task-instructions": {
            eyebrow: "READ BEFORE YOU ACT",
            title: "Check the reason, instructions and completion standard together",
            body: "These cards explain why the task exists, what to do and what counts as complete. Viewing them never changes task status.",
            button: "Show the task workspace",
            image: "/assets/toki/worktoki.png",
          },
          "task-workspace": {
            eyebrow: "TASK WORKSPACE",
            title: "Continue with the controls already on the page",
            body: "This is the task’s original workspace. The guide adds no extra start button and performs no task action for you.",
            note: "Only real task controls can change progress. You can replay this guide later from the account menu.",
            button: "Finish guide",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.taskResult]: {
        stepLabel: "TASK RESULT",
        steps: {
          "task-result-summary": {
            eyebrow: "RESULT SAVED",
            title: "Confirm what the task result says",
            body: "This area shows the latest saved task outcome. The guide never changes the result or marks another task complete.",
            button: "Show score timing",
            image: "/assets/toki/worktoki.png",
          },
          "task-result-score-sync": {
            eyebrow: "SCORE UPDATE",
            title: "A score update can take a moment",
            body: "After a qualifying required task completes, My TIDE reads the settled score result. Do not repeat the task while that update is still in progress.",
            button: "Show what to do next",
            image: "/assets/toki/worktoki.png",
          },
          "task-result-next-action": {
            eyebrow: "NEXT ACTION",
            title: "Continue from the next recommendation",
            body: "Return to Tasks or My TIDE when you are ready. The next available recommendation will use your latest task status.",
            button: "Finish result guide",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.messagesTickets]: {
        stepLabel: "MESSAGES & TICKETS",
        steps: {
          "messages-overview": {
            eyebrow: "MESSAGE CENTER",
            title: "Keep real updates in one history",
            body: "This page keeps the messages and support activity that actually exist for your account. The guide does not create sample notifications.",
            button: "Show the two views",
            image: "/assets/toki/worktoki.png",
          },
          "messages-tabs": {
            eyebrow: "TWO VIEWS",
            title: "System messages and tickets stay separate",
            body: "System messages contain task and review updates. My submitted tickets keeps conversations that need operations support. You never need sample data to understand these two views.",
            button: "Finish messages guide",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.helpRoutes]: {
        stepLabel: "HELP ROUTES",
        steps: {
          "help-route-choices": {
            eyebrow: "CHOOSE A ROUTE",
            title: "Match the support route to the question",
            body: "Use the FAQ assistant for approved knowledge and submit a ticket when operations needs to step in. Ticket replies appear under Messages.",
            button: "Finish help guide",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.personalizedTaskFirst]: {
        stepLabel: "PERSONALIZED SUPPORT",
        steps: {
          "personalized-task-area": {
            eyebrow: "PERSONALIZED TASKS",
            title: "Find focused support separately from required tasks",
            body: "This area appears only when the system has a relevant improvement task for you. These tasks support improvement and do not add required-task points.",
            button: "Show why it appeared",
            image: "/assets/toki/worktoki.png",
          },
          "personalized-task-reason": {
            eyebrow: "WHY THIS TASK",
            title: "Read the reason before deciding what to do",
            body: "The reason explains the growth need behind this support. It does not change your result, submit work or perform the task for you.",
            button: "Finish personalized-task guide",
            image: "/assets/toki/happy.png",
          },
        },
      },
    },
  },
  zh: {
    overallProgress: (step, stepCount) => `第 ${step} 步，共 ${stepCount} 步`,
    languageLabel: "引导语言",
    skip: "跳过引导",
    close: "关闭引导",
    back: "上一步",
    retry: "重新保存",
    continue: "暂时继续",
    saving: "保存中…",
    findingTarget: "正在定位下一步…",
    targetFallback: "这个区域还在加载。你可以继续，引导也会继续尝试定位。",
    targetActionHint: "请点击页面中的黄色高亮区域；只有实际点击后，引导才会继续。",
    clickHighlightedArea: "点击高亮区域继续",
    syncErrorTitle: "本次选择尚未保存",
    syncErrorBody: "你可以重试，也可以先继续使用。下次登录时，这份引导可能再次出现。",
    modules: {
      [ONBOARDING_GUIDE_CODES.firstLogin]: {
        stepLabel: "全局导览",
        steps: {
          "global-navigation": {
            eyebrow: "先认清全局",
            title: "先知道训练营每个板块是做什么的",
            body: "“我的成长”看成长状态，“我的任务”看学习路径，“消息”收系统更新和支持回复。开始任务前，我们先快速走一遍真正有用的入口。",
            button: "先看我的成长",
            image: "/assets/toki/wave.png",
          },
          "my-tide-overview": {
            eyebrow: "我的成长",
            title: "在这里看成长状态和最清晰的下一步",
            body: "这里是成长首页，集中展示当前阶段、积分和 Toki 建议，不需要每次翻找整个计划。",
            button: "再看消息",
            image: "/assets/toki/worktoki.png",
          },
          "messages-entry": {
            eyebrow: "消息",
            title: "任务更新和支持回复都会到这里",
            body: "请点击页面中真实的“消息”入口。进入消息页不会自动打开消息，也不会改变已读状态。",
            image: "/assets/toki/worktoki.png",
          },
          "messages-tabs": {
            eyebrow: "两类记录",
            title: "系统消息和自己的工单分开查看",
            body: "系统消息保留任务、审核等更新；“我提交的工单”保留需要运营跟进的沟通。列表为空只代表暂时没有新内容。",
            button: "记住帮助入口",
            image: "/assets/toki/worktoki.png",
          },
          "help-entry": {
            eyebrow: "帮助",
            title: "遇到问题时，按类型选择支持方式",
            body: "常见知识问题问 FAQ 助手，需要运营介入时提交工单。全局已经认识完，接下来带你进入第一项真实任务。",
            button: "开始首次任务引导",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.myTideOverview]: {
        stepLabel: "MY TIDE 概览",
        steps: {
          "my-tide-overview": {
            eyebrow: "我的成长",
            title: "在一个页面看清今天的成长状态",
            body: "这里集中展示当前建议、最新成长情况，以及影响成长的大类来源。",
            button: "看当前建议",
            image: "/assets/toki/wave.png",
          },
          "my-tide-recommendation": {
            eyebrow: "当前建议",
            title: "每次先看一个清晰的下一步",
            body: "Toki 会随任务状态更新当前建议，你不需要每次都翻找整个计划。",
            button: "看成长维度",
            image: "/assets/toki/worktoki.png",
          },
          "my-tide-dimensions": {
            eyebrow: "成长维度",
            title: "看清成长的主要来源",
            body: "这些维度汇总最新可用结果。需要追溯时，再主动展开对应维度查看即可。",
            button: "完成概览",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.scoreDetails]: {
        stepLabel: "积分明细",
        steps: {
          "score-detail-summary": {
            eyebrow: "积分概要",
            title: "分清已获得和尚可获得",
            body: "已获得积分来自最新返回的积分卡；可获得积分表示未完成必修任务仍可获得的任务分，更新时间说明本页数据新鲜度。",
            button: "看里程碑",
            image: "/assets/toki/worktoki.png",
          },
          "score-detail-milestones": {
            eyebrow: "里程碑",
            title: "资格判断以系统结果为准",
            body: "积分只是出营和金牌资格的一部分条件。可以在这里查看里程碑与规则区域，最终以系统返回的资格结果为准。",
            button: "完成积分引导",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.taskPath]: {
        stepLabel: "开始第一项任务",
        steps: {
          "tasks-entry": {
            eyebrow: "开始任务",
            title: "从这里进入完整学习路径",
            body: "请点击页面中真实的“我的任务”入口。推荐的下一步和完整必修路径都会保留在这里。",
            image: "/assets/toki/worktoki.png",
          },
          "primary-task": {
            eyebrow: "建议先做",
            title: "当前推荐任务会放在最前面",
            body: "系统按既定学习顺序排列任务，不按分值高低排序。确认时间和截止要求后，点击高亮的真实任务进入。",
            image: "/assets/toki/worktoki.png",
          },
          "task-instructions": {
            eyebrow: "操作前先看清",
            title: "一次看懂为什么做、怎么做、怎样算完成",
            body: "这些说明来自真实任务记录。查看说明不会提交内容，也不会把任务标成已完成。",
            button: "看任务工作区",
            image: "/assets/toki/worktoki.png",
          },
          "task-workspace": {
            eyebrow: "任务工作区",
            title: "接下来使用页面原有的操作",
            body: "这里就是任务原本的工作区，引导不会额外添加开始按钮，也不会代替你执行任务。",
            note: "只有使用任务中的真实控件，进度才会变化。之后可以从账户菜单重新播放本引导。",
            button: "完成引导",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.taskResult]: {
        stepLabel: "任务结果",
        steps: {
          "task-result-summary": {
            eyebrow: "结果已保存",
            title: "先确认任务结果",
            body: "这里展示最新保存的任务结果。引导不会改变本次结果，也不会把其他任务标成已完成。",
            button: "看积分更新",
            image: "/assets/toki/worktoki.png",
          },
          "task-result-score-sync": {
            eyebrow: "积分更新",
            title: "积分更新可能需要一点时间",
            body: "符合条件的必修任务完成后，My TIDE 会读取结算后的积分结果。更新中无需重复执行任务。",
            button: "看下一步",
            image: "/assets/toki/worktoki.png",
          },
          "task-result-next-action": {
            eyebrow: "下一步",
            title: "从新的建议继续",
            body: "准备好后返回我的任务或 My TIDE。下一项可用建议会使用你的最新任务状态。",
            button: "完成结果引导",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.messagesTickets]: {
        stepLabel: "消息与工单",
        steps: {
          "messages-overview": {
            eyebrow: "消息中心",
            title: "真实更新会保留在同一份历史中",
            body: "这个页面只保留账号真实存在的消息和支持记录，引导不会创建示例通知。",
            button: "看两种视图",
            image: "/assets/toki/worktoki.png",
          },
          "messages-tabs": {
            eyebrow: "两种视图",
            title: "系统消息和工单分开查看",
            body: "系统消息保留任务与审核更新；“我提交的工单”保留需要运营支持的沟通。即使现在没有消息，也能先记住这两个入口。",
            button: "完成消息引导",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.helpRoutes]: {
        stepLabel: "帮助方式",
        steps: {
          "help-route-choices": {
            eyebrow: "选择方式",
            title: "按问题选择合适的求助方式",
            body: "常见知识问题使用 FAQ 助手；需要运营介入时提交工单，后续回复会出现在消息中。",
            button: "完成帮助引导",
            image: "/assets/toki/happy.png",
          },
        },
      },
      [ONBOARDING_GUIDE_CODES.personalizedTaskFirst]: {
        stepLabel: "个性化支持",
        steps: {
          "personalized-task-area": {
            eyebrow: "个性化任务",
            title: "针对性支持与必修任务分开展示",
            body: "只有系统产生了相关改善任务时，这个区域才会出现。这类任务用于改善支持，不增加必修任务积分。",
            button: "看为什么出现",
            image: "/assets/toki/worktoki.png",
          },
          "personalized-task-reason": {
            eyebrow: "任务原因",
            title: "先看原因，再决定怎样继续",
            body: "任务原因会说明这项支持对应的成长需要。查看引导不会改变结果、提交内容或代替你执行任务。",
            button: "完成个性化任务引导",
            image: "/assets/toki/happy.png",
          },
        },
      },
    },
  },
};

const focusableSelector = [
  "button:not([disabled])",
  "[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function analyticsProperties(guideCode, guideVersion, step, extra = {}) {
  return {
    guideCode: guideCode || ONBOARDING_GUIDE_CODE,
    guideVersion,
    onboardingStep: step + 1,
    ...extra,
  };
}

function isRendered(element) {
  if (!element || typeof element.getBoundingClientRect !== "function") return false;
  const style = window.getComputedStyle(element);
  if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
    return false;
  }
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function intersectsViewport(rect) {
  return rect.bottom > 0
    && rect.right > 0
    && rect.top < window.innerHeight
    && rect.left < window.innerWidth;
}

function isViewportAnchored(element) {
  for (let current = element; current && current !== document.body; current = current.parentElement) {
    const position = window.getComputedStyle(current).position;
    if (position === "fixed" || position === "sticky") return true;
  }
  return false;
}

function intersectsGuideViewport(element, rect) {
  if (!intersectsViewport(rect)) return false;
  if (window.innerWidth > 620 || isViewportAnchored(element)) return true;
  const safeTop = 72;
  const safeBottom = Math.max(safeTop, window.innerHeight - 88);
  return rect.bottom > safeTop && rect.top < safeBottom;
}

function resolveVisibleTarget(targetName) {
  const candidates = [...document.querySelectorAll(
    `[data-onboarding-target="${targetName}"]`,
  )].filter(isRendered);
  const viewportTarget = window.innerWidth <= 620 ? "mobile" : "desktop";
  const preferred = candidates.filter(
    (candidate) => candidate.dataset.onboardingViewport === viewportTarget,
  );
  const shared = candidates.filter((candidate) => !candidate.dataset.onboardingViewport);
  if (preferred.length > 0) {
    return preferred.find((candidate) => (
      intersectsGuideViewport(candidate, candidate.getBoundingClientRect())
    )) || preferred[0];
  }
  return shared.find((candidate) => (
    intersectsGuideViewport(candidate, candidate.getBoundingClientRect())
  )) || shared[0] || candidates[0] || null;
}

function resolveVisibleAction(actionId, preferredTarget = null) {
  if (!actionId) return null;
  const selector = `[data-onboarding-action="${actionId}"]`;
  if (preferredTarget?.matches?.(selector) && isRendered(preferredTarget)) {
    return preferredTarget;
  }
  const nestedTarget = preferredTarget?.querySelector?.(selector);
  if (nestedTarget && isRendered(nestedTarget)) return nestedTarget;
  return [...document.querySelectorAll(selector)].find(isRendered) || null;
}

function visibleGuideRect(target) {
  const rect = target.getBoundingClientRect();
  const maximumHeight = Number(target.dataset.onboardingMaxHeight);
  if (!Number.isFinite(maximumHeight) || maximumHeight <= 0 || rect.height <= maximumHeight) {
    return rect;
  }
  return {
    top: rect.top,
    left: rect.left,
    right: rect.right,
    bottom: rect.top + maximumHeight,
    width: rect.width,
    height: maximumHeight,
  };
}

function rectsMatch(left, right) {
  if (!left || !right) return false;
  return ["top", "left", "right", "bottom", "width", "height"]
    .every((key) => Math.abs(left[key] - right[key]) < 0.5);
}

function viewportNow() {
  return { width: window.innerWidth, height: window.innerHeight };
}

function GuideProgress({ current, stepCount }) {
  return (
    <div
      className="onboarding-progress"
      style={{ "--onboarding-step-count": stepCount }}
      aria-hidden="true"
    >
      {Array.from({ length: stepCount }, (_, index) => (
        <span className={index <= current ? "is-active" : ""} key={index} />
      ))}
    </div>
  );
}

export default function OnboardingGuide({
  open,
  mode = "automatic",
  language = "en",
  guideCode = ONBOARDING_GUIDE_CODE,
  guideVersion,
  analyticsEnabled = true,
  returnFocusElement = null,
  onLanguageChange,
  onAcknowledge,
  onClose,
  onOpenMessages,
  onOpenTasks,
  onOpenMyTide,
  onOpenPrimaryTask,
}) {
  const [state, dispatch] = useReducer(
    reduceOnboardingGuide,
    initialOnboardingGuideState,
  );
  const [spotlight, setSpotlight] = useState({
    status: "idle",
    rect: null,
    viewport: { width: 0, height: 0 },
  });
  const [coachmarkSize, setCoachmarkSize] = useState(DEFAULT_COACHMARK_SIZE);
  const dialogRef = useRef(null);
  const restoreFocusRef = useRef(null);
  const stateRef = useRef(state);
  const targetElementRef = useRef(null);
  const localized = guideCopy[language === "zh" ? "zh" : "en"];
  const guideDefinition = getOnboardingGuideDefinition(guideCode);
  const effectiveGuideCode = guideDefinition.guideCode;
  const effectiveGuideVersion = guideVersion || guideDefinition.guideVersion;
  const guideSteps = getOnboardingSteps(effectiveGuideCode);
  const stepCount = getOnboardingStepCount(effectiveGuideCode);
  const currentStepIndex = Math.min(state.step, stepCount - 1);
  const localizedModule = localized.modules[effectiveGuideCode]
    || localized.modules[ONBOARDING_GUIDE_CODE];
  const stepDefinition = guideSteps[currentStepIndex];
  const step = localizedModule.steps[stepDefinition?.id];
  const targetName = stepDefinition?.target;
  const requiresTargetAction = stepDefinition?.advanceOn === ONBOARDING_ADVANCE_ON.targetClick;
  const isReplay = mode === "replay";
  const busy = state.phase === "submitting";

  useEffect(() => {
    dispatch({ type: "RESET" });
  }, [effectiveGuideCode]);

  const closeGuide = useCallback(() => {
    onClose();
  }, [onClose]);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    if (!open) {
      if (
        stateRef.current.step !== 0
        || stateRef.current.phase !== "idle"
        || stateRef.current.pendingCommand
      ) {
        dispatch({ type: "RESET" });
      }
      return undefined;
    }
    restoreFocusRef.current = returnFocusElement || document.activeElement;
    return () => restoreFocusRef.current?.focus?.();
  }, [open, returnFocusElement]);

  useEffect(() => {
    if (!open || !analyticsEnabled) return undefined;
    if (isReplay) {
      trackProductEvent("ONBOARDING_REPLAYED", {
        properties: analyticsProperties(effectiveGuideCode, effectiveGuideVersion, 0, {
          entrySource: "ACCOUNT_MENU",
        }),
      });
    } else {
      trackProductEvent("ONBOARDING_SHOWN", {
        properties: analyticsProperties(effectiveGuideCode, effectiveGuideVersion, 0, {
          entrySource: effectiveGuideCode,
        }),
      });
    }
    return undefined;
  }, [analyticsEnabled, effectiveGuideCode, effectiveGuideVersion, isReplay, open]);

  useEffect(() => {
    if (!open || !analyticsEnabled) return;
    trackProductEvent("ONBOARDING_STEP_VIEWED", {
      properties: analyticsProperties(
        effectiveGuideCode,
        effectiveGuideVersion,
        currentStepIndex,
        { entrySource: isReplay ? "ACCOUNT_MENU" : effectiveGuideCode },
      ),
    });
  }, [analyticsEnabled, currentStepIndex, effectiveGuideCode, effectiveGuideVersion, isReplay, open]);

  useEffect(() => {
    if (!open || !targetName) {
      targetElementRef.current = null;
      setSpotlight({ status: "idle", rect: null, viewport: { width: 0, height: 0 } });
      return undefined;
    }

    let disposed = false;
    let retryTimer = null;
    let missingTimer = null;
    let resizeObserver = null;
    let mutationObserver = null;
    let observedElement = null;
    let scrollRequestedFor = null;

    const scheduleRetry = () => {
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(measure, TARGET_RETRY_DELAY_MS);
    };

    const scheduleFallback = () => {
      if (missingTimer !== null) return;
      missingTimer = window.setTimeout(() => {
        if (disposed || targetElementRef.current) return;
        setSpotlight({ status: "fallback", rect: null, viewport: viewportNow() });
      }, TARGET_FALLBACK_DELAY_MS);
    };

    const clearFallback = () => {
      window.clearTimeout(missingTimer);
      missingTimer = null;
    };

    const observeTarget = (target) => {
      if (observedElement === target || typeof ResizeObserver === "undefined") return;
      resizeObserver?.disconnect();
      observedElement = target;
      resizeObserver = new ResizeObserver(() => measure());
      resizeObserver.observe(target);
    };

    function measure() {
      if (disposed) return;
      const target = resolveVisibleTarget(targetName);
      if (!target) {
        targetElementRef.current = null;
        scheduleFallback();
        return;
      }

      const rawRect = target.getBoundingClientRect();
      if (!intersectsGuideViewport(target, rawRect)) {
        targetElementRef.current = null;
        scheduleFallback();
        if (scrollRequestedFor !== target) {
          scrollRequestedFor = target;
          const behavior = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
            ? "auto"
            : "smooth";
          if (target.dataset.onboardingScrollBlock === "start") {
            const safeTop = window.innerWidth <= 620 ? 84 : 12;
            window.scrollTo({
              top: Math.max(0, window.scrollY + rawRect.top - safeTop),
              behavior,
            });
          } else {
            target.scrollIntoView({ block: "center", inline: "nearest", behavior });
          }
        }
        scheduleRetry();
        return;
      }

      clearFallback();
      targetElementRef.current = target;
      observeTarget(target);
      const viewport = viewportNow();
      const rect = calculateSpotlightRect(
        visibleGuideRect(target),
        viewport,
        { padding: requiresTargetAction ? 0 : 10 },
      );
      setSpotlight((current) => (
        current.status === "found"
        && rectsMatch(current.rect, rect)
        && current.viewport.width === viewport.width
        && current.viewport.height === viewport.height
          ? current
          : { status: "found", rect, viewport }
      ));
    }

    setSpotlight({ status: "searching", rect: null, viewport: viewportNow() });
    if (typeof MutationObserver !== "undefined" && document.body) {
      mutationObserver = new MutationObserver(measure);
      mutationObserver.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["class", "style", "disabled"],
      });
    }
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);

    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
      clearFallback();
      resizeObserver?.disconnect();
      mutationObserver?.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
      targetElementRef.current = null;
    };
  }, [currentStepIndex, open, requiresTargetAction, targetName]);

  useEffect(() => {
    if (!open || spotlight.status === "searching") return undefined;
    const coachmark = dialogRef.current;
    if (!coachmark) return undefined;
    const updateSize = () => {
      const rect = coachmark.getBoundingClientRect();
      setCoachmarkSize({ width: rect.width, height: rect.height });
    };
    updateSize();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(updateSize);
    observer.observe(coachmark);
    return () => observer.disconnect();
  }, [currentStepIndex, open, spotlight.status]);

  useEffect(() => {
    if (!open || (currentStepIndex > 0 && spotlight.status === "searching")) return undefined;
    const frame = window.requestAnimationFrame(() => dialogRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [currentStepIndex, open, spotlight.status]);

  const runCommand = useCallback(async (command) => {
    dispatch({ type: "SUBMIT", command });
    try {
      await onAcknowledge(command.outcome, command.idempotencyKey);
      if (analyticsEnabled) {
        trackProductEvent(
          command.outcome === ONBOARDING_OUTCOMES.skipped
            ? "ONBOARDING_SKIPPED"
            : "ONBOARDING_COMPLETED",
          {
            properties: analyticsProperties(
              effectiveGuideCode,
              effectiveGuideVersion,
              stateRef.current.step,
              { result: "SUCCESS" },
            ),
          },
        );
      }
      closeGuide();
    } catch {
      dispatch({ type: "FAILED" });
    }
  }, [analyticsEnabled, closeGuide, effectiveGuideCode, effectiveGuideVersion, onAcknowledge]);

  const startCommand = useCallback((outcome) => {
    if (busy) return;
    if (outcome === ONBOARDING_OUTCOMES.completed && targetElementRef.current) {
      restoreFocusRef.current = targetElementRef.current;
    }
    if (isReplay) {
      closeGuide();
      return;
    }
    void runCommand({
      outcome,
      idempotencyKey: newCommandKey("onboarding-acknowledge"),
    });
  }, [busy, closeGuide, isReplay, runCommand]);

  const continueWithoutSync = useCallback(() => {
    const command = stateRef.current.pendingCommand;
    if (!command) return;
    if (analyticsEnabled) {
      trackProductEvent(
        command.outcome === ONBOARDING_OUTCOMES.skipped
          ? "ONBOARDING_SKIPPED"
          : "ONBOARDING_COMPLETED",
        {
          properties: analyticsProperties(
            effectiveGuideCode,
            effectiveGuideVersion,
            stateRef.current.step,
            { result: "ACKNOWLEDGEMENT_FAILED" },
          ),
        },
      );
    }
    closeGuide();
  }, [analyticsEnabled, closeGuide, effectiveGuideCode, effectiveGuideVersion]);

  const handleGuideKeyDown = useCallback((event) => {
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (busy) return;
      if (onboardingEscapeAction(mode) === "CLOSE") closeGuide();
      else startCommand(ONBOARDING_OUTCOMES.skipped);
      return;
    }
    const dialog = dialogRef.current;
    if (!dialog) return;
    const target = requiresTargetAction
      ? resolveVisibleAction(stepDefinition?.actionId, targetElementRef.current)
      : null;
    if (event.key !== "Tab") return;
    const dialogFocusable = [...dialog.querySelectorAll(focusableSelector)].filter(isRendered);
    if (dialogFocusable.length === 0 && !target) {
      event.preventDefault();
      dialog.focus();
      return;
    }
    if (target) {
      const first = dialogFocusable[0] || target;
      const last = dialogFocusable[dialogFocusable.length - 1] || target;
      if (document.activeElement === target) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (!dialog.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
        event.preventDefault();
        target.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        target.focus();
      }
      return;
    }
    const first = dialogFocusable[0];
    const last = dialogFocusable[dialogFocusable.length - 1];
    if (!dialog.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
    } else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }, [busy, closeGuide, mode, open, requiresTargetAction, startCommand, stepDefinition?.actionId]);

  useEffect(() => {
    if (!open) return undefined;
    document.addEventListener("keydown", handleGuideKeyDown, true);
    return () => document.removeEventListener("keydown", handleGuideKeyDown, true);
  }, [handleGuideKeyDown, open]);

  const openGuideRoute = useCallback((route) => {
    if (route === "MY_TIDE") onOpenMyTide?.();
    if (route === "MESSAGES") onOpenMessages?.();
    if (route === "TASKS") onOpenTasks?.();
    if (route === "TASK_DETAIL") onOpenPrimaryTask?.();
  }, [onOpenMessages, onOpenMyTide, onOpenPrimaryTask, onOpenTasks]);

  const moveToStep = useCallback((nextStep, actionType, interaction = {}) => {
    const route = onboardingRouteIntent(
      currentStepIndex,
      nextStep,
      interaction,
      effectiveGuideCode,
    );
    if (route) openGuideRoute(route);
    dispatch({ type: actionType, stepCount });
  }, [currentStepIndex, effectiveGuideCode, openGuideRoute, stepCount]);
  const targetFlowRef = useRef({ moveToStep, startCommand });
  targetFlowRef.current = { moveToStep, startCommand };

  const handleBack = useCallback(() => {
    if (currentStepIndex === 0) return;
    moveToStep(currentStepIndex - 1, "BACK");
  }, [currentStepIndex, moveToStep]);

  const handlePrimaryControl = useCallback(() => {
    if (requiresTargetAction) return;
    const result = resolveOnboardingInteraction(currentStepIndex, {
      kind: ONBOARDING_ADVANCE_ON.nextButton,
    }, effectiveGuideCode);
    if (result === "NEXT") {
      moveToStep(currentStepIndex + 1, "NEXT", {
        kind: ONBOARDING_ADVANCE_ON.nextButton,
      });
    }
    if (result === "COMPLETE") startCommand(ONBOARDING_OUTCOMES.completed);
  }, [currentStepIndex, effectiveGuideCode, moveToStep, requiresTargetAction, startCommand]);

  useEffect(() => {
    if (!open || !requiresTargetAction || !stepDefinition?.actionId) return undefined;
    const capturedStep = currentStepIndex;
    const actionId = stepDefinition.actionId;
    let activationTimer = null;
    const handleTargetClick = (event) => {
      const actionTarget = event.target?.closest?.(
        `[data-onboarding-action="${actionId}"]`,
      );
      if (
        !actionTarget
        || !targetElementRef.current
        || (
          actionTarget !== targetElementRef.current
          && !targetElementRef.current.contains(actionTarget)
        )
        || actionTarget.disabled
        || actionTarget.getAttribute("aria-disabled") === "true"
        || activationTimer !== null
      ) {
        return;
      }
      activationTimer = window.setTimeout(() => {
        if (stateRef.current.step !== capturedStep) {
          activationTimer = null;
          return;
        }
        const result = resolveOnboardingInteraction(capturedStep, {
          kind: ONBOARDING_ADVANCE_ON.targetClick,
          actionId,
        }, effectiveGuideCode);
        if (result === "NEXT") {
          targetFlowRef.current.moveToStep(capturedStep + 1, "NEXT", {
            kind: ONBOARDING_ADVANCE_ON.targetClick,
            targetNavigates: true,
          });
        } else if (result === "COMPLETE") {
          targetFlowRef.current.startCommand(ONBOARDING_OUTCOMES.completed);
        } else {
          activationTimer = null;
        }
      }, 0);
    };
    document.addEventListener("click", handleTargetClick, true);
    return () => {
      window.clearTimeout(activationTimer);
      document.removeEventListener("click", handleTargetClick, true);
    };
  }, [currentStepIndex, effectiveGuideCode, open, requiresTargetAction, stepDefinition?.actionId]);

  useEffect(() => {
    if (!open || !requiresTargetAction || spotlight.status !== "found") return undefined;
    const target = resolveVisibleAction(
      stepDefinition?.actionId,
      targetElementRef.current,
    );
    if (!target) return undefined;
    const previousDescription = target.getAttribute("aria-describedby");
    const descriptions = new Set(
      `${previousDescription || ""} onboarding-description`.trim().split(/\s+/),
    );
    target.setAttribute("aria-describedby", [...descriptions].join(" "));
    return () => {
      if (previousDescription === null) target.removeAttribute("aria-describedby");
      else target.setAttribute("aria-describedby", previousDescription);
    };
  }, [currentStepIndex, open, requiresTargetAction, spotlight.rect, spotlight.status, stepDefinition?.actionId]);

  const coachmarkPosition = useMemo(() => {
    if (spotlight.status !== "found" || !spotlight.rect) return null;
    return calculateCoachmarkPosition(
      spotlight.rect,
      spotlight.viewport,
      coachmarkSize,
    );
  }, [coachmarkSize, spotlight]);

  const maskStyles = useMemo(() => {
    if (spotlight.status !== "found" || !spotlight.rect) return [];
    const { rect, viewport } = spotlight;
    return [
      { top: 0, left: 0, width: viewport.width, height: rect.top },
      { top: rect.top, left: 0, width: rect.left, height: rect.height },
      { top: rect.top, left: rect.right, width: Math.max(0, viewport.width - rect.right), height: rect.height },
      { top: rect.bottom, left: 0, width: viewport.width, height: Math.max(0, viewport.height - rect.bottom) },
    ];
  }, [spotlight]);

  if (!open) return null;

  const languageControl = (
    <div className="onboarding-language" aria-label={localized.languageLabel}>
      <button
        type="button"
        aria-pressed={language !== "zh"}
        onClick={() => onLanguageChange("en")}
      >
        EN
      </button>
      <button
        type="button"
        aria-pressed={language === "zh"}
        onClick={() => onLanguageChange("zh")}
      >
        中文
      </button>
    </div>
  );

  const exitControl = isReplay ? (
    <button
      className="onboarding-close"
      type="button"
      onClick={closeGuide}
      aria-label={localized.close}
    >
      <X size={20} />
    </button>
  ) : (
    <button
      className="onboarding-skip"
      type="button"
      disabled={busy}
      onClick={() => startCommand(ONBOARDING_OUTCOMES.skipped)}
    >
      {localized.skip}
    </button>
  );

  const syncError = state.phase === "error" ? (
    <div className="onboarding-sync-error" role="alert">
      <div>
        <strong>{localized.syncErrorTitle}</strong>
        <p>{localized.syncErrorBody}</p>
      </div>
      <div>
        <button type="button" onClick={continueWithoutSync}>{localized.continue}</button>
        <button
          className="is-primary"
          type="button"
          onClick={() => void runCommand(state.pendingCommand)}
        >
          {localized.retry}
        </button>
      </div>
    </div>
  ) : null;

  if (spotlight.status === "searching" || spotlight.status === "idle") {
    return (
      <div className="onboarding-layer">
        <div className="onboarding-search-backdrop" />
        <div className="onboarding-search-status" role="status" aria-live="polite">
          <span aria-hidden="true" />
          {localized.findingTarget}
        </div>
      </div>
    );
  }

  const spotlightFound = spotlight.status === "found" && spotlight.rect;
  const coachmarkStyle = spotlightFound && coachmarkPosition
    ? { top: coachmarkPosition.top, left: coachmarkPosition.left }
    : undefined;
  const stepIcons = {
    "global-navigation": Compass,
    "messages-entry": ListChecks,
    "help-entry": Compass,
    "tasks-entry": Compass,
    "primary-task": ListChecks,
    "task-instructions": ClipboardText,
    "task-workspace": CheckCircle,
    "my-tide-overview": Compass,
    "my-tide-recommendation": Sparkle,
    "my-tide-dimensions": Star,
    "score-detail-summary": Star,
    "score-detail-milestones": Target,
    "task-result-summary": CheckCircle,
    "task-result-score-sync": Compass,
    "task-result-next-action": ArrowRight,
    "messages-overview": Compass,
    "messages-tabs": ListChecks,
    "help-route-choices": ListChecks,
    "personalized-task-area": Sparkle,
    "personalized-task-reason": Star,
  };
  const StepIcon = stepIcons[stepDefinition?.id] || Sparkle;

  return (
    <div className="onboarding-layer">
      {spotlightFound ? (
        <>
          {maskStyles.map((style, index) => (
            <div className="onboarding-spotlight-mask" style={style} key={index} />
          ))}
          <div
            className={`onboarding-spotlight-hole${requiresTargetAction ? " is-target-action" : ""}`}
            style={{
              top: spotlight.rect.top,
              left: spotlight.rect.left,
              width: spotlight.rect.width,
              height: spotlight.rect.height,
            }}
            aria-hidden="true"
          />
        </>
      ) : (
        <div className="onboarding-centered-backdrop" />
      )}

      <section
        ref={dialogRef}
        className={`onboarding-coachmark${spotlightFound ? "" : " is-fallback"}`}
        data-placement={coachmarkPosition?.placement || "center"}
        style={coachmarkStyle}
        role="dialog"
        aria-modal={!requiresTargetAction}
        aria-labelledby="onboarding-title"
        aria-describedby="onboarding-description"
        aria-busy={busy}
        tabIndex={-1}
      >
        <header className="onboarding-coachmark-topbar">
          <div>
            <span>{localizedModule.stepLabel}</span>
            <small
              aria-live="polite"
              aria-label={localized.overallProgress(currentStepIndex + 1, stepCount)}
            >
              {localized.overallProgress(currentStepIndex + 1, stepCount)}
            </small>
          </div>
          <div className="onboarding-topbar-actions">
            {languageControl}
            {exitControl}
          </div>
        </header>
        <GuideProgress current={currentStepIndex} stepCount={stepCount} />
        <div className="onboarding-coachmark-body">
          <div className="onboarding-coachmark-heading">
            <span className="onboarding-step-icon" aria-hidden="true">
              <StepIcon size={22} weight="duotone" />
            </span>
            <img src={publicAsset(step.image)} alt="" aria-hidden="true" />
          </div>
          <span className="onboarding-eyebrow">{step.eyebrow}</span>
          <h2 id="onboarding-title">{step.title}</h2>
          <p id="onboarding-description">{step.body}</p>
          {requiresTargetAction && spotlightFound && (
            <p className="onboarding-target-action-hint">{localized.targetActionHint}</p>
          )}
          {!spotlightFound && <p className="onboarding-target-fallback">{localized.targetFallback}</p>}
          {step.note && <p className="onboarding-note">{step.note}</p>}
        </div>
        {syncError || (
          <footer className="onboarding-coachmark-footer">
            <button
              className="onboarding-secondary"
              type="button"
              disabled={busy || currentStepIndex === 0}
              onClick={handleBack}
            >
              <ArrowLeft size={17} /> {localized.back}
            </button>
            {requiresTargetAction ? (
              <span className="onboarding-click-instruction" aria-live="polite">
                <Target size={18} weight="bold" />
                {localized.clickHighlightedArea}
              </span>
            ) : (
              <button
                className="onboarding-primary"
                type="button"
                disabled={busy}
                onClick={handlePrimaryControl}
              >
                {busy ? localized.saving : step.button}
                {currentStepIndex === stepCount - 1
                  ? <CheckCircle size={18} weight="bold" />
                  : <ArrowRight size={18} />}
              </button>
            )}
          </footer>
        )}
      </section>
    </div>
  );
}
