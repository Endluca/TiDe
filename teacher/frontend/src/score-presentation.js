const scorecardGroupCopy = {
  USER_FEEDBACK: {
    title: { en: "User Feedback", zh: "用户反馈" },
    description: {
      en: "Praise accumulates by event; favorites count unique learners. This dimension has no cap.",
      zh: "好评按次累计，收藏按去重学员人数累计，本维度不封顶。",
    },
  },
  RELIABILITY: {
    title: { en: "Class Reliability", zh: "上课稳定度" },
    description: {
      en: "Eligible classes earn the per-class points in the current scorecard.",
      zh: "符合条件的课程按当前积分卡中的单课分值累计。",
    },
  },
  CLASS_QUALITY: {
    title: { en: "Hardware Quality", zh: "硬件质量" },
    description: {
      en: "Hardware-quality points follow the current scorecard.",
      zh: "硬件质量积分以当前积分卡为准。",
    },
  },
  CAPACITY: {
    title: { en: "Teaching Availability", zh: "有效供给" },
    description: {
      en: "A one-time award is granted after reaching the required bookable-slot target.",
      zh: "达到规定的可约课时目标后获得一次性积分。",
    },
  },
  NEW_TEACHER_TASK: {
    title: { en: "Required Tasks", zh: "必修任务" },
    description: {
      en: "Each required task earns the corresponding points after completion.",
      zh: "每项必修任务完成后，获得对应积分。",
    },
  },
};

const scorecardRuleCopy = {
  FEEDBACK_PRAISE: {
    title: { en: "Positive learner feedback", zh: "学员好评" },
    condition: { en: "Each confirmed positive feedback", zh: "每收到 1 次已确认好评" },
    unit: { en: "/event", zh: "/次" },
  },
  FEEDBACK_FAVORITE: {
    title: { en: "Learner favorite", zh: "学员收藏" },
    condition: {
      en: "Each learner's first eligible favorite of this teacher; later favorites do not score again",
      zh: "每名学员首次符合计分条件的收藏课；后续收藏不重复计分",
    },
    unit: { en: "/learner", zh: "/人" },
  },
  FEEDBACK_REBOOK_15D: {
    title: { en: "15-day rebooking", zh: "15 天内复约" },
    condition: { en: "Each confirmed rebooking within 15 days", zh: "每产生 1 次已确认的 15 天内复约" },
    unit: { en: "/event", zh: "/次" },
  },
  ON_TIME_COMPLETED: {
    title: { en: "On-time class completion", zh: "准时完课" },
    condition: { en: "Each eligible class completed on time", zh: "每节符合条件的准时完课" },
    unit: { en: "/class", zh: "/节" },
  },
  PEAK_COMPLETED: {
    title: { en: "Peak-time class completion", zh: "高峰时段完课" },
    condition: { en: "Each eligible class completed in a peak period", zh: "每节符合条件的高峰时段完课" },
    unit: { en: "/class", zh: "/节" },
  },
  PERFECT_COMPLETED: {
    title: { en: "Perfect class completion", zh: "完美完课" },
    condition: { en: "Each eligible perfect class completion", zh: "每节符合条件的完美完课" },
    unit: { en: "/class", zh: "/节" },
  },
  CLASS_QUALITY_HARDWARE: {
    title: {
      en: "No device, network, or teaching-environment issues",
      zh: "无设备网络&教学环境问题",
    },
    condition: {
      en: "Each eligible class without device, network, or teaching-environment issues",
      zh: "每节无设备、网络及教学环境问题的课程",
    },
    unit: { en: "/class", zh: "/节" },
  },
  CLASS_QUALITY_PERFECT_COUNT: {
    title: { en: "Hardware quality passed", zh: "硬件质量达标" },
    condition: { en: "Each eligible class meeting the hardware quality requirements", zh: "每节符合硬件质量标准的课程" },
    unit: { en: "/class", zh: "/节" },
  },
  CAPACITY_PEAK_SLOT_40: {
    title: { en: "Peak-time bookable slots", zh: "高峰时段可约课时数" },
    condition: { en: "First reaches 40 peak-time bookable slots", zh: "高峰时段可约课时数首次达到 40" },
    unit: { en: " once", zh: "（一次性）" },
  },
};

