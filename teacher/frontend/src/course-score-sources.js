const apiDimensionToCatalogKey = {
  USER_FEEDBACK: "userFeedback",
  RELIABILITY: "reliability",
  CLASS_QUALITY: "classQuality",
};

export const courseScoreSourceLabels = {
  FEEDBACK_PRAISE: { en: "Learner feedback", zh: "学员好评" },
  FEEDBACK_FAVORITE: { en: "Learner favorite", zh: "学员收藏" },
  FEEDBACK_REBOOK_15D: { en: "15-day rebooking", zh: "15 天内复约" },
  ON_TIME_COMPLETED: { en: "On-time class completion", zh: "准时完课情况" },
  PEAK_COMPLETED: { en: "Peak-time class completion", zh: "高峰时段完课" },
  PERFECT_COMPLETED: { en: "Perfect class completion", zh: "完美完课" },
  RELIABILITY_NO_EARLY_LEAVE: { en: "Class completion", zh: "课程完成情况" },
  RELIABILITY_EARLY_LEAVE_CORRECTED: { en: "Class-record review", zh: "课程记录复核" },
  CLASS_QUALITY_HARDWARE: {
    en: "No device, network, or teaching-environment issues",
    zh: "无设备网络&教学环境问题",
  },
  CLASS_QUALITY_PERFECT_COUNT: { en: "Hardware-quality result", zh: "硬件质量结果" },
  CLASS_QUALITY_CAMERA_ON: { en: "Camera status", zh: "摄像头状态" },
  CLASS_QUALITY_CPU_STABLE: { en: "Computer status", zh: "电脑运行状态" },
  CLASS_QUALITY_NETWORK_STABLE: { en: "Network status", zh: "网络状态" },
  CAPACITY_PEAK_SLOT_40: { en: "Peak-time bookable slots", zh: "高峰时段可约课时数" },
};

export const courseScoreDimensionGroups = [
  {
    id: "feedback",
    apiDimension: "USER_FEEDBACK",
    title: { en: "User Feedback", zh: "用户反馈" },
  },
  {
    id: "reliability",
    apiDimension: "RELIABILITY",
    title: { en: "Reliability", zh: "上课稳定度" },
  },
  {
    id: "quality",
    apiDimension: "CLASS_QUALITY",
    title: { en: "Hardware Quality", zh: "硬件质量" },
  },
];

export function positiveCourseScoreSources(course) {
  return (course?.dimensions || []).flatMap((dimension) =>
    (dimension.components || []).flatMap((component) => {
      const score = Number(component.score);
      if (
        component.awarded !== true ||
        !Number.isFinite(score) ||
        score <= 0
      ) {
        return [];
      }
      return [{
        dimension: dimension.code,
        sourceKey: component.code,
        score,
        scoreRuleVersion: course.scoreRuleVersion,
        evidenceStatus: component.evidenceStatus,
      }];
    }));
}

const factValue = (value, whenTrue, whenFalse) => {
  if (value === null || value === undefined) {
    return {
      value: { en: "No data yet", zh: "暂无数据" },
      tone: "missing",
    };
  }
  return value
    ? { value: whenTrue, tone: "positive" }
    : { value: whenFalse, tone: "neutral" };
};

