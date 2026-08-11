import {
  Fragment,
  Suspense,
  createContext,
  lazy,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Link,
  Navigate,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  ArrowsLeftRight,
  BookOpen,
  CalendarCheck,
  CalendarBlank,
  ChatCircleDots,
  Camera,
  CaretDown,
  Check,
  CheckCircle,
  ChartLineUp,
  ClipboardText,
  Clock,
  Flag,
  Gauge,
  Globe,
  GraduationCap,
  Hourglass,
  Info,
  ListChecks,
  Lock,
  LockKey,
  MapPin,
  Medal,
  Monitor,
  Question,
  SealCheck,
  ShieldCheck,
  Sparkle,
  Star,
  Tag,
  Target,
  Timer,
  UserCircle,
  UsersThree,
  WifiHigh,
  X,
} from "@phosphor-icons/react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { Toki as MotionToki } from "./components/UI";
import KuozhiProgressCard from "./features/task-content/KuozhiProgressCard";
import OnboardingGuide from "./components/OnboardingGuide";
import GuideLibraryDialog from "./components/GuideLibraryDialog";
import { logoutTeacher, requestPasswordReset } from "./api/auth-api";
import { restoreSession } from "./api/api-client";
import {
  acknowledgeOnboarding,
  getAllCourses,
  getCourses,
  getG01Review,
  getNotifications,
  getOnboardingStatus,
  getTeacherProfile,
  getTideSummary,
  markNotificationClicked,
  markNotificationRead,
} from "./api/tide-api";
import { getTask, listTasks, viewTask } from "./api/task-api";
import { listSupportTickets } from "./api/support-ticket-api";
import {
  beginPageAnalytics,
  observeEffectiveImpressions,
  setAnalyticsLanguage,
  taskEntryAttribution,
  trackProductEvent,
} from "./analytics/product-analytics";
import { publicAsset } from "./public-assets";
import {
  adaptTaskContext,
  composeGrowthMapTasks,
  composePresentationTasks,
  sortTaskContexts,
  taskCodeToRouteId,
} from "./task-adapter";
import { localizeApiError } from "./api-error-copy";
import {
  buildGrowthTip,
  stageIndexForAvailableTasks,
  stageIndexFromPathSearch,
} from "./growth-tip";
import {
  clockwiseOrbitSlot,
  nextClockwiseStageIndex,
  normalizeStageIndex,
} from "./growth-map-rotation";
import {
  AI_HELP_DESKTOP_SIZE,
  AI_HELP_MOBILE_BREAKPOINT,
  AI_HELP_MOBILE_SIZE,
  clampAiHelpPosition,
  defaultAiHelpPosition,
  getAiHelpBounds,
} from "./ai-help-position";
import {
  courseScoreDimensionGroups,
  courseScoreSourceLabels,
  mergeScorecardCourseSources,
  lessonLifecycleStatusLabel,
  visibleCourseIndicators,
} from "./course-score-sources";
import {
  buildLessonPageItems,
  clampLessonPage,
  LESSONS_PER_PAGE,
} from "./lesson-pagination";
import { pollScorecard } from "./score-sync";
import {
  presentStageAction,
  presentScorecardRules,
  scoreStageStates,
} from "./score-presentation";
import { createNotificationRequestQueue } from "./notification-refresh";
import { localizeNotification } from "./notification-copy";
import { buildCourseAbilityTags } from "./course-ability-tags";
import {
  getOnboardingGuide,
  normalizeOnboardingCatalog,
  normalizeOnboardingStatus,
  ONBOARDING_DEFAULT_VERSION,
  ONBOARDING_GUIDE_CODE,
  ONBOARDING_GUIDE_CODES,
  ONBOARDING_GUIDE_ORDER,
  ONBOARDING_OUTCOMES,
  shouldLoadOnboarding,
} from "./onboarding-guide";
import { I18nProvider, localizeStage, localizeTask } from "./i18n";
import { describeDataError } from "./data-error";
import { loadTaskContexts } from "./task-context-loader";
import {
  dimensionCatalog,
  fixedTaskCatalog,
  localizeCatalogValue as localizeTitValue,
  scoreMilestones,
  stageDescriptions,
} from "./live-catalog";

gsap.registerPlugin(useGSAP);

const IntegratedTaskFlow = lazy(() => import("./components/IntegratedTaskFlow"));
const AuthScreen = lazy(() => import("./components/AuthScreen"));
const FaqHelpDialog = lazy(() => import("./components/FaqHelpDialog"));
const MessageCenter = lazy(() => import("./components/MessageCenter"));

const copy = (language, english, chinese) =>
  language === "zh" ? chinese : english;

function LazyPanelFallback() {
  return <div className="lazy-panel-fallback" aria-busy="true" />;
}

let growthPathReturnSnapshot = null;

