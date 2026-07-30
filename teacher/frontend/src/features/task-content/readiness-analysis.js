const PENDING_BACKEND_STATUSES = new Set(["UPLOADING", "CHECKING", "BEAUTIFYING"]);
const UNAVAILABLE_BACKEND_STATUSES = new Set(["UNDER_REVIEW", "PROCESSING_FAILED"]);

const CHECK_STATUS_COPY = {
  camera_angle: {
    pass: ["The camera angle meets the current requirement.", "摄像头角度符合当前要求。"],
    fail: ["The camera angle needs adjustment.", "摄像头角度需要调整。"],
    uncertain: ["The camera angle could not be assessed reliably.", "暂时无法可靠判断摄像头角度。"],
  },
  lighting: {
    pass: ["The lighting meets the current requirement.", "光线符合当前要求。"],
    fail: ["The lighting needs adjustment.", "光线需要调整。"],
    uncertain: ["The lighting could not be assessed reliably.", "暂时无法可靠判断光线。"],
  },
  background: {
    pass: ["The background meets the current requirement.", "背景符合当前要求。"],
    fail: ["The background needs adjustment.", "背景需要调整。"],
    uncertain: ["The background could not be assessed reliably.", "暂时无法可靠判断背景。"],
  },
  dressing: {
    pass: ["The visible clothing meets the current requirement.", "画面中的着装符合当前要求。"],
    fail: ["The visible clothing needs adjustment.", "画面中的着装需要调整。"],
    uncertain: ["The visible clothing could not be assessed reliably.", "暂时无法可靠判断画面中的着装。"],
  },
};

export function isReadinessPendingStatus(status) {
  return PENDING_BACKEND_STATUSES.has(String(status || "").toUpperCase());
}

function isChineseCopy(c) {
  return c("__en__", "__zh__") === "__zh__";
}

function checkStatusMessage(checkId, status, c) {
  const copy = CHECK_STATUS_COPY[checkId]?.[status];
  if (copy) return c(copy[0], copy[1]);
  if (status === "pass") {
    return c("Meets the current requirement.", "符合当前要求。");
  }
  if (status === "fail") {
    return c(
      "This item needs adjustment before you retake the photo.",
      "本项需要调整后重新拍照。",
    );
  }
  return c(
    "This item could not be assessed reliably.",
    "本项暂时无法可靠判断。",
  );
}

function stableTeacherMessage(status, backendStatus, backendDecision, c) {
  if (status === "approved") {
    return c(
      "All visible items meet the current requirement.",
      "画面中的各项内容均符合当前要求。",
    );
  }
  if (status === "changes_requested") {
    return c(
      "Some items need adjustment. Review the results, then retake the photo.",
      "部分项目需要调整，请查看检测结果后重新拍照。",
    );
  }
  if (status === "processing") {
    if (backendStatus === "UPLOADING") {
      return c(
        "The photo is being uploaded.",
        "照片正在上传中。",
      );
    }
    if (backendStatus === "BEAUTIFYING") {
      return c(
        "The check passed. The photo is being prepared and saved.",
        "画面检测已通过，正在处理并保存照片。",
      );
    }
    return c(
      "The camera view is being checked. This usually takes 10-15 seconds.",
      "正在审核，预计 10-15 秒。",
    );
  }
  if (backendStatus === "UNDER_REVIEW") {
    return c(
      "The automatic camera check is taking longer than expected. Please try again later.",
      "自动画面检测耗时较长，请稍后重新检测。",
    );
  }
  if (
    backendStatus === "PROCESSING_FAILED" ||
    backendDecision === "ERROR"
  ) {
    return c(
      "The automatic camera check could not be completed. Please try again.",
      "自动画面检测未能完成，请重新检测。",
    );
  }
  return c(
    "The automatic camera check is temporarily unavailable. Please try again.",
    "自动画面检测暂时不可用，请重新检测。",
  );
}

export function normalizeReadinessAnalysis(payload, criteria, c) {
  const backendStatus = String(payload?.status || "").toUpperCase();
  const backendDecision = String(payload?.decision || "").toUpperCase();
  const useBackendCopy = isChineseCopy(c);
  const suppliedChecks = new Map(
    (Array.isArray(payload?.checks) ? payload.checks : []).map((check) => [
      check.id || check.code,
      check,
    ]),
  );
  const checks = criteria.map((standard) => {
    const supplied = suppliedChecks.get(standard.id) || {};
    const rawStatus = String(supplied.status || "").toLowerCase();
    const status = ["pass", "fail", "uncertain"].includes(rawStatus)
      ? rawStatus
      : "uncertain";
    return {
      id: standard.id,
      title: standard.title,
      status,
      message:
        (useBackendCopy && supplied.message) ||
        checkStatusMessage(standard.id, status, c),
      suggestion:
        status === "pass"
          ? ""
          : (useBackendCopy && supplied.suggestion) || standard.detail,
    };
  });
  const hasCompleteChecks = criteria.every((standard) => {
    const check = suppliedChecks.get(standard.id);
    return ["pass", "fail", "uncertain"].includes(
      String(check?.status || "").toLowerCase(),
    );
  });
  const allPassed = hasCompleteChecks && checks.every((check) => check.status === "pass");

  let status;
  if (isReadinessPendingStatus(backendStatus)) {
    status = "processing";
  } else if (
    UNAVAILABLE_BACKEND_STATUSES.has(backendStatus) ||
    backendDecision === "ERROR"
  ) {
    status = "unavailable";
  } else if (backendStatus === "READY") {
    status = allPassed ? "approved" : "unavailable";
  } else if (backendStatus === "RETRY_REQUIRED" || backendDecision === "RETRY") {
    status = hasCompleteChecks ? "changes_requested" : "unavailable";
  } else {
    status = allPassed
      ? "approved"
      : hasCompleteChecks
        ? "changes_requested"
        : "unavailable";
  }

  const visibleChecks =
    status === "approved" || status === "changes_requested" ? checks : [];

  return {
    status,
    provider: payload?.provider || "readiness-ai",
    modelVersion: payload?.modelVersion || null,
    checkedAt: payload?.checkedAt || new Date().toISOString(),
    checks: visibleChecks,
    photoRunId: payload?.photoRunId || null,
    beautyStatus: payload?.status || null,
    finalPhoto: payload?.finalPhoto || null,
    teacherMessage:
      (useBackendCopy && payload?.teacherMessage) ||
      stableTeacherMessage(status, backendStatus, backendDecision, c),
  };
}