const visibleCourseFactDefinitions = [
  {
    dimension: "USER_FEEDBACK",
    sourceKey: "FEEDBACK_PRAISE",
    read: (facts) => factValue(
      facts.positiveFeedback,
      { en: "Positive feedback received", zh: "已收到好评" },
      { en: "No positive feedback recorded", zh: "暂无好评" },
    ),
  },
  {
    dimension: "USER_FEEDBACK",
    sourceKey: "FEEDBACK_FAVORITE",
    read: (facts) => factValue(
      facts.favorited,
      { en: "Saved as a favorite", zh: "已被学员收藏" },
      { en: "No favorite recorded", zh: "暂无收藏" },
    ),
  },
  {
    dimension: "USER_FEEDBACK",
    sourceKey: "FEEDBACK_REBOOK_15D",
    read: (facts) => factValue(
      facts.rebooked,
      { en: "Rebooked within 15 days", zh: "15 天内已复约" },
      { en: "No rebooking within 15 days", zh: "暂无 15 天内复约" },
    ),
  },
  {
    dimension: "RELIABILITY",
    sourceKey: "ON_TIME_COMPLETED",
    read: (facts) => {
      const effectiveEarlyLeave = facts.falseEarlyLeave === true
        ? false
        : facts.earlyLeave;
      if (
        facts.late === null ||
        facts.late === undefined ||
        effectiveEarlyLeave === null ||
        effectiveEarlyLeave === undefined
      ) {
        return {
          value: { en: "Incomplete timing data", zh: "时间记录不完整" },
          tone: "missing",
        };
      }
      return facts.late === false && effectiveEarlyLeave === false
        ? {
            value: { en: "Completed on time", zh: "准时完成" },
            tone: "positive",
          }
        : {
            value: { en: "Not recorded as completed on time", zh: "未记录为准时完成" },
            tone: "attention",
          };
    },
  },
  {
    dimension: "RELIABILITY",
    sourceKey: "RELIABILITY_NO_EARLY_LEAVE",
    read: (facts) => {
      if (facts.falseEarlyLeave === true) {
        return {
          value: { en: "Completed in full after review", zh: "复核后确认完整完成" },
          tone: "positive",
        };
      }
      const result = factValue(
        facts.earlyLeave,
        { en: "An early finish was recorded", zh: "有提前结束记录" },
        { en: "Completed in full", zh: "完整完成" },
      );
      return result.tone === "positive"
        ? { ...result, tone: "attention" }
        : result.tone === "neutral"
          ? { ...result, tone: "positive" }
          : result;
    },
  },
  {
    dimension: "RELIABILITY",
    sourceKey: "RELIABILITY_EARLY_LEAVE_CORRECTED",
    read: (facts) => factValue(
      facts.falseEarlyLeave,
      { en: "A mistaken record was corrected", zh: "误判记录已修正" },
      { en: "No correction recorded", zh: "无修正记录" },
    ),
  },
  {
    dimension: "CLASS_QUALITY",
    sourceKey: "CLASS_QUALITY_CAMERA_ON",
    read: (facts) => {
      const result = factValue(
        facts.cameraOff,
        { en: "An off-camera period was recorded", zh: "有关闭摄像头记录" },
        { en: "Camera remained on", zh: "摄像头保持开启" },
      );
      return result.tone === "positive"
        ? { ...result, tone: "attention" }
        : result.tone === "neutral"
          ? { ...result, tone: "positive" }
          : result;
    },
  },
  {
    dimension: "CLASS_QUALITY",
    sourceKey: "CLASS_QUALITY_CPU_STABLE",
    read: (facts) => {
      const result = factValue(
        facts.cpuUsageHigh,
        { en: "High computer load was recorded", zh: "有电脑高负载记录" },
        { en: "Computer ran steadily", zh: "电脑运行稳定" },
      );
      return result.tone === "positive"
        ? { ...result, tone: "attention" }
        : result.tone === "neutral"
          ? { ...result, tone: "positive" }
          : result;
    },
  },
  {
    dimension: "CLASS_QUALITY",
    sourceKey: "CLASS_QUALITY_NETWORK_STABLE",
    read: (facts) => {
      const result = factValue(
        facts.networkDelayHigh,
        { en: "A network-delay period was recorded", zh: "有网络延迟记录" },
        { en: "Network remained stable", zh: "网络连接稳定" },
      );
      return result.tone === "positive"
        ? { ...result, tone: "attention" }
        : result.tone === "neutral"
          ? { ...result, tone: "positive" }
          : result;
    },
  },
];

export function visibleCourseIndicators(course) {
  if (course?.lifecycleStatus?.trim().toLowerCase() !== "end") return [];

  const scoredSources = positiveCourseScoreSources(course);
  const scoredByKey = new Map(
    scoredSources.map((source) => [source.sourceKey, source]),
  );
  const indicators = visibleCourseFactDefinitions
    .map((definition) => {
      const scored = scoredByKey.get(definition.sourceKey);
      const result = definition.read(course.facts || {});
      return {
        dimension: definition.dimension,
        sourceKey: definition.sourceKey,
        value: result.value,
        tone: result.tone,
        score: scored ? Number(scored.score) : null,
      };
    });
  const visibleKeys = new Set(indicators.map((indicator) => indicator.sourceKey));
  scoredSources.forEach((source) => {
    if (visibleKeys.has(source.sourceKey)) return;
    indicators.push({
      dimension: source.dimension,
      sourceKey: source.sourceKey,
      value: { en: "Confirmed", zh: "已确认" },
      tone: "positive",
      score: Number(source.score),
    });
  });
  return indicators;
}

export const lessonLifecycleStatusLabels = {
  end: { en: "Completed", zh: "已完课" },
  s_absent: { en: "Learner absent", zh: "学员缺席" },
  t_absent: { en: "Teacher absent", zh: "教师缺席" },
};

export function lessonLifecycleStatusLabel(status) {
  const normalized = status?.trim().toLowerCase();
  return lessonLifecycleStatusLabels[normalized] || {
    en: status || "Recorded",
    zh: status || "已记录",
  };
}

export function courseSourcesForDimension(courses, catalogSourceKey) {
  return courses.flatMap((course, index) =>
    positiveCourseScoreSources(course)
      .filter(
        (source) =>
          apiDimensionToCatalogKey[source.dimension] === catalogSourceKey,
      )
      .map((source) => ({
        sourceKey: source.sourceKey,
        value: 1,
        unit: "CLASSES",
        score: Number(source.score),
        lessonId: course.lessonId,
        lessonNumber: course.lessonSequence || index + 1,
        lessonStartedAt: course.scheduledStartAt,
      })),
  );
}

export function mergeScorecardCourseSources(
  courses,
  catalogSourceKey,
  scorecardSources = [],
) {
  const summaries = new Map(
    scorecardSources.map((source) => [source.sourceKey, source]),
  );
  const attributed = courseSourcesForDimension(courses, catalogSourceKey).map(
    (source) => {
      const summary = summaries.get(source.sourceKey);
      return {
        ...source,
        summaryScore: summary?.score,
        summaryValue: summary?.value,
      };
    },
  );
  const attributedKeys = new Set(
    attributed.map((source) => source.sourceKey),
  );
  const summaryOnly = scorecardSources.flatMap((source) => {
    const score = Number(source.score);
    if (
      attributedKeys.has(source.sourceKey) ||
      !Number.isFinite(score) ||
      score <= 0
    ) {
      return [];
    }
    return [{
      ...source,
      score,
      summaryScore: score,
      summaryValue: source.value,
      attributionMissing: source.unit === "CLASSES",
    }];
  });

  return [...attributed, ...summaryOnly];
}