function TaskEvidenceDetails({ facts = [], language, standalone = false }) {
  const relatedCourses = facts.filter((fact) => fact.lessonId);
  const courseSummaries = new Set(
    relatedCourses.flatMap((fact) => [fact.value, fact.valueZh].filter(Boolean)),
  );
  const observations = facts.filter(
    (fact) => !fact.lessonId && !courseSummaries.has(fact.value) && !courseSummaries.has(fact.valueZh),
  );

  if (relatedCourses.length === 0 && observations.length === 0) return null;

  return (
    <div
      className={`task-evidence-details${standalone ? " standalone" : ""}`}
      aria-label={copy(language, "Task evidence", "任务依据")}
    >
      {observations.map((fact, index) => (
        <div className="task-evidence-observation" key={`${fact.label}-${fact.value}-${index}`}>
          <strong>{copy(language, fact.label, fact.labelZh || fact.label)}</strong>
          <p>{copy(language, fact.value, fact.valueZh || fact.value)}</p>
        </div>
      ))}
      {relatedCourses.length > 0 && (
        <div className="task-related-courses">
          <strong>{copy(language, "Related classes", "关联课程")}</strong>
          <ul>
            {relatedCourses.map((course, index) => (
              <li key={`${course.lessonId}-${index}`}>
                <span>
                  {copy(language, "Class", "课程")} <b>{course.lessonId}</b>
                </span>
                <p>{copy(language, course.value, course.valueZh || course.value)}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

const taskAnalyticsAttributes = (task, entrySource, displayPosition) => (
  task?.backendId
    ? {
        "data-analytics-task-card": "true",
        "data-analytics-task-assignment-id": task.backendId,
        "data-analytics-task-code": task.taskCode,
        "data-analytics-task-type": task.taskCategory,
        "data-analytics-entry-source": entrySource,
        "data-analytics-display-position": displayPosition,
      }
    : {}
);
const COURSE_MATRIX_PAGE_SIZE = 6;
const TeacherContext = createContext(null);
const useTeacher = () => useContext(TeacherContext);
const taskIcons = {
  "profile-credentials": UserCircle,
  "device-network": WifiHigh,
  "platform-policies": SealCheck,
  "student-types": UsersThree,
  "lesson-preparation": ClipboardText,
  "ttp-orientation": BookOpen,
  "me-culture": GraduationCap,
  "reliability-training": Target,
  "cocos-training": BookOpen,
  "set-fundamentals": GraduationCap,
  "classroom-environment-coaching": Monitor,
  "classroom-quality-reminder": WifiHigh,
  "attendance-reliability-refresher": Target,
  "lesson-memo-rules-learning": ClipboardText,
  "feedback-self-study": BookOpen,
  "feedback-interaction-engagement": UsersThree,
  "feedback-correction-explanation": BookOpen,
  "feedback-speaking-pace": Gauge,
  "feedback-scaffolding-language": BookOpen,
  "feedback-teaching-aids": Sparkle,
  "feedback-student-response": ChatCircleDots,
  "feedback-pronunciation": Globe,
  "feedback-professionalism": ShieldCheck,
  "feedback-blacklist-review": ClipboardText,
};
const dimensionIcons = {
  feedback: UsersThree,
  reliability: ShieldCheck,
  quality: GraduationCap,
  availability: ChartLineUp,
  "required-tasks": ListChecks,
};
const scoreRuleGroupIcons = {
  USER_FEEDBACK: UsersThree,
  RELIABILITY: ShieldCheck,
  CLASS_QUALITY: GraduationCap,
  CAPACITY: ChartLineUp,
  NEW_TEACHER_TASK: ListChecks,
};
const statusLabels = {
  available: ["To do", "待完成"],
  started: ["In progress", "进行中"],
  submitting: ["In progress", "进行中"],
  submitted: ["Submitted", "已提交"],
  verifying: ["Under review", "审核中"],
  completed: ["Completed", "已完成"],
  retry_required: ["Not passed", "需要处理"],
  failed_final: ["Not passed", "需要处理"],
  waived: ["No action needed", "无需完成"],
  cancelled: ["Task cancelled", "任务已取消"],
  expired: ["Ended", "已结束"],
  preview: ["Preview", "可以先看"],
  sync_pending: ["Waiting for review result", "等待审核结果"],
};
const workspaceMeta = {
  profile_credentials: ["Profile and TESOL completion", "档案与 TESOL 完成", "Complete all four conditions in one place.", "在一个页面内完成全部四项条件。"],
  learning_checklist: ["Completion checklist", "完成步骤", "Tick every item, then confirm to record this task as complete.", "勾选全部步骤，再点击“完成任务”。"],
  external_status: ["Review status", "审核状态", "Check the latest available result.", "查看最新审核结果。"],
  readiness_photo: ["Two-part lesson preparation", "首课两项准备", "Use one photo for the four AI checks and confirm courseware preparation in either order.", "任意顺序完成照片四项 AI 检测和课件准备确认。"],
  upload_review: ["Submit for review", "上传材料", "Follow the steps below to submit your material for review.", "按照下方要求提交材料并查看审核结果。"],
  embedded_course: ["In-platform course", "站内课程", "Complete every learning section inside this task page.", "在当前任务页内完成全部学习内容。"],
  guidance_acknowledgement: ["Result and next step", "结果与下一步", "Review the affected classes, complete the AC/ACE check and record the result.", "查看触发课程，完成 AC／ACE 检测并记录结果。"],
  factual_response: ["Factual response", "事实说明", "Describe the verifiable classroom facts, save a draft if needed, then submit it for operational review.", "填写可核实的课堂事实；需要时先保存草稿，再提交运营复核。"],
  content_pending: ["Content pending", "内容待补充", "This required task is confirmed; its official content and completion method are still being prepared.", "这项必修任务已经确认，正式内容和完成方式仍在准备中。"],
};

const stageMapVisuals = [
  {
    unlocked: publicAsset("/assets/growth-maps/module-01-first-lesson-prep-v4.png"),
    checkpoints: [
      { id: "profile-credentials", image: publicAsset("/assets/checkpoints/m01-profile-credentials.png"), x: 34, y: 29 },
      { id: "platform-policies", image: publicAsset("/assets/checkpoints/m01-platform-policies.png"), x: 71, y: 29 },
      { id: "lesson-preparation", image: publicAsset("/assets/checkpoints/m01-lesson-preparation.png"), x: 29, y: 52 },
      { id: "student-types", image: publicAsset("/assets/checkpoints/m02-free-trial-training.png"), x: 74, y: 52 },
    ],
  },
  {
    unlocked: publicAsset("/assets/growth-maps/module-02-teaching-integration-v4.png"),
    locked: publicAsset("/assets/growth-maps/module-02-teaching-integration-locked-v4.png"),
    checkpoints: [
      { id: "ttp-orientation", image: publicAsset("/assets/checkpoints/m02-ttp-orientation.png"), x: 30, y: 29 },
      { id: "me-culture", image: publicAsset("/assets/checkpoints/m02-me-culture-parsnip.png"), x: 69, y: 29 },
      { id: "reliability-training", image: publicAsset("/assets/checkpoints/m02-reliability-training.png"), x: 51, y: 64 },
    ],
  },
  {
    unlocked: publicAsset("/assets/growth-maps/module-03-capability-advance.png"),
    locked: publicAsset("/assets/growth-maps/module-03-capability-advance-locked.png"),
    checkpoints: [
      { id: "cocos-training", image: publicAsset("/assets/checkpoints/m03-cocos-course-training.png"), x: 35, y: 31 },
      { id: "set-fundamentals", image: publicAsset("/assets/checkpoints/m03-set-teaching-fundamentals.png"), x: 72, y: 31 },
    ],
  },
];

function Toki({ className = "", ...props }) {
  return <MotionToki className={`ref-toki ${className}`} {...props} />;
}

const AI_HELP_POSITION_STORAGE_KEY = "new-teacher-camp-ai-help-position-v2";

const visibleRect = (selector) => {
  const element = document.querySelector(selector);
  if (!element) return null;
  const rect = element.getBoundingClientRect();
  const style = window.getComputedStyle(element);
  return rect.width > 0 &&
    rect.height > 0 &&
    style.display !== "none" &&
    style.visibility !== "hidden"
    ? rect
    : null;
};

const measureAiHelpLayout = () => {
  const mobile = window.innerWidth <= AI_HELP_MOBILE_BREAKPOINT;
  const mode = mobile ? "mobile" : "desktop";
  const buttonSize = mobile ? AI_HELP_MOBILE_SIZE : AI_HELP_DESKTOP_SIZE;
  const edgeGap = mobile ? 15 : 28;
  const headerBottom =
    visibleRect(".ref-header")?.bottom ?? (mobile ? 66 : 74);
  const lowerBoundary = mobile
    ? [visibleRect(".ref-mobile-nav"), visibleRect(".task-continue")]
        .filter(Boolean)
        .reduce(
          (boundary, rect) => Math.min(boundary, rect.top),
          window.innerHeight,
        )
    : window.innerHeight;

  return {
    mode,
    bounds: getAiHelpBounds({
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      headerBottom,
      lowerBoundary,
      buttonSize,
      edgeGap,
    }),
  };
};

const readAiHelpPositions = () => {
  try {
    const value = JSON.parse(
      localStorage.getItem(AI_HELP_POSITION_STORAGE_KEY) || "{}",
    );
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
};

const saveAiHelpPosition = (mode, position) => {
  try {
    localStorage.setItem(
      AI_HELP_POSITION_STORAGE_KEY,
      JSON.stringify({
        ...readAiHelpPositions(),
        [mode]: position,
      }),
    );
  } catch {
    // Position persistence is optional when browser storage is unavailable.
  }
};

function FloatingAiHelpButton({ language, onOpen, routeKey }) {
  const label = copy(language, "Open AI support", "打开 AI 客服");
  const [position, setPosition] = useState(null);
  const [dragging, setDragging] = useState(false);
  const buttonRef = useRef(null);
  const dragRef = useRef(null);
  const positionRef = useRef(null);
  const modeRef = useRef("");
  const suppressClickRef = useRef(false);

  const updatePosition = (nextPosition) => {
    positionRef.current = nextPosition;
    setPosition(nextPosition);
  };

  useLayoutEffect(() => {
    const syncPosition = () => {
      const layout = measureAiHelpLayout();
      const stored = readAiHelpPositions()[layout.mode];
      const candidate = layout.mode === "mobile"
        ? null
        : modeRef.current === layout.mode ? positionRef.current : stored;
      const nextPosition = clampAiHelpPosition(
        candidate || defaultAiHelpPosition(
          layout.bounds,
          layout.mode === "mobile" ? 18 : 48,
        ),
        layout.bounds,
      );
      modeRef.current = layout.mode;
      updatePosition(nextPosition);
    };

    syncPosition();
    window.addEventListener("resize", syncPosition);
    return () => window.removeEventListener("resize", syncPosition);
  }, [routeKey]);

  const finishDrag = (event, cancelled = false) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag.cleanup?.();
    if (buttonRef.current?.hasPointerCapture?.(event.pointerId)) {
      buttonRef.current.releasePointerCapture(event.pointerId);
    }
    if (drag.moved) {
      suppressClickRef.current = true;
      if (!cancelled && positionRef.current) {
        saveAiHelpPosition(modeRef.current, positionRef.current);
      }
      window.setTimeout(() => {
        suppressClickRef.current = false;
      }, 0);
    }
    dragRef.current = null;
    setDragging(false);
  };

  useEffect(
    () => () => {
      dragRef.current?.cleanup?.();
    },
    [],
  );

  return (
    <div
      className={[
        "ai-help-fab",
        position ? "is-positioned" : "",
        position && position.x < window.innerWidth / 2 ? "is-near-left" : "",
        dragging ? "is-dragging" : "",
      ].filter(Boolean).join(" ")}
      style={position ? { left: `${position.x}px`, top: `${position.y}px` } : undefined}
    >
      <span className="ai-help-fab-hint" aria-hidden="true">
        <strong>{copy(language, "AI Support", "AI 客服")}</strong>
      </span>
      <button
        ref={buttonRef}
        type="button"
        data-onboarding-target="help-entry"
        onClick={(event) => {
          if (suppressClickRef.current) {
            event.preventDefault();
            return;
          }
          onOpen();
        }}
        onDragStart={(event) => event.preventDefault()}
        onPointerDown={(event) => {
          if (window.innerWidth <= AI_HELP_MOBILE_BREAKPOINT) return;
          if (event.button !== 0) return;
          const rect = event.currentTarget.getBoundingClientRect();
          const move = (pointerEvent) => {
            const drag = dragRef.current;
            if (!drag || drag.pointerId !== pointerEvent.pointerId) return;
            const distance = Math.hypot(
              pointerEvent.clientX - drag.startX,
              pointerEvent.clientY - drag.startY,
            );
            if (!drag.moved && distance < 5) return;
            drag.moved = true;
            const layout = measureAiHelpLayout();
            updatePosition(
              clampAiHelpPosition(
                {
                  x: pointerEvent.clientX - drag.offsetX,
                  y: pointerEvent.clientY - drag.offsetY,
                },
                layout.bounds,
              ),
            );
            pointerEvent.preventDefault();
          };
          const end = (pointerEvent) => finishDrag(pointerEvent);
          const cancel = (pointerEvent) => finishDrag(pointerEvent, true);
          const cleanup = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", end);
            window.removeEventListener("pointercancel", cancel);
          };
          dragRef.current = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            offsetX: event.clientX - rect.left,
            offsetY: event.clientY - rect.top,
            moved: false,
            cleanup,
          };
          window.addEventListener("pointermove", move, { passive: false });
          window.addEventListener("pointerup", end);
          window.addEventListener("pointercancel", cancel);
          event.currentTarget.setPointerCapture(event.pointerId);
          setDragging(true);
          event.preventDefault();
        }}
        aria-label={label}
        title={label}
      >
        <img src={publicAsset("/assets/assistant/ai-helper.png")} alt="" aria-hidden="true" />
        <span className="ai-help-fab-badge" aria-hidden="true">
          <ChatCircleDots size={14} weight="fill" />
        </span>
      </button>
    </div>
  );
}

function SourceUnavailableCard({ language, title, message, compact = false }) {
  return (
    <section className={`source-unavailable-card ${compact ? "is-compact" : ""}`} role="alert">
      <span className="source-unavailable-icon"><Hourglass size={28} weight="duotone" /></span>
      <div>
        <h2>{title}</h2>
        <p>{message}</p>
      </div>
      <button type="button" onClick={() => window.location.reload()}>
        {copy(language, "Try again", "重新加载")}
      </button>
    </section>
  );
}

function TaskDataErrorScreen({ error, language, onRetry }) {
  const details = describeDataError(error, language);
  const [retrySeconds, setRetrySeconds] = useState(details.retryAfterSeconds);

  useEffect(() => {
    setRetrySeconds(details.retryAfterSeconds);
    if (details.retryAfterSeconds <= 0) return undefined;
    const timer = window.setInterval(() => {
      setRetrySeconds((current) => {
        if (current <= 1) {
          window.clearInterval(timer);
          return 0;
        }
        return current - 1;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [details.retryAfterSeconds, error]);

  return (
    <main className="auth-screen">
      <section className="auth-card" role="alert" aria-live="polite">
        <h1>{details.title}</h1>
        <p>{details.message}</p>
        <button
          className="auth-submit-button"
          type="button"
          disabled={retrySeconds > 0}
          onClick={onRetry}
        >
          {retrySeconds > 0
            ? copy(language, `Try again in ${retrySeconds}s`, `${retrySeconds} 秒后可重试`)
            : copy(language, "Try again", "重新加载")}
        </button>
      </section>
    </main>
  );
}

function Header({ language, unreadCount, onHelp, onLanguageChange, onLogout, onMessagesOpen, onQuickGuide, onResetPassword }) {
  const teacher = useTeacher();
  const location = useLocation();
  const navigate = useNavigate();
  const [languageOpen, setLanguageOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const profileButtonRef = useRef(null);
  const isTask = location.pathname.startsWith("/task/");
  return (
    <header className={`ref-header ${isTask ? "task-header" : ""}`}>
      {isTask && (
        <button
          className="mobile-back"
          type="button"
          onClick={() => navigate(-1)}
          aria-label="Go back"
        >
          <ArrowLeft size={28} />
        </button>
      )}
      <Link className="ref-brand" to="/" aria-label="51Talk New Teacher Camp">
        <img src={publicAsset("/assets/brand/51talk-logo-blue.png")} alt="51Talk" />
        <span>{copy(language, "New Teacher Camp", "新师训练营")}</span>
      </Link>
      <nav
        className="ref-desktop-nav"
        aria-label="Main navigation"
        data-onboarding-target="main-navigation"
        data-onboarding-viewport="desktop"
      >
        <NavLink to="/" end>
          {copy(language, "My TIDE", "我的成长")}
        </NavLink>
        <NavLink
          to="/path"
          data-onboarding-target="tasks-entry"
          data-onboarding-action="open-tasks"
          data-onboarding-viewport="desktop"
        >
          {copy(language, "Tasks", "我的任务")}
        </NavLink>
        <NavLink
          className="nav-message-link"
          to="/messages"
          onClick={onMessagesOpen}
          data-onboarding-target="messages-entry"
          data-onboarding-action="open-messages"
          data-onboarding-viewport="desktop"
        >
          {copy(language, "Messages", "消息")}
          {unreadCount > 0 && <span className="nav-unread-badge" aria-label={copy(language, `${unreadCount} unread messages`, `${unreadCount} 条未读消息`)}>{unreadCount}</span>}
        </NavLink>
      </nav>
      <div className="ref-header-actions">
        <button
          type="button"
          onClick={onHelp}
          aria-label={copy(language, "Help", "帮助")}
          data-onboarding-target="help-entry"
          data-onboarding-viewport="desktop"
        >
          <Question size={20} />
          <span>{copy(language, "Help", "帮助")}</span>
        </button>
        <div className="language-menu">
          <button
            type="button"
            onClick={() => setLanguageOpen((value) => !value)}
            aria-expanded={languageOpen}
            aria-label="Change language"
          >
            <Globe size={21} />
            {language === "zh" ? "中" : "EN"}
            <CaretDown size={15} />
          </button>
          {languageOpen && (
            <div className="language-popover">
              <button
                type="button"
                aria-pressed={language === "en"}
                onClick={() => {
                  onLanguageChange("en");
                  setLanguageOpen(false);
                }}
              >
                English
              </button>
              <button
                type="button"
                aria-pressed={language === "zh"}
                onClick={() => {
                  onLanguageChange("zh");
                  setLanguageOpen(false);
                }}
              >
                中文
              </button>
            </div>
          )}
        </div>
        <div className="profile-menu">
          <button
            ref={profileButtonRef}
            className="profile-button"
            type="button"
            onClick={() => setProfileOpen((value) => !value)}
            aria-expanded={profileOpen}
            aria-label={`${teacher.name}'s account menu`}
          >
            <img src={publicAsset("/assets/toki/worktoki.png")} alt={teacher.name} />
            <span>{teacher.name}</span>
            <CaretDown size={14} />
          </button>
          {profileOpen && (
            <div className="profile-popover">
              <div className="profile-account-summary">
                <strong>{teacher.name}</strong>
                <small>{teacher.email}</small>
              </div>
              <button
                className="profile-guide-button"
                type="button"
                onClick={() => {
                  setProfileOpen(false);
                  onQuickGuide(profileButtonRef.current);
                }}
              >
                <Sparkle size={18} />
                {copy(language, "Feature guides", "功能引导")}
              </button>
              <button
                className="profile-security-button"
                type="button"
                onClick={() => {
                  setProfileOpen(false);
                  onResetPassword();
                }}
              >
                <LockKey size={18} />
                {copy(language, "Security and reset password", "修改密码")}
              </button>
              <button className="profile-logout-button" type="button" onClick={onLogout}>
                {copy(language, "Log out", "退出登录")}
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

function MobileNav({ language, unreadCount = 0, onMessagesOpen }) {
  return (
    <nav
      className="ref-mobile-nav"
      aria-label="Mobile navigation"
      data-onboarding-target="main-navigation"
      data-onboarding-viewport="mobile"
    >
      <NavLink to="/" end>
        <GraduationCap size={25} />
        <span>{copy(language, "My TIDE", "我的成长")}</span>
      </NavLink>
      <NavLink
        to="/path"
        data-onboarding-target="tasks-entry"
        data-onboarding-action="open-tasks"
        data-onboarding-viewport="mobile"
      >
        <ListChecks size={25} />
        <span>{copy(language, "Tasks", "我的任务")}</span>
      </NavLink>
      <NavLink
        className="nav-message-link"
        to="/messages"
        onClick={onMessagesOpen}
        data-onboarding-target="messages-entry"
        data-onboarding-action="open-messages"
        data-onboarding-viewport="mobile"
      >
        <ChatCircleDots size={25} />
        <span>{copy(language, "Messages", "消息")}</span>
        {unreadCount > 0 && <span className="nav-unread-badge" aria-label={copy(language, `${unreadCount} unread messages`, `${unreadCount} 条未读消息`)}>{unreadCount}</span>}
      </NavLink>
    </nav>
  );
}
function ProgressBar({ value = 0 }) {
  return (
    <div
      className="ref-progress"
      role="progressbar"
      aria-valuemin="0"
      aria-valuemax="100"
      aria-valuenow={value}
    >
      <span style={{ width: `${value}%` }} />
    </div>
  );
}
function stageTasks(tasks, range) {
  return tasks.filter((task) => task.stage === range);
}
function stageComplete(tasks, range) {
  const items = stageTasks(tasks, range);
  return items.length > 0 && items.every((task) => task.status === "completed");
}

function stageState(tasks, index) {
  const items = stageTasks(tasks, stageDescriptions[index].range);
  if (items.length > 0 && items.every((task) => task.locked)) return "locked";
  return stageComplete(tasks, stageDescriptions[index].range)
    ? "complete"
    : "current";
}
function currentStageIndex(tasks) {
  const index = stageDescriptions.findIndex(
    (_, itemIndex) => stageState(tasks, itemIndex) === "current",
  );
  return index === -1 ? stageDescriptions.length - 1 : index;
}
function statusLabel(language, status) {
  const labels = statusLabels[status] || statusLabels.available;
  return copy(language, labels[0], labels[1]);
}
function displayStatus(task) {
  return task.status;
}
function taskAction(language, task) {
  if (task.locked) return copy(language, "Preview", "先看看");
  if (task.status === "completed") return copy(language, "View result", "查看结果");
  if (task.method === "external_status") return copy(language, "View status", "查看状态");
  if (task.method === "content_pending") return copy(language, "Wait for content", "等待内容发布");
  if (task.id === "profile-credentials" && task.externalStatusItems?.some((item) => item.status === "action_required")) return copy(language, "View status", "查看状态");
  if (task.id === "profile-credentials" && task.status === "verifying") return copy(language, "Nothing to do now", "现在无需操作");
  if (task.method === "external_status") return copy(language, "View status", "查看状态");
  if (["submitted", "verifying"].includes(task.status))
    return copy(language, "View status", "查看审核进度");
  if (task.status === "started") return copy(language, "Continue", "继续");
  if (task.status === "retry_required") {
    if (["device_readiness", "readiness_photo"].includes(task.method)) return copy(language, "Retry", "重试");
    if (task.method === "upload_review") return copy(language, "Update files", "补充材料");
    return copy(language, "Update", "重新完成");
  }
  return copy(language, "Start", "开始");
}
function nextTask(tasks) {
  const items = tasks
    .filter((task) => task.status !== "completed" && !task.locked)
    .sort((a, b) => (a.displayRank ?? 99) - (b.displayRank ?? 99));
  return (
    items.find((task) => task.isPrimary) ||
    items[0] ||
    tasks[tasks.length - 1]
  );
}

function onboardingPrimaryRequiredTask(tasks) {
  const requiredTasks = tasks.filter((task) => (
    task.taskCategory !== "personalized"
    && !task.assignmentMissing
    && !["cancelled", "expired"].includes(task.status)
  ));
  return nextTask(requiredTasks);
}

function TodayPage({ tasks, language }) {
  const teacher = useTeacher();
  const index = currentStageIndex(tasks);
  const stage = localizeStage(stageDescriptions[index], language);
  const done = tasks.filter((task) => task.status === "completed").length;
  const focus = localizeTask(nextTask(tasks), language);
  const focusRaw = nextTask(tasks);
  const secondary = stageTasks(tasks, stageDescriptions[index].range)
    .filter((task) => task.id !== focus.id && task.status !== "completed")
    .slice(0, 2);
  const remaining = stageTasks(tasks, stageDescriptions[index].range).filter(
    (task) => task.status !== "completed",
  ).length;
  const nextStage =
    stageDescriptions[index + 1] &&
    localizeStage(stageDescriptions[index + 1], language);
  const FocusIcon = taskIcons[focus.id] || ClipboardText;
  return (
    <main className="ref-page today-screen">
      <div className="today-grid">
        <div className="today-main">
          <section className="welcome-card">
            <div className="welcome-title">
              <span className="icon-disc blue">
                <CalendarBlank size={27} weight="fill" />
              </span>
              <div>
                <h1>
                  {copy(language, "Day", "第")} <em>{teacher.day}</em> /{" "}
                  {teacher.totalDays}
                </h1>
                <p>{stage.title}</p>
              </div>
            </div>
            <h2>
              {copy(language, "Welcome back", "欢迎回来")}, {teacher.name}
            </h2>
            <div className="mobile-progress-summary">
              <p>
                <strong>{done}</strong> / {tasks.length}{" "}
                {copy(language, "tasks completed", "项任务已完成")}
              </p>
              <ProgressBar value={(done / tasks.length) * 100} />
            </div>
            <Link className="milestone-row" to="/path">
              <span className="icon-disc yellow">
                <Lock size={20} weight="fill" />
              </span>
              <span>
                {nextStage ? (
                  <>
                    {copy(
                      language,
                      `Complete ${remaining} more tasks to unlock`,
                      `再完成 ${remaining} 项任务即可解锁`,
                    )}{" "}
                    <strong>{nextStage.title}</strong>
                  </>
                ) : (
                  copy(
                    language,
                    "Complete the final tasks to finish your training journey",
                    "完成最后几项任务，顺利结束训练营",
                  )
                )}
              </span>
              <ArrowRight size={19} />
            </Link>
          </section>
          <div className="desktop-summary-row">
            <section className="summary-card">
              <h3>{copy(language, "Your growth progress", "你的成长进度")}</h3>
              <p>
                <strong>{done}</strong> / {tasks.length}{" "}
                {copy(language, "tasks completed", "项任务已完成")}
              </p>
              <ProgressBar value={(done / tasks.length) * 100} />
            </section>
            <Link className="summary-card milestone-card" to="/path">
              <span className="icon-disc pale">
                <Lock size={24} weight="fill" />
              </span>
              <div>
                <h3>{copy(language, "Next milestone", "下一个目标")}</h3>
                <p>
                  {nextStage ? (
                    <>
                      {copy(
                        language,
                        `Complete ${remaining} more tasks`,
                        `再完成 ${remaining} 项任务`,
                      )}
                      <br />
                      {copy(language, "to unlock", "即可解锁")}{" "}
                      {nextStage.title}
                    </>
                  ) : (
                    copy(
                      language,
                      "Complete your training journey",
                      "完成训练营任务",
                    )
                  )}
                </p>
              </div>
            </Link>
          </div>
          <h2 className="section-title">
            {copy(language, "Today’s focus", "今日重点")}
          </h2>
          <section className="focus-card">
            <span className="icon-disc focus-icon">
              <FocusIcon size={33} weight="fill" />
            </span>
            <div className="focus-content">
              <h3>{focus.name}</h3>
              <span className="state-pill">
                {statusLabel(language, focusRaw.status)}
              </span>
              <div className="meta-row">
                <span>
                  <Clock size={18} />
                  {focus.duration}
                </span>
                <span>
                  <CalendarBlank size={18} />
                  {focus.due}
                </span>
              </div>
              <p>
                <strong>{copy(language, "Benefit:", "完成收益：")}</strong>{" "}
                {focus.value}
              </p>
            </div>
            <Link
              className="primary-cta"
              to={`/task/${focus.id}`}
              {...taskAnalyticsAttributes(focusRaw, "MY_TIDE", "PRIMARY")}
            >
              {taskAction(language, focusRaw)}
            </Link>
          </section>
          <div className="compact-task-list">
            {secondary.map((raw, index) => {
              const task = localizeTask(raw, language);
              const Icon = taskIcons[task.id] || ClipboardText;
              return (
                <Link
                  key={task.id}
                  to={`/task/${task.id}`}
                  {...taskAnalyticsAttributes(raw, "MY_TIDE", `SECONDARY_${index + 1}`)}
                >
                  <span className="icon-disc pale">
                    <Icon size={25} weight="fill" />
                  </span>
                  <strong>{task.name}</strong>
                  <span className="desktop-task-meta">
                    <Clock size={17} />
                    {task.duration}
                    <CalendarBlank size={17} />
                    {task.due}
                  </span>
                  <ArrowRight size={21} />
                </Link>
              );
            })}
          </div>
        </div>
        <aside className="today-aside">
          <section className="coach-card">
            <h2>{copy(language, "Your coach, Toki", "你的陪练伙伴 Toki")}</h2>
            <div className="speech">
              {copy(language, "Start with", "先完成")} {focus.name}.<br />
              {copy(language, "It takes about", "预计需要")} {focus.duration}.
            </div>
            <Toki mood="wave" motion="welcome" />
          </section>
          <Link className="side-link" to="/path">
            <span className="icon-disc pale">
              <Flag size={25} weight="fill" />
            </span>
            <strong>
              {copy(language, "View Growth Path", "查看成长路径")}
            </strong>
            <ArrowRight size={21} />
          </Link>
          <Link className="side-link" to="/tide">
            <span className="icon-disc pale">
              <GraduationCap size={26} weight="fill" />
            </span>
            <strong>{copy(language, "Open My TIDE", "查看我的成长")}</strong>
            <ArrowRight size={21} />
          </Link>
        </aside>
      </div>
      <section className="mobile-coach">
        <Toki mood="wave" motion="welcome" />
        <p>
          <strong>
            {copy(language, "Start with", "先完成")} {focus.name}.
          </strong>
          <br />
          {copy(language, "It takes about", "预计需要")} {focus.duration}.
        </p>
      </section>
      <MobileNav language={language} />
    </main>
  );
}

function mapTaskState(task, isFocus) {
  if (task.status === "completed") return "done";
  if (task.locked) return "locked";
  return isFocus ? "focus" : "available";
}

function MapTaskPin({ task, visual, language, isFocus, stageLocked = false }) {
  const item = localizeTask(task, language);
  const pinLocked = stageLocked || task.locked || task.assignmentMissing;
  const assignmentUnavailable = task.assignmentUnavailable;
  const state = pinLocked ? "locked" : mapTaskState(task, isFocus);
  const content = (
    <>
      <img src={visual.image} alt="" aria-hidden="true" />
      {pinLocked && (
        <span className="map-task-lock" aria-hidden="true">
          <Lock size={17} weight="fill" />
        </span>
      )}
      <span className="map-task-label">
        <strong>{item.shortName || item.name}</strong>
        <small>
          {pinLocked && <Lock size={13} weight="fill" />}
          {state === "done" && <CheckCircle size={14} weight="fill" />}
          {state === "focus" && <Sparkle size={14} weight="fill" />}
          {pinLocked
            ? assignmentUnavailable
              ? copy(language, "Data syncing", "数据待同步")
              : copy(language, "Locked", "暂未开放")
            : statusLabel(language, task.status)}
        </small>
      </span>
    </>
  );

  const className = `map-task-pin ${state}`;
  const style = { "--pin-x": `${visual.x}%`, "--pin-y": `${visual.y}%` };
  const ariaLabel = `${item.name} · ${pinLocked
    ? assignmentUnavailable
      ? copy(language, "Data syncing", "数据待同步")
      : copy(language, "Locked", "暂未开放")
    : statusLabel(language, task.status)}`;

  if (pinLocked) {
    return (
      <div className={className} style={style} aria-label={ariaLabel}>
        {content}
      </div>
    );
  }

  return (
    <Link
      className={className}
      style={style}
      to={`/task/${task.id}`}
      aria-label={ariaLabel}
      {...taskAnalyticsAttributes(
        task,
        "TASKS_GROWTH_MAP",
        `${task.stage || "UNKNOWN"}_${task.displayRank || "UNKNOWN"}`,
      )}
    >
      {content}
    </Link>
  );
}

function StageMap({
  index,
  tasks,
  language,
  selected,
  focusId,
  mobile = false,
  stageRef,
  onSelect,
}) {
  const source = stageDescriptions[index];
  const stage = localizeStage(source, language);
  const state = stageState(tasks, index);
  const items = stageTasks(tasks, source.range);
  const visual = stageMapVisuals[index];
  const completed = items.filter((task) => task.status === "completed").length;
  const mapSource = state === "locked" && visual.locked ? visual.locked : visual.unlocked;
  const showProgress = mobile || (selected && state !== "locked");
  const stateCopy =
    state === "complete"
      ? copy(language, "Completed", "已完成")
      : state === "locked"
        ? copy(language, `Preview · Day ${source.releaseDay}`, `可以先看 · 第 ${source.releaseDay} 天开放`)
        : copy(language, "In progress", "进行中");
  return (
    <article
      ref={stageRef}
      className={`${mobile ? "mobile" : "desktop"}-map-stage is-${state}${selected ? " is-selected" : ""}${!mobile && !selected && onSelect ? " is-switchable" : ""}`}
      data-stage-index={index}
      data-selected={selected ? "true" : "false"}
      aria-label={`${copy(language, "Module", "成长阶段")} ${source.number}: ${stage.title}`}
    >
      <div className="map-stage-chip">
        <strong>{source.number}</strong>
        <span>
          <b>{copy(language, "Module", "成长阶段")} {source.number}</b>
          <small>{stage.range}</small>
        </span>
        <em className={`stage-state-pill ${state}`}>
          {state === "locked" && <Lock size={13} weight="fill" />}
          {state === "complete" && <Check size={13} weight="bold" />}
          {stateCopy}
        </em>
      </div>

      <div className="map-artboard">
        <img className="map-base-image" src={mapSource} alt="" aria-hidden="true" />
        {visual.checkpoints.map((checkpoint) => {
          const task = items.find((item) => item.id === checkpoint.id);
          if (!task) return null;
          return (
            <MapTaskPin
              key={checkpoint.id}
              task={task}
              visual={checkpoint}
              language={language}
              isFocus={state === "current" && task.id === focusId}
              stageLocked={state === "locked"}
            />
          );
        })}

        {state === "locked" && (
          <div
            className="map-locked-note"
            aria-label={copy(language, "Module available to preview", "本阶段可以提前查看")}
            title={copy(language, `Complete the previous module to unlock early, or wait until Day ${source.releaseDay}`, `完成上一阶段可提前解锁，最晚第 ${source.releaseDay} 天自动开放`)}
          >
            <img
              src={publicAsset("/assets/growth-maps/stage-locked-padlock.png")}
              alt=""
              aria-hidden="true"
            />
          </div>
        )}
      </div>

      {(showProgress || selected) && (
        <div className="map-stage-hud">
          {selected && (
            <div className="map-toki-static">
              <Toki
                mood="cheer"
                motion={state === "locked" ? undefined : "unlock"}
              />
              <span>
                {state === "locked"
                  ? copy(language, `Preview now; complete the previous module to unlock early, or wait until Day ${source.releaseDay}.`, `现在可以先查看；完成上一阶段可提前解锁，最晚第 ${source.releaseDay} 天自动开放。`)
                  : copy(language, "I’m here with you for this module.", "这一阶段，我会陪你一起完成。")}
              </span>
            </div>
          )}

          {showProgress && (
            <div className={`map-stage-progress ${state === "locked" ? "locked" : ""}`}>
              <span>
                <strong>{completed} / {items.length}</strong>
                {state === "locked"
                  ? copy(language, "planned tasks", "项任务")
                  : copy(language, "tasks complete", "项任务已完成")}
              </span>
              <div className="map-progress-track" aria-hidden="true">
                <i style={{ width: `${(completed / items.length) * 100}%` }} />
              </div>
              <small>
                {state === "locked"
                  ? copy(language, `Preview is available. Actions open early after the previous module, or automatically on Day ${source.releaseDay}.`, `任务始终可以预览；完成上一阶段可提前开始，最晚第 ${source.releaseDay} 天自动开放。`)
                  : state === "complete"
                  ? copy(language, "This module is complete.", "本阶段已完成。")
                  : copy(language, "Complete this module to open the next map.", "完成本阶段后，可以进入下一阶段。")}
              </small>
            </div>
          )}
        </div>
      )}

      {!mobile && !selected && onSelect && (
        <button
          className="map-stage-select-button"
          type="button"
          onClick={onSelect}
          aria-label={copy(
            language,
            `Switch to module ${source.number}: ${stage.title}`,
            `切换到成长阶段 ${source.number}：${stage.title}`,
          )}
          title={copy(language, "Click to view this module", "点击查看此成长阶段")}
        />
      )}
    </article>
  );
}

function RequiredTaskShortcut({ raw, language, priority = "primary" }) {
  if (!raw) return null;
  const task = localizeTask(raw, language);
  const Icon = taskIcons[raw.id] || ClipboardText;
  return (
    <article className={`tasks-required-shortcut ${priority}`}>
      <header>
        <span className="tasks-shortcut-icon"><Icon size={priority === "primary" ? 30 : 25} weight="fill" /></span>
        <div>
          <small>
            {priority === "primary"
              ? copy(language, "PRIMARY REQUIRED TASK", "建议先做")
              : copy(language, "NEXT REQUIRED TASK", "完成后继续")}
          </small>
          <h2>{task.name}</h2>
        </div>
        <div className="tasks-required-status-line">
          <span className="state-pill">{statusLabel(language, raw.status)}</span>
          <span className="tasks-mobile-duration"><Clock size={12} />{task.duration}</span>
        </div>
      </header>
      <div className="tasks-shortcut-meta">
        <span><Clock size={16} />{task.duration}</span>
        <span><CalendarBlank size={16} />{task.due}</span>
      </div>
      <p>{task.reason}</p>
      <dl>
        <div>
          <dt>{copy(language, "Completion standard", "完成标准")}</dt>
          <dd>{task.standard}</dd>
        </div>
        <div>
          <dt>{copy(language, "How it helps", "完成后你会")}</dt>
          <dd>{task.value}</dd>
        </div>
      </dl>
      <Link
        to={`/task/${raw.id}`}
        data-onboarding-target={priority === "primary" ? "primary-task-action" : undefined}
        data-onboarding-action={priority === "primary" ? "open-primary-task" : undefined}
        {...taskAnalyticsAttributes(
          raw,
          "TASKS",
          priority === "primary" ? "PRIMARY" : "SECONDARY",
        )}
      >
        {taskAction(language, raw)}
        <ArrowRight size={18} />
      </Link>
    </article>
  );
}

function PersonalizedTaskGrid({ rawTasks, language }) {
  if (rawTasks.length === 0) return null;
  return (
    <section
      className="tasks-personalized-section"
      aria-labelledby="tasks-personalized-title"
      data-onboarding-target="personalized-task-area"
      data-onboarding-max-height="320"
      data-onboarding-scroll-block="start"
    >
      <header className="tasks-personalized-heading">
        <div>
          <small>{copy(language, "PERSONALIZED FOR YOU", "为你定制")}</small>
          <h2 id="tasks-personalized-title">
            {copy(language, "Your personalized improvement tasks", "你的专属改善任务")}
          </h2>
          <p>
            {copy(
              language,
              "Focused support selected for your current growth needs.",
              "根据当前成长需要，为你提供针对性的改善支持。",
            )}
          </p>
        </div>
        <span>{rawTasks.length} {copy(language, rawTasks.length === 1 ? "task" : "tasks", "项任务")}</span>
      </header>
      <div className="tasks-personalized-grid">
        {rawTasks.map((raw, index) => {
          const task = localizeTask(raw, language);
          const Icon = taskIcons[raw.id] || Sparkle;
          return (
            <article
              className="tasks-personalized-strip"
              key={raw.backendId || raw.id}
              data-onboarding-target={index === 0 ? "personalized-task-reason" : undefined}
              data-onboarding-max-height={index === 0 ? "230" : undefined}
              data-onboarding-scroll-block={index === 0 ? "center" : undefined}
            >
              <span className="tasks-personalized-icon"><Icon size={30} weight="duotone" /></span>
              <div className="tasks-personalized-copy">
                <div className="tasks-personalized-kicker">
                  <span className="tasks-personalized-badge">
                    <Sparkle size={13} weight="fill" />
                    {copy(language, "PERSONALIZED IMPROVEMENT", "个性化改善")}
                  </span>
                </div>
                <h3>{task.name}</h3>
                <p>{task.reason}</p>
                <div className="tasks-personalized-context">
                  <span>{statusLabel(language, raw.status)}</span>
                  <small className="tasks-personalized-due">
                    <CalendarBlank size={14} />
                    {task.due}
                  </small>
                  <small>{copy(language, "Improvement support only · No task points", "帮助改善课堂表现 · 不计任务积分")}</small>
                </div>
              </div>
              <Link
                to={`/task/${raw.id}`}
                {...taskAnalyticsAttributes(raw, "TASKS", "PERSONALIZED_LIST")}
              >
                {taskAction(language, raw)}
                <ArrowRight size={18} />
              </Link>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function GrowthPathPage({
  tasks,
  language,
  unreadCount,
  onMessagesOpen,
  returnSnapshot,
}) {
  const teacher = useTeacher();
  const location = useLocation();
  const requiredTasks = tasks.filter((task) => task.taskCategory !== "personalized");
  const growthMapTasks = useMemo(
    () => composeGrowthMapTasks(requiredTasks, teacher.day),
    [requiredTasks, teacher.day],
  );
  const personalizedTasks = tasks.filter((task) => task.taskCategory === "personalized" && task.surface !== "growth_only" && !["cancelled", "expired"].includes(task.status));
  const highestAvailableStageIndex = stageIndexForAvailableTasks(
    growthMapTasks,
    teacher.day,
  );
  const activeIndex = stageIndexFromPathSearch(
    location.search,
    highestAvailableStageIndex,
  );
  const active = localizeStage(stageDescriptions[activeIndex], language);
  const assignedGrowthMapTasks = growthMapTasks.filter((task) => !task.assignmentMissing);
  const currentRequiredItems = stageTasks(assignedGrowthMapTasks, stageDescriptions[activeIndex].range)
    .filter((task) => task.status !== "completed" && !task.locked)
    .sort((a, b) => (a.displayRank ?? 99) - (b.displayRank ?? 99));
  const primaryRaw = currentRequiredItems.find((task) => task.isPrimary) || currentRequiredItems[0] || nextTask(assignedGrowthMapTasks) || null;
  const secondaryRaw = primaryRaw ? currentRequiredItems.find((task) => task.id !== primaryRaw.id) || null : null;
  const sortedPersonalizedTasks = [...personalizedTasks].sort((a, b) => (a.displayRank ?? 99) - (b.displayRank ?? 99));
  const done = growthMapTasks.filter((task) => task.status === "completed").length;
  const restoredSelectedIndex = Number.isInteger(returnSnapshot?.selectedIndex)
    ? returnSnapshot.selectedIndex
    : activeIndex;
  const restoredMobileSelectedIndex = Number.isInteger(returnSnapshot?.mobileSelectedIndex)
    ? returnSnapshot.mobileSelectedIndex
    : activeIndex;
  const [selectedIndex, setSelectedIndex] = useState(restoredSelectedIndex);
  const [mobileSelectedIndex, setMobileSelectedIndex] = useState(restoredMobileSelectedIndex);
  const [carouselSize, setCarouselSize] = useState({ width: 0, height: 0 });
  const carouselRef = useRef(null);
  const desktopStageRefs = useRef([]);
  const mobileStageRefs = useRef([]);
  const mobileScrollerRef = useRef(null);
  const initialMobileScrollLeftRef = useRef(returnSnapshot?.mobileScrollLeft);
  const mobileReadyRef = useRef(false);
  const mobileRafRef = useRef(0);
  const hasAnimatedRef = useRef(false);
  const previousActiveIndexRef = useRef(activeIndex);
  const selectedIndexRef = useRef(restoredSelectedIndex);
  const requestedIndexRef = useRef(restoredSelectedIndex);
  const previousAnimatedIndexRef = useRef(restoredSelectedIndex);
  const carouselAnimatingRef = useRef(false);
  const carouselContinuationFrameRef = useRef(0);

  const advanceClockwise = useCallback(() => {
    if (carouselAnimatingRef.current) return;
    const current = selectedIndexRef.current;
    if (current === requestedIndexRef.current) return;
    const next = nextClockwiseStageIndex(current, stageDescriptions.length);
    carouselAnimatingRef.current = true;
    selectedIndexRef.current = next;
    setSelectedIndex(next);
  }, []);

  const selectStageClockwise = useCallback((targetIndex) => {
    const target = normalizeStageIndex(targetIndex, stageDescriptions.length);
    requestedIndexRef.current = target;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      carouselAnimatingRef.current = false;
      selectedIndexRef.current = target;
      previousAnimatedIndexRef.current = target;
      setSelectedIndex(target);
      return;
    }
    advanceClockwise();
  }, [advanceClockwise]);

  const selectPrevious = () =>
    selectStageClockwise(selectedIndexRef.current - 1);
  const selectNext = () =>
    selectStageClockwise(selectedIndexRef.current + 1);

  useEffect(() => {
    document.body.classList.add("tasks-hub-active");
    return () => {
      document.body.classList.remove("tasks-hub-active");
      cancelAnimationFrame(carouselContinuationFrameRef.current);
    };
  }, []);

  useEffect(() => {
    if (previousActiveIndexRef.current === activeIndex) return;
    previousActiveIndexRef.current = activeIndex;
    requestedIndexRef.current = activeIndex;
    selectedIndexRef.current = activeIndex;
    previousAnimatedIndexRef.current = activeIndex;
    carouselAnimatingRef.current = false;
    setSelectedIndex(activeIndex);
    setMobileSelectedIndex(activeIndex);
  }, [activeIndex]);

  useLayoutEffect(() => {
    if (!returnSnapshot || !Number.isFinite(returnSnapshot.scrollY)) return undefined;
    let frame = 0;
    window.scrollTo({ top: returnSnapshot.scrollY, behavior: "auto" });
    frame = requestAnimationFrame(() => {
      window.scrollTo({ top: returnSnapshot.scrollY, behavior: "auto" });
    });
    return () => cancelAnimationFrame(frame);
  }, [returnSnapshot]);

  useEffect(() => {
    const carousel = carouselRef.current;
    if (!carousel) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setCarouselSize({ width, height });
    });
    observer.observe(carousel);
    return () => observer.disconnect();
  }, []);

  useGSAP(
    () => {
      if (!carouselSize.width || !carouselSize.height) return;
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const previousIndex = previousAnimatedIndexRef.current;
      const isClockwiseStep = selectedIndex === nextClockwiseStageIndex(
        previousIndex,
        stageDescriptions.length,
      );
      const shouldAnimate = hasAnimatedRef.current && isClockwiseStep && !reduced;
      const orbitPoint = (angle) => {
        const radians = (angle * Math.PI) / 180;
        return {
          x: carouselSize.width * (0.1466667 + 0.3266667 * Math.cos(radians)),
          y: carouselSize.height * 0.2886751 * Math.sin(radians),
        };
      };
      const appearances = [
        { scale: 1, opacity: 1, zIndex: 3 },
        { scale: 0.55, opacity: 0.78, zIndex: 2 },
        { scale: 0.55, opacity: 0.66, zIndex: 1 },
      ];
      const slotAngles = [180, 300, 420];
      const timeline = shouldAnimate
        ? gsap.timeline({
            defaults: { overwrite: "auto" },
            onComplete: () => {
              carouselAnimatingRef.current = false;
              advanceClockwise();
            },
          })
        : null;

      desktopStageRefs.current.forEach((node, index) => {
        if (!node) return;
        const relative = clockwiseOrbitSlot(
          index,
          selectedIndex,
          stageDescriptions.length,
        );
        const targetPoint = orbitPoint(slotAngles[relative]);
        const targetAppearance = appearances[relative];
        const target = {
          xPercent: -50,
          yPercent: -50,
          x: targetPoint.x,
          y: targetPoint.y,
          scale: targetAppearance.scale,
          opacity: 1,
          "--stage-dim-opacity": targetAppearance.opacity,
          zIndex: targetAppearance.zIndex,
          force3D: true,
        };

        if (!timeline) {
          gsap.set(node, { ...target, overwrite: true });
          return;
        }

        const previousRelative = clockwiseOrbitSlot(
          index,
          previousIndex,
          stageDescriptions.length,
        );
        const previousAppearance = appearances[previousRelative];
        const midPoint = orbitPoint(slotAngles[previousRelative] + 60);
        timeline.to(node, {
          keyframes: [
            {
              xPercent: -50,
              yPercent: -50,
              x: midPoint.x,
              y: midPoint.y,
              scale: (previousAppearance.scale + targetAppearance.scale) / 2,
              opacity: 1,
              "--stage-dim-opacity": (previousAppearance.opacity + targetAppearance.opacity) / 2,
              zIndex: Math.max(previousAppearance.zIndex, targetAppearance.zIndex),
              duration: 0.34,
              ease: "sine.in",
            },
            {
              ...target,
              duration: 0.38,
              ease: "sine.out",
            },
          ],
        }, 0);
      });
      previousAnimatedIndexRef.current = selectedIndex;
      hasAnimatedRef.current = true;

      if (!timeline) {
        carouselAnimatingRef.current = false;
        if (selectedIndexRef.current !== requestedIndexRef.current) {
          cancelAnimationFrame(carouselContinuationFrameRef.current);
          carouselContinuationFrameRef.current = requestAnimationFrame(() => {
            carouselContinuationFrameRef.current = 0;
            advanceClockwise();
          });
        }
      }
    },
    {
      dependencies: [
        advanceClockwise,
        selectedIndex,
        carouselSize.width,
        carouselSize.height,
      ],
      scope: carouselRef,
    },
  );

  useGSAP(
    () => {
      const node = mobileStageRefs.current[mobileSelectedIndex];
      if (!node || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      gsap.fromTo(
        node.querySelector(".map-artboard"),
        { y: 14, scale: 0.97, opacity: 0.72 },
        { y: 0, scale: 1, opacity: 1, duration: 0.5, ease: "power2.out", overwrite: "auto" },
      );
    },
    { dependencies: [mobileSelectedIndex], scope: mobileScrollerRef },
  );

  useEffect(() => {
    const scroller = mobileScrollerRef.current;
    if (!scroller || !window.matchMedia("(max-width: 720px)").matches) return undefined;
    mobileReadyRef.current = false;
    let frame = 0;
    let followupFrame = 0;
    let cancelled = false;
    const images = [...scroller.querySelectorAll("img")];
    const imageReady = images.map((image) =>
      image.complete
        ? Promise.resolve()
        : new Promise((resolve) => {
            image.addEventListener("load", resolve, { once: true });
            image.addEventListener("error", resolve, { once: true });
          }),
    );
    Promise.all(imageReady).then(() => {
      if (cancelled) return;
      frame = requestAnimationFrame(() => {
        const targetIndex = initialMobileScrollLeftRef.current == null
          ? activeIndex
          : restoredMobileSelectedIndex;
        const current = mobileStageRefs.current[targetIndex];
        if (!current) return;
        scroller.style.scrollSnapType = "none";
        scroller.scrollLeft = Number.isFinite(initialMobileScrollLeftRef.current)
          ? initialMobileScrollLeftRef.current
          : Math.max(0, current.offsetLeft - (scroller.clientWidth - current.clientWidth) / 2);
        initialMobileScrollLeftRef.current = null;
        setMobileSelectedIndex(targetIndex);
        followupFrame = requestAnimationFrame(() => {
          scroller.style.scrollSnapType = "";
          mobileReadyRef.current = true;
        });
      });
    });
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      cancelAnimationFrame(followupFrame);
    };
  }, [activeIndex]);

  const preserveGrowthPathView = (event) => {
    const anchor = event.target.closest?.("a[href]");
    if (!anchor) return;
    const taskPath = new URL(anchor.href, window.location.href).hash
      .replace(/^#/, "")
      .split("?")[0];
    if (!taskPath.startsWith("/task/")) return;
    growthPathReturnSnapshot = {
      taskPath,
      scrollY: window.scrollY,
      selectedIndex,
      mobileSelectedIndex,
      mobileScrollLeft: mobileScrollerRef.current?.scrollLeft ?? 0,
    };
  };

  const handleMobileScroll = () => {
    if (!mobileReadyRef.current) return;
    if (mobileRafRef.current) cancelAnimationFrame(mobileRafRef.current);
    mobileRafRef.current = requestAnimationFrame(() => {
      const scroller = mobileScrollerRef.current;
      if (!scroller) return;
      const center = scroller.getBoundingClientRect().left + scroller.clientWidth / 2;
      let closest = mobileSelectedIndex;
      let distance = Number.POSITIVE_INFINITY;
      mobileStageRefs.current.forEach((node, index) => {
        if (!node) return;
        const rect = node.getBoundingClientRect();
        const nextDistance = Math.abs(rect.left + rect.width / 2 - center);
        if (nextDistance < distance) {
          closest = index;
          distance = nextDistance;
        }
      });
      if (closest !== mobileSelectedIndex) setMobileSelectedIndex(closest);
    });
  };

  const handleKeyDown = (event) => {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      selectNext();
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      selectPrevious();
    }
  };

  return (
    <main
      className="ref-page path-screen map-path-screen tasks-hub-screen"
      onClickCapture={preserveGrowthPathView}
    >
      <header className="tasks-hub-heading">
        <div className="tasks-hub-title">
          <small>{copy(language, "TASKS", "我的任务")}</small>
          <h1>{copy(language, "Start here today", "今天从这里开始")}</h1>
          <p>{copy(language, "Start with the two required actions below. The islands keep your complete required-task path.", "先完成上方推荐任务，也可以在下方查看全部必修任务。")}</p>
        </div>
        <div className="tasks-hub-summary">
          <div>
            <span><CalendarBlank size={18} />{copy(language, "Day", "第")} {teacher.day} / {teacher.totalDays}</span>
            <span><ClipboardText size={18} />{active.title}</span>
          </div>
          <p><strong>{done}</strong> / {growthMapTasks.length} {copy(language, "required tasks completed", "项必修任务已完成")}</p>
          <ProgressBar value={(done / growthMapTasks.length) * 100} />
        </div>
      </header>

      <section
        className="tasks-current-section"
        aria-labelledby="tasks-current-title"
      >
        <div className="tasks-section-heading">
          <div>
            <small>{copy(language, "DO THIS NEXT", "建议先做")}</small>
            <h2 id="tasks-current-title">{copy(language, "Required tasks", "必修任务")}</h2>
          </div>
          <span>{copy(language, "The system follows the configured order and does not rank by score.", "已按学习顺序为你排列。")}</span>
        </div>
        <div className={`tasks-required-grid ${secondaryRaw ? "has-secondary" : "single"}`}>
          <RequiredTaskShortcut raw={primaryRaw} language={language} priority="primary" />
          <RequiredTaskShortcut raw={secondaryRaw} language={language} priority="secondary" />
        </div>
      </section>

      <PersonalizedTaskGrid
        rawTasks={sortedPersonalizedTasks}
        language={language}
      />

      <section
        className="tasks-required-path"
        aria-labelledby="required-path-title"
      >
        <header className="tasks-path-heading">
          <div>
            <small>{copy(language, "REQUIRED TASK PATH", "完整必修任务路径")}</small>
            <h2 id="required-path-title">{copy(language, "Explore the required-task islands", "查看成长地图")}</h2>
            <p>
              {copy(language, "Every island, stage and checkpoint below remains part of the full required-task journey.", "在成长地图中查看各阶段任务和开放顺序。")}
              <span className="tasks-map-mobile-hint">
                {copy(language, " Swipe left or right to view more stages.", " 可左右滑动查看更多阶段。")}
              </span>
            </p>
          </div>
          <span><strong>{done} / {growthMapTasks.length}</strong>{copy(language, "required tasks complete", "必修任务已完成")}</span>
        </header>

      <section
        className={`desktop-map-carousel selected-stage-${selectedIndex}`}
        ref={carouselRef}
        tabIndex="0"
        role="region"
        aria-roledescription="carousel"
        aria-label={copy(language, "Growth path module carousel", "成长阶段轮播")}
        onKeyDown={handleKeyDown}
      >
        <svg
          className="map-route-network"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <ellipse className="map-route-underlay" cx="64.667" cy="50" rx="32.667" ry="28.868" />
          <ellipse className="map-route-dashes" cx="64.667" cy="50" rx="32.667" ry="28.868" pathLength="100" />
        </svg>
        {stageDescriptions.map((stage, index) => (
          <StageMap
            key={stage.id}
            index={index}
            tasks={growthMapTasks}
            language={language}
            selected={selectedIndex === index}
            focusId={primaryRaw?.id}
            stageRef={(node) => { desktopStageRefs.current[index] = node; }}
            onSelect={() => selectStageClockwise(index)}
          />
        ))}
        <div className="map-carousel-controls">
          <button type="button" onClick={selectPrevious} aria-label={copy(language, "Previous module", "上一阶段")}>
            <ArrowLeft size={20} weight="bold" />
          </button>
          <div className="map-carousel-dots" aria-label={copy(language, "Select module", "选择成长阶段")}>
            {stageDescriptions.map((stage, index) => (
              <button
                key={stage.id}
                type="button"
                className={selectedIndex === index ? "active" : ""}
                onClick={() => selectStageClockwise(index)}
                aria-label={`${copy(language, "Module", "成长阶段")} ${stage.number}`}
                aria-current={selectedIndex === index ? "true" : undefined}
              />
            ))}
          </div>
          <button type="button" onClick={selectNext} aria-label={copy(language, "Next module", "下一阶段")}>
            <ArrowRight size={20} weight="bold" />
          </button>
          <span>{copy(language, "Choose any stage; the islands always move clockwise", "选择任意阶段，海岛始终顺时针转动")}</span>
        </div>
      </section>

      <section
        className="mobile-map-scroller"
        ref={mobileScrollerRef}
        onScroll={handleMobileScroll}
        aria-label={copy(language, "Growth modules from left to right", "由左至右的成长阶段")}
      >
        {stageDescriptions.map((stage, index) => (
          <StageMap
            key={stage.id}
            index={index}
            tasks={growthMapTasks}
            language={language}
            selected={mobileSelectedIndex === index}
            focusId={primaryRaw?.id}
            mobile
            stageRef={(node) => { mobileStageRefs.current[index] = node; }}
          />
        ))}
      </section>
      </section>
      <MobileNav language={language} unreadCount={unreadCount} onMessagesOpen={onMessagesOpen} />
    </main>
  );
}

function TaskDetailPage({
  tasks,
  onRefresh,
  onTaskSubmitted,
  onHelp,
  language,
  previewMode = false,
}) {
  const { taskId } = useParams();
  const raw = tasks.find(
    (task) => task.id === taskId || task.backendId === taskId,
  );
  const viewedTasksRef = useRef(new Set());
  const analyticsViewedTasksRef = useRef(new Set());
  const analyticsExitTimerRef = useRef(null);
  const analyticsVisitRef = useRef(null);
  const latestTaskStatusRef = useRef(raw?.status);
  const attemptedTaskIdRef = useRef(null);
  const [missingTaskLoading, setMissingTaskLoading] = useState(!raw);
  const [kuozhiProgressState, setKuozhiProgressState] = useState(null);
  useEffect(() => {
    setKuozhiProgressState(null);
  }, [raw?.backendId, raw?.method]);
  useEffect(() => {
    latestTaskStatusRef.current = raw?.status;
  }, [raw?.status]);
  useEffect(() => {
    if (previewMode || !raw?.backendId) return undefined;
    if (analyticsExitTimerRef.current?.taskId === raw.backendId) {
      window.clearTimeout(analyticsExitTimerRef.current.timer);
      analyticsExitTimerRef.current = null;
    }
    let visit = analyticsVisitRef.current;
    if (!visit || visit.taskId !== raw.backendId) {
      const attribution = taskEntryAttribution(raw.backendId);
      visit = {
        attribution,
        startedAt: performance.now(),
        task: raw,
        taskId: raw.backendId,
      };
      analyticsVisitRef.current = visit;
    }
    if (!analyticsViewedTasksRef.current.has(raw.backendId)) {
      analyticsViewedTasksRef.current.add(raw.backendId);
      trackProductEvent("TASK_DETAIL_VIEWED", {
        task: raw,
        properties: visit.attribution,
      });
      if (raw.status === "started") {
        trackProductEvent("TASK_PROGRESS_RESUMED", {
          task: raw,
          properties: { ...visit.attribution, result: "RESUMED" },
        });
      }
    }
    return () => {
      const timer = window.setTimeout(() => {
        if (latestTaskStatusRef.current !== "completed") {
          trackProductEvent("TASK_EXITED", {
            task: visit.task,
            properties: {
              ...visit.attribution,
              effectiveDurationMs: Math.max(
                0,
                Math.round(performance.now() - visit.startedAt),
              ),
              taskStatus: latestTaskStatusRef.current || visit.task.status,
            },
          });
        }
        if (analyticsExitTimerRef.current?.timer === timer) {
          analyticsExitTimerRef.current = null;
        }
      }, 500);
      analyticsExitTimerRef.current = { taskId: visit.taskId, timer };
    };
  }, [previewMode, raw?.backendId]);
  useEffect(() => {
    if (previewMode || raw || !taskId || attemptedTaskIdRef.current === taskId) return;
    let active = true;
    attemptedTaskIdRef.current = taskId;
    setMissingTaskLoading(true);
    Promise.resolve(onRefresh?.(taskId))
      .catch(() => undefined)
      .finally(() => {
        if (active) setMissingTaskLoading(false);
      });
    return () => {
      active = false;
    };
  }, [onRefresh, previewMode, raw, taskId]);
  useEffect(() => {
    if (previewMode || !raw?.backendId || raw.backendStatus !== "ASSIGNED" || viewedTasksRef.current.has(raw.backendId)) return;
    viewedTasksRef.current.add(raw.backendId);
    viewTask(raw.backendId, raw.stateVersion)
      .then(() => onRefresh?.(raw.backendId))
      .catch(() => {
        viewedTasksRef.current.delete(raw.backendId);
      });
  }, [onRefresh, previewMode, raw]);
  if (
    !raw &&
    !previewMode &&
    (missingTaskLoading || attemptedTaskIdRef.current !== taskId)
  ) {
    return (
      <main className="ref-page">
        <p>{copy(language, "Loading task…", "正在加载任务…")}</p>
      </main>
    );
  }
  if (!raw) return <Navigate to="/path" replace />;
  const task = localizeTask(raw, language);
  const isProfileCredentials = raw.id === "profile-credentials";
  const isLessonPreparation = raw.id === "lesson-preparation";
  const isKuozhiTask = ["external_course", "profile_credentials"].includes(raw.method);
  const profileHasAction = isProfileCredentials && raw.externalStatusItems?.some((item) => item.status === "action_required");
  const approvedExternalItems = raw.externalStatusItems?.filter((item) => item.status === "approved").length || 0;
  const externalItemCount = raw.externalStatusItems?.length || 0;
  const progress = raw.progress ?? (
    raw.status === "completed"
      ? 100
      : ["started", "submitted", "verifying", "retry_required"].includes(raw.status)
        ? 55
        : 0
  );
  const mood =
    raw.status === "completed"
      ? "celebrate"
      : raw.status === "retry_required"
        ? "thumb"
        : ["submitted", "verifying"].includes(raw.status)
          ? "happy"
          : "cheer";
  const tokiMotion =
    ["retry_required", "failed_final"].includes(raw.status)
      ? "encourage"
      : ["submitted", "verifying"].includes(raw.status)
        ? "scan"
        : raw.locked || raw.status === "completed"
          ? undefined
          : "welcome";
  const workspaceCopy = raw.method === "content_pending" && raw.taskCategory === "personalized"
    ? [
        "Content pending",
        "内容待补充",
        "This personalized improvement task is confirmed; its official content and completion method are still being prepared.",
        "这项个性化改善任务已经确认，正式内容和完成方式仍在准备中。",
      ]
    : workspaceMeta[raw.method] || ["Task actions", "任务操作", "Complete the action below to update your task progress.", "完成下方操作后，任务进度会自动更新。"];
  const [workspaceTitleEn, workspaceTitleZh, workspaceHintEn, workspaceHintZh] = workspaceCopy;
  return (
    <main
      className={[
        "ref-page",
        "task-screen",
        isLessonPreparation ? "lesson-preparation-screen" : "",
        raw.method === "external_course" ? "kuozhi-task-screen" : "",
      ].filter(Boolean).join(" ")}
    >
      <div className="task-route-actions">
        <Link
          className="task-home-button"
          to="/path"
          data-onboarding-target="task-result-next-action"
          data-onboarding-scroll-block="start"
        >
          <ArrowLeft size={17} />
          {copy(language, "Back to Tasks", "返回任务列表")}
        </Link>
      </div>
      <div className="task-layout">
        <section className="task-main-card">
          <div
            className="task-title-row"
            data-onboarding-target="task-result-summary"
            data-onboarding-scroll-block="start"
          >
            <div>
              <h1>{task.name}</h1>
              <div className="task-pills">
                <span>{raw.taskCategory === "personalized" ? copy(language, "Personalized", "个性化") : copy(language, "Required", "必修")}</span>
                {raw.taskCategory !== "personalized" && <span>{task.stage}</span>}
                <span>{statusLabel(language, displayStatus(raw))}</span>
                {raw.locked && <span>{copy(language, "Preview only · Opens later", "暂未开放 · 可以先查看")}</span>}
                {raw.dueStatus === "overdue" && <span className="supportive-overdue">{copy(language, "Recommended catch-up", "还有内容未完成 · 建议尽快完成")}</span>}
              </div>
            </div>
          </div>
          <div
            className="task-info-grid"
            data-onboarding-target="task-instructions"
            data-onboarding-max-height="430"
            data-onboarding-scroll-block="start"
          >
              <article>
                <span className="outlined-icon">
                  <Star size={22} weight="fill" />
                </span>
                <div>
                  <h3>{copy(language, "Why this task", "为什么要做")}</h3>
                  <div>{task.reason}</div>
                  {raw.taskCategory !== "personalized" && (
                    <TaskEvidenceDetails facts={raw.signalFacts} language={language} />
                  )}
                </div>
              </article>
              <article>
                <span className="outlined-icon">
                  <ClipboardText size={22} weight="fill" />
                </span>
                <div>
                  <h3>{copy(language, "How to complete", "怎么完成")}</h3>
                  <div>{task.result}</div>
                </div>
              </article>
              <article>
                <span className="outlined-icon">
                  <Target size={22} weight="fill" />
                </span>
                <div>
                  <h3>{copy(language, "Completion standard", "完成标准")}</h3>
                  <div>{task.standard}</div>
                </div>
              </article>
              <article>
                <span className="outlined-icon">
                  <Sparkle size={22} weight="fill" />
                </span>
                <div>
                  <h3>{copy(language, "What you’ll gain", "完成收益")}</h3>
                  <div>{task.value}</div>
                </div>
              </article>
          </div>
          {(isProfileCredentials || isLessonPreparation) && (
            <TaskEvidenceDetails facts={raw.signalFacts} language={language} standalone />
          )}
          <section
            className="task-workspace"
            data-onboarding-target="task-workspace"
            data-onboarding-max-height="360"
            data-onboarding-scroll-block="start"
          >
            {!isLessonPreparation && !isProfileCredentials && (
              <div className="workspace-heading">
                <h2>{isProfileCredentials ? copy(language, "Profile status", "档案状态") : copy(language, workspaceTitleEn, workspaceTitleZh)}</h2>
                <p>{isProfileCredentials ? copy(language, "See what is complete, what still needs action and what to do next.", "查看哪些项目已完成、哪些仍需处理，以及下一步做什么。") : copy(language, workspaceHintEn, workspaceHintZh)}</p>
              </div>
            )}
            <Suspense fallback={<LazyPanelFallback />}>
              <IntegratedTaskFlow
                key={task.backendId || task.id}
                task={previewMode
                  ? { ...raw, locked: true, previewReadOnly: true }
                  : raw}
                onRefresh={onRefresh}
                onTaskSubmitted={onTaskSubmitted}
                onHelp={onHelp}
                onKuozhiProgressStateChange={isKuozhiTask ? setKuozhiProgressState : undefined}
              />
            </Suspense>
          </section>
        </section>
        {!isLessonPreparation && <aside className="task-aside">
          <section className="task-facts">
            <div>
              <span className="icon-disc pale">
                <Clock size={22} />
              </span>
              {task.duration}
            </div>
            <div>
              <span className="icon-disc pale">
                <CalendarBlank size={22} />
              </span>
              {task.due}
            </div>
            {raw.taskCategory !== "personalized" && (
              <div>
                <span className="icon-disc yellow">
                  <Star size={22} />
                </span>
                {task.priority}
              </div>
            )}
            <div>
              <span className="icon-disc pale">
                <ClipboardText size={20} />
              </span>
              <span>
                {isProfileCredentials
                  ? <strong className="profile-fact-count">{copy(language, `${approvedExternalItems} of ${externalItemCount} approved`, `已通过 ${approvedExternalItems} / ${externalItemCount} 项`)}</strong>
                  : <>{statusLabel(language, displayStatus(raw))}<ProgressBar value={progress} /></>}
              </span>
            </div>
          </section>
          {isKuozhiTask ? (
            <KuozhiProgressCard
              canRefresh={kuozhiProgressState?.canRefresh}
              className="kuozhi-progress-card--sidebar"
              loading={!kuozhiProgressState || kuozhiProgressState.loading}
              onRefresh={kuozhiProgressState?.onRefresh}
              progress={kuozhiProgressState?.progress}
              progressError={kuozhiProgressState?.launchError || kuozhiProgressState?.progressError}
              refreshing={kuozhiProgressState?.refreshing}
            />
          ) : (
            <section className="task-execution-card">
              <h3>{copy(language, "Task progress", "任务进度")}</h3>
              <dl>
                <div><dt>{copy(language, "Current result", "当前状态")}</dt><dd>{statusLabel(language, displayStatus(raw))}</dd></div>
                <div><dt>{copy(language, "Allowed action", "下一步")}</dt><dd>{taskAction(language, raw)}</dd></div>
              </dl>
            </section>
          )}
          <section className="task-toki">
            <Toki
              mood={mood}
              motion={tokiMotion}
              loop={tokiMotion === "scan"}
            />
            <strong>
              {raw.status === "completed"
                ? copy(
                    language,
                    "A meaningful step\ncomplete.",
                    "又完成了重要一步。",
                  )
                : raw.locked
                  ? copy(
                      language,
                      "Preview the goal.\nIt opens with your\nnext stage.",
                      "可以先看看任务内容，\n开放后再开始。",
                    )
                  : isProfileCredentials
                    ? profileHasAction
                      ? copy(
                          language,
                          "One item is pending.\nThe latest result\nappears here.",
                          "还有一项未完成，\n最新结果会在\n本页更新。",
                        )
                      : copy(
                          language,
                          "Nothing else to do\nwhile your credential\nis under review.",
                          "资质审核期间\n无需重复提交。",
                        )
                    : copy(
                        language,
                        "You’re moving\nforward one clear\nstep at a time.",
                        "每次完成一个\n清晰的小步骤，\n都在向前成长。",
                      )}
            </strong>
          </section>
        </aside>}
      </div>
      {!isLessonPreparation && <section className="mobile-task-toki">
        <Toki
          mood={mood}
          motion={tokiMotion}
          loop={tokiMotion === "scan"}
        />
        <span>
          {raw.status === "completed"
            ? copy(
                language,
                "This task is recorded in your growth progress.",
                "这项任务已记录到你的成长进度。",
              )
            : isProfileCredentials
              ? profileHasAction
                ? copy(
                    language,
                    "One status has not passed yet. The latest result will appear here.",
                    "还有一项状态未通过，最新结果会在本页更新。",
                  )
                : copy(
                    language,
                    "No action is needed while the status is under review.",
                    "状态审核期间无需操作。",
                  )
              : copy(
                  language,
                  "Clear steps, then one confident action at a time.",
                  "先看清步骤，再从容完成一个行动。",
                )}
        </span>
      </section>}
    </main>
  );
}

function DimensionVisual({ visual, language }) {
  if (!visual) return null;
  if (visual.type === "activity") {
    return (
      <div className="dimension-growth-summary" aria-label={copy(language, `Recently added ${visual.value} points`, `近期新增 ${visual.value} 分`)}>
        <strong>+{visual.value}</strong>
        <span>{copy(language, `latest ${visual.sourceCount} classes`, `近 ${visual.sourceCount} 节课`)}</span>
      </div>
    );
  }
  if (visual.type === "checks") {
    return (
      <div className="dimension-checks" aria-label={copy(language, `${visual.value} of ${visual.total} checks passed`, `${visual.total} 项中已通过 ${visual.value} 项`)}>
        {Array.from({ length: visual.total }, (_, index) => (
          <span key={index} className={index < visual.value ? "passed" : ""}>
            {index < visual.value && <Check size={13} weight="bold" />}
          </span>
        ))}
      </div>
    );
  }
  if (visual.type === "samples") {
    return (
      <div className="dimension-samples" aria-label={copy(language, `${visual.value} feedback samples`, `${visual.value} 个评价样本`)}>
        {Array.from({ length: Math.min(8, visual.value) }, (_, index) => (
          <UserCircle key={index} size={18} weight="fill" />
        ))}
      </div>
    );
  }
  return <ProgressBar value={visual.value} />;
}

function DimensionCard({ dimension, active, onToggle, language, column }) {
  const title = localizeTitValue(dimension.title, language);
  const panelId = `dimension-panel-${dimension.id}`;
  const DimensionIcon = dimensionIcons[dimension.id] || ChartLineUp;
  return (
    <article
      className={`dimension-card-new ${active ? "active" : ""}`}
      style={{ "--dimension-column": column }}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={active}
        aria-controls={panelId}
      >
        <span className="dimension-card-icon" aria-hidden="true">
          <DimensionIcon size={26} weight="duotone" />
        </span>
        <div className="dimension-card-heading">
          <div>
            <h3>{title}</h3>
            <small>{dimension.englishTitle}</small>
          </div>
          <span className={`dimension-state ${dimension.statusTone}`}>
            {localizeTitValue(dimension.status, language)}
          </span>
        </div>
        <div className="dimension-card-score">
          <strong>{dimension.score ?? "—"}</strong>
          <span>{copy(language, "pts", "分")}</span>
          {dimension.unbounded && (
            <small className="is-unbounded">
              {copy(language, "No fixed cap", "累计无上限")}
            </small>
          )}
          {dimension.cap && (
            <small>{copy(language, `cap ${dimension.cap}`, `上限 ${dimension.cap} 分`)}</small>
          )}
        </div>
        <p>{localizeTitValue(dimension.headline, language)}</p>
        <DimensionVisual visual={dimension.visual} language={language} />
        <span className="dimension-expand-label">
          {copy(language, active ? "Hide" : "Metrics", active ? "收起指标" : "展开指标")}
          <CaretDown size={16} />
        </span>
      </button>
    </article>
  );
}

function MetricCard({ metric, language }) {
  const value = localizeTitValue(metric.value, language);
  const unit = localizeTitValue(metric.unit, language);
  const note = localizeTitValue(metric.note, language);
  const label = localizeTitValue(metric.label, language);
  const normalizedLabel = `${localizeTitValue(metric.label, "en")} ${label}`.toLowerCase();
  const NumberIcon = normalizedLabel.includes("network") || normalizedLabel.includes("带宽") || normalizedLabel.includes("延迟")
    ? WifiHigh
    : normalizedLabel.includes("class") || normalizedLabel.includes("课程") || normalizedLabel.includes("完课")
      ? CalendarCheck
      : normalizedLabel.includes("task") || normalizedLabel.includes("任务")
        ? ListChecks
        : normalizedLabel.includes("rating") || normalizedLabel.includes("好评") || normalizedLabel.includes("收藏")
          ? Medal
          : Timer;
  const StatusIcon = normalizedLabel.includes("device") || normalizedLabel.includes("cpu") || normalizedLabel.includes("memory") || normalizedLabel.includes("设备") || normalizedLabel.includes("内存")
    ? Monitor
    : metric.tone === "positive"
      ? CheckCircle
      : metric.tone === "observing"
        ? Hourglass
        : ListChecks;

  const visual = (() => {
    if (metric.type === "ratio") {
      return (
        <div className="metric-gauge-visual">
          <span className="metric-gauge-icon"><Gauge size={42} weight="duotone" /></span>
          <div>
            <strong>{value}</strong>
            {unit && <span>{unit}</span>}
          </div>
          <progress max="100" value={metric.progress ?? 0} aria-label={`${label} ${value}`} />
        </div>
      );
    }
    if (metric.type === "checks") {
      const passed = Math.round((metric.progress ?? 0) / 20);
      return (
        <div className="metric-check-visual" aria-label={`${label} ${value}`}>
          <div className="metric-check-icons">
            {Array.from({ length: 5 }, (_, index) => (
              <span key={index} className={index < passed ? "passed" : ""}>
                {index < passed && <Check size={14} weight="bold" />}
              </span>
            ))}
          </div>
          <strong>{value}</strong>
        </div>
      );
    }
    if (metric.type === "samples") {
      return (
        <div className="metric-sample-visual">
          <span className="metric-visual-icon"><UsersThree size={34} weight="duotone" /></span>
          <div><strong>{value}</strong>{unit && <span>{unit}</span>}</div>
          <div className="sample-progress" aria-hidden="true">
            {Array.from({ length: 12 }, (_, index) => (
              <span key={index} className={index < 8 ? "filled" : ""} />
            ))}
          </div>
        </div>
      );
    }
    if (metric.type === "trend") {
      return (
        <div className="metric-trend-visual">
          <span className="metric-visual-icon"><ChartLineUp size={34} weight="duotone" /></span>
          <div><strong>{value}</strong>{unit && <span>{unit}</span>}</div>
          <span className="metric-trend-caption">{copy(language, "Current trend", "当前趋势")}</span>
        </div>
      );
    }
    if (metric.type === "tags") {
      return (
        <div className="metric-tag-visual">
          <Tag size={28} weight="duotone" />
          <div>{String(value).split(/\s*·\s*/).map((item) => <span key={item}>{item}</span>)}</div>
        </div>
      );
    }
    if (metric.type === "status") {
      return (
        <div className="metric-status-visual">
          <span className="metric-visual-icon"><StatusIcon size={30} weight="duotone" /></span>
          <strong>{value}</strong>
          {unit && <span>{unit}</span>}
        </div>
      );
    }
    return (
      <div className="metric-number-visual">
        <span className="metric-visual-icon"><NumberIcon size={30} weight="duotone" /></span>
        <div><strong>{value}</strong>{unit && <span>{unit}</span>}</div>
      </div>
    );
  })();
  return (
    <article className={`metric-card metric-${metric.type}${metric.tone ? ` ${metric.tone}` : ""}`}>
      <span className="metric-label">{label}</span>
      {visual}
      {note && <small>{note}</small>}
    </article>
  );
}

function matrixCellLabel(value, language) {
  const localized = localizeTitValue(value, language);
  if (typeof localized === "number") return localized > 0 ? `+${localized}` : "0";
  const labels = {
    pending: copy(language, "Pending", "审核中"),
    ready: copy(language, "Ready", "已准备"),
    improve: copy(language, "Improve", "可改善"),
    completed: copy(language, "Completed", "已完成"),
    "to-do": copy(language, "To do", "待完成"),
    "in-progress": copy(language, "In progress", "进行中"),
  };
  return labels[localized] || localized;
}

function matrixCellTone(value, language) {
  const localized = String(localizeTitValue(value, language)).toLowerCase();
  if (typeof value === "number") return value > 0 ? "earned" : "empty";
  if (localized === "pending") return "pending";
  if (localized === "improve") return "improve";
  if (["ready", "completed"].includes(localized)) return "ready";
  if (["to-do", "in-progress"].includes(localized)) return "neutral";
  return "info";
}

function matrixDataLabel(value, language) {
  const localized = localizeTitValue(value, language);
  if (typeof localized === "number") return String(localized);
  return matrixCellLabel(value, language);
}

function MatrixDataScore({ data, score, language, taskLinked, onOpenTask }) {
  const dataLabel = matrixDataLabel(data, language);
  const scoreLabel = matrixCellLabel(score, language);
  return (
    <div className={`matrix-data-score ${taskLinked ? "has-task" : ""}`}>
      <span className="matrix-raw-data" aria-label={`${copy(language, "Data", "数据")}：${dataLabel}`}>
        <strong>{dataLabel}</strong>
      </span>
      {taskLinked ? (
        <button className={`matrix-score-value ${matrixCellTone(score, language)}`} type="button" onClick={onOpenTask}>
          <span aria-label={`${copy(language, "Score", "得分")}：${scoreLabel}`}><strong>{scoreLabel}</strong></span>
          <em>{copy(language, "Open task", "进入任务")}<ArrowRight size={12} /></em>
        </button>
      ) : (
        <span className={`matrix-score-value ${matrixCellTone(score, language)}`} aria-label={`${copy(language, "Score", "得分")}：${scoreLabel}`}>
          <strong>{scoreLabel}</strong>
        </span>
      )}
    </div>
  );
}

function AttributionMatrix({ dimension, language, onOpenTask }) {
  const { matrix } = dimension;
  return (
    <section className="attribution-view" aria-label={copy(language, "Indicator data and score attribution", "指标数据与得分明细")}>
      <div className="attribution-view-heading">
        <div>
          <span>{copy(language, "DATA AND SCORE", "数据与得分")}</span>
          <h3>{copy(language, "See the data and score behind every indicator", "查看每项指标的数据和得分")}</h3>
        </div>
        <small>{copy(language, "Score source", "得分来源")}</small>
      </div>

      <div className="data-score-guide"><Info size={14} />{copy(language, "Gray shows data · Blue shows score", "灰色为数据 · 蓝色为得分")}</div>

      <div className="attribution-matrix-shell">
        <table className="attribution-matrix">
          <thead>
            <tr>
              <th>{copy(language, "Detailed indicator", "具体指标")}</th>
              {matrix.sources.map((source) => (
                <th key={source.id}>
                  <strong>{localizeTitValue(source.label, language)}</strong>
                  <small>{localizeTitValue(source.meta, language)}</small>
                </th>
              ))}
              <th>{copy(language, "Indicator total", "项目合计")}</th>
            </tr>
          </thead>
          <tbody>
            {matrix.rows.map((row) => (
              <tr key={localizeTitValue(row.label, "en")}>
                <th>
                  <strong>{localizeTitValue(row.label, language)}</strong>
                  {row.taskCell !== undefined && dimension.action && (
                    <button className="matrix-indicator-task" type="button" onClick={onOpenTask}>
                      <Sparkle size={12} weight="fill" />{copy(language, "Improvement task available", "已触发改善任务")}
                    </button>
                  )}
                </th>
                {row.cells.map((cell, cellIndex) => {
                  const taskLinked = row.taskCell === cellIndex && dimension.action;
                  const data = row.dataCells?.[cellIndex] ?? cell;
                  return (
                    <td key={`${matrix.sources[cellIndex].id}-${localizeTitValue(row.label, "en")}`}>
                      <MatrixDataScore data={data} score={cell} language={language} taskLinked={taskLinked} onOpenTask={onOpenTask} />
                    </td>
                  );
                })}
                <td className="matrix-row-total">
                  <span aria-label={copy(language, "Data", "数据")}><strong>{localizeTitValue(row.dataTotal ?? row.total, language)}</strong></span>
                  <span aria-label={copy(language, "Score", "得分")}><strong>{localizeTitValue(row.total, language)}{typeof row.total === "number" ? copy(language, " pts", " 分") : ""}</strong></span>
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th>{copy(language, "Dimension total after source", "累计积分")}</th>
              {matrix.runningTotals.map((total, index) => <td key={matrix.sources[index].id}>{total}</td>)}
              <td>
                <strong>{dimension.score}</strong>
                {dimension.cap ? ` / ${dimension.cap}` : copy(language, " pts", " 分")}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      <div className="attribution-source-cards">
        {matrix.sources.map((source, sourceIndex) => (
          <article key={source.id}>
            <header>
              <div><strong>{localizeTitValue(source.label, language)}</strong><small>{localizeTitValue(source.meta, language)}</small></div>
              <span>{copy(language, "Running total", "累计积分")} {matrix.runningTotals[sourceIndex]}</span>
            </header>
            <dl>
              {matrix.rows.map((row) => {
                const cell = row.cells[sourceIndex];
                const data = row.dataCells?.[sourceIndex] ?? cell;
                const taskLinked = row.taskCell === sourceIndex && dimension.action;
                return (
                  <div key={localizeTitValue(row.label, "en")}>
                    <dt>{localizeTitValue(row.label, language)}</dt>
                    <dd>
                      <MatrixDataScore data={data} score={cell} language={language} taskLinked={taskLinked} onOpenTask={onOpenTask} />
                    </dd>
                  </div>
                );
              })}
            </dl>
          </article>
        ))}
      </div>

      <div className="attribution-mobile-summary">
        {matrix.rows.map((row) => (
          <article key={localizeTitValue(row.label, "en")}>
            <header>
              <div>
                <small>{copy(language, "DETAILED INDICATOR", "具体指标")}</small>
                <strong>{localizeTitValue(row.label, language)}</strong>
              </div>
              <div className="mobile-indicator-totals">
                <span aria-label={copy(language, "Data", "数据")}><strong>{localizeTitValue(row.dataTotal ?? row.total, language)}</strong></span>
                <span aria-label={copy(language, "Score", "得分")}><strong>{localizeTitValue(row.total, language)}{typeof row.total === "number" ? copy(language, " pts", " 分") : ""}</strong></span>
              </div>
            </header>
            {row.taskCell !== undefined && dimension.action && (
              <button className="mobile-indicator-task" type="button" onClick={onOpenTask}>
                <Sparkle size={15} weight="fill" />
                <span><strong>{copy(language, "An improvement task was triggered", "该指标已触发改善任务")}</strong><small>{localizeTitValue(dimension.action.label, language)}</small></span>
                <ArrowRight size={15} />
              </button>
            )}
            <details>
              <summary>
                {copy(language, "View source details", "查看来源明细")}
                <small>{matrix.sources.length} {copy(language, "sources", "个来源")}</small>
              </summary>
              <ul>
                {matrix.sources.map((source, sourceIndex) => {
                  const cell = row.cells[sourceIndex];
                  const data = row.dataCells?.[sourceIndex] ?? cell;
                  const taskLinked = row.taskCell === sourceIndex && dimension.action;
                  return (
                    <li key={source.id}>
                      <div>
                        <strong>{localizeTitValue(source.label, language)}</strong>
                        <small>{localizeTitValue(source.meta, language)}</small>
                      </div>
                      <MatrixDataScore data={data} score={cell} language={language} taskLinked={taskLinked} onOpenTask={onOpenTask} />
                    </li>
                  );
                })}
              </ul>
            </details>
          </article>
        ))}
      </div>

      <footer className="attribution-note"><Info size={17} />{localizeTitValue(matrix.note, language)}</footer>
    </section>
  );
}

function DimensionDetails({
  dimension,
  language,
  attributionUnavailable = false,
}) {
  const DimensionIcon = dimensionIcons[dimension.id] || ChartLineUp;
  const progress = dimension.progress;
  const [matrixPage, setMatrixPage] = useState(1);
  const isCourseScoreDimension = ["userFeedback", "reliability", "classQuality"]
    .includes(dimension.sourceKey);
  const sourceLabels = {
    ...courseScoreSourceLabels,
    CAPACITY_PEAK_SLOT_40: { en: "Peak-time bookable slots", zh: "高峰时段可约课时数" },
  };
  const sourceMeta = (source) => {
    if (source.lessonId) {
      const date = new Date(source.lessonStartedAt);
      const localizedDate = Number.isNaN(date.getTime())
        ? ""
        : date.toLocaleDateString(language === "zh" ? "zh-CN" : "en-US");
      return copy(
        language,
        `Class ${source.lessonNumber}${localizedDate ? ` · ${localizedDate}` : ""}`,
        `第 ${source.lessonNumber} 节课${localizedDate ? ` · ${localizedDate}` : ""}`,
      );
    }
    if (source.taskCode) {
      return Number(source.value) > 0
        ? copy(language, "Completed · points earned", "已完成 · 已获得积分")
        : copy(language, "Not complete yet", "尚未完成");
    }
    const units = {
      COUNT: copy(language, "records", "次"),
      CLASSES: copy(language, "classes", "节"),
      SLOTS: copy(language, "slots", "个课时"),
      TASKS: copy(language, "tasks", "项"),
    };
    const current = `${source.value ?? 0} ${units[source.unit] || ""}`;
    const value = Number(source.value);
    const totalScore = Number(source.score);
    const configuredPoints = Number(source.pointsPerUnit);
    const pointsPerUnit = Number.isFinite(configuredPoints)
      ? configuredPoints
      : value > 0 && Number.isFinite(totalScore)
        ? Math.round(((totalScore / value) + Number.EPSILON) * 100) / 100
        : null;
    if (
      isCourseScoreDimension
      && !source.lessonId
      && Number.isFinite(pointsPerUnit)
      && pointsPerUnit > 0
    ) {
      return copy(
        language,
        `${current} × ${pointsPerUnit} pts`,
        `${current} × ${pointsPerUnit} 分`,
      );
    }
    return source.target
      ? copy(language, `Current ${current} · target ${source.target}`, `当前 ${current} · 达标线 ${source.target}`)
      : copy(language, `Current ${current}`, `当前 ${current}`);
  };
  const sourceTitle = (source) => source.title
    || fixedTaskCatalog.find((task) => task.taskCode === source.taskCode)?.name
    || localizeTitValue(sourceLabels[source.sourceKey], language)
    || source.sourceKey;
  const indicatorGroups = Array.from(
    (dimension.sources || []).reduce((groups, source, index) => {
      const groupKey = source.taskCode
        ? `${source.sourceKey}:${source.taskCode}`
        : source.sourceKey || `source-${index}`;
      const current = groups.get(groupKey) || {
        key: groupKey,
        title: sourceTitle(source),
        sources: [],
        totalScore: 0,
        attributedScore: 0,
        summaryScore: null,
        summaryValue: null,
        hasUnattributed: false,
      };
      current.sources.push(source);
      if (Number.isFinite(Number(source.score))) {
        current.totalScore += Number(source.score);
        if (source.lessonId) {
          current.attributedScore += Number(source.score);
        }
      }
      if (Number.isFinite(Number(source.summaryScore))) {
        current.summaryScore = Number(source.summaryScore);
      }
      if (source.summaryValue !== null && source.summaryValue !== undefined) {
        current.summaryValue = Number(source.summaryValue);
      }
      current.hasUnattributed ||= source.attributionMissing === true;
      groups.set(groupKey, current);
      return groups;
    }, new Map()).values(),
  ).map((group) => {
    const authoritativeTotal = Number.isFinite(group.summaryScore)
      ? group.summaryScore
      : group.totalScore;
    const totalScore = Math.round(
      (authoritativeTotal + Number.EPSILON) * 100,
    ) / 100;
    const attributedScore = Math.round(
      (group.attributedScore + Number.EPSILON) * 100,
    ) / 100;
    return {
      ...group,
      totalScore,
      hasUnattributed:
        group.hasUnattributed || totalScore > attributedScore + 0.001,
      scoringLessonCount: new Set(
        group.sources
          .filter((source) => source.lessonId)
          .map((source) => source.lessonId),
      ).size,
      scoreByLesson: group.sources.reduce((scores, source) => {
        if (!source.lessonId) return scores;
        const score = Number(source.score);
        if (!Number.isFinite(score)) return scores;
        scores.set(source.lessonId, (scores.get(source.lessonId) || 0) + score);
        return scores;
      }, new Map()),
    };
  });
  const courseColumns = Array.from(
    (dimension.sources || []).reduce((lessons, source) => {
      if (source.lessonId && !lessons.has(source.lessonId)) {
        lessons.set(source.lessonId, {
          lessonId: source.lessonId,
          lessonNumber: source.lessonNumber,
          lessonStartedAt: source.lessonStartedAt,
        });
      }
      return lessons;
    }, new Map()).values(),
  );
  const usesCourseMatrix = courseColumns.length > 0;
  const isCapacityDimension = dimension.sourceKey === "capacity";
  const isRequiredTaskDimension = dimension.sourceKey === "newTeacherTask";
  const capacitySource = isCapacityDimension
    ? (dimension.sources || []).find((source) => source.sourceKey === "CAPACITY_PEAK_SLOT_40")
    : null;
  const capacityValue = Number(capacitySource?.value);
  const capacityTarget = Number(capacitySource?.target);
  const capacityScore = Number(capacitySource?.score);
  const capacityReached = Number.isFinite(capacityValue)
    && Number.isFinite(capacityTarget)
    && capacityTarget > 0
    && capacityValue >= capacityTarget;
  const capacityProgress = Number.isFinite(capacityValue)
    && Number.isFinite(capacityTarget)
    && capacityTarget > 0
    ? Math.min((capacityValue / capacityTarget) * 100, 100)
    : 0;
  const capacityRemaining = Number.isFinite(capacityValue)
    && Number.isFinite(capacityTarget)
    ? Math.max(capacityTarget - capacityValue, 0)
    : null;
  const matrixPageCount = Math.max(1, Math.ceil(courseColumns.length / COURSE_MATRIX_PAGE_SIZE));
  const safeMatrixPage = clampLessonPage(matrixPage, matrixPageCount);
  const matrixPageItems = buildLessonPageItems(safeMatrixPage, matrixPageCount);
  const matrixStart = (safeMatrixPage - 1) * COURSE_MATRIX_PAGE_SIZE;
  const visibleCourseColumns = courseColumns.slice(matrixStart, matrixStart + COURSE_MATRIX_PAGE_SIZE);
  const matrixRangeEnd = Math.min(matrixStart + COURSE_MATRIX_PAGE_SIZE, courseColumns.length);
  const scoreNumber = (value) => new Intl.NumberFormat(language === "zh" ? "zh-CN" : "en-US", {
    maximumFractionDigits: 2,
  }).format(Number(value) || 0);
  const lessonDate = (lesson) => {
    const date = new Date(lesson.lessonStartedAt);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString(language === "zh" ? "zh-CN" : "en-US", {
      month: "numeric",
      day: "numeric",
    });
  };
  const lessonLabel = (lesson) => copy(
    language,
    `Class ${lesson.lessonNumber}`,
    `第 ${lesson.lessonNumber} 节课`,
  );
  const selectMatrixPage = (nextPage) => {
    setMatrixPage(clampLessonPage(nextPage, matrixPageCount));
  };

  useEffect(() => {
    if (safeMatrixPage !== matrixPage) setMatrixPage(safeMatrixPage);
  }, [matrixPage, safeMatrixPage]);

  return (
    <section
      className="dimension-detail-panel"
      id={`dimension-panel-${dimension.id}`}
      aria-labelledby={`dimension-title-${dimension.id}`}
    >
      <div className="dimension-detail-heading">
        <span className="dimension-detail-icon" aria-hidden="true">
          <DimensionIcon size={22} weight="duotone" />
        </span>
        <div>
          <span>{copy(language, "DIMENSION DETAILS", "得分详情")}</span>
          <h2 id={`dimension-title-${dimension.id}`}>
            {localizeTitValue(dimension.title, language)}
          </h2>
          <p>{usesCourseMatrix
            ? copy(language, "Compare every indicator and class score source at a glance.", "直接对照各指标累计积分、来源课程和每课加分。")
            : isCapacityDimension
              ? copy(language, "See the current peak-time availability, target and point status.", "查看当前高峰时段可约课时数、达标线和积分状态。")
              : isRequiredTaskDimension
                ? copy(language, "See every required task and its current points.", "直接查看每项必修任务及当前积分。")
                : copy(language, "See each component's accumulated points.", "查看各子项累计积分。")}</p>
        </div>
      </div>
      <div className="dimension-data-meta">
        <span><Clock size={17} />{copy(language, "Updated", "更新于")}：{localizeTitValue(dimension.updated, language)}</span>
      </div>
      <div className="dimension-summary-only">
        {isCourseScoreDimension && attributionUnavailable ? (
          <SourceUnavailableCard
            compact
            language={language}
            title={copy(language, "Class attribution is temporarily unavailable", "逐课积分归因暂时无法加载")}
            message={copy(language, "The cumulative score is still available. Reload later to view its full class attribution.", "累计积分仍可正常查看，请稍后重新加载完整逐课归因。")}
          />
        ) : usesCourseMatrix ? (
          <article className="dimension-course-matrix">
            <header className="dimension-course-matrix-heading">
              <div>
                <span className="dimension-course-matrix-icon" aria-hidden="true">
                  <CalendarCheck size={20} weight="duotone" />
                </span>
                <span>
                  <strong>{copy(language, "Class score map", "课程积分分布")}</strong>
                  <small>
                    {copy(
                      language,
                      `${courseColumns.length} classes generated displayable points in this dimension`,
                      `本维度共有 ${courseColumns.length} 节课程产生可展示加分`,
                    )}
                  </small>
                </span>
              </div>
              <span className="dimension-course-matrix-legend">
                <i aria-hidden="true" />
                {copy(language, "Class points", "本课加分")}
              </span>
            </header>

            <div className="dimension-course-matrix-desktop">
              <table
                aria-label={copy(
                  language,
                  `${localizeTitValue(dimension.title, language)} indicator scores by class`,
                  `${localizeTitValue(dimension.title, language)}各指标逐课积分分布`,
                )}
              >
                <thead>
                  <tr>
                    <th scope="col">{copy(language, "Indicator", "指标")}</th>
                    {visibleCourseColumns.map((lesson) => (
                      <th scope="col" key={lesson.lessonId}>
                        <strong>{lessonLabel(lesson)}</strong>
                        <small>{lessonDate(lesson)}</small>
                      </th>
                    ))}
                    <th scope="col">{copy(language, "Indicator total", "指标累计")}</th>
                  </tr>
                </thead>
                <tbody>
                  {indicatorGroups.map((group) => (
                    <tr key={group.key}>
                      <th scope="row">
                        <strong>{group.title}</strong>
                        <small>
                          {group.hasUnattributed
                            ? group.scoringLessonCount > 0
                              ? copy(
                                  language,
                                  `${group.scoringLessonCount} attributed ${group.scoringLessonCount === 1 ? "class" : "classes"} · remaining points have no class attribution`,
                                  `${group.scoringLessonCount} 节已归因 · 其余积分暂无逐课归因`,
                                )
                              : copy(
                                  language,
                                  `${Number.isFinite(group.summaryValue) ? `${group.summaryValue} classes in the score summary · ` : ""}No class attribution yet`,
                                  `${Number.isFinite(group.summaryValue) ? `积分汇总 ${group.summaryValue} 节 · ` : ""}暂无逐课归因`,
                                )
                            : copy(
                                language,
                                `${group.scoringLessonCount} scoring ${group.scoringLessonCount === 1 ? "class" : "classes"}`,
                                `${group.scoringLessonCount} 节课程加分`,
                              )}
                        </small>
                      </th>
                      {visibleCourseColumns.map((lesson) => {
                        const score = group.scoreByLesson.get(lesson.lessonId);
                        return (
                          <td
                            className={score === undefined ? "" : "has-score"}
                            key={lesson.lessonId}
                            aria-label={score === undefined
                              ? copy(language, "No points added for this indicator in this class", "本课该指标没有加分")
                              : undefined}
                          >
                            {score !== undefined && <strong>+{scoreNumber(score)}</strong>}
                          </td>
                        );
                      })}
                      <td className="dimension-course-matrix-total">
                        <small>{copy(language, "Total", "累计")}</small>
                        <strong>+{scoreNumber(group.totalScore)}</strong>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="dimension-course-matrix-mobile">
              <div className="dimension-course-mobile-totals">
                {indicatorGroups.map((group) => (
                  <span key={group.key}>
                    <small>{group.title}</small>
                    <strong>+{scoreNumber(group.totalScore)} {copy(language, "pts", "分")}</strong>
                  </span>
                ))}
              </div>
              <div className="dimension-course-mobile-list">
                {visibleCourseColumns.map((lesson) => (
                  <article key={lesson.lessonId}>
                    <header>
                      <span>{String(lesson.lessonNumber).padStart(2, "0")}</span>
                      <div>
                        <strong>{lessonLabel(lesson)}</strong>
                        <small>{lessonDate(lesson)}</small>
                      </div>
                    </header>
                    <dl>
                      {indicatorGroups.map((group) => {
                        const score = group.scoreByLesson.get(lesson.lessonId);
                        if (score === undefined) return null;
                        return (
                          <div key={group.key}>
                            <dt>{group.title}</dt>
                            <dd>+{scoreNumber(score)} {copy(language, "pts", "分")}</dd>
                          </div>
                        );
                      })}
                    </dl>
                  </article>
                ))}
              </div>
            </div>

            {matrixPageCount > 1 && (
              <nav
                className="dimension-course-matrix-pagination"
                aria-label={copy(language, "Class score map pagination", "课程积分分布分页")}
              >
                <span aria-live="polite">
                  {copy(
                    language,
                    `Showing ${matrixStart + 1}–${matrixRangeEnd} of ${courseColumns.length} scoring classes`,
                    `当前显示 ${matrixStart + 1}–${matrixRangeEnd} / 共 ${courseColumns.length} 节加分课程`,
                  )}
                </span>
                <div className="dimension-course-matrix-pagination-controls">
                  <div className="dimension-course-matrix-pages">
                    <button
                      type="button"
                      disabled={safeMatrixPage === 1}
                      aria-label={copy(language, "Previous classes", "上一组课程")}
                      onClick={() => selectMatrixPage(safeMatrixPage - 1)}
                    >
                      <ArrowLeft size={16} weight="bold" />
                    </button>
                    {matrixPageItems.map((item) => typeof item === "number" ? (
                      <button
                        type="button"
                        key={item}
                        className={item === safeMatrixPage ? "active" : ""}
                        aria-current={item === safeMatrixPage ? "page" : undefined}
                        aria-label={copy(language, `Page ${item}`, `第 ${item} 页`)}
                        onClick={() => selectMatrixPage(item)}
                      >
                        {item}
                      </button>
                    ) : (
                      <span className="dimension-course-matrix-page-ellipsis" aria-hidden="true" key={item}>…</span>
                    ))}
                    <button
                      type="button"
                      disabled={safeMatrixPage === matrixPageCount}
                      aria-label={copy(language, "Next classes", "下一组课程")}
                      onClick={() => selectMatrixPage(safeMatrixPage + 1)}
                    >
                      <ArrowRight size={16} weight="bold" />
                    </button>
                  </div>
                  <label className="dimension-course-matrix-page-jump">
                    <span>{copy(language, "Jump to", "跳转到")}</span>
                    <select
                      aria-label={copy(language, "Jump to class score page", "跳转到课程积分页")}
                      value={safeMatrixPage}
                      onChange={(event) => selectMatrixPage(event.target.value)}
                    >
                      {Array.from({ length: matrixPageCount }, (_, index) => index + 1).map((pageNumber) => (
                        <option value={pageNumber} key={pageNumber}>
                          {copy(language, `Page ${pageNumber}`, `第 ${pageNumber} 页`)}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              </nav>
            )}
          </article>
        ) : isCapacityDimension && capacitySource ? (
          <article className="capacity-milestone-card">
            <header>
              <span aria-hidden="true"><Gauge size={23} weight="duotone" /></span>
              <div>
                <strong>{copy(language, "Peak-time bookable slots", "高峰时段可约课时数")}</strong>
                <p>
                  {copy(
                    language,
                    "This is the latest availability snapshot. Reaching the target awards points once, and awarded points remain locked.",
                    "这里展示最新可约课时快照；首次达到目标后一次性加分，已获得的积分永久保留。",
                  )}
                </p>
              </div>
              <em className={capacityReached ? "reached" : ""}>
                {capacityReached
                  ? copy(language, "Target reached", "已达标")
                  : copy(language, "In progress", "进行中")}
              </em>
            </header>
            <div className="capacity-milestone-values">
              <span>
                <small>{copy(language, "Current", "当前")}</small>
                <strong>{Number.isFinite(capacityValue) ? scoreNumber(capacityValue) : "—"}</strong>
                <em>{copy(language, "slots", "个课时")}</em>
              </span>
              <ArrowRight size={18} weight="bold" aria-hidden="true" />
              <span>
                <small>{copy(language, "Target", "达标线")}</small>
                <strong>{Number.isFinite(capacityTarget) ? scoreNumber(capacityTarget) : "—"}</strong>
                <em>{copy(language, "slots", "个课时")}</em>
              </span>
              <span className="capacity-milestone-score">
                <small>{copy(language, "Points status", "积分状态")}</small>
                <strong>
                  {Number.isFinite(capacityScore) && capacityScore > 0
                    ? `+${scoreNumber(capacityScore)} ${copy(language, "pts", "分")}`
                    : copy(language, "Not awarded", "暂未获得")}
                </strong>
              </span>
            </div>
            <div className="capacity-milestone-track" aria-hidden="true">
              <span style={{ width: `${capacityProgress}%` }} />
            </div>
            <footer>
              <CheckCircle size={17} weight={capacityReached ? "fill" : "regular"} />
              <span>
                {capacityReached
                  ? copy(
                      language,
                      "The availability milestone has been reached; later snapshot changes will not remove these points.",
                      "可上课里程碑已达成；后续快照值发生变化，也不会撤回已获得的积分。",
                    )
                  : copy(
                      language,
                      `${capacityRemaining ?? "—"} more peak-time bookable slots to reach the target.`,
                      `距离达标还差 ${capacityRemaining ?? "—"} 个高峰时段可约课时。`,
                    )}
              </span>
            </footer>
          </article>
        ) : isRequiredTaskDimension && indicatorGroups.length ? (
          <article className="required-task-score-summary">
            <header>
              <ListChecks size={22} weight="duotone" />
              <div>
                <strong>{copy(language, "Required-task details", "必修任务明细")}</strong>
                <p>{copy(language, "See each required task's latest status and points.", "查看每项必修任务的最新状态与积分。")}</p>
              </div>
              {progress && (
                <span>
                  {copy(
                    language,
                    `${progress.completed}/${progress.total} completed`,
                    `已完成 ${progress.completed}/${progress.total}`,
                  )}
                </span>
              )}
            </header>
            <ul className="required-task-score-list">
              {indicatorGroups.map((group) => {
                const source = group.sources[0];
                return (
                  <li key={group.key}>
                    <span className="required-task-score-code">{source.taskCode}</span>
                    <div>
                      <strong>{group.title}</strong>
                      <small>{sourceMeta(source)}</small>
                    </div>
                    <em>
                      {group.totalScore > 0
                        ? `+${scoreNumber(group.totalScore)} ${copy(language, "pts", "分")}`
                        : copy(language, "Not earned", "暂未获得")}
                    </em>
                  </li>
                );
              })}
            </ul>
          </article>
        ) : (
          <div className="dimension-source-empty">
            {isCourseScoreDimension
              ? copy(
                  language,
                  "No class-level score source is available for this dimension yet.",
                  "该维度暂时没有可准确定位到课程的积分来源。",
                )
              : copy(
                  language,
                  "No displayable score source is available yet.",
                  "暂时没有可展示的积分来源。",
                )}
          </div>
        )}
      </div>
      <footer className="dimension-detail-note">
        <Info size={17} />
        {isCapacityDimension
          ? copy(
              language,
              "The current value and point status use the latest scorecard.",
              "当前值和积分状态以最新积分卡为准。",
            )
          : isRequiredTaskDimension
            ? copy(
                language,
                "Task status and points use the latest available results.",
                "任务状态与积分以系统最新结果为准。",
              )
            : copy(
                language,
                "Dimension and component points use the latest available results.",
                "各维度与子项积分均以系统最新结果为准。",
              )}
      </footer>
    </section>
  );
}

function LessonCourseView({
  lessons,
  activeLessonId,
  onSelectLesson,
  language,
  page = 1,
  totalCount = lessons.length,
  loading = false,
  onPageChange,
}) {
  const [activeGroupId, setActiveGroupId] = useState(() => lessons[0]?.groups[0]?.id || "");
  const pageCount = Math.max(1, Math.ceil(totalCount / LESSONS_PER_PAGE));
  const safePage = clampLessonPage(page, pageCount);
  const activeLesson = lessons.find((lesson) => lesson.id === activeLessonId) || lessons[0];
  const pageItems = buildLessonPageItems(safePage, pageCount);

  useEffect(() => {
    if (activeLesson && activeLesson.id !== activeLessonId) {
      onSelectLesson(activeLesson.id);
    }
  }, [activeLesson, activeLessonId, onSelectLesson]);

  useEffect(() => {
    if (!activeLesson?.groups.some((group) => group.id === activeGroupId)) {
      setActiveGroupId(activeLesson?.groups[0]?.id || "");
    }
  }, [activeGroupId, activeLesson]);

  if (!activeLesson) return null;
  const metricCount = activeLesson.groups.reduce((total, group) => total + group.metrics.length, 0);
  const scoredMetricCount = activeLesson.groups.reduce(
    (total, group) => total + group.metrics.filter((metric) => metric.score !== null).length,
    0,
  );
  const lessonScore = Number(activeLesson.lessonTotalScore) || 0;
  const lessonScoreNumber = (value) => new Intl.NumberFormat(
    language === "zh" ? "zh-CN" : "en-US",
    { maximumFractionDigits: 2 },
  ).format(value);
  const selectPage = (nextPage) => {
    const targetPage = clampLessonPage(nextPage, pageCount);
    if (targetPage !== safePage) onPageChange?.(targetPage);
  };

  return (
    <section className="lesson-course-view" aria-label={copy(language, "Single-class data", "单次课程数据") }>
      <div className="lesson-selector-heading">
        <strong>{copy(language, "Choose a class", "选择课程")}</strong>
        <span>
          <ArrowsLeftRight size={15} />
          {copy(
            language,
            `${LESSONS_PER_PAGE} classes per page; swipe on small screens`,
            `每页 ${LESSONS_PER_PAGE} 节，小屏可左右滑动`,
          )}
        </span>
      </div>
      <div className="lesson-selector-shell">
        <div className="lesson-selector" role="tablist" aria-label={copy(language, "Choose a class", "选择课程") }>
          {lessons.map((lesson, index) => {
            const active = lesson.id === activeLesson.id;
            const lessonNumber = (safePage - 1) * LESSONS_PER_PAGE + index + 1;
            return (
              <button
                key={lesson.id}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls={`lesson-report-${lesson.id}`}
                className={active ? "active" : ""}
                onClick={() => onSelectLesson(lesson.id)}
              >
                <span className="lesson-selector-index">{String(lessonNumber).padStart(2, "0")}</span>
                <span className="lesson-selector-copy">
                  <strong>{localizeTitValue(lesson.label, language)}</strong>
                  <small>{localizeTitValue(lesson.date, language)} · {lesson.time}</small>
                </span>
                <span className="lesson-selector-status">{localizeTitValue(lesson.status, language)}</span>
              </button>
            );
          })}
        </div>
      </div>
      <nav
        className="lesson-pagination"
        aria-label={copy(language, "Class list pagination", "课程列表分页")}
      >
        <span>
          {copy(
            language,
            `${totalCount} classes · page ${safePage} of ${pageCount}`,
            `共 ${totalCount} 节 · 第 ${safePage}/${pageCount} 页`,
          )}
        </span>
        <div>
          <button
            type="button"
            disabled={loading || safePage === 1}
            aria-label={copy(language, "Previous page", "上一页")}
            onClick={() => selectPage(safePage - 1)}
          >
            <ArrowLeft size={15} />
          </button>
          {pageItems.map((item) => typeof item === "number" ? (
            <button
              type="button"
              key={item}
              className={item === safePage ? "active" : ""}
              aria-current={item === safePage ? "page" : undefined}
              aria-label={copy(language, `Page ${item}`, `第 ${item} 页`)}
              disabled={loading}
              onClick={() => selectPage(item)}
            >
              {item}
            </button>
          ) : (
            <span aria-hidden="true" key={item}>…</span>
          ))}
          <button
            type="button"
            disabled={loading || safePage === pageCount}
            aria-label={copy(language, "Next page", "下一页")}
            onClick={() => selectPage(safePage + 1)}
          >
            <ArrowRight size={15} />
          </button>
        </div>
      </nav>

      <article className="lesson-report" id={`lesson-report-${activeLesson.id}`} role="tabpanel">
        <header className="lesson-report-heading">
          <span className="lesson-report-icon" aria-hidden="true"><CalendarCheck size={28} weight="duotone" /></span>
          <div className="lesson-report-title">
            <span>{copy(language, "SINGLE-CLASS VIEW", "单次课程数据")}</span>
            <h3>{localizeTitValue(activeLesson.label, language)}</h3>
            <p>{localizeTitValue(activeLesson.date, language)} · {activeLesson.time} · {localizeTitValue(activeLesson.status, language)}</p>
          </div>
          <div className="lesson-report-stats">
            <span>
              <small>{copy(language, "Course records", "本课记录项")}</small>
              <strong>{metricCount} {copy(language, "items", "项")}</strong>
            </span>
            <span>
              <small>{copy(language, "Score items", "本课加分项")}</small>
              <strong>{scoredMetricCount} {copy(language, "items", "项")}</strong>
            </span>
            <span className="score-total">
              <small>{copy(language, "Points earned", "本课获得")}</small>
              <strong>+{lessonScoreNumber(lessonScore)} {copy(language, "pts", "分")}</strong>
            </span>
          </div>
        </header>

        <div className={`data-score-guide lesson-data-score-guide ${activeLesson.scoreStatus === "SOURCE_MISSING" ? "is-warning" : ""}`}>
          <Info size={14} />
          {activeLesson.scoreStatus === "SOURCE_MISSING"
            ? copy(language, "Part of this class evidence is currently unavailable, so no points were inferred.", "本课部分证据暂时缺失，系统未推算积分。")
            : copy(
                language,
                `Course facts and scores use the ${activeLesson.scoreRuleVersion || "current"} result.`,
                `课程事实与积分以${activeLesson.scoreRuleVersion ? ` ${activeLesson.scoreRuleVersion} ` : "当前"}结果为准。`,
              )}
        </div>

        {activeLesson.groups.length > 0 && (
          <>
            <div
              className="lesson-dimension-switch"
              role="tablist"
              aria-label={copy(language, "Choose a course dimension", "选择课程数据维度")}
            >
              {activeLesson.groups.map((group) => {
                const groupScore = group.metrics.reduce(
                  (total, metric) => total + (Number(metric.score) || 0),
                  0,
                );
                const active = group.id === activeGroupId;
                return (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={active}
                    aria-controls={`lesson-dimension-${activeLesson.id}-${group.id}`}
                    className={`${group.id}${active ? " active" : ""}`}
                    key={group.id}
                    onClick={() => setActiveGroupId(group.id)}
                  >
                    <span>{localizeTitValue(group.title, language)}</span>
                    {groupScore > 0
                      ? <strong>+{lessonScoreNumber(groupScore)} {copy(language, "pts", "分")}</strong>
                      : <small>{group.metrics.length} {copy(language, "items", "项")}</small>}
                  </button>
                );
              })}
            </div>
            <div className="lesson-dimension-groups">
              {activeLesson.groups.map((group) => {
                const GroupIcon = dimensionIcons[group.id] || ChartLineUp;
                const groupScore = group.metrics.reduce(
                  (total, metric) => total + (Number(metric.score) || 0),
                  0,
                );
                return (
                  <section
                    className={[
                      "lesson-dimension-group",
                      group.id,
                      group.id === activeGroupId ? "is-mobile-active" : "",
                    ].join(" ")}
                    id={`lesson-dimension-${activeLesson.id}-${group.id}`}
                    role="tabpanel"
                    key={group.id}
                  >
                    <header>
                      <span><GroupIcon size={21} weight="duotone" /></span>
                      <div>
                        <h4>{localizeTitValue(group.title, language)}</h4>
                        <small>
                          {group.metrics.length} {copy(language, "records", "项记录")}
                          {" · "}
                          {group.metrics.filter((metric) => metric.score !== null).length} {copy(language, "scored", "项加分")}
                        </small>
                      </div>
                      {groupScore > 0 && (
                        <strong>+{lessonScoreNumber(groupScore)} {copy(language, "pts", "分")}</strong>
                      )}
                    </header>
                    <dl className="lesson-metric-list">
                      {group.metrics.map((metric) => (
                        <div
                          className={[
                            metric.score === null ? "fact-only" : "has-score",
                            metric.tone || "neutral",
                          ].join(" ")}
                          key={metric.sourceKey}
                        >
                          <dt>{localizeTitValue(metric.label, language)}</dt>
                          <dd className={`lesson-metric-data ${metric.tone || "neutral"}`}>
                            <small>{copy(language, "Course fact", "事实值")}</small>
                            <strong>{localizeTitValue(metric.value, language)}</strong>
                          </dd>
                          {metric.score !== null && (
                            <dd className="lesson-metric-score" aria-label={copy(language, "Score added", "获得积分")}>
                              <small>{copy(language, "Points", "本课加分")}</small>
                              <strong>+{metric.score} {copy(language, "pts", "分")}</strong>
                            </dd>
                          )}
                        </div>
                      ))}
                    </dl>
                  </section>
                );
              })}
            </div>
          </>
        )}
        {activeLesson.groups.length === 0 && (
          <div className="lesson-facts-empty">
            {copy(
              language,
              "This class status has no course-dimension facts to display.",
              "该课程状态下暂无可展示的课程维度数据。",
            )}
          </div>
        )}

        <footer className="lesson-report-note">
          <Info size={17} />
          <span>{copy(language, "Course facts outside the scoring rules are shown without points. Cumulative totals use the current scorecard.", "不在积分规则中的课程事实不标分数；累计分以当前积分卡为准。")}</span>
        </footer>
      </article>
    </section>
  );
}

function ScoreDetailDialog({ open, onClose, language, score }) {
  const publicRuleGroups = useMemo(
    () => presentScorecardRules(score.rules, language),
    [language, score.rules],
  );
  const scoreOnlyStageStates = scoreStageStates(
    score.current,
    score.graduationMilestone,
    score.goldMilestone,
  );
  const stageStates = {
    graduation: {
      ...scoreOnlyStageStates.graduation,
      reached: score.graduationQualified === true,
    },
    gold: {
      ...scoreOnlyStageStates.gold,
      reached: score.goldQualified === true,
    },
  };
  const availableTaskByCode = new Map(
    score.availableItems.map((item) => [item.taskCode, item]),
  );
  const remainingRequiredTasks = availableTaskByCode.size;
  const stageStatus = (stage) => presentStageAction(
    stage,
    remainingRequiredTasks,
    language,
  );
  const graduationStatus = stageStatus(stageStates.graduation);
  const goldStatus = stageStatus(stageStates.gold);
  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="score-detail-backdrop" onMouseDown={onClose}>
      <section
        className="score-detail-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="score-detail-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <button className="close-dialog" type="button" onClick={onClose} aria-label={copy(language, "Close", "关闭")}>
          <X size={20} />
        </button>
        <span className="score-dialog-kicker">{copy(language, "SCORE DETAILS", "积分明细")}</span>
        <h2 id="score-detail-title">{copy(language, "Growth score details", "成长积分明细")}</h2>
        <p className="score-dialog-intro">
          {copy(
            language,
            "Current points use the latest scorecard. Available points are the sum of unfinished required tasks and disappear after all required tasks are complete.",
            "已获得积分以最新积分卡为准；可获得积分为未完成必修任务分值之和，全部完成后不再显示。",
          )}
        </p>
        <div
          className="score-dialog-summary"
          data-onboarding-target="score-detail-summary"
          data-onboarding-scroll-block="start"
        >
          <span><small>{copy(language, "Current", "已获得积分")}</small><strong>{score.current ?? "—"}</strong></span>
          {score.available !== null && (
            <span><small>{copy(language, "Available", "可获得积分")}</small><strong>{score.available}</strong></span>
          )}
        </div>
        <section
          className="score-stage-section"
          aria-labelledby="score-stage-title"
          data-onboarding-target="score-detail-milestones"
          data-onboarding-max-height="360"
          data-onboarding-scroll-block="start"
        >
          <header>
            <div>
              <span><Medal size={18} weight="duotone" /></span>
              <div>
                <h3 id="score-stage-title">{copy(language, "Milestones and encouragement", "成长阶段与激励")}</h3>
                <p>{copy(language, "See what each growth stage means and the incentive you receive after reaching it.", "了解每个成长阶段的要求，以及达成后可以获得的激励。")}</p>
              </div>
            </div>
            <small>{copy(language, "Stage incentives", "阶段激励")}</small>
          </header>
          <div className="score-stage-grid">
            <article
              className={[
                "score-stage-card",
                "graduation",
                stageStates.graduation.reached ? "achieved" : "",
                stageStates.graduation.current ? "current" : "",
              ].join(" ")}
            >
              <div className="score-stage-card-heading">
                <span><GraduationCap size={24} weight="duotone" /></span>
                <div>
                  <small>{copy(language, "MILESTONE 1", "阶段一")}</small>
                  <h4>{copy(language, "Graduation stage", "出营阶段")}</h4>
                </div>
                <strong>{score.graduationMilestone} {copy(language, "pts", "分")}</strong>
              </div>
              <p>{copy(language, "Work toward 100 points and all 9 required tasks; the final graduation status uses the system result.", "出营阶段关注达到 100 分并完成 9 项必修任务；最终状态以系统返回结果为准。")}</p>
              {graduationStatus && (
                <div className="score-stage-progress-copy">
                  <span>{graduationStatus}</span>
                  {stageStates.graduation.current && <em>{copy(language, "Current goal", "当前目标")}</em>}
                </div>
              )}
              <aside>
                <Sparkle size={16} weight="fill" />
                <span><b>{copy(language, "Incentive:", "激励说明：")}</b>{copy(language, " After graduation, your Rank increases by 1 level.", "成功出营后，你的 Rank 将提升 1 级。")}</span>
              </aside>
            </article>
            <article
              className={[
                "score-stage-card",
                "gold",
                stageStates.gold.reached ? "achieved" : "",
                stageStates.gold.current ? "current" : "",
              ].join(" ")}
            >
              <div className="score-stage-card-heading">
                <span><Medal size={24} weight={stageStates.gold.reached ? "fill" : "duotone"} /></span>
                <div>
                  <small>{copy(language, "MILESTONE 2", "阶段二")}</small>
                  <h4>{copy(language, "Gold Teacher stage", "金牌教师阶段")}</h4>
                </div>
                <strong>{score.goldMilestone} {copy(language, "pts", "分")}</strong>
              </div>
              <p>{copy(language, "Reaching 200 points advances you to the Gold Teacher assessment; the final status uses the system result.", "达到 200 分后进入金牌教师评定；最终状态以系统返回结果为准。")}</p>
              {goldStatus && (
                <div className="score-stage-progress-copy">
                  <span>{goldStatus}</span>
                  {stageStates.gold.current && <em>{stageStates.gold.reached ? copy(language, "Current stage", "当前阶段") : copy(language, "Next goal", "下一目标")}</em>}
                </div>
              )}
              <aside>
                <Sparkle size={16} weight="fill" />
                <span><b>{copy(language, "Incentive:", "激励说明：")}</b>{copy(language, " After becoming a Gold Teacher, your Rank increases by another level.", "成为金牌教师后，你的 Rank 将再提升 1 级。")}</span>
              </aside>
            </article>
          </div>
        </section>
        <section className="score-rules-section" aria-labelledby="score-rules-title">
          <header>
            <span><ChartLineUp size={20} weight="duotone" /></span>
            <div>
              <h3 id="score-rules-title">{copy(language, "Point rules", "积分规则说明")}</h3>
              <p>{copy(language, "The positive rules below reflect the current scorecard.", "以下正向加分规则以当前积分卡为准。")}</p>
            </div>
            <small className="score-rules-version">
              {copy(language, "Version", "版本")}：{score.resultVersion}
            </small>
          </header>
          {publicRuleGroups.length > 0 ? (
            <div className="score-rule-grid">
              {publicRuleGroups.map((group) => {
                const RuleIcon = scoreRuleGroupIcons[group.code] || ChartLineUp;
                const isRequiredTaskGroup = group.code === "NEW_TEACHER_TASK";
                const groupClass = isRequiredTaskGroup
                  ? "required_tasks"
                  : group.code.toLowerCase();
                const requiredTaskCurrentScore = Number(group.currentScore) || 0;
                const requiredTaskAvailableScore = score.availableItems.reduce(
                  (total, item) => total + (Number(item.score) || 0),
                  0,
                );
                const requiredTaskTotalScore = requiredTaskCurrentScore
                  + requiredTaskAvailableScore;
                const formatScore = (value) => new Intl.NumberFormat(
                  language === "zh" ? "zh-CN" : "en-US",
                  { maximumFractionDigits: 2 },
                ).format(value);
                return (
                  <article className={`score-rule-group ${groupClass}`} key={group.code}>
                    <header>
                      <span><RuleIcon size={19} weight="duotone" /></span>
                      <div>
                        <h4>{isRequiredTaskGroup ? copy(language, "Required-task points", "必修任务积分") : group.title}</h4>
                        <p>{isRequiredTaskGroup ? copy(language, "See earned and remaining points in one place.", "统一查看每项任务的积分与完成状态。") : group.description}</p>
                      </div>
                      {Number.isFinite(group.currentScore) && (
                        <b className="score-rule-current">
                          {isRequiredTaskGroup
                            ? copy(
                                language,
                                `Earned ${formatScore(requiredTaskCurrentScore)} / ${formatScore(requiredTaskTotalScore)} pts`,
                                `已获得 ${formatScore(requiredTaskCurrentScore)} / ${formatScore(requiredTaskTotalScore)} 分`,
                              )
                            : `${copy(language, "Current", "当前")} ${group.currentScore} ${copy(language, "pts", "分")}`}
                        </b>
                      )}
                    </header>
                    <ul>
                      {group.items.map((item) => {
                        const availableTask = availableTaskByCode.get(item.code);
                        const taskName = availableTask?.title
                          || fixedTaskCatalog.find((task) => task.taskCode === item.code)?.name;
                        return (
                          <li
                            className={isRequiredTaskGroup ? (availableTask ? "is-pending" : "is-earned") : ""}
                            key={item.code}
                          >
                            <div>
                              <strong>{isRequiredTaskGroup && taskName ? `${item.code} · ${taskName}` : item.title}</strong>
                              <small>{isRequiredTaskGroup
                                ? availableTask
                                  ? copy(language, "To do · points available", "待完成 · 完成后可得分")
                                  : copy(language, "Points earned", "已完成 · 积分已获得")
                                : item.condition}</small>
                            </div>
                            <em>{item.pointsLabel}</em>
                          </li>
                        );
                      })}
                    </ul>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="score-rules-empty">
              {copy(language, "The teacher-visible rule summary is being updated.", "教师可见的积分规则正在更新中。")}
            </div>
          )}
          <footer>
            <strong>{copy(language, "The total uses the current scorecard; this page does not recalculate points.", "总分以当前积分卡为准，本页面不重新计算积分。")}</strong>
            <span>{copy(language, "After a rule update recalculates the scorecard, this summary updates automatically.", "积分规则更新并完成重算后，本说明会自动同步更新。")}</span>
          </footer>
        </section>
      </section>
    </div>
  );
}

function TokiGrowthTipCard({ tip, language, onOpenTask }) {
  const motion = {
    recovery: "encourage",
    waiting: "scan",
    complete: "allDone",
  }[tip.action.tone] || "welcome";
  const previewLoop = import.meta.env.DEV
    && new URLSearchParams(window.location.search).get("tokiPreview") === "allDone";

  return (
    <aside
      className={`tit-next-step-card tit-growth-tip-card is-${tip.action.tone}`}
      aria-labelledby="toki-growth-tip-title"
      data-onboarding-target="my-tide-recommendation"
      data-onboarding-max-height="330"
      data-onboarding-scroll-block="center"
    >
      <div className="tit-growth-tip-heading">
        <span className="tit-next-step-kicker">
          <Sparkle size={23} weight="fill" />
          {copy(language, "Toki growth tip", "Toki 成长建议")}
        </span>
        <span className="tit-growth-stage-chip">{tip.stageLabel}</span>
      </div>
      <h2 id="toki-growth-tip-title">{tip.title}</h2>
      <p>{tip.body}</p>
      <div className="toki-growth-action">
        <small>{tip.action.label}</small>
        <strong>{tip.action.title}</strong>
        <span>{tip.action.prompt}</span>
        {tip.action.due && (
          <time className="toki-growth-due" dateTime={tip.action.dueAt}>
            <CalendarBlank size={13} weight="bold" />
            {tip.action.due}
          </time>
        )}
      </div>
      {tip.action.task && tip.action.buttonLabel && (
        <button type="button" onClick={() => onOpenTask(tip.action.task)}>
          {tip.action.buttonLabel}
          <ArrowRight size={17} weight="bold" />
        </button>
      )}
      <Toki mood={tip.mood} motion={motion} loop={motion === "scan" || previewLoop} />
    </aside>
  );
}

function MyTitPage({
  tasks,
  tideSummary,
  courses,
  attributionCourses,
  coursePage,
  courseTotalCount,
  coursesLoading,
  onCoursePageChange,
  sourceErrors,
  language,
  unreadCount,
  onMessagesOpen,
  onScoreDetailsOpen,
  scoreGuideRequestKey = 0,
}) {
  const teacher = useTeacher();
  const [activeDimension, setActiveDimension] = useState("");
  const [growthView, setGrowthView] = useState("dimensions");
  const [activeLessonId, setActiveLessonId] = useState(courses[0]?.lessonId || "");
  const [scoreDetailsOpen, setScoreDetailsOpen] = useState(false);
  const handledScoreGuideRequestRef = useRef(scoreGuideRequestKey);
  const scoreDetailsGuideNotifiedRef = useRef(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (scoreGuideRequestKey === handledScoreGuideRequestRef.current) return;
    handledScoreGuideRequestRef.current = scoreGuideRequestKey;
    setScoreDetailsOpen(true);
  }, [scoreGuideRequestKey]);

  useEffect(() => {
    if (!scoreDetailsOpen) {
      scoreDetailsGuideNotifiedRef.current = false;
      return;
    }
    if (scoreDetailsGuideNotifiedRef.current) return;
    scoreDetailsGuideNotifiedRef.current = true;
    onScoreDetailsOpen?.();
  }, [onScoreDetailsOpen, scoreDetailsOpen]);
  const stage = localizeStage(
    stageDescriptions[stageIndexForAvailableTasks(tasks, teacher.day)],
    language,
  );
  const tags = buildCourseAbilityTags(tasks, language);
  const score = teacher.growthScore;
  const currentScore = score.current === null || score.current === undefined || score.current === ""
    ? null
    : Number.isFinite(Number(score.current))
      ? Number(score.current)
      : null;
  const currentScoreTarget = currentScore === null || currentScore < score.graduationMilestone
    ? score.graduationMilestone
    : score.goldMilestone;
  const allScoreMilestonesReached = score.goldQualified === true;
  const nextMilestoneGap = currentScore === null
    ? null
    : Math.max(currentScoreTarget - currentScore, 0).toFixed(1);
  const scoreProgress = currentScore === null ? 0 : Math.min((currentScore / score.total) * 100, 100);
  const growthTipTasks = tasks.map((task) => localizeTask(task, language));
  const previewAllDone = import.meta.env.DEV
    && new URLSearchParams(window.location.search).get("tokiPreview") === "allDone";
  const growthTip = buildGrowthTip({
    teacherName: teacher.name,
    campDay: teacher.day,
    tasks: previewAllDone
      ? growthTipTasks.map((task) => ({ ...task, status: "completed" }))
      : growthTipTasks,
    language,
  });
  const unavailableGrowthTip = buildGrowthTip({
    teacherName: teacher.name,
    campDay: teacher.day,
    tasks: growthTipTasks,
    language,
    sourceUnavailable: true,
  });
  const runtimeDimensions = useMemo(() => {
    const dimensionCodes = {
      userFeedback: "USER_FEEDBACK",
      reliability: "RELIABILITY",
      classQuality: "CLASS_QUALITY",
      capacity: "CAPACITY",
      newTeacherTask: "NEW_TEACHER_TASK",
    };
    return dimensionCatalog.map((dimension) => {
      const detail = (tideSummary?.dimensions || []).find(
        (item) => item.code === dimensionCodes[dimension.sourceKey],
      );
      const rawScore = detail?.score;
      const nextScore = rawScore === null || rawScore === undefined || rawScore === ""
        ? null
        : Number(rawScore);
      const scoreAvailable = Number.isFinite(nextScore);
      const updated = detail?.calculatedAt || tideSummary?.calculatedAt;
      const isRequiredTasks = dimension.sourceKey === "newTeacherTask";
      const scorecardSources = (detail?.components || []).map((component) => ({
        sourceKey: component.code,
        value: component.unitCount,
        unit: component.sourceScope === "TASK"
          ? "TASKS"
          : component.sourceScope === "TEACHER"
            ? "SLOTS"
            : "CLASSES",
        score: component.score,
        pointsPerUnit: component.pointsPerUnit,
        taskCode: component.sourceScope === "TASK" ? component.code : undefined,
        target: component.code === "CAPACITY_PEAK_SLOT_40" ? 40 : undefined,
      }));
      const sources = ["userFeedback", "reliability", "classQuality"].includes(
        dimension.sourceKey,
      )
        ? mergeScorecardCourseSources(
            attributionCourses,
            dimension.sourceKey,
            scorecardSources,
          )
        : scorecardSources;
      const visibleRequiredTasks = tasks.filter(
        (task) => task.taskCategory !== "personalized" && task.taskCode !== "G00",
      );
      const progress = isRequiredTasks
        ? {
            completed: visibleRequiredTasks.filter(
              (task) => task.status === "completed",
            ).length,
            total: fixedTaskCatalog.length,
            maximumScore: sources.reduce(
              (total, source) => total + (Number(source.pointsPerUnit) || 0),
              0,
            ),
          }
        : null;
      const scoringLessonCount = new Set(
        sources.map((source) => source.lessonId).filter(Boolean),
      ).size;
      const reportedLessonCount = Math.max(
        0,
        ...scorecardSources
          .filter(
            (source) =>
              source.unit === "CLASSES" &&
              Number(source.score) > 0 &&
              Number(source.value) > 0,
          )
          .map((source) => Number(source.value)),
      );
      const displayLessonCount = scoringLessonCount || reportedLessonCount;
      const noCourseSourcesHeadline = {
        en: "No positive score breakdown has been produced yet",
        zh: "暂未产生可展示的正向积分明细",
      };
      const capacitySource = sources.find(
        (source) => source.sourceKey === "CAPACITY_PEAK_SLOT_40",
      );
      const capacityValue = Number(capacitySource?.value);
      const capacityTarget = Number(capacitySource?.target);
      const headline = !scoreAvailable
        ? { en: "Waiting for the source result", zh: "等待来源数据返回" }
        : isRequiredTasks
          ? {
              en: `${progress.completed}/${progress.total} required tasks completed`,
              zh: `已完成 ${progress.completed}/${progress.total} 项必修任务`,
            }
          : dimension.sourceKey === "userFeedback"
            ? displayLessonCount > 0
              ? {
                  en: `Feedback points from ${displayLessonCount} ${displayLessonCount === 1 ? "class" : "classes"}`,
                  zh: `来自 ${displayLessonCount} 节课的用户反馈加分`,
                }
              : noCourseSourcesHeadline
            : dimension.sourceKey === "reliability"
              ? displayLessonCount > 0
                ? {
                    en: `${displayLessonCount} ${displayLessonCount === 1 ? "class" : "classes"} generated reliability points`,
                    zh: `${displayLessonCount} 节课产生稳定履约加分`,
                  }
                : noCourseSourcesHeadline
              : dimension.sourceKey === "classQuality"
                ? displayLessonCount > 0
                  ? {
                      en: `${displayLessonCount} ${displayLessonCount === 1 ? "class" : "classes"} generated hardware-quality points`,
                      zh: `${displayLessonCount} 节课产生硬件质量加分`,
                    }
                  : noCourseSourcesHeadline
                : dimension.sourceKey === "capacity" &&
              Number.isFinite(capacityValue) &&
              Number.isFinite(capacityTarget)
                  ? capacityValue >= capacityTarget
                    ? {
                        en: "Peak-time bookable-slot target reached",
                        zh: "已达到高峰时段可约课时目标",
                      }
                    : {
                        en: `Peak-time bookable slots ${capacityValue}/${capacityTarget}`,
                        zh: `高峰时段可约课时数 ${capacityValue}/${capacityTarget}`,
                      }
                  : {
                      en: "Teaching availability result returned",
                      zh: "有效供给结果已返回",
                    };
      return {
        ...dimension,
        score: scoreAvailable ? nextScore : null,
        status: scoreAvailable
          ? { en: "Latest returned result", zh: "最新返回结果" }
          : { en: "Data unavailable", zh: "数据暂不可用" },
        statusTone: scoreAvailable ? "positive" : "observing",
        headline,
        visual: scoreAvailable && (progress?.maximumScore || dimension.cap)
          ? {
              type: "progress",
              value: Math.min(
                Math.round(
                  (nextScore / (progress?.maximumScore || dimension.cap)) * 100,
                ),
                100,
              ),
            }
          : null,
        guidance: {
          en: "This dimension uses the current scorecard.",
          zh: "本维度以当前积分卡为准。",
        },
        updated: updated ? { en: new Date(updated).toLocaleString("en-US"), zh: new Date(updated).toLocaleString("zh-CN") } : { en: "Updating", zh: "更新中" },
        sources,
        progress,
        liveSummaryOnly: true,
      };
    });
  }, [attributionCourses, tasks, tideSummary]);
  const runtimeLessons = useMemo(() => {
    return courses.map((course, index) => {
      const date = new Date(
        course.scheduledStartAt ||
          `${course.lessonLocalDate || ""}T${course.lessonLocalTime || "00:00:00"}`,
      );
      const visibleIndicators = visibleCourseIndicators(course);
      const groups = courseScoreDimensionGroups
        .map((group) => ({
          id: group.id,
          title: group.title,
          metrics: visibleIndicators
            .filter((source) => source.dimension === group.apiDimension)
            .map((source) => ({
              sourceKey: source.sourceKey,
              label: courseScoreSourceLabels[source.sourceKey] || {
                en: source.sourceKey,
                zh: source.sourceKey,
              },
              value: source.value,
              tone: source.tone,
              score: source.score === null ? null : Number(source.score),
            })),
        }))
        .filter((group) => group.metrics.length > 0);
      return {
        id: course.lessonId,
        label: {
          en: `Class ${course.lessonSequence || index + 1}`,
          zh: `第 ${course.lessonSequence || index + 1} 节课`,
        },
        date: Number.isNaN(date.getTime())
          ? { en: course.lessonLocalDate || "—", zh: course.lessonLocalDate || "—" }
          : { en: date.toLocaleDateString("en-US"), zh: date.toLocaleDateString("zh-CN") },
        time: Number.isNaN(date.getTime())
          ? course.lessonLocalTime || "—"
          : date.toLocaleTimeString(language === "zh" ? "zh-CN" : "en-US", { hour: "2-digit", minute: "2-digit" }),
        status: lessonLifecycleStatusLabel(course.lifecycleStatus),
        scoreStatus: (course.dimensions || []).some(
          (dimension) => dimension.evidenceStatus === "SOURCE_MISSING",
        )
          ? "SOURCE_MISSING"
          : course.evidenceStatus,
        scoreRuleVersion: course.scoreRuleVersion,
        lessonTotalScore: course.lessonTotalScore,
        groups,
      };
    });
  }, [courses, language]);
  if (sourceErrors.profile || sourceErrors.summary) {
    return (
      <main className="ref-page tit-screen tit-merged-screen">
        <div className="tit-heading">
          <div>
            <h1>{copy(language, "My TIDE", "我的成长")}</h1>
            <p>{copy(language, "Your real growth data will appear here after the source reconnects.", "数据源恢复后，这里会继续展示你的真实成长数据。")}</p>
          </div>
        </div>
        <section className="tit-source-state-layout">
          <SourceUnavailableCard
            language={language}
            title={copy(language, "Growth data is temporarily unavailable", "成长数据暂时无法加载")}
            message={copy(language, "The missing result was not replaced with zero or local data. Please try again shortly.", "当前没有用 0 分或本地数据代替缺失结果，请稍后重新加载。")}
          />
          <TokiGrowthTipCard
            tip={unavailableGrowthTip}
            language={language}
            onOpenTask={(task) => navigate(`/task/${task.id}`)}
          />
        </section>
        <MobileNav language={language} unreadCount={unreadCount} onMessagesOpen={onMessagesOpen} />
      </main>
    );
  }
  return (
    <main className="ref-page tit-screen tit-merged-screen">
      <div className="tit-heading">
        <div>
          <h1>{copy(language, "My TIDE", "我的成长")}</h1>
          <p>{copy(language, "Understand your score, its sources and what to improve next", "了解积分怎么来的，看看下一步做什么")}</p>
        </div>
      </div>

      <section className="tit-hero-layout">
        <section
          className={`tit-overview-card ${allScoreMilestonesReached ? "is-gold-stage" : ""}`}
          data-onboarding-target="my-tide-overview"
          data-onboarding-max-height="410"
          data-onboarding-scroll-block="start"
        >
          <div className="tit-welcome-block">
            <h2>{copy(language, "Welcome back", "欢迎回来")}, {teacher.name}</h2>
          </div>
          <div className="tit-day-block">
            <span className="metric-icon"><CalendarBlank size={29} weight="fill" /></span>
            <div>
              <strong>{copy(language, "Day", "第")} <em>{teacher.day}</em>/{teacher.totalDays} {copy(language, "days", "天")}</strong>
              <p className="tit-stage-label">
                <span>{copy(language, "Current stage", "当前阶段")}：{stage.title}</span>
                <span>{copy(language, "Camp status", "出营状态")}：{teacher.graduationStatus}</span>
              </p>
            </div>
          </div>
          <button
            className="tit-total-score"
            type="button"
            onClick={() => setScoreDetailsOpen(true)}
            aria-haspopup="dialog"
          >
            <span className="score-block-title">
              <span>{copy(language, "Total growth score", "我的成长积分")}</span>
              <Info size={17} />
            </span>
            <div className="score-current-block">
              <strong>{currentScore ?? "—"} <small>/ {currentScoreTarget}</small></strong>
              {allScoreMilestonesReached && (
                <span className="tit-gold-stage-badge">
                  <Medal size={18} weight="fill" />
                  {copy(language, "Gold Teacher stage", "金牌教师阶段")}
                </span>
              )}
            </div>
          </button>
          <div className="tit-score-track" aria-label={currentScore === null
            ? copy(language, "Growth score is unavailable", "成长积分暂不可用")
            : copy(language, `${currentScore} of ${score.total} points`, `当前 ${currentScore} 分，共 ${score.total} 分`)}>
            <div className="tit-score-track-line"><span style={{ width: `${scoreProgress}%` }} /></div>
            {currentScore !== null && !allScoreMilestonesReached && <span className="tit-score-marker current" style={{ left: `${scoreProgress}%` }}><strong>{currentScore}</strong></span>}
            <span className={`tit-score-marker graduation ${score.graduationQualified ? "is-achieved" : ""}`} style={{ left: `${(score.graduationMilestone / score.total) * 100}%` }}><i /><b>{score.graduationMilestone}</b><small><span>{copy(language, "Graduation", "出营")}</span><span>{copy(language, "9 required tasks", "9 项必修任务")}</span></small></span>
            <span className={`tit-score-marker gold ${allScoreMilestonesReached ? "is-current-stage" : ""}`} style={{ left: `${(score.goldMilestone / score.total) * 100}%` }}><i /><b>{score.goldMilestone}</b><small><span>{copy(language, "Gold stage", "金牌阶段")}</span><span>{allScoreMilestonesReached ? copy(language, "Achieved", "已达成") : copy(language, "Excellent", "优秀")}</span></small></span>
            <span className="tit-score-zero">0</span>
          </div>
          <div className="tit-score-footer">
            <div className="score-points-breakdown">
              {score.available !== null && (
                <span className="score-summary-item is-available"><Star size={21} weight="fill" /><small>{copy(language, "Available", "可获得积分")}</small><strong>{score.available}</strong></span>
              )}
              <span className="score-summary-item is-milestone">
                <Flag size={21} weight="fill" />
                <small>{copy(language, "Next milestone", "下一个目标")}</small>
                <strong>
                  {currentScore === null
                    ? copy(language, "Updating", "更新中")
                    : allScoreMilestonesReached
                      ? copy(language, "Achieved", "已达成")
                      : copy(language, `${nextMilestoneGap} pts to go`, `还差 ${nextMilestoneGap} 分`)}
                </strong>
              </span>
            </div>
            <span className="tit-data-updated"><CalendarBlank size={17} />{copy(language, `Data through ${score.updatedAt}`, `更新于 ${score.updatedAt}`)}</span>
          </div>
        </section>

        <TokiGrowthTipCard
          tip={growthTip}
          language={language}
          onOpenTask={(task) => navigate(`/task/${task.id}`)}
        />
      </section>

      <section
        className="dimension-board-shell"
        data-onboarding-target="my-tide-dimensions"
        data-onboarding-max-height="360"
        data-onboarding-scroll-block="start"
      >
        <div className="dimension-section-heading">
          <div>
            <h2>{copy(language, "My five growth dimensions", "我的成长表现")}</h2>
            <p>{growthView === "dimensions"
            ? copy(language, "See each dimension total and its available score sources", "查看各维度总分和可用的加分来源")
              : copy(language, "See every safe course fact and the indicators that actually earned points", "查看每节课的事实数据及实际加分")}</p>
          </div>
          <div className="dimension-view-controls">
            <span className="growth-view-hint">{copy(language, "Switch view to see different details", "切换视图查看不同明细")}</span>
            <div className="growth-view-switch" role="tablist" aria-label={copy(language, "Growth-performance view", "成长表现查看方式") }>
              <button
                type="button"
                role="tab"
                aria-selected={growthView === "dimensions"}
                className={growthView === "dimensions" ? "active" : ""}
                onClick={() => setGrowthView("dimensions")}
              >
                <ChartLineUp size={17} weight="duotone" />
                {copy(language, "By dimension", "维度总览")}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={growthView === "lessons"}
                className={growthView === "lessons" ? "active" : ""}
                onClick={() => setGrowthView("lessons")}
              >
                <CalendarCheck size={17} weight="duotone" />
                {copy(language, "By class", "单次课程")}
              </button>
            </div>
          </div>
        </div>
        {growthView === "dimensions" ? (
          <>
            <div className="dimension-score-scope-note">
              <Info size={16} />
              <span>
                {copy(
                  language,
                  "User feedback, reliability and hardware quality are uncapped cumulative scores. The total growth score above is displayed up to 200 points.",
                  "用户反馈、上课稳定度和硬件质量为无上限累计分；上方成长总分最高展示 200 分。",
                )}
              </span>
            </div>
            <section className="dimension-grid-new" aria-label={copy(language, "Five growth dimensions", "五维成长指标")}>
              {runtimeDimensions.map((dimension, index) => (
                <Fragment key={dimension.id}>
                  <DimensionCard
                    dimension={dimension}
                    active={activeDimension === dimension.id}
                    onToggle={() => setActiveDimension((current) => current === dimension.id ? "" : dimension.id)}
                    language={language}
                    column={index + 1}
                  />
                  {activeDimension === dimension.id && (
                    <DimensionDetails
                      dimension={dimension}
                      language={language}
                      attributionUnavailable={Boolean(sourceErrors.courseAttributions)}
                    />
                  )}
                </Fragment>
              ))}
            </section>
            <div className="dimension-pagination" aria-hidden="true"><span className="active" /><span /></div>
          </>
        ) : sourceErrors.courses ? (
          <SourceUnavailableCard
            compact
            language={language}
            title={copy(language, "Class details are temporarily unavailable", "单次课程数据暂时无法加载")}
            message={copy(language, "The cumulative score above is unchanged. Reload later to view class-level details.", "上方累计积分不受影响，请稍后重新加载查看逐课明细。")}
          />
        ) : coursesLoading && courses.length === 0 ? (
          <div className="faq-loading-state" role="status">
            {copy(language, "Loading class details…", "正在加载课程明细…")}
          </div>
        ) : (
          <LessonCourseView
            lessons={runtimeLessons}
            activeLessonId={activeLessonId}
            onSelectLesson={setActiveLessonId}
            language={language}
            page={coursePage}
            totalCount={courseTotalCount}
            loading={coursesLoading}
            onPageChange={onCoursePageChange}
          />
        )}
      </section>

      <section className="tit-support-row">
        <article className="tags-card">
          <h2>{copy(language, "Course ability tags", "课程能力标签")}</h2>
          <div>
            {tags.map((tag) => (
              <span className={tag.state} key={tag.taskId}>
                <Medal size={28} weight={tag.state === "earned" ? "fill" : "duotone"} aria-hidden="true" />
                {tag.label} · {
                  tag.state === "earned"
                    ? copy(language, "Earned", "已获得")
                    : tag.state === "pending"
                      ? copy(language, "To do", "待完成")
                      : copy(language, "Syncing", "状态同步中")
                }
              </span>
            ))}
          </div>
        </article>
      </section>
      <ScoreDetailDialog open={scoreDetailsOpen} onClose={() => setScoreDetailsOpen(false)} language={language} score={score} />
      <MobileNav language={language} unreadCount={unreadCount} onMessagesOpen={onMessagesOpen} />
    </main>
  );
}

function PasswordResetDialog({ open, onClose, language, email }) {
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open, onClose]);

  useEffect(() => {
    if (open) return;
    setError("");
    setSent(false);
  }, [open]);

  if (!open) return null;

  const submit = async (event) => {
    event.preventDefault();
    setSending(true);
    setError("");
    try {
      await requestPasswordReset(email);
      setSent(true);
    } catch (caught) {
      setError(localizeApiError(
        caught,
        language,
        copy(language, "Unable to send the reset link.", "重置链接发送失败，请稍后重试。"),
      ));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="help-backdrop" onMouseDown={onClose}>
      <section className="ref-help-dialog account-security-dialog" role="dialog" aria-modal="true" aria-labelledby="security-dialog-title" onMouseDown={(event) => event.stopPropagation()}>
        <button className="close-dialog" type="button" onClick={onClose} aria-label={copy(language, "Close", "关闭")}><X size={20} /></button>
        <span className="metric-icon"><ShieldCheck size={27} weight="duotone" /></span>
        <h2 id="security-dialog-title">{copy(language, "Change password", "修改密码")}</h2>
        <p>{copy(language, `We will send a reset link to ${email}.`, `我们会向 ${email} 发送密码重置链接。`)}</p>
        {sent ? (
          <div className="account-security-success" role="status">
            <CheckCircle size={34} weight="fill" />
            <strong>{copy(language, "Reset link sent", "重置链接已发送")}</strong>
            <span>{copy(language, "Open the link in your email to set a new password.", "请打开邮件中的链接设置新密码。")}</span>
            <button className="auth-submit-button" type="button" onClick={onClose}>{copy(language, "Done", "完成")}</button>
          </div>
        ) : (
          <form className="account-security-form" onSubmit={submit}>
            {error && <div className="auth-form-error" role="alert">{error}</div>}
            <button className="auth-submit-button" type="submit" disabled={sending}>{sending ? copy(language, "Sending…", "发送中…") : copy(language, "Send reset link", "发送重置链接")}<ArrowRight size={18} weight="bold" /></button>
          </form>
        )}
      </section>
    </div>
  );
}

function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const [helpOpen, setHelpOpen] = useState(false);
  const [helpLoaded, setHelpLoaded] = useState(false);
  const [helpEntrySource, setHelpEntrySource] = useState("UNKNOWN");
  const [passwordResetOpen, setPasswordResetOpen] = useState(false);
  const [language, setLanguage] = useState(
    () => localStorage.getItem("new-teacher-camp-language") || "en",
  );
  const [authenticated, setAuthenticated] = useState(false);
  const [authReady, setAuthReady] = useState(false);
  const [tasks, setTasks] = useState([]);
  const [messages, setMessages] = useState([]);
  const [messageTotalCount, setMessageTotalCount] = useState(0);
  const [messageUnreadCount, setMessageUnreadCount] = useState(0);
  const [messageNextCursor, setMessageNextCursor] = useState(null);
  const [messageFilter, setMessageFilter] = useState("ALL");
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesLoaded, setMessagesLoaded] = useState(false);
  const [supportTickets, setSupportTickets] = useState([]);
  const [supportTicketsLoading, setSupportTicketsLoading] = useState(false);
  const [supportTicketsLoaded, setSupportTicketsLoaded] = useState(false);
  const [profile, setProfile] = useState(null);
  const [tideSummary, setTideSummary] = useState(null);
  const [scoreSyncStatus, setScoreSyncStatus] = useState("idle");
  const [g01Review, setG01Review] = useState(null);
  const [courses, setCourses] = useState([]);
  const [attributionCourses, setAttributionCourses] = useState([]);
  const [coursePage, setCoursePage] = useState(1);
  const [courseTotalCount, setCourseTotalCount] = useState(0);
  const [coursesLoading, setCoursesLoading] = useState(false);
  const [dataReady, setDataReady] = useState(false);
  const [dataError, setDataError] = useState(null);
  const [dataReloadKey, setDataReloadKey] = useState(0);
  const [sourceErrors, setSourceErrors] = useState({});
  const [onboardingStatus, setOnboardingStatus] = useState(() => ({
    required: false,
    guideCode: ONBOARDING_GUIDE_CODE,
    guideVersion: ONBOARDING_DEFAULT_VERSION,
  }));
  const [onboardingCatalog, setOnboardingCatalog] = useState(
    () => normalizeOnboardingCatalog(null),
  );
  const [onboardingCatalogReady, setOnboardingCatalogReady] = useState(false);
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const [onboardingMode, setOnboardingMode] = useState("automatic");
  const [onboardingRetryKey, setOnboardingRetryKey] = useState(0);
  const [guideLibraryOpen, setGuideLibraryOpen] = useState(false);
  const [scoreGuideRequestKey, setScoreGuideRequestKey] = useState(0);
  const authenticatedRef = useRef(false);
  const messageFilterRef = useRef("ALL");
  const messageCursorRef = useRef(null);
  const notificationRevisionRef = useRef(0);
  const notificationRequestQueueRef = useRef(null);
  const previousPathRef = useRef(location.pathname);
  const tideSummaryRef = useRef(null);
  const scoreSyncAbortRef = useRef(null);
  const scoreSyncResetTimerRef = useRef(null);
  const coursePageAbortRef = useRef(null);
  const pageLoadFailureRef = useRef("");
  const onboardingRequestRef = useRef({
    checked: false,
    loading: false,
    retryCount: 0,
  });
  const onboardingReturnFocusRef = useRef(null);
  const guideShownThisSessionRef = useRef(false);
  const chainedOnboardingGuideRef = useRef(null);
  const currentTaskId = location.pathname.startsWith("/task/")
    ? tasks.find((item) => (
        location.pathname.endsWith(`/task/${item.id}`)
        || location.pathname.endsWith(`/task/${item.backendId}`)
      ))?.backendId || null
    : null;
  if (!notificationRequestQueueRef.current) {
    notificationRequestQueueRef.current = createNotificationRequestQueue(
      ({ cursor, filter }) => getNotifications(undefined, { cursor, filter }),
    );
  }
  const supportTicketUnreadCount = supportTickets.filter((ticket) => ticket.unread).length;
  const unreadCount = messageUnreadCount + supportTicketUnreadCount;
  const hideMobileNav = useMemo(
    () => location.pathname.startsWith("/task/"),
    [location.pathname],
  );
  const returningToGrowthPath = (
    location.pathname === "/path"
    && growthPathReturnSnapshot?.taskPath === previousPathRef.current
  );
  const growthPathSnapshot = returningToGrowthPath
    ? growthPathReturnSnapshot
    : null;
  const adaptNotification = useCallback((message) => {
    const englishCopy = localizeNotification(message, "en");
    const chineseCopy = localizeNotification(message, "zh");
    return {
      id: message.sourceNotificationId,
      type: message.actionType === "TASK_DETAIL" ? "TASK" : "TEXT",
      typeCode: message.typeCode,
      title: englishCopy.title,
      titleZh: chineseCopy.title,
      body: englishCopy.body,
      bodyZh: chineseCopy.body,
      issuedAt: new Date(message.issuedAt).toLocaleString("en-US"),
      issuedAtZh: new Date(message.issuedAt).toLocaleString("zh-CN"),
      relatedTaskId: taskCodeToRouteId[message.relatedTaskCode] || null,
      relatedTaskInstanceId: message.relatedTaskInstanceId,
      actionType: message.actionType,
      actionTarget: message.actionTarget,
      actionAvailable: message.actionAvailable,
      expired: message.expired,
      read: message.read,
      mock: false,
    };
  }, []);
  useEffect(() => {
    if (returningToGrowthPath) return;
    window.scrollTo({ top: 0, behavior: "auto" });
  }, [location.pathname]);
  useEffect(() => {
    setAnalyticsLanguage(language);
  }, [language]);
  useEffect(() => {
    tideSummaryRef.current = tideSummary;
  }, [tideSummary]);
  useEffect(() => () => {
    scoreSyncAbortRef.current?.abort();
    coursePageAbortRef.current?.abort();
    clearTimeout(scoreSyncResetTimerRef.current);
  }, []);
  useEffect(() => {
    if (!authReady || (authenticated && !dataReady)) return undefined;
    const task = currentTaskId
      ? tasks.find((item) => item.backendId === currentTaskId)
      : null;
    const loadResult = dataError ? "FAILURE" : "SUCCESS";
    if (dataError) {
      pageLoadFailureRef.current = `${location.pathname}:${dataError.code || dataError.name || "PAGE_DATA_UNAVAILABLE"}`;
    }
    return beginPageAnalytics({
      task,
      loadResult,
    });
  }, [
    authReady,
    authenticated,
    currentTaskId,
    dataReady,
    location.pathname,
  ]);
  useEffect(() => {
    if (!authReady || (authenticated && !dataReady)) return;
    if (!dataError) {
      pageLoadFailureRef.current = "";
      return;
    }
    const errorCode = dataError.code || dataError.name || "PAGE_DATA_UNAVAILABLE";
    const failureKey = `${location.pathname}:${errorCode}`;
    if (pageLoadFailureRef.current === failureKey) return;
    pageLoadFailureRef.current = failureKey;
    const task = currentTaskId
      ? tasks.find((item) => item.backendId === currentTaskId)
      : null;
    trackProductEvent("PAGE_LOAD_FAILED", {
      task,
      properties: {
        result: "FAILURE",
        errorCode,
      },
    });
  }, [
    authReady,
    authenticated,
    currentTaskId,
    dataError,
    dataReady,
    location.pathname,
  ]);
  useEffect(() => {
    if (!authReady || (authenticated && !dataReady)) return undefined;
    let stopTaskImpressions = () => {};
    let stopMessageImpressions = () => {};
    const frame = window.requestAnimationFrame(() => {
      stopTaskImpressions = observeEffectiveImpressions(
        "[data-analytics-task-card]",
        "TASK_CARD_IMPRESSION",
      );
      stopMessageImpressions = observeEffectiveImpressions(
        "[data-analytics-message]",
        "MESSAGE_IMPRESSION",
      );
    });
    return () => {
      window.cancelAnimationFrame(frame);
      stopTaskImpressions();
      stopMessageImpressions();
    };
  }, [
    authReady,
    authenticated,
    dataReady,
    location.pathname,
    messages.length,
    tasks.length,
  ]);
  useEffect(() => {
    let active = true;
    restoreSession().then((restored) => {
      if (!active) return;
      authenticatedRef.current = restored;
      setAuthenticated(restored);
      setAuthReady(true);
    });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!authenticated) return undefined;
    const controller = new AbortController();
    scoreSyncAbortRef.current?.abort();
    clearTimeout(scoreSyncResetTimerRef.current);
    setScoreSyncStatus("idle");
    setDataReady(false);
    setDataError(null);
    setSourceErrors({});
    setProfile(null);
    setTideSummary(null);
    setG01Review(null);
    setMessages([]);
    setMessageTotalCount(0);
    setMessageUnreadCount(0);
    setMessageNextCursor(null);
    setMessagesLoaded(false);
    setSupportTickets([]);
    setSupportTicketsLoaded(false);
    messageCursorRef.current = null;
    messageFilterRef.current = "ALL";
    setMessageFilter("ALL");
    setCourses([]);
    setAttributionCourses([]);
    setCoursePage(1);
    setCourseTotalCount(0);
    setCoursesLoading(true);
    Promise.allSettled([
      getTeacherProfile(controller.signal),
      getTideSummary(controller.signal),
      getG01Review(controller.signal),
      listTasks(controller.signal),
    ]).then(async ([profileResult, summaryResult, g01Result, taskResult]) => {
      if (controller.signal.aborted) return;
      setSourceErrors({
        profile: profileResult.status === "rejected" ? profileResult.reason : null,
        summary: summaryResult.status === "rejected" ? summaryResult.reason : null,
        g01: g01Result.status === "rejected" ? g01Result.reason : null,
      });
      setProfile(profileResult.status === "fulfilled" ? profileResult.value : null);
      setTideSummary(summaryResult.status === "fulfilled" ? summaryResult.value : null);
      const latestG01 = g01Result.status === "fulfilled" ? g01Result.value : null;
      setG01Review(latestG01);
      if (taskResult.status === "fulfilled") {
        try {
          const bundledContexts = Array.isArray(taskResult.value.contexts)
            ? taskResult.value.contexts
            : null;
          const contextResults = bundledContexts
            ? bundledContexts.map((value) => ({ status: "fulfilled", value }))
            : await loadTaskContexts(
              taskResult.value.items,
              getTask,
              controller.signal,
            );
          if (!controller.signal.aborted) {
            const contexts = contextResults
              .filter((result) => result?.status === "fulfilled")
              .map((result) => result.value);
            const failures = contextResults.filter((result) => result?.status === "rejected");
            const adaptedTasks = contexts.map((context) => adaptTaskContext(context, latestG01));
            setTasks(composePresentationTasks(adaptedTasks));
            if (failures.length > 0) {
              setSourceErrors((current) => ({
                ...current,
                tasks: "TASK_DETAILS_PARTIAL",
              }));
            }
            if (taskResult.value.items.length > 0 && contexts.length === 0) {
              setDataError(failures[0]?.reason || new Error("Task data is unavailable"));
            }
          }
        } catch (error) {
          if (!controller.signal.aborted) {
            setDataError(error);
          }
        }
      } else {
        setDataError(taskResult.reason || new Error("Task data is unavailable"));
      }
      if (controller.signal.aborted) return;
      setDataReady(true);

      Promise.allSettled([
        getNotifications(controller.signal),
        listSupportTickets(controller.signal),
        getCourses(controller.signal, {
          page: 1,
          pageSize: LESSONS_PER_PAGE,
        }),
        getAllCourses(controller.signal),
      ]).then(([
        notificationResult,
        supportTicketResult,
        courseResult,
        attributionCourseResult,
      ]) => {
        if (controller.signal.aborted) return;
        setSourceErrors((current) => ({
          ...current,
          notifications: notificationResult.status === "rejected"
            ? notificationResult.reason
            : null,
          supportTickets: supportTicketResult.status === "rejected"
            ? supportTicketResult.reason
            : null,
          courses: courseResult.status === "rejected"
            ? courseResult.reason
            : null,
          courseAttributions: attributionCourseResult.status === "rejected"
            ? attributionCourseResult.reason
            : null,
        }));
        if (notificationResult.status === "fulfilled") {
          setMessages(notificationResult.value.items.map(adaptNotification));
          setMessageTotalCount(notificationResult.value.totalCount);
          setMessageUnreadCount(notificationResult.value.unreadCount);
          setMessageNextCursor(notificationResult.value.nextCursor);
          messageCursorRef.current = notificationResult.value.nextCursor;
        }
        setMessagesLoaded(true);
        if (supportTicketResult.status === "fulfilled") {
          setSupportTickets(supportTicketResult.value.items);
        }
        setSupportTicketsLoaded(true);
        if (courseResult.status === "fulfilled") {
          setCourses(courseResult.value.items);
          setCoursePage(courseResult.value.page);
          setCourseTotalCount(courseResult.value.totalCount);
        }
        if (attributionCourseResult.status === "fulfilled") {
          setAttributionCourses(attributionCourseResult.value.items);
        }
        setCoursesLoading(false);
      });
    });
    return () => controller.abort();
  }, [authenticated, dataReloadKey]);
  useEffect(() => {
    const requestState = onboardingRequestRef.current;
    if (
      dataError
      || !shouldLoadOnboarding({
        authenticated,
        dataReady,
        checked: requestState.checked,
        loading: requestState.loading,
      })
    ) {
      return undefined;
    }

    const controller = new AbortController();
    let settled = false;
    let retryTimer = null;
    requestState.loading = true;
    getOnboardingStatus(controller.signal).then(
      (payload) => {
        if (controller.signal.aborted || !authenticatedRef.current) return;
        settled = true;
        requestState.loading = false;
        requestState.checked = true;
        const nextCatalog = normalizeOnboardingCatalog(payload);
        const nextStatus = nextCatalog.find(
          (guide) => guide.guideCode === ONBOARDING_GUIDE_CODE,
        ) || normalizeOnboardingStatus(payload);
        setOnboardingCatalog(nextCatalog);
        setOnboardingCatalogReady(true);
        setOnboardingStatus(nextStatus);
        if (nextStatus.required) {
          guideShownThisSessionRef.current = true;
          onboardingReturnFocusRef.current = null;
          setOnboardingMode("automatic");
          if (location.pathname !== "/") navigate("/", { replace: true });
          setOnboardingOpen(true);
        }
      },
      () => {
        if (controller.signal.aborted) return;
        settled = true;
        requestState.loading = false;
        requestState.checked = false;
        if (requestState.retryCount < 2) {
          requestState.retryCount += 1;
          retryTimer = window.setTimeout(() => {
            if (!authenticatedRef.current) return;
            setOnboardingRetryKey((current) => current + 1);
          }, requestState.retryCount * 1_500);
        } else {
          requestState.checked = true;
          setOnboardingCatalogReady(false);
        }
        // The optional guide never blocks the page while its status is retried.
      },
    );
    return () => {
      if (!settled) {
        controller.abort();
        requestState.loading = false;
      }
      window.clearTimeout(retryTimer);
    };
  }, [
    authenticated,
    dataError,
    dataReady,
    location.pathname,
    navigate,
    onboardingRetryKey,
  ]);
  useEffect(() => {
    if (!authenticated || !dataReady || location.pathname !== "/") return undefined;
    const controller = new AbortController();
    getTideSummary(controller.signal).then(
      (latestSummary) => {
        if (controller.signal.aborted) return;
        setTideSummary(latestSummary);
        setSourceErrors((current) => ({ ...current, summary: null }));
      },
      () => {
        // 保留已展示的积分；进入 My TIDE 时的补充刷新失败不覆盖现有结果。
      },
    );
    return () => controller.abort();
  }, [authenticated, dataReady, location.pathname]);
  const refreshNotifications = useCallback(async ({
    filter = messageFilterRef.current,
    forceFresh = false,
  } = {}) => {
    const queue = notificationRequestQueueRef.current;
    const revision = forceFresh
      ? ++notificationRevisionRef.current
      : notificationRevisionRef.current;
    setMessagesLoading(true);
    try {
      const result = await queue.run({ filter, revision });
      if (!authenticatedRef.current || messageFilterRef.current !== filter) return result;
      setMessages(result.items.map(adaptNotification));
      setMessageTotalCount(result.totalCount);
      setMessageUnreadCount(result.unreadCount);
      setMessageNextCursor(result.nextCursor);
      messageCursorRef.current = result.nextCursor;
      setSourceErrors((current) => ({ ...current, notifications: null }));
      setMessagesLoaded(true);
      return result;
    } catch (error) {
      if (authenticatedRef.current && messageFilterRef.current === filter) {
        setSourceErrors((current) => ({
          ...current,
          notifications: error,
        }));
      }
      setMessagesLoaded(true);
      throw error;
    } finally {
      if (!queue.isRunning()) setMessagesLoading(false);
    }
  }, [adaptNotification]);
  const refreshSupportTickets = useCallback(async () => {
    setSupportTicketsLoading(true);
    try {
      const result = await listSupportTickets();
      if (!authenticatedRef.current) return result;
      setSupportTickets(result.items);
      setSourceErrors((current) => ({ ...current, supportTickets: null }));
      setSupportTicketsLoaded(true);
      return result;
    } catch (error) {
      if (authenticatedRef.current) {
        setSourceErrors((current) => ({ ...current, supportTickets: error }));
      }
      setSupportTicketsLoaded(true);
      throw error;
    } finally {
      setSupportTicketsLoading(false);
    }
  }, []);
  const updateSupportTicket = useCallback((updated) => {
    setSupportTicketsLoaded(true);
    setSupportTickets((current) => {
      const exists = current.some((ticket) => ticket.ticketId === updated.ticketId);
      return exists
        ? current.map((ticket) => ticket.ticketId === updated.ticketId ? updated : ticket)
        : [updated, ...current];
    });
  }, []);
  const loadCoursePage = useCallback(async (nextPage) => {
    const controller = new AbortController();
    coursePageAbortRef.current?.abort();
    coursePageAbortRef.current = controller;
    setCoursesLoading(true);
    try {
      const result = await getCourses(controller.signal, {
        page: nextPage,
        pageSize: LESSONS_PER_PAGE,
      });
      if (controller.signal.aborted || !authenticatedRef.current) return result;
      setCourses(result.items);
      setCoursePage(result.page);
      setCourseTotalCount(result.totalCount);
      setSourceErrors((current) => ({ ...current, courses: null }));
      return result;
    } catch (error) {
      if (!controller.signal.aborted && authenticatedRef.current) {
        setSourceErrors((current) => ({ ...current, courses: error }));
      }
      throw error;
    } finally {
      if (coursePageAbortRef.current === controller) {
        coursePageAbortRef.current = null;
        setCoursesLoading(false);
      }
    }
  }, []);
  useEffect(() => {
    const previousPath = previousPathRef.current;
    previousPathRef.current = location.pathname;
    if (
      location.pathname.startsWith("/task/")
      && previousPath !== "/path"
      && previousPath !== location.pathname
    ) {
      growthPathReturnSnapshot = null;
    }
    if (
      authenticated
      && dataReady
      && location.pathname === "/messages"
      && previousPath !== "/messages"
    ) {
      void refreshNotifications().catch(() => undefined);
      void refreshSupportTickets().catch(() => undefined);
    }
  }, [authenticated, dataReady, location.pathname, refreshNotifications, refreshSupportTickets]);
  useEffect(() => {
    if (!authenticated || !dataReady) return undefined;
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") {
        void refreshNotifications().catch(() => undefined);
      }
    };
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => document.removeEventListener("visibilitychange", refreshWhenVisible);
  }, [authenticated, dataReady, refreshNotifications]);
  const refreshTask = useCallback(async (taskInstanceId) => {
    if (!taskInstanceId) return;
    const context = await getTask(taskInstanceId);
    const refreshedTask = adaptTaskContext(context, g01Review);
    setTasks((current) => {
      const exists = current.some(
        (task) => task.backendId === taskInstanceId,
      );
      return sortTaskContexts(
        exists
          ? current.map((task) =>
            task.backendId === taskInstanceId ? refreshedTask : task)
          : [...current, refreshedTask],
      );
    });
    return context;
  }, [g01Review]);
  const changeMessageFilter = async (nextFilter) => {
    if (nextFilter === messageFilterRef.current) return;
    messageFilterRef.current = nextFilter;
    messageCursorRef.current = null;
    setMessageFilter(nextFilter);
    setMessageNextCursor(null);
    try {
      await refreshNotifications({ filter: nextFilter });
    } catch {
      // refreshNotifications 已保留当前列表并更新消息源错误状态。
    }
  };
  const loadMoreMessages = async () => {
    if (!messageNextCursor || messagesLoading) return;
    const cursor = messageNextCursor;
    const filter = messageFilterRef.current;
    const queue = notificationRequestQueueRef.current;
    setMessagesLoading(true);
    try {
      const result = await queue.run({
        cursor,
        filter,
        revision: notificationRevisionRef.current,
      });
      if (messageFilterRef.current !== filter || messageCursorRef.current !== cursor) return;
      setMessages((current) => {
        const known = new Set(current.map((message) => message.id));
        return [
          ...current,
          ...result.items
            .filter((message) => !known.has(message.sourceNotificationId))
            .map(adaptNotification),
        ];
      });
      setMessageTotalCount(result.totalCount);
      setMessageUnreadCount(result.unreadCount);
      setMessageNextCursor(result.nextCursor);
      messageCursorRef.current = result.nextCursor;
      setSourceErrors((current) => ({ ...current, notifications: null }));
    } catch (error) {
      setSourceErrors((current) => ({
        ...current,
        notifications: error,
      }));
    } finally {
      if (!queue.isRunning()) setMessagesLoading(false);
    }
  };
  const changeLanguage = (nextLanguage) => {
    if (nextLanguage === language) return;
    trackProductEvent("LANGUAGE_CHANGED", {
      properties: {
        interaction: `${language}_TO_${nextLanguage}`,
        result: "SUCCESS",
      },
    });
    localStorage.setItem("new-teacher-camp-language", nextLanguage);
    setLanguage(nextLanguage);
  };
  const readMessage = (messageId, openAction) => {
    const openedMessage = messages.find((message) => message.id === messageId);
    trackProductEvent("MESSAGE_DETAIL_OPENED", {
      properties: {
        notificationType: openedMessage?.typeCode || openedMessage?.type || "UNKNOWN",
        actionType: openedMessage?.actionType || "NONE",
        result: "OPENED",
      },
    });
    if (
      openedMessage?.actionType
      && openedMessage.actionAvailable === false
    ) {
      trackProductEvent("MESSAGE_ACTION_EXPIRED", {
        properties: {
          notificationType: openedMessage.typeCode || openedMessage.type || "UNKNOWN",
          actionType: openedMessage.actionType,
          result: "EXPIRED",
        },
      });
    }
    const wasUnread = messages.some((message) => message.id === messageId && !message.read);
    if (!wasUnread) return;
    setMessages((current) => current.map((message) =>
      message.id === messageId && !message.read
        ? { ...message, read: true, readAt: new Date().toISOString(), openAction }
        : message,
    ));
    setMessageUnreadCount((current) => Math.max(0, current - 1));
    markNotificationRead(messageId).then(
      () => {
        void refreshNotifications({ forceFresh: true }).catch(() => undefined);
      },
      () => {
        setMessages((current) => current.map((message) =>
          message.id === messageId ? { ...message, read: false, readAt: null, openAction: null } : message,
        ));
        setMessageUnreadCount((current) => current + 1);
      },
    );
  };
  const clickMessage = (message) => {
    markNotificationClicked(message.id).catch(() => {});
    if (message.actionType === "TASK_DETAIL" && message.relatedTaskInstanceId) {
      trackProductEvent("MESSAGE_TASK_CLICKED", {
        taskAssignmentId: message.relatedTaskInstanceId,
        properties: {
          entrySource: "MESSAGES",
          displayPosition: "MESSAGE_ACTION",
          notificationType: message.typeCode || "TASK",
          actionType: "TASK_DETAIL",
        },
      });
      trackProductEvent("TASK_OPENED_FROM_MESSAGE", {
        taskAssignmentId: message.relatedTaskInstanceId,
        properties: {
          entrySource: "MESSAGES",
          displayPosition: "MESSAGE_ACTION",
          notificationType: message.typeCode || "TASK",
        },
      });
    }
    if (message.actionType && !message.actionAvailable) {
      trackProductEvent("MESSAGE_ACTION_EXPIRED", {
        properties: {
          notificationType: message.typeCode || message.type || "UNKNOWN",
          actionType: message.actionType,
          result: "EXPIRED",
        },
      });
    }
    if (message.actionType === "HELP") {
      openHelp("MESSAGE");
    }
    if (message.actionType === "ACCOUNT") setPasswordResetOpen(true);
  };
  const onboardingCatalogByCode = useMemo(
    () => new Map(onboardingCatalog.map((guide) => [guide.guideCode, guide])),
    [onboardingCatalog],
  );
  const firstCompletedRequiredTask = useMemo(
    () => tasks.find((task) => task.taskCategory !== "personalized" && task.status === "completed") || null,
    [tasks],
  );
  const firstPersonalizedTask = useMemo(
    () => tasks.find((task) => (
      task.taskCategory === "personalized"
      && task.surface !== "growth_only"
      && !["cancelled", "expired"].includes(task.status)
    )) || null,
    [tasks],
  );
  const onboardingAvailability = useMemo(() => ({
    [ONBOARDING_GUIDE_CODES.firstLogin]: true,
    [ONBOARDING_GUIDE_CODES.myTideOverview]: Boolean(
      profile && tideSummary && !sourceErrors.profile && !sourceErrors.summary,
    ),
    [ONBOARDING_GUIDE_CODES.scoreDetails]: Boolean(
      tideSummary && !sourceErrors.summary,
    ),
    [ONBOARDING_GUIDE_CODES.taskPath]: tasks.some((task) => task.taskCategory !== "personalized"),
    [ONBOARDING_GUIDE_CODES.taskResult]: Boolean(firstCompletedRequiredTask),
    [ONBOARDING_GUIDE_CODES.messagesTickets]: Boolean(
      messagesLoaded && supportTicketsLoaded,
    ),
    [ONBOARDING_GUIDE_CODES.helpRoutes]: true,
    [ONBOARDING_GUIDE_CODES.personalizedTaskFirst]: Boolean(firstPersonalizedTask),
  }), [
    firstCompletedRequiredTask,
    firstPersonalizedTask,
    messagesLoaded,
    profile,
    sourceErrors.profile,
    sourceErrors.summary,
    supportTicketsLoaded,
    tasks,
    tideSummary,
  ]);
  const guideLibraryGuides = useMemo(
    () => ONBOARDING_GUIDE_ORDER.map((guideCode) => ({
      ...(onboardingCatalogByCode.get(guideCode) || { guideCode }),
      guideCode,
      available: onboardingAvailability[guideCode] === true,
      unavailableLabel: guideCode === ONBOARDING_GUIDE_CODES.messagesTickets
        ? copy(language, "Available after the message center finishes loading", "消息中心加载完成后可查看")
        : guideCode === ONBOARDING_GUIDE_CODES.taskResult
          ? copy(language, "Available after a required task has a result", "必修任务产生结果后可查看")
          : guideCode === ONBOARDING_GUIDE_CODES.personalizedTaskFirst
            ? copy(language, "Available after your first personalized task is assigned", "分配首项个性化任务后可查看")
            : copy(language, "The related data is not available yet", "相关数据暂不可用"),
    })),
    [language, onboardingAvailability, onboardingCatalogByCode],
  );
  const startOnboardingGuide = useCallback((guideCode, {
    mode = "automatic",
    returnFocusElement = null,
    forceAvailable = false,
  } = {}) => {
    const guide = getOnboardingGuide(guideCode);
    const state = onboardingCatalogByCode.get(guide.guideCode);
    if (mode === "automatic" && (
      !onboardingCatalogReady
      || guideShownThisSessionRef.current
      || onboardingOpen
      || guideLibraryOpen
      || (!forceAvailable && !onboardingAvailability[guide.guideCode])
      || !state?.required
      || Boolean(state.status)
    )) {
      return false;
    }

    guideShownThisSessionRef.current = true;
    onboardingReturnFocusRef.current = returnFocusElement || document.activeElement;
    setOnboardingStatus({
      required: state?.required === true,
      guideCode: guide.guideCode,
      guideVersion: state?.guideVersion || guide.guideVersion,
    });
    setOnboardingMode(mode);
    setGuideLibraryOpen(false);
    setOnboardingOpen(true);
    return true;
  }, [
    guideLibraryOpen,
    onboardingAvailability,
    onboardingCatalogByCode,
    onboardingCatalogReady,
    onboardingOpen,
  ]);
  const openHelp = useCallback((entrySource, { suppressGuide = false } = {}) => {
    const returnFocusElement = document.activeElement;
    setHelpLoaded(true);
    setHelpEntrySource(entrySource);
    trackProductEvent("AI_HELP_OPENED", {
      properties: { entrySource },
    });
    setHelpOpen(true);
    if (!suppressGuide) {
      window.requestAnimationFrame(() => {
        startOnboardingGuide(ONBOARDING_GUIDE_CODES.helpRoutes, { returnFocusElement });
      });
    }
  }, [startOnboardingGuide]);
  const closeHelp = useCallback(() => setHelpOpen(false), []);
  const closeOnboarding = useCallback(() => {
    setOnboardingOpen(false);
    const nextGuideCode = chainedOnboardingGuideRef.current;
    chainedOnboardingGuideRef.current = null;
    if (!nextGuideCode) return;

    const guide = getOnboardingGuide(nextGuideCode);
    const state = onboardingCatalogByCode.get(nextGuideCode);
    if (
      !state?.required
      || state.status
      || !onboardingAvailability[nextGuideCode]
    ) {
      return;
    }

    setHelpOpen(false);
    guideShownThisSessionRef.current = true;
    onboardingReturnFocusRef.current = null;
    setOnboardingStatus({
      required: true,
      guideCode: nextGuideCode,
      guideVersion: state.guideVersion || guide.guideVersion,
    });
    setOnboardingMode("automatic");
    window.setTimeout(() => setOnboardingOpen(true), 0);
  }, [onboardingAvailability, onboardingCatalogByCode]);
  const closeGuideLibrary = useCallback(() => setGuideLibraryOpen(false), []);
  const openGuideLibrary = useCallback((returnFocusElement) => {
    onboardingReturnFocusRef.current = returnFocusElement;
    setGuideLibraryOpen(true);
  }, []);
  const playGuideFromLibrary = useCallback((guideCode) => {
    if (!onboardingAvailability[guideCode]) return;
    const returnFocusElement = onboardingReturnFocusRef.current;
    if (guideCode === ONBOARDING_GUIDE_CODES.firstLogin
      || guideCode === ONBOARDING_GUIDE_CODES.myTideOverview
      || guideCode === ONBOARDING_GUIDE_CODES.scoreDetails) {
      navigate("/", { replace: true });
    }
    if (guideCode === ONBOARDING_GUIDE_CODES.taskPath) {
      navigate("/", { replace: true });
    }
    if (guideCode === ONBOARDING_GUIDE_CODES.personalizedTaskFirst) {
      navigate("/path", { replace: true });
    }
    if (guideCode === ONBOARDING_GUIDE_CODES.taskResult && firstCompletedRequiredTask) {
      navigate(`/task/${firstCompletedRequiredTask.id}`, { replace: true });
    }
    if (guideCode === ONBOARDING_GUIDE_CODES.messagesTickets) {
      navigate("/messages", { replace: true });
      void refreshNotifications().catch(() => undefined);
      void refreshSupportTickets().catch(() => undefined);
    }
    if (guideCode === ONBOARDING_GUIDE_CODES.scoreDetails) {
      setScoreGuideRequestKey((current) => current + 1);
    }
    if (guideCode === ONBOARDING_GUIDE_CODES.helpRoutes) {
      openHelp("GUIDE_LIBRARY", { suppressGuide: true });
    }
    startOnboardingGuide(guideCode, { mode: "replay", returnFocusElement });
  }, [
    firstCompletedRequiredTask,
    navigate,
    onboardingAvailability,
    openHelp,
    refreshNotifications,
    refreshSupportTickets,
    startOnboardingGuide,
  ]);
  const acknowledgeCurrentOnboarding = useCallback(async (outcome, idempotencyKey) => {
    const result = await acknowledgeOnboarding({
      guideCode: onboardingStatus.guideCode,
      guideVersion: onboardingStatus.guideVersion,
      outcome,
    }, idempotencyKey);
    setOnboardingCatalog((current) => current.map((guide) => (
      guide.guideCode === onboardingStatus.guideCode
        ? {
            ...guide,
            ...result,
            required: false,
            status: result?.status || outcome,
          }
        : guide
    )));
    setOnboardingStatus((current) => ({ ...current, required: false }));
    if (
      outcome === ONBOARDING_OUTCOMES.completed
      && onboardingStatus.guideCode === ONBOARDING_GUIDE_CODE
    ) {
      const taskPathState = onboardingCatalogByCode.get(ONBOARDING_GUIDE_CODES.taskPath);
      if (
        taskPathState?.required
        && !taskPathState.status
        && onboardingAvailability[ONBOARDING_GUIDE_CODES.taskPath]
      ) {
        chainedOnboardingGuideRef.current = ONBOARDING_GUIDE_CODES.taskPath;
      }
    }
    return result;
  }, [
    onboardingAvailability,
    onboardingCatalogByCode,
    onboardingStatus.guideCode,
    onboardingStatus.guideVersion,
  ]);
  const openTasksFromOnboarding = useCallback(() => {
    setHelpOpen(false);
    navigate("/path", { replace: true });
  }, [navigate]);
  const openMyTideFromOnboarding = useCallback(() => {
    setHelpOpen(false);
    navigate("/", { replace: true });
  }, [navigate]);
  const openPrimaryTaskFromOnboarding = useCallback(() => {
    const task = onboardingPrimaryRequiredTask(tasks);
    navigate(task ? `/task/${task.id}` : "/path", { replace: true });
  }, [navigate, tasks]);
  const openMessagesFromOnboarding = useCallback(() => {
    setHelpOpen(false);
    navigate("/messages", { replace: true });
  }, [navigate]);
  const openMessages = useCallback(() => {
    if (location.pathname === "/messages") {
      void refreshNotifications().catch(() => undefined);
      void refreshSupportTickets().catch(() => undefined);
    }
  }, [location.pathname, refreshNotifications, refreshSupportTickets]);
  const openScoreDetailsGuide = useCallback(() => {
    startOnboardingGuide(ONBOARDING_GUIDE_CODES.scoreDetails, {
      returnFocusElement: document.activeElement,
    });
  }, [startOnboardingGuide]);
  useEffect(() => {
    if (
      !authenticated
      || !dataReady
      || !onboardingCatalogReady
      || guideShownThisSessionRef.current
      || onboardingOpen
      || guideLibraryOpen
      || helpOpen
    ) {
      return undefined;
    }

    let guideCode = null;
    if (location.pathname === "/") {
      guideCode = ONBOARDING_GUIDE_CODES.myTideOverview;
    } else if (location.pathname === "/path") {
      const taskPathState = onboardingCatalogByCode.get(ONBOARDING_GUIDE_CODES.taskPath);
      guideCode = taskPathState?.required && !taskPathState.status
        ? ONBOARDING_GUIDE_CODES.taskPath
        : ONBOARDING_GUIDE_CODES.personalizedTaskFirst;
    } else if (location.pathname === "/messages") {
      guideCode = ONBOARDING_GUIDE_CODES.messagesTickets;
    }

    if (!guideCode || !onboardingAvailability[guideCode]) return undefined;
    const timer = window.setTimeout(() => {
      startOnboardingGuide(guideCode);
    }, 240);
    return () => window.clearTimeout(timer);
  }, [
    authenticated,
    dataReady,
    guideLibraryOpen,
    helpOpen,
    location.pathname,
    onboardingAvailability,
    onboardingCatalogByCode,
    onboardingCatalogReady,
    onboardingOpen,
    startOnboardingGuide,
  ]);
  const refreshAfterTask = useCallback((response) => {
    void refreshNotifications({ forceFresh: true }).catch(() => undefined);
    const completedTask = tasks.find(
      (task) => task.backendId === response?.taskInstanceId,
    );
    if (
      response?.status !== "COMPLETED"
      || completedTask?.taskCategory === "personalized"
    ) {
      return;
    }

    scoreSyncAbortRef.current?.abort();
    clearTimeout(scoreSyncResetTimerRef.current);
    const controller = new AbortController();
    scoreSyncAbortRef.current = controller;
    setScoreSyncStatus("syncing");
    window.requestAnimationFrame(() => {
      startOnboardingGuide(ONBOARDING_GUIDE_CODES.taskResult, {
        returnFocusElement: document.activeElement,
        forceAvailable: true,
      });
    });

    void pollScorecard({
      baseline: tideSummaryRef.current,
      signal: controller.signal,
      load: getTideSummary,
    }).then((result) => {
      if (controller.signal.aborted || result.status === "cancelled") return;
      if (result.status === "updated") {
        setTideSummary(result.scorecard);
        setSourceErrors((current) => ({ ...current, summary: null }));
        setScoreSyncStatus("updated");
      } else {
        setScoreSyncStatus("delayed");
      }
      scoreSyncResetTimerRef.current = setTimeout(
        () => setScoreSyncStatus("idle"),
        result.status === "updated" ? 5_000 : 10_000,
      );
    });
  }, [refreshNotifications, startOnboardingGuide, tasks]);
  const login = () => {
    onboardingRequestRef.current = {
      checked: false,
      loading: false,
      retryCount: 0,
    };
    onboardingReturnFocusRef.current = null;
    guideShownThisSessionRef.current = false;
    chainedOnboardingGuideRef.current = null;
    setOnboardingOpen(false);
    setOnboardingMode("automatic");
    setGuideLibraryOpen(false);
    setOnboardingCatalog(normalizeOnboardingCatalog(null));
    setOnboardingCatalogReady(false);
    setOnboardingStatus({
      required: false,
      guideCode: ONBOARDING_GUIDE_CODE,
      guideVersion: ONBOARDING_DEFAULT_VERSION,
    });
    navigate("/", { replace: true });
    authenticatedRef.current = true;
    setAuthenticated(true);
  };
  const logout = async () => {
    try {
      await logoutTeacher();
    } finally {
      onboardingRequestRef.current = {
        checked: false,
        loading: false,
        retryCount: 0,
      };
      onboardingReturnFocusRef.current = null;
      guideShownThisSessionRef.current = false;
      chainedOnboardingGuideRef.current = null;
      setOnboardingOpen(false);
      setOnboardingMode("automatic");
      setGuideLibraryOpen(false);
      setOnboardingCatalog(normalizeOnboardingCatalog(null));
      setOnboardingCatalogReady(false);
      authenticatedRef.current = false;
      setAuthenticated(false);
    }
  };
  const runtimeTeacher = useMemo(() => {
    return {
      name: profile?.name || copy(language, "Teacher", "老师"),
      email: profile?.email || "",
      day: profile?.campDay ?? "—",
      totalDays: profile?.totalCampDays ?? "—",
      graduationStatus: tideSummary?.graduationState
        || profile?.graduationState
        || copy(language, "Result updating", "结果更新中"),
      growthScore: {
        current: tideSummary?.publicTotalScore ?? null,
        total: tideSummary?.goldThreshold ?? scoreMilestones.total,
        graduationMilestone: tideSummary?.graduationThreshold
          ?? scoreMilestones.graduation,
        goldMilestone: tideSummary?.goldThreshold ?? scoreMilestones.gold,
        graduationQualified: tideSummary?.graduationQualified === true,
        goldQualified: tideSummary?.goldQualified === true,
        available: tideSummary?.availableScore?.score ?? null,
        availableItems: tideSummary?.availableScore?.items || [],
        rules: tideSummary?.dimensions || [],
        updatedAt: tideSummary?.calculatedAt || tideSummary?.freshness?.sourceUpdatedAt
          ? new Date(tideSummary?.calculatedAt || tideSummary.freshness.sourceUpdatedAt).toLocaleString(language === "zh" ? "zh-CN" : "en-US")
          : copy(language, "Waiting for data", "等待数据"),
        resultVersion: tideSummary?.scoreRuleVersion || "UNAVAILABLE",
      },
      g01Review,
    };
  }, [g01Review, language, profile, tideSummary]);
  const localizedSourceErrors = {
    profile: sourceErrors.profile
      ? localizeApiError(sourceErrors.profile, language, copy(language, "Teacher profile is unavailable.", "教师资料暂时无法加载。"))
      : "",
    summary: sourceErrors.summary
      ? localizeApiError(sourceErrors.summary, language, copy(language, "TIDE summary is unavailable.", "成长积分暂时无法加载。"))
      : "",
    g01: sourceErrors.g01
      ? localizeApiError(sourceErrors.g01, language, copy(language, "G01 review status is unavailable.", "G01 审核状态暂时无法加载。"))
      : "",
    notifications: sourceErrors.notifications
      ? localizeApiError(sourceErrors.notifications, language, copy(language, "Notifications are unavailable.", "通知暂时无法加载。"))
      : "",
    supportTickets: sourceErrors.supportTickets
      ? localizeApiError(sourceErrors.supportTickets, language, copy(language, "Support tickets are unavailable.", "工单暂时无法加载。"))
      : "",
    courses: sourceErrors.courses
      ? localizeApiError(sourceErrors.courses, language, copy(language, "Course details are unavailable.", "课程详情暂时无法加载。"))
      : "",
  };
  const localizedDataError = dataError
    ? localizeApiError(
        dataError,
        language,
        copy(language, "Task data is unavailable.", "任务数据暂时无法加载。"),
      )
    : "";

  if (!authReady) {
    return <main className="auth-screen" aria-busy="true" />;
  }

  if (!authenticated) {
    return (
      <I18nProvider language={language}>
        <Suspense fallback={<main className="auth-screen" aria-busy="true" />}>
          <AuthScreen language={language} onLanguageChange={changeLanguage} onAuthenticated={login} />
        </Suspense>
      </I18nProvider>
    );
  }
  if (!dataReady) {
    return <main className="auth-screen" aria-busy="true" />;
  }
  if (dataError) {
    return (
      <TaskDataErrorScreen
        error={dataError}
        language={language}
        onRetry={() => setDataReloadKey((current) => current + 1)}
      />
    );
  }
  return (
    <I18nProvider language={language}>
      <TeacherContext.Provider value={runtimeTeacher}>
      <div
        key={language}
        className={`reference-app ${hideMobileNav ? "detail-route" : ""}`}
      >
        <Header
          language={language}
          unreadCount={unreadCount}
          onHelp={() => openHelp("HEADER")}
          onLanguageChange={changeLanguage}
          onLogout={logout}
          onMessagesOpen={openMessages}
          onQuickGuide={openGuideLibrary}
          onResetPassword={() => setPasswordResetOpen(true)}
        />
        <Routes>
          <Route
            path="/"
            element={
              <MyTitPage
                tasks={tasks}
                tideSummary={tideSummary}
                courses={courses}
                attributionCourses={attributionCourses}
                coursePage={coursePage}
                courseTotalCount={courseTotalCount}
                coursesLoading={coursesLoading}
                onCoursePageChange={(page) => {
                  void loadCoursePage(page).catch(() => undefined);
                }}
                sourceErrors={localizedSourceErrors}
                language={language}
                unreadCount={unreadCount}
                onMessagesOpen={openMessages}
                onScoreDetailsOpen={openScoreDetailsGuide}
                scoreGuideRequestKey={scoreGuideRequestKey}
              />
            }
          />
          <Route
            path="/path"
            element={
              <GrowthPathPage
                tasks={tasks}
                language={language}
                unreadCount={unreadCount}
                onMessagesOpen={openMessages}
                returnSnapshot={growthPathSnapshot}
              />
            }
          />
          <Route
            path="/messages"
            element={
              <Suspense fallback={<LazyPanelFallback />}>
                <MessageCenter
                  messages={messages}
                  tasks={tasks.map((task) => localizeTask(task, language))}
                  language={language}
                  onRead={readMessage}
                  onAction={clickMessage}
                  filter={messageFilter}
                  onFilterChange={changeMessageFilter}
                  totalCount={messageTotalCount}
                  unreadCount={messageUnreadCount}
                  nextCursor={messageNextCursor}
                  loading={messagesLoading}
                  onLoadMore={loadMoreMessages}
                  error={localizedSourceErrors.notifications}
                  supportTickets={supportTickets}
                  supportTicketsLoading={supportTicketsLoading}
                  supportTicketsError={localizedSourceErrors.supportTickets}
                  onSupportTicketsRefresh={() => void refreshSupportTickets().catch(() => undefined)}
                  onSupportTicketUpdated={updateSupportTicket}
                  mobileNav={<MobileNav language={language} unreadCount={unreadCount} onMessagesOpen={openMessages} />}
                />
              </Suspense>
            }
          />
          <Route path="/tide" element={<Navigate to="/" replace />} />
          <Route path="/tit" element={<Navigate to="/" replace />} />
          <Route
            path="/task/:taskId"
            element={
              <TaskDetailPage
                tasks={tasks}
                language={language}
                onRefresh={refreshTask}
                onTaskSubmitted={refreshAfterTask}
                onHelp={() => openHelp("TASK_DETAIL")}
              />
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        {scoreSyncStatus !== "idle" && (
          <div
            className={`score-sync-toast is-${scoreSyncStatus}`}
            role="status"
            aria-live="polite"
            data-onboarding-target="task-result-score-sync"
          >
            {scoreSyncStatus === "updated"
              ? <CheckCircle size={22} weight="fill" />
              : <Hourglass size={22} weight="bold" />}
            <span>
              <strong>
                {scoreSyncStatus === "syncing"
                  ? copy(language, "Score syncing", "积分同步中")
                  : scoreSyncStatus === "updated"
                    ? copy(language, "Score updated", "积分已更新")
                    : copy(language, "Score is still settling", "积分仍在结算")}
              </strong>
              <small>
                {scoreSyncStatus === "syncing"
                  ? copy(language, "Task completed. Refreshing the latest score.", "任务已完成，正在更新最新积分。")
                  : scoreSyncStatus === "updated"
                    ? copy(language, "My TIDE now shows the latest result.", "My TIDE 已自动更新为最新结果。")
                    : copy(language, "My TIDE will read it again when you return.", "返回 My TIDE 时会继续自动读取。")}
              </small>
            </span>
          </div>
        )}
        {!helpOpen && (
          <FloatingAiHelpButton
            language={language}
            onOpen={() => openHelp("FLOATING_BUTTON")}
            routeKey={location.pathname}
          />
        )}
        {helpLoaded && (
          <Suspense fallback={null}>
            <FaqHelpDialog
              open={helpOpen}
              language={language}
              entrySource={helpEntrySource}
              onClose={closeHelp}
              taskOptions={tasks
                .filter((task) => task.backendId)
                .map((task) => ({
                  id: task.backendId,
                  code: task.taskCode,
                  name: task.name,
                  displayName: localizeTask(task, language).name,
                }))}
              lessonOptions={courses.map((course) => ({
                id: course.lessonId,
                sequence: course.lessonSequence,
                scheduledStartAt: course.scheduledStartAt,
                localDate: course.lessonLocalDate,
                localTime: course.lessonLocalTime,
              }))}
              supportContext={{
                myTideLastUpdated: tideSummary?.calculatedAt || tideSummary?.freshness?.sourceUpdatedAt || null,
                taskAssignmentId: currentTaskId,
                taskCode: tasks.find((task) => task.backendId === currentTaskId)?.taskCode || null,
                taskName: tasks.find((task) => task.backendId === currentTaskId)?.name || null,
              }}
              onTicketCreated={updateSupportTicket}
            />
          </Suspense>
        )}
        <PasswordResetDialog
          open={passwordResetOpen}
          language={language}
          email={runtimeTeacher.email}
          onClose={() => setPasswordResetOpen(false)}
        />
      </div>
      <GuideLibraryDialog
        open={guideLibraryOpen}
        language={language}
        guides={guideLibraryGuides}
        returnFocusElement={onboardingReturnFocusRef.current}
        onClose={closeGuideLibrary}
        onPlay={playGuideFromLibrary}
      />
      <OnboardingGuide
        open={onboardingOpen}
        mode={onboardingMode}
        language={language}
        guideCode={onboardingStatus.guideCode}
        guideVersion={onboardingStatus.guideVersion}
        returnFocusElement={onboardingReturnFocusRef.current}
        onLanguageChange={changeLanguage}
        onAcknowledge={acknowledgeCurrentOnboarding}
        onClose={closeOnboarding}
        onOpenTasks={openTasksFromOnboarding}
        onOpenMyTide={openMyTideFromOnboarding}
        onOpenMessages={openMessagesFromOnboarding}
        onOpenPrimaryTask={openPrimaryTaskFromOnboarding}
      />
      </TeacherContext.Provider>
    </I18nProvider>
  );
}

function buildOnboardingPreviewTasks(language) {
  const methods = {
    G01: "profile_credentials",
    G02: "external_course",
    G03: "content_pending",
    G04: "readiness_photo",
    G05: "external_course",
    G06: "external_course",
    G07: "external_course",
    G08: "external_course",
    G09: "external_course",
  };
  return fixedTaskCatalog.map((catalogTask, index) => {
    const firstTask = catalogTask.taskCode === "G01";
    const laterStage = index >= 4;
    return {
      ...catalogTask,
      localizationId: catalogTask.id,
      backendId: null,
      taskCode: catalogTask.taskCode,
      stateVersion: null,
      backendStatus: "VIEWED",
      backendContext: null,
      name: catalogTask.name,
      shortName: catalogTask.name,
      method: methods[catalogTask.taskCode] || "content_pending",
      status: "available",
      locked: laterStage,
      taskCategory: "required",
      sourceStage: catalogTask.stage,
      duration: copy(language, "About 15 min", "约 15 分钟"),
      due: firstTask
        ? copy(language, "Complete before your first lesson", "建议首课前完成")
        : copy(language, "Available in your growth path", "按成长路径开放"),
      dueAt: null,
      priority: firstTask
        ? copy(language, "Recommended first", "建议先做")
        : copy(language, "Required task", "必修任务"),
      reason: firstTask
        ? copy(
            language,
            "Complete your teacher profile and credential requirements so you are ready for your first lesson.",
            "完善教师档案与资质要求，为第一节课做好准备。",
          )
        : copy(
            language,
            "Complete this required step to keep your 30-day growth path moving.",
            "完成这项必修内容，继续推进你的 30 天成长路径。",
          ),
      value: copy(
        language,
        "Know exactly what is ready and what to complete next.",
        "清楚知道哪些已经准备好，以及下一步要完成什么。",
      ),
      result: firstTask
        ? copy(
            language,
            "Review your profile status and complete the credential actions shown in the workspace.",
            "查看档案状态，并在任务工作区完成资质相关操作。",
          )
        : copy(
            language,
            "Open the task and follow the instructions in its workspace.",
            "打开任务，并按照工作区中的说明完成。",
          ),
      standard: firstTask
        ? copy(
            language,
            "All required TESOL learning conditions are complete.",
            "所有 TESOL 学习条件均已完成。",
          )
        : copy(
            language,
            "Meet every completion condition listed in the task.",
            "达到任务中列出的全部完成条件。",
          ),
      steps: firstTask
        ? [
            copy(language, "Review TESOL status", "查看 TESOL 状态"),
            copy(language, "Complete the required learning work", "完成必需学习任务"),
            copy(language, "Confirm the completion result", "确认完成结果"),
          ]
        : [copy(language, "Review the task instructions", "查看任务说明")],
      backendSteps: [],
      backendProgressByStep: {},
      progress: 0,
      displayRank: index + 1,
      isPrimary: firstTask,
      dataOrigin: "DEV_PREVIEW",
      signalFacts: [],
      externalStatusItems: firstTask
        ? [
            {
              id: "preview-credential",
              type: "credential",
              label: "TESOL / teaching credential",
              labelZh: "TESOL / 教学资质",
              source: "Preview status",
              sourceZh: "预览状态",
              updatedAt: "Preview",
              updatedAtZh: "预览",
              status: "waiting",
            },
          ]
        : [],
    };
  });
}

function OnboardingAcceptancePreview() {
  const [language, setLanguage] = useState("zh");
  const [open, setOpen] = useState(true);
  const [previewGuideMode, setPreviewGuideMode] = useState("automatic");
  const [activeGuideCode, setActiveGuideCode] = useState(ONBOARDING_GUIDE_CODE);
  const [guideLibraryOpen, setGuideLibraryOpen] = useState(false);
  const [scoreGuideRequestKey, setScoreGuideRequestKey] = useState(0);
  const [previewHelpOpen, setPreviewHelpOpen] = useState(false);
  const guideReturnFocusRef = useRef(null);
  const chainedPreviewGuideRef = useRef(null);
  const location = useLocation();
  const navigate = useNavigate();
  const tasks = useMemo(
    () => buildOnboardingPreviewTasks(language),
    [language],
  );
  const calculatedAt = "2026-08-07T09:00:00+08:00";
  const tideSummary = useMemo(() => ({
    publicTotalScore: 18,
    graduationThreshold: 100,
    goldThreshold: 200,
    graduationQualified: false,
    goldQualified: false,
    graduationState: copy(language, "Growing", "成长中"),
    availableScore: { score: 182, items: [] },
    scoreRuleVersion: "DEV_PREVIEW",
    calculatedAt,
    dimensions: [
      { code: "USER_FEEDBACK", score: 6, components: [], calculatedAt },
      { code: "RELIABILITY", score: 4, components: [], calculatedAt },
      { code: "CLASS_QUALITY", score: 3, components: [], calculatedAt },
      { code: "CAPACITY", score: 5, components: [], calculatedAt },
      { code: "NEW_TEACHER_TASK", score: 0, components: [], calculatedAt },
    ],
  }), [language]);
  const teacher = useMemo(() => ({
    name: copy(language, "Teacher Mia", "Mia 老师"),
    email: "preview.teacher@example.invalid",
    day: 1,
    totalDays: 30,
    graduationStatus: copy(language, "Growing", "成长中"),
    growthScore: {
      current: tideSummary.publicTotalScore,
      total: tideSummary.goldThreshold,
      graduationMilestone: tideSummary.graduationThreshold,
      goldMilestone: tideSummary.goldThreshold,
      graduationQualified: false,
      goldQualified: false,
      available: tideSummary.availableScore.score,
      availableItems: [],
      rules: tideSummary.dimensions,
      updatedAt: copy(language, "Preview data", "预览数据"),
      resultVersion: tideSummary.scoreRuleVersion,
    },
    g01Review: null,
  }), [language, tideSummary]);
  const sourceErrors = useMemo(() => ({
    profile: "",
    summary: "",
    g01: "",
    notifications: "",
    supportTickets: "",
    courses: "",
    courseAttributions: "",
  }), []);
  const previewMessages = useMemo(() => [], []);
  const previewGuideStates = useMemo(
    () => ONBOARDING_GUIDE_ORDER.map((guideCode) => {
      const available = ![
        ONBOARDING_GUIDE_CODES.taskResult,
        ONBOARDING_GUIDE_CODES.personalizedTaskFirst,
      ].includes(guideCode);
      return {
        guideCode,
        guideVersion: getOnboardingGuide(guideCode).guideVersion,
        required: false,
        status: guideCode === ONBOARDING_GUIDE_CODE ? "COMPLETED" : null,
        available,
        unavailableLabel: guideCode === ONBOARDING_GUIDE_CODES.taskResult
          ? copy(language, "Complete a real required task to unlock this guide", "真实必修任务产生完成结果后开放")
          : copy(language, "A real personalized assignment is required", "出现真实个性化任务后开放"),
      };
    }),
    [language],
  );
  const changePreviewLanguage = (nextLanguage) => {
    const normalized = nextLanguage === "zh" ? "zh" : "en";
    setLanguage(normalized);
  };
  const openPreviewGuideLibrary = (returnFocusElement) => {
    guideReturnFocusRef.current = returnFocusElement;
    setGuideLibraryOpen(true);
  };
  const playPreviewGuide = (guideCode) => {
    const guide = previewGuideStates.find((item) => item.guideCode === guideCode);
    if (!guide?.available) return;
    setGuideLibraryOpen(false);
    setActiveGuideCode(guideCode);
    setPreviewGuideMode("replay");
    if ([
      ONBOARDING_GUIDE_CODES.firstLogin,
      ONBOARDING_GUIDE_CODES.myTideOverview,
      ONBOARDING_GUIDE_CODES.scoreDetails,
    ].includes(guideCode)) {
      navigate("/", { replace: true });
    }
    if (guideCode === ONBOARDING_GUIDE_CODES.taskPath) {
      navigate("/", { replace: true });
    }
    if (guideCode === ONBOARDING_GUIDE_CODES.messagesTickets) {
      navigate("/messages", { replace: true });
    }
    if (guideCode === ONBOARDING_GUIDE_CODES.scoreDetails) {
      setScoreGuideRequestKey((current) => current + 1);
    }
    if (guideCode === ONBOARDING_GUIDE_CODES.helpRoutes) {
      setPreviewHelpOpen(true);
    }
    setOpen(true);
  };

  return (
    <I18nProvider language={language}>
      <TeacherContext.Provider value={teacher}>
        <div
          key={language}
          className={`reference-app onboarding-real-preview ${location.pathname.startsWith("/task/") ? "detail-route" : ""}`}
        >
          <Header
            language={language}
            unreadCount={0}
            onHelp={() => {
              setPreviewHelpOpen(true);
              setActiveGuideCode(ONBOARDING_GUIDE_CODES.helpRoutes);
              setOpen(true);
            }}
            onLanguageChange={changePreviewLanguage}
            onLogout={() => undefined}
            onMessagesOpen={() => undefined}
            onQuickGuide={openPreviewGuideLibrary}
            onResetPassword={() => undefined}
          />
          <Routes>
            <Route
              path="/"
              element={(
                <MyTitPage
                  tasks={tasks}
                  tideSummary={tideSummary}
                  courses={[]}
                  attributionCourses={[]}
                  coursePage={1}
                  courseTotalCount={0}
                  coursesLoading={false}
                  onCoursePageChange={() => undefined}
                  sourceErrors={sourceErrors}
                  language={language}
                  unreadCount={0}
                  onMessagesOpen={() => undefined}
                  onScoreDetailsOpen={() => {
                    if (open) return;
                    setActiveGuideCode(ONBOARDING_GUIDE_CODES.scoreDetails);
                    setOpen(true);
                  }}
                  scoreGuideRequestKey={scoreGuideRequestKey}
                />
              )}
            />
            <Route
              path="/path"
              element={(
                <GrowthPathPage
                  tasks={tasks}
                  language={language}
                  unreadCount={0}
                  onMessagesOpen={() => undefined}
                />
              )}
            />
            <Route
              path="/messages"
              element={(
                <Suspense fallback={<LazyPanelFallback />}>
                  <MessageCenter
                    messages={previewMessages}
                    tasks={tasks.map((task) => localizeTask(task, language))}
                    language={language}
                    onRead={() => undefined}
                    onAction={() => undefined}
                    filter="ALL"
                    onFilterChange={() => undefined}
                    totalCount={previewMessages.length}
                    unreadCount={previewMessages.length}
                    nextCursor=""
                    loading={false}
                    onLoadMore={() => undefined}
                    error=""
                    mobileNav={(
                      <MobileNav
                        language={language}
                        unreadCount={previewMessages.length}
                        onMessagesOpen={() => undefined}
                      />
                    )}
                  />
                </Suspense>
              )}
            />
            <Route
              path="/task/:taskId"
              element={(
                <TaskDetailPage
                  tasks={tasks}
                  onRefresh={async () => undefined}
                  onTaskSubmitted={async () => undefined}
                  onHelp={() => undefined}
                  language={language}
                  previewMode
                />
              )}
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          <FloatingAiHelpButton
            language={language}
            onOpen={() => {
              setPreviewHelpOpen(true);
              setActiveGuideCode(ONBOARDING_GUIDE_CODES.helpRoutes);
              setOpen(true);
            }}
            routeKey={location.pathname}
          />
          <Suspense fallback={null}>
            <FaqHelpDialog
              open={previewHelpOpen}
              language={language}
              entrySource="ONBOARDING_PREVIEW"
              onClose={() => setPreviewHelpOpen(false)}
              supportContext={{}}
              taskOptions={tasks.map((task) => ({
                id: task.id,
                code: task.taskCode,
                name: task.name,
                displayName: localizeTask(task, language).name,
              }))}
              lessonOptions={[]}
              onTicketCreated={() => undefined}
            />
          </Suspense>
        </div>
        <GuideLibraryDialog
          open={guideLibraryOpen}
          language={language}
          guides={previewGuideStates}
          returnFocusElement={guideReturnFocusRef.current}
          onClose={() => setGuideLibraryOpen(false)}
          onPlay={playPreviewGuide}
        />
        <OnboardingGuide
          open={open}
          mode={previewGuideMode}
          language={language}
          guideCode={activeGuideCode}
          guideVersion={getOnboardingGuide(activeGuideCode).guideVersion}
          analyticsEnabled={false}
          returnFocusElement={guideReturnFocusRef.current}
          onLanguageChange={changePreviewLanguage}
          onAcknowledge={async (outcome) => {
            if (
              previewGuideMode === "automatic"
              && activeGuideCode === ONBOARDING_GUIDE_CODE
              && outcome === ONBOARDING_OUTCOMES.completed
            ) {
              chainedPreviewGuideRef.current = ONBOARDING_GUIDE_CODES.taskPath;
            }
          }}
          onClose={() => {
            setOpen(false);
            const nextGuideCode = chainedPreviewGuideRef.current;
            chainedPreviewGuideRef.current = null;
            if (!nextGuideCode) return;
            setPreviewHelpOpen(false);
            setActiveGuideCode(nextGuideCode);
            setPreviewGuideMode("automatic");
            window.setTimeout(() => setOpen(true), 0);
          }}
          onOpenTasks={() => navigate("/path", { replace: true })}
          onOpenMyTide={() => navigate("/", { replace: true })}
          onOpenMessages={() => navigate("/messages", { replace: true })}
          onOpenPrimaryTask={() => {
            const task = onboardingPrimaryRequiredTask(tasks);
            navigate(task ? `/task/${task.id}` : "/path", { replace: true });
          }}
        />
      </TeacherContext.Provider>
    </I18nProvider>
  );
}

export default function App({ onboardingPreview = false }) {
  return onboardingPreview ? <OnboardingAcceptancePreview /> : <AppShell />;
}