const localize = (value, language) =>
  language === "zh" ? value.zh : value.en;

export function presentScorecardRules(dimensions, language) {
  return (dimensions || []).flatMap((dimension) => {
    const group = scorecardGroupCopy[dimension.code];
    if (!group) return [];

    const items = (dimension.components || []).flatMap((component) => {
      const configuredPoints = component.pointsPerUnit === null ||
        component.pointsPerUnit === undefined
        ? null
        : Number(component.pointsPerUnit);
      const settledPoints = Number(component.score);
      const points =
        component.code === "CAPACITY_PEAK_SLOT_40" &&
        (!Number.isFinite(configuredPoints) || configuredPoints <= 0)
          ? settledPoints
          : configuredPoints;
      if (!Number.isFinite(points) || points <= 0) return [];

      const isRequiredTask =
        dimension.code === "NEW_TEACHER_TASK" &&
        /^G(?:0[1-9]|10)$/.test(component.code);
      const rule = scorecardRuleCopy[component.code];
      if (!rule && !isRequiredTask) return [];

      const title = isRequiredTask
        ? component.code
        : localize(rule.title, language);
      const condition = isRequiredTask
        ? language === "zh"
          ? `完成 ${component.code} 必修任务`
          : `Complete required task ${component.code}`
        : localize(rule.condition, language);
      const unit = isRequiredTask ? "" : localize(rule.unit, language);

      return [{
        code: component.code,
        title,
        condition,
        pointsLabel: `+${new Intl.NumberFormat(
          language === "zh" ? "zh-CN" : "en-US",
          { maximumFractionDigits: 2 },
        ).format(points)}${unit}`,
      }];
    });

    return items.length > 0
      ? [{
          code: dimension.code,
          title: localize(group.title, language),
          description: localize(group.description, language),
          currentScore: Number(dimension.score),
          scoreRuleVersion: dimension.scoreRuleVersion,
          items,
        }]
      : [];
  });
}

export function scoreStageStates(currentValue, graduationTarget, goldTarget) {
  const current = Number(currentValue);
  const graduation = Number(graduationTarget);
  const gold = Number(goldTarget);
  const hasCurrent =
    currentValue !== null &&
    currentValue !== undefined &&
    currentValue !== "" &&
    Number.isFinite(current);
  const graduationReached =
    hasCurrent && Number.isFinite(graduation) && current >= graduation;
  const goldReached = hasCurrent && Number.isFinite(gold) && current >= gold;
  return {
    graduation: {
      reached: graduationReached,
      current: !graduationReached,
      gap: hasCurrent && Number.isFinite(graduation)
        ? roundOneDecimal(Math.max(graduation - current, 0))
        : null,
    },
    gold: {
      reached: goldReached,
      current: graduationReached,
      gap: hasCurrent && Number.isFinite(gold)
        ? roundOneDecimal(Math.max(gold - current, 0))
        : null,
    },
  };
}

export function presentStageAction(stage, remainingRequiredTasks, language) {
  if (stage.reached) {
    return language === "zh" ? "已达成" : "Achieved";
  }
  if (stage.gap === null) {
    return language === "zh" ? "等待积分更新" : "Waiting for score";
  }

  const taskCount = Math.max(0, Number(remainingRequiredTasks) || 0);
  const scoreAction = stage.gap > 0
    ? language === "zh"
      ? `还差 ${stage.gap.toFixed(1)} 分`
      : `${stage.gap.toFixed(1)} points to go`
    : null;
  const taskAction = taskCount > 0
    ? language === "zh"
      ? `完成 ${taskCount} 项必修任务`
      : `complete ${taskCount} required ${taskCount === 1 ? "task" : "tasks"}`
    : null;

  if (scoreAction && taskAction) {
    return language === "zh"
      ? `${scoreAction}，并${taskAction}`
      : `${scoreAction}, and ${taskAction}`;
  }
  if (scoreAction) return scoreAction;
  if (taskAction) {
    return language === "zh" ? `还需${taskAction}` : `Still need to ${taskAction}`;
  }

  // No user-actionable gap remains. Do not expose historical conditions that
  // cannot be reversed or replace the backend's final qualification result.
  return null;
}

function roundOneDecimal(value) {
  return Math.round((value + Number.EPSILON) * 10) / 10;
}
